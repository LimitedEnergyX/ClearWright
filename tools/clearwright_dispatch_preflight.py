#!/usr/bin/env python3
"""tools/clearwright_dispatch_preflight.py: reviewer-failure classification +
pre-allocation dispatch eligibility (operator-directed enablers A and B).

These are ADDITIVE, fail-closed-preserving helpers used by the council engine:

  A. classify_reviewer_failure(): map a failed reviewer attempt to a safe,
     durable, normalized class so `reviewer_unavailable` stops being opaque and
     ALF can tell a safety refusal from provider flakiness. It NEVER returns
     secrets or raw provider bodies - only one of NORMALIZED_FAILURE_CLASSES.

  B. dispatch_eligibility(): a DETERMINISTIC pre-allocation check over signals
     that are known before any adapter call. It can only REFUSE earlier and more
     informatively than the downstream egress guard - it never authorizes a
     dispatch the guard would block, so no fail-closed control is weakened. When
     it refuses, the caller records the normalized reason and consumes NO council
     id or reviewer attempt.

Pure module: no imports from the council engine (avoids a cycle); the engine
imports these.
"""

NORMALIZED_FAILURE_CLASSES = (
    "policy_denial", "repo_not_approved", "provenance_unresolved",
    "sensitive_content_prohibited", "tripwire_refusal",
    "composition_or_hash_mismatch", "provider_unavailable", "auth_failure",
    "rate_limit", "timeout", "malformed_response", "adapter_failure",
    "classifier_unresolved", "unknown",
)

# Ordered (specific -> general) keyword rules over the safe signal text. Each rule
# is (predicate, class). Predicates take the lowercased signal string.
def _has_all(*subs):
    return lambda t: all(s in t for s in subs)


def _has_any(*subs):
    return lambda t: any(s in t for s in subs)


_RULES = (
    (_has_any("tripwire", "confusable"), "tripwire_refusal"),
    (_has_any("composition", "hash mismatch", "byte mismatch", "sha mismatch"),
     "composition_or_hash_mismatch"),
    (_has_all("repo", "not approved"), "repo_not_approved"),
    (_has_any("repo_unresolvable", "unresolvable", "provenance", "not git-tracked",
              "outside repo", "outside the repo"), "provenance_unresolved"),
    (_has_any("sensitive content", "sensitive_content", "prohibited", "embargo"),
     "sensitive_content_prohibited"),
    (_has_all("policy", "den"), "policy_denial"),
    (_has_any("rate limit", "rate_limit", "429", "too many requests"), "rate_limit"),
    (_has_any("timeout", "timed out", "deadline exceeded"), "timeout"),
    (_has_any("auth", "unauthorized", "401", "403", "api key", "credential",
              "permission denied"), "auth_failure"),
    (_has_any("malformed", "invalid json", "parse", "no_verdict", "invalid_verdict",
              "unvalidated", "source_mismatch", "schema"), "malformed_response"),
    (_has_any("connection", "network", "unavailable", "provider", "503", "502",
              "500", "cannot reach", "refused"), "provider_unavailable"),
    (_has_any("egress"), "policy_denial"),
    (_has_any("adapter"), "adapter_failure"),
)


def _safe_signal_text(result, status):
    """Assemble a lowercased signal string from SAFE, non-body fields only. Raw
    reviewer/provider content is never read here."""
    parts = []
    if status:
        parts.append(str(status))
    if isinstance(result, dict):
        for k in ("error", "classification", "reason", "error_class", "code"):
            v = result.get(k)
            if v is not None:
                parts.append(str(v))
    return " ".join(parts).lower()


def classify_reviewer_failure(result, status=None):
    """Return one of NORMALIZED_FAILURE_CLASSES for a failed reviewer attempt.
    `result` is the adapter result dict (or None); `status` is the evaluator's
    coarse status (missing/unavailable/no_verdict/invalid_verdict/...)."""
    if result is None and status in (None, "missing", "unavailable"):
        return "provider_unavailable"
    text = _safe_signal_text(result, status)
    if not text:
        return "unknown"
    for predicate, cls in _RULES:
        if predicate(text):
            return cls
    return "unknown"


# --------------------------------------------------------------------------- #
# Enabler B: deterministic pre-allocation eligibility.
# --------------------------------------------------------------------------- #
# Each check is (signal_key, expected_truthy, reason_when_failed). A signal that
# is absent defaults to eligible (True) so the check never INVENTS a blocker the
# caller did not assert - it only refuses on an explicitly-failed signal.
_ELIGIBILITY_CHECKS = (
    ("lane_authorized", True, "policy_denial"),
    ("classification_conflict", False, "policy_denial"),
    ("repo_approved", True, "repo_not_approved"),
    ("provenance_resolved", True, "provenance_unresolved"),
    ("sensitive_prohibited", False, "sensitive_content_prohibited"),
    ("composition_bound", True, "composition_or_hash_mismatch"),
    ("exact_bytes_ok", True, "composition_or_hash_mismatch"),
    # ordered BEFORE tripwire_clear: an unresolved classifier must report its own
    # distinct reason and must never be reported as a tripwire hit.
    ("classifier_resolved", True, "classifier_unresolved"),
    ("tripwire_clear", True, "tripwire_refusal"),
    ("provider_ready", True, "provider_unavailable"),
    ("auth_ok", True, "auth_failure"),
)


def dispatch_eligibility(signals):
    """Deterministic pre-allocation eligibility over already-computed signals.
    Returns (ok: bool, normalized_reason: str|None). Refuses on the FIRST failed
    check (stable order). Absent signals are treated as eligible, so this can only
    refuse where the caller proved a blocker - it never weakens the guard."""
    for key, expected, reason in _ELIGIBILITY_CHECKS:
        if key not in signals:
            continue
        if bool(signals[key]) != expected:
            return (False, reason)
    return (True, None)


def production_signals(*, dispatch_lane, review_profile, artifact_count,
                       lineage_bound, raw_provenance_standard, tripwire_clear,
                       classifier_resolved=True):
    """Derive AUTHORITATIVE pre-allocation signals from production preflight
    outputs. Callers pass already-computed facts; this function invents nothing.

    SAFETY INVARIANT: every signal here mirrors an EXISTING UNCONDITIONAL,
    DETERMINISTIC refusal that the engine or the egress guard already performs,
    so this check can only refuse EARLIER - never refuse something that would
    otherwise have dispatched successfully:

      - lane_authorized      mirrors run_round's internal_technical refusals of
                             artifacts and of any non-code review profile;
      - composition_bound    mirrors run_round's refusal of a missing lineage
                             graph or candidate on that lane;
      - provenance_resolved  mirrors run_round's refusal of a RAW node without
                             STANDARD provenance on that lane;
      - tripwire_clear       mirrors the guard's unconditional tripwire_hit block
                             (enforced on EVERY lane). See the one-directional
                             note below.
      - classifier_resolved  the classifier returned a verdict this gate
                             UNDERSTANDS. The classifier contract is treated as
                             exactly two known verdicts, "clear" and "hit".
                             Anything else -- unknown, malformed, empty, absent,
                             or a verdict added in future -- sets this False and
                             refuses with the DISTINCT reason
                             classifier_unresolved. An unrecognised verdict is
                             NEVER treated as authorization, and is never
                             mislabelled as a tripwire hit.

    DELIBERATELY EXCLUDED: provider readiness and credential presence. Those are
    DYNAMIC ENVIRONMENTAL conditions, not deterministic content properties: a
    dispatch may legitimately proceed through an injected or differently-resolved
    adapter, so refusing on them could newly deny a packet that would otherwise
    dispatch. That would break the invariant above. Readiness is already gated by
    the start-time preflight, and a genuinely absent provider still surfaces as a
    normal reviewer_unavailable outcome.

    TRIPWIRE SCOPE (one-directional, by construction): the caller can only scan
    the packet CONTEXT, because the complete outbound byte set is not assembled
    until after a council exists. The context is a SUBSET of those bytes, so a
    hit on the context PROVES a hit at send (no false refusal), while a clear
    context does NOT prove the outbound bytes are clear. This gate therefore
    catches the common case early and never over-refuses; the egress guard
    remains the complete and authoritative check over the exact outbound bytes.

    The internal_technical-only signals are OMITTED on other lanes. An absent
    signal is treated as eligible by dispatch_eligibility, so a lane that does
    not perform a given check never acquires a new blocker from it.
    """
    signals = {"tripwire_clear": bool(tripwire_clear),
               "classifier_resolved": bool(classifier_resolved)}
    if dispatch_lane == "internal_technical":
        signals["lane_authorized"] = (int(artifact_count or 0) == 0
                                      and review_profile == "code")
        signals["composition_bound"] = bool(lineage_bound)
        signals["provenance_resolved"] = bool(raw_provenance_standard)
    return signals


def refused_dispatch_record(*, phase, dispatch_lane, normalized_reason, detail=""):
    """A safe, durable record for a pre-allocation refusal - no council id and no
    reviewer attempt were consumed. Content-free beyond the normalized reason and
    a short detail (truncated). The caller writes this to the invocation log."""
    return {
        "command": "dispatch-refused-preallocation",
        "phase": phase,
        "dispatch_lane": dispatch_lane,
        "council_id": None,
        "attempt": 0,
        "normalized_reason": normalized_reason,
        "detail": (detail or "")[:200],
    }
