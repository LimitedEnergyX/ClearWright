#!/usr/bin/env python3
"""
tools/clearwright_verdict.py: the shared structured-review contract for the
ClearWright Review Council.

GPT and Codex reviewers return the SAME logical structure so agreement can be
evaluated deterministically over fields, never over prose similarity. This
module is the single source of truth for that structure and its validation, so
the GPT adapter, the Codex adapter, and the council engine all agree exactly.

A verdict is a dict:

    {
      "reviewer": "gpt" | "codex",
      "verdict": "approve" | "approve_with_changes" | "revise" | "block",
      "confidence": 0.0 .. 1.0,
      "risk_level": "low" | "medium" | "high" | "critical",
      "blocking_findings": [ ... ],
      "required_changes": [ ... ],
      "nonblocking_findings": [ ... ],
      "disagreements": [ ... ],
      "assumptions": [ ... ],
      "questions": [ ... ],
      "recommended_plan": [ ... ],
      "summary": "substantive text"
    }

A reconciliation (Claude's, between rounds) is a dict:

    {
      "accepted_findings": [ ... ],
      "rejected_findings": [ {"finding": "", "reason": "", "evidence": [ ... ]}, ... ],
      "required_plan_changes": [ ... ],
      "revised_plan": [ ... ],
      "unresolved_blockers": [ ... ],
      "ready_to_proceed": bool,
      "summary": "text"
    }

Validation raises VerdictError with a clear message; it never prints secrets and
never invokes a model. Nothing here calls GPT or Codex.
"""
import json

VERDICTS = ("approve", "approve_with_changes", "revise", "block")
RISK_LEVELS = ("low", "medium", "high", "critical")
REVIEWERS = ("gpt", "codex")

# Verdicts that do NOT block agreement (an unresolved blocker or a revise/block
# verdict always prevents agreement regardless of any numeric score).
NON_BLOCKING_VERDICTS = ("approve", "approve_with_changes")

VERDICT_LIST_FIELDS = (
    "blocking_findings", "required_changes", "nonblocking_findings",
    "disagreements", "assumptions", "questions", "recommended_plan",
)
VERDICT_REQUIRED_FIELDS = ("reviewer", "verdict", "confidence", "risk_level",
                           "summary") + VERDICT_LIST_FIELDS

RECON_LIST_FIELDS = ("accepted_findings", "rejected_findings",
                     "required_plan_changes", "revised_plan",
                     "unresolved_blockers")
RECON_REQUIRED_FIELDS = RECON_LIST_FIELDS + ("ready_to_proceed", "summary")

# Per-item resolution dispositions. A resolution binds one specific final-round
# reviewer finding (by its ref, e.g. "gpt.required_changes[0]") to a disposition,
# so agreement cannot be reached by an unrelated or duplicated disposition count.
#
# blocked_by_capability means "the reviewer is RIGHT and the harness cannot
# satisfy the requirement". It is a first-class disposition for the per-item
# accounting, but it NEVER counts as resolved: any occurrence forces
# operator_required immediately, can never coexist with ready_to_proceed=true,
# and can never contribute to agreement_threshold_met.
RESOLUTION_DISPOSITIONS = ("accepted", "planned", "rejected", "blocked_by_capability")

MIN_SUMMARY_CHARS = 8


class VerdictError(ValueError):
    """A structured review or reconciliation failed validation."""


def extract_json_object(text):
    """Return the first JSON object found in text as a dict.

    Tolerant of a model wrapping JSON in prose or a ```json fenced block: it
    strips common code fences and, failing a direct parse, scans for the first
    balanced {...} span. Raises VerdictError if no JSON object can be parsed.
    """
    if isinstance(text, dict):
        return text
    if not text or not str(text).strip():
        raise VerdictError("empty reviewer output; nothing to parse")
    s = str(text).strip()
    # Strip a leading/trailing markdown code fence if present.
    if s.startswith("```"):
        s = s.split("\n", 1)[1] if "\n" in s else s
        if s.endswith("```"):
            s = s[: -3]
        s = s.strip()
        if s.lower().startswith("json"):
            s = s[4:].strip()
    try:
        obj = json.loads(s)
        if isinstance(obj, dict):
            return obj
    except ValueError:
        pass
    # Fall back to scanning for the first balanced object.
    start = s.find("{")
    while start != -1:
        depth = 0
        in_str = False
        esc = False
        for i in range(start, len(s)):
            c = s[i]
            if in_str:
                if esc:
                    esc = False
                elif c == "\\":
                    esc = True
                elif c == '"':
                    in_str = False
            else:
                if c == '"':
                    in_str = True
                elif c == "{":
                    depth += 1
                elif c == "}":
                    depth -= 1
                    if depth == 0:
                        candidate = s[start:i + 1]
                        try:
                            obj = json.loads(candidate)
                            if isinstance(obj, dict):
                                return obj
                        except ValueError:
                            break
        start = s.find("{", start + 1)
    raise VerdictError("no parseable JSON object in reviewer output")


def _as_list(value, field):
    if value is None:
        return []
    if not isinstance(value, list):
        raise VerdictError("field {!r} must be an array".format(field))
    return value


def validate_verdict(obj, reviewer=None):
    """Validate and normalize a structured verdict. Returns a new dict with all
    fields present and typed. Raises VerdictError on any violation.

    If `reviewer` is given, the verdict's reviewer must match it exactly (so a
    GPT adapter can never post a verdict that claims to be Codex, or vice versa).
    """
    if not isinstance(obj, dict):
        raise VerdictError("verdict must be a JSON object")

    who = str(obj.get("reviewer") or "").strip().lower()
    if who not in REVIEWERS:
        raise VerdictError("reviewer must be one of: {}".format(", ".join(REVIEWERS)))
    if reviewer is not None and who != reviewer:
        raise VerdictError("reviewer mismatch: expected {!r}, got {!r}".format(reviewer, who))

    verdict = str(obj.get("verdict") or "").strip().lower()
    if verdict not in VERDICTS:
        raise VerdictError("verdict must be one of: {}".format(", ".join(VERDICTS)))

    risk = str(obj.get("risk_level") or "").strip().lower()
    if risk not in RISK_LEVELS:
        raise VerdictError("risk_level must be one of: {}".format(", ".join(RISK_LEVELS)))

    raw_conf = obj.get("confidence")
    try:
        confidence = float(raw_conf)
    except (TypeError, ValueError):
        raise VerdictError("confidence must be a number between 0.0 and 1.0")
    if not (0.0 <= confidence <= 1.0):
        raise VerdictError("confidence must be between 0.0 and 1.0")

    summary = obj.get("summary")
    if not isinstance(summary, str) or len(summary.strip()) < MIN_SUMMARY_CHARS:
        raise VerdictError("summary must be substantive (>= {} chars)".format(MIN_SUMMARY_CHARS))

    normalized = {
        "reviewer": who,
        "verdict": verdict,
        "confidence": confidence,
        "risk_level": risk,
        "summary": summary.strip(),
    }
    for field in VERDICT_LIST_FIELDS:
        normalized[field] = _as_list(obj.get(field), field)
    return normalized


def validate_reconciliation(obj):
    """Validate and normalize Claude's reconciliation. Every rejected finding
    must carry non-empty evidence (dissent must never be summarized away without
    a reason). Returns a normalized dict. Raises VerdictError on any violation."""
    if not isinstance(obj, dict):
        raise VerdictError("reconciliation must be a JSON object")

    ready = obj.get("ready_to_proceed")
    if not isinstance(ready, bool):
        raise VerdictError("ready_to_proceed must be a boolean")

    summary = obj.get("summary")
    if not isinstance(summary, str) or len(summary.strip()) < MIN_SUMMARY_CHARS:
        raise VerdictError("reconciliation summary must be substantive")

    normalized = {"ready_to_proceed": ready, "summary": summary.strip()}
    for field in RECON_LIST_FIELDS:
        normalized[field] = _as_list(obj.get(field), field)

    rejected = []
    for entry in normalized["rejected_findings"]:
        if not isinstance(entry, dict):
            raise VerdictError("each rejected finding must be an object with finding/reason/evidence")
        finding = str(entry.get("finding") or "").strip()
        reason = str(entry.get("reason") or "").strip()
        evidence = _as_list(entry.get("evidence"), "evidence")
        if not finding or not reason:
            raise VerdictError("a rejected finding requires both 'finding' and 'reason'")
        if not [e for e in evidence if str(e).strip()]:
            raise VerdictError("a rejected finding requires non-empty 'evidence'")
        rejected.append({"finding": finding, "reason": reason, "evidence": evidence})
    normalized["rejected_findings"] = rejected

    # Optional per-item resolution map: each entry binds a specific reviewer
    # finding ref to a disposition. A rejected disposition requires evidence.
    resolutions = []
    for entry in _as_list(obj.get("resolutions"), "resolutions"):
        if not isinstance(entry, dict):
            raise VerdictError("each resolution must be an object with ref/disposition")
        ref = str(entry.get("ref") or "").strip()
        disp = str(entry.get("disposition") or "").strip().lower()
        if not ref:
            raise VerdictError("a resolution requires a non-empty 'ref'")
        if disp not in RESOLUTION_DISPOSITIONS:
            raise VerdictError("resolution disposition must be one of: {}".format(
                ", ".join(RESOLUTION_DISPOSITIONS)))
        evidence = _as_list(entry.get("evidence"), "evidence")
        if disp == "rejected" and not [e for e in evidence if str(e).strip()]:
            raise VerdictError("a rejected resolution requires non-empty 'evidence'")
        if disp == "blocked_by_capability":
            limitation = str(entry.get("limitation") or entry.get("note") or "").strip()
            if not limitation:
                raise VerdictError("a blocked_by_capability resolution requires a "
                                   "'limitation' statement naming the capability gap")
            if not [e for e in evidence if str(e).strip()]:
                raise VerdictError("a blocked_by_capability resolution requires "
                                   "non-empty 'evidence'")
        resolutions.append({"ref": ref, "disposition": disp,
                            "note": str(entry.get("note") or "").strip(),
                            "limitation": str(entry.get("limitation") or "").strip(),
                            "evidence": evidence})
    normalized["resolutions"] = resolutions

    # Impossibility can never be waved through: a reconciliation that declares
    # ready_to_proceed while carrying a capability block is invalid on its face.
    if normalized["ready_to_proceed"] and any(
            r["disposition"] == "blocked_by_capability" for r in resolutions):
        raise VerdictError("ready_to_proceed cannot be true while a resolution is "
                           "blocked_by_capability")
    return normalized
