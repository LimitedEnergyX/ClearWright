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

import clearwright_message as cwm
import clearwright_claim as cwc
import clearwright_gate as cwg
import clearwright_writer_lock as cwl
import clearwright_identity as cwid

LANES = ["clearance_outbox", "clearance_in_progress",
         "clearance_done", "clearance_failed"]

# The operator acts through the authority channel and is never gated; every
# other actor is an agent whose governed mutations on a gated item are refused.
OPERATOR_ACTOR = "OPERATOR-0001"


def _gate_block(root, work_item_id, actor):
    """Return a refusal dict if an unresolved gate blocks an agent-actor
    mutation on this work item; else None. The operator is never blocked."""
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

    verify_c = next((c for c in councils if c.get("phase") == "verify"), None)
    if verify_c is not None and verify_c.get("outcome") != "agreement_threshold_met":
        return "verification"
    plan_c = next((c for c in councils if c.get("phase") == "plan"), None)
    if plan_c is not None and plan_c.get("outcome") != "agreement_threshold_met":
        return "planning"

    responses = [m for m in bound if m.get("direction") == "outbound"
                 or m.get("status") == "responded"]
    if responses:
        return "done"
    return "claimed" if claims else "open"


def derive_work_items(root, include="nonterminal"):
    """Return the derived work-item list. include="nonterminal" (default) lists
    open/claimed/planning/operator_required/verification/legacy_ambiguity/
    malformed items; include="all" also lists done/closed/superseded with their
    true state. Nothing is written. Message work items are MESSAGE-SCOPED: every
    actionable message derives its own item, so two actionable messages in one
    thread remain fully independent."""
    import clearwright_review_council as cwrc
    items = []
    warnings = []
    for p in _read_packets(root):
        pid, status, lane = p["packet_id"], p["status"], p["lane"]
        if not pid:
            continue
        if lane == "clearance_outbox" and status == "CTA":
            items.append(_work_item(
                "packet:{}:cta".format(pid), "packet", "open", "claim",
                packet_id=pid, title=p["title"], summary=p["title"]))
        elif lane == "clearance_outbox" and status == "RFI_PENDING":
            items.append(_work_item(
                "rfi:{}".format(pid), "rfi", "open", "answer clarification",
                packet_id=pid, title=p["title"], summary=p["title"]))
        elif lane == "clearance_in_progress" and status == "IN_PROGRESS":
            items.append(_work_item(
                "in_progress:{}".format(pid), "in_progress", "open",
                "post progress or complete",
                packet_id=pid, title=p["title"], summary=p["title"]))

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

        claim_msg = next((m for m in bound if m.get("status") == "claimed"), None)
        items.append(_work_item(
            wid, "message", state, _next_action_for(state),
            thread_id=tid, packet_id=origin.get("packet_id"),
            actor=origin.get("actor"), source=origin.get("source"),
            title=origin.get("message"), summary=origin.get("message"),
            created_at=origin.get("at"),
            claimed_by=(claim_msg or {}).get("actor"),
            claimed_at=(claim_msg or {}).get("at")))

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
    blocked = _gate_block(root, work_item_id, actor)
    if blocked is not None:
        return blocked
    if find_work_item(root, work_item_id) is None:
        return {"ok": False, "error": "work_item_not_found", "work_item_id": work_item_id}

    packet_claimed = None
    if kind == "packet":
        path = _find_packet_path(root, ref, lane="clearance_outbox")
        if path is None:
            return {"ok": False, "error": "CTA packet {!r} not found in the outbox".format(ref)}
        code = cwc.claim(path, claimant=str(actor).strip())
        if code != 0:
            return {"ok": False, "error": "claim tool refused the packet claim (exit {})".format(code)}
        packet_claimed = ref

    cwid.ensure_migrated(root)
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
    blocked = _gate_block(root, work_item_id, actor)
    if blocked is not None:
        return blocked
    if find_work_item(root, work_item_id) is None:
        return {"ok": False, "error": "work_item_not_found", "work_item_id": work_item_id}
    cwid.ensure_migrated(root)
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
    blocked = _gate_block(root, work_item_id, actor)
    if blocked is not None:
        return blocked
    if find_work_item(root, work_item_id) is None:
        return {"ok": False, "error": "work_item_not_found", "work_item_id": work_item_id}
    cwid.ensure_migrated(root)
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
    items (terminal, malformed, legacy_ambiguity included) so gated, closed, and
    ambiguous items remain discoverable rather than vanishing from lookup."""
    for item in derive_work_items(root, include="all"):
        if item.get("work_item_id") == work_item_id:
            return item
    # A message work item whose origin message exists on disk but is not (yet)
    # an actionable origin still resolves for binding purposes.
    mid = cwid.message_id_of(work_item_id)
    if mid:
        for m in cwm.read_messages(root):
            if m.get("message_id") == mid:
                return _work_item(work_item_id, "message", "open", "respond",
                                  thread_id=m.get("thread_id"),
                                  title=m.get("message"), created_at=m.get("at"))
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
