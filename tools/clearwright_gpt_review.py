#!/usr/bin/env python3
"""
tools/clearwright_gpt_review.py: telemetry-backed GPT structured review via the
OpenAI Responses API, for the ClearWright Review Council.

This is the real GPT reviewer. It builds the request payload and parses the
response, but it does NOT reach the provider itself: the egress guard
(clearwright_egress_guard) owns the provider URL, the auth header, credential
resolution, and the wire transport (SDEG Decision 2A). The adapter posts a GPT
reviewer message into a ClearWright thread ONLY after a real, successful
response validates against the shared structured-verdict contract. GPT is never
faked: a missing credential, an API error, an empty response, an egress block,
or a malformed/invalid verdict posts NO gpt/reviewer message.

Egress boundary (hard rules):
  - The credential is never resolved, seen, printed, logged, persisted, or
    written into any CW record or telemetry field by this adapter. It asks the
    guard for a bool (present or not) for the hard gate; the guard alone
    resolves and uses the value.
  - The exact serialized request bytes are validated by the guard before the
    send, and proven unmutated between validation and the wire.
  - Only safe telemetry is captured (requested/actual model, response id,
    elapsed, input/output character counts, API status, phase, council id,
    round, error class).
  - Bounded timeout; exactly ONE call per invocation. The Review Council engine
    is the sole retry owner, so total spend per reviewer per round is bounded
    by the council's attempt budget and never multiplied by adapter retries.

Exit codes: 0 completed (posted a real GPT review), 1 refused/invalid/no post,
2 argument error, 5 hard gate (missing key or model unavailable).
"""
import argparse
import json
import os
import sys
import time

import clearwright_message as cwm
import clearwright_verdict as cwv
import clearwright_egress_guard as _egress

# SDEG (Decision 2A): this adapter dispatches ONLY through the egress guard.
# The marker lets the guard's startup self-test confirm the guarded adapter is
# the live implementation before the control plane accepts council dispatch.
GUARDED = True
_egress.register_adapter("clearwright_gpt_review")

DEFAULT_GPT_MODEL = "gpt-5.6-terra"
CRITICAL_GPT_MODEL = "gpt-5.6-sol"
DEFAULT_TIMEOUT = 60
# A full structured verdict plus a reasoning model's internal tokens needs real
# headroom; too small a cap truncates the JSON and fails validation.
DEFAULT_MAX_OUTPUT_TOKENS = 3000
# Bumped whenever the adapter's dispatch behavior changes; part of the council's
# dispatch fingerprint so cached results are never reused across adapter changes.
ADAPTER_VERSION = "gpt-adapter/2"

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


# NOTE (SDEG Decision 2A): this adapter no longer contains a provider URL, an
# Authorization header, credential resolution, or a wire transport. The egress
# guard owns all four. The adapter builds the request payload and parses the
# response; the guard validates the exact bytes and sends them.


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


def _usage_tokens(resp_json):
    """Actual token usage from a Responses API payload, when present. Returns
    (input_tokens, output_tokens) with None where the provider did not report.
    Estimates are never substituted here; absent means absent."""
    usage = resp_json.get("usage") if isinstance(resp_json, dict) else None
    if not isinstance(usage, dict):
        return None, None
    def _int(v):
        return int(v) if isinstance(v, (int, float)) else None
    return _int(usage.get("input_tokens")), _int(usage.get("output_tokens"))


def provider_key_available(key_getter=None):
    """Adapter-side hard-gate check ONLY: returns a bool via the guard, without
    the adapter ever resolving or seeing the credential value (SDEG Decision
    2A). Credential resolution lives solely in the guard."""
    return _egress.provider_key_available(key_getter)


def call_gpt(context_text, model, *, key_getter=None, timeout=DEFAULT_TIMEOUT,
             max_output_tokens=DEFAULT_MAX_OUTPUT_TOKENS,
             transport=None, egress_context=None):
    """Perform exactly ONE GPT call. The Review Council engine is the sole
    retry owner (one adapter invocation = one API call), so worst-case API
    spend per reviewer per round is bounded by the council's attempt budget,
    never multiplied by hidden adapter retries. Returns a dict with keys:
    ok, text, actual_model, response_id, api_status, error, detail,
    input_chars, output_chars, actual token usage, elapsed_seconds.

    Egress boundary (SDEG, Decision 2A): the adapter builds ONLY the request
    payload. The guard validates the exact serialized bytes, resolves the
    credential, builds the Authorization header, knows the provider URL, and
    owns the transport. ``egress_context`` is REQUIRED; its absence is
    fail-closed. ``key_getter``/``transport`` are TEST-ONLY injections passed
    straight through to the guard."""
    # On the sensitive tier the guard owns the canonical request shape (a fixed
    # scaffold + the construction-proven derivative as the only user content),
    # and validates the outbound by byte-equality — so nothing can be wrapped
    # around the derivative. On the standard tier the adapter builds the normal
    # review payload; the guard validates the exact bytes with the tripwire.
    if egress_context is not None and getattr(egress_context, "tier", None) == "sensitive":
        body_bytes = _egress.build_sensitive_gpt_body(model, context_text,
                                                      max_output_tokens)
    else:
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
    try:
        # The guard validates body_bytes (the final serialized provider request),
        # resolves the key, builds the header, knows the URL, and sends. A
        # missing/invalid context or any guard refusal blocks the send.
        status, text = _egress.gpt_send(
            body_bytes, timeout, context=egress_context,
            key_getter=key_getter, transport=transport)
    except _egress.EgressBlocked as exc:
        # A missing credential is a hard gate, not a content block.
        err = "missing_openai_api_key" if exc.reason == "provider_key_missing" \
            else "egress_blocked"
        return {
            "ok": False, "error": err, "detail": exc.reason,
            "hard_gate": err in HARD_GATE_ERRORS,
            "api_status": None, "input_chars": input_chars, "output_chars": 0,
            "actual_input_tokens": None, "actual_output_tokens": None,
            "elapsed_seconds": round(time.monotonic() - start, 3),
        }
    except Exception as exc:  # noqa: BLE001 - a transport failure is one failed call
        return {
            "ok": False, "error": "transport_error", "detail": type(exc).__name__,
            "api_status": None, "input_chars": input_chars, "output_chars": 0,
            "actual_input_tokens": None, "actual_output_tokens": None,
            "elapsed_seconds": round(time.monotonic() - start, 3),
        }
    try:
        resp_json = json.loads(text) if text else {}
    except ValueError:
        resp_json = {}
    if status == 200:
        out = extract_output_text(resp_json)
        in_tok, out_tok = _usage_tokens(resp_json)
        return {
            "ok": True,
            "text": out,
            "actual_model": (resp_json.get("model") if isinstance(resp_json, dict) else None),
            "response_id": (resp_json.get("id") if isinstance(resp_json, dict) else None),
            "api_status": status,
            "input_chars": input_chars,
            "output_chars": len(out or ""),
            "actual_input_tokens": in_tok,
            "actual_output_tokens": out_tok,
            "elapsed_seconds": round(time.monotonic() - start, 3),
        }
    error_class, detail = _classify_api_error(status, resp_json)
    return {
        "ok": False, "error": error_class, "detail": detail, "api_status": status,
        "transient": _is_transient(status),
        "input_chars": input_chars, "output_chars": 0,
        "actual_input_tokens": None, "actual_output_tokens": None,
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
        "actual_input_tokens": call_result.get("actual_input_tokens"),
        "actual_output_tokens": call_result.get("actual_output_tokens"),
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
              "tokens_in={actual_input_tokens}, tokens_out={actual_output_tokens}, "
              "council={council_id}, round={round}, "
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
           transport=None, key_getter=None, env_get=os.environ.get,
           note_on_failure=True, egress_context=None):
    """Run ONE real GPT structured review and post it into the thread, ONLY on
    a validated success. Exactly one API call per invocation; the Review
    Council engine owns all retry policy. Returns a compact machine-readable
    result. `transport`, `key_getter`, and `env_get` are injectable so tests
    never hit the network or touch the real environment."""
    requested_model = resolve_model(model, env_get=env_get)

    # Hard gate: no credential. The adapter asks the guard for a BOOL only; it
    # never resolves or sees the key value (SDEG Decision 2A). ``key_getter`` is
    # a TEST-ONLY injection forwarded to the guard.
    if not provider_key_available(key_getter):
        tel = _telemetry(requested_model, {}, council_id, round, phase,
                         error="missing_openai_api_key")
        if note_on_failure:
            _post(root, "claude", "orchestrator", "internal", "gpt-review-helper",
                  "GPT unavailable: OPENAI_API_KEY not present in the environment; "
                  "no GPT participation claimed.", thread_id, work_item_id, packet_id)
        return {"ok": False, "posted": False, "reviewer": "gpt",
                "error": "missing_openai_api_key", "hard_gate": True, "telemetry": tel}

    call = call_gpt(context_text, requested_model, key_getter=key_getter,
                    timeout=timeout, max_output_tokens=max_output_tokens,
                    transport=transport, egress_context=egress_context)

    if not call.get("ok"):
        error = call.get("error", "api_error")
        tel = _telemetry(requested_model, call, council_id, round, phase, error=error)
        if note_on_failure:
            _post(root, "claude", "orchestrator", "internal", "gpt-review-helper",
                  "GPT review did not complete ({}); no GPT participation claimed.".format(error),
                  thread_id, work_item_id, packet_id)
        return {"ok": False, "posted": False, "reviewer": "gpt", "error": error,
                "detail": call.get("detail", ""), "transient": call.get("transient", False),
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
    # SDEG: scan the reviewer output before it is persisted; on a residual-
    # sensitive-data hit the body is replaced by a content-free notice (the
    # verdict fields the council needs are validated separately above).
    _safe_body, _ = _egress.redact_for_persistence(_review_body(verdict, tel))
    msg = _post(root, "gpt", "reviewer", "inbound", "openai-api",
                _safe_body, thread_id, work_item_id, packet_id)
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
