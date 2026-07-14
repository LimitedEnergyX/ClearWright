# Archive operation

The archive layer moves old terminal records (and smoke/proof runs regardless
of age) out of the active queue into `runtime/queues/archive/<YYYY-MM>/`, so
the Local Council site shows only live and recent work, while every byte is
preserved and every id stays resolvable through an archive-aware read
fallback. This document is the operator runbook: policy, the approval flow,
the confirmation conditions, and recovery. See `tools/clearwright_archive.py`
for the implementation and `tests/test_archive.py` and `tests/test_writer_lock.py`
for the regression coverage this document describes.

This is early alpha software and a local reference implementation.

## Retention policy

Keep active:

- all nonterminal work (open, claimed, or carrying an unresolved plan gate)
- terminal operational work younger than 72 hours
- the latest five genuine completed operational runs, even if older
- any record the operator has pinned (`archive pin --id <id>`)

Archive:

- older terminal work
- smoke and proof runs, **regardless of age** — a smoke-labeled thread from an
  hour ago still archives; recency never protects it, only a pin does
- the records named in the checked-in approved inventory
  (`tests/fixtures/archive_inventory.json`)

A "genuine completed operational run" is a terminal work item with a bound
council or a progress/result message whose originating message does not match
the smoke keyword set (`smoke`, `proof`, `e2e`, `harness check`,
`adapter-fix`, word-boundary matched). A "smoke/proof run" is a keyword match
on the originating message, or explicit listing in the approved inventory.

## The approved inventory is an upper bound, not a source of truth

At `dry-run` and `execute`, every retention class is **recomputed from live
state**. A record moves only when it is BOTH in the approved inventory AND
still currently eligible under that live recomputation:

- a live-computed candidate that is **not** in the approved inventory stops
  the run entirely and reports the exact ids under `extra`, for operator
  review — the run never silently expands beyond what was approved
- an approved record that no longer qualifies (for example, it was pinned
  after the inventory was generated) is **skipped**, not treated as an error,
  and reported under `skipped_not_qualifying`

**Re-approving a changed inventory**: run `dry-run`, review the reported
`extra` ids, and if they should be archived, generate a new
`archive_inventory.json` (a schema-versioned, content-hashed JSON file: see
`clearwright_archive.build_inventory`) covering the full current candidate
set, and commit it. The shipped inventory is never evergreen; each new
archive operation is reviewed against live state, not against last month's
approval.

## Zero data loss

No record's content is ever discarded. Archive moves preserve **content
bytes** exactly at the archive path — not filesystem metadata, ACLs,
timestamps, or alternate data streams, which the move does not attempt to
preserve. The guarantee rests on the filesystem's rename/durability
guarantees plus the detection-and-recovery protocol below, not on protection
against arbitrary storage corruption.

Every move is journaled and hash-verified: before any file is renamed, its
current content is re-hashed and must match the hash recorded when the plan
was generated (a source that drifted between planning and execution halts,
rather than moving stale content); after the rename, the destination is
re-hashed again. A destination that already exists is a hard stop — the code
contains no path that overwrites or deletes a source file.

## The hash-bound approval flow

Archive execution is destructive, so it requires an approval that names the
**exact** move plan, not just "archive whatever qualifies right now":

1. `archive dry-run <queue_root>` computes the current plan and its full
   SHA-256 hash (`plan_hash`). Nothing moves.
2. The operator reviews the reported inventory (record and file counts, the
   confirmation conditions below) and, if it is correct, posts a durable
   inbound message (actor `OPERATOR-0001`) that contains that exact
   `plan_hash`.
3. The operator runs `archive approve <queue_root> --plan-hash <hash>
   --operator-message-id <id>`, which writes an approval record under
   `operator_authority/archive-approvals/`.
4. `archive execute <queue_root>` requires, in order: the committed inventory
   artifact's hash to verify; exactly one eligible approval whose
   `approved_plan_sha256` matches the **freshly recomputed** plan hash (not
   the hash from step 1 — the plan is recomputed again under exclusivity
   immediately before moving, and any drift since the approval was written
   halts the run); the approval's referenced operator message to resolve and
   contain that hash. Only then does the journal exist and moves begin.

**The API/server surface cannot create, edit, revoke, or select an approval.**
There is no HTTP route under `operator_authority/`, and no server code path
calls `write_approval`, `revoke_approval`, or `clearwright_archive.execute`
(`tests/test_archive.py::NoServerWriteRouteTests` asserts this by scanning
`server.py`). An approval can only be written by the `archive approve` CLI
command or by direct operator filesystem placement.

### The honest boundary

This is an **enforced** control against the network/API agent surface — the
only interfaces most agent activity in this system uses. It is **not** an
OS-level security control. ClearWright is a stdlib-only local Python tool with
no authentication subsystem, running as the same Windows user as anything
else on the machine; a process with local filesystem or CLI access under that
same user can, in principle, write an approval file directly or edit queue
files outside ClearWright entirely. ClearWright governs the paths it owns
(gates, approvals, the archive, message writes) and stops its own governed
workflow completely, but it cannot physically prevent an out-of-band,
same-user file edit. The operator has explicitly accepted this residual for
this local, single-user alpha; it is documented here as an accepted
limitation, not represented anywhere as a completed OS-level control.

## Writer/archive exclusion

A durable writer and an active archive operation cannot race. Every durable
writer (message writes, gate writes, council persistence) acquires a
short-lived token before mutating; `archive execute` acquires exclusivity only
when no live-or-indeterminate token remains, and both sides check-and-set
under one short-held mutex so neither can observe a stale view of the other.

Liveness is conservative by construction (`clearwright_writer_lock.liveness`):
a token or the exclusive flag is swept **only** on confirmed process
non-liveness (same host, and either the PID is dead or it was reused by a
different process, detected via a process-start-time mismatch). Age alone
never sweeps anything — a genuinely long-running writer keeps its protection
for as long as it is alive, at the cost of an archive run waiting for it
(bounded by `CLEARWRIGHT_ARCHIVE_DRAIN_DEADLINE`, default 60 seconds, after
which the run fails safe with no exclusive flag left rather than forcing
progress). Indeterminate liveness (a different host, or the OS liveness query
failing) always fails safe the same way.

A registry lock left behind by a crashed process is itself only ever removed
after confirmed non-liveness of its recorded owner, using the same rule.

## Recovery

Every move is preceded by a durable, fsynced journal
(`pending-<opid>.json` + a per-move completion log) written **before** any
file is renamed. If `archive execute` (or a resume) is interrupted, the next
invocation finds the pending journal and resumes forward-only:

| Observed state | Recovery action |
|---|---|
| source exists, destination missing | re-hash the source against the journal; redo the move if it matches, halt if it does not |
| destination exists, hash matches, source missing | already done; record it complete |
| both source and destination exist | halt (an anomaly that must never happen under the protocol; reported for operator review) |
| any hash mismatch | halt, nothing moved for that record |
| journal missing or truncated | that file is treated as **untouched** — nothing moves without a durable journal |
| journal complete but the manifest or index is missing or torn | rebuild both from the journal and completion log (`clearwright_archive.rebuild_index`) |

Reruns are idempotent: resuming a fully completed journal is a no-op, and the
index can always be rebuilt from the set of completed manifests.

The journal carries the approval lineage (`approval_id` and
`operator_message_id`) alongside `approved_plan_sha256`, so a manifest
finalized by recovery keeps the same manifest-to-approval audit link as an
uninterrupted run.

## Execution runbook (local operator sequence)

The writer/archive exclusion above covers durable **writers**; it does not —
and by design cannot — stop **readers**. On Windows a plain `open()` for
reading blocks `os.rename` on the same file (sharing violation, WinError 32),
so a long-running local reader such as the control-plane server polling the
queue can halt an execute mid-journal. Recovery handles that interruption
cleanly, but the operational path must not depend on discovering it
mid-journal. Execute in this order:

1. **Stop the local control-plane server** (and any other known queue
   readers) before `archive execute`. On Windows this is required, not
   advisory; on POSIX it is still recommended so the operation runs against a
   quiet queue.
2. Run `archive dry-run` and confirm the plan hash matches the recorded
   hash-bound approval exactly.
3. Run `archive execute`.
4. If the run is interrupted for any reason (sharing violation, crash,
   power loss):
   - identify the pending journal: `runtime/queues/archive/<YYYY-MM>/pending-<opid>.json`
     (with its per-move completion log `pending-<opid>.log`);
   - resume it forward-only — the next `archive execute` call runs
     `recover_pending` first, or invoke `clearwright_archive.recover_pending(root)`
     directly; recovery re-verifies every hash and never re-moves a completed
     entry;
   - verify completion: the journal renamed to `completed-<opid>.json`, the
     `manifest-<opid>.json` present with the approved plan hash and approval
     lineage, and `archive status` showing `exclusive: null` (the exclusive
     flag is released even on an interrupted run; a stale flag from a dead
     process is swept on confirmed non-liveness only).
5. Validate integrity before restarting anything: every manifest row's
   `archive_path` exists and hash-verifies, every `original_path` is gone
   from the active queue, and the archive index resolves every archived id.
6. **Restart the local server** and confirm the site reflects the archive:
   archived records labeled in History (`/api/ledger?scope=archived`), the
   Archived queue group populated, and all nonterminal work items unchanged.

## Archive-aware reads

Once a record is archived, `/api/audit`, `/api/messages`, `/api/review-council`,
and `/api/work-summary` still resolve it: the active store is always checked
first, and the archive index is consulted **only** on an active miss. Every
response resolved from the archive is labeled (`archived: true`, or
`source: "archive"`), so a client can never mistake archived data for live
data.

## Log rotation

`invocation_log.jsonl` rotates automatically — on a calendar-month change or
once it exceeds 5&nbsp;MB — into
`runtime/queues/archive/<YYYY-MM>/invocation_log-<YYYY-MM>[-n].jsonl`. There is
no manual rotation path; this is the only mechanism.

## CLI

    python tools/clearwright_archive.py dry-run <queue_root> [--inventory-file PATH] --json
    python tools/clearwright_archive.py execute <queue_root> [--inventory-file PATH] --json
    python tools/clearwright_archive.py approve <queue_root> --plan-hash <sha256> --operator-message-id <id> --operator NAME --json
    python tools/clearwright_archive.py revoke <queue_root> --approval-id <id> --json
    python tools/clearwright_archive.py pin <queue_root> --id <id> --json
    python tools/clearwright_archive.py unpin <queue_root> --id <id> --json
    python tools/clearwright_archive.py status <queue_root> --json

See [USE_CW.md](USE_CW.md) for the plan-gate and Review Council mechanisms the
archive layer shares its writer-exclusion primitives with, and
[LOCAL_COMMUNICATIONS.md](LOCAL_COMMUNICATIONS.md) for message payload
integrity.
