#!/usr/bin/env python3
"""tools/clearwright_alf_seed.py: ALF Phase 1 initial findings (packet section 21)
+ the residual dispatch-eligibility candidate (section 10), and the GalleyQuest
acceptance-fixture observation builder.

The three approved seed findings and the residual TRIAGED candidate enter the
store at first synthesis, fully schema-valid per the seed construction rule, with
their exact evidence bindings from the packet. Seeding is idempotent.

The GalleyQuest builder produces immutable ALF OBSERVATIONS from the GQ run's
CW-evidence characteristics (packet/report). It touches no GalleyQuest code,
config, service, repo, database, deployment, or runtime - only ClearWright's own
durable governance evidence, as authorized. It deliberately does NOT assert that
sensitivity alone caused the reviewer failures.
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import clearwright_alf as alf  # noqa: E402
import clearwright_alf_synth as syn  # noqa: E402

AUTH = "msg-20260719T181306094084"
AUTH_SHA = "de155644f62e46370e6fbcfc4dd539d5dfcd2c9be6d71b7186f00f062d1fdb63"

SEED_DEFAULTS = {
    "status": "TRIAGED", "operator_disposition": "none",
    "immediate_containment": "none required", "immediate_workaround": "none recorded",
    "owner": "operator", "related_entries": [], "supersession_lineage": [],
    "dependencies": [], "blockers": [], "promotion_state": None,
    "deferral_reason": None, "review_date": None, "last_operator_reviewed_at": None,
    "implementation_work_item_id": None, "released_version": None,
    "verification_evidence": None, "release_baseline": None,
    "occurrence_count": 1, "affected_run_count": 1, "affected_work_item_count": 1,
    "cumulative_operator_minutes": 0, "cumulative_execution_delay": 0,
    "cumulative_token_estimate": 0, "cumulative_api_attempts_wasted": 0,
    "cumulative_tool_attempts_wasted": 0, "cumulative_council_attempts_wasted": 0,
    "priority_model_version": "priority-model-v1", "authority_seeded": True,
}
IMPACT_AXES = ("security_impact", "authority_integrity_impact",
               "durable_record_integrity_impact", "reliability_impact",
               "operator_time_impact", "execution_delay_impact",
               "token_api_compute_impact")


def _seed(iv, impacts, evidence, **fields):
    f = dict(SEED_DEFAULTS)
    f.update(dict(zip(IMPACT_AXES, impacts)))
    f["evidence_references"] = syn.alf.normalize_evidence(evidence)
    f.update(fields)
    td = syn.assign_tier(iv)
    f["tier_decision"] = td
    f["priority_tier"] = td["tier"]
    f["priority_score"] = syn.compute_score(f)
    return f


def _ev(ref, sha, role):
    return {"ref": ref, "sha256": sha, "role": role, "archived_location": None}


def seed_records():
    """The four ordered seed records (ALF-0001..0004 when created on a fresh store)."""
    alf0001 = _seed(
        iv={"risk_activity": "historical", "exposure_class": "none",
            "mutation_class": "none", "record_integrity_class": "none",
            "ownership_conflict": False, "authority_integrity_impact": 3,
            "durable_record_integrity_impact": 2, "failure_class": "lifecycle_failure"},
        impacts=(0, 3, 2, 2, 2, 2, 1),
        evidence=[
            _ev("message:msg-20260713T211909139280",
                "08c3f3b351d1ead5ecf14edf389749ee609ef4bfcfb8f27828719814794ae3f3",
                "observed_occurrence"),
            _ev("message:msg-20260713T175640232571",
                "7fcf9c190f7095123db13403bc9954e15b09c0b85cc996567d6c8b9d28408263",
                "observed_occurrence"),
            _ev("summary:msg-20260719T181730501217",
                "d5bbdcf3f1293c5e10c27553ac484a2abc24a8200a7e461f6b53d5713779b7cb",
                "observed_occurrence"),
            _ev("summary:msg-20260719T183202666915",
                "db096c0a81f898944129fe35a04a0bc04bb1d8e6f8c3141d8f60e91b706bfa35",
                "observed_occurrence")],
        title="Missing deterministic terminal-disposition engine",
        subsystem="work_item_lifecycle", failure_class="lifecycle_failure",
        blast_radius="multiple_subsystems", estimated_effort=5,
        root_cause_confidence="0.90",
        confidence_basis="mechanism absence is directly inspectable in the CLI surface",
        problem_statement=("The executor repeatedly interprets whether a work item "
                           "should be completed, closed, superseded, abandoned, "
                           "cancelled, or left open."),
        root_cause=("ClearWright lacks a mechanical lifecycle and disposition "
                    "preflight that returns deliverable status, verification "
                    "requirements, legal terminal actions, required actor, and the "
                    "exact authority needed."),
        observed_symptoms=("repeated deliberation over terminal choices; a "
                           "mis-declared item requiring operator closure; a delivered "
                           "repair whose DONE path was structurally unavailable."))

    alf0002 = _seed(
        iv={"risk_activity": "historical", "exposure_class": "none",
            "mutation_class": "none", "record_integrity_class": "none",
            "ownership_conflict": False, "failure_class": "excess_deliberation"},
        impacts=(0, 0, 0, 1, 2, 2, 2),
        evidence=[_ev("message:" + AUTH, AUTH_SHA, "defining_authority")],
        title="Repeated deliberation without new evidence",
        subsystem="executor_process", failure_class="excess_deliberation",
        blast_radius="single_run", estimated_effort=3, root_cause_confidence="0.70",
        confidence_basis=("detector absence is structural; recurrence dynamics not "
                          "yet measured"),
        problem_statement=("The same entities and candidate decisions recur across a "
                           "run with no new tool evidence, unchanged authority, and "
                           "unchanged durable state, while tokens and elapsed time "
                           "increase without execution progress."),
        root_cause=("no run-level detector correlates decision recurrence with "
                    "evidence staleness; the executor cannot see its own repetition."),
        observed_symptoms=("the same candidate decisions recur within a run; token "
                           "and elapsed-time cost rises with no execution progress."))

    alf0003 = _seed(
        iv={"risk_activity": "historical", "exposure_class": "none",
            "mutation_class": "destructive_action_risk",
            "record_integrity_class": "corruption_risk", "ownership_conflict": False,
            "failure_class": "durable_record_integrity"},
        impacts=(2, 1, 3, 2, 1, 1, 0),
        evidence=[_ev("message:" + AUTH, AUTH_SHA, "defining_authority")],
        title="Destructive cleanup safety is model-driven instead of tool-enforced",
        subsystem="cli", failure_class="durable_record_integrity",
        blast_radius="single_subsystem", estimated_effort=5,
        root_cause_confidence="0.80",
        confidence_basis=("preflight-tool absence is structural; risk surface "
                          "partially measured"),
        problem_statement=("Safety decisions around destructive cleanup depend on "
                           "executor judgment instead of a mechanical preflight."),
        root_cause=("no cleanup preflight tool exists; the guard rails live in "
                    "prompts and conventions rather than refusal-capable tooling."),
        observed_symptoms=("destructive cleanup decided from executor judgment; "
                           "force paths available without a recorded normal-removal "
                           "failure."))

    residual = _seed(
        iv={"risk_activity": "historical", "exposure_class": "none",
            "mutation_class": "none", "record_integrity_class": "none",
            "ownership_conflict": False, "failure_class": "council_failure"},
        impacts=(0, 1, 0, 2, 2, 2, 2),
        evidence=[
            _ev("work-record:REPAIR-GPT-RCA-DISPROOF.md",
                "dd3c91d7bbd5256604de0b366f96316fec7e8735ba028ecd95418f2021436fb5",
                "correction"),
            _ev("work-record:REPAIR-RESULT.md",
                "506a5e86dd22b0090c7570f7b521b8502b6d47ebe4f46bd0369492d958590daf",
                "correction"),
            _ev("work-record:FUTURE-DEFECT-gpt-its-body-construction.md",
                "db5ce223f1eb1ffaafb30b1b2232d10711ddc7c273c7e8413586a2ec79b8e30e",
                "observed_occurrence"),
            _ev("council-outcome:cw-council-20260719T171638796929",
                "c45c27b746438546014cf046d00d207cfbccc857a7674b3e36c0a76dd16e7327",
                "observed_occurrence")],
        title="Council dispatch eligibility is checked too late",
        subsystem="council_engine", failure_class="council_failure",
        blast_radius="all_councils", estimated_effort=5, root_cause_confidence="0.70",
        confidence_basis=("residual concept from the original fourth authority "
                          "finding; headline evidence resolved/disproved, residual "
                          "concept preserved (packet section 10)"),
        problem_statement=("Reviewer attempts are consumed before dispatch "
                           "eligibility is proven; a deterministic pre-allocation "
                           "eligibility preflight is missing."),
        root_cause=("eligibility (approved repo, provenance, composition, exact "
                    "bytes, tripwire, provider readiness) is proven at send, after "
                    "an attempt is already counted."),
        observed_symptoms=("source-outside-repo rejections, unicode-confusable "
                           "rejections, and reviewer attempts consumed before "
                           "eligibility was proven."),
        lineage_note=("residual of the original fourth authority finding; not one of "
                      "the three approved initial findings (packet section 10)."))
    return [alf0001, alf0002, alf0003, residual]


def seed_initial_findings(q):
    if syn.list_findings(q):
        return {"seeded": False, "reason": "findings already present"}
    syn.materialize_model(q)
    syn.materialize_dedup_policy(q)
    ids = [syn.create_finding(q, f, revising_actor="alf-seed", reason="initial_seed")
           for f in seed_records()]
    return {"seeded": True, "entry_ids": ids}


def _cmd_seed(args):
    sys.stdout.write(json.dumps({"ok": True, "command": "seed",
                                 **seed_initial_findings(args.queue_root)}) + "\n")
    return 0


def build_parser():
    p = argparse.ArgumentParser(prog="clearwright_alf_seed",
                                description="Seed ALF initial findings (packet s21).")
    sub = p.add_subparsers(dest="command", required=True)
    s = sub.add_parser("seed", help="Idempotently seed the initial findings.")
    s.add_argument("queue_root")
    s.set_defaults(func=_cmd_seed)
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except alf.AlfError as exc:
        sys.stdout.write(json.dumps({"ok": False, "error": str(exc)}) + "\n")
        return 1


if __name__ == "__main__":
    sys.exit(main())
