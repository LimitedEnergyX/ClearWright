# ClearWright Protocol

ClearWright Protocol is the underlying specification that ClearWright&trade; implements. It
defines how a request to act becomes an authorized, recorded, and auditable
outcome under an operator-defined policy framework.

ClearWright is the public product. ClearWright Protocol is the spec. See
[NAMING.md](NAMING.md) for the naming split. For the plain-
language rationale see PROTOCOL_RATIONALE.md; for the full
authority numbering see [AUTHORITY_MODEL.md](AUTHORITY_MODEL.md).

## Protocol in one paragraph

ClearWright Protocol is how an agent confirms that the workflow channel, the resource, the
authority boundary, the priority lane, and the next stage are clear before it
starts a large, expensive, risky, conflicting, or consequential action. An actor
issues a Request to Act (RTA); an authorized actor, arbiter, policy rule, or
operator responds with a Clear to Act (CTA), a bounded lease, or a Denied to
Act (DTA), a successful hold. Authority is delegated and ordered, decisions are
resolved at the lowest sufficient level, routine work proceeds without human
approval, risky or conflicting work escalates through the chain of command, and
every decision is recorded. The operator remains highest authority and holds final
override within the active root policy; a control marked non-bypassable is changed
only by a separate, explicit, governed root-policy decision, not by a routine
instruction (see [AUTHORITY_MODEL.md](AUTHORITY_MODEL.md)).

## Core framing

ClearWright is a QoS-style coordination, clearance, and backpressure-control layer
for multi-AI-agent work. The protocol is inspired by wireless RTS/CTS (Request to
Send / Clear to Send) collision avoidance and by the hidden-node and
channel-reservation concepts that motivate it: before an agent begins expensive,
noisy, or consequential work, it must confirm that the channel is clear and that
it holds the authority to proceed.

ClearWright borrows the reservation intuition but EXTENDS the model well beyond a
radio channel — to authority, intent, dependencies, evidence, resources, workflow
state, backpressure, and bounded leases. ClearWright is therefore NOT equivalent to
IEEE 802.11: it does not inherit 802.11 wire semantics or its operational
guarantees, and the analogy is conceptual, not a claim of protocol compatibility.

Agents coordinate through RTA / CTA / DTA messages. Which actor may issue each
message depends on delegated authority, clearance class, and operator-defined
policy, not on a blanket requirement for human approval at every step.

**Operator-controlled does not mean the operator approves every action.**
Operator-controlled means:
- The operator defines policy boundaries.
- The operator defines authority classes and delegation rules.
- The operator defines which actions require escalation.
- The operator remains highest authority and final override within the active root
  policy; changing a control marked non-bypassable requires a separate, explicit,
  governed root-policy decision (see [AUTHORITY_MODEL.md](AUTHORITY_MODEL.md)).
- Agents may coordinate autonomously inside delegated authority.

## Collision avoidance and work invalidation

ClearWright exists to keep agents from colliding with one another and from doing
work that another action has already invalidated. It is designed to prevent
duplicate work; stale work; contradictory work; work invalidated by another active
task; work made unnecessary by another task; unauthorized action; and expensive
parallel work launched without coordinated clearance.

Coordination is not only about file locks. ClearWright can DENY or RESERVE work
even when there is NO direct file-level conflict, because collisions take several
forms [protocol direction]:

- **Resource collision:** two actors modify the same file, branch, queue record,
  service, schema, environment, or governed resource.
- **Intent collision:** two actors pursue incompatible or duplicative solutions to
  the same underlying problem.
- **Dependency collision:** one action changes assumptions, inputs, or
  prerequisites another action depends on.
- **Redundancy collision:** one action is expected to make another action
  unnecessary.
- **Authority collision:** two grants or actors permit incompatible actions.
- **Evidence collision:** an artifact is reviewed while another actor is changing,
  replacing, or invalidating it.

Because intent, dependency, redundancy, authority, and evidence collisions do not
require two actors to touch the same file, ClearWright may withhold or reserve
clearance on work that would look independent at the file level.

## Agents collaborate before escalating

The goal is not to silence agents; it is to prevent unmanaged noise, duplicate
effort, stale work, and unauthorized action. Agents should work together, build on
each other's work, challenge each other's assumptions, improve the work product,
and resolve routine conflicts within their delegated authority. Two agents that
disagree about a routine matter resolve it between themselves or with a reviewer
before either escalates.

Escalation is a tool, not a reflex. Decisions are routed to the **lowest
sufficient authority**: the lowest authority level adequate for the risk, scope,
clearance class, and channel involved.

> Escalation should move upward only as far as necessary, not as high as possible.

## Authority model

Not all agents have equal authority. The protocol defines authority tiers:

| Tier | Actor | Role |
|------|-------|------|
| 0 | Operator / User | Highest authority. Defines policy. Final override within the active root policy. Approves escalated or high-risk work. Can override any agent, arbiter, or policy result; a control marked non-bypassable is changed only by a separate, explicit, governed root-policy decision (see [AUTHORITY_MODEL.md](AUTHORITY_MODEL.md)). |
| 1 | Orchestrator / PM Agent | Coordinates workflow. Manages queue and stage transitions. May issue routine CTA/DTA within delegated policy. Escalates risk, conflict, or uncertainty to the operator. |
| 2 | Reviewer / Verifier Agent | Reviews work. Challenges assumptions. Issues DTA for unsafe, invalid, stale, or conflicting work. Recommends CTA where allowed by delegated authority. |
| 3 | Worker Agent | Requests action. Performs scoped work after clearance. Cannot be sole authority for consequential self-clearance. May self-clear only explicitly allowed low-risk actions. |
| 4 | Observer / Logger / Clerk | Read-only or mostly read-only. Records state. Reports status. Does not grant broad execution authority unless explicitly delegated. |

**Lower authority actors request. Higher authority actors, arbiters, or policy rules
clear, deny, defer, or escalate. The operator remains final authority and override
within the active root policy; a control marked non-bypassable is changed only by a
separate, explicit, governed root-policy decision (see [AUTHORITY_MODEL.md](AUTHORITY_MODEL.md)).**

## Command tier and domain authority

The five-tier view above is the coarse model. The fine-grained model assigns each
actor a four-digit `authority_level` and is defined in full in
[AUTHORITY_MODEL.md](AUTHORITY_MODEL.md). The essentials:

- An actor is `actor_class + authority_level + authority_domain + delegation_scope`.
- `authority_level` sorts **ascending**: `0000` is highest authority, `9999` is
  lowest. A lower number means greater authority.
- `actor_class` is one of `OPERATOR`, `ORCHESTRATOR`, `POLICY_ENGINE`, `REVIEWER`,
  `WORKER`, `OBSERVER`, `SYSTEM`. There can be many of each.

**Command tier (`0000-0099`).** The general officer tier. A valid command-tier
actor holds global go, stop, freeze, revoke, supersede, and override authority
across all lanes unless restricted by root policy. When command tier says GO or
STOP, lower authority complies. `OPERATOR-0001` can override `OPERATOR-0002`;
`OPERATOR-0002` cannot override `OPERATOR-0001`; `OPERATOR-0000` is final root
authority. Command-tier actors can issue global GO, STOP, FREEZE, override, or
superseding decisions, but **every command-tier action remains auditable and
policy-bound**. Strong authority is not arbitrary authority. Command-tier
overrides are logged and supersede, never erase, the prior decision.

**Domain authority bands.** Below command tier, authority is organized by
functional domain (deployment `0100-0199`, security `0200-0299`, code `0300-0399`,
documentation `0400-0499`, data `0500-0599`, model risk `0600-0699`, infrastructure
and energy `0700-0799`, product `0800-0899`, legal and finance `0900-0999`, and
delegated/orchestration/worker/observer ranges above). Within a domain, a lower
number outranks a higher number. Across domains, numeric order does not settle
disputes. Those escalate. These bands are default policy examples, not fixed
protocol limits.

> Command-tier authority controls the system. Domain authority controls its lane.
> Cross-domain conflict escalates.

## Clearance classes

Clearance class defines the scope and risk level of authority a CTA grants:

| Class | Meaning |
|-------|---------|
| `READ_ONLY` | May read state; no mutations |
| `DOCS_ONLY` | May update documentation; no runtime or schema changes |
| `BRANCH_CODE` | May propose code changes on a branch |
| `QUEUE_MOVE` | May claim and move queue packets |
| `EXECUTION_CANDIDATE` | May be submitted for execution pending additional approval |
| `MERGE_CANDIDATE` | May be submitted for merge pending domain-authority clearance — [protocol direction] |
| `DEPLOYMENT_CANDIDATE` | May be submitted for deploy or external-system action pending clearance — [protocol direction] |
| `HUMAN_REQUIRED` | Requires operator approval before any agent may act |

The [implemented] `clearance_class` values enforced in code today are `READ_ONLY`,
`DOCS_ONLY`, `BRANCH_CODE`, `QUEUE_MOVE`, `EXECUTION_CANDIDATE`, and
`HUMAN_REQUIRED`. `MERGE_CANDIDATE` and `DEPLOYMENT_CANDIDATE` are documented but
not code-enforced today ([protocol direction]).

## Clearance lease model

A CTA is a **bounded lease, not a blank check.** Each CTA is scoped by:

`actor_id`, `actor_class`, `authority_level`, `requested_action`, `clearance_class`,
`clearance_level`, `priority_class`, `priority_level`, `channel_id`,
`resource_scope`, `valid_from`, `expires_at`, `delegated_by`, `conditions`, and
`invalidation_rules`.

A CTA may expire, be revoked, be superseded, be narrowed, or be escalated. A CTA is
**never silently broadened** beyond its original scope; broadening requires a new
clearance decision.

### Canonical CTA lease fields

A CTA lease is scope-bound, resource-bound, condition-bound, AND time-bound. The
canonical minimum lease descriptor is 11 fields [protocol direction]:

- `lease_id` — unique identifier for the lease.
- `approved_scope_hash` — hash of the scope the lease actually approves.
- `reserved_resources` — the resources the lease reserves.
- `protected_dependencies` — the dependencies the lease assumes remain stable.
- `input_hashes` — hashes of the inputs the cleared work is reasoned against.
- `valid_from` — when the lease becomes active.
- `expires_at` — when the lease expires.
- `renewal_policy` — whether and how the lease may be renewed.
- `release_conditions` — the conditions under which the lease is released.
- `invalidation_conditions` — the conditions that invalidate the lease.
- `supersedes_lease_id` — the prior lease this one replaces, if any.

`expires_at` maps to the [implemented] `clearance_expires_at` (the CTA
`--lease-minutes` value, default 120); the remaining fields are [protocol
direction].

A CTA becomes INVALID when: the approved scope changes; a bound source artifact
changes; the base commit changes materially; a dependency becomes stale; a higher
authority supersedes it; another completed action makes the work unnecessary; or
the lease expires. Agents should EXPLICITLY RELEASE abandoned or completed leases
rather than relying only on expiration, so a reserved channel is freed as soon as
the work is done or dropped.

### Clearance Consequence Level

Clearance is not binary. A `clearance_class` (above) names the category of action;
a `clearance_level` expresses how consequential it is — canonically, a higher
`clearance_level` means greater consequence. The field name `clearance_level` is
unchanged. Example levels:

| Level | Meaning |
|-------|---------|
| `CTA-L0001` | Safe read-only inspection |
| `CTA-L1000` | Status reporting / observation |
| `CTA-L2000` | Docs or draft changes |
| `CTA-L3000` | Low-risk branch changes |
| `CTA-L4000` | Code changes in an isolated branch |
| `CTA-L5000` | Queue movement or state transition |
| `CTA-L6000` | Reviewer-approved execution candidate |
| `CTA-L7000` | Merge candidate |
| `CTA-L8000` | Deploy or external-system action candidate |
| `CTA-L9000` | High-risk action |
| `CTA-L9999` | Human / root-operator approval required |

The numeric `clearance_level` ladder above is illustrative and [protocol direction];
the `clearance_class` category it draws from is [implemented].

## Authority, clearance, and priority are separate

These are independent dimensions. Conflating them is the most common way to reason
about the protocol incorrectly.

| Field | Answers |
|-------|---------|
| `authority_level` | Who can decide? |
| `authority_domain` | Which lane does that authority control? |
| `authority_band` | Which functional command range does the actor belong to? |
| `clearance_level` | How consequential is the action? |
| `priority_level` | How urgent is the request? |
| `channel_state` | Is the lane available? |
| `escalation_required` | Must higher authority review this? |

The three numeric scales **do not run in the same direction**, so compare them
carefully:

| Scale | Direction |
|-------|-----------|
| `authority_level` | Lower number = greater authority (`0000` highest). |
| `clearance_level` | Higher number = greater consequence. |
| `priority_level` | Lower number = greater urgency (`0000` highest urgency, `9999` lowest). |

Rules that follow from the separation:

- A high-priority request is not automatically high-authority.
- A high-authority actor is not automatically cleared for every action.
- A code authority cannot force deployment unless policy grants that authority.
- A deployment authority can block deployment even if code is approved.
- A busy channel can block even an authorized actor.
- Command-tier operators can override across the system within active root policy;
  domain operators control their assigned lanes; cross-domain conflict escalates. A
  control marked non-bypassable requires a separate, explicit, governed root-policy
  decision to change (see [AUTHORITY_MODEL.md](AUTHORITY_MODEL.md)).
- The root operator (`OPERATOR-0000`) remains final override, exercised through the
  governed root-policy decision process.

## Priority classes

| Class | Meaning |
|-------|---------|
| `LOW` | Deferred or background work |
| `NORMAL` | Standard workflow priority |
| `HIGH` | Elevated priority; may preempt LOW work |
| `URGENT` | Highest operational priority; used sparingly |
| `EMERGENCY` | Reserved for incident or command-tier action — [protocol direction] |

The [implemented] `priority_class` values are `LOW`, `NORMAL`, `HIGH`, and
`URGENT`. `EMERGENCY` is [protocol direction].

Priority has a `priority_class` (above) and may also carry a numeric
`priority_level`, where `0000` is the highest urgency and `9999` the lowest (a
lower number means greater urgency). Priority is intended to inform **ordering,
preemption, and backoff behavior**; authority determines who may clear or deny;
clearance determines what kind of action is allowed; channel state determines
whether the lane is available. Today `priority_level` is unenforced scheduling
metadata: no scheduler acts on it, so it records intended urgency rather than a
guaranteed execution order.

## Channels

A channel is whatever an actor must occupy or clear before acting. A channel can
represent a file, a branch, a packet, a queue stage, a review lane, a deployment
lane, an operator attention lane, an external system, an execution environment, a
compute cluster, a GPU queue, a power or cooling capacity window, a data access
boundary, or a model evaluation gate.

## Channel states

| State | Meaning |
|-------|---------|
| `CLEAR` | Channel is available; CTA may be issued |
| `BUSY` | Another agent holds the channel |
| `BLOCKED` | A dependency or policy condition prevents clearance |
| `STALE` | A previous claim expired without completion |
| `ESCALATED` | A condition requiring operator review is open |
| `FROZEN` | Work is held by a command-tier or domain freeze — [protocol direction] |
| `DEGRADED` | The channel is operating with reduced capacity or confidence — [protocol direction] |
| `RESERVED` | The channel is held for a pending authorized actor — [protocol direction] |

The [implemented] `channel_state` values are `CLEAR`, `BUSY`, `BLOCKED`, `STALE`,
and `ESCALATED`. `RESERVED`, `FROZEN`, and `DEGRADED` are [protocol direction].

## The four protocol dimensions

RTA, CTA, and DTA are clearance MESSAGES — an RTA requests clearance, a CTA
grants it, and a DTA denies it — not execution states. Reasoning about the
protocol correctly means keeping four separate dimensions distinct
[protocol direction]:

- **Clearance decision:** `RTA`, `CTA`, `DTA` — the request, the grant, and the
  denial in the clearance dimension.
- **Execution state:** `QUEUED`, `CLAIMED`, `IN_PROGRESS`, `VERIFYING`, `DONE`,
  `FAILED`, `SUPERSEDED`.
- **Review state:** `IN_REVIEW`, `RFI_PENDING`, `OPERATOR_REQUIRED`.
- **Channel state:** `CLEAR`, `BUSY`, `BLOCKED`, `RESERVED`, `STALE`, `FROZEN`,
  `ESCALATED`, `DEGRADED`.

The current implementation records ONE combined packet `status`; these dimensions
are a documentation lens over that single field, not four separate enums in code.
The [implemented] packet `status` values map to the dimensions as follows:

| Packet `status` | Dimension | Reading |
|-----------------|-----------|---------|
| `RTA` | Clearance decision | A request to act has been raised. |
| `IN_REVIEW` | Review | The request is under review. |
| `RFI_PENDING` | Review | A Request for Information is open. |
| `CTA` | Clearance decision | The request is cleared to act. |
| `IN_PROGRESS` | Execution | Cleared work is executing under a lease. |
| `DTA` | Clearance decision | The request is denied; terminal to `clearance_done`. |
| `DONE` | Execution | Execution completed and was accepted. |
| `FAILED` | Execution | Execution broke AFTER a valid clearance. |
| `SUPERSEDED` | Execution | A later decision replaced this one. |

`RTA`, `CTA`, and `DTA` are clearance messages that today ALSO appear as values of
the single combined `status` field; the protocol does not claim a separate
execution-state enum in code. The execution-state values `QUEUED`, `CLAIMED`,
and `VERIFYING`, the review-state value `OPERATOR_REQUIRED`, and the channel
states `RESERVED`, `FROZEN`, and `DEGRADED`, are [protocol direction].

## The request lifecycle

### Request to Act (RTA)

An actor declares intent to occupy a workflow channel, resource, stage, or
authority boundary. The RTA states what the actor intends to do and why. An RTA
is a request, not permission. RTAs may be issued by agents at any authority tier.

**Canonical minimum RTA payload [protocol direction].** A complete RTA carries
enough context for a reviewer to reason about collisions, authority, and scope.
The canonical minimum payload is 17 fields:

- `request_id` — unique identifier for this request.
- `work_item_id` — the governed work item the request belongs to.
- `actor_id` — the actor issuing the request.
- `requested_action` — what the actor intends to do.
- `scope` — the concrete surface the action touches.
- `scope_hash` — a hash of that scope, so a later scope change is detectable.
- `resources_requested` — the resources the action needs to occupy or use.
- `claim_mode` — how the actor intends to hold the resources (see below).
- `dependencies` — the actions, inputs, or prerequisites this action relies on.
- `expected_outputs` — the artifacts or results the action is expected to produce.
- `expected_side_effects` — effects beyond the primary output.
- `repo_commit` — the base commit the request is reasoned against.
- `input_artifact_hashes` — hashes of the input artifacts, so their change is
  detectable.
- `priority_class` — the requested priority lane.
- `requested_clearance_class` — the clearance class the action needs.
- `requested_lease_duration` — how long the actor expects to hold clearance.
- `authority_reference` — the authority under which the request is made.

Today's clearance packet columns cover a subset of these conceptually
(`packet_id` ~ `request_id`, `requesting_agent` ~ `actor_id`, `proposed_action` ~
`requested_action`, `scope`, `clearance_class` ~ `requested_clearance_class`,
`priority_class`); the remaining fields are [protocol direction].

**Claim modes [protocol direction].** `claim_mode` declares how an actor intends to
hold the resources it requests. There are five:

- `SHARED_READ` — read a resource without excluding other readers.
- `EXCLUSIVE_WRITE` — hold a resource for exclusive modification.
- `INTENT_EXCLUSIVE` — reserve a problem, not a file: it protects against duplicate
  or incompatible problem-solving EVEN WHEN the agents involved would touch
  different files.
- `DEPENDENCY_WAIT` — wait on a prerequisite action or input before proceeding.
- `DEPLOYMENT_EXCLUSIVE` — hold a deployment or external-action lane exclusively.

Today a claim is a single status move to `IN_PROGRESS` with no distinct mode; the
five claim modes are [protocol direction].

### clearance review

The RTA is reviewed by one or more reviewing agents, arbiters, policy rules, or
the operator. Review may produce a recommendation, a DTA, or a CTA, depending
on the clearance class, the reviewing actor's authority, and the operator policy
in effect for that action type.

### Clear to Act (CTA)

A CTA grants bounded clearance to proceed within a defined scope, time, risk
level, priority class, and authority class. A CTA is a bounded lease, not a blank
check. It expires. It does not authorize actions beyond its stated scope.

Who may issue a CTA:
- An **orchestrator or reviewer agent**, for routine low-risk actions within
  their delegated authority and clearance class.
- A **policy engine or arbiter**, when the action matches a pre-approved policy
  rule.
- The **operator**, for high-risk, high-authority, or escalated actions, or any
  time the operator chooses to approve directly.

The operator is never required for routine actions within delegated authority.
The operator is required when the clearance class is `HUMAN_REQUIRED`, risk is
elevated, reviewers disagree, or a policy boundary is crossed.

### Denied to Act (DTA)

A DTA denies, defers, blocks, or escalates a request because the channel,
resource, authority, dependency, policy boundary, or next stage is not clear.
A DTA may be issued by an agent, reviewer, arbiter, policy engine, or operator.

**DTA is a successful safety outcome.** DTA is not a failure. The system worked
as intended: the channel was not clear and the agent did not proceed.

DTA is never routed to `FAILED`. `FAILED` means execution broke after a valid CTA
was claimed; it has nothing to do with a deliberate denial.

**Typed DTA dispositions and reason codes [protocol direction].** A DTA carries a
typed disposition describing how the request was denied, and a reason code
describing why. The six dispositions are:

- `DENY` — the request is refused outright.
- `DEFER` — the request is held to be retried later.
- `BLOCK` — the request is held pending a blocking condition.
- `ESCALATE` — the request is routed to higher authority.
- `SUPERSEDED` — a later decision has replaced this request.
- `NO_LONGER_NEEDED` — another action has made the request unnecessary.

The thirteen reason codes are: `resource_collision`, `intent_collision`,
`dependency_active`, `duplicate_work`, `work_no_longer_needed`, `scope_conflict`,
`insufficient_authority`, `stale_context`, `stale_commit`, `policy_block`,
`channel_busy`, `operator_required`, and `lease_unavailable`.

Retry is allowed only under CHANGED conditions — for example a new commit, a
refreshed dependency, a resolved conflict, renewed authority, or a freed resource.
Retrying an unchanged request against unchanged conditions is expected to be denied
again. Today a DTA is a flat move to `clearance_done` with a free-text reason and
no typed disposition or reason code; the typed dispositions and reason codes are
[protocol direction]. A DTA archives to `clearance_done` and is never routed to
`clearance_failed`.


## Backpressure and channel readiness

ClearWright relieves multi-agent backpressure. When agents start expensive work
without coordinating, the result is:

- Redundant work and conflicting outputs
- Token waste and API cost
- Compute waste and latency
- Human review noise
- Unnecessary energy use, heat, and cooling load

The RTA/CTA/DTA cycle is the backpressure valve. An agent checks whether the
channel is open before committing resources. A DTA tells the agent to hold: the
channel is busy, blocked, stale, or escalated. The agent does not waste resources
on work that will conflict, duplicate, or be discarded.

This connects to a broader engineering principle: better coordination reduces
unnecessary compute load, which reduces power, heat, cooling, and waste.

Before a CTA is issued, the channel must be confirmed clear:
- No other agent actively occupies the same workflow stage or resource.
- The dependency chain is satisfied.
- The risk level is within the clearance class.
- The requesting agent's authority is sufficient.
- No backpressure condition blocks the channel.

If the channel is not clear, a DTA is issued. The requesting agent waits and
re-requests when conditions change.

## Human approval as escalation, not every action

Human approval is required when:
- The clearance class is `HUMAN_REQUIRED`.
- Risk or scope exceeds the orchestrator's delegated authority.
- Reviewers disagree and the conflict cannot be resolved autonomously.
- A policy boundary is crossed.
- An unknown condition requires operator judgment.

Human approval is NOT required for:
- Routine, low-risk actions within a defined clearance class and delegated authority.
- Autonomous DTA decisions by reviewers or policy rules.
- Status checks, queue inspection, and read-only operations.

The operator remains highest authority and final override within the active root
policy. Any decision can be escalated. The operator can override any agent,
arbiter, or policy result at any time; a control marked non-bypassable is changed
only by a separate, explicit, governed root-policy decision, not by a routine
override (see [AUTHORITY_MODEL.md](AUTHORITY_MODEL.md)).

## clearance packet

Every request and its lifecycle is captured in a durable clearance packet: the
request, reviews, challenges, RFIs, the clearance or denial, outcome, and audit
events. The packet is the record. See
the durable-packets design.

## Escalation routing

Escalation does not jump to `OPERATOR-0000`. It climbs one sufficient step at a
time:

1. A **worker** resolves with a peer or a reviewer.
2. A **reviewer** resolves by validation, DTA, or a Request for Information (RFI).
3. An **orchestrator** resolves workflow sequencing and channel contention.
4. A **domain authority** resolves lane-specific decisions.
5. A **policy engine or command-tier actor** resolves cross-domain conflict.
6. The **root operator** resolves only final, emergency, or unresolved high-risk
   matters.

Escalate when, and only when: peers cannot resolve the conflict; the channel is
blocked; the requested clearance exceeds delegated authority; the risk crosses a
threshold; a policy boundary is reached; cross-domain authority is required; an
active lease must be revoked or superseded; human judgment is explicitly required;
or a command-level decision is needed.

> Escalation should move upward only as far as necessary, not as high as possible.

## RFI escalation

When a request cannot be cleared or denied directly, for example reviewers
disagree, or a constraint is unclear, the system escalates with a Request for
Information (RFI). The RFI presents the constraint, options, and tradeoffs to the
operator, who makes the call. The operator's directive resolves the presented
decision and is recorded as durable authority. Execution proceeds within that
authority unless new evidence, a safety boundary, or a material scope conflict
requires a new RTA, DTA, or escalation.

## Audit and superseding decisions

Every step is recorded so that anyone can later answer: what was requested, who
or what reviewed it, who issued the CTA or DTA and under what authority, and what
happened. The audit trail is durable and is not silently rewritten.

When a higher authority overrides, revokes, narrows, expands, freezes, or
supersedes a decision, the original decision is **not deleted.** A new decision
event is created; the new event references the prior decision; the active decision
is always unambiguous; and the audit trail shows who changed what, when, why, and
under what authority.

Decision-level audit fields (documented as direction; see [AUTHORITY_MODEL.md](AUTHORITY_MODEL.md) for which exist today):

`decision_id`, `supersedes_decision_id`, `active_decision`, `actor_id`,
`actor_class`, `authority_level`, `authority_band`, `authority_domain`,
`delegated_by`, `delegation_scope_json`, `delegation_expires_at`,
`override_scope_json`, `requested_clearance_class`, `requested_clearance_level`,
`granted_clearance_class`, `granted_clearance_level`, `priority_class`,
`priority_level`, `channel_id`, `channel_state`, `resource_scope_json`,
`clearance_expires_at`, `escalation_required`, `escalation_threshold_level`,
`escalation_reason`, `backpressure_json`, `override_reason`, `overridden_by`.

## Metrics to track

The protocol is designed to be measurable. Useful metrics for future tracking:

RTA count; CTA count; DTA count; DTA by reason; deferred requests; retries; stale
claims; channel busy time; average wait time; avoided duplicate work; estimated
tokens avoided; estimated cost avoided; human escalations; human escalations
avoided; override count; clearance lease expiration count; command-tier overrides;
cross-domain escalations; and energy/cooling related deferrals.

These are tracking targets, not measured claims. No specific savings are asserted.

## Separation of duties

The agent that requests an action is not the sole authority that clears it. Review,
clearance, and execution are distinct roles. A worker agent may not self-clear a
consequential action. An orchestrator or reviewer may clear actions within
delegated authority; the operator clears actions beyond that boundary.

## Relationship to consensus

The consensus process (see CONSENSUS_PROTOCOL.md) is how
proposals are reviewed and confidence is built before a clearance decision.
Consensus is input to clearance. It supports a CTA recommendation but does not
substitute for clearance authority. Clearance authority derives from authority tier,
clearance class, and operator-defined policy.

## Engineering control feedback loops

The mechanics above can be read as a set of engineering control feedback loops:
the system senses channel and packet state, grants or denies clearance, acts
under a bounded lease, verifies output, corrects course, records evidence, and
improves future routing, policy, thresholds, and backpressure controls. This is a
control-system view over the existing protocol, not new machinery, new packet
states, or new vocabulary. It is also distinct from the multi-round consensus
loop: the consensus loop refines work; the control loop governs clearance,
verification, correction, and audit around it. See
[ENGINEERING_CONTROL_LOOPS.md](ENGINEERING_CONTROL_LOOPS.md).
