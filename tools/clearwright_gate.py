#!/usr/bin/env python3
"""
tools/clearwright_gate.py: plan-level gate enforcement for ClearWright.

When a plan or incident Review Council bound to a work item ends
``operator_required`` or ``hard_gate``, that work item gets a durable, unresolved
GATE. While the gate is unresolved the governed workflow is fail-closed: progress,
council rounds, verification, and completion for that work item are refused
(EXIT_GATE / HTTP 409). Proceeding requires a durable inbound operator message,
created AFTER the gate, that names the work item or council and explicitly
authorizes proceeding. The original task request never satisfies a later gate.

Honest boundary: ClearWright can stop everything it governs, but it cannot
physically prevent a process running under the same OS user from editing files
outside ClearWright. This module enforces the governed workflow; it is not an OS
security control.

This module is imported by the wrapper, the work functions, the message writer,
the council engine, and the server so every durable write path shares ONE gate
check. It writes only under ``<root>/gates/`` and never mutates a packet, a
message body, or a council record.
"""
import hashlib
import json
import os
import re
from datetime import datetime, timezone

import clearwright_writer_lock as cwl

GATES_DIR = "gates"
DISPOSITIONS = ("unresolved", "resolved", "closed_unresolved")

# The authority channel and read-derived reflections are exempt from gate
# refusal on the message path: an inbound operator message is how a gate gets
# resolved, and these sources never advance derived work-item state.
EXEMPT_MESSAGE_SOURCES = ("use-cw-gate", "use-cw-annotation", "use-cw-summary")

# Intent-safe authorization phrases. A match is rejected when negated within a
# short preceding window or when it appears inside quotation marks (see
# ``phrase_authorizes``). These bind grant-proceed; closure has its own set.
PROCEED_PHRASES = (
    "authorize proceeding", "authorized to proceed", "authorize continuation",
    "proceed despite", "resume execution despite",
)
CLOSE_PHRASES = (
    "authorize closure", "authorize closing", "close work item",
    "accept without verification",
)
_NEGATIONS = ("not", "no", "never", "do not", "don't", "cannot", "won't")

# Work-item / council id token charset. An id "matches" only when it appears
# bounded by string start/end or a character OUTSIDE this set, so a token never
# matches a substring of a longer id.
_ID_CHARS = r"A-Za-z0-9:_-"


class GateError(Exception):
    """Raised by ``require_open`` when an unresolved gate blocks a mutation.

    ``payload`` is the machine-readable refusal body (also used verbatim as the
    HTTP 409 response body)."""

    def __init__(self, payload):
        super().__init__(payload.get("error", "unresolved_gate"))
        self.payload = payload


# --------------------------------------------------------------------------- #
# Canonical subject
# --------------------------------------------------------------------------- #

def canonical_subject(work_item_id):
    """Return the single canonical gate subject for a work item id.

    A message item persists as ``message:<mid>``. Every packet-derived alias
    (``packet:<pid>:cta``, ``in_progress:<pid>``, ``rfi:<pid>``, a bare
    ``packet:<pid>``, or a bare packet id) resolves to ``packet:<pid>`` so a
    gate created against one alias blocks mutation through every alias. An
    unrecognized value is returned unchanged (treated as its own subject)."""
    wid = str(work_item_id or "").strip()
    if not wid:
        return ""
    if wid.startswith("message:"):
        return wid
    if wid.startswith("packet:") and wid.endswith(":cta"):
        return "packet:" + wid[len("packet:"):-len(":cta")]
    if wid.startswith("in_progress:"):
        return "packet:" + wid[len("in_progress:"):]
    if wid.startswith("rfi:"):
        return "packet:" + wid[len("rfi:"):]
    if wid.startswith("packet:"):
        return "packet:" + wid[len("packet:"):]
    # A bare packet id (no scheme) is treated as a packet subject.
    if ":" not in wid:
        return "packet:" + wid
    return wid


def _subject_path(root, subject):
    digest = hashlib.sha256(subject.encode("utf-8")).hexdigest()
    return os.path.join(root, GATES_DIR, digest + ".json")


# --------------------------------------------------------------------------- #
# Store (atomic; append-only gate list; stored-subject assertion on load)
# --------------------------------------------------------------------------- #

def _now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _load_record(root, subject):
    path = _subject_path(root, subject)
    if not os.path.isfile(path):
        return {"subject": subject, "gates": []}
    with open(path, encoding="utf-8") as fh:
        record = json.load(fh)
    stored = record.get("subject")
    if stored != subject:
        # Full-sha256 filenames make this practically unreachable; assert it
        # anyway so a hash collision can never silently serve the wrong gate.
        raise GateError({"ok": False, "error": "gate_subject_mismatch",
                         "expected": subject, "stored": stored})
    record.setdefault("gates", [])
    return record


def _write_record(root, record):
    """Raises clearwright_writer_lock.MaintenanceInProgress while an archive
    operation holds exclusivity over this queue root."""
    directory = os.path.join(root, GATES_DIR)
    os.makedirs(directory, exist_ok=True)
    path = _subject_path(root, record["subject"])
    tmp = path + ".tmp"
    with cwl.write_token(root, purpose="gate"):
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(record, fh, indent=2)
            fh.write("\n")
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
        _fsync_dir(directory)


def _fsync_dir(directory):
    # POSIX directory-entry durability; a no-op where not supported (Windows
    # relies on NTFS metadata journaling, documented in the runbook).
    try:
        fd = os.open(directory, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)


def load_gates(root, work_item_id):
    """Return the gate list for a work item's canonical subject (oldest first)."""
    subject = canonical_subject(work_item_id)
    if not subject:
        return []
    return _load_record(root, subject).get("gates", [])


def active_gate(root, work_item_id):
    """Return the current unresolved gate for the subject, or None."""
    for gate in load_gates(root, work_item_id):
        if gate.get("disposition") == "unresolved":
            return gate
    return None


# --------------------------------------------------------------------------- #
# Enforcement
# --------------------------------------------------------------------------- #

def refusal_payload(gate):
    return {
        "ok": False,
        "error": "unresolved_gate",
        "exit_equivalent": 9,
        "work_item_id": gate.get("work_item_id_as_seen"),
        "subject": gate.get("subject"),
        "gate_id": gate.get("gate_id"),
        "council_id": gate.get("council_id"),
        "remediation": [
            "This work item has an unresolved plan-level gate; the governed "
            "workflow is stopped.",
            "To proceed, the operator posts a durable inbound message (after "
            "the gate) naming the work item or council {} and authorizing "
            "proceeding, then runs grant-proceed.".format(gate.get("council_id")),
            "To accept unverified work instead, the operator authorizes and "
            "runs close.",
        ],
    }


def require_open(root, work_item_id):
    """Raise GateError if the work item's canonical subject has an unresolved
    gate. Call this from every governed durable-write path before mutating."""
    gate = active_gate(root, work_item_id)
    if gate is not None:
        raise GateError(refusal_payload(gate))


def is_blocked(root, work_item_id):
    """Non-raising check: True if an unresolved gate blocks this work item."""
    return active_gate(root, work_item_id) is not None


# --------------------------------------------------------------------------- #
# Creation and transitions (the only disposition writers)
# --------------------------------------------------------------------------- #

# Gate escalation-identity version. Bumped ONLY when escalation semantics
# change, so a changed governing rule can require fresh authority even when the
# council, phase, outcome, round count, and scope are unchanged.
GATE_POLICY_VERSION = 1


def _dedup_key(subject, council_id, phase, outcome, rounds, scope_hash,
               policy_version=GATE_POLICY_VERSION):
    """Stable escalation identity over a versioned canonical-JSON serialization
    (delimiter collisions impossible by construction). Case-insensitive fields
    are casefolded; rounds is an integer; scope defaults to 'none'."""
    import unicodedata

    def norm(v, fold=False):
        s = unicodedata.normalize("NFC", str(v if v is not None else ""))
        return s.casefold() if fold else s

    payload = {
        "v": int(policy_version),
        "subject": norm(subject),
        "council": norm(council_id),
        "phase": norm(phase, fold=True),
        "outcome": norm(outcome, fold=True),
        "rounds": int(rounds or 0),
        "scope": norm(scope_hash or "none"),
    }
    blob = json.dumps(payload, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()


def ensure_gate(root, work_item_id, council_id, phase, outcome,
                substantive_round_count, scope_hash, invocation_id):
    """Idempotent gate creation keyed on the unique escalation event. Runs the
    entire read-check-write atomically under the interprocess writer lock, so
    concurrent evaluations of the same terminal council create exactly ONE gate.

    - An existing gate (resolved OR unresolved) with the same dedup key -> no
      new gate; a deduplicated_events entry {council_id, trigger,
      invocation_id} is appended (idempotent on that triple), and the original
      authority linkage is untouched.
    - A legacy gate lacking a stored dedup_key is matched by its normalized
      field tuple under the frozen v1-era key and BACKFILLED, so a pre-upgrade
      resolved gate cannot re-gate the first post-upgrade reconciliation.
    - A genuinely new substantive round, outcome, or scope produces a new key
      and a new gate.
    invocation_id is required and must be non-null (the caller's council
    invocation id, or a deterministic fallback)."""
    if not invocation_id:
        raise GateError({"ok": False, "error": "invocation_id_required"})
    subject = canonical_subject(work_item_id)
    key = _dedup_key(subject, council_id, phase, outcome,
                     substantive_round_count, scope_hash)
    # Lock-free fast path: a gate already recognized for this exact escalation
    # AND this exact invocation is a pure READ - repeated heals must not take
    # the writer token (they would needlessly contend, rewrite nothing, and
    # fail during an archive-maintenance window even though no write is
    # needed). Any miss - including a stale read - falls through to the
    # atomic locked path below.
    record = _load_record(root, subject)
    for gate in record["gates"]:
        if gate.get("dedup_key") == key:
            triple = (council_id, "reconcile", invocation_id)
            if any((e.get("council_id"), e.get("trigger"),
                    e.get("invocation_id")) == triple
                   for e in (gate.get("deduplicated_events") or [])):
                return {"gate": gate, "deduplicated": True}
            break
    with cwl.write_token(root, purpose="gate"):
        record = _load_record(root, subject)
        match = None
        backfilled = False
        for gate in record["gates"]:
            stored = gate.get("dedup_key")
            if stored == key:
                match = gate
                break
            if not stored and _legacy_matches(gate, subject, council_id, phase,
                                               outcome, substantive_round_count,
                                               scope_hash):
                gate["dedup_key"] = key  # backfill the frozen match
                backfilled = True
                match = gate
                break
        if match is not None:
            events = match.setdefault("deduplicated_events", [])
            triple = (council_id, "reconcile", invocation_id)
            appended = False
            if not any((e.get("council_id"), e.get("trigger"),
                        e.get("invocation_id")) == triple for e in events):
                events.append({"council_id": council_id, "trigger": "reconcile",
                               "invocation_id": invocation_id, "at": _now_iso()})
                appended = True
            if appended or backfilled:
                # Write only when something actually changed (a new dedup
                # event or a legacy-key backfill); pure recognition rewrites
                # nothing.
                _write_record_locked(root, record)
            return {"gate": match, "deduplicated": True}
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%f")
        gate = {
            "gate_id": "gate-" + stamp,
            "subject": subject,
            "work_item_id_as_seen": str(work_item_id),
            "work_item_id": subject,
            "council_id": council_id,
            "phase": phase,
            "outcome": outcome,
            "dedup_key": key,
            "created_at": _now_iso(),
            "disposition": "unresolved",
            "authority": None,
        }
        record["gates"].append(gate)
        _write_record_locked(root, record)
        return {"gate": gate, "deduplicated": False}


# --------------------------------------------------------------------------- #
# Escalation-gate creation, healing, and fail-closed failure reporting
# --------------------------------------------------------------------------- #
# A plan/incident council whose persisted outcome is operator_required or
# hard_gate MUST have a durable gate. These helpers create it from the
# authoritative durable records (never from caller-held state), HEAL a missing
# gate before any governed advancement (the failure-window containment), and
# fail CLOSED with a durable notice when the records themselves are malformed.
# Failure never masquerades as success, and a gate-creation failure can never
# be silent: every failure posts one idempotent "use-cw-gate" notice through
# _post_gate_failure_notice, the single shared notice primitive.

ESCALATION_OUTCOMES = ("operator_required", "hard_gate")
ESCALATION_PHASES = ("plan", "incident")
GATE_FAILURE_ERROR = "gate_creation_failed"

# Recognized council-directory names: the persisted council-id format. Anything
# else under the councils root is not part of the governance record and is
# IGNORED; a RECOGNIZED directory whose council.json is missing or unreadable
# is a malformed governance record and fails closed (it could hide an
# escalation). The predicate is a pure function of name + file readability, so
# behavior is deterministic across repeated calls and process restarts.
_COUNCIL_DIR_RE = re.compile(r"^cw-council-\d{8}T\d{12}$")


def _strict_json(path):
    """(data, state) with state 'ok' | 'absent' | 'unreadable'. Strict: a
    present-but-unparseable (or non-object) file is NEVER silently skipped."""
    if not os.path.isfile(path):
        return None, "absent"
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return None, "unreadable"
    if not isinstance(data, dict):
        return None, "unreadable"
    return data, "ok"


def _failure_payload(invariant, council_id, work_item_id, phase, outcome_value,
                     detail):
    """The single fail-closed payload shape. `error` and `error_code` carry the
    IDENTICAL value so status mapping works from either field."""
    return {
        "ok": False,
        "error": GATE_FAILURE_ERROR,
        "error_code": GATE_FAILURE_ERROR,
        "error_class": "governance_integrity",
        "invariant": invariant,
        "council_id": council_id,
        "work_item_id": work_item_id,
        "phase": phase,
        "outcome": outcome_value,
        "detail": detail,
    }


def _post_gate_failure_notice(root, payload, reporting_context):
    """The ONE shared primitive for the durable gate-failure notice. Guarded
    best-effort: a posting failure adds payload['posting_failure'] and never
    masks the fail-closed result. Idempotent: the deterministic key makes an
    identical failure state replay the SAME notice instead of duplicating.
    Thread resolution order: readable council record -> reporting_context ->
    the work item's origin message -> a fresh thread bound by work_item_id."""
    import clearwright_message as cwm  # lazy: avoids a circular import
    ctx = reporting_context or {}
    wid = payload.get("work_item_id")
    council_id = payload.get("council_id")
    thread_id = None
    if council_id:
        try:
            import clearwright_review_council as cwrc  # lazy: avoids a circular import
            rec = cwrc.load_council(root, council_id)
            if isinstance(rec, dict):
                thread_id = rec.get("thread_id")
        except Exception:
            thread_id = None
    if not thread_id:
        thread_id = ctx.get("thread_id")
    if not thread_id and wid:
        # Origin-thread resolution for ANY work-item id scheme (message: ids
        # via their origin message; packet-derived ids via the shared work
        # resolver), so the idempotent replay anchors to one stable thread.
        try:
            import clearwright_work as cww  # lazy: avoids a circular import
            thread_id, _ = cww._resolve_target(root, wid)
        except Exception:
            thread_id = None
    detail_tag = hashlib.sha256(
        str(payload.get("detail") or "").encode("utf-8")).hexdigest()[:8]
    ikey = "gate-fail:{}:{}:{}:{}".format(wid or "none", council_id or "none",
                                          payload.get("invariant") or "none",
                                          detail_tag)
    text = ("GOVERNANCE INTEGRITY: mandatory gate creation failed for work "
            "item {} (council {}). Invariant: {}. Detail: {}. The governed "
            "workflow is fail-closed until the durable records are repaired; "
            "no gate exists for this escalation and no advancement is "
            "permitted.").format(wid or "<unknown>", council_id or "<none>",
                                 payload.get("invariant"),
                                 payload.get("detail"))
    try:
        msg = cwm.build_message("claude", text, role="orchestrator",
                                thread_id=thread_id, direction="internal",
                                status="posted", source="use-cw-gate",
                                work_item_id=wid, idempotency_key=ikey)
        cwm.write_message_idempotent(root, msg)
    except Exception as exc:  # best-effort by contract; never masks the result
        payload["posting_failure"] = str(exc)
    return payload


def ensure_escalation_gate(root, council_id, outcome):
    """Create/recognize the mandatory gate for ONE escalated council, from a
    single authoritative council.json read. Raises GateError carrying the
    fail-closed payload on any integrity violation; cwl.MaintenanceInProgress
    propagates untouched. Returns {ok, gate, deduplicated} on success."""
    import clearwright_review_council as cwrc  # lazy: avoids a circular import
    outcome_value = (outcome or {}).get("outcome")
    council = cwrc.load_council(root, council_id)
    if not isinstance(council, dict):
        raise GateError(_failure_payload(
            "council_record_unreadable", council_id, None,
            (outcome or {}).get("phase"), outcome_value,
            "council.json missing or unreadable for {}".format(council_id)))
    wid = council.get("work_item_id")
    phase = council.get("phase")
    # Mandatory gates exist ONLY for work-item-bound plan/incident escalations
    # (verify councils use the completion gate; unbound councils have no
    # subject to gate) - the same rule the healing sweep's candidacy filter
    # applies, judged here from the AUTHORITATIVE reload.
    if not wid or phase not in ESCALATION_PHASES:
        return None
    recorded = council.get("rounds") or []
    if (not isinstance(recorded, list)
            or not all(isinstance(n, int) for n in recorded)
            or len(set(recorded)) != len(recorded)):
        raise GateError(_failure_payload(
            "round_records_inconsistent", council_id, wid, phase,
            outcome_value,
            "recorded round list invalid: {!r}".format(recorded)))
    loaded, missing = [], []
    for n in sorted(recorded):
        data, state = _strict_json(cwrc._round_path(root, council_id, n))
        if state != "ok":
            missing.append(n)
            continue
        if data.get("round") != n:
            raise GateError(_failure_payload(
                "round_records_inconsistent", council_id, wid, phase,
                outcome_value,
                "round file {} carries round={!r}".format(n, data.get("round"))))
        loaded.append(data)
    if missing:
        raise GateError(_failure_payload(
            "round_records_unreadable", council_id, wid, phase, outcome_value,
            "recorded rounds missing/unreadable: {}".format(missing)))
    substantive = cwrc.substantive_round_count(loaded)
    scope_hash = council.get("approved_scope_sha256") or "none"
    invocation_id = "gateeval-{}-{}-{}".format(council_id, outcome_value,
                                               substantive)
    result = ensure_gate(root, wid, council_id, phase, outcome_value,
                         substantive, scope_hash, invocation_id)
    return {"ok": True, "gate": result["gate"],
            "deduplicated": result["deduplicated"]}


def record_escalation_gate(root, council_id, outcome, reporting_context=None):
    """The caller-facing single-council entry (outcome-time call sites and the
    healing sweep both use it). Returns None for a non-escalating outcome;
    ensure_escalation_gate's success shape unchanged on success; the FULL
    fail-closed payload (after posting the single durable notice) on failure.
    cwl.MaintenanceInProgress propagates untouched."""
    if not outcome or outcome.get("outcome") not in ESCALATION_OUTCOMES:
        return None
    ctx = reporting_context or {}
    try:
        return ensure_escalation_gate(root, council_id, outcome)
    except cwl.MaintenanceInProgress:
        raise  # routine transient window; the caller's handler owns it
    except GateError as exc:
        raw = exc.payload if isinstance(exc.payload, dict) else {}
        payload = _failure_payload(
            raw.get("invariant") or raw.get("error") or "gate_error",
            raw.get("council_id") or council_id,
            raw.get("work_item_id") or ctx.get("work_item_id"),
            raw.get("phase") or (outcome or {}).get("phase"),
            outcome.get("outcome"),
            raw.get("detail") or raw.get("error") or "gate creation failed")
        return _post_gate_failure_notice(root, payload, ctx)
    except Exception as exc:  # OSError, ValueError, anything: never silent,
        payload = _failure_payload(  # never a bare traceback - fail closed.
            "gate_error", council_id, ctx.get("work_item_id"),
            (outcome or {}).get("phase"), outcome.get("outcome"), str(exc))
        return _post_gate_failure_notice(root, payload, ctx)


def heal_escalation_gates(root, work_item_id):
    """The work-item-level healing preflight: ensure the mandatory gate exists
    for EVERY authoritative plan/incident council of this work item whose
    persisted outcome is operator_required or hard_gate. Strict deterministic
    discovery (recognized-directory predicate + direct reads; list summaries
    are never trusted). Returns {ok:true, healed:[...]} or the first fail-closed
    payload; cwl.MaintenanceInProgress propagates untouched."""
    import clearwright_review_council as cwrc  # lazy: avoids a circular import
    croot = cwrc.councils_root(root)
    healed = []
    if not os.path.isdir(croot):
        return {"ok": True, "healed": healed}
    try:
        names = sorted(os.listdir(croot))
    except OSError as exc:
        payload = _failure_payload(
            "council_discovery_failed", None, work_item_id, None, None,
            "cannot list {}: {}".format(croot, exc))
        return _post_gate_failure_notice(root, payload,
                                         {"work_item_id": work_item_id})
    subject = canonical_subject(work_item_id)
    for name in names:
        cdir = os.path.join(croot, name)
        if not _COUNCIL_DIR_RE.match(name) or not os.path.isdir(cdir):
            continue  # not part of the governance record: ignored entirely
        council, state = _strict_json(os.path.join(cdir, "council.json"))
        if state == "absent":
            # A COMPLETELY EMPTY recognized directory is an archived remnant
            # (the archiver moves files and, by its zero-deletion policy,
            # leaves the emptied directory behind) or an interrupted
            # pre-write; it contains no files and therefore cannot hide an
            # escalation - skip it. A recognized directory that DOES contain
            # files but no readable council.json stays fail-closed.
            try:
                if not os.listdir(cdir):
                    continue
            except OSError:
                pass
            payload = _failure_payload(
                "council_record_unreadable", name, work_item_id, None, None,
                "council.json absent but directory is non-empty: {}".format(cdir))
            return _post_gate_failure_notice(root, payload,
                                             {"work_item_id": work_item_id})
        if state != "ok":
            payload = _failure_payload(
                "council_record_unreadable", name, work_item_id, None, None,
                "council.json {} in {}".format(state, cdir))
            return _post_gate_failure_notice(root, payload,
                                             {"work_item_id": work_item_id})
        if (canonical_subject(council.get("work_item_id")) != subject
                or council.get("phase") not in ESCALATION_PHASES):
            continue
        out, ostate = _strict_json(os.path.join(cdir, "outcome.json"))
        if ostate == "absent":
            continue  # a valid unfinished council blocks nothing
        if ostate != "ok" or not isinstance(out.get("outcome"), str):
            payload = _failure_payload(
                "outcome_record_unreadable", name, work_item_id,
                council.get("phase"), None,
                "outcome.json unreadable or invalid in {}".format(cdir))
            return _post_gate_failure_notice(
                root, payload, {"work_item_id": work_item_id,
                                "thread_id": council.get("thread_id")})
        if out.get("outcome") not in ESCALATION_OUTCOMES:
            continue
        res = record_escalation_gate(
            root, name, out, {"work_item_id": work_item_id,
                              "thread_id": council.get("thread_id")})
        if res is None:
            continue
        if not res.get("ok"):
            return res
        healed.append(res)
    return {"ok": True, "healed": healed}


def _legacy_matches(gate, subject, council_id, phase, outcome, rounds, scope_hash):
    """Frozen v1-era field-tuple equivalence for a keyless legacy gate. A gate
    missing council_id or outcome is EXCLUDED (returns False) so the system
    fails toward a visible new gate, never toward suppressing an escalation."""
    if not gate.get("council_id") or not gate.get("outcome"):
        return False
    legacy_key = _dedup_key(gate.get("subject") or subject, gate.get("council_id"),
                            gate.get("phase"), gate.get("outcome"),
                            gate.get("substantive_round_count", rounds),
                            gate.get("scope_hash", scope_hash), policy_version=1)
    this_key = _dedup_key(subject, council_id, phase, outcome, rounds,
                          scope_hash, policy_version=1)
    return legacy_key == this_key


def _write_record_locked(root, record):
    """Write a gate record WITHOUT acquiring the writer lock (the caller already
    holds it). Mirrors _write_record's atomic replace + fsync."""
    directory = os.path.join(root, GATES_DIR)
    os.makedirs(directory, exist_ok=True)
    path = _subject_path(root, record["subject"])
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(record, fh, indent=2)
        fh.write("\n")
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)
    _fsync_dir(directory)


def create_gate(root, work_item_id, council_id, phase, outcome):
    """Create an unresolved gate for the work item's canonical subject. At most
    one unresolved gate may exist per subject; a second creation is refused
    (defensive: council activity on a gated subject is already refused, so no
    new gate source normally exists). Returns the created gate.

    Retained for compatibility; new escalation paths use ensure_gate for
    idempotency."""
    subject = canonical_subject(work_item_id)
    record = _load_record(root, subject)
    for gate in record["gates"]:
        if gate.get("disposition") == "unresolved":
            raise GateError({"ok": False, "error": "gate_already_unresolved",
                             "subject": subject,
                             "gate_id": gate.get("gate_id")})
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%f")
    gate = {
        "gate_id": "gate-" + stamp,
        "subject": subject,
        "work_item_id_as_seen": str(work_item_id),
        "council_id": council_id,
        "phase": phase,
        "outcome": outcome,
        "created_at": _now_iso(),
        "disposition": "unresolved",
        "authority": None,
    }
    record["gates"].append(gate)
    _write_record(root, record)
    return gate


def _iso(dt_text):
    """Parse a stored UTC iso-Z timestamp; return an aware datetime or None."""
    if not dt_text:
        return None
    try:
        return datetime.strptime(dt_text, "%Y-%m-%dT%H:%M:%S.%fZ").replace(
            tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return None


def id_token_present(body, token):
    """True if token appears in body bounded by start/end or a non-id char."""
    if not token:
        return False
    pattern = r"(?<![{c}])".format(c=_ID_CHARS) + re.escape(token) + \
              r"(?![{c}])".format(c=_ID_CHARS)
    return re.search(pattern, body) is not None


def phrase_authorizes(body, phrases):
    """True if any allowlisted phrase appears with intent-safe matching: not
    negated within a short preceding window and not inside quotation marks."""
    low = body.lower()
    for phrase in phrases:
        for m in re.finditer(re.escape(phrase), low):
            start = m.start()
            # Reject if inside quotes: an odd number of quote marks precedes it.
            prefix = body[:start]
            if (prefix.count('"') % 2) == 1 or (prefix.count("'") % 2) == 1:
                continue
            window = low[max(0, start - 24):start]
            if any(re.search(r"\b" + re.escape(neg) + r"\b", window)
                   for neg in _NEGATIONS):
                continue
            return True
    return False


def _find_message(root, message_id):
    import clearwright_message as cwm
    for m in cwm.read_messages(root):
        if m.get("message_id") == message_id:
            return m
    return None


def _validate_authority(root, gate, operator_message_id, phrases,
                        after_outcome_at=None):
    """Shared grammar for grant-proceed and close authority. Returns the message
    dict on success; raises GateError naming the failed criterion otherwise."""
    msg = _find_message(root, operator_message_id)
    if msg is None:
        raise GateError({"ok": False, "error": "authority_message_not_found",
                         "operator_message_id": operator_message_id})
    if msg.get("actor") != "OPERATOR-0001" or msg.get("direction") != "inbound":
        raise GateError({"ok": False, "error": "authority_not_operator_inbound"})
    if msg.get("source") in EXEMPT_MESSAGE_SOURCES:
        raise GateError({"ok": False, "error": "authority_source_excluded",
                         "source": msg.get("source")})
    created = _iso(msg.get("at"))
    gate_created = _iso(gate.get("created_at"))
    if created is None or gate_created is None or not (created > gate_created):
        raise GateError({"ok": False, "error": "authority_not_after_gate",
                         "message_at": msg.get("at"),
                         "gate_created_at": gate.get("created_at")})
    if after_outcome_at is not None:
        outcome_dt = _iso(after_outcome_at)
        if outcome_dt is not None and not (created > outcome_dt):
            raise GateError({"ok": False,
                             "error": "authority_not_after_failed_outcome",
                             "message_at": msg.get("at"),
                             "outcome_at": after_outcome_at})
    body = msg.get("message", "")
    names_target = (id_token_present(body, gate.get("work_item_id_as_seen"))
                    or id_token_present(body, gate.get("subject"))
                    or id_token_present(body, gate.get("council_id")))
    if not names_target:
        raise GateError({"ok": False, "error": "authority_missing_target_id",
                         "expected_any": [gate.get("work_item_id_as_seen"),
                                          gate.get("council_id")]})
    if not phrase_authorizes(body, phrases):
        raise GateError({"ok": False, "error": "authority_missing_phrase"})
    return msg


def _record_disposition(root, work_item_id, disposition, message):
    subject = canonical_subject(work_item_id)
    record = _load_record(root, subject)
    gate = None
    for candidate in record["gates"]:
        if candidate.get("disposition") == "unresolved":
            gate = candidate
            break
    if gate is None:
        raise GateError({"ok": False, "error": "no_unresolved_gate",
                         "subject": subject})
    excerpt = (message.get("message", "") or "")[:280]
    gate["disposition"] = disposition
    gate["authority"] = {
        "message_id": message.get("message_id"),
        "excerpt": excerpt,
        "recorded_at": _now_iso(),
    }
    _write_record(root, record)
    return gate


def grant_proceed(root, work_item_id, operator_message_id):
    """Resolve the current unresolved gate with a qualifying operator message.
    Disposition is written before any item transition (the caller performs no
    transition here; resolving the gate simply unblocks the governed path)."""
    gate = active_gate(root, work_item_id)
    if gate is None:
        raise GateError({"ok": False, "error": "no_unresolved_gate",
                         "work_item_id": str(work_item_id)})
    msg = _validate_authority(root, gate, operator_message_id, PROCEED_PHRASES)
    resolved = _record_disposition(root, work_item_id, "resolved", msg)
    return {"ok": True, "disposition": "resolved", "gate_id": resolved["gate_id"],
            "authority": resolved["authority"]}


def record_operator_closure(root, work_item_id, message):
    """Mark the current unresolved gate ``closed_unresolved`` on behalf of the
    operator-only close path, which has ALREADY validated closure authority.
    Returns the gate, or None when there is no unresolved gate. This never
    creates authority to proceed."""
    if active_gate(root, work_item_id) is None:
        return None
    return _record_disposition(root, work_item_id, "closed_unresolved", message)


def close_gate_unresolved(root, work_item_id, operator_message_id,
                          after_outcome_at=None):
    """Record disposition ``closed_unresolved`` for the current gate under
    closure authority. This never creates authority to proceed; the caller
    (operator-only close) terminalizes the item separately."""
    gate = active_gate(root, work_item_id)
    if gate is None:
        raise GateError({"ok": False, "error": "no_unresolved_gate",
                         "work_item_id": str(work_item_id)})
    msg = _validate_authority(root, gate, operator_message_id, CLOSE_PHRASES,
                              after_outcome_at=after_outcome_at)
    closed = _record_disposition(root, work_item_id, "closed_unresolved", msg)
    return {"ok": True, "disposition": "closed_unresolved",
            "gate_id": closed["gate_id"], "authority": closed["authority"]}
