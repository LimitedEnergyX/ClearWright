# ALF Phase 1 (Automated Leap Frog) - Final Planning Packet

Work item: message:msg-20260719T182047862744 (governed, internal_technical, verification required, clearance required)
Authority: msg-20260719T181306094084 (OPERATOR-0001, operator-ui, 2026-07-19T18:13:06Z)
Deployed baseline under review: ClearWright commit 419b1f8 (merge of PR 83), single local control-plane server, operator mode
Packet date: 2026-07-20. Executor: single stateful executor (model claude-fable-5).
Packet status: PLANNING ONLY. This packet grants no implementation authority. The work stops at the plan gate. Implementation requires the recorded plan-gate outcome plus any required operator CTA under the existing authority.

Reviewer instructions: You are reviewing an implementation-ready PLAN for ALF Phase 1 against the current deployed ClearWright architecture summarized in section 3 and the authority summarized in section 2. Evaluate correctness, completeness against the authority, storage and schema soundness, integrity of the immutability and hash-binding model, the priority and deduplication design, the operator-control boundary (nothing in ALF may create authority, create governed work, or act without the operator), and the safety of the Phase 1 exclusions. Identify any HIGH or CRITICAL planning defect explicitly. Answer the review questions in section 22.

Revision 2 note (round 2 of the plan council): this packet was amended after round 1 to resolve every accepted round-1 finding. Summary of the corrections: (a) deterministic content-derived observation identity (capture time excluded from identity; filename equals id); (b) canonical serialization specified for every hashed artifact; (c) append discipline under the existing single-writer lock with flush-before-release; (d) a durable observation-to-finding attribution ledger making synthesis, recurrence, regression, waste accounting, and deltas idempotent; (e) a versioned deterministic hard-tier policy with persisted tier-decision inputs and rationale; (f) a persisted release baseline with a mechanical regression ranking floor and defined cross-model-version semantics; (g) confidence_basis added as a first-class finding field; (h) evidence entries normalized to one-to-one objects with roles (authority-seeded evidence explicitly marked) and verifier result categories defined; (i) an enforceable delta issuance mechanism idempotent per run id with divergence refusal plus a missing-delta verifier escalating at Tier 1, and the every-run claim narrowed to that mechanism; (j) a complete lifecycle transition table with actors, automated disposition-free surfacing into OPERATOR_REVIEW, one-disposition-per-operator-message replay refusal, and promote defined as approval recording plus state-neutral specification rendering; (k) an explicit tamper threat model (hash-based mutation detection plus chained anchors carried in deltas) replacing the physically-append-only wording.

Revision 3 note (round 3 of the plan council): amended again to resolve every accepted round-2 finding: (r3-a) an operation journal making cross-file synthesis writes transactional with deterministic crash replay; (r3-b) a field-by-field canonical data-representation table (fixed two-decimal confidence strings, integer-only JSON numbers in hashed records); (r3-c) the complete priority-model-v1 artifact embedded verbatim, including exact waste-band thresholds and absent-metric rules; (r3-d) delta rerun semantics compatible with generated_at and anchors (preserved first-generation values; deterministic-content comparison; no false divergence); (r3-e) the three seed findings restated as fully schema-valid constructions with exact evidence objects and explicit defaults, plus an observed-occurrence evidence requirement for planning approval; (r3-f) per-line hash chaining for the observation index and attribution ledger with an earliest-unanchored-range verifier rule; (r3-g) mirror-transition actor and external-record reconciliation rules; (r3-h) dedup-policy-v1 named and specified; (r3-i) short-id collision refusal; (r3-j) the section 17 goal sentence narrowed to the enforced mechanism.

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

Storage: QUEUE_ROOT subtree alf/observations/, one JSON file per observation named obs-<identity-hash-16>.json, plus a tamper-evident index alf/observations/index.jsonl (one line per observation: observation_id, sha256 of the observation file bytes, captured_at, run_id, kind, prev_line_sha256, line_sha256 - the per-line hash chain of section 8). Files are written with the existing atomic tmp-then-replace pattern and are never modified afterward; index appends run under the single-writer lock with flush-before-release (section 8 write discipline).

Collision refusal: on writing an observation whose id already exists, the writer compares the FULL canonical identity tuple, not the truncated id. Identical tuple = verified no-op. Same truncated id with a DIFFERENT tuple = the write is REFUSED and reported as a Tier 1 durable-record-integrity finding candidate (id_collision); it is never treated as an ordinary existing-file condition.

Observation identity (deterministic, single scheme): observation_id = "obs-" + first 16 hex of sha256 over the canonical serialization (section 8) of the identity tuple {kind, subsystem, work_item_id, thread_id, council_id, gate_id, summary, source_refs, metrics}. captured_at, run_id, capture_method, and capturing_actor are recorded fields but are EXCLUDED from identity, so re-capturing the same facts yields the same id and is a verified no-op (the writer confirms byte-equality of the identity fields and skips). Different metrics or different source facts produce a different identity and therefore a distinct observation. The filename, the index key, and every evidence reference use this same observation_id.

Observation schema (alf_record_version 1):
- observation_id: string, obs-<identity-hash-16> (deterministic; see identity rule above)
- captured_at: UTC ISO timestamp (recorded; excluded from identity)
- run_id: string binding the capturing run (work item id plus capture sequence)
- work_item_id, thread_id, council_id, gate_id: optional subject bindings
- kind: enum - run_started, run_completed, run_closed, council_round, council_outcome, reviewer_attempt, dispatch_failure, gate_created, gate_resolved, lifecycle_event, complete_refusal, close_recorded, operator_intervention, resource_usage, executor_note
- subsystem: enum - envelope_classification, work_item_lifecycle, council_engine, reviewer_gpt, reviewer_codex, egress_guard, dispatch_lane, queue_store, gates, server_lifecycle, operator_ui, cli, executor_process, other
- summary: short plain-language statement of the observed fact (no secrets, no personal content; same hygiene rules as council packets)
- source_refs: array of evidence objects {ref, sha256, role, archived_location} in the section 8 form (single source of truth; no parallel arrays)
- metrics: optional object - operator_minutes, execution_delay_seconds, token_estimate, gpt_tokens_actual_in, gpt_tokens_actual_out, api_attempts, tool_attempts, council_attempts, invocations
- capture_method: enum - cli_explicit (alf-observe invocation), run_boundary (emitted by the run wrapper at start and terminal events), backfill (operator-authorized historical import)
- capturing_actor: string (executor identity)

Capture design (Phase 1): observation capture is invocation-driven, not a daemon. A new CLI verb (section 18, ALF-CLI) reads the existing durable surfaces for a named run or work item and emits observations deterministically; re-running capture for the same facts is idempotent under the identity rule above. No polling process, no background service.

Immutability enforcement (tamper-evident, application-level): the writer refuses to write an observation file that already exists with different identity-field bytes; the index receives appends only; a verification helper re-hashes files against the index and reports any divergence as a Tier 0 durable-record-integrity event. The protection claim and its boundary are stated precisely in the section 8 threat model (these records are tamper-EVIDENT through hashes and chained anchors, not physically append-only on a local filesystem).

## 6. Durable, versioned findings

Storage: QUEUE_ROOT subtree alf/findings/<entry_id>.json (head record) plus alf/findings/history/<entry_id>.jsonl (append-only revision log). Every revision line contains: revision_no, revised_at, revising_actor, reason, the full finding record as of that revision, prev_revision_sha256 (hash chain), and revision_sha256 (sha256 of the canonical serialized revision line minus its own hash field). The head file always equals the last revision's record. Rebuilding the head from the log must reproduce it byte-for-byte (regression-tested).

Finding schema (alf_record_version 1) - the full mandatory field set from the authority:
entry_id; title; status; priority_tier; priority_score; priority_model_version; first_seen_at; last_seen_at; occurrence_count; affected_run_count; affected_work_item_count; cumulative_operator_minutes; cumulative_execution_delay; cumulative_token_estimate; cumulative_api_attempts_wasted; cumulative_tool_attempts_wasted; cumulative_council_attempts_wasted; subsystem; failure_class; security_impact; reliability_impact; authority_integrity_impact; durable_record_integrity_impact; operator_time_impact; execution_delay_impact; token_api_compute_impact; blast_radius; problem_statement; observed_symptoms; root_cause; root_cause_confidence; confidence_basis; immediate_containment; immediate_workaround; permanent_resolution; objective_acceptance_criteria; required_regression_tests; evidence_references; evidence_hashes; related_entries; supersession_lineage; owner; estimated_effort; dependencies; blockers; promotion_state; operator_disposition; deferral_reason; review_date; last_operator_reviewed_at; implementation_work_item_id; released_version; verification_evidence. Plus the schema-extension fields added by this plan: tier_decision (section 15), release_baseline (section 12), authority_seeded (boolean; section 21).

Field conventions:
- entry_id: ALF-<four-digit sequence> (ALF-0001 upward), allocated once, never reused.
- status: the primary lifecycle enum (section 16). promotion_state mirrors the promotion-relevant subset. operator_disposition: none, accepted_risk, deferred, rejected, superseded, not_reproducible, approved.
- impact axes (security_impact, reliability_impact, authority_integrity_impact, durable_record_integrity_impact, operator_time_impact, execution_delay_impact, token_api_compute_impact): integer 0 to 3 (none, low, medium, high), scored under the versioned model (section 15).
- blast_radius: single_event, single_run, single_work_item, single_subsystem, multiple_subsystems, all_councils, system_wide, external_or_public.
- root_cause_confidence: stored as a fixed two-decimal string "0.00" through "1.00" per the section 8 canonical representation table (numeric only in display surfaces); confidence_basis is a first-class mandatory string field stating the basis for that value; confidence values below "0.50" render prominently in operator review (section 16).
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

Evidence entry form (one-to-one by construction): evidence_references is an array of objects {ref, sha256, role, archived_location} where ref uses the typed grammar above, sha256 is computed over the exact referenced bytes at capture time (whole file for record references; the exact byte span for log spans; the committed blob for repo-file references), role is one of defining_authority | observed_occurrence | verification | correction, and archived_location is optional. The flat mandatory field evidence_hashes is DERIVED by the writer from the objects (same order) and validated for exact length and order parity on every revision; the objects are the single source of truth. A revision that adds a reference without its hash, or whose derived array diverges, is rejected by the writer. Duplicate refs are normalized (one object per distinct ref; a re-capture updates role or archived_location only through a new revision).

Hash verification is re-runnable: a verifier command re-hashes every reference and classifies each as verified (bytes match), archived_unavailable (source no longer present at the recorded location; capture-time hash stands as evidence), or divergent (bytes exist and differ - which itself becomes a Tier 0 durable-record-integrity observation).

Canonical serialization (applies to every hashed artifact - observation identity tuples and files, finding records, revision lines, index and ledger lines, tier decisions, model input vectors, delta records): UTF-8, JSON with sorted keys, compact separators (comma and colon, no spaces), ensure_ascii off, exactly one trailing newline per record line, byte spans referenced by exact offsets. Two implementations following this section must reproduce identical hashes.

Canonical data-representation table (field by field; hashed records contain ONLY these forms, and the writer refuses any non-integer JSON number in a hashed record):
- Confidence-like values (root_cause_confidence, merge-proposal confidence, automated-judgment confidence): fixed two-decimal STRINGS, "0.00" through "1.00", in every stored and hashed record; numeric rendering happens only in display surfaces.
- Counters and attempts (occurrence_count, affected counts, api/tool/council attempts, invocations): JSON integers.
- cumulative_operator_minutes: integer minutes, rounded up at capture. cumulative_execution_delay: integer seconds, rounded up. cumulative_token_estimate and actual token fields: integers.
- Scores, tiers, blast-radius ranks, waste bands, effort points: integers. Leverage: two-decimal string, display-derived, never hashed as a number.
- Timestamps (captured_at, generated_at, at, computed_at, first_seen_at, last_seen_at, surfaced_at): UTC ISO-8601 strings with Z suffix, microsecond precision.
- Identifiers, enums, refs, hashes: strings from their defined vocabularies. Booleans: JSON booleans. Absent optional values: JSON null (never omitted keys in hashed structures).
Cross-language serialization fixtures with known hashes are part of the P1a test set.

Operation journal (transactional synthesis; the exactly-once mechanism): alf/journal/journal.jsonl, append-only under the writer lock with the same per-line hash chain as the ledger. Every synthesis operation (create finding, recurrence update, regression reopen, waste attribution, merge confirmation, disposition, delta generation) runs as: (1) append op_begin {op_id, operation_kind, staged_writes: [{target_path_rel, content_sha256}], at} where op_id = sha256-16 over canonical {operation_kind, primary attribution_id or subject ids, staged content hashes} and every staged payload is fully derived BEFORE the journal entry; (2) perform the staged writes in journal order (ledger line, then finding revision line, then finding head, then index or delta files); (3) append op_commit {op_id, at}. Readers treat data reachable only from an uncommitted operation as pending. Startup and alf-verify recovery: for each op_begin without op_commit, re-verify each staged write by content hash - present and matching stays, absent is completed byte-identically from the staged derivation, then op_commit is appended; because every staged payload is content-derived and idempotent (ledger and revision lines carry deterministic ids), replay converges to exactly the staged state and can neither double-count nor lose an attribution. An operation that cannot be completed (staged derivation no longer reproducible) is REFUSED by the verifier and reported as a Tier 1 durable-record-integrity finding candidate with the exact op_id. Crash-point regression tests cover a kill between every adjacent write in the sequence.

Write discipline: all ALF mutations run under the existing ClearWright single-writer lock. JSONL appends are written whole-line, flushed to disk before the lock is released; head files (finding heads, delta files, model files) are written with the existing atomic tmp-then-replace pattern (with the Windows sharing-violation retry already shipped in the queue writer). A torn or interleaved append is therefore excluded by the lock, and a crash mid-append leaves at most one incomplete final line, which the reader detects (invalid JSON line) and the verifier reports.

Attribution ledger (idempotency authority for synthesis): alf/attributions/ledger.jsonl, append-only under the same lock, per-line hash chained (each line carries prev_line_sha256 and line_sha256 over the canonical line minus its own hash field; the observation index and the journal use the same chaining). One line per attribution: {attribution_id = sha256-16 over canonical {observation_id, entry_id, attribution_type}, observation_id, entry_id, attribution_type (initial_evidence | recurrence | regression | waste | delta_report), at, run_id, prev_line_sha256, line_sha256}. Every counter update, recurrence link, regression reopen, waste accumulation, and delta line item is written inside an operation-journal transaction (above) together with its ledger line; an attribution whose id already exists is a verified no-op (nothing increments twice). Re-running synthesis over the same observations is therefore idempotent end to end, including across crashes. The ledger is the authoritative answer to what has been counted.

Threat model (explicit, honest boundary): ALF integrity protection is application-level and tamper-EVIDENT, not physically append-only and not externally anchored WORM storage. Detected intrinsically (per-line chains): in-file mutation, reordering, or truncation of index, ledger, journal, and finding-history lines breaks the line chain and is reported with the first broken position. Detected by chained anchors: wholesale replacement of a chained file after the first delta exists - each Run Improvement Delta carries an anchors block {observations_index_head_sha256, ledger_head_sha256, findings_revision_heads_sha256} computed at first generation, and each delta records the previous delta's anchor-block sha256, forming a chain; replacing history without forging every subsequent delta (which run summaries reference) breaks the chain and is reported. Honesty rule: content written after the most recent delta anchor is protected only by its line chain until the next anchor; the verifier explicitly identifies this earliest unanchored range instead of implying total coverage. NOT defended: an attacker with full filesystem write access who consistently rewrites all ALF records, all deltas, and all referencing run records - outside the supported threat model and stated as such.

## 9. Finding deduplication

Deduplication is proposal-based, never silent for protected classes:
- Candidate key: (subsystem, failure_class, normalized root-cause signature). Normalization and similarity are fixed by a versioned artifact alf/meta/dedup-policy-v1.json: ASCII-lowercase the root_cause text; split on every non-alphanumeric character except underscore (identifiers with underscores and digits survive as single tokens); drop the exact stopword list enumerated in the artifact; the signature is the sorted unique token set. Exact key match proposes duplicate_of with confidence "0.90"; Jaccard similarity of token sets at or above 0.80 proposes with confidence "0.80"; at or above 0.60 proposes with confidence "0.60"; below 0.60 no proposal. The dedup-policy version is stored with every proposal, and policy changes require a new artifact version.
- A proposed merge is a pending relationship on the newer entry (duplicate_of, proposed, confidence, rationale). The operator confirms or rejects it in review. On confirmation the newer entry's status becomes MERGED, meaning deduplicated into another ALF finding (not a Git or code merge); its occurrence and waste counters fold into the surviving entry; both entries record the supersession_lineage pair; the merged entry stays readable forever.
- Silent-merge prohibition: findings whose failure_class or impact axes touch security, privacy, authority integrity, or durable-record integrity are never auto-merged; the merge proposal renders prominently and waits for the operator.
- Auto-accept threshold: none in Phase 1. Every merge is operator-confirmed. (Conservative start; a later authority may loosen.)

## 10. Lineage and supersession

Every finding carries supersession_lineage: an ordered array of {entry_id, relationship, at, reason, evidence_ref}. Rules:
- supersedes / superseded_by are symmetric pairs written to both entries in one operation.
- A superseding entry must reference the evidence that changed the understanding.
- Lineage is append-only within the revision log; a lineage entry is corrected only by a subsequent lineage entry, never by rewriting.
- Merged and superseded entries remain in the store and in listings filtered by status.

Applied lineage record (initial state, from durable evidence): the original authority text listed a fourth initial finding, council dispatch eligibility is checked too late. After the authority was posted, its two headline evidence items changed: the governed internal_technical classification coupling was corrected in production at commit 419b1f8 (work-record REPAIR-RESULT.md, sha256 506a5e86dd22b0090c7570f7b521b8502b6d47ebe4f46bd0369492d958590daf), and the suspected GPT ITS request-body construction defect was empirically disproved (work-record REPAIR-GPT-RCA-DISPROOF.md, sha256 dd3c91d7bbd5256604de0b366f96316fec7e8735ba028ecd95418f2021436fb5: the production adapter body is byte-identical to the guard canonical form). The operator's resume directive therefore names three approved initial findings (section 21). The residual concept from the fourth - a deterministic dispatch-eligibility preflight that runs before any council id or reviewer attempt is allocated - remains real (its remaining evidence: source-outside-repo rejections, pre-SDEG envelope incompatibility, unicode-confusable rejections, and reviewer attempts consumed before eligibility was proven) and enters the store at first synthesis as a TRIAGED candidate carrying this lineage note, subject to normal operator review. It is not one of the three approved initial findings and no history about it was rewritten. Mechanics (deterministic): at first synthesis it is allocated the next free entry sequence after the three seed findings; its evidence entries are {ref work-record:REPAIR-GPT-RCA-DISPROOF.md, sha256 dd3c91d7bbd5256604de0b366f96316fec7e8735ba028ecd95418f2021436fb5, role correction} and {ref work-record:REPAIR-RESULT.md, sha256 506a5e86dd22b0090c7570f7b521b8502b6d47ebe4f46bd0369492d958590daf, role correction} plus the surviving occurrence evidence, each marked with its role; it is never auto-approved and follows the normal transition table of section 16.

## 11. Recurrence detection

- Every synthesis pass matches new observations against existing findings by the deduplication key before creating anything new. A match updates last_seen_at, occurrence_count, affected_run_count, affected_work_item_count, and the cumulative waste counters, and appends a recurrence_of relationship from the triggering observation - every such update written together with its attribution-ledger line (section 8), so a re-run of synthesis over the same observations changes nothing.
- Recurrence never lowers priority. A recurrence while status is RELEASED or MONITORING is a regression (section 12), not a plain recurrence.
- Recurrence of a DEFERRED or ACCEPTED_RISK finding re-raises it into OPERATOR_REVIEW at the next delta with the recurrence evidence attached (the operator sees that the accepted risk is recurring; the disposition is not silently changed).
- The conservative excess-deliberation detector (Finding ALF-0002, section 21) is recurrence-driven: it reports only when the same entities and candidate decisions recur with no new tool evidence, unchanged authority, unchanged durable state, and unchanged constraints while tokens and elapsed time increase without execution progress - and its output is advisory only.

## 12. Regression handling

When a released or monitored issue recurs:
1. Reopen the ORIGINAL finding (status RELEASED or MONITORING back to PRIORITIZED; never a new low-priority duplicate).
2. Link the previous correction (implementation_work_item_id, released_version) and the previous verification_evidence.
3. Link the new run and the new evidence (references plus hashes).
4. Update occurrence_count and affected_run_count.
5. Re-score priority under the current model version and enforce the RANKING FLOOR mechanically: when a finding first reaches RELEASED, the writer persists release_baseline = {tier, score, priority_model_version, at}; a reopened regression's effective tier is min(new_tier_number, baseline_tier_number) (numerically lower tier wins, Tier 0 lowest) and, when the effective tier equals the baseline tier, its effective score is max(recomputed_score, baseline_score_mapped) where baseline_score_mapped is the baseline score when the model version is unchanged, or the baseline input vector re-evaluated under the current model version when the model was upgraded (the stored input vector makes this deterministic). The regression escalation term of section 15 applies on top. Both the recomputed and the floored values are stored in the tier_decision record, so the floor application is itself reproducible.
6. Classify the reopening as regression_of in related_entries and set failure_class accordingly if the recurrence reveals a broader class.
7. Surface the regression in the next Run Improvement Delta and in operator review as a distinct regressions group.

## 13. Cumulative waste accounting

Per finding, monotonically non-decreasing counters, updated only from observation metrics (never estimated retroactively): cumulative_operator_minutes; cumulative_execution_delay (seconds); cumulative_token_estimate (plus actual provider-reported tokens when the surface records them); cumulative_api_attempts_wasted; cumulative_tool_attempts_wasted; cumulative_council_attempts_wasted. Attribution rule: an observation's metrics are attributed to a finding only when the observation is linked to it (recurrence, regression, or initial evidence), and every attribution is recorded exactly once in the attribution ledger (section 8) - a metrics contribution without a fresh ledger line is refused, and an existing ledger line makes the contribution a no-op, so totals can never double-count across re-runs. Waste totals are display inputs and scoring inputs (section 15) and appear in every Run Improvement Delta as deltas plus running totals.

## 14. Hard priority tiers

A hard tier is assigned before any numeric score and is never reduced by effort:
- Tier 0: active or plausible authority bypass; privacy exposure; credential exposure; sensitive-data egress; destructive-action risk; unauthorized mutation; durable-record corruption; production ownership conflict.
- Tier 1: authority-integrity defects; durable-record-integrity defects; correctness failures; operational reliability failures; stale or misleading state; broken recovery; repeatable work blockers; council, queue, lifecycle, or deployment failures.
- Tier 2: operator time; execution delay; unnecessary councils; unnecessary retries; repeated tool calls; token, API, and compute waste; poor diagnostics; excessive deliberation without new evidence.
- Tier 3: clarity; user experience; documentation; maintainability; nice-to-have improvements.

Display rules: priority (tier plus score), estimated_effort, and leverage are displayed separately; effort never changes tier or score; visible critical findings are never capped; work-in-progress limits apply only to active implementation and planning states, never to visibility.

Deterministic tier assignment (versioned tier policy): tier assignment is mechanical, not prose judgment. The versioned model file (section 15) carries tier-policy-v1: an ordered list of predicates over structured finding inputs, evaluated top down, first match wins, default Tier 3. Structured inputs added for this purpose (recorded on the finding and in every tier decision): risk_activity (active | plausible | historical), exposure_class (none | privacy | credential | sensitive_egress), mutation_class (none | unauthorized_mutation_risk | destructive_action_risk), record_integrity_class (none | corruption_risk | corruption_observed), ownership_conflict (boolean). tier-policy-v1 predicates: Tier 0 when risk_activity in (active, plausible) AND (exposure_class is not none OR mutation_class is not none OR record_integrity_class is not none OR ownership_conflict); Tier 1 when authority_integrity_impact >= 2 OR durable_record_integrity_impact >= 2 OR failure_class in (authority_bypass_risk, durable_record_integrity, correctness, operational_reliability, stale_state, broken_recovery, work_blocker, council_failure, queue_failure, lifecycle_failure, deployment_failure); Tier 2 when failure_class in (operator_time, execution_delay, resource_waste, poor_failure_reporting, excess_deliberation) OR operator_time_impact >= 2 OR execution_delay_impact >= 2 OR token_api_compute_impact >= 2; Tier 3 otherwise. Every ranking revision persists a tier_decision record: {tier_policy_version, input_vector (all predicate inputs), matched_predicate, tier, computed_at} - identical inputs always reproduce the identical tier, offline.

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
- Every stored ranking decision records: entry_id, model version, tier_policy_version, the full input vector (impact axes, blast radius, occurrence, regression flag, waste bands, and the tier predicate inputs of section 14), matched tier predicate, score, tier, any regression floor application (recomputed and floored values), computed_at, and the triggering observation or revision. The model file also fixes the exact waste-band thresholds. Identical stored inputs reproduce identical tier and score offline.

Complete priority-model-v1 artifact (embedded verbatim; the file alf/meta/priority-model-v1.json is byte-identical to this block, and every tier decision records priority_model_sha256 over these bytes):

    {"model_version":"priority-model-v1",
     "tier_policy_version":"tier-policy-v1",
     "weights":{"security_impact":4,"authority_integrity_impact":4,
       "durable_record_integrity_impact":4,"reliability_impact":3,
       "operator_time_impact":2,"execution_delay_impact":2,
       "token_api_compute_impact":1},
     "radius_multiplier":2,
     "recurrence_multiplier":2,"recurrence_cap":10,
     "regression_term":12,
     "waste_multiplier":2,
     "waste_bands":{
       "cumulative_operator_minutes":{"band1":30,"band2":120,"band3":480},
       "cumulative_execution_delay":{"band1":600,"band2":3600,"band3":14400},
       "cumulative_token_estimate":{"band1":100000,"band2":500000,"band3":2000000},
       "cumulative_api_attempts_wasted":{"band1":3,"band2":10,"band3":25},
       "cumulative_tool_attempts_wasted":{"band1":10,"band2":50,"band3":200},
       "cumulative_council_attempts_wasted":{"band1":2,"band2":5,"band3":10}},
     "waste_band_rule":"per counter: band 0 below band1; thresholds are INCLUSIVE lower bounds (value >= band1 gives 1, >= band2 gives 2, >= band3 gives 3); an absent or null metric is band 0; WB = maximum band across all six counters",
     "effort_points_enum":[1,2,3,5,8],
     "score_rule":"score = sum(weights[axis]*axis_value) + radius_multiplier*blast_radius_rank + recurrence_multiplier*min(occurrence_count-1,recurrence_cap) + regression_term*regression_flag + waste_multiplier*WB",
     "offline_recompute_rule":"recomputation MUST use the raw persisted cumulative counters and this artifact; a stored WB value is a cache and is never authoritative"}

Changing the formula requires a new model file version (priority-model-v2 and so on); historical decisions keep their original version reference; re-ranking under a new version is an explicit revision with reason model_upgrade.

## 16. Operator review and promotion controls

Primary lifecycle: OBSERVED -> TRIAGED -> MERGED (dedup terminal) or PRIORITIZED -> OPERATOR_REVIEW -> APPROVED_FOR_PLANNING -> WORK_ITEM_CREATED -> IN_PROGRESS -> VERIFICATION -> RELEASED -> MONITORING -> CLOSED. Additional dispositions: ACCEPTED_RISK, DEFERRED, REJECTED, SUPERSEDED, NOT_REPRODUCIBLE.

Complete legal transition table (structural, enforced by the writer; any transition not listed is refused with the exact reason):
- OBSERVED -> TRIAGED: automated (synthesis). TRIAGED -> MERGED: operator-only (merge confirmation). TRIAGED -> PRIORITIZED: automated (scoring completes; tier_decision persisted).
- PRIORITIZED -> OPERATOR_REVIEW: automated and disposition-free - the finding is SURFACED for review when it first appears in a Run Improvement Delta or an operator listing; entering OPERATOR_REVIEW records only surfaced_at and changes no judgment field. Re-scoring while in OPERATOR_REVIEW is permitted (automated) and never leaves the state.
- OPERATOR_REVIEW -> APPROVED_FOR_PLANNING: operator-only, and additionally gated by the promotion elements below.
- OPERATOR_REVIEW -> DEFERRED | REJECTED | ACCEPTED_RISK | SUPERSEDED | NOT_REPRODUCIBLE: operator-only dispositions. DEFERRED requires deferral_reason and review_date. Recurrence of DEFERRED or ACCEPTED_RISK re-surfaces into OPERATOR_REVIEW automatically (section 11) without changing the recorded disposition history.
- APPROVED_FOR_PLANNING -> WORK_ITEM_CREATED -> IN_PROGRESS -> VERIFICATION -> RELEASED -> MONITORING -> CLOSED: recorded by REFERENCE only, mirroring the normal governed workflow's own records (implementation_work_item_id, released_version, verification_evidence); ALF never creates or advances the underlying governed work. RELEASED persists release_baseline (section 12). RELEASED or MONITORING -> PRIORITIZED: automated regression reopen (section 12).
- Regression reopen, recurrence updates, and merge PROPOSALS are automated; merge CONFIRMATION is operator-only.

Operator-message binding (all operator-only transitions, CLI and UI identically): the disposition command references a durable INBOUND operator message that (a) exists in the communications store, (b) has role operator and direction inbound, (c) was created AFTER the finding revision it disposes, (d) names the entry_id (or the merge pair), and (e) has not been used for any prior ALF disposition - one disposition per message id, so replay is refused. The consumed message id is recorded on the disposition revision (same authority pattern as the existing close and grant-proceed commands). No operator message, no transition.
- APPROVED_FOR_PLANNING gate (all required): defined permanent_resolution; objective_acceptance_criteria; required_regression_tests; evidence references plus hashes INCLUDING at least one observed_occurrence evidence entry (defining-authority evidence alone seeds a finding but does not qualify it for planning approval); an understood root cause (root_cause_confidence at or above "0.50") or an explicit investigation requirement recorded instead; known dependencies and blockers. The writer refuses the transition when any element is missing.
- Mirror-transition actor and reconciliation (WORK_ITEM_CREATED through CLOSED): the synthesis pass (automated) writes mirror updates by READING the governed workflow's own durable records (envelopes, summaries, clearance records). Binding proof: a promoted specification records its spec id; the operator includes that spec id in the governed work item's envelope text when creating the implementation work; the mirror accepts implementation_work_item_id only when the referenced envelope cites the spec id. A missing, contradictory, or non-citing external record NEVER advances the mirror - the finding is flagged items_requiring_operator_review with reason external_record_mismatch and waits for the operator.
- Prominence rules in review: root_cause_confidence below "0.50", low-confidence merge proposals, and any Tier 0 entry render at the top with explicit markers. Regressions render as their own group. Authority-seeded findings (authority_seeded true) are visibly marked until observed-occurrence evidence attaches.
- The promote action = the operator's APPROVED_FOR_PLANNING disposition (message-bound as above) PLUS a state-neutral rendering of the specification document (section 18). Rendering writes only the spec file; it changes no finding state by itself and is re-runnable.

## 17. Run Improvement Delta

Phase 1 guarantee (stated exactly; nothing stronger is claimed): ALF provides invocation-driven delta generation plus missing-delta verification and escalation, so that every terminal governed run either has its Run Improvement Delta or is explicitly reported as lacking one. The authority's every-run outcome is reached through this mechanism and operator process; automatic wiring into the terminal-summary path is a Phase 2 candidate and is NOT claimed here.

Mechanics: the delta generator runs at run boundaries (invocation-driven, same trigger as observation capture) and writes alf/deltas/rid-<run-id>.json inside an operation-journal transaction. Idempotence and rerun semantics (deterministic, timestamp-safe): generated_at is the FIRST successful generation time and anchors reflect the state at first generation; both are preserved verbatim on any rerun. A rerun for an existing run_id recomputes ONLY the deterministic content (every field except generated_at and anchors) from the run-scoped ledger lines (attributions carrying that run_id) and compares it to the stored delta's deterministic content: equal = verified no-op (file untouched, later global anchor movement never re-anchors an existing delta and is never divergence); different = REFUSED divergent rewrite, reported as a Tier 1 durable-record-integrity finding candidate (delta content is a pure function of the run-scoped ledger, so divergence signals a defect). The MISSING-DELTA VERIFIER (part of alf-verify) lists terminal governed runs (from summaries) that lack a delta and reports each as a Tier 1 lifecycle_failure finding candidate, so gaps are detected and escalated rather than silently accepted.

Delta schema (alf_record_version 1): run_id; work_item_id; generated_at; new_observations [ids]; new_findings [entry ids]; observations_merged_into_existing [{observation_id, entry_id}]; findings_priority_changed [{entry_id, old_tier, old_score, new_tier, new_score, model_version, reason}]; released_fixes_revalidated [{entry_id, verification_evidence_ref}]; regressions_detected [{entry_id, evidence_refs}]; items_requiring_operator_review [entry ids with reasons]; cumulative_waste_changes {per counter: delta and new total}; anchors {observations_index_head_sha256, ledger_head_sha256, findings_revision_heads_sha256, prev_delta_anchors_sha256} (the tamper-evidence chain of section 8).

An empty delta is still written (all arrays empty, anchors populated) so a governed run with ALF capture invoked always has its delta record, and the missing-delta verifier detects every terminal governed run without one.

## 18. Proposed governed-work specifications

When the operator approves a finding for planning, ALF renders a specification document alf/specs/spec-<entry_id>-v<n>.md containing: problem statement; evidence summary with references and hashes; permanent resolution; objective acceptance criteria; required regression tests; dependencies and blockers; estimated effort; proposed envelope skeleton (task_kind governed unless the operator directs otherwise, explicit approved-scope draft, excluded-actions draft carrying every ALF prohibition that applies). The specification is input material for the OPERATOR to create authority and a work item through the normal workflow. ALF never posts it anywhere.

Implementation phasing proposal for Phase 1 itself (post-CTA, for the later implementation turn; listed here so the plan is complete and testable):
- P1a: storage layer plus schemas (observations, findings, revision log, index), writers with immutability and hash-chain enforcement, ALF-CLI verbs alf-observe, alf-list, alf-show, alf-verify-hashes. Regression tests for immutability refusal, hash-chain integrity, head-rebuild equality, idempotent capture.
- P1b: synthesis (dedup proposals, recurrence, regression reopen), scoring under priority-model-v1, delta generation. Regression tests: dedup key determinism, protected-class silent-merge refusal, recurrence counter updates, regression reopen path, scoring reproducibility from stored inputs, empty-delta emission.
- P1c: operator review surface (section 19) and operator-authority-checked disposition verbs (alf-review with an operator message id), promotion-gate refusal tests, actor-rule enforcement tests.
- Compatibility and migration: purely additive - a new alf/ subtree under QUEUE_ROOT; no existing record shape changes; every ALF record carries alf_record_version 1; readers ignore unknown fields; absence of the alf/ subtree means ALF simply has no data (all existing flows unaffected). No backfill in Phase 1 except operator-authorized explicit imports (capture_method backfill).
- Test plan (full): the unit and integration tests above, plus negative and recovery tests - attempted observation rewrite refused; evidence entry without hash refused; derived evidence_hashes parity violation refused; APPROVED_FOR_PLANNING without gate elements (including a missing observed_occurrence evidence entry) refused; automated actor attempting an operator-only transition refused; operator message reuse (replay), wrong-entry, or stale-order message refused; repeated synthesis over the same observations changes no counter, link, or delta (ledger idempotence); crash-point tests killing the writer between EVERY adjacent write in the journal sequence (op_begin, ledger line, revision line, head replace, index or delta write, op_commit) followed by recovery replay that converges byte-identically without double-count or loss; a stuck unrecoverable operation is refused and reported with its op_id; cross-language canonical-serialization fixtures reproduce the published hashes (including two-decimal confidence strings and integer-only numerics); delta rerun for the same run_id is a verified no-op both before and after unrelated ALF activity moved global anchors, and a genuinely divergent rewrite is refused; interrupted append leaves a detectable incomplete final line that the verifier reports; per-line chain break (mutation, reorder, truncation) in index, ledger, or journal is reported at the first broken position, and the verifier names the earliest unanchored range; a terminal governed run without a delta is reported by the missing-delta verifier; a model upgrade after RELEASED re-evaluates the stored baseline input vector deterministically; tier-policy edge cases (each predicate boundary) and waste-band boundary values (each inclusive threshold) reproduce identical tiers and scores from identical inputs; observation identity collisions: identical tuple is a verified no-op, same-short-id different-tuple is refused and reported; index or ledger replacement breaks the delta anchor chain and is reported; delta generator never mutates findings except through the synthesis path; no code path callable from ALF creates a work item, posts to GitHub, or edits repository files (asserted by construction and by tests that the CLI verbs registered for ALF contain no such calls).

## 19. Minimal operator-facing view

Extend the existing local console with one route group (list plus detail), no dashboard redesign:
- List view: findings ordered by (tier, score), filterable by status, subsystem, and disposition; columns: entry_id, title, tier, score, status, occurrence_count, last_seen_at, root_cause_confidence marker, regression marker. Tier 0 and low-confidence markers always visible at top. No cap on visible critical findings.
- Detail view: full finding record, revision history, evidence references with hashes, related entries and lineage, current merge proposals, waste counters, and - when populated - the specification rendering.
- Disposition actions: approve for planning, defer (with deferral_reason and review_date), reject, accept risk, confirm or reject merge, supersede, not reproducible, promote (approval plus state-neutral specification rendering). Binding flow (explicit): the UI first records the operator's disposition statement as a normal durable INBOUND operator message through the existing console message path (operator-authored, idempotency-keyed, naming the entry_id and the action), and only then invokes the disposition writer referencing that message id; the writer re-verifies existence, inbound operator role, post-revision creation order, entry match, and single use (replay refused) - exactly the checks the CLI applies, so the UI grants nothing the CLI would refuse.
- Read parity CLI: alf-list and alf-show render the same data in the terminal for headless operation.

## 20. Explicit Phase 1 exclusions

Not implemented in Phase 1 (per authority): autonomous correction; automatic governed-work creation; GitHub issue synchronization; broad dashboard redesign; cross-repository integration; automatic authority issuance; self-modification; unrelated lifecycle, restart, installer, SSO, connector, or public-alpha work. GitHub boundary: the existing 26 open GitHub issues are intentional roadmap items and are not closed, merged, reorganized, relabeled, rewritten, or synchronized during ALF Phase 1; GitHub Issues are not the canonical ALF store; operator-approved findings may later be linked or promoted to GitHub issues under separate authority only. Additional standing prohibitions restated: ALF may not create operator authority, create governed work items automatically, begin implementation without authority, modify code without authority, make operator dispositions, or self-modify; the canonical ALF store remains inside ClearWright; a finding is never implementation authority; council agreement is never implementation authority; implementation begins only after the plan gate and any required CTA under the existing governed workflow.

## 21. Initial evidence-bound ALF findings

Three approved initial findings enter the store at first synthesis, evidence-bound as follows. (Original authority finding four: see the lineage record in section 10 - resolved-or-disproved headline evidence; residual concept preserved as a TRIAGED candidate, not an approved initial finding.)

Seed construction rule (makes each seed fully schema-valid at synthesis): every mandatory field is populated; fields not stated per finding take these defaults - immediate_containment "none required", immediate_workaround "none recorded", owner "operator", related_entries [], supersession_lineage [], dependencies [], blockers [], promotion_state null, deferral_reason null, review_date null, last_operator_reviewed_at null, implementation_work_item_id null, released_version null, verification_evidence null, release_baseline null; first_seen_at and last_seen_at = synthesis capture time; occurrence_count 1, affected_run_count 1, affected_work_item_count 1; all cumulative counters 0 until attributed through the ledger; evidence entries are exactly {ref, sha256, role, archived_location} with archived_location null; tier_decision is computed at synthesis under tier-policy-v1 from the stated input vector and persisted with priority_model_sha256; priority_score is computed from the stated impact vector under priority-model-v1. Stated per-finding inputs below use the canonical representations of section 8.

### ALF-0001 - Missing deterministic terminal-disposition engine
- priority_tier: 1 (failure_class lifecycle_failure; authority_integrity_impact 3, reliability_impact 2, operator_time_impact 2; blast_radius multiple_subsystems to system_wide)
- problem_statement: The executor repeatedly interprets whether a work item should be completed, closed, superseded, abandoned, cancelled, or left open. Lifecycle classification is model-driven where it should be mechanical.
- root_cause: ClearWright lacks a mechanical lifecycle and disposition preflight that returns: deliverable status; verification requirements; legal terminal actions; required actor; exact authority needed; disallowed actions with reasons; valid superseding relationships.
- observed_symptoms: repeated deliberation over terminal choices; a mis-declared item requiring operator closure (classification conflict); a delivered repair whose formal DONE path was structurally unavailable, resolved only by operator interpretation and an operator-only close.
- blast_radius: multiple_subsystems (work-item lifecycle, councils, clearance, summaries) escalating to system_wide when misdisposition would corrupt governance state.
- permanent_resolution: Add a terminal-disposition preflight returning allowed and disallowed actions, reasons, required actor, authority requirements, and the exact next command.
- objective_acceptance_criteria: routine lifecycle classification no longer requires repeated model deliberation; unsupported terminal actions fail before execution with an exact reason; operator-only actions are clearly identified; the correct terminal command and required authority are returned mechanically.
- required_regression_tests (from the authority): verification-required and verification-not-required items; completed; incomplete; abandoned; cancelled; superseded; accepted-risk; false-DONE prevention; actor and authority enforcement.
- evidence_references (exact object form):
  - {"ref":"message:msg-20260713T211909139280","sha256":"08c3f3b351d1ead5ecf14edf389749ee609ef4bfcfb8f27828719814794ae3f3","role":"observed_occurrence","archived_location":null}
  - {"ref":"message:msg-20260713T175640232571","sha256":"7fcf9c190f7095123db13403bc9954e15b09c0b85cc996567d6c8b9d28408263","role":"observed_occurrence","archived_location":null}
  - {"ref":"summary:msg-20260719T181730501217","sha256":"d5bbdcf3f1293c5e10c27553ac484a2abc24a8200a7e461f6b53d5713779b7cb","role":"observed_occurrence","archived_location":null} (mis-declared false start; operator close required)
  - {"ref":"summary:msg-20260719T183202666915","sha256":"db096c0a81f898944129fe35a04a0bc04bb1d8e6f8c3141d8f60e91b706bfa35","role":"observed_occurrence","archived_location":null} (complete refused verification_incomplete; operator close under recorded exception)
- impact vector: security_impact 0; authority_integrity_impact 3; durable_record_integrity_impact 2; reliability_impact 2; operator_time_impact 2; execution_delay_impact 2; token_api_compute_impact 1
- tier-decision inputs: risk_activity "historical"; exposure_class "none"; mutation_class "none"; record_integrity_class "none"; ownership_conflict false; failure_class "lifecycle_failure" (matches the Tier 1 predicate)
- subsystem: work_item_lifecycle; estimated_effort: 5; blast_radius: multiple_subsystems
- root_cause_confidence: "0.90"; confidence_basis: "mechanism absence is directly inspectable in the CLI surface"
- authority_seeded: true (observed-occurrence evidence already attached; planning-approval eligible once the operator so disposes)
- status: TRIAGED; operator_disposition: none (operator review pending)

### ALF-0002 - Repeated deliberation without new evidence
- priority_tier: 2, escalating only when recurrence blocks execution (per authority)
- problem_statement: The same entities and candidate decisions recur across a run with no new tool evidence, unchanged authority, unchanged durable state, and unchanged constraints, while tokens and elapsed time increase without execution progress.
- detection_conditions (conservative, all required): same entities and candidate decisions recur; no new tool evidence has appeared; authority has not changed; durable state has not changed; constraints have not changed; tokens and elapsed time increase without execution progress.
- response_constraint: advisory only. It must not interrupt legitimate multi-step analysis, destructive-action caution, changing evidence, authority investigation, or uncertain ownership or concurrency investigation.
- root_cause: no run-level detector correlates decision recurrence with evidence staleness; the executor cannot see its own repetition.
- permanent_resolution: a recurrence-driven advisory detector over run observations (section 11) that reports suspected repetition with the exact recurring entities and the evidence-staleness basis, in the Run Improvement Delta and operator review only.
- objective_acceptance_criteria: detector reports only when every detection condition holds; zero interruptions of the protected analysis classes; advisory output carries the recurring decision set and staleness evidence; false-positive review path through operator disposition.
- required_regression_tests: fixture runs with genuine progress (no report); repeated decisions with new evidence (no report); repeated decisions without new evidence (report); protected classes never interrupted.
- evidence_references (exact object form): {"ref":"message:msg-20260719T181306094084","sha256":"de155644f62e46370e6fbcfc4dd539d5dfcd2c9be6d71b7186f00f062d1fdb63","role":"defining_authority","archived_location":null} (run-level exemplars accrue at capture time from invocation-log and council-round observations as observed_occurrence entries; planning approval requires at least one such entry per section 16)
- impact vector: security_impact 0; authority_integrity_impact 0; durable_record_integrity_impact 0; reliability_impact 1; operator_time_impact 2; execution_delay_impact 2; token_api_compute_impact 2
- tier-decision inputs: risk_activity "historical"; exposure_class "none"; mutation_class "none"; record_integrity_class "none"; ownership_conflict false; failure_class "excess_deliberation" (matches the Tier 2 predicate)
- subsystem: executor_process; estimated_effort: 3; blast_radius: single_run
- root_cause_confidence: "0.70"; confidence_basis: "detector absence is structural; recurrence dynamics not yet measured"
- authority_seeded: true (visibly marked until observed-occurrence evidence attaches)
- status: TRIAGED; operator_disposition: none

### ALF-0003 - Destructive cleanup safety is model-driven instead of tool-enforced
- priority_tier: 1, escalating when actual data-loss risk exists (per authority)
- problem_statement: Safety decisions around destructive cleanup (branch and artifact removal, forced deletion) depend on executor judgment instead of a mechanical preflight.
- root_cause: no cleanup preflight tool exists; the guard rails live in prompts and conventions rather than refusal-capable tooling.
- permanent_resolution: a cleanup preflight covering: branch ancestry; merged pull-request state; tracked-file cleanliness; untracked artifacts; ignored artifacts; live process use; active work-item dependencies; checkpoint dependencies; normal-removal eligibility; force justification. Forced removal must be refused unless normal removal failed for a recorded reason.
- objective_acceptance_criteria: destructive operations are refused unless the preflight passes or records a justified force path; refusal messages state the exact failed check; forced removal without a recorded normal-removal failure is impossible through the tooling; preflight results are durable observations.
- required_regression_tests: each preflight dimension with pass and fail fixtures; force-without-recorded-failure refusal; live-process and active-dependency refusal; checkpoint-dependency refusal.
- evidence_references (exact object form): {"ref":"message:msg-20260719T181306094084","sha256":"de155644f62e46370e6fbcfc4dd539d5dfcd2c9be6d71b7186f00f062d1fdb63","role":"defining_authority","archived_location":null} (exemplars accrue from cleanup-bearing runs at capture time as observed_occurrence entries; planning approval requires at least one such entry per section 16)
- impact vector: security_impact 2; authority_integrity_impact 1; durable_record_integrity_impact 3; reliability_impact 2; operator_time_impact 1; execution_delay_impact 1; token_api_compute_impact 0
- tier-decision inputs: risk_activity "historical" (the defect is structural; no active or plausible data-loss event is in evidence at seed time); exposure_class "none"; mutation_class "destructive_action_risk"; record_integrity_class "corruption_risk"; ownership_conflict false; failure_class "durable_record_integrity". Tier-policy-v1 evaluation: the Tier 0 predicate does not match (risk_activity is historical), the Tier 1 predicate matches on failure_class - deterministic Tier 1, exactly the authority's seed tier. Escalation rule: an observed occurrence carrying an active or plausible data-loss event updates risk_activity, and re-evaluation then yields Tier 0 mechanically (the authority's escalating-on-actual-risk requirement).
- subsystem: cli; estimated_effort: 5; blast_radius: single_subsystem
- root_cause_confidence: "0.80"; confidence_basis: "preflight-tool absence is structural; risk surface partially measured"
- authority_seeded: true (visibly marked until observed-occurrence evidence attaches)
- status: TRIAGED; operator_disposition: none

## 22. Review questions

1. Is the three-layer separation (immutable observations, versioned findings, operator-promoted improvements) structurally sound as specified - in particular the append-only revision log with hash chain and the head-equals-rebuild rule?
2. Do the observation and finding schemas cover the mandatory field set and the Phase 1 capabilities without hidden implementation authority?
3. Is the priority model (hard tier first, transparent formula, versioned model file, effort never reducing priority, regression escalation) correct and reproducible as specified?
4. Are the deduplication, lineage, recurrence, and regression rules safe - especially the operator-confirmed merges and the silent-merge prohibition for protected classes?
5. Is the operator-control boundary complete - can any specified ALF path create authority, create governed work, modify code, alter dispositions, or bypass the promotion gate?
6. Are the three initial findings faithfully evidence-bound, and is the section 10 lineage treatment of the original fourth authority finding (resolved-or-disproved headline evidence, residual preserved as TRIAGED candidate) the correct non-rewriting disposition?
7. Any HIGH or CRITICAL planning defect that must block the plan gate?

End of packet.
