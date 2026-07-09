#!/usr/bin/env python3
"""
tools/clearwright_codex_review.py: telemetry-backed Codex read-only review.

Run the local Codex CLI in read-only mode against the repo and post the result
into a ClearWright work-item thread, but only when Codex actually ran and
produced substantive output. This exists because the first "use CW" proof test
showed Codex can hang waiting on stdin: a hang or an empty run must never be
recorded as a real Codex review.

Safety and honesty:
  - Codex is invoked read-only (`codex exec -s read-only`) with stdin from the
    null device and a hard timeout, so it cannot edit files or block forever.
  - Telemetry (exit code, elapsed seconds, byte and line counts, timed-out flag)
    is captured and attached to whatever is posted.
  - Only a clean, substantive run is posted as actor=codex, role=reviewer,
    source=codex-cli. Otherwise a claude/orchestrator note records that Codex was
    unavailable, timed out, or produced no substantive output, and no Codex
    participation is claimed.
  - GPT / ChatGPT are never claimed; this only wraps the local Codex CLI.

The subprocess call lives in run_codex(); the classification and posting logic
are pure functions so they are unit-tested without ever invoking real Codex.

Exit codes: 0 completed (any classification is reported), 1 refused/invalid,
2 argument error
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
import time

import clearwright_message as cwm
import clearwright_work as cww

STDIN_HANG_MARKER = "Reading additional input from stdin"
MIN_SUBSTANTIVE_BYTES = 40

DEFAULT_PROMPT = (
    "Read-only review. Do NOT edit any files. Review the ClearWright worker "
    "command bridge and runbook (tools/clearwright_worker.py, "
    "tools/clearwright_work.py, docs/WORKER_RUNBOOK.md) for correctness, "
    "consistency, and clarity. Give a concise summary under 200 words with any "
    "risks and 1-3 concrete suggestions."
)


def codex_available():
    """True if a `codex` executable is on PATH."""
    return shutil.which("codex") is not None


def build_telemetry(output, exit_code, elapsed_seconds, timed_out=False):
    text = output or ""
    return {
        "exit_code": exit_code,
        "elapsed_seconds": round(float(elapsed_seconds), 3),
        "bytes": len(text.encode("utf-8")),
        "lines": len(text.splitlines()),
        "timed_out": bool(timed_out),
    }


def is_substantive(output, exit_code, min_bytes=MIN_SUBSTANTIVE_BYTES):
    """A run counts as a real review only if Codex exited cleanly and produced
    output beyond the stdin-hang marker / a trivial size."""
    if exit_code != 0:
        return False
    text = (output or "").strip()
    if not text:
        return False
    stripped = text.replace(STDIN_HANG_MARKER, "").strip()
    if not stripped:
        return False
    return len(stripped.encode("utf-8")) >= min_bytes


def classify(available, telemetry, output):
    """Return one of: unavailable, timeout, non_substantive, review."""
    if not available:
        return "unavailable"
    if telemetry.get("timed_out"):
        return "timeout"
    if is_substantive(output, telemetry.get("exit_code")):
        return "review"
    return "non_substantive"


def run_codex(prompt, timeout, cwd=None):
    """Invoke the local Codex CLI read-only, non-interactively, with stdin from
    the null device and a hard timeout. Returns (output, telemetry). Never
    raises on timeout; the telemetry records timed_out. (Not unit-tested; the
    classification/posting logic around it is.)"""
    cmd = ["codex", "exec", "-s", "read-only", prompt]
    start = time.monotonic()
    try:
        with open(os.devnull, "rb") as devnull:
            proc = subprocess.run(
                cmd, stdin=devnull, capture_output=True, text=True,
                timeout=timeout, cwd=cwd, encoding="utf-8", errors="replace")
        elapsed = time.monotonic() - start
        output = (proc.stdout or "") + (("\n" + proc.stderr) if proc.stderr else "")
        return output, build_telemetry(output, proc.returncode, elapsed, timed_out=False)
    except subprocess.TimeoutExpired as exc:
        elapsed = time.monotonic() - start
        partial = (exc.stdout or "")
        if isinstance(partial, bytes):
            partial = partial.decode("utf-8", "replace")
        return partial, build_telemetry(partial, None, elapsed, timed_out=True)


def _note(root, item, actor, role, direction, source, message, simulated=False):
    msg = cwm.build_message(
        actor, message, role=role, packet_id=item.get("packet_id"),
        thread_id=item.get("thread_id"), direction=direction, status="posted",
        source=source, simulated=simulated, work_item_id=item.get("work_item_id"))
    cwm.write_message(root, msg)
    return msg


def _review_body(output, telemetry, limit=1600):
    text = (output or "").strip()
    if len(text) > limit:
        text = text[-limit:]
    footer = ("Telemetry: exit={exit_code}, elapsed={elapsed_seconds}s, "
              "bytes={bytes}, lines={lines}.").format(**telemetry)
    return ("Codex CLI read-only review (codex-cli). " + footer + "\n\n" + text)


def review(root, work_item_id, actor="claude", timeout=90, prompt=DEFAULT_PROMPT,
           runner=run_codex, available_fn=codex_available, cwd=None):
    """Orchestrate a telemetry-backed Codex review and post the outcome into the
    work item's thread. `runner` and `available_fn` are injectable so tests never
    call real Codex. Returns a result dict with the classification and telemetry."""
    item = cww.find_work_item(root, work_item_id)
    if item is None:
        return {"ok": False, "error": "work_item_not_found", "work_item_id": work_item_id}

    available = bool(available_fn())
    if not available:
        telemetry = build_telemetry("", None, 0.0)
        msg = _note(root, item, actor, "orchestrator", "internal", "codex-review-helper",
                    "Codex CLI unavailable; no Codex participation claimed.")
        return {"ok": True, "classification": "unavailable", "codex_posted": False,
                "telemetry": telemetry, "message_id": msg["message_id"]}

    output, telemetry = runner(prompt, timeout, cwd)
    kind = classify(available, telemetry, output)
    if kind == "timeout":
        msg = _note(root, item, actor, "orchestrator", "internal", "codex-review-helper",
                    "Codex CLI timed out after {}s; no Codex participation claimed.".format(timeout))
        return {"ok": True, "classification": kind, "codex_posted": False,
                "telemetry": telemetry, "message_id": msg["message_id"]}
    if kind == "review":
        msg = _note(root, item, "codex", "reviewer", "inbound", "codex-cli",
                    _review_body(output, telemetry))
        return {"ok": True, "classification": kind, "codex_posted": True,
                "telemetry": telemetry, "message_id": msg["message_id"]}
    # non_substantive
    msg = _note(root, item, actor, "orchestrator", "internal", "codex-review-helper",
                "Codex CLI produced no substantive output (non-substantive); no Codex participation claimed.")
    return {"ok": True, "classification": kind, "codex_posted": False,
            "telemetry": telemetry, "message_id": msg["message_id"]}


def main():
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            try:
                reconfigure(encoding="utf-8", errors="backslashreplace")
            except (ValueError, OSError):
                pass
    parser = argparse.ArgumentParser(
        prog="clearwright_codex_review",
        description=(
            "Run the local Codex CLI read-only and post a telemetry-backed "
            "review into a ClearWright work-item thread. Only a clean, "
            "substantive run is recorded as Codex participation; a hang, "
            "timeout, or empty run is recorded as such and never claims Codex "
            "reviewed. GPT/ChatGPT are never claimed."),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("queue_root", help="Clearance queue root directory.")
    parser.add_argument("--work-item-id", required=True, metavar="ID",
                        help="Required. Work item whose thread receives the result.")
    parser.add_argument("--actor", default="claude", metavar="ID",
                        help="Actor for the unavailable/timeout note (default: claude).")
    parser.add_argument("--timeout", type=int, default=90, metavar="SECONDS",
                        help="Hard timeout for the Codex run (default: 90).")
    parser.add_argument("--prompt", default=None, metavar="TEXT",
                        help="Override the review prompt.")
    parser.add_argument("--prompt-file", default=None, metavar="PATH",
                        help="Read the review prompt from a file.")
    args = parser.parse_args()

    if not os.path.isdir(args.queue_root):
        print("REFUSED: queue root {!r} does not exist".format(args.queue_root), file=sys.stderr)
        return 1
    prompt = args.prompt or DEFAULT_PROMPT
    if args.prompt_file:
        try:
            with open(args.prompt_file, encoding="utf-8") as fh:
                prompt = fh.read()
        except OSError as exc:
            print("REFUSED: {}".format(exc), file=sys.stderr)
            return 1

    result = review(args.queue_root, args.work_item_id, actor=args.actor,
                    timeout=args.timeout, prompt=prompt, cwd=args.queue_root and os.getcwd())
    print(json.dumps(result, indent=2))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
