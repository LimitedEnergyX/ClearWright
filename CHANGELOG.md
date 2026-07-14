# Changelog

All notable changes to this repository are recorded here. Dates and release tags
will be added when releases begin.

## Unreleased

- Local Council site information-architecture completion (corrective
  follow-up to the hardening/redesign PR, same governed work item). The
  operator desktop is now three regions: a compact grouped work queue
  (Attention/Active/Recent/Archived; rows show title, status, phase hint,
  age, and the attention reason), a center task workspace under a
  persistent selected-task header (title, work-item id, status, phase,
  council, gate, claim, next action) with a six-phase stepper
  (Request/Plan Review/Authority/Execute/Verify/Complete) replacing the
  workflow canvas — only the selected task's current phase animates,
  operator-required renders amber and static — and a right operator panel
  (next action, authority state, compact zero-state clearance card,
  composer, actions). Navigation is Command Center / Work / History with
  Attention as a count/filter chip; the unified Work page
  (Overview/Conversation/Councils/Evidence/Audit) replaces the
  Conversations and Active Run top-level views; History is one filterable
  ledger (`GET /api/ledger`) across packets, messages, and agent events,
  active and archived, with row-click detail, and hosts the clearance
  lanes; the Tool Log is a developer surface (hidden by default,
  Ctrl+Shift+L). New read-only routes: `/api/task-state`,
  `/api/archive-index`. Live-acceptance fixes: a superseded
  operator_required council no longer flags a thread as Attention forever;
  the Work composer docks sticky at the viewport bottom; the page never
  scrolls horizontally.
- ClearWright hardening and Local Council site redesign (skill v1.4.0).
  **Plan-level gate enforcement**: a plan or incident council that ends
  `operator_required`/`hard_gate` now creates a durable, unresolved gate on
  the work item; the governed workflow (progress, every council call,
  verify, complete) fails closed with a new exit 9 until a durable, post-gate
  operator message explicitly authorizes proceeding (`grant-proceed`); the
  original task request never satisfies a later gate; the gate and its
  linked authority now surface in both the conversation timeline and the
  canonical summary. **Council efficiency and review profiles**:
  `review_profile: code|editorial` on the task envelope, prompt-only role
  lanes and scoped follow-up-round guidance that never touch the
  deterministic agreement rule. **Archive layer**: a journaled, crash-safe
  archive command (dry-run and hash-bound-approval execute) moves old
  terminal and smoke/proof records into `runtime/queues/archive/<YYYY-MM>/`
  with zero data loss, archive-aware reads, a shared writer/archive
  mutual-exclusion primitive (`clearwright_writer_lock`), and automatic
  invocation-log rotation; see `docs/ARCHIVE_OPERATION.md`. **Local Council
  site redesign**: a task workspace (Overview/Conversation/Councils/
  Evidence/Audit tabs bound to one selected item), a shared popover manager
  (outside-click/Escape/navigation dismissal, single-popover, focus
  restore) applied to System Health, Tool Log and the Live agent feed
  collapsed by default, and a compact empty-request state. **Message
  payload integrity**: both operator composers are multiline
  auto-growing textareas with one canonical content contract, a documented
  65536-byte limit enforced identically client/server, atomic thread-scoped
  idempotency, binding-scoped post-write verification, and draft
  preservation across navigation and retries; see
  `docs/LOCAL_COMMUNICATIONS.md`. The public website is untouched.
- Streamlined Desktop "Use CW" workflow (from the passed acceptance run). The
  skill (v1.3.0) codifies standing defaults so the operator is no longer asked
  settled questions: read-only means no changes to the target product, repo,
  deployment, or external systems while CW governance records and runtime
  evidence are always permitted; "operator interface" defaults to the web UI;
  the local execution transport (e.g. Desktop Commander) is an implementation
  detail, never an operator question. Each council round now posts a bounded
  plan/context digest into the conversation timeline (full packet stays
  hash-bound in the council record), so the thread reads as the complete
  exchange: request -> plan digest -> reviews -> reconciliation -> outcome.
  Canonical summary posting is idempotent on the summary's semantic fingerprint
  (status/outcome/councils/findings; volatile usage counters and timestamps
  never cause duplicate posts). docs/ACCEPTANCE.md records the passed
  acceptance fixtures, and tests/test_use_cw_e2e.py replays the exact Desktop
  invocation sequence (start envelope -> 2-round plan council -> agreement ->
  2-round verify council -> agreement -> DONE) with mocked reviewers.
- Artifact & operator layer (PR 2 of the acceptance-hardening design). New
  `tools/clearwright_artifacts.py`: artifacts are registered and pinned under
  `review_artifacts/` with the FULL sha256 as identity (short ids are display
  aliases with collision detection), re-verified before every dispatch
  (tampering is a hard stop), and derived renderings carry their own hashes
  linked to the original. Reviewer delivery is capability-aware: GPT (text-only)
  always receives its capability statement and gets the full line-numbered
  artifact inline when the phase budget allows, else a bounded excerpt pack
  whose manifest states it is the only evidence it may rely on; Codex receives
  the absolute pinned path + expected hash and reads from disk (empirically
  verified: the read-only sandbox restricts writes, not reads). New
  `blocked_by_capability` reconciliation disposition: the reviewer is right and
  the harness cannot comply — requires a limitation statement + evidence, never
  counts as resolved, escalates `operator_required` immediately, and can never
  coexist with `ready_to_proceed` (the agreement invariants are preserved and
  strengthened). `verification_required` is now enforced at completion: DONE is
  permitted only when verification was not required or the bound verify council
  reached agreement — absence of a council never bypasses it (envelope records
  are persisted for lexical starts too). New OPERATOR-ONLY `close` command:
  CLOSED_BY_OPERATOR / `accepted_with_verification_incomplete`, requiring a
  closure-specific authority record (an inbound operator message, created after
  the failed outcome, naming the work item or verify council and explicitly
  authorizing closure — the original task approval is not sufficient); it is
  never DONE and never changes the council outcome. Attempt-recovery grants are
  now retry-specific and additive: the authority record must name the council,
  the reviewer, and explicitly authorize attempts, be created after exhaustion,
  and grants record how many attempts were added — they never touch the
  substantive round ceiling. The harness now generates and posts the canonical
  concise summary at terminal events (durable `use-cw-summary` message +
  `summaries/<id>.json` + `status --summary` + read-only `GET /api/work-summary`);
  the skill presents it and never authors governance status. New
  `retrospective` command reports usage/failures from the invocation log.
  Skill v1.2.0.
- Council reliability pass (from the first full acceptance run). Codex prompts
  now travel via stdin (never argv, which Windows caps at ~23 KB effective) and
  the Codex timeout scales with packet size. The council engine is the sole
  retry owner: adapters make exactly one call per invocation, each reviewer gets
  at most two total adapter calls per substantive round (persisted across
  reinvocations; a changed packet/config never resets the budget, it only gates
  reuse of the cached validated result), failed rounds are not counted toward
  the 2-5 substantive-round budget, and `2 <= min_rounds <= max_rounds <= 5` is
  enforced in the engine itself. Recovery from an exhausted budget requires a
  new council or an explicit operator-authorized grant anchored to a durable
  operator message. Dispatch fails fast — before spending an attempt — when the
  assembled packet exceeds the phase input budget (plan/incident 32K, verify
  96K estimated input tokens; estimates labeled, actual GPT token usage
  recorded when returned). New `preflight` (readiness with exact remediation;
  implicit in `start`; the key is reported as a boolean + source — including a
  Windows User-scope registry fallback — never a value), `schema`
  (envelope/verdict/reconciliation contracts with rules and examples), and
  reconcile `--dry-run` (schema + exact-ref binding validation at zero reviewer
  cost). `start` accepts a structured task envelope as the primary
  classification input: excluded_actions carry the operator's guardrails and
  are never read as risk, intended-action/scope conflicts exit 3 instead of
  silently inheriting a classification, and `verification_required` is recorded
  at start (governed/high-risk clamp to true). Every command and every reviewer
  attempt (including failures) appends a metadata-only line to
  `invocation_log.jsonl` — never prompts, artifact content, or secrets. The
  deterministic agreement rule is unchanged.
- Executable "Use CW" skill. `tools/clearwright_use_cw.py` is one stable command
  surface (start / plan / council / progress / incident / verify / complete /
  status) that turns "Use CW to do X" into an automatic governed loop with no
  manual copy/paste: create a conversation and work item, run the Review Council,
  proceed inside the operator-approved scope only on agreement, consult an
  Incident Council on glitches, run a Verification Council on the result, and
  record completion. It delegates to the existing message / work-item / council
  helpers, emits compact JSON, preserves thread/work-item/packet/council ids, and
  uses stable exit codes (0 continue, 2 revision, 3 operator, 4 reviewer
  unavailable, 5 hard gate, 6 required authority not granted). Council agreement
  never grants authority; the operator's approved scope does. The repository
  skill lives at `.claude/skills/use-cw/SKILL.md`, installed by the safe
  `tools/install_use_cw_skill.py` (backup, atomic, verify). `OPENAI_API_KEY` is
  never printed or stored. See [docs/USE_CW.md](docs/USE_CW.md). No new
  dependency; no changes to the private demo target.
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
