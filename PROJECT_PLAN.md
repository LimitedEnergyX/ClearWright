# ClearWright Project Plan

This is the strategic, public-safe source of truth for ClearWright's
development. [ROADMAP.md](ROADMAP.md) is the concise public summary; the
GitHub Project and milestones mirror this plan; GitHub issues are the
execution units. Private operational detail (hosts, endpoints, operator
identities, credentials, schedules) lives in a private operations record
maintained by the operator outside the public repository and never appears
here or on any public surface.

Status language used throughout: **early local alpha**. Nothing in this plan
is a production-readiness, SaaS, certification, or compliance claim.

## 1. Product definition

ClearWright is an operator-controlled authorization, consensus, and audit
layer for multi-AI-agent work. Agents request clearance before acting, real
independent reviewers (GPT and Codex) review plans and results through a
deterministic Review Council, fail-closed gates stop governed work when a
council escalates, and every decision leaves a durable record. Consensus
supports clearance; it never creates authority. The operator remains the
highest authority and final override.

ClearWright does not create its own authority. Work proceeds only within
operator-defined scope and recorded delegation.

## 2. Strategic mission

Turn the proven single-operator local system into a safely self-improving,
efficient, multi-operator platform that can be installed and piloted by
others - without ever weakening the fail-closed governance that makes the
system trustworthy.

## 3. Current verified state

Evidence labels used for every claim in this plan:

- **[repo-verifiable]** - backed by merged pull requests, committed tests,
  CI, and committed documentation that anyone can inspect in this
  repository.
- **[operator-attested]** - demonstrated on the operator's live local
  system, backed by durable local records (dated), summarized here in
  sanitized form. Not independently verifiable from the public repository.

Capability matrix:

| Capability | State | Evidence |
|---|---|---|
| Clearance protocol (RTA/CTA/DTA/RFI), 4-lane queue, packet schema, validator, lifecycle and decision tools | local alpha | [repo-verifiable] PRs #1-#10; `schema/`, `tools/`, tests |
| Local operator console: durable queue, messages/threads, derived work items, worker bridge, operator mode | local alpha | [repo-verifiable] PRs #9-#24, #35-#37 |
| Real GPT + Codex Review Council with structured verdicts and a deterministic agreement rule | local alpha | [repo-verifiable] PRs #25, #28, #30; [operator-attested] real multi-round governed missions to DONE (July 2026) |
| "Use CW" governed loop (start / plan council / progress / incident / verify / complete, stable exit codes) | local alpha | [repo-verifiable] PRs #26-#27, #30-#34; [operator-attested] this repository's own development is governed by it |
| Fail-closed plan gates (councils that escalate create durable gates; governed interfaces refuse until durable post-gate operator authority) | local alpha | [repo-verifiable] PR #35, `tests/test_plan_gate.py`; [operator-attested] real escalations resolved by durable operator authority (July 2026) |
| Fail-closed verification before DONE (completion refuses without verify-council agreement) | local alpha | [repo-verifiable] PRs #31, #33-#35; [operator-attested] an honest live completion refusal (July 2026) |
| Review profiles (code / editorial) and replacement packet discipline | local alpha | [repo-verifiable] PR #35, `tests/test_council_profiles.py` |
| Message payload integrity (canonical content, size caps, thread-scoped idempotency, HTTP framing) | local alpha | [repo-verifiable] PR #35, `tests/test_message_integrity.py` |
| Durable archive layer: hash-bound operator approval, journaled crash-safe moves, forward-only recovery, archive-aware reads, runbook | local alpha | [repo-verifiable] PRs #35, #38, `tests/test_archive.py`; [operator-attested] one real archive execution including a real mid-operation interruption recovered forward-only with full integrity (July 2026) |
| Artifact and evidence handling (pinning, hashes, capability-aware reviewer delivery) | local alpha | [repo-verifiable] PRs #31-#32 |
| Task-centered operator site (three-region desktop, six-phase stepper, unified History ledger, archived-record labeling) | local alpha | [repo-verifiable] PRs #35-#37, `tests/test_site_ia_corrective.py` |
| Canonical summaries, durable operator authority records, append-only audit trail | local alpha | [repo-verifiable] PRs #25-#38 |
| Stable/Candidate self-improvement topology (Project Leapfrog) | planned | no code exists |
| Multi-user identity, SSO, workspace isolation | planned | no code exists |
| Project registry and scoped connector | planned | no code exists |
| Installer and deployment | planned (deferred) | no code exists |
| VPN infrastructure | non-goal | ClearWright may operate over an administrator-selected private network; it does not provide or manage VPN infrastructure |

Known defects under repair (work in progress, own governed work item):
message-scoped work-item identity in shared threads, duplicate gate creation
on capped-council bookkeeping, selected-task display isolation, server
lifecycle logging, and a safe manual launcher. These are tracked as the
**Pre-Leapfrog Stabilization Gate** (section 10).

## 4. Completed foundation

Phase 0 (Proven Local Council Alpha) is substantially complete: the full
clearance protocol, the operator console, real two-reviewer councils with
deterministic agreement, fail-closed gates and verification, payload
integrity, artifact handling, the archive layer, and the governed
development loop - all delivered through 38 merged pull requests with a
green stdlib test suite and a CI naming/confidentiality gate, and exercised
end-to-end by governing this repository's own development.

## 5. Current active phase

**Planning alignment** (this document set) plus the **Pre-Leapfrog
Stabilization Gate** (section 10). Project Leapfrog is the next active
implementation milestone and begins only after the stabilization gate is
verified complete.

## 6. Planned phases

- **Phase 0 - Proven Local Council Alpha.** Substantially complete
  (section 4).
- **Phase 1 - Project Leapfrog.** One trusted Stable instance governs
  development and verification of one isolated Candidate instance
  (section 9).
- **Phase 2 - Council Efficiency and Observability.** Replacement packet
  enforcement, scoped later-round review, deterministic parallel dispatch
  where safe, packet/token/cost/latency/finding-lifecycle/interruption
  metrics. No one-round agreement fast path.
- **Phase 3 - Multi-user Identity, SSO, and Workspace Isolation.**
  Authenticated operators, administrator and standard roles, server-side
  workspace isolation, MFA, session management, connector tokens and
  revocation, device registration, administrator audit access,
  per-workspace reviewer credential isolation, concurrent isolated work
  items. Operators continue using their own Claude Desktop accounts;
  ClearWright does not require an Anthropic API for this workflow.
- **Phase 4 - Project Registry and Scoped Connector.** Registry fields:
  workspace, project, repository, permitted paths, operators, reviewer
  profile, risk level, reviewer credentials (provider-neutral), budget,
  deployment target, active work claims, concurrency and collision rules.
- **Phase 5 - Installer and Deployment.** Begins only after the Leapfrog
  topology, identity model, connector interface, and data-migration rules
  stabilize.
- **Phase 6 - Reliability, Recovery, and Policy Packs.** Reliability beyond
  the promotion path; never a substitute for Phase 1's promotion-safety
  gates.
- **Phase 7 - Trusted Pilot.**
- **Phase 8 - Public Alpha.**

## 7. Dependency map and parallel lanes

```text
planning alignment
  -> pre-Leapfrog stabilization gate
    -> Project Leapfrog
      -> first successful promotion
        -> council efficiency and observability
          -> multi-user identity / SSO / workspace isolation
            -> project registry and scoped connector
              -> installer and deployment
                -> reliability, recovery, policy packs
                  -> trusted pilot
                    -> public alpha
```

Parallel after the Leapfrog foundation (research only; implementation never
outruns the Stable/Candidate architecture):

```text
Leapfrog foundation
├── council efficiency metrics (definitions and baselines)
├── identity architecture research
└── installer requirements research
```

## 8. Acceptance criteria per phase

**Project Leapfrog** - the six release-gate tests of section 9 pass with
retained evidence, plus: isolated runtime roots; Candidate cannot write
Stable state; Stable governs Candidate changes; migration dry run passes;
promotion requires operator authority; rollback works; a first complete
promotion is demonstrated end-to-end.

**Council Efficiency and Observability** - packet growth is bounded;
later-round review is scoped; reviewer contribution is measurable; cost and
latency are reported; clean technical councils normally converge in two or
three rounds; fail-closed completion is not weakened.

**Multi-user and SSO** - two authenticated operators; two devices; isolated
workspaces; administrator cross-workspace visibility; a non-administrator
cannot discover another workspace; per-workspace reviewer credential
isolation; concurrent independent work items.

**Project Registry and Connector** - registry entries enforce permitted
paths and collision rules on real concurrent work.

**Installer and Deployment** - clean install, upgrade preserving runtime
data, and uninstall on a machine that never ran ClearWright.

**Reliability and Policy Packs** - recovery drills pass from injected
failures; policy packs express the shipped gate behaviors declaratively.

**Trusted Pilot** - an external pilot operator completes a governed work
item end-to-end without maintainer intervention.

**Public Alpha** - pilot feedback incorporated; public documentation
matches shipped behavior with evidence.

## 9. Project Leapfrog (public-safe definition)

One trusted **Stable** ClearWright instance governs development and
verification of one isolated **Candidate** instance.

**Threat model:** the adversary is a Candidate process with full control of
its own runtime root, queue, configuration, and secrets, able to submit
requests through the permitted channels, holding no Stable credentials. The
human operator and the operator's own account remain trusted (the accepted
residual limitation of the local alpha, carried forward unchanged - it
means trust in the operator's account, never permission for Stable and
Candidate to share a runtime principal).

**Structural requirements:** Stable and Candidate run as distinct OS-level
service principals with separate runtime roots, queues, configuration, and
secret stores. An explicit, finite permitted-channel matrix (versioned in
the Leapfrog gate specification) defines the only cross-instance
interfaces: Stable-initiated read-only inspection of Candidate, and a
Stable-driven promotion channel. No shared writable state. Candidate cannot
approve itself.

**Release gates - six finite pass/fail tests, all evidence retained:**

1. **T1 Write-attempt matrix.** Candidate attempts writes to each Stable
   surface class (queue lanes, communications, council records, gate
   records, authority store, configuration, secret store, promotion
   manifest) via direct filesystem paths, each permitted channel, and
   crafted request payloads. Pass: zero writes occur and every attempt is
   durably recorded on the Stable side. A missing audit record fails the
   test.
2. **T2 Authority independence.** Promotion proceeds only when a
   Stable-side durable operator authority record exists in storage
   Candidate cannot reach under T1. Pass: promotion with the record
   succeeds; without it fails; with a Candidate-forged record fails.
3. **T3 Migration dry-run gate.** The schema-compatibility check runs on a
   copy of the Candidate dataset and reports zero errors; promotion is
   blocked on failure.
4. **T4 Cut-point invariants.** At three kill points (pre-manifest,
   post-manifest/pre-activation, mid-activation) the process is killed and
   recovery run. Pass: after recovery, either the prior Stable serves or
   the promoted version serves - never a mixed state - and every manifest
   artifact hash re-verifies.
5. **T5 Rollback.** Triggers: operator command, or a failed
   post-activation acceptance check. Pass: the retained prior Stable
   serves again and passes its acceptance checks; the prior Stable is
   retained until the operator explicitly releases it.
6. **T6 First real promotion.** One end-to-end promotion of a real
   Candidate build: manifest with full artifact hashes verified before
   activation, durable operator promotion authority, activation,
   post-activation acceptance - with the complete evidence set retained.

## 10. Pre-Leapfrog Stabilization Gate (mandatory)

Leapfrog implementation cannot start until each of the following is
verified complete on merged code:

1. **Message-scoped work-item identity** - every actionable message has
   its own work-item identity; claims, councils, gates, summaries,
   verification, authority, and closure bind to it.
2. **Same-thread work-item isolation** - multiple work items sharing one
   conversation thread remain fully independent.
3. **Derived queue completeness** - no governed work item disappears from
   derived views; integrity defects surface as visible warnings.
4. **Gate idempotency** - one durable gate per unique escalation event;
   bookkeeping never creates duplicates; authority linkage is durable.
5. **Selected-task state isolation** - every operator-facing surface for a
   selected task binds to the same work-item identity; cross-item activity
   never relabels or animates another task.
6. **Server lifecycle evidence** - durable start/stop/failure records with
   secret redaction and rotation, surfaced through the status API.
7. **Safe manual startup and clean-stop procedure** - a repository-contained
   launcher with duplicate-process and port protection; no persistence
   registration.

## 11. Release gates

- Every phase completes only through its acceptance criteria with retained
  evidence and a final two-reviewer verification council reaching
  agreement.
- Promotion-path recovery, integrity, and rollback guarantees are
  **Phase 1** release gates (they are never deferred to Phase 6).
- Completion is fail-closed: work items are marked done only after
  verification-council agreement, and operator-only closure requires a
  durable, post-outcome operator authority record.

## 12. Risk register

| Risk | Likelihood | Impact | Mitigation | Detection | Owner | Release gate |
|---|---|---|---|---|---|---|
| Circular self-governance (Candidate influences its own approval) | medium | critical | Stable-side authority storage unreachable by Candidate; T2 | T1/T2 adversarial tests | operator | Leapfrog |
| Candidate self-promotion | medium | critical | promotion channel driven by Stable only; operator authority record required | T2 forged-record test | operator | Leapfrog |
| Queue or schema migration failure | medium | high | migration dry-run gate; T3 | dry-run reports; T4 recovery drills | maintainer | Leapfrog |
| Rollback failure | low | critical | prior-Stable retention until operator release; T5 | rollback exercise evidence | operator | Leapfrog |
| Workspace data leakage | medium | critical | server-side isolation; non-admin discovery tests | multi-user acceptance tests | maintainer | Multi-user |
| Forged operator identity | low | critical | SSO + MFA + device registration; durable authority records | authority-record audits | operator | Multi-user |
| Secrets appearing in public files | medium | critical | public/private boundary (section 16); two-stage privacy review; naming gate in CI | pre-publication review; CI | operator | every phase |
| Stale verification against changed code | medium | high | verification councils run against merged, identified revisions | revision recorded in council packets | maintainer | every phase |
| Council cost escalation | medium | medium | replacement packets, scoped later rounds, metrics | Phase 2 cost/latency reporting | maintainer | Efficiency |
| Reviewer disagreement treadmill | medium | medium | bounded 2-5 rounds; capped councils escalate to the operator | round counts in council records | operator | every phase |
| Archive or audit corruption | low | high | journaled crash-safe moves; hash-bound approvals; forward-only recovery | integrity re-verification | maintainer | shipped (PR #35/#38) |
| Installer upgrades damaging runtime data | medium | critical | installer deferred until migration rules stabilize | upgrade tests on copies | maintainer | Installer |
| Public claims outrunning proven capability | medium | high | evidence labels; claim-to-evidence verification before publication | final council checks the claim map | operator | every phase |

## 13. Success metrics

Definitions (recorded today in durable council records, invocation logs,
and canonical-summary usage blocks; a reporting surface lands in Phase 2):
plan rounds per work item; verification rounds per work item; reviewer
token use; reviewer latency; estimated council cost; findings by reviewer;
accepted vs rejected vs repeated findings; validation failures; transport
retries; operator interruptions; completion refusals; rollback count; time
from request to DONE; promotion duration; archive integrity failures.

Metrics policy: baselines are computed only from retained durable records
and may be incomplete before instrumentation - no historical measurements
are fabricated. Public reporting is aggregated and redacted: no operator
identities, workspace names, account-linked token or cost records, or
private identifiers. Raw records stay local under operator-controlled
access.

## 14. Architectural decisions

The decision register lives in [docs/DECISIONS.md](docs/DECISIONS.md).
Load-bearing summary: human operator authority remains primary; reviewer
agreement never creates authority; Stable governs Candidate and Candidate
cannot approve itself; public and private planning remain separate; VPN
infrastructure is external; Claude remains the implementation agent through
operators' own Claude Desktop accounts (no Anthropic API requirement);
ClearWright remains the governance layer; the installer follows identity
and connector stabilization; completed claims require evidence;
per-workspace reviewer credential isolation (provider and account
arrangements stay private).

## 15. Explicitly deferred work

- Installer and deployment (until Leapfrog, identity, connector, and
  migration rules stabilize - rationale: an installer that moves runtime
  data before the data model stabilizes risks unrecoverable damage).
- One-round council fast path (deliberately not planned at this stage:
  convergence speed must come from packet discipline, not from weakening
  deterministic agreement).
- VPN infrastructure (permanent non-goal; administrator-selected private
  networks are outside ClearWright).
- Automated private-identifier scanning of public artifacts (manual
  two-stage review is the current control; automation would require a
  separately scoped design that avoids storing sensitive identifier lists
  in the repository).

## 16. Public/private information boundary

Public surfaces include every channel, not only documents: PROJECT_PLAN.md,
ROADMAP.md, README.md, docs/, GitHub milestones, the GitHub Project, issues,
PR titles and descriptions, commit messages, branch names, issue comments,
Project field values, labels, linked artifacts, screenshots, CI logs,
release notes, automation payloads, and any metrics or evidence exports.

Public surfaces must never contain: private machine names; internal service
endpoints or ports; operator personal names; real private operator
identifiers; private workspace names; API-key, credential, or
provider-account arrangements (including named provider projects); private
network details; private project names; local-only file paths (including
the location of the private operations record); private runtime or council
identifiers outsiders cannot resolve; raw council transcripts; reviewer
account identifiers; account-linked token or cost records; environment
variable or service names that reveal providers or accounts; branch names
derived from private project names; or attachments containing any of the
above.

Process: the operator owns pre-publication review. Stage 1 (pre-push)
reviews changed files, branch names, commit messages, and PR text - and the
expected output of tests, scripts, and automation, so nothing is pushed
whose generated output would print prohibited values. Stage 2 (post-push,
pre-merge) reviews actual CI logs and rendered surfaces as a detection and
containment backstop. Every post-merge GitHub write receives the same
review before submission and a re-read after creation. Remediation for
accidental disclosure: immediate removal commit, git-history exposure
assessment, operator notification, and a decision-register entry;
unscrubbable history escalates to the operator.

Operator-attested claims follow the attestation standard: the public form
states what was demonstrated, when, and the class of durable local record
backing it, with no private identifiers; local evidence references are kept
in the private operations record; records are retained at least until the
claim is superseded or removed.

## 17. Project-management conventions

- Every substantial GitHub issue contains: Problem, Outcome, Scope, Out of
  scope, Dependencies, Risks, Acceptance criteria, Evidence required, and
  its Milestone.
- Epics decompose into focused child issues only when the work is
  understood; no empty speculative placeholders.
- Statuses: Backlog, Ready, Planning, In progress, Verification, Operator
  required, Done, Deferred.

## 18. GitHub alignment and documentation maintenance rules

- PROJECT_PLAN.md is the strategic source of truth; ROADMAP.md is the
  public summary; the GitHub Project mirrors this plan and never becomes an
  independent or conflicting plan; issues are execution units.
- Every major merged capability updates this plan's status in the same
  change set; contradictions are corrected in the pull request that creates
  them.
- Every promotion or major release reconciles the roadmap and GitHub
  status.
- Completed claims require evidence (section 3 labels); unsupported claims
  are downgraded or removed, never left to age.
- Deferred items retain their rationale (section 15).
- Public documents never absorb private operational detail.
- Planning is reviewed at every milestone boundary.
- Before publication, every quantitative or artifact-specific claim is
  validated against the live repository state (revision, PR references
  resolve, named test files exist), and the claim-to-evidence map is
  checked by the final verification council.
