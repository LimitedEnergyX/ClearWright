#!/usr/bin/env python3
"""
tools/clearwright_worker.py: ClearWright worker command bridge.

A thin orchestration layer so Claude Desktop, Claude Code, Codex, or any script
can act as a ClearWright worker over the command line, PowerShell, curl, or local
HTTP, without ChromeMCP and without copy/paste into the web page. This is the
integration path when the operator says "use CW" or "review with CW": find
pending work, claim it, post progress, and post a final response, all durable and
visible in the ClearWright web UI and history.

The bridge is orchestration only. It reuses the existing work-item and message
functions in clearwright_work / clearwright_message, so the CLI and the
/api/work-items HTTP routes share one implementation: the same work_item_id
format, claim semantics, thread_id and packet_id preservation, durable message
files, and sorting. It does not change the packet schema or validator, add a
database, or grant authority; the operator decides.

Commands:
  next     print the next actionable work item (JSON), or report none
  claim    record a durable claim on a work item
  progress post a durable progress note (internal) on a work item
  respond  post a durable final response (outbound) on a work item
  status   print a small read-only worker status (counts)

Exit codes: 0 ok (or nothing to do), 1 refused/invalid, 2 argument error
"""
import argparse
import json
import os
import sys

import clearwright_work as cww

DEFAULT_ROLE = "worker"


def _require_queue(root):
    if not os.path.isdir(root):
        print("REFUSED: queue root {!r} does not exist".format(root), file=sys.stderr)
        return False
    return True


def _emit(result):
    print(json.dumps(result, indent=2))


def cmd_next(args):
    if not _require_queue(args.queue_root):
        return 1
    items = cww.derive_work_items(args.queue_root)
    open_items = [it for it in items if it.get("status") == "open"] or items
    if not open_items:
        _emit({"ok": True, "work_item": None, "message": "No open ClearWright work items."})
        return 0
    _emit({"ok": True, "work_item": open_items[0]})
    return 0


def cmd_claim(args):
    if not _require_queue(args.queue_root):
        return 1
    if cww.find_work_item(args.queue_root, args.work_item_id) is None:
        print("REFUSED: no open work item {!r}".format(args.work_item_id), file=sys.stderr)
        return 1
    result = cww.claim_work_item(args.queue_root, args.work_item_id, args.actor,
                                 role=args.role, source="worker-bridge")
    if not result.get("ok"):
        print("REFUSED: {}".format(result.get("error")), file=sys.stderr)
        return 1
    _emit(result)
    return 0


def cmd_progress(args):
    if not _require_queue(args.queue_root):
        return 1
    if cww.find_work_item(args.queue_root, args.work_item_id) is None:
        print("REFUSED: no open work item {!r}".format(args.work_item_id), file=sys.stderr)
        return 1
    result = cww.progress_work_item(args.queue_root, args.work_item_id, args.actor,
                                    args.message, role=args.role, source="worker-bridge")
    if not result.get("ok"):
        print("REFUSED: {}".format(result.get("error")), file=sys.stderr)
        return 1
    _emit(result)
    return 0


def cmd_respond(args):
    if not _require_queue(args.queue_root):
        return 1
    if cww.find_work_item(args.queue_root, args.work_item_id) is None:
        print("REFUSED: no open work item {!r}".format(args.work_item_id), file=sys.stderr)
        return 1
    result = cww.respond_work_item(args.queue_root, args.work_item_id, args.actor,
                                   args.message, role=args.role, source="worker-bridge")
    if not result.get("ok"):
        print("REFUSED: {}".format(result.get("error")), file=sys.stderr)
        return 1
    _emit(result)
    return 0


def cmd_status(args):
    if not _require_queue(args.queue_root):
        return 1
    _emit(cww.worker_status(args.queue_root))
    return 0


def _add_item_args(sub, with_message):
    sub.add_argument("queue_root", help="Clearance queue root directory.")
    sub.add_argument("--work-item-id", required=True, metavar="ID",
                     help="Required. The work item id (from next).")
    sub.add_argument("--actor", required=True, metavar="ID",
                     help="Required. Who is acting (for example claude).")
    sub.add_argument("--role", default=DEFAULT_ROLE, metavar="ROLE",
                     help="Actor role (default: {}).".format(DEFAULT_ROLE))
    if with_message:
        sub.add_argument("--message", required=True, metavar="TEXT",
                         help="Required. The message text.")


def build_parser():
    parser = argparse.ArgumentParser(
        prog="clearwright_worker",
        description=(
            "ClearWright worker command bridge: find, claim, progress, and "
            "respond to work items over CLI or local HTTP, without a browser. "
            "This is the integration path for 'use CW' / 'review with CW'. It "
            "reuses the existing work-item and message functions; the web UI is "
            "the operator display and grants no authority.\n\n"
            "Exit codes:\n"
            "  0  ok (or nothing to do)\n"
            "  1  refused or invalid\n"
            "  2  argument error"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    subs = parser.add_subparsers(dest="command", required=True)

    p_next = subs.add_parser("next", help="Print the next actionable work item.")
    p_next.add_argument("queue_root", help="Clearance queue root directory.")
    p_next.add_argument("--actor", default=None, metavar="ID",
                        help="Optional actor context (worker id).")
    p_next.set_defaults(func=cmd_next)

    p_claim = subs.add_parser("claim", help="Claim a work item.")
    _add_item_args(p_claim, with_message=False)
    p_claim.set_defaults(func=cmd_claim)

    p_progress = subs.add_parser("progress", help="Post a progress note on a work item.")
    _add_item_args(p_progress, with_message=True)
    p_progress.set_defaults(func=cmd_progress)

    p_respond = subs.add_parser("respond", help="Post a final response on a work item.")
    _add_item_args(p_respond, with_message=True)
    p_respond.set_defaults(func=cmd_respond)

    p_status = subs.add_parser("status", help="Print a small worker status.")
    p_status.add_argument("queue_root", help="Clearance queue root directory.")
    p_status.set_defaults(func=cmd_status)

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
