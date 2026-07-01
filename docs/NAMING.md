# Naming

This document is the canonical reference for what to call things in public-facing
material for ClearWright.

## The names

### ClearWright: the product and platform

ClearWright is the public product and platform brand. Use it as the front door:
the repository name, README titles, project descriptions, documentation headings,
and any external material. When a reader needs one name for the project, that name
is ClearWright.

### ClearWright Protocol: the specification

ClearWright Protocol is the underlying specification that ClearWright implements.
It defines how a request to act becomes an authorized, recorded, and auditable
outcome. ClearWright is the product; ClearWright Protocol is the specification it
runs on. See [CLEARWRIGHT_PROTOCOL.md](CLEARWRIGHT_PROTOCOL.md).

Protocol artifacts and concepts:

- clearance packet: the durable record of a request and its lifecycle
- clearance queue: the four-state filesystem queue the packets move through
- clearance: a granted authorization to act (a CTA)
- Request to Act (RTA), Clear to Act (CTA), Denied to Act (DTA), Request for
  Information (RFI)
- ClearWright Local Repo Profile: the enforceable local, single-machine profile

## Terminology rules

- Spell the product **ClearWright** (capital C, capital W, one word).
- Spell the specification **ClearWright Protocol**.
- Call the record artifact a **clearance packet**.
- Call the queue the **clearance queue**, with lanes `clearance_outbox`,
  `clearance_in_progress`, `clearance_done`, and `clearance_failed`.
- A DTA is a successful safety and governance outcome, not a failure. It archives
  to `clearance_done`, never to `clearance_failed`.
- `clearance_failed` is for execution or processing failure only.
- DEFER and FREEZE are not packet statuses.
- Authority level `0000` is an emergency root halt only. `0001` is the highest
  normal human command.

## What to avoid

- Do not present the specification as the front-door product name. The product is
  ClearWright; the specification is ClearWright Protocol.
- Do not use hype framing such as "agent swarm" or similar.
- Do not overclaim. Describe what exists today as existing, and what is planned as
  a documented direction. Avoid "production-ready", "certified", "compliant", and
  "open standard" unless and until such claims are separately and accurately
  established.

## Retired name

An earlier working name for the specification is retired and is not used in this
project. Public material uses ClearWright Protocol. The retired name is not a
synonym and should not appear in this repository.

## Visual identity

The primary mark is the hexagon, C, and check system, representing clearance,
bounded execution, and operator approval.

- Tagline: Build with clarity. Approve with confidence.
- Primary color: navy #021B3D
- Accent color: blue #103797
- Typography: Montserrat
- Brand assets: [../assets/brand/](../assets/brand/)

## Trademark

ClearWright is the subject of a pending U.S. trademark application (Serial No.
99912120; not yet registered). ClearWright&trade; signals the claimed mark; the
registered-trademark symbol is not used and registration is not claimed. This
document does not claim legal clearance, registrability, or availability for any
other name.
