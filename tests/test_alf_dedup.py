"""P1b tests: dedup-policy-v1 proposals + silent-merge prohibition, the idempotent
attribution ledger, recurrence counter updates, and regression reopen with the
tier-and-score floor (tools/clearwright_alf_synth.py, sections 9/11/12/13)."""
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "tools"))
import clearwright_alf as alf  # noqa: E402
import clearwright_alf_synth as syn  # noqa: E402


class DedupTest(unittest.TestCase):
    def setUp(self):
        self.q = tempfile.mkdtemp(prefix="alf-dd-")
        alf.ensure_layout(self.q)

    def tearDown(self):
        shutil.rmtree(self.q, ignore_errors=True)

    def test_signature_normalizes_and_drops_stopwords(self):
        sig = syn.dedup_signature("The council_engine is Broken and stale")
        self.assertEqual(sig, sorted({"council_engine", "broken", "stale"}))

    def test_exact_key_match_proposes_090(self):
        f = {"subsystem": "council_engine", "failure_class": "council_failure",
             "root_cause": "reviewer dispatch fails before eligibility check"}
        syn.create_finding(self.q, dict(f, title="a"))
        prop = syn.propose_dedup(self.q, dict(f, title="b", entry_id="ALF-9999"))
        self.assertIsNotNone(prop)
        self.assertEqual(prop["confidence"], "0.90")
        self.assertEqual(prop["duplicate_of"], "ALF-0001")

    def test_no_match_when_key_differs(self):
        syn.create_finding(self.q, {"subsystem": "council_engine", "title": "a",
                                    "failure_class": "council_failure",
                                    "root_cause": "reviewer dispatch fails"})
        prop = syn.propose_dedup(self.q, {"subsystem": "cli", "title": "b",
                                          "failure_class": "council_failure",
                                          "root_cause": "reviewer dispatch fails",
                                          "entry_id": "ALF-9999"})
        self.assertIsNone(prop)

    def test_protected_class_flagged(self):
        f = {"subsystem": "queue_store", "failure_class": "durable_record_integrity",
             "root_cause": "index chain break undetected", "title": "a"}
        syn.create_finding(self.q, dict(f))
        prop = syn.propose_dedup(self.q, dict(f, title="b", entry_id="ALF-9999"))
        self.assertTrue(prop["protected"])


class LedgerRecurrenceTest(unittest.TestCase):
    def setUp(self):
        self.q = tempfile.mkdtemp(prefix="alf-lr-")
        alf.ensure_layout(self.q)
        self.eid = syn.create_finding(self.q, {
            "title": "reviewer waste", "status": "PRIORITIZED",
            "subsystem": "council_engine", "failure_class": "council_failure",
            "blast_radius": "all_councils", "occurrence_count": 1,
            "affected_run_count": 1})

    def tearDown(self):
        shutil.rmtree(self.q, ignore_errors=True)

    def _occ(self, run_id, metrics=None, occ_id=None):
        return {"occurrence_id": occ_id or ("occ-" + run_id),
                "observation_id": "obs-1", "run_id": run_id,
                "captured_at": "2026-07-24T00:00:00.000000Z", "metrics": metrics}

    def test_attribution_is_idempotent(self):
        occ = self._occ("run-A", {"council_attempts": 4})
        r1 = syn.record_recurrence(self.q, self.eid, occ)
        self.assertTrue(r1["attributed"])
        r2 = syn.record_recurrence(self.q, self.eid, occ)
        self.assertFalse(r2["attributed"])
        head = syn.load_finding(self.q, self.eid)
        self.assertEqual(head["occurrence_count"], 2)  # incremented exactly once
        self.assertEqual(head["cumulative_council_attempts_wasted"], 4)

    def test_cross_run_recurrence_counts_and_folds(self):
        syn.record_recurrence(self.q, self.eid, self._occ("run-A", {"council_attempts": 4}))
        syn.record_recurrence(self.q, self.eid, self._occ("run-B", {"council_attempts": 6}))
        head = syn.load_finding(self.q, self.eid)
        self.assertEqual(head["occurrence_count"], 3)       # 1 seed + 2 recurrences
        self.assertEqual(head["affected_run_count"], 3)     # seed run + A + B
        self.assertEqual(head["cumulative_council_attempts_wasted"], 10)
        self.assertTrue(alf.verify_hashes(self.q)["ok"])

    def test_ledger_and_finding_chain_intact(self):
        syn.record_recurrence(self.q, self.eid, self._occ("run-A", {"api_attempts": 3}))
        self.assertEqual(alf.verify_chain(alf.ledger_path(self.q)), [])
        self.assertEqual(
            alf.verify_chain(syn.finding_history_path(self.q, self.eid)), [])
        self.assertTrue(syn.head_equals_rebuild(self.q, self.eid))


class RegressionTest(unittest.TestCase):
    def setUp(self):
        self.q = tempfile.mkdtemp(prefix="alf-rg-")
        alf.ensure_layout(self.q)

    def tearDown(self):
        shutil.rmtree(self.q, ignore_errors=True)

    def _released(self, **kw):
        base = {"title": "fixed then broke", "status": "RELEASED",
                "subsystem": "council_engine", "failure_class": "council_failure",
                "blast_radius": "single_run", "occurrence_count": 2,
                "affected_run_count": 2, "priority_tier": 1, "priority_score": 100,
                "release_baseline": {"tier": 1, "score": 100,
                                     "priority_model_version": "priority-model-v1",
                                     "at": "2026-07-01T00:00:00.000000Z"}}
        base.update(kw)
        return syn.create_finding(self.q, base)

    def _occ(self, run_id="run-new"):
        return {"occurrence_id": "occ-" + run_id, "observation_id": "obs-1",
                "run_id": run_id, "captured_at": "2026-07-24T00:00:00.000000Z",
                "metrics": {"council_attempts": 1}}

    def test_regression_reopens_and_floors_score(self):
        eid = self._released()
        res = syn.record_regression(self.q, eid, self._occ())
        self.assertTrue(res["attributed"])
        head = syn.load_finding(self.q, eid)
        self.assertEqual(head["status"], "PRIORITIZED")
        # recomputed score is low; the baseline score 100 floors it.
        self.assertEqual(head["priority_score"], 100)
        self.assertEqual(head["priority_tier"], 1)
        self.assertTrue(head["tier_decision"]["regression_floor_applied"])

    def test_regression_tier_floor_keeps_better_tier(self):
        # recomputed tier would be 2 (excess_deliberation) but baseline tier is 1.
        eid = self._released(failure_class="excess_deliberation",
                             release_baseline={"tier": 1, "score": 5,
                                               "priority_model_version": "priority-model-v1",
                                               "at": "2026-07-01T00:00:00.000000Z"})
        syn.record_regression(self.q, eid, self._occ())
        head = syn.load_finding(self.q, eid)
        self.assertEqual(head["priority_tier"], 1)  # min(2, 1)

    def test_regression_idempotent(self):
        eid = self._released()
        occ = self._occ()
        syn.record_regression(self.q, eid, occ)
        again = syn.record_regression(self.q, eid, occ)
        self.assertFalse(again["attributed"])


if __name__ == "__main__":
    unittest.main()
