# Changelog

All notable changes to this repository are recorded here. Dates and release tags
will be added when releases begin.

## Unreleased

- Agent conversation console (simulated): an "Ask ClearWright" panel where the
  operator asks a question, up to five locally simulated agent turns deliberate
  (analysis, challenge, code/test impact, revised recommendation, final
  review), and ClearWright condenses a recommended CTA / DTA / RFI with risks
  and a proposed next action. No real external model integration; consensus
  does not grant authority.
- Control plane operator-console framing: a clearance-workflow panel, an
  incoming-clearance-request card, and a simulated live agent feed; the queue
  board remains visible as the durable record. Packet intake is passive: an
  Inject demo request form (backed by a new manual intake tool,
  `tools/clearwright_request.py`) simulates what an agent, tool, or integration
  would submit. Completion results (summary, verification, changed files,
  findings) are stored as one nested `results` object on the DONE audit event.
  No schema change.
- Added peer-review guidance for protocol, lifecycle, authority, and
  implementation feedback (`docs/PEER_REVIEW.md`, a protocol review issue
  template, and README/CONTRIBUTING pointers).
- End-of-alpha target workflow document (`docs/END_OF_ALPHA_TARGET.md`):
  protocol-vision diagram of the human-command-to-final-output flow, labeled as
  target state rather than a current-state implementation claim.
- Manual clearance decision tool (`tools/clearwright_decide.py`): clear (CTA),
  deny (DTA), or request information (RFI) on one outbox packet.
- Repository baseline hardening.
- Example clearance packet language sanitized.
- Legacy PR and ADR references removed.
- Tool docstrings scrubbed of legacy references.
- Public posture documentation in progress.
