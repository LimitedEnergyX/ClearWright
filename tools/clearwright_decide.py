#!/usr/bin/env python3
"""
tools/clearwright_decide.py: ClearWright Protocol v0.1 manual clearance decisions.

Manual, operator-issued clearance decisions on a packet waiting in the clearance
queue outbox. It offers three subcommands:

  cta   Clear one RTA/IN_REVIEW packet to act (status CTA). The packet stays in
        clearance_outbox/ until it is claimed; a bounded clearance lease is set.
  dta   Deny one RTA/IN_REVIEW packet (status DTA) and move it to clearance_done/.
        A DTA is a successful safety and governance outcome, not a failure.
  rfi   Request more information for one RTA/IN_REVIEW packet (status
        RFI_PENDING). The packet stays in clearance_outbox/ for follow-up.

This is a manual operator surface, not a background worker: no daemon, scheduler,
policy engine, automatic retry, or Discord behavior. Each decision is explicit and
acts on exactly one named packet. A decision records who issued it, when, and the
reason or note, and appends an audit event while preserving prior history.

Doctrine (see docs/CLEARWRIGHT_PROTOCOL.md, docs/QUEUE_MODEL.md, and
docs/AUTHORITY_MODEL.md):
  Only a packet physically in clearance_outbox/ with status RTA or IN_REVIEW may
  be decided. CTA and RFI_PENDING are pre-claim outbox states, so those packets
  stay in clearance_outbox/. DTA is a closed governance outcome and archives to
  clearance_done/, never clearance_failed/. Command-authority examples use
  OPERATOR-0001; 0000 is reserved for an emergency root halt only. Consensus may
  support a clearance; it does not grant authority, and the operator remains the
  final override.

Exit codes: 0 decided (or dry-run validated), 1 refused/invalid, 2 file/parse error
"""
import argparse
import json
import os
import sys
from datetime import datetime, timedelta, timezone

# Reuse the validator's rules and the claim tool's safe-write helpers rather than
# duplicating them.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import clearwright_validate as wpv  # noqa: E402
import clearwright_claim as wpc  # noqa: E402

OUTBOX = "clearance_outbox"
DONE = "clearance_done"
# Only pre-decision outbox states may be manually decided.
DECIDABLE = {"RTA", "IN_REVIEW"}
DEFAULT_ACTOR = "OPERATOR-0001"
DEFAULT_LEASE_MINUTES = 120


def _refuse(msg):
    print("REFUSED: {}".format(msg), file=sys.stderr)
    return 1


def _iso_in(minutes):
    """A UTC timestamp `minutes` from now, in the repository's ISO 8601 Z style."""
    return (datetime.now(timezone.utc) + timedelta(minutes=minutes)).strftime(
        "%Y-%m-%dT%H:%M:%SZ"
    )


def _append_audit(out, event):
    """Append an audit event, creating a minimal audit_json.events shape if absent
    so a decision and its reason are never silently lost. This mirrors
    clearwright_lifecycle.py."""
    audit = out.get("audit_json")
    if isinstance(audit, dict) and isinstance(audit.get("events"), list):
        audit = dict(audit)
        audit["events"] = list(audit["events"]) + [event]
    else:
        audit = {"events": [event]}
    out["audit_json"] = audit


def build_decision(packet, filename, decision, actor, reason, lease_minutes):
    """Return a NEW dict for the decided packet. Does not mutate the input.

    packet_hash is intentionally left unchanged: the repository defines no
    canonical hashing scheme and the validator does not verify the hash, so
    recomputing it would be inventing doctrine. This mirrors the claim and
    lifecycle tools and is a documented known limitation.
    """
    out = dict(packet)
    now = wpc._utc_now()
    actor = actor or DEFAULT_ACTOR
    out["updated_at"] = now

    if decision == "CTA":
        expires = _iso_in(lease_minutes if lease_minutes else DEFAULT_LEASE_MINUTES)
        out["status"] = "CTA"
        out["source_path"] = "{}/{}".format(OUTBOX, filename)
        out["cleared_by"] = actor
        out["clearance_expires_at"] = expires
        out["decision_json"] = {
            "decision": "CTA", "decided_by": actor, "decided_at": now,
            "rationale": reason or "", "clearance_expires_at": expires,
        }
        note = "Cleared to act; lease expires {}".format(expires)
        if reason:
            note += ": {}".format(reason)
        _append_audit(out, {"at": now, "event": "CTA", "actor": actor, "note": note})

    elif decision == "DTA":
        out["status"] = "DTA"
        out["source_path"] = "{}/{}".format(DONE, filename)
        out["denied_by"] = actor
        out["decision_json"] = {
            "decision": "DTA", "decided_by": actor, "decided_at": now,
            "rationale": reason,
        }
        _append_audit(out, {
            "at": now, "event": "DTA", "actor": actor,
            "note": "Denied to act: {}".format(reason), "reason": reason,
        })

    elif decision == "RFI_PENDING":
        out["status"] = "RFI_PENDING"
        out["source_path"] = "{}/{}".format(OUTBOX, filename)
        out["rfi_json"] = {
            "requested_by": actor, "requested_at": now, "question": reason,
        }
        _append_audit(out, {
            "at": now, "event": "RFI_PENDING", "actor": actor,
            "note": "Information requested: {}".format(reason), "reason": reason,
        })

    return out


def write_inplace(path, packet):
    """Rewrite `path` atomically (temp file, fsync, then os.replace). Used for
    decisions that keep the packet in clearance_outbox/ (CTA, RFI_PENDING).
    Returns None on success or an error string."""
    tmp = path + ".tmp"
    try:
        fd = os.open(tmp, os.O_CREAT | os.O_TRUNC | os.O_WRONLY, 0o644)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(packet, fh, indent=2)
            fh.write("\n")
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except OSError as exc:
        try:
            os.remove(tmp)
        except OSError:
            pass
        return "could not write {}: {}".format(path, exc)
    return None


def decide(packet_file, decision, actor=None, reason=None, dry_run=False,
           lease_minutes=None):
    packet, code = wpc.load_packet(packet_file)
    if code is not None:
        return code

    # 1. Source must be a structurally valid packet.
    errors = wpv.validate(packet)
    if errors:
        for err in errors:
            print("  - {}".format(err), file=sys.stderr)
        return _refuse("source packet is invalid; not deciding")

    src_abs = os.path.abspath(packet_file)
    src_parent = os.path.basename(os.path.dirname(src_abs))
    filename = os.path.basename(src_abs)

    # 2. Only packets physically in clearance_outbox/ may be decided.
    if src_parent != OUTBOX:
        return _refuse(
            "packet is not in {}/ (parent is {!r}); only outbox packets awaiting a "
            "decision may be decided".format(OUTBOX, src_parent)
        )

    # 3. Filesystem location and status must already agree (no repair mode).
    path_errors = wpv.validate_queue_path(packet_file, packet.get("status"))
    if path_errors:
        for err in path_errors:
            print("  - {}".format(err), file=sys.stderr)
        return _refuse("source path/status mismatch; not deciding (no repair mode)")

    # 4. Status must be RTA or IN_REVIEW. This refuses an already-decided CTA, an
    #    RFI_PENDING, a claimed IN_PROGRESS, and every terminal state.
    status = packet.get("status")
    if status not in DECIDABLE:
        return _refuse(
            "status {!r} is not decidable; only RTA or IN_REVIEW packets in {}/ may "
            "be cleared, denied, or sent to RFI".format(status, OUTBOX)
        )

    # 5. Destination: DTA archives to clearance_done/; CTA and RFI stay in outbox.
    queue_root = os.path.dirname(os.path.dirname(src_abs))
    dest_dir = DONE if decision == "DTA" else OUTBOX
    dest = os.path.join(queue_root, dest_dir, filename)

    # 6. Build and validate the RESULT before touching the filesystem.
    result = build_decision(packet, filename, decision, actor, reason, lease_minutes)
    post = wpv.validate(result) + wpv.validate_queue_path(dest, result.get("status"))
    if post:
        for err in post:
            print("  - {}".format(err), file=sys.stderr)
        return _refuse(
            "decision would produce an invalid {} packet; not writing".format(
                result.get("status")
            )
        )

    # 7. Dry-run: report intent, change nothing.
    if dry_run:
        print("DRY-RUN: would set status {} (no changes written)".format(result["status"]))
        print("  packet:      {}".format(src_abs))
        print("  status:      {} -> {}".format(status, result["status"]))
        print("  source_path: {}".format(result["source_path"]))
        if decision == "DTA":
            print("  move:        {} -> {}".format(src_abs, dest))
        if result["status"] == "CTA":
            print("  clearance_expires_at: {}".format(result["clearance_expires_at"]))
        if reason:
            print("  reason:      {}".format(reason))
        print("  validations: PASS")
        return 0

    # 8a. DTA is a move: write the destination exclusively, re-validate from disk,
    #     then remove the source last (fail toward a visible duplicate, not loss).
    if decision == "DTA":
        werr = wpc.write_exclusive(dest, result)
        if werr:
            return _refuse(werr)
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
        try:
            os.remove(src_abs)
        except OSError as exc:
            print(
                "WARNING: DTA packet written to {} but could not remove source {}: "
                "{}".format(dest, src_abs, exc), file=sys.stderr
            )
            print(
                "MANUAL CLEANUP NEEDED: the destination is the authoritative packet; "
                "remove the stale source duplicate.", file=sys.stderr
            )
            return 1
        print("DTA: {} -> {}".format(src_abs, dest))
        print("Reason: {}".format(reason))
        return 0

    # 8b. CTA and RFI_PENDING stay in the outbox: rewrite the file in place.
    werr = write_inplace(src_abs, result)
    if werr:
        return _refuse(werr)
    written, wcode = wpc.load_packet(src_abs)
    post_disk = ["packet unreadable after write"] if wcode is not None else (
        wpv.validate(written) + wpv.validate_queue_path(src_abs, written.get("status"))
    )
    if post_disk:
        for err in post_disk:
            print("  - {}".format(err), file=sys.stderr)
        return _refuse("post-write validation failed; the on-disk packet may need review")

    if result["status"] == "CTA":
        print("CTA: {} (status CTA, lease expires {})".format(
            src_abs, result["clearance_expires_at"]))
        if reason:
            print("Reason: {}".format(reason))
    else:
        print("RFI_PENDING: {} (status RFI_PENDING)".format(src_abs))
        print("Question: {}".format(reason))
    return 0


def cmd_cta(args):
    reason = (args.reason or "").strip() or None
    return decide(args.packet_file, "CTA", actor=args.actor, reason=reason,
                  dry_run=args.dry_run, lease_minutes=args.lease_minutes)


def cmd_dta(args):
    if not args.reason or not args.reason.strip():
        return _refuse("--reason is required and must be non-empty")
    return decide(args.packet_file, "DTA", actor=args.actor,
                  reason=args.reason.strip(), dry_run=args.dry_run)


def cmd_rfi(args):
    if not args.reason or not args.reason.strip():
        return _refuse("--reason is required and must be non-empty")
    return decide(args.packet_file, "RFI_PENDING", actor=args.actor,
                  reason=args.reason.strip(), dry_run=args.dry_run)


def _add_common(p):
    p.add_argument("packet_file", help="Path to the RTA/IN_REVIEW packet JSON in clearance_outbox/.")
    p.add_argument("--actor", default=None, metavar="ACTOR_ID",
                   help="Actor id that issues the decision (default: OPERATOR-0001).")
    p.add_argument("--dry-run", action="store_true",
                   help="Validate and report the intended decision without writing anything.")


def build_parser():
    parser = argparse.ArgumentParser(
        prog="clearwright_decide",
        description=(
            "Manual ClearWright clearance decisions (v0.1) on one packet waiting in "
            "clearance_outbox/. Clear (CTA), deny (DTA), or request information "
            "(RFI). Explicit and manual: no daemon, scheduler, policy engine, or "
            "Discord behavior. Only RTA or IN_REVIEW packets in clearance_outbox/ "
            "may be decided. Command-authority examples use OPERATOR-0001.\n\n"
            "Exit codes:\n"
            "  0  decided (or dry-run validated)\n"
            "  1  refused or invalid\n"
            "  2  file not found, unreadable, or JSON parse error"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_cta = sub.add_parser(
        "cta",
        help="Clear to act (status CTA). Packet stays in clearance_outbox until claimed.",
        description=(
            "Clear one RTA/IN_REVIEW packet to act. Sets status CTA and a bounded "
            "clearance lease (clearance_expires_at); the packet stays in "
            "clearance_outbox/ until an authorized actor claims it. Consensus may "
            "support a clearance, but the operator remains the final override."
        ),
    )
    _add_common(p_cta)
    p_cta.add_argument("--reason", default=None, metavar="TEXT",
                       help="Optional rationale recorded in the decision and audit event.")
    p_cta.add_argument("--lease-minutes", type=int, default=DEFAULT_LEASE_MINUTES,
                       metavar="N",
                       help="Clearance lease length in minutes (default: 120).")
    p_cta.set_defaults(func=cmd_cta)

    p_dta = sub.add_parser(
        "dta",
        help="Deny to act (status DTA). Moves the packet to clearance_done/.",
        description=(
            "Deny one RTA/IN_REVIEW packet. Sets status DTA and moves it to "
            "clearance_done/. A DTA is a successful safety and governance outcome, "
            "not a failure, and never goes to clearance_failed/. Requires --reason."
        ),
    )
    _add_common(p_dta)
    p_dta.add_argument("--reason", required=True, metavar="TEXT",
                       help="Required. Why the request is denied.")
    p_dta.set_defaults(func=cmd_dta)

    p_rfi = sub.add_parser(
        "rfi",
        help="Request information (status RFI_PENDING). Packet stays in clearance_outbox.",
        description=(
            "Mark one RTA/IN_REVIEW packet as needing more information. Sets status "
            "RFI_PENDING and records the question; the packet stays in "
            "clearance_outbox/ for follow-up. Requires --reason (the question)."
        ),
    )
    _add_common(p_rfi)
    p_rfi.add_argument("--reason", required=True, metavar="TEXT",
                       help="Required. The information requested (the question).")
    p_rfi.set_defaults(func=cmd_rfi)

    return parser


def main():
    wpc._reconfigure_stdio()
    args = build_parser().parse_args()
    sys.exit(args.func(args))


if __name__ == "__main__":
    main()
