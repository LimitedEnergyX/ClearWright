#!/usr/bin/env python3
"""
tools/clearwright_validate.py, ClearWright Protocol v0.1 packet validator.

Default mode validates packet field rules only. The optional --strict-path mode
additionally checks that the packet file lives in a known clearance queue
directory and that its status is valid for that directory. It does not move,
claim, or modify any packet; it only reads and reports.

Exit codes: 0 valid, 1 invalid packet, 2 file/parse error
"""
import argparse
import json
import os
import sys

REQUIRED_FIELDS = [
    "packet_id", "packet_type", "title", "requesting_agent",
    "created_at", "updated_at", "status", "source_path", "packet_hash",
]

ALLOWED_STATUS = {
    "RTA", "IN_REVIEW", "RFI_PENDING", "CTA", "IN_PROGRESS",
    "DTA", "DONE", "FAILED", "SUPERSEDED",
}

# Authority and coordination enums (validated when present; not required fields)
ALLOWED_AUTHORITY_CLASS = {
    "OPERATOR", "ORCHESTRATOR", "REVIEWER", "WORKER", "OBSERVER", "POLICY_ENGINE",
}

ALLOWED_CLEARANCE_CLASS = {
    "READ_ONLY", "DOCS_ONLY", "BRANCH_CODE", "QUEUE_MOVE",
    "EXECUTION_CANDIDATE", "HUMAN_REQUIRED",
}

ALLOWED_PRIORITY_CLASS = {
    "LOW", "NORMAL", "HIGH", "URGENT",
}

ALLOWED_CHANNEL_STATE = {
    "CLEAR", "BUSY", "BLOCKED", "STALE", "ESCALATED",
}

JSON_BLOB_FIELDS = [
    "inputs_json", "review_json", "rfi_json", "decision_json", "audit_json",
    "backpressure_json",
]

# Canonical clearance queue directories and the packet statuses valid in each.
# Derived from docs/QUEUE_MODEL.md. clearance_outbox holds the pre-claim
# states (RTA, IN_REVIEW, RFI_PENDING, CTA); a packet stays in the outbox until
# the claim-move into clearance_in_progress. DTA and SUPERSEDED are successful
# closed outcomes and live in clearance_done, never clearance_failed. clearance_failed
# is for execution/processing failure after a claim only.
QUEUE_STATUS = {
    "clearance_outbox":      {"RTA", "IN_REVIEW", "RFI_PENDING", "CTA"},
    "clearance_in_progress": {"IN_PROGRESS"},
    "clearance_done":        {"DONE", "DTA", "SUPERSEDED"},
    "clearance_failed":      {"FAILED"},
}


def validate(packet):
    errors = []

    # Required fields
    for field in REQUIRED_FIELDS:
        if field not in packet or packet[field] is None or packet[field] == "":
            errors.append("missing required field: {!r}".format(field))

    # Status enum
    status = packet.get("status")
    if status is not None and (not isinstance(status, str) or status not in ALLOWED_STATUS):
        allowed = ", ".join(sorted(ALLOWED_STATUS))
        errors.append("invalid status {!r}; allowed: {}".format(status, allowed))

    # Authority and coordination enum fields (optional; validated when present)
    for field, allowed_set in (
        ("authority_class",  ALLOWED_AUTHORITY_CLASS),
        ("clearance_class",  ALLOWED_CLEARANCE_CLASS),
        ("priority_class",   ALLOWED_PRIORITY_CLASS),
        ("channel_state",    ALLOWED_CHANNEL_STATE),
    ):
        value = packet.get(field)
        if value is not None and (not isinstance(value, str) or value not in allowed_set):
            allowed = ", ".join(sorted(allowed_set))
            errors.append(
                "invalid {} {!r}; allowed: {}".format(field, value, allowed)
            )

    # escalation_required must be boolean-like (bool, or int 0/1) when present
    esc = packet.get("escalation_required")
    if esc is not None:
        if not isinstance(esc, (bool, int)) or (isinstance(esc, int) and esc not in (0, 1)):
            errors.append(
                "escalation_required must be boolean or integer 0/1, got {!r}".format(esc)
            )

    # clearance_expires_at should be present when status is CTA or IN_PROGRESS
    if status in ("CTA", "IN_PROGRESS") and not packet.get("clearance_expires_at"):
        errors.append(
            "clearance_expires_at should be set when status is {!r} "
            "(CTA is a bounded lease)".format(status)
        )

    # JSON blob fields must be dict or list when not null
    for field in JSON_BLOB_FIELDS:
        value = packet.get(field)
        if value is not None and not isinstance(value, (dict, list)):
            errors.append(
                "field {!r} must be a JSON object or array, got {!r}".format(
                    field, type(value).__name__
                )
            )

    return errors


def validate_queue_path(packet_file, status, queue_root=None):
    """Strict-path checks (read-only).

    Verify the packet file lives in a known clearance queue directory and that
    its status is compatible with that directory, per docs/QUEUE_MODEL.md.
    Returns a list of error strings (empty when valid). This never moves,
    claims, or mutates the packet; it only inspects the path and status.
    """
    errors = []
    parent_dir = os.path.dirname(os.path.abspath(packet_file))
    queue_name = os.path.basename(parent_dir)

    if queue_name not in QUEUE_STATUS:
        known = ", ".join(sorted(QUEUE_STATUS))
        errors.append(
            "strict-path: packet is not inside a known clearance queue directory "
            "(parent directory is {!r}); expected one of: {}".format(queue_name, known)
        )
        return errors

    if queue_root is not None:
        expected_parent = os.path.abspath(os.path.join(queue_root, queue_name))
        if parent_dir != expected_parent:
            errors.append(
                "strict-path: packet is in a {!r} directory but not directly under "
                "--queue-root {!r} (resolved parent: {})".format(
                    queue_name, queue_root, parent_dir
                )
            )

    # Only assert status/location compatibility for a recognised status; an
    # unknown, missing, or non-string status is already reported by field validation.
    if isinstance(status, str) and status in ALLOWED_STATUS and status not in QUEUE_STATUS[queue_name]:
        allowed_here = ", ".join(sorted(QUEUE_STATUS[queue_name]))
        errors.append(
            "strict-path: status {!r} is not valid in {}/; that directory holds: "
            "{}".format(status, queue_name, allowed_here)
        )

    return errors


def build_parser():
    parser = argparse.ArgumentParser(
        prog="clearwright_validate",
        description=(
            "Validate a clearance packet JSON file against the ClearWright Protocol v0.1 schema.\n\n"
            "Exit codes:\n"
            "  0  valid\n"
            "  1  invalid packet\n"
            "  2  file not found, unreadable, or JSON parse error"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "packet_file",
        help="Path to the clearance packet JSON file to validate.",
    )
    parser.add_argument(
        "--strict-path",
        action="store_true",
        help=(
            "Also verify the packet file lives in a known clearance queue "
            "directory (clearance_outbox, clearance_in_progress, clearance_done, "
            "clearance_failed) and that its status is valid for that directory. "
            "Read-only: it never moves or claims packets. Off by default; "
            "default validation behaviour is unchanged."
        ),
    )
    parser.add_argument(
        "--queue-root",
        default=None,
        metavar="PATH",
        help=(
            "Optional. With --strict-path, also require the packet to live "
            "directly under PATH/<queue-dir>/ (for example: "
            "--queue-root orchestrator). When omitted, only the immediate "
            "parent directory name is checked."
        ),
    )
    return parser


def main():
    parser = build_parser()
    args = parser.parse_args()
    try:
        with open(args.packet_file, encoding="utf-8") as fh:
            packet = json.load(fh)
    except FileNotFoundError:
        print("ERROR: file not found: {}".format(args.packet_file), file=sys.stderr)
        sys.exit(2)
    except json.JSONDecodeError as exc:
        print(
            "ERROR: JSON parse error in {}: {}".format(args.packet_file, exc),
            file=sys.stderr,
        )
        sys.exit(2)
    except OSError as exc:
        print("ERROR: {}".format(exc), file=sys.stderr)
        sys.exit(2)

    if not isinstance(packet, dict):
        print("INVALID: {}".format(args.packet_file))
        print("  - packet must be a JSON object, got {!r}".format(type(packet).__name__))
        sys.exit(1)

    errors = validate(packet)
    if args.strict_path:
        errors = errors + validate_queue_path(
            args.packet_file, packet.get("status"), args.queue_root
        )
    if errors:
        print("INVALID: {}".format(args.packet_file))
        for err in errors:
            print("  - {}".format(err))
        sys.exit(1)

    print("OK: {}".format(args.packet_file))
    sys.exit(0)


if __name__ == "__main__":
    main()
