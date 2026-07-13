# Operator mode and demo mode

The ClearWright control plane runs in one of two modes. **Operator mode** is the
live local operator console for real local use. **Demo mode** is the guided
walkthrough. This document explains the difference, how agents and tools drive
the console, and what the browser UI is for.

This is early alpha software. It is a local reference implementation and is
not intended for production use. Use the terms "operator mode" or "live local
operator mode"; the console does not claim to be a finished product.

## The two modes

| | Operator mode | Demo mode |
| --- | --- | --- |
| Purpose | Live local operator console | Guided walkthrough |
| Selected by | `--queue-root PATH` (default), or `--mode operator` | no `--queue-root` (default), or `--mode demo` |
| Queue | Durable, on disk, survives restarts | Temporary, created and removed on exit (unless a durable queue is given) |
| Demo packets | Never seeded; a fresh queue starts empty | Seeded into an empty queue from `examples/demo_packets/` |
| Reset demo | Disabled, so live work can never be destroyed | Available |
| Mission panel | Hidden | Shown |
| Primary feed | Real local agent events | Real local agent events, with a clearly labeled simulated demo feed as a fallback |

Mode selection in one line: an explicit `--mode` always wins; otherwise a
`--queue-root` defaults to operator mode and the default temporary queue defaults
to demo mode.

### Run operator mode

    python apps/control-plane/server.py --port 8787 --queue-root path/to/queue

The queue and its four lanes (`clearance_outbox`, `clearance_in_progress`,
`clearance_done`, `clearance_failed`) are created if missing. Nothing is seeded,
and an existing queue is never cleared or overwritten. The console shows real
local agent events and the live clearance queue. When there is nothing to act
on it says "No active clearance requests." and "No local agent events yet.
Agents and tools can submit events through the local adapter."

### Run demo mode

    python apps/control-plane/server.py

A temporary queue is seeded with the demo packets so the walkthrough has
something to show, and it is removed on exit. Reset demo returns the queue to the
seed packets. To run a demo walkthrough against a durable queue, add
`--mode demo`; to run an empty live console on a temporary queue, add
`--mode operator`.

## System health (/api/health)

`GET /api/health` is a **read-only** readiness snapshot: is ClearWright ready to
use right now? It reports mode, durable flag, queue root, packet counts by lane,
message/event/run/work-item counts, the latest run timestamp, the pulse, cheap
capability checks (worker bridge, proof tool, Codex helper, and whether a
`codex` executable is on PATH), and plain-language warnings/errors.

Status meanings — readiness guidance, not compliance:

- **green (Healthy)**: operator mode on a readable durable queue, tools present,
  nothing waiting.
- **yellow (Attention)**: something deserves a look — open or claimed work
  items, demo mode, or the Codex CLI missing while the helper exists.
- **red (Problem)**: the queue root or a lane is missing/unreadable, failed
  packets exist, or the health check itself failed.

The health check never runs Codex or tests and never mutates the queue. Codex
availability is a cheap PATH capability check only — never proof of
participation. The console shows a compact health chip in the top bar
(Healthy / Attention / Problem) with the top reason as its tooltip; clicking
opens a read-only details panel.

## Conversation Workspace

**Conversations** (top bar) opens the conversation-first view of the durable
message threads: a thread list on the left, a readable timeline and a prominent
composer on the right. The operator sends real inbound `OPERATOR-0001` messages
("Send Agents a Message"); replying with a conversation selected continues that
thread, and sending with none selected starts a new one. Replies from Claude,
Codex, or other workers appear only when they actually post back through the
local adapter — the target selector (All / Claude / Codex / Operator note) is an
**intent hint only**, written as a plain label into the message text, and never
claims a model participated.

The escalation ladder is deliberate: **conversation → work item (if actionable)
→ clearance packet (if a governed change) → CTA/DTA/RFI → worker claim → DONE
results → History.** Normal chat needs no packet. "Create work item" posts a
real follow-up request; "Request clearance packet" files an RTA through the
existing request intake; "Mark reviewed" writes a durable acknowledge note.
Nothing is deleted or archived by these actions.

## Durable record vs active work

The **Durable record** panel is a packet lane/audit snapshot, **not the active
work list** — active work lives in **Work items** and **Active Run**. Terminal
packets (DONE / DTA / SUPERSEDED) older than the 24-hour recent-terminal window
collapse into a compact "N archived completed packets" line with a
**Show completed** toggle. This is a display flag only: the packet files are
never moved or deleted, they still count in the lane totals, and History (the
full ledger), the run registry, and audit trails always show everything.
Failed packets are never archived and keep health red.

Two windows, two meanings: **5 minutes** is the activity/pulse recency window
(what counts as "happening now"); **24 hours** is the recent-terminal display
window (how long a completed packet stays expanded in the Durable record).

## Workflow pulse and the pulse inspector

The workflow graph separates state from activity: a dim outline is an available
stage, and a bright pulse means **recent activity only** — an open work item
pulses Incoming, a claim pulses Claimed Work, recent progress or a reviewer
message pulses Verification, and a final response or fresh packet completion
pulses DONE briefly. A stale completed packet never pulses.

The pulse object in `/api/state` carries inspector metadata — `active_phase`,
`reason`, `source_thread_id` / `source_work_item_id` / `source_packet_id`, and
`expires_at` / `seconds_remaining` — and a compact **pulse inspector** line under
the workflow title shows why the graph is pulsing and when the time-based part
expires (standing states such as an unclaimed work item hold until acted on).
When nothing is recent it reads "Pulse: idle · no recent activity". The
inspector's source thread id tells you whether graph activity matches the run
selected in Active Run (the graph pulse itself is global).

## /api/state metadata

`/api/state` reports the current mode so the UI and any client can adapt:

- `mode`: `"operator"` or `"demo"`
- `queue_root`: the absolute queue path in use
- `durable`: `true` for a persistent `--queue-root`, `false` for a temporary queue
- `demo_seeded`: `true` only when demo packets were seeded into this queue

## How agents and tools drive it

The browser UI is the **operator display**. The **integration surface** is the
server, its local HTTP API, the CLI tools, and the durable queue on disk. Agents,
tools, and scripts (for example Claude Desktop, Codex, or a shell script) submit
clearance requests and agent events **directly** through that surface. No
copy/paste into the web page is required, and no browser automation is needed to
drive ClearWright.

Record a real local agent event from the CLI:

    python tools/clearwright_agent_event.py path/to/queue \
        --actor claude --role orchestrator \
        --message "Claimed the cleared work and started the change."

Or over local HTTP:

    curl -s -X POST http://127.0.0.1:8787/api/agent-events \
        -H "Content-Type: application/json" \
        -d '{"actor":"claude","role":"orchestrator","message":"Sent via local adapter."}'

    curl -s http://127.0.0.1:8787/api/agent-events

Agent events are a durable log under the queue root in `agent_events/`. An agent
event is not a clearance packet and grants no authority; the operator decides.

Beyond one-shot events, the **local communications and dispatch loop** lets
agents, tools, and scripts hold a real, threaded, packet-linked conversation with
ClearWright over the CLI (`tools/clearwright_message.py`,
`tools/clearwright_work.py`) or local HTTP (`/api/messages`, `/api/work-items`).
The operator can type a request in the console's operator chat (a real inbound
`OPERATOR-0001` message, never a fake reply); agents pick it up as a derived
**work item**, claim it, and respond. In operator mode the console shows these
real messages, events, and work items only, updating live; it never presents
simulated conversation as if real agents participated. The simulated
agent-conversation console is a demo-mode walkthrough aid. The workflow graph
pulses the active stage from real durable state (pulse is recent-activity state,
not permanent packet lifecycle, so a stale completed packet does not keep DONE
pulsing), and a real Codex review is only recorded when the local Codex CLI
actually ran and produced substantive output (telemetry-backed); GPT / ChatGPT
are never claimed. A read-only History view lists every packet, message, and
event, and a focused Active Run view shows a run as one readable thread with a
click-to-select list of recent runs (derived from the durable message threads,
no new database), copy buttons, and Codex telemetry as fields. See
[LOCAL_COMMUNICATIONS.md](LOCAL_COMMUNICATIONS.md) and
[WORKER_RUNBOOK.md](WORKER_RUNBOOK.md).

### Browser automation is not the integration method

Browser automation (for example ChromeMCP) is **not the integration method** for
ClearWright. It is only for visual inspection of the operator display, or for
GitHub authentication when that is genuinely needed. Agents integrate over local
HTTP, the CLI tools, and the queue on disk, never by clicking the operator page.

## Honest boundaries

- Operator mode is for **live local** use. It is still early alpha and is not
  intended for production use.
- The console is human-commanded and operator-controlled. It does not execute
  the proposed actions itself; every decision is carried out by the existing
  ClearWright tools.
- Consensus or agent chatter never grants authority. A recommendation is input
  to a human-commanded decision, never a decision itself.
- The demo mode simulated feed and conversation console are local walkthrough
  aids only. There is no real external model integration, no API call, and no
  credential anywhere in this repository.
- The clearance queue and audit trail remain the durable record behind the work.

See [CONTROL_PLANE_DEMO.md](CONTROL_PLANE_DEMO.md) for the full console tour.
