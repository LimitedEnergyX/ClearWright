# Contributing to ClearWright

Thank you for your interest in ClearWright. Contributions are welcome but
controlled: this project values small, reviewed, well-scoped changes and a durable
record of decisions.

## Ground rules

- Keep changes surgical and focused. One logical change per pull request.
- The operator is the highest authority. Merges are gated by review; no author
  merges their own pull request without approval.
- Describe what exists as existing and what is planned as a documented direction.
  Avoid overclaiming.
- Use the naming rules in [docs/NAMING.md](docs/NAMING.md). Retired terminology is
  rejected by continuous integration.

## Naming and public language

- Follow the naming rules in [docs/NAMING.md](docs/NAMING.md), which the
  continuous-integration naming gate enforces.
- Do not reintroduce retired or legacy names and references: the earlier protocol
  name, the legacy lab codename, retired packet or queue identifiers, or old pull
  request and decision-record numbers from the previous repository.
- Keep examples generic. Do not include secrets, tokens, customer data, live
  clearance packet data, or personal names.
- Keep public-facing language calm and non-hype. Describe what exists as existing
  and what is planned as direction.
- In authority examples, use `OPERATOR-0001` for the top normal human command and
  `CEO` rather than a personal name. Use `EMERGENCY_ROOT_HALT` / `0000` only for an
  emergency halt.

## Local development

The tooling is standard-library Python and uses no third-party runtime
dependencies.

```sh
# Compile the tools
python -m py_compile tools/clearwright_validate.py \
    tools/clearwright_claim.py tools/clearwright_lifecycle.py

# Validate the example clearance packet
python tools/clearwright_validate.py schema/examples/clearance_packet.example.json

# Load the schema
sqlite3 :memory: < schema/clearance_packet.sql

# Run the test suite
python -m unittest discover -s tests
```

## Pull requests

- Fill out the pull request template.
- Ensure the full test suite passes and the continuous-integration checks are
  green, including the naming gate.
- Do not commit runtime clearance packet contents, `.env` files, secrets, or
  credentials.
- Prefer stdlib-only tests unless a framework is intentionally adopted and
  recorded.

## Decision records

Significant decisions are captured as Architecture Decision Records under
[docs/ADR/](docs/ADR/). If your change alters a documented decision, add or update
an ADR in the same pull request.
