# ClearWright&trade;

![ClearWright](assets/brand/clearwright-dark-badge.png)

ClearWright is an operator-controlled authorization, consensus, and audit layer
for multi-AI-agent work. Agents request clearance before they occupy the next
workflow channel, check readiness before starting expensive work, and act within
operator-defined authority classes. Consensus supports clearance; it does not
grant authority. The operator remains the highest authority and final override,
and every request leaves a durable record.

Operator-controlled means the operator defines policy, authority classes, and
escalation rules, not that the operator approves every routine action. Agents may
clear and deny routine actions autonomously within their delegated authority.

The specification is the **ClearWright Protocol**: Request to Act (RTA), Clear to
Act (CTA), Denied to Act (DTA), Request for Information (RFI), and durable
**clearance packets** that move through a four-state **clearance queue**.

## Why ClearWright

Capable agents still need coordination. Without a clearance layer, agents start
work against stale assumptions, collide with one another, duplicate effort, spend
tokens on avoidable work, and generate noisy review loops. ClearWright gives an
agent a way to request, grant, deny, defer, or escalate an action before it takes
the next step.

The design principles are simple:

- Human authority stays central. AI accelerates the work; it does not replace
  judgment.
- The point of automation is to reduce friction and error, not to remove the
  operator from the loop.
- Every important decision leaves a record that can be inspected later.
- High-value work is handled locally first, where it can be controlled.
- Unbounded agent autonomy creates risk and noise, so authority is bounded and
  ordered.

## Core model in plain English

- An agent files an **RTA** (request to act) as a clearance packet.
- A reviewer or orchestrator issues a **CTA** (cleared) or **DTA** (denied), or
  asks for more information with an **RFI**. A DTA is a successful governance
  outcome, not a failure.
- A cleared packet is **claimed** into the in-progress lane, worked, then marked
  **DONE**, or **FAILED** if execution actually broke.
- Authority is ordered like a chain of command with domain lanes. `0001` is the
  highest normal human command; `0000` is reserved for an emergency root halt
  only. Escalation climbs only as far as it must.

See [docs/CLEARWRIGHT_PROTOCOL.md](docs/CLEARWRIGHT_PROTOCOL.md) for the protocol,
[docs/AUTHORITY_MODEL.md](docs/AUTHORITY_MODEL.md) for the authority model,
[docs/QUEUE_MODEL.md](docs/QUEUE_MODEL.md) for the clearance queue,
[docs/LOCAL_REPO_PROFILE.md](docs/LOCAL_REPO_PROFILE.md) for the enforceable local
profile, [docs/GLOSSARY.md](docs/GLOSSARY.md) for terms, and
[docs/NAMING.md](docs/NAMING.md) for the naming rules.

## What exists today

This repository ships the local, single-machine foundation:

- A clearance packet schema and JSON example ([schema/](schema/)).
- A packet validator with optional strict queue-path checks
  (`tools/clearwright_validate.py`).
- A single-packet claim tool that moves a packet from the outbox to in-progress
  (`tools/clearwright_claim.py`).
- A manual lifecycle tool: inspect, complete, fail, stale detection, and status
  (`tools/clearwright_lifecycle.py`).
- A stdlib test suite ([tests/](tests/)).

Documented as direction, not yet implemented here: manual clearance decisions
(CTA/DTA/RFI tooling), a read-only packet index, a canonical packet hash policy,
and a unified operator command surface. These are planned steps, described
honestly as future work.

## Quickstart

```sh
# Validate the example clearance packet
python tools/clearwright_validate.py schema/examples/clearance_packet.example.json

# Report queue health across a clearance queue root (read-only)
python tools/clearwright_lifecycle.py status examples/queue/

# Inspect one packet (read-only)
python tools/clearwright_lifecycle.py inspect \
    examples/queue/clearance_in_progress/<packet>.json
```

Runtime clearance packets are local data and are not committed to the repository.
The paths above are illustrative.

## What ClearWright is and is not

ClearWright is the authorization, consensus, and audit layer for agent work: who
may act, whether the channel is clear, what clearance was granted, who can
override, when work should defer or escalate, and what the layer prevented. It is
not a tool-access framework, an agent-to-agent messaging bus, or a workflow
orchestrator. It sits above and beside those.

## Naming

The product and platform is ClearWright. The specification is the ClearWright
Protocol. The record artifact is a clearance packet. See
[docs/NAMING.md](docs/NAMING.md) for the full naming rules.

## Contributing and security

See [CONTRIBUTING.md](CONTRIBUTING.md), [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md),
and [SECURITY.md](SECURITY.md).

## License

Licensed under the Apache License, Version 2.0. See [LICENSE](LICENSE).

---

Built by Shawn C. Tovey, RCDD / LimitedEnergyX.

ClearWright&trade; is a trademark of Shawn C. Tovey, RCDD. U.S. trademark
application Serial No. 99912120 is pending; registration is not claimed.
