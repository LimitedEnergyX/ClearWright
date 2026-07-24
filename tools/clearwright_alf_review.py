#!/usr/bin/env python3
"""tools/clearwright_alf_review.py: ALF Phase 1 operator review + promotion (P1c).

Layer-2/Layer-3 boundary (packet section 16): findings SURFACE into OPERATOR_REVIEW
automatically (disposition-free); every operator-only transition is bound to a
durable INBOUND operator message that (a) exists, (b) has role operator + direction
inbound, (c) was created AFTER the finding revision it disposes, (d) names the
entry_id, and (e) has not been used for any prior ALF disposition (single use;
replay refused). APPROVED_FOR_PLANNING is additionally gated by the promotion
elements. Promote = the approval PLUS a state-neutral rendering of the governed-work
specification (section 18) - which changes no finding state and is re-runnable.

ALF creates no authority, no governed work item, no GitHub state, and no code
change here; it only records the operator's own recorded decision and renders a
document the OPERATOR later uses to open governed work through the normal workflow.
"""
import argparse
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import clearwright_alf as alf  # noqa: E402
import clearwright_alf_synth as syn  # noqa: E402

ALF_RECORD_VERSION = alf.ALF_RECORD_VERSION

# Operator-only transitions (packet section 16). Any transition not listed is
# refused with an exact reason.
OPERATOR_TRANSITIONS = {
    "OPERATOR_REVIEW": {"APPROVED_FOR_PLANNING", "DEFERRED", "REJECTED",
                        "ACCEPTED_RISK", "SUPERSEDED", "NOT_REPRODUCIBLE"},
    "TRIAGED": {"MERGED"},
}
DISPOSITION_FOR_STATUS = {
    "APPROVED_FOR_PLANNING": "approved", "DEFERRED": "deferred",
    "REJECTED": "rejected", "ACCEPTED_RISK": "accepted_risk",
    "SUPERSEDED": "superseded", "NOT_REPRODUCIBLE": "not_reproducible",
    "MERGED": "superseded"}


def dispositions_path(q):
    return alf._p(q, "meta", "dispositions.jsonl")


def _read_message(q, message_id):
    path = os.path.join(q, "communications", message_id + ".json")
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def _message_consumed(q, message_id):
    recs, _ = alf._read_valid_lines(dispositions_path(q))
    return any(r.get("operator_message_id") == message_id for r in recs)


# --------------------------------------------------------------------------- #
# Surfacing (automated, disposition-free): PRIORITIZED -> OPERATOR_REVIEW
# --------------------------------------------------------------------------- #
def surface_for_review(q, entry_id):
    entry_id = alf.safe_id(entry_id, "entry_id")
    ctx = {}

    def _produce(head):
        if head is None:
            raise alf.AlfError("no finding {}".format(entry_id))
        if head.get("status") != "PRIORITIZED":
            ctx["skipped"] = True
            return None
        return (dict(head, status="OPERATOR_REVIEW", surfaced_at=alf.now_iso()),
                "surface_for_review", None)

    syn._atomic_finding_update(q, entry_id, "surface", _produce)
    if ctx.get("skipped"):
        return {"surfaced": False, "reason": "not PRIORITIZED"}
    return {"surfaced": True}


# --------------------------------------------------------------------------- #
# Promotion gate (packet section 16): all elements required for planning approval
# --------------------------------------------------------------------------- #
def _conf_at_least(conf, threshold):
    """Type-safe confidence comparison: numeric or fixed-decimal-string values are
    coerced; anything malformed is treated as below threshold (fails closed) rather
    than raising or mis-ordering lexicographically."""
    try:
        return float(conf) >= threshold
    except (TypeError, ValueError):
        return False


def promotion_gate_problems(finding):
    problems = []
    for field in ("permanent_resolution", "objective_acceptance_criteria",
                  "required_regression_tests"):
        if not finding.get(field):
            problems.append("missing {}".format(field))
    evidence = finding.get("evidence_references") or []
    if not any(e.get("role") == "observed_occurrence" for e in evidence):
        problems.append("no observed_occurrence evidence entry")
    if not (_conf_at_least(finding.get("root_cause_confidence"), 0.50)
            or finding.get("investigation_requirement")):
        problems.append("root_cause_confidence < 0.50 and no investigation_requirement")
    for field in ("dependencies", "blockers"):
        if finding.get(field) is None:
            problems.append("{} not populated".format(field))
    return problems


# --------------------------------------------------------------------------- #
# Disposition (operator-only, message-bound)
# --------------------------------------------------------------------------- #
def dispose(q, entry_id, target_status, operator_message_id, actor="OPERATOR-0001",
            deferral_reason=None, review_date=None):
    """Operator-message-bound disposition. ALL checks (legal transition, message
    existence/role/order, the entry_id token binding, single-use/replay, DEFERRED
    fields, and the promotion gate) and the write execute UNDER one writer lock
    (HIGH-3), so a replay cannot slip between the single-use check and the commit."""
    entry_id = alf.safe_id(entry_id, "entry_id")
    msg = _read_message(q, operator_message_id)

    def _produce(head):
        if head is None:
            raise alf.AlfError("no finding {}".format(entry_id))
        cur = head.get("status")
        if target_status not in OPERATOR_TRANSITIONS.get(cur, set()):
            raise alf.AlfError("illegal transition {} -> {}".format(cur, target_status))
        if msg is None:
            raise alf.AlfError("operator message {} not found".format(operator_message_id))
        if msg.get("role") != "operator" or msg.get("direction") != "inbound":
            raise alf.AlfError("message is not an inbound operator message")
        latest = syn._read_history(q, entry_id)[-1]
        if (msg.get("at") or "") <= (latest.get("revised_at") or ""):
            raise alf.AlfError("operator message must postdate the disposed revision")
        # Whole-token match (not a loose substring), so 'ALF-0001' does not match
        # inside 'ALF-00012' or an incidental mention.
        if not re.search(r"(?<![A-Za-z0-9])" + re.escape(entry_id) + r"(?![A-Za-z0-9])",
                         msg.get("message") or ""):
            raise alf.AlfError("operator message must name the entry_id as a distinct token")
        if _message_consumed(q, operator_message_id):
            raise alf.AlfError("operator message already used for a disposition (replay refused)")
        if target_status == "DEFERRED" and not (deferral_reason and review_date):
            raise alf.AlfError("DEFERRED requires deferral_reason and review_date")
        if target_status == "APPROVED_FOR_PLANNING":
            problems = promotion_gate_problems(head)
            if problems:
                raise alf.AlfError("promotion gate: " + "; ".join(problems))
        nxt = dict(head, status=target_status,
                   operator_disposition=DISPOSITION_FOR_STATUS[target_status],
                   last_operator_reviewed_at=alf.now_iso())
        if target_status == "DEFERRED":
            nxt["deferral_reason"] = deferral_reason
            nxt["review_date"] = review_date
        disposition_line = {
            "alf_record_version": ALF_RECORD_VERSION, "entry_id": entry_id,
            "target_status": target_status, "operator_message_id": operator_message_id,
            "actor": actor, "at": alf.now_iso()}
        return (nxt, "dispose:{}".format(target_status),
                [("meta/dispositions.jsonl", disposition_line)])

    res = syn._atomic_finding_update(q, entry_id, "disposition", _produce, actor=actor)
    return {"disposed": True, "status": target_status,
            "revision_no": res.get("revision_no")}


# --------------------------------------------------------------------------- #
# Spec rendering (packet section 18): state-neutral, re-runnable
# --------------------------------------------------------------------------- #
def render_spec(q, entry_id, version=1):
    entry_id = alf.safe_id(entry_id, "entry_id")
    if not isinstance(version, int) or isinstance(version, bool) or version < 1:
        raise alf.AlfError("spec version must be a positive integer")
    head = syn.load_finding(q, entry_id)
    if head is None:
        raise alf.AlfError("no finding {}".format(entry_id))
    ev = head.get("evidence_references") or []
    lines = [
        "# Governed-work specification: {} (v{})".format(entry_id, version), "",
        "> Rendered by ALF from finding {}. This is input material for the operator "
        "to create authority and a work item through the normal ClearWright workflow. "
        "ALF posts nothing and grants nothing.".format(entry_id), "",
        "## Problem statement", head.get("problem_statement", ""), "",
        "## Permanent resolution", head.get("permanent_resolution", ""), "",
        "## Objective acceptance criteria", head.get("objective_acceptance_criteria", ""), "",
        "## Required regression tests", head.get("required_regression_tests", ""), "",
        "## Dependencies", json.dumps(head.get("dependencies", [])),
        "## Blockers", json.dumps(head.get("blockers", [])),
        "## Estimated effort", str(head.get("estimated_effort", "")), "",
        "## Evidence"]
    for e in ev:
        lines.append("- `{}` sha256 `{}` role {}".format(
            e.get("ref"), e.get("sha256"), e.get("role")))
    lines += [
        "", "## Proposed envelope skeleton",
        "- task_kind: governed (unless the operator directs otherwise)",
        "- approved_scope: <operator to draft from the resolution above>",
        "- excluded_actions: carries every applicable ALF prohibition", ""]
    body = "\n".join(lines) + "\n"
    path = alf._contained(
        alf._p(q, "specs", "spec-{}-v{}.md".format(entry_id, version)), q)
    alf.ensure_layout(q)
    with alf.cwl.write_token(q, purpose="alf-spec"):
        alf._replace_bytes_fsync(path, body.encode("utf-8"))
    return {"spec_path": path, "rendered": True}


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def _emit(obj):
    sys.stdout.write(json.dumps(obj, ensure_ascii=False) + "\n")


def _cmd_surface(args):
    _emit({"ok": True, "command": "surface",
           **surface_for_review(args.queue_root, args.entry_id)})
    return 0


def _cmd_dispose(args):
    _emit({"ok": True, "command": "dispose", **dispose(
        args.queue_root, args.entry_id, args.status,
        operator_message_id=args.operator_message_id, actor=args.actor,
        deferral_reason=args.deferral_reason, review_date=args.review_date)})
    return 0


def _cmd_render(args):
    _emit({"ok": True, "command": "render-spec",
           **render_spec(args.queue_root, args.entry_id, args.version)})
    return 0


def build_parser():
    p = argparse.ArgumentParser(
        prog="clearwright_alf_review",
        description="ALF Phase 1 operator review, disposition, and spec rendering.")
    sub = p.add_subparsers(dest="command", required=True)
    s = sub.add_parser("surface", help="Surface a PRIORITIZED finding for review.")
    s.add_argument("queue_root")
    s.add_argument("entry_id")
    s.set_defaults(func=_cmd_surface)
    d = sub.add_parser("dispose", help="Record an operator-message-bound disposition.")
    d.add_argument("queue_root")
    d.add_argument("entry_id")
    d.add_argument("--status", required=True)
    d.add_argument("--operator-message-id", required=True)
    d.add_argument("--actor", default="OPERATOR-0001")
    d.add_argument("--deferral-reason", default=None)
    d.add_argument("--review-date", default=None)
    d.set_defaults(func=lambda a: _cmd_dispose(a))
    r = sub.add_parser("render-spec", help="Render a governed-work specification.")
    r.add_argument("queue_root")
    r.add_argument("entry_id")
    r.add_argument("--version", type=int, default=1)
    r.set_defaults(func=_cmd_render)
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except alf.AlfError as exc:
        _emit({"ok": False, "error": str(exc), "type": type(exc).__name__})
        return 1


if __name__ == "__main__":
    sys.exit(main())
