# Decision register

Public-safe record of ClearWright's load-bearing decisions. Each entry:
decision, rationale, consequences, status, related milestone. Private
implementation detail (hosts, endpoints, operator identities, credential
and provider-account arrangements, schedules) lives only in a private
operations record maintained by the operator outside the public repository.

Statuses: **standing** (in force), **planned** (adopted for future work).

---

## D-01. Human operator authority remains primary

- **Decision:** the operator is the highest authority and final override
  for every governed action; ClearWright never creates its own authority.
- **Rationale:** governance software that can mint authority for itself is
  a circular trust failure; bounded, ordered authority is the product's
  core promise.
- **Consequences:** every escalation terminates at a durable operator
  decision; automation reduces friction, never removes the operator.
- **Status:** standing. **Milestone:** all.

## D-02. Reviewer agreement never creates authority

- **Decision:** council consensus (GPT + Codex agreement) supports
  clearance but grants nothing; only operator-defined scope and recorded
  delegation authorize work.
- **Rationale:** reviewers judge quality, not permission; conflating the
  two would let model output substitute for human intent.
- **Consequences:** agreement is a necessary-but-never-sufficient condition
  for governed completion; authority records are separate, durable
  artifacts.
- **Status:** standing. **Milestone:** all.

## D-03. Stable governs Candidate

- **Decision:** in Project Leapfrog, one trusted Stable instance governs
  the development, verification, and promotion of one isolated Candidate
  instance.
- **Rationale:** a system that improves itself needs a trusted fixed point;
  the running Stable instance is that fixed point.
- **Consequences:** separate runtime roots, queues, configuration, and
  secret stores; an explicit permitted-channel matrix; promotion is
  Stable-driven only.
- **Status:** planned. **Milestone:** Project Leapfrog.

## D-04. Candidate cannot approve itself

- **Decision:** nothing writable by Candidate can create, satisfy, or
  influence a promotion decision; promotion authority is a Stable-side
  durable operator record.
- **Rationale:** self-approval is the precise failure mode a
  self-improving governance system must exclude by construction.
- **Consequences:** adversarial write-attempt and forged-record tests
  (T1/T2) are release gates; a failed denial is a released-blocking defect.
- **Status:** planned. **Milestone:** Project Leapfrog.

## D-05. Public and private planning remain separate

- **Decision:** public surfaces (repository, GitHub metadata, CI logs,
  screenshots, exports) never carry private machine names, endpoints,
  operator identities, credential or provider-account arrangements,
  private project names, local paths, or unresolvable private identifiers;
  operational detail lives in a private operations record outside the
  repository.
- **Rationale:** the repository is public; operational security and
  personal privacy cannot depend on nobody looking.
- **Consequences:** a two-stage pre-publication privacy review with the
  operator as owner; remediation procedure for accidental disclosure;
  attestation standard for operator-attested claims.
- **Status:** standing. **Milestone:** all.

## D-06. VPN infrastructure is external

- **Decision:** ClearWright may operate over an administrator-selected
  private network but does not provide or manage VPN infrastructure.
- **Rationale:** network plumbing is a solved, specialized problem;
  bundling it would expand the attack surface and the support burden
  without advancing governance.
- **Consequences:** deployment documentation states the boundary; no VPN
  code is ever in scope.
- **Status:** standing. **Milestone:** all.

## D-07. Claude remains the implementation agent

- **Decision:** operators use their own Claude Desktop (or Claude Code)
  accounts for implementation work; ClearWright does not require an
  Anthropic API for this workflow.
- **Rationale:** the operator's existing assistant relationship and
  billing stay theirs; ClearWright stays vendor-light on the worker side.
- **Consequences:** the worker bridge and "Use CW" loop drive governance
  through local CLI/HTTP; no server-side Anthropic credential exists.
- **Status:** standing. **Milestone:** all.

## D-08. ClearWright remains the governance layer

- **Decision:** ClearWright is the authorization, consensus, and audit
  layer - not a tool-access framework, agent messaging bus, or workflow
  orchestrator.
- **Rationale:** a sharp scope keeps the trust surface auditable.
- **Consequences:** integrations sit above or beside; scope creep into
  orchestration is rejected at planning time.
- **Status:** standing. **Milestone:** all.

## D-09. Installer follows identity and connector stabilization

- **Decision:** installer and deployment work begins only after the
  Leapfrog topology, identity model, connector interface, and
  data-migration rules stabilize.
- **Rationale:** an installer that moves runtime data before the data
  model stabilizes risks unrecoverable damage to durable governance
  records.
- **Consequences:** installer work is explicitly deferred and gated.
- **Status:** standing. **Milestone:** Installer and Deployment.

## D-10. Completed claims require evidence

- **Decision:** every public "done" or capability claim carries evidence -
  repo-verifiable (merged PRs, tests, CI) or operator-attested under the
  attestation standard - and unsupported claims are downgraded or removed.
- **Rationale:** a governance product whose own claims outrun its evidence
  undermines its reason to exist.
- **Consequences:** claim-to-evidence verification before publication; the
  final verification council checks the claim map; roadmap reconciliation
  at every milestone boundary.
- **Status:** standing. **Milestone:** all.

## D-11. Per-workspace reviewer credential isolation

- **Decision:** each workspace uses its own isolated reviewer credentials;
  provider and account arrangements are private operational detail.
- **Rationale:** shared credentials would let one workspace's usage,
  costs, or compromise bleed into another's.
- **Consequences:** the multi-user phase carries per-workspace credential
  acceptance criteria; public documents stay provider-neutral about
  account structure.
- **Status:** planned. **Milestone:** Multi-user and SSO.

## D-12. Pre-Leapfrog Stabilization Gate

- **Decision:** Leapfrog implementation cannot start until message-scoped
  work-item identity, same-thread isolation, derived-queue completeness,
  gate idempotency, selected-task state isolation, server lifecycle
  evidence, and a safe manual launcher are verified complete on merged
  code.
- **Rationale:** live operation surfaced identity-collision, duplicate-
  gate, display-isolation, and lifecycle-evidence defects; building a
  self-improving two-instance topology on top of unstable identity and
  gating would compound every defect.
- **Consequences:** a dedicated governed stabilization work item precedes
  Leapfrog; its completion is a hard release gate (PROJECT_PLAN.md
  section 10).
- **Status:** standing. **Milestone:** Pre-Leapfrog Stabilization Gate.

## D-13. Deterministic agreement is not weakened for speed

- **Decision:** no one-round agreement fast path; efficiency comes from
  packet discipline, scoped review, and metrics - never from loosening the
  deterministic agreement rule or fail-closed completion.
- **Rationale:** the agreement rule is the product's integrity floor;
  trading it for latency would be self-defeating.
- **Consequences:** Phase 2 optimizes cost and latency within the
  existing rule; capped councils still escalate to the operator.
- **Status:** standing. **Milestone:** Council Efficiency and
  Observability.
