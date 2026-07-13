#!/usr/bin/env python3
"""
tools/clearwright_use_cw.py: the one stable "Use CW" entry point.

This is the thin orchestration wrapper the Use CW skill drives so that "Use CW to
do X" becomes an automatic loop with no manual copy/paste: create/select a
conversation, create and claim an actionable work item, run the Review Council,
post progress, consult an Incident Council on glitches, run a Verification
Council, and record completion, all over one command surface that emits compact
stable JSON and meaningful exit codes.

It DELEGATES to the existing ClearWright helpers and NEVER duplicates them:
  - conversations/messages -> clearwright_message
  - work items             -> clearwright_work
  - GPT + Codex council     -> clearwright_review_council (which calls the GPT and
                               Codex adapters; OPENAI_API_KEY is read only from
                               the environment by the GPT adapter and never
                               printed or stored here)

Authority: council agreement never grants authority; the operator's approved
scope does. This wrapper performs no destructive or outward-facing action and
enforces no action-time authorization itself; the calling skill/agent checks
each proposed action against the approved scope and stops for hard gates.

Commands: start, plan, council, progress, incident, verify, complete, status.

Exit codes (stable; the skill parses one JSON response and these codes):
  0  completed / agreement threshold met -> Claude may continue
  2  revision or another review round required
  3  operator required
  4  reviewer unavailable
  5  hard gate
  6  required authority not granted (e.g. a governed change without clearance)
  other nonzero  argument or runtime failure
"""
import argparse
import json
import os
import re
import sys

import clearwright_message as cwm
import clearwright_work as cww
import clearwright_review_council as cwrc

EXIT_OK = 0
EXIT_REVISION = 2
EXIT_OPERATOR = 3
EXIT_REVIEWER_UNAVAILABLE = 4
EXIT_HARD_GATE = 5
EXIT_AUTHORITY = 6
EXIT_USAGE = 7
EXIT_RUNTIME = 8

OUTCOME_EXIT = {
    "agreement_threshold_met": EXIT_OK,
    "needs_revision": EXIT_REVISION,
    "operator_required": EXIT_OPERATOR,
    "reviewer_unavailable": EXIT_REVIEWER_UNAVAILABLE,
    "hard_gate": EXIT_HARD_GATE,
}

# Coarse request classification. chat stays chat (PR #24: never a work item);
# everything else is actionable and creates a claimed work item. governed and
# high_risk additionally signal that a clearance packet / operator authority is
# expected before execution (the skill enforces that).
KINDS = ("chat", "analysis", "actionable", "governed", "high_risk")
_GOVERNED_HINTS = ("deploy", "publish", "release", "force-push", "force push",
                   "delete", "drop table", "migration", "schema", "credential",
                   "secret", "billing", "payment", "production")
_HIGH_RISK_HINTS = ("access control", "permission", "license", "dns", "webhook",
                    "token", "api key")
_CHAT_HINTS = ("just checking", "fyi", "thoughts", "what do you think",
               "how are you", "hello", "hi", "thanks")


def _phrase_present(text, phrases):
    """True if any phrase appears as a whole word/phrase (word-boundary match),
    so a hint never false-matches inside a larger word (e.g. 'fyi' inside
    'clarifying', or 'hi' inside 'this')."""
    for p in phrases:
        if re.search(r"\b" + re.escape(p) + r"\b", text):
            return True
    return False


def classify_request(text):
    """A coarse, deterministic classification. The skill may override with an
    explicit --kind; this is only a default. Hints match on word boundaries."""
    t = (text or "").lower()
    if _phrase_present(t, _HIGH_RISK_HINTS):
        return "high_risk"
    if _phrase_present(t, _GOVERNED_HINTS):
        return "governed"
    if _phrase_present(t, _CHAT_HINTS) and len(t.split()) < 25:
        return "chat"
    return "actionable"


def _load(inline, path):
    if inline:
        return inline
    if path:
        with open(path, encoding="utf-8") as fh:
            return fh.read()
    return ""


def _emit(result, code, as_json=True):
    text = json.dumps(result) if as_json else json.dumps(result, indent=2)
    print(text)
    return code


def _require_queue(root):
    return os.path.isdir(root)


# --------------------------------------------------------------------------- #
# Commands
# --------------------------------------------------------------------------- #

def cmd_start(args):
    """Create or continue a conversation and, for actionable work, create and
    claim a work item. Chat-only requests stay chat and create no work item."""
    text = _load(args.request, args.request_file).strip()
    if not text:
        return _emit({"ok": False, "error": "empty request"}, EXIT_USAGE, args.json)
    kind = args.kind or classify_request(text)
    intent = "chat" if kind == "chat" else "request"
    res = _do_message(args.queue_root, text, intent, args.thread_id, args.packet_id)
    if not res.get("ok"):
        return _emit({"ok": False, "error": res.get("error")}, EXIT_RUNTIME, args.json)
    thread_id = res["thread_id"]
    out = {"ok": True, "command": "start", "kind": kind, "thread_id": thread_id,
           "work_item_id": None, "claimed": False,
           "approved_scope": args.approved_scope,
           "requires_clearance": kind in ("governed", "high_risk")}
    if intent == "chat":
        out["note"] = "chat is not work; no work item created"
        return _emit(out, EXIT_OK, args.json)

    # Derive the work item for this new inbound request and claim it as claude.
    wid = "message:" + res["message"]["message_id"]
    claim = cww.claim_work_item(args.queue_root, wid, args.actor, role="orchestrator",
                                source="use-cw")
    out["work_item_id"] = wid
    out["claimed"] = bool(claim.get("ok"))
    if not claim.get("ok"):
        out["claim_error"] = claim.get("error")
    return _emit(out, EXIT_OK, args.json)


def _do_message(root, text, intent, thread_id, packet_id):
    """Post a real inbound operator message via the shared message builder."""
    try:
        msg = cwm.build_message("OPERATOR-0001", text, role="operator",
                                packet_id=packet_id, thread_id=thread_id,
                                direction="inbound", status="posted",
                                source="use-cw", intent=intent)
        cwm.write_message(root, msg)
    except (ValueError, OSError) as exc:
        return {"ok": False, "error": str(exc)}
    return {"ok": True, "message": msg, "thread_id": msg["thread_id"]}


def _council(args, phase):
    """Shared council driver for plan/council/incident/verify. Runs a review
    round or attaches a reconciliation, then returns the outcome + exit code."""
    root = args.queue_root
    stage = getattr(args, "stage", "review")

    if stage == "reconcile":
        council = cwrc.load_council(root, args.council_id) if args.council_id else None
        if not council:
            return _emit({"ok": False, "error": "reconcile requires an existing --council-id"},
                         EXIT_USAGE, args.json)
        council = cwrc.set_approved_scope(root, council, args.approved_scope)
        recon_text = _load(None, args.reconciliation_file)
        try:
            recon = cwrc.cwv.extract_json_object(recon_text)
            cwrc.attach_reconciliation(root, council, recon)
        except cwrc.cwv.VerdictError as exc:
            return _emit({"ok": False, "error": str(exc)}, EXIT_USAGE, args.json)
        outcome = cwrc.evaluate(cwrc.load_council(root, council["council_id"]),
                                cwrc.load_rounds(root, council["council_id"]))
        cwrc.save_outcome(root, council["council_id"], outcome)
        return _emit(_council_result(outcome), OUTCOME_EXIT.get(outcome["outcome"], EXIT_RUNTIME), args.json)

    # stage == review
    council = cwrc.load_council(root, args.council_id) if args.council_id else None
    if council is None:
        if not args.thread_id:
            return _emit({"ok": False, "error": "a new council requires --thread-id"},
                         EXIT_USAGE, args.json)
        council = cwrc.create_council(
            root, thread_id=args.thread_id, work_item_id=args.work_item_id,
            packet_id=args.packet_id, phase=phase, model=args.model,
            approved_scope=args.approved_scope)
    else:
        council = cwrc.set_approved_scope(root, council, args.approved_scope)

    if len(council.get("rounds", [])) >= int(council.get("max_rounds", cwrc.DEFAULT_MAX_ROUNDS)):
        outcome = cwrc.evaluate(council, cwrc.load_rounds(root, council["council_id"]))
        if outcome["outcome"] != "agreement_threshold_met":
            outcome["outcome"] = "operator_required"
            outcome["operator_required"] = True
            outcome["reason"] = "already at max rounds; no further rounds run"
        cwrc.save_outcome(root, council["council_id"], outcome)
        return _emit(_council_result(outcome), OUTCOME_EXIT.get(outcome["outcome"], EXIT_RUNTIME), args.json)

    context = _load(args.prompt, args.context_file or args.plan_file)
    if not context.strip():
        return _emit({"ok": False, "error": "provide --plan-file/--context-file or --prompt"},
                     EXIT_USAGE, args.json)
    cwrc.run_round(root, council, context, model=args.model, repo=args.repo, timeout=args.timeout)
    council = cwrc.load_council(root, council["council_id"])
    outcome = cwrc.evaluate(council, cwrc.load_rounds(root, council["council_id"]))
    cwrc.save_outcome(root, council["council_id"], outcome)
    return _emit(_council_result(outcome), OUTCOME_EXIT.get(outcome["outcome"], EXIT_RUNTIME), args.json)


def _council_result(outcome):
    """Compact, stable council result with the fields the skill parses."""
    return {
        "ok": outcome.get("outcome") == "agreement_threshold_met",
        "command": "council",
        "council_id": outcome.get("council_id"),
        "thread_id": outcome.get("thread_id"),
        "work_item_id": outcome.get("work_item_id"),
        "phase": outcome.get("phase"),
        "current_round": outcome.get("current_round"),
        "outcome": outcome.get("outcome"),
        "ready_to_proceed": outcome.get("ready_to_proceed", False),
        "operator_required": outcome.get("operator_required", False),
        "hard_gate": outcome.get("hard_gate", False),
        "gpt_status": outcome.get("gpt_status"),
        "codex_status": outcome.get("codex_status"),
        "approved_scope_sha256": outcome.get("approved_scope_sha256"),
        "unresolved_blockers": outcome.get("unresolved_blockers", []),
        "reason": outcome.get("reason"),
    }


def cmd_progress(args):
    msg = _load(args.message, args.message_file).strip()
    if not msg:
        return _emit({"ok": False, "error": "empty progress message"}, EXIT_USAGE, args.json)
    res = cww.progress_work_item(args.queue_root, args.work_item_id, "claude", msg,
                                 role="orchestrator", source="use-cw")
    if not res.get("ok"):
        code = EXIT_USAGE if res.get("error") == "work_item_not_found" else EXIT_RUNTIME
        return _emit({"ok": False, "error": res.get("error")}, code, args.json)
    return _emit({"ok": True, "command": "progress", "work_item_id": args.work_item_id,
                  "thread_id": res["thread_id"], "message_id": res["message"]["message_id"]},
                 EXIT_OK, args.json)


def cmd_complete(args):
    """Record completion: post the final response and mark the work item done.
    A governed work item whose clearance packet is not in clearance_done is a
    required-authority stop (exit 6)."""
    result_text = _load(args.result, args.result_file).strip()
    if not result_text:
        return _emit({"ok": False, "error": "empty result"}, EXIT_USAGE, args.json)
    if args.packet_id:
        path = cww._find_packet_path(args.queue_root, args.packet_id, lane="clearance_done")
        if path is None:
            return _emit({"ok": False, "command": "complete",
                          "error": "required_authority_not_granted",
                          "detail": "governed change packet {!r} is not in clearance_done".format(args.packet_id)},
                         EXIT_AUTHORITY, args.json)
    res = cww.respond_work_item(args.queue_root, args.work_item_id, "claude", result_text,
                                role="orchestrator", source="use-cw")
    if not res.get("ok"):
        code = EXIT_USAGE if res.get("error") == "work_item_not_found" else EXIT_RUNTIME
        return _emit({"ok": False, "error": res.get("error")}, code, args.json)
    return _emit({"ok": True, "command": "complete", "work_item_id": args.work_item_id,
                  "thread_id": res["thread_id"], "message_id": res["message"]["message_id"],
                  "status": "done"}, EXIT_OK, args.json)


def cmd_status(args):
    root = args.queue_root
    if args.council_id:
        full = cwrc.get_council(root, args.council_id)
        if not full:
            return _emit({"ok": False, "error": "council not found"}, EXIT_USAGE, args.json)
        outcome = full["outcome"] or cwrc.evaluate(full["council"], full["rounds"])
        return _emit({"ok": True, "command": "status", "council": full["summary"],
                      "outcome": outcome}, OUTCOME_EXIT.get(outcome.get("outcome"), EXIT_OK), args.json)
    status = cww.worker_status(root)
    councils = cwrc.list_councils(root, thread_id=args.thread_id)
    return _emit({"ok": True, "command": "status", "worker": status,
                  "review_councils": councils}, EXIT_OK, args.json)


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #

def _add_council_args(p):
    p.add_argument("--council-id", default=None, metavar="ID")
    p.add_argument("--thread-id", default=None, metavar="ID")
    p.add_argument("--work-item-id", default=None, metavar="ID")
    p.add_argument("--packet-id", default=None, metavar="ID")
    p.add_argument("--repo", default=None, metavar="PATH")
    p.add_argument("--plan-file", default=None, metavar="PATH")
    p.add_argument("--context-file", default=None, metavar="PATH")
    p.add_argument("--prompt", default=None, metavar="TEXT")
    p.add_argument("--reconciliation-file", default=None, metavar="PATH")
    p.add_argument("--stage", default="review", choices=["review", "reconcile"])
    p.add_argument("--model", default=None, metavar="NAME")
    p.add_argument("--approved-scope", default=None, metavar="TEXT")
    p.add_argument("--timeout", type=int, default=90, metavar="SECONDS")


def build_parser():
    parser = argparse.ArgumentParser(
        prog="clearwright_use_cw",
        description=("The 'Use CW' entry point: one command surface over the "
                     "ClearWright conversation, work-item, and Review Council "
                     "helpers, emitting compact JSON and stable exit codes. It "
                     "performs no destructive action and grants no authority."),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    subs = parser.add_subparsers(dest="command", required=True)

    p_start = subs.add_parser("start", help="Create/continue a conversation and claim a work item.")
    p_start.add_argument("queue_root")
    p_start.add_argument("--request", default=None, metavar="TEXT")
    p_start.add_argument("--request-file", default=None, metavar="PATH")
    p_start.add_argument("--kind", default=None, choices=list(KINDS))
    p_start.add_argument("--thread-id", default=None, metavar="ID")
    p_start.add_argument("--packet-id", default=None, metavar="ID")
    p_start.add_argument("--approved-scope", default=None, metavar="TEXT")
    p_start.add_argument("--actor", default="claude", metavar="ID")
    p_start.add_argument("--json", action="store_true")
    p_start.set_defaults(func=cmd_start)

    for name, phase, helptext in (
            ("plan", "plan", "Run a planning Review Council round (or reconcile)."),
            ("council", "plan", "Run a Review Council round for a phase (or reconcile)."),
            ("incident", "incident", "Run a focused Incident Council round (or reconcile)."),
            ("verify", "verify", "Run a final Verification Council round (or reconcile).")):
        p = subs.add_parser(name, help=helptext)
        p.add_argument("queue_root")
        p.add_argument("--phase", default=phase, choices=["plan", "incident", "verify"])
        _add_council_args(p)
        p.add_argument("--json", action="store_true")
        p.set_defaults(func=lambda a, _p=phase: _council(a, a.phase))

    p_prog = subs.add_parser("progress", help="Post a durable progress note.")
    p_prog.add_argument("queue_root")
    p_prog.add_argument("--work-item-id", required=True, metavar="ID")
    p_prog.add_argument("--message", default=None, metavar="TEXT")
    p_prog.add_argument("--message-file", default=None, metavar="PATH")
    p_prog.add_argument("--json", action="store_true")
    p_prog.set_defaults(func=cmd_progress)

    p_done = subs.add_parser("complete", help="Record completion (final response + done).")
    p_done.add_argument("queue_root")
    p_done.add_argument("--work-item-id", required=True, metavar="ID")
    p_done.add_argument("--packet-id", default=None, metavar="ID")
    p_done.add_argument("--result", default=None, metavar="TEXT")
    p_done.add_argument("--result-file", default=None, metavar="PATH")
    p_done.add_argument("--json", action="store_true")
    p_done.set_defaults(func=cmd_complete)

    p_status = subs.add_parser("status", help="Read-only status (no reviewers run).")
    p_status.add_argument("queue_root")
    p_status.add_argument("--council-id", default=None, metavar="ID")
    p_status.add_argument("--thread-id", default=None, metavar="ID")
    p_status.add_argument("--json", action="store_true")
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
    if not _require_queue(args.queue_root):
        print(json.dumps({"ok": False, "error": "queue root does not exist"}))
        return EXIT_USAGE
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
