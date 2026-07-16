#!/usr/bin/env python3
"""
tools/clearwright_work.py: ClearWright local dispatch / work-item loop.

List, claim, and respond to work items derived from a clearance queue's existing
durable state, so Claude Desktop, Codex, scripts, or future workers can pick up
and act on real local work over CLI, PowerShell, curl, or local HTTP, without a
browser. The web UI is the operator display; this is the worker surface.

Work items are DERIVED, not a separate database. The source records stay
authoritative:
  - clearance packets remain the authority record,
  - messages remain the communication record,
  - agent events remain activity/context,
  - DONE results remain the outcome record.

Derivation:
  - an inbound ACTIONABLE message thread with no response -> kind "message"
    (a message with intent "chat" is plain conversation, never a work item;
    chat is not work)
  - a CTA packet in clearance_outbox            -> kind "packet"   (claimable)
  - an IN_PROGRESS packet                        -> kind "in_progress"
  - an RFI_PENDING packet                        -> kind "rfi"

Work item ids are stable and deterministic:
  message:<message_id>, packet:<packet_id>:cta, in_progress:<packet_id>,
  rfi:<packet_id>.

Claiming and responding never mutate the packet schema or validator. Claiming a
CTA packet uses the existing clearwright_claim lifecycle for the real packet
move; every claim and response is also written as a durable message in the
related thread, so the original request is never lost. Conversation and claims
grant no authority; the operator decides.

The control plane server imports derive_work_items, claim_work_item, and
respond_work_item for its /api/work-items endpoints, so the CLI and the API
share one implementation.

Exit codes: 0 ok (or listed), 1 refused/invalid, 2 argument error
"""
import argparse
import json
import os
import sys
from datetime import datetime, timezone

import clearwright_message as cwm
import clearwright_claim as cwc
import clearwright_gate as cwg
import clearwright_writer_lock as cwl
import clearwright_identity as cwid

LANES = ["clearance_outbox", "clearance_in_progress",
         "clearance_done", "clearance_failed"]

# --------------------------------------------------------------------------- #
# Command Center Queue Hygiene and Current-State UX (presentation derivation).
#
# Everything below is PRESENTATION-ONLY and additive: it derives display fields
# from durable records already loaded by derive_work_items and writes NOTHING.
# Canonical governance state (_derive_state) is unchanged and authoritative.
# --------------------------------------------------------------------------- #

# Pinned windows (single source of truth; seconds). See the plan, section 2.
RECENT_WINDOW = 24 * 3600     # a completed item stays "current" for one day
STALE_WINDOW = 24 * 3600      # no meaningful activity for a day -> eligible stale
RUNNING_WINDOW = 15 * 60      # activity within 15 min counts as an active runner

# Canonical status groupings used by the presentation derivation.
_TERMINAL = frozenset(("superseded", "done", "closed"))
_ACTIONABLE = frozenset(("open", "claimed", "planning", "verification",
                         "operator_required"))

# Presentation states shown in the current-only default view (section 2).
_DEFAULT_VIEW_STATES = frozenset((
    "needs_operator", "blocked", "running", "waiting_on_claude",
    "waiting_on_operator", "recently_completed"))

# Last-meaningful-activity event classes, lowest number = highest tie-break
# precedence (section 3). The value is (precedence, label).
_EVENT_COMPLETION = (1, "completion")
_EVENT_VERIFICATION = (2, "verification")
_EVENT_COUNCIL = (3, "council")
_EVENT_GATE = (4, "gate")
_EVENT_PROGRESS = (5, "progress")
_EVENT_CLAIM = (6, "claim")
_EVENT_RESPONSE = (7, "response")
_EVENT_EVIDENCE = (8, "evidence")


def _parse_iso(at):
    """Parse a durable `at`/ISO timestamp to an aware UTC datetime, or None.
    Tolerates a trailing Z and variable fractional-second digits."""
    if not at or not isinstance(at, str):
        return None
    s = at.strip()
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    # Normalise fractional seconds to at most 6 digits for fromisoformat.
    if "." in s:
        head, _, tail = s.partition(".")
        frac = ""
        off = ""
        for i, ch in enumerate(tail):
            if ch.isdigit():
                frac += ch
            else:
                off = tail[i:]
                break
        s = head + "." + (frac[:6] or "0") + off
    try:
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _now_dt(now=None):
    """Resolve the injected `now` (ISO string or datetime) to an aware UTC
    datetime. `now` is injected for testability; callers in production pass the
    server's wall clock."""
    if isinstance(now, datetime):
        return now if now.tzinfo else now.replace(tzinfo=timezone.utc)
    parsed = _parse_iso(now) if isinstance(now, str) else None
    return parsed or datetime.now(timezone.utc)


def _age_seconds(activity_at, created_at, now_dt):
    """Seconds since the reference activity (last activity, else creation).
    Missing/unparseable timestamps yield a very large age (treated as old)."""
    ref = _parse_iso(activity_at) or _parse_iso(created_at)
    if ref is None:
        return float("inf")
    return max(0.0, (now_dt - ref).total_seconds())


def _message_event_class(message):
    """Map a bound durable message to a last-activity event class."""
    closure = str(message.get("closure") or "").strip().casefold()
    if closure in cwid.RECOGNIZED_CLOSURES:
        return _EVENT_COMPLETION
    status = message.get("status")
    if status == "claimed":
        return _EVENT_CLAIM
    if status == "responded" or message.get("direction") == "outbound":
        return _EVENT_RESPONSE
    if status == "posted" or message.get("direction") == "internal":
        return _EVENT_PROGRESS
    return _EVENT_EVIDENCE


def _activity_candidates(bound, councils, gate):
    """Every (at, precedence, source_id, label) qualifying-activity candidate
    for one work item, drawn from records already loaded. Read-only."""
    out = []
    for m in bound:
        at = m.get("at")
        if not at:
            continue
        prec, label = _message_event_class(m)
        out.append((at, prec, str(m.get("message_id") or ""), label))
    for c in councils or []:
        at = c.get("created_at")
        if not at:
            continue
        prec, label = _EVENT_COUNCIL
        out.append((at, prec, str(c.get("council_id") or ""), label))
    if gate is not None:
        at = gate.get("resolved_at") or gate.get("created_at")
        if at:
            prec, label = _EVENT_GATE
            out.append((at, prec, str(gate.get("gate_id") or ""), label))
    return out


def _total_order_winner(candidates):
    """Winner under the TOTAL order (parsed-time DESC, event_class_precedence
    ASC, source_id ASC) over (at_str, precedence, source_id, label) candidates.
    Comparison is on PARSED UTC instants, so mixed ISO encodings (trailing Z vs
    +00:00, differing fractional-second widths) order chronologically, not
    lexicographically. Unparseable timestamps are dropped. Returns the winning
    candidate tuple, or None."""
    best = None      # (dt, at_str, precedence, source_id, label)
    for at, prec, sid, label in candidates:
        dt = _parse_iso(at)
        if dt is None:
            continue
        cand = (dt, at, prec, sid, label)
        if best is None:
            best = cand
            continue
        if dt > best[0] or (dt == best[0] and (prec < best[2]
                            or (prec == best[2] and sid < best[3]))):
            best = cand
    return best


def last_activity(bound, councils, gate):
    """Return (last_activity_at, last_activity_event, last_activity_source_id)
    for a work item using the TOTAL order (parsed-time DESC, event_class_
    precedence ASC, source_id ASC). Deterministic and order-independent; the
    returned last_activity_at is the winner's original ISO string. When no
    qualifying activity exists, returns (None, "created", None) -- the labeled
    creation fallback (section 3)."""
    best = _total_order_winner(_activity_candidates(bound, councils, gate))
    if best is None:
        return None, "created", None
    _dt, at_str, _prec, sid, label = best
    return at_str, label, sid


def _has_lifecycle(bound, councils, gate, claim_msg):
    """True when the item shows real work activity (claim/council/gate/progress/
    response/closure) -- the structural signal that separates governed work from
    an inert authority/note record. Read-only."""
    if claim_msg or councils or gate is not None:
        return True
    for m in bound:
        if m.get("closure") or m.get("status") in ("claimed", "posted",
                                                    "responded"):
            return True
    return False


def classify_record(origin, has_lifecycle, status):
    """Presentation `record_class` for a message work item -- STRUCTURAL fields
    only (intent/role/source/lifecycle/status), never message content. Only
    `governed_work` appears in the default governed queue (section 5)."""
    intent = str(origin.get("intent") or "").strip()
    role = str(origin.get("role") or "").strip()
    source = str(origin.get("source") or "").strip()
    if intent == "chat":
        return "chat"
    if has_lifecycle:
        return "governed_work"
    if intent == "request":
        return "governed_work"
    if role == "operator" and source == "operator-ui":
        return "authority"
    if status in _ACTIONABLE:
        return "governed_work"
    return "note"


def _awaiting_operator_reply(bound):
    """True when the latest bound record is an agent->operator ask still awaiting
    the operator. Selects the latest record with the SAME total order as
    last_activity -- (at DESC, event_class_precedence ASC, source_id ASC) -- so
    equal-timestamp records are order-independent (Rule R3).

    NOTE (honest limitation): no current durable writer sets `awaiting_operator`,
    so on today's records this is dormant and `waiting_on_operator` is reserved
    for when an agent->operator question marker is recorded. The operator-required
    case is already covered by `needs_operator` (an unresolved gate / CTA / RFI)."""
    by_id = {}
    cands = []
    for m in bound:
        at = m.get("at")
        if not at:
            continue
        prec, _label = _message_event_class(m)
        mid = str(m.get("message_id") or "")
        by_id[mid] = m
        cands.append((at, prec, mid, _label))
    best = _total_order_winner(cands)   # parsed-time total order (Rule R3)
    if best is None:
        return False
    latest = by_id.get(best[3])
    return (latest is not None
            and latest.get("direction") == "outbound"
            and str(latest.get("status") or "") not in ("responded",)
            and str(latest.get("role") or "") != "operator"
            and bool(latest.get("awaiting_operator")))


def _latest_claim(bound):
    """The latest claim record by the same total order (parsed-time DESC,
    source_id ASC), so claimed_by/claimed_at are deterministic and input-order
    independent even if multiple claim records exist (Rule R3, GPT verify b0)."""
    cands = [(m.get("at"), _EVENT_CLAIM[0], str(m.get("message_id") or ""), m)
             for m in bound if m.get("status") == "claimed" and m.get("at")]
    best = _total_order_winner([(c[0], c[1], c[2], "claim") for c in cands])
    if best is None:
        return None
    return next((c[3] for c in cands if c[2] == best[3]), None)


def runner_state(claimed, claim_at, active_runner, in_council, awaiting_operator,
                 has_gate, status, now_dt):
    """Honest runner state (section 4). Claimed is NOT running. Degrades to
    claimed_idle/stale_or_no_heartbeat/unknown when positive evidence is absent
    -- ClearWright has no heartbeat channel, so this is derived, never asserted."""
    if not claimed:
        return "unowned"
    if active_runner:
        return "active_runner"
    if in_council:
        return "waiting_on_council"
    if has_gate or status == "operator_required" or awaiting_operator:
        return "waiting_on_operator"
    claim_age = _age_seconds(claim_at, claim_at, now_dt)
    if claim_age <= RUNNING_WINDOW:
        return "claimed_idle"
    if claim_age > STALE_WINDOW:
        return "stale_or_no_heartbeat"
    return "claimed_idle"


def presentation_state(signals, now=None):
    """The ONE ordered, total, mutually-exclusive presentation-state function
    (section 1). Pure: identical `signals` -> identical result, no writes.
    `signals` keys: status, kind, needs_operator, blocked, awaiting_operator,
    claimed, active_runner, last_activity_at, created_at."""
    now_dt = _now_dt(now)
    status = signals.get("status")
    age = _age_seconds(signals.get("last_activity_at"),
                       signals.get("created_at"), now_dt)
    # 1-2: terminal states first, so a terminal item is never pulled into
    # Current by needs_operator/blocked.
    if status == "superseded":
        return "superseded"
    if status in ("done", "closed"):
        return "recently_completed" if age <= RECENT_WINDOW else "historical"
    # 3-8: non-terminal only.
    if signals.get("needs_operator"):
        return "needs_operator"
    if signals.get("blocked"):
        return "blocked"
    if signals.get("awaiting_operator"):
        return "waiting_on_operator"
    if signals.get("claimed"):
        if signals.get("active_runner"):
            return "running"
        return "waiting_on_claude" if age <= STALE_WINDOW else "stale"
    if age > STALE_WINDOW:
        return "stale"
    return "waiting_on_claude"


def in_default_view(item):
    """True when a derived item belongs in the current-only default view
    (section 2): a governed work item whose presentation state is current.
    Non-governed records (authority/chat/note) are excluded."""
    if item.get("kind") == "message" and \
            item.get("record_class", "governed_work") != "governed_work":
        return False
    return item.get("presentation_state") in _DEFAULT_VIEW_STATES


# Sort rank for the attention-first default order (section 11).
_SORT_RANK = {
    "needs_operator": 0, "running": 1, "blocked": 2,
    "waiting_on_operator": 3, "waiting_on_claude": 3,
    "recently_completed": 4, "stale": 5, "superseded": 6, "historical": 7,
}

# Filter chips (section 11). Each maps to the presentation states it includes;
# "current" and "all" are handled specially.
_FILTER_STATES = {
    "needs_attention": frozenset(("needs_operator",)),
    "running": frozenset(("running",)),
    "blocked": frozenset(("blocked",)),
    "stale": frozenset(("stale",)),
    "recently_completed": frozenset(("recently_completed",)),
}


def queue_view(items, mode="current", query=""):
    """Pure presentation filter+sort over derived items (sections 2, 11).
    Never mutates items or durable state; returns a new ordered list.
    mode: current | needs_attention | running | blocked | stale |
    recently_completed | all. query matches title or work_item_id."""
    q = (query or "").strip().casefold()

    def matches_query(it):
        if not q:
            return True
        return (q in str(it.get("title") or "").casefold()
                or q in str(it.get("work_item_id") or "").casefold())

    def in_scope(it):
        if mode == "all":
            keep = True
        elif mode == "current":
            keep = in_default_view(it)
        else:
            states = _FILTER_STATES.get(mode)
            if states is None:
                keep = in_default_view(it)
            else:
                # A filter still shows only governed work, except "all".
                governed = (it.get("kind") != "message"
                            or it.get("record_class", "governed_work")
                            == "governed_work")
                keep = governed and it.get("presentation_state") in states
        return keep and matches_query(it)

    selected = [it for it in items if in_scope(it)]
    selected.sort(key=lambda it: (
        _SORT_RANK.get(it.get("presentation_state"), 9),
        _neg_time(it.get("last_activity_at") or it.get("created_at"))))
    return selected


def attention_counts(items):
    """Top-bar counts (section 10): running / operator-required / blocked /
    stale over governed work items. Pure."""
    counts = {"running": 0, "needs_operator": 0, "blocked": 0, "stale": 0}
    for it in items:
        if it.get("kind") == "message" and \
                it.get("record_class", "governed_work") != "governed_work":
            continue
        ps = it.get("presentation_state")
        if ps in counts:
            counts[ps] += 1
    return counts

# The operator acts through the authority channel and is never gated; every
# other actor is an agent whose governed mutations on a gated item are refused.
OPERATOR_ACTOR = "OPERATOR-0001"


def _gate_block(root, work_item_id, actor):
    """Heal any MISSING mandatory escalation gate for this work item first
    (an authoritative capped plan/incident council whose persisted outcome is
    operator_required/hard_gate must always have a durable gate - the healing
    closes the failure window in which gate creation previously crashed), then
    return a refusal dict if an unresolved gate blocks an agent-actor mutation;
    else None. Healing runs BEFORE the operator exemption so the gate
    MATERIALIZES for operator visibility, but the operator is never blocked.
    A healing integrity failure returns the fail-closed payload - the caller
    must refuse, never proceed. Callers pass a validated existing work item."""
    try:
        healed = cwg.heal_escalation_gates(root, work_item_id)
    except cwl.MaintenanceInProgress:
        return {"ok": False, "error": "maintenance_in_progress",
                "error_code": "maintenance_in_progress"}
    if not healed.get("ok"):
        return healed
    if str(actor).strip() == OPERATOR_ACTOR:
        return None
    gate = cwg.active_gate(root, work_item_id)
    if gate is None:
        return None
    return cwg.refusal_payload(gate)


def _read_packets(root):
    """Return a light view of every packet across the lanes:
    {packet_id, status, lane, title, filename, path}. Unreadable files are
    skipped rather than raising."""
    rows = []
    for lane in LANES:
        lane_dir = os.path.join(root, lane)
        if not os.path.isdir(lane_dir):
            continue
        for name in sorted(os.listdir(lane_dir)):
            if not name.endswith(".json"):
                continue
            path = os.path.join(lane_dir, name)
            try:
                with open(path, encoding="utf-8") as fh:
                    packet = json.load(fh)
            except (OSError, ValueError):
                continue
            rows.append({
                "packet_id": packet.get("packet_id"),
                "status": packet.get("status"),
                "lane": lane,
                "title": packet.get("title"),
                "filename": name,
                "path": path,
            })
    return rows


def _work_item(work_item_id, kind, status, next_action, **extra):
    item = {
        "work_item_id": work_item_id,
        "kind": kind,
        "status": status,
        "next_action": next_action,
    }
    for key, value in extra.items():
        if value is not None and value != "":
            item[key] = value
    return item


# Priority ordering for the derived list: actionable packets first, then
# clarification, then in-progress, then message threads (newest first).
_KIND_ORDER = {"packet": 0, "rfi": 1, "in_progress": 2, "message": 3}


# Terminal states are hidden by the default (nonterminal) derivation but always
# resolvable via include="all". operator_required / verification / planning /
# legacy_ambiguity are NONterminal and always listed, even without a claim, so a
# governed item never disappears because it is gated, unclaimed, or malformed.
_TERMINAL_STATES = frozenset(("done", "closed", "superseded"))


def _origin_message_ids(root, messages):
    """The set of message ids that are ACTIONABLE origins under the closed
    identity rule (v2) or the frozen legacy manifest."""
    legacy = cwid.legacy_origin_ids(root)
    origins = set()
    for m in messages:
        mid = m.get("message_id")
        if not mid:
            continue
        if cwid.is_v2(m):
            if cwid.v2_is_origin(m):
                origins.add(mid)
        elif mid in legacy:
            origins.add(mid)
    return origins


def _bound(records, work_item_id, thread_id, single_thread_item):
    """Records bound to this work item: those carrying work_item_id ==
    work_item_id, plus (legacy only) records in a single-item thread that carry
    no work_item_id. Returns (bound_list, had_unbound_in_multi)."""
    bound, unbound_multi = [], False
    for r in records:
        rwid = r.get("work_item_id")
        if rwid == work_item_id:
            bound.append(r)
        elif not rwid and r.get("thread_id") == thread_id:
            if single_thread_item:
                bound.append(r)  # unambiguous legacy: one item in the thread
            else:
                unbound_multi = True  # ambiguous: do not select arbitrarily
    return bound, unbound_multi


def _derive_state(origin, bound, gate, councils, warnings, wid):
    """Deterministic state precedence for one message work item. Appends any
    integrity warnings (which annotate, never override, the derived state)."""
    if origin is None:
        return "malformed"

    closures = [m for m in bound if m.get("closure")]
    recognized = [m for m in closures
                  if str(m.get("closure") or "").strip().casefold()
                  in cwid.RECOGNIZED_CLOSURES]
    unknown = [m for m in closures if m not in recognized]
    for m in unknown:
        warnings.append({"code": "unknown_closure_value", "work_item_id": wid,
                         "record_id": m.get("message_id"),
                         "value": m.get("closure")})
    if len(recognized) > 1:
        warnings.append({"code": "conflicting_closures", "work_item_id": wid,
                         "record_ids": [m.get("message_id") for m in recognized]})

    claims = [m for m in bound if m.get("status") == "claimed"]
    if len(claims) > 1:
        warnings.append({"code": "duplicate_claim", "work_item_id": wid,
                         "record_ids": [m.get("message_id") for m in claims]})

    if recognized:
        winner = max(recognized, key=lambda m: m.get("at") or "")
        cval = str(winner.get("closure") or "").strip().casefold()
        reason = str((winner.get("closure_meta") or {}).get("reason")
                     or winner.get("message") or "").casefold()
        disp = str((winner.get("closure_meta") or {}).get("disposition") or "").casefold()
        superseded = disp == "superseded" or "supersede" in reason
        if gate is not None:
            warnings.append({"code": "gate_open_at_closure", "work_item_id": wid,
                             "gate_id": gate.get("gate_id")})
        if cval == "closed_by_operator":
            return "superseded" if superseded else "closed"
        return "done"

    if gate is not None:
        return "operator_required"

    # A completed/answered item is terminal DONE. The completion path records an
    # outbound "responded" message (not necessarily a closure field), and it
    # takes precedence over a stale council outcome -- e.g. a plan council that
    # ended operator_required but was later resolved and the item verified and
    # completed must read DONE, not "planning".
    responses = [m for m in bound if m.get("direction") == "outbound"
                 or m.get("status") == "responded"]
    if responses:
        return "done"

    verify_c = next((c for c in councils if c.get("phase") == "verify"), None)
    if verify_c is not None and verify_c.get("outcome") != "agreement_threshold_met":
        return "verification"
    plan_c = next((c for c in councils if c.get("phase") == "plan"), None)
    if plan_c is not None and plan_c.get("outcome") != "agreement_threshold_met":
        return "planning"

    return "claimed" if claims else "open"


def _council_in_flight(councils):
    """True when any bound council has no terminal agreement outcome yet."""
    for c in councils or []:
        if c.get("outcome") != "agreement_threshold_met":
            return True
    return False


def derive_work_items(root, include="nonterminal", active_run=None, now=None):
    """Return the derived work-item list. include="nonterminal" (default) lists
    open/claimed/planning/operator_required/verification/legacy_ambiguity/
    malformed items; include="all" also lists done/closed/superseded with their
    true state. Nothing is written. Message work items are MESSAGE-SCOPED: every
    actionable message derives its own item, so two actionable messages in one
    thread remain fully independent.

    Each item additionally carries PRESENTATION-ONLY derived fields (no durable
    write): last_activity_at/last_activity_event/last_activity_source_id (section
    3), runner_state (section 4), record_class (section 5, message items), and
    presentation_state (section 1). `active_run` is an OPTIONAL read-only snapshot
    (dict keyed by work_item_id or thread_id) injected by the server; when None
    the derivation is fully deterministic from durable records alone and never
    asserts a false active runner. `now` is injected for testability."""
    import clearwright_review_council as cwrc
    now_dt = _now_dt(now)
    active_run = active_run or {}
    items = []
    warnings = []
    for p in _read_packets(root):
        pid, status, lane = p["packet_id"], p["status"], p["lane"]
        if not pid:
            continue
        if lane == "clearance_outbox" and status == "CTA":
            items.append(_work_item(
                "packet:{}:cta".format(pid), "packet", "open", "claim",
                packet_id=pid, title=p["title"], summary=p["title"],
                record_class="governed_work", presentation_state="needs_operator",
                runner_state="waiting_on_operator", last_activity_event="created"))
        elif lane == "clearance_outbox" and status == "RFI_PENDING":
            items.append(_work_item(
                "rfi:{}".format(pid), "rfi", "open", "answer clarification",
                packet_id=pid, title=p["title"], summary=p["title"],
                record_class="governed_work", presentation_state="needs_operator",
                runner_state="waiting_on_operator", last_activity_event="created"))
        elif lane == "clearance_in_progress" and status == "IN_PROGRESS":
            items.append(_work_item(
                "in_progress:{}".format(pid), "in_progress", "open",
                "post progress or complete",
                packet_id=pid, title=p["title"], summary=p["title"],
                record_class="governed_work",
                presentation_state="waiting_on_claude",
                runner_state="unknown", last_activity_event="created"))

    messages = cwm.read_messages(root)
    by_id = {m.get("message_id"): m for m in messages if m.get("message_id")}
    origins = _origin_message_ids(root, messages)

    # How many origins share each thread -> single-item threads bind unbound
    # legacy records; multi-item threads flag legacy ambiguity.
    thread_origin_count = {}
    for mid in origins:
        tid = by_id[mid].get("thread_id") or "thr-unknown"
        thread_origin_count[tid] = thread_origin_count.get(tid, 0) + 1

    all_councils = cwrc.list_councils(root)

    for mid in origins:
        origin = by_id[mid]
        wid = cwid.work_item_id_for(mid)
        tid = origin.get("thread_id") or "thr-unknown"
        single = thread_origin_count.get(tid, 0) <= 1
        bound, unbound_multi = _bound(messages, wid, tid, single)
        if unbound_multi:
            warnings.append({"code": "legacy_ambiguity", "work_item_id": wid,
                             "thread_id": tid})
        try:
            gate = cwg.active_gate(root, wid)
        except Exception:
            gate = None
        councils = [c for c in all_councils if c.get("work_item_id") == wid]
        # Legacy fallback: a single-item thread may hold thread-bound councils.
        if not councils and single:
            councils = [c for c in all_councils if c.get("thread_id") == tid]
        state = _derive_state(origin, bound, gate, councils, warnings, wid)

        claim_msg = _latest_claim(bound)   # deterministic latest claim (Rule R3)

        # ---- presentation-only derived fields (no durable write) -------------
        # The origin message IS the creation event, not post-creation activity;
        # exclude it so a brand-new, untouched request falls to the labeled
        # "created" fallback rather than showing its own creation as "activity".
        activity_bound = [m for m in bound if m.get("message_id") != mid]
        la_at, la_event, la_sid = last_activity(activity_bound, councils, gate)
        has_lifecycle = _has_lifecycle(activity_bound, councils, gate, claim_msg)
        record_class = classify_record(origin, has_lifecycle, state)
        claimed = claim_msg is not None
        claim_at = (claim_msg or {}).get("at")
        # "running" requires positive evidence of ACTIVE work -- a fresh CLAIM is
        # NOT running (the plan's core "claimed != running" honesty). Running
        # evidence = a non-claim activity record (progress/council/verify/
        # completion/gate) within RUNNING_WINDOW, OR the optional injected
        # active_run snapshot. The claim alone never sets active_runner.
        run_bound = [m for m in activity_bound if m.get("status") != "claimed"]
        run_at, _run_event, _run_sid = last_activity(run_bound, councils, gate)
        run_age = _age_seconds(run_at, None, now_dt)
        active = claimed and run_at is not None and run_age <= RUNNING_WINDOW
        if active_run.get(wid) or active_run.get(tid):
            active = True
        awaiting_op = _awaiting_operator_reply(bound)
        wid_warned = any(w.get("work_item_id") == wid for w in warnings)
        signals = {
            "status": state, "kind": "message",
            "needs_operator": state == "operator_required",
            "blocked": state == "malformed" or wid_warned,
            "awaiting_operator": awaiting_op,
            "claimed": claimed, "active_runner": active,
            "last_activity_at": la_at, "created_at": origin.get("at"),
        }
        pstate = presentation_state(signals, now_dt)
        rstate = runner_state(
            claimed, claim_at, active, _council_in_flight(councils),
            awaiting_op, gate is not None, state, now_dt)

        items.append(_work_item(
            wid, "message", state, _next_action_for(state),
            thread_id=tid, packet_id=origin.get("packet_id"),
            actor=origin.get("actor"), source=origin.get("source"),
            title=origin.get("message"), summary=origin.get("message"),
            created_at=origin.get("at"),
            claimed_by=(claim_msg or {}).get("actor"),
            claimed_at=claim_at,
            last_activity_at=la_at, last_activity_event=la_event,
            last_activity_source_id=la_sid, runner_state=rstate,
            record_class=record_class, presentation_state=pstate))

    if include != "all":
        items = [it for it in items if it["status"] not in _TERMINAL_STATES]

    items.sort(key=lambda it: (_KIND_ORDER.get(it["kind"], 9),
                               _neg_time(it.get("created_at"))))
    return items


_NEXT_ACTION = {
    "open": "respond", "claimed": "respond", "planning": "continue plan council",
    "operator_required": "resolve gate (operator authority required)",
    "verification": "run or finish verification", "done": "none (terminal)",
    "closed": "none (closed by operator)", "superseded": "none (superseded)",
    "malformed": "inspect origin record",
}


def _next_action_for(state):
    return _NEXT_ACTION.get(state, "respond")


def integrity_warnings(root):
    """Machine-readable derived-queue integrity defects. Includes any
    legacy-manifest-missing condition plus per-item collision warnings."""
    warnings = []
    ms = cwid.manifest_status(root)
    if ms:
        warnings.append({"code": ms})
    # Re-run derivation over ALL items to collect item-level warnings.
    import clearwright_review_council as cwrc
    messages = cwm.read_messages(root)
    by_id = {m.get("message_id"): m for m in messages if m.get("message_id")}
    origins = _origin_message_ids(root, messages)
    thread_origin_count = {}
    for mid in origins:
        tid = by_id[mid].get("thread_id") or "thr-unknown"
        thread_origin_count[tid] = thread_origin_count.get(tid, 0) + 1
    all_councils = cwrc.list_councils(root)
    for mid in origins:
        origin = by_id[mid]
        wid = cwid.work_item_id_for(mid)
        tid = origin.get("thread_id") or "thr-unknown"
        single = thread_origin_count.get(tid, 0) <= 1
        bound, unbound_multi = _bound(messages, wid, tid, single)
        if unbound_multi:
            warnings.append({"code": "legacy_ambiguity", "work_item_id": wid,
                             "thread_id": tid})
        try:
            gate = cwg.active_gate(root, wid)
        except Exception:
            gate = None
        councils = [c for c in all_councils if c.get("work_item_id") == wid]
        if not councils and single:
            councils = [c for c in all_councils if c.get("thread_id") == tid]
        _derive_state(origin, bound, gate, councils, warnings, wid)
    return warnings


def _neg_time(at):
    # Sort message items newest-first; empty timestamps sort last.
    return "" if not at else "".join(chr(255 - ord(c)) if ord(c) < 255 else c for c in at)


def parse_work_item_id(work_item_id):
    """Return (kind, ref) for a work item id, or (None, None) if unrecognized.
    ref is the message_id for messages and the packet_id for packet kinds."""
    wid = str(work_item_id or "")
    if wid.startswith("message:"):
        return "message", wid[len("message:"):]
    if wid.startswith("packet:") and wid.endswith(":cta"):
        return "packet", wid[len("packet:"):-len(":cta")]
    if wid.startswith("in_progress:"):
        return "in_progress", wid[len("in_progress:"):]
    if wid.startswith("rfi:"):
        return "rfi", wid[len("rfi:"):]
    return None, None


def _resolve_target(root, work_item_id):
    """Return (thread_id, packet_id) for a work item so a claim/response lands
    in the right thread. A message reuses its own thread; a packet reuses an
    existing packet thread if one exists, otherwise a new thread is started."""
    kind, ref = parse_work_item_id(work_item_id)
    if kind == "message":
        for m in cwm.read_messages(root):
            if m.get("message_id") == ref:
                return m.get("thread_id"), m.get("packet_id")
        return None, None
    if kind in ("packet", "in_progress", "rfi"):
        packet_id = ref
        for m in cwm.read_messages(root, packet_id=packet_id):
            if m.get("thread_id"):
                return m.get("thread_id"), packet_id
        return None, packet_id
    return None, None


def _find_packet_path(root, packet_id, lane=None):
    for p in _read_packets(root):
        if p["packet_id"] == packet_id and (lane is None or p["lane"] == lane):
            return p["path"]
    return None


def claim_work_item(root, work_item_id, actor, role=cwm.DEFAULT_ROLE,
                    source="local-http"):
    """Claim a work item. For a CTA packet, perform the real packet claim
    through the existing clearwright_claim lifecycle, then record a durable
    claim message. For message/rfi/in_progress items, record a durable claim
    message only (the packet lifecycle is unchanged). The original request is
    never lost."""
    if not actor or not str(actor).strip():
        return {"ok": False, "error": "actor is required and must be non-empty"}
    kind, ref = parse_work_item_id(work_item_id)
    if kind is None:
        return {"ok": False, "error": "unrecognized work_item_id"}
    # Existence FIRST: an unknown id performs no healing, creates no gate, and
    # writes nothing (the no-write-on-unknown-id contract). The resolution
    # reads are read-only and side-effect-free.
    if find_work_item(root, work_item_id) is None:
        return {"ok": False, "error": "work_item_not_found", "work_item_id": work_item_id}
    blocked = _gate_block(root, work_item_id, actor)
    if blocked is not None:
        return blocked

    packet_claimed = None
    if kind == "packet":
        path = _find_packet_path(root, ref, lane="clearance_outbox")
        if path is None:
            return {"ok": False, "error": "CTA packet {!r} not found in the outbox".format(ref)}
        code = cwc.claim(path, claimant=str(actor).strip())
        if code != 0:
            return {"ok": False, "error": "claim tool refused the packet claim (exit {})".format(code)}
        packet_claimed = ref

    try:
        cwid.ensure_migrated(root)
    except cwl.MaintenanceInProgress:
        return {"ok": False, "error": "maintenance_in_progress",
                "error_code": "maintenance_in_progress"}
    thread_id, packet_id = _resolve_target(root, work_item_id)
    note = ("claimed CTA packet " + ref) if kind == "packet" else ("claimed work item " + str(work_item_id))
    try:
        message = cwm.build_message(
            actor, note, role=role, packet_id=packet_id, thread_id=thread_id,
            direction="internal", status="claimed", source=source,
            work_item_id=work_item_id)
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}
    try:
        cwm.write_message(root, message)
    except cwl.MaintenanceInProgress:
        return {"ok": False, "error": "maintenance_in_progress"}
    except OSError as exc:
        return {"ok": False, "error": str(exc)}
    result = {"ok": True, "work_item_id": work_item_id, "kind": kind,
              "message": message, "thread_id": message["thread_id"]}
    if packet_claimed:
        result["packet_claimed"] = packet_claimed
    return result


def respond_work_item(root, work_item_id, actor, message, role=cwm.DEFAULT_ROLE,
                      source="local-http"):
    """Respond to a work item by writing a durable response message in the
    related thread. The packet status is not altered here; the operator uses the
    existing lifecycle tools for that."""
    if parse_work_item_id(work_item_id)[0] is None:
        return {"ok": False, "error": "unrecognized work_item_id"}
    # Existence FIRST (no healing, no gate, no write on an unknown id).
    if find_work_item(root, work_item_id) is None:
        return {"ok": False, "error": "work_item_not_found", "work_item_id": work_item_id}
    blocked = _gate_block(root, work_item_id, actor)
    if blocked is not None:
        return blocked
    try:
        cwid.ensure_migrated(root)
    except cwl.MaintenanceInProgress:
        return {"ok": False, "error": "maintenance_in_progress",
                "error_code": "maintenance_in_progress"}
    thread_id, packet_id = _resolve_target(root, work_item_id)
    try:
        msg = cwm.build_message(
            actor, message, role=role, packet_id=packet_id, thread_id=thread_id,
            direction="outbound", status="responded", source=source,
            work_item_id=work_item_id)
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}
    try:
        cwm.write_message(root, msg)
    except cwl.MaintenanceInProgress:
        return {"ok": False, "error": "maintenance_in_progress"}
    except OSError as exc:
        return {"ok": False, "error": str(exc)}
    return {"ok": True, "work_item_id": work_item_id, "message": msg,
            "thread_id": msg["thread_id"]}


def progress_work_item(root, work_item_id, actor, message, role=cwm.DEFAULT_ROLE,
                       source="local-http"):
    """Post a progress note on a work item as a durable internal message in the
    related thread. Progress is working context, not a final answer, so the work
    item stays open."""
    if parse_work_item_id(work_item_id)[0] is None:
        return {"ok": False, "error": "unrecognized work_item_id"}
    # Existence FIRST (no healing, no gate, no write on an unknown id).
    if find_work_item(root, work_item_id) is None:
        return {"ok": False, "error": "work_item_not_found", "work_item_id": work_item_id}
    blocked = _gate_block(root, work_item_id, actor)
    if blocked is not None:
        return blocked
    try:
        cwid.ensure_migrated(root)
    except cwl.MaintenanceInProgress:
        return {"ok": False, "error": "maintenance_in_progress",
                "error_code": "maintenance_in_progress"}
    thread_id, packet_id = _resolve_target(root, work_item_id)
    try:
        msg = cwm.build_message(
            actor, message, role=role, packet_id=packet_id, thread_id=thread_id,
            direction="internal", status="posted", source=source,
            work_item_id=work_item_id)
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}
    try:
        cwm.write_message(root, msg)
    except cwl.MaintenanceInProgress:
        return {"ok": False, "error": "maintenance_in_progress"}
    except OSError as exc:
        return {"ok": False, "error": str(exc)}
    return {"ok": True, "work_item_id": work_item_id, "message": msg,
            "thread_id": msg["thread_id"]}


def find_work_item(root, work_item_id):
    """Return the work item matching work_item_id, or None. Resolves over ALL
    derived items (terminal, malformed, legacy_ambiguity included) so gated,
    closed, and ambiguous items remain discoverable rather than vanishing from
    lookup.

    Packet-kind ids resolve through the derived list; a message id resolves only
    when it is an ACTUAL origin (an actionable request under the closed identity
    rule or the frozen legacy manifest). A non-origin message -- a chat, an
    authority record, or a reviewer post -- is deliberately NOT resolvable, so a
    claim/response/task-state can never bind to a message that derive_work_items
    correctly excludes."""
    for item in derive_work_items(root, include="all"):
        if item.get("work_item_id") == work_item_id:
            return item
    mid = cwid.message_id_of(work_item_id)
    if mid:
        messages = cwm.read_messages(root)
        origins = _origin_message_ids(root, messages)
        if mid in origins:
            origin = next((m for m in messages if m.get("message_id") == mid), None)
            if origin is not None:
                return _work_item(work_item_id, "message", "open", "respond",
                                  thread_id=origin.get("thread_id"),
                                  title=origin.get("message"),
                                  created_at=origin.get("at"))
    return None


def worker_status(root):
    """A small read-only worker view: work-item counts by status and kind,
    packet counts by lane, and recent message and agent-event counts. Shared by
    the worker CLI (status) and GET /api/worker-status so both agree."""
    items = derive_work_items(root)
    packets = _read_packets(root)
    lanes = {}
    for p in packets:
        lanes[p["lane"]] = lanes.get(p["lane"], 0) + 1
    kinds = {}
    for it in items:
        kinds[it["kind"]] = kinds.get(it["kind"], 0) + 1
    messages = cwm.read_messages(root)
    try:
        import clearwright_agent_event as cwae
        events = cwae.read_events(root)
    except Exception:
        events = []
    return {
        "work_items_total": len(items),
        "work_items_open": len([i for i in items if i.get("status") == "open"]),
        "work_items_claimed": len([i for i in items if i.get("status") == "claimed"]),
        "work_items_by_kind": kinds,
        "packets_by_lane": lanes,
        "messages_total": len(messages),
        "agent_events_total": len(events),
        "next_work_item_id": items[0]["work_item_id"] if items else None,
    }


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #

def _require_queue(root):
    if not os.path.isdir(root):
        print("REFUSED: queue root {!r} does not exist".format(root), file=sys.stderr)
        return False
    return True


def cli_list(args):
    if not _require_queue(args.queue_root):
        return 1
    items = derive_work_items(args.queue_root)
    if args.kind:
        items = [it for it in items if it["kind"] == args.kind]
    print(json.dumps(items, indent=2))
    return 0


def cli_claim(args):
    if not _require_queue(args.queue_root):
        return 1
    result = claim_work_item(args.queue_root, args.work_item_id, args.actor, role=args.role)
    if not result["ok"]:
        print("REFUSED: {}".format(result["error"]), file=sys.stderr)
        return 1
    print("CLAIMED: {} ({})".format(result["work_item_id"], result["message"]["message_id"]))
    return 0


def cli_respond(args):
    if not _require_queue(args.queue_root):
        return 1
    result = respond_work_item(args.queue_root, args.work_item_id, args.actor,
                               args.message, role=args.role)
    if not result["ok"]:
        print("REFUSED: {}".format(result["error"]), file=sys.stderr)
        return 1
    print("RESPONDED: {} ({} in thread {})".format(
        result["work_item_id"], result["message"]["message_id"], result["thread_id"]))
    return 0


def build_parser():
    parser = argparse.ArgumentParser(
        prog="clearwright_work",
        description=(
            "List, claim, and respond to work items derived from a clearance "
            "queue's durable state (messages and packets). This is the local "
            "worker surface for agents, tools, and scripts (CLI / curl / local "
            "HTTP); the web UI is the operator display. Work items grant no "
            "authority; the operator decides.\n\n"
            "Exit codes:\n"
            "  0  ok (or listed)\n"
            "  1  refused or invalid\n"
            "  2  argument error"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    subs = parser.add_subparsers(dest="command", required=True)

    p_list = subs.add_parser("list", help="List open work items.")
    p_list.add_argument("queue_root", help="Clearance queue root directory.")
    p_list.add_argument("--kind", default=None,
                        choices=["message", "packet", "in_progress", "rfi"],
                        help="Only work items of this kind.")
    p_list.set_defaults(func=cli_list)

    p_claim = subs.add_parser("claim", help="Claim a work item.")
    p_claim.add_argument("queue_root", help="Clearance queue root directory.")
    p_claim.add_argument("--work-item-id", required=True, metavar="ID",
                         help="Required. The work item id from list.")
    p_claim.add_argument("--actor", required=True, metavar="ID",
                         help="Required. Who is claiming (for example claude).")
    p_claim.add_argument("--role", default=cwm.DEFAULT_ROLE, metavar="ROLE",
                         help="Actor role (default: {}).".format(cwm.DEFAULT_ROLE))
    p_claim.set_defaults(func=cli_claim)

    p_respond = subs.add_parser("respond", help="Respond to a work item.")
    p_respond.add_argument("queue_root", help="Clearance queue root directory.")
    p_respond.add_argument("--work-item-id", required=True, metavar="ID",
                           help="Required. The work item id from list.")
    p_respond.add_argument("--actor", required=True, metavar="ID",
                           help="Required. Who is responding (for example claude).")
    p_respond.add_argument("--message", required=True, metavar="TEXT",
                           help="Required. The response text.")
    p_respond.add_argument("--role", default=cwm.DEFAULT_ROLE, metavar="ROLE",
                           help="Actor role (default: {}).".format(cwm.DEFAULT_ROLE))
    p_respond.set_defaults(func=cli_respond)

    return parser


def main():
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            try:
                reconfigure(encoding="utf-8", errors="backslashreplace")
            except (ValueError, OSError):
                pass
    args = build_parser().parse_args()
    sys.exit(args.func(args))


if __name__ == "__main__":
    main()
