"""P1a tests for the ALF durable store (tools/clearwright_alf.py):
canonical serialization, per-line hash chains, immutable observation capture,
cross-run occurrences, verify-hashes tamper detection, and operation-journal
crash recovery."""
import json
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "tools"))
import clearwright_alf as alf  # noqa: E402


class AlfStoreTest(unittest.TestCase):
    def setUp(self):
        self.q = tempfile.mkdtemp(prefix="alf-test-")
        alf.ensure_layout(self.q)

    def tearDown(self):
        shutil.rmtree(self.q, ignore_errors=True)

    def _obs(self, summary="a fact", run_id="run-1", **kw):
        return alf.build_observation(
            kind=kw.get("kind", "council_outcome"),
            subsystem=kw.get("subsystem", "council_engine"),
            summary=summary, run_id=run_id,
            source_refs=kw.get("source_refs", [
                {"ref": "council-outcome:c1", "sha256": "a" * 64,
                 "role": "observed_occurrence"}]),
            metrics=kw.get("metrics"))

    # -- canonical serialization -------------------------------------------- #
    def test_float_is_refused_in_hashed_record(self):
        with self.assertRaises(alf.AlfError):
            alf.canonical_bytes({"x": 1.5})
        # booleans and ints are fine
        alf.canonical_bytes({"a": True, "b": 3, "c": None, "d": "s"})

    def test_canonical_is_deterministic_and_sorted(self):
        a = alf.canonical_bytes({"b": 1, "a": 2})
        b = alf.canonical_bytes({"a": 2, "b": 1})
        self.assertEqual(a, b)
        self.assertEqual(a, b'{"a":2,"b":1}')

    # -- observation identity + capture ------------------------------------- #
    def test_identity_excludes_capture_context(self):
        o1 = self._obs(run_id="run-1")
        o2 = self._obs(run_id="run-2")
        self.assertEqual(o1["observation_id"], o2["observation_id"])

    def test_capture_creates_fact_and_occurrence(self):
        res = alf.capture(self.q, self._obs())
        self.assertTrue(res["created_fact"])
        self.assertTrue(res["created_occurrence"])
        self.assertEqual(len(alf.list_observations(self.q)), 1)
        self.assertTrue(alf.verify_hashes(self.q)["ok"])

    def test_recapture_same_run_is_noop(self):
        obs = self._obs()
        alf.capture(self.q, obs)
        res = alf.capture(self.q, obs)
        self.assertFalse(res["created_fact"])
        self.assertFalse(res["created_occurrence"])
        # exactly one occurrence line
        recs, _ = alf._read_valid_lines(alf.occurrences_path(self.q))
        self.assertEqual(len(recs), 1)

    def test_cross_run_occurrence(self):
        alf.capture(self.q, self._obs(run_id="run-A"))
        res = alf.capture(self.q, self._obs(run_id="run-B"))
        self.assertFalse(res["created_fact"])      # one deduplicated fact
        self.assertTrue(res["created_occurrence"])  # but a new occurrence
        self.assertEqual(len(alf.list_observations(self.q)), 1)
        recs, _ = alf._read_valid_lines(alf.occurrences_path(self.q))
        self.assertEqual(len(recs), 2)
        self.assertEqual({r["run_id"] for r in recs}, {"run-A", "run-B"})
        self.assertTrue(alf.verify_hashes(self.q)["ok"])

    def test_id_collision_refused(self):
        obs = self._obs()
        alf.capture(self.q, obs)
        # Corrupt the stored file so its identity fields diverge from the id.
        path = alf.observation_file(self.q, obs["observation_id"])
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
        data["summary"] = "tampered different fact"
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(data, fh)
        with self.assertRaises(alf.IntegrityHalt):
            alf.capture(self.q, self._obs(run_id="run-Z"))

    # -- hash chains + verify ----------------------------------------------- #
    def test_chain_intact_after_multiple_captures(self):
        for i in range(4):
            alf.capture(self.q, self._obs(summary="fact-{}".format(i)))
        self.assertEqual(alf.verify_chain(alf.index_path(self.q)), [])
        self.assertEqual(alf.verify_chain(alf.occurrences_path(self.q)), [])
        self.assertTrue(alf.verify_hashes(self.q)["ok"])

    def test_verify_detects_observation_byte_tamper(self):
        obs = self._obs()
        alf.capture(self.q, obs)
        path = alf.observation_file(self.q, obs["observation_id"])
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(" ")  # change file bytes without touching the index
        report = alf.verify_hashes(self.q)
        self.assertFalse(report["ok"])
        self.assertTrue(any("diverge from index" in p for p in report["problems"]))

    def test_verify_detects_chain_break(self):
        alf.capture(self.q, self._obs(summary="one"))
        alf.capture(self.q, self._obs(summary="two"))
        # Rewrite the index tampering an interior line's content.
        path = alf.index_path(self.q)
        recs, _ = alf._read_valid_lines(path)
        recs[0]["kind"] = "reviewer_attempt"  # break the recorded line hash
        with open(path, "w", encoding="utf-8") as fh:
            for r in recs:
                fh.write(json.dumps(r, sort_keys=True, separators=(",", ":")) + "\n")
        self.assertNotEqual(alf.verify_chain(path), [])

    # -- operation-journal crash recovery ----------------------------------- #
    def test_recover_completes_interrupted_append(self):
        # Simulate a crash AFTER op_begin + staging but BEFORE apply/commit.
        occ_path = alf.occurrences_path(self.q)
        prev, count = alf.chain_head(occ_path)
        payload = {"alf_record_version": 1, "occurrence_id": "occ-manual",
                   "observation_id": "obs-manual", "run_id": "run-x",
                   "captured_at": alf.now_iso(), "capture_method": "cli_explicit",
                   "capturing_actor": "test", "metrics": None}
        rec = alf.chained_record(payload, prev)
        line = alf.canonical_line(rec)
        content_sha = alf.sha256_hex(line)
        op_id = "op-manualtest0001"
        sdir = alf.staged_dir(self.q, op_id)
        os.makedirs(sdir, exist_ok=True)
        staged_file = "0-" + content_sha[:16]
        alf._write_bytes_fsync(os.path.join(sdir, staged_file), line)
        jpath = alf.journal_path(self.q)
        jprev, _ = alf.chain_head(jpath)
        begin = alf.chained_record({
            "op_id": op_id, "operation_kind": "test", "subject_ids": ["x"],
            "staged_writes": [{
                "target_path_rel": "observations/occurrences.jsonl",
                "staged_file": staged_file, "content_sha256": content_sha,
                "write_kind": "append_line", "expected_prev_line_sha256": prev,
                "expected_chain_position": count + 1}],
            "at": alf.now_iso(), "event": "op_begin"}, jprev)
        alf._append_line_fsync(jpath, alf.canonical_line(begin))

        report = alf.recover(self.q)
        self.assertIn(op_id, report["recovered"])
        recs, _ = alf._read_valid_lines(occ_path)
        self.assertTrue(any(r.get("occurrence_id") == "occ-manual" for r in recs))
        # Recovery is idempotent: a second pass finds nothing to do.
        self.assertEqual(alf.recover(self.q)["recovered"], [])
        self.assertFalse(os.path.isdir(sdir))  # staging cleaned

    def test_recover_missing_staged_bytes_fails_closed(self):
        jpath = alf.journal_path(self.q)
        jprev, _ = alf.chain_head(jpath)
        begin = alf.chained_record({
            "op_id": "op-broken0001", "operation_kind": "test",
            "subject_ids": ["x"], "staged_writes": [{
                "target_path_rel": "observations/occurrences.jsonl",
                "staged_file": "0-deadbeefdeadbeef",
                "content_sha256": "d" * 64, "write_kind": "append_line",
                "expected_prev_line_sha256": alf.SENTINEL,
                "expected_chain_position": 1}],
            "at": alf.now_iso(), "event": "op_begin"}, jprev)
        alf._append_line_fsync(jpath, alf.canonical_line(begin))
        with self.assertRaises(alf.IntegrityHalt):
            alf.recover(self.q)

    def test_normal_capture_leaves_no_staging(self):
        alf.capture(self.q, self._obs())
        staged_root = os.path.join(alf.alf_root(self.q), "journal", "staged")
        self.assertEqual(os.listdir(staged_root), [])
        # journal has op_begin + op_commit for the capture
        jrecs, _ = alf._read_valid_lines(alf.journal_path(self.q))
        events = [r["event"] for r in jrecs]
        self.assertIn("op_begin", events)
        self.assertIn("op_commit", events)
        self.assertEqual(alf.verify_chain(alf.journal_path(self.q)), [])


if __name__ == "__main__":
    unittest.main()
