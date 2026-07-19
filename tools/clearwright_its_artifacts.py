"""tools/clearwright_its_artifacts.py: the ClearWright derived-artifact registry
for the INTERNAL_TECHNICAL_STANDARD (ITS) lane.

A council's multi-round technical self-review earns ITS provenance only when the
generated content it carries forward (reviewer findings, Claude's reconciliation,
and the per-round summaries the engine folds into a follow-up packet) is a
hash-bound, pre-persistence-scanned derived artifact. This module is the durable
home of those artifacts. It records ONLY content-free audit fields on a failed
scan (never the scanned text), captures the exact content hash so a later round
can prove the reused content was not mutated, and re-scans on reuse so nothing
that has since become residual-sensitive can re-enter a packet.

Storage (per council, under the queue root):
  review_councils/<council_id>/its_artifacts/<artifact_id>.json   one record
  review_councils/<council_id>/its_artifacts.json                 ordered index

Every write goes through clearwright_review_council._atomic_write_json (imported
late, inside functions, so this module and the council engine can import each
other without a cycle). The guard owns all scanning and hashing; this module owns
no transport and imports no socket/urllib/subprocess primitive.

Style: str.format (no f-strings); content-free errors (a refusal carries only
category names, counts, hashes, and reason codes — never the scanned content).
All fail-closed paths raise clearwright_egress_guard.EgressBlocked with existing
reason codes only.
"""
from __future__ import annotations

import json
import os

import clearwright_egress_guard as guard
import clearwright_message as cwm

# kind -> the short token embedded in an artifact id. A kind outside this map is
# a caller programming error and fails closed.
_KIND_SHORT = {
    "gpt_finding": "gptf",
    "codex_finding": "cdxf",
    "reconciliation": "recon",
    "round_summary": "rsum",
}


def _cwrc():
    """Late import: the council engine imports this module, so importing it at
    module top would create a cycle. Every writer resolves it here instead."""
    import clearwright_review_council as cwrc
    return cwrc


def _artifacts_dir(root, council_id):
    return os.path.join(_cwrc().council_dir(root, council_id), "its_artifacts")


def _artifact_path(root, council_id, artifact_id):
    return os.path.join(_artifacts_dir(root, council_id), artifact_id + ".json")


def _index_path(root, council_id):
    return os.path.join(_cwrc().council_dir(root, council_id), "its_artifacts.json")


def _read_index(root, council_id):
    try:
        with open(_index_path(root, council_id), encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        data = None
    if not isinstance(data, dict) or not isinstance(data.get("artifacts"), list):
        return {"artifacts": []}
    return data


def _persist(root, council_id, artifact_id, record):
    cwrc = _cwrc()
    cwrc._atomic_write_json(_artifact_path(root, council_id, artifact_id), record)
    index = _read_index(root, council_id)
    if artifact_id not in index["artifacts"]:
        index["artifacts"].append(artifact_id)
        cwrc._atomic_write_json(_index_path(root, council_id), index)


def _scan(content, loaded):
    """Pre-persistence residue scan. Returns (passed, category_counts). ``passed``
    is decided by the HARD categories only (unicode_confusable is advisory), and
    the counts are content-free. A scanner exception fails closed (not passed)."""
    _safe, findings = guard.redact_for_persistence(content, loaded)
    hard = {k: v for k, v in findings.items() if k != "unicode_confusable"}
    return (not hard), findings


def register(root, council_id, *, kind, producer, round_no, phase, content,
             scaffold_version, source_ids, loaded=None):
    """Register one derived artifact, scanning its content BEFORE persistence.

    Deterministic and idempotent: the id is a pure function of (kind, round,
    content), so re-registering identical content is a no-op that returns the
    existing record. On a hard scan hit the record is persisted WITHOUT the
    content field (scan.passed False, sensitivity_result "sensitive") and the
    content is written NOWHERE; only a clean scan stores the content and earns
    the internal_technical_standard classification. Returns the record."""
    loaded = loaded or guard.load_policy()
    short = _KIND_SHORT.get(kind)
    if short is None:
        raise guard.EgressBlocked("its_component_mismatch", {"detail": "bad_kind"})
    text = content or ""
    content_sha = guard._sha256_bytes(text.encode("utf-8"))
    artifact_id = "its-{}-r{:02d}-{}".format(short, int(round_no), content_sha[:12])
    existing = load(root, council_id, artifact_id)
    if existing is not None:
        return existing
    passed, category_counts = _scan(text, loaded)
    record = {
        "artifact_id": artifact_id,
        "kind": kind,
        "producer": producer,
        "council_id": council_id,
        "round": int(round_no),
        "phase": phase,
        "policy_version": loaded["policy_version"],
        "policy_sha256": loaded["policy_sha256"],
        "scaffold_version": scaffold_version,
        "content_sha256": content_sha,
        "scan": {"passed": bool(passed), "category_counts": category_counts},
        "sensitivity_result": (guard.SENSITIVITY_ITS if passed
                               else guard.SENSITIVITY_SENSITIVE),
        "source_ids": list(source_ids or []),
        "created_at": cwm._now_iso(),
    }
    if passed:
        # Content is stored ONLY on a clean scan; a failed scan leaves the
        # durable record content-free (the counts above are the audit trail).
        record["content"] = text
    _persist(root, council_id, artifact_id, record)
    return record


def load(root, council_id, artifact_id):
    """Return the stored record, or None if it is absent or unreadable."""
    try:
        with open(_artifact_path(root, council_id, artifact_id), encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


def verify_for_reuse(root, council_id, artifact_id, loaded=None):
    """Fail-closed gate a prior-round artifact must pass BEFORE its content is
    folded into a later round's packet. Checks, in order: the record exists; its
    scan passed exactly True; its content is present; the content still hashes to
    the recorded content_sha256 (no mutation between persistence and reuse); and
    a fresh re-scan finds no hard residue (content that has since become
    residual-sensitive is refused). Returns the record on success."""
    record = load(root, council_id, artifact_id)
    if record is None:
        raise guard.EgressBlocked("its_component_missing", {"detail": "artifact_absent"})
    scan = record.get("scan") or {}
    if scan.get("passed") is not True:
        raise guard.EgressBlocked("its_generated_scan_failed", {"detail": "not_passed"})
    content = record.get("content")
    if not isinstance(content, str):
        raise guard.EgressBlocked("its_component_missing", {"detail": "content_absent"})
    if guard._sha256_bytes(content.encode("utf-8")) != record.get("content_sha256"):
        raise guard.EgressBlocked("its_component_mismatch", {"detail": "content_hash"})
    loaded = loaded or guard.load_policy()
    passed, _counts = _scan(content, loaded)
    if not passed:
        raise guard.EgressBlocked("its_generated_scan_failed", {"detail": "residue"})
    return record


def list_ids(root, council_id):
    """Ordered artifact ids (registration order) for this council."""
    return list(_read_index(root, council_id)["artifacts"])


# --------------------------------------------------------------------------- #
# Deterministic renderings. Pure functions of their arguments (no timestamps),
# so the same verdict/reconciliation/round always renders to the same bytes and
# hashes identically across rounds.
# --------------------------------------------------------------------------- #

def render_verdict(verdict):
    """Canonical rendering of a structured verdict for hashing and reuse."""
    return json.dumps(verdict, sort_keys=True, ensure_ascii=False,
                      separators=(",", ": "), indent=1)


def render_reconciliation(normalized):
    """Canonical rendering of a normalized reconciliation for hashing and reuse."""
    return json.dumps(normalized, sort_keys=True, ensure_ascii=False,
                      separators=(",", ": "), indent=1)


def _excerpt(items, n=5, width=160):
    """Bounded, content-free-safe excerpt block. Mirrors the excerpt helper of
    clearwright_review_council._augment_context byte-for-byte so a rendered
    round summary is continuous with the Phase-1 reviewer-facing format."""
    out = []
    for it in (items or [])[:n]:
        out.append("      - " + str(it)[:width])
    return "\n".join(out) if out else "      (none)"


def render_round_summary(round_no, gpt_verdict, codex_verdict, reconciliation=None):
    """Reproduce the per-round block of clearwright_review_council._augment_context
    (the "Round N:" header, each reviewer's verdict line plus required_changes and
    blocking_findings excerpts, and the reconciliation block) so a follow-up ITS
    packet carries the SAME reviewer-facing continuity the sensitive lane does.
    Pure function of its arguments; the surrounding framing sentences live in the
    fixed follow-up scaffold, not here."""
    lines = ["Round {}:".format(round_no)]
    for who, v in (("gpt", gpt_verdict), ("codex", codex_verdict)):
        if v:
            lines.append("  {} verdict={} confidence={:.2f} risk={}".format(
                who.upper(), v["verdict"], v["confidence"], v["risk_level"]))
            lines.append("    required_changes:")
            lines.append(_excerpt(v.get("required_changes")))
            lines.append("    blocking_findings:")
            lines.append(_excerpt(v.get("blocking_findings")))
        else:
            lines.append("  {}: no validated review (unavailable)".format(who.upper()))
    if reconciliation:
        lines.append("  Claude reconciliation:")
        lines.append("    revised_plan:")
        lines.append(_excerpt(reconciliation.get("revised_plan")))
        lines.append("    rejected_findings (with evidence):")
        lines.append(_excerpt([f.get("finding")
                               for f in reconciliation.get("rejected_findings", [])]))
        lines.append("    unresolved_blockers:")
        lines.append(_excerpt(reconciliation.get("unresolved_blockers")))
    return "\n".join(lines)
