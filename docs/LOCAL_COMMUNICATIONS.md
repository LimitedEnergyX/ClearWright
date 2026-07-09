# Local communications loop

The local communications loop lets agents, tools, and scripts hold a real,
durable, packet-linked conversation with ClearWright over the command line,
local HTTP, or files. It is the first real ClearWright communication transport.
Claude Desktop, Codex, a PowerShell script, or a future worker can post a
message, retrieve pending messages or packet context, and post a response tied
to a thread or packet, all without ChromeMCP and without copy/paste into the
web page.

This is early alpha software. It is a local reference implementation and is
not intended for production use.

## What a message is

A message is a durable JSON record in a clearance queue's `communications/`
store. It is not a clearance packet and not an agent event, and it does not
touch the packet schema or validator. A message never grants clearance or moves
a packet. Conversation never grants authority; the operator decides.

Each message carries:

- `message_id`, `thread_id` (generated on post, reused on respond)
- optional `packet_id` (the clearance packet it relates to)
- `actor`, `role`
- `direction`: `inbound`, `outbound`, or `internal`
- `status`: `posted`, `read`, or `responded`
- `at` (UTC timestamp), `source` (for example `local-adapter`, `local-http`)
- `simulated`: `false` for real communication

## Where messages are stored

Under the queue root, alongside the clearance lanes and the agent-event log:

    <queue-root>/communications/<message_id>.json

The store is append-only and durable: messages persist across server restarts
and are never overwritten.

## CLI

`tools/clearwright_message.py` is the command-line surface. It has three
subcommands: `post`, `list`, and `respond`.

Post a message (starts a new thread unless `--thread-id` is given):

    python tools/clearwright_message.py post path/to/queue \
        --actor claude --role orchestrator \
        --message "Claude posted this through the local communications loop." \
        --packet-id cw-harness-301

List messages, optionally filtered by packet or thread:

    python tools/clearwright_message.py list path/to/queue --packet-id cw-harness-301
    python tools/clearwright_message.py list path/to/queue --thread-id thr-...

Respond on an existing thread (`--thread-id` is required; the response defaults
to an outbound, responded message):

    python tools/clearwright_message.py respond path/to/queue --thread-id thr-... \
        --actor claude --role orchestrator --message "Review complete."

## Local HTTP

The same operations are available over local HTTP, so curl or any local client
can drive them:

    # Post a message
    curl -s -X POST http://127.0.0.1:8787/api/messages \
        -H "Content-Type: application/json" \
        -d '{"actor":"claude","role":"orchestrator","message":"Posted via local HTTP.","packet_id":"cw-harness-301"}'

    # List messages (optionally filter with ?packet_id=... or ?thread_id=...)
    curl -s http://127.0.0.1:8787/api/messages
    curl -s "http://127.0.0.1:8787/api/messages?packet_id=cw-harness-301"

    # Respond on a thread
    curl -s -X POST http://127.0.0.1:8787/api/messages/respond \
        -H "Content-Type: application/json" \
        -d '{"thread_id":"thr-...","actor":"claude","message":"Review complete."}'

Endpoints:

- `POST /api/messages` posts a message (starts or continues a thread).
- `GET /api/messages` lists messages; `?packet_id=` and `?thread_id=` filter, `?limit=` trims to the most recent N.
- `POST /api/messages/respond` posts an outbound response; requires `thread_id`.

## What the browser shows

The browser UI is the **operator display**. Browser automation (for example
ChromeMCP) is **not the integration method**; it is only for visual inspection
of the operator display, or for GitHub authentication when that is genuinely
needed. Agents, tools, and scripts communicate through the CLI, local HTTP, or
the files on disk, never by clicking the operator page.

In operator mode, the console shows the real communication threads and real
local agent events only. It does not present simulated conversation as if real
agents participated. The packet audit drawer also surfaces the related agent
events and messages for a packet as working context: the packet stays the
authority record, and the conversation and activity sit beside it.

## Operator mode vs demo mode

- **Operator mode** is real-only: real local messages and agent events, no
  simulated conversation.
- **Demo mode** keeps the simulated agent-conversation console and simulated
  feed as clearly labeled walkthrough aids. Simulation belongs only in demo
  mode.

See [OPERATOR_MODE.md](OPERATOR_MODE.md) for the two modes and
[CONTROL_PLANE_DEMO.md](CONTROL_PLANE_DEMO.md) for the full console tour.

## Dispatch: work items

The communications loop is also a real local dispatch loop. The operator can type
a request in the console's operator chat (posted as a real inbound message from
`OPERATOR-0001`, role `operator`, source `operator-ui`, never a fake reply), or
send one from the CLI or local HTTP. ClearWright then surfaces **work items**
that agents, tools, and scripts can pick up and act on, over
`tools/clearwright_work.py` or the `/api/work-items` endpoints, without a browser.

Work items are **derived** from existing durable state, not a separate database:

- an inbound message thread with no response -> `kind: message`
- a CTA packet in `clearance_outbox` -> `kind: packet` (claimable)
- an `IN_PROGRESS` packet -> `kind: in_progress` (needs an update)
- an `RFI_PENDING` packet -> `kind: rfi` (needs clarification)

Work item ids are stable and deterministic (`message:<message_id>`,
`packet:<packet_id>:cta`, `in_progress:<packet_id>`, `rfi:<packet_id>`).

Claiming and responding never touch the packet schema or validator. Claiming a
CTA packet uses the existing claim lifecycle to move the real packet; every claim
and response is also written as a durable message in the related thread, so the
original request is never lost.

CLI:

    python tools/clearwright_work.py list path/to/queue
    python tools/clearwright_work.py claim path/to/queue --work-item-id <id> --actor claude
    python tools/clearwright_work.py respond path/to/queue --work-item-id <id> --actor claude --message "Reviewing the repo."

Local HTTP:

- `GET /api/work-items` lists the derived work items.
- `POST /api/work-items/claim` records a claim (input: `work_item_id`, `actor`, optional `role`, `source`).
- `POST /api/work-items/progress` posts a durable internal progress note (input: `work_item_id`, `actor`, `message`, optional `role`, `source`).
- `POST /api/work-items/respond` writes a response (input: `work_item_id`, `actor`, `message`, optional `role`, `source`).

The claim, progress, and respond routes share the CLI's `find_work_item` guard:
an unknown `work_item_id` returns 404 and writes nothing (no stray message, no
new thread, no packet change), so the CLI and HTTP agree exactly. The
`GET /api/state` response also carries a `pulse` object computed from real
durable state, so the workflow graph pulses the active stage and a stale
`clearance_done` packet does not keep DONE pulsing.

Example end-to-end flow (operator asks, an agent picks it up):

    # operator posts a request (CLI, or the console operator chat)
    python tools/clearwright_message.py post path/to/queue \
        --actor OPERATOR-0001 --role operator --message "Review this repo under CW."
    # an agent lists, claims, and responds
    python tools/clearwright_work.py list path/to/queue
    python tools/clearwright_work.py claim path/to/queue --work-item-id message:<id> --actor claude
    python tools/clearwright_work.py respond path/to/queue --work-item-id message:<id> --actor claude \
        --message "I am reviewing the repo."

The operator console shows the work items and the message threads live (fast
polling, no WebSockets), and the workflow graph pulses from real queue and
message state (pulse is recent-activity state, not permanent packet lifecycle,
so a completed packet does not pulse forever). A read-only **History** view
lists every packet, message, and agent event with basic filters, and a focused
**Active Run** view (`GET /api/active-run`, or
`GET /api/active-run?thread_id=<id>` for a specific run) shows a run as one
readable sequence with copy buttons and Codex telemetry as fields. A small
**run registry** (`GET /api/runs`) derives one summary per durable message
thread (thread id, title, timestamps, message count, actors, sources,
open/claimed/responded status, and any Codex telemetry) with simple filters
(`limit`, `status`, `actor`, `source`, `has_codex`, `packet_id`), and the
Active Run view uses it as a click-to-select run list. Runs are derived from
the durable threads, never a new database; History remains the full ledger.
A read-only `GET /api/health` reports readiness (green/yellow/red with counts,
capabilities, and plain-language warnings; see
[OPERATOR_MODE.md](OPERATOR_MODE.md)). None of this edits or deletes the record.

For the "use CW" worker behavior (find work, claim, post progress, respond),
`tools/clearwright_worker.py` is a thin bridge over these same functions with
`next`, `claim`, `progress`, `respond`, and `status` commands, and
`GET /api/worker-status` gives the same summary over HTTP. See
[WORKER_RUNBOOK.md](WORKER_RUNBOOK.md) for exactly what Claude should do when the
operator says "use CW" or "review with CW".

## Discord and other transports (future)

Discord is **not connected** in ClearWright yet. The local communications loop
is the first transport, and ClearWright is meant to work locally before any
remote transport is added. Discord is a **future** transport option: it will
require explicit operator setup and credentials (a bot token or webhook), which
are never requested or stored by this loop. No real external model API (GPT,
Codex, Claude, or otherwise) is wired here either; the loop carries messages
between whatever local agents, tools, or scripts the operator runs.

## Honest boundaries

- Messages are working context, not authority. The clearance packet and its
  audit trail remain the durable authority record.
- No message grants clearance or moves a packet; the operator decides.
- The loop is local and durable. It adds no external dependency, no network
  service beyond the local HTTP server, and no secret.
