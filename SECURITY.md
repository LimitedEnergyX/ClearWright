# Security Policy

## Reporting a vulnerability

If you discover a security issue in ClearWright, please report it privately rather
than opening a public issue.

- Use the repository's private vulnerability reporting (GitHub Security Advisories)
  if enabled, or contact the maintainer directly.
- Include a clear description, steps to reproduce, affected files or commands, and
  any suggested mitigation.
- Please allow reasonable time for a fix before any public disclosure.

## Scope

This project is local-first and single-machine at present. It does not run a
network service, a daemon, or a scheduler, and it does not manage secrets. The
most relevant concerns are:

- Handling of clearance packet files on the local filesystem.
- Correctness of validation, claim, and lifecycle transitions.
- Ensuring runtime packet contents and any environment secrets are never
  committed (see `.gitignore`).

## What is out of scope

- Third-party services, credentials, or infrastructure not contained in this
  repository.
- Anything requiring access to private or production systems.

## Handling of secrets

Never commit `.env` files, credentials, tokens, or private keys. Runtime packet
contents are treated as local data and are excluded from version control.
