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

def create_gate(root, work_item_id, council_id, phase, outcome):
    """Create an unresolved gate for the work item's canonical subject. At most
    one unresolved gate may exist per subject; a second creation is refused
    (defensive: council activity on a gated subject is already refused, so no
    new gate source normally exists). Returns the created gate."""
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
