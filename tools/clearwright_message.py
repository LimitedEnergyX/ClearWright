#!/usr/bin/env python3
"""
tools/clearwright_message.py: ClearWright local communications loop.

Post, list, and respond to durable local messages in a clearance queue's
communications/ store, so Claude Desktop, Codex, scripts, or future workers can
hold a real, packet-linked conversation with the control plane over CLI,
PowerShell, curl, or local HTTP, without clicking the web page. The web UI is
the operator display; this is the integration surface.

Messages are a separate, durable log alongside the clearance queue, distinct
from clearance packets and from agent events. They do not touch the packet
schema or validator: a message never grants clearance or moves a packet.
Conversation never grants authority; the operator decides.

A message carries a thread_id (generated on post, reused on respond), an
optional packet_id, an actor, a role, a direction (inbound/outbound/internal),
a status (posted/read/responded), a timestamp, a source, and a simulated flag
(false for real local messages). Every message is real by default; simulated
demo conversation lives only in demo mode.

A message may also carry an optional intent: "chat" marks plain conversation
that needs no action, and "request" marks an actionable request. Chat is not
work: an inbound message with intent "chat" never derives a work item. When
intent is absent the message stays actionable, so existing worker tools,
relays, and scripts keep their behavior without any change.

The control plane server imports build_message, write_message, and read_messages
for its /api/messages endpoints, so the CLI and the API share one implementation.

Exit codes: 0 recorded (or dry-run/listed), 1 refused/invalid, 2 argument error
"""
import argparse
import json
import os
import sys
from datetime import datetime, timedelta, timezone

import clearwright_writer_lock as cwl

COMMS_DIR = "communications"
DEFAULT_ROLE = "agent"
DEFAULT_SOURCE = "local-adapter"
DEFAULT_DIRECTION = "inbound"
DEFAULT_STATUS = "posted"
DIRECTIONS = ("inbound", "outbound", "internal")
STATUSES = ("posted", "read", "claimed", "responded")
INTENTS = ("chat", "request")
# Terminal closure markers on a closing message. "done" is governed completion;
# "closed_by_operator" is an explicit operator decision that is NOT DONE and
# never overrides the underlying council outcome.
CLOSURES = ("done", "closed_by_operator")


_last_dt = None


def _monotonic_dt():
    """Return a strictly increasing UTC datetime so message ids and timestamps
    stay unique and correctly ordered even when several are built within the
    same microsecond (a tight loop, or one process handling a burst)."""
    global _last_dt
    now = datetime.now(timezone.utc)
    if _last_dt is not None and now <= _last_dt:
        now = _last_dt + timedelta(microseconds=1)
    _last_dt = now
    return now


def _now_iso():
    # Microsecond precision, monotonic, so each message_id is unique per writer.
    return _monotonic_dt().strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _stamp():
    return _monotonic_dt().strftime("%Y%m%dT%H%M%S%f")


def build_message(actor, message, role=DEFAULT_ROLE, packet_id=None,
                  thread_id=None, direction=DEFAULT_DIRECTION,
                  status=DEFAULT_STATUS, source=DEFAULT_SOURCE, simulated=False,
                  work_item_id=None, intent=None, closure=None, closure_meta=None):
    """Return a new message dict. Raises ValueError if actor or message is
    missing/empty, or if direction/status/intent is not one of the allowed
    values. A new thread_id is generated when one is not supplied. Only a
    non-empty packet_id, work_item_id, and intent are included. intent "chat"
    marks plain conversation (never a work item); absent means actionable."""
    if not actor or not str(actor).strip():
        raise ValueError("actor is required and must be non-empty")
    if not message or not str(message).strip():
        raise ValueError("message is required and must be non-empty")
    direction = (str(direction).strip() or DEFAULT_DIRECTION) if direction else DEFAULT_DIRECTION
    if direction not in DIRECTIONS:
        raise ValueError("direction must be one of: {}".format(", ".join(DIRECTIONS)))
    status = (str(status).strip() or DEFAULT_STATUS) if status else DEFAULT_STATUS
    if status not in STATUSES:
        raise ValueError("status must be one of: {}".format(", ".join(STATUSES)))
    intent = str(intent).strip() if intent and str(intent).strip() else None
    if intent is not None and intent not in INTENTS:
        raise ValueError("intent must be one of: {}".format(", ".join(INTENTS)))
    closure = str(closure).strip() if closure and str(closure).strip() else None
    if closure is not None and closure not in CLOSURES:
        raise ValueError("closure must be one of: {}".format(", ".join(CLOSURES)))
    if closure_meta is not None and not isinstance(closure_meta, dict):
        raise ValueError("closure_meta must be an object")
    stamp = _stamp()
    thread = str(thread_id).strip() if thread_id and str(thread_id).strip() else "thr-" + stamp
    msg = {
        "message_id": "msg-" + stamp,
        "thread_id": thread,
        "at": _now_iso(),
        "actor": str(actor).strip(),
        "role": (str(role).strip() or DEFAULT_ROLE) if role else DEFAULT_ROLE,
        "direction": direction,
        "status": status,
        "message": str(message).strip(),
        "source": (str(source).strip() or DEFAULT_SOURCE) if source else DEFAULT_SOURCE,
        "simulated": bool(simulated),
    }
    if packet_id and str(packet_id).strip():
        msg["packet_id"] = str(packet_id).strip()
    if work_item_id and str(work_item_id).strip():
        msg["work_item_id"] = str(work_item_id).strip()
    if intent is not None:
        msg["intent"] = intent
    if closure is not None:
        msg["closure"] = closure
        if closure_meta:
            msg["closure_meta"] = closure_meta
    return msg


def comms_dir(root):
    return os.path.join(root, COMMS_DIR)


def write_message(root, message):
    """Write one message as a durable JSON file under root/communications/.
    Creates the directory if missing. Never overwrites: on the rare same-
    microsecond collision, a suffix is added. Returns the path written.

    Raises clearwright_writer_lock.MaintenanceInProgress while an archive
    operation holds exclusivity over this queue root -- message writes are one
    of the shared choke points every durable writer drains through before
    archive begins moving records."""
    directory = comms_dir(root)
    os.makedirs(directory, exist_ok=True)
    base = message["message_id"]
    with cwl.write_token(root, purpose="message"):
        for attempt in range(1000):
            name = base + (".json" if attempt == 0 else "-{}.json".format(attempt))
            path = os.path.join(directory, name)
            try:
                fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
            except FileExistsError:
                continue
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(message, fh, indent=2)
                fh.write("\n")
                fh.flush()
                os.fsync(fh.fileno())
            return path
    raise OSError("could not allocate a unique message filename")


def read_messages(root, packet_id=None, thread_id=None, limit=None):
    """Return messages as a list of dicts, ordered oldest-first by (at,
    filename). Optional packet_id and/or thread_id filters; optional limit
    returns the most recent N. Missing or empty store yields an empty list."""
    directory = comms_dir(root)
    if not os.path.isdir(directory):
        return []
    rows = []
    for name in os.listdir(directory):
        if not name.endswith(".json"):
            continue
        try:
            with open(os.path.join(directory, name), encoding="utf-8") as fh:
                message = json.load(fh)
        except (OSError, ValueError):
            continue
        rows.append((message.get("at", ""), name, message))
    rows.sort(key=lambda r: (r[0], r[1]))
    messages = [r[2] for r in rows]
    if packet_id:
        messages = [m for m in messages if m.get("packet_id") == packet_id]
    if thread_id:
        messages = [m for m in messages if m.get("thread_id") == thread_id]
    if limit is not None and limit >= 0:
        messages = messages[-limit:]
    return messages


def _record(args, respond):
    thread_id = getattr(args, "thread_id", None)
    if respond and not (thread_id and str(thread_id).strip()):
        print("REFUSED: respond requires --thread-id", file=sys.stderr)
        return 1
    direction = args.direction or ("outbound" if respond else DEFAULT_DIRECTION)
    status = args.status or ("responded" if respond else DEFAULT_STATUS)
    try:
        message = build_message(
            args.actor, args.message, role=args.role, packet_id=args.packet_id,
            thread_id=thread_id, direction=direction, status=status,
            source=args.source, simulated=args.simulated, intent=args.intent,
        )
    except ValueError as exc:
        print("REFUSED: {}".format(exc), file=sys.stderr)
        return 1

    if args.dry_run:
        print("DRY-RUN: would record message (no changes written)")
        print(json.dumps(message, indent=2))
        return 0

    if not os.path.isdir(args.queue_root):
        print("REFUSED: queue root {!r} does not exist".format(args.queue_root),
              file=sys.stderr)
        return 1
    try:
        path = write_message(args.queue_root, message)
    except OSError as exc:
        print("REFUSED: {}".format(exc), file=sys.stderr)
        return 1
    print("RECORDED: {} ({} in thread {})".format(
        path, message["message_id"], message["thread_id"]))
    return 0


def post(args):
    return _record(args, respond=False)


def respond(args):
    return _record(args, respond=True)


def listing(args):
    messages = read_messages(
        args.queue_root, packet_id=args.packet_id, thread_id=args.thread_id,
        limit=args.limit,
    )
    print(json.dumps(messages, indent=2))
    return 0


def _add_write_args(sub, thread_required):
    sub.add_argument("queue_root", help="Clearance queue root directory.")
    sub.add_argument("--actor", required=True, metavar="ID",
                     help="Required. Who sent the message (for example claude, codex, script).")
    sub.add_argument("--message", required=True, metavar="TEXT",
                     help="Required. The message text.")
    sub.add_argument("--role", default=DEFAULT_ROLE, metavar="ROLE",
                     help="Actor role (default: {}).".format(DEFAULT_ROLE))
    sub.add_argument("--packet-id", default=None, metavar="ID",
                     help="Optional clearance packet this message relates to.")
    sub.add_argument("--thread-id", default=None, metavar="ID",
                     required=thread_required,
                     help=("Conversation thread id. "
                           + ("Required for respond." if thread_required
                              else "A new thread is started when omitted.")))
    sub.add_argument("--direction", default=None, choices=DIRECTIONS,
                     help="Message direction (default: inbound for post, outbound for respond).")
    sub.add_argument("--status", default=None, choices=STATUSES,
                     help="Message status (default: posted for post, responded for respond).")
    sub.add_argument("--source", default=DEFAULT_SOURCE, metavar="NAME",
                     help="Optional source label (default: {}).".format(DEFAULT_SOURCE))
    sub.add_argument("--intent", default=None, choices=INTENTS,
                     help=("Optional intent: chat is plain conversation (never "
                           "a work item); request is actionable. Omitted means "
                           "actionable, so existing callers are unchanged."))
    sub.add_argument("--simulated", action="store_true",
                     help="Mark this message as simulated/demo, not real communication.")
    sub.add_argument("--dry-run", action="store_true",
                     help="Validate and print the message without writing anything.")


def build_parser():
    parser = argparse.ArgumentParser(
        prog="clearwright_message",
        description=(
            "Post, list, and respond to durable local messages in a clearance "
            "queue's communications/ store. This is the local integration "
            "surface for agents, tools, and scripts (CLI / curl / local HTTP); "
            "the web UI is the operator display. A message is not a clearance "
            "packet and grants no authority.\n\n"
            "Exit codes:\n"
            "  0  recorded (or dry-run / listed)\n"
            "  1  refused or invalid\n"
            "  2  argument error"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    subs = parser.add_subparsers(dest="command", required=True)

    p_post = subs.add_parser("post", help="Post a new message (starts a thread unless --thread-id).")
    _add_write_args(p_post, thread_required=False)
    p_post.set_defaults(func=post)

    p_respond = subs.add_parser("respond", help="Respond on an existing thread (--thread-id required).")
    _add_write_args(p_respond, thread_required=True)
    p_respond.set_defaults(func=respond)

    p_list = subs.add_parser("list", help="List messages, optionally filtered by packet or thread.")
    p_list.add_argument("queue_root", help="Clearance queue root directory.")
    p_list.add_argument("--packet-id", default=None, metavar="ID",
                        help="Only messages related to this clearance packet.")
    p_list.add_argument("--thread-id", default=None, metavar="ID",
                        help="Only messages in this conversation thread.")
    p_list.add_argument("--limit", type=int, default=None, metavar="N",
                        help="Return only the most recent N messages.")
    p_list.set_defaults(func=listing)

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
