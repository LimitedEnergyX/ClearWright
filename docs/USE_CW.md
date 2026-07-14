# Use CW: the executable skill

"Use CW to do X" turns a task into an automatic governed loop with no manual
copy/paste between models: create a conversation and work item, get real
independent GPT + Codex review through the Review Council, proceed inside the
operator-approved scope only when the deterministic agreement rule is met, post
progress, consult an Incident Council on glitches, run a Verification Council on
the result, and record completion. The operator supplies the task and the scope;
the skill runs the workflow.

This is early alpha software and a local reference implementation.

## One entry point

`tools/clearwright_use_cw.py` is the single, stable command surface the skill
drives. It DELEGATES to the existing helpers (`clearwright_message`,
`clearwright_work`, `clearwright_review_council`) and never duplicates them, and
it performs no destructive or outward-facing action itself.

    python tools/clearwright_use_cw.py <command> <queue_root> ... --json

Commands: `preflight`, `start`, `plan`, `council`, `incident`, `verify`,
`progress`, `complete`, `status`, `schema`. Every command emits compact JSON,
preserves `thread_id`, `work_item_id`, `packet_id`, and `council_id`, and
appends a metadata-only line to the queue's `invocation_log.jsonl` (including
failed invocations; never prompts, artifact content, or secrets).

### Exit codes (the skill branches on these)

    0  completed / agreement threshold met -> continue
    2  revision or another review round required
    3  operator required (incl. a classification conflict)
    4  reviewer unavailable (attempt budget exhausted; not retryable by rerun)
    5  hard gate (incl. preflight failure and packet over budget)
    6  required authority not granted (a governed change without clearance)
    7  usage or validation error (bad flags, invalid schema, round bounds)
    8  runtime failure

### Reliability layer

- **`preflight`** checks readiness (key present as a boolean + source — never
  the value — including the Windows User-scope fallback; Codex on PATH +
  version; queue writable; budgets; round bounds) and exits 5 with exact
  remediation steps. `start` re-runs the cheap checks implicitly and creates
  nothing on failure (the invocation-log line is still written).
- **Structured task envelope** (`start --envelope-file`, see `schema envelope`)
  is the primary classification input: `excluded_actions` carry the operator's
  guardrails and are never read as risk; a conflict between `intended_actions`
  and the approved scope exits 3 instead of silently inheriting either
  classification. Free-text `--request` remains as a lexical fallback with
  exclusion-section stripping. `verification_required` is recorded at start
  (governed/high-risk clamp to true).
- **Attempt budget**: at most two adapter calls per reviewer per substantive
  round (initial + one retry), persisted across reinvocations; changing the
  packet or config never grants more attempts. Exhaustion returns exit 4;
  continuing requires a new council or an explicit operator-authorized recovery
  (`--grant-attempts <reviewer> --operator-message-id <durable operator
  message>`), recorded on the council. Failed rounds are not counted toward the
  2-5 substantive-round budget, and no command can create a sixth round
  (`2 <= --min-rounds <= --max-rounds <= 5`).
- **Packet budgets**: dispatch fails fast (exit 5, no attempt spent) when the
  final assembled packet exceeds the phase input budget — plan/incident 32,000
  and verify 96,000 ESTIMATED input tokens (`ceil(chars/3.0)` by default;
  `CLEARWRIGHT_GPT_{PLAN,INCIDENT,VERIFY}_INPUT_BUDGET`,
  `CLEARWRIGHT_TOKEN_ESTIMATE_DIVISOR`). Codex prompts travel via stdin (no
  Windows argv ceiling) and its timeout scales with packet size
  (`CLEARWRIGHT_CODEX_TIMEOUT_BASE/_PER_100KB/_CAP`). Estimates are labeled and
  never reported as actual usage; actual GPT token usage is recorded when the
  API returns it.
- **`schema <envelope|verdict|reconciliation>`** prints each contract with rules
  and a valid example, and **`--stage reconcile --dry-run`** validates a
  reconciliation (schema + exact-ref binding against the real latest round) at
  zero reviewer cost.

### Artifact & operator layer

- **Artifacts** (`--artifact <path>`, repeatable; remembered by the council):
  registered and pinned under `review_artifacts/` with the FULL sha256 as the
  identity, re-verified before every dispatch (tampering is a hard stop).
  Delivery is capability-aware — GPT gets the full line-numbered artifact
  inline under budget, else a bounded excerpt pack with a manifest naming the
  full artifact's hash and stating the excerpts are the only evidence it may
  rely on; Codex gets the absolute pinned path + expected hash to read from
  disk. Derived renderings carry their own hashes linked to the original.
- **`blocked_by_capability`** reconciliation disposition: the reviewer is right
  and the harness cannot comply. Requires a limitation statement + evidence,
  never counts as resolved, escalates `operator_required` immediately, and can
  never coexist with `ready_to_proceed: true`.
- **Completion gate**: DONE is permitted only when verification was not
  required, or the bound verify council reached agreement. An unpassed council
  — or required verification that was never run — refuses DONE (exit 3,
  `verification_incomplete`).
- **`close` (operator-only)**: `CLOSED_BY_OPERATOR` /
  `accepted_with_verification_incomplete` — the human closes without a false
  DONE. Requires a closure-specific authority record: an inbound operator
  message created after the failed outcome, naming the work item or verify
  council and explicitly authorizing closure. Never invoked autonomously; the
  underlying council outcome is unchanged; history and evidence remain intact.
- **Canonical summary**: generated and posted by the HARNESS at terminal events
  (a durable `use-cw-summary` message, `summaries/<id>.json`,
  `status --summary`, and read-only `GET /api/work-summary`). The skill presents
  it and never authors governance status. **`retrospective`** reports
  usage/failures from the invocation log.
- **Recovery grants** are retry-specific and additive: the operator's authority
  message must name the council and reviewer and explicitly authorize attempts,
  be created after exhaustion, and each grant records how many attempts it
  added (`--grant-count`, default 1). Grants never affect the 2-5 substantive
  round ceiling.

## Flow

1. **start** — creates or continues a conversation and, for actionable work,
   creates and claims a work item. Chat-only requests stay chat (never a work
   item). Governed / high-risk requests are flagged `requires_clearance`.
2. **plan / council** — run real GPT + Codex rounds (`--stage review`), then
   attach Claude's reconciliation (`--stage reconcile --reconciliation-file`),
   2–5 rounds, until exit 0 or a stop (3/4/5). Round one is independent; a secret
   scan on the context is a hard gate.
3. **proceed** — only on exit 0 and only inside the approved scope. Post progress
   with `progress`.
4. **incident** — on a routine glitch, run a focused Incident Council before
   asking the operator.
5. **verify** — before DONE, run a Verification Council over the actual diff /
   test / CI / smoke evidence.
6. **complete** — record the final response and mark the work item done. For a
   governed change, pass `--packet-id`; exit 6 means its clearance packet is not
   in `clearance_done` (operator authority not granted).

## Authority and secrets

Council agreement never grants authority; the operator's approved scope does.
Within that scope the skill proceeds automatically; it stops for the operator
only on a hard gate (secrets, force-push, destructive delete, deploy/publish,
repo settings, billing, access control, private data, unclear scope, required
RTA/CTA not granted, or unresolved blockers after five rounds). Action-time
authorization against the approved scope lives in this execution layer; the
council engine itself takes no actions.

`OPENAI_API_KEY` is read only from the environment by the GPT adapter; the
wrapper and skill never print, paste, or store it.

## Installing the personal skill

The repository skill is `.claude/skills/use-cw/SKILL.md`. Install it to the
personal skills directory safely with:

    python tools/install_use_cw_skill.py

The installer backs up any existing version first, writes atomically, never
touches unrelated skills, prints the installed path and version, and verifies
the installed file matches the repository version. Use `--dry-run` to preview
and `--target` to override the destination.

See [REVIEW_COUNCIL.md](REVIEW_COUNCIL.md) for the council engine it drives.
