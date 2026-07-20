# Roadmap

ClearWright is an early local alpha: an operator-controlled authorization,
consensus, and audit layer for multi-AI-agent work. This roadmap is the
concise public summary of [PROJECT_PLAN.md](PROJECT_PLAN.md), which is the
strategic source of truth. Direction and priorities only - no dates and no
commitments.

## Current status

- Early **local alpha**, in daily governed use by its operator - including
  governing this repository's own development.
- Working today (evidence in [PROJECT_PLAN.md](PROJECT_PLAN.md) section 3):
  the clearance protocol and queue; a local operator console; real GPT and
  Codex Review Councils with structured verdicts and a deterministic
  agreement rule; the "Use CW" governed loop; fail-closed plan gates and
  fail-closed verification before completion; review profiles; message
  payload integrity; artifact and evidence handling; a durable archive
  layer with hash-bound operator approval and crash-safe recovery; canonical
  summaries and a durable audit trail; and a fail-closed egress guard on the
  review-council dispatch path with a dedicated internal_technical (ITS) lane
  for governed self-review of ClearWright's own code.
- A local long-running operator service exists. Fail-closed plan gates and
  review profiles exist. ClearWright does not create autonomous authority
  and does not ship an independent production policy engine or general
  scheduler.

## Next

- **Pre-Leapfrog Stabilization Gate: complete** (verified on merged code,
  2026-07-15). Message-scoped work-item identity, same-thread isolation,
  derived-queue completeness, gate idempotency, selected-task state
  isolation, server lifecycle evidence, and a safe manual launcher are done;
  the internal_technical classification repair is part of the merged baseline.
- **ALF Phase 1 planning** (Automated Leapfrog, active now): an internal,
  evidence-bound track that observes governed runs, records durable
  improvement findings, and proposes governed-work specifications for operator
  promotion. Phase 1 is planning only - schemas and architecture to a plan
  gate - and creates no autonomous authority. It is the concrete
  self-improvement vehicle feeding Project Leapfrog.
- **Project Leapfrog** (next major implementation milestone): one trusted
  Stable ClearWright instance governs development and verification of one
  isolated Candidate instance - separate runtime roots and secrets, an
  explicit permitted-channel matrix, promotion only under durable operator
  authority, verified rollback, and a first end-to-end promotion proof.

## Later (in dependency order)

1. Council efficiency and observability (bounded packets, scoped review,
   cost/latency metrics - without weakening deterministic agreement).
2. Multi-user identity, SSO, and workspace isolation.
3. Project registry and scoped connector.
4. Installer and deployment (only after identity, connector, and migration
   rules stabilize).
5. Reliability, recovery, and policy packs.
6. Trusted pilot, then public alpha.

## Non-goals and honest limitations

- Early alpha; **not production-ready**.
- No SaaS claim.
- No certification or compliance claim.
- ClearWright does not create its own authority. Work proceeds only within
  operator-defined scope and recorded delegation.
- ClearWright may operate over an administrator-selected private network;
  it **does not provide or manage VPN infrastructure**.
