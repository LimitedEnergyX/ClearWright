# Verification Council round 2 - ALF Phase 1 implementation (CODE-INCLUSIVE)

Round 1 (needs_revision) reviewed a summary only; this packet INLINES the actual
committed source and tests below so you can perform line-level implementation
review. Branch operator/alf-phase1-impl; full suite 1139 tests OK. Identify any
HIGH or CRITICAL defect explicitly. Do not manufacture findings or default to a
pessimistic verdict; approve when the evidence supports it.

## Reconciliation of round-1 findings (all addressed; source inlined below)

R1-1 "provide the actual code/diff": DONE. The full source of clearwright_alf.py,
clearwright_alf_synth.py, clearwright_alf_delta.py, clearwright_alf_review.py,
clearwright_dispatch_preflight.py, the exact council-engine enabler diff, and the
key tests are inlined verbatim below from committed tracked sources.

R1-2 "state vs attempt_state for normalized reasons": In run_round,
`state = attempt_state.setdefault(key, {"calls": 0, "grants": []})`. setdefault
returns the SAME object stored under attempt_state[key], so appending to
`state["normalized_reasons"]` mutates the durable per-attempt record; the
exhausted return reads it back via `attempt_state.get(_attempt_key(...))`. Same
object. Proven by test_alf_hardening.test_enabler_a_records_normalized_reason
(the returned normalized_reasons contain the classified reason).

R1-3 classifier no-leak: classify_reviewer_failure reads ONLY safe fields
(error/classification/reason/error_class/code) and returns ONLY one of 13 fixed
classes, never input text. Tests: test_never_returns_raw_text,
test_body_verdict_content_fields_never_read, test_output_is_always_a_fixed_class.

R1-4 malformed/attacker preallocation_signals: dispatch_eligibility treats absent
signals as eligible (guard decides at SEND) and a positive blocker only REFUSES;
malformed values are coerced by bool() and can never invert to an allow. enabler B
refuses BEFORE any attempt and absent signals proceed to the SEND-time egress
guard (independent, unchanged). Tests: test_attacker_signals_can_only_refuse_never_bypass,
test_enabler_b_refuses_before_any_attempt, test_absent_signals_do_not_refuse.

R1-5 multi-target partial journal recovery: test_multi_target_partial_recovery
stages two targets, applies one, crashes before op_commit, and recovery completes
the un-applied target exactly once (no double-apply) then commits; idempotent.

R1-6 external-effect audit: test_no_external_effect_calls_in_alf_modules proves the
ALF modules contain no subprocess/socket/urllib/requests/os.system/os.popen/
Popen/github/urlopen references; test_all_writes_composed_under_alf_root proves
every path helper composes strictly under QUEUE_ROOT/alf/.

R1-7 QUALIFIED additive wording (accepted): "no existing record shapes changed"
applies to the ADDITIVE alf/ subtree. The APPROVED enabler A does add a
`normalized_reasons` list to council attempt-state and a `normalized_reasons` map
to the exhausted-round return object; enabler B adds a `preallocation_refused`
path and `preallocation_signals` read. These are authorized enabler schema
additions to the council engine (CTA items 2-3), NOT part of the additive-alf
"no shape change" claim. No durable governance record (work items, messages,
councils, summaries, gates) shape is changed.

## Review questions
1. Journal acyclic anchoring + crash recovery: correct, fail-closed, exactly-once?
2. Operator-control boundary: can any inlined path create authority, governed work,
   GitHub state, mutate code, alter a disposition without a valid operator message,
   or bypass the promotion gate?
3. Do enablers A/B weaken any fail-closed control? Is classify free of leakage?
4. Any HIGH or CRITICAL implementation defect in the inlined source?

---
# INLINED COMMITTED SOURCE


## SOURCE: tools/clearwright_dispatch_preflight.py (commit 0c8636210f96aa45e4ba9589ae0c0c1a304a1d79 sha256 454fc9649e58edf29df4603ce55995f227a4396c426f5ce2d5248a588d6ec8a0)
```python
#!/usr/bin/env python3
"""tools/clearwright_dispatch_preflight.py: reviewer-failure classification +
pre-allocation dispatch eligibility (operator-directed enablers A and B).

These are ADDITIVE, fail-closed-preserving helpers used by the council engine:

  A. classify_reviewer_failure(): map a failed reviewer attempt to a safe,
     durable, normalized class so `reviewer_unavailable` stops being opaque and
     ALF can tell a safety refusal from provider flakiness. It NEVER returns
     secrets or raw provider bodies - only one of NORMALIZED_FAILURE_CLASSES.

  B. dispatch_eligibility(): a DETERMINISTIC pre-allocation check over signals
     that are known before any adapter call. It can only REFUSE earlier and more
     informatively than the downstream egress guard - it never authorizes a
     dispatch the guard would block, so no fail-closed control is weakened. When
     it refuses, the caller records the normalized reason and consumes NO council
     id or reviewer attempt.

Pure module: no imports from the council engine (avoids a cycle); the engine
imports these.
"""

NORMALIZED_FAILURE_CLASSES = (
    "policy_denial", "repo_not_approved", "provenance_unresolved",
    "sensitive_content_prohibited", "tripwire_refusal",
    "composition_or_hash_mismatch", "provider_unavailable", "auth_failure",
    "rate_limit", "timeout", "malformed_response", "adapter_failure", "unknown",
)

# Ordered (specific -> general) keyword rules over the safe signal text. Each rule
# is (predicate, class). Predicates take the lowercased signal string.
def _has_all(*subs):
    return lambda t: all(s in t for s in subs)


def _has_any(*subs):
    return lambda t: any(s in t for s in subs)


_RULES = (
    (_has_any("tripwire", "confusable"), "tripwire_refusal"),
    (_has_any("composition", "hash mismatch", "byte mismatch", "sha mismatch"),
     "composition_or_hash_mismatch"),
    (_has_all("repo", "not approved"), "repo_not_approved"),
    (_has_any("repo_unresolvable", "unresolvable", "provenance", "not git-tracked",
              "outside repo", "outside the repo"), "provenance_unresolved"),
    (_has_any("sensitive content", "sensitive_content", "prohibited", "embargo"),
     "sensitive_content_prohibited"),
    (_has_all("policy", "den"), "policy_denial"),
    (_has_any("rate limit", "rate_limit", "429", "too many requests"), "rate_limit"),
    (_has_any("timeout", "timed out", "deadline exceeded"), "timeout"),
    (_has_any("auth", "unauthorized", "401", "403", "api key", "credential",
              "permission denied"), "auth_failure"),
    (_has_any("malformed", "invalid json", "parse", "no_verdict", "invalid_verdict",
              "unvalidated", "source_mismatch", "schema"), "malformed_response"),
    (_has_any("connection", "network", "unavailable", "provider", "503", "502",
              "500", "cannot reach", "refused"), "provider_unavailable"),
    (_has_any("egress"), "policy_denial"),
    (_has_any("adapter"), "adapter_failure"),
)


def _safe_signal_text(result, status):
    """Assemble a lowercased signal string from SAFE, non-body fields only. Raw
    reviewer/provider content is never read here."""
    parts = []
    if status:
        parts.append(str(status))
    if isinstance(result, dict):
        for k in ("error", "classification", "reason", "error_class", "code"):
            v = result.get(k)
            if v is not None:
                parts.append(str(v))
    return " ".join(parts).lower()


def classify_reviewer_failure(result, status=None):
    """Return one of NORMALIZED_FAILURE_CLASSES for a failed reviewer attempt.
    `result` is the adapter result dict (or None); `status` is the evaluator's
    coarse status (missing/unavailable/no_verdict/invalid_verdict/...)."""
    if result is None and status in (None, "missing", "unavailable"):
        return "provider_unavailable"
    text = _safe_signal_text(result, status)
    if not text:
        return "unknown"
    for predicate, cls in _RULES:
        if predicate(text):
            return cls
    return "unknown"


# --------------------------------------------------------------------------- #
# Enabler B: deterministic pre-allocation eligibility.
# --------------------------------------------------------------------------- #
# Each check is (signal_key, expected_truthy, reason_when_failed). A signal that
# is absent defaults to eligible (True) so the check never INVENTS a blocker the
# caller did not assert - it only refuses on an explicitly-failed signal.
_ELIGIBILITY_CHECKS = (
    ("lane_authorized", True, "policy_denial"),
    ("classification_conflict", False, "policy_denial"),
    ("repo_approved", True, "repo_not_approved"),
    ("provenance_resolved", True, "provenance_unresolved"),
    ("sensitive_prohibited", False, "sensitive_content_prohibited"),
    ("composition_bound", True, "composition_or_hash_mismatch"),
    ("exact_bytes_ok", True, "composition_or_hash_mismatch"),
    ("tripwire_clear", True, "tripwire_refusal"),
    ("provider_ready", True, "provider_unavailable"),
    ("auth_ok", True, "auth_failure"),
)


def dispatch_eligibility(signals):
    """Deterministic pre-allocation eligibility over already-computed signals.
    Returns (ok: bool, normalized_reason: str|None). Refuses on the FIRST failed
    check (stable order). Absent signals are treated as eligible, so this can only
    refuse where the caller proved a blocker - it never weakens the guard."""
    for key, expected, reason in _ELIGIBILITY_CHECKS:
        if key not in signals:
            continue
        if bool(signals[key]) != expected:
            return (False, reason)
    return (True, None)


def refused_dispatch_record(*, phase, dispatch_lane, normalized_reason, detail=""):
    """A safe, durable record for a pre-allocation refusal - no council id and no
    reviewer attempt were consumed. Content-free beyond the normalized reason and
    a short detail (truncated). The caller writes this to the invocation log."""
    return {
        "command": "dispatch-refused-preallocation",
        "phase": phase,
        "dispatch_lane": dispatch_lane,
        "council_id": None,
        "attempt": 0,
        "normalized_reason": normalized_reason,
        "detail": (detail or "")[:200],
    }

```

## SOURCE: tools/clearwright_alf.py (commit 0c8636210f96aa45e4ba9589ae0c0c1a304a1d79 sha256 1fe76afbef5ea8f0e951ce0bb84fd3f4aa510434d4e15da3d17d6fb3a65292a0)
```python
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
import sys
from datetime import datetime, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import clearwright_writer_lock as cwl  # noqa: E402

ALF_RECORD_VERSION = 1
SENTINEL = "0" * 64
_REPLACE_RETRY_WINERRORS = (5, 32)

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
    return _p(q, "observations", obs_id + ".json")


def index_path(q):
    return _p(q, "observations", "index.jsonl")


def occurrences_path(q):
    return _p(q, "observations", "occurrences.jsonl")


def ledger_path(q):
    return _p(q, "attributions", "ledger.jsonl")


def journal_path(q):
    return _p(q, "journal", "journal.jsonl")


def staged_dir(q, op_id):
    return _p(q, "journal", "staged", op_id)


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
              _p(q, "findings", "history"), _p(q, "deltas"), _p(q, "specs")):
        os.makedirs(d, exist_ok=True)


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
        self._appends.append((target_rel, dict(payload)))
        return self

    def replace_file(self, target_rel, obj):
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

    def commit(self):
        ensure_layout(self.q)
        with cwl.write_token(self.q, purpose="alf"):
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
    """Idempotently apply one staged write. Used by both commit and recovery, so
    a replay converges byte-identically or fails closed."""
    path = _p(q, *target_rel.split("/"))
    if write_kind == "append_line":
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
    with cwl.write_token(q, purpose="alf-recover"):
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
                sf = os.path.join(sdir, entry["staged_file"])
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
    transaction. Idempotent: re-capturing identical facts is a verified no-op;
    a same-short-id different-identity write is refused (collision)."""
    ensure_layout(queue_root)
    obs_id = obs["observation_id"]
    ofile = observation_file(queue_root, obs_id)
    occ_id = "occ-" + content_sha256(
        {"observation_id": obs_id, "run_id": obs.get("run_id")})[:16]

    with cwl.write_token(queue_root, purpose="alf-capture"):
        new_fact = not os.path.exists(ofile)
        if not new_fact:
            with open(ofile, "rb") as fh:
                existing = json.loads(fh.read().decode("utf-8"))
            if _identity_fields(existing) != _identity_fields(obs):
                raise IntegrityHalt(
                    "id_collision: {} exists with different identity".format(obs_id))
        # Occurrence idempotence: same occurrence_id already present -> no-op.
        occ_records, _ = _read_valid_lines(occurrences_path(queue_root))
        if any(r.get("occurrence_id") == occ_id for r in occ_records):
            return {"observation_id": obs_id, "occurrence_id": occ_id,
                    "created_fact": False, "created_occurrence": False}

    obs_bytes = canonical_bytes(obs) + b"\n"
    op = Operation(queue_root, "capture", [obs_id, occ_id])
    if new_fact:
        # The immutable observation file is a replace_file into a non-existent
        # target (expected_mode absent), so it is journaled + crash-safe too.
        op.replace_file("observations/{}.json".format(obs_id), obs)
        op.append_line("observations/index.jsonl", {
            "alf_record_version": ALF_RECORD_VERSION,
            "observation_id": obs_id, "sha256": sha256_hex(obs_bytes),
            "captured_at": obs["captured_at"], "run_id": obs.get("run_id"),
            "kind": obs["kind"],
        })
    op.append_line("observations/occurrences.jsonl", {
        "alf_record_version": ALF_RECORD_VERSION,
        "occurrence_id": occ_id, "observation_id": obs_id,
        "run_id": obs.get("run_id"), "captured_at": obs["captured_at"],
        "capture_method": obs["capture_method"],
        "capturing_actor": obs["capturing_actor"],
        "metrics": obs.get("metrics"),
    })
    op.commit()
    return {"observation_id": obs_id, "occurrence_id": occ_id,
            "created_fact": new_fact, "created_occurrence": True}


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

```

## SOURCE: tools/clearwright_alf_synth.py (commit 0c8636210f96aa45e4ba9589ae0c0c1a304a1d79 sha256 b6c0899b6d55c2016cf68889ce79d512319f6d1923cde6401092860da5c7ed16)
```python
#!/usr/bin/env python3
"""tools/clearwright_alf_synth.py: ALF Phase 1 synthesis (Layer 2).

Builds on the Layer-1 store + operation journal in clearwright_alf.py. Provides:
  * priority-model-v1: the hash-bound, versioned scoring artifact (packet s15) and
    tier-policy-v1 deterministic tier assignment (packet s14).
  * the durable, versioned findings store: append-only per-finding revision log
    with a hash chain, a head file that always equals the last revision, and a
    byte-exact head-rebuild guarantee (packet s6).
  * crash-safe, gap-allowed entry_id allocation (packet s6).

Dedup, recurrence, regression, and the Run Improvement Delta are layered on these
primitives (added incrementally). Nothing here creates authority, governed work,
GitHub state, or mutates code (packet s7/s20): the module contains no such call.
"""
import argparse
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import clearwright_alf as alf  # noqa: E402

ALF_RECORD_VERSION = alf.ALF_RECORD_VERSION

FAILURE_CLASSES = {
    "authority_bypass_risk", "durable_record_integrity", "correctness",
    "operational_reliability", "stale_state", "broken_recovery", "work_blocker",
    "council_failure", "queue_failure", "lifecycle_failure", "deployment_failure",
    "operator_time", "execution_delay", "resource_waste", "poor_failure_reporting",
    "excess_deliberation", "clarity", "user_experience", "documentation",
    "maintainability",
}
BLAST_RADIUS = ("single_event", "single_run", "single_work_item",
                "single_subsystem", "multiple_subsystems", "all_councils",
                "system_wide", "external_or_public")

# --------------------------------------------------------------------------- #
# priority-model-v1: embedded verbatim (packet section 15). The stored file is
# the CANONICAL COMPACT serialization of this structure; priority_model_sha256 is
# computed over those bytes. Editing any string here is a model version change.
# --------------------------------------------------------------------------- #
MODEL_V1 = {
    "model_version": "priority-model-v1",
    "tier_policy_version": "tier-policy-v1",
    "blast_radius_ranks": {
        "single_event": 0, "single_run": 1, "single_work_item": 2,
        "single_subsystem": 3, "multiple_subsystems": 4, "all_councils": 5,
        "system_wide": 6, "external_or_public": 7},
    "tier_policy": {
        "evaluation": "top down, first match wins, default tier 3",
        "predicates": [
            {"tier": 0, "when": "risk_activity in (active,plausible) AND (exposure_class!=none OR mutation_class!=none OR record_integrity_class!=none OR ownership_conflict)"},
            {"tier": 1, "when": "authority_integrity_impact>=2 OR durable_record_integrity_impact>=2 OR failure_class in (authority_bypass_risk,durable_record_integrity,correctness,operational_reliability,stale_state,broken_recovery,work_blocker,council_failure,queue_failure,lifecycle_failure,deployment_failure)"},
            {"tier": 2, "when": "failure_class in (operator_time,execution_delay,resource_waste,poor_failure_reporting,excess_deliberation) OR operator_time_impact>=2 OR execution_delay_impact>=2 OR token_api_compute_impact>=2"},
            {"tier": 3, "when": "otherwise"}]},
    "weights": {
        "security_impact": 4, "authority_integrity_impact": 4,
        "durable_record_integrity_impact": 4, "reliability_impact": 3,
        "operator_time_impact": 2, "execution_delay_impact": 2,
        "token_api_compute_impact": 1},
    "radius_multiplier": 2,
    "recurrence_multiplier": 2, "recurrence_cap": 10,
    "regression_term": 12,
    "waste_multiplier": 2,
    "waste_bands": {
        "cumulative_operator_minutes": {"band1": 30, "band2": 120, "band3": 480},
        "cumulative_execution_delay": {"band1": 600, "band2": 3600, "band3": 14400},
        "cumulative_token_estimate": {"band1": 100000, "band2": 500000, "band3": 2000000},
        "cumulative_api_attempts_wasted": {"band1": 3, "band2": 10, "band3": 25},
        "cumulative_tool_attempts_wasted": {"band1": 10, "band2": 50, "band3": 200},
        "cumulative_council_attempts_wasted": {"band1": 2, "band2": 5, "band3": 10}},
    "waste_band_rule": "per counter: band 0 below band1; thresholds are INCLUSIVE lower bounds (value >= band1 gives 1, >= band2 gives 2, >= band3 gives 3); an absent or null metric is band 0; WB = maximum band across all six counters",
    "effort_points_enum": [1, 2, 3, 5, 8],
    "score_rule": "score = sum(weights[axis]*axis_value) + radius_multiplier*blast_radius_rank + recurrence_multiplier*min(occurrence_count-1,recurrence_cap) + regression_term*regression_flag + waste_multiplier*WB",
    "offline_recompute_rule": "recomputation MUST use the raw persisted cumulative counters and this artifact; a stored WB value is a cache and is never authoritative",
}
WASTE_COUNTERS = (
    "cumulative_operator_minutes", "cumulative_execution_delay",
    "cumulative_token_estimate", "cumulative_api_attempts_wasted",
    "cumulative_tool_attempts_wasted", "cumulative_council_attempts_wasted",
)


def model_bytes():
    return alf.canonical_bytes(MODEL_V1) + b"\n"


def model_sha256():
    return alf.sha256_hex(model_bytes())


def model_path(q):
    return alf._p(q, "meta", "priority-model-v1.json")


def materialize_model(q):
    """Write alf/meta/priority-model-v1.json (canonical compact) if absent, and
    return its sha256. Idempotent: refuses to overwrite a divergent existing
    model (that would be a silent version change)."""
    alf.ensure_layout(q)
    path = model_path(q)
    want = model_bytes()
    if os.path.exists(path):
        with open(path, "rb") as fh:
            have = fh.read()
        if have != want:
            raise alf.IntegrityHalt("priority-model-v1.json on disk diverges from "
                                    "the embedded model; refusing to overwrite")
        return model_sha256()
    with alf.cwl.write_token(q, purpose="alf-model"):
        alf._replace_bytes_fsync(path, want)
    return model_sha256()


# --------------------------------------------------------------------------- #
# tier-policy-v1 (packet section 14): deterministic, top-down, first match wins.
# --------------------------------------------------------------------------- #
def assign_tier(iv):
    """iv: input vector with predicate inputs + impact axes + failure_class.
    Returns a tier_decision record (packet s14/s15)."""
    fc = iv.get("failure_class")
    matched = None
    tier = 3
    if (iv.get("risk_activity") in ("active", "plausible") and (
            iv.get("exposure_class", "none") != "none"
            or iv.get("mutation_class", "none") != "none"
            or iv.get("record_integrity_class", "none") != "none"
            or bool(iv.get("ownership_conflict")))):
        tier, matched = 0, "tier0"
    elif (iv.get("authority_integrity_impact", 0) >= 2
          or iv.get("durable_record_integrity_impact", 0) >= 2
          or fc in ("authority_bypass_risk", "durable_record_integrity",
                    "correctness", "operational_reliability", "stale_state",
                    "broken_recovery", "work_blocker", "council_failure",
                    "queue_failure", "lifecycle_failure", "deployment_failure")):
        tier, matched = 1, "tier1"
    elif (fc in ("operator_time", "execution_delay", "resource_waste",
                 "poor_failure_reporting", "excess_deliberation")
          or iv.get("operator_time_impact", 0) >= 2
          or iv.get("execution_delay_impact", 0) >= 2
          or iv.get("token_api_compute_impact", 0) >= 2):
        tier, matched = 2, "tier2"
    else:
        tier, matched = 3, "tier3"
    return {
        "tier_policy_version": "tier-policy-v1",
        "priority_model_version": "priority-model-v1",
        "priority_model_sha256": model_sha256(),
        "input_vector": dict(iv),
        "matched_predicate": matched,
        "tier": tier,
        "computed_at": alf.now_iso(),
    }


def _waste_band(counter_name, value):
    if value is None:
        return 0
    bands = MODEL_V1["waste_bands"][counter_name]
    if value >= bands["band3"]:
        return 3
    if value >= bands["band2"]:
        return 2
    if value >= bands["band1"]:
        return 1
    return 0


def waste_band_max(finding):
    return max((_waste_band(c, finding.get(c)) for c in WASTE_COUNTERS),
               default=0)


def compute_score(finding, occurrence_count=None, regression_flag=0):
    w = MODEL_V1["weights"]
    base = sum(w[axis] * int(finding.get(axis, 0)) for axis in w)
    br = MODEL_V1["blast_radius_ranks"].get(finding.get("blast_radius"), 0)
    oc = occurrence_count if occurrence_count is not None else finding.get(
        "occurrence_count", 1)
    rec = MODEL_V1["recurrence_multiplier"] * min(
        max(int(oc) - 1, 0), MODEL_V1["recurrence_cap"])
    reg = MODEL_V1["regression_term"] * (1 if regression_flag else 0)
    wb = MODEL_V1["waste_multiplier"] * waste_band_max(finding)
    return base + MODEL_V1["radius_multiplier"] * br + rec + reg + wb


# --------------------------------------------------------------------------- #
# Findings store (packet section 6): head + append-only chained revision log.
# --------------------------------------------------------------------------- #
def finding_head_path(q, entry_id):
    return alf._p(q, "findings", entry_id + ".json")


def finding_history_path(q, entry_id):
    return alf._p(q, "findings", "history", entry_id + ".jsonl")


def _seq_path(q):
    return alf._p(q, "meta", "entry-seq.json")


def _next_entry_id(q):
    path = _seq_path(q)
    last = 0
    if os.path.exists(path):
        import json as _j
        with open(path, encoding="utf-8") as fh:
            last = _j.load(fh).get("last", 0)
    nxt = last + 1
    return "ALF-{:04d}".format(nxt), nxt


def _revision_record(finding, revision_no, revising_actor, reason, run_id, prev):
    payload = {
        "revision_no": revision_no,
        "revised_at": alf.now_iso(),
        "revising_actor": revising_actor,
        "reason": reason,
        "run_id": run_id,
        "record": finding,
        "prev_revision_sha256": prev,
    }
    payload["revision_sha256"] = alf.sha256_hex(alf.canonical_bytes(payload))
    return payload


def create_finding(q, finding, revising_actor="alf-synth", reason="created",
                   run_id=None):
    """Allocate a gap-allowed entry_id and write revision 1 + the head, all inside
    one journal transaction. Returns the entry_id."""
    materialize_model(q)
    entry_id, nxt = _next_entry_id(q)
    finding = dict(finding)
    finding["entry_id"] = entry_id
    finding["alf_record_version"] = ALF_RECORD_VERSION
    hist_rel = "findings/history/{}.jsonl".format(entry_id)
    prev, _ = alf.chain_head(finding_history_path(q, entry_id))
    revision = _revision_record(finding, 1, revising_actor, reason, run_id, prev)

    op = alf.Operation(q, "create_finding", [entry_id])
    op.append_line(hist_rel, revision)
    op.replace_file("findings/{}.json".format(entry_id), finding)
    op.replace_file("meta/entry-seq.json", {"last": nxt})
    op.commit()
    return entry_id


def update_finding(q, entry_id, mutate, revising_actor="alf-synth",
                   reason="update", run_id=None):
    """Append a new revision. `mutate` receives the current head record and returns
    the next record. Head is rewritten to equal the new revision's record."""
    head = load_finding(q, entry_id)
    if head is None:
        raise alf.AlfError("no finding {}".format(entry_id))
    nxt_record = dict(mutate(dict(head)))
    nxt_record["entry_id"] = entry_id
    nxt_record["alf_record_version"] = ALF_RECORD_VERSION
    hist_path = finding_history_path(q, entry_id)
    revisions = _read_history(q, entry_id)
    revision_no = revisions[-1]["revision_no"] + 1 if revisions else 1
    prev, _ = alf.chain_head(hist_path)
    revision = _revision_record(nxt_record, revision_no, revising_actor, reason,
                                run_id, prev)
    op = alf.Operation(q, "update_finding", [entry_id, str(revision_no)])
    op.append_line("findings/history/{}.jsonl".format(entry_id), revision)
    op.replace_file("findings/{}.json".format(entry_id), nxt_record)
    op.commit()
    return revision_no


def load_finding(q, entry_id):
    import json as _j
    path = finding_head_path(q, entry_id)
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as fh:
        return _j.load(fh)


def _read_history(q, entry_id):
    records, _ = alf._read_valid_lines(finding_history_path(q, entry_id))
    return records


def rebuild_head(q, entry_id):
    """Rebuild the head from the revision log: the last revision's record. Returns
    the canonical bytes (used to prove head == rebuild byte-for-byte)."""
    revisions = _read_history(q, entry_id)
    if not revisions:
        raise alf.AlfError("no history for {}".format(entry_id))
    return alf.canonical_bytes(revisions[-1]["record"]) + b"\n"


def head_equals_rebuild(q, entry_id):
    with open(finding_head_path(q, entry_id), "rb") as fh:
        head_bytes = fh.read()
    return head_bytes == rebuild_head(q, entry_id)


def list_findings(q):
    d = alf._p(q, "findings")
    out = []
    if not os.path.isdir(d):
        return out
    import json as _j
    for name in sorted(os.listdir(d)):
        if name.endswith(".json"):
            with open(os.path.join(d, name), encoding="utf-8") as fh:
                out.append(_j.load(fh))
    return out


# --------------------------------------------------------------------------- #
# dedup-policy-v1 (packet section 9): proposal-based, never silent for protected
# classes.
# --------------------------------------------------------------------------- #
DEDUP_STOPWORDS = sorted({
    "the", "a", "an", "and", "or", "of", "to", "in", "is", "are", "was", "were",
    "that", "this", "it", "its", "for", "on", "with", "as", "by", "at", "be",
    "not", "no", "when", "which", "from", "into", "than", "then", "so",
})
DEDUP_POLICY_V1 = {
    "policy_version": "dedup-policy-v1",
    "normalization": ("ascii-lowercase the root_cause; split on every "
                      "non-alphanumeric character except underscore; drop the "
                      "stopword list; signature is the sorted unique token set"),
    "stopwords": DEDUP_STOPWORDS,
    "thresholds": {"exact": "0.90", "jaccard_high": "0.80", "jaccard_mid": "0.60"},
}
PROTECTED_FAILURE_CLASSES = {"authority_bypass_risk", "durable_record_integrity"}
PROTECTED_IMPACT_AXES = ("security_impact", "authority_integrity_impact",
                         "durable_record_integrity_impact")


def dedup_policy_path(q):
    return alf._p(q, "meta", "dedup-policy-v1.json")


def materialize_dedup_policy(q):
    alf.ensure_layout(q)
    path = dedup_policy_path(q)
    want = alf.canonical_bytes(DEDUP_POLICY_V1) + b"\n"
    if os.path.exists(path):
        with open(path, "rb") as fh:
            if fh.read() != want:
                raise alf.IntegrityHalt("dedup-policy-v1.json diverges; refusing")
        return
    with alf.cwl.write_token(q, purpose="alf-dedup-policy"):
        alf._replace_bytes_fsync(path, want)


def dedup_signature(root_cause):
    toks = re.split(r"[^a-z0-9_]+", (root_cause or "").lower())
    stop = set(DEDUP_STOPWORDS)
    return sorted({t for t in toks if t and t not in stop})


def _jaccard(a, b):
    sa, sb = set(a), set(b)
    if not sa and not sb:
        return 1.0
    return len(sa & sb) / len(sa | sb)


def is_protected(finding):
    if finding.get("failure_class") in PROTECTED_FAILURE_CLASSES:
        return True
    return any(int(finding.get(a, 0) or 0) >= 2 for a in PROTECTED_IMPACT_AXES)


def propose_dedup(q, finding):
    """Highest-confidence duplicate_of proposal for `finding` against the store,
    or None. Never auto-merges; protected-class pairs are flagged so the caller
    holds them for the operator (silent-merge prohibition, packet s9)."""
    sig = dedup_signature(finding.get("root_cause", ""))
    key = (finding.get("subsystem"), finding.get("failure_class"))
    best = None
    for other in list_findings(q):
        if other.get("entry_id") == finding.get("entry_id"):
            continue
        if (other.get("subsystem"), other.get("failure_class")) != key:
            continue
        osig = dedup_signature(other.get("root_cause", ""))
        if osig == sig:
            conf = "0.90"
        else:
            j = _jaccard(sig, osig)
            conf = "0.80" if j >= 0.80 else ("0.60" if j >= 0.60 else None)
        if conf is None:
            continue
        if best is None or conf > best["confidence"]:
            best = {"duplicate_of": other["entry_id"], "confidence": conf,
                    "relationship": "duplicate_of", "proposed": True,
                    "dedup_policy_version": "dedup-policy-v1",
                    "protected": is_protected(finding) or is_protected(other)}
    return best


# --------------------------------------------------------------------------- #
# Attribution ledger + occurrence attribution (packet sections 8, 11, 13)
# --------------------------------------------------------------------------- #
_WASTE_FROM_METRIC = {
    "cumulative_operator_minutes": "operator_minutes",
    "cumulative_execution_delay": "execution_delay_seconds",
    "cumulative_token_estimate": "token_estimate",
    "cumulative_api_attempts_wasted": "api_attempts",
    "cumulative_tool_attempts_wasted": "tool_attempts",
    "cumulative_council_attempts_wasted": "council_attempts",
}


def attribution_id(occurrence_id, entry_id, attribution_type):
    return "att-" + alf.content_sha256({
        "occurrence_id": occurrence_id, "entry_id": entry_id,
        "attribution_type": attribution_type})[:16]


def _ledger_records(q):
    recs, _ = alf._read_valid_lines(alf.ledger_path(q))
    return recs


def ledger_has(q, att_id):
    return any(r.get("attribution_id") == att_id for r in _ledger_records(q))


def _runs_attributed(q, entry_id):
    return {r.get("run_id") for r in _ledger_records(q)
            if r.get("entry_id") == entry_id}


def _write_finding_revision(q, entry_id, nxt_record, reason, run_id, actor,
                            ledger_lines=None, op_kind="update_finding"):
    """Append a finding revision + head + optional ledger lines in ONE journal
    transaction, so a counter update and its ledger attribution are atomic."""
    revisions = _read_history(q, entry_id)
    revision_no = revisions[-1]["revision_no"] + 1 if revisions else 1
    prev, _ = alf.chain_head(finding_history_path(q, entry_id))
    revision = _revision_record(nxt_record, revision_no, actor, reason, run_id, prev)
    op = alf.Operation(q, op_kind, [entry_id, str(revision_no)])
    op.append_line("findings/history/{}.jsonl".format(entry_id), revision)
    op.replace_file("findings/{}.json".format(entry_id), nxt_record)
    for ll in (ledger_lines or []):
        op.append_line("attributions/ledger.jsonl", ll)
    op.commit()
    return revision_no


def _fold_metrics(record, metrics):
    for cum, src in _WASTE_FROM_METRIC.items():
        v = (metrics or {}).get(src)
        if v:
            record[cum] = int(record.get(cum, 0) or 0) + int(v)


def _iv_from_finding(f):
    iv = {"risk_activity": "historical", "failure_class": f.get("failure_class")}
    for axis in ("authority_integrity_impact", "durable_record_integrity_impact",
                 "operator_time_impact", "execution_delay_impact",
                 "token_api_compute_impact"):
        iv[axis] = int(f.get(axis, 0) or 0)
    return iv


def attribute_occurrence(q, entry_id, occurrence, attribution_type):
    """Fold one occurrence's metrics into a finding EXACTLY once (idempotent via
    the ledger). attribution_type in initial_evidence|recurrence|regression|waste."""
    occ_id = occurrence["occurrence_id"]
    run_id = occurrence.get("run_id")
    att_id = attribution_id(occ_id, entry_id, attribution_type)
    if ledger_has(q, att_id):
        return {"attributed": False, "attribution_id": att_id, "reason": "idempotent"}
    head = load_finding(q, entry_id)
    if head is None:
        raise alf.AlfError("no finding {}".format(entry_id))
    is_new_run = run_id not in _runs_attributed(q, entry_id)
    nxt = dict(head)
    if attribution_type in ("recurrence", "regression"):
        nxt["occurrence_count"] = int(nxt.get("occurrence_count", 0) or 0) + 1
    if is_new_run:
        nxt["affected_run_count"] = int(nxt.get("affected_run_count", 0) or 0) + 1
    if occurrence.get("captured_at"):
        nxt["last_seen_at"] = occurrence["captured_at"]
    _fold_metrics(nxt, occurrence.get("metrics"))
    ledger_line = {
        "alf_record_version": ALF_RECORD_VERSION, "attribution_id": att_id,
        "occurrence_id": occ_id, "observation_id": occurrence.get("observation_id"),
        "entry_id": entry_id, "attribution_type": attribution_type,
        "at": alf.now_iso(), "run_id": run_id}
    rn = _write_finding_revision(q, entry_id, nxt,
                                 "attribute:{}".format(attribution_type), run_id,
                                 "alf-synth", [ledger_line], op_kind="attribute")
    return {"attributed": True, "attribution_id": att_id, "revision_no": rn,
            "new_run": is_new_run}


# --------------------------------------------------------------------------- #
# Recurrence + regression (packet sections 11, 12)
# --------------------------------------------------------------------------- #
RELEASED_STATES = ("RELEASED", "MONITORING")


def set_release_baseline(q, entry_id):
    """Persist release_baseline the first time a finding reaches RELEASED (s12)."""
    head = load_finding(q, entry_id)
    if head is None or head.get("release_baseline"):
        return
    baseline = {"tier": head.get("priority_tier"),
                "score": head.get("priority_score"),
                "priority_model_version": "priority-model-v1", "at": alf.now_iso()}
    _write_finding_revision(q, entry_id, dict(head, release_baseline=baseline),
                            "release_baseline", None, "alf-synth")


def record_recurrence(q, entry_id, occurrence):
    """A repeat occurrence of an existing finding. RELEASED/MONITORING findings
    become regressions (reopen + floor); otherwise a plain recurrence."""
    head = load_finding(q, entry_id)
    if head is None:
        raise alf.AlfError("no finding {}".format(entry_id))
    if head.get("status") in RELEASED_STATES:
        return record_regression(q, entry_id, occurrence)
    return attribute_occurrence(q, entry_id, occurrence, "recurrence")


def record_regression(q, entry_id, occurrence):
    """Reopen a released/monitored finding to PRIORITIZED with the tier-and-score
    floor from its release_baseline (packet section 12). Idempotent per run."""
    occ_id = occurrence["occurrence_id"]
    run_id = occurrence.get("run_id")
    att_id = attribution_id(occ_id, entry_id, "regression")
    if ledger_has(q, att_id):
        return {"attributed": False, "attribution_id": att_id, "reason": "idempotent"}
    head = load_finding(q, entry_id)
    baseline = head.get("release_baseline") or {}
    nxt = dict(head)
    nxt["occurrence_count"] = int(nxt.get("occurrence_count", 0) or 0) + 1
    if run_id not in _runs_attributed(q, entry_id):
        nxt["affected_run_count"] = int(nxt.get("affected_run_count", 0) or 0) + 1
    if occurrence.get("captured_at"):
        nxt["last_seen_at"] = occurrence["captured_at"]
    _fold_metrics(nxt, occurrence.get("metrics"))
    nxt["status"] = "PRIORITIZED"
    recomputed_tier = assign_tier(_iv_from_finding(nxt))["tier"]
    baseline_tier = baseline.get("tier")
    eff_tier = min(recomputed_tier, baseline_tier) if baseline_tier is not None \
        else recomputed_tier
    recomputed_score = compute_score(nxt, occurrence_count=nxt["occurrence_count"],
                                     regression_flag=1)
    if baseline_tier is not None and eff_tier == baseline_tier \
            and baseline.get("score") is not None:
        eff_score = max(recomputed_score, baseline["score"])
    else:
        eff_score = recomputed_score
    nxt["priority_tier"] = eff_tier
    nxt["priority_score"] = eff_score
    rel = list(nxt.get("related_entries", []))
    rel.append({"entry_id": entry_id, "relationship": "regression_of"})
    nxt["related_entries"] = rel
    nxt["tier_decision"] = {
        "recomputed_tier": recomputed_tier, "recomputed_score": recomputed_score,
        "baseline_tier": baseline_tier, "baseline_score": baseline.get("score"),
        "effective_tier": eff_tier, "effective_score": eff_score,
        "regression_floor_applied": True, "computed_at": alf.now_iso()}
    ledger_line = {
        "alf_record_version": ALF_RECORD_VERSION, "attribution_id": att_id,
        "occurrence_id": occ_id, "observation_id": occurrence.get("observation_id"),
        "entry_id": entry_id, "attribution_type": "regression",
        "at": alf.now_iso(), "run_id": run_id}
    rn = _write_finding_revision(q, entry_id, nxt, "regression_reopen", run_id,
                                 "alf-synth", [ledger_line], op_kind="regression")
    return {"attributed": True, "attribution_id": att_id, "revision_no": rn,
            "effective_tier": eff_tier, "effective_score": eff_score}


# --------------------------------------------------------------------------- #
# CLI (read-only + model materialization; disposition/synthesis verbs later)
# --------------------------------------------------------------------------- #
def _emit(obj):
    import json as _j
    sys.stdout.write(_j.dumps(obj, ensure_ascii=False) + "\n")


def _cmd_model(args):
    _emit({"ok": True, "command": "model",
           "priority_model_sha256": materialize_model(args.queue_root)})
    return 0


def _cmd_list(args):
    _emit({"ok": True, "command": "list-findings",
           "findings": list_findings(args.queue_root)})
    return 0


def build_parser():
    p = argparse.ArgumentParser(
        prog="clearwright_alf_synth",
        description="ALF Phase 1 synthesis (findings, scoring, model).")
    sub = p.add_subparsers(dest="command", required=True)
    m = sub.add_parser("model", help="Materialize + hash priority-model-v1.")
    m.add_argument("queue_root")
    m.set_defaults(func=_cmd_model)
    lf = sub.add_parser("list-findings", help="List finding head records.")
    lf.add_argument("queue_root")
    lf.set_defaults(func=_cmd_list)
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except alf.AlfError as exc:
        _emit({"ok": False, "error": str(exc), "type": type(exc).__name__})
        return 1


if __name__ == "__main__":
    sys.exit(main())

```

## SOURCE: tools/clearwright_alf_delta.py (commit 0c8636210f96aa45e4ba9589ae0c0c1a304a1d79 sha256 e3f870163871f636fd62b6a5173836836459455db4b9ffedfb65c147aa1922cb)
```python
#!/usr/bin/env python3
"""tools/clearwright_alf_delta.py: Run Improvement Delta (packet section 17).

At a run boundary ALF writes alf/deltas/rid-<run>.json plus an IMMUTABLE,
self-sufficient input snapshot rid-<run>.input.json persisted at first generation.
The delta's deterministic content is a PURE FUNCTION of that snapshot: reruns
resolve the stored snapshot, re-read each content-addressed reference from the
append-only stores (hash-verifying it, fail-closed on divergence), and recompute
- equal is a verified no-op; a genuine difference is a REFUSED divergent rewrite
(Tier 1). generated_at and the anchors block are fixed at first generation and
preserved verbatim. An empty delta is still written so the missing-delta verifier
can prove every terminal governed run has one.
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import clearwright_alf as alf  # noqa: E402
import clearwright_alf_synth as syn  # noqa: E402

GENESIS = "0" * 64
ALF_RECORD_VERSION = alf.ALF_RECORD_VERSION


def delta_path(q, run_id):
    return alf._p(q, "deltas", "rid-{}.json".format(run_id))


def snapshot_path(q, run_id):
    return alf._p(q, "deltas", "rid-{}.input.json".format(run_id))


def _delta_chain_path(q):
    return alf._p(q, "meta", "delta-chain.json")


# --------------------------------------------------------------------------- #
# Immutable input snapshot (packet section 17)
# --------------------------------------------------------------------------- #
def _build_snapshot(q, run_id):
    occ_recs, _ = alf._read_valid_lines(alf.occurrences_path(q))
    occurrences = sorted(
        [{"occurrence_id": o["occurrence_id"], "observation_id": o["observation_id"],
          "run_id": o["run_id"], "line_sha256": o["line_sha256"]}
         for o in occ_recs if o.get("run_id") == run_id],
        key=lambda x: x["occurrence_id"])
    idx, _ = alf._read_valid_lines(alf.index_path(q))
    idx_by_id = {r["observation_id"]: r for r in idx}
    obs_ids = sorted({o["observation_id"] for o in occurrences})
    observations = [{"observation_id": oid, "sha256": idx_by_id[oid]["sha256"]}
                    for oid in obs_ids if oid in idx_by_id]
    led, _ = alf._read_valid_lines(alf.ledger_path(q))
    attributions = sorted(
        [{"attribution_id": a["attribution_id"],
          "attribution_type": a["attribution_type"], "line_sha256": a["line_sha256"]}
         for a in led if a.get("run_id") == run_id
         and a.get("attribution_type") != "delta_report"],
        key=lambda x: x["attribution_id"])
    finding_revisions = []
    baselines = []
    for f in syn.list_findings(q):
        eid = f["entry_id"]
        revs = syn._read_history(q, eid)
        run_revs = [r for r in revs if r.get("run_id") == run_id]
        if not run_revs:
            continue
        endpoint = max(run_revs, key=lambda r: r["revision_no"])
        finding_revisions.append({"entry_id": eid,
                                  "revision_no": endpoint["revision_no"],
                                  "revision_sha256": endpoint["revision_sha256"]})
        first_run_rn = min(r["revision_no"] for r in run_revs)
        prior = [r for r in revs if r["revision_no"] < first_run_rn]
        if prior:
            b = prior[-1]["record"]
            baselines.append({
                "entry_id": eid, "baseline_tier": b.get("priority_tier"),
                "baseline_score": b.get("priority_score"),
                "baseline_model_version": "priority-model-v1",
                "baseline_status": b.get("status"),
                "baseline_cumulative_waste": {c: int(b.get(c, 0) or 0)
                                              for c in syn.WASTE_COUNTERS}})
        else:
            baselines.append({
                "entry_id": eid, "baseline_tier": None, "baseline_score": None,
                "baseline_model_version": "priority-model-v1",
                "baseline_status": None,
                "baseline_cumulative_waste": {c: 0 for c in syn.WASTE_COUNTERS}})
    finding_revisions.sort(key=lambda x: x["entry_id"])
    baselines.sort(key=lambda x: x["entry_id"])
    return {
        "snapshot_version": 2, "run_id": run_id,
        "membership_rule": ("occurrences, attributions, and finding revisions whose "
                            "run_id equals this run_id; delta_report attributions "
                            "excluded; each set canonically ordered by id"),
        "occurrences": occurrences, "observations": observations,
        "attributions": attributions, "finding_revisions": finding_revisions,
        "baselines": baselines}


# --------------------------------------------------------------------------- #
# Deterministic derivation (pure function of the snapshot + hash-verified refs)
# --------------------------------------------------------------------------- #
def _resolve_finding_revision(q, eid, rn, expected_sha):
    for r in syn._read_history(q, eid):
        if r["revision_no"] == rn:
            if r["revision_sha256"] != expected_sha:
                raise alf.IntegrityHalt("finding {} rev {} sha divergent".format(eid, rn))
            return r["record"]
    raise alf.IntegrityHalt("finding {} rev {} missing".format(eid, rn))


def _derive(q, snapshot):
    run_id = snapshot["run_id"]
    idx, _ = alf._read_valid_lines(alf.index_path(q))
    first_seen = {r["observation_id"]: r["run_id"] for r in idx}
    new_observations = sorted(
        o["observation_id"] for o in snapshot["observations"]
        if first_seen.get(o["observation_id"]) == run_id)
    baseline_by = {b["entry_id"]: b for b in snapshot["baselines"]}
    new_findings, findings_priority_changed, cumulative_waste_changes = [], [], []
    for fr in snapshot["finding_revisions"]:
        eid = fr["entry_id"]
        rec = _resolve_finding_revision(q, eid, fr["revision_no"], fr["revision_sha256"])
        b = baseline_by.get(eid, {})
        if b.get("baseline_tier") is None and b.get("baseline_status") is None:
            new_findings.append(eid)
        old = (b.get("baseline_tier"), b.get("baseline_score"))
        new = (rec.get("priority_tier"), rec.get("priority_score"))
        if old != new:
            findings_priority_changed.append({
                "entry_id": eid, "old_tier": old[0], "old_score": old[1],
                "new_tier": new[0], "new_score": new[1],
                "model_version": "priority-model-v1", "reason": "synthesis"})
        per = {}
        base_w = b.get("baseline_cumulative_waste") or {}
        for c in syn.WASTE_COUNTERS:
            nv = int(rec.get(c, 0) or 0)
            ov = int(base_w.get(c, 0) or 0)
            if nv != ov:
                per[c] = {"delta": nv - ov, "new_total": nv}
        if per:
            cumulative_waste_changes.append({"entry_id": eid, "per_counter": per})
    led, _ = alf._read_valid_lines(alf.ledger_path(q))
    led_by_line = {a["line_sha256"]: a for a in led}
    regressions_detected = []
    for a in snapshot["attributions"]:
        if a["attribution_type"] == "regression":
            row = led_by_line.get(a["line_sha256"])
            if row:
                regressions_detected.append({
                    "entry_id": row["entry_id"],
                    "evidence_refs": [row.get("observation_id")]})
    return {
        "new_observations": new_observations,
        "new_findings": sorted(new_findings),
        "observations_merged_into_existing": [],
        "findings_priority_changed": sorted(findings_priority_changed,
                                            key=lambda x: x["entry_id"]),
        "released_fixes_revalidated": [],
        "regressions_detected": sorted(regressions_detected,
                                       key=lambda x: x["entry_id"]),
        "items_requiring_operator_review": [],
        "cumulative_waste_changes": sorted(cumulative_waste_changes,
                                           key=lambda x: x["entry_id"])}


def _content_with_meta(content, run_id, work_item_id):
    d = {"alf_record_version": ALF_RECORD_VERSION, "run_id": run_id,
         "work_item_id": work_item_id}
    d.update(content)
    return d


# --------------------------------------------------------------------------- #
# Anchors (packet sections 8, 17): pre-transaction heads, acyclic.
# --------------------------------------------------------------------------- #
def _structured_anchor(path):
    h, c = alf.chain_head(path)
    return {"head_line_sha256": h, "line_count": c}


def _findings_revision_heads_sha256(q):
    entries = []
    for f in syn.list_findings(q):
        eid = f["entry_id"]
        h, c = alf.chain_head(syn.finding_history_path(q, eid))
        entries.append({"entry_id": eid, "history_line_count": c,
                        "head_line_sha256": h})
    entries.sort(key=lambda x: x["entry_id"])
    return alf.sha256_hex(alf.canonical_bytes(entries))


def _last_delta_anchors(q):
    path = _delta_chain_path(q)
    if os.path.exists(path):
        with open(path, encoding="utf-8") as fh:
            return json.load(fh).get("last_anchors_sha256", GENESIS)
    return GENESIS


def _anchors(q, input_snapshot_sha256):
    return {
        "observations_index": _structured_anchor(alf.index_path(q)),
        "ledger": _structured_anchor(alf.ledger_path(q)),
        "journal": _structured_anchor(alf.journal_path(q)),
        "findings_revision_heads_sha256": _findings_revision_heads_sha256(q),
        "input_snapshot_sha256": input_snapshot_sha256,
        "prev_delta_anchors_sha256": _last_delta_anchors(q)}


# --------------------------------------------------------------------------- #
# Generation + idempotent rerun
# --------------------------------------------------------------------------- #
def generate_delta(q, run_id, work_item_id=None):
    alf.ensure_layout(q)
    spath, dpath = snapshot_path(q, run_id), delta_path(q, run_id)
    if os.path.exists(spath):
        with open(spath, encoding="utf-8") as fh:
            snapshot = json.load(fh)
        recomputed = _content_with_meta(_derive(q, snapshot), run_id, work_item_id)
        with open(dpath, encoding="utf-8") as fh:
            stored = json.load(fh)
        stored_content = {k: v for k, v in stored.items()
                          if k not in ("generated_at", "anchors")}
        if stored_content == recomputed:
            return {"status": "noop", "run_id": run_id}
        raise alf.IntegrityHalt(
            "Run Improvement Delta rerun divergence for run {} against its immutable "
            "snapshot (Tier 1 durable-record-integrity)".format(run_id))
    snapshot = _build_snapshot(q, run_id)
    input_snapshot_sha256 = alf.sha256_hex(alf.canonical_bytes(snapshot) + b"\n")
    anchors = _anchors(q, input_snapshot_sha256)
    delta = _content_with_meta(_derive(q, snapshot), run_id, work_item_id)
    delta["generated_at"] = alf.now_iso()
    delta["anchors"] = anchors
    anchors_sha = alf.sha256_hex(alf.canonical_bytes(anchors))
    op = alf.Operation(q, "delta_generate", [run_id])
    op.replace_file("deltas/rid-{}.input.json".format(run_id), snapshot)
    op.replace_file("deltas/rid-{}.json".format(run_id), delta)
    op.replace_file("meta/delta-chain.json", {"last_anchors_sha256": anchors_sha})
    op.commit()
    return {"status": "generated", "run_id": run_id,
            "input_snapshot_sha256": input_snapshot_sha256,
            "anchors_sha256": anchors_sha}


def load_delta(q, run_id):
    if not os.path.exists(delta_path(q, run_id)):
        return None
    with open(delta_path(q, run_id), encoding="utf-8") as fh:
        return json.load(fh)


def missing_delta_verifier(q, terminal_run_ids):
    """Report every terminal governed run lacking a delta as a Tier 1
    lifecycle_failure candidate (packet section 17)."""
    return [{"run_id": r, "tier": 1, "failure_class": "lifecycle_failure",
             "reason": "terminal governed run has no Run Improvement Delta"}
            for r in terminal_run_ids if not os.path.exists(delta_path(q, r))]


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def _emit(obj):
    sys.stdout.write(json.dumps(obj, ensure_ascii=False) + "\n")


def _cmd_generate(args):
    _emit({"ok": True, "command": "delta-generate",
           **generate_delta(args.queue_root, args.run_id, args.work_item_id)})
    return 0


def _cmd_show(args):
    d = load_delta(args.queue_root, args.run_id)
    _emit({"ok": d is not None, "command": "delta-show", "delta": d})
    return 0 if d is not None else 1


def build_parser():
    p = argparse.ArgumentParser(
        prog="clearwright_alf_delta",
        description="ALF Run Improvement Delta generation (packet section 17).")
    sub = p.add_subparsers(dest="command", required=True)
    g = sub.add_parser("generate", help="Generate (or idempotently verify) a delta.")
    g.add_argument("queue_root")
    g.add_argument("run_id")
    g.add_argument("--work-item-id", default=None)
    g.set_defaults(func=_cmd_generate)
    s = sub.add_parser("show", help="Show a delta.")
    s.add_argument("queue_root")
    s.add_argument("run_id")
    s.set_defaults(func=_cmd_show)
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except alf.AlfError as exc:
        _emit({"ok": False, "error": str(exc), "type": type(exc).__name__})
        return 1


if __name__ == "__main__":
    sys.exit(main())

```

## SOURCE: tools/clearwright_alf_review.py (commit 0c8636210f96aa45e4ba9589ae0c0c1a304a1d79 sha256 7ecb889d09d84cacdea8e5fd169331a8a5de3051570f5e76951ba1b3cfa32a74)
```python
#!/usr/bin/env python3
"""tools/clearwright_alf_review.py: ALF Phase 1 operator review + promotion (P1c).

Layer-2/Layer-3 boundary (packet section 16): findings SURFACE into OPERATOR_REVIEW
automatically (disposition-free); every operator-only transition is bound to a
durable INBOUND operator message that (a) exists, (b) has role operator + direction
inbound, (c) was created AFTER the finding revision it disposes, (d) names the
entry_id, and (e) has not been used for any prior ALF disposition (single use;
replay refused). APPROVED_FOR_PLANNING is additionally gated by the promotion
elements. Promote = the approval PLUS a state-neutral rendering of the governed-work
specification (section 18) - which changes no finding state and is re-runnable.

ALF creates no authority, no governed work item, no GitHub state, and no code
change here; it only records the operator's own recorded decision and renders a
document the OPERATOR later uses to open governed work through the normal workflow.
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import clearwright_alf as alf  # noqa: E402
import clearwright_alf_synth as syn  # noqa: E402

ALF_RECORD_VERSION = alf.ALF_RECORD_VERSION

# Operator-only transitions (packet section 16). Any transition not listed is
# refused with an exact reason.
OPERATOR_TRANSITIONS = {
    "OPERATOR_REVIEW": {"APPROVED_FOR_PLANNING", "DEFERRED", "REJECTED",
                        "ACCEPTED_RISK", "SUPERSEDED", "NOT_REPRODUCIBLE"},
    "TRIAGED": {"MERGED"},
}
DISPOSITION_FOR_STATUS = {
    "APPROVED_FOR_PLANNING": "approved", "DEFERRED": "deferred",
    "REJECTED": "rejected", "ACCEPTED_RISK": "accepted_risk",
    "SUPERSEDED": "superseded", "NOT_REPRODUCIBLE": "not_reproducible",
    "MERGED": "superseded"}


def dispositions_path(q):
    return alf._p(q, "meta", "dispositions.jsonl")


def _read_message(q, message_id):
    path = os.path.join(q, "communications", message_id + ".json")
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def _message_consumed(q, message_id):
    recs, _ = alf._read_valid_lines(dispositions_path(q))
    return any(r.get("operator_message_id") == message_id for r in recs)


# --------------------------------------------------------------------------- #
# Surfacing (automated, disposition-free): PRIORITIZED -> OPERATOR_REVIEW
# --------------------------------------------------------------------------- #
def surface_for_review(q, entry_id):
    head = syn.load_finding(q, entry_id)
    if head is None:
        raise alf.AlfError("no finding {}".format(entry_id))
    if head.get("status") != "PRIORITIZED":
        return {"surfaced": False, "reason": "not PRIORITIZED"}
    nxt = dict(head, status="OPERATOR_REVIEW", surfaced_at=alf.now_iso())
    syn._write_finding_revision(q, entry_id, nxt, "surface_for_review", None,
                                "alf-synth", op_kind="surface")
    return {"surfaced": True}


# --------------------------------------------------------------------------- #
# Promotion gate (packet section 16): all elements required for planning approval
# --------------------------------------------------------------------------- #
def promotion_gate_problems(finding):
    problems = []
    for field in ("permanent_resolution", "objective_acceptance_criteria",
                  "required_regression_tests"):
        if not finding.get(field):
            problems.append("missing {}".format(field))
    evidence = finding.get("evidence_references") or []
    if not any(e.get("role") == "observed_occurrence" for e in evidence):
        problems.append("no observed_occurrence evidence entry")
    conf = finding.get("root_cause_confidence")
    if not ((conf is not None and conf >= "0.50")
            or finding.get("investigation_requirement")):
        problems.append("root_cause_confidence < 0.50 and no investigation_requirement")
    for field in ("dependencies", "blockers"):
        if finding.get(field) is None:
            problems.append("{} not populated".format(field))
    return problems


# --------------------------------------------------------------------------- #
# Disposition (operator-only, message-bound)
# --------------------------------------------------------------------------- #
def _write_disposition(q, entry_id, nxt_record, reason, actor, disposition_line):
    revisions = syn._read_history(q, entry_id)
    revision_no = revisions[-1]["revision_no"] + 1 if revisions else 1
    prev, _ = alf.chain_head(syn.finding_history_path(q, entry_id))
    revision = syn._revision_record(nxt_record, revision_no, actor, reason, None, prev)
    op = alf.Operation(q, "disposition", [entry_id, str(revision_no)])
    op.append_line("findings/history/{}.jsonl".format(entry_id), revision)
    op.replace_file("findings/{}.json".format(entry_id), nxt_record)
    op.append_line("meta/dispositions.jsonl", disposition_line)
    op.commit()
    return revision_no


def dispose(q, entry_id, target_status, operator_message_id, actor="OPERATOR-0001",
            deferral_reason=None, review_date=None):
    head = syn.load_finding(q, entry_id)
    if head is None:
        raise alf.AlfError("no finding {}".format(entry_id))
    cur = head.get("status")
    if target_status not in OPERATOR_TRANSITIONS.get(cur, set()):
        raise alf.AlfError("illegal transition {} -> {}".format(cur, target_status))
    # operator-message binding
    msg = _read_message(q, operator_message_id)
    if msg is None:
        raise alf.AlfError("operator message {} not found".format(operator_message_id))
    if msg.get("role") != "operator" or msg.get("direction") != "inbound":
        raise alf.AlfError("message is not an inbound operator message")
    latest = syn._read_history(q, entry_id)[-1]
    if (msg.get("at") or "") <= (latest.get("revised_at") or ""):
        raise alf.AlfError("operator message must postdate the disposed revision")
    if entry_id not in (msg.get("message") or ""):
        raise alf.AlfError("operator message must name the entry_id")
    if _message_consumed(q, operator_message_id):
        raise alf.AlfError("operator message already used for a disposition (replay refused)")
    if target_status == "DEFERRED" and not (deferral_reason and review_date):
        raise alf.AlfError("DEFERRED requires deferral_reason and review_date")
    if target_status == "APPROVED_FOR_PLANNING":
        problems = promotion_gate_problems(head)
        if problems:
            raise alf.AlfError("promotion gate: " + "; ".join(problems))
    nxt = dict(head, status=target_status,
               operator_disposition=DISPOSITION_FOR_STATUS[target_status],
               last_operator_reviewed_at=alf.now_iso())
    if target_status == "DEFERRED":
        nxt["deferral_reason"] = deferral_reason
        nxt["review_date"] = review_date
    disposition_line = {
        "alf_record_version": ALF_RECORD_VERSION, "entry_id": entry_id,
        "target_status": target_status, "operator_message_id": operator_message_id,
        "actor": actor, "at": alf.now_iso()}
    rn = _write_disposition(q, entry_id, nxt, "dispose:{}".format(target_status),
                            actor, disposition_line)
    return {"disposed": True, "status": target_status, "revision_no": rn}


# --------------------------------------------------------------------------- #
# Spec rendering (packet section 18): state-neutral, re-runnable
# --------------------------------------------------------------------------- #
def render_spec(q, entry_id, version=1):
    head = syn.load_finding(q, entry_id)
    if head is None:
        raise alf.AlfError("no finding {}".format(entry_id))
    ev = head.get("evidence_references") or []
    lines = [
        "# Governed-work specification: {} (v{})".format(entry_id, version), "",
        "> Rendered by ALF from finding {}. This is input material for the operator "
        "to create authority and a work item through the normal ClearWright workflow. "
        "ALF posts nothing and grants nothing.".format(entry_id), "",
        "## Problem statement", head.get("problem_statement", ""), "",
        "## Permanent resolution", head.get("permanent_resolution", ""), "",
        "## Objective acceptance criteria", head.get("objective_acceptance_criteria", ""), "",
        "## Required regression tests", head.get("required_regression_tests", ""), "",
        "## Dependencies", json.dumps(head.get("dependencies", [])),
        "## Blockers", json.dumps(head.get("blockers", [])),
        "## Estimated effort", str(head.get("estimated_effort", "")), "",
        "## Evidence"]
    for e in ev:
        lines.append("- `{}` sha256 `{}` role {}".format(
            e.get("ref"), e.get("sha256"), e.get("role")))
    lines += [
        "", "## Proposed envelope skeleton",
        "- task_kind: governed (unless the operator directs otherwise)",
        "- approved_scope: <operator to draft from the resolution above>",
        "- excluded_actions: carries every applicable ALF prohibition", ""]
    body = "\n".join(lines) + "\n"
    path = alf._p(q, "specs", "spec-{}-v{}.md".format(entry_id, version))
    alf.ensure_layout(q)
    with alf.cwl.write_token(q, purpose="alf-spec"):
        alf._replace_bytes_fsync(path, body.encode("utf-8"))
    return {"spec_path": path, "rendered": True}


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def _emit(obj):
    sys.stdout.write(json.dumps(obj, ensure_ascii=False) + "\n")


def _cmd_surface(args):
    _emit({"ok": True, "command": "surface",
           **surface_for_review(args.queue_root, args.entry_id)})
    return 0


def _cmd_dispose(args):
    _emit({"ok": True, "command": "dispose", **dispose(
        args.queue_root, args.entry_id, args.status,
        operator_message_id=args.operator_message_id, actor=args.actor,
        deferral_reason=args.deferral_reason, review_date=args.review_date)})
    return 0


def _cmd_render(args):
    _emit({"ok": True, "command": "render-spec",
           **render_spec(args.queue_root, args.entry_id, args.version)})
    return 0


def build_parser():
    p = argparse.ArgumentParser(
        prog="clearwright_alf_review",
        description="ALF Phase 1 operator review, disposition, and spec rendering.")
    sub = p.add_subparsers(dest="command", required=True)
    s = sub.add_parser("surface", help="Surface a PRIORITIZED finding for review.")
    s.add_argument("queue_root")
    s.add_argument("entry_id")
    s.set_defaults(func=_cmd_surface)
    d = sub.add_parser("dispose", help="Record an operator-message-bound disposition.")
    d.add_argument("queue_root")
    d.add_argument("entry_id")
    d.add_argument("--status", required=True)
    d.add_argument("--operator-message-id", required=True)
    d.add_argument("--actor", default="OPERATOR-0001")
    d.add_argument("--deferral-reason", default=None)
    d.add_argument("--review-date", default=None)
    d.set_defaults(func=lambda a: _cmd_dispose(a))
    r = sub.add_parser("render-spec", help="Render a governed-work specification.")
    r.add_argument("queue_root")
    r.add_argument("entry_id")
    r.add_argument("--version", type=int, default=1)
    r.set_defaults(func=_cmd_render)
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except alf.AlfError as exc:
        _emit({"ok": False, "error": str(exc), "type": type(exc).__name__})
        return 1


if __name__ == "__main__":
    sys.exit(main())

```

## SOURCE: tests/test_alf_hardening.py (commit 0c8636210f96aa45e4ba9589ae0c0c1a304a1d79 sha256 8718ecdf073b5b11e2c6cc5ec944a44e1a2bba9856f366724ac18ec79430321d)
```python
"""Round-2 verification evidence (reviewer-requested): enabler-wiring behavior in
run_round, classifier no-leak, malformed/attacker preallocation_signals, multi-target
partial-journal recovery, and a deterministic external-effect audit."""
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "tools"))
import clearwright_alf as alf  # noqa: E402
import clearwright_alf_synth as syn  # noqa: E402
import clearwright_alf_delta as dlt  # noqa: E402
import clearwright_dispatch_preflight as cwdp  # noqa: E402
import clearwright_review_council as council  # noqa: E402


def _failed_attempt(classification="timeout"):
    # An unposted (failed) reviewer result: not validated -> counts as a failed
    # attempt, mirroring the codex "not posted" shape in test_review_council.
    return {"ok": True, "posted": False, "classification": classification,
            "telemetry": {}}


class EnablerWiringTest(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="alf-hw-")

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def _council(self, **kw):
        return council.create_council(self.root, thread_id="thr-t", work_item_id="wi-t",
                                      phase="verify", approved_scope="test scope",
                                      data_sensitivity="standard", **kw)

    def test_enabler_b_refuses_before_any_attempt(self):
        c = self._council()
        c["preallocation_signals"] = {"repo_approved": False}

        def _must_not_call(*a, **k):
            raise AssertionError("reviewer called despite pre-allocation refusal")

        r = council.run_round(self.root, c, "ctx", sleep=lambda *_: None,
                              gpt_fn=_must_not_call, codex_fn=_must_not_call)
        self.assertTrue(r.get("preallocation_refused"))
        self.assertEqual(r.get("normalized_reason"), "repo_not_approved")
        self.assertEqual(r["attempts"], {})  # no council id / attempt consumed

    def test_absent_signals_do_not_refuse_and_proceed_to_send_path(self):
        # An ordinary council (no preallocation_signals) must NOT be short-circuited;
        # it proceeds to the attempt/send path where the egress guard is the real gate.
        c = self._council()
        calls = {"n": 0}

        def _f(root, pt, **k):
            calls["n"] += 1
            return _failed_attempt()

        r = council.run_round(self.root, c, "ctx", sleep=lambda *_: None,
                              gpt_fn=_f, codex_fn=_f)
        self.assertFalse(r.get("preallocation_refused"))
        self.assertGreater(calls["n"], 0)

    def test_attacker_signals_can_only_refuse_never_bypass(self):
        # No preallocation_signals value can turn a refusal into an allow at the guard;
        # a positive blocker only refuses (fail-safe direction), and malformed values
        # never crash or invert the decision.
        self.assertEqual(cwdp.dispatch_eligibility({"repo_approved": False})[0], False)
        self.assertEqual(cwdp.dispatch_eligibility({"repo_approved": 0})[0], False)   # falsy
        self.assertEqual(cwdp.dispatch_eligibility({"sensitive_prohibited": "yes"})[0], False)  # truthy -> refuse
        self.assertEqual(cwdp.dispatch_eligibility({"repo_approved": object()})[0], True)  # truthy ok-signal passes
        self.assertEqual(cwdp.dispatch_eligibility({})[0], True)  # absent -> guard decides at SEND

    def test_enabler_a_records_normalized_reason(self):
        c = self._council()

        def _f(root, pt, **k):
            return _failed_attempt("request timed out")

        r = council.run_round(self.root, c, "ctx", sleep=lambda *_: None,
                              gpt_fn=_f, codex_fn=_f)
        self.assertIn("normalized_reasons", r)
        allr = (r["normalized_reasons"].get("gpt", [])
                + r["normalized_reasons"].get("codex", []))
        self.assertIn("timeout", allr)


class ClassifierNoLeakTest(unittest.TestCase):
    def test_never_returns_raw_text(self):
        secret = "sk-live-ABC123 secret token bearer 429 rate limit"
        cls = cwdp.classify_reviewer_failure({"error": secret})
        self.assertIn(cls, cwdp.NORMALIZED_FAILURE_CLASSES)
        self.assertNotIn("sk-live", cls)
        self.assertNotIn("ABC123", cls)

    def test_body_verdict_content_fields_never_read(self):
        # Only safe fields (error/classification/reason/error_class/code) are read;
        # a secret hidden in body/verdict/content cannot influence or leak into output.
        cls = cwdp.classify_reviewer_failure(
            {"body": "sk-secret 429", "verdict": {"x": "timeout"},
             "content": "authorization 401", "stderr": "traceback secret"})
        self.assertEqual(cls, "unknown")

    def test_output_is_always_a_fixed_class(self):
        for probe in (None, {}, {"error": "boom"}, {"reason": "weird"},
                      {"classification": "egress_blocked"}):
            self.assertIn(cwdp.classify_reviewer_failure(probe),
                          cwdp.NORMALIZED_FAILURE_CLASSES)

    def test_refused_record_truncates_and_has_no_council_id(self):
        rec = cwdp.refused_dispatch_record(phase="verify", dispatch_lane="user",
                                           normalized_reason="repo_not_approved",
                                           detail="x" * 1000)
        self.assertIsNone(rec["council_id"])
        self.assertEqual(rec["attempt"], 0)
        self.assertLessEqual(len(rec["detail"]), 200)


class MultiTargetRecoveryTest(unittest.TestCase):
    def setUp(self):
        self.q = tempfile.mkdtemp(prefix="alf-mt-")
        alf.ensure_layout(self.q)

    def tearDown(self):
        shutil.rmtree(self.q, ignore_errors=True)

    def test_multi_target_partial_recovery(self):
        # Two targets staged; the FIRST is applied; crash before op_commit; recovery
        # completes the SECOND exactly once and commits (no double-apply of the first).
        idx, occ = alf.index_path(self.q), alf.occurrences_path(self.q)
        p1, c1 = alf.chain_head(idx)
        rec1 = alf.chained_record({"alf_record_version": 1, "observation_id": "obs-a",
                                   "sha256": "a" * 64, "captured_at": alf.now_iso(),
                                   "run_id": "r", "kind": "executor_note"}, p1)
        line1 = alf.canonical_line(rec1)
        p2, c2 = alf.chain_head(occ)
        rec2 = alf.chained_record({"alf_record_version": 1, "occurrence_id": "occ-a",
                                   "observation_id": "obs-a", "run_id": "r",
                                   "captured_at": alf.now_iso(),
                                   "capture_method": "cli_explicit",
                                   "capturing_actor": "t", "metrics": None}, p2)
        line2 = alf.canonical_line(rec2)
        op_id = "op-multitarget01"
        sdir = alf.staged_dir(self.q, op_id)
        os.makedirs(sdir, exist_ok=True)
        sf1 = "0-" + alf.sha256_hex(line1)[:16]
        sf2 = "1-" + alf.sha256_hex(line2)[:16]
        alf._write_bytes_fsync(os.path.join(sdir, sf1), line1)
        alf._write_bytes_fsync(os.path.join(sdir, sf2), line2)
        alf._append_line_fsync(idx, line1)  # apply ONLY the first target
        jpath = alf.journal_path(self.q)
        jp, _ = alf.chain_head(jpath)
        begin = alf.chained_record({
            "op_id": op_id, "operation_kind": "test", "subject_ids": ["x"],
            "staged_writes": [
                {"target_path_rel": "observations/index.jsonl", "staged_file": sf1,
                 "content_sha256": alf.sha256_hex(line1), "write_kind": "append_line",
                 "expected_prev_line_sha256": p1, "expected_chain_position": c1 + 1},
                {"target_path_rel": "observations/occurrences.jsonl", "staged_file": sf2,
                 "content_sha256": alf.sha256_hex(line2), "write_kind": "append_line",
                 "expected_prev_line_sha256": p2, "expected_chain_position": c2 + 1}],
            "at": alf.now_iso(), "event": "op_begin"}, jp)
        alf._append_line_fsync(jpath, alf.canonical_line(begin))

        report = alf.recover(self.q)
        self.assertIn(op_id, report["recovered"])
        i, _ = alf._read_valid_lines(idx)
        o, _ = alf._read_valid_lines(occ)
        self.assertEqual(sum(1 for r in i if r.get("observation_id") == "obs-a"), 1)
        self.assertEqual(sum(1 for r in o if r.get("occurrence_id") == "occ-a"), 1)
        self.assertEqual(alf.recover(self.q)["recovered"], [])  # idempotent


class ExternalEffectAuditTest(unittest.TestCase):
    ALF_MODULES = ["clearwright_alf.py", "clearwright_alf_synth.py",
                   "clearwright_alf_delta.py", "clearwright_alf_review.py",
                   "clearwright_alf_seed.py", "clearwright_alf_gqfixture.py",
                   "clearwright_dispatch_preflight.py"]
    FORBIDDEN = ["import subprocess", "import socket", "import urllib",
                 "import requests", "os.system(", "os.popen(", "subprocess.",
                 "Popen(", "urlopen(", "api.github", "github.com", "gh api"]

    def test_no_external_effect_calls_in_alf_modules(self):
        tools = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "tools")
        for mod in self.ALF_MODULES:
            with open(os.path.join(tools, mod), encoding="utf-8") as fh:
                src = fh.read()
            for bad in self.FORBIDDEN:
                self.assertNotIn(bad, src, "{} references forbidden {!r}".format(mod, bad))

    def test_all_writes_composed_under_alf_root(self):
        q = tempfile.mkdtemp(prefix="alf-root-")
        try:
            root = alf.alf_root(q)
            for p in (alf.observation_file(q, "obs-x"), alf.index_path(q),
                      alf.occurrences_path(q), alf.ledger_path(q),
                      alf.journal_path(q), alf.checkpoint_path(q),
                      dlt.delta_path(q, "r"), syn.finding_head_path(q, "ALF-0001"),
                      syn.finding_history_path(q, "ALF-0001"), syn.model_path(q)):
                self.assertTrue(os.path.abspath(p).startswith(os.path.abspath(root)),
                                "{} escapes alf root".format(p))
        finally:
            shutil.rmtree(q, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()

```

## SOURCE: tests/test_alf_store.py (commit 0c8636210f96aa45e4ba9589ae0c0c1a304a1d79 sha256 e7582adbbce7048392ea1b0e56721c492986450cbf41a44f2d0190f785565eb5)
```python
"""P1a tests for the ALF durable store (tools/clearwright_alf.py):
canonical serialization, per-line hash chains, immutable observation capture,
cross-run occurrences, verify-hashes tamper detection, and operation-journal
crash recovery."""
import json
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "tools"))
import clearwright_alf as alf  # noqa: E402


class AlfStoreTest(unittest.TestCase):
    def setUp(self):
        self.q = tempfile.mkdtemp(prefix="alf-test-")
        alf.ensure_layout(self.q)

    def tearDown(self):
        shutil.rmtree(self.q, ignore_errors=True)

    def _obs(self, summary="a fact", run_id="run-1", **kw):
        return alf.build_observation(
            kind=kw.get("kind", "council_outcome"),
            subsystem=kw.get("subsystem", "council_engine"),
            summary=summary, run_id=run_id,
            source_refs=kw.get("source_refs", [
                {"ref": "council-outcome:c1", "sha256": "a" * 64,
                 "role": "observed_occurrence"}]),
            metrics=kw.get("metrics"))

    # -- canonical serialization -------------------------------------------- #
    def test_float_is_refused_in_hashed_record(self):
        with self.assertRaises(alf.AlfError):
            alf.canonical_bytes({"x": 1.5})
        # booleans and ints are fine
        alf.canonical_bytes({"a": True, "b": 3, "c": None, "d": "s"})

    def test_canonical_is_deterministic_and_sorted(self):
        a = alf.canonical_bytes({"b": 1, "a": 2})
        b = alf.canonical_bytes({"a": 2, "b": 1})
        self.assertEqual(a, b)
        self.assertEqual(a, b'{"a":2,"b":1}')

    # -- observation identity + capture ------------------------------------- #
    def test_identity_excludes_capture_context(self):
        o1 = self._obs(run_id="run-1")
        o2 = self._obs(run_id="run-2")
        self.assertEqual(o1["observation_id"], o2["observation_id"])

    def test_capture_creates_fact_and_occurrence(self):
        res = alf.capture(self.q, self._obs())
        self.assertTrue(res["created_fact"])
        self.assertTrue(res["created_occurrence"])
        self.assertEqual(len(alf.list_observations(self.q)), 1)
        self.assertTrue(alf.verify_hashes(self.q)["ok"])

    def test_recapture_same_run_is_noop(self):
        obs = self._obs()
        alf.capture(self.q, obs)
        res = alf.capture(self.q, obs)
        self.assertFalse(res["created_fact"])
        self.assertFalse(res["created_occurrence"])
        # exactly one occurrence line
        recs, _ = alf._read_valid_lines(alf.occurrences_path(self.q))
        self.assertEqual(len(recs), 1)

    def test_cross_run_occurrence(self):
        alf.capture(self.q, self._obs(run_id="run-A"))
        res = alf.capture(self.q, self._obs(run_id="run-B"))
        self.assertFalse(res["created_fact"])      # one deduplicated fact
        self.assertTrue(res["created_occurrence"])  # but a new occurrence
        self.assertEqual(len(alf.list_observations(self.q)), 1)
        recs, _ = alf._read_valid_lines(alf.occurrences_path(self.q))
        self.assertEqual(len(recs), 2)
        self.assertEqual({r["run_id"] for r in recs}, {"run-A", "run-B"})
        self.assertTrue(alf.verify_hashes(self.q)["ok"])

    def test_id_collision_refused(self):
        obs = self._obs()
        alf.capture(self.q, obs)
        # Corrupt the stored file so its identity fields diverge from the id.
        path = alf.observation_file(self.q, obs["observation_id"])
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        data["summary"] = "tampered different fact"
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(data, fh)
        with self.assertRaises(alf.IntegrityHalt):
            alf.capture(self.q, self._obs(run_id="run-Z"))

    # -- hash chains + verify ----------------------------------------------- #
    def test_chain_intact_after_multiple_captures(self):
        for i in range(4):
            alf.capture(self.q, self._obs(summary="fact-{}".format(i)))
        self.assertEqual(alf.verify_chain(alf.index_path(self.q)), [])
        self.assertEqual(alf.verify_chain(alf.occurrences_path(self.q)), [])
        self.assertTrue(alf.verify_hashes(self.q)["ok"])

    def test_verify_detects_observation_byte_tamper(self):
        obs = self._obs()
        alf.capture(self.q, obs)
        path = alf.observation_file(self.q, obs["observation_id"])
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(" ")  # change file bytes without touching the index
        report = alf.verify_hashes(self.q)
        self.assertFalse(report["ok"])
        self.assertTrue(any("diverge from index" in p for p in report["problems"]))

    def test_verify_detects_chain_break(self):
        alf.capture(self.q, self._obs(summary="one"))
        alf.capture(self.q, self._obs(summary="two"))
        # Rewrite the index tampering an interior line's content.
        path = alf.index_path(self.q)
        recs, _ = alf._read_valid_lines(path)
        recs[0]["kind"] = "reviewer_attempt"  # break the recorded line hash
        with open(path, "w", encoding="utf-8") as fh:
            for r in recs:
                fh.write(json.dumps(r, sort_keys=True, separators=(",", ":")) + "\n")
        self.assertNotEqual(alf.verify_chain(path), [])

    # -- operation-journal crash recovery ----------------------------------- #
    def test_recover_completes_interrupted_append(self):
        # Simulate a crash AFTER op_begin + staging but BEFORE apply/commit.
        occ_path = alf.occurrences_path(self.q)
        prev, count = alf.chain_head(occ_path)
        payload = {"alf_record_version": 1, "occurrence_id": "occ-manual",
                   "observation_id": "obs-manual", "run_id": "run-x",
                   "captured_at": alf.now_iso(), "capture_method": "cli_explicit",
                   "capturing_actor": "test", "metrics": None}
        rec = alf.chained_record(payload, prev)
        line = alf.canonical_line(rec)
        content_sha = alf.sha256_hex(line)
        op_id = "op-manualtest0001"
        sdir = alf.staged_dir(self.q, op_id)
        os.makedirs(sdir, exist_ok=True)
        staged_file = "0-" + content_sha[:16]
        alf._write_bytes_fsync(os.path.join(sdir, staged_file), line)
        jpath = alf.journal_path(self.q)
        jprev, _ = alf.chain_head(jpath)
        begin = alf.chained_record({
            "op_id": op_id, "operation_kind": "test", "subject_ids": ["x"],
            "staged_writes": [{
                "target_path_rel": "observations/occurrences.jsonl",
                "staged_file": staged_file, "content_sha256": content_sha,
                "write_kind": "append_line", "expected_prev_line_sha256": prev,
                "expected_chain_position": count + 1}],
            "at": alf.now_iso(), "event": "op_begin"}, jprev)
        alf._append_line_fsync(jpath, alf.canonical_line(begin))

        report = alf.recover(self.q)
        self.assertIn(op_id, report["recovered"])
        recs, _ = alf._read_valid_lines(occ_path)
        self.assertTrue(any(r.get("occurrence_id") == "occ-manual" for r in recs))
        # Recovery is idempotent: a second pass finds nothing to do.
        self.assertEqual(alf.recover(self.q)["recovered"], [])
        self.assertFalse(os.path.isdir(sdir))  # staging cleaned

    def test_recover_missing_staged_bytes_fails_closed(self):
        jpath = alf.journal_path(self.q)
        jprev, _ = alf.chain_head(jpath)
        begin = alf.chained_record({
            "op_id": "op-broken0001", "operation_kind": "test",
            "subject_ids": ["x"], "staged_writes": [{
                "target_path_rel": "observations/occurrences.jsonl",
                "staged_file": "0-deadbeefdeadbeef",
                "content_sha256": "d" * 64, "write_kind": "append_line",
                "expected_prev_line_sha256": alf.SENTINEL,
                "expected_chain_position": 1}],
            "at": alf.now_iso(), "event": "op_begin"}, jprev)
        alf._append_line_fsync(jpath, alf.canonical_line(begin))
        with self.assertRaises(alf.IntegrityHalt):
            alf.recover(self.q)

    def test_normal_capture_leaves_no_staging(self):
        alf.capture(self.q, self._obs())
        staged_root = os.path.join(alf.alf_root(self.q), "journal", "staged")
        self.assertEqual(os.listdir(staged_root), [])
        # journal has op_begin + op_commit for the capture
        jrecs, _ = alf._read_valid_lines(alf.journal_path(self.q))
        events = [r["event"] for r in jrecs]
        self.assertIn("op_begin", events)
        self.assertIn("op_commit", events)
        self.assertEqual(alf.verify_chain(alf.journal_path(self.q)), [])


if __name__ == "__main__":
    unittest.main()

```

## COUNCIL-ENGINE ENABLER DIFF
```diff
diff --git a/tools/clearwright_review_council.py b/tools/clearwright_review_council.py
index 08e12e1..021c211 100644
--- a/tools/clearwright_review_council.py
+++ b/tools/clearwright_review_council.py
@@ -880,6 +880,22 @@ def run_round(root, council, base_context, *, model=None, repo=None, timeout=90,
     # bound components (below): NO _augment_context / _guidance_header prose is
     # appended, so neither helper is ever called on this path.
     its_lane = (council.get("dispatch_lane") == "internal_technical")
+    # Enabler B (pre-allocation dispatch eligibility): if DETERMINISTIC signals on
+    # the council prove a blocker, refuse BEFORE any council id or reviewer attempt
+    # is spent, recording a safe normalized reason. Absent signals pass through
+    # unchanged; the egress guard still independently re-enforces every rule at
+    # send (fail-closed). This can only refuse earlier - it never authorizes.
+    import clearwright_dispatch_preflight as cwdp
+    _elig_ok, _elig_reason = cwdp.dispatch_eligibility(
+        council.get("preallocation_signals") or {})
+    if not _elig_ok:
+        log_invocation(root, cwdp.refused_dispatch_record(
+            phase=phase, dispatch_lane=council.get("dispatch_lane"),
+            normalized_reason=_elig_reason, detail="pre-allocation eligibility"))
+        return {"committed": False, "substantive": False, "round": round_no,
+                "hard_gate": False, "statuses": {}, "attempts": {},
+                "preallocation_refused": True, "normalized_reason": _elig_reason,
+                "reason": "pre-allocation dispatch ineligible: " + _elig_reason}
     if its_lane:
         context = base_context
     else:
@@ -1414,6 +1430,11 @@ def run_round(root, council, base_context, *, model=None, repo=None, timeout=90,
                 "error_class": (None if _validated(result) else
                                 (result or {}).get("error") or (result or {}).get("classification")),
             })
+            # Enabler A: persist a SAFE normalized failure class per failed attempt
+            # so `reviewer_unavailable` is no longer opaque (no secrets/raw bodies).
+            if not _validated(result) and not (result or {}).get("hard_gate"):
+                state.setdefault("normalized_reasons", []).append(
+                    cwdp.classify_reviewer_failure(result))
             if (result or {}).get("hard_gate"):
                 break
             if _validated(result):
@@ -1505,9 +1526,15 @@ def run_round(root, council, base_context, *, model=None, repo=None, timeout=90,
 
     _persist_council(root, council)
     exhausted = [rev for rev, st in statuses.items() if st == "attempts_exhausted"]
+    # Enabler A (approved enabler schema addition per CTA items 2-3, NOT the
+    # additive-alf subtree): surface the durable normalized failure reasons per
+    # reviewer. This adds normalized_reasons to attempt_state and to this return.
+    normalized_reasons = {
+        rev: attempt_state.get(_attempt_key(round_no, rev), {}).get("normalized_reasons", [])
+        for rev in statuses}
     return {"committed": False, "substantive": False, "round": round_no,
             "hard_gate": False, "statuses": statuses, "attempts": attempts_used,
-            "exhausted": exhausted,
+            "exhausted": exhausted, "normalized_reasons": normalized_reasons,
             "reason": ("reviewer attempt budget exhausted for: {}; the round was not "
                        "counted. Continue with a new council or an explicit "
                        "operator-authorized recovery grant.").format(", ".join(exhausted))

```
