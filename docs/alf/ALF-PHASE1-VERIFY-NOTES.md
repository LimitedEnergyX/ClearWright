# Verification Council — ALF Phase 1 implementation

You are reviewing the IMPLEMENTATION of ALF Phase 1 against the plan-gate-approved
planning packet (PR #85, docs/alf/ALF-PHASE1-PLANNING-PACKET.md) and the operator
CTA scope. Branch operator/alf-phase1-impl (6 commits) vs main 427558c. Full test
suite: **1126 tests OK (skipped=1)**, including tests/test_naming_gate.py. Assess
correctness, faithfulness to the approved design, integrity of the immutability +
hash-binding + crash-recovery model, the operator-control boundary, and whether
enablers A/B weaken any fail-closed control. Identify any HIGH or CRITICAL defect
explicitly. Answer the review questions.

## Approved scope (CTA alf-phase1-impl-cta-20260724)
Repo D:/AI-Agents/ClearWright/ClearWright only. (1) plan-gate-approved additive ALF
Phase 1 (new alf/ subtree under QUEUE_ROOT; no existing record shapes changed;
alf_record_version 1); (2) operator-directed enablers A (durable normalized
reviewer-attempt failure reasons) and B (pre-allocation dispatch eligibility) which
modify the council/egress/dispatch path but must not weaken any fail-closed control;
(3) tests, this verification, PR, merge, graceful deploy, post-deploy verify. GQ
records are immutable evidence + acceptance fixture only; no GalleyQuest work.

## What was implemented (diff: +3471 lines, 15 files; additive except a 27-line
## council-engine change)

ADDITIVE new modules under tools/ (writing only under QUEUE_ROOT/alf/):
- clearwright_alf.py (888) — Layer 1 + journal. Canonical serialization (sorted
  keys, compact, ensure_ascii off, one trailing newline, INTEGER-ONLY numbers -
  floats refused in hashed records); per-line hash chains (64-zero sentinel);
  immutable content-addressed observations (deterministic identity EXCLUDING
  capture context; same-short-id different-identity refused = IntegrityHalt);
  per-run occurrences; the crash-safe operation journal (content-addressed durable
  staged payloads, op_begin/op_commit, ACYCLIC anchoring to PRE-transaction heads,
  mandatory expected-head checkpoint as final staged write, four-way replace_file +
  predecessor-anchored per-target-ordered append recovery, fail-closed on
  missing/corrupt staged bytes); verify-hashes (chain audit + observation-bytes-vs-
  index + checkpoint journal-authentication + ancestry proof); CLI observe/list/
  show/verify-hashes/recover.
- clearwright_alf_synth.py (621) — priority-model-v1 (embedded verbatim; canonical
  bytes + reproducible sha; divergent-overwrite refused); tier-policy-v1
  (deterministic, top-down first-match); transparent scoring; versioned findings
  store (gap-allowed crash-safe entry_id allocation; append-only hash-chained
  revision log; byte-exact head-rebuild); dedup-policy-v1 (proposal-based; protected-
  class silent-merge prohibition); idempotent attribution ledger; recurrence;
  regression reopen with tier-and-score floor.
- clearwright_alf_delta.py (310) — Run Improvement Delta: immutable self-sufficient
  input snapshot; deterministic derivation via hash-verified refs; rerun is a
  verified no-op or a REFUSED divergent rewrite (Tier 1); acyclic anchor chain;
  empty delta still written; missing-delta verifier.
- clearwright_alf_review.py (254) — legal lifecycle transition table; disposition
  verbs bound to a durable inbound operator message (exists / operator+inbound /
  postdates the disposed revision / names the entry_id / single-use replay refused);
  APPROVED_FOR_PLANNING promotion gate; disposition-free surfacing; state-neutral
  spec rendering (grants nothing).
- clearwright_dispatch_preflight.py (136) — enablers A/B pure logic.
- clearwright_alf_seed.py (216) — the three approved seed findings + residual
  candidate, exact evidence bindings, idempotent.
- clearwright_alf_gqfixture.py (104) — GQ acceptance fixture (read-only evidence).

MODIFYING (the only change to existing code) tools/clearwright_review_council.py
(+27) — enablers A/B, inline below.

## SECURITY-CRITICAL CHANGE (review closely): council-engine enabler wiring
```
@@ run_round entry (after its_lane) @@
+    # Enabler B (pre-allocation dispatch eligibility)
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

@@ inside the attempt loop, after log_invocation @@
+    if not _validated(result) and not (result or {}).get("hard_gate"):
+        state.setdefault("normalized_reasons", []).append(
+            cwdp.classify_reviewer_failure(result))

@@ exhausted return @@
+    normalized_reasons = {rev: attempt_state.get(_attempt_key(round_no, rev), {})
+        .get("normalized_reasons", []) for rev in statuses}
     return {..., "exhausted": exhausted, "normalized_reasons": normalized_reasons, ...}
```
Non-weakening argument: enabler B only refuses when `council.preallocation_signals`
prove a blocker (normal councils carry no such key → dispatch_eligibility({}) →
(True, None) → unchanged path); the egress guard still independently re-enforces
every rule at SEND. classify_reviewer_failure reads only safe fields
(error/classification/reason/error_class/code) — never raw provider bodies/verdicts
— and returns one of 13 fixed classes.

## Design invariants asserted (and tested)
- Additive: no existing record shape changed; alf_record_version 1; absence of alf/
  means no data. verify: full suite green incl unchanged council/queue/gate tests.
- Immutability tamper-EVIDENT (not physically append-only) — honest boundary in the
  code: chains detect mutation/reorder/non-terminal deletion; expected-head
  checkpoints (journal-authenticated, ancestry-proof) detect isolated truncation;
  coordinated rollback is reported as an explicit undetectable boundary, never a
  false success.
- Crash safety: recovery replays ONLY from durable staged bytes; missing/corrupt
  staged bytes HALT (IntegrityHalt), never proceed on partial state.
- Operator-control boundary (STRUCTURAL): no ALF code path creates a work item,
  operator authority, GitHub state, or mutates code; dispositions require a durable
  operator message (single-use); promote renders a spec only (no state change, no
  posting).

## Test evidence
81 ALF tests + full suite 1126 OK. Coverage includes: float rejection; chain
integrity + break detection; idempotent capture; id-collision refusal; cross-run
occurrences; byte-tamper detection; journal crash-recovery + fail-closed on missing
staged bytes; model hash reproducibility + divergent-overwrite refusal; all four
tier boundaries + ALF-0003 escalation; exact scoring; head-rebuild; dedup confidence
tiers + protected-class flag; ledger idempotence; regression tier-and-score floor;
delta immutable-snapshot rerun no-op + divergent-rerun refusal + anchor chain +
missing-delta verifier; all five disposition binding checks + replay refusal +
promotion gate; enabler classifier (incl. does-not-read-body) + eligibility order;
seed tiers/scores; GQ fixture quantifies 10 reviewer_unavailable / 40 attempts /
4 work items and PRESERVES causal uncertainty.

## Review questions
1. Is the three-layer separation implemented soundly — the append-only hash-chained
   revision log and byte-exact head-rebuild?
2. Is the operation journal's acyclic anchoring + crash recovery correct and
   fail-closed (no double-count, no loss, halt on missing staged bytes)?
3. Is the operator-control boundary complete — can ANY implemented ALF path create
   authority, governed work, GitHub state, mutate code, alter a disposition without
   a valid operator message, or bypass the promotion gate?
4. Do enablers A/B weaken ANY fail-closed security/provenance/authority/egress
   control? Is classify_reviewer_failure free of secret/body leakage?
5. Is the GQ fixture faithful and does it correctly preserve causal uncertainty?
6. Any HIGH or CRITICAL implementation defect?
