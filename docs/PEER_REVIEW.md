# Peer Review

ClearWright is early alpha and public so the protocol, lifecycle model, and local
reference implementation can be reviewed in the open. It is human-commanded and
operator-controlled, and it is a local reference implementation, not a
production-ready system, an official or open standard, or a compliance framework.
Peer review is welcome, and honest, specific review is the most useful kind.

## 1. What review is most useful

Review that challenges the model is more valuable than review that only confirms
it. The following areas are especially useful:

- **Authority model clarity**: whether authority classes, ordering, and the
  operator-as-final-override are unambiguous.
- **CTA / DTA / RFI lifecycle correctness**: whether the clear, deny, and
  request-for-information transitions behave as documented.
- **Queue lane semantics**: whether the four physical lanes
  (`clearance_outbox`, `clearance_in_progress`, `clearance_done`,
  `clearance_failed`) admit only the intended statuses.
- **Audit trail completeness**: whether every decision and transition leaves a
  durable, reconstructable record.
- **Human-commanded boundaries**: whether the points that require human or
  delegated authority are correct and enforced.
- **Failure, supersede, stale, and revoke paths**: whether `FAILED`,
  `SUPERSEDED`, stale-lock recovery, and lease revocation are handled safely.
- **Naming clarity**: whether terms are consistent and unambiguous (see
  [NAMING.md](NAMING.md)).
- **Security and privacy assumptions**: whether the local, single-machine trust
  model and its assumptions hold.
- **Simplicity of the local reference implementation**: whether the tools stay
  small, readable, and stdlib-only, and where they could be simpler.

## 2. What is out of scope for now

These are intentionally not part of the current alpha. Review that assumes they
exist, or asks for them now, is less useful:

- Autonomous execution authority
- Daemons
- Schedulers
- Policy engines
- Discord automation
- SaaS claims
- Compliance or certification claims

## 3. How to review

- Open an issue for protocol questions or lifecycle concerns. The
  [protocol review issue template](../.github/ISSUE_TEMPLATE/protocol-review.md)
  gives a useful structure.
- Open a pull request for small docs corrections.
- Use clear examples when proposing lifecycle changes.
- Explain which status, lane, or authority boundary is affected.
- Keep examples generic.
- Do not include secrets, customer data, live clearance packets, or personal
  data.

## 4. Consensus and authority

- Peer consensus is useful for review quality.
- Consensus does not grant authority in the protocol.
- Human or delegated authority remains responsible for CTA / DTA / RFI decisions.

Consensus may support a review or a recommendation, but it never substitutes for
a human-commanded decision bounded by clearance. See
[CLEARWRIGHT_PROTOCOL.md](CLEARWRIGHT_PROTOCOL.md) and
[AUTHORITY_MODEL.md](AUTHORITY_MODEL.md) for the authority model.

## 5. Current implementation boundary

- The current public alpha includes local manual tools for validate, decide,
  claim, and lifecycle transitions.
- The multi-agent review and consensus loops shown in the target workflow are
  protocol-vision and end-of-alpha direction, not autonomous shipped automation.
  See [END_OF_ALPHA_TARGET.md](END_OF_ALPHA_TARGET.md) for that target state and
  its implementation-boundary note.
