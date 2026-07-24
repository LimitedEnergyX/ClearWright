"""P1b tests for ALF synthesis (tools/clearwright_alf_synth.py): priority-model-v1
materialization + hashing, tier-policy-v1 boundaries, exact scoring, the findings
store (entry_id allocation, revision log, byte-exact head-rebuild)."""
import json
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "tools"))
import clearwright_alf as alf  # noqa: E402
import clearwright_alf_synth as syn  # noqa: E402


class ModelAndScoringTest(unittest.TestCase):
    def setUp(self):
        self.q = tempfile.mkdtemp(prefix="alf-syn-")
        alf.ensure_layout(self.q)

    def tearDown(self):
        shutil.rmtree(self.q, ignore_errors=True)

    def test_model_hash_reproducible_and_persisted(self):
        h1 = syn.materialize_model(self.q)
        self.assertEqual(h1, syn.model_sha256())
        # file bytes are the canonical compact form and re-materialization is a no-op
        with open(syn.model_path(self.q), "rb") as fh:
            self.assertEqual(fh.read(), syn.model_bytes())
        self.assertEqual(syn.materialize_model(self.q), h1)

    def test_model_divergent_overwrite_refused(self):
        syn.materialize_model(self.q)
        with open(syn.model_path(self.q), "a", encoding="utf-8") as fh:
            fh.write(" ")
        with self.assertRaises(alf.IntegrityHalt):
            syn.materialize_model(self.q)

    def test_tier0_active_exposure(self):
        iv = {"risk_activity": "active", "exposure_class": "credential",
              "failure_class": "authority_bypass_risk"}
        self.assertEqual(syn.assign_tier(iv)["tier"], 0)

    def test_tier1_authority_impact(self):
        iv = {"risk_activity": "historical", "authority_integrity_impact": 2,
              "failure_class": "clarity"}
        self.assertEqual(syn.assign_tier(iv)["tier"], 1)

    def test_tier1_failure_class(self):
        iv = {"risk_activity": "historical", "failure_class": "lifecycle_failure"}
        self.assertEqual(syn.assign_tier(iv)["tier"], 1)

    def test_tier2_excess_deliberation(self):
        iv = {"risk_activity": "historical", "failure_class": "excess_deliberation"}
        self.assertEqual(syn.assign_tier(iv)["tier"], 2)

    def test_tier3_default(self):
        iv = {"risk_activity": "historical", "failure_class": "documentation"}
        self.assertEqual(syn.assign_tier(iv)["tier"], 3)

    def test_alf0003_seed_tier_and_escalation(self):
        base = {"risk_activity": "historical", "exposure_class": "none",
                "mutation_class": "destructive_action_risk",
                "record_integrity_class": "corruption_risk",
                "ownership_conflict": False,
                "failure_class": "durable_record_integrity",
                "durable_record_integrity_impact": 3, "authority_integrity_impact": 1}
        self.assertEqual(syn.assign_tier(base)["tier"], 1)  # seed tier
        escalated = dict(base, risk_activity="plausible")
        self.assertEqual(syn.assign_tier(escalated)["tier"], 0)  # data-loss event

    def test_exact_score_alf0001_vector(self):
        finding = {
            "security_impact": 0, "authority_integrity_impact": 3,
            "durable_record_integrity_impact": 2, "reliability_impact": 2,
            "operator_time_impact": 2, "execution_delay_impact": 2,
            "token_api_compute_impact": 1, "blast_radius": "multiple_subsystems",
            "occurrence_count": 1}
        # base 35 + radius 2*4 + rec 0 + reg 0 + waste 0 = 43
        self.assertEqual(syn.compute_score(finding), 43)

    def test_score_recurrence_regression_waste(self):
        finding = {"reliability_impact": 1, "blast_radius": "single_run",
                   "occurrence_count": 4, "cumulative_council_attempts_wasted": 6}
        # base 3*1=3 + radius 2*1=2 + rec 2*min(3,10)=6 + reg 12 + waste band(6>=5)=2 *2=4
        self.assertEqual(syn.compute_score(finding, regression_flag=1), 3 + 2 + 6 + 12 + 4)

    def test_waste_band_inclusive_thresholds(self):
        self.assertEqual(syn.waste_band_max({"cumulative_api_attempts_wasted": 3}), 1)
        self.assertEqual(syn.waste_band_max({"cumulative_api_attempts_wasted": 2}), 0)
        self.assertEqual(syn.waste_band_max({"cumulative_api_attempts_wasted": 25}), 3)


class FindingsStoreTest(unittest.TestCase):
    def setUp(self):
        self.q = tempfile.mkdtemp(prefix="alf-fnd-")
        alf.ensure_layout(self.q)

    def tearDown(self):
        shutil.rmtree(self.q, ignore_errors=True)

    def _finding(self, title="t", **kw):
        f = {"title": title, "status": "TRIAGED", "subsystem": "cli",
             "failure_class": "lifecycle_failure", "blast_radius": "single_subsystem"}
        f.update(kw)
        return f

    def test_create_allocates_gap_allowed_ids(self):
        a = syn.create_finding(self.q, self._finding("first"))
        b = syn.create_finding(self.q, self._finding("second"))
        self.assertEqual(a, "ALF-0001")
        self.assertEqual(b, "ALF-0002")
        self.assertEqual(len(syn.list_findings(self.q)), 2)

    def test_head_equals_rebuild_on_create(self):
        eid = syn.create_finding(self.q, self._finding())
        self.assertTrue(syn.head_equals_rebuild(self.q, eid))

    def test_update_appends_revision_and_head_tracks(self):
        eid = syn.create_finding(self.q, self._finding(status="TRIAGED"))
        rn = syn.update_finding(self.q, eid,
                                lambda r: dict(r, status="PRIORITIZED"),
                                reason="scored")
        self.assertEqual(rn, 2)
        head = syn.load_finding(self.q, eid)
        self.assertEqual(head["status"], "PRIORITIZED")
        self.assertTrue(syn.head_equals_rebuild(self.q, eid))
        # history chain intact
        self.assertEqual(alf.verify_chain(syn.finding_history_path(self.q, eid)), [])
        revs = syn._read_history(self.q, eid)
        self.assertEqual([r["revision_no"] for r in revs], [1, 2])

    def test_create_is_transactional_no_staging(self):
        syn.create_finding(self.q, self._finding())
        staged_root = os.path.join(alf.alf_root(self.q), "journal", "staged")
        self.assertEqual(os.listdir(staged_root), [])
        self.assertTrue(alf.verify_hashes(self.q)["ok"])


if __name__ == "__main__":
    unittest.main()
