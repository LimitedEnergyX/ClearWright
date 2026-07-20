# ClearWright Protocol: Human Authority for AI-Assisted Work

ClearWright is a local, operator-controlled clearance, review, and audit layer for AI-assisted work. It is an early reference implementation of the ClearWright Protocol. It is not a multi-user platform, not a certified command system, and not production-ready for regulated or safety-critical deployment. It is a working single-operator reference implementation, intended for an operator who wants AI assistance without surrendering authority.

The core idea is simple and strict:

- AI may prepare plans and perform work inside an approved scope.
- Independent reviewers may challenge those plans.
- Only an explicit, durable human authorization releases the next step.
- The system stays fail-closed. Absence of clearance is treated as denial.
- Every decision, including the authorization itself, is written to a durable, append-only audit record.

Authority flows in one direction only. The system never becomes the source of permission. Reviewers judge quality; they never grant clearance. Terminal actions (close, grant-proceed, clear-to-act) remain operator-only.

## Where the pattern is useful

At the conceptual level, the same shape may be relevant to domains where:

1. Someone prepares a plan or proposed action.
2. A higher-authority human must explicitly clear it before execution.
3. Independent review or challenge is valuable.
4. An audit trail of who authorized what, when, and under what scope matters.
5. Proceeding without clearance is unacceptable.

Concrete domains where this shape appears:

- High-stakes operational planning (formal command-authorization processes are one instance of a broader class).
- Regulated or safety-critical work where a responsible person must sign off before irreversible steps.
- Enterprise AI governance inside organizations that already require human approval gates for certain classes of action.
- Research and development environments that want AI assistance but refuse to let the model become the authority.
- Any workflow that currently relies on informal chat and human memory and would benefit from turning that into durable, enforceable clearance records.

These are examples of where the governance pattern resembles existing human-approval workflows. They are not claims that ClearWright is deployed, certified, compliant, or ready for use in those domains.

The value is not "AI runs the work." The value is "AI can prepare and be challenged, but only the authorized human can release the action, and the decision is written to a durable, append-only audit record."

## Current maturity

ClearWright today is a local, single-operator, early-alpha proof of concept that implements and exercises these mechanisms locally for governed workflows: a clearance queue, a durable operator console and an append-only audit record, an automated review council that runs real independent review by two separate AI models through a fail-closed egress guard, and fail-closed gates and verification before completion. Planning for its first self-improvement capability is complete and has passed a two-reviewer plan gate; no implementation authority has been granted and no such code exists yet. Beyond that governed two-model review lane, review and advising for this work have also been exercised through ChatGPT (API and WebUI), Codex, Claude, and Grok Pro WebUI. Grok currently participates via the WebUI as an out-of-band production advisor; full API integration into the governed review lane is pending. The clearance, review, and audit pattern is the core idea; the local implementation is an evolving proof of concept.
