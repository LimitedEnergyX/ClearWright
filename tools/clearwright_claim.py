#!/usr/bin/env python3
"""
tools/clearwright_claim.py: ClearWright Protocol v0.1 single-packet claim.

Claim one packet by moving it from orchestrator/clearance_outbox/ to
orchestrator/clearance_in_progress/, setting status to IN_PROGRESS and updating
source_path. The packet is validated before and after the move, and the claim
fails safely (leaving the source packet unchanged) on any error.

This is the minimal claim step only. It claims exactly one packet named on the
command line. It does not schedule, assign workers, run a daemon, or touch
Discord. The destination is created exclusively and is never overwritten.

Exit codes: 0 claimed (or dry-run validated), 1 refused/invalid, 2 file/parse error
"""
import argparse
import json
import os
import sys
from datetime import datetime, timezone

# Reuse the validator's rules rather than duplicating them.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import clearwright_validate as wpv  # noqa: E402

OUTBOX = "clearance_outbox"
IN_PROGRESS = "clearance_in_progress"
# Claimable source statuses are exactly the pre-claim outbox states.
CLAIMABLE = wpv.QUEUE_STATUS[OUTBOX]  # {RTA, IN_REVIEW, RFI_PENDING, CTA}


def _utc_now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _refuse(msg):
    print("REFUSED: {}".format(msg), file=sys.stderr)
    return 1


def load_packet(path):
    """Return (packet, error_code). error_code is None on success, else 2."""
    try:
        with open(path, encoding="utf-8") as fh:
            packet = json.load(fh)
    except FileNotFoundError:
        print("ERROR: file not found: {}".format(path), file=sys.stderr)
        return None, 2
    except json.JSONDecodeError as exc:
        print("ERROR: JSON parse error in {}: {}".format(path, exc), file=sys.stderr)
        return None, 2
    except OSError as exc:
        print("ERROR: {}".format(exc), file=sys.stderr)
        return None, 2
    if not isinstance(packet, dict):
        print("ERROR: packet must be a JSON object: {}".format(path), file=sys.stderr)
        return None, 2
    return packet, None


def build_claimed(packet, filename, claimant):
    """Return a NEW dict representing the claimed (IN_PROGRESS) packet.

    Does not mutate the input. packet_hash is intentionally left unchanged: the
    repository defines no canonical hashing scheme and the validator does not
    verify the hash, so recomputing it here would be inventing doctrine. This is
    documented as a known limitation.
    """
    claimed = dict(packet)
    now = _utc_now()
    claimed["status"] = "IN_PROGRESS"
    claimed["source_path"] = "{}/{}".format(IN_PROGRESS, filename)
    claimed["updated_at"] = now
    if claimant:
        claimed["claimed_by"] = claimant
        claimed["claimed_at"] = now
    # Preserve and extend audit history only if it already exists in that shape.
    audit = claimed.get("audit_json")
    if isinstance(audit, dict) and isinstance(audit.get("events"), list):
        audit = dict(audit)
        audit["events"] = list(audit["events"]) + [{
            "at": now,
            "event": "IN_PROGRESS",
            "actor": claimant or "system",
            "note": "Claimed from {} and moved to {}".format(OUTBOX, IN_PROGRESS),
        }]
        claimed["audit_json"] = audit
    return claimed


def write_exclusive(dest, packet):
    """Write packet JSON to dest, failing if dest already exists.

    Atomic create via O_EXCL; fsync before close. Returns None on success, or an
    error string. On write failure the partial destination is removed.
    """
    try:
        fd = os.open(dest, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
    except FileExistsError:
        return "destination already exists: {}".format(dest)
    except OSError as exc:
        return "could not create destination {}: {}".format(dest, exc)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(packet, fh, indent=2)
            fh.write("\n")
            fh.flush()
            os.fsync(fh.fileno())
    except OSError as exc:
        try:
            os.remove(dest)
        except OSError:
            pass
        return "could not write destination {}: {}".format(dest, exc)
    return None


def claim(packet_file, claimant=None, dry_run=False):
    packet, code = load_packet(packet_file)
    if code is not None:
        return code

    # 1. Source must be a structurally valid packet.
    errors = wpv.validate(packet)
    if errors:
        for err in errors:
            print("  - {}".format(err), file=sys.stderr)
        return _refuse("source packet is invalid; not claiming")

    src_abs = os.path.abspath(packet_file)
    src_parent = os.path.basename(os.path.dirname(src_abs))
    filename = os.path.basename(src_abs)

    # 2. Only packets in clearance_outbox may be claimed.
    if src_parent != OUTBOX:
        return _refuse(
            "packet is not in {}/ (parent is {!r}); only outbox packets may be "
            "claimed".format(OUTBOX, src_parent)
        )

    # 3. The actual directory and status must already be consistent (no repair
    #    mode). This checks the filesystem location against the status, not the
    #    source_path field, which is rewritten to the truthful value below.
    path_errors = wpv.validate_queue_path(packet_file, packet.get("status"))
    if path_errors:
        for err in path_errors:
            print("  - {}".format(err), file=sys.stderr)
        return _refuse("source path/status mismatch; not claiming (no repair mode)")

    # 4. Status must be claimable.
    status = packet.get("status")
    if status not in CLAIMABLE:
        return _refuse(
            "status {!r} is not claimable from {}/; claimable: {}".format(
                status, OUTBOX, ", ".join(sorted(CLAIMABLE))
            )
        )

    # 5. Destination: sibling clearance_in_progress under the same queue root.
    queue_root = os.path.dirname(os.path.dirname(src_abs))
    dest = os.path.join(queue_root, IN_PROGRESS, filename)

    # 6. Validate the RESULT before touching the filesystem.
    claimed = build_claimed(packet, filename, claimant)
    post = wpv.validate(claimed) + wpv.validate_queue_path(dest, claimed.get("status"))
    if post:
        for err in post:
            print("  - {}".format(err), file=sys.stderr)
        return _refuse("claim would produce an invalid IN_PROGRESS packet; not moving")

    # 7. Dry-run: report intent, change nothing.
    if dry_run:
        print("DRY-RUN: would claim (no changes written)")
        print("  move:        {} -> {}".format(src_abs, dest))
        print("  status:      {} -> IN_PROGRESS".format(status))
        print("  source_path: {}".format(claimed["source_path"]))
        if claimant:
            print("  claimed_by:  {}".format(claimant))
        print("  validations: PASS")
        return 0

    # 8. Create the destination exclusively (fails if it exists), write the packet.
    werr = write_exclusive(dest, claimed)
    if werr:
        return _refuse(werr)

    # 9. Re-validate the destination as written on disk (final post-move check).
    written, wcode = load_packet(dest)
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
        return _refuse("post-move validation failed; rolled back destination, source left unchanged")

    # 10. Remove the source; the move (claim) is now complete.
    try:
        os.remove(src_abs)
    except OSError as exc:
        print(
            "WARNING: claimed packet written to {} but could not remove source {}: "
            "{}".format(dest, src_abs, exc), file=sys.stderr
        )
        print(
            "MANUAL CLEANUP NEEDED: the destination is the authoritative claimed "
            "packet; remove the stale source duplicate.", file=sys.stderr
        )
        return 1

    print("CLAIMED: {} -> {} (status IN_PROGRESS)".format(src_abs, dest))
    return 0


def build_parser():
    parser = argparse.ArgumentParser(
        prog="clearwright_claim",
        description=(
            "Claim one clearance packet: move it from clearance_outbox to "
            "clearance_in_progress, set status IN_PROGRESS, and update "
            "source_path. Validates before and after the move and fails safely "
            "(source unchanged) on any error.\n\n"
            "Exit codes:\n"
            "  0  claimed (or dry-run validated)\n"
            "  1  refused or invalid\n"
            "  2  file not found, unreadable, or JSON parse error"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "packet_file",
        help="Path to the packet JSON file in clearance_outbox/.",
    )
    parser.add_argument(
        "--claimant",
        metavar="ACTOR_ID",
        default=None,
        help="Optional actor id to record as claimed_by (and claimed_at).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate and report the intended claim without writing or moving anything.",
    )
    return parser


def _reconfigure_stdio():
    """Make reporting robust to packet paths containing characters outside the
    console code page, so a completed claim can never be reported as a failure
    by a print() that raises UnicodeEncodeError."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            try:
                reconfigure(encoding="utf-8", errors="backslashreplace")
            except (ValueError, OSError):
                pass


def main():
    _reconfigure_stdio()
    args = build_parser().parse_args()
    sys.exit(claim(args.packet_file, claimant=args.claimant, dry_run=args.dry_run))


if __name__ == "__main__":
    main()
