# Review Council: automated GPT and Codex review

The Review Council is how ClearWright coordinates independent review of a plan
by real GPT and real Codex, records every round durably, and decides whether
Claude may proceed using a **deterministic** agreement rule over structured
fields, never prose similarity. It removes manual copy/paste of reviews: the
council engine calls the reviewers, stores the results, and returns one
machine-readable JSON verdict with a meaningful exit code.

Every reviewer request is constructed and transmitted through a single
fail-closed egress guard, and governed self-review of ClearWright's own code
runs in a dedicated internal_technical (ITS) dispatch lane.

This is early alpha software and a local reference implementation.

## Honesty and authority

- GPT and Codex are never faked. A GPT reviewer message (actor `gpt`, source
  `openai-api`) is posted only after a real, successful OpenAI Responses API
  call validates against the structured contract. A Codex reviewer message
  (actor `codex`, source `codex-cli`) is posted only after a real, substantive,
  validated read-only Codex run. A missing key, an API error, a timeout, an
  empty run, or a malformed/invalid verdict posts **no** reviewer participation.
- **Council agreement does not create authority.** Authority comes from the
  operator's approved scope. Within that scope, agreement lets Claude proceed
  without asking; outside it, or at any hard gate, the operator decides.

## The two credentialed adapters

The adapter **code** lives in this repository; the **secret never does**.

- `tools/clearwright_gpt_review.py` calls the OpenAI Responses API using
  `OPENAI_API_KEY` read only from the process environment. The key is never
  returned, printed, logged, persisted, or written into any CW record or
  telemetry field, and the Authorization header is never logged. Standard-
  library HTTP only (no SDK dependency); bounded timeout; at most two retries on
  transient failures with bounded backoff. Model precedence: `--model`, then
  `CLEARWRIGHT_GPT_MODEL`, then the documented default `gpt-5.6-terra`
  (`gpt-5.6-sol` is reserved for critical/unresolved reviews). The model is
  never silently switched after a failure, and the actual model returned by the
  API is recorded.
- `tools/clearwright_codex_review.py` gained a `--structured` mode
  (`review_structured`) that returns the same structured verdict shape from a
  real read-only Codex CLI run. The existing telemetry-backed `review` behavior
  is unchanged.

## The structured verdict (shared contract)

`tools/clearwright_verdict.py` is the single source of truth. Each reviewer
returns:

    {
      "reviewer": "gpt" | "codex",
      "verdict": "approve" | "approve_with_changes" | "revise" | "block",
      "confidence": 0.0 .. 1.0,
      "risk_level": "low" | "medium" | "high" | "critical",
      "blocking_findings": [ ... ], "required_changes": [ ... ],
      "nonblocking_findings": [ ... ], "disagreements": [ ... ],
      "assumptions": [ ... ], "questions": [ ... ],
      "recommended_plan": [ ... ], "summary": "substantive text"
    }

Between rounds Claude records a reconciliation; every rejected finding must
carry evidence, so dissent is never summarized away:

    {
      "accepted_findings": [ ... ],
      "rejected_findings": [ {"finding": "", "reason": "", "evidence": [ ... ]} ],
      "required_plan_changes": [ ... ], "revised_plan": [ ... ],
      "unresolved_blockers": [ ... ], "ready_to_proceed": bool, "summary": ""
    }

## The engine

`tools/clearwright_review_council.py` runs the council. Phases: `plan`,
`incident`, `verify`; plus read-only `status`. Each phase has two stages:

- `--stage review` runs one round. Round one is independent: GPT does not see
  Codex's output and vice versa. Before any reviewer call, a basic secret scan
  runs on the context packet; a probable secret is a hard gate and no reviewer
  is called. The assembled packet is also checked against the phase input
  budget (plan/incident 32K, verify 96K estimated input tokens) and dispatch
  fails fast — before any attempt is spent — when it cannot be delivered.
- **The engine is the sole retry owner.** One adapter invocation is exactly one
  API/CLI call. Each reviewer gets at most TWO total adapter calls per
  substantive round (initial + one retry with bounded backoff), persisted on
  the council record across reinvocations; a changed dispatch fingerprint
  (packet, model, config) never resets the budget — it only gates reuse of the
  cached validated result, so a real review from an aborted round is never
  re-spent. Exhaustion returns `reviewer_unavailable`; continuing requires a
  new council or an explicit operator-authorized recovery grant
  (`--grant-attempts` anchored to a durable inbound operator message, recorded
  on the council). A round in which a reviewer failed is NOT counted toward the
  round budget; only rounds where both reviewers produced validated reviews
  are substantive. The bounds `2 <= min_rounds <= max_rounds <= 5` are enforced
  in the engine itself — no caller can create a sixth substantive round.
- **Codex transport**: the prompt travels via stdin (never argv, which Windows
  caps at ~23 KB effective), and the Codex timeout scales with packet size.
- `--stage reconcile --reconciliation-file <path>` attaches Claude's
  reconciliation to the latest round, posts it durably, and re-evaluates.

Council state is stored under the durable queue root at
`review_councils/<council_id>/` (`council.json`, `round-NN.json`,
`outcome.json`), written atomically and reload-safe, with no credentials.

### Deterministic agreement rule

`agreement_threshold_met` requires ALL of: at least two completed rounds; a real
GPT result and a real Codex result on the final round; neither verdict is
`revise` or `block`; no unresolved blocking finding; every final-round required
change and blocking finding bound by ref to a reconciliation resolution
(per-item map: accepted, planned, or rejected with evidence, not a disposition
count); each reviewer
confidence at least 0.70; Claude reconciliation `ready_to_proceed = true`; a
recorded operator `approved_scope`; and no hard gate. A numeric score, if shown,
is derived transparently from these fields and can never override a blocker, a
`revise`/`block` verdict, a missing reviewer, or a hard gate. After the maximum
rounds (default 5) without agreement, the outcome is `operator_required`.

The evaluator enforces the honesty invariant at the trust boundary, not only in
the adapters: a final-round reviewer counts as real only when its record has
`ok = true`, `posted = true`, a verdict that re-validates against the contract,
a matching reviewer identity, `validated = true`, and a `source` matching the
reviewer (`openai-api` / `codex-cli`). Each round records `context_sha256` (a
hash of the exact reviewed context) and the council records `approved_scope`
with an `approved_scope_sha256`, so agreement is bound to a specific reviewed
plan and scope and the execution layer (PR #26) can verify it enforces the same
scope that was reviewed. The council engine performs no actions; action-time
authorization against the approved scope and prohibited action classes is the
execution layer's responsibility (PR #26), which fails closed.

Outcomes: `agreement_threshold_met`, `needs_revision`, `reviewer_unavailable`,
`operator_required`, `hard_gate`.

### Exit codes (the skill contract)

    0  agreement_threshold_met  (Claude may proceed inside approved scope)
    2  needs_revision           (run another round or reconcile)
    3  operator_required
    4  reviewer_unavailable
    5  hard_gate
    other nonzero  argument or runtime failure

The compact JSON carries `council_id`, `thread_id`, `work_item_id`, `phase`,
`current_round`, `outcome`, `ready_to_proceed`, `operator_required`,
`hard_gate`, `gpt_status`, `codex_status`, and `unresolved_blockers`, so a
caller can parse one response and decide to proceed, revise, consult again, or
stop.

## Read-only API and health

The web server only READS council state; GPT and Codex are never run inside an
HTTP request handler. `GET /api/review-councils` (optionally
`?thread_id=<id>`) lists council summaries, and `GET /api/review-council?id=<id>`
returns the full council. The Conversation Workspace shows a **Review Council**
card with the outcome, phase, round, and per-round GPT/Codex verdicts and
reconciliation.

`GET /api/health` reports safe capability booleans only:
`gpt_helper`, `openai_api_key_configured` (a boolean, never the value),
`configured_gpt_model`, `codex_helper`, `codex_cli_on_path`, and
`council_available`. Health never invokes GPT or Codex.

## Provisioning

The full engine and its tests run with mocked reviewers and need no key. Before
the first real GPT call, verify only that `OPENAI_API_KEY` is present (a
boolean, never printed) and that the configured model is available. Set the key
in the local user environment; never commit, print, or paste it. If the key is
missing at runtime, that is a hard gate: stop and provision it, then retry.
