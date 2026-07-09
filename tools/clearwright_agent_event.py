#!/usr/bin/env python3
"""
tools/clearwright_agent_event.py: ClearWright local agent event adapter.

Record one agent event into a clearance queue's durable agent_events/ store, so
Claude Desktop, Codex, scripts, or future workers can send real agent activity
into the control plane over CLI, PowerShell, curl, or local HTTP, without
clicking the web page. The web UI is the operator display; this is the
integration surface.

Agent events are a separate, durable log alongside the clearance queue. They are
NOT clearance packets and do not touch the packet schema or validator: an event
never grants clearance or moves a packet. Consensus or agent chatter never
grants authority; the operator decides.

The control plane server imports build_event, write_event, and read_events for
its /api/agent-events endpoints, so the CLI and the API share one implementation.

Exit codes: 0 recorded (or dry-run validated), 1 refused/invalid, 2 argument error
"""
import argparse
import json
import os
import sys
from datetime import datetime, timezone

AGENT_EVENTS_DIR = "agent_events"
DEFAULT_ROLE = "agent"
DEFAULT_SEVERITY = "info"
DEFAULT_SOURCE = "local-adapter"


def _now_iso():
    # Microsecond precision so each event_id is unique per writer.
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _stamp():
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%f")


def build_event(actor, message, role=DEFAULT_ROLE, packet_id=None,
                severity=DEFAULT_SEVERITY, source=DEFAULT_SOURCE,
                simulated=False):
    """Return a new agent-event dict. Raises ValueError if actor or message is
    missing/empty. Only non-empty optional fields are included."""
    if not actor or not str(actor).strip():
        raise ValueError("actor is required and must be non-empty")
    if not message or not str(message).strip():
        raise ValueError("message is required and must be non-empty")
    event = {
        "event_id": "evt-" + _stamp(),
        "at": _now_iso(),
        "actor": str(actor).strip(),
        "role": (str(role).strip() or DEFAULT_ROLE) if role else DEFAULT_ROLE,
        "message": str(message).strip(),
        "severity": (str(severity).strip() or DEFAULT_SEVERITY) if severity else DEFAULT_SEVERITY,
        "source": (str(source).strip() or DEFAULT_SOURCE) if source else DEFAULT_SOURCE,
        "simulated": bool(simulated),
    }
    if packet_id and str(packet_id).strip():
        event["packet_id"] = str(packet_id).strip()
    return event


def events_dir(root):
    return os.path.join(root, AGENT_EVENTS_DIR)


def write_event(root, event):
    """Write one event as a durable JSON file under root/agent_events/.
    Creates the directory if missing. Never overwrites: on the rare same-
    microsecond collision, a suffix is added. Returns the path written."""
    directory = events_dir(root)
    os.makedirs(directory, exist_ok=True)
    base = event["event_id"]
    for attempt in range(1000):
        name = base + (".json" if attempt == 0 else "-{}.json".format(attempt))
        path = os.path.join(directory, name)
        try:
            fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
        except FileExistsError:
            continue
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(event, fh, indent=2)
            fh.write("\n")
            fh.flush()
            os.fsync(fh.fileno())
        return path
    raise OSError("could not allocate a unique event filename")


def read_events(root, packet_id=None, limit=None):
    """Return agent events as a list of dicts, ordered oldest-first by (at,
    filename). Optional packet_id filter; optional limit returns the most
    recent N. Missing or empty store yields an empty list."""
    directory = events_dir(root)
    if not os.path.isdir(directory):
        return []
    rows = []
    for name in os.listdir(directory):
        if not name.endswith(".json"):
            continue
        try:
            with open(os.path.join(directory, name), encoding="utf-8") as fh:
                event = json.load(fh)
        except (OSError, ValueError):
            continue
        rows.append((event.get("at", ""), name, event))
    rows.sort(key=lambda r: (r[0], r[1]))
    events = [r[2] for r in rows]
    if packet_id:
        events = [e for e in events if e.get("packet_id") == packet_id]
    if limit is not None and limit >= 0:
        events = events[-limit:]
    return events


def record(args):
    try:
        event = build_event(
            args.actor, args.message, role=args.role, packet_id=args.packet_id,
            severity=args.severity, source=args.source, simulated=args.simulated,
        )
    except ValueError as exc:
        print("REFUSED: {}".format(exc), file=sys.stderr)
        return 1

    if args.dry_run:
        print("DRY-RUN: would record agent event (no changes written)")
        print(json.dumps(event, indent=2))
        return 0

    if not os.path.isdir(args.queue_root):
        print("REFUSED: queue root {!r} does not exist".format(args.queue_root),
              file=sys.stderr)
        return 1
    try:
        path = write_event(args.queue_root, event)
    except OSError as exc:
        print("REFUSED: {}".format(exc), file=sys.stderr)
        return 1
    print("RECORDED: {} ({})".format(path, event["event_id"]))
    return 0


def build_parser():
    parser = argparse.ArgumentParser(
        prog="clearwright_agent_event",
        description=(
            "Record one agent event into a clearance queue's durable "
            "agent_events/ store. This is the local integration surface for "
            "agents, tools, and scripts (CLI / curl / local HTTP); the web UI "
            "is the operator display. An event is not a clearance packet and "
            "grants no authority.\n\n"
            "Exit codes:\n"
            "  0  recorded (or dry-run validated)\n"
            "  1  refused or invalid\n"
            "  2  argument error"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("queue_root", help="Clearance queue root directory.")
    parser.add_argument("--actor", required=True, metavar="ID",
                        help="Required. Who emitted the event (for example claude, codex, script).")
    parser.add_argument("--message", required=True, metavar="TEXT",
                        help="Required. The event message.")
    parser.add_argument("--role", default=DEFAULT_ROLE, metavar="ROLE",
                        help="Actor role (default: {}).".format(DEFAULT_ROLE))
    parser.add_argument("--packet-id", default=None, metavar="ID",
                        help="Optional clearance packet this event relates to.")
    parser.add_argument("--severity", default=DEFAULT_SEVERITY, metavar="LEVEL",
                        help="Optional severity (default: {}).".format(DEFAULT_SEVERITY))
    parser.add_argument("--source", default=DEFAULT_SOURCE, metavar="NAME",
                        help="Optional source label (default: {}).".format(DEFAULT_SOURCE))
    parser.add_argument("--simulated", action="store_true",
                        help="Mark this event as a simulated/demo event, not real agent activity.")
    parser.add_argument("--dry-run", action="store_true",
                        help="Validate and print the event without writing anything.")
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
    sys.exit(record(args))


if __name__ == "__main__":
    main()
