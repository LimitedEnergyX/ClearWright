# Changelog

All notable changes to this repository are recorded here. Dates and release tags
will be added when releases begin.

## Unreleased

- Automated GPT and Codex Review Council. ClearWright now coordinates real,
  independent reviews of a plan and decides deterministically whether Claude may
  proceed, with no manual copy/paste. New `tools/clearwright_gpt_review.py`
  calls the OpenAI Responses API (standard-library HTTP, no new dependency),
  reading `OPENAI_API_KEY` only from the environment and never returning,
  printing, logging, persisting, or recording it; it posts a GPT reviewer
  message only after a real, successful response validates. `clearwright_codex_review.py`
  gained a `--structured` mode returning the same verdict shape from a real
  read-only Codex run (existing behavior unchanged). A shared
  `tools/clearwright_verdict.py` defines the structured verdict and Claude's
  reconciliation (every rejected finding requires evidence). `tools/clearwright_review_council.py`
  runs `plan` / `incident` / `verify` rounds (round one independent; a secret
  scan on the context is a hard gate), attaches reconciliations, and evaluates a
  deterministic agreement rule (>= 2 rounds, real GPT + real Codex, no
  revise/block, no unresolved blocker, confidence >= 0.70, ready_to_proceed, no
  hard gate) into `agreement_threshold_met | needs_revision | reviewer_unavailable |
  operator_required | hard_gate` with matching exit codes. Council state is
  stored durably under `review_councils/<id>/`. Read-only `GET /api/review-councils`
  and `GET /api/review-council` (the server never runs a reviewer), a
  Conversation Workspace council card, and health capability booleans
  (`gpt_helper`, `openai_api_key_configured`, `configured_gpt_model`,
  `council_available`; the key value is never exposed). Council agreement never
  grants authority; the operator's approved scope does. See
  [docs/REVIEW_COUNCIL.md](docs/REVIEW_COUNCIL.md).
- Chat/work separation. Plain conversation no longer becomes actionable work.
  Messages may carry an optional `intent`: `chat` is normal durable conversation
  that never derives a work item and never raises an Attention state, and
  `request` is an actionable ask. When `intent` is omitted a message stays
  actionable, so every existing tool, relay, and script is unchanged. Work-item
  derivation now skips `chat` threads (a chat thread becomes work only when an
  actionable follow-up is posted into it); runs and conversations gain a `chat`
  status; the default Active Run selection skips chat-only threads (they remain
  explicitly selectable); and a chat message no longer turns the health chip
  yellow. The console composer gains a mode selector defaulting to Message
  (chat), with Ask agent / Create work item (actionable) and Request clearance
  (RTA); the compact operator quick box now posts normal chat. No schema or
  validator change, no new dependency, no model API.
- Conversation Workspace. New "Conversations" view makes ClearWright
  conversation-first: a thread list with status/Codex/count badges, a readable
  message timeline, and a prominent composer ("Send Agents a Message") that
  continues the selected thread or starts a new one, posting real inbound
  `OPERATOR-0001` messages. A target selector (All / Claude / Codex / Operator
  note) adds a plain intent label to the message text and never claims a model
  participated - replies appear only when a worker actually posts back through
  the local adapter. Small escalation actions: Mark reviewed (durable
  acknowledge note), Create work item (real follow-up request), and Request
  clearance packet (RTA via the existing request intake) - normal chat needs no
  packet; packets remain the authority layer for governed changes. New read-only
  `GET /api/conversations` returns the same derived thread summaries as
  `/api/runs` (same filters), and thread retrieval reuses
  `/api/active-run?thread_id=`. Local communications stays as a compact recent
  feed; Active Run, Run Registry, History, Health, Durable Record, and the pulse
  inspector are unchanged. No schema or validator change, no new dependency, no
  Discord, no model API.
- Archive-aware durable record and pulse inspector. Terminal packets
  (DONE/DTA/SUPERSEDED) older than a 24-hour recent-terminal window are flagged
  `archived` in `/api/state` and collapsed in the console behind a compact
  "N archived completed packets" line with a Show completed toggle - a display
  flag only (files never moved or deleted; History, runs, and audit unchanged;
  failed packets never archived and still turn health red). The pulse object now
  carries inspector metadata (`active_phase`, `reason`, source thread / work
  item / packet ids, `expires_at` / `seconds_remaining`), reviewer messages
  (actor codex or role reviewer) now pulse Verification, and a compact pulse
  inspector under the workflow title shows why the graph is pulsing and when it
  stops ("Pulse: idle · no recent activity" when nothing is recent). The pulse
  visual is brighter with a stronger glow and a ~50% longer cycle with a long
  high-brightness hold (reduced-motion fallback included), and the graph keys on
  pulse booleans only so the ticking countdown cannot restart the animation.
  The health chip tooltip now states the top reason (e.g. "Attention: 1 open
  work item"). Durable-record wording clarifies it is a packet lane/audit
  snapshot, not the active work list. No schema or validator change, no new
  dependency, no Discord, no model API.
- System health and readiness panel. New read-only `GET /api/health` answers
  "is ClearWright ready to use right now?": mode, durable flag, queue root and
  lane checks, packet counts by lane, message/event/run/work-item counts, latest
  run timestamp, pulse, cheap capability checks (worker bridge, proof tool,
  Codex helper, `codex` on PATH), and plain-language warnings/errors with a
  green/yellow/red status (green = ready; yellow = attention such as open work
  items, demo mode, or Codex CLI missing; red = problem such as a missing queue
  root/lane or failed packets). The health check never runs Codex or tests and
  never mutates the queue; Codex availability is a capability probe only, never
  proof of participation. The console gains a compact topbar health chip
  (Healthy / Attention / Problem) that opens a read-only details panel. No
  schema or validator change, no new dependency, no Discord, no model API.
- Run registry and Active Run selector. New read-only `GET /api/runs` derives
  one summary per durable message thread (thread id, work item and packet ids,
  title, first/last timestamps, message count, actors, sources,
  open/claimed/responded status, Codex flag and parsed telemetry, latest-message
  preview) with simple filters (`limit`, `status`, `actor`, `source`,
  `has_codex`, `packet_id`); no new database. `GET /api/active-run` now accepts
  `?thread_id=<id>` to load a specific run (default behavior unchanged). The
  Active Run view gains a click-to-select run list with status/Codex/count
  badges; copy buttons and filters kept; History remains the full ledger. No
  schema or validator change, no new dependency, no Discord, no model API.
- Active run view and proof ergonomics. `clearwright_proof.py` and
  `clearwright_codex_review.py` now take an absolute `--repo` path and run
  git/tests/Codex with that as the subprocess working directory, so they work
  from any directory without `cd` or chained shell; the proof tool adds a
  `--server-url` preflight (`GET /api/state`) that fails clearly before posting
  anything if the server is down, and both add `--json`. New read-only
  `GET /api/active-run` returns the most recent thread grouped with `thread_id`,
  `work_item_id`, `packet_id`, its messages, and parsed Codex telemetry; a new
  **Active Run** view renders it as one readable thread with Active/Recent/All
  filters, copy buttons (thread_id / work_item_id / summary), and Codex telemetry
  shown as fields. Long panel copy stays behind `?` tooltips; the workflow
  tooltip now clarifies that pulse is recent activity, not permanent packet
  state. Docs updated (absolute paths reduce approval prompts). No schema or
  validator change, no new dependency, no Discord, no model API.
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
