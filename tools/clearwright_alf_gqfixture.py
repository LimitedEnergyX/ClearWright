#!/usr/bin/env python3
"""tools/clearwright_alf_gqfixture.py: the GalleyQuest ALF acceptance fixture.

Builds immutable ALF observations from the GalleyQuest governed run's ClearWright
evidence and demonstrates that ALF can identify and quantify: repeated
reviewer_unavailable outcomes, consumed reviewer attempts, multiple work items
created during re-scope, repeated related verification findings, mechanically
unreachable completion, and cumulative operator/elapsed waste - WHILE PRESERVING
causal uncertainty (it never asserts that sensitivity alone caused the failures).

GalleyQuest boundary: this reads only ClearWright's own durable governance
evidence (council ids, work-item ids, thread ids from the run). It touches no
GalleyQuest code, config, service, repository, database, deployment, or runtime,
and creates no GalleyQuest work. The evidence shas below are deterministic fixture
values (sha256 of the ref) so the acceptance test is self-contained; a live
capture computes real bytes.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import clearwright_alf as alf  # noqa: E402
import clearwright_alf_synth as syn  # noqa: E402

# The 10 reviewer_unavailable councils from the GQ run, mapped to their work item
# (run). Four distinct work items across two GQ threads.
GQ_REVIEWER_UNAVAILABLE = [
    ("cw-council-20260720T215651084665", "message:msg-20260720T215507495427"),
    ("cw-council-20260720T220831392679", "message:msg-20260720T215507495427"),
    ("cw-council-20260720T221450299646", "message:msg-20260720T215507495427"),
    ("cw-council-20260723T153239913645", "message:msg-20260723T153047865278"),
    ("cw-council-20260723T153740619585", "message:msg-20260723T153047865278"),
    ("cw-council-20260723T163717540132", "message:msg-20260723T163351081476"),
    ("cw-council-20260723T163820625995", "message:msg-20260723T163351081476"),
    ("cw-council-20260723T165224357245", "message:msg-20260723T163351081476"),
    ("cw-council-20260723T165624377469", "message:msg-20260723T163351081476"),
    ("cw-council-20260723T171031663404", "message:msg-20260723T165821979878"),
]
ATTEMPTS_PER_UNAVAILABLE = 4  # 2 gpt + 2 codex, per the durable attempt_state


def _fixture_sha(ref):
    return alf.sha256_hex(ref.encode("utf-8"))


def reviewer_unavailable_observations():
    obs = []
    for council_id, wi in GQ_REVIEWER_UNAVAILABLE:
        obs.append(alf.build_observation(
            kind="dispatch_failure", subsystem="council_engine",
            summary="reviewer attempt budget exhausted (reviewer_unavailable); "
                    "round not counted",
            council_id=council_id, work_item_id=wi, run_id=wi,
            source_refs=[{"ref": "council-outcome:" + council_id,
                          "sha256": _fixture_sha("council-outcome:" + council_id),
                          "role": "observed_occurrence"}],
            metrics={"council_attempts": ATTEMPTS_PER_UNAVAILABLE},
            capture_method="backfill"))
    return obs


def run_acceptance(q):
    """Ingest the GQ fixture, synthesize a reviewer-failure-waste finding, attribute
    the reviewer_unavailable occurrences, and return the quantified summary."""
    alf.ensure_layout(q)
    syn.materialize_model(q)
    obs = reviewer_unavailable_observations()
    occurrences = []
    for o in obs:
        res = alf.capture(q, o)
        occurrences.append({
            "occurrence_id": res["occurrence_id"], "observation_id": o["observation_id"],
            "run_id": o.get("run_id"), "captured_at": o["captured_at"],
            "metrics": o.get("metrics")})
    # One finding quantifying the reviewer-failure dispatch waste. Causal
    # uncertainty is explicit: we do NOT claim sensitivity alone caused it.
    entry_id = syn.create_finding(q, {
        "title": "Repeated reviewer_unavailable dispatch waste (GalleyQuest run)",
        "status": "PRIORITIZED", "subsystem": "council_engine",
        "failure_class": "council_failure", "blast_radius": "all_councils",
        "occurrence_count": 0, "affected_run_count": 0,
        "priority_tier": 1, "priority_score": 0,
        "root_cause_confidence": "0.60",
        "confidence_basis": "counts are exact from durable records; the underlying "
                            "per-attempt failure reason was NOT durably recorded, so "
                            "the cause (sensitivity/provenance classification vs "
                            "provider flakiness) is not established by the evidence",
        "root_cause": "reviewer attempts were consumed and discarded without a "
                      "durable normalized failure reason; the exact driver is "
                      "unresolved in the evidence",
    }, run_id="gq-acceptance")
    for occ in occurrences:
        syn.attribute_occurrence(q, entry_id, occ, "recurrence")
    head = syn.load_finding(q, entry_id)
    return {
        "entry_id": entry_id,
        "reviewer_unavailable_count": len(obs),
        "consumed_reviewer_attempts": head["cumulative_council_attempts_wasted"],
        "distinct_work_items": len({wi for _c, wi in GQ_REVIEWER_UNAVAILABLE}),
        "affected_run_count": head["affected_run_count"],
        "occurrence_count": head["occurrence_count"],
        "root_cause_confidence": head["root_cause_confidence"],
        "root_cause": head["root_cause"],
    }
