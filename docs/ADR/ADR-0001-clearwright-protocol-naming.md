# ADR-0001: ClearWright Protocol naming and clean public repository

**Status:** Accepted

**Date:** 2026-07-01

**Deciders:** Shawn C. Tovey, RCDD (CEO / highest human command)

---

## Context

The project needs a clean, public-facing home. An earlier working name for the
specification created a conceptual conflict with an unrelated external project in
the AI-agent delegation and evidence space. Continuing to publish under that name
would invite confusion. The implementation also grew inside a private lab
codebase whose codename and runtime identifiers are not suitable for a public
front door.

## Decision

1. The public product and platform brand is **ClearWright**.
2. The public specification name is **ClearWright Protocol**. The earlier working
   name is retired and does not appear in this repository.
3. The durable record artifact is a **clearance packet**. The queue is the
   **clearance queue** with lanes `clearance_outbox`, `clearance_in_progress`,
   `clearance_done`, and `clearance_failed`.
4. This repository is a fresh, curated public repository rather than a rename of
   the private lab. The private lab remains the internal development environment
   and is not referenced by codename in public material.
5. Tooling uses the `clearwright_` prefix: `clearwright_validate.py`,
   `clearwright_claim.py`, `clearwright_lifecycle.py`. The schema table and file
   are `clearance_packet`.

## Consequences

- Public docs, code identifiers, schema names, and tool names use ClearWright
  terminology only. A continuous-integration naming gate rejects any retired term.
- The four-state queue model, the authority model (`0000` emergency root halt,
  `0001` highest human command), and the rule that a DTA is a successful
  governance outcome (archived to `clearance_done`, never `clearance_failed`) are
  carried forward unchanged in meaning.
- History and provenance from the private lab are not imported; the lab is
  preserved separately and is not deleted.

## What this ADR does not decide

- The manual clearance decision tooling (CTA/DTA/RFI), the packet index, the
  canonical packet hash policy, and the unified operator command surface. Each is
  future work with its own change record.
