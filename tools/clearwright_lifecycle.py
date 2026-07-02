#!/usr/bin/env python3
"""
tools/clearwright_lifecycle.py: ClearWright Protocol v0.1 manual queue lifecycle control.

A safe, manual operator surface for the local ClearWright clearance queue after a packet
has been claimed into clearance_in_progress/. It offers five subcommands:

  inspect   Read one packet and summarize its lifecycle state (read-only).
  complete  Move one IN_PROGRESS packet to clearance_done/ with status DONE.
  fail      Move one IN_PROGRESS packet to clearance_failed/ with status FAILED.
  stale     Scan a directory for stale or invalid active packets (read-only).
  status    Report counts and health across the four queue dirs (read-only).

This is a manual operator tool, not a background worker. It does not run a
daemon, schedule work, retry, requeue, arbitrate, or touch Discord. complete and
fail each act on exactly one named packet and never overwrite a destination.

Doctrine (see docs/QUEUE_MODEL.md and docs/LOCAL_REPO_PROFILE.md):
  DTA is a successful safety/governance outcome and lives in clearance_done/,
  never clearance_failed/. SUPERSEDED is a closed replacement, not a failure.
  clearance_failed/ is for execution or processing failure only. DEFER and FREEZE
  are not packet statuses. This tool never routes DTA, DONE, or SUPERSEDED to
  failed: complete and fail act only on a packet that is physically in
  clearance_in_progress/ with status IN_PROGRESS.

Exit codes (consistent with clearwright_validate.py and clearwright_claim.py):
  0  success / valid / no stale / clean
  1  refused, invalid, or stale/invalid packets found
  2  file not found, unreadable, or JSON parse error
"""
import argparse
import json
import os
import sys
from datetime import datetime, timezone

# Reuse the validator's rules and the claim tool's safe-move helpers rather than
# duplicating them. The claim tool already implements exclusive-create writes
# with fsync, robust packet loading, a UTC timestamp, and stdio reconfiguration.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import clearwright_validate as wpv  # noqa: E402
import clearwright_claim as wpc  # noqa: E402

IN_PROGRESS_DIR = "clearance_in_progress"
DONE_DIR = "clearance_done"
FAILED_DIR = "clearance_failed"
CANONICAL_DIRS = [
    "clearance_outbox",
    "clearance_in_progress",
    "clearance_done",
    "clearance_failed",
]
DEFAULT_ACTOR = "operator"


def _refuse(msg):
    print("REFUSED: {}".format(msg), file=sys.stderr)
    return 1


def _now():
    return datetime.now(timezone.utc)


def parse_iso(value):
    """Parse an ISO 8601 timestamp string into an aware UTC datetime.

    Accepts a trailing 'Z' (as the packets use) or an explicit offset. Returns
    (datetime, None) on success or (None, error_message) on failure. A naive
    timestamp is assumed to be UTC.
    """
    if not isinstance(value, str) or not value.strip():
        return None, "not a non-empty string"
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None, "invalid datetime format: {!r}".format(value)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt, None


def expected_queue_for(status):
    """Return the canonical queue dir a given status belongs in, or None."""
    for queue_dir, statuses in wpv.QUEUE_STATUS.items():
        if status in statuses:
            return queue_dir
    return None


def audit_event_count(packet):
    """Return the number of audit events if audit_json.events is a list, else None."""
    audit = packet.get("audit_json")
    if isinstance(audit, dict) and isinstance(audit.get("events"), list):
        return len(audit["events"])
    return None


def analyze(path, packet, now, queue_root=None):
    """Summarize a single packet's lifecycle state. Pure read: mutates nothing.

    Returns a dict of operator-relevant fields shared by inspect, stale, and
    status. now must be an aware datetime.
    """
    status = packet.get("status")
    physical_queue = os.path.basename(os.path.dirname(os.path.abspath(path)))

    field_errors = wpv.validate(packet)
    strict_errors = wpv.validate_queue_path(path, status, queue_root)

    invalid_datetimes = []
    claim_expired = False
    clearance_expired = False

    claim_exp_raw = packet.get("claim_expires_at")
    if claim_exp_raw is not None:
        dt, err = parse_iso(claim_exp_raw)
        if err is not None:
            invalid_datetimes.append("claim_expires_at {}".format(err))
        elif dt < now:
            claim_expired = True

    clearance_exp_raw = packet.get("clearance_expires_at")
    if clearance_exp_raw is not None:
        dt, err = parse_iso(clearance_exp_raw)
        if err is not None:
            invalid_datetimes.append("clearance_expires_at {}".format(err))
        elif dt < now:
            clearance_expired = True

    # Stale definition (docs/QUEUE_MODEL.md, docs/LOCAL_REPO_PROFILE.md): a present lease that is
    # earlier than now. A missing claim_expires_at does not expire. A missing
    # clearance_expires_at for IN_PROGRESS is reported as invalid by the field
    # validator, not treated as stale here.
    stale = claim_expired or clearance_expired

    return {
        "path": path,
        "filename": os.path.basename(path),
        "packet_id": packet.get("packet_id"),
        "status": status,
        "source_path": packet.get("source_path"),
        "physical_queue": physical_queue,
        "expected_queue": expected_queue_for(status),
        "strict_path_ok": not strict_errors,
        "strict_path_errors": strict_errors,
        "field_valid": not field_errors,
        "field_errors": field_errors,
        "claimed_by": packet.get("claimed_by"),
        "claimed_at": packet.get("claimed_at"),
        "claim_expires_at": claim_exp_raw,
        "clearance_expires_at": clearance_exp_raw,
        "claim_expired": claim_expired,
        "clearance_expired": clearance_expired,
        "invalid_datetimes": invalid_datetimes,
        "stale": stale,
        "audit_event_count": audit_event_count(packet),
    }


def stale_reasons(info):
    """Return human-readable reasons a packet is stale (may be empty)."""
    reasons = []
    if info["claim_expired"]:
        reasons.append("claim_expires_at expired {}".format(info["claim_expires_at"]))
    if info["clearance_expired"]:
        reasons.append(
            "clearance_expires_at expired {}".format(info["clearance_expires_at"])
        )
    return reasons


def invalid_reasons(info):
    """Return reasons a packet is invalid for scan reporting (may be empty)."""
    reasons = list(info["field_errors"]) + list(info["strict_path_errors"])
    reasons.extend(info["invalid_datetimes"])
    return reasons


# --------------------------------------------------------------------------- #
# inspect
# --------------------------------------------------------------------------- #

def cmd_inspect(args):
    packet, code = wpc.load_packet(args.packet_file)
    if code is not None:
        return code

    info = analyze(args.packet_file, packet, _now(), queue_root=args.queue_root)

    if args.json:
        print(json.dumps(info, indent=2))
    else:
        print("Packet: {}".format(info["filename"]))
        print("Path: {}".format(info["path"]))
        print("packet_id: {}".format(info["packet_id"]))
        print("Status: {}".format(info["status"]))
        print("source_path: {}".format(info["source_path"]))
        print("Physical queue: {}".format(info["physical_queue"]))
        print("Expected queue for status: {}".format(info["expected_queue"]))
        print("Strict path: {}".format("OK" if info["strict_path_ok"] else "FAIL"))
        if not info["strict_path_ok"]:
            for err in info["strict_path_errors"]:
                print("  - {}".format(err))
        print("Field valid: {}".format("OK" if info["field_valid"] else "FAIL"))
        if not info["field_valid"]:
            for err in info["field_errors"]:
                print("  - {}".format(err))
        print("Claimed by: {}".format(info["claimed_by"]))
        print("Claimed at: {}".format(info["claimed_at"]))
        print("Claim expires: {}".format(info["claim_expires_at"]))
        print("Clearance expires: {}".format(info["clearance_expires_at"]))
        print("Claim lease expired: {}".format("yes" if info["claim_expired"] else "no"))
        print(
            "Clearance lease expired: {}".format(
                "yes" if info["clearance_expired"] else "no"
            )
        )
        if info["invalid_datetimes"]:
            for msg in info["invalid_datetimes"]:
                print("  - invalid datetime: {}".format(msg))
        print("Stale: {}".format("yes" if info["stale"] else "no"))
        count = info["audit_event_count"]
        print("Audit events: {}".format(count if count is not None else "n/a"))

    # exit 0 only if valid, strict-path OK, and not stale.
    ok = info["field_valid"] and info["strict_path_ok"] and not info["stale"]
    return 0 if ok else 1


# --------------------------------------------------------------------------- #
# complete / fail (single-packet mutating transitions)
# --------------------------------------------------------------------------- #

def build_transition(packet, filename, new_status, dest_dir, actor, reason=None):
    """Return a NEW dict for the transitioned packet. Does not mutate the input.

    status, source_path, and updated_at are set. A lifecycle audit event is
    appended to audit_json.events, preserving prior history. If audit_json is
    absent or not in the {events: [...]} shape, a minimal one is created so a
    terminal transition (and a fail reason) is never silently lost. This is a
    deliberate, documented divergence from clearwright_claim.py, which skips the
    audit append when no audit shape exists; here the terminal event and any fail
    reason must be recorded.

    packet_hash is intentionally left unchanged: the repository defines no
    canonical hashing scheme and the validator does not verify the hash, so
    recomputing it would be inventing doctrine. This mirrors the claim tool and is
    a documented known limitation.
    """
    out = dict(packet)
    now = wpc._utc_now()
    out["status"] = new_status
    out["source_path"] = "{}/{}".format(dest_dir, filename)
    out["updated_at"] = now

    event = {"at": now, "event": new_status, "actor": actor or DEFAULT_ACTOR}
    if reason:
        event["reason"] = reason
        event["note"] = "Failed and moved to {}: {}".format(dest_dir, reason)
    else:
        event["note"] = "Completed and moved to {}".format(dest_dir)

    audit = out.get("audit_json")
    if isinstance(audit, dict) and isinstance(audit.get("events"), list):
        audit = dict(audit)
        audit["events"] = list(audit["events"]) + [event]
    else:
        audit = {"events": [event]}
    out["audit_json"] = audit
    return out


def transition(packet_file, dest_dir, new_status, actor, dry_run, reason=None):
    """Shared safe single-packet transition from clearance_in_progress/.

    Mirrors the claim tool's safety model: validate the source, refuse on any
    path/status mismatch, validate the prospective result in memory, write the
    destination exclusively (never overwrite), re-validate it from disk, and
    remove the source only last. On any earlier failure the source is left
    untouched and no destination remains.
    """
    packet, code = wpc.load_packet(packet_file)
    if code is not None:
        return code

    # 1. Source must be a structurally valid packet.
    errors = wpv.validate(packet)
    if errors:
        for err in errors:
            print("  - {}".format(err), file=sys.stderr)
        return _refuse("source packet is invalid; not transitioning")

    src_abs = os.path.abspath(packet_file)
    src_parent = os.path.basename(os.path.dirname(src_abs))
    filename = os.path.basename(src_abs)

    # 2. Only packets physically in clearance_in_progress/ are eligible.
    if src_parent != IN_PROGRESS_DIR:
        return _refuse(
            "packet is not in {}/ (parent is {!r}); only active packets may be "
            "completed or failed".format(IN_PROGRESS_DIR, src_parent)
        )

    # 3. Filesystem location and status must already agree (no repair mode).
    path_errors = wpv.validate_queue_path(packet_file, packet.get("status"))
    if path_errors:
        for err in path_errors:
            print("  - {}".format(err), file=sys.stderr)
        return _refuse("source path/status mismatch; not transitioning (no repair mode)")

    # 4. Status must be exactly IN_PROGRESS. This is what refuses DTA, DONE,
    #    FAILED, SUPERSEDED, and every pre-claim status: none of them are
    #    IN_PROGRESS, and none of them are valid in clearance_in_progress/.
    status = packet.get("status")
    if status != "IN_PROGRESS":
        return _refuse(
            "status {!r} is not eligible; only IN_PROGRESS packets in {}/ may be "
            "completed or failed".format(status, IN_PROGRESS_DIR)
        )

    # 5. Destination: sibling queue dir under the same queue root, same filename.
    queue_root = os.path.dirname(os.path.dirname(src_abs))
    dest = os.path.join(queue_root, dest_dir, filename)

    # 6. Build and validate the RESULT before touching the filesystem.
    result = build_transition(packet, filename, new_status, dest_dir, actor, reason)
    post = wpv.validate(result) + wpv.validate_queue_path(dest, result.get("status"))
    if post:
        for err in post:
            print("  - {}".format(err), file=sys.stderr)
        return _refuse(
            "transition would produce an invalid {} packet; not moving".format(new_status)
        )

    # 7. Dry-run: report intent, change nothing.
    if dry_run:
        print("DRY-RUN: would {} (no changes written)".format(new_status))
        print("  move:        {} -> {}".format(src_abs, dest))
        print("  status:      {} -> {}".format(status, new_status))
        print("  source_path: {}".format(result["source_path"]))
        if reason:
            print("  reason:      {}".format(reason))
        print("  validations: PASS")
        return 0

    # 8. Create the destination exclusively (fails if it exists), write the packet.
    werr = wpc.write_exclusive(dest, result)
    if werr:
        return _refuse(werr)

    # 9. Re-validate the destination as written on disk.
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
        return _refuse(
            "post-move validation failed; rolled back destination, source left unchanged"
        )

    # 10. Remove the source last; the transition is now complete.
    try:
        os.remove(src_abs)
    except OSError as exc:
        print(
            "WARNING: {} packet written to {} but could not remove source {}: "
            "{}".format(new_status, dest, src_abs, exc), file=sys.stderr
        )
        print(
            "MANUAL CLEANUP NEEDED: the destination is the authoritative packet; "
            "remove the stale source duplicate.", file=sys.stderr
        )
        return 1

    if new_status == "FAILED":
        print("FAILED: {} -> {}".format(src_abs, dest))
        print("Reason: {}".format(reason))
    else:
        print("COMPLETED: {} -> {}".format(src_abs, dest))
    return 0


def cmd_complete(args):
    return transition(
        args.packet_file, DONE_DIR, "DONE", args.actor, args.dry_run
    )


def cmd_fail(args):
    if not args.reason or not args.reason.strip():
        return _refuse("--reason is required and must be non-empty")
    return transition(
        args.packet_file, FAILED_DIR, "FAILED", args.actor, args.dry_run,
        reason=args.reason.strip(),
    )


# --------------------------------------------------------------------------- #
# stale (read-only scan of one directory)
# --------------------------------------------------------------------------- #

def scan_dir(directory, queue_root=None):
    """Read-only scan of JSON packets in one directory.

    Returns (results, malformed) where results is a list of (info, packet)
    tuples and malformed is a list of (filename, message). Ignores .gitkeep and
    non-.json files. Never mutates anything.
    """
    now = _now()
    results = []
    malformed = []
    for name in sorted(os.listdir(directory)):
        if name == ".gitkeep" or not name.endswith(".json"):
            continue
        path = os.path.join(directory, name)
        if not os.path.isfile(path):
            continue
        packet, code = wpc.load_packet(path)
        if code is not None:
            malformed.append((name, "malformed or unreadable JSON"))
            continue
        results.append((analyze(path, packet, now, queue_root), packet))
    return results, malformed


def cmd_stale(args):
    directory = args.directory
    if not os.path.isdir(directory):
        print("ERROR: not a directory: {}".format(directory), file=sys.stderr)
        return 2

    try:
        results, malformed = scan_dir(directory)
    except OSError as exc:
        print("ERROR: cannot read {}: {}".format(directory, exc), file=sys.stderr)
        return 2

    stale = []
    invalid = []
    for info, _packet in results:
        if info["stale"]:
            stale.append(info)
        # In clearance_in_progress a packet that is not IN_PROGRESS, fails strict
        # path, or has invalid fields/datetimes is reported invalid.
        problems = invalid_reasons(info)
        if info["physical_queue"] == IN_PROGRESS_DIR and info["status"] != "IN_PROGRESS":
            problems = problems + [
                "status {!r} is not IN_PROGRESS in {}/".format(
                    info["status"], IN_PROGRESS_DIR
                )
            ]
        if problems:
            invalid.append((info, problems))

    if args.json:
        summary = {
            "directory": directory,
            "scanned": len(results),
            "stale_count": len(stale),
            "invalid_count": len(invalid) + len(malformed),
            "malformed_count": len(malformed),
            "stale": [
                {"filename": i["filename"], "reasons": stale_reasons(i)} for i in stale
            ],
            "invalid": [
                {"filename": i["filename"], "reasons": r} for i, r in invalid
            ],
            "malformed": [{"filename": n, "reason": m} for n, m in malformed],
        }
        print(json.dumps(summary, indent=2))
    else:
        for info in stale:
            for reason in stale_reasons(info):
                print("STALE: {} {}".format(info["filename"], reason))
        for info, reasons in invalid:
            print("INVALID: {}: {}".format(info["filename"], "; ".join(reasons)))
        for name, msg in malformed:
            print("MALFORMED: {}: {}".format(name, msg))
        print(
            "Scanned {} packet(s): {} stale, {} invalid, {} malformed".format(
                len(results), len(stale), len(invalid), len(malformed)
            )
        )

    return _scan_exit(len(stale), len(invalid) + len(malformed), args)


# --------------------------------------------------------------------------- #
# status (read-only health across the four queue dirs)
# --------------------------------------------------------------------------- #

def cmd_status(args):
    queue_root = args.queue_root
    if not os.path.isdir(queue_root):
        print("ERROR: not a directory: {}".format(queue_root), file=sys.stderr)
        return 2

    counts = {}
    by_status = {}
    missing_dirs = []
    stale_names = []
    invalid_entries = []
    malformed_names = []
    total = 0

    for qdir in CANONICAL_DIRS:
        path = os.path.join(queue_root, qdir)
        if not os.path.isdir(path):
            missing_dirs.append(qdir)
            counts[qdir] = 0
            continue
        try:
            results, malformed = scan_dir(path, queue_root=queue_root)
        except OSError as exc:
            print("ERROR: cannot read {}: {}".format(path, exc), file=sys.stderr)
            return 2
        counts[qdir] = len(results) + len(malformed)
        total += counts[qdir]
        for name, _msg in malformed:
            malformed_names.append(name)
        for info, _packet in results:
            status = info["status"]
            by_status[status] = by_status.get(status, 0) + 1
            if qdir == IN_PROGRESS_DIR and info["stale"]:
                stale_names.append(info["filename"])
            problems = invalid_reasons(info)
            if problems:
                invalid_entries.append((info["filename"], problems))

    stale_count = len(stale_names)
    invalid_count = len(invalid_entries)
    malformed_count = len(malformed_names)

    if args.json:
        summary = {
            "queue_root": queue_root,
            "counts": counts,
            "by_status": by_status,
            "total": total,
            "stale_in_progress": stale_count,
            "invalid_path_status": invalid_count,
            "malformed_json": malformed_count,
            "missing_queue_dirs": missing_dirs,
            "stale": stale_names,
            "invalid": [{"filename": n, "reasons": r} for n, r in invalid_entries],
            "malformed": malformed_names,
        }
        print(json.dumps(summary, indent=2))
    else:
        print("Queue root: {}".format(queue_root))
        for qdir in CANONICAL_DIRS:
            print("{}: {}".format(qdir, counts.get(qdir, 0)))
        print("stale_in_progress: {}".format(stale_count))
        print("invalid_path_status: {}".format(invalid_count))
        print("malformed_json: {}".format(malformed_count))
        print("missing_queue_dirs: {}".format(len(missing_dirs)))
        print("total: {}".format(total))
        if by_status:
            parts = ", ".join(
                "{}={}".format(k, by_status[k]) for k in sorted(by_status)
            )
            print("by_status: {}".format(parts))
        for name in stale_names:
            print("STALE: {}".format(name))
        for name, reasons in invalid_entries:
            print("INVALID: {}: {}".format(name, "; ".join(reasons)))
        for name in malformed_names:
            print("MALFORMED: {}".format(name))

    return _scan_exit(stale_count, invalid_count + malformed_count, args)


def _scan_exit(stale_count, invalid_count, args):
    """Shared exit policy for read-only scans (stale, status).

    Default (no --fail-on-* flag): exit 1 if any stale OR invalid/malformed
    packet was found, else 0. When one or both --fail-on-* flags are given, only
    the selected condition(s) cause exit 1; the other is reported but does not
    affect the exit code. This keeps a strict default while letting a caller
    (for example CI) narrow what counts as failure.
    """
    fail_on_stale = getattr(args, "fail_on_stale", False)
    fail_on_invalid = getattr(args, "fail_on_invalid", False)
    if not fail_on_stale and not fail_on_invalid:
        return 1 if (stale_count or invalid_count) else 0
    if fail_on_stale and stale_count:
        return 1
    if fail_on_invalid and invalid_count:
        return 1
    return 0


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #

def build_parser():
    parser = argparse.ArgumentParser(
        prog="clearwright_lifecycle",
        description=(
            "Manual ClearWright queue lifecycle control (v0.1). Inspect, complete, or "
            "fail one claimed packet, and run read-only stale and status scans. "
            "This is a manual operator tool: no daemon, scheduler, retry, "
            "requeue, or Discord behavior.\n\n"
            "Exit codes:\n"
            "  0  success / valid / no stale / clean\n"
            "  1  refused, invalid, or stale/invalid packets found\n"
            "  2  file not found, unreadable, or JSON parse error"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_inspect = sub.add_parser(
        "inspect",
        help="Read one packet and summarize its lifecycle state (read-only).",
        description="Read one packet and summarize its lifecycle state. Read-only.",
    )
    p_inspect.add_argument("packet_file", help="Path to the packet JSON file.")
    p_inspect.add_argument(
        "--queue-root", default=None, metavar="PATH",
        help="Optional queue root to anchor strict-path validation.",
    )
    p_inspect.add_argument("--json", action="store_true", help="Machine-readable output.")
    p_inspect.set_defaults(func=cmd_inspect)

    p_complete = sub.add_parser(
        "complete",
        help="Move one IN_PROGRESS packet to clearance_done/ (status DONE).",
        description=(
            "Complete one active packet: move it from clearance_in_progress/ to "
            "clearance_done/, set status DONE, update source_path and updated_at, "
            "and append a DONE audit event. Refuses anything that is not an "
            "IN_PROGRESS packet in clearance_in_progress/. Never overwrites a "
            "destination; validates before and after; removes the source last."
        ),
    )
    p_complete.add_argument("packet_file", help="Path to the IN_PROGRESS packet JSON.")
    p_complete.add_argument(
        "--actor", default=None, metavar="ACTOR_ID",
        help="Optional actor id recorded in the DONE audit event.",
    )
    p_complete.add_argument(
        "--dry-run", action="store_true",
        help="Validate and report the intended completion without writing anything.",
    )
    p_complete.set_defaults(func=cmd_complete)

    p_fail = sub.add_parser(
        "fail",
        help="Move one IN_PROGRESS packet to clearance_failed/ (status FAILED).",
        description=(
            "Fail one active packet: move it from clearance_in_progress/ to "
            "clearance_failed/, set status FAILED, update source_path and "
            "updated_at, and append a FAILED audit event carrying the reason. "
            "FAILED means execution or processing failure only. Do not use fail "
            "for a DTA, a DEFER, a FREEZE, or to supersede a decision. Requires "
            "a non-empty --reason. Refuses anything that is not an IN_PROGRESS "
            "packet in clearance_in_progress/. Never overwrites a destination."
        ),
    )
    p_fail.add_argument("packet_file", help="Path to the IN_PROGRESS packet JSON.")
    p_fail.add_argument(
        "--reason", required=True, metavar="TEXT",
        help="Required. Why the packet failed (execution or processing failure).",
    )
    p_fail.add_argument(
        "--actor", default=None, metavar="ACTOR_ID",
        help="Optional actor id recorded in the FAILED audit event.",
    )
    p_fail.add_argument(
        "--dry-run", action="store_true",
        help="Validate and report the intended failure without writing anything.",
    )
    p_fail.set_defaults(func=cmd_fail)

    p_stale = sub.add_parser(
        "stale",
        help="Scan a directory for stale or invalid active packets (read-only).",
        description=(
            "Read-only scan of a directory (normally clearance_in_progress/) for "
            "stale packets (expired claim_expires_at or clearance_expires_at) and "
            "invalid packets (bad datetimes, strict-path mismatch, non-IN_PROGRESS "
            "status, malformed JSON). Mutates nothing."
        ),
    )
    p_stale.add_argument("directory", help="Directory to scan (for example clearance_in_progress/).")
    p_stale.add_argument("--json", action="store_true", help="Machine-readable output.")
    p_stale.add_argument(
        "--fail-on-stale", action="store_true",
        help="Exit 1 only when stale packets are found (invalid still reported).",
    )
    p_stale.add_argument(
        "--fail-on-invalid", action="store_true",
        help="Exit 1 only when invalid/malformed packets are found (stale still reported).",
    )
    p_stale.set_defaults(func=cmd_stale)

    p_status = sub.add_parser(
        "status",
        help="Report counts and health across the four queue dirs (read-only).",
        description=(
            "Read-only operator visibility across a queue root: counts per "
            "canonical queue dir, counts by status, stale in-progress count, "
            "invalid path/status count, malformed JSON count, and missing queue "
            "directory count. Does not persist metrics, write reports, move "
            "packets, or auto-correct anything."
        ),
    )
    p_status.add_argument("queue_root", help="Queue root directory (for example orchestrator/).")
    p_status.add_argument("--json", action="store_true", help="Machine-readable output.")
    p_status.add_argument(
        "--fail-on-stale", action="store_true",
        help="Exit 1 only when stale packets are found (invalid still reported).",
    )
    p_status.add_argument(
        "--fail-on-invalid", action="store_true",
        help="Exit 1 only when invalid/malformed packets are found (stale still reported).",
    )
    p_status.set_defaults(func=cmd_status)

    return parser


def main():
    wpc._reconfigure_stdio()
    args = build_parser().parse_args()
    sys.exit(args.func(args))


if __name__ == "__main__":
    main()
