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

KIND_TIER = {"chat": 0, "analysis": 1, "actionable": 2, "governed": 3, "high_risk": 4}
_STRIP_EXCLUSION_MARKERS = re.compile(
    r"out of scope|not in scope|excluded|exclusions|do not|will not|prohibited", re.I)


def strip_exclusion_sections(text):
    """Lexical-fallback tripwire: drop lines under exclusion markers (until the
    next blank line) before scanning for risk words, so an operator's written
    guardrails ('Out of scope: ... deploy ... dns') are never read as risk."""
    kept, skipping = [], False
    for line in (text or "").splitlines():
        if _STRIP_EXCLUSION_MARKERS.search(line):
            skipping = True
            continue
        if skipping and not line.strip():
            skipping = False
            continue
        if not skipping:
            kept.append(line)
    return "\n".join(kept)


def _intended_risk_kind(intended_actions):
    """The highest governed/high_risk tier lexically present in the intended
    actions, or None. The default-actionable fallback never triggers this —
    only explicit risk hints escalate, so ordinary actions cannot conflict."""
    worst = None
    for action in intended_actions or []:
        k = classify_request(str(action))
        if k in ("governed", "high_risk"):
            if worst is None or KIND_TIER[k] > KIND_TIER[worst]:
                worst = k
    return worst


ENVELOPE_REQUIRED = ("task_kind", "approved_scope", "intended_actions",
                     "excluded_actions", "operator_authority_source")


def load_envelope(path):
    """Parse and validate a structured task envelope. Returns (envelope, error)."""
    try:
        with open(path, encoding="utf-8") as fh:
            env = json.load(fh)
    except (OSError, ValueError) as exc:
        return None, "envelope unreadable: {}".format(exc)
    if not isinstance(env, dict):
        return None, "envelope must be a JSON object"
    missing = [f for f in ENVELOPE_REQUIRED if not env.get(f)]
    if missing:
        return None, "envelope missing required field(s): " + ", ".join(missing)
    if env["task_kind"] not in KINDS:
        return None, "task_kind must be one of: " + ", ".join(KINDS)
    for f in ("intended_actions", "excluded_actions"):
        if not isinstance(env[f], list):
            return None, "{} must be an array".format(f)
    return env, None


def classify_envelope(env):
    """Classify from the structured envelope (primary path). excluded_actions
    NEVER raise risk — they are the operator's guardrails. A conflict exists
    only when an intended action lexically escalates above BOTH the declared
    task_kind and the approved scope."""
    declared = env["task_kind"]
    scope_kind = classify_request(str(env["approved_scope"]))
    risk = _intended_risk_kind(env.get("intended_actions"))
    conflict = bool(risk and KIND_TIER[risk] > KIND_TIER[declared]
                    and KIND_TIER[risk] > KIND_TIER[scope_kind])
    detail = None
    if conflict:
        detail = ("intended action(s) classify as {} but the declared kind is {} "
                  "and the approved scope reads as {}; operator must resolve"
                  ).format(risk, declared, scope_kind)
    return declared, conflict, detail


def _resolve_verification_required(env_or_none, kind):
    """verification_required is recorded at start. Declared value wins, except
    governed/high_risk clamp to True; lexical fallback defaults by kind."""
    default = kind in ("actionable", "governed", "high_risk")
    declared = None
    if env_or_none is not None and "verification_required" in env_or_none:
        declared = bool(env_or_none["verification_required"])
    value = default if declared is None else declared
    source = "default" if declared is None else "declared"
    if kind in ("governed", "high_risk") and not value:
        value, source = True, "clamped"
    return value, source


def _persist_envelope(root, message_id, env, audit):
    directory = os.path.join(root, "task_envelopes")
    os.makedirs(directory, exist_ok=True)
    record = dict(env)
    record["_audit"] = audit
    path = os.path.join(directory, message_id + ".json")
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(record, fh, indent=2)
        fh.write("\n")
    os.replace(tmp, path)
    return path


def cmd_start(args):
    """Create or continue a conversation and, for actionable work, create and
    claim a work item. Chat-only requests stay chat and create no work item.
    A structured envelope (--envelope-file) is the primary classification input;
    free-text --request/--request-file uses the lexical fallback. A conflict
    between intended actions and the approved scope is surfaced (exit 3), never
    silently inherited."""
    pf = _preflight_checks(args.queue_root, implicit=True)
    if pf["remediation"]:
        return _emit({"ok": False, "command": "start", "error": "preflight_failed",
                      "checks": pf["checks"], "remediation": pf["remediation"],
                      "note": "no work item, council, or artifact was created"},
                     EXIT_HARD_GATE, args.json)

    envelope = None
    if getattr(args, "envelope_file", None):
        envelope, err = load_envelope(args.envelope_file)
        if err:
            return _emit({"ok": False, "command": "start", "error": err}, EXIT_USAGE, args.json)
        text = str(envelope.get("request") or "").strip() or (
            "Task: " + "; ".join(str(a) for a in envelope["intended_actions"]))
        kind, conflict, conflict_detail = classify_envelope(envelope)
        method = "envelope"
        approved_scope = str(envelope["approved_scope"])
    else:
        text = _load(args.request, args.request_file).strip()
        if not text:
            return _emit({"ok": False, "error": "empty request (provide --envelope-file, "
                          "--request, or --request-file)"}, EXIT_USAGE, args.json)
        kind = args.kind or classify_request(strip_exclusion_sections(text))
        conflict, conflict_detail = False, None
        method = "explicit_kind" if args.kind else "lexical_fallback"
        approved_scope = args.approved_scope
    if args.kind:
        kind, method = args.kind, "explicit_kind"

    verification_required, vr_source = _resolve_verification_required(envelope, kind)
    intent = "chat" if kind == "chat" else "request"
    res = _do_message(args.queue_root, text, intent, args.thread_id, args.packet_id)
    if not res.get("ok"):
        return _emit({"ok": False, "error": res.get("error")}, EXIT_RUNTIME, args.json)
    thread_id = res["thread_id"]
    message_id = res["message"]["message_id"]

    envelope_sha = None
    if envelope is not None:
        blob = json.dumps(envelope, sort_keys=True).encode("utf-8")
        envelope_sha = __import__("hashlib").sha256(blob).hexdigest()
        _persist_envelope(args.queue_root, message_id, envelope, {
            "classification": kind, "classification_method": method,
            "classification_conflict": conflict, "conflict_detail": conflict_detail,
            "verification_required": verification_required,
            "verification_required_source": vr_source,
            "envelope_sha256": envelope_sha, "received_at": cwm._now_iso(),
            "thread_id": thread_id, "message_id": message_id})

    out = {"ok": not conflict, "command": "start", "kind": kind,
           "classification_method": method, "classification_conflict": conflict,
           "conflict_detail": conflict_detail, "thread_id": thread_id,
           "work_item_id": None, "claimed": False,
           "approved_scope": approved_scope, "envelope_sha256": envelope_sha,
           "verification_required": verification_required,
           "verification_required_source": vr_source,
           "requires_clearance": kind in ("governed", "high_risk"),
           "preflight": "ok"}
    if intent == "chat":
        out["note"] = "chat is not work; no work item created"
        return _emit(out, EXIT_OK if not conflict else EXIT_OPERATOR, args.json)

    # Derive the work item for this new inbound request and claim it as claude.
    wid = "message:" + message_id
    claim = cww.claim_work_item(args.queue_root, wid, args.actor, role="orchestrator",
                                source="use-cw")
    out["work_item_id"] = wid
    out["claimed"] = bool(claim.get("ok"))
    if not claim.get("ok"):
        out["claim_error"] = claim.get("error")
    return _emit(out, EXIT_OK if not conflict else EXIT_OPERATOR, args.json)


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

    try:
        cwrc.clamp_rounds(getattr(args, "min_rounds", cwrc.DEFAULT_MIN_ROUNDS),
                          getattr(args, "max_rounds", cwrc.DEFAULT_MAX_ROUNDS))
    except ValueError as exc:
        return _emit({"ok": False, "error": str(exc)}, EXIT_USAGE, args.json)

    if stage == "reconcile":
        council = cwrc.load_council(root, args.council_id) if args.council_id else None
        if not council:
            return _emit({"ok": False, "error": "reconcile requires an existing --council-id"},
                         EXIT_USAGE, args.json)
        recon_text = _load(None, args.reconciliation_file)
        if getattr(args, "dry_run", False):
            # Validate-only: schema plus exact-ref binding against the actual
            # latest round, at zero reviewer cost. Nothing is written or posted.
            try:
                recon = cwrc.cwv.extract_json_object(recon_text)
                normalized = cwrc.cwv.validate_reconciliation(recon)
            except cwrc.cwv.VerdictError as exc:
                return _emit({"ok": False, "command": "council", "dry_run": True,
                              "valid": False, "error": str(exc)}, EXIT_USAGE, args.json)
            rounds = [r for r in cwrc.load_rounds(root, council["council_id"])
                      if r.get("substantive", True)]
            unbound = []
            if rounds:
                gv = (rounds[-1].get("gpt") or {}).get("verdict") or {}
                cv = (rounds[-1].get("codex") or {}).get("verdict") or {}
                refs = cwrc.required_item_refs(gv, cv)
                covered = {str(r.get("ref")) for r in (normalized.get("resolutions") or [])}
                unbound = [r for r in refs if r not in covered]
            return _emit({"ok": not unbound, "command": "council", "dry_run": True,
                          "valid": True, "unbound_refs": unbound,
                          "note": "validation only; nothing submitted"},
                         EXIT_OK if not unbound else EXIT_USAGE, args.json)
        council = cwrc.set_approved_scope(root, council, args.approved_scope)
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
        try:
            council = cwrc.create_council(
                root, thread_id=args.thread_id, work_item_id=args.work_item_id,
                packet_id=args.packet_id, phase=phase, model=args.model,
                min_rounds=args.min_rounds, max_rounds=args.max_rounds,
                approved_scope=args.approved_scope)
        except ValueError as exc:
            return _emit({"ok": False, "error": str(exc)}, EXIT_USAGE, args.json)
    else:
        council = cwrc.set_approved_scope(root, council, args.approved_scope)

    if getattr(args, "grant_attempts", None):
        if not getattr(args, "operator_message_id", None):
            return _emit({"ok": False, "error": "--grant-attempts requires "
                          "--operator-message-id (a durable inbound operator "
                          "authority record)"}, EXIT_USAGE, args.json)
        reviewers = ["gpt", "codex"] if args.grant_attempts == "both" else [args.grant_attempts]
        for reviewer in reviewers:
            granted = cwrc.grant_attempts(root, council, reviewer, args.operator_message_id)
            if not granted.get("ok"):
                return _emit({"ok": False, "error": granted.get("error")}, EXIT_USAGE, args.json)
        council = cwrc.load_council(root, council["council_id"])

    rounds = cwrc.load_rounds(root, council["council_id"])
    if cwrc.substantive_round_count(rounds) >= int(council.get("max_rounds", cwrc.DEFAULT_MAX_ROUNDS)):
        outcome = cwrc.evaluate(council, rounds)
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

    report = cwrc.run_round(root, council, context, model=args.model, repo=args.repo,
                            timeout=args.timeout)
    council = cwrc.load_council(root, council["council_id"])

    if report.get("packet_undeliverable"):
        return _emit({"ok": False, "command": "council",
                      "council_id": council["council_id"], "phase": council.get("phase"),
                      "outcome": "hard_gate", "hard_gate": True,
                      "error_class": "packet_undeliverable",
                      "estimated_input_tokens": report.get("estimated_input_tokens"),
                      "budget": report.get("budget"), "reason": report.get("reason")},
                     EXIT_HARD_GATE, args.json)
    if not report.get("committed"):
        payload = {"ok": False, "command": "council",
                   "council_id": council["council_id"], "phase": council.get("phase"),
                   "outcome": "reviewer_unavailable",
                   "round_attempted": report.get("round"),
                   "statuses": report.get("statuses"), "attempts": report.get("attempts"),
                   "reason": report.get("reason")}
        cwrc.save_outcome(root, council["council_id"],
                          dict(payload, ready_to_proceed=False,
                               operator_required=False, hard_gate=False))
        return _emit(payload, EXIT_REVIEWER_UNAVAILABLE, args.json)

    outcome = cwrc.evaluate(council, cwrc.load_rounds(root, council["council_id"]))
    outcome["attempts"] = report.get("attempts")
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
        "attempts": outcome.get("attempts"),
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


def _preflight_checks(root, implicit=False, key_resolver=None, codex_which=None):
    """Readiness checks. Read-only except a queue-writability probe. The key is
    reported as a boolean + source, never a value. Returns {checks, remediation}."""
    import clearwright_gpt_review as gpt_adapter
    import clearwright_codex_review as codex_adapter
    key_resolver = key_resolver or gpt_adapter.resolve_api_key
    codex_which = codex_which or codex_adapter.codex_executable
    checks, remediation = {}, []

    key, source = key_resolver()
    checks["openai_api_key"] = {"present": bool(key), "source": source, "value_shown": False}
    if not key:
        remediation.append(
            "1. Set OPENAI_API_KEY in your Windows USER environment "
            "(Settings > System > About > Advanced system settings > Environment "
            "Variables > User variables > New). 2. Close and reopen the launching "
            "application so new processes inherit it. Never paste the key into "
            "chat, code, or a config file.")
    checks["gpt_model"] = gpt_adapter.resolve_model()

    exe = codex_which()
    version = codex_adapter.codex_version() if (exe and not implicit) else None
    checks["codex_cli"] = {"on_path": bool(exe), "version": version,
                           "prompt_transport": "stdin"}
    if not exe:
        remediation.append(
            "Install the Codex CLI and ensure `codex` is on PATH "
            "(verify with: codex --version).")

    writable = False
    exists = os.path.isdir(root)
    if exists:
        try:
            probe = os.path.join(root, ".cw_preflight_probe")
            with open(probe, "w", encoding="utf-8") as fh:
                fh.write("ok")
            os.remove(probe)
            writable = True
        except OSError:
            writable = False
    checks["queue_root"] = {"exists": exists, "writable": writable}
    if not exists or not writable:
        remediation.append("Queue root {!r} must exist and be writable.".format(root))

    if not implicit:
        checks["budgets"] = {
            "plan": cwrc.phase_input_budget("plan"),
            "incident": cwrc.phase_input_budget("incident"),
            "verify": cwrc.phase_input_budget("verify"),
            "estimate_divisor_default": 3.0,
            "note": "estimated input tokens on the final assembled packet; "
                    "estimates are never reported as actual usage",
        }
        checks["round_bounds"] = {"min": cwrc.MIN_ROUNDS_FLOOR, "max": cwrc.MAX_ROUNDS_CEILING}
        checks["attempt_budget"] = {"max_attempts_per_reviewer_per_round":
                                    cwrc.MAX_ATTEMPTS_PER_ROUND}
        import clearwright_codex_review as ccr_mod
        checks["codex_timeout_policy"] = {
            "base_s": ccr_mod.effective_timeout(0),
            "example_260kb_s": ccr_mod.effective_timeout(260_000),
        }
    return {"checks": checks, "remediation": remediation}


def cmd_preflight(args):
    """Readiness gate: exit 0 ready, exit 5 with exact remediation steps. On
    failure nothing is created (no work item, council, or artifact)."""
    pf = _preflight_checks(args.queue_root, implicit=False)
    ok = not pf["remediation"]
    return _emit({"ok": ok, "command": "preflight", "checks": pf["checks"],
                  "remediation": pf["remediation"]},
                 EXIT_OK if ok else EXIT_HARD_GATE, args.json)


SCHEMAS = {
    "envelope": {
        "description": "Structured task envelope for `start --envelope-file` "
                       "(primary classification input; excluded_actions are the "
                       "operator's guardrails and NEVER raise risk).",
        "required": list(ENVELOPE_REQUIRED),
        "optional": ["request", "targets", "verification_required", "envelope_version"],
        "rules": [
            "task_kind must be one of: " + ", ".join(KINDS),
            "intended_actions / excluded_actions must be arrays",
            "an intended action that lexically classifies governed/high_risk above "
            "both task_kind and the approved scope is a conflict -> exit 3",
            "verification_required defaults by kind (actionable/governed/high_risk "
            "-> true; chat/analysis -> false); governed/high_risk clamp to true",
        ],
        "example": {
            "envelope_version": 1, "task_kind": "analysis",
            "request": "Review the live page and produce a findings report.",
            "approved_scope": "Read-only review of the live page and pinned source.",
            "intended_actions": ["fetch page", "inspect source", "produce report"],
            "excluded_actions": ["edit files", "deploy", "publish", "change hosting"],
            "operator_authority_source": "operator message of 2026-07-14",
            "verification_required": True,
        },
    },
    "verdict": {
        "description": "Structured reviewer verdict (shared GPT/Codex contract).",
        "required": ["reviewer", "verdict", "confidence", "risk_level", "summary",
                     "blocking_findings", "required_changes", "nonblocking_findings",
                     "disagreements", "assumptions", "questions", "recommended_plan"],
        "rules": [
            "reviewer: gpt | codex (coerced authoritatively by the adapter)",
            "verdict: approve | approve_with_changes | revise | block",
            "confidence: number 0.0-1.0; risk_level: low | medium | high | critical",
            "all *_findings/changes/plan fields are arrays; summary is substantive",
        ],
    },
    "reconciliation": {
        "description": "Claude's reconciliation for `--stage reconcile` "
                       "(validate first with --dry-run at zero reviewer cost).",
        "required": ["accepted_findings", "rejected_findings", "required_plan_changes",
                     "revised_plan", "unresolved_blockers", "ready_to_proceed", "summary"],
        "optional": ["resolutions"],
        "rules": [
            "ready_to_proceed must be a boolean; summary must be substantive",
            "each rejected finding needs finding + reason + non-empty evidence ARRAY",
            "resolutions bind final-round items by EXACT ref, e.g. "
            "gpt.required_changes[0] (no annotations or composites)",
            "resolution disposition: accepted | planned | rejected "
            "(rejected requires non-empty evidence array)",
            "every final-round required_change and blocking_finding must be bound "
            "by ref before agreement is possible",
        ],
        "example": {
            "accepted_findings": ["finding text"],
            "rejected_findings": [{"finding": "…", "reason": "…", "evidence": ["…"]}],
            "required_plan_changes": [], "revised_plan": ["…"],
            "unresolved_blockers": [],
            "resolutions": [{"ref": "gpt.required_changes[0]",
                             "disposition": "accepted", "note": "…"}],
            "ready_to_proceed": False, "summary": "…",
        },
    },
}


def cmd_schema(args):
    """Print a schema, its validation rules, and a valid example — so the
    reconciliation contract is learnable without spending reviewer rounds."""
    schema = SCHEMAS.get(args.name)
    if not schema:
        return _emit({"ok": False, "error": "unknown schema {!r}; one of: {}".format(
            args.name, ", ".join(sorted(SCHEMAS)))}, EXIT_USAGE, args.json)
    return _emit({"ok": True, "command": "schema", "name": args.name,
                  "schema": schema}, EXIT_OK, args.json)


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
    p.add_argument("--dry-run", action="store_true",
                   help="reconcile stage: validate schema + exact-ref binding "
                        "only; nothing submitted, zero reviewer cost.")
    p.add_argument("--model", default=None, metavar="NAME")
    p.add_argument("--approved-scope", default=None, metavar="TEXT")
    p.add_argument("--min-rounds", type=int, default=cwrc.DEFAULT_MIN_ROUNDS,
                   help="Substantive-round floor (bounds: 2 <= min <= max <= 5).")
    p.add_argument("--max-rounds", type=int, default=cwrc.DEFAULT_MAX_ROUNDS,
                   help="Substantive-round ceiling (bounds: 2 <= min <= max <= 5).")
    p.add_argument("--grant-attempts", default=None, choices=["gpt", "codex", "both"],
                   help="Operator-authorized recovery: reset the attempt budget "
                        "for the current round. Requires --operator-message-id.")
    p.add_argument("--operator-message-id", default=None, metavar="ID",
                   help="Durable inbound operator message authorizing the grant.")
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
    p_start.add_argument("--envelope-file", default=None, metavar="PATH",
                         help="Structured task envelope (primary classification "
                              "input; see `schema envelope`).")
    p_start.add_argument("--request", default=None, metavar="TEXT")
    p_start.add_argument("--request-file", default=None, metavar="PATH")
    p_start.add_argument("--kind", default=None, choices=list(KINDS))
    p_start.add_argument("--thread-id", default=None, metavar="ID")
    p_start.add_argument("--packet-id", default=None, metavar="ID")
    p_start.add_argument("--approved-scope", default=None, metavar="TEXT")
    p_start.add_argument("--actor", default="claude", metavar="ID")
    p_start.add_argument("--json", action="store_true")
    p_start.set_defaults(func=cmd_start)

    p_pre = subs.add_parser("preflight", help="Readiness gate: exit 0 ready, "
                            "exit 5 with exact remediation (never prints the key).")
    p_pre.add_argument("queue_root")
    p_pre.add_argument("--json", action="store_true")
    p_pre.set_defaults(func=cmd_preflight)

    p_schema = subs.add_parser("schema", help="Print a schema + rules + example "
                               "(envelope | verdict | reconciliation).")
    p_schema.add_argument("name", choices=sorted(SCHEMAS))
    p_schema.add_argument("--json", action="store_true")
    p_schema.set_defaults(func=cmd_schema)
    # schema needs no queue root; give it a benign default for main()'s check.
    p_schema.set_defaults(queue_root=".")

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
    if args.command != "schema" and not _require_queue(args.queue_root):
        print(json.dumps({"ok": False, "error": "queue root does not exist"}))
        return EXIT_USAGE

    # Metadata-only invocation log: every command invocation leaves a line,
    # INCLUDING failed/aborted ones — the failures used to be invisible, which
    # is exactly what made their cost impossible to account for.
    import time as _time
    t0 = _time.monotonic()
    code = EXIT_RUNTIME
    try:
        code = args.func(args)
        return code
    finally:
        if args.command != "schema" and os.path.isdir(getattr(args, "queue_root", "")):
            error_class = None
            if code == EXIT_USAGE:
                error_class = "validation_error"
            elif code == EXIT_RUNTIME:
                error_class = "runtime_error"
            cwrc.log_invocation(args.queue_root, {
                "command": args.command,
                "phase": getattr(args, "phase", None),
                "stage": getattr(args, "stage", None),
                "council_id": getattr(args, "council_id", None),
                "work_item_id": getattr(args, "work_item_id", None),
                "duration_s": round(_time.monotonic() - t0, 3),
                "exit_code": code, "error_class": error_class,
            })


if __name__ == "__main__":
    sys.exit(main())
