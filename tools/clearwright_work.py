#!/usr/bin/env python3
"""
tools/clearwright_work.py: ClearWright local dispatch / work-item loop.

List, claim, and respond to work items derived from a clearance queue's existing
durable state, so Claude Desktop, Codex, scripts, or future workers can pick up
and act on real local work over CLI, PowerShell, curl, or local HTTP, without a
browser. The web UI is the operator display; this is the worker surface.

Work items are DERIVED, not a separate database. The source records stay
authoritative:
  - clearance packets remain the authority record,
  - messages remain the communication record,
  - agent events remain activity/context,
  - DONE results remain the outcome record.

Derivation:
  - an inbound message thread with no response  -> kind "message"
  - a CTA packet in clearance_outbox            -> kind "packet"   (claimable)
  - an IN_PROGRESS packet                        -> kind "in_progress"
  - an RFI_PENDING packet                        -> kind "rfi"

Work item ids are stable and deterministic:
  message:<message_id>, packet:<packet_id>:cta, in_progress:<packet_id>,
  rfi:<packet_id>.

Claiming and responding never mutate the packet schema or validator. Claiming a
CTA packet uses the existing clearwright_claim lifecycle for the real packet
move; every claim and response is also written as a durable message in the
related thread, so the original request is never lost. Conversation and claims
grant no authority; the operator decides.

The control plane server imports derive_work_items, claim_work_item, and
respond_work_item for its /api/work-items endpoints, so the CLI and the API
share one implementation.

Exit codes: 0 ok (or listed), 1 refused/invalid, 2 argument error
"""
import argparse
import json
import os
import sys

import clearwright_message as cwm
import clearwright_claim as cwc

LANES = ["clearance_outbox", "clearance_in_progress",
         "clearance_done", "clearance_failed"]


def _read_packets(root):
    """Return a light view of every packet across the lanes:
    {packet_id, status, lane, title, filename, path}. Unreadable files are
    skipped rather than raising."""
    rows = []
    for lane in LANES:
        lane_dir = os.path.join(root, lane)
        if not os.path.isdir(lane_dir):
            continue
        for name in sorted(os.listdir(lane_dir)):
            if not name.endswith(".json"):
                continue
            path = os.path.join(lane_dir, name)
            try:
                with open(path, encoding="utf-8") as fh:
                    packet = json.load(fh)
            except (OSError, ValueError):
                continue
            rows.append({
                "packet_id": packet.get("packet_id"),
                "status": packet.get("status"),
                "lane": lane,
                "title": packet.get("title"),
                "filename": name,
                "path": path,
            })
    return rows


def _work_item(work_item_id, kind, status, next_action, **extra):
    item = {
        "work_item_id": work_item_id,
        "kind": kind,
        "status": status,
        "next_action": next_action,
    }
    for key, value in extra.items():
        if value is not None and value != "":
            item[key] = value
    return item


# Priority ordering for the derived list: actionable packets first, then
# clarification, then in-progress, then message threads (newest first).
_KIND_ORDER = {"packet": 0, "rfi": 1, "in_progress": 2, "message": 3}


def derive_work_items(root):
    """Return the derived work-item list, most actionable first. Nothing is
    written; the list is computed from packets and messages on disk."""
    items = []
    for p in _read_packets(root):
        pid, status, lane = p["packet_id"], p["status"], p["lane"]
        if not pid:
            continue
        if lane == "clearance_outbox" and status == "CTA":
            items.append(_work_item(
                "packet:{}:cta".format(pid), "packet", "open", "claim",
                packet_id=pid, title=p["title"], summary=p["title"]))
        elif lane == "clearance_outbox" and status == "RFI_PENDING":
            items.append(_work_item(
                "rfi:{}".format(pid), "rfi", "open", "answer clarification",
                packet_id=pid, title=p["title"], summary=p["title"]))
        elif lane == "clearance_in_progress" and status == "IN_PROGRESS":
            items.append(_work_item(
                "in_progress:{}".format(pid), "in_progress", "open",
                "post progress or complete",
                packet_id=pid, title=p["title"], summary=p["title"]))

    # Message threads: an inbound request with no response is open work.
    messages = cwm.read_messages(root)
    threads = {}
    order = []
    for m in messages:
        tid = m.get("thread_id") or "thr-unknown"
        if tid not in threads:
            threads[tid] = []
            order.append(tid)
        threads[tid].append(m)
    for tid in order:
        msgs = threads[tid]
        origin = next((m for m in msgs if m.get("direction") == "inbound"), None)
        if origin is None:
            continue  # worker-only thread, not an inbound request
        has_response = any(m.get("direction") == "outbound"
                           or m.get("status") == "responded" for m in msgs)
        if has_response:
            continue  # the request was answered; the thread is closed
        claim_msg = next((m for m in msgs if m.get("status") == "claimed"), None)
        status = "claimed" if claim_msg else "open"
        items.append(_work_item(
            "message:{}".format(origin.get("message_id")), "message", status,
            "respond",
            thread_id=tid, packet_id=origin.get("packet_id"),
            actor=origin.get("actor"), source=origin.get("source"),
            title=origin.get("message"), summary=origin.get("message"),
            created_at=origin.get("at"),
            claimed_by=(claim_msg or {}).get("actor"),
            claimed_at=(claim_msg or {}).get("at")))

    items.sort(key=lambda it: (_KIND_ORDER.get(it["kind"], 9),
                               _neg_time(it.get("created_at"))))
    return items


def _neg_time(at):
    # Sort message items newest-first; empty timestamps sort last.
    return "" if not at else "".join(chr(255 - ord(c)) if ord(c) < 255 else c for c in at)


def parse_work_item_id(work_item_id):
    """Return (kind, ref) for a work item id, or (None, None) if unrecognized.
    ref is the message_id for messages and the packet_id for packet kinds."""
    wid = str(work_item_id or "")
    if wid.startswith("message:"):
        return "message", wid[len("message:"):]
    if wid.startswith("packet:") and wid.endswith(":cta"):
        return "packet", wid[len("packet:"):-len(":cta")]
    if wid.startswith("in_progress:"):
        return "in_progress", wid[len("in_progress:"):]
    if wid.startswith("rfi:"):
        return "rfi", wid[len("rfi:"):]
    return None, None


def _resolve_target(root, work_item_id):
    """Return (thread_id, packet_id) for a work item so a claim/response lands
    in the right thread. A message reuses its own thread; a packet reuses an
    existing packet thread if one exists, otherwise a new thread is started."""
    kind, ref = parse_work_item_id(work_item_id)
    if kind == "message":
        for m in cwm.read_messages(root):
            if m.get("message_id") == ref:
                return m.get("thread_id"), m.get("packet_id")
        return None, None
    if kind in ("packet", "in_progress", "rfi"):
        packet_id = ref
        for m in cwm.read_messages(root, packet_id=packet_id):
            if m.get("thread_id"):
                return m.get("thread_id"), packet_id
        return None, packet_id
    return None, None


def _find_packet_path(root, packet_id, lane=None):
    for p in _read_packets(root):
        if p["packet_id"] == packet_id and (lane is None or p["lane"] == lane):
            return p["path"]
    return None


def claim_work_item(root, work_item_id, actor, role=cwm.DEFAULT_ROLE,
                    source="local-http"):
    """Claim a work item. For a CTA packet, perform the real packet claim
    through the existing clearwright_claim lifecycle, then record a durable
    claim message. For message/rfi/in_progress items, record a durable claim
    message only (the packet lifecycle is unchanged). The original request is
    never lost."""
    if not actor or not str(actor).strip():
        return {"ok": False, "error": "actor is required and must be non-empty"}
    kind, ref = parse_work_item_id(work_item_id)
    if kind is None:
        return {"ok": False, "error": "unrecognized work_item_id"}

    packet_claimed = None
    if kind == "packet":
        path = _find_packet_path(root, ref, lane="clearance_outbox")
        if path is None:
            return {"ok": False, "error": "CTA packet {!r} not found in the outbox".format(ref)}
        code = cwc.claim(path, claimant=str(actor).strip())
        if code != 0:
            return {"ok": False, "error": "claim tool refused the packet claim (exit {})".format(code)}
        packet_claimed = ref

    thread_id, packet_id = _resolve_target(root, work_item_id)
    note = ("claimed CTA packet " + ref) if kind == "packet" else ("claimed work item " + str(work_item_id))
    try:
        message = cwm.build_message(
            actor, note, role=role, packet_id=packet_id, thread_id=thread_id,
            direction="internal", status="claimed", source=source,
            work_item_id=work_item_id)
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}
    try:
        cwm.write_message(root, message)
    except OSError as exc:
        return {"ok": False, "error": str(exc)}
    result = {"ok": True, "work_item_id": work_item_id, "kind": kind,
              "message": message, "thread_id": message["thread_id"]}
    if packet_claimed:
        result["packet_claimed"] = packet_claimed
    return result


def respond_work_item(root, work_item_id, actor, message, role=cwm.DEFAULT_ROLE,
                      source="local-http"):
    """Respond to a work item by writing a durable response message in the
    related thread. The packet status is not altered here; the operator uses the
    existing lifecycle tools for that."""
    if parse_work_item_id(work_item_id)[0] is None:
        return {"ok": False, "error": "unrecognized work_item_id"}
    thread_id, packet_id = _resolve_target(root, work_item_id)
    try:
        msg = cwm.build_message(
            actor, message, role=role, packet_id=packet_id, thread_id=thread_id,
            direction="outbound", status="responded", source=source,
            work_item_id=work_item_id)
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}
    try:
        cwm.write_message(root, msg)
    except OSError as exc:
        return {"ok": False, "error": str(exc)}
    return {"ok": True, "work_item_id": work_item_id, "message": msg,
            "thread_id": msg["thread_id"]}


def progress_work_item(root, work_item_id, actor, message, role=cwm.DEFAULT_ROLE,
                       source="local-http"):
    """Post a progress note on a work item as a durable internal message in the
    related thread. Progress is working context, not a final answer, so the work
    item stays open."""
    if parse_work_item_id(work_item_id)[0] is None:
        return {"ok": False, "error": "unrecognized work_item_id"}
    thread_id, packet_id = _resolve_target(root, work_item_id)
    try:
        msg = cwm.build_message(
            actor, message, role=role, packet_id=packet_id, thread_id=thread_id,
            direction="internal", status="posted", source=source,
            work_item_id=work_item_id)
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}
    try:
        cwm.write_message(root, msg)
    except OSError as exc:
        return {"ok": False, "error": str(exc)}
    return {"ok": True, "work_item_id": work_item_id, "message": msg,
            "thread_id": msg["thread_id"]}


def find_work_item(root, work_item_id):
    """Return the derived work item matching work_item_id, or None."""
    for item in derive_work_items(root):
        if item.get("work_item_id") == work_item_id:
            return item
    return None


def worker_status(root):
    """A small read-only worker view: work-item counts by status and kind,
    packet counts by lane, and recent message and agent-event counts. Shared by
    the worker CLI (status) and GET /api/worker-status so both agree."""
    items = derive_work_items(root)
    packets = _read_packets(root)
    lanes = {}
    for p in packets:
        lanes[p["lane"]] = lanes.get(p["lane"], 0) + 1
    kinds = {}
    for it in items:
        kinds[it["kind"]] = kinds.get(it["kind"], 0) + 1
    messages = cwm.read_messages(root)
    try:
        import clearwright_agent_event as cwae
        events = cwae.read_events(root)
    except Exception:
        events = []
    return {
        "work_items_total": len(items),
        "work_items_open": len([i for i in items if i.get("status") == "open"]),
        "work_items_claimed": len([i for i in items if i.get("status") == "claimed"]),
        "work_items_by_kind": kinds,
        "packets_by_lane": lanes,
        "messages_total": len(messages),
        "agent_events_total": len(events),
        "next_work_item_id": items[0]["work_item_id"] if items else None,
    }


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #

def _require_queue(root):
    if not os.path.isdir(root):
        print("REFUSED: queue root {!r} does not exist".format(root), file=sys.stderr)
        return False
    return True


def cli_list(args):
    if not _require_queue(args.queue_root):
        return 1
    items = derive_work_items(args.queue_root)
    if args.kind:
        items = [it for it in items if it["kind"] == args.kind]
    print(json.dumps(items, indent=2))
    return 0


def cli_claim(args):
    if not _require_queue(args.queue_root):
        return 1
    result = claim_work_item(args.queue_root, args.work_item_id, args.actor, role=args.role)
    if not result["ok"]:
        print("REFUSED: {}".format(result["error"]), file=sys.stderr)
        return 1
    print("CLAIMED: {} ({})".format(result["work_item_id"], result["message"]["message_id"]))
    return 0


def cli_respond(args):
    if not _require_queue(args.queue_root):
        return 1
    result = respond_work_item(args.queue_root, args.work_item_id, args.actor,
                               args.message, role=args.role)
    if not result["ok"]:
        print("REFUSED: {}".format(result["error"]), file=sys.stderr)
        return 1
    print("RESPONDED: {} ({} in thread {})".format(
        result["work_item_id"], result["message"]["message_id"], result["thread_id"]))
    return 0


def build_parser():
    parser = argparse.ArgumentParser(
        prog="clearwright_work",
        description=(
            "List, claim, and respond to work items derived from a clearance "
            "queue's durable state (messages and packets). This is the local "
            "worker surface for agents, tools, and scripts (CLI / curl / local "
            "HTTP); the web UI is the operator display. Work items grant no "
            "authority; the operator decides.\n\n"
            "Exit codes:\n"
            "  0  ok (or listed)\n"
            "  1  refused or invalid\n"
            "  2  argument error"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    subs = parser.add_subparsers(dest="command", required=True)

    p_list = subs.add_parser("list", help="List open work items.")
    p_list.add_argument("queue_root", help="Clearance queue root directory.")
    p_list.add_argument("--kind", default=None,
                        choices=["message", "packet", "in_progress", "rfi"],
                        help="Only work items of this kind.")
    p_list.set_defaults(func=cli_list)

    p_claim = subs.add_parser("claim", help="Claim a work item.")
    p_claim.add_argument("queue_root", help="Clearance queue root directory.")
    p_claim.add_argument("--work-item-id", required=True, metavar="ID",
                         help="Required. The work item id from list.")
    p_claim.add_argument("--actor", required=True, metavar="ID",
                         help="Required. Who is claiming (for example claude).")
    p_claim.add_argument("--role", default=cwm.DEFAULT_ROLE, metavar="ROLE",
                         help="Actor role (default: {}).".format(cwm.DEFAULT_ROLE))
    p_claim.set_defaults(func=cli_claim)

    p_respond = subs.add_parser("respond", help="Respond to a work item.")
    p_respond.add_argument("queue_root", help="Clearance queue root directory.")
    p_respond.add_argument("--work-item-id", required=True, metavar="ID",
                           help="Required. The work item id from list.")
    p_respond.add_argument("--actor", required=True, metavar="ID",
                           help="Required. Who is responding (for example claude).")
    p_respond.add_argument("--message", required=True, metavar="TEXT",
                           help="Required. The response text.")
    p_respond.add_argument("--role", default=cwm.DEFAULT_ROLE, metavar="ROLE",
                           help="Actor role (default: {}).".format(cwm.DEFAULT_ROLE))
    p_respond.set_defaults(func=cli_respond)

    return parser


def main():
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            try:
                reconfigure(encoding="utf-8", errors="backslashreplace")
            except (ValueError, OSError):
                pass
    args = build_parser().parse_args()
    sys.exit(args.func(args))


if __name__ == "__main__":
    main()
