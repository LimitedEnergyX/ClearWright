"""Tests for enablers A/B (tools/clearwright_dispatch_preflight.py): normalized
reviewer-failure classification (no secrets/bodies) and deterministic
pre-allocation dispatch eligibility."""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "tools"))
import clearwright_dispatch_preflight as cwdp  # noqa: E402


class ClassifyTest(unittest.TestCase):
    def _c(self, result, status=None):
        cls = cwdp.classify_reviewer_failure(result, status)
        self.assertIn(cls, cwdp.NORMALIZED_FAILURE_CLASSES)
        return cls

    def test_none_result_is_provider_unavailable(self):
        self.assertEqual(self._c(None), "provider_unavailable")
        self.assertEqual(self._c(None, "missing"), "provider_unavailable")

    def test_rate_limit(self):
        self.assertEqual(self._c({"error": "HTTP 429 Too Many Requests"}), "rate_limit")

    def test_timeout(self):
        self.assertEqual(self._c({"error": "request timed out after 300s"}), "timeout")

    def test_auth(self):
        self.assertEqual(self._c({"classification": "401 unauthorized: bad api key"}),
                         "auth_failure")

    def test_tripwire(self):
        self.assertEqual(self._c({"error": "egress tripwire refusal"}), "tripwire_refusal")

    def test_provenance(self):
        self.assertEqual(self._c({"error": "repo_unresolvable source"}),
                         "provenance_unresolved")

    def test_repo_not_approved(self):
        self.assertEqual(self._c({"error": "repo not approved for egress"}),
                         "repo_not_approved")

    def test_composition_mismatch(self):
        self.assertEqual(self._c({"error": "composition-to-lineage hash mismatch"}),
                         "composition_or_hash_mismatch")

    def test_malformed_from_status(self):
        self.assertEqual(self._c({}, "invalid_verdict"), "malformed_response")

    def test_provider_unavailable_5xx(self):
        self.assertEqual(self._c({"error": "503 service unavailable"}),
                         "provider_unavailable")

    def test_unknown_when_no_signal(self):
        self.assertEqual(self._c({}, None), "unknown")

    def test_egress_blocked_is_policy_denial(self):
        self.assertEqual(self._c({"error": "egress_blocked"}), "policy_denial")

    def test_unicode_confusable_is_tripwire(self):
        self.assertEqual(self._c({"reason": "unicode_confusable detected"}),
                         "tripwire_refusal")

    def test_does_not_read_body_fields(self):
        # a "body"/"content"/"verdict" carrying 429 must NOT leak into the class;
        # only safe fields (error/classification/reason/code) are read.
        self.assertEqual(self._c({"body": "429 rate limit", "verdict": "timeout"}),
                         "unknown")


class EligibilityTest(unittest.TestCase):
    def test_all_clear(self):
        ok, reason = cwdp.dispatch_eligibility({
            "lane_authorized": True, "repo_approved": True,
            "provenance_resolved": True, "provider_ready": True})
        self.assertTrue(ok)
        self.assertIsNone(reason)

    def test_absent_signals_pass(self):
        self.assertEqual(cwdp.dispatch_eligibility({}), (True, None))

    def test_repo_not_approved(self):
        self.assertEqual(cwdp.dispatch_eligibility({"repo_approved": False}),
                         (False, "repo_not_approved"))

    def test_provenance_unresolved(self):
        self.assertEqual(cwdp.dispatch_eligibility({"provenance_resolved": False}),
                         (False, "provenance_unresolved"))

    def test_sensitive_prohibited(self):
        self.assertEqual(cwdp.dispatch_eligibility({"sensitive_prohibited": True}),
                         (False, "sensitive_content_prohibited"))

    def test_tripwire_not_clear(self):
        self.assertEqual(cwdp.dispatch_eligibility({"tripwire_clear": False}),
                         (False, "tripwire_refusal"))

    def test_first_failure_wins_in_order(self):
        # repo_approved is checked before provider_ready
        self.assertEqual(cwdp.dispatch_eligibility(
            {"repo_approved": False, "provider_ready": False}),
            (False, "repo_not_approved"))

    def test_refused_record_shape(self):
        rec = cwdp.refused_dispatch_record(phase="verify", dispatch_lane="user",
                                           normalized_reason="repo_not_approved",
                                           detail="x" * 500)
        self.assertIsNone(rec["council_id"])
        self.assertEqual(rec["attempt"], 0)
        self.assertEqual(rec["normalized_reason"], "repo_not_approved")
        self.assertLessEqual(len(rec["detail"]), 200)


if __name__ == "__main__":
    unittest.main()
