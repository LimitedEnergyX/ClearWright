#!/usr/bin/env python3
"""tools/clearwright_alf_delta.py: Run Improvement Delta (packet section 17).

At a run boundary ALF writes alf/deltas/rid-<run>.json plus an IMMUTABLE,
self-sufficient input snapshot rid-<run>.input.json persisted at first generation.
The delta's deterministic content is a PURE FUNCTION of that snapshot: reruns
resolve the stored snapshot, re-read each content-addressed reference from the
append-only stores (hash-verifying it, fail-closed on divergence), and recompute
- equal is a verified no-op; a genuine difference is a REFUSED divergent rewrite
(Tier 1). generated_at and the anchors block are fixed at first generation and
preserved verbatim. An empty delta is still written so the missing-delta verifier
can prove every terminal governed run has one.
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import clearwright_alf as alf  # noqa: E402
import clearwright_alf_synth as syn  # noqa: E402

GENESIS = "0" * 64
ALF_RECORD_VERSION = alf.ALF_RECORD_VERSION


def delta_path(q, run_id):
    return alf._contained(
        alf._p(q, "deltas", "rid-{}.json".format(alf.safe_id(run_id, "run_id"))), q)


def snapshot_path(q, run_id):
    return alf._contained(alf._p(
        q, "deltas", "rid-{}.input.json".format(alf.safe_id(run_id, "run_id"))), q)


def _unique_by(records, id_field):
    """Build an id->record map, failing closed on a DUPLICATE id in an append-only
    store (round-3 HIGH): a duplicate could shadow the snapshot's referenced row."""
    out = {}
    for r in records:
        k = r.get(id_field)
        if k in out:
            raise alf.IntegrityHalt(
                "duplicate {} {!r} in append-only store".format(id_field, k))
        out[k] = r
    return out


def _verify_snapshot_refs(q, snapshot):
    """Verify every content-addressed reference the immutable snapshot records still
    resolves to bytes with the recorded hash (HIGH-4, round-3): the referenced chained
    stores are first RE-AUTHENTICATED by verify_chain (catching an altered field with a
    retained line hash), DUPLICATE ids fail closed, then each snapshot reference is
    matched and hash-checked. Finding revisions are hash-verified (and duplicate
    revision numbers rejected) in _resolve_finding_revision."""
    for path in (alf.occurrences_path(q), alf.index_path(q), alf.ledger_path(q)):
        chain = alf.verify_chain(path)
        if chain:
            raise alf.IntegrityHalt("delta ref store chain break: " + "; ".join(chain))
    occ_by_id = _unique_by(
        alf._read_valid_lines(alf.occurrences_path(q))[0], "occurrence_id")
    for o in snapshot.get("occurrences", []):
        live = occ_by_id.get(o["occurrence_id"])
        if live is None or live.get("line_sha256") != o.get("line_sha256"):
            raise alf.IntegrityHalt("delta snapshot occurrence {} missing or altered"
                                    .format(o["occurrence_id"]))
    idx_by_id = _unique_by(
        alf._read_valid_lines(alf.index_path(q))[0], "observation_id")
    for ob in snapshot.get("observations", []):
        live = idx_by_id.get(ob["observation_id"])
        if live is None or live.get("sha256") != ob.get("sha256"):
            raise alf.IntegrityHalt("delta snapshot observation {} missing or altered "
                                    "in index".format(ob["observation_id"]))
        ofile = alf.observation_file(q, ob["observation_id"])
        if not os.path.exists(ofile):
            raise alf.IntegrityHalt("delta snapshot observation {} file missing"
                                    .format(ob["observation_id"]))
        with open(ofile, "rb") as fh:
            if alf.sha256_hex(fh.read()) != ob["sha256"]:
                raise alf.IntegrityHalt("delta snapshot observation {} bytes diverge"
                                        .format(ob["observation_id"]))
    led_by_id = _unique_by(
        alf._read_valid_lines(alf.ledger_path(q))[0], "attribution_id")
    for a in snapshot.get("attributions", []):
        live = led_by_id.get(a["attribution_id"])
        if live is None or live.get("line_sha256") != a.get("line_sha256"):
            raise alf.IntegrityHalt("delta snapshot attribution {} missing or altered"
                                    .format(a["attribution_id"]))
    # Re-authenticate every referenced finding-history chain too (round-4 HIGH); the
    # per-revision revision_sha256 is recomputed in _resolve_finding_revision.
    for fr in snapshot.get("finding_revisions", []):
        chain = alf.verify_chain(syn.finding_history_path(q, fr["entry_id"]))
        if chain:
            raise alf.IntegrityHalt("delta finding-history chain break for {}: {}"
                                    .format(fr["entry_id"], "; ".join(chain)))


def _delta_chain_path(q):
    return alf._p(q, "meta", "delta-chain.json")


# --------------------------------------------------------------------------- #
# Immutable input snapshot (packet section 17)
# --------------------------------------------------------------------------- #
def _build_snapshot(q, run_id):
    occ_recs, _ = alf._read_valid_lines(alf.occurrences_path(q))
    occurrences = sorted(
        [{"occurrence_id": o["occurrence_id"], "observation_id": o["observation_id"],
          "run_id": o["run_id"], "line_sha256": o["line_sha256"]}
         for o in occ_recs if o.get("run_id") == run_id],
        key=lambda x: x["occurrence_id"])
    idx, _ = alf._read_valid_lines(alf.index_path(q))
    idx_by_id = {r["observation_id"]: r for r in idx}
    obs_ids = sorted({o["observation_id"] for o in occurrences})
    observations = [{"observation_id": oid, "sha256": idx_by_id[oid]["sha256"]}
                    for oid in obs_ids if oid in idx_by_id]
    led, _ = alf._read_valid_lines(alf.ledger_path(q))
    attributions = sorted(
        [{"attribution_id": a["attribution_id"],
          "attribution_type": a["attribution_type"], "line_sha256": a["line_sha256"]}
         for a in led if a.get("run_id") == run_id
         and a.get("attribution_type") != "delta_report"],
        key=lambda x: x["attribution_id"])
    finding_revisions = []
    baselines = []
    for f in syn.list_findings(q):
        eid = f["entry_id"]
        revs = syn._read_history(q, eid)
        run_revs = [r for r in revs if r.get("run_id") == run_id]
        if not run_revs:
            continue
        endpoint = max(run_revs, key=lambda r: r["revision_no"])
        finding_revisions.append({"entry_id": eid,
                                  "revision_no": endpoint["revision_no"],
                                  "revision_sha256": endpoint["revision_sha256"]})
        first_run_rn = min(r["revision_no"] for r in run_revs)
        prior = [r for r in revs if r["revision_no"] < first_run_rn]
        if prior:
            b = prior[-1]["record"]
            baselines.append({
                "entry_id": eid, "baseline_tier": b.get("priority_tier"),
                "baseline_score": b.get("priority_score"),
                "baseline_model_version": "priority-model-v1",
                "baseline_status": b.get("status"),
                "baseline_cumulative_waste": {c: int(b.get(c, 0) or 0)
                                              for c in syn.WASTE_COUNTERS}})
        else:
            baselines.append({
                "entry_id": eid, "baseline_tier": None, "baseline_score": None,
                "baseline_model_version": "priority-model-v1",
                "baseline_status": None,
                "baseline_cumulative_waste": {c: 0 for c in syn.WASTE_COUNTERS}})
    finding_revisions.sort(key=lambda x: x["entry_id"])
    baselines.sort(key=lambda x: x["entry_id"])
    return {
        "snapshot_version": 2, "run_id": run_id,
        "membership_rule": ("occurrences, attributions, and finding revisions whose "
                            "run_id equals this run_id; delta_report attributions "
                            "excluded; each set canonically ordered by id"),
        "occurrences": occurrences, "observations": observations,
        "attributions": attributions, "finding_revisions": finding_revisions,
        "baselines": baselines}


# --------------------------------------------------------------------------- #
# Deterministic derivation (pure function of the snapshot + hash-verified refs)
# --------------------------------------------------------------------------- #
def _resolve_finding_revision(q, eid, rn, expected_sha):
    matches = [r for r in syn._read_history(q, eid) if r.get("revision_no") == rn]
    if len(matches) != 1:  # missing OR duplicate revision_no -> fail closed (round-3)
        raise alf.IntegrityHalt("finding {} rev {}: {} matching revisions (expected "
                                "exactly 1)".format(eid, rn, len(matches)))
    r = matches[0]
    # RECOMPUTE revision_sha256 over the revision payload (minus its own hash and the
    # outer chain fields) so an altered record that retained a stale revision_sha256
    # AND recomputed the outer line chain is still caught (round-4 HIGH).
    body = {k: v for k, v in r.items()
            if k not in ("revision_sha256", "prev_line_sha256", "line_sha256")}
    if alf.sha256_hex(alf.canonical_bytes(body)) != r.get("revision_sha256"):
        raise alf.IntegrityHalt("finding {} rev {} revision_sha256 does not "
                                "authenticate its record".format(eid, rn))
    if r.get("revision_sha256") != expected_sha:
        raise alf.IntegrityHalt("finding {} rev {} sha divergent from snapshot"
                                .format(eid, rn))
    return r["record"]


def _derive(q, snapshot):
    _verify_snapshot_refs(q, snapshot)  # HIGH-4: fail closed on any divergent ref
    run_id = snapshot["run_id"]
    idx, _ = alf._read_valid_lines(alf.index_path(q))
    first_seen = {r["observation_id"]: r["run_id"] for r in idx}
    new_observations = sorted(
        o["observation_id"] for o in snapshot["observations"]
        if first_seen.get(o["observation_id"]) == run_id)
    baseline_by = {b["entry_id"]: b for b in snapshot["baselines"]}
    new_findings, findings_priority_changed, cumulative_waste_changes = [], [], []
    for fr in snapshot["finding_revisions"]:
        eid = fr["entry_id"]
        rec = _resolve_finding_revision(q, eid, fr["revision_no"], fr["revision_sha256"])
        b = baseline_by.get(eid, {})
        if b.get("baseline_tier") is None and b.get("baseline_status") is None:
            new_findings.append(eid)
        old = (b.get("baseline_tier"), b.get("baseline_score"))
        new = (rec.get("priority_tier"), rec.get("priority_score"))
        if old != new:
            findings_priority_changed.append({
                "entry_id": eid, "old_tier": old[0], "old_score": old[1],
                "new_tier": new[0], "new_score": new[1],
                "model_version": "priority-model-v1", "reason": "synthesis"})
        per = {}
        base_w = b.get("baseline_cumulative_waste") or {}
        for c in syn.WASTE_COUNTERS:
            nv = int(rec.get(c, 0) or 0)
            ov = int(base_w.get(c, 0) or 0)
            if nv != ov:
                per[c] = {"delta": nv - ov, "new_total": nv}
        if per:
            cumulative_waste_changes.append({"entry_id": eid, "per_counter": per})
    led, _ = alf._read_valid_lines(alf.ledger_path(q))
    led_by_line = {a["line_sha256"]: a for a in led}
    regressions_detected = []
    for a in snapshot["attributions"]:
        if a["attribution_type"] == "regression":
            row = led_by_line.get(a["line_sha256"])
            if row:
                regressions_detected.append({
                    "entry_id": row["entry_id"],
                    "evidence_refs": [row.get("observation_id")]})
    return {
        "new_observations": new_observations,
        "new_findings": sorted(new_findings),
        "observations_merged_into_existing": [],
        "findings_priority_changed": sorted(findings_priority_changed,
                                            key=lambda x: x["entry_id"]),
        "released_fixes_revalidated": [],
        "regressions_detected": sorted(regressions_detected,
                                       key=lambda x: x["entry_id"]),
        "items_requiring_operator_review": [],
        "cumulative_waste_changes": sorted(cumulative_waste_changes,
                                           key=lambda x: x["entry_id"])}


def _content_with_meta(content, run_id, work_item_id):
    d = {"alf_record_version": ALF_RECORD_VERSION, "run_id": run_id,
         "work_item_id": work_item_id}
    d.update(content)
    return d


# --------------------------------------------------------------------------- #
# Anchors (packet sections 8, 17): pre-transaction heads, acyclic.
# --------------------------------------------------------------------------- #
def _structured_anchor(path):
    h, c = alf.chain_head(path)
    return {"head_line_sha256": h, "line_count": c}


def _findings_revision_heads_sha256(q):
    entries = []
    for f in syn.list_findings(q):
        eid = f["entry_id"]
        h, c = alf.chain_head(syn.finding_history_path(q, eid))
        entries.append({"entry_id": eid, "history_line_count": c,
                        "head_line_sha256": h})
    entries.sort(key=lambda x: x["entry_id"])
    return alf.sha256_hex(alf.canonical_bytes(entries))


def _last_delta_anchors(q):
    path = _delta_chain_path(q)
    if os.path.exists(path):
        with open(path, encoding="utf-8") as fh:
            return json.load(fh).get("last_anchors_sha256", GENESIS)
    return GENESIS


def _anchors(q, input_snapshot_sha256):
    return {
        "observations_index": _structured_anchor(alf.index_path(q)),
        "ledger": _structured_anchor(alf.ledger_path(q)),
        "journal": _structured_anchor(alf.journal_path(q)),
        "findings_revision_heads_sha256": _findings_revision_heads_sha256(q),
        "input_snapshot_sha256": input_snapshot_sha256,
        "prev_delta_anchors_sha256": _last_delta_anchors(q)}


# --------------------------------------------------------------------------- #
# Generation + idempotent rerun
# --------------------------------------------------------------------------- #
def generate_delta(q, run_id, work_item_id=None):
    alf.ensure_layout(q)
    spath, dpath = snapshot_path(q, run_id), delta_path(q, run_id)
    if os.path.exists(spath):
        if not os.path.exists(dpath):
            raise alf.AlfError("run {}: input snapshot exists but the delta file is "
                               "missing (inconsistent state; fail closed)".format(run_id))
        with open(spath, encoding="utf-8") as fh:
            snapshot = json.load(fh)
        recomputed = _content_with_meta(_derive(q, snapshot), run_id, work_item_id)
        with open(dpath, encoding="utf-8") as fh:
            stored = json.load(fh)
        stored_content = {k: v for k, v in stored.items()
                          if k not in ("generated_at", "anchors")}
        if stored_content == recomputed:
            return {"status": "noop", "run_id": run_id}
        raise alf.IntegrityHalt(
            "Run Improvement Delta rerun divergence for run {} against its immutable "
            "snapshot (Tier 1 durable-record-integrity)".format(run_id))
    snapshot = _build_snapshot(q, run_id)
    input_snapshot_sha256 = alf.sha256_hex(alf.canonical_bytes(snapshot) + b"\n")
    anchors = _anchors(q, input_snapshot_sha256)
    delta = _content_with_meta(_derive(q, snapshot), run_id, work_item_id)
    delta["generated_at"] = alf.now_iso()
    delta["anchors"] = anchors
    anchors_sha = alf.sha256_hex(alf.canonical_bytes(anchors))
    op = alf.Operation(q, "delta_generate", [run_id])
    op.replace_file("deltas/rid-{}.input.json".format(run_id), snapshot)
    op.replace_file("deltas/rid-{}.json".format(run_id), delta)
    op.replace_file("meta/delta-chain.json", {"last_anchors_sha256": anchors_sha})
    op.commit()
    return {"status": "generated", "run_id": run_id,
            "input_snapshot_sha256": input_snapshot_sha256,
            "anchors_sha256": anchors_sha}


def load_delta(q, run_id):
    if not os.path.exists(delta_path(q, run_id)):
        return None
    with open(delta_path(q, run_id), encoding="utf-8") as fh:
        return json.load(fh)


def missing_delta_verifier(q, terminal_run_ids):
    """Report every terminal governed run lacking a delta as a Tier 1
    lifecycle_failure candidate (packet section 17)."""
    return [{"run_id": r, "tier": 1, "failure_class": "lifecycle_failure",
             "reason": "terminal governed run has no Run Improvement Delta"}
            for r in terminal_run_ids if not os.path.exists(delta_path(q, r))]


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #
def _emit(obj):
    sys.stdout.write(json.dumps(obj, ensure_ascii=False) + "\n")


def _cmd_generate(args):
    _emit({"ok": True, "command": "delta-generate",
           **generate_delta(args.queue_root, args.run_id, args.work_item_id)})
    return 0


def _cmd_show(args):
    d = load_delta(args.queue_root, args.run_id)
    _emit({"ok": d is not None, "command": "delta-show", "delta": d})
    return 0 if d is not None else 1


def build_parser():
    p = argparse.ArgumentParser(
        prog="clearwright_alf_delta",
        description="ALF Run Improvement Delta generation (packet section 17).")
    sub = p.add_subparsers(dest="command", required=True)
    g = sub.add_parser("generate", help="Generate (or idempotently verify) a delta.")
    g.add_argument("queue_root")
    g.add_argument("run_id")
    g.add_argument("--work-item-id", default=None)
    g.set_defaults(func=_cmd_generate)
    s = sub.add_parser("show", help="Show a delta.")
    s.add_argument("queue_root")
    s.add_argument("run_id")
    s.set_defaults(func=_cmd_show)
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
