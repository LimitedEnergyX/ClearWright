# Acceptance record — the Use CW mission

The mission: an operator says **"Use CW to do X"** in Claude Code or Claude
Desktop, and the whole governed loop runs automatically — conversation, work
item, real GPT + Codex Review Council rounds, reconciliation, deterministic
agreement, verification, honest completion — with no manual copy/paste between
models and no operator interruption except at defined hard gates.

State: **PASSED** on 2026-07-14. The fixtures below are the runs that proved it
(durable records live under the operator's queue root; ids are recorded here so
the runs can be re-inspected).

| Criterion | Fixture | Result |
|---|---|---|
| Review engine (adversarial, deterministic) | 5-round planning council `cw-council-20260713T201027505377` | PASSED — reviewers drove ~9 real hardening changes; council refused to rubber-stamp and escalated `operator_required` at the cap |
| Claude Code end-to-end | work item `message:msg-20260713T210135514313` | PASSED — 2-round council, honest no-change outcome (`agreement_threshold_met`), DONE |
| Large-artifact verification (the transport regression) | council `cw-council-20260714T041127289486`, 260 KB pinned capture `art-b12030ecfe6d` | PASSED — both reviewers real on first attempt every round; file-capable reviewer read the pinned artifact and approved; text-only reviewer received cited-line excerpts and surfaced three verified content findings; `complete` honestly refused DONE (`verification_incomplete`) |
| Claude Desktop end-to-end ("Use CW" phrase) | work item `message:msg-20260714T062502728906` (review of this repo's operator UI) | PASSED — Desktop Commander used automatically; plan council 2 rounds + verify council 2 rounds, all real, both `agreement_threshold_met`; reviewer corrections reconciled in both directions; read-only product scope honored (empty diffs proven); DONE |
| No-copy/paste | all of the above | PASSED — zero manual relay between models |

Honesty outcomes worth keeping visible: across these runs the council falsified
claims made by the orchestrating agent (withdrawn in place with evidence),
rejected a reviewer's stale claim with byte-identity proof, refused a
verification stamp it could not honestly grant, and refused DONE when a bound
verification council had not passed. That behavior — not any single green run —
is the acceptance.

Regression tests for the exact Desktop invocation sequence live in
`tests/test_use_cw_e2e.py` (mocked reviewers; the live fixtures above used real
ones).
