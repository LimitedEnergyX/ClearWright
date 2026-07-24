#!/usr/bin/env python3
"""tools/clearwright_alf.py: ALF (Automated Leap Frog) Phase 1 durable store + CLI.

Additive ClearWright subsystem living entirely under QUEUE_ROOT/alf/. It implements
the three-layer model's Layer 1 (immutable run observations plus per-run
occurrences), the crash-safe operation-journal write primitive, and the
tamper-evident hash-chain integrity model described in
docs/alf/ALF-PHASE1-PLANNING-PACKET.md (the plan-gate-approved baseline).

Design invariants (packet sections 5, 8):
  * No existing ClearWright record shape changes; every ALF record carries
    alf_record_version 1; absence of alf/ simply means ALF has no data.
  * Every hashed artifact uses ONE canonical serialization: UTF-8 JSON, sorted
    keys, compact separators, ensure_ascii off, one trailing newline per line,
    integer-only JSON numbers (floats are refused in hashed records).
  * All mutations run under the existing single-writer lock
    (clearwright_writer_lock.write_token); chained JSONL files carry a per-line
    hash chain; head files use atomic tmp-then-replace with the winerror 5/32
    retry already shipped in the queue writer.
  * Synthesis writes are transactional through an append-only operation journal
    with content-addressed durable staged payloads and deterministic crash
    recovery that replays ONLY from staged bytes, never from mutable state.

This module is Layer 1 + the journal primitive + the alf-observe/list/show/
verify-hashes CLI. Synthesis (findings, dedup, recurrence, delta) and the
operator review surface build on these primitives in later phases.
"""
import argparse
import hashlib
import json
import os
import re
import sys
import threading
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import clearwright_writer_lock as cwl  # noqa: E402

ALF_RECORD_VERSION = 1
SENTINEL = "0" * 64
_REPLACE_RETRY_WINERRORS = (5, 32)
# In-process serialization that COMPLEMENTS the cross-process writer token: the OS
# region lock serializes concurrent CLI processes, and this lock serializes threads
# within one process, so a compound transaction is atomic on both axes (HIGH-3).
_COMMIT_LOCK = threading.RLock()

OBSERVATION_KINDS = {
    "run_started", "run_completed", "run_closed", "council_round",
    "council_outcome", "reviewer_attempt", "dispatch_failure", "gate_created",
    "gate_resolved", "lifecycle_event", "complete_refusal", "close_recorded",
    "operator_intervention", "resource_usage", "executor_note",
}
SUBSYSTEMS = {
    "envelope_classification", "work_item_lifecycle", "council_engine",
    "reviewer_gpt", "reviewer_codex", "egress_guard", "dispatch_lane",
    "queue_store", "gates", "server_lifecycle", "operator_ui", "cli",
    "executor_process", "other",
}
CAPTURE_METHODS = {"cli_explicit", "run_boundary", "backfill"}
EVIDENCE_ROLES = {"defining_authority", "observed_occurrence", "verification",
                  "correction"}
# Canonical metrics keys: present-as-null when the whole object is absent; when
# present every key is present with JSON null for any absent sub-value.
METRIC_KEYS = (
    "operator_minutes", "execution_delay_seconds", "token_estimate",
    "gpt_tokens_actual_in", "gpt_tokens_actual_out", "api_attempts",
    "tool_attempts", "council_attempts", "invocations",
)


# --------------------------------------------------------------------------- #
# Errors
# --------------------------------------------------------------------------- #
class AlfError(Exception):
    """A refused ALF operation (validation, collision, or divergence)."""


class IntegrityHalt(AlfError):
    """A fail-closed condition that halts further ALF synthesis (Tier 0/1)."""


# --------------------------------------------------------------------------- #
# Paths
# --------------------------------------------------------------------------- #
def alf_root(queue_root):
    return os.path.join(queue_root, "alf")


def _p(queue_root, *parts):
    return os.path.join(alf_root(queue_root), *parts)


def observations_dir(q):
    return _p(q, "observations")


def observation_file(q, obs_id):
    return _contained(
        _p(q, "observations", safe_id(obs_id, "observation_id") + ".json"), q)


def index_path(q):
    return _p(q, "observations", "index.jsonl")


def occurrences_path(q):
    return _p(q, "observations", "occurrences.jsonl")


def ledger_path(q):
    return _p(q, "attributions", "ledger.jsonl")


def journal_path(q):
    return _p(q, "journal", "journal.jsonl")


def staged_dir(q, op_id):
    return _contained(_p(q, "journal", "staged", safe_id(op_id, "op_id")), q)


def checkpoint_path(q):
    return _p(q, "meta", "expected-heads.json")


# Chained JSONL files that the expected-head checkpoint tracks. The journal is
# tracked separately (its head is the PRE-transaction journal head).
def _chained_files(q):
    return {
        "observations/index.jsonl": index_path(q),
        "observations/occurrences.jsonl": occurrences_path(q),
        "attributions/ledger.jsonl": ledger_path(q),
    }


def ensure_layout(q):
    for d in (observations_dir(q), _p(q, "attributions"), _p(q, "journal"),
              _p(q, "journal", "staged"), _p(q, "meta"), _p(q, "findings"),
              _p(q, "findings", "history"), _p(q, "deltas"), _p(q, "specs"),
              quarantine_dir(q)):
        os.makedirs(d, exist_ok=True)


def quarantine_dir(q):
    return _p(q, "quarantine")


# --------------------------------------------------------------------------- #
# Identifier + path-containment safety (verification HIGH-1)
# --------------------------------------------------------------------------- #
_SAFE_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,190}$")


def safe_id(value, kind="id"):
    """Validate a caller-influenced identifier used as a filename component BEFORE
    any filesystem access. Rejects empties, path separators, drive/UNC, absolute
    paths, '..' traversal, colons, and control characters. Returns the value."""
    if (not isinstance(value, str) or ".." in value
            or not _SAFE_ID.match(value)):
        raise AlfError("unsafe {} {!r}: must be [A-Za-z0-9][A-Za-z0-9._-]* with no "
                       "separators, drive, absolute path, colon, or traversal"
                       .format(kind, value))
    return value


def safe_rel(rel):
    """Validate a relative target path (e.g. 'observations/index.jsonl'): forward
    slashes only, every component a safe id, no '.'/'..'/absolute/drive/UNC/backslash/
    colon/NUL. Returns the component list."""
    if (not isinstance(rel, str) or not rel or rel.startswith("/")
            or "\\" in rel or ":" in rel or "\x00" in rel):
        raise AlfError("unsafe target_rel {!r}".format(rel))
    parts = rel.split("/")
    for p in parts:
        if p in ("", ".", ".."):
            raise AlfError("unsafe target_rel component in {!r}".format(rel))
        safe_id(p, "path component")
    return parts


def _contained(path, q):
    """Ensure the normalized absolute path is a descendant of alf_root(q)."""
    root = os.path.abspath(alf_root(q))
    full = os.path.abspath(path)
    if full != root and not full.startswith(root + os.sep):
        raise AlfError("path escapes alf root: {!r}".format(path))
    return path


def _heal_torn_tail(q, path):
    """If a chained-JSONL file ends WITHOUT a terminating newline, its final line is
    a demonstrably torn partial write (a crash mid-append). Archive the torn bytes
    verbatim to alf/quarantine/ and truncate to the last complete line, so a
    subsequent append can never concatenate onto a partial line (HIGH-2). A
    parseable line that BREAKS the hash chain is NOT healed here; verify_chain
    reports it and callers fail closed. Returns True if it healed a torn tail."""
    if not os.path.exists(path):
        return False
    with open(path, "rb") as fh:
        data = fh.read()
    if not data or data.endswith(b"\n"):
        return False
    idx = data.rfind(b"\n")
    torn = data[idx + 1:]
    os.makedirs(quarantine_dir(q), exist_ok=True)
    qpath = os.path.join(quarantine_dir(q), "{}.torn-{}".format(
        os.path.basename(path), sha256_hex(torn)[:16]))
    with open(qpath, "wb") as fh:
        fh.write(torn)
        fh.flush()
        os.fsync(fh.fileno())
    with open(path, "r+b") as fh:
        fh.truncate(idx + 1)
        fh.flush()
        os.fsync(fh.fileno())
    return True


# --------------------------------------------------------------------------- #
# Canonical serialization + hashing (packet section 8)
# --------------------------------------------------------------------------- #
def _reject_floats(obj, where="$"):
    if isinstance(obj, bool):
        return
    if isinstance(obj, float):
        raise AlfError("non-integer JSON number in hashed record at {}".format(where))
    if isinstance(obj, dict):
        for k, v in obj.items():
            _reject_floats(v, "{}.{}".format(where, k))
    elif isinstance(obj, (list, tuple)):
        for i, v in enumerate(obj):
            _reject_floats(v, "{}[{}]".format(where, i))


def canonical_bytes(obj):
    """UTF-8, sorted keys, compact separators, ensure_ascii off. Floats refused."""
    _reject_floats(obj)
    return json.dumps(obj, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False).encode("utf-8")


def canonical_line(obj):
    return canonical_bytes(obj) + b"\n"


def sha256_hex(data):
    return hashlib.sha256(data).hexdigest()


def content_sha256(obj):
    return sha256_hex(canonical_bytes(obj))


def now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


# --------------------------------------------------------------------------- #
# Per-line hash chain (packet section 8)
# --------------------------------------------------------------------------- #
def chained_record(payload, prev_line_sha256):
    """Return payload + {prev_line_sha256, line_sha256}. line_sha256 is computed
    over the canonical record MINUS its own hash field."""
    rec = dict(payload)
    rec["prev_line_sha256"] = prev_line_sha256
    rec["line_sha256"] = sha256_hex(canonical_bytes(rec))
    return rec


def _read_valid_lines(path):
    """Return (records, torn_tail_text). Records are parsed JSON objects for every
    complete valid line; torn_tail_text is a final partial line (no newline / not
    JSON) if present, else None."""
    if not os.path.exists(path):
        return [], None
    with open(path, "rb") as fh:
        raw = fh.read()
    if not raw:
        return [], None
    text = raw.decode("utf-8")
    ends_newline = text.endswith("\n")
    parts = text.split("\n")
    if ends_newline:
        parts = parts[:-1]  # drop the empty trailing element
        tail = None
    else:
        tail = parts.pop() if parts else None
    records = []
    for i, line in enumerate(parts):
        try:
            records.append(json.loads(line))
        except ValueError:
            # An interior unparseable line is a hard chain break, surfaced by
            # verify_chain; treat the remainder (incl this) as a tail for callers
            # that only need the valid prefix head.
            tail = "\n".join(parts[i:]) if tail is None else tail
            break
    return records, tail


def chain_head(path):
    """(head_line_sha256, line_count) over the valid prefix. Empty -> sentinel/0."""
    records, _ = _read_valid_lines(path)
    if not records:
        return SENTINEL, 0
    return records[-1].get("line_sha256", SENTINEL), len(records)


def verify_chain(path):
    """Return a list of problems; empty means the chain is intact. Checks per-line
    hash recomputation and prev-linkage; reports the first broken position."""
    records, tail = _read_valid_lines(path)
    problems = []
    prev = SENTINEL
    for i, rec in enumerate(records):
        stored = rec.get("line_sha256")
        body = {k: v for k, v in rec.items() if k != "line_sha256"}
        recomputed = sha256_hex(canonical_bytes(body))
        if stored != recomputed:
            problems.append("{}: line {} hash mismatch".format(path, i + 1))
            break
        if rec.get("prev_line_sha256") != prev:
            problems.append("{}: line {} prev-link break".format(path, i + 1))
            break
        prev = stored
    if tail is not None:
        problems.append("{}: torn/partial tail after {} valid line(s)".format(
            path, len(records)))
    return problems


# --------------------------------------------------------------------------- #
# Metrics + evidence normalization (deterministic canonical shapes)
# --------------------------------------------------------------------------- #
def normalize_metrics(metrics):
    """None stays None (whole object absent). A dict is expanded to every metric
    key present with null for any absent sub-value (packet section 5/8)."""
    if metrics is None:
        return None
    out = {}
    for k in METRIC_KEYS:
        v = metrics.get(k)
        if v is None:
            out[k] = None
        else:
            if isinstance(v, bool) or not isinstance(v, int):
                raise AlfError("metric {!r} must be an integer".format(k))
            out[k] = v
    return out


def normalize_evidence(refs):
    """Evidence objects {ref, sha256, role, archived_location}, canonically ordered
    by (ref, sha256). role must be a known role; archived_location defaults null."""
    out = []
    for e in refs or []:
        if "ref" not in e or "sha256" not in e:
            raise AlfError("evidence entry needs ref and sha256")
        role = e.get("role")
        if role is not None and role not in EVIDENCE_ROLES:
            raise AlfError("unknown evidence role {!r}".format(role))
        out.append({
            "ref": e["ref"],
            "sha256": e["sha256"],
            "role": role,
            "archived_location": e.get("archived_location"),
        })
    out.sort(key=lambda o: (o["ref"], o["sha256"]))
    return out


# --------------------------------------------------------------------------- #
# Operation journal (packet section 8): the crash-safe transaction primitive.
# --------------------------------------------------------------------------- #
class Operation:
    """One synthesis transaction. Caller registers append_line / replace_file
    intents; commit() derives content-addressed staged payloads bound to the
    PRE-transaction heads (acyclic anchoring), stages them durably, writes
    op_begin, applies, refreshes the expected-head checkpoint, and writes
    op_commit. Recovery replays exclusively from staged bytes."""

    def __init__(self, queue_root, operation_kind, subject_ids):
        self.q = queue_root
        self.operation_kind = operation_kind
        self.subject_ids = list(subject_ids)
        self._appends = []   # (target_rel, payload_dict)
        self._replaces = []  # (target_rel, obj)

    def append_line(self, target_rel, payload):
        safe_rel(target_rel)  # reject traversal before it is ever staged
        self._appends.append((target_rel, dict(payload)))
        return self

    def replace_file(self, target_rel, obj):
        safe_rel(target_rel)
        self._replaces.append((target_rel, obj))
        return self

    # -- staging derivation (all values fixed now, bound to pre-transaction heads)
    def _derive(self):
        staged = []
        # Group appends per target so multi-append-to-one-target chains in order.
        per_target = {}
        for target_rel, payload in self._appends:
            per_target.setdefault(target_rel, []).append(payload)
        for target_rel, payloads in per_target.items():
            path = _p(self.q, *target_rel.split("/"))
            prev, count = chain_head(path)
            for payload in payloads:
                rec = chained_record(payload, prev)
                payload_bytes = canonical_line(rec)
                staged.append({
                    "target_rel": target_rel,
                    "write_kind": "append_line",
                    "payload_bytes": payload_bytes,
                    "content_sha256": sha256_hex(payload_bytes),
                    "expected_prev_line_sha256": prev,
                    "expected_chain_position": count + 1,
                })
                prev = rec["line_sha256"]
                count += 1
        for target_rel, obj in self._replaces:
            path = _p(self.q, *target_rel.split("/"))
            payload_bytes = (canonical_bytes(obj) + b"\n") if not isinstance(obj, (bytes, bytearray)) else bytes(obj)
            if os.path.exists(path):
                with open(path, "rb") as fh:
                    existing = fh.read()
                expected_mode = "file_exists"
                expected_existing = sha256_hex(existing)
            else:
                expected_mode = "absent"
                expected_existing = None
            staged.append({
                "target_rel": target_rel,
                "write_kind": "replace_file",
                "payload_bytes": payload_bytes,
                "content_sha256": sha256_hex(payload_bytes),
                "expected_mode": expected_mode,
                "expected_existing_sha256": expected_existing,
            })
        # Mandatory final staged write: the expected-head checkpoint (pre-tx heads).
        checkpoint = self._pre_transaction_checkpoint()
        cp_bytes = canonical_bytes(checkpoint) + b"\n"
        cp_path = checkpoint_path(self.q)
        if os.path.exists(cp_path):
            with open(cp_path, "rb") as fh:
                cp_existing = fh.read()
            cp_mode, cp_hash = "file_exists", sha256_hex(cp_existing)
        else:
            cp_mode, cp_hash = "absent", None
        staged.append({
            "target_rel": "meta/expected-heads.json",
            "write_kind": "replace_file",
            "payload_bytes": cp_bytes,
            "content_sha256": sha256_hex(cp_bytes),
            "expected_mode": cp_mode,
            "expected_existing_sha256": cp_hash,
        })
        return staged

    def _pre_transaction_checkpoint(self):
        heads = {}
        for rel, path in sorted(_chained_files(self.q).items()):
            h, c = chain_head(path)
            heads[rel] = {"head_line_sha256": h, "line_count": c}
        jh, jc = chain_head(journal_path(self.q))
        return {
            "alf_record_version": ALF_RECORD_VERSION,
            "journal": {"head_line_sha256": jh, "line_count": jc},
            "chained_files": heads,
        }

    def commit(self, build=None):
        """Atomic transaction (HIGH-3): the OPTIONAL `build(op)` callback runs
        UNDER the single writer lock so every existence/idempotence/sequence check,
        staged-write registration, head read, and the commit occur inside one
        serialization boundary. If build returns non-None, it is a no-op result and
        no records are written."""
        ensure_layout(self.q)
        with _COMMIT_LOCK, cwl.write_token(self.q, purpose="alf"):
            if build is not None:
                noop = build(self)
                if noop is not None:
                    return noop
            staged = self._derive()
            op_id = "op-" + sha256_hex(canonical_bytes({
                "operation_kind": self.operation_kind,
                "subject_ids": self.subject_ids,
                "staged": [s["content_sha256"] for s in staged],
            }))[:16]
            self._collision_check(op_id)
            sdir = staged_dir(self.q, op_id)
            os.makedirs(sdir, exist_ok=True)
            staged_meta = []
            for n, s in enumerate(staged):
                staged_file = "{}-{}".format(n, s["content_sha256"][:16])
                _write_bytes_fsync(os.path.join(sdir, staged_file), s["payload_bytes"])
                entry = {
                    "target_path_rel": s["target_rel"],
                    "staged_file": staged_file,
                    "content_sha256": s["content_sha256"],
                    "write_kind": s["write_kind"],
                }
                if s["write_kind"] == "replace_file":
                    entry["expected_mode"] = s["expected_mode"]
                    entry["expected_existing_sha256"] = s["expected_existing_sha256"]
                else:
                    entry["expected_prev_line_sha256"] = s["expected_prev_line_sha256"]
                    entry["expected_chain_position"] = s["expected_chain_position"]
                staged_meta.append(entry)
            cwl._fsync_dir(sdir)
            # op_begin
            self._journal_append({
                "op_id": op_id, "operation_kind": self.operation_kind,
                "subject_ids": self.subject_ids, "staged_writes": staged_meta,
                "at": now_iso(), "event": "op_begin",
            })
            # apply
            for s in staged:
                _apply_staged_write(self.q, s["target_rel"], s["payload_bytes"],
                                    s["write_kind"], s)
            # op_commit
            self._journal_append({"op_id": op_id, "at": now_iso(),
                                  "event": "op_commit"})
            _rmtree(sdir)
            return op_id

    def _collision_check(self, op_id):
        for rec in _read_valid_lines(journal_path(self.q))[0]:
            if rec.get("event") == "op_begin" and rec.get("op_id") == op_id:
                if (rec.get("operation_kind") == self.operation_kind
                        and rec.get("subject_ids") == self.subject_ids):
                    raise AlfError("op_id {} already recorded (verified duplicate; "
                                   "resume via recovery, not a fresh commit)".format(op_id))
                raise IntegrityHalt("op_id short-collision {} with a different "
                                    "operation identity".format(op_id))

    def _journal_append(self, payload):
        path = journal_path(self.q)
        _heal_torn_tail(self.q, path)  # never append onto a torn journal tail
        prev, _ = chain_head(path)
        rec = chained_record(payload, prev)
        _append_line_fsync(path, canonical_line(rec))


# -- low-level durable writers (used inside the lock) ------------------------ #
def _write_bytes_fsync(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as fh:
        fh.write(data)
        fh.flush()
        os.fsync(fh.fileno())


def _append_line_fsync(path, line_bytes):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "ab") as fh:
        fh.write(line_bytes)
        fh.flush()
        os.fsync(fh.fileno())


def _replace_bytes_fsync(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp-" + sha256_hex(data)[:8]
    with open(tmp, "wb") as fh:
        fh.write(data)
        fh.flush()
        os.fsync(fh.fileno())
    import time as _t
    for attempt in range(6):
        try:
            os.replace(tmp, path)
            break
        except OSError as exc:
            if getattr(exc, "winerror", None) in _REPLACE_RETRY_WINERRORS and attempt < 5:
                _t.sleep(0.05 * (attempt + 1))
                continue
            try:
                os.remove(tmp)
            except OSError:
                pass
            raise


def _rmtree(path):
    if not os.path.isdir(path):
        return
    for name in os.listdir(path):
        try:
            os.remove(os.path.join(path, name))
        except OSError:
            pass
    try:
        os.rmdir(path)
    except OSError:
        pass


def _apply_staged_write(q, target_rel, staged_bytes, write_kind, bindings):
    """Idempotently apply one staged write. Used by both commit and recovery, so a
    replay converges byte-identically or fails closed. target_rel is validated and
    contained under alf_root before any filesystem access, including during journal
    replay (HIGH-1)."""
    parts = safe_rel(target_rel)
    path = _contained(_p(q, *parts), q)
    if write_kind == "append_line":
        _heal_torn_tail(q, path)  # never append onto a torn target tail (HIGH-2)
        records, _ = _read_valid_lines(path)
        pos = bindings["expected_chain_position"]
        staged_line = json.loads(staged_bytes.decode("utf-8"))
        # Already applied at exactly the expected position?
        if len(records) >= pos and records[pos - 1].get("line_sha256") == staged_line.get("line_sha256"):
            return "already"
        # Ready to append: target ends at expected predecessor / position-1.
        if len(records) == pos - 1:
            last_hash = records[-1]["line_sha256"] if records else SENTINEL
            if last_hash == bindings["expected_prev_line_sha256"]:
                _append_line_fsync(path, staged_bytes)
                return "applied"
        raise IntegrityHalt("append_line divergence at {} pos {}".format(target_rel, pos))
    # replace_file: four-way recovery against the expected-old binding.
    live = None
    if os.path.exists(path):
        with open(path, "rb") as fh:
            live = fh.read()
    staged_hash = sha256_hex(staged_bytes)
    if live is not None and sha256_hex(live) == staged_hash:
        return "already"
    if bindings["expected_mode"] == "file_exists":
        if live is not None and sha256_hex(live) == bindings["expected_existing_sha256"]:
            _replace_bytes_fsync(path, staged_bytes)
            return "applied"
    else:  # expected absent
        if live is None:
            _replace_bytes_fsync(path, staged_bytes)
            return "applied"
    raise IntegrityHalt("replace_file divergence at {}".format(target_rel))


# --------------------------------------------------------------------------- #
# Recovery (packet section 8): replay from journal + staged bytes only.
# --------------------------------------------------------------------------- #
def recover(q):
    """Complete or clean up any interrupted operation. Returns a report dict.
    Fails closed (IntegrityHalt) on missing staged bytes or a divergent target."""
    jpath = journal_path(q)
    if not os.path.exists(jpath):
        return {"status": "clean", "recovered": []}
    with _COMMIT_LOCK, cwl.write_token(q, purpose="alf-recover"):
        # HIGH-2: quarantine + truncate a torn journal tail BEFORE using any record;
        # then fail closed on any surviving interior chain/hash break rather than
        # replaying from an unauthenticated prefix.
        _heal_torn_tail(q, jpath)
        chain_problems = verify_chain(jpath)
        if chain_problems:
            raise IntegrityHalt("journal chain break; halting ALF synthesis: "
                                + "; ".join(chain_problems))
        records, tail = _read_valid_lines(jpath)
        recovered = []
        committed = {r["op_id"] for r in records if r.get("event") == "op_commit"}
        begins = [r for r in records if r.get("event") == "op_begin"]
        for begin in begins:
            op_id = begin["op_id"]
            if op_id in committed:
                _rmtree(staged_dir(q, op_id))
                continue
            sdir = staged_dir(q, op_id)
            for entry in begin["staged_writes"]:
                # Treat journal-derived staged_file / target_path_rel as HOSTILE:
                # validate before any filesystem access (round-3 HIGH).
                safe_id(entry.get("staged_file"), "staged_file")
                safe_rel(entry.get("target_path_rel"))
                sf = _contained(os.path.join(sdir, entry["staged_file"]), q)
                if not os.path.exists(sf):
                    raise IntegrityHalt("op {} staged file {} missing; halting ALF "
                                        "synthesis".format(op_id, entry["staged_file"]))
                with open(sf, "rb") as fh:
                    data = fh.read()
                if sha256_hex(data) != entry["content_sha256"]:
                    raise IntegrityHalt("op {} staged file {} corrupt".format(
                        op_id, entry["staged_file"]))
                _apply_staged_write(q, entry["target_path_rel"], data,
                                    entry["write_kind"], entry)
            prev, _ = chain_head(jpath)
            _append_line_fsync(jpath, canonical_line(
                chained_record({"op_id": op_id, "at": now_iso(),
                                "event": "op_commit"}, prev)))
            _rmtree(sdir)
            recovered.append(op_id)
        # Orphan staging dirs with no op_begin: safe to delete.
        sroot = _p(q, "journal", "staged")
        known = {b["op_id"] for b in begins}
        if os.path.isdir(sroot):
            for name in os.listdir(sroot):
                if name not in known:
                    _rmtree(os.path.join(sroot, name))
        return {"status": "recovered" if recovered else "clean",
                "recovered": recovered, "torn_tail": tail is not None}


# --------------------------------------------------------------------------- #
# Observation identity + capture (packet section 5)
# --------------------------------------------------------------------------- #
def observation_identity(kind, subsystem, work_item_id, thread_id, council_id,
                         gate_id, summary, source_refs, metrics):
    source_identity = sorted(
        ({"ref": e["ref"], "sha256": e["sha256"]} for e in source_refs),
        key=lambda o: (o["ref"], o["sha256"]))
    tuple_obj = {
        "kind": kind, "subsystem": subsystem, "work_item_id": work_item_id,
        "thread_id": thread_id, "council_id": council_id, "gate_id": gate_id,
        "summary": summary, "source_identity": source_identity,
        "metrics": normalize_metrics(metrics),
    }
    return "obs-" + content_sha256(tuple_obj)[:16]


def build_observation(kind, subsystem, summary, source_refs=None, metrics=None,
                      work_item_id=None, thread_id=None, council_id=None,
                      gate_id=None, run_id=None, capture_method="cli_explicit",
                      capturing_actor="claude", captured_at=None):
    if kind not in OBSERVATION_KINDS:
        raise AlfError("unknown observation kind {!r}".format(kind))
    if subsystem not in SUBSYSTEMS:
        raise AlfError("unknown subsystem {!r}".format(subsystem))
    if capture_method not in CAPTURE_METHODS:
        raise AlfError("unknown capture_method {!r}".format(capture_method))
    evidence = normalize_evidence(source_refs)
    obs_id = observation_identity(kind, subsystem, work_item_id, thread_id,
                                  council_id, gate_id, summary, evidence, metrics)
    return {
        "alf_record_version": ALF_RECORD_VERSION,
        "observation_id": obs_id,
        "captured_at": captured_at or now_iso(),
        "run_id": run_id,
        "work_item_id": work_item_id,
        "thread_id": thread_id,
        "council_id": council_id,
        "gate_id": gate_id,
        "kind": kind,
        "subsystem": subsystem,
        "summary": summary,
        "source_refs": evidence,
        "metrics": normalize_metrics(metrics),
        "capture_method": capture_method,
        "capturing_actor": capturing_actor,
    }


def _identity_fields(obs):
    return {k: obs.get(k) for k in ("kind", "subsystem", "work_item_id",
            "thread_id", "council_id", "gate_id", "summary", "source_refs",
            "metrics")}


def capture(queue_root, obs):
    """Record one observation + its per-run occurrence in a single journal
    transaction, with ALL existence/idempotence/collision checks performed INSIDE
    the same writer lock as the commit (HIGH-3), so concurrent captures cannot both
    observe absence and duplicate-write. A same-short-id different-identity write is
    refused (collision)."""
    ensure_layout(queue_root)
    obs_id = safe_id(obs["observation_id"], "observation_id")
    ofile = observation_file(queue_root, obs_id)
    occ_id = "occ-" + content_sha256(
        {"observation_id": obs_id, "run_id": obs.get("run_id")})[:16]
    obs_bytes = canonical_bytes(obs) + b"\n"
    state = {}

    def _build(op):
        new_fact = not os.path.exists(ofile)
        if not new_fact:
            with open(ofile, "rb") as fh:
                existing = json.loads(fh.read().decode("utf-8"))
            if _identity_fields(existing) != _identity_fields(obs):
                raise IntegrityHalt(
                    "id_collision: {} exists with different identity".format(obs_id))
        occ_records, _ = _read_valid_lines(occurrences_path(queue_root))
        if any(r.get("occurrence_id") == occ_id for r in occ_records):
            state["result"] = {"observation_id": obs_id, "occurrence_id": occ_id,
                               "created_fact": False, "created_occurrence": False}
            return True  # non-None: commit skips; result is stashed in state
        if new_fact:
            op.replace_file("observations/{}.json".format(obs_id), obs)
            op.append_line("observations/index.jsonl", {
                "alf_record_version": ALF_RECORD_VERSION, "observation_id": obs_id,
                "sha256": sha256_hex(obs_bytes), "captured_at": obs["captured_at"],
                "run_id": obs.get("run_id"), "kind": obs["kind"]})
        op.append_line("observations/occurrences.jsonl", {
            "alf_record_version": ALF_RECORD_VERSION, "occurrence_id": occ_id,
            "observation_id": obs_id, "run_id": obs.get("run_id"),
            "captured_at": obs["captured_at"], "capture_method": obs["capture_method"],
            "capturing_actor": obs["capturing_actor"], "metrics": obs.get("metrics")})
        state["created_fact"] = new_fact
        return None

    Operation(queue_root, "capture", [obs_id, occ_id]).commit(build=_build)
    if "result" in state:
        return state["result"]
    return {"observation_id": obs_id, "occurrence_id": occ_id,
            "created_fact": state["created_fact"], "created_occurrence": True}


# --------------------------------------------------------------------------- #
# verify-hashes (packet sections 5, 8)
# --------------------------------------------------------------------------- #
def verify_hashes(queue_root):
    """Re-hash observation files against the index, verify every chain, and
    authenticate the expected-head checkpoint by ancestry proof. Returns a
    report with a top-level ok flag; never mutates."""
    problems = []
    q = queue_root
    if not os.path.isdir(alf_root(q)):
        return {"ok": True, "problems": [], "note": "no alf/ subtree"}
    # 1. chains
    for rel, path in _chained_files(q).items():
        problems += verify_chain(path)
    problems += verify_chain(journal_path(q))
    # 2. observation file bytes vs index sha256
    index_records, _ = _read_valid_lines(index_path(q))
    for rec in index_records:
        ofile = observation_file(q, rec["observation_id"])
        if not os.path.exists(ofile):
            problems.append("index cites {} but file is missing".format(
                rec["observation_id"]))
            continue
        with open(ofile, "rb") as fh:
            actual = sha256_hex(fh.read())
        if actual != rec["sha256"]:
            problems.append("observation {} bytes diverge from index sha256".format(
                rec["observation_id"]))
    # 3. checkpoint ancestry proof (best-effort authentication vs journal)
    problems += _verify_checkpoint(q)
    # 4. finding history chains + head-equals-rebuild (extended coverage)
    hist_dir = _p(q, "findings", "history")
    if os.path.isdir(hist_dir):
        for name in sorted(os.listdir(hist_dir)):
            if not name.endswith(".jsonl"):
                continue
            entry_id = name[:-6]
            try:
                safe_id(entry_id, "entry_id")  # reject malformed ALF filenames
            except AlfError:
                problems.append("malformed finding-history filename {!r}".format(name))
                continue
            hpath = os.path.join(hist_dir, name)
            problems += verify_chain(hpath)
            revs, _ = _read_valid_lines(hpath)
            if revs:
                head_file = _p(q, "findings", entry_id + ".json")
                if not os.path.exists(head_file):
                    problems.append("finding {} history without head".format(entry_id))
                    continue
                with open(head_file, "rb") as fh:
                    head_bytes = fh.read()
                if head_bytes != canonical_bytes(revs[-1].get("record")) + b"\n":
                    problems.append("finding {} head != last revision (rebuild "
                                    "mismatch)".format(entry_id))
    return {"ok": not problems, "problems": problems,
            "observation_count": len(index_records)}


def _verify_checkpoint(q):
    cp = checkpoint_path(q)
    if not os.path.exists(cp):
        return []
    problems = []
    try:
        with open(cp, "rb") as fh:
            cp_bytes = fh.read()
        checkpoint = json.loads(cp_bytes.decode("utf-8"))
    except (OSError, ValueError):
        return ["expected-heads.json unreadable"]
    # Authenticate: the live bytes must match the latest committed checkpoint
    # staged-write content hash in the journal.
    jrecords, _ = _read_valid_lines(journal_path(q))
    committed = {r["op_id"] for r in jrecords if r.get("event") == "op_commit"}
    latest_hash = None
    for r in jrecords:
        if r.get("event") == "op_begin" and r.get("op_id") in committed:
            for sw in r.get("staged_writes", []):
                if sw.get("target_path_rel") == "meta/expected-heads.json":
                    latest_hash = sw["content_sha256"]
    if latest_hash is not None and sha256_hex(cp_bytes) != latest_hash:
        problems.append("expected-heads.json not authenticated by journal "
                        "(possible tamper)")
        return problems
    # Ancestry proof: each recorded head must appear at its recorded position.
    for rel, exp in checkpoint.get("chained_files", {}).items():
        path = _p(q, *rel.split("/"))
        records, _ = _read_valid_lines(path)
        pos = exp["line_count"]
        if pos == 0:
            if records:
                # A checkpoint of empty is only valid if suffix-only growth; the
                # pre-transaction head was empty, later appends are permitted.
                pass
            continue
        if len(records) < pos:
            problems.append("{}: checkpoint head beyond live length".format(rel))
        elif records[pos - 1].get("line_sha256") != exp["head_line_sha256"]:
            problems.append("{}: checkpoint head not present at recorded position "
                            "(Tier 0 integrity)".format(rel))
    return problems


# --------------------------------------------------------------------------- #
# Read helpers for list/show
# --------------------------------------------------------------------------- #
def list_observations(queue_root):
    records, _ = _read_valid_lines(index_path(queue_root))
    return records


def show_observation(queue_root, obs_id):
    path = observation_file(queue_root, obs_id)
    if not os.path.exists(path):
        raise AlfError("no observation {}".format(obs_id))
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def _emit(obj):
    sys.stdout.write(json.dumps(obj, ensure_ascii=False) + "\n")


def _cmd_observe(args):
    refs = json.loads(args.source_refs) if args.source_refs else []
    metrics = json.loads(args.metrics) if args.metrics else None
    obs = build_observation(
        kind=args.kind, subsystem=args.subsystem, summary=args.summary,
        source_refs=refs, metrics=metrics, work_item_id=args.work_item_id,
        thread_id=args.thread_id, council_id=args.council_id, gate_id=args.gate_id,
        run_id=args.run_id, capture_method=args.capture_method,
        capturing_actor=args.actor)
    res = capture(args.queue_root, obs)
    _emit({"ok": True, "command": "observe", **res})
    return 0


def _cmd_list(args):
    _emit({"ok": True, "command": "list",
           "observations": list_observations(args.queue_root)})
    return 0


def _cmd_show(args):
    _emit({"ok": True, "command": "show",
           "observation": show_observation(args.queue_root, args.observation_id)})
    return 0


def _cmd_verify(args):
    report = verify_hashes(args.queue_root)
    _emit({"command": "verify-hashes", **report})
    return 0 if report.get("ok") else 1


def _cmd_recover(args):
    _emit({"command": "recover", **recover(args.queue_root)})
    return 0


def build_parser():
    p = argparse.ArgumentParser(
        prog="clearwright_alf",
        description="ALF Phase 1 durable store CLI (observe/list/show/"
                    "verify-hashes/recover). Additive; writes only under "
                    "QUEUE_ROOT/alf/.")
    sub = p.add_subparsers(dest="command", required=True)

    obs = sub.add_parser("observe", help="Capture one observation + occurrence.")
    obs.add_argument("queue_root")
    obs.add_argument("--kind", required=True)
    obs.add_argument("--subsystem", required=True)
    obs.add_argument("--summary", required=True)
    obs.add_argument("--source-refs", default=None,
                     help="JSON array of {ref,sha256,role,archived_location}.")
    obs.add_argument("--metrics", default=None, help="JSON metrics object.")
    obs.add_argument("--work-item-id", default=None)
    obs.add_argument("--thread-id", default=None)
    obs.add_argument("--council-id", default=None)
    obs.add_argument("--gate-id", default=None)
    obs.add_argument("--run-id", default=None)
    obs.add_argument("--capture-method", default="cli_explicit")
    obs.add_argument("--actor", default="claude")
    obs.set_defaults(func=_cmd_observe)

    lst = sub.add_parser("list", help="List observation index entries.")
    lst.add_argument("queue_root")
    lst.set_defaults(func=_cmd_list)

    show = sub.add_parser("show", help="Show one observation record.")
    show.add_argument("queue_root")
    show.add_argument("observation_id")
    show.set_defaults(func=_cmd_show)

    ver = sub.add_parser("verify-hashes", help="Re-hash + chain + checkpoint audit.")
    ver.add_argument("queue_root")
    ver.set_defaults(func=_cmd_verify)

    rec = sub.add_parser("recover", help="Complete/clean any interrupted operation.")
    rec.add_argument("queue_root")
    rec.set_defaults(func=_cmd_recover)
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except AlfError as exc:
        _emit({"ok": False, "error": str(exc), "type": type(exc).__name__})
        return 1


if __name__ == "__main__":
    sys.exit(main())
