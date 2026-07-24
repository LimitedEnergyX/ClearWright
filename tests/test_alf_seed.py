"""P1b/P1c tests: initial seed findings (packet s21 + residual s10) and the
GalleyQuest acceptance fixture (operator-directed)."""
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "tools"))
import clearwright_alf as alf  # noqa: E402
import clearwright_alf_synth as syn  # noqa: E402
import clearwright_alf_seed as seed  # noqa: E402
import clearwright_alf_gqfixture as gq  # noqa: E402


class SeedTest(unittest.TestCase):
    def setUp(self):
        self.q = tempfile.mkdtemp(prefix="alf-seed-")
        alf.ensure_layout(self.q)

    def tearDown(self):
        shutil.rmtree(self.q, ignore_errors=True)

    def test_seed_creates_four_findings_with_correct_tiers(self):
        res = seed.seed_initial_findings(self.q)
        self.assertTrue(res["seeded"])
        self.assertEqual(res["entry_ids"], ["ALF-0001", "ALF-0002", "ALF-0003", "ALF-0004"])
        f1 = syn.load_finding(self.q, "ALF-0001")
        self.assertEqual((f1["priority_tier"], f1["priority_score"]), (1, 43))
        f2 = syn.load_finding(self.q, "ALF-0002")
        self.assertEqual((f2["priority_tier"], f2["priority_score"]), (2, 15))
        f3 = syn.load_finding(self.q, "ALF-0003")
        self.assertEqual((f3["priority_tier"], f3["priority_score"]), (1, 40))
        f4 = syn.load_finding(self.q, "ALF-0004")  # residual dispatch-eligibility
        self.assertEqual(f4["priority_tier"], 1)
        self.assertIn("lineage_note", f4)

    def test_seed_is_idempotent(self):
        seed.seed_initial_findings(self.q)
        again = seed.seed_initial_findings(self.q)
        self.assertFalse(again["seeded"])
        self.assertEqual(len(syn.list_findings(self.q)), 4)

    def test_seed_integrity(self):
        seed.seed_initial_findings(self.q)
        self.assertTrue(alf.verify_hashes(self.q)["ok"])
        for eid in ("ALF-0001", "ALF-0002", "ALF-0003", "ALF-0004"):
            self.assertTrue(syn.head_equals_rebuild(self.q, eid))
        # ALF-0001 carries observed_occurrence evidence (planning-approval eligible)
        f1 = syn.load_finding(self.q, "ALF-0001")
        self.assertTrue(any(e["role"] == "observed_occurrence"
                            for e in f1["evidence_references"]))


class GqFixtureTest(unittest.TestCase):
    def setUp(self):
        self.q = tempfile.mkdtemp(prefix="alf-gq-")
        alf.ensure_layout(self.q)

    def tearDown(self):
        shutil.rmtree(self.q, ignore_errors=True)

    def test_acceptance_quantifies_gq_waste(self):
        r = gq.run_acceptance(self.q)
        self.assertEqual(r["reviewer_unavailable_count"], 10)
        self.assertEqual(r["consumed_reviewer_attempts"], 40)   # 10 councils x 4 attempts
        self.assertEqual(r["distinct_work_items"], 4)
        self.assertEqual(r["affected_run_count"], 4)
        self.assertEqual(r["occurrence_count"], 10)
        self.assertTrue(alf.verify_hashes(self.q)["ok"])

    def test_acceptance_preserves_causal_uncertainty(self):
        r = gq.run_acceptance(self.q)
        # must NOT claim sensitivity alone caused the failures
        self.assertNotIn("sensitivity alone", r["root_cause"].lower())
        self.assertIn("unresolved", r["root_cause"].lower())
        self.assertNotEqual(r["root_cause_confidence"], "1.00")


if __name__ == "__main__":
    unittest.main()
