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


def _run_tests(repo):
    """Run the unittest suite as a subprocess with cwd=repo (no shell cd);
    return (passed, summary)."""
    proc = subprocess.run(
        [sys.executable, "-m", "unittest", "discover", "-s", "tests"],
        cwd=repo, capture_output=True, text=True, encoding="utf-8", errors="replace")
    tail = (proc.stderr or proc.stdout or "").strip().splitlines()
    summary = tail[-1] if tail else ""
    return proc.returncode == 0, summary


def _repo_clean(repo):
    """True if `git status --short` in repo is empty. Uses cwd=repo, not cd."""
    try:
        proc = subprocess.run(["git", "status", "--short"], cwd=repo,
                              capture_output=True, text=True, encoding="utf-8", errors="replace")
    except OSError:
        return None
    return not (proc.stdout or "").strip()


def preflight(server_url):
    """GET <server_url>/api/state; return (ok, info) so the proof can fail
    clearly before writing anything when the server is down."""
    import urllib.request
    try:
        with urllib.request.urlopen(server_url.rstrip("/") + "/api/state", timeout=5) as r:
            st = json.loads(r.read().decode())
        return True, {"alive": True, "mode": st.get("mode"), "durable": st.get("durable"),
                      "queue_root": st.get("queue_root"), "pulse": st.get("pulse")}
    except Exception as exc:  # noqa: BLE001 - report any connection failure clearly
        return False, {"alive": False, "error": str(exc)}


def run_proof(queue_root, message, actor="claude", role="orchestrator",
              relay_actor="OPERATOR-0001", source="claude-desktop-relay",
              run_tests=False, repo=None, use_codex=False, codex_timeout=90,
              final=None, server_url=None):
    """Execute the proof flow and return a result dict. Does not edit files.
    Uses absolute paths and subprocess cwd=repo, so no shell cd is needed."""
    if not os.path.isdir(queue_root):
        return {"ok": False, "error": "queue root does not exist", "queue_root": queue_root}
    repo = repo or os.getcwd()

    server = None
    if server_url:
        ok, server = preflight(server_url)
        if not ok:
            return {"ok": False, "error": "server preflight failed",
                    "server_url": server_url, "server": server}

    repo_clean_before = _repo_clean(repo)
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
        passed, summary = _run_tests(repo)
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
                                      timeout=codex_timeout, cwd=repo)
            steps.append({"step": "codex", "classification": codex_result.get("classification")})
        except Exception as exc:  # never let an optional step break the flow
            codex_result = {"ok": False, "error": str(exc)}
            steps.append({"step": "codex", "error": str(exc)})

    final_msg = final or "Proof flow complete. See the thread for progress, tests, and any Codex review."
    resp = cww.respond_work_item(queue_root, work_item_id, actor, final_msg,
                                 role=role, source="worker-bridge")
    steps.append({"step": "respond", "ok": resp.get("ok")})

    return {"ok": True, "thread_id": thread_id, "work_item_id": work_item_id,
            "steps": steps, "tests": tests_result, "codex": codex_result,
            "server": server, "repo": repo,
            "repo_clean_before": repo_clean_before, "repo_clean_after": _repo_clean(repo)}


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
    parser.add_argument("--repo", "--repo-root", dest="repo", default=None, metavar="PATH",
                        help="Absolute repo path for git/tests/Codex; used as subprocess "
                             "cwd so no shell cd is needed (default: current directory).")
    parser.add_argument("--server-url", default=None, metavar="URL",
                        help="If set, GET <URL>/api/state first and fail clearly if the "
                             "server is down before posting anything.")
    parser.add_argument("--codex", action="store_true",
                        help="Run a telemetry-backed Codex read-only review if Codex is available.")
    parser.add_argument("--codex-timeout", type=int, default=90, metavar="SECONDS",
                        help="Timeout for the Codex review (default: 90).")
    parser.add_argument("--final", default=None, metavar="TEXT",
                        help="Override the final response message.")
    parser.add_argument("--json", action="store_true",
                        help="Print compact JSON only (no readable summary line).")
    args = parser.parse_args()

    result = run_proof(
        args.queue_root, args.message, actor=args.actor, role=args.role,
        relay_actor=args.relay_actor, source=args.source, run_tests=args.run_tests,
        repo=args.repo, use_codex=args.codex, codex_timeout=args.codex_timeout,
        final=args.final, server_url=args.server_url)
    if args.json:
        print(json.dumps(result))
    else:
        print(json.dumps(result, indent=2))
        if result.get("ok"):
            print("SUMMARY: thread_id={} work_item_id={} tests={} repo_clean={}->{}".format(
                result.get("thread_id"), result.get("work_item_id"),
                (result.get("tests") or {}).get("summary", "not run"),
                result.get("repo_clean_before"), result.get("repo_clean_after")))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
