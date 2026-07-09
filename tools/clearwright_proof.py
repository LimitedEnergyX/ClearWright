#!/usr/bin/env python3
"""
tools/clearwright_proof.py: one-command "use CW" proof flow.

A single, prompt-friendly command that runs the common worker proof sequence
without chained shell syntax: relay an operator message into ClearWright, find
and claim the derived work item, post progress, optionally run the test suite,
optionally run a telemetry-backed Codex review, and post a final response. It
prints the thread_id and work_item_id so no follow-up shell chaining is needed.

It reuses the existing message / work-item / Codex-review functions and is
read-only with respect to the repository: it never edits files, creates a
branch, commits, or opens a PR. It only writes durable messages into the queue.

Exit codes: 0 ok, 1 refused/invalid, 2 argument error
"""
import argparse
import json
import os
import subprocess
import sys

import clearwright_message as cwm
import clearwright_work as cww


def _run_tests(repo_root):
    """Run the unittest suite as a subprocess; return (passed, summary)."""
    proc = subprocess.run(
        [sys.executable, "-m", "unittest", "discover", "-s", "tests"],
        cwd=repo_root, capture_output=True, text=True, encoding="utf-8", errors="replace")
    tail = (proc.stderr or proc.stdout or "").strip().splitlines()
    summary = tail[-1] if tail else ""
    return proc.returncode == 0, summary


def run_proof(queue_root, message, actor="claude", role="orchestrator",
              relay_actor="OPERATOR-0001", source="claude-desktop-relay",
              run_tests=False, repo_root=None, use_codex=False, codex_timeout=90,
              final=None):
    """Execute the proof flow and return a result dict. Does not edit files."""
    if not os.path.isdir(queue_root):
        return {"ok": False, "error": "queue root does not exist", "queue_root": queue_root}

    steps = []
    relay = cwm.build_message(relay_actor, message, role="operator", source=source,
                              direction="inbound", status="posted")
    cwm.write_message(queue_root, relay)
    work_item_id = "message:" + relay["message_id"]
    thread_id = relay["thread_id"]
    steps.append({"step": "relay", "message_id": relay["message_id"]})

    claim = cww.claim_work_item(queue_root, work_item_id, actor, role=role, source="worker-bridge")
    if not claim.get("ok"):
        return {"ok": False, "error": "claim failed", "detail": claim,
                "thread_id": thread_id, "work_item_id": work_item_id}
    steps.append({"step": "claim", "ok": True})

    prog = cww.progress_work_item(queue_root, work_item_id, actor,
                                  "Confirmed ClearWright; starting the proof flow.",
                                  role=role, source="worker-bridge")
    steps.append({"step": "progress", "ok": prog.get("ok")})

    tests_result = None
    if run_tests:
        passed, summary = _run_tests(repo_root or os.getcwd())
        tests_result = {"passed": passed, "summary": summary}
        cww.progress_work_item(queue_root, work_item_id, actor,
                               "Test suite: {} ({}).".format("passed" if passed else "FAILED", summary),
                               role=role, source="worker-bridge")
        steps.append({"step": "tests", "passed": passed})

    codex_result = None
    if use_codex:
        try:
            import clearwright_codex_review as ccr
            codex_result = ccr.review(queue_root, work_item_id, actor=actor,
                                      timeout=codex_timeout, cwd=repo_root or os.getcwd())
            steps.append({"step": "codex", "classification": codex_result.get("classification")})
        except Exception as exc:  # never let an optional step break the flow
            codex_result = {"ok": False, "error": str(exc)}
            steps.append({"step": "codex", "error": str(exc)})

    final_msg = final or "Proof flow complete. See the thread for progress, tests, and any Codex review."
    resp = cww.respond_work_item(queue_root, work_item_id, actor, final_msg,
                                 role=role, source="worker-bridge")
    steps.append({"step": "respond", "ok": resp.get("ok")})

    return {"ok": True, "thread_id": thread_id, "work_item_id": work_item_id,
            "steps": steps, "tests": tests_result, "codex": codex_result}


def main():
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            try:
                reconfigure(encoding="utf-8", errors="backslashreplace")
            except (ValueError, OSError):
                pass
    parser = argparse.ArgumentParser(
        prog="clearwright_proof",
        description=(
            "Run the 'use CW' proof flow in one command (relay -> claim -> "
            "progress -> optional tests -> optional Codex -> respond). Reuses "
            "existing worker functions; never edits files, branches, or opens a "
            "PR."),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("queue_root", help="Clearance queue root directory.")
    parser.add_argument("--message", required=True, metavar="TEXT",
                        help="Required. The operator request to relay into CW.")
    parser.add_argument("--actor", default="claude", metavar="ID",
                        help="Worker actor (default: claude).")
    parser.add_argument("--role", default="orchestrator", metavar="ROLE",
                        help="Worker role (default: orchestrator).")
    parser.add_argument("--relay-actor", default="OPERATOR-0001", metavar="ID",
                        help="Actor to record the relayed request under (default: OPERATOR-0001).")
    parser.add_argument("--source", default="claude-desktop-relay", metavar="NAME",
                        help="Source label for the relayed request (default: claude-desktop-relay).")
    parser.add_argument("--run-tests", action="store_true",
                        help="Run the unittest suite and post the result.")
    parser.add_argument("--repo-root", default=None, metavar="PATH",
                        help="Repo root for --run-tests / --codex (default: current directory).")
    parser.add_argument("--codex", action="store_true",
                        help="Run a telemetry-backed Codex read-only review if Codex is available.")
    parser.add_argument("--codex-timeout", type=int, default=90, metavar="SECONDS",
                        help="Timeout for the Codex review (default: 90).")
    parser.add_argument("--final", default=None, metavar="TEXT",
                        help="Override the final response message.")
    args = parser.parse_args()

    result = run_proof(
        args.queue_root, args.message, actor=args.actor, role=args.role,
        relay_actor=args.relay_actor, source=args.source, run_tests=args.run_tests,
        repo_root=args.repo_root, use_codex=args.codex, codex_timeout=args.codex_timeout,
        final=args.final)
    print(json.dumps(result, indent=2))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
