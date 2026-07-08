# Sample project (local test project)

This folder describes a generic **sample web application** used only to demonstrate
the ClearWright clearance model. It is a stand-in for "some software project an
agent has been asked to work on." It contains no real product, no real data, and
no credentials.

The sample project is deliberately vague on purpose. What matters for the demo is
not the application itself but the **clearance decisions** an operator makes about
proposed actions against it.

## What the demo represents

An agent (a worker role) proposes actions against the sample web application. Each
proposed action becomes a **clearance packet** that waits in the **clearance queue**
for a human-commanded decision. The operator can:

- grant a **bounded clearance** (CTA) so the work may proceed once claimed,
- **deny** the request (DTA) as a governance outcome,
- or **request more information** (RFI) before deciding.

The demo ships three example requests:

1. **Add a health-check endpoint** (low risk). Intended to show the CTA path:
   grant, claim, complete.
2. **Bulk-delete inactive records** (high risk, irreversible). Intended to show the
   DTA path: the operator denies it, and that denial is a successful governance
   outcome, not a failure.
3. **Change authentication settings** (unclear scope). Intended to show the RFI
   path: the operator asks for clarification before deciding.

## Mission intake

The mission framing used by the control plane lives in
[mission.json](mission.json): mission name, target project label, allowed scope,
disallowed scope, a test command, and risk notes. It is display-only context.

## What this is not

This is an early-alpha, local reference implementation used for demonstration. It
is human-commanded and operator-controlled. It does not execute the proposed
actions, connect to any external service, or act on its own.
