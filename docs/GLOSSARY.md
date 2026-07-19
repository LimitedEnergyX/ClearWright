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

**Clearance message vs execution state.** RTA, CTA, and DTA are clearance decisions
(messages), not execution states. Execution state describes how work progresses (for
example queued, claimed, in progress, verifying, done, failed, superseded). Today the
implementation records one combined packet status in which RTA/CTA/DTA also appear as
values; there is no separate execution-state enum. FAILED means execution broke after
valid clearance; a deliberate DTA is not a failure and never routes to FAILED. See
[CLEARWRIGHT_PROTOCOL.md](CLEARWRIGHT_PROTOCOL.md).

**Collision.** A conflict ClearWright coordinates against; it can DENY or RESERVE
work even with no direct file-level conflict. Six collision classes are [protocol
direction] (see [CLEARWRIGHT_PROTOCOL.md](CLEARWRIGHT_PROTOCOL.md) for the normative
definitions):

- Resource collision: two actors modify the same file, branch, queue record,
  service, schema, environment, or governed resource.
- Intent collision: two actors pursue incompatible or duplicative solutions to the
  same underlying problem.
- Dependency collision: one action changes assumptions, inputs, or prerequisites
  another action depends on.
- Redundancy collision: one action is expected to make another action unnecessary.
- Authority collision: two grants or actors permit incompatible actions.
- Evidence collision: an artifact is reviewed while another actor is changing,
  replacing, or invalidating it.

**Claim mode.** How an RTA proposes to occupy a channel. Five modes are [protocol
direction]: `SHARED_READ`, `EXCLUSIVE_WRITE`, `INTENT_EXCLUSIVE`, `DEPENDENCY_WAIT`,
`DEPLOYMENT_EXCLUSIVE`. `INTENT_EXCLUSIVE` protects against duplicate or incompatible
problem-solving even when agents would touch different files. See
[CLEARWRIGHT_PROTOCOL.md](CLEARWRIGHT_PROTOCOL.md).

**DTA disposition and reason code.** A DTA carries a disposition and a reason code,
both [protocol direction] (today a DTA records a free-text reason and archives to
`clearance_done`, never `clearance_failed`). The six dispositions are `DENY`,
`DEFER`, `BLOCK`, `ESCALATE`, `SUPERSEDED`, `NO_LONGER_NEEDED`. The thirteen reason
codes are `resource_collision`, `intent_collision`, `dependency_active`,
`duplicate_work`, `work_no_longer_needed`, `scope_conflict`, `insufficient_authority`,
`stale_context`, `stale_commit`, `policy_block`, `channel_busy`, `operator_required`,
`lease_unavailable`. Retry is allowed only under changed conditions (a new commit, a
refreshed dependency, a resolved conflict, renewed authority, or a freed resource).
See [CLEARWRIGHT_PROTOCOL.md](CLEARWRIGHT_PROTOCOL.md).

**Work invalidation.** A CTA lease can become invalid before work completes: when the
approved scope changes, a bound source artifact changes, the base commit changes
materially, a dependency becomes stale, a higher authority supersedes it, another
completed action makes the work unnecessary, or the lease expires. Agents should
explicitly release abandoned or completed leases rather than relying only on
expiration. This is [protocol direction]. See
[CLEARWRIGHT_PROTOCOL.md](CLEARWRIGHT_PROTOCOL.md).

**Channel.** Whatever an actor must occupy or clear before acting: a file,
branch, packet, queue stage, review lane, deployment lane, operator attention
lane, external system, execution environment, compute cluster, GPU queue, power or
cooling capacity window, data access boundary, or model evaluation gate.

**channel_state.** The readiness of a channel. Five values are [implemented] in
code: `CLEAR`, `BUSY`, `BLOCKED`, `STALE`, `ESCALATED`. `RESERVED`, `FROZEN`, and
`DEGRADED` are [protocol direction] (documented, not code-enforced today).

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

**clearance_class.** The category of action a CTA permits. Six classes are
[implemented] in code: `READ_ONLY`, `DOCS_ONLY`, `BRANCH_CODE`, `QUEUE_MOVE`,
`EXECUTION_CANDIDATE`, `HUMAN_REQUIRED`. `MERGE_CANDIDATE` and `DEPLOYMENT_CANDIDATE`
are [protocol direction] (documented, not code-enforced today).

**clearance_level (Clearance Consequence Level).** A numeric expression of how
consequential a cleared action is. Higher numbers mean greater consequence (for
example `CTA-L2000` docs vs `CTA-L8000` deploy candidate); this runs opposite to
`authority_level`. The field name `clearance_level` is unchanged; "Clearance
Consequence Level" is the canonical term for the concept. The numeric
`clearance_level` ladder is [protocol direction].

**priority_class.** The scheduling priority of a request. Four values are
[implemented] in code: `LOW`, `NORMAL`, `HIGH`, `URGENT`. `EMERGENCY` is
[protocol direction] (documented, not code-enforced today). Priority affects
ordering and preemption, not decision rights.

**priority_level.** A numeric scheduling priority. Canonically `0000` is the
highest urgency and `9999` the lowest; a lower `priority_level` means greater
urgency. It is unenforced scheduling metadata today (no scheduler acts on it), and
the numeric direction is [protocol direction].

**Backpressure.** The condition of a channel being busy, stale, blocked, degraded,
frozen, or escalated. The protocol relieves backpressure by denying, deferring,
backing off, retrying, or escalating rather than letting more agents pile on.

**Escalation.** Routing a decision to a higher authority when it cannot be resolved
at the current level. Escalation moves upward only as far as necessary, not as high
as possible.

**Operator override.** The operator's standing ability to override any agent,
arbiter, or policy result, exercised within the active root policy. The operator
remains highest authority and final override. A routine operator instruction cannot
silently bypass a control marked non-bypassable; changing such a control requires a
separate, explicit, governed root-policy decision. Overrides are logged and
supersede, never erase, prior decisions. See
[AUTHORITY_MODEL.md](AUTHORITY_MODEL.md), which owns this definition.

**Non-bypassable control.** A safeguard that a routine operator instruction cannot
silently bypass. The operator holds final decision authority within the active root
policy, but changing a control marked non-bypassable requires a separate, explicit,
governed root-policy decision (for example, the raw PII/PHI egress block enforced by
the Sensitive Data Egress Guard). See [AUTHORITY_MODEL.md](AUTHORITY_MODEL.md).

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

---

## Acronyms and abbreviations

Acronyms are expanded on first meaningful use in substantial documents; this section
is the canonical reference. Status labels follow the glossary convention:
[implemented] is enforced in current code, [protocol direction] is documented but not
code-enforced today, [planned] is adopted for future work. Some entries carry a
descriptive status tag instead — [product] for the platform itself, [in development]
for controls being built (optionally with a clarifying qualifier), [completed
governance work] for finished governance efforts. Industry-standard terms carry no
status.

**CW — ClearWright.** The product and the protocol it implements. [product] See
[CLEARWRIGHT_PROTOCOL.md](CLEARWRIGHT_PROTOCOL.md).

**RTA — Request to Act.** A coordination message declaring intent to occupy a
channel, resource, stage, or authority boundary; a request, not permission.
[implemented] See [CLEARWRIGHT_PROTOCOL.md](CLEARWRIGHT_PROTOCOL.md).

**CTA — Clear to Act.** A coordination message granting bounded clearance to proceed
within a defined scope, time, and class; a lease, not a blank check. [implemented]
See [CLEARWRIGHT_PROTOCOL.md](CLEARWRIGHT_PROTOCOL.md).

**DTA — Denied to Act.** A coordination message that denies, defers, blocks, or
escalates a request; a successful safety outcome, not a failure. [implemented] See
[CLEARWRIGHT_PROTOCOL.md](CLEARWRIGHT_PROTOCOL.md).

**RFI — Request for Information.** A coordination message asking for more information
before a clearance decision is made. [implemented] See
[CLEARWRIGHT_PROTOCOL.md](CLEARWRIGHT_PROTOCOL.md).

**SDEG — Sensitive Data Egress Guard.** A control that prevents raw sensitive data
(for example PII or PHI) from crossing a boundary; its raw PII/PHI egress block is a
non-bypassable control. [in development, SDEG feature branch unmerged]

**ITS — Internal Technical Standard.** A provenance classification for ClearWright's
own internal technical content, allowing governed self-review to proceed when every
input has verified technical ancestry. [in development]

**RRQH — Repository, Runtime, and Queue Hygiene.** Governance work keeping the
repository, runtime, and clearance queue clean and consistent. [completed governance
work]

**PII — Personally Identifiable Information.** Data that can identify a specific
person.

**PHI — Protected Health Information.** Health data protected under applicable
privacy regulation.

**TOCTOU — Time of Check to Time of Use.** A class of race condition where state
changes between when it is checked and when it is used.

**MCP — Model Context Protocol.** An open protocol for connecting AI models to tools
and data sources.

**API — Application Programming Interface.** A defined interface through which
software components interact.

**CLI — Command-Line Interface.** A text-command interface to a program.

**UI — User Interface.** The surface through which a person interacts with a system.

**UX — User Experience.** The overall experience of a person using a product.

**CI — Continuous Integration.** Automated building and testing of changes as they
are merged.

**PR — Pull Request.** A proposed change submitted for review and merge.

**PID — Process Identifier.** A number identifying a running process.

**ACL — Access Control List.** A list defining which actors may access a resource and
how.

**SSO — Single Sign-On.** Authentication letting one identity access multiple
systems.

**JSON — JavaScript Object Notation.** A lightweight text format for structured data.

**SHA-256 — Secure Hash Algorithm, 256-bit.** A cryptographic hash function producing
a 256-bit digest.

**RACI+S — Responsible, Accountable, Consulted, Informed, plus System-enforced.** A
responsibility-assignment model extended with a system-enforced dimension.

**WIP — Work in Progress.** Work that has started but is not yet complete.
