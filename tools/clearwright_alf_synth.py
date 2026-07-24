#!/usr/bin/env python3
"""tools/clearwright_alf_synth.py: ALF Phase 1 synthesis (Layer 2).

Builds on the Layer-1 store + operation journal in clearwright_alf.py. Provides:
  * priority-model-v1: the hash-bound, versioned scoring artifact (packet s15) and
    tier-policy-v1 deterministic tier assignment (packet s14).
  * the durable, versioned findings store: append-only per-finding revision log
    with a hash chain, a head file that always equals the last revision, and a
    byte-exact head-rebuild guarantee (packet s6).
  * crash-safe, gap-allowed entry_id allocation (packet s6).

Dedup, recurrence, regression, and the Run Improvement Delta are layered on these
primitives (added incrementally). Nothing here creates authority, governed work,
GitHub state, or mutates code (packet s7/s20): the module contains no such call.
"""
import argparse
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import clearwright_alf as alf  # noqa: E402

ALF_RECORD_VERSION = alf.ALF_RECORD_VERSION

FAILURE_CLASSES = {
    "authority_bypass_risk", "durable_record_integrity", "correctness",
    "operational_reliability", "stale_state", "broken_recovery", "work_blocker",
    "council_failure", "queue_failure", "lifecycle_failure", "deployment_failure",
    "operator_time", "execution_delay", "resource_waste", "poor_failure_reporting",
    "excess_deliberation", "clarity", "user_experience", "documentation",
    "maintainability",
}
BLAST_RADIUS = ("single_event", "single_run", "single_work_item",
                "single_subsystem", "multiple_subsystems", "all_councils",
                "system_wide", "external_or_public")

# --------------------------------------------------------------------------- #
# priority-model-v1: embedded verbatim (packet section 15). The stored file is
# the CANONICAL COMPACT serialization of this structure; priority_model_sha256 is
# computed over those bytes. Editing any string here is a model version change.
# --------------------------------------------------------------------------- #
MODEL_V1 = {
    "model_version": "priority-model-v1",
    "tier_policy_version": "tier-policy-v1",
    "blast_radius_ranks": {
        "single_event": 0, "single_run": 1, "single_work_item": 2,
        "single_subsystem": 3, "multiple_subsystems": 4, "all_councils": 5,
        "system_wide": 6, "external_or_public": 7},
    "tier_policy": {
        "evaluation": "top down, first match wins, default tier 3",
        "predicates": [
            {"tier": 0, "when": "risk_activity in (active,plausible) AND (exposure_class!=none OR mutation_class!=none OR record_integrity_class!=none OR ownership_conflict)"},
            {"tier": 1, "when": "authority_integrity_impact>=2 OR durable_record_integrity_impact>=2 OR failure_class in (authority_bypass_risk,durable_record_integrity,correctness,operational_reliability,stale_state,broken_recovery,work_blocker,council_failure,queue_failure,lifecycle_failure,deployment_failure)"},
            {"tier": 2, "when": "failure_class in (operator_time,execution_delay,resource_waste,poor_failure_reporting,excess_deliberation) OR operator_time_impact>=2 OR execution_delay_impact>=2 OR token_api_compute_impact>=2"},
            {"tier": 3, "when": "otherwise"}]},
    "weights": {
        "security_impact": 4, "authority_integrity_impact": 4,
        "durable_record_integrity_impact": 4, "reliability_impact": 3,
        "operator_time_impact": 2, "execution_delay_impact": 2,
        "token_api_compute_impact": 1},
    "radius_multiplier": 2,
    "recurrence_multiplier": 2, "recurrence_cap": 10,
    "regression_term": 12,
    "waste_multiplier": 2,
    "waste_bands": {
        "cumulative_operator_minutes": {"band1": 30, "band2": 120, "band3": 480},
        "cumulative_execution_delay": {"band1": 600, "band2": 3600, "band3": 14400},
        "cumulative_token_estimate": {"band1": 100000, "band2": 500000, "band3": 2000000},
        "cumulative_api_attempts_wasted": {"band1": 3, "band2": 10, "band3": 25},
        "cumulative_tool_attempts_wasted": {"band1": 10, "band2": 50, "band3": 200},
        "cumulative_council_attempts_wasted": {"band1": 2, "band2": 5, "band3": 10}},
    "waste_band_rule": "per counter: band 0 below band1; thresholds are INCLUSIVE lower bounds (value >= band1 gives 1, >= band2 gives 2, >= band3 gives 3); an absent or null metric is band 0; WB = maximum band across all six counters",
    "effort_points_enum": [1, 2, 3, 5, 8],
    "score_rule": "score = sum(weights[axis]*axis_value) + radius_multiplier*blast_radius_rank + recurrence_multiplier*min(occurrence_count-1,recurrence_cap) + regression_term*regression_flag + waste_multiplier*WB",
    "offline_recompute_rule": "recomputation MUST use the raw persisted cumulative counters and this artifact; a stored WB value is a cache and is never authoritative",
}
WASTE_COUNTERS = (
    "cumulative_operator_minutes", "cumulative_execution_delay",
    "cumulative_token_estimate", "cumulative_api_attempts_wasted",
    "cumulative_tool_attempts_wasted", "cumulative_council_attempts_wasted",
)


def model_bytes():
    return alf.canonical_bytes(MODEL_V1) + b"\n"


def model_sha256():
    return alf.sha256_hex(model_bytes())


def model_path(q):
    return alf._p(q, "meta", "priority-model-v1.json")


def materialize_model(q):
    """Write alf/meta/priority-model-v1.json (canonical compact) if absent, and
    return its sha256. Idempotent: refuses to overwrite a divergent existing
    model (that would be a silent version change)."""
    alf.ensure_layout(q)
    path = model_path(q)
    want = model_bytes()
    if os.path.exists(path):
        with open(path, "rb") as fh:
            have = fh.read()
        if have != want:
            raise alf.IntegrityHalt("priority-model-v1.json on disk diverges from "
                                    "the embedded model; refusing to overwrite")
        return model_sha256()
    with alf.cwl.write_token(q, purpose="alf-model"):
        alf._replace_bytes_fsync(path, want)
    return model_sha256()


# --------------------------------------------------------------------------- #
# tier-policy-v1 (packet section 14): deterministic, top-down, first match wins.
# --------------------------------------------------------------------------- #
def assign_tier(iv):
    """iv: input vector with predicate inputs + impact axes + failure_class.
    Returns a tier_decision record (packet s14/s15)."""
    fc = iv.get("failure_class")
    matched = None
    tier = 3
    if (iv.get("risk_activity") in ("active", "plausible") and (
            iv.get("exposure_class", "none") != "none"
            or iv.get("mutation_class", "none") != "none"
            or iv.get("record_integrity_class", "none") != "none"
            or bool(iv.get("ownership_conflict")))):
        tier, matched = 0, "tier0"
    elif (iv.get("authority_integrity_impact", 0) >= 2
          or iv.get("durable_record_integrity_impact", 0) >= 2
          or fc in ("authority_bypass_risk", "durable_record_integrity",
                    "correctness", "operational_reliability", "stale_state",
                    "broken_recovery", "work_blocker", "council_failure",
                    "queue_failure", "lifecycle_failure", "deployment_failure")):
        tier, matched = 1, "tier1"
    elif (fc in ("operator_time", "execution_delay", "resource_waste",
                 "poor_failure_reporting", "excess_deliberation")
          or iv.get("operator_time_impact", 0) >= 2
          or iv.get("execution_delay_impact", 0) >= 2
          or iv.get("token_api_compute_impact", 0) >= 2):
        tier, matched = 2, "tier2"
    else:
        tier, matched = 3, "tier3"
    return {
        "tier_policy_version": "tier-policy-v1",
        "priority_model_version": "priority-model-v1",
        "priority_model_sha256": model_sha256(),
        "input_vector": dict(iv),
        "matched_predicate": matched,
        "tier": tier,
        "computed_at": alf.now_iso(),
    }


def _waste_band(counter_name, value):
    if value is None:
        return 0
    bands = MODEL_V1["waste_bands"][counter_name]
    if value >= bands["band3"]:
        return 3
    if value >= bands["band2"]:
        return 2
    if value >= bands["band1"]:
        return 1
    return 0


def waste_band_max(finding):
    return max((_waste_band(c, finding.get(c)) for c in WASTE_COUNTERS),
               default=0)


def compute_score(finding, occurrence_count=None, regression_flag=0):
    w = MODEL_V1["weights"]
    base = sum(w[axis] * int(finding.get(axis, 0)) for axis in w)
    br = MODEL_V1["blast_radius_ranks"].get(finding.get("blast_radius"), 0)
    oc = occurrence_count if occurrence_count is not None else finding.get(
        "occurrence_count", 1)
    rec = MODEL_V1["recurrence_multiplier"] * min(
        max(int(oc) - 1, 0), MODEL_V1["recurrence_cap"])
    reg = MODEL_V1["regression_term"] * (1 if regression_flag else 0)
    wb = MODEL_V1["waste_multiplier"] * waste_band_max(finding)
    return base + MODEL_V1["radius_multiplier"] * br + rec + reg + wb


# --------------------------------------------------------------------------- #
# Findings store (packet section 6): head + append-only chained revision log.
# --------------------------------------------------------------------------- #
def finding_head_path(q, entry_id):
    return alf._p(q, "findings", entry_id + ".json")


def finding_history_path(q, entry_id):
    return alf._p(q, "findings", "history", entry_id + ".jsonl")


def _seq_path(q):
    return alf._p(q, "meta", "entry-seq.json")


def _next_entry_id(q):
    path = _seq_path(q)
    last = 0
    if os.path.exists(path):
        import json as _j
        with open(path, encoding="utf-8") as fh:
            last = _j.load(fh).get("last", 0)
    nxt = last + 1
    return "ALF-{:04d}".format(nxt), nxt


def _revision_record(finding, revision_no, revising_actor, reason, run_id, prev):
    payload = {
        "revision_no": revision_no,
        "revised_at": alf.now_iso(),
        "revising_actor": revising_actor,
        "reason": reason,
        "run_id": run_id,
        "record": finding,
        "prev_revision_sha256": prev,
    }
    payload["revision_sha256"] = alf.sha256_hex(alf.canonical_bytes(payload))
    return payload


def create_finding(q, finding, revising_actor="alf-synth", reason="created",
                   run_id=None):
    """Allocate a gap-allowed entry_id and write revision 1 + the head, all inside
    one journal transaction. Returns the entry_id."""
    materialize_model(q)
    entry_id, nxt = _next_entry_id(q)
    finding = dict(finding)
    finding["entry_id"] = entry_id
    finding["alf_record_version"] = ALF_RECORD_VERSION
    hist_rel = "findings/history/{}.jsonl".format(entry_id)
    prev, _ = alf.chain_head(finding_history_path(q, entry_id))
    revision = _revision_record(finding, 1, revising_actor, reason, run_id, prev)

    op = alf.Operation(q, "create_finding", [entry_id])
    op.append_line(hist_rel, revision)
    op.replace_file("findings/{}.json".format(entry_id), finding)
    op.replace_file("meta/entry-seq.json", {"last": nxt})
    op.commit()
    return entry_id


def update_finding(q, entry_id, mutate, revising_actor="alf-synth",
                   reason="update", run_id=None):
    """Append a new revision. `mutate` receives the current head record and returns
    the next record. Head is rewritten to equal the new revision's record."""
    head = load_finding(q, entry_id)
    if head is None:
        raise alf.AlfError("no finding {}".format(entry_id))
    nxt_record = dict(mutate(dict(head)))
    nxt_record["entry_id"] = entry_id
    nxt_record["alf_record_version"] = ALF_RECORD_VERSION
    hist_path = finding_history_path(q, entry_id)
    revisions = _read_history(q, entry_id)
    revision_no = revisions[-1]["revision_no"] + 1 if revisions else 1
    prev, _ = alf.chain_head(hist_path)
    revision = _revision_record(nxt_record, revision_no, revising_actor, reason,
                                run_id, prev)
    op = alf.Operation(q, "update_finding", [entry_id, str(revision_no)])
    op.append_line("findings/history/{}.jsonl".format(entry_id), revision)
    op.replace_file("findings/{}.json".format(entry_id), nxt_record)
    op.commit()
    return revision_no


def load_finding(q, entry_id):
    import json as _j
    path = finding_head_path(q, entry_id)
    if not os.path.exists(path):
        return None
    with open(path, encoding="utf-8") as fh:
        return _j.load(fh)


def _read_history(q, entry_id):
    records, _ = alf._read_valid_lines(finding_history_path(q, entry_id))
    return records


def rebuild_head(q, entry_id):
    """Rebuild the head from the revision log: the last revision's record. Returns
    the canonical bytes (used to prove head == rebuild byte-for-byte)."""
    revisions = _read_history(q, entry_id)
    if not revisions:
        raise alf.AlfError("no history for {}".format(entry_id))
    return alf.canonical_bytes(revisions[-1]["record"]) + b"\n"


def head_equals_rebuild(q, entry_id):
    with open(finding_head_path(q, entry_id), "rb") as fh:
        head_bytes = fh.read()
    return head_bytes == rebuild_head(q, entry_id)


def list_findings(q):
    d = alf._p(q, "findings")
    out = []
    if not os.path.isdir(d):
        return out
    import json as _j
    for name in sorted(os.listdir(d)):
        if name.endswith(".json"):
            with open(os.path.join(d, name), encoding="utf-8") as fh:
                out.append(_j.load(fh))
    return out


# --------------------------------------------------------------------------- #
# dedup-policy-v1 (packet section 9): proposal-based, never silent for protected
# classes.
# --------------------------------------------------------------------------- #
DEDUP_STOPWORDS = sorted({
    "the", "a", "an", "and", "or", "of", "to", "in", "is", "are", "was", "were",
    "that", "this", "it", "its", "for", "on", "with", "as", "by", "at", "be",
    "not", "no", "when", "which", "from", "into", "than", "then", "so",
})
DEDUP_POLICY_V1 = {
    "policy_version": "dedup-policy-v1",
    "normalization": ("ascii-lowercase the root_cause; split on every "
                      "non-alphanumeric character except underscore; drop the "
                      "stopword list; signature is the sorted unique token set"),
    "stopwords": DEDUP_STOPWORDS,
    "thresholds": {"exact": "0.90", "jaccard_high": "0.80", "jaccard_mid": "0.60"},
}
PROTECTED_FAILURE_CLASSES = {"authority_bypass_risk", "durable_record_integrity"}
PROTECTED_IMPACT_AXES = ("security_impact", "authority_integrity_impact",
                         "durable_record_integrity_impact")


def dedup_policy_path(q):
    return alf._p(q, "meta", "dedup-policy-v1.json")


def materialize_dedup_policy(q):
    alf.ensure_layout(q)
    path = dedup_policy_path(q)
    want = alf.canonical_bytes(DEDUP_POLICY_V1) + b"\n"
    if os.path.exists(path):
        with open(path, "rb") as fh:
            if fh.read() != want:
                raise alf.IntegrityHalt("dedup-policy-v1.json diverges; refusing")
        return
    with alf.cwl.write_token(q, purpose="alf-dedup-policy"):
        alf._replace_bytes_fsync(path, want)


def dedup_signature(root_cause):
    toks = re.split(r"[^a-z0-9_]+", (root_cause or "").lower())
    stop = set(DEDUP_STOPWORDS)
    return sorted({t for t in toks if t and t not in stop})


def _jaccard(a, b):
    sa, sb = set(a), set(b)
    if not sa and not sb:
        return 1.0
    return len(sa & sb) / len(sa | sb)


def is_protected(finding):
    if finding.get("failure_class") in PROTECTED_FAILURE_CLASSES:
        return True
    return any(int(finding.get(a, 0) or 0) >= 2 for a in PROTECTED_IMPACT_AXES)


def propose_dedup(q, finding):
    """Highest-confidence duplicate_of proposal for `finding` against the store,
    or None. Never auto-merges; protected-class pairs are flagged so the caller
    holds them for the operator (silent-merge prohibition, packet s9)."""
    sig = dedup_signature(finding.get("root_cause", ""))
    key = (finding.get("subsystem"), finding.get("failure_class"))
    best = None
    for other in list_findings(q):
        if other.get("entry_id") == finding.get("entry_id"):
            continue
        if (other.get("subsystem"), other.get("failure_class")) != key:
            continue
        osig = dedup_signature(other.get("root_cause", ""))
        if osig == sig:
            conf = "0.90"
        else:
            j = _jaccard(sig, osig)
            conf = "0.80" if j >= 0.80 else ("0.60" if j >= 0.60 else None)
        if conf is None:
            continue
        if best is None or conf > best["confidence"]:
            best = {"duplicate_of": other["entry_id"], "confidence": conf,
                    "relationship": "duplicate_of", "proposed": True,
                    "dedup_policy_version": "dedup-policy-v1",
                    "protected": is_protected(finding) or is_protected(other)}
    return best


# --------------------------------------------------------------------------- #
# Attribution ledger + occurrence attribution (packet sections 8, 11, 13)
# --------------------------------------------------------------------------- #
_WASTE_FROM_METRIC = {
    "cumulative_operator_minutes": "operator_minutes",
    "cumulative_execution_delay": "execution_delay_seconds",
    "cumulative_token_estimate": "token_estimate",
    "cumulative_api_attempts_wasted": "api_attempts",
    "cumulative_tool_attempts_wasted": "tool_attempts",
    "cumulative_council_attempts_wasted": "council_attempts",
}


def attribution_id(occurrence_id, entry_id, attribution_type):
    return "att-" + alf.content_sha256({
        "occurrence_id": occurrence_id, "entry_id": entry_id,
        "attribution_type": attribution_type})[:16]


def _ledger_records(q):
    recs, _ = alf._read_valid_lines(alf.ledger_path(q))
    return recs


def ledger_has(q, att_id):
    return any(r.get("attribution_id") == att_id for r in _ledger_records(q))


def _runs_attributed(q, entry_id):
    return {r.get("run_id") for r in _ledger_records(q)
            if r.get("entry_id") == entry_id}


def _write_finding_revision(q, entry_id, nxt_record, reason, run_id, actor,
                            ledger_lines=None, op_kind="update_finding"):
    """Append a finding revision + head + optional ledger lines in ONE journal
    transaction, so a counter update and its ledger attribution are atomic."""
    revisions = _read_history(q, entry_id)
    revision_no = revisions[-1]["revision_no"] + 1 if revisions else 1
    prev, _ = alf.chain_head(finding_history_path(q, entry_id))
    revision = _revision_record(nxt_record, revision_no, actor, reason, run_id, prev)
    op = alf.Operation(q, op_kind, [entry_id, str(revision_no)])
    op.append_line("findings/history/{}.jsonl".format(entry_id), revision)
    op.replace_file("findings/{}.json".format(entry_id), nxt_record)
    for ll in (ledger_lines or []):
        op.append_line("attributions/ledger.jsonl", ll)
    op.commit()
    return revision_no


def _fold_metrics(record, metrics):
    for cum, src in _WASTE_FROM_METRIC.items():
        v = (metrics or {}).get(src)
        if v:
            record[cum] = int(record.get(cum, 0) or 0) + int(v)


def _iv_from_finding(f):
    iv = {"risk_activity": "historical", "failure_class": f.get("failure_class")}
    for axis in ("authority_integrity_impact", "durable_record_integrity_impact",
                 "operator_time_impact", "execution_delay_impact",
                 "token_api_compute_impact"):
        iv[axis] = int(f.get(axis, 0) or 0)
    return iv


def attribute_occurrence(q, entry_id, occurrence, attribution_type):
    """Fold one occurrence's metrics into a finding EXACTLY once (idempotent via
    the ledger). attribution_type in initial_evidence|recurrence|regression|waste."""
    occ_id = occurrence["occurrence_id"]
    run_id = occurrence.get("run_id")
    att_id = attribution_id(occ_id, entry_id, attribution_type)
    if ledger_has(q, att_id):
        return {"attributed": False, "attribution_id": att_id, "reason": "idempotent"}
    head = load_finding(q, entry_id)
    if head is None:
        raise alf.AlfError("no finding {}".format(entry_id))
    is_new_run = run_id not in _runs_attributed(q, entry_id)
    nxt = dict(head)
    if attribution_type in ("recurrence", "regression"):
        nxt["occurrence_count"] = int(nxt.get("occurrence_count", 0) or 0) + 1
    if is_new_run:
        nxt["affected_run_count"] = int(nxt.get("affected_run_count", 0) or 0) + 1
    if occurrence.get("captured_at"):
        nxt["last_seen_at"] = occurrence["captured_at"]
    _fold_metrics(nxt, occurrence.get("metrics"))
    ledger_line = {
        "alf_record_version": ALF_RECORD_VERSION, "attribution_id": att_id,
        "occurrence_id": occ_id, "observation_id": occurrence.get("observation_id"),
        "entry_id": entry_id, "attribution_type": attribution_type,
        "at": alf.now_iso(), "run_id": run_id}
    rn = _write_finding_revision(q, entry_id, nxt,
                                 "attribute:{}".format(attribution_type), run_id,
                                 "alf-synth", [ledger_line], op_kind="attribute")
    return {"attributed": True, "attribution_id": att_id, "revision_no": rn,
            "new_run": is_new_run}


# --------------------------------------------------------------------------- #
# Recurrence + regression (packet sections 11, 12)
# --------------------------------------------------------------------------- #
RELEASED_STATES = ("RELEASED", "MONITORING")


def set_release_baseline(q, entry_id):
    """Persist release_baseline the first time a finding reaches RELEASED (s12)."""
    head = load_finding(q, entry_id)
    if head is None or head.get("release_baseline"):
        return
    baseline = {"tier": head.get("priority_tier"),
                "score": head.get("priority_score"),
                "priority_model_version": "priority-model-v1", "at": alf.now_iso()}
    _write_finding_revision(q, entry_id, dict(head, release_baseline=baseline),
                            "release_baseline", None, "alf-synth")


def record_recurrence(q, entry_id, occurrence):
    """A repeat occurrence of an existing finding. RELEASED/MONITORING findings
    become regressions (reopen + floor); otherwise a plain recurrence."""
    head = load_finding(q, entry_id)
    if head is None:
        raise alf.AlfError("no finding {}".format(entry_id))
    if head.get("status") in RELEASED_STATES:
        return record_regression(q, entry_id, occurrence)
    return attribute_occurrence(q, entry_id, occurrence, "recurrence")


def record_regression(q, entry_id, occurrence):
    """Reopen a released/monitored finding to PRIORITIZED with the tier-and-score
    floor from its release_baseline (packet section 12). Idempotent per run."""
    occ_id = occurrence["occurrence_id"]
    run_id = occurrence.get("run_id")
    att_id = attribution_id(occ_id, entry_id, "regression")
    if ledger_has(q, att_id):
        return {"attributed": False, "attribution_id": att_id, "reason": "idempotent"}
    head = load_finding(q, entry_id)
    baseline = head.get("release_baseline") or {}
    nxt = dict(head)
    nxt["occurrence_count"] = int(nxt.get("occurrence_count", 0) or 0) + 1
    if run_id not in _runs_attributed(q, entry_id):
        nxt["affected_run_count"] = int(nxt.get("affected_run_count", 0) or 0) + 1
    if occurrence.get("captured_at"):
        nxt["last_seen_at"] = occurrence["captured_at"]
    _fold_metrics(nxt, occurrence.get("metrics"))
    nxt["status"] = "PRIORITIZED"
    recomputed_tier = assign_tier(_iv_from_finding(nxt))["tier"]
    baseline_tier = baseline.get("tier")
    eff_tier = min(recomputed_tier, baseline_tier) if baseline_tier is not None \
        else recomputed_tier
    recomputed_score = compute_score(nxt, occurrence_count=nxt["occurrence_count"],
                                     regression_flag=1)
    if baseline_tier is not None and eff_tier == baseline_tier \
            and baseline.get("score") is not None:
        eff_score = max(recomputed_score, baseline["score"])
    else:
        eff_score = recomputed_score
    nxt["priority_tier"] = eff_tier
    nxt["priority_score"] = eff_score
    rel = list(nxt.get("related_entries", []))
    rel.append({"entry_id": entry_id, "relationship": "regression_of"})
    nxt["related_entries"] = rel
    nxt["tier_decision"] = {
        "recomputed_tier": recomputed_tier, "recomputed_score": recomputed_score,
        "baseline_tier": baseline_tier, "baseline_score": baseline.get("score"),
        "effective_tier": eff_tier, "effective_score": eff_score,
        "regression_floor_applied": True, "computed_at": alf.now_iso()}
    ledger_line = {
        "alf_record_version": ALF_RECORD_VERSION, "attribution_id": att_id,
        "occurrence_id": occ_id, "observation_id": occurrence.get("observation_id"),
        "entry_id": entry_id, "attribution_type": "regression",
        "at": alf.now_iso(), "run_id": run_id}
    rn = _write_finding_revision(q, entry_id, nxt, "regression_reopen", run_id,
                                 "alf-synth", [ledger_line], op_kind="regression")
    return {"attributed": True, "attribution_id": att_id, "revision_no": rn,
            "effective_tier": eff_tier, "effective_score": eff_score}


# --------------------------------------------------------------------------- #
# CLI (read-only + model materialization; disposition/synthesis verbs later)
# --------------------------------------------------------------------------- #
def _emit(obj):
    import json as _j
    sys.stdout.write(_j.dumps(obj, ensure_ascii=False) + "\n")


def _cmd_model(args):
    _emit({"ok": True, "command": "model",
           "priority_model_sha256": materialize_model(args.queue_root)})
    return 0


def _cmd_list(args):
    _emit({"ok": True, "command": "list-findings",
           "findings": list_findings(args.queue_root)})
    return 0


def build_parser():
    p = argparse.ArgumentParser(
        prog="clearwright_alf_synth",
        description="ALF Phase 1 synthesis (findings, scoring, model).")
    sub = p.add_subparsers(dest="command", required=True)
    m = sub.add_parser("model", help="Materialize + hash priority-model-v1.")
    m.add_argument("queue_root")
    m.set_defaults(func=_cmd_model)
    lf = sub.add_parser("list-findings", help="List finding head records.")
    lf.add_argument("queue_root")
    lf.set_defaults(func=_cmd_list)
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
