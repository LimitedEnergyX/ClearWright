# Changelog

All notable changes to this repository are recorded here. Dates and release tags
will be added when releases begin.

## Unreleased

- Local agent event adapter: a durable agent-event log under the queue root
  (`agent_events/`), a `POST`/`GET /api/agent-events` local HTTP API, and a
  `tools/clearwright_agent_event.py` CLI so Claude Desktop, Codex, scripts, or
  future workers send real agent events into the control plane over CLI, curl,
  or local HTTP, without browser automation. The Web UI's live feed shows real
  local events distinctly from the simulated demo fallback. Agent events are
  not clearance packets and grant no authority; no schema or validator change.
- Agent conversation console (simulated): the operator asks agents a question,
  up to five locally simulated agent turns deliberate (analysis, challenge,
  code/test impact, revised recommendation, final review), and ClearWright
  condenses a recommended CTA / DTA / RFI with risks and a proposed next
  action. A bounded CTA recommendation can be sent to the clearance queue with
  one click: the packet is derived in the background, with no packet paperwork
  in the operator flow. No real external model integration; consensus does not
  grant authority.
- Control plane operator-console framing: a clearance-workflow panel, an
  incoming-clearance-request card, and a simulated live agent feed; the queue
  board remains visible as the durable record. Packet intake is passive: RTA
  packets come from agents, tools, scripts, or integrations through a new
  manual request tool (`tools/clearwright_request.py`) and its API, which are
  backend plumbing rather than a human form. Completion results (summary,
  verification, changed files, findings) are stored as one nested `results`
  object on the DONE audit event. No schema change.
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
