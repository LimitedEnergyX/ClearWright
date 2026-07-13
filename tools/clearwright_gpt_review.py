#!/usr/bin/env python3
"""
tools/clearwright_gpt_review.py: telemetry-backed GPT structured review via the
OpenAI Responses API, for the ClearWright Review Council.

This is the real GPT reviewer. It calls the OpenAI Responses API from the local
process, using OPENAI_API_KEY read ONLY from the environment, and posts a GPT
reviewer message into a ClearWright thread ONLY after a real, successful
response validates against the shared structured-verdict contract. GPT is never
faked: a missing key, an API error, an empty response, or a malformed/invalid
verdict posts NO gpt/reviewer message.

Secret handling (hard rules enforced here):
  - OPENAI_API_KEY is read only from the process environment (injectable getter
    for tests). It is never returned, printed, logged, persisted, or written
    into any CW record or telemetry field.
  - The Authorization header is built locally and never logged or returned.
  - Only safe telemetry is captured (requested/actual model, response id,
    elapsed, input/output character counts, API status, retry count, phase,
    council id, round, error class).

Network and robustness:
  - Standard-library HTTP only (urllib); no SDK dependency is added.
  - Bounded timeout; at most two retries on transient failures with bounded
    exponential backoff (both injectable so tests never sleep or hit the network).
  - The transport is injectable, so unit tests validate parsing/validation/
    posting without any real network call.

Exit codes: 0 completed (posted a real GPT review), 1 refused/invalid/no post,
2 argument error, 5 hard gate (missing key or model unavailable).
"""
import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request

import clearwright_message as cwm
import clearwright_verdict as cwv

OPENAI_RESPONSES_URL = "https://api.openai.com/v1/responses"
DEFAULT_GPT_MODEL = "gpt-5.6-terra"
CRITICAL_GPT_MODEL = "gpt-5.6-sol"
DEFAULT_TIMEOUT = 60
# A full structured verdict plus a reasoning model's internal tokens needs real
# headroom; too small a cap truncates the JSON and fails validation.
DEFAULT_MAX_OUTPUT_TOKENS = 3000
MAX_RETRIES = 2

# Error classes that are hard gates for the operator (never silently retried or
# worked around): a missing key, or the configured model being unavailable.
HARD_GATE_ERRORS = ("missing_openai_api_key", "model_unavailable")

INSTRUCTION = (
    "You are an independent code/plan reviewer for the ClearWright control "
    "plane. Review the provided context critically and honestly. Respond with "
    "ONLY a single JSON object, no prose and no code fences, with EXACTLY these "
    "keys: reviewer (must be the string \"gpt\"), verdict (one of "
    "\"approve\", \"approve_with_changes\", \"revise\", \"block\"), confidence "
    "(a number from 0.0 to 1.0), risk_level (one of \"low\", \"medium\", "
    "\"high\", \"critical\"), blocking_findings (array), required_changes "
    "(array), nonblocking_findings (array), disagreements (array), assumptions "
    "(array), questions (array), recommended_plan (array), and summary (a "
    "substantive string). Do not claim participation you did not perform; base "
    "the verdict only on the provided context."
)


def resolve_model(model=None, env_get=os.environ.get):
    """Model precedence: explicit --model, then CLEARWRIGHT_GPT_MODEL, then the
    documented default. Never silently switched after an API failure."""
    if model and str(model).strip():
        return str(model).strip()
    env_model = env_get("CLEARWRIGHT_GPT_MODEL")
    if env_model and str(env_model).strip():
        return str(env_model).strip()
    return DEFAULT_GPT_MODEL


def _real_transport(url, headers, body_bytes, timeout):
    """Default transport: one HTTP POST via urllib. Returns (status, text).
    Raises urllib.error.URLError / socket timeout on transport failure. The
    Authorization header passes through here but is never logged or stored."""
    req = urllib.request.Request(url, data=body_bytes, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.getcode(), resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        # An HTTP error still has a body (the API error JSON); read it.
        body = ""
        try:
            body = exc.read().decode("utf-8", "replace")
        except Exception:  # noqa: BLE001
            body = ""
        return exc.code, body


def extract_output_text(resp_json):
    """Pull the assistant text out of an OpenAI Responses API payload, tolerant
    of shape differences. Returns "" if none found."""
    if not isinstance(resp_json, dict):
        return ""
    direct = resp_json.get("output_text")
    if isinstance(direct, str) and direct.strip():
        return direct
    parts = []
    for item in resp_json.get("output") or []:
        if not isinstance(item, dict):
            continue
        for chunk in item.get("content") or []:
            if isinstance(chunk, dict) and isinstance(chunk.get("text"), str):
                parts.append(chunk["text"])
    return "".join(parts)


def _classify_api_error(status, resp_json):
    """Map an OpenAI error body to (error_class, short_detail). Model problems
    are a hard gate; auth/rate/other are api_error (auth should not happen since
    we verified the key, but never leak specifics)."""
    err = {}
    if isinstance(resp_json, dict):
        err = resp_json.get("error") or {}
    code = str(err.get("code") or "").lower()
    etype = str(err.get("type") or "").lower()
    if "model" in code or "model_not_found" in code or "model" in etype and "not" in etype:
        return "model_unavailable", "configured model was not accepted by the API"
    if status in (401, 403):
        return "api_error", "API rejected the request (auth/permission)"
    if status == 404 and "model" in json.dumps(err).lower():
        return "model_unavailable", "configured model was not found"
    return "api_error", "API returned status {}".format(status)


def _is_transient(status):
    return status in (429, 500, 502, 503, 504)


def call_gpt(context_text, model, *, key, timeout=DEFAULT_TIMEOUT,
             max_output_tokens=DEFAULT_MAX_OUTPUT_TOKENS,
             transport=_real_transport, sleep=time.sleep, max_retries=MAX_RETRIES):
    """Perform the GPT call with bounded retries. Returns a dict with keys:
    ok, text, actual_model, response_id, api_status, retry_count, error, detail,
    input_chars, output_chars, elapsed_seconds. Never includes the key/headers.
    """
    headers = {
        "Authorization": "Bearer " + key,
        "Content-Type": "application/json",
    }
    payload = {
        "model": model,
        "input": [
            {"role": "developer", "content": INSTRUCTION},
            {"role": "user", "content": context_text},
        ],
        "max_output_tokens": max_output_tokens,
    }
    body_bytes = json.dumps(payload).encode("utf-8")
    input_chars = len(context_text or "")

    start = time.monotonic()
    last = {"error": "transport_error", "detail": "no attempt made"}
    for attempt in range(max_retries + 1):
        try:
            status, text = transport(OPENAI_RESPONSES_URL, dict(headers), body_bytes, timeout)
        except Exception as exc:  # noqa: BLE001 - transport failure is transient
            last = {"error": "transport_error", "detail": type(exc).__name__}
            if attempt < max_retries:
                sleep(0.5 * (2 ** attempt))
                continue
            break
        try:
            resp_json = json.loads(text) if text else {}
        except ValueError:
            resp_json = {}
        if status == 200:
            out = extract_output_text(resp_json)
            return {
                "ok": True,
                "text": out,
                "actual_model": (resp_json.get("model") if isinstance(resp_json, dict) else None),
                "response_id": (resp_json.get("id") if isinstance(resp_json, dict) else None),
                "api_status": status,
                "retry_count": attempt,
                "input_chars": input_chars,
                "output_chars": len(out or ""),
                "elapsed_seconds": round(time.monotonic() - start, 3),
            }
        error_class, detail = _classify_api_error(status, resp_json)
        last = {"error": error_class, "detail": detail, "api_status": status}
        if _is_transient(status) and attempt < max_retries:
            sleep(0.5 * (2 ** attempt))
            continue
        break

    return {
        "ok": False,
        "error": last.get("error", "api_error"),
        "detail": last.get("detail", ""),
        "api_status": last.get("api_status"),
        "retry_count": attempt,
        "input_chars": input_chars,
        "output_chars": 0,
        "elapsed_seconds": round(time.monotonic() - start, 3),
    }


def _telemetry(requested_model, call_result, council_id, round_no, phase, error=None):
    """Safe telemetry only; never the key or headers."""
    tel = {
        "reviewer": "gpt",
        "requested_model": requested_model,
        "actual_model": call_result.get("actual_model"),
        "response_id": call_result.get("response_id"),
        "api_status": call_result.get("api_status"),
        "elapsed_seconds": call_result.get("elapsed_seconds"),
        "input_chars": call_result.get("input_chars"),
        "output_chars": call_result.get("output_chars"),
        "retry_count": call_result.get("retry_count"),
        "council_id": council_id,
        "round": round_no,
        "phase": phase,
    }
    if error:
        tel["error_class"] = error
    return tel


def _review_body(verdict, telemetry):
    footer = ("GPT structured review (openai-api). Telemetry: reviewer=gpt, "
              "model={actual_model}, response_id={response_id}, "
              "elapsed={elapsed_seconds}s, input_chars={input_chars}, "
              "output_chars={output_chars}, api_status={api_status}, "
              "retries={retry_count}, council={council_id}, round={round}, "
              "phase={phase}.").format(**telemetry)
    head = ("verdict={verdict}, confidence={confidence}, risk={risk_level}"
            ).format(**verdict)
    return footer + "\n" + head + "\n\n" + verdict["summary"]


def _post(root, actor, role, direction, source, message, thread_id,
          work_item_id, packet_id):
    msg = cwm.build_message(actor, message, role=role, packet_id=packet_id,
                            thread_id=thread_id, direction=direction,
                            status="posted", source=source,
                            work_item_id=work_item_id)
    cwm.write_message(root, msg)
    return msg


def review(root, context_text, *, thread_id, work_item_id=None, packet_id=None,
           council_id=None, round=None, phase="plan", model=None,
           timeout=DEFAULT_TIMEOUT, max_output_tokens=DEFAULT_MAX_OUTPUT_TOKENS,
           transport=_real_transport, key_getter=None, env_get=os.environ.get,
           sleep=time.sleep, max_retries=MAX_RETRIES, note_on_failure=True):
    """Run a real GPT structured review and post it into the thread, ONLY on a
    validated success. Returns a compact machine-readable result. `transport`,
    `key_getter`, `env_get`, and `sleep` are injectable so tests never hit the
    network, sleep, or touch the real environment."""
    key_getter = key_getter or (lambda: env_get("OPENAI_API_KEY"))
    requested_model = resolve_model(model, env_get=env_get)

    key = key_getter()
    if not key or not str(key).strip():
        # Hard gate: no key. Post NO gpt/reviewer message.
        tel = _telemetry(requested_model, {}, council_id, round, phase,
                         error="missing_openai_api_key")
        if note_on_failure:
            _post(root, "claude", "orchestrator", "internal", "gpt-review-helper",
                  "GPT unavailable: OPENAI_API_KEY not present in the environment; "
                  "no GPT participation claimed.", thread_id, work_item_id, packet_id)
        return {"ok": False, "posted": False, "reviewer": "gpt",
                "error": "missing_openai_api_key", "hard_gate": True, "telemetry": tel}

    call = call_gpt(context_text, requested_model, key=str(key).strip(),
                    timeout=timeout, max_output_tokens=max_output_tokens,
                    transport=transport, sleep=sleep, max_retries=max_retries)

    if not call.get("ok"):
        error = call.get("error", "api_error")
        tel = _telemetry(requested_model, call, council_id, round, phase, error=error)
        if note_on_failure:
            _post(root, "claude", "orchestrator", "internal", "gpt-review-helper",
                  "GPT review did not complete ({}); no GPT participation claimed.".format(error),
                  thread_id, work_item_id, packet_id)
        return {"ok": False, "posted": False, "reviewer": "gpt", "error": error,
                "detail": call.get("detail", ""),
                "hard_gate": error in HARD_GATE_ERRORS, "telemetry": tel}

    text = call.get("text") or ""
    if not text.strip():
        tel = _telemetry(requested_model, call, council_id, round, phase, error="empty_output")
        if note_on_failure:
            _post(root, "claude", "orchestrator", "internal", "gpt-review-helper",
                  "GPT returned an empty response; no GPT participation claimed.",
                  thread_id, work_item_id, packet_id)
        return {"ok": False, "posted": False, "reviewer": "gpt",
                "error": "empty_output", "telemetry": tel}

    try:
        raw = cwv.extract_json_object(text)
        # The reviewer identity is authoritative from THIS adapter (a real,
        # validated OpenAI call), not from whatever the model wrote in the
        # `reviewer` field. Models phrase self-identification inconsistently
        # ("GPT", "gpt-5.6-terra", "assistant"); coerce it so a good review is
        # not discarded on a self-label mismatch. The rest of the verdict still
        # validates the model's actual output.
        if isinstance(raw, dict):
            raw["reviewer"] = "gpt"
        verdict = cwv.validate_verdict(raw, reviewer="gpt")
    except cwv.VerdictError as exc:
        err = "malformed_output" if "parse" in str(exc).lower() or "json" in str(exc).lower() else "invalid_verdict"
        tel = _telemetry(requested_model, call, council_id, round, phase, error=err)
        if note_on_failure:
            _post(root, "claude", "orchestrator", "internal", "gpt-review-helper",
                  "GPT response failed validation ({}); no GPT participation claimed.".format(err),
                  thread_id, work_item_id, packet_id)
        return {"ok": False, "posted": False, "reviewer": "gpt", "error": err,
                "detail": str(exc), "telemetry": tel}

    tel = _telemetry(requested_model, call, council_id, round, phase)
    msg = _post(root, "gpt", "reviewer", "inbound", "openai-api",
                _review_body(verdict, tel), thread_id, work_item_id, packet_id)
    # Provenance: this result came from a validated openai-api response. The
    # council evaluator re-validates the verdict, but recording provenance makes
    # a durable reviewer record self-describing.
    return {"ok": True, "posted": True, "reviewer": "gpt", "verdict": verdict,
            "validated": True, "source": "openai-api", "telemetry": tel,
            "message_id": msg["message_id"]}


def build_parser():
    parser = argparse.ArgumentParser(
        prog="clearwright_gpt_review",
        description=("Run a real GPT structured review via the OpenAI Responses "
                     "API and post it into a ClearWright thread, only on a "
                     "validated success. OPENAI_API_KEY is read from the "
                     "environment and never printed or stored."),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("queue_root", help="Clearance queue root directory.")
    parser.add_argument("--thread-id", required=True, metavar="ID",
                        help="Required. Thread that receives the review.")
    parser.add_argument("--work-item-id", default=None, metavar="ID")
    parser.add_argument("--packet-id", default=None, metavar="ID")
    parser.add_argument("--council-id", default=None, metavar="ID")
    parser.add_argument("--round", type=int, default=None, metavar="N")
    parser.add_argument("--phase", default="plan", choices=["plan", "incident", "verify"])
    parser.add_argument("--model", default=None, metavar="NAME",
                        help="Override the model (else CLEARWRIGHT_GPT_MODEL, else {}).".format(DEFAULT_GPT_MODEL))
    parser.add_argument("--prompt", default=None, metavar="TEXT",
                        help="Review context text.")
    parser.add_argument("--prompt-file", default=None, metavar="PATH",
                        help="Read the review context from a file.")
    parser.add_argument("--timeout", type=int, default=DEFAULT_TIMEOUT, metavar="SECONDS")
    parser.add_argument("--max-output-tokens", type=int, default=DEFAULT_MAX_OUTPUT_TOKENS, metavar="N")
    parser.add_argument("--json", action="store_true", help="Print compact JSON only.")
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
    if not os.path.isdir(args.queue_root):
        print("REFUSED: queue root {!r} does not exist".format(args.queue_root), file=sys.stderr)
        return 2
    context = args.prompt or ""
    if args.prompt_file:
        try:
            with open(args.prompt_file, encoding="utf-8") as fh:
                context = fh.read()
        except OSError as exc:
            print("REFUSED: {}".format(exc), file=sys.stderr)
            return 2
    if not context.strip():
        print("REFUSED: provide --prompt or --prompt-file", file=sys.stderr)
        return 2

    result = review(args.queue_root, context, thread_id=args.thread_id,
                    work_item_id=args.work_item_id, packet_id=args.packet_id,
                    council_id=args.council_id, round=args.round, phase=args.phase,
                    model=args.model, timeout=args.timeout,
                    max_output_tokens=args.max_output_tokens)
    print(json.dumps(result) if args.json else json.dumps(result, indent=2))
    if result.get("ok"):
        return 0
    if result.get("hard_gate"):
        return 5
    return 1


if __name__ == "__main__":
    sys.exit(main())
