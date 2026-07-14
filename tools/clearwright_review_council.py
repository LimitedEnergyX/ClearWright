#!/usr/bin/env python3
"""
tools/clearwright_review_council.py: the ClearWright Review Council engine.

ClearWright coordinates independent GPT and Codex reviews of Claude's plan,
records every round durably, and evaluates a DETERMINISTIC agreement rule over
structured fields (never prose similarity). This is the one stable, machine-
readable entry point the future "Use CW" skill drives: a phase command runs a
review round or attaches Claude's reconciliation, and returns compact JSON plus
a meaningful exit code so the caller can decide to proceed, revise, consult
again, or stop.

Design guarantees:
  - Agreement is a rule over structured verdicts and Claude's reconciliation,
    not a prose score. A revise/block verdict, an unresolved blocker, a missing
    real reviewer, or a hard gate can never be overridden by any number.
  - Round one is independent: GPT does not see Codex's output and vice versa.
  - Every reviewer result and reconciliation is stored under the durable queue
    root without touching the clearance packet schema or validator.
  - No credentials, authorization headers, or unrelated data are ever stored.
  - The web server only READS this state; GPT/Codex run here (CLI/helper),
    never inside an HTTP request handler.

Phases: plan | incident | verify (same engine, recorded phase). status is
read-only. Stages within a phase: review (run a round) | reconcile (attach
Claude's reconciliation and re-evaluate).

Exit codes:
  0  agreement_threshold_met (Claude may proceed inside approved scope)
  2  needs_revision (another round or a reconciliation is required)
  3  operator_required (max rounds without agreement, or explicit)
  4  reviewer_unavailable (a real GPT or Codex result is missing)
  5  hard_gate (missing key, model unavailable, or a possible secret in context)
  2xx/other nonzero  argument or runtime failure
"""
import argparse
import hashlib
import json
import math
import os
import re
import sys
import time

import clearwright_message as cwm
import clearwright_verdict as cwv
import clearwright_gpt_review as gpt_adapter
import clearwright_codex_review as codex_adapter

COUNCILS_DIR = "review_councils"
DEFAULT_MIN_ROUNDS = 2
DEFAULT_MAX_ROUNDS = 5
# Product boundary: 2 to 5 substantive rounds, enforced here in the engine so
# no caller, wrapper, skill, or direct invocation can create a sixth round.
MIN_ROUNDS_FLOOR = 2
MAX_ROUNDS_CEILING = 5
CONFIDENCE_FLOOR = 0.70

# Attempt budget: at most TWO total adapter calls per reviewer per substantive
# round (initial + one retry), persisted across command reinvocations. Changing
# the dispatch fingerprint (packet, timeout, model, config) does NOT grant more
# attempts — the fingerprint only gates result-cache REUSE. After exhaustion the
# outcome is reviewer_unavailable; continuing requires a new council or an
# explicit operator-authorized recovery grant recorded durably on the council.
MAX_ATTEMPTS_PER_ROUND = 2
ATTEMPT_BACKOFF_SECONDS = 2.0

# Reviewer capability declarations (part of the dispatch fingerprint; the
# artifact-aware packaging layer builds on these).
REVIEWER_CAPABILITIES = {
    "gpt": {"can_read_files": False, "transport": "https-inline"},
    "codex": {"can_read_files": True, "transport": "stdin-prompt"},
}

# Phase input budgets for the GPT (inline-text) reviewer, measured on the FINAL
# assembled packet in ESTIMATED input tokens. Estimates are labeled as such and
# never reported as actual usage.
PHASE_BUDGET_DEFAULTS = {"plan": 32000, "incident": 32000, "verify": 96000}


def clamp_rounds(min_rounds, max_rounds):
    """Validate the product boundary 2 <= min_rounds <= max_rounds <= 5.
    Raises ValueError on violation; there is no override."""
    mn, mx = int(min_rounds), int(max_rounds)
    if not (MIN_ROUNDS_FLOOR <= mn <= mx <= MAX_ROUNDS_CEILING):
        raise ValueError(
            "round bounds must satisfy {} <= min_rounds <= max_rounds <= {}".format(
                MIN_ROUNDS_FLOOR, MAX_ROUNDS_CEILING))
    return mn, mx


def phase_input_budget(phase, env_get=os.environ.get):
    name = "CLEARWRIGHT_GPT_{}_INPUT_BUDGET".format(str(phase or "plan").upper())
    default = PHASE_BUDGET_DEFAULTS.get(phase, PHASE_BUDGET_DEFAULTS["plan"])
    try:
        return int(env_get(name) or default)
    except (TypeError, ValueError):
        return default


def estimate_tokens(char_count, env_get=os.environ.get):
    """Conservative ESTIMATE of input tokens from characters (ceil(chars/3.0)
    by default; divisor configurable). Never a substitute for provider-reported
    actual usage."""
    try:
        divisor = float(env_get("CLEARWRIGHT_TOKEN_ESTIMATE_DIVISOR") or 3.0)
    except (TypeError, ValueError):
        divisor = 3.0
    if divisor <= 0:
        divisor = 3.0
    return int(math.ceil((char_count or 0) / divisor))


def log_invocation(root, record):
    """Append one metadata-only line to the invocation log. Records every
    dispatch attempt including aborted ones — the run that motivated this had
    eight invisible failures. Never logs prompts, artifact content, credentials,
    headers, or key values; logging failures never break the run."""
    rec = {"invocation_id": "inv-" + cwm._stamp(), "at": cwm._now_iso()}
    rec.update({k: v for k, v in record.items() if v is not None})
    try:
        path = os.path.join(root, "invocation_log.jsonl")
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec) + "\n")
    except OSError:
        pass
    return rec

# A real reviewer result must carry the matching provenance source, so a stored
# round cannot be hand-shaped to look like a validated reviewer run.
EXPECTED_SOURCE = {"gpt": "openai-api", "codex": "codex-cli"}

OUTCOMES = ("agreement_threshold_met", "needs_revision", "reviewer_unavailable",
            "operator_required", "hard_gate")
EXIT_CODES = {
    "agreement_threshold_met": 0,
    "needs_revision": 2,
    "operator_required": 3,
    "reviewer_unavailable": 4,
    "hard_gate": 5,
}

# Conservative signatures for a basic pre-send secret scan. This is a safety net
# to keep credentials out of a context packet, not a complete DLP system.
_SECRET_PATTERNS = [
    ("openai_key", re.compile(r"sk-[A-Za-z0-9_\-]{16,}")),
    ("aws_access_key", re.compile(r"AKIA[0-9A-Z]{12,}")),
    ("bearer_header", re.compile(r"[Aa]uthorization\s*:\s*Bearer\s+\S+")),
    ("private_key_block", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("slack_token", re.compile(r"xox[baprs]-[A-Za-z0-9\-]{8,}")),
    ("github_token", re.compile(r"gh[pousr]_[A-Za-z0-9]{20,}")),
    ("env_key_assignment", re.compile(r"OPENAI_API_KEY\s*=\s*\S+")),
]


def secret_scan(text):
    """Return a list of secret-pattern names that match in text (never the
    matched value). Used to refuse sending a context packet that may leak a
    credential."""
    hits = []
    s = text or ""
    for name, pattern in _SECRET_PATTERNS:
        if pattern.search(s):
            hits.append(name)
    return hits


# --------------------------------------------------------------------------- #
# Durable council storage (atomic, reload-safe, no secrets)
# --------------------------------------------------------------------------- #

def councils_root(root):
    return os.path.join(root, COUNCILS_DIR)


def council_dir(root, council_id):
    return os.path.join(councils_root(root), council_id)


def _atomic_write_json(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, indent=2)
        fh.write("\n")
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)
    return path


def _read_json(path, default=None):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return default


def new_council_id():
    return "cw-council-" + cwm._stamp()


def _scope_hash(scope):
    if not scope or not str(scope).strip():
        return None
    return hashlib.sha256(str(scope).encode("utf-8")).hexdigest()


def create_council(root, *, thread_id, work_item_id=None, packet_id=None,
                   phase="plan", min_rounds=DEFAULT_MIN_ROUNDS,
                   max_rounds=DEFAULT_MAX_ROUNDS, model=None, council_id=None,
                   approved_scope=None):
    min_rounds, max_rounds = clamp_rounds(min_rounds, max_rounds)
    council_id = council_id or new_council_id()
    council = {
        "council_id": council_id,
        "thread_id": thread_id,
        "work_item_id": work_item_id,
        "packet_id": packet_id,
        "phase": phase,
        "min_rounds": int(min_rounds),
        "max_rounds": int(max_rounds),
        "model": model,
        # The operator-approved scope for this council, recorded for audit. The
        # council engine takes no actions, so it does not enforce scope at action
        # time; the execution layer (the Use CW wrapper) is where scope is
        # checked before each action. Recording it here binds agreement to a
        # stated scope.
        "approved_scope": approved_scope,
        "approved_scope_sha256": _scope_hash(approved_scope),
        "created_at": cwm._now_iso(),
        "rounds": [],
        # Attempt budget and validated-result cache, persisted so reinvoking a
        # round can never reset the budget or re-spend a real review.
        "attempt_state": {},
        "pending_results": {},
    }
    _atomic_write_json(os.path.join(council_dir(root, council_id), "council.json"), council)
    return council


def load_council(root, council_id):
    return _read_json(os.path.join(council_dir(root, council_id), "council.json"))


def _round_path(root, council_id, round_no):
    return os.path.join(council_dir(root, council_id), "round-{:02d}.json".format(round_no))


def load_rounds(root, council_id):
    council = load_council(root, council_id)
    if not council:
        return []
    rounds = []
    for n in council.get("rounds", []):
        data = _read_json(_round_path(root, council_id, n))
        if data:
            rounds.append(data)
    return rounds


def save_round(root, council, round_data):
    council_id = council["council_id"]
    n = round_data["round"]
    _atomic_write_json(_round_path(root, council_id, n), round_data)
    if n not in council["rounds"]:
        council["rounds"].append(n)
        council["rounds"].sort()
        _atomic_write_json(os.path.join(council_dir(root, council_id), "council.json"), council)
    return round_data


def save_outcome(root, council_id, outcome):
    _atomic_write_json(os.path.join(council_dir(root, council_id), "outcome.json"), outcome)
    return outcome


# --------------------------------------------------------------------------- #
# Deterministic agreement rule
# --------------------------------------------------------------------------- #

def _reviewer_status(result, expected_reviewer=None):
    """Classify a stored reviewer result at the trust boundary. A result counts
    as a real "review" ONLY when it was a successful, posted run whose verdict
    re-validates against the shared contract AND whose reviewer identity matches
    the expected slot. This enforces the honesty invariant in the evaluator
    itself, not only in adapter internals: durable round data and injected
    reviewer functions live outside the adapters, so the policy gate re-checks
    rather than trusting the posted+verdict shape."""
    if not result:
        return "missing"
    # Require an explicit successful, posted run. A record that merely OMITS ok
    # (or sets it falsey) must not pass the trust boundary just because it is
    # shaped like a posted result.
    if result.get("ok") is not True:
        return result.get("error") or "unavailable"
    if result.get("posted") is not True:
        return result.get("error") or result.get("classification") or "unavailable"
    verdict = result.get("verdict")
    if not verdict:
        return "no_verdict"
    try:
        cwv.validate_verdict(verdict, reviewer=expected_reviewer)
    except cwv.VerdictError:
        return "invalid_verdict"
    # Provenance: the adapter marks a validated, real run with validated=true and
    # the reviewer's own source. A record lacking these (or with a mismatched
    # source) is not trusted as a real review even if it is shaped like one.
    if result.get("validated") is not True:
        return "unvalidated"
    if expected_reviewer is not None:
        want = EXPECTED_SOURCE.get(expected_reviewer)
        if want is not None and result.get("source") != want:
            return "source_mismatch"
    return "review"


def required_item_refs(gpt_verdict, codex_verdict):
    """Stable refs for every final-round required_change and blocking_finding,
    e.g. "gpt.required_changes[0]", so the reconciliation can bind a disposition
    to each specific item. Deterministic (index-based, no timestamps)."""
    refs = []
    for who, v in (("gpt", gpt_verdict or {}), ("codex", codex_verdict or {})):
        for field in ("required_changes", "blocking_findings"):
            for i in range(len(v.get(field) or [])):
                refs.append("{}.{}[{}]".format(who, field, i))
    return refs


def evaluate(council, rounds):
    """Evaluate the deterministic agreement rule over the recorded rounds.
    Returns an outcome dict. Never derives agreement from prose; a revise/block
    verdict, an unresolved blocker, a missing real reviewer, or a hard gate can
    never be overridden."""
    min_rounds = int(council.get("min_rounds", DEFAULT_MIN_ROUNDS))
    max_rounds = int(council.get("max_rounds", DEFAULT_MAX_ROUNDS))
    # Only committed substantive rounds count toward min/max; hard-gate and
    # secret-scan records are audit entries (they can still short-circuit below).
    completed = substantive_round_count(rounds)

    base = {
        "council_id": council.get("council_id"),
        "thread_id": council.get("thread_id"),
        "work_item_id": council.get("work_item_id"),
        "packet_id": council.get("packet_id"),
        "phase": council.get("phase"),
        "current_round": completed,
        "min_rounds": min_rounds,
        "max_rounds": max_rounds,
        "ready_to_proceed": False,
        "operator_required": False,
        "hard_gate": False,
        "gpt_status": None,
        "codex_status": None,
        "approved_scope_sha256": council.get("approved_scope_sha256"),
        "unresolved_blockers": [],
    }

    # Hard gate (missing key / model unavailable / secret) short-circuits even
    # when the gate record is a non-substantive audit round.
    if rounds:
        gate = rounds[-1]
        gate_gpt = gate.get("gpt")
        if (gate_gpt and gate_gpt.get("hard_gate")) or gate.get("hard_gate"):
            base.update({"outcome": "hard_gate", "hard_gate": True,
                         "reason": (gate_gpt or {}).get("error")
                         or gate.get("hard_gate_reason") or "hard gate"})
            return base

    substantive = [r for r in rounds if r.get("substantive", True)]
    if not substantive:
        base.update({"outcome": "needs_revision", "reason": "no review rounds yet"})
        return base

    latest = substantive[-1]
    gpt = latest.get("gpt")
    codex = latest.get("codex")
    recon = latest.get("reconciliation")
    base["gpt_status"] = _reviewer_status(gpt, "gpt")
    base["codex_status"] = _reviewer_status(codex, "codex")

    gpt_ok = base["gpt_status"] == "review" and bool(gpt and gpt.get("verdict"))
    codex_ok = base["codex_status"] == "review" and bool(codex and codex.get("verdict"))
    if not gpt_ok or not codex_ok:
        missing = []
        if not gpt_ok:
            missing.append("gpt:" + str(base["gpt_status"]))
        if not codex_ok:
            missing.append("codex:" + str(base["codex_status"]))
        base.update({"outcome": "reviewer_unavailable",
                     "reason": "a real reviewer result is missing (" + ", ".join(missing) + ")"})
        return base

    gv, cv = gpt["verdict"], codex["verdict"]
    blockers = []
    if gv["verdict"] not in cwv.NON_BLOCKING_VERDICTS:
        blockers.append("gpt verdict is " + gv["verdict"])
    if cv["verdict"] not in cwv.NON_BLOCKING_VERDICTS:
        blockers.append("codex verdict is " + cv["verdict"])
    if gv["confidence"] < CONFIDENCE_FLOOR:
        blockers.append("gpt confidence {:.2f} < {:.2f}".format(gv["confidence"], CONFIDENCE_FLOOR))
    if cv["confidence"] < CONFIDENCE_FLOOR:
        blockers.append("codex confidence {:.2f} < {:.2f}".format(cv["confidence"], CONFIDENCE_FLOOR))

    if recon is None:
        base["unresolved_blockers"] = blockers + ["no Claude reconciliation for the latest round"]
        if completed >= max_rounds:
            base.update({"outcome": "operator_required", "operator_required": True,
                         "reason": "reached max rounds without a final reconciliation"})
        else:
            base.update({"outcome": "needs_revision",
                         "reason": "latest round needs Claude reconciliation"})
        return base

    recon_blockers = [str(b) for b in (recon.get("unresolved_blockers") or [])]
    all_blockers = blockers + recon_blockers

    # Machine-checkable PER-ITEM resolution: every final-round required_change and
    # blocking_finding is given a stable ref, and the reconciliation must bind a
    # disposition to each specific ref (accepted / planned / rejected-with-
    # evidence). This is a true item-to-disposition map, not a disposition count,
    # so unrelated or duplicated dispositions cannot satisfy the rule.
    required_refs = required_item_refs(gv, cv)
    if required_refs:
        covered = {str(r.get("ref")) for r in (recon.get("resolutions") or [])}
        missing = [ref for ref in required_refs if ref not in covered]
        if missing:
            all_blockers = all_blockers + [
                "{} of {} final-round required/blocking item(s) unresolved in reconciliation "
                "resolutions: {}".format(len(missing), len(required_refs), ", ".join(missing[:6]))]

    # Executable agreement must be bound to a recorded operator-approved scope, so
    # PR #26 can verify it is enforcing the same scope that was reviewed. Without
    # it, reviewer agreement is review-only and not executable.
    if not (council.get("approved_scope") or "").strip():
        all_blockers = all_blockers + ["approved_scope not recorded (required before executable agreement)"]

    base["unresolved_blockers"] = all_blockers
    ready = bool(recon.get("ready_to_proceed"))

    agreement = (completed >= min_rounds and not all_blockers and ready)
    if agreement:
        base.update({"outcome": "agreement_threshold_met", "ready_to_proceed": True,
                     "reason": "deterministic agreement rule satisfied"})
        return base

    if completed >= max_rounds:
        base.update({"outcome": "operator_required", "operator_required": True,
                     "reason": "reached max rounds ({}) without agreement".format(max_rounds)})
        return base

    reason = "agreement not yet met"
    if all_blockers:
        reason = "unresolved blockers: " + "; ".join(all_blockers[:4])
    elif not ready:
        reason = "reconciliation ready_to_proceed is false"
    elif completed < min_rounds:
        reason = "minimum {} rounds not yet completed".format(min_rounds)
    base.update({"outcome": "needs_revision", "reason": reason})
    return base


# --------------------------------------------------------------------------- #
# Running a round
# --------------------------------------------------------------------------- #

def _default_gpt(root, context_text, *, thread_id, work_item_id, packet_id,
                 council_id, round_no, phase, model, timeout):
    # Give the reviewer generous output headroom: a full structured verdict plus
    # a reasoning model's internal tokens truncates under a small cap, which
    # would otherwise fail validation and read as reviewer_unavailable.
    return gpt_adapter.review(
        root, context_text, thread_id=thread_id, work_item_id=work_item_id,
        packet_id=packet_id, council_id=council_id, round=round_no, phase=phase,
        model=model, timeout=timeout, max_output_tokens=4000)


def _default_codex(root, context_text, *, thread_id, work_item_id, packet_id,
                   council_id, round_no, phase, repo, timeout):
    return codex_adapter.review_structured(
        root, thread_id=thread_id, work_item_id=work_item_id, packet_id=packet_id,
        council_id=council_id, round=round_no, phase=phase, context_text=context_text,
        timeout=timeout, cwd=repo)


def _augment_context(base_context, rounds):
    """For round >= 2, prepend prior reviewer findings and Claude's reconciliation
    so both reviewers re-review the revised plan and can state whether earlier
    blockers are resolved. Bounded so the packet stays small."""
    if not rounds:
        return base_context

    def excerpt(items, n=5, width=160):
        out = []
        for it in (items or [])[:n]:
            out.append("      - " + str(it)[:width])
        return "\n".join(out) if out else "      (none)"

    lines = ["=== Prior review rounds (for context; re-review the revised plan below) ==="]
    for rd in rounds:
        lines.append("Round {}:".format(rd.get("round")))
        for who in ("gpt", "codex"):
            r = rd.get(who)
            v = (r or {}).get("verdict")
            if v:
                lines.append("  {} verdict={} confidence={:.2f} risk={}".format(
                    who.upper(), v["verdict"], v["confidence"], v["risk_level"]))
                lines.append("    required_changes:")
                lines.append(excerpt(v.get("required_changes")))
                lines.append("    blocking_findings:")
                lines.append(excerpt(v.get("blocking_findings")))
            else:
                lines.append("  {}: no validated review ({})".format(
                    who.upper(), _reviewer_status(r)))
        rec = rd.get("reconciliation")
        if rec:
            lines.append("  Claude reconciliation:")
            lines.append("    revised_plan:")
            lines.append(excerpt(rec.get("revised_plan")))
            lines.append("    rejected_findings (with evidence):")
            lines.append(excerpt([f.get("finding") for f in rec.get("rejected_findings", [])]))
            lines.append("    unresolved_blockers:")
            lines.append(excerpt(rec.get("unresolved_blockers")))
    lines.append("=== Please state whether prior blockers are resolved, then review the revised plan below. ===")
    lines.append("")
    lines.append(base_context)
    return "\n".join(lines)


def substantive_round_count(rounds):
    """Committed substantive rounds only (secret-scan / hard-gate records are
    audit entries, not review rounds). Legacy round files predate the flag and
    default to substantive."""
    return len([r for r in rounds if r.get("substantive", True)])


def dispatch_fingerprint(reviewer, context_sha256, requested_model, phase,
                         adapter_version, artifact_hashes=()):
    """Identity of one exact dispatch. Gates result-cache REUSE only — it never
    grants attempts. Covers everything that could change what the reviewer saw:
    assembled context hash, artifact hashes, reviewer, requested model, adapter
    version/config, phase, and the reviewer's capability declaration."""
    blob = json.dumps({
        "context_sha256": context_sha256,
        "artifacts": sorted(artifact_hashes or ()),
        "reviewer": reviewer,
        "requested_model": requested_model,
        "adapter_version": adapter_version,
        "phase": phase,
        "capability": REVIEWER_CAPABILITIES.get(reviewer, {}),
    }, sort_keys=True)
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()


def _attempt_key(round_no, reviewer):
    return "r{}:{}".format(round_no, reviewer)


def _persist_council(root, council):
    _atomic_write_json(os.path.join(council_dir(root, council["council_id"]),
                                    "council.json"), council)


def _validated(result):
    return bool(result) and result.get("ok") is True and result.get("posted") is True \
        and result.get("validated") is True and isinstance(result.get("verdict"), dict)


def grant_attempts(root, council, reviewer, operator_message_id):
    """Operator-authorized recovery: reset the attempt budget for one reviewer
    on the CURRENT (next) round, anchored to a durable inbound operator-authored
    message. This explicit, recorded operator action is the ONLY in-council
    budget reset — fingerprint changes never grant attempts."""
    if reviewer not in REVIEWER_CAPABILITIES:
        return {"ok": False, "error": "unknown reviewer {!r}".format(reviewer)}
    match = [m for m in cwm.read_messages(root)
             if m.get("message_id") == operator_message_id]
    if not match:
        return {"ok": False, "error": "operator message {!r} not found".format(operator_message_id)}
    m = match[0]
    if m.get("direction") != "inbound" or (m.get("role") or "").lower() != "operator":
        return {"ok": False,
                "error": "referenced message is not an inbound operator authority record"}
    round_no = len(council.get("rounds", [])) + 1
    key = _attempt_key(round_no, reviewer)
    state = council.setdefault("attempt_state", {}).setdefault(
        key, {"calls": 0, "grants": []})
    state.setdefault("grants", []).append({
        "operator_message_id": operator_message_id, "at": cwm._now_iso(),
        "calls_before_grant": state.get("calls", 0)})
    state["calls"] = 0
    _persist_council(root, council)
    return {"ok": True, "round": round_no, "reviewer": reviewer,
            "operator_message_id": operator_message_id}


def run_round(root, council, base_context, *, model=None, repo=None, timeout=90,
              gpt_fn=None, codex_fn=None, sleep=time.sleep):
    """Dispatch one review round under the persistent attempt budget and return
    a dispatch report (NOT a round record):

      {"committed": bool, "substantive": bool, "round": n, "statuses": {...},
       "attempts": {...}, "hard_gate": bool, "packet_undeliverable": bool,
       "reason": str}

    Rules enforced here:
      - at most MAX_ATTEMPTS_PER_ROUND total adapter calls per reviewer per
        substantive round, persisted across reinvocations; a changed dispatch
        fingerprint never resets the budget (it only gates cache reuse);
      - the engine is the sole retry owner (one adapter call per attempt, with
        a bounded backoff between attempts);
      - a validated result from an aborted round is cached and reused when the
        SAME dispatch (exact fingerprint) is re-run, so a real review is never
        re-spent;
      - a round is committed (and counts toward min/max) only when BOTH
        reviewers produced validated reviews;
      - a probable secret in the context, or a reviewer hard gate, records a
        non-substantive audit round and stops;
      - an assembled packet over the phase input budget fails fast BEFORE any
        attempt is spent."""
    gpt_fn = gpt_fn or _default_gpt
    codex_fn = codex_fn or _default_codex
    council_id = council["council_id"]
    phase = council.get("phase")
    prior_rounds = load_rounds(root, council_id)
    round_no = len(council.get("rounds", [])) + 1
    context = _augment_context(base_context, prior_rounds) \
        if substantive_round_count(prior_rounds) > 0 else base_context

    hits = secret_scan(context)
    if hits:
        round_data = {"round": round_no, "phase": phase, "at": cwm._now_iso(),
                      "substantive": False, "gpt": None, "codex": None,
                      "reconciliation": None, "hard_gate": True,
                      "hard_gate_reason": "possible secret(s) in context: " + ", ".join(hits)}
        save_round(root, council, round_data)
        return {"committed": True, "substantive": False, "round": round_no,
                "hard_gate": True, "statuses": {}, "attempts": {},
                "reason": round_data["hard_gate_reason"]}

    # Budget fail-fast on the FINAL assembled inline packet, before any attempt.
    packet_chars = len(gpt_adapter.INSTRUCTION) + len(context)
    est_tokens = estimate_tokens(packet_chars)
    budget = phase_input_budget(phase)
    if est_tokens > budget:
        return {"committed": False, "substantive": False, "round": round_no,
                "packet_undeliverable": True, "statuses": {}, "attempts": {},
                "estimated_input_tokens": est_tokens, "budget": budget,
                "reason": ("assembled packet (~{} estimated input tokens) exceeds the "
                           "{} phase budget of {}; no reviewer attempt was spent. "
                           "Shrink the packet or raise CLEARWRIGHT_GPT_{}_INPUT_BUDGET."
                           ).format(est_tokens, phase, budget, str(phase or "plan").upper())}

    ctx_sha = hashlib.sha256(context.encode("utf-8")).hexdigest()
    packet_bytes = len(context.encode("utf-8"))
    requested_model = model or council.get("model")
    kw = dict(thread_id=council.get("thread_id"),
              work_item_id=council.get("work_item_id"),
              packet_id=council.get("packet_id"),
              council_id=council_id, round_no=round_no, phase=phase)

    codex_timeout = max(int(timeout or 0),
                        codex_adapter.effective_timeout(packet_bytes, base=timeout))
    plan = [
        ("gpt", gpt_fn, gpt_adapter.ADAPTER_VERSION, dict(model=requested_model, timeout=timeout)),
        ("codex", codex_fn, codex_adapter.ADAPTER_VERSION, dict(repo=repo, timeout=codex_timeout)),
    ]

    attempt_state = council.setdefault("attempt_state", {})
    pending = council.setdefault("pending_results", {})
    results, statuses, attempts_used, fingerprints = {}, {}, {}, {}

    for reviewer, fn, adapter_version, extra in plan:
        key = _attempt_key(round_no, reviewer)
        fp = dispatch_fingerprint(reviewer, ctx_sha, requested_model, phase, adapter_version)
        fingerprints[reviewer] = fp

        cached = pending.get(key)
        if cached and cached.get("fingerprint") == fp and _validated(cached.get("result")):
            results[reviewer] = cached["result"]
            statuses[reviewer] = "review"
            attempts_used[reviewer] = 0  # reused, no new call
            continue

        state = attempt_state.setdefault(key, {"calls": 0, "grants": []})
        result = None
        while state.get("calls", 0) < MAX_ATTEMPTS_PER_ROUND:
            if state.get("calls", 0) > 0:
                sleep(ATTEMPT_BACKOFF_SECONDS)
            state["calls"] = state.get("calls", 0) + 1
            state["last_fingerprint"] = fp
            _persist_council(root, council)
            t0 = time.monotonic()
            result = fn(root, context, **extra, **kw)
            tel = (result or {}).get("telemetry") or {}
            log_invocation(root, {
                "command": "council-dispatch", "phase": phase, "stage": "review",
                "council_id": council_id, "work_item_id": council.get("work_item_id"),
                "reviewer": reviewer, "round": round_no, "attempt": state["calls"],
                "duration_s": round(time.monotonic() - t0, 3),
                "packet_bytes": packet_bytes, "estimated_input_tokens": est_tokens,
                "actual_input_tokens": tel.get("actual_input_tokens"),
                "actual_output_tokens": tel.get("actual_output_tokens"),
                "error_class": (None if _validated(result) else
                                (result or {}).get("error") or (result or {}).get("classification")),
            })
            if (result or {}).get("hard_gate"):
                break
            if _validated(result):
                pending[key] = {"fingerprint": fp, "result": result, "at": cwm._now_iso()}
                _persist_council(root, council)
                break
        results[reviewer] = result
        attempts_used[reviewer] = attempt_state.get(key, {}).get("calls", 0)
        if result is None:
            statuses[reviewer] = "attempts_exhausted"
        elif result.get("hard_gate"):
            statuses[reviewer] = "hard_gate"
        elif _validated(result):
            statuses[reviewer] = "review"
        else:
            statuses[reviewer] = "attempts_exhausted"

    if any((r or {}).get("hard_gate") for r in results.values() if r):
        gated = [rev for rev, r in results.items() if (r or {}).get("hard_gate")]
        round_data = {"round": round_no, "phase": phase, "at": cwm._now_iso(),
                      "substantive": False, "context_sha256": ctx_sha,
                      "gpt": results.get("gpt"), "codex": results.get("codex"),
                      "reconciliation": None, "hard_gate": True,
                      "hard_gate_reason": "reviewer hard gate: " + ", ".join(
                          "{} ({})".format(rev, (results[rev] or {}).get("error"))
                          for rev in gated)}
        save_round(root, council, round_data)
        return {"committed": True, "substantive": False, "round": round_no,
                "hard_gate": True, "statuses": statuses, "attempts": attempts_used,
                "reason": round_data["hard_gate_reason"]}

    if all(statuses.get(rev) == "review" for rev, _f, _v, _e in plan):
        round_data = {"round": round_no, "phase": phase, "at": cwm._now_iso(),
                      "substantive": True, "context_sha256": ctx_sha,
                      "fingerprints": fingerprints, "attempts": attempts_used,
                      "gpt": results["gpt"], "codex": results["codex"],
                      "reconciliation": None}
        save_round(root, council, round_data)
        for rev, _f, _v, _e in plan:
            pending.pop(_attempt_key(round_no, rev), None)
            attempt_state.pop(_attempt_key(round_no, rev), None)
        _persist_council(root, council)
        return {"committed": True, "substantive": True, "round": round_no,
                "hard_gate": False, "statuses": statuses, "attempts": attempts_used,
                "reason": "round committed"}

    _persist_council(root, council)
    exhausted = [rev for rev, st in statuses.items() if st == "attempts_exhausted"]
    return {"committed": False, "substantive": False, "round": round_no,
            "hard_gate": False, "statuses": statuses, "attempts": attempts_used,
            "exhausted": exhausted,
            "reason": ("reviewer attempt budget exhausted for: {}; the round was not "
                       "counted. Continue with a new council or an explicit "
                       "operator-authorized recovery grant.").format(", ".join(exhausted))
            if exhausted else "reviewer unavailable; round not counted"}


def attach_reconciliation(root, council, reconciliation):
    """Validate and attach Claude's reconciliation to the latest round, and post
    it durably into the conversation. Raises VerdictError on invalid input."""
    rounds = [r for r in load_rounds(root, council["council_id"])
              if r.get("substantive", True)]
    if not rounds:
        raise cwv.VerdictError("no round to reconcile yet")
    normalized = cwv.validate_reconciliation(reconciliation)
    latest = rounds[-1]
    latest["reconciliation"] = normalized
    save_round(root, council, latest)

    # Post the reconciliation durably so dissent stays visible in the thread.
    summary = ("Claude reconciliation (round {}): ready_to_proceed={}. {}".format(
        latest["round"], normalized["ready_to_proceed"], normalized["summary"]))
    if normalized["rejected_findings"]:
        summary += " Rejected (with evidence): " + "; ".join(
            f["finding"] for f in normalized["rejected_findings"][:5])
    if normalized["unresolved_blockers"]:
        summary += " Unresolved blockers: " + "; ".join(
            str(b) for b in normalized["unresolved_blockers"][:5])
    msg = cwm.build_message(
        "claude", summary, role="orchestrator", packet_id=council.get("packet_id"),
        thread_id=council.get("thread_id"), direction="internal", status="posted",
        source="review-council", work_item_id=council.get("work_item_id"))
    cwm.write_message(root, msg)
    return latest


# --------------------------------------------------------------------------- #
# Read-only summaries for the API and status command
# --------------------------------------------------------------------------- #

def _summary(council, outcome):
    return {
        "council_id": council.get("council_id"),
        "thread_id": council.get("thread_id"),
        "work_item_id": council.get("work_item_id"),
        "packet_id": council.get("packet_id"),
        "phase": council.get("phase"),
        "min_rounds": council.get("min_rounds"),
        "max_rounds": council.get("max_rounds"),
        "current_round": len(council.get("rounds", [])),
        "created_at": council.get("created_at"),
        "outcome": (outcome or {}).get("outcome"),
        "ready_to_proceed": (outcome or {}).get("ready_to_proceed", False),
        "operator_required": (outcome or {}).get("operator_required", False),
        "hard_gate": (outcome or {}).get("hard_gate", False),
        "gpt_status": (outcome or {}).get("gpt_status"),
        "codex_status": (outcome or {}).get("codex_status"),
    }


def list_councils(root, thread_id=None):
    """Read-only: one summary per council, newest first. Used by the web API;
    never runs GPT or Codex."""
    base = councils_root(root)
    if not os.path.isdir(base):
        return []
    out = []
    for cid in os.listdir(base):
        council = load_council(root, cid)
        if not council:
            continue
        if thread_id and council.get("thread_id") != thread_id:
            continue
        outcome = _read_json(os.path.join(council_dir(root, cid), "outcome.json"))
        out.append(_summary(council, outcome))
    out.sort(key=lambda c: c.get("created_at") or "", reverse=True)
    return out


def get_council(root, council_id):
    """Read-only: full council state (council, rounds, outcome). API + status."""
    council = load_council(root, council_id)
    if not council:
        return None
    rounds = load_rounds(root, council_id)
    outcome = _read_json(os.path.join(council_dir(root, council_id), "outcome.json"))
    return {"council": council, "rounds": rounds, "outcome": outcome,
            "summary": _summary(council, outcome)}


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #

def _emit(result, as_json=True):
    print(json.dumps(result) if as_json else json.dumps(result, indent=2))
    return EXIT_CODES.get(result.get("outcome"), 1)


def _load_text(inline, path):
    if inline:
        return inline
    if path:
        with open(path, encoding="utf-8") as fh:
            return fh.read()
    return ""


def set_approved_scope(root, council, scope):
    """Record the operator-approved scope on an existing council if it is not
    already set. Returns the (possibly updated) council."""
    if scope and str(scope).strip() and not (council.get("approved_scope") or "").strip():
        council["approved_scope"] = str(scope)
        council["approved_scope_sha256"] = _scope_hash(scope)
        _atomic_write_json(os.path.join(council_dir(root, council["council_id"]), "council.json"), council)
    return council


def _phase_command(args):
    root = args.queue_root
    if not os.path.isdir(root):
        print("REFUSED: queue root {!r} does not exist".format(root), file=sys.stderr)
        return 2

    if args.stage == "reconcile":
        council = load_council(root, args.council_id) if args.council_id else None
        if not council:
            print("REFUSED: reconcile requires an existing --council-id", file=sys.stderr)
            return 2
        council = set_approved_scope(root, council, args.approved_scope)
        recon_text = _load_text(None, args.reconciliation_file)
        if not recon_text.strip():
            print("REFUSED: reconcile requires --reconciliation-file", file=sys.stderr)
            return 2
        try:
            recon = cwv.extract_json_object(recon_text)
            attach_reconciliation(root, council, recon)
        except cwv.VerdictError as exc:
            print("REFUSED: {}".format(exc), file=sys.stderr)
            return 2
        council = load_council(root, council["council_id"])
        outcome = evaluate(council, load_rounds(root, council["council_id"]))
        save_outcome(root, council["council_id"], outcome)
        return _emit(outcome, args.json)

    # stage == review
    council = load_council(root, args.council_id) if args.council_id else None
    if council is None:
        if not args.thread_id:
            print("REFUSED: a new council requires --thread-id", file=sys.stderr)
            return 2
        try:
            council = create_council(
                root, thread_id=args.thread_id, work_item_id=args.work_item_id,
                packet_id=args.packet_id, phase=args.phase,
                min_rounds=args.min_rounds, max_rounds=args.max_rounds, model=args.model,
                approved_scope=args.approved_scope)
        except ValueError as exc:
            print("REFUSED: {}".format(exc), file=sys.stderr)
            return 2
    else:
        council = set_approved_scope(root, council, args.approved_scope)

    if getattr(args, "grant_attempts", None):
        if not getattr(args, "operator_message_id", None):
            print("REFUSED: --grant-attempts requires --operator-message-id "
                  "(a durable inbound operator authority record)", file=sys.stderr)
            return 2
        reviewers = ["gpt", "codex"] if args.grant_attempts == "both" else [args.grant_attempts]
        for reviewer in reviewers:
            granted = grant_attempts(root, council, reviewer, args.operator_message_id)
            if not granted.get("ok"):
                print("REFUSED: {}".format(granted.get("error")), file=sys.stderr)
                return 2
        council = load_council(root, council["council_id"])

    rounds = load_rounds(root, council["council_id"])
    if substantive_round_count(rounds) >= int(council.get("max_rounds", DEFAULT_MAX_ROUNDS)):
        outcome = evaluate(council, rounds)
        if outcome["outcome"] != "agreement_threshold_met":
            outcome["outcome"] = "operator_required"
            outcome["operator_required"] = True
            outcome["reason"] = "already at max rounds; no further rounds run"
        save_outcome(root, council["council_id"], outcome)
        return _emit(outcome, args.json)

    context = _load_text(args.prompt, args.context_file or args.plan_file)
    if not context.strip():
        print("REFUSED: provide --context-file/--plan-file or --prompt", file=sys.stderr)
        return 2

    report = run_round(root, council, context, model=args.model, repo=args.repo,
                       timeout=args.timeout)
    council = load_council(root, council["council_id"])

    if report.get("packet_undeliverable"):
        payload = {"council_id": council["council_id"], "phase": council.get("phase"),
                   "outcome": "hard_gate", "hard_gate": True,
                   "error_class": "packet_undeliverable",
                   "estimated_input_tokens": report.get("estimated_input_tokens"),
                   "budget": report.get("budget"), "reason": report.get("reason")}
        return _emit(payload, args.json)
    if not report.get("committed"):
        payload = {"council_id": council["council_id"], "phase": council.get("phase"),
                   "outcome": "reviewer_unavailable",
                   "round_attempted": report.get("round"),
                   "statuses": report.get("statuses"), "attempts": report.get("attempts"),
                   "reason": report.get("reason")}
        save_outcome(root, council["council_id"], dict(payload, ready_to_proceed=False,
                                                       operator_required=False, hard_gate=False))
        return _emit(payload, args.json)

    outcome = evaluate(council, load_rounds(root, council["council_id"]))
    outcome["attempts"] = report.get("attempts")
    save_outcome(root, council["council_id"], outcome)
    return _emit(outcome, args.json)


def _status_command(args):
    root = args.queue_root
    if not os.path.isdir(root):
        print("REFUSED: queue root {!r} does not exist".format(root), file=sys.stderr)
        return 2
    if args.council_id:
        full = get_council(root, args.council_id)
        if not full:
            print("REFUSED: council {!r} not found".format(args.council_id), file=sys.stderr)
            return 2
        outcome = full["outcome"] or evaluate(full["council"], full["rounds"])
        payload = {"summary": full["summary"], "outcome": outcome,
                   "rounds": len(full["rounds"])}
        print(json.dumps(payload) if args.json else json.dumps(payload, indent=2))
        return EXIT_CODES.get(outcome.get("outcome"), 1)
    councils = list_councils(root, thread_id=args.thread_id)
    print(json.dumps({"councils": councils}) if args.json
          else json.dumps({"councils": councils}, indent=2))
    return 0


def build_parser():
    parser = argparse.ArgumentParser(
        prog="clearwright_review_council",
        description=("Run the ClearWright Review Council: independent GPT and "
                     "Codex structured reviews, Claude reconciliation, and a "
                     "deterministic agreement rule, recorded durably. This is "
                     "the stable entry point the Use CW skill drives."),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    subs = parser.add_subparsers(dest="command", required=True)

    def add_phase(name, help_text):
        p = subs.add_parser(name, help=help_text)
        p.add_argument("queue_root", help="Clearance queue root directory.")
        p.add_argument("--stage", default="review", choices=["review", "reconcile"],
                       help="review runs a round; reconcile attaches Claude's reconciliation.")
        p.add_argument("--council-id", default=None, metavar="ID",
                       help="Existing council; omit on a review stage to create one.")
        p.add_argument("--thread-id", default=None, metavar="ID")
        p.add_argument("--work-item-id", default=None, metavar="ID")
        p.add_argument("--packet-id", default=None, metavar="ID")
        p.add_argument("--repo", default=None, metavar="PATH",
                       help="Absolute repo path Codex reviews against.")
        p.add_argument("--plan-file", default=None, metavar="PATH",
                       help="Plan/context packet file.")
        p.add_argument("--context-file", default=None, metavar="PATH",
                       help="Alias for --plan-file.")
        p.add_argument("--prompt", default=None, metavar="TEXT")
        p.add_argument("--reconciliation-file", default=None, metavar="PATH",
                       help="reconcile stage: Claude's structured reconciliation.")
        p.add_argument("--model", default=None, metavar="NAME")
        p.add_argument("--approved-scope", default=None, metavar="TEXT",
                       help="Operator-approved scope, recorded on the council for audit.")
        p.add_argument("--min-rounds", type=int, default=DEFAULT_MIN_ROUNDS,
                       help="Substantive-round floor (bounds: 2 <= min <= max <= 5).")
        p.add_argument("--max-rounds", type=int, default=DEFAULT_MAX_ROUNDS,
                       help="Substantive-round ceiling (bounds: 2 <= min <= max <= 5).")
        p.add_argument("--grant-attempts", default=None, choices=["gpt", "codex", "both"],
                       help="Operator-authorized recovery: reset the attempt budget "
                            "for the current round. Requires --operator-message-id.")
        p.add_argument("--operator-message-id", default=None, metavar="ID",
                       help="Durable inbound operator message authorizing the grant.")
        p.add_argument("--timeout", type=int, default=90, metavar="SECONDS")
        p.add_argument("--json", action="store_true", help="Compact JSON output.")
        p.set_defaults(func=_phase_command, phase=name)
        return p

    add_phase("plan", "Run a planning review round or reconcile one.")
    add_phase("incident", "Run a focused incident review round or reconcile one.")
    add_phase("verify", "Run a final verification review round or reconcile one.")

    p_status = subs.add_parser("status", help="Read-only council status (no reviewers run).")
    p_status.add_argument("queue_root", help="Clearance queue root directory.")
    p_status.add_argument("--council-id", default=None, metavar="ID")
    p_status.add_argument("--thread-id", default=None, metavar="ID")
    p_status.add_argument("--json", action="store_true")
    p_status.set_defaults(func=_status_command)

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
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
