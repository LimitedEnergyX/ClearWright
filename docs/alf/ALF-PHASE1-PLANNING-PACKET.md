# ALF Phase 1 (Automated Leap Frog) - Final Planning Packet

Work item: message:msg-20260719T182047862744 (governed, internal_technical, verification required, clearance required)
Authority: msg-20260719T181306094084 (OPERATOR-0001, operator-ui, 2026-07-19T18:13:06Z)
Deployed baseline under review: ClearWright commit 419b1f8 (merge of PR 83), single local control-plane server, operator mode
Packet date: 2026-07-20. Executor: single stateful executor (model claude-fable-5).
Packet status: PLANNING ONLY. This packet grants no implementation authority. The work stops at the plan gate. Implementation requires the recorded plan-gate outcome plus any required operator CTA under the existing authority.

Reviewer instructions: You are reviewing an implementation-ready PLAN for ALF Phase 1 against the current deployed ClearWright architecture summarized in section 3 and the authority summarized in section 2. Evaluate correctness, completeness against the authority, storage and schema soundness, integrity of the immutability and hash-binding model, the priority and deduplication design, the operator-control boundary (nothing in ALF may create authority, create governed work, or act without the operator), and the safety of the Phase 1 exclusions. Identify any HIGH or CRITICAL planning defect explicitly. Answer the review questions in section 22.

## 1. Purpose and scope

ALF (Automated Leap Frog) is a ClearWright-internal continuous-improvement subsystem. It observes how governed ClearWright runs succeed, fail, stall, retry, consume resources, and require operator intervention; converts evidence-bound observations into deduplicated, dynamically prioritized, durable improvement findings; and proposes testable governed-work specifications. The operator alone decides which findings become governed implementation work.

Phase 1 scope (per authority, planning target for later implementation):
1. Structured run observations
2. Durable synthesized findings
3. Evidence and cryptographic hash binding
4. Deduplication with preserved lineage
5. Recurrence and cumulative-waste tracking
6. Tiered, transparent, versioned priority scoring
7. Root-cause confidence and blast-radius assessment
8. Regression detection, reopening, and escalation
9. Run Improvement Delta generation
10. Proposed governed-work specifications
11. Operator review and promotion controls
12. A minimal operator-usable view to inspect findings and approve, defer, reject, merge, or promote them

This packet is the bounded plan-council input required by the authority. It reuses existing ClearWright capabilities and does not duplicate them. Nothing here modifies production behavior.

## 2. Exact Phase 1 authority

Authority record: msg-20260719T181306094084, posted by OPERATOR-0001 through the operator UI at 2026-07-19T18:13:06Z, durable in the active queue communications store, sha256 de155644f62e46370e6fbcfc4dd539d5dfcd2c9be6d71b7186f00f062d1fdb63.

Binding elements of that authority reflected in this plan:
- Three-layer model: immutable run observations; synthesized durable improvement findings; governed improvements promoted only after operator approval.
- Observations are historical evidence and must not be rewritten. Findings may be updated as evidence, recurrence, confidence, priority, and understanding change. Governed improvements remain subject to normal ClearWright authority, clearance, council, verification, and closure requirements.
- The twelve Phase 1 capabilities listed in section 1, and only those.
- The mandatory finding fields (reproduced in section 6), blast-radius enum, relationship types, hard priority tiers (section 14), transparent versioned priority formula (section 15), promotion requirements and lifecycle (section 16), automated judgment boundary (sections 7 and 16), Run Improvement Delta (section 17), regression behavior (section 12), initial required findings (section 21), Phase 1 exclusions and GitHub boundary (section 20).
- Canonical ALF store stays inside ClearWright. GitHub Issues are not the canonical ALF store. The 26 existing open GitHub issues are intentional roadmap items and are not touched.
- Process: durable context checkpoint; inspect actual current repository and runtime state; one bounded plan council; explicit schemas and architecture; reach the plan gate; obtain any required CTA before implementation. Do not materially reinterpret or expand scope.

Related lifecycle authority context (evidence for section 21, not new scope): the ITS council-enablement repair work item message:msg-20260719T183202666915 was closed by the operator under closure authority msg-20260720T023229145870 (sha256 b98f949728504ef002839f1084a08bd6eec4d5bc7901e0649abb24074a42be4a) with status closed_by_operator and outcome accepted_with_verification_incomplete; its harness summary record has fingerprint a4238ef9841b9ad4d7a5e08cd6f5fa8b04922dda87b56a5aebfaa8e43fc0ca66.

## 3. Current deployed ClearWright architecture

Deployed baseline: commit 419b1f8 (merge of PR 83) running as a single local control-plane server process bound to the loopback interface, operator mode, with an egress-guard self-test reporting ok on policy 1.0.0. The durable state lives in an operator-configured active queue root outside the repository (referred to below as QUEUE_ROOT).

Components (all under tools/ in the approved repository):
- clearwright_use_cw.py: the single stable CLI entry point. Subcommands: start (envelope-classified conversation + work-item claim), council (create and run protected review councils; stage review or reconcile; dry-run validation), incident, verify, progress, complete, close (operator-only closure with operator-message authority), grant-proceed (operator gate resolution), retrospective, status (read-only), preflight (cheap readiness gate), schema (schema printer). Envelope classification resolves task_kind (chat, analysis, actionable, governed, high_risk), verification_required, review_profile, and data_sensitivity. Since commit 419b1f8, a governed envelope that explicitly declares data_sensitivity internal_technical resolves into the internal_technical lane dynamically from the preserved envelope (no historical rewrite; fail-closed default remains sensitive; high_risk, chat, ambiguous, and unspecified stay sensitive). Clearance requirement keys on task_kind (governed and high_risk), independent of the sensitivity axis.
- clearwright_review_council.py: the council engine. Persistent councils under a review-councils subtree of QUEUE_ROOT with durable council, per-round, and outcome records; two independent reviewers (GPT and Codex) with a persisted two-attempts-per-reviewer-per-round budget; rounds 2 to 5; reconciliation validation; deterministic agreement evaluation; escalation gates recorded on operator_required and hard_gate outcomes. In the internal_technical (ITS) dispatch lane the packet is assembled only from a fixed versioned scaffold plus hash-bound components; a lineage stamp binds the exact context bytes; artifacts and non-code profiles are refused; an input-budget fail-fast precedes any attempt spend.
- clearwright_egress_guard.py: the sole sanctioned egress boundary (SDEG). Live lineage graph binds the outbound packet to declared content sources; source classification is provenance-defined (approved repository identity from an operator-local uncommitted config; approved path allowlist; git-tracked; working-tree content byte-equal to the committed blob at HEAD; symlink and traversal refusal; everything else fail-closed sensitive). Enforcement validates the exact outbound bytes: composition manifest decomposition, scaffold registry hash, per-component sha256, composition-to-lineage reachability binding, canonical body and prompt byte-equality (GPT request body and Codex stdin), a full-bytes tripwire scan including a unicode-confusable detector, and a TOCTOU re-verification of source bindings at send. Provider credentials are resolved only inside the guard and never exposed.
- clearwright_gpt_review.py and clearwright_codex_review.py: reviewer adapters (real providers only; the guard owns transport, URL, and credentials; adapters see none of them).
- clearwright_work.py, clearwright_message.py, clearwright_claim.py: durable queue model - messages (communications store), task envelopes with a preserved _audit block, work items keyed as message:<message-id>, summaries generated only by the harness on terminal events, gates, operator-authority records, invocation log (metadata-only JSONL), agent events, writer locking (single-writer with atomic tmp-then-replace writes and a Windows sharing-violation retry), archive tooling.
- clearwright_gate.py: durable escalation gates; fail-closed refusal of progress, council, verify, and complete while an unresolved gate exists; operator-only resolution via a durable inbound operator message created after the gate.
- clearwright_server_lifecycle.py plus the server app: local operator console and API on the loopback port; health endpoint reports instance identity (pid, commit, bind, queue root), packet counts, work-item counts, pulse, capabilities, and egress-guard self-test; graceful-stop sentinel mechanism; lifecycle log.
- Lifecycle facts relevant to ALF: work items resolve to open, claimed, or terminal; complete refuses DONE when required verification is missing (exit verification_incomplete) and, for governed items, without a clearance packet in clearance_done; close is operator-only and records closed_by_operator with a closure-authority message id; the harness posts the canonical summary on every terminal event.

Durable observation surfaces that already exist (ALF Phase 1 consumes these; it does not re-instrument them): the invocation log (every dispatch attempt including aborted ones, metadata only), council round and outcome records, reviewer attempt state, summaries, gates, lifecycle log, communications, task envelopes, and health output.

## 4. Three-layer model

Layer 1 - Immutable run observations. Facts captured from durable surfaces at run boundaries. Append-only, content-addressed, never edited, never deleted within Phase 1 retention. An observation that later proves wrong is countered by a new correcting observation that references it; the original stays.

Layer 2 - Durable synthesized findings. Mutable, versioned records synthesized from observations. Every mutation appends a new revision to an append-only per-finding history log with a revision hash chain; the head record is a pure function of the revision log. Findings change as evidence, recurrence, confidence, priority, and understanding change - with full audit.

Layer 3 - Governed improvements. Only the operator promotes a finding toward implementation. Promotion produces a governed-work specification document, never a work item, never authority. Any actual implementation runs through the normal ClearWright governed workflow (operator authority, clearance, plan council, gates, verification, closure).

Boundary rule: data flows upward only by reference (observations referenced by findings, findings referenced by specifications). Nothing in a higher layer rewrites a lower layer.

## 5. Immutable run observations

Storage: QUEUE_ROOT subtree alf/observations/, one JSON file per observation named obs-<UTC-compact-timestamp>.json (same timestamp-id style as existing queue records), plus an append-only index alf/observations/index.jsonl (one line per observation: observation_id, sha256 of the observation file bytes, captured_at, run_id, kind). Files are written with the existing atomic tmp-then-replace pattern and are never modified afterward; the index line's sha256 makes any later mutation detectable.

Observation schema (alf_record_version 1):
- observation_id: string, obs-<UTC-compact-timestamp>
- captured_at: UTC ISO timestamp
- run_id: string binding the capturing run (work item id plus capture sequence)
- work_item_id, thread_id, council_id, gate_id: optional subject bindings
- kind: enum - run_started, run_completed, run_closed, council_round, council_outcome, reviewer_attempt, dispatch_failure, gate_created, gate_resolved, lifecycle_event, complete_refusal, close_recorded, operator_intervention, resource_usage, executor_note
- subsystem: enum - envelope_classification, work_item_lifecycle, council_engine, reviewer_gpt, reviewer_codex, egress_guard, dispatch_lane, queue_store, gates, server_lifecycle, operator_ui, cli, executor_process, other
- summary: short plain-language statement of the observed fact (no secrets, no personal content; same hygiene rules as council packets)
- source_refs: array of typed evidence references (section 8)
- source_hashes: array of sha256 values parallel to source_refs
- metrics: optional object - operator_minutes, execution_delay_seconds, token_estimate, gpt_tokens_actual_in, gpt_tokens_actual_out, api_attempts, tool_attempts, council_attempts, invocations
- capture_method: enum - cli_explicit (alf-observe invocation), run_boundary (emitted by the run wrapper at start and terminal events), backfill (operator-authorized historical import)
- capturing_actor: string (executor identity)

Capture design (Phase 1): observation capture is invocation-driven, not a daemon. A new CLI verb (section 18, ALF-CLI) reads the existing durable surfaces for a named run or work item and emits observations deterministically; re-running capture for the same facts is idempotent because the observation id is derived from (kind, subject binding, source hash set) - identical content produces the same id and is skipped. No polling process, no background service.

Immutability enforcement: the writer refuses to write an observation file that already exists with different bytes; the index is append-only; a verification helper re-hashes files against the index and reports any divergence as a Tier 0 durable-record-integrity event.

## 6. Durable, versioned findings

Storage: QUEUE_ROOT subtree alf/findings/<entry_id>.json (head record) plus alf/findings/history/<entry_id>.jsonl (append-only revision log). Every revision line contains: revision_no, revised_at, revising_actor, reason, the full finding record as of that revision, prev_revision_sha256 (hash chain), and revision_sha256 (sha256 of the canonical serialized revision line minus its own hash field). The head file always equals the last revision's record. Rebuilding the head from the log must reproduce it byte-for-byte (regression-tested).

Finding schema (alf_record_version 1) - the full mandatory field set from the authority:
entry_id; title; status; priority_tier; priority_score; priority_model_version; first_seen_at; last_seen_at; occurrence_count; affected_run_count; affected_work_item_count; cumulative_operator_minutes; cumulative_execution_delay; cumulative_token_estimate; cumulative_api_attempts_wasted; cumulative_tool_attempts_wasted; cumulative_council_attempts_wasted; subsystem; failure_class; security_impact; reliability_impact; authority_integrity_impact; durable_record_integrity_impact; operator_time_impact; execution_delay_impact; token_api_compute_impact; blast_radius; problem_statement; observed_symptoms; root_cause; root_cause_confidence; immediate_containment; immediate_workaround; permanent_resolution; objective_acceptance_criteria; required_regression_tests; evidence_references; evidence_hashes; related_entries; supersession_lineage; owner; estimated_effort; dependencies; blockers; promotion_state; operator_disposition; deferral_reason; review_date; last_operator_reviewed_at; implementation_work_item_id; released_version; verification_evidence.

Field conventions:
- entry_id: ALF-<four-digit sequence> (ALF-0001 upward), allocated once, never reused.
- status: the primary lifecycle enum (section 16). promotion_state mirrors the promotion-relevant subset. operator_disposition: none, accepted_risk, deferred, rejected, superseded, not_reproducible, approved.
- impact axes (security_impact, reliability_impact, authority_integrity_impact, durable_record_integrity_impact, operator_time_impact, execution_delay_impact, token_api_compute_impact): integer 0 to 3 (none, low, medium, high), scored under the versioned model (section 15).
- blast_radius: single_event, single_run, single_work_item, single_subsystem, multiple_subsystems, all_councils, system_wide, external_or_public.
- root_cause_confidence: 0.0 to 1.0 with a confidence_basis note; values below 0.5 render prominently in operator review (section 16).
- failure_class: enum - authority_bypass_risk, durable_record_integrity, correctness, operational_reliability, stale_state, broken_recovery, work_blocker, council_failure, queue_failure, lifecycle_failure, deployment_failure, operator_time, execution_delay, resource_waste, poor_failure_reporting, excess_deliberation, clarity, user_experience, documentation, maintainability.
- related_entries: array of {entry_id, relationship} with relationship in: duplicate_of, supersedes, superseded_by, caused_by, contributes_to, blocked_by, related_to, recurrence_of, regression_of, discovered_while_fixing.
- evidence_references / evidence_hashes: section 8.

## 7. Governed improvement proposals

A finding may carry a proposed governed-work specification (section 18) once its permanent_resolution, objective_acceptance_criteria, and required_regression_tests are populated. Proposals are Layer 2 artifacts: text plus references, zero authority. ALF may propose likely root cause, likely duplicate or related entry, priority tier and score, permanent resolution, and a governed-work specification - always recording confidence for each automated judgment. ALF may not create operator authority, may not create governed work items automatically, may not begin implementation, may not modify code, may not make operator dispositions, may not synchronize GitHub issues, and may not modify itself. These prohibitions are enforced structurally: the Phase 1 code paths contain no work-item-creating call, no code-mutation call, and no GitHub call.

## 8. Evidence references and hash binding

Typed evidence reference forms (source_refs / evidence_references):
- message:<message-id> - a communications record in QUEUE_ROOT
- envelope:<message-id> - a task-envelope record
- summary:<work-item-message-id> - a harness summary record
- council:<council-id> - a council record; council-round:<council-id>#<n> - one round record; council-outcome:<council-id>
- gate:<gate-id>; invocation-log:<UTC-date>#<line-span>; lifecycle:<UTC-timestamp>; agent-event:<event-id>
- repo-file:<repo-relative-path>#<commit> - committed repository content
- alf-observation:<observation-id>; alf-finding:<entry-id>#rev<n>
- work-record:<file-name> - a durable planning or result record in the runtime work directory

Hash binding rule: every evidence reference is paired one-to-one with a sha256 in evidence_hashes, computed over the exact referenced bytes at capture time (whole file for record references; the exact byte span for log spans; the committed blob for repo-file references). A finding revision that adds a reference without its hash is rejected by the writer. Hash verification is re-runnable: a verifier command re-hashes every reference that still exists and reports divergence (divergence itself becomes a Tier 0 durable-record-integrity observation). Hashes of records that are later archived remain valid as capture-time evidence; the reference records the archive location when known.

## 9. Finding deduplication

Deduplication is proposal-based, never silent for protected classes:
- Candidate key: (subsystem, failure_class, normalized root-cause signature). The signature is a deterministic normalization of root_cause (lowercase, stopword-trimmed, identifier-preserving token sequence). Exact key match proposes duplicate_of with confidence 0.9; token-overlap similarity above a versioned threshold proposes with proportional confidence.
- A proposed merge is a pending relationship on the newer entry (duplicate_of, proposed, confidence, rationale). The operator confirms or rejects it in review. On confirmation the newer entry's status becomes MERGED, meaning deduplicated into another ALF finding (not a Git or code merge); its occurrence and waste counters fold into the surviving entry; both entries record the supersession_lineage pair; the merged entry stays readable forever.
- Silent-merge prohibition: findings whose failure_class or impact axes touch security, privacy, authority integrity, or durable-record integrity are never auto-merged; the merge proposal renders prominently and waits for the operator.
- Auto-accept threshold: none in Phase 1. Every merge is operator-confirmed. (Conservative start; a later authority may loosen.)

## 10. Lineage and supersession

Every finding carries supersession_lineage: an ordered array of {entry_id, relationship, at, reason, evidence_ref}. Rules:
- supersedes / superseded_by are symmetric pairs written to both entries in one operation.
- A superseding entry must reference the evidence that changed the understanding.
- Lineage is append-only within the revision log; a lineage entry is corrected only by a subsequent lineage entry, never by rewriting.
- Merged and superseded entries remain in the store and in listings filtered by status.

Applied lineage record (initial state, from durable evidence): the original authority text listed a fourth initial finding, council dispatch eligibility is checked too late. After the authority was posted, its two headline evidence items changed: the governed internal_technical classification coupling was corrected in production at commit 419b1f8 (work-record REPAIR-RESULT.md, sha256 506a5e86dd22b0090c7570f7b521b8502b6d47ebe4f46bd0369492d958590daf), and the suspected GPT ITS request-body construction defect was empirically disproved (work-record REPAIR-GPT-RCA-DISPROOF.md, sha256 dd3c91d7bbd5256604de0b366f96316fec7e8735ba028ecd95418f2021436fb5: the production adapter body is byte-identical to the guard canonical form). The operator's resume directive therefore names three approved initial findings (section 21). The residual concept from the fourth - a deterministic dispatch-eligibility preflight that runs before any council id or reviewer attempt is allocated - remains real (its remaining evidence: source-outside-repo rejections, pre-SDEG envelope incompatibility, unicode-confusable rejections, and reviewer attempts consumed before eligibility was proven) and enters the store at first synthesis as a TRIAGED candidate carrying this lineage note, subject to normal operator review. It is not one of the three approved initial findings and no history about it was rewritten.

## 11. Recurrence detection

- Every synthesis pass matches new observations against existing findings by the deduplication key before creating anything new. A match updates last_seen_at, occurrence_count, affected_run_count, affected_work_item_count, and the cumulative waste counters, and appends a recurrence_of relationship from the triggering observation.
- Recurrence never lowers priority. A recurrence while status is RELEASED or MONITORING is a regression (section 12), not a plain recurrence.
- Recurrence of a DEFERRED or ACCEPTED_RISK finding re-raises it into OPERATOR_REVIEW at the next delta with the recurrence evidence attached (the operator sees that the accepted risk is recurring; the disposition is not silently changed).
- The conservative excess-deliberation detector (Finding ALF-0002, section 21) is recurrence-driven: it reports only when the same entities and candidate decisions recur with no new tool evidence, unchanged authority, unchanged durable state, and unchanged constraints while tokens and elapsed time increase without execution progress - and its output is advisory only.

## 12. Regression handling

When a released or monitored issue recurs:
1. Reopen the ORIGINAL finding (status RELEASED or MONITORING back to PRIORITIZED; never a new low-priority duplicate).
2. Link the previous correction (implementation_work_item_id, released_version) and the previous verification_evidence.
3. Link the new run and the new evidence (references plus hashes).
4. Update occurrence_count and affected_run_count.
5. Re-score priority under the current model version; a regression adds the regression escalation term (section 15) so a regressed finding always ranks at or above its pre-release position.
6. Classify the reopening as regression_of in related_entries and set failure_class accordingly if the recurrence reveals a broader class.
7. Surface the regression in the next Run Improvement Delta and in operator review as a distinct regressions group.

## 13. Cumulative waste accounting

Per finding, monotonically non-decreasing counters, updated only from observation metrics (never estimated retroactively): cumulative_operator_minutes; cumulative_execution_delay (seconds); cumulative_token_estimate (plus actual provider-reported tokens when the surface records them); cumulative_api_attempts_wasted; cumulative_tool_attempts_wasted; cumulative_council_attempts_wasted. Attribution rule: an observation's metrics are attributed to a finding only when the observation is linked to it (recurrence, regression, or initial evidence). Waste totals are display inputs and scoring inputs (section 15) and appear in every Run Improvement Delta as deltas plus running totals.

## 14. Hard priority tiers

A hard tier is assigned before any numeric score and is never reduced by effort:
- Tier 0: active or plausible authority bypass; privacy exposure; credential exposure; sensitive-data egress; destructive-action risk; unauthorized mutation; durable-record corruption; production ownership conflict.
- Tier 1: authority-integrity defects; durable-record-integrity defects; correctness failures; operational reliability failures; stale or misleading state; broken recovery; repeatable work blockers; council, queue, lifecycle, or deployment failures.
- Tier 2: operator time; execution delay; unnecessary councils; unnecessary retries; repeated tool calls; token, API, and compute waste; poor diagnostics; excessive deliberation without new evidence.
- Tier 3: clarity; user experience; documentation; maintainability; nice-to-have improvements.

Display rules: priority (tier plus score), estimated_effort, and leverage are displayed separately; effort never changes tier or score; visible critical findings are never capped; work-in-progress limits apply only to active implementation and planning states, never to visibility.

## 15. Versioned scoring methodology

Model file: alf/meta/priority-model-v1.json (versioned; every ranking decision stores priority_model_version plus the exact inputs and the computed score, so any ranking is reproducible offline).

priority-model-v1 (transparent formula):
- Inputs: the seven impact axes (each 0 to 3), blast_radius rank BR (single_event=0 up to external_or_public=7), occurrence_count OC, regression flag RG (0 or 1), cumulative waste bands WB (0 to 3, from banded thresholds on the waste counters recorded in the model file).
- base = 4*security_impact + 4*authority_integrity_impact + 4*durable_record_integrity_impact + 3*reliability_impact + 2*operator_time_impact + 2*execution_delay_impact + 1*token_api_compute_impact
- radius_term = 2 * BR
- recurrence_term = 2 * min(OC - 1, 10)
- regression_term = 12 * RG
- waste_term = 2 * WB
- score = base + radius_term + recurrence_term + regression_term + waste_term (integer; higher is more urgent; ordering within a tier only - tier always dominates).
- Leverage (displayed separately, never part of score): leverage = score / max(estimated_effort_points, 1) with effort points from a small enum (1, 2, 3, 5, 8).
- Every stored ranking decision records: entry_id, model version, input vector, score, tier, computed_at, and the triggering observation or revision.

Changing the formula requires a new model file version (priority-model-v2 and so on); historical decisions keep their original version reference; re-ranking under a new version is an explicit revision with reason model_upgrade.

## 16. Operator review and promotion controls

Primary lifecycle: OBSERVED -> TRIAGED -> MERGED (dedup terminal) or PRIORITIZED -> OPERATOR_REVIEW -> APPROVED_FOR_PLANNING -> WORK_ITEM_CREATED -> IN_PROGRESS -> VERIFICATION -> RELEASED -> MONITORING -> CLOSED. Additional dispositions: ACCEPTED_RISK, DEFERRED, REJECTED, SUPERSEDED, NOT_REPRODUCIBLE.

Actor rules (structural, enforced by the writer):
- ALF (automated) may perform: OBSERVED -> TRIAGED (synthesis), TRIAGED -> PRIORITIZED (scoring), recurrence and regression updates, and merge PROPOSALS.
- Operator-only transitions: any disposition (approve, defer, reject, accept risk, supersede, not reproducible), merge confirmation, OPERATOR_REVIEW -> APPROVED_FOR_PLANNING, and everything from WORK_ITEM_CREATED onward (those states only mirror the governed workflow's own records).
- Every operator-only transition requires a durable inbound operator message id recorded on the revision (same authority pattern as the existing close and grant-proceed commands). No operator message, no transition.
- APPROVED_FOR_PLANNING gate (all required): defined permanent_resolution; objective_acceptance_criteria; required_regression_tests; evidence references plus hashes; an understood root cause (root_cause_confidence at or above 0.5) or an explicit investigation requirement recorded instead; known dependencies and blockers. The writer refuses the transition when any element is missing.
- Prominence rules in review: root_cause_confidence below 0.5, low-confidence merge proposals, and any Tier 0 entry render at the top with explicit markers. Regressions render as their own group.
- WORK_ITEM_CREATED and beyond are recorded by reference only: implementation_work_item_id, released_version, verification_evidence come from the normal governed workflow records; ALF never creates them.

## 17. Run Improvement Delta

Every governed ClearWright run eventually produces (a) the normal governed work result and (b) an ALF Improvement Delta. Phase 1 mechanism: a delta generator runs at run boundaries (invocation-driven, same trigger as observation capture) and writes alf/deltas/rid-<run-id>.json plus a compact operator-readable rendering in the run's summary flow.

Delta schema (alf_record_version 1): run_id; work_item_id; generated_at; new_observations [ids]; new_findings [entry ids]; observations_merged_into_existing [{observation_id, entry_id}]; findings_priority_changed [{entry_id, old_tier, old_score, new_tier, new_score, model_version, reason}]; released_fixes_revalidated [{entry_id, verification_evidence_ref}]; regressions_detected [{entry_id, evidence_refs}]; items_requiring_operator_review [entry ids with reasons]; cumulative_waste_changes {per counter: delta and new total}.

An empty delta is still written (all arrays empty) so every governed run has exactly one delta record and absence is detectable.

## 18. Proposed governed-work specifications

When the operator approves a finding for planning, ALF renders a specification document alf/specs/spec-<entry_id>-v<n>.md containing: problem statement; evidence summary with references and hashes; permanent resolution; objective acceptance criteria; required regression tests; dependencies and blockers; estimated effort; proposed envelope skeleton (task_kind governed unless the operator directs otherwise, explicit approved-scope draft, excluded-actions draft carrying every ALF prohibition that applies). The specification is input material for the OPERATOR to create authority and a work item through the normal workflow. ALF never posts it anywhere.

Implementation phasing proposal for Phase 1 itself (post-CTA, for the later implementation turn; listed here so the plan is complete and testable):
- P1a: storage layer plus schemas (observations, findings, revision log, index), writers with immutability and hash-chain enforcement, ALF-CLI verbs alf-observe, alf-list, alf-show, alf-verify-hashes. Regression tests for immutability refusal, hash-chain integrity, head-rebuild equality, idempotent capture.
- P1b: synthesis (dedup proposals, recurrence, regression reopen), scoring under priority-model-v1, delta generation. Regression tests: dedup key determinism, protected-class silent-merge refusal, recurrence counter updates, regression reopen path, scoring reproducibility from stored inputs, empty-delta emission.
- P1c: operator review surface (section 19) and operator-authority-checked disposition verbs (alf-review with an operator message id), promotion-gate refusal tests, actor-rule enforcement tests.
- Compatibility and migration: purely additive - a new alf/ subtree under QUEUE_ROOT; no existing record shape changes; every ALF record carries alf_record_version 1; readers ignore unknown fields; absence of the alf/ subtree means ALF simply has no data (all existing flows unaffected). No backfill in Phase 1 except operator-authorized explicit imports (capture_method backfill).
- Test plan (full): the unit and integration tests above, plus negative tests - attempted observation rewrite refused; evidence reference without hash refused; APPROVED_FOR_PLANNING without gate elements refused; automated actor attempting an operator-only transition refused; delta generator never mutates findings except through the synthesis path; no code path callable from ALF creates a work item, posts to GitHub, or edits repository files (asserted by construction and by tests that the CLI verbs registered for ALF contain no such calls).

## 19. Minimal operator-facing view

Extend the existing local console with one route group (list plus detail), no dashboard redesign:
- List view: findings ordered by (tier, score), filterable by status, subsystem, and disposition; columns: entry_id, title, tier, score, status, occurrence_count, last_seen_at, root_cause_confidence marker, regression marker. Tier 0 and low-confidence markers always visible at top. No cap on visible critical findings.
- Detail view: full finding record, revision history, evidence references with hashes, related entries and lineage, current merge proposals, waste counters, and - when populated - the specification rendering.
- Disposition actions: approve for planning, defer (with deferral_reason and review_date), reject, accept risk, confirm or reject merge, supersede, not reproducible, promote (renders the specification). Every action posts the operator disposition through the same authority-checked path as the CLI (durable inbound operator message reference required), so the UI grants nothing the CLI would refuse.
- Read parity CLI: alf-list and alf-show render the same data in the terminal for headless operation.

## 20. Explicit Phase 1 exclusions

Not implemented in Phase 1 (per authority): autonomous correction; automatic governed-work creation; GitHub issue synchronization; broad dashboard redesign; cross-repository integration; automatic authority issuance; self-modification; unrelated lifecycle, restart, installer, SSO, connector, or public-alpha work. GitHub boundary: the existing 26 open GitHub issues are intentional roadmap items and are not closed, merged, reorganized, relabeled, rewritten, or synchronized during ALF Phase 1; GitHub Issues are not the canonical ALF store; operator-approved findings may later be linked or promoted to GitHub issues under separate authority only. Additional standing prohibitions restated: ALF may not create operator authority, create governed work items automatically, begin implementation without authority, modify code without authority, make operator dispositions, or self-modify; the canonical ALF store remains inside ClearWright; a finding is never implementation authority; council agreement is never implementation authority; implementation begins only after the plan gate and any required CTA under the existing governed workflow.

## 21. Initial evidence-bound ALF findings

Three approved initial findings enter the store at first synthesis, evidence-bound as follows. (Original authority finding four: see the lineage record in section 10 - resolved-or-disproved headline evidence; residual concept preserved as a TRIAGED candidate, not an approved initial finding.)

### ALF-0001 - Missing deterministic terminal-disposition engine
- priority_tier: 1 (failure_class lifecycle_failure; authority_integrity_impact 3, reliability_impact 2, operator_time_impact 2; blast_radius multiple_subsystems to system_wide)
- problem_statement: The executor repeatedly interprets whether a work item should be completed, closed, superseded, abandoned, cancelled, or left open. Lifecycle classification is model-driven where it should be mechanical.
- root_cause: ClearWright lacks a mechanical lifecycle and disposition preflight that returns: deliverable status; verification requirements; legal terminal actions; required actor; exact authority needed; disallowed actions with reasons; valid superseding relationships.
- observed_symptoms: repeated deliberation over terminal choices; a mis-declared item requiring operator closure (classification conflict); a delivered repair whose formal DONE path was structurally unavailable, resolved only by operator interpretation and an operator-only close.
- blast_radius: multiple_subsystems (work-item lifecycle, councils, clearance, summaries) escalating to system_wide when misdisposition would corrupt governance state.
- permanent_resolution: Add a terminal-disposition preflight returning allowed and disallowed actions, reasons, required actor, authority requirements, and the exact next command.
- objective_acceptance_criteria: routine lifecycle classification no longer requires repeated model deliberation; unsupported terminal actions fail before execution with an exact reason; operator-only actions are clearly identified; the correct terminal command and required authority are returned mechanically.
- required_regression_tests (from the authority): verification-required and verification-not-required items; completed; incomplete; abandoned; cancelled; superseded; accepted-risk; false-DONE prevention; actor and authority enforcement.
- evidence_references / evidence_hashes:
  - message:msg-20260713T211909139280 - 08c3f3b351d1ead5ecf14edf389749ee609ef4bfcfb8f27828719814794ae3f3
  - message:msg-20260713T175640232571 - 7fcf9c190f7095123db13403bc9954e15b09c0b85cc996567d6c8b9d28408263
  - summary:msg-20260719T181730501217 (mis-declared false start; operator close required) - d5bbdcf3f1293c5e10c27553ac484a2abc24a8200a7e461f6b53d5713779b7cb
  - summary:msg-20260719T183202666915 (complete refused verification_incomplete; operator close under recorded exception) - db096c0a81f898944129fe35a04a0bc04bb1d8e6f8c3141d8f60e91b706bfa35
- root_cause_confidence: 0.9 (mechanism absence is directly inspectable in the CLI surface)
- status: TRIAGED; promotion_state: none; operator_disposition: none (operator review pending)

### ALF-0002 - Repeated deliberation without new evidence
- priority_tier: 2, escalating only when recurrence blocks execution (per authority)
- problem_statement: The same entities and candidate decisions recur across a run with no new tool evidence, unchanged authority, unchanged durable state, and unchanged constraints, while tokens and elapsed time increase without execution progress.
- detection_conditions (conservative, all required): same entities and candidate decisions recur; no new tool evidence has appeared; authority has not changed; durable state has not changed; constraints have not changed; tokens and elapsed time increase without execution progress.
- response_constraint: advisory only. It must not interrupt legitimate multi-step analysis, destructive-action caution, changing evidence, authority investigation, or uncertain ownership or concurrency investigation.
- root_cause: no run-level detector correlates decision recurrence with evidence staleness; the executor cannot see its own repetition.
- permanent_resolution: a recurrence-driven advisory detector over run observations (section 11) that reports suspected repetition with the exact recurring entities and the evidence-staleness basis, in the Run Improvement Delta and operator review only.
- objective_acceptance_criteria: detector reports only when every detection condition holds; zero interruptions of the protected analysis classes; advisory output carries the recurring decision set and staleness evidence; false-positive review path through operator disposition.
- required_regression_tests: fixture runs with genuine progress (no report); repeated decisions with new evidence (no report); repeated decisions without new evidence (report); protected classes never interrupted.
- evidence_references / evidence_hashes: authority message:msg-20260719T181306094084 - de155644f62e46370e6fbcfc4dd539d5dfcd2c9be6d71b7186f00f062d1fdb63 (defining record; run-level exemplars accrue at capture time from invocation-log and council-round observations)
- root_cause_confidence: 0.7
- status: TRIAGED; promotion_state: none; operator_disposition: none

### ALF-0003 - Destructive cleanup safety is model-driven instead of tool-enforced
- priority_tier: 1, escalating when actual data-loss risk exists (per authority)
- problem_statement: Safety decisions around destructive cleanup (branch and artifact removal, forced deletion) depend on executor judgment instead of a mechanical preflight.
- root_cause: no cleanup preflight tool exists; the guard rails live in prompts and conventions rather than refusal-capable tooling.
- permanent_resolution: a cleanup preflight covering: branch ancestry; merged pull-request state; tracked-file cleanliness; untracked artifacts; ignored artifacts; live process use; active work-item dependencies; checkpoint dependencies; normal-removal eligibility; force justification. Forced removal must be refused unless normal removal failed for a recorded reason.
- objective_acceptance_criteria: destructive operations are refused unless the preflight passes or records a justified force path; refusal messages state the exact failed check; forced removal without a recorded normal-removal failure is impossible through the tooling; preflight results are durable observations.
- required_regression_tests: each preflight dimension with pass and fail fixtures; force-without-recorded-failure refusal; live-process and active-dependency refusal; checkpoint-dependency refusal.
- evidence_references / evidence_hashes: authority message:msg-20260719T181306094084 - de155644f62e46370e6fbcfc4dd539d5dfcd2c9be6d71b7186f00f062d1fdb63 (defining record; subsystem cli plus queue_store; exemplars accrue from cleanup-bearing runs at capture time)
- root_cause_confidence: 0.8
- status: TRIAGED; promotion_state: none; operator_disposition: none

## 22. Review questions

1. Is the three-layer separation (immutable observations, versioned findings, operator-promoted improvements) structurally sound as specified - in particular the append-only revision log with hash chain and the head-equals-rebuild rule?
2. Do the observation and finding schemas cover the mandatory field set and the Phase 1 capabilities without hidden implementation authority?
3. Is the priority model (hard tier first, transparent formula, versioned model file, effort never reducing priority, regression escalation) correct and reproducible as specified?
4. Are the deduplication, lineage, recurrence, and regression rules safe - especially the operator-confirmed merges and the silent-merge prohibition for protected classes?
5. Is the operator-control boundary complete - can any specified ALF path create authority, create governed work, modify code, alter dispositions, or bypass the promotion gate?
6. Are the three initial findings faithfully evidence-bound, and is the section 10 lineage treatment of the original fourth authority finding (resolved-or-disproved headline evidence, residual preserved as TRIAGED candidate) the correct non-rewriting disposition?
7. Any HIGH or CRITICAL planning defect that must block the plan gate?

End of packet.
