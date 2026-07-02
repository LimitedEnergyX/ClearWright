# ClearWright Local Repo Profile v0.1

**Status:** Accepted

**Date:** 2026-06-30

**Deciders:** Shawn C. Tovey

**Related:** [CLEARWRIGHT_PROTOCOL.md](CLEARWRIGHT_PROTOCOL.md), [AUTHORITY_MODEL.md](AUTHORITY_MODEL.md), [QUEUE_MODEL.md](QUEUE_MODEL.md)

---

## Context

ClearWright&trade;'s ClearWright Protocol now has working local foundations: JSON clearance packets
and a schema validator, the four-directory filesystem queue with path and status
validation, and a single-packet claim tool that moves one packet from the outbox
to in-progress with validation before and after the move. These were built in a
series of small, governed PRs and are documented in
[CLEARWRIGHT_PROTOCOL.md](CLEARWRIGHT_PROTOCOL.md), [QUEUE_MODEL.md](QUEUE_MODEL.md),
[AUTHORITY_MODEL.md](AUTHORITY_MODEL.md), and the `schema/` and `tools/`
directories.

Before adding more behavior (daemon workers, batch processing, a policy engine,
signing), we pin the first enforceable profile. The purpose is to bound
complexity and make the policy boundary explicit: a reader should be able to tell,
without reading the code, exactly what the local implementation guarantees today
and what it deliberately defers. This profile changes no code, schema, CI, or queue
behavior. It records a boundary.

The profile is local-first and single-machine. The packet files are the record;
the SQLite registry is an index (see [QUEUE_MODEL.md](QUEUE_MODEL.md)).

---

## Decision

We define the **ClearWright Local Repo Profile v0.1**, the first enforceable
ClearWright Protocol profile. It names what is in scope now, what is out of scope now, the
default policy rules and which of them are enforced in code today, the priority
semantics, the channel taxonomy, and the enforcement mechanisms now and later.

### In scope now

- **JSON clearance packets**: the packet shape in
  [schema/examples/clearance_packet.example.json](../schema/examples/clearance_packet.example.json),
  validated by `tools/clearwright_validate.py`.
- **Filesystem queue directories**: `clearance_outbox/`, `clearance_in_progress/`,
  `clearance_done/`, `clearance_failed/` under `examples/queue/` (see [QUEUE_MODEL.md](QUEUE_MODEL.md)).
- **Single-packet claim**: `tools/clearwright_claim.py` claims exactly one named
  packet, moving it from `clearance_outbox/` to `clearance_in_progress/`.
- **Validate before and after the move**: the claim is refused unless the packet
  validates beforehand and the resulting `IN_PROGRESS` packet validates after.
- **Bounded leases**: a CTA carries a `clearance_expires_at`. A CTA is a
  time-boxed lease, not a blank check.
- **Human and operator final authority**: the operator remains the highest
  authority and final override (see [AUTHORITY_MODEL.md](AUTHORITY_MODEL.md)).
- **The full packet status set**: `RTA`, `IN_REVIEW`, `RFI_PENDING`, `CTA`,
  `DTA`, `IN_PROGRESS`, `DONE`, `FAILED`, `SUPERSEDED`. All nine are in scope;
  `IN_REVIEW` and `RFI_PENDING` are already part of shipped claim behavior (they
  are valid pre-claim outbox states) and are not reserved.
- **DTA as successful governance**: a DTA is a successful safety and governance
  outcome, not a failure. It archives to `clearance_done/`, never to
  `clearance_failed/`.

### Out of scope now

None of the following are part of v0.1. Each requires a new ADR or a profile bump:

- daemon workers
- batch processing
- automatic retry
- a generalized policy engine
- cryptographic signing
- cross-domain arbitration automation
- metrics feedback loops
- a production scheduler

### Default policy rules

These are the v0.1 default policy rules. The protocol asserts all of them, but
only some are enforced in code today. The rest are profile requirements that the
current tooling does not yet check; they are held by review, process, and human
judgment until a later profile implements them. Do not assume a rule is enforced
in code unless this table says so.

| Rule | Enforcement in v0.1 |
|------|---------------------|
| **Default deny** | Profile posture. The current tools embody it by refusing rather than proceeding on invalid, malformed, or non-claimable input. A general default-deny policy engine across all action types is future. |
| **Malformed packets refused** | Enforced in code today by the validator and the claim tool (JSON parse and required-field checks). |
| **Unknown status refused** | Enforced in code today by the validator status-enum check and the claim tool. |
| **Expired lease invalid** | Profile requirement, not yet enforced in code. The validator checks that `clearance_expires_at` is present for `CTA` and `IN_PROGRESS`; it does not yet compare the timestamp to the current time. Expiry evaluation is future. |
| **CTA cannot expand scope** | Profile requirement, not yet enforced in code. A CTA must never be silently broadened beyond its original scope (see CLEARWRIGHT_PROTOCOL.md); this is held by review today. |
| **Cross-domain conflict escalates** | Profile requirement, enforced by process today. Escalation is a human and operator step (see [AUTHORITY_MODEL.md](AUTHORITY_MODEL.md)), not code-arbitrated. |
| **Source removed only after destination write and re-validation** | Enforced in code today by the claim tool: the source is removed only after the destination is exclusively written and re-validated from disk. |

### Priority semantics

`priority_level: 0` means highest urgency. Larger numbers mean lower urgency.

This profile pins a direction that the protocol docs previously left
organization-defined (GLOSSARY.md and CLEARWRIGHT_PROTOCOL.md state the organization must
document whether higher or lower means more urgent). The chosen direction aligns
with `authority_level`, where a lower number means greater authority. Note that
`priority_level` is metadata in v0.1: no scheduler acts on it (a production
scheduler is out of scope).

### Channel types

The profile names seven channel types as its taxonomy: `queue`, `file`, `repo`,
`worktree`, `human_attention`, `compute`, `deployment`.

**Only `queue` is implemented today.** It is realized by the filesystem dispatch
directories and the claim and validate tools. The other six (`file`, `repo`,
`worktree`, `human_attention`, `compute`, `deployment`) are reserved profile
vocabulary: named for consistency and future use, not implemented behavior.
Referencing them does not imply any tooling, enforcement, or runtime exists for
them in v0.1.

### Enforcement today

- **git review**: PR-only changes into a protected `main`.
- **CI checks**: `py_compile`, the help smoke tests, and the claim-tool unit tests.
- **human merge gate**: no agent merges its own PR; the operator approves merges.
- **explicit stop conditions**: the governed PR flow halts on ambiguity or on any
  boundary violation rather than guessing.
- **tool-level validation**: `tools/clearwright_validate.py` and the validate
  steps inside `tools/clearwright_claim.py`.

### Enforcement later

- sandboxed workers
- signed packets (cryptographic signing)
- append-only audit logs
- a policy engine
- token-scoped execution

---

## Consequences

- The profile is the enforceable boundary for v0.1. Anything in the out-of-scope
  or enforcement-later lists is not part of the current guarantee and requires a
  new ADR or a profile bump.
- DTA remains a successful governance outcome, archived to `clearance_done/`, never
  `clearance_failed/`.
- The `priority_level` direction is now pinned for this profile (`0` is highest).
- Only the `queue` channel is implemented; the other six channel types are
  reserved vocabulary and must not be treated as working behavior.
- Several default policy rules (expired lease invalid, CTA cannot expand scope,
  cross-domain conflict escalates, and the general default-deny posture) are
  profile requirements held by review and process today, not code-enforced.
  Future implementers must not assume the tooling already checks them.
- This profile records existing behavior and the intended boundary. It changes no
  code, schema, CI, or queue behavior.

## What this profile does not decide

- The implementation of any out-of-scope item (daemon workers, batch processing,
  automatic retry, the policy engine, signing, cross-domain arbitration, metrics
  loops, the scheduler). Each is a future ADR or PR.
- Implementation of any channel type beyond `queue`.
- The lease-expiry evaluation mechanism, the CTA scope-broadening check, and the
  cross-domain arbitration logic. These are named here as profile requirements
  but their implementation is future work.
- The on-disk format of signed packets, append-only audit logs, or the policy
  engine.
- Any profile beyond v0.1.
