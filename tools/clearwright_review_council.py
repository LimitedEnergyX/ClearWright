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
import clearwright_writer_lock as cwl
import clearwright_egress_guard as guard

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
        import clearwright_archive as cwarch  # lazy: avoids a circular import
        cwarch.rotate_invocation_log_if_needed(root)
    except Exception:
        pass
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
    """Raises clearwright_writer_lock.MaintenanceInProgress while an archive
    operation holds exclusivity over this queue root. ``path`` is always under
    the queue root passed to the top-level council functions."""
    root = _queue_root_from_path(path)
    directory = os.path.dirname(path)
    os.makedirs(directory, exist_ok=True)
    tmp = path + ".tmp"
    with cwl.write_token(root, purpose="council"):
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(obj, fh, indent=2)
            fh.write("\n")
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    return path


def _queue_root_from_path(path):
    """review_councils/<id>/<file>.json under the queue root -> the queue
    root, so the write-token lock file lives in the same root as every other
    writer's lock, and archive draining sees every writer consistently."""
    marker = os.sep + COUNCILS_DIR + os.sep
    idx = path.find(marker)
    return path[:idx] if idx != -1 else os.path.dirname(os.path.dirname(path))


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


REVIEW_PROFILES = ("code", "editorial")

# Reviewer role lanes are PROMPT GUIDANCE ONLY: they steer where each reviewer
# spends attention, never filter or downgrade a recorded finding, and either
# reviewer may raise a critical issue outside its lane.
ROLE_LANES = (
    "Reviewer role lanes (guidance, not filters; either reviewer may raise a "
    "critical issue outside its lane):\n"
    "  - GPT: messaging, factual claims, clarity, positioning, audience, and "
    "semantic consistency.\n"
    "  - Codex: implementation consistency, source and citation accuracy, HTML "
    "and structural integrity, accessibility, security, and diff/artifact "
    "verification. Do not spend a round on routine taste feedback.\n")

# For round >= 2, reviewers judge resolution and regressions rather than
# restarting a full critique. New findings on UNCHANGED material are blocking
# only for the listed categories; otherwise they are advisory (backlog). This is
# prompt guidance; each reviewer still classifies its own findings and the
# deterministic agreement rule is unchanged.
SCOPED_ROUND_GUIDANCE = (
    "This is a follow-up round. Focus on: (a) whether prior findings are "
    "resolved, (b) whether the changed sections introduced regressions, and "
    "(c) newly discovered critical issues. A new finding on UNCHANGED material "
    "is blocking only for safety, factual accuracy, security, legal risk, or a "
    "material contradiction; otherwise record it as advisory (backlog).\n")

EDITORIAL_DEFAULT_MAX_ROUNDS = 3

# INTERNAL_TECHNICAL_STANDARD council scaffolds. These are FIXED, versioned
# packet framings, registered with the guard AT IMPORT so an ITS packet's
# scaffold node resolves to a hash-pinned fixed_scaffold (never free-form prose
# appended at assembly time). The follow-up scaffold FOLDS the exact Phase-1
# scoped-round guidance and prior-rounds framing sentences into its fixed text,
# so a continuation packet carries the same reviewer-facing continuity without
# any wrapper text being appended by the engine.
ITS_SCAFFOLD_ROUND1 = "cw-its-round1/1.0.0"
ITS_SCAFFOLD_FOLLOWUP = "cw-its-followup/1.0.0"

_ITS_ROUND1_TEXT = (
    "ClearWright internal technical self-review packet. The sections below are "
    "length-delimited framed components: any prior-round summaries first, then "
    "the review context. Frame header lines are packet plumbing; review the "
    "component contents. Re-review critically and honestly.")

_ITS_FOLLOWUP_TEXT = (
    _ITS_ROUND1_TEXT + "\n" + SCOPED_ROUND_GUIDANCE
    + "=== Prior review rounds (for context; re-review the revised plan below) ===\n"
    + "=== Please state whether prior blockers are resolved, then review the "
    "revised plan below. ===")

guard.register_its_scaffold(ITS_SCAFFOLD_ROUND1, _ITS_ROUND1_TEXT)
guard.register_its_scaffold(ITS_SCAFFOLD_FOLLOWUP, _ITS_FOLLOWUP_TEXT)


def _guidance_header(review_profile, round_no):
    """PROMPT-ONLY guidance prepended to the reviewer context. Role lanes apply
    to editorial work (where messaging-vs-implementation division of labor
    helps); scoped follow-up guidance applies from round 2 for any profile.
    Returns '' for a round-1 code review so existing behavior is unchanged where
    no profile guidance is needed."""
    parts = []
    if review_profile == "editorial":
        parts.append(ROLE_LANES)
    if round_no >= 2:
        parts.append(SCOPED_ROUND_GUIDANCE)
    if not parts:
        return ""
    return "=== Review guidance ===\n" + "\n".join(parts) + \
           "=== End review guidance ===\n\n"


def create_council(root, *, thread_id, work_item_id=None, packet_id=None,
                   phase="plan", min_rounds=DEFAULT_MIN_ROUNDS,
                   max_rounds=DEFAULT_MAX_ROUNDS, model=None, council_id=None,
                   approved_scope=None, review_profile="code",
                   data_sensitivity=None, lineage=None, lineage_candidate=None,
                   source_bindings=None):
    min_rounds, max_rounds = clamp_rounds(min_rounds, max_rounds)
    if review_profile not in REVIEW_PROFILES:
        review_profile = "code"
    # SDEG: three DISTINCT audit axes travel with every dispatch. The declared
    # content class (data_sensitivity) and the dispatch lane are recorded here.
    # Fail-closed: anything other than an explicit "standard" or
    # "internal_technical" is "sensitive". "internal_technical" is the
    # CW-technical-self-review lane (dispatch_lane "internal_technical"); every
    # other class dispatches in the "user" lane and can NEVER carry ITS-resolved
    # content. The lane only ENABLES ITS for clean technical ancestry; it never
    # relabels SENSITIVE ancestry (the guard re-enforces at send).
    _ds = str(data_sensitivity or "").strip().lower()
    if _ds == "standard":
        data_sensitivity, dispatch_lane = "standard", "user"
    elif _ds == "internal_technical":
        data_sensitivity, dispatch_lane = "internal_technical", "internal_technical"
    else:
        data_sensitivity, dispatch_lane = "sensitive", "user"
    council_id = council_id or new_council_id()
    council = {
        "council_id": council_id,
        "thread_id": thread_id,
        "work_item_id": work_item_id,
        "packet_id": packet_id,
        "phase": phase,
        "review_profile": review_profile,
        "data_sensitivity": data_sensitivity,
        "dispatch_lane": dispatch_lane,
        # Live lineage (content-free), recorded at assembly and rebuilt at each
        # round: the immutable candidate graph, the outbound candidate id, and
        # the verified-STANDARD source hash bindings for the TOCTOU re-check.
        "lineage": lineage,
        "lineage_candidate": lineage_candidate,
        "source_bindings": source_bindings,
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

    # blocked_by_capability: the reviewer is RIGHT and the harness cannot
    # satisfy the requirement. The completed round remains counted; the block
    # stops FURTHER rounds and escalates to the operator immediately. It never
    # counts as resolved and no score, count, or readiness flag can override it
    # (validate_reconciliation already rejects ready_to_proceed=true alongside
    # it).
    capability_blocked = [r.get("ref") for r in (recon.get("resolutions") or [])
                          if r.get("disposition") == "blocked_by_capability"]
    if capability_blocked:
        base["capability_blocked_refs"] = capability_blocked
        base["unresolved_blockers"] = ["blocked_by_capability: " + str(ref)
                                       for ref in capability_blocked]
        base.update({"outcome": "operator_required", "operator_required": True,
                     "reason": "capability-blocked finding(s) escalated to the "
                               "operator: " + ", ".join(map(str, capability_blocked))})
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
                 council_id, round_no, phase, model, timeout, egress_context=None):
    # Give the reviewer generous output headroom: a full structured verdict plus
    # a reasoning model's internal tokens truncates under a small cap, which
    # would otherwise fail validation and read as reviewer_unavailable. The
    # egress_context carries the live lineage enforcement to the guard.
    return gpt_adapter.review(
        root, context_text, thread_id=thread_id, work_item_id=work_item_id,
        packet_id=packet_id, council_id=council_id, round=round_no, phase=phase,
        model=model, timeout=timeout, max_output_tokens=4000,
        egress_context=egress_context)


def _default_codex(root, context_text, *, thread_id, work_item_id, packet_id,
                   council_id, round_no, phase, repo, timeout, egress_context=None):
    return codex_adapter.review_structured(
        root, thread_id=thread_id, work_item_id=work_item_id, packet_id=packet_id,
        council_id=council_id, round=round_no, phase=phase, context_text=context_text,
        timeout=timeout, cwd=repo, egress_context=egress_context)


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


def grant_attempts(root, council, reviewer, operator_message_id, extra=1):
    """Operator-authorized recovery: grant ADDITIONAL attempts for one reviewer
    on the CURRENT (next) round. This explicit, recorded operator action is the
    ONLY way to extend an attempt budget — fingerprint changes never do.

    The authority record must be RETRY-SPECIFIC. The original task approval or
    an unrelated operator message is not sufficient; the referenced message must
    be an inbound operator-authored record that:
      - names this council_id,
      - names this reviewer,
      - explicitly mentions attempt/retry authorization,
      - was created AFTER the attempt budget was exhausted.
    Grants extend attempts only; they never increase or reset the substantive
    round ceiling."""
    if reviewer not in REVIEWER_CAPABILITIES:
        return {"ok": False, "error": "unknown reviewer {!r}".format(reviewer)}
    try:
        extra = int(extra)
    except (TypeError, ValueError):
        return {"ok": False, "error": "grant count must be an integer"}
    if not (1 <= extra <= MAX_ATTEMPTS_PER_ROUND):
        return {"ok": False, "error": "grant count must be between 1 and {}".format(
            MAX_ATTEMPTS_PER_ROUND)}
    match = [m for m in cwm.read_messages(root)
             if m.get("message_id") == operator_message_id]
    if not match:
        return {"ok": False, "error": "operator message {!r} not found".format(operator_message_id)}
    m = match[0]
    if m.get("direction") != "inbound" or (m.get("role") or "").lower() != "operator":
        return {"ok": False,
                "error": "referenced message is not an inbound operator authority record"}
    body = (m.get("message") or "")
    body_l = body.lower()
    if council["council_id"].lower() not in body_l:
        return {"ok": False, "error": "authority record does not name council "
                "{!r}; a retry grant must be council-specific".format(council["council_id"])}
    if not re.search(r"\b" + re.escape(reviewer) + r"\b", body_l):
        return {"ok": False, "error": "authority record does not name reviewer "
                "{!r}".format(reviewer)}
    if not re.search(r"\battempt|\bretry|\bretries\b", body_l):
        return {"ok": False, "error": "authority record does not explicitly "
                "authorize additional attempts/retries"}
    round_no = len(council.get("rounds", [])) + 1
    key = _attempt_key(round_no, reviewer)
    state = council.setdefault("attempt_state", {}).setdefault(
        key, {"calls": 0, "grants": []})
    exhausted_at = state.get("last_call_at")
    if state.get("calls", 0) + _granted_extra(state) < MAX_ATTEMPTS_PER_ROUND:
        return {"ok": False, "error": "attempt budget for {} round {} is not "
                "exhausted; a grant applies only after exhaustion".format(reviewer, round_no)}
    if exhausted_at and (m.get("at") or "") <= exhausted_at:
        return {"ok": False, "error": "authority record predates the exhausted "
                "attempt budget; retry authorization must be issued after the failure"}
    state.setdefault("grants", []).append({
        "operator_message_id": operator_message_id, "at": cwm._now_iso(),
        "extra_attempts": extra, "calls_at_grant": state.get("calls", 0),
        "authority_excerpt": body[:200]})
    _persist_council(root, council)
    return {"ok": True, "round": round_no, "reviewer": reviewer, "extra": extra,
            "operator_message_id": operator_message_id}


def _granted_extra(state):
    return sum(int(g.get("extra_attempts", 0)) for g in (state or {}).get("grants", []))


def _verdict_residue_hit(verdict):
    """Universal pre-persistence residue gate for a reviewer verdict on the
    NON-ITS path (V2). Scans the COMPLETE validated verdict — every field, string,
    list item, and nested mapping value (findings are stored as objects like
    {"finding": "..."} / {"change": "..."}, so a string-only scan would miss their
    free text) — by rendering the whole verdict to a canonical JSON string and
    scanning that. Returns True on a HARD residue hit (any category except
    unicode_confusable). Fails closed to a hit on any rendering or scanner error.
    Clean verdicts return False, so the normal path is unchanged."""
    try:
        text = json.dumps(verdict or {}, ensure_ascii=False, sort_keys=True,
                          default=str)
        _safe, findings = guard.redact_for_persistence(text)
    except Exception:  # noqa: BLE001 - render/scan failure must not open the gate
        return True
    return any(k != "unicode_confusable" for k in (findings or {}))


def run_round(root, council, base_context, *, model=None, repo=None, timeout=90,
              artifact_ids=(), gpt_fn=None, codex_fn=None, sleep=time.sleep):
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
    # The dispatch lane authorizes INTERNAL_TECHNICAL_STANDARD. On the ITS lane
    # the packet is assembled ONLY from a fixed versioned scaffold plus hash-
    # bound components (below): NO _augment_context / _guidance_header prose is
    # appended, so neither helper is ever called on this path.
    its_lane = (council.get("dispatch_lane") == "internal_technical")
    if its_lane:
        context = base_context
    else:
        _augmented = substantive_round_count(prior_rounds) > 0
        context = _augment_context(base_context, prior_rounds) \
            if _augmented else base_context
        # PROMPT-ONLY guidance (role lanes + scoped follow-up rounds). This
        # changes only the reviewer prompt string; evaluate(), the verdict
        # schema, and the reconciliation validation are untouched.
        context = _guidance_header(council.get("review_profile", "code"),
                                   round_no) + context

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

    if its_lane:
        # INTERNAL_TECHNICAL_STANDARD lane: the packet is assembled ONLY from a
        # fixed versioned scaffold plus hash-bound, pre-scanned derived
        # components (prior-round summaries) and the git-verified review context.
        # No artifacts, no capability statement, no free-form prose. The guard
        # independently re-enforces every rule below at send.
        import clearwright_egress_guard as _egress
        import clearwright_its_artifacts as cwia
        # 1. Artifacts and non-code profiles are refused: an ITS packet has no
        #    cwa capability statement and no codex_reference_block; a genuinely
        #    STANDARD file must be declared with --source (git-verified).
        _its_all_ids = sorted(set(list(council.get("artifact_ids") or []) +
                                  [a for a in (artifact_ids or []) if a]))
        if _its_all_ids:
            return {"committed": False, "substantive": False, "round": round_no,
                    "its_blocked": True, "statuses": {}, "attempts": {},
                    "reason": "artifacts are not permitted in the internal_technical "
                              "lane; declare git-verified files with --source"}
        if council.get("review_profile", "code") != "code":
            return {"committed": False, "substantive": False, "round": round_no,
                    "its_blocked": True, "statuses": {}, "attempts": {},
                    "reason": "only the code profile is supported in the "
                              "internal_technical lane"}
        # 2. Stamp check FIRST (content-free): the live lineage was built for a
        #    specific packet content and stamped with its sha256. A missing or
        #    stale stamp means the graph does not describe these bytes.
        _its_stamp = council.get("lineage_context_sha256")
        _its_incoming = hashlib.sha256((base_context or "").encode("utf-8")).hexdigest()
        if not _its_stamp or _its_stamp != _its_incoming:
            return {"committed": False, "substantive": False, "round": round_no,
                    "its_blocked": True, "statuses": {}, "attempts": {},
                    "reason": "lineage stamp missing or stale for the "
                              "internal_technical lane; rebuild via the use-cw "
                              "council command"}
        # 3. Rebuild the immutable candidate graph; require the candidate. Engine
        #    pre-check (UX only; the guard independently re-enforces): every RAW
        #    node must declare a STANDARD provenance class — any sensitive_source
        #    / inline-unverified node blocks in the internal_technical lane.
        _its_lineage = council.get("lineage")
        cand = council.get("lineage_candidate")
        graph_obj = _egress.LineageGraph.from_records(_its_lineage) if _its_lineage else None
        if graph_obj is None or cand is None:
            return {"committed": False, "substantive": False, "round": round_no,
                    "its_blocked": True, "statuses": {}, "attempts": {},
                    "reason": "lineage graph or candidate missing for the "
                              "internal_technical lane; rebuild via the use-cw "
                              "council command"}
        for _rec in (_its_lineage or []):
            if _rec.get("classification") != _egress.CLASS_RAW:
                continue
            if (_rec.get("provenance") or {}).get("class") not in _egress._STANDARD_PROVENANCE:
                return {"committed": False, "substantive": False, "round": round_no,
                        "its_blocked": True, "statuses": {}, "attempts": {},
                        "reason": "unverified or user-supplied content source in "
                                  "the internal_technical lane"}
        _cand_node = graph_obj.get(cand) or {}
        _existing_src_ids = list(_cand_node.get("source_ids") or [])

        def _its_scaffold_node(ver):
            _nid = "scaffold-" + str(ver)
            if graph_obj.get(_nid) is None:
                graph_obj.add(_nid, _egress.CLASS_RAW,
                              provenance={"class": "fixed_scaffold",
                                          "version": str(ver),
                                          "sha256": _egress.its_scaffold_sha(ver)})
            return _nid

        def _its_derived_node(rec, rd_round):
            graph_obj.add(rec["artifact_id"], _egress.CLASS_MACHINE,
                          source_ids=list(rec.get("source_ids") or []),
                          derived={"content_hash": rec["content_sha256"],
                                   "scan_passed": True,
                                   "producer": rec.get("producer"),
                                   "council_id": council_id, "round": rd_round,
                                   "policy_version": rec.get("policy_version"),
                                   "scaffold_version": rec.get("scaffold_version")})

        # 4. Assemble prior-round components, ascending over prior SUBSTANTIVE
        #    rounds. Each finding/reconciliation is verified for reuse (hash +
        #    re-scan) and folded into a registered round-summary artifact.
        components = []
        summary_ids = []
        _prior_substantive = [rd for rd in prior_rounds if rd.get("substantive", True)]
        for rd in _prior_substantive:
            _rd_no = rd.get("round")
            _meta = rd.get("its") or {}
            _fids = _meta.get("artifact_ids") or {}
            _gid, _cid = _fids.get("gpt"), _fids.get("codex")
            if not _gid or not _cid:
                return {"committed": False, "substantive": False, "round": round_no,
                        "its_blocked": True, "statuses": {}, "attempts": {},
                        "reason": "prior round has no registered finding artifacts; "
                                  "unregistered augmentation is not dispatchable"}
            _rid = None
            try:
                _grec = cwia.verify_for_reuse(root, council_id, _gid)
                _crec = cwia.verify_for_reuse(root, council_id, _cid)
                _recon_rec = None
                if rd.get("reconciliation") is not None:
                    _rid = _fids.get("reconciliation")
                    if not _rid:
                        return {"committed": False, "substantive": False,
                                "round": round_no, "its_blocked": True,
                                "statuses": {}, "attempts": {},
                                "reason": "prior round reconciliation is not a "
                                          "registered artifact; unregistered "
                                          "augmentation is not dispatchable"}
                    _recon_rec = cwia.verify_for_reuse(root, council_id, _rid)
            except _egress.EgressBlocked as exc:
                return {"committed": False, "substantive": False, "round": round_no,
                        "its_blocked": True, "statuses": {}, "attempts": {},
                        "reason": exc.reason}
            _gv = json.loads(_grec["content"])
            _cv = json.loads(_crec["content"])
            _rv = json.loads(_recon_rec["content"]) if _recon_rec else None
            _summary_text = cwia.render_round_summary(_rd_no, _gv, _cv, _rv)
            _summary_sources = [_gid, _cid] + ([_rid] if _recon_rec else [])
            _srec = cwia.register(root, council_id, kind="round_summary",
                                  producer="engine", round_no=_rd_no, phase=phase,
                                  content=_summary_text,
                                  scaffold_version=_meta.get("scaffold_version"),
                                  source_ids=_summary_sources)
            if (_srec.get("scan") or {}).get("passed") is not True:
                return {"committed": False, "substantive": False, "round": round_no,
                        "its_blocked": True, "statuses": {}, "attempts": {},
                        "reason": "its_generated_scan_failed"}
            components.append({"id": _srec["artifact_id"], "text": _summary_text})
            summary_ids.append(_srec["artifact_id"])
            _its_scaffold_node(_meta.get("scaffold_version"))
            _its_derived_node(_grec, _rd_no)
            _its_derived_node(_crec, _rd_no)
            if _recon_rec:
                _its_derived_node(_recon_rec, _rd_no)
            _its_derived_node(_srec, _rd_no)
        # 5. The review context is the LAST component, and is represented in the
        #    lineage as a hash-bound ASSEMBLY node over the verified src nodes so
        #    the guard's composition-to-lineage binding resolves it (V1). The node
        #    id is the SAME id used for the ctx component; its content_hash is
        #    sha256(base_context). It carries NO `derived`, so it resolves STANDARD
        #    when the srcs are STANDARD and never forces sensitivity by itself.
        ctx_sha = hashlib.sha256(base_context.encode("utf-8")).hexdigest()
        ctx_id = "ctx-" + ctx_sha[:12]
        components.append({"id": ctx_id, "text": base_context})
        graph_obj.add(ctx_id, _egress.CLASS_MACHINE,
                      source_ids=list(_existing_src_ids), content_hash=ctx_sha)
        # 6. Fixed scaffold for this round: round-1 preamble, or the follow-up
        #    scaffold (which folds the scoped-round guidance + prior-rounds
        #    framing) once at least one prior substantive round exists.
        its_scaffold_version = (ITS_SCAFFOLD_ROUND1 if not _prior_substantive
                                else ITS_SCAFFOLD_FOLLOWUP)
        _scaffold_node_id = _its_scaffold_node(its_scaffold_version)
        # 7. Rebind the candidate over the src nodes + this round's scaffold +
        #    the prior-round summary artifacts + the ctx node (no inline node
        #    exists — step 3 refused any). Every composition["components"] entry
        #    (each summary + the ctx) is now a lineage node reachable from the
        #    candidate whose content hash equals its component frame sha.
        graph_obj.add(cand, _egress.CLASS_MACHINE,
                      source_ids=(_existing_src_ids + [_scaffold_node_id]
                                  + summary_ids + [ctx_id]),
                      domain=_cand_node.get("domain"))
        # 8. Build the canonical ITS packet + composition; bind the dynamic
        #    provider metadata (model + token cap) into the composition so the
        #    guard can prove the outbound body by byte-equality.
        requested_model = model or council.get("model")
        packet_text, composition = _egress.build_its_packet(its_scaffold_version,
                                                            components)
        composition["provider_binding"] = {
            "gpt_model": gpt_adapter.resolve_model(requested_model),
            "max_output_tokens": 4000}
        # 9. Both reviewers receive the composed packet; the guard owns the
        #    canonical body/prompt. No capability statement / codex_reference_block
        #    / _guidance_header / _augment_context on this path.
        gpt_body = packet_text
        codex_prompt = packet_text
        gpt_delivery = "its_composition"
        all_artifact_ids = []
        artifact_hashes = []
        ctx_sha = hashlib.sha256(context.encode("utf-8")).hexdigest()
        packet_bytes = len(codex_prompt.encode("utf-8"))
        its_finding_sources = [_scaffold_node_id] + summary_ids + _existing_src_ids
        its_finding_ids = {}

        def _its_register_finding(_reviewer, _result):
            """Scan + register one reviewer verdict as a hash-bound derived
            finding (requirement 7). Returns the record on a clean scan, or None
            on residue. Deterministic and idempotent (id = kind+round+content
            sha), so re-invoking for a reviewer whose result is served from the
            cross-invocation pending cache recovers the SAME artifact id rather
            than dropping it — a partially-failed round that is re-run must still
            record every finding pointer and must not brick later rounds."""
            _kind = "gpt_finding" if _reviewer == "gpt" else "codex_finding"
            _frec = cwia.register(
                root, council_id, kind=_kind, producer=_reviewer,
                round_no=round_no, phase=phase,
                content=cwia.render_verdict(_result["verdict"]),
                scaffold_version=its_scaffold_version,
                source_ids=its_finding_sources)
            return _frec if (_frec.get("scan") or {}).get("passed") is True else None
        # 10. Egress context carries the lane and the composition manifest.
        egress_context = _egress.EgressContext(
            "internal_technical", work_item_id=council.get("work_item_id"),
            graph=graph_obj, candidate_id=cand,
            source_bindings=council.get("source_bindings") or [],
            require_graph=True, lane="internal_technical",
            its_composition=composition)
        try:
            effective_tier = egress_context.resolve().get("tier", "sensitive")
        except _egress.EgressBlocked:
            effective_tier = "sensitive"
        kw = dict(thread_id=council.get("thread_id"),
                  work_item_id=council.get("work_item_id"),
                  packet_id=council.get("packet_id"),
                  council_id=council_id, round_no=round_no, phase=phase,
                  egress_context=egress_context)
        # 11. Budget estimate uses the guard-owned ITS instruction (replaces the
        #     standard adapter INSTRUCTION on this path).
        est_tokens = estimate_tokens(len(_egress.ITS_GPT_INSTRUCTION) + len(packet_text))
        # Budget fail-fast on the FINAL assembled ITS packet, before any attempt.
        budget = phase_input_budget(phase)
        if est_tokens > budget:
            return {"committed": False, "substantive": False, "round": round_no,
                    "packet_undeliverable": True, "statuses": {}, "attempts": {},
                    "estimated_input_tokens": est_tokens, "budget": budget,
                    "reason": ("assembled packet (~{} estimated input tokens) exceeds the "
                               "{} phase budget of {}; no reviewer attempt was spent. "
                               "Shrink the packet or raise CLEARWRIGHT_GPT_{}_INPUT_BUDGET."
                               ).format(est_tokens, phase, budget, str(phase or "plan").upper())}
        codex_cwd = repo
        codex_timeout = max(int(timeout or 0),
                            codex_adapter.effective_timeout(packet_bytes, base=timeout))
        plan = [
            ("gpt", gpt_fn, gpt_adapter.ADAPTER_VERSION,
             dict(model=requested_model, timeout=timeout), gpt_body),
            ("codex", codex_fn, codex_adapter.ADAPTER_VERSION,
             dict(repo=codex_cwd, timeout=codex_timeout), codex_prompt),
        ]
    else:
        # Artifacts: remembered on the council across rounds; every pinned copy is
        # re-verified against its FULL sha256 immediately before dispatch.
        import clearwright_artifacts as cwa
        all_artifact_ids = sorted(set(list(council.get("artifact_ids") or []) +
                                      [a for a in (artifact_ids or []) if a]))
        if all_artifact_ids != list(council.get("artifact_ids") or []):
            council["artifact_ids"] = all_artifact_ids
            _persist_council(root, council)
        artifact_hashes = []
        try:
            for aid in all_artifact_ids:
                artifact_hashes.append(cwa.verify(root, aid)["sha256"])
        except cwa.ArtifactError as exc:
            return {"committed": False, "substantive": False, "round": round_no,
                    "hard_gate": True, "artifact_error": str(exc),
                    "statuses": {}, "attempts": {},
                    "reason": "artifact verification failed before dispatch: {}".format(exc)}

        # Capability-aware packaging. GPT (text-only) always receives its capability
        # statement; the full line-numbered artifact is inlined only when the FINAL
        # assembled packet fits the phase budget, otherwise a bounded excerpt pack
        # with a plain manifest. Codex (file-capable) receives a concise prompt with
        # absolute pinned paths + expected hashes to read from disk.
        budget = phase_input_budget(phase)
        divisor_chars = budget * 3  # chars corresponding to the token budget (est.)
        gpt_delivery = "none"
        gpt_body = context
        codex_prompt = context
        if all_artifact_ids:
            gpt_base = cwa.GPT_CAPABILITY_STATEMENT + "\n\n" + context + "\n\n"
            inline_parts, inline_ok = [], True
            for aid in all_artifact_ids:
                text, _rec = cwa.inline_rendering(root, aid)
                inline_parts.append(text)
            candidate = gpt_base + "\n\n".join(inline_parts)
            if estimate_tokens(len(gpt_adapter.INSTRUCTION) + len(candidate)) <= budget:
                gpt_body, gpt_delivery = candidate, "inline_full"
            else:
                remaining = max(4000, divisor_chars - len(gpt_adapter.INSTRUCTION)
                                - len(gpt_base) - 2000)
                per = max(2000, remaining // max(1, len(all_artifact_ids)))
                # Target the excerpts at the exact lines the review context cites:
                # the reviewer's own citations ARE the evidence it needs to check.
                focus = cwa.cited_line_numbers(context)
                packs = [cwa.excerpt_pack(root, aid, per, focus_lines=focus)[0]
                         for aid in all_artifact_ids]
                gpt_body, gpt_delivery = gpt_base + "\n\n".join(packs), "excerpt_pack"
            codex_prompt = context + "\n\n" + cwa.codex_reference_block(root, all_artifact_ids)
        else:
            gpt_body = cwa.GPT_CAPABILITY_STATEMENT + "\n\n" + context

        # Budget fail-fast on the FINAL assembled GPT packet, before any attempt.
        packet_chars = len(gpt_adapter.INSTRUCTION) + len(gpt_body)
        est_tokens = estimate_tokens(packet_chars)
        if est_tokens > budget:
            return {"committed": False, "substantive": False, "round": round_no,
                    "packet_undeliverable": True, "statuses": {}, "attempts": {},
                    "estimated_input_tokens": est_tokens, "budget": budget,
                    "reason": ("assembled packet (~{} estimated input tokens) exceeds the "
                               "{} phase budget of {}; no reviewer attempt was spent. "
                               "Shrink the packet or raise CLEARWRIGHT_GPT_{}_INPUT_BUDGET."
                               ).format(est_tokens, phase, budget, str(phase or "plan").upper())}

        ctx_sha = hashlib.sha256(context.encode("utf-8")).hexdigest()
        packet_bytes = len(codex_prompt.encode("utf-8"))
        requested_model = model or council.get("model")

        # SDEG egress boundary (Decision 1A/2A): the tier travels with the council
        # (set from the work item's declared data_sensitivity at creation). The
        # guard validates the EXACT outbound bytes at each adapter; an un-annotated
        # council defaults to the standard tier, where every byte is still scanned
        # by the guard's tripwires and the final-serialized-request check. Operator-
        # declared SENSITIVE work items carry "sensitive" and may dispatch only a
        # construction-proven closed-schema derivative.
        import clearwright_egress_guard as _egress
        tier = "sensitive" if council.get("data_sensitivity") == "sensitive" else "standard"
        # Live lineage: rebuild the immutable candidate graph recorded at packet
        # assembly. require_graph=True means a missing graph/candidate fails closed
        # on real dispatch — there is NO fallback to the declared tier. The guard
        # resolves effective sensitivity from the graph and TOCTOU-re-verifies the
        # bound source hashes at send.
        _lineage = council.get("lineage")
        _cand = council.get("lineage_candidate")
        _graph_obj = _egress.LineageGraph.from_records(_lineage) if _lineage else None
        # CONTENT-TO-LINEAGE BINDING (engine choke point, covers EVERY caller):
        # the persisted lineage was built for a specific packet content, stamped
        # with its sha256. If the content dispatched THIS round differs from the
        # stamp (a continuation that did not rebuild the lineage — e.g. the lower-
        # level engine CLI, or any future caller), the graph does not describe these
        # bytes: force SENSITIVE so a stale STANDARD graph cannot launder fresh
        # content. Fail-closed and caller-independent.
        _incoming_sha = hashlib.sha256((base_context or "").encode("utf-8")).hexdigest()
        _stamp = council.get("lineage_context_sha256")
        # A lineage graph present WITHOUT a matching content stamp cannot be trusted
        # to describe these bytes (create_council persists a graph but no stamp; an
        # engine-CLI or early-return path may dispatch it). Absent OR mismatched
        # stamp => fail closed to SENSITIVE. Only a stamp that matches the incoming
        # content lets the graph's resolution stand.
        if _graph_obj is not None and (not _stamp or _stamp != _incoming_sha):
            tier = "sensitive"
        # EVERY inlined artifact is content-bearing but carries NO git provenance
        # (register() pins any path). They reach the packet through the artifact_id /
        # council-remembered channel, which bypasses --source, so they MUST be added
        # to the candidate's ancestry here, at the choke point where all_artifact_ids
        # is finalized (it includes ids remembered on the council across rounds).
        # Each is a SENSITIVE RAW node, so any artifact-bearing council resolves
        # SENSITIVE and a declared-standard dispatch fails closed; a genuinely
        # STANDARD file must be declared as --source (git-verified), not --artifact.
        _extra_sources = []
        if all_artifact_ids and _graph_obj is not None and _cand is not None:
            for _aid in all_artifact_ids:
                _nid = "art-" + hashlib.sha256(_aid.encode()).hexdigest()[:12]
                _graph_obj.add(_nid, _egress.CLASS_RAW,
                               provenance={"class": "sensitive_source",
                                           "reason": "inlined_artifact"})
                _extra_sources.append(_nid)
        # Round >=2 packets carry machine-generated augmentation (prior reviewer
        # findings + Claude's reconciliation free-text) that is NOT named by the
        # lineage and NOT covered by the base-content stamp. It has no verified
        # STANDARD ancestry, so fold it in as a SENSITIVE ancestor: a declared-
        # standard multi-round council resolves SENSITIVE and fails closed.
        if _augmented and _graph_obj is not None and _cand is not None:
            _graph_obj.add("round-augmentation", _egress.CLASS_RAW,
                           provenance={"class": "sensitive_source",
                                       "reason": "round_augmentation"})
            _extra_sources.append("round-augmentation")
        if _extra_sources and _graph_obj is not None and _cand is not None:
            _cn = _graph_obj.get(_cand) or {}
            _graph_obj.add(_cand, _egress.CLASS_MACHINE,
                           source_ids=list(_cn.get("source_ids") or []) + _extra_sources,
                           domain=_cn.get("domain"))
        egress_context = _egress.EgressContext(
            tier, work_item_id=council.get("work_item_id"),
            graph=_graph_obj, candidate_id=_cand,
            source_bindings=council.get("source_bindings") or [],
            require_graph=True)
        # The EFFECTIVE (lineage-derived) sensitivity — not the declared tier —
        # decides digest hygiene below. Fail-closed to sensitive if the lineage
        # cannot resolve (missing graph, sensitive ancestor, etc.).
        try:
            _resolved = egress_context.resolve()
            effective_tier = _resolved.get("tier", "sensitive")
        except _egress.EgressBlocked:
            effective_tier = "sensitive"

        kw = dict(thread_id=council.get("thread_id"),
                  work_item_id=council.get("work_item_id"),
                  packet_id=council.get("packet_id"),
                  council_id=council_id, round_no=round_no, phase=phase,
                  egress_context=egress_context)

        # Codex readable-workspace contract (verified in the acceptance regression:
        # the read-only sandbox reads within its workspace ROOT, not arbitrary
        # absolute paths). When artifacts are in play, Codex runs with the artifact
        # registry as its working directory so the pinned absolute paths fall inside
        # the readable root — narrowly scoped, no broad permission granted. Without
        # artifacts the caller's --repo remains the working directory.
        codex_cwd = os.path.join(root, "review_artifacts") if all_artifact_ids else repo
        codex_timeout = max(int(timeout or 0),
                            codex_adapter.effective_timeout(
                                packet_bytes + sum(
                                    cwa.get(root, a).get("bytes", 0)
                                    for a in all_artifact_ids if cwa.get(root, a)),
                                base=timeout))
        plan = [
            ("gpt", gpt_fn, gpt_adapter.ADAPTER_VERSION,
             dict(model=requested_model, timeout=timeout), gpt_body),
            ("codex", codex_fn, codex_adapter.ADAPTER_VERSION,
             dict(repo=codex_cwd, timeout=codex_timeout), codex_prompt),
        ]

    # Make the plan visible in the conversation: the timeline should read as the
    # complete exchange (operator request -> plan -> reviews -> reconciliation ->
    # outcome), so each round posts a bounded digest of what was dispatched. The
    # full packet stays in the council record, hash-bound; only a capped digest
    # is posted. SDEG: the digest is scanned by the egress guard BEFORE it is
    # written to the durable thread, so a mislabeled/raw packet can never echo
    # sensitive content into a persisted record (content-free on a hit).
    if effective_tier != "standard":
        # Effective (lineage-derived) sensitivity, not the declared tier: a
        # mis-declared-standard packet with sensitive lineage still withholds,
        # and an internal_technical packet is withheld the same way (content-free
        # digest). Only a resolved-STANDARD packet posts a redacted digest.
        digest = "[content digest withheld: {}-tier council]".format(effective_tier)
    else:
        digest = " ".join(context.split())[:400]
        try:
            import clearwright_egress_guard as _eg
            _safe, _findings = _eg.redact_for_persistence(digest)
            digest = _safe
        except Exception:  # noqa: BLE001 - a scan failure fails closed
            digest = "[digest withheld: pre-persistence scan unavailable]"
    try:
        note = ("Review Council round {} starting ({} phase, {}). GPT delivery: {}. "
                "Artifacts: {}. Context digest: {}{} [full packet sha256 {} in the "
                "council record]").format(
            round_no, phase, council_id,
            gpt_delivery if all_artifact_ids else "text_only",
            ", ".join(all_artifact_ids) if all_artifact_ids else "none",
            digest, "…" if len(context) > 400 else "",
            hashlib.sha256(context.encode("utf-8")).hexdigest()[:16])
        msg = cwm.build_message("claude", note, role="orchestrator",
                                packet_id=council.get("packet_id"),
                                thread_id=council.get("thread_id"),
                                direction="internal", status="posted",
                                source="review-council",
                                work_item_id=council.get("work_item_id"))
        cwm.write_message(root, msg)
    except (ValueError, OSError):
        pass  # a narration failure must never block a dispatch

    attempt_state = council.setdefault("attempt_state", {})
    pending = council.setdefault("pending_results", {})
    results, statuses, attempts_used, fingerprints = {}, {}, {}, {}

    for reviewer, fn, adapter_version, extra, packet_text in plan:
        key = _attempt_key(round_no, reviewer)
        pkt_sha = hashlib.sha256(packet_text.encode("utf-8")).hexdigest()
        fp = dispatch_fingerprint(reviewer, pkt_sha, requested_model, phase,
                                  adapter_version, artifact_hashes)
        fingerprints[reviewer] = fp

        cached = pending.get(key)
        if cached and cached.get("fingerprint") == fp and _validated(cached.get("result")):
            if its_lane:
                # A cached ITS result was scan-registered on the earlier
                # invocation; recover its finding-artifact id (idempotent
                # re-register) so a re-run of a partially-failed round still
                # records this reviewer's finding pointer. A residue result is
                # never cached, so the re-scan passes; the defensive None branch
                # cannot leave a null id in a committed round.
                _rec = _its_register_finding(reviewer, cached["result"])
                if _rec is None:
                    statuses[reviewer] = "residue_blocked"
                    results[reviewer] = None
                    attempts_used[reviewer] = 0
                    continue
                its_finding_ids[reviewer] = _rec["artifact_id"]
            elif _verdict_residue_hit(cached["result"].get("verdict")):
                # V2: re-scan the cached NON-ITS verdict before reuse; a hard
                # residue hit blocks reuse and the round does not commit
                # (mirrors the ITS residue gate above).
                statuses[reviewer] = "residue_blocked"
                results[reviewer] = None
                attempts_used[reviewer] = 0
                continue
            results[reviewer] = cached["result"]
            statuses[reviewer] = "review"
            attempts_used[reviewer] = 0  # reused, no new call
            continue

        state = attempt_state.setdefault(key, {"calls": 0, "grants": []})
        result = None
        while state.get("calls", 0) < MAX_ATTEMPTS_PER_ROUND + _granted_extra(state):
            if state.get("calls", 0) > 0:
                sleep(ATTEMPT_BACKOFF_SECONDS)
            state["calls"] = state.get("calls", 0) + 1
            state["last_call_at"] = cwm._now_iso()
            state["last_fingerprint"] = fp
            _persist_council(root, council)
            t0 = time.monotonic()
            result = fn(root, packet_text, **extra, **kw)
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
                if its_lane:
                    # ITS reviewer-output residue gate (requirement 7): the
                    # verdict is scanned and registered as a derived finding
                    # BEFORE it is cached or counted. Residue -> the content-free
                    # record is the durable audit trail; the result is NOT cached,
                    # NOT counted as a review, so the round cannot commit.
                    _frec = _its_register_finding(reviewer, result)
                    if _frec is None:
                        statuses[reviewer] = "residue_blocked"
                        result = None
                        break
                    its_finding_ids[reviewer] = _frec["artifact_id"]
                elif _verdict_residue_hit(result.get("verdict")):
                    # V2: universal pre-persistence residue gate on the NON-ITS
                    # path. A hard residue hit in the reviewer's own verdict text
                    # discards the result: it is NOT cached and the round cannot
                    # commit (mirrors the ITS reviewer-output residue gate).
                    statuses[reviewer] = "residue_blocked"
                    result = None
                    break
                pending[key] = {"fingerprint": fp, "result": result, "at": cwm._now_iso()}
                _persist_council(root, council)
                break
        results[reviewer] = result
        attempts_used[reviewer] = attempt_state.get(key, {}).get("calls", 0)
        if statuses.get(reviewer) == "residue_blocked":
            pass  # residue (ITS or non-ITS): not a counted review; not persisted
        elif result is None:
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

    if all(statuses.get(rev) == "review" for rev, _f, _v, _e, _p in plan):
        round_data = {"round": round_no, "phase": phase, "at": cwm._now_iso(),
                      "substantive": True, "context_sha256": ctx_sha,
                      "fingerprints": fingerprints, "attempts": attempts_used,
                      "artifact_ids": all_artifact_ids,
                      "artifact_hashes": artifact_hashes,
                      "delivery": {"gpt": gpt_delivery if (all_artifact_ids or its_lane)
                                          else "text_only",
                                   "codex": ("stdin_prompt+pinned_path"
                                             if all_artifact_ids else "stdin_prompt")},
                      "gpt": results["gpt"], "codex": results["codex"],
                      "reconciliation": None}
        if its_lane:
            # Three DISTINCT audit axes: content sensitivity stays
            # council["data_sensitivity"]; provenance class is the lineage
            # resolution; lane is dispatch_lane. The registered finding artifact
            # ids let a later round fold this round forward as verified components.
            round_data["its"] = {
                "lane": "internal_technical",
                "effective_sensitivity": "internal_technical_standard",
                "scaffold_version": its_scaffold_version,
                "composition": composition,
                "artifact_ids": {"gpt": its_finding_ids.get("gpt"),
                                 "codex": its_finding_ids.get("codex")}}
        save_round(root, council, round_data)
        for rev, _f, _v, _e, _p in plan:
            pending.pop(_attempt_key(round_no, rev), None)
            attempt_state.pop(_attempt_key(round_no, rev), None)
        _persist_council(root, council)
        return {"committed": True, "substantive": True, "round": round_no,
                "hard_gate": False, "statuses": statuses, "attempts": attempts_used,
                "gpt_delivery": gpt_delivery if (all_artifact_ids or its_lane)
                                else "text_only",
                "estimated_input_tokens": est_tokens,
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
    if council.get("dispatch_lane") == "internal_technical":
        # ITS lane: the reconciliation is a derived artifact too. Scan and
        # register it BEFORE save_round; a residue hit fails closed and the round
        # file is NOT updated. On success record the artifact id so a later round
        # can fold this reconciliation forward as a verified component.
        import clearwright_its_artifacts as cwia
        _its = latest.get("its") or {}
        _fids = _its.get("artifact_ids") or {}
        rec = cwia.register(
            root, council["council_id"], kind="reconciliation", producer="claude",
            round_no=latest["round"], phase=council.get("phase"),
            content=cwia.render_reconciliation(normalized),
            scaffold_version=_its.get("scaffold_version"),
            source_ids=[_fids.get("gpt"), _fids.get("codex")])
        if (rec.get("scan") or {}).get("passed") is not True:
            raise cwv.VerdictError("reconciliation failed the ITS pre-persistence "
                                   "residue scan; nothing was attached")
        _fids["reconciliation"] = rec["artifact_id"]
        _its["artifact_ids"] = _fids
        latest["its"] = _its
    else:
        # V2: universal pre-persistence residue gate on the NON-ITS path. The
        # reconciliation free-text must be residue-scanned before it is durably
        # attached, mirroring the ITS gate. A hard hit fails closed and the round
        # file is NOT updated (save_round is never reached).
        parts = [normalized.get("summary") or ""]
        parts.extend(str(p) for p in (normalized.get("revised_plan") or []))
        parts.extend(str((f or {}).get("finding") or "")
                     for f in (normalized.get("rejected_findings") or []))
        parts.extend(str(b) for b in (normalized.get("unresolved_blockers") or []))
        # Fail closed on ANY scanner error (defense in depth): redact_for_persistence
        # already returns a scanner_exception finding rather than raising, but an
        # unexpected exception at the call site is also treated as a hard hit.
        try:
            _safe, _findings = guard.redact_for_persistence("\n".join(parts))
            _hard = any(k != "unicode_confusable" for k in (_findings or {}))
        except Exception:  # noqa: BLE001 - scanner failure must not open the gate
            _hard = True
        if _hard:
            raise cwv.VerdictError("reconciliation failed the pre-persistence "
                                   "residue scan; nothing was attached")
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


def stamp_context(root, council, context_sha256):
    """Bind the council's live lineage to the EXACT packet content it was built
    for by recording that content's sha256. run_round refuses to dispatch under
    a lineage whose stamp does not match the current packet, so a stale graph
    cannot launder fresh content (caller-independent)."""
    council["lineage_context_sha256"] = context_sha256
    _atomic_write_json(os.path.join(council_dir(root, council["council_id"]),
                                    "council.json"), council)
    return council


def set_lineage(root, council, lineage, candidate, bindings):
    """Replace the council's live lineage with the CURRENT round's graph and
    persist it. The lineage is a per-dispatch provenance proof, not a durable
    identity, so a continuation round rebinds to the content it is actually
    about (a stale round-1 graph must never gate round-2 bytes)."""
    council["lineage"] = lineage
    council["lineage_candidate"] = candidate
    council["source_bindings"] = bindings
    _atomic_write_json(os.path.join(council_dir(root, council["council_id"]),
                                    "council.json"), council)
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
            granted = grant_attempts(root, council, reviewer, args.operator_message_id,
                                     extra=getattr(args, "grant_count", 1))
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

    artifact_ids = list(getattr(args, "artifact_id", None) or [])
    for path in (getattr(args, "artifact", None) or []):
        import clearwright_artifacts as cwa
        try:
            artifact_ids.append(cwa.register(root, path)["artifact_id"])
        except cwa.ArtifactError as exc:
            print("REFUSED: {}".format(exc), file=sys.stderr)
            return 2

    report = run_round(root, council, context, model=args.model, repo=args.repo,
                       timeout=args.timeout, artifact_ids=artifact_ids)
    council = load_council(root, council["council_id"])

    if report.get("artifact_error"):
        payload = {"council_id": council["council_id"], "phase": council.get("phase"),
                   "outcome": "hard_gate", "hard_gate": True,
                   "error_class": "artifact_verification_failed",
                   "reason": report.get("reason")}
        return _emit(payload, args.json)
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
                       help="Operator-authorized recovery: grant additional attempts "
                            "for the current round. Requires --operator-message-id "
                            "(a retry-specific authority record naming this council "
                            "and reviewer, created after exhaustion).")
        p.add_argument("--operator-message-id", default=None, metavar="ID",
                       help="Durable inbound operator message authorizing the grant.")
        p.add_argument("--grant-count", type=int, default=1, metavar="N",
                       help="Additional attempts granted (default 1; never affects "
                            "the substantive round ceiling).")
        p.add_argument("--artifact", action="append", default=None, metavar="PATH",
                       help="Register and pin an artifact for this council (repeatable).")
        p.add_argument("--artifact-id", action="append", default=None, metavar="ID",
                       help="Reference an already-registered artifact (repeatable).")
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
