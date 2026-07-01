# ClearWright&trade;: Authority Model

This document defines the authority model for the ClearWright Protocol: who may decide,
how much authority each actor holds, which lane that authority controls, and how
conflicts escalate. It is one of the core protocol documents.

This file is the source of record for the authority bands, actor classes, and the
authority-level numbering scheme. Other documents (CLEARWRIGHT_PROTOCOL and GLOSSARY) restate parts of this model so they stand alone; when
they differ, this document is authoritative.

---

## Overview

The ClearWright Protocol authority model is similar to a chain of command combined with
domain-specific civil authority.

- There is a top command tier.
- There are domain authorities, each controlling one functional lane.
- There are delegated operators.
- There are orchestrators, reviewers, workers, observers, and system actors.

Actors collaborate horizontally where they can and escalate vertically only when
they must. The governing principle is **lowest sufficient authority**: a decision
is resolved at the lowest authority level that is adequate for the risk, scope,
clearance class, and channel involved.

> Command-tier authority controls the system. Domain authority controls its lane.
> Cross-domain conflict escalates.

---

## Authority is not equality

The protocol is not a flat peer network. Actors hold different authority. A worker
agent cannot be the sole authority that clears its own consequential action. A
reviewer can deny work it considers unsafe even when a worker disagrees. A
command-tier operator can stop all work on a packet.

Authority is not a single number, either. An actor is best understood as a
combination of four things:

```
actor_class + authority_level + authority_domain + delegation_scope
```

- **actor_class**: what kind of actor it is (operator, orchestrator, reviewer, worker, ...).
- **authority_level**: how much decision authority it holds, as a four-digit number.
- **authority_domain**: which functional lane that authority controls (code, deployment, ...).
- **delegation_scope**: the bounded grant under which it acts (which repo, branch, project, environment).

---

## Authority levels sort ascending

Authority levels are four-digit numbers. **Lower numbers carry greater authority.**

- `0000` is the highest authority (root / final override).
- `9999` is the lowest authority (no delegated decision authority).

This is the single most important rule in the model, and it is easy to get
backwards. A higher number does **not** mean higher authority. `OPERATOR-0001`
outranks `OPERATOR-0002`. `OPERATOR-0300` outranks `OPERATOR-0301` for code
decisions.

This ascending convention is deliberate: it mirrors precedence ordering in many
command and protocol systems, where rank 1 leads. It also leaves headroom. New,
lower-authority roles can always be added by using larger numbers without
renumbering existing actors.

> Note: the authority scale runs opposite to the clearance and priority scales.
> See "Authority, clearance, and priority are separate" in
> [CLEARWRIGHT_PROTOCOL.md](CLEARWRIGHT_PROTOCOL.md) for the side-by-side direction table.

---

## Command tier

The `0000-0099` range is the **command tier** (also called the general officer
tier). It is special.

Actors in this range hold global go, stop, freeze, revoke, supersede, and override
authority across all divisions of work, unless explicitly restricted by root
policy. When a valid command-tier actor says GO or STOP, lower-authority actors
comply.

Protocol mapping:

- **GO** may mean a bounded CTA, release of a hold, a superseding CTA, or a command authorization.
- **STOP** may mean a DTA, a FREEZE, a revocation, a superseding DTA, or an escalation hold.

Command-tier examples:

| Actor | Role |
|-------|------|
| `OPERATOR-0000` | Root operator / Commander-in-Chief equivalent / final authority |
| `OPERATOR-0001` | Senior command authority |
| `OPERATOR-0002` | Command authority subordinate to 0001, superior to 0003 and below |
| `OPERATOR-0010` | Enterprise command / incident command / strategic command |
| `OPERATOR-0099` | Lowest command-tier authority |

Command-tier rules:

- `OPERATOR-0001` can override `OPERATOR-0002`.
- `OPERATOR-0002` cannot override `OPERATOR-0001`.
- `OPERATOR-0002` may challenge, request reconsideration, or escalate to
  `OPERATOR-0001` or `OPERATOR-0000` if policy allows.
- `OPERATOR-0000` is final root authority.

Command-tier actors can issue global GO, STOP, FREEZE, override, or superseding
decisions, but **every command-tier action remains auditable and policy-bound.**
Strong authority is not arbitrary authority. A command-tier override does not erase
the original decision. It **supersedes** the prior decision and preserves the
audit trail. Command-tier authority is bounded by root policy and is always
recorded with who acted, what they did, when, why, and under what authority. See
"Override rules" below.

---

## Domain authority bands

Below the command tier, authority is organized by functional domain. Each band
owns one lane of work.

The following are **default policy examples, not fixed protocol limits.** An
organization may define its own bands.

| Band | Domain |
|------|--------|
| `0000-0099` | Command / root / general officer authority |
| `0100-0199` | Deployment / release authority |
| `0200-0299` | Security / safety / incident authority |
| `0300-0399` | Code / engineering authority |
| `0400-0499` | Documentation / records authority |
| `0500-0599` | Data / privacy / governance authority |
| `0600-0699` | Model risk / evaluation / AI safety authority |
| `0700-0799` | Infrastructure / capacity / energy / cooling authority |
| `0800-0899` | Product / mission / customer-impact authority |
| `0900-0999` | Legal / compliance / finance / FinOps authority |
| `1000-1999` | Senior delegated operators, service leads, enterprise automation |
| `2000-3999` | Orchestrators, policy engines, reviewers |
| `4000-7999` | Workers and scoped execution agents |
| `8000-9999` | Observers, loggers, clerks, read-only actors, or no authority |

### Within a domain band

Lower numbers outrank higher numbers **for that domain**.

- `OPERATOR-0300` outranks `OPERATOR-0301` for code decisions.
- `OPERATOR-0400` outranks `OPERATOR-0405` for documentation decisions.
- `OPERATOR-0100` outranks `OPERATOR-0110` for deployment decisions.

### Across domain bands

Do not assume that every numeric comparison below the command tier automatically
resolves every dispute. Numeric order decides rank **inside a lane**, not between
lanes.

- `OPERATOR-0100` owns deployment decisions.
- `OPERATOR-0300` owns code decisions.
- `OPERATOR-0400` owns documentation decisions.

`OPERATOR-0300` may say the code is acceptable. `OPERATOR-0100` may still deny
deployment if the release channel is not clear. `OPERATOR-0400` may require
documentation corrections but cannot force a production deployment. When code,
deployment, security, documentation, model risk, or infrastructure authorities
conflict, the protocol escalates according to policy.

> Command-tier authority controls the system. Domain authority controls its lane.
> Cross-domain conflict escalates.

---

## Actor classes

`actor_class` is separate from `authority_level`. The class says what kind of
actor it is; the level says how much authority it holds.

Suggested `actor_class` values:

| Class | Typical role |
|-------|--------------|
| `OPERATOR` | Human or command authority. Defines policy, clears high-risk work, final override. |
| `ORCHESTRATOR` | Coordinates workflow, sequencing, and queue transitions. Issues routine CTA/DTA within delegated authority. |
| `POLICY_ENGINE` | Evaluates requests against pre-approved policy rules and issues CTA/DTA automatically. |
| `REVIEWER` | Reviews, challenges, and validates work. Issues DTA for unsafe or invalid work; recommends CTA. |
| `WORKER` | Requests action and performs scoped work after clearance. |
| `OBSERVER` | Read-only. Records state, reports status, holds no decision authority. |
| `SYSTEM` | Non-agent system actor (timers, queue movers, audit writers). |

There can be **many** operators, orchestrators, policy engines, reviewers, workers,
and observers. Each can carry its own `authority_level`, `authority_domain`, and
`delegation_scope`. The model scales out horizontally; it is not a single chain of
five fixed roles.

### Relationship to the earlier tier model

Earlier protocol docs described authority as Tiers 0-4 (Operator, Orchestrator,
Reviewer, Worker, Observer). That tier model is the coarse view; this document is
the fine-grained view. The mapping is direct: Tier 0 maps to command-tier
`OPERATOR-00xx`, Tier 1 to orchestrators, Tier 2 to reviewers, Tier 3 to workers,
Tier 4 to observers. Operator remains highest in both views.

---

## Actor identifier forms

An actor identifier has one **canonical form** and one **accepted shorthand**.

**Canonical form:** `ACTORCLASS-NNNN / DOMAIN / SCOPE`, for example
`OPERATOR-0300 / CODE / CORE_REPO`. This is the form the durable record uses. The
full record must preserve `actor_class`, `authority_level`, `authority_domain`, and
`delegation_scope` as **separate fields**; it does not collapse them into a single
label.

**Accepted shorthand:** `DOMAIN-NNNN`, for example `CODE-0300`. This is a
readability convenience for prose and examples only. `CODE-0300` is shorthand for
`OPERATOR-0300 / CODE`; it is not a distinct identifier.

> CODE-0300 is shorthand only. The full record should preserve actor_class,
> authority_level, authority_domain, and delegation_scope separately.

Throughout these docs, examples may use the shorthand for brevity, but the canonical
full form is authoritative. Tooling and schema should store the four fields
separately and never rely on parsing a folded label.

Worked identifier examples:

| Identifier | actor_class | authority_level | authority_domain | delegation_scope |
|------------|-------------|-----------------|------------------|------------------|
| `OPERATOR-0100 / DEPLOYMENT / PROD` | OPERATOR | 0100 | DEPLOYMENT | PROD |
| `OPERATOR-0300 / CODE / CORE_REPO` | OPERATOR | 0300 | CODE | CORE_REPO |
| `OPERATOR-0400 / RECORDS / AUDIT_DOCS` | OPERATOR | 0400 | RECORDS | AUDIT_DOCS |
| `ORCHESTRATOR-2000 / WORKFLOW / PROJECT_CLEARWRIGHT` | ORCHESTRATOR | 2000 | WORKFLOW | PROJECT_CLEARWRIGHT |
| `REVIEWER-4000 / CODE_REVIEW / REPO_X` | REVIEWER | 4000 | CODE_REVIEW | REPO_X |
| `WORKER-6000 / CODE / FEATURE_BRANCH` | WORKER | 6000 | CODE | FEATURE_BRANCH |
| `OBSERVER-8000 / AUDIT / GLOBAL_READ` | OBSERVER | 8000 | AUDIT | GLOBAL_READ |

---

## Authority domains

`authority_domain` names the lane an actor controls. Domains are operator-defined;
common examples include:

`COMMAND`, `DEPLOYMENT`, `SECURITY`, `CODE`, `RECORDS` (documentation),
`DATA`, `MODEL_RISK`, `INFRA`, `ENERGY`, `PRODUCT`, `LEGAL`, `FINOPS`,
`WORKFLOW`, `AUDIT`.

A domain authority controls decisions in its lane and only its lane. Holding code
authority does not grant deployment authority. This separation is what keeps the
model honest: no single sub-command actor can unilaterally drive a consequential,
cross-cutting action.

---

## Delegation scope

Authority is granted, not inherent. Each non-root actor is delegated its authority
by a higher authority, within a bounded scope and (optionally) a time limit.

Delegation fields:

- `delegated_by`: the higher-authority actor that granted the authority.
- `delegation_scope`: the bounded grant: which repo, branch, project, or environment.
- `delegation_expires_at`: when the delegation lapses, if time-bounded.

An actor cannot act outside its `delegation_scope`, and it cannot grant authority
it does not itself hold. Delegation chains terminate at `OPERATOR-0000`, the root
authority.

---

## Override rules

A higher authority may override a lower authority within the same domain, and the
command tier may override across domains. Override is governed, logged, and
non-destructive.

- An override never deletes the prior decision. It **supersedes** it.
- A new decision event is created that references the superseded decision.
- The active decision is always unambiguous.
- The audit trail records who changed what, when, why, and under what authority.

See the audit model in [CLEARWRIGHT_PROTOCOL.md](CLEARWRIGHT_PROTOCOL.md) for the superseding
decision fields (`decision_id`, `supersedes_decision_id`, `active_decision`,
`override_reason`, `overridden_by`).

---

## Equal authority conflicts

When two actors of equal authority disagree (for example two `OPERATOR-0002`
actors), authority level alone does not resolve it. The protocol resolves the tie
by checking, in order:

1. **Explicit assignment**: was one actor explicitly assigned this decision?
2. **Domain ownership**: does the decision fall in one actor's `authority_domain`?
3. **Channel ownership**: does one actor own the channel or resource in question?
4. **Active lease**: does one actor hold an active, valid CTA lease on the resource?
5. **Priority**: does policy let priority break the tie for scheduling purposes?
6. **Escalation**: if still unresolved, escalate to the next higher authority
   (for two `OPERATOR-0002` actors, that is `OPERATOR-0001`).

Authority level decides decision rights. It does not, by itself, break ties
between equals.

---

## Cross-domain conflicts

When authorities in different domains conflict: code says ready, deployment says
the release lane is blocked, documentation requires corrections. No domain wins
by numeric comparison alone. The protocol routes the conflict to a policy engine
or a command-tier actor according to policy.

The default disposition is conservative: if any domain authority required for an
action issues a DTA within its lane, the action does not proceed until the
conflict is resolved or a command-tier actor supersedes the decision (and is
logged doing so).

---

## Escalation path

Escalation moves upward only as far as necessary, not as high as possible.

1. A **worker** resolves with a peer or a reviewer.
2. A **reviewer** resolves by validation, DTA, or a Request for Information (RFI).
3. An **orchestrator** resolves workflow sequencing and channel contention.
4. A **domain authority** resolves lane-specific decisions.
5. A **policy engine or command-tier actor** resolves cross-domain conflict.
6. The **root operator** resolves only final, emergency, or otherwise unresolved
   high-risk matters.

> Escalation should move upward only as far as necessary, not as high as possible.

---

## Examples

**Example 1: routine docs, no human needed.**
`WORKER-6000` requests an RTA for a `DOCS_ONLY` `CTA-L2000` on a documentation
file. `REVIEWER-4000` (or `ORCHESTRATOR-2000`) sees the channel is clear and policy
allows routine docs work. `CTA-L2000` is granted for 30 minutes. No human approval
is required.

**Example 2: merge candidate escalates to domain authority, not root.**
`WORKER-6000` requests an RTA for a `MERGE_CANDIDATE` `CTA-L7000`. `REVIEWER-4000`
validates and recommends CTA. Policy says final merge clearance requires
`OPERATOR-0300` or `OPERATOR-0100` depending on release stage. The request
escalates to the proper domain authority, not directly to `OPERATOR-0000`.

**Example 3: cross-domain: code approved, deployment denied.**
`OPERATOR-0300` says the code is ready. `OPERATOR-0100` blocks deployment because
the release lane is not clear. The deployment DTA controls deployment, but it does
not erase the code approval. The packet records both decisions; both remain in the
audit trail.

**Example 4: documentation gate on release.**
`OPERATOR-0400` requires a documentation correction. `OPERATOR-0300` cannot ignore
the documentation issue if release policy requires docs clearance. The protocol
routes the conflict to the right domain authority or escalates.

**Example 5: command-tier STOP.**
`OPERATOR-0001` issues STOP on all active work for a packet. All domain actors and
agents comply, because 0001 is command tier. The prior decisions remain in the
audit trail; they are superseded, not deleted.

**Example 6: equal authority disagreement.**
Two `OPERATOR-0002` actors disagree. Neither wins by `authority_level` alone. The
protocol checks explicit assignment, domain ownership, channel ownership, active
lease, and priority; if still unresolved, it escalates to `OPERATOR-0001`.

**Example 7: priority is not authority.**
A high-priority request from `WORKER-6000` does not override a lower-priority
decision from `ORCHESTRATOR-2000`. Priority affects scheduling. Authority affects
decision rights.

**Example 8: a hyperscale deployment needs many domain clearances.**
A hyperscale AI operator wants to deploy a new agent workflow. The request may
require, in parallel and by lane:

| Clearance | Meaning |
|-----------|---------|
| `CODE-0300` | Implementation is acceptable |
| `MODEL_RISK-0600` | Model behavior passed evaluations |
| `DATA-0500` | Data access is allowed |
| `SECURITY-0200` | Secrets and access are safe |
| `INFRA-0700` | Compute capacity is available |
| `ENERGY-0705` | Power and cooling window is acceptable |
| `FINOPS-0900` | Spend threshold is acceptable |
| `DEPLOYMENT-0100` | Release window is clear |
| `COMMAND-0001` | Only if the action is high-risk or a conflict escalates |

This is why one flat "approve" button is not enough. The action is cleared only
when each required lane is clear; any lane can issue a DTA that holds the action
until resolved. Command tier is consulted only on high risk or unresolved conflict.

---

## Future schema fields

The authority model is documented ahead of full schema implementation. The
following fields are **documented here as direction; they are not all implemented
in the current schema.** The authority model above lists which authority columns exist today.

Per-actor fields:

- `actor_id`
- `actor_class`
- `authority_level`
- `authority_band`
- `authority_domain`
- `delegated_by`
- `delegation_scope`
- `delegation_expires_at`
- `override_scope`
- `active_status`

Per-decision fields (see CLEARWRIGHT_PROTOCOL.md audit model):

- `decision_id`
- `supersedes_decision_id`
- `active_decision`
- `override_reason`
- `overridden_by`

No schema change is made in this documentation PR beyond what the current schema already
records.
