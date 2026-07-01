# ClearWright&trade;: Engineering Control Feedback Loops

This document describes ClearWright as a closed-loop engineering control system.
It is an explanatory view over mechanisms the ClearWright Protocol already defines:
channel clearance, bounded CTA leases, DTA as a successful safety outcome,
backpressure relief, review, verification, escalation routing, command-tier
override, audit, and metrics. It does not introduce new machinery, a new packet
lifecycle, or a competing vocabulary. It is a lens, not a new layer.

For the protocol mechanics see [CLEARWRIGHT_PROTOCOL.md](CLEARWRIGHT_PROTOCOL.md); for the
authority numbering and bands see [AUTHORITY_MODEL.md](AUTHORITY_MODEL.md); for
plain-language term definitions see [GLOSSARY.md](GLOSSARY.md). Where this
document and those differ, the protocol and authority docs are authoritative.

> In these docs, an engineering control feedback loop is not the same thing as
> the multi-agent consensus loop. The consensus loop (see
> CONSENSUS_PROTOCOL.md) describes how agents challenge
> and refine work. The engineering control loop describes how the system senses
> state, grants or denies clearance, verifies output, corrects course, records
> evidence, and improves future control decisions. The consensus loop is one
> quality mechanism inside the larger engineering control model.

---

## 1. Executive summary

ClearWright treats multi-agent work as a closed-loop engineering control system.
Agents request clearance, operate under bounded authority, submit work for
verification, respond to correction, and leave audit evidence that improves
future control decisions.

**ClearWright is not a one-way approval path. It is a closed-loop engineering
control system for autonomous and semi-autonomous agent work.**

Agents do not simply act and move on. They declare intent, request clearance,
act under bounded authority, submit work for review or verification, respond to
correction, escalate only when necessary, leave audit evidence, and improve
future routing, policy, thresholds, and backpressure controls.

Engineering control feedback loops are different from the consensus loop. The
consensus loop is one quality mechanism inside the larger engineering control
model. This document reads the existing ClearWright Protocol as a set of feedback loops;
it does not add states, schema, or runtime behavior.

---

## 2. Why engineering control loops matter

Real engineering systems do not rely on one-shot decisions. They use feedback.
A controller senses the state of the system, acts within bounded limits, checks
the result, and corrects. ClearWright applies the same discipline to multi-agent
work.

The control cycle:

- sense state
- declare intent
- request clearance
- act under bounded authority
- verify output
- correct course
- complete or escalate
- record evidence
- improve future control

Without feedback loops, multi-agent systems become:

- noisy
- wasteful
- stale
- unauditable
- hard to govern
- prone to repeated mistakes
- prone to token and compute waste
- prone to unnecessary human interruption

The loops are what turn a pile of capable agents into a governed system that
slows down gracefully under load instead of thrashing.

---

## 3. The core control loop

The basic loop reads the existing protocol mechanics as a control cycle:

**Sense.** The system observes channel state, packet state, authority state,
priority, claims, leases, and policy boundaries before anything starts.

**Intent.** An actor declares what it wants to do through a Request to Act (RTA).
An RTA is a request, not permission.

**Clearance.** The protocol checks channel state, authority level, authority
domain, clearance class, priority, and policy, then issues a Clear to Act (CTA),
a Denied to Act (DTA), or a Request for Information (RFI).

**Action.** Work proceeds only under a bounded CTA lease, scoped by action,
clearance class, clearance level, priority, channel, resource, issuer, and
expiry.

**Verification.** A reviewer, policy engine, orchestrator, test, domain
authority, or operator evaluates the result. Work is not accepted merely because
an agent finished it.

**Correction.** When the result is not acceptable, the system issues an RFI, a
DTA, a retry, a backoff, rework, an escalation, or a superseding decision.

**Completion.** Successful work moves to `DONE` or to the next approved stage.

**Improvement.** The audit trail and metrics improve future routing, policy,
prompts, thresholds, channel rules, and backpressure controls.

### Improvement means operational learning, not self-retraining

"Learning" here does not mean agents silently retrain themselves or invent new
authority. It means operational learning: the operator and policy layer tune the
system using the audit evidence the loops produce. Concretely, that is:

- better policies
- better thresholds
- better prompts
- better routing
- better test requirements
- better escalation rules
- better cost controls
- better energy and cooling awareness
- better audit evidence

No loop grants an agent new authority, and no loop rewrites policy on its own.
Improvement is a decision the operator and policy layer make with better evidence.

---

## 4. Named engineering control loops

These are explanatory control loops, not new packet states. Each one is a way of
reading mechanisms the protocol already defines.

### A. Clearance control loop

`RTA -> CTA / DTA / RFI -> action or wait`

Prevents agents from starting work when the channel, resource, authority, or next
stage is not clear. A CTA starts a bounded lease; a DTA holds the work; an RFI
routes an unresolved question to the operator.

### B. Review control loop

`draft -> review -> challenge -> revise -> validate`

Improves work quality through challenge, refinement, and verification. This
overlaps with the consensus loop, but the review control loop is framed as
engineering control: output is checked, corrected, accepted, or sent back. The
consensus loop remains the place where multi-round agent challenge and refinement
is specified.

### C. Verification control loop

`claim -> execute -> test -> validate -> accept or reject`

Ensures work is not accepted merely because an agent completed it. A claim and a
completion are not the same as a verified, accepted result.

### D. Escalation control loop

`peer -> reviewer -> orchestrator -> domain authority -> command tier`

Preserves lowest sufficient authority. Escalation moves upward only as far as
necessary, not as high as possible: to the lowest authority adequate for the
risk, scope, clearance class, and channel involved.

### E. Backpressure control loop

`measure channel load -> DTA / defer / retry / backoff -> reduce wasted work -> reopen channel`

Prevents agents from piling work into a busy, blocked, stale, frozen, or degraded
system. `DEFER` is not a first-class packet status. Deferral is a disposition
inside a DTA, or a retry instruction, not a peer status beside RTA, CTA, and DTA.

### F. Audit improvement loop

`decision -> result -> metric -> lesson -> policy update`

Turns completed work and blocked work into evidence for better future control.
The lesson updates policy, thresholds, and routing; it does not silently change
authority.

### G. Safety control loop

`detect risk -> DTA or FREEZE -> review -> correct -> resume or escalate`

Treats DTA, FREEZE, and escalation as successful safety controls, not failures.
FREEZE is not a packet status. FREEZE is a command-tier verb that stops active
work; `FROZEN` is a channel state. A DTA is a successful safety outcome, never a
`FAILED` execution.

### H. Cost and capacity control loop

`detect waste or capacity pressure -> reduce duplicate work -> route to a smaller, cheaper, slower, or deferred path where appropriate -> record savings indicators`

Reduces unnecessary tokens, compute cycles, queue pressure, and API cost, and may
reduce possible downstream energy and cooling demand. Savings indicators are
recorded as tracking targets, not as measured claims.

---

## 5. Relationship to ClearWright Protocol

Engineering control loops use existing ClearWright Protocol concepts. They do not add or
rename any:

- **RTA** declares intent.
- **CTA** grants bounded clearance: a lease, not a blank check, that expires.
- **DTA** prevents unsafe, stale, conflicting, unauthorized, or premature work.
  A DTA may defer, block, deny, or escalate. It is a successful safety outcome.
- **RFI** requests more information from the operator when a request cannot be
  cleared or denied directly.
- **FREEZE** is a command-tier verb that stops active work.
- **FROZEN** is a channel state.
- **DONE** completes a stage.
- **FAILED** remains execution failure after a valid claim, never a denial.
- **SUPERSEDED** records a replacement decision without deleting the prior one.

Do not read `DEFER` or `FREEZE` as packet lifecycle statuses. The registry
statuses are `RTA`, `IN_REVIEW`, `RFI_PENDING`, `CTA`, `IN_PROGRESS`, `DTA`,
`DONE`, `FAILED`, and `SUPERSEDED`. `DEFER` is a disposition inside a DTA or a
retry instruction. `FREEZE` is a command action; `FROZEN` is a channel state.

---

## 6. Relationship to authority model

The loops run inside the authority model defined in
[AUTHORITY_MODEL.md](AUTHORITY_MODEL.md):

- Command tier controls the system.
- Domain authority controls its lane.
- Cross-domain conflict escalates.
- Lowest sufficient authority resolves work at the lowest level adequate for the
  risk, scope, clearance class, and channel involved.
- No worker agent can be the sole authority for consequential self-clearance.
- Authority, clearance, priority, channel state, and escalation are separate
  concerns.

Keep the numeric ordering precise. `authority_level` sorts ascending:

- a lower number means stronger authority
- `0000` is highest
- `9999` is lowest

Do not apply this inversion to `clearance_level` or `priority_level`.
`clearance_level` may increase with consequence (a higher number can mean a more
consequential action). `priority_level` is policy-defined; each deployment
documents whether higher or lower means more urgent.

---

## 7. Examples

**Example 1: routine docs work, no human approval.**
A worker agent requests an RTA to edit docs. The reviewer or orchestrator sees
the documentation channel is clear. A `CTA-L2000` clearance level (docs or draft
changes) is granted for 30 minutes. The worker edits. The reviewer validates. The
packet moves to `DONE`. No human approval is required.

**Example 2: contended channel.**
A code agent wants to modify a branch while another agent is reviewing the same
target. The channel is `BUSY`. The policy engine issues a DTA with defer or retry
guidance. This prevents stale review, conflicting edits, and wasted tokens.

**Example 3: rejected output is not a failure.**
A reviewer rejects an agent's output. The result is not `FAILED`. The review
control loop sends the work back for revision through an RFI or a DTA. The packet
records the decision and the reason.

**Example 4: cross-domain conflict.**
A deployment request has code approval, but deployment authority says the release
lane is not clear. The deployment DTA controls deployment. The code approval
remains recorded. The conflict escalates only if policy requires it.

**Example 5: command-tier freeze.**
A command-tier operator issues a FREEZE on a packet. FREEZE is a command-tier
action, not a packet status. All active loops stop or defer. The original
decisions remain in the audit trail. The FREEZE supersedes active clearance until
it is released or replaced.

**Example 6: backpressure under load.**
A hyperscale AI workflow is consuming too many tokens through duplicate review.
Backpressure metrics show repeated RTAs against a busy review lane. A policy
change routes duplicate requests to a DTA with defer or retry guidance instead of
allowing repeated agent runs. Future token waste is designed to be reduced.

---

## 8. What the loops are designed to save

Engineering control feedback loops are designed to reduce:

- duplicate agent work
- stale work
- unnecessary prompts
- repeated completions
- unnecessary retries
- unnecessary review cycles
- conflicting branches
- discarded work
- human interruption
- queue pressure
- compute cycles
- API cost
- latency
- downstream power, heat, and cooling demand, as a potential benefit

This document does not claim measured savings. It does not claim regulatory
compliance, and it does not claim that ClearWright satisfies any law, rule, or
standard. The loops are described using "designed to reduce," "may reduce," and
"potential downstream benefit" deliberately. Energy and cooling effects are
framed as potential downstream benefits of doing less avoidable work, not as
measured figures.

---

## 9. What this does not mean

- Engineering control loops do not grant agents unlimited autonomy.
- Engineering control loops do not let agents silently rewrite policy.
- Engineering control loops do not replace human judgment where policy requires it.
- Engineering control loops do not make every issue a command-tier issue.
- Engineering control loops do not erase audit history.
- Engineering control loops do not turn DTA into failure.
- Engineering control loops do not replace the consensus loop.
- Engineering control loops provide the broader control-system frame around
  clearance, review, verification, escalation, backpressure, and audit.

---

## 10. Future metrics

The loops are designed to be measurable. The following are future tracking
targets, not metrics that are instrumented today and not measured claims:

- RTA count
- CTA count
- DTA count
- RFI count
- DTA-defer count
- command FREEZE count
- review loop count
- rework count
- stale claim count
- channel busy time
- average wait time
- retry count
- escalation count
- command-tier override count
- cross-domain conflict count
- human escalations avoided
- estimated duplicate work avoided
- estimated tokens avoided
- estimated cost avoided
- energy and cooling related deferrals

These are tracking targets for a future instrumentation phase. No specific
savings are asserted.
