# Control plane demo

A small, local web control plane that demonstrates the ClearWright clearance model
against a generic sample software project. It is the first working model you can
show when someone asks what ClearWright does.

This is a local reference implementation and an early-alpha demonstration surface.
It is human-commanded and operator-controlled. It does not execute the proposed
actions, connect to any external service, run a background worker, or act on its
own. Every operator decision is carried out by the existing ClearWright tools.

## What it shows

1. **Mission intake**: mission name, target project label, allowed scope,
   disallowed scope, a test command, and risk notes (from
   [examples/sample_project/mission.json](../examples/sample_project/mission.json)).
2. **Clearance queue board**: the four lanes, `clearance_outbox`,
   `clearance_in_progress`, `clearance_done`, and `clearance_failed`.
3. **Packet cards**: packet id, requested action, requesting role, current status,
   authority required, risk notes, the CTA lease when present, and the audit event
   count.
4. **Operator decision panel** (per card): Grant CTA, Deny DTA, Request RFI, Claim
   cleared work, Mark DONE, Mark FAILED. Actions appear only when they are valid
   for that packet.
5. **Audit trail viewer**: the packet lifecycle events in readable order.

Supersede is intentionally not offered. No current tool sets the `SUPERSEDED`
status cleanly, so the demo does not fabricate that transition.

## How decisions map to the tools

| Panel action | Tool invoked |
| --- | --- |
| Grant CTA | `tools/clearwright_decide.py cta` |
| Deny DTA | `tools/clearwright_decide.py dta --reason ...` |
| Request RFI | `tools/clearwright_decide.py rfi --reason ...` |
| Claim cleared work | `tools/clearwright_claim.py` |
| Mark DONE | `tools/clearwright_lifecycle.py complete` |
| Mark FAILED | `tools/clearwright_lifecycle.py fail --reason ...` |

Command-authority examples use `OPERATOR-0001`. No personal names are used.

## The three demonstrated paths

**CTA path**: an `RTA` packet starts in `clearance_outbox`. The operator grants a
bounded clearance (`CTA`); the packet stays in `clearance_outbox` until it is
claimed. Claiming moves it to `clearance_in_progress` as `IN_PROGRESS`. Completing
it moves it to `clearance_done` as `DONE`. The final packet validates.

**DTA path**: an `RTA` packet is denied with a reason. It moves to `clearance_done`
with status `DTA`. A `DTA` is a successful governance outcome, not a failure, and
never enters `clearance_failed`.

**RFI path**: an `RTA` packet is sent back for clarification with a reason. It stays
in `clearance_outbox` with status `RFI_PENDING`. This is pre-decision clarification
only.

`FAILED` is only reachable after a claim. A pre-claim `RTA` cannot jump to `FAILED`;
the control plane offers Mark FAILED only for an `IN_PROGRESS` packet.

## Running it

The control plane uses only the Python standard library. There is no database and
no external runtime dependency.

    python apps/control-plane/server.py

Then open the printed local address (default http://127.0.0.1:8787/). The demo
queue is created in a temporary directory outside the repository, seeded from
[examples/demo_packets/](../examples/demo_packets/). Use **Reset demo** in the UI to
return to the seed packets. Runtime clearance packets are local demo data and are
not committed.

## Scope

This phase is the local control plane only. It does not connect to any external
agent, add a scheduler or daemon, add a policy engine, or analyze a real target
project. Those are out of scope here by design.
