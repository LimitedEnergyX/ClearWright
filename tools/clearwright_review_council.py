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
import os
import re
import sys

import clearwright_message as cwm
import clearwright_verdict as cwv
import clearwright_gpt_review as gpt_adapter
import clearwright_codex_review as codex_adapter

COUNCILS_DIR = "review_councils"
DEFAULT_MIN_ROUNDS = 2
DEFAULT_MAX_ROUNDS = 5
CONFIDENCE_FLOOR = 0.70

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
    completed = len(rounds)

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

    if completed == 0:
        base.update({"outcome": "needs_revision", "reason": "no review rounds yet"})
        return base

    latest = rounds[-1]
    gpt = latest.get("gpt")
    codex = latest.get("codex")
    recon = latest.get("reconciliation")
    base["gpt_status"] = _reviewer_status(gpt, "gpt")
    base["codex_status"] = _reviewer_status(codex, "codex")

    # Hard gate (missing key / model unavailable / secret) short-circuits.
    if (gpt and gpt.get("hard_gate")) or latest.get("hard_gate"):
        base.update({"outcome": "hard_gate", "hard_gate": True,
                     "reason": (gpt or {}).get("error") or latest.get("hard_gate_reason") or "hard gate"})
        return base

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


def run_round(root, council, base_context, *, model=None, repo=None, timeout=90,
              gpt_fn=None, codex_fn=None):
    """Run one review round: GPT and Codex review the same context independently
    (round one: neither sees the other). Stores and returns the round dict.
    A basic secret scan runs before any reviewer call; a probable secret is a
    hard gate and no reviewer is called."""
    gpt_fn = gpt_fn or _default_gpt
    codex_fn = codex_fn or _default_codex
    round_no = len(council.get("rounds", [])) + 1

    context = _augment_context(base_context, load_rounds(root, council["council_id"])) \
        if round_no > 1 else base_context

    hits = secret_scan(context)
    if hits:
        round_data = {"round": round_no, "phase": council.get("phase"),
                      "at": cwm._now_iso(), "gpt": None, "codex": None,
                      "reconciliation": None, "hard_gate": True,
                      "hard_gate_reason": "possible secret(s) in context: " + ", ".join(hits)}
        return save_round(root, council, round_data)

    kw = dict(thread_id=council.get("thread_id"),
              work_item_id=council.get("work_item_id"),
              packet_id=council.get("packet_id"),
              council_id=council["council_id"], round_no=round_no,
              phase=council.get("phase"))
    gpt_result = gpt_fn(root, context, model=model, timeout=timeout, **kw)
    codex_result = codex_fn(root, context, repo=repo, timeout=timeout, **kw)

    round_data = {"round": round_no, "phase": council.get("phase"),
                  "at": cwm._now_iso(),
                  # Bind the round to the exact context reviewed, so agreement is
                  # auditable against a specific plan revision (not a moving target).
                  "context_sha256": hashlib.sha256(context.encode("utf-8")).hexdigest(),
                  "gpt": gpt_result, "codex": codex_result, "reconciliation": None}
    return save_round(root, council, round_data)


def attach_reconciliation(root, council, reconciliation):
    """Validate and attach Claude's reconciliation to the latest round, and post
    it durably into the conversation. Raises VerdictError on invalid input."""
    rounds = load_rounds(root, council["council_id"])
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
        council = create_council(
            root, thread_id=args.thread_id, work_item_id=args.work_item_id,
            packet_id=args.packet_id, phase=args.phase,
            min_rounds=args.min_rounds, max_rounds=args.max_rounds, model=args.model,
            approved_scope=args.approved_scope)
    else:
        council = set_approved_scope(root, council, args.approved_scope)

    if len(council.get("rounds", [])) >= int(council.get("max_rounds", DEFAULT_MAX_ROUNDS)):
        outcome = evaluate(council, load_rounds(root, council["council_id"]))
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

    run_round(root, council, context, model=args.model, repo=args.repo, timeout=args.timeout)
    council = load_council(root, council["council_id"])
    outcome = evaluate(council, load_rounds(root, council["council_id"]))
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
        p.add_argument("--min-rounds", type=int, default=DEFAULT_MIN_ROUNDS)
        p.add_argument("--max-rounds", type=int, default=DEFAULT_MAX_ROUNDS)
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
