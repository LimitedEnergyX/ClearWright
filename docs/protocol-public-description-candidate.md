# Review candidate: ClearWright Protocol public description

Reviewer note (NOT part of the published text). This is a short protected review of a proposed
PUBLIC description of the ClearWright Protocol and governance model. Review the candidate below for:
1. Factual accuracy against the current system (grounding facts follow).
2. Overclaim risk - especially maturity, military, regulated/safety-critical, and multi-user language.
3. Clarity and precision of the core claim (human authority; fail-closed; one-way authority; permanent record).
Keep the text SHORT and HONEST. Flag any sentence that a reasonable reader could take as a maturity,
capability, certification, or deployment claim beyond what is true today. Prefer plain language.

## Grounding facts (current, verifiable state - for accuracy checking only)
- ClearWright is LOCAL, SINGLE-OPERATOR, early-alpha / proof-of-concept. NOT multi-user, NOT publicly
  deployable, NOT production-ready. No certification or compliance claim. Not a certified command system.
- What exists and is exercised today: the clearance protocol (RTA / CTA / DTA / RFI) and a four-lane
  clearance queue; a local operator console with durable messages, threads, and work items; an
  automated Review Council that runs REAL independent GPT and Codex review of a plan and decides with
  a deterministic agreement rule over structured verdicts (never prose); the governed "Use CW" loop;
  fail-closed plan gates and fail-closed verification before completion; a fail-closed egress guard on
  the review-council dispatch path (provenance and composition binding, exact-byte and tripwire checks)
  with a dedicated internal_technical dispatch lane for governed self-review; a durable append-only
  audit trail; and a crash-safe archive layer.
- Authority is one-way and operator-only for terminal actions: reviewers judge quality but never grant
  clearance; close, grant-proceed, and clear-to-act (CTA) are operator-only; the system never mints its
  own authority.
- Self-improvement status: PLANNING for the first self-improvement capability (ALF, Automated Leapfrog,
  Phase 1) is COMPLETE and passed a two-reviewer plan gate on 2026-07-20. NO implementation authority
  has been granted and NO ALF implementation code exists yet.
- Honesty rule the project holds itself to: every public capability claim must be backed by evidence,
  and unsupported claims are downgraded or removed.

---

# CANDIDATE (this is the text proposed for publication)

## ClearWright Protocol: Human Authority for AI-Assisted Work

ClearWright is a local, operator-controlled clearance, review, and audit layer for AI-assisted work.
It is an early reference implementation of the ClearWright Protocol. It is not a multi-user platform,
not a certified command system, and not production-ready for regulated or safety-critical deployment.
It is a working single-operator reference implementation, intended for an operator who wants AI
assistance without surrendering authority.

The core idea is simple and strict:

- AI may prepare plans and perform work inside an approved scope.
- Independent reviewers may challenge those plans.
- Only an explicit, durable human authorization releases the next step.
- The system stays fail-closed. Absence of clearance is treated as denial.
- Every decision, including the authorization itself, is written to a durable, append-only audit record.

Authority flows in one direction only. The system never becomes the source of permission. Reviewers
judge quality; they never grant clearance. Terminal actions (close, grant-proceed, clear-to-act) remain
operator-only.

### Where the pattern is useful

At the conceptual level, the same shape may be relevant to domains where:

1. Someone prepares a plan or proposed action.
2. A higher-authority human must explicitly clear it before execution.
3. Independent review or challenge is valuable.
4. An audit trail of who authorized what, when, and under what scope matters.
5. Proceeding without clearance is unacceptable.

Concrete domains where this shape appears:

- High-stakes operational planning (formal command-authorization processes are one instance of a
  broader class).
- Regulated or safety-critical work where a responsible person must sign off before irreversible steps.
- Enterprise AI governance inside organizations that already require human approval gates for certain
  classes of action.
- Research and development environments that want AI assistance but refuse to let the model become the
  authority.
- Any workflow that currently relies on informal chat and human memory and would benefit from turning
  that into durable, enforceable clearance records.

These are examples of where the governance pattern resembles existing human-approval workflows. They
are not claims that ClearWright is deployed, certified, compliant, or ready for use in those domains.

The value is not "AI runs the work."
The value is "AI can prepare and be challenged, but only the authorized human can release the action,
and the decision is written to a durable, append-only audit record."

### Current maturity

ClearWright today is a local, single-operator, early-alpha proof of concept that implements and
exercises these mechanisms locally for governed workflows: a clearance queue, a durable operator
console and an append-only audit record, an automated review council that runs real independent review
by two separate AI models through a fail-closed egress guard, and fail-closed gates and verification
before completion. Planning for its first self-improvement capability is complete and has passed a
two-reviewer plan gate; no implementation authority has been granted and no such code exists yet. The
clearance, review, and audit pattern is the core idea; the local implementation is an evolving proof of
concept.
