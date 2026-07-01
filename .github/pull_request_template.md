# Pull Request

## Summary

Describe the change in one or two sentences.

## Motivation

Why is this change needed? Link any related issue or ADR.

## Changes

- What was added, changed, or removed.

## Validation

- [ ] `python -m py_compile` passes for changed tools
- [ ] `python -m unittest discover -s tests` passes
- [ ] Example clearance packet validates
- [ ] Schema loads: `sqlite3 :memory: < schema/clearance_packet.sql`
- [ ] Naming gate passes (no retired terms)
- [ ] No runtime packet contents, `.env`, secrets, or credentials committed

## Scope and safety

- [ ] Change is surgical and focused
- [ ] Docs and ADRs updated if a documented decision changed
- [ ] Describes what exists as existing; planned work marked as direction

## Notes

Anything reviewers should know, including known limitations.
