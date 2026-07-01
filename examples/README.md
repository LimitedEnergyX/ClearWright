# Examples

This directory holds illustrative material for working with ClearWright locally.

## Clearance queue skeleton

`queue/` contains the four-lane clearance queue as empty, version-tracked
channels:

```
queue/clearance_outbox/
queue/clearance_in_progress/
queue/clearance_done/
queue/clearance_failed/
```

Each lane is kept in version control with a `.gitkeep` file so the channel exists
in the repository. Actual clearance packet files are local runtime data and are
not committed; they are excluded by `.gitignore`.

## Example clearance packet

A complete, valid clearance packet in its JSON shape lives at
[../schema/examples/clearance_packet.example.json](../schema/examples/clearance_packet.example.json).

## Trying the tools against the skeleton

```sh
# Read-only status across the clearance queue root
python tools/clearwright_lifecycle.py status examples/queue/

# Read-only stale scan of the in-progress lane
python tools/clearwright_lifecycle.py stale examples/queue/clearance_in_progress/
```

These commands are read-only and safe to run at any time. They report on whatever
packets are present without moving or mutating anything.
