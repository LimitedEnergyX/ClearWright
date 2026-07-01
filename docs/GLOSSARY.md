# ClearWright&trade;: Glossary

Plain-language definitions of the core ClearWright Protocol terms. For the full authority
numbering and bands, see [AUTHORITY_MODEL.md](AUTHORITY_MODEL.md); for protocol
mechanics, see [CLEARWRIGHT_PROTOCOL.md](CLEARWRIGHT_PROTOCOL.md). Where this glossary and the
authority model differ, the authority model is authoritative.

---

**RTA (Request to Act).** A coordination message in which an actor declares intent
to occupy a workflow channel, resource, stage, priority lane, or authority
boundary. An RTA is a request, not permission.

**CTA (Clear to Act).** A coordination message granting bounded clearance to
proceed within a defined scope, time, risk level, clearance class, priority class,
and authority class. A CTA is a bounded lease, not a blank check. It may expire,
be revoked, be superseded, or be narrowed, and it is never silently broadened.

**DTA (Denied to Act).** A coordination message that denies, defers, blocks, or
escalates a request because the channel, resource, authority, dependency, policy
boundary, priority lane, or next stage is not clear. DTA is a successful safety
outcome, not a failure, and it never flows to FAILED.

**Channel.** Whatever an actor must occupy or clear before acting: a file,
branch, packet, queue stage, review lane, deployment lane, operator attention
lane, external system, execution environment, compute cluster, GPU queue, power or
cooling capacity window, data access boundary, or model evaluation gate.

**channel_state.** The readiness of a channel. One of `CLEAR`, `BUSY`, `BLOCKED`,
`STALE`, `ESCALATED`, `FROZEN`, `DEGRADED`, or `RESERVED`.

**Clearance lease.** The bounded grant represented by a CTA: scoped by action,
clearance class, clearance level, priority, channel, resource, issuer, and expiry.
The lease is the unit of authorized action.

**actor_class.** The kind of actor, independent of how much authority it holds.
One of `OPERATOR`, `ORCHESTRATOR`, `POLICY_ENGINE`, `REVIEWER`, `WORKER`,
`OBSERVER`, or `SYSTEM`.

**authority_level.** A four-digit number expressing how much decision authority an
actor holds. Sorts ascending: `0000` is highest, `9999` is lowest. A lower number
means greater authority.

**authority_band.** The numeric range an actor's `authority_level` falls in, which
maps to a functional command range (for example `0000-0099` command, `0100-0199`
deployment, `0300-0399` code).

**authority_domain.** The functional lane an actor controls, for example
`COMMAND`, `DEPLOYMENT`, `SECURITY`, `CODE`, `RECORDS`, `DATA`, `MODEL_RISK`,
`INFRA`, `ENERGY`, `PRODUCT`, `LEGAL`, `FINOPS`, `WORKFLOW`, `AUDIT`.

**Command tier.** The `0000-0099` authority band (general officer tier). Actors
here hold global go, stop, freeze, revoke, supersede, and override authority across
all lanes unless restricted by root policy. Command-tier authority controls the
system.

**Domain authority.** Authority over one functional lane, below the command tier.
A domain authority controls decisions in its lane and only its lane. Domain
authority controls its lane; cross-domain conflict escalates.

**delegation_scope.** The bounded grant under which a non-root actor acts, which
repo, branch, project, or environment. An actor cannot act outside its delegation
scope and cannot grant authority it does not itself hold.

**clearance_class.** The category of action a CTA permits, for example
`READ_ONLY`, `DOCS_ONLY`, `BRANCH_CODE`, `QUEUE_MOVE`, `EXECUTION_CANDIDATE`,
`MERGE_CANDIDATE`, `DEPLOYMENT_CANDIDATE`, or `HUMAN_REQUIRED`.

**clearance_level.** A numeric expression of how consequential a cleared action is.
Higher numbers may mean more consequential actions (for example `CTA-L2000` docs vs
`CTA-L8000` deploy candidate). Note this runs opposite to `authority_level`.

**priority_class.** The scheduling priority of a request: `LOW`, `NORMAL`,
`HIGH`, `URGENT`, or `EMERGENCY`. Priority affects ordering and preemption, not
decision rights.

**priority_level.** A numeric scheduling priority. The organization defines whether
higher means more urgent or lower means more urgent; it must be documented per
deployment.

**Backpressure.** The condition of a channel being busy, stale, blocked, degraded,
frozen, or escalated. The protocol relieves backpressure by denying, deferring,
backing off, retrying, or escalating rather than letting more agents pile on.

**Escalation.** Routing a decision to a higher authority when it cannot be resolved
at the current level. Escalation moves upward only as far as necessary, not as high
as possible.

**Operator override.** The operator's standing ability to override any agent,
arbiter, or policy result at any time. The operator remains highest authority and
final override. Overrides are logged and supersede, never erase, prior decisions.

**Delegated authority.** Authority granted by a higher authority within a bounded
scope and optional time limit. All delegation chains terminate at `OPERATOR-0000`,
the root authority.

**Lowest sufficient authority.** The principle that a decision is resolved at the
lowest authority level adequate for the risk, scope, clearance class, and channel
involved, not routed to the highest available authority by default.

**Superseding decision.** A new decision that replaces a prior active decision
without deleting it. The new decision references the one it supersedes, the active
decision stays unambiguous, and the audit trail records who changed what, when,
why, and under what authority.

**Engineering control feedback loop.** A control-system view over mechanisms the
ClearWright Protocol already defines: sense state, request clearance, act under a bounded
lease, verify, correct, complete or escalate, record evidence, and improve future
control. It is an explanatory lens, not new machinery or new packet states, and it
is distinct from the multi-round consensus loop. See
[ENGINEERING_CONTROL_LOOPS.md](ENGINEERING_CONTROL_LOOPS.md).

**Clearance control loop.** The loop `RTA -> CTA / DTA / RFI -> action or wait`.
It prevents an agent from starting work when the channel, resource, authority, or
next stage is not clear.

**Review control loop.** The loop `draft -> review -> challenge -> revise ->
validate`. It improves work quality through challenge, refinement, and
verification. It overlaps with the consensus loop but is framed as engineering
control: output is checked, corrected, accepted, or sent back.

**Verification control loop.** The loop `claim -> execute -> test -> validate ->
accept or reject`. It ensures work is not accepted merely because an agent
completed it.

**Escalation control loop.** The loop `peer -> reviewer -> orchestrator -> domain
authority -> command tier`. It preserves lowest sufficient authority, climbing
only as far as necessary.

**Backpressure control loop.** The loop `measure channel load -> DTA / defer /
retry / backoff -> reduce wasted work -> reopen channel`. It keeps agents from
piling work onto a busy, blocked, stale, frozen, or degraded channel. Defer is a
DTA disposition or retry instruction, not a packet status.

**Audit improvement loop.** The loop `decision -> result -> metric -> lesson ->
policy update`. It turns completed and blocked work into evidence for better
future control. The lesson updates policy, thresholds, and routing, never
authority.

**Safety control loop.** The loop `detect risk -> DTA or FREEZE -> review ->
correct -> resume or escalate`. It treats DTA, FREEZE, and escalation as
successful safety controls, not failures. FREEZE is a command-tier verb and
`FROZEN` is a channel state; neither is a packet status.
