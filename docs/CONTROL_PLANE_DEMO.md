# Control plane demo

A small, local web control plane that demonstrates the ClearWright clearance model
against a generic sample software project. It is the first working model you can
show when someone asks what ClearWright does.

This is a local reference implementation and an early-alpha demonstration surface.
It is human-commanded and operator-controlled. It does not execute the proposed
actions, connect to any external service, run a background worker, or act on its
own. Every operator decision is carried out by the existing ClearWright tools.

## How the console is framed

The control plane is an **operator console**, not a packet-authoring surface.
Packet intake is passive: requests would normally be submitted by an agent,
tool, or integration, and they arrive for review. The operator's job is to
review and decide, and the packet queues and audit trail remain the durable
record behind that work.

- **Passive packet intake**: RTA packets normally come from agents, tools,
  scripts, or integrations. The request tool and its API are backend plumbing
  for those integrations, not the normal human UI. In the demo, ClearWright
  derives a packet from the simulated conversation recommendation.
- **Operator review and decision**: the incoming request card summarizes what
  the agent wants, why clearance is required, the allowed and disallowed scope,
  and the risk, with the decision actions right there. The operator reviews
  and decides; nobody fills out packet paperwork.
- **Workflow visualization as a demo aid**: a simple clearance-path panel shows
  the stages a request travels; it is a visual aid, not a workflow editor.
- **Durable background record**: the four queue lanes and the per-packet audit
  trail stay visible but secondary. They are the record, not the work surface.

## What it shows

1. **Mission intake**: mission name, target project label, allowed scope,
   disallowed scope, a test command, and risk notes (from
   [examples/sample_project/mission.json](../examples/sample_project/mission.json)).
2. **Clearance workflow canvas**: the fixed path a request travels, drawn as a
   vertical node graph on a dark canvas: Mission Start through Planner Review,
   Scope/Risk Check, Incoming Clearance Request, Operator Decision, a
   CTA / DTA / RFI branch, Claimed Work, Verification, and DONE, with an RFI
   edge looping back to the incoming request. Stages light up from the live
   queue state, and simple zoom controls fit the graph to the panel. The
   pre-intake stages are drawn dashed as simulated agent context. It is a
   visual aid with fixed nodes, not a workflow editor.
3. **Incoming clearance request card**: the operator-facing summary of the next
   decidable request: what the agent wants to do, why clearance is required,
   allowed scope, disallowed scope (from the mission), risk, and the requested
   decision, with Grant CTA / Deny DTA / Request RFI actions in place.
4. **Live agent feed (simulated)**: a right-side feed of agent-style activity
   lines (planner, reviewer, worker, clearwright). It is simulated for the demo;
   there is no real streaming and no agent integration.
5. **Clearance queues (durable record)**: the four lanes, `clearance_outbox`,
   `clearance_in_progress`, `clearance_done`, and `clearance_failed`, with one
   card per packet and per-card actions (Claim cleared work, Mark DONE, Mark
   FAILED) where valid. Visually secondary by design.
6. **Audit trail viewer**: the packet lifecycle events in readable order,
   including completion results when they were recorded.
7. **Background packet creation**: RTA packets normally come from agents,
   tools, scripts, or integrations through the request tool and its API. In the
   demo, ClearWright derives a packet from the simulated conversation
   recommendation when the operator clicks "Send to clearance queue". There is
   no packet paperwork in the operator flow: required fields are enforced by
   the request tool, and the target-project label is constrained to approved
   generic labels so no private names can enter a packet from the console.
8. **Completion results** (Mark DONE): completion asks for a summary, an optional
   verification result, changed files, and a findings note. These are stored as
   one nested `results` object on the DONE audit event, so the audit trail
   carries the outcome, not just the transition. No new packet field and no
   schema change.

Supersede is intentionally not offered. No current tool sets the `SUPERSEDED`
status cleanly, so the demo does not fabricate that transition.

## How decisions map to the tools

| Panel action | Tool invoked |
| --- | --- |
| Send to clearance queue | `tools/clearwright_request.py --title ... --type ... --action ...` |
| Grant CTA | `tools/clearwright_decide.py cta` |
| Deny DTA | `tools/clearwright_decide.py dta --reason ...` |
| Request RFI | `tools/clearwright_decide.py rfi --reason ...` |
| Claim cleared work | `tools/clearwright_claim.py` |
| Mark DONE | `tools/clearwright_lifecycle.py complete [--summary ... --verification ... --changed-file ... --findings ...]` |
| Mark FAILED | `tools/clearwright_lifecycle.py fail --reason ...` |

Command-authority examples use `OPERATOR-0001`. No personal names are used.

## Agent conversation console (simulated)

The console includes an "Ask ClearWright" panel where the operator asks a
question in plain language instead of filling out packet forms. Up to five
simulated agent turns deliberate on it:

1. a Claude-style analysis,
2. a GPT-style challenge and risk critique,
3. a Codex-style code/test impact note (with a demo test idea),
4. a Claude-style revised recommendation,
5. a GPT-style final review.

ClearWright then condenses the deliberation into a single decision card: the
decision needed, a recommended CTA / DTA / RFI, the risks, a scope boundary,
and a proposed next action. When the recommendation is a bounded CTA, one
"Send to clearance queue" click derives the RTA packet in the background and
places it in the clearance queue; it then appears as an incoming clearance
request for the operator to clear, deny, or send to RFI, and travels the
normal packet lifecycle. The operator never fills out packet fields.

Honest boundaries:

- **The console is simulated in this local demo.** Every turn is generated
  locally by the demo server. There is **no real external model integration**,
  no API call, and no credential anywhere in this repository.
- The purpose is to show how agent deliberation can be condensed into one
  human decision.
- **Authority remains with the operator.** Consensus or agent chatter does not
  grant authority; a recommendation is input to a human-commanded decision,
  never a decision itself.
- Unsafe or destructive wording is never condensed into a CTA recommendation;
  it condenses to DTA or RFI.
- The packet queues and audit trail remain the durable record behind the
  conversation.

## Local agent adapter (integration surface)

The browser UI is the **operator display**. The **integration surface** is the
server, its local HTTP API, the CLI tools, and the durable queue on disk.
Agents, tools, and scripts (Claude Desktop, Codex, or a shell script) submit
events and packets **directly** through that surface. They do not click the web
page, and no copy/paste into the UI is required.

- **Claude Desktop and other agents should use local HTTP, CLI, or file events**
  to interact with ClearWright. Browser automation is **not the integration
  method**; it is only for visual inspection of the operator display or for
  GitHub authentication when that is genuinely needed.
- Agent activity is recorded as durable **agent events** under the queue root in
  `agent_events/`. An agent event is not a clearance packet and grants no
  authority.

Record an agent event from the CLI:

    python tools/clearwright_agent_event.py D:/path/to/queue \
        --actor claude --role orchestrator \
        --message "Reviewed calc harness and found extractor risk." \
        --packet-id cw-harness-301

Or over local HTTP:

    curl -s -X POST http://127.0.0.1:8787/api/agent-events \
        -H "Content-Type: application/json" \
        -d '{"actor":"claude","role":"orchestrator","message":"Sent via local adapter."}'

    curl -s http://127.0.0.1:8787/api/agent-events

In the UI, the Live agent feed shows these as **local** (real) events; the
simulated demo lines remain only as a clearly labeled fallback.

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

Then open the printed local address (default http://127.0.0.1:8787/). By default
the demo queue is created in a temporary directory outside the repository, seeded
from [examples/demo_packets/](../examples/demo_packets/), and removed on exit. Use
**Reset demo** in the UI to return to the seed packets. Runtime clearance packets
are local demo data and are not committed.

### Durable queue (`--queue-root`)

To keep the console pointed at a persistent queue, so active governed work stays
visible across restarts, pass `--queue-root`:

    python apps/control-plane/server.py --port 8787 --queue-root path/to/queue

Behavior with `--queue-root`:

- The directory and the four lanes (`clearance_outbox`,
  `clearance_in_progress`, `clearance_done`, `clearance_failed`) are created if
  missing.
- Demo packets are seeded only when the queue is empty; a queue that already
  holds packets is left exactly as-is and never cleared or overwritten.
- The queue is not removed on exit (it is durable).
- **Reset demo** is disabled on a durable queue, so the UI can never destroy
  governed work; reset applies only to the default temporary queue.

Omitting `--queue-root` preserves the original temporary-queue behavior.

## Scope

This phase is the local control plane only. It does not connect to any external
agent, add a scheduler or daemon, add a policy engine, or analyze a real target
project. The agent feed is simulated and the workflow panel is a fixed visual
aid: there is no real streaming, no live agents, no workflow import, and no
drag-and-drop workflow editing. Those are out of scope here by design.
