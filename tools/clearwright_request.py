#!/usr/bin/env python3
"""
tools/clearwright_request.py: ClearWright Protocol v0.1 manual RTA intake.

Author one new RTA (Request to Act) clearance packet and place it in
clearance_outbox/ to await a manual decision. This is the intake step of the
manual lifecycle: request -> decide -> claim -> lifecycle.

This is a manual authoring surface, not a background worker: no daemon,
scheduler, policy engine, automatic retry, or Discord behavior. It creates
exactly one packet per invocation, validates the packet in memory before any
write, creates the destination exclusively (never overwriting an existing
packet), and re-validates what was written from disk.

Doctrine (see docs/CLEARWRIGHT_PROTOCOL.md and docs/QUEUE_MODEL.md):
  A new request always starts as RTA in clearance_outbox/. Authoring a request
  grants nothing: clearing, denying, or asking for information is a separate
  manual decision by a human or delegated authority (clearwright_decide.py).
  Command-authority examples use OPERATOR-0001; requester examples use a worker
  role. packet_hash is a placeholder: the repository defines no canonical
  hashing scheme yet (documented known limitation, consistent with the other
  tools).

Exit codes: 0 created (or dry-run validated), 1 refused/invalid, 2 file/parse error
"""
import argparse
import os
import sys
from datetime import datetime, timezone

# Reuse the validator's rules and the claim tool's safe-write helpers rather
# than duplicating them.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import clearwright_validate as wpv  # noqa: E402
import clearwright_claim as wpc  # noqa: E402

OUTBOX = "clearance_outbox"
DEFAULT_AGENT = "agent/worker"
DEFAULT_TARGET_LABEL = "sample software project"


def _refuse(msg):
    print("REFUSED: {}".format(msg), file=sys.stderr)
    return 1


def _default_packet_id():
    """A locally unique default id. A same-second collision is refused safely
    by the exclusive create, never overwritten."""
    return "cw-req-" + datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")


def build_request(args):
    """Return a NEW RTA packet dict built from parsed arguments.

    Only non-empty optional fields are included, so authored packets stay
    small and readable.
    """
    now = wpc._utc_now()
    packet_id = (args.id or "").strip() or _default_packet_id()
    filename = packet_id + ".json"

    inputs = {
        "target_project": args.target_label,
        "requested_action": args.action.strip(),
    }
    if args.scope and args.scope.strip():
        inputs["allowed_scope"] = args.scope.strip()
    if args.test_command and args.test_command.strip():
        inputs["test_command"] = args.test_command.strip()

    packet = {
        "packet_id": packet_id,
        "packet_type": args.type.strip(),
        "title": args.title.strip(),
        "requesting_agent": args.agent.strip(),
        "created_at": now,
        "updated_at": now,
        "status": "RTA",
        "source_path": "{}/{}".format(OUTBOX, filename),
        "packet_hash": "sha256:unverified-manual-request",
        "authority_class": args.authority,
        "clearance_class": args.clearance,
        "priority_class": args.priority,
        "inputs_json": inputs,
        "audit_json": {
            "events": [
                {
                    "at": now,
                    "event": "RTA",
                    "actor": args.agent.strip(),
                    "note": "Requested clearance: {}".format(args.title.strip()),
                }
            ]
        },
    }
    if args.risk and args.risk.strip():
        packet["risk_notes"] = args.risk.strip()
    return packet, filename


def request(args):
    # 1. Required text fields must be non-empty after stripping. argparse
    #    guarantees presence; this guards against blank values.
    for field, value in (
        ("--title", args.title), ("--type", args.type),
        ("--agent", args.agent), ("--action", args.action),
    ):
        if not value or not value.strip():
            return _refuse("{} is required and must be non-empty".format(field))

    # 2. The queue root must already contain a clearance_outbox/ directory.
    #    This tool authors packets; it does not create or repair queue layouts.
    outbox_dir = os.path.join(args.queue_root, OUTBOX)
    if not os.path.isdir(outbox_dir):
        return _refuse(
            "queue root {!r} has no {}/ directory; not creating one".format(
                args.queue_root, OUTBOX
            )
        )

    # 3. Build the packet and validate the RESULT in memory before any write.
    packet, filename = build_request(args)
    dest = os.path.join(outbox_dir, filename)
    errors = wpv.validate(packet) + wpv.validate_queue_path(dest, packet["status"])
    if errors:
        for err in errors:
            print("  - {}".format(err), file=sys.stderr)
        return _refuse("request would produce an invalid RTA packet; not writing")

    # 4. Dry-run: report intent, change nothing.
    if args.dry_run:
        print("DRY-RUN: would create RTA (no changes written)")
        print("  packet_id:   {}".format(packet["packet_id"]))
        print("  destination: {}".format(dest))
        print("  title:       {}".format(packet["title"]))
        print("  validations: PASS")
        return 0

    # 5. Create the destination exclusively (fails if it exists).
    werr = wpc.write_exclusive(dest, packet)
    if werr:
        return _refuse(werr)

    # 6. Re-validate the destination as written on disk.
    written, wcode = wpc.load_packet(dest)
    post_disk = ["destination unreadable after write"] if wcode is not None else (
        wpv.validate(written) + wpv.validate_queue_path(dest, written.get("status"))
    )
    if post_disk:
        for err in post_disk:
            print("  - {}".format(err), file=sys.stderr)
        try:
            os.remove(dest)
        except OSError:
            pass
        return _refuse("post-write validation failed; rolled back destination")

    print("REQUESTED: {} (status RTA)".format(dest))
    return 0


def build_parser():
    parser = argparse.ArgumentParser(
        prog="clearwright_request",
        description=(
            "Author one new RTA clearance packet into clearance_outbox/ to await "
            "a manual decision (v0.1). Manual intake only: no daemon, scheduler, "
            "policy engine, or Discord behavior. Authoring a request grants "
            "nothing; clearance is a separate manual decision.\n\n"
            "Exit codes:\n"
            "  0  created (or dry-run validated)\n"
            "  1  refused or invalid\n"
            "  2  argument error"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "queue_root",
        help="Queue root directory containing clearance_outbox/ (for example orchestrator/).",
    )
    parser.add_argument("--title", required=True, metavar="TEXT",
                        help="Required. Short title of the requested action.")
    parser.add_argument("--type", required=True, metavar="TYPE",
                        help="Required. Packet type (for example analysis, code_change, docs_change, config_change, data_change).")
    parser.add_argument("--agent", default=DEFAULT_AGENT, metavar="ACTOR_ID",
                        help="Requesting agent id (default: {}).".format(DEFAULT_AGENT))
    parser.add_argument("--action", required=True, metavar="TEXT",
                        help="Required. The requested action, stated plainly.")
    parser.add_argument("--target-label", default=DEFAULT_TARGET_LABEL, metavar="TEXT",
                        help="Generic target project label (default: {!r}). Keep labels generic; no private names.".format(DEFAULT_TARGET_LABEL))
    parser.add_argument("--scope", default=None, metavar="TEXT",
                        help="Optional allowed scope for the work.")
    parser.add_argument("--test-command", default=None, metavar="TEXT",
                        help="Optional verification command for the work.")
    parser.add_argument("--risk", default=None, metavar="TEXT",
                        help="Optional risk notes recorded on the packet.")
    parser.add_argument("--authority", default="WORKER", metavar="CLASS",
                        choices=sorted(wpv.ALLOWED_AUTHORITY_CLASS),
                        help="Authority class of the requester (default: WORKER).")
    parser.add_argument("--clearance", default="READ_ONLY", metavar="CLASS",
                        choices=sorted(wpv.ALLOWED_CLEARANCE_CLASS),
                        help="Clearance class requested (default: READ_ONLY).")
    parser.add_argument("--priority", default="NORMAL", metavar="CLASS",
                        choices=sorted(wpv.ALLOWED_PRIORITY_CLASS),
                        help="Priority class (default: NORMAL).")
    parser.add_argument("--id", default=None, metavar="PACKET_ID",
                        help="Optional packet id (default: generated cw-req-<utc-stamp>).")
    parser.add_argument("--dry-run", action="store_true",
                        help="Validate and report the intended packet without writing anything.")
    return parser


def main():
    wpc._reconfigure_stdio()
    args = build_parser().parse_args()
    sys.exit(request(args))


if __name__ == "__main__":
    main()
