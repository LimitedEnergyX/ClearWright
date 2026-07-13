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
version: 1.0.0
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
    3  operator required
    4  reviewer unavailable
    5  hard gate
    6  required authority not granted (governed change without clearance)
    other nonzero  argument or runtime failure

Use absolute paths and `--json`. Never print, paste, or store `OPENAI_API_KEY`;
the GPT adapter reads it from the environment. The queue root is the durable
ClearWright queue (for this operator, `D:/AI-Agents/ClearWright/runtime/queues/active`).

## Preconditions

1. Check readiness first: `GET /api/health` (or `status`). Confirm
   `openai_api_key_configured` is true and `codex_cli_on_path` is true before a
   real council. If the key is missing, that is a hard gate: stop and give the
   operator exact steps to set `OPENAI_API_KEY` in their environment (never ask
   them to paste it to you), then retry.

## Classify the request

- **chat only** -> answer normally; `start` records it as chat (never a work item).
- **analysis/research** -> may run a planning council without executing changes.
- **actionable** -> `start` creates and claims a work item.
- **governed** (deploy, publish, schema/migration, delete, force-push, secrets,
  billing, production) -> requires a clearance packet / operator authority before
  execution. Agreement never substitutes for it.
- **high risk** -> treat as governed and stop for the operator on any hard gate.

## Workflow

1. **start** — `start <queue> --request-file <path> --approved-scope "<scope>" --json`.
   Records the operator request and approved scope; for actionable work, returns
   `thread_id` and a claimed `work_item_id`.
2. **plan the council** — draft a bounded context packet (operator request, scope,
   constraints, your proposed plan, expected files, relevant excerpts/diffs,
   risks, open questions, explicit review questions). Do NOT include secrets or
   whole repositories.
3. **council (planning)** — loop:
   - `council <queue> --council-id <id?> --thread-id <id> --work-item-id <id> --phase plan --stage review --plan-file <ctx> --repo <repo> --approved-scope "<scope>" --json`
     runs one real GPT + Codex round.
   - Read both structured verdicts. Write a reconciliation JSON: accept valid
     findings, reject incorrect ones **with evidence**, bind a resolution to each
     final-round `required_change`/`blocking_finding` by ref (e.g.
     `gpt.required_changes[0]`), set `ready_to_proceed` honestly.
   - `council ... --stage reconcile --council-id <id> --reconciliation-file <recon> --json`.
   - Repeat 2–5 rounds until exit 0 (agreement), or stop on 3/4/5.
4. **proceed** — only on exit 0, and only inside the approved scope. Use the tools
   available in your environment (filesystem/shell, Desktop Commander, Chrome
   MCP, git, python, ClearWright helpers); pick the least-risky effective path.
   Post progress with `progress <queue> --work-item-id <id> --message-file <path> --json`.
5. **incident** — on a routine glitch, gather bounded evidence and run
   `incident <queue> --work-item-id <id> --phase incident --plan-file <incident-ctx> --json`
   (then reconcile). Proceed if the focused rule is met and the fix stays in
   scope. Ask the operator only on a hard gate, unresolved disagreement,
   reviewer failure after retries, or scope expansion.
6. **verify** — before DONE, run
   `verify <queue> --work-item-id <id> --phase verify --plan-file <evidence-ctx> --repo <repo> --json`
   with the actual diff, files changed, and test/CI/smoke results; reconcile;
   fix scoped findings and rerun.
7. **complete** — `complete <queue> --work-item-id <id> --result-file <path> --json`
   records the final response and marks the work item done. For a governed
   change, pass `--packet-id`; exit 6 means the clearance packet is not in
   `clearance_done` (operator authority not granted) — stop.

## Authority and hard gates

Council agreement does **not** grant authority; the operator's approved scope
does. Within that scope, proceed automatically after agreement and do not ask
about routine details. Stop and ask the operator only for: secrets/credentials,
missing API provisioning, destructive deletion, force-push, unapproved
deploy/publish, repo settings/visibility/license/release/tag changes,
billing/payments, access-control changes, private-data exposure, unclear scope,
a required RTA/CTA not granted, or unresolved blockers after five rounds. When
you must stop, give the operator the exact numbered steps and wait.

Never fake GPT or Codex. Never claim participation without a real, successful,
recorded reviewer result. Never touch the private demo target.
