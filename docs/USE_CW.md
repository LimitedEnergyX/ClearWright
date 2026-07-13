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

Commands: `start`, `plan`, `council`, `progress`, `incident`, `verify`,
`complete`, `status`. Every command emits compact JSON and preserves
`thread_id`, `work_item_id`, `packet_id`, and `council_id`.

### Exit codes (the skill branches on these)

    0  completed / agreement threshold met -> continue
    2  revision or another review round required
    3  operator required
    4  reviewer unavailable
    5  hard gate
    6  required authority not granted (a governed change without clearance)
    other nonzero  argument or runtime failure

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
