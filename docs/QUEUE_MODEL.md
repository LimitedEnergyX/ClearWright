# ClearWright&trade;: Clearance Queue Lifecycle

This document defines the filesystem queue model for clearance packets in ClearWright&trade;.
The queue is the durable layer. The SQLite registry (see `schema/clearance_packet.sql`)
indexes packet state but does not replace the packet files.

---

## The four queue directories

| Directory | Meaning |
|-----------|---------|
| `clearance_outbox/` | Available for the next controlled step. Not approved work, awaiting review or claim. |
| `clearance_in_progress/` | Claimed. Presence here means a claim exists, not that execution is actively running. |
| `clearance_done/` | Terminal successful outcome: CTA executed to completion, or DTA issued. |
| `clearance_failed/` | Execution failed after a claim was made. DTA packets never go here. |

### clearance_outbox

A packet in `clearance_outbox/` is available for the next controlled step.
That step may be operator review, consensus review, or claim by an authorized agent.
The outbox does not imply any approval has been granted.
A packet in the outbox holds a pre-claim status in the SQLite registry: `RTA`,
`IN_REVIEW`, `RFI_PENDING`, or `CTA`. `RFI_PENDING` (waiting on clarification
before work can proceed) and `CTA` (cleared to act, but not yet claimed) remain
in the outbox until an authorized agent claims the packet and moves it to
`clearance_in_progress/`. The clearance is recorded; the packet has not moved.

### clearance_in_progress

A packet in `clearance_in_progress/` has been claimed.
Presence here means a claim record exists (`claimed_by`, `claimed_at`,
`claim_expires_at` are set).
It does not mean execution is actively running. The claiming agent may be
preparing, queued, or waiting on a dependency.
Status in the registry: `IN_PROGRESS`.

### clearance_done

A packet moves to `clearance_done/` when it reaches a closed successful outcome:

- A CTA was issued, a claim was made, and execution completed successfully (`DONE`).
- A DTA was issued (`DTA`). DTA is a successful safety outcome. The channel
  was not clear and the system correctly denied or deferred the action. A DTA
  may be issued by an agent, reviewer, arbiter, policy rule, or the operator.
  The system worked as intended.

DTA packets belong in `clearance_done/`, not `clearance_failed/`.

### clearance_failed

A packet moves to `clearance_failed/` when execution failed after a claim was made.
This covers runtime errors, timeouts, and unrecoverable processing failures that
occurred during the `IN_PROGRESS` phase.
Status in the registry: `FAILED`.

DTA is never `FAILED`.
FAILED means the execution broke, not that the operator said no.

---

## Move-first / read-second claim model

Claiming a packet is a two-step operation:

1. **Move** the packet file from `clearance_outbox/` to `clearance_in_progress/`.
2. **Read** the packet after the move to confirm the claim succeeded.

The move is the claim.
Filesystem moves within the same volume are atomic on most operating systems,
so only one agent can successfully move a given packet.
An agent that attempts to move a file that is already gone knows another agent
claimed it first and must back off.

Rules:

- Never read a packet from the outbox and then decide to claim it separately.
  Another agent may claim it between the read and the intended move.
- Never update SQLite before the file move succeeds.
  The file move is the ground truth; the SQLite update follows.
- The SQLite transition (`status` to `IN_PROGRESS`, claim fields set) must happen
  inside a single `BEGIN IMMEDIATE` transaction, executed after the file move.

### Claiming a packet (tooling)

`tools/clearwright_claim.py` performs the single-packet claim move described
above. It moves one packet from `clearance_outbox/` to `clearance_in_progress/`,
sets its status to `IN_PROGRESS`, and updates `source_path`. It validates the
packet before and after the move and fails safely, leaving the source unchanged,
on any error. The source is removed only after the destination has been written
(with an exclusive create) and re-validated from disk, so a failure at any
earlier step leaves the original packet in `clearance_outbox/` untouched and
creates no destination.

```sh
# Validate and report the intended claim without moving anything
python tools/clearwright_claim.py --dry-run \
    examples/queue/clearance_outbox/<packet>.json

# Claim the packet (optionally recording who claimed it)
python tools/clearwright_claim.py --claimant agent/worker-1 \
    examples/queue/clearance_outbox/<packet>.json
```

This is the minimal claim step only: it claims exactly one named packet. The
destination is created exclusively and is never overwritten; if the destination
already exists, the claim is refused.

Only `RTA`, `IN_REVIEW`, `RFI_PENDING`, or `CTA` packets in `clearance_outbox/`
may be claimed. `DTA`, `DONE`, `FAILED`, `SUPERSEDED`, and `IN_PROGRESS` are
refused. `DTA` is a successful safety outcome, not a failure, and is never
treated as failure handling by this tool.

A claimable packet must also be able to validate as `IN_PROGRESS` after the move.
Because an `IN_PROGRESS` packet requires a clearance lease (`clearance_expires_at`),
a bare `RTA` with no lease is refused: clear it first (a `CTA` carries the lease)
or the post-move validation rejects it and the source is left untouched.

The tool deliberately does not:

- iterate directories or discover packets (it acts on exactly one named file)
- process batches
- run as a daemon, schedule, or assign workers beyond an optional `claimed_by`
- touch `clearance_failed/` or route anything to `FAILED`

The example paths above are illustrative, since runtime packets are not
committed to the repository.

### Lifecycle control after claim (tooling)

Once a packet has been claimed into `clearance_in_progress/`, the next controlled
steps are manual. `tools/clearwright_lifecycle.py` is the operator surface for
them. It is a manual tool, not a background worker: it runs no daemon, no
scheduler, no automatic retry, no automatic requeue, and no Discord integration.

It offers five subcommands:

- `inspect`: read one packet and summarize its lifecycle state. Read-only.
- `complete`: move one `IN_PROGRESS` packet to `clearance_done/` and set status
  `DONE`. Acts on exactly one named packet.
- `fail`: move one `IN_PROGRESS` packet to `clearance_failed/` and set status
  `FAILED`. Requires a non-empty `--reason`. Acts on exactly one named packet.
- `stale`: read-only scan of a directory (normally `clearance_in_progress/`) for
  stale or invalid active packets.
- `status`: read-only report of counts and health across the four queue dirs.

`complete` and `fail` accept only a packet that is physically in
`clearance_in_progress/` with status `IN_PROGRESS`. They validate before and
after the move, write the destination with an exclusive create (never
overwriting), re-validate it from disk, and remove the source only last, so a
failure at any earlier step leaves the original packet in place. Each supports
`--dry-run` and an optional `--actor`.

`FAILED` means execution or processing failure only. A `DTA` is a successful
safety and governance outcome and is never routed to `clearance_failed/` by this
tool; a `SUPERSEDED` packet is a closed replacement, not a failure. `DEFER` and
`FREEZE` are not packet statuses. Because `complete` and `fail` require an
`IN_PROGRESS` source in `clearance_in_progress/`, a `DTA`, `DONE`, `FAILED`, or
`SUPERSEDED` packet cannot be completed or failed by this tool.

`stale` and `status` are read-only: they never write, move, rename, or delete a
packet, never persist metrics, and never auto-correct paths. `stale` flags a
packet whose `claim_expires_at` or `clearance_expires_at` is earlier than the
current UTC time. A packet with no `claim_expires_at` does not expire (see
"Stale lock handling" below). Recovery of a stale packet (moving it back to the
outbox) remains a reviewed human or recovery step and is not performed by this
tool.

```sh
# Inspect one active packet (read-only)
python tools/clearwright_lifecycle.py inspect \
    examples/queue/clearance_in_progress/<packet>.json

# Preview a completion, then complete it
python tools/clearwright_lifecycle.py complete \
    examples/queue/clearance_in_progress/<packet>.json --dry-run
python tools/clearwright_lifecycle.py complete \
    examples/queue/clearance_in_progress/<packet>.json

# Fail one active packet (reason is required)
python tools/clearwright_lifecycle.py fail \
    examples/queue/clearance_in_progress/<packet>.json --reason "execution failed"

# Read-only scans
python tools/clearwright_lifecycle.py stale examples/queue/clearance_in_progress/
python tools/clearwright_lifecycle.py status examples/queue/
```

The example paths above are illustrative: runtime packets are local and are not
committed to the repository.

---

## Stale lock handling

A packet is stale when it remains in `clearance_in_progress/` past its
`claim_expires_at` timestamp with no evidence of completion.
Stale packets are a recovery condition, not a normal workflow state.

When a stale packet is detected:

1. Do not automatically retry execution. Treat the packet as suspect.
2. Log the stale condition to the audit trail (`audit_json`).
3. Move the packet back to `clearance_outbox/` only after a human operator or an
   authorized recovery process has reviewed the situation.
4. Update the SQLite registry to reflect the rollback (status back to `RTA` or
   `IN_REVIEW`) and clear the claim fields.

A packet with no `claim_expires_at` (NULL) does not expire and will not be flagged
as stale by time-based checks.
This is appropriate for synchronous or supervised claims where the operator is
actively watching.

---

## Archive rules

Packets are never deleted from the queue directories.
All terminal-state packets remain in place as a permanent, inspectable audit record.

| Terminal state | Directory | Deletable? |
|----------------|-----------|------------|
| `DONE` | `clearance_done/` | No |
| `DTA` | `clearance_done/` | No |
| `FAILED` | `clearance_failed/` | No |
| `SUPERSEDED` | `clearance_done/` | No |

If storage becomes a concern, packets may be compressed or cold-archived to an
offline store, but the original files must remain restorable.
The SQLite registry can be reconstructed entirely from the packet files;
losing the files loses the audit trail.

---

## Queue inspection rules

Queue inspection tools (listing packets, reading state, reporting counts) must never
claim or mutate packets as a side effect of inspection.

- A read is a read. It changes nothing.
- Listing the contents of `clearance_outbox/` does not reserve or claim any packet.
- Counting `clearance_in_progress/` entries is informational only.
- Inspection commands must be safe to run at any time without side effects.

---

## Canonical queue paths and path validation

The four queue directories live under `examples/queue/`:

```
examples/queue/clearance_outbox/
examples/queue/clearance_in_progress/
examples/queue/clearance_done/
examples/queue/clearance_failed/
```

Each directory is tracked in Git by a `.gitkeep` file so the channel exists in
the repository. The packet files themselves are local runtime data: they are
gitignored and are never committed. The `.gitkeep` keeps the empty channel under
version control without committing any packet.

These directories establish channels. They do not, by themselves, create runtime
behavior. Creating them does not move, claim, or populate packets.

### Status to directory mapping

| Directory | Valid statuses |
|-----------|----------------|
| `clearance_outbox/` | `RTA`, `IN_REVIEW`, `RFI_PENDING`, `CTA` (pre-claim) |
| `clearance_in_progress/` | `IN_PROGRESS` (claimed) |
| `clearance_done/` | `DONE`, `DTA`, `SUPERSEDED` (closed successful outcomes) |
| `clearance_failed/` | `FAILED` (execution or processing failure only) |

`DTA` is a successful safety outcome and belongs in `clearance_done/`, never in
`clearance_failed/`. `SUPERSEDED` is a closed replacement, not a failure.
`clearance_failed/` is for execution or processing failure after a claim only.

### Validating packet placement

The packet validator can check this mapping. Normal validation checks packet
field rules only:

```sh
python tools/clearwright_validate.py schema/examples/clearance_packet.example.json
```

With `--strict-path`, it also checks that the packet file lives in a known queue
directory and that its status is valid for that directory:

```sh
# Illustrative: queue directories hold local runtime packets that are not
# committed, so substitute a real packet path when running this.
python tools/clearwright_validate.py --strict-path \
    examples/queue/clearance_outbox/<packet>.json

# Optionally anchor the check to a queue root:
python tools/clearwright_validate.py --strict-path --queue-root orchestrator \
    examples/queue/clearance_outbox/<packet>.json
```

Strict-path validation is read-only. It inspects the path and the status and
reports whether they are compatible. It does not move packets, does not claim
packets, and does not mutate any state. Queue inspection and validation never
claim a packet as a side effect.

---

## Relationship to the SQLite registry

The filesystem queue is the source of truth.
The SQLite registry (`schema/clearance_packet.sql`) is an index that makes querying
faster and supports atomic claim transitions.

If the registry and the filesystem diverge:

- The filesystem wins.
- The registry should be rebuilt from packet files, not the other way around.
- Divergence should be logged and reported; it is a signal of a process error.

---

## What this document does not cover

- Python implementation of the queue worker. Deferred to a later change.
- Runtime integration with the orchestrator. Deferred to a later change.
- Multi-agent claim coordination beyond move-first/read-second. Future ADR.
- Runtime population and movement of packets between queue directories. The
  canonical directories now exist as Git-tracked channels with `.gitkeep` (see
  "Canonical queue paths and path validation" above), but claiming, moving, and
  populating packets remains runtime behavior deferred to a later PR.
