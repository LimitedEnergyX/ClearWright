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
import clearwright_verdict as cwv
import clearwright_egress_guard as _egress

# SDEG (Decision 2A): Codex dispatch is stdin-only and flows ONLY through the
# egress guard. The marker lets the guard self-test confirm the guarded adapter
# is live before the control plane accepts council dispatch.
GUARDED = True
_egress.register_adapter("clearwright_codex_review")

STDIN_HANG_MARKER = "Reading additional input from stdin"
MIN_SUBSTANTIVE_BYTES = 40

STRUCTURED_PROMPT = (
    "Read-only review. Do NOT edit any files. Review the provided ClearWright "
    "context critically and honestly. Respond with ONLY a single JSON object "
    "(no prose, no code fences) with EXACTLY these keys: reviewer (the string "
    "\"codex\"), verdict (one of \"approve\", \"approve_with_changes\", "
    "\"revise\", \"block\"), confidence (a number 0.0-1.0), risk_level (one of "
    "\"low\", \"medium\", \"high\", \"critical\"), blocking_findings (array), "
    "required_changes (array), nonblocking_findings (array), disagreements "
    "(array), assumptions (array), questions (array), recommended_plan (array), "
    "and summary (a substantive string)."
)

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


# Bumped whenever the adapter's dispatch behavior changes; part of the council's
# dispatch fingerprint so cached results are never reused across adapter changes.
ADAPTER_VERSION = "codex-adapter/2"


def build_codex_cmd():
    """The Codex CLI invocation: read-only sandbox, skip the trusted-git-repo
    check so a review can run against any target directory, and read the prompt
    from STDIN (the explicit `-` argument). The prompt is deliberately NOT an
    argv argument: Windows CreateProcess caps the command line at 32,767 chars
    (~23 KB effective), which silently limited how much evidence a council
    could carry. stdin has no such ceiling. read-only keeps Codex unable to
    edit files, so skipping the trust check grants no write access."""
    return ["codex", "exec", "-s", "read-only", "--skip-git-repo-check", "-"]


def effective_timeout(packet_bytes, base=None, env_get=os.environ.get):
    """Codex timeout scaled to packet size, so evidence-sized packets are not
    punished by a flat default: base + per-100KB increment, capped. All three
    knobs are env-configurable."""
    def _int(name, default):
        try:
            return int(env_get(name) or default)
        except (TypeError, ValueError):
            return default
    base = base if base is not None else _int("CLEARWRIGHT_CODEX_TIMEOUT_BASE", 120)
    per = _int("CLEARWRIGHT_CODEX_TIMEOUT_PER_100KB", 60)
    cap = _int("CLEARWRIGHT_CODEX_TIMEOUT_CAP", 600)
    import math
    return min(cap, base + per * math.ceil((packet_bytes or 0) / 100_000))


def run_codex(prompt, timeout, cwd=None, egress_context=None):
    """Invoke the local Codex CLI read-only and non-interactively, passing the
    prompt via STDIN (with EOF, so the old "waiting on stdin" hang cannot
    occur), with a hard timeout. Returns (output, telemetry). Never raises on
    timeout; the telemetry records timed_out. (Not unit-tested; the
    classification/posting logic around it is.)

    Egress boundary (SDEG, Decision 2A): the EXACT stdin bytes are validated by
    the egress guard, which also owns the subprocess launch (stdin-only; cwd is
    a fresh temp dir, never a ClearWright tree). ``egress_context`` is REQUIRED;
    its absence is fail-closed (returns a content-free egress_blocked telemetry,
    never a dispatch)."""
    cmd = build_codex_cmd()
    start = time.monotonic()
    try:
        proc = _egress.codex_launch(cmd, prompt, timeout, context=egress_context,
                                    caller="clearwright_codex_review")
        elapsed = time.monotonic() - start
        output = (proc.stdout or "") + (("\n" + proc.stderr) if proc.stderr else "")
        return output, build_telemetry(output, proc.returncode, elapsed, timed_out=False)
    except _egress.EgressBlocked as exc:
        elapsed = time.monotonic() - start
        tel = build_telemetry("", None, elapsed, timed_out=False)
        tel["egress_blocked"] = exc.reason
        return "", tel
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
    # A stable, machine-parseable footer so the UI can show telemetry as fields
    # rather than burying it in prose.
    footer = ("Telemetry: exit={exit_code}, elapsed={elapsed_seconds}s, "
              "bytes={bytes}, lines={lines}, timed_out={timed_out}, "
              "classification=review.").format(**telemetry)
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


def codex_executable():
    """Absolute path to the codex executable, or None. Safe to log (a path)."""
    return shutil.which("codex")


def codex_version(runner=None, timeout=10):
    """Best-effort codex version string, or None. Runs `codex --version` with a
    short timeout; never raises. Injectable for tests. (Not part of the review
    path, so a failure here never affects a review.)"""
    if runner is not None:
        try:
            return runner()
        except Exception:  # noqa: BLE001
            return None
    if not codex_available():
        return None
    try:
        with open(os.devnull, "rb") as devnull:
            proc = subprocess.run(["codex", "--version"], stdin=devnull,
                                  capture_output=True, text=True, timeout=timeout,
                                  encoding="utf-8", errors="replace")
        out = (proc.stdout or proc.stderr or "").strip()
        return out.splitlines()[0] if out else None
    except Exception:  # noqa: BLE001
        return None


def _structured_telemetry(telemetry, council_id, round_no, phase, classification):
    """Extend the base run telemetry with council context and the executable
    path (a path is safe to record; no secrets are involved with Codex)."""
    tel = dict(telemetry)
    tel.update({
        "reviewer": "codex",
        "classification": classification,
        "executable": codex_executable(),
        "council_id": council_id,
        "round": round_no,
        "phase": phase,
    })
    return tel


def _structured_body(verdict, telemetry):
    footer = ("Codex structured review (codex-cli). Telemetry: reviewer=codex, "
              "exit={exit_code}, elapsed={elapsed_seconds}s, bytes={bytes}, "
              "lines={lines}, timed_out={timed_out}, classification={classification}, "
              "council={council_id}, round={round}, phase={phase}.").format(**telemetry)
    head = ("verdict={verdict}, confidence={confidence}, risk={risk_level}"
            ).format(**verdict)
    return footer + "\n" + head + "\n\n" + verdict["summary"]


def review_structured(root, *, thread_id=None, work_item_id=None, packet_id=None,
                      council_id=None, round=None, phase="plan", context_text="",
                      prompt=None, timeout=90, runner=run_codex,
                      available_fn=codex_available, cwd=None, actor="claude",
                      note_on_failure=True, egress_context=None):
    """Run a real Codex read-only review and return the SAME structured verdict
    shape as the GPT adapter. Posts a codex/reviewer message ONLY on a real,
    successful, substantive, and validated run. A hang, timeout, empty run, or
    malformed/invalid verdict posts no Codex participation. `runner` and
    `available_fn` are injectable so tests never call real Codex.

    Target is addressed by explicit thread_id/work_item_id/packet_id; if only
    work_item_id is given, the thread and packet are resolved from it."""
    if not thread_id and work_item_id:
        item = cww.find_work_item(root, work_item_id)
        if item is not None:
            thread_id = item.get("thread_id")
            packet_id = packet_id or item.get("packet_id")

    def note(message, classification, telemetry):
        if note_on_failure:
            _note(root, {"thread_id": thread_id, "work_item_id": work_item_id,
                         "packet_id": packet_id}, actor, "orchestrator",
                  "internal", "codex-review-helper", message)
        return {"ok": True, "posted": False, "reviewer": "codex",
                "classification": classification,
                "telemetry": _structured_telemetry(telemetry, council_id, round, phase, classification)}

    if not bool(available_fn()):
        return note("Codex CLI unavailable; no Codex participation claimed.",
                    "unavailable", build_telemetry("", None, 0.0))

    full_prompt = prompt or (STRUCTURED_PROMPT + "\n\n" + (context_text or ""))
    try:
        output, telemetry = runner(full_prompt, timeout, cwd,
                                   egress_context=egress_context)
    except TypeError:
        # A test-injected runner may not accept egress_context; the guard is
        # still enforced by the production run_codex path.
        output, telemetry = runner(full_prompt, timeout, cwd)
    if isinstance(telemetry, dict) and telemetry.get("egress_blocked"):
        return note("Codex dispatch blocked by the egress guard ({}); no Codex "
                    "participation claimed.".format(telemetry["egress_blocked"]),
                    "egress_blocked", telemetry)
    kind = classify(True, telemetry, output)
    if kind == "timeout":
        return note("Codex CLI timed out after {}s; no Codex participation claimed.".format(timeout),
                    "timeout", telemetry)
    if kind != "review":
        return note("Codex CLI produced no substantive output; no Codex participation claimed.",
                    "non_substantive", telemetry)

    try:
        raw = cwv.extract_json_object(output)
        # Reviewer identity is authoritative from this adapter (a real,
        # substantive codex-cli run), not from the model's self-label.
        if isinstance(raw, dict):
            raw["reviewer"] = "codex"
        verdict = cwv.validate_verdict(raw, reviewer="codex")
    except cwv.VerdictError as exc:
        err = "malformed_output" if "parse" in str(exc).lower() or "json" in str(exc).lower() else "invalid_verdict"
        result = note("Codex output failed structured validation ({}); no Codex participation claimed.".format(err),
                      err, telemetry)
        result["detail"] = str(exc)
        return result

    tel = _structured_telemetry(telemetry, council_id, round, phase, "review")
    msg = _note(root, {"thread_id": thread_id, "work_item_id": work_item_id,
                       "packet_id": packet_id}, "codex", "reviewer", "inbound",
                "codex-cli", _structured_body(verdict, tel))
    # Provenance: a validated, substantive codex-cli run.
    return {"ok": True, "posted": True, "reviewer": "codex", "verdict": verdict,
            "validated": True, "source": "codex-cli", "telemetry": tel,
            "message_id": msg["message_id"]}


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
    parser.add_argument("--repo", "--repo-root", dest="repo", default=None, metavar="PATH",
                        help="Absolute repo path; Codex runs with this as subprocess cwd "
                             "so no shell cd is needed (default: current directory).")
    parser.add_argument("--timeout", type=int, default=90, metavar="SECONDS",
                        help="Hard timeout for the Codex run (default: 90).")
    parser.add_argument("--prompt", default=None, metavar="TEXT",
                        help="Override the review prompt.")
    parser.add_argument("--prompt-file", default=None, metavar="PATH",
                        help="Read the review prompt from a file.")
    parser.add_argument("--structured", action="store_true",
                        help="Return the shared structured verdict shape for the "
                             "Review Council (reviewer=codex, verdict, confidence, "
                             "risk_level, findings, summary); post only on a "
                             "validated substantive run.")
    parser.add_argument("--thread-id", default=None, metavar="ID",
                        help="Structured mode: thread that receives the review "
                             "(else resolved from --work-item-id).")
    parser.add_argument("--packet-id", default=None, metavar="ID")
    parser.add_argument("--council-id", default=None, metavar="ID")
    parser.add_argument("--round", type=int, default=None, metavar="N")
    parser.add_argument("--phase", default="plan", choices=["plan", "incident", "verify"])
    parser.add_argument("--context-file", default=None, metavar="PATH",
                        help="Structured mode: review context appended to the "
                             "structured prompt.")
    parser.add_argument("--json", action="store_true",
                        help="Print compact JSON only.")
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

    if args.structured:
        context = ""
        if args.context_file:
            try:
                with open(args.context_file, encoding="utf-8") as fh:
                    context = fh.read()
            except OSError as exc:
                print("REFUSED: {}".format(exc), file=sys.stderr)
                return 1
        override_prompt = prompt if (args.prompt or args.prompt_file) else None
        result = review_structured(
            args.queue_root, thread_id=args.thread_id, work_item_id=args.work_item_id,
            packet_id=args.packet_id, council_id=args.council_id, round=args.round,
            phase=args.phase, context_text=context, prompt=override_prompt,
            timeout=args.timeout, cwd=args.repo or os.getcwd(), actor=args.actor)
        print(json.dumps(result) if args.json else json.dumps(result, indent=2))
        return 0 if result.get("ok") else 1

    result = review(args.queue_root, args.work_item_id, actor=args.actor,
                    timeout=args.timeout, prompt=prompt, cwd=args.repo or os.getcwd())
    print(json.dumps(result) if args.json else json.dumps(result, indent=2))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
