# Worker runbook: "use CW"

This runbook defines exactly what Claude (Desktop or Code), Codex, or any worker
should do when the operator asks to route work through ClearWright. It makes
"use CW" a real, repeatable worker behavior over the CLI and local HTTP, with the
web UI and history as the visible record.

This is early alpha software. It is a local reference implementation and is
not intended for production use.

## When it applies

Trigger this runbook when the operator says any of:

- "use CW"
- "review with CW"
- "run this under CW"
- "process this through CW"

## What Claude should do

1. **Confirm the server is alive.** Check `GET http://127.0.0.1:8787/api/state`
   (or the operator's host/port). If it does not respond, tell the operator and
   stop; do not fabricate results.
2. **Use the CLI or local HTTP, never a browser.** The integration path is
   `tools/clearwright_worker.py`, the other `clearwright_*` tools, and the local
   HTTP API. ChromeMCP is not the integration path.
3. **Find pending work.** Run:

       python tools/clearwright_worker.py next D:/AI-Agents/ClearWright/runtime/queues/active --actor claude

   or `GET /api/work-items`.
4. **If the request came inside Claude, not the ClearWright UI, post it in
   first, clearly labeled.** Record it as a real operator or worker message so
   the work is visible and durable:

       python tools/clearwright_message.py post <queue> --actor OPERATOR-0001 --role operator --message "Review this repo under CW."

   Then find the new work item with `next`.
5. **Claim the work item.**

       python tools/clearwright_worker.py claim <queue> --work-item-id message:msg-... --actor claude --role orchestrator

6. **Post progress while working.** Post short, honest progress notes as you go:

       python tools/clearwright_worker.py progress <queue> --work-item-id message:msg-... --actor claude --message "Reading repository state."

7. **Post the final response.**

       python tools/clearwright_worker.py respond <queue> --work-item-id message:msg-... --actor claude --message "Review complete. Recommendation: CTA for docs-only cleanup."

8. **If a clearance packet is required, create it through existing request
   tooling.** Use `tools/clearwright_request.py` or `POST /api/request` to derive
   an RTA; then it travels the normal clearance lifecycle (CTA / DTA / RFI ->
   claim -> DONE). The worker bridge never mutates the packet schema.
9. **Keep the web UI and history as the visible record.** Everything above shows
   up live in the operator console and in the read-only History view.

Note: claiming a `packet:<id>:cta` work item runs the real claim lifecycle and
moves the packet, so it then appears as an `in_progress:<id>` work item; continue
progress and responses on that id. The message flow (operator message -> claim ->
progress -> respond) keeps one work item id throughout.

## What Claude must not do

- **Do not claim that GPT, Codex, or any other model participated unless it was
  actually invoked.** If a model was not called, do not attribute messages to it.
  Post under your own actor, and say plainly that other models were not consulted.
- Do not use ChromeMCP or browser automation to drive ClearWright. The browser is
  the operator display only; use it for GitHub auth or a single visual check.
- Do not invent results, approvals, or clearances. Consensus and conversation
  grant no authority; the operator decides.
- Do not edit or delete the durable record by hand. Messages, events, and packets
  are append-only working and authority records.

## Handling unavailable GPT / Codex

If the operator expects a multi-model review but GPT or Codex is not actually
available in this environment, do not simulate them and do not present their
absence as participation. Post a real message stating that only the models
actually invoked were consulted (for example, "Reviewed by claude only; GPT and
Codex were not invoked in this run."), and continue with the real work. Simulated
multi-agent conversation lives only in demo mode and is never real participation.

Prefer the telemetry-backed helper for Codex so a hang or empty run can never be
mistaken for a real review:

    python tools/clearwright_codex_review.py path/to/queue --work-item-id <id> --actor claude --timeout 90

It runs Codex read-only with stdin from the null device and a hard timeout,
captures telemetry (exit code, elapsed seconds, byte and line counts, timed-out
flag), and only posts an `actor=codex, role=reviewer, source=codex-cli` message
when Codex exited cleanly and produced substantive output. If Codex is
unavailable, times out, or produces only the stdin-hang marker, it records that
as a `claude/orchestrator` note and claims no Codex participation. GPT / ChatGPT
are never claimed; they are not connected to this local ClearWright instance.

## Fewer approval prompts

Long chained shell commands trigger more approval prompts than single, focused
commands. Prefer one Python tool invocation at a time over `&&`-chains, pipes,
inline environment variables, redirects, and command substitution.

- Good: `python tools/clearwright_worker.py status path/to/queue`
- Good: `python tools/clearwright_worker.py progress path/to/queue --work-item-id <id> --actor claude --message "Running tests."`
- Good: `python tools/clearwright_proof.py path/to/queue --message "Worker proof smoke." --actor claude`
- Avoid: `cd ... && Q=... && WID=... && python ... | python -c ... && curl ... && git status`

`tools/clearwright_proof.py` runs the whole "use CW" flow (relay -> claim ->
progress -> optional tests -> optional Codex -> respond) in one command and
prints the `thread_id` and `work_item_id`, so no follow-up shell chaining is
needed. It never edits files, branches, commits, or opens a PR.

## Verifying in the UI and history

- The operator console (operator mode) shows the message thread and work item
  live, updating on a fast poll.
- Open the **History** view (top bar) or `GET /api/history` to see the packet,
  the full message thread, and any agent events, read-only, with filters.
- `python tools/clearwright_worker.py status <queue>` (or `GET /api/worker-status`)
  prints a small summary: open and claimed work items, packets by lane, and
  message and event counts.

## Stopping at hard gates

Stop and ask the operator, rather than proceeding, on any hard gate: a schema or
validator change, a new dependency, any secret or token (Discord, model API, or
otherwise), repository settings/license/release changes, a destructive action, a
force-push, a reference to a private target in the public repository, or unclear
scope. When in doubt, surface it instead of acting.

## Honest boundaries

- The **browser UI is the operator display.** The **CLI, local HTTP, and durable
  files are the integration path.** ChromeMCP is **not the integration** method.
- **Discord is a future** optional transport that will require explicit operator
  credentials; none are requested or stored here. ClearWright works locally first.
- **Operator mode is real-only**: real local messages, events, and work items.
  **Demo mode** is where simulation lives, and simulated demo conversation is not
  real agent participation.
- ClearWright is early alpha and not intended for production use.

See [LOCAL_COMMUNICATIONS.md](LOCAL_COMMUNICATIONS.md) and
[OPERATOR_MODE.md](OPERATOR_MODE.md) for the underlying loop and modes.
