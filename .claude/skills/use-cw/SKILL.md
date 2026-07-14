---
name: use-cw
description: >-
  Govern a task through ClearWright: create a conversation and work item, get
  real independent GPT + Codex review via the Review Council, proceed inside the
  operator-approved scope only when the deterministic agreement rule is met,
  post progress, consult an Incident Council on glitches, run a Verification
  Council on the result, and record completion. Use when the operator says "Use
  CW", "review this with CW", "run this through CW", "govern this through CW",
  "check this with GPT and Codex", or invokes /use-cw.
version: 1.2.0
---

# Use CW

When the operator says **"Use CW to do X"** (or a close variant), govern the task
through ClearWright with no manual copy/paste between models. The operator gives
the task and the scope; you run the workflow.

Drive everything through the one stable entry point:

    python <repo>/tools/clearwright_use_cw.py <command> <queue_root> ... --json

Parse the single JSON response and branch on the **exit code**:

    0  completed / agreement threshold met  -> continue
    2  revision or another review round required
    3  operator required (incl. classification conflict)
    4  reviewer unavailable (attempt budget exhausted -- do NOT hammer; see below)
    5  hard gate (incl. preflight failure, packet over budget)
    6  required authority not granted (governed change without clearance)
    7  usage or validation error (bad flags, invalid schema, round bounds)
    8  runtime failure

Use absolute paths and `--json`. Never print, paste, or store `OPENAI_API_KEY`;
the GPT adapter resolves it from the environment (including the Windows
User scope) itself. The queue root is the durable ClearWright queue (for this
operator, `D:/AI-Agents/ClearWright/runtime/queues/active`).

## Preconditions

1. Run `preflight <queue> --json` first (and note that `start` re-runs the cheap
   checks itself and exits 5 creating nothing if they fail). Exit 5 means stop:
   present the `remediation` steps to the operator verbatim, then retry after
   they act. Never ask the operator to paste the key to you.

## Start with a structured envelope

Build a **task envelope** (see `schema envelope`) instead of prose classification:

    {"task_kind": "analysis",
     "request": "<the operator's ask>",
     "approved_scope": "<what the operator authorized>",
     "intended_actions": ["fetch page", "inspect source", "produce report"],
     "excluded_actions": ["edit files", "deploy", "publish"],
     "operator_authority_source": "<which operator instruction authorizes this>",
     "verification_required": true}

Then: `start <queue> --envelope-file <path> --json`.

- `excluded_actions` are the operator's guardrails — list every prohibition
  there; they are **never** read as risk.
- kinds: **chat** (no work item) · **analysis** · **actionable** (work item
  created + claimed) · **governed / high_risk** (requires a clearance packet;
  agreement never substitutes for it).
- A conflict between `intended_actions` and the approved scope exits 3 — resolve
  it with the operator; never reclassify to make it pass.
- `verification_required` is recorded at start (governed/high-risk always true).

## Workflow

1. **start** — as above; returns `thread_id` and a claimed `work_item_id`.
2. **plan the council** — draft a bounded context packet (operator request, scope,
   constraints, your proposed plan, expected files, relevant excerpts/diffs,
   risks, open questions, explicit review questions). Do NOT include secrets or
   whole repositories. The assembled packet must fit the phase input budget
   (plan/incident 32K, verify 96K estimated tokens) or dispatch fails fast (exit
   5) without spending an attempt.

   **Artifacts:** when the thing under review is a document/page/file rather
   than a diff, register it with `--artifact <absolute path>` on the council
   command (repeatable; remembered by the council across rounds). CW pins it,
   owns the hash, delivers it capability-aware — full line-numbered inline to
   GPT when it fits the budget, a bounded excerpt pack + manifest when it does
   not, and the absolute pinned path + expected hash to Codex to read from
   disk. NEVER paste a large artifact into the context packet yourself.
3. **council (planning)** — loop:
   - `council <queue> --council-id <id?> --thread-id <id> --work-item-id <id> --phase plan --stage review --plan-file <ctx> --repo <repo> --approved-scope "<scope>" --json`
     runs one real GPT + Codex round.
   - Read both structured verdicts. Write a reconciliation JSON (see
     `schema reconciliation`): accept valid findings, reject incorrect ones
     **with evidence**, bind a resolution to each final-round
     `required_change`/`blocking_finding` by exact ref (e.g.
     `gpt.required_changes[0]` — no annotations), set `ready_to_proceed` honestly.
   - When a reviewer is RIGHT but the harness cannot satisfy the requirement,
     bind that ref with disposition `blocked_by_capability` (requires a
     `limitation` statement + evidence). It escalates `operator_required`
     immediately — never grind rounds against an impossibility, and never mark
     it `rejected` (the reviewer is not wrong).
   - **Validate first at zero cost:** `... --stage reconcile --dry-run
     --reconciliation-file <recon> --json` (exit 0 = valid and fully bound).
   - Then submit without `--dry-run`.
   - Repeat 2-5 rounds until exit 0 (agreement), or stop on 3/5.
4. **proceed** — only on exit 0, and only inside the approved scope. Use the tools
   available in your environment; pick the least-risky effective path. Post
   progress with `progress <queue> --work-item-id <id> --message-file <path> --json`.
5. **incident** — on a routine glitch, gather bounded evidence and run
   `incident <queue> --work-item-id <id> --phase incident --plan-file <incident-ctx> --json`
   (then reconcile). Proceed if the focused rule is met and the fix stays in
   scope. Ask the operator only on a hard gate, unresolved disagreement,
   reviewer failure after the attempt budget, or scope expansion.
6. **verify** — before DONE, run
   `verify <queue> --thread-id <id> --work-item-id <id> --phase verify --plan-file <evidence-ctx> --repo <repo> --approved-scope "<scope>" --json`
   (a NEW verify council requires `--thread-id`, and `--approved-scope` must be
   passed or agreement is blocked) with the actual diff, files changed, and
   test/CI/smoke results; reconcile; fix scoped findings and rerun.
7. **complete** — `complete <queue> --work-item-id <id> --result-file <path> --json`
   records the final response and marks the work item done. DONE is permitted
   only when verification was not required, or the bound verify council reached
   agreement; exit 3 (`verification_incomplete`) means run/finish verification —
   never work around it. For a governed change, pass `--packet-id`; exit 6 means
   the clearance packet is not in `clearance_done` — stop. On every terminal
   event the HARNESS generates and posts the canonical summary: **present that
   summary to the operator; never author or rewrite governance status yourself**
   (`status <queue> --summary <work_item_id>` returns it).

**`close` is operator-only.** You must NEVER invoke `close` autonomously. It
exists so the human can close a work item whose verification did not pass,
without presenting it as DONE, and it requires a closure-specific authority
record: an inbound operator message, written AFTER the failed outcome, naming
the work item or verify council and explicitly authorizing the closure. If the
operator wants to accept unverified work, ask them to post that message in CW,
then give them the exact `close` command to run — do not run it for them.

## Reviewer attempts and exit 4

Each reviewer gets at most **two adapter calls per substantive round**, persisted
across invocations — reinvoking the same round does NOT retry, and changing the
packet does not earn more attempts. On exit 4:

1. Diagnose the cause from `statuses` and the invocation log.
2. Fix the cause, then either start a **new council**, or ask the operator to
   authorize a recovery and pass
   `--grant-attempts <reviewer> --operator-message-id <id of the operator's
   durable authorizing message>`.
3. Never loop retries; never fabricate a reviewer result.

Rounds where a reviewer failed are NOT counted toward the 2-5 round budget.
`--min-rounds`/`--max-rounds` are available within `2 <= min <= max <= 5`; no
command can create a sixth substantive round.

## Authority and hard gates

Council agreement does **not** grant authority; the operator's approved scope
does. Within that scope, proceed automatically after agreement and do not ask
about routine details. Stop and ask the operator only for: secrets/credentials,
missing API provisioning, destructive deletion, force-push, unapproved
deploy/publish, repo settings/visibility/license/release/tag changes,
billing/payments, access-control changes, private-data exposure, unclear scope,
a required RTA/CTA not granted, a classification conflict, or unresolved
blockers after the round budget. When you must stop, give the operator the exact
numbered steps and wait.

Never fake GPT or Codex. Never claim participation without a real, successful,
recorded reviewer result. Never touch the private demo target.
