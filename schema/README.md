# ClearWright Protocol, Schema

This directory contains the SQLite schema for the ClearWright ClearWright Protocol packet registry.

## Purpose

The registry indexes the state of clearance packets. It does not replace or duplicate the
durable packet artifacts stored in the clearance queue directories. The database is
reconstructable from packet artifacts at any time.

For the filesystem queue model that this registry indexes, see
[docs/QUEUE_MODEL.md](../docs/QUEUE_MODEL.md).

## Files

| File | Description |
|------|-------------|
| `clearance_packet.sql` | `CREATE TABLE`, indexes, and update trigger for the `clearance_packet` registry |
| `examples/clearance_packet.example.json` | Concrete sample of a clearance packet in its JSON shape |

## Status lifecycle

DTA is a successful safety outcome, the operator explicitly rejected the action.
FAILED means execution failed after CTA was issued and a claim was made.

```
RTA -> IN_REVIEW -> CTA -> IN_PROGRESS -> DONE
         |           |
         |           +-> FAILED
         |
         +-> RFI_PENDING -> IN_REVIEW  (loop until resolved)
         |
         +-> DTA        (terminal, operator denied; successful safety outcome)
         |
         +-> SUPERSEDED (terminal, replaced by a newer packet)
```

## Core principles

1. **SQLite indexes packet state.** The durable clearance packet remains the record.
2. **The database is reconstructable from packet artifacts.** It is an index, not an archive.
3. **No packet is deleted.** Terminal states: `DTA`, `DONE`, `FAILED`, `SUPERSEDED`.
4. **Atomic CTA + claim.** Transitioning a packet to `IN_PROGRESS` (claim) must happen
   atomically with the `CTA` transition inside a single `BEGIN IMMEDIATE` transaction.
   Claim fields (`claimed_by`, `claimed_at`, `claim_expires_at`) are set in the same write.
5. **Agent-issued clearance.** CTA and DTA may be issued by agents, reviewers, arbiters,
   policy rules, or the operator, depending on authority class and delegated policy.
   `cleared_by` and `denied_by` record who issued the decision. See ADR-0006.
6. **Operator is highest authority.** The operator may override any CTA or DTA at any time.

## Validation

Run these commands from the repo root to verify the schema and example are well-formed:

```sh
# Validate SQL: load schema into an in-memory SQLite database
sqlite3 :memory: < schema/clearance_packet.sql

# Validate JSON: parse and pretty-print the example packet
python -m json.tool schema/examples/clearance_packet.example.json

# Validate a clearance packet against the ClearWright Protocol v0.1 field rules
python tools/clearwright_validate.py schema/examples/clearance_packet.example.json
```

All commands should exit cleanly with no errors.

The packet validator (`tools/clearwright_validate.py`) checks required fields,
allowed status enum values, and JSON blob field types. Exit codes: 0 = valid,
1 = invalid packet, 2 = file or parse error.

Pass `--strict-path` for optional queue-placement validation. In that mode the
validator also checks that the packet file lives in a known clearance queue
directory (`clearance_outbox`, `clearance_in_progress`, `clearance_done`,
`clearance_failed`) and that its status is valid for that directory, per
[docs/QUEUE_MODEL.md](../docs/QUEUE_MODEL.md). It is read-only: it does not
move or claim packets. Behavior without `--strict-path` is unchanged.

To claim a packet (move it from `clearance_outbox` to `clearance_in_progress` and
set status `IN_PROGRESS`), use `tools/clearwright_claim.py`; it validates before
and after the move and fails safely. See
[docs/QUEUE_MODEL.md](../docs/QUEUE_MODEL.md).

After a packet is claimed, `tools/clearwright_lifecycle.py` provides manual
lifecycle control: `inspect` (read-only summary), `complete` (move one
`IN_PROGRESS` packet to `clearance_done` as `DONE`), `fail` (move one
`IN_PROGRESS` packet to `clearance_failed` as `FAILED`, reason required), and the
read-only `stale` and `status` scans. It is a manual operator tool with no
daemon, scheduler, retry, requeue, or Discord behavior. `FAILED` is execution or
processing failure only; a `DTA` is a successful governance outcome and is never
routed to `clearance_failed`. See
[docs/QUEUE_MODEL.md](../docs/QUEUE_MODEL.md).

## Migration notes

This is v0.1. The table schema may gain columns in future PRs. Use
`ALTER TABLE clearance_packet ADD COLUMN` for additive changes. Destructive changes
require a new migration file and an ADR update.

## New fields (v0.1, PR #21)

The schema gained authority and coordination fields in PR #21 (see ADR-0006):

| Field | Type | Purpose |
|-------|------|---------|
| `authority_class` | TEXT | Authority tier of requesting actor (OPERATOR / ORCHESTRATOR / REVIEWER / WORKER / OBSERVER / POLICY_ENGINE) |
| `clearance_class` | TEXT | Scope of clearance (READ_ONLY / DOCS_ONLY / BRANCH_CODE / QUEUE_MOVE / EXECUTION_CANDIDATE / HUMAN_REQUIRED) |
| `priority_class` | TEXT | Scheduling priority (LOW / NORMAL / HIGH / URGENT) |
| `channel_id` | TEXT | Logical workflow channel |
| `channel_state` | TEXT | Channel readiness (CLEAR / BUSY / BLOCKED / STALE / ESCALATED) |
| `cleared_by` | TEXT | Actor that issued CTA |
| `denied_by` | TEXT | Actor that issued DTA |
| `delegated_by` | TEXT | Higher-authority actor that authorized delegation |
| `clearance_expires_at` | TEXT | CTA lease expiry (distinct from `claim_expires_at`) |
| `escalation_required` | INTEGER | 1 = operator approval required |
| `escalation_reason` | TEXT | Why escalation is required |
| `backpressure_json` | TEXT | Backpressure state at time of RTA |

Allowed values are enforced by the application layer validator, not CHECK
constraints, to allow schema extension without table recreation.

## What comes next

- **Future**, Reusable ClearWright Protocol package/module after the validator stabilizes
- **Future**, Runtime orchestrator integration wiring
