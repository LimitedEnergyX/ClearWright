"""P1b tests for the Run Improvement Delta (tools/clearwright_alf_delta.py):
immutable input snapshot, deterministic derivation, idempotent-or-refused reruns,
the anchor chain, empty-delta emission, and the missing-delta verifier."""
import json
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "tools"))
import clearwright_alf as alf  # noqa: E402
import clearwright_alf_synth as syn  # noqa: E402
import clearwright_alf_delta as dlt  # noqa: E402


class DeltaTest(unittest.TestCase):
    def setUp(self):
        self.q = tempfile.mkdtemp(prefix="alf-dl-")
        alf.ensure_layout(self.q)

    def tearDown(self):
        shutil.rmtree(self.q, ignore_errors=True)

    def _mk(self, run_id, tier=1, score=43, **kw):
        f = {"title": "t", "status": "PRIORITIZED", "subsystem": "cli",
             "failure_class": "lifecycle_failure", "blast_radius": "single_subsystem",
             "priority_tier": tier, "priority_score": score,
             "occurrence_count": 1, "affected_run_count": 1}
        f.update(kw)
        return syn.create_finding(self.q, f, run_id=run_id)

    def test_empty_delta_is_written(self):
        res = dlt.generate_delta(self.q, "run-empty")
        self.assertEqual(res["status"], "generated")
        d = dlt.load_delta(self.q, "run-empty")
        self.assertEqual(d["new_findings"], [])
        self.assertEqual(d["anchors"]["prev_delta_anchors_sha256"], dlt.GENESIS)
        self.assertTrue(alf.verify_hashes(self.q)["ok"])

    def test_delta_captures_new_findings_and_priority(self):
        eid = self._mk("run-1", tier=1, score=43)
        dlt.generate_delta(self.q, "run-1")
        d = dlt.load_delta(self.q, "run-1")
        self.assertEqual(d["new_findings"], [eid])
        self.assertEqual(len(d["findings_priority_changed"]), 1)
        pc = d["findings_priority_changed"][0]
        self.assertEqual((pc["old_tier"], pc["old_score"]), (None, None))
        self.assertEqual((pc["new_tier"], pc["new_score"]), (1, 43))

    def test_rerun_is_noop_even_after_unrelated_change(self):
        self._mk("run-1")
        dlt.generate_delta(self.q, "run-1")
        before = dlt.load_delta(self.q, "run-1")
        # unrelated synthesis in another run must not change run-1's delta
        self._mk("run-2")
        res = dlt.generate_delta(self.q, "run-1")
        self.assertEqual(res["status"], "noop")
        self.assertEqual(dlt.load_delta(self.q, "run-1"), before)

    def test_divergent_rerun_refused(self):
        self._mk("run-1")
        dlt.generate_delta(self.q, "run-1")
        # tamper the stored delta's deterministic content
        path = dlt.delta_path(self.q, "run-1")
        with open(path, encoding="utf-8") as fh:
            d = json.load(fh)
        d["new_findings"] = ["ALF-9999"]
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(d, fh)
        with self.assertRaises(alf.IntegrityHalt):
            dlt.generate_delta(self.q, "run-1")

    def test_anchor_chain_links_deltas(self):
        self._mk("run-1")
        r1 = dlt.generate_delta(self.q, "run-1")
        self._mk("run-2")
        dlt.generate_delta(self.q, "run-2")
        d2 = dlt.load_delta(self.q, "run-2")
        self.assertEqual(d2["anchors"]["prev_delta_anchors_sha256"],
                         r1["anchors_sha256"])

    def test_missing_delta_verifier(self):
        self._mk("run-1")
        dlt.generate_delta(self.q, "run-1")
        missing = dlt.missing_delta_verifier(self.q, ["run-1", "run-absent"])
        self.assertEqual(len(missing), 1)
        self.assertEqual(missing[0]["run_id"], "run-absent")
        self.assertEqual(missing[0]["tier"], 1)

    def test_snapshot_persisted_and_immutable_ref(self):
        self._mk("run-1")
        dlt.generate_delta(self.q, "run-1")
        self.assertTrue(os.path.exists(dlt.snapshot_path(self.q, "run-1")))
        with open(dlt.snapshot_path(self.q, "run-1"), encoding="utf-8") as fh:
            snap = json.load(fh)
        self.assertEqual(snap["snapshot_version"], 2)
        # the delta anchors bind the snapshot by hash
        d = dlt.load_delta(self.q, "run-1")
        self.assertIn("input_snapshot_sha256", d["anchors"])


if __name__ == "__main__":
    unittest.main()
