# Changelog

All notable changes to this repository are recorded here. Dates and release tags
will be added when releases begin.

## Unreleased

- Dispatch console cleanup and worker HTTP parity. Cleaned the operator UI: long
  panel descriptions moved behind keyboard-focusable `?` help tooltips, and the
  operator chat placeholder is now "Send Agents a Message". Fixed the workflow
  pulse: the active stage is computed server-side from real durable state (real
  messages, packets, and derived work items) with a recency window and returned
  as `state.pulse`, so a stale `clearance_done` packet no longer keeps DONE
  pulsing; DONE pulses only for a recent completion. Hardened worker HTTP parity:
  the `find_work_item` guard now lives in the shared `claim/progress/respond`
  functions, so an unknown `work_item_id` returns 404 and writes nothing on both
  CLI and HTTP; added `POST /api/work-items/progress` reusing the shared
  `progress_work_item`. Added `tools/clearwright_codex_review.py` (telemetry-
  backed Codex read-only review: only a clean, substantive run is posted as
  Codex; a hang/timeout/empty run is recorded as such and claims no
  participation) and `tools/clearwright_proof.py` (one-command "use CW" flow to
  reduce approval prompts). Docs updated (single-purpose commands over chained
  shell). No schema/validator change, no new dependency, no Discord, no model API.
- Worker command bridge and runbook: `tools/clearwright_worker.py` makes "use
  CW" / "review with CW" a real, repeatable worker behavior over CLI or local
  HTTP, with `next`, `claim`, `progress`, `respond`, and `status` commands. It is
  thin orchestration over the existing work-item and message functions (shared
  `progress_work_item`, `find_work_item`, and `worker_status` helpers), so the
  CLI and HTTP agree on ids, claim semantics, thread and packet preservation, and
  durable files. Adds a small read-only `GET /api/worker-status`, small work-item
  and communications UI hints, and `docs/WORKER_RUNBOOK.md` (what Claude should
  and must not do, command examples, how to verify in the UI/history, how to
  avoid ChromeMCP, how to handle unavailable GPT/Codex, and the hard gates).
  No schema or validator change, no new dependency, no browser automation, no
  Discord, no model API.
- Fixed durable message and agent-event ordering: ids and timestamps are now
  strictly monotonic, so messages and events built within the same microsecond
  (a burst or a tight loop) keep their write order and stay uniquely identified.
  This makes thread order (post progress, then respond) reliable for the worker
  loop. Message log only; no schema or validator change.
- Live dispatch console and history: the control plane is now a working local
  dispatch loop. An operator chat in the console posts real inbound
  `OPERATOR-0001` messages (no fake replies); agents, tools, and scripts pick up
  **work items** derived from existing durable state (unanswered messages, CTA
  packets ready to claim, `IN_PROGRESS` packets needing an update,
  `RFI_PENDING` packets needing clarification) over `tools/clearwright_work.py`
  or `GET /api/work-items`, `POST /api/work-items/claim`, and
  `POST /api/work-items/respond`. Claiming a CTA packet uses the existing claim
  lifecycle; every claim and response is a durable message, so the original
  request is never lost. The UI polls live (every 2s, no WebSockets), the
  workflow graph pulses from real queue and message state, and a read-only
  History view (`GET /api/history`) lists every packet, message, and agent event
  with filters. Operator mode stays real-only; simulation remains demo-only.
  Work items are derived, not a new store; no schema or validator change, no new
  dependency, and no Discord or model API. See `docs/LOCAL_COMMUNICATIONS.md`.
- Local communications loop: a durable, threaded, packet-linked message store
  under the queue root (`communications/`), `POST`/`GET /api/messages` and
  `POST /api/messages/respond` local HTTP endpoints, and a
  `tools/clearwright_message.py` CLI (`post`, `list`, `respond`) so Claude
  Desktop, Codex, scripts, or future workers hold a real local conversation with
  ClearWright over CLI or local HTTP, without a browser. The operator console
  shows real message threads in a Local communications panel, and the packet
  audit drawer surfaces related messages and agent events as working context;
  operator mode never presents simulated conversation as real. Discord remains a
  future transport (explicit credentials required, none stored). No real model
  API is wired. Messages are not clearance packets and grant no authority; no
  schema or validator change. See `docs/LOCAL_COMMUNICATIONS.md`.
- Operator mode for live local use: the control plane now runs as an operator
  console by default whenever a durable `--queue-root` is given, with an
  explicit `--mode operator|demo` override. Operator mode never seeds demo
  packets, disables Reset demo, hides the demo mission, and treats real local
  agent events as the primary feed; demo mode keeps the seeded walkthrough and a
  clearly labeled simulated feed. `/api/state` now reports `mode`, `queue_root`,
  `durable`, and `demo_seeded`, and the UI shows a mode badge with operator
  empty-states ("No active clearance requests."). New `docs/OPERATOR_MODE.md`
  explains the two modes. No schema or validator change.
- Local agent event adapter: a durable agent-event log under the queue root
  (`agent_events/`), a `POST`/`GET /api/agent-events` local HTTP API, and a
  `tools/clearwright_agent_event.py` CLI so Claude Desktop, Codex, scripts, or
  future workers send real agent events into the control plane over CLI, curl,
  or local HTTP, without browser automation. The Web UI's live feed shows real
  local events distinctly from the simulated demo fallback. Agent events are
  not clearance packets and grant no authority; no schema or validator change.
- Agent conversation console (simulated): the operator asks agents a question,
  up to five locally simulated agent turns deliberate (analysis, challenge,
  code/test impact, revised recommendation, final review), and ClearWright
  condenses a recommended CTA / DTA / RFI with risks and a proposed next
  action. A bounded CTA recommendation can be sent to the clearance queue with
  one click: the packet is derived in the background, with no packet paperwork
  in the operator flow. No real external model integration; consensus does not
  grant authority.
- Control plane operator-console framing: a clearance-workflow panel, an
  incoming-clearance-request card, and a simulated live agent feed; the queue
  board remains visible as the durable record. Packet intake is passive: RTA
  packets come from agents, tools, scripts, or integrations through a new
  manual request tool (`tools/clearwright_request.py`) and its API, which are
  backend plumbing rather than a human form. Completion results (summary,
  verification, changed files, findings) are stored as one nested `results`
  object on the DONE audit event. No schema change.
- Added peer-review guidance for protocol, lifecycle, authority, and
  implementation feedback (`docs/PEER_REVIEW.md`, a protocol review issue
  template, and README/CONTRIBUTING pointers).
- End-of-alpha target workflow document (`docs/END_OF_ALPHA_TARGET.md`):
  protocol-vision diagram of the human-command-to-final-output flow, labeled as
  target state rather than a current-state implementation claim.
- Manual clearance decision tool (`tools/clearwright_decide.py`): clear (CTA),
  deny (DTA), or request information (RFI) on one outbox packet.
- Repository baseline hardening.
- Example clearance packet language sanitized.
- Legacy PR and ADR references removed.
- Tool docstrings scrubbed of legacy references.
- Public posture documentation in progress.
