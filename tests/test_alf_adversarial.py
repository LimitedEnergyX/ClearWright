"""Adversarial round-3 tests for the HIGH-finding corrections: identifier +
path-containment attacks, torn journal records, concurrent duplicate mutation,
delta-reference deletion/tampering, whole-token operator-message binding, and
type-safe promotion-gate values."""
import json
import os
import shutil
import sys
import tempfile
import threading
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "tools"))
import clearwright_alf as alf  # noqa: E402
import clearwright_alf_synth as syn  # noqa: E402
import clearwright_alf_delta as dlt  # noqa: E402
import clearwright_alf_review as rev  # noqa: E402

FUTURE = "2099-01-01T00:00:00.000000Z"


class IdentityAndContainmentTest(unittest.TestCase):
    def setUp(self):
        self.q = tempfile.mkdtemp(prefix="alf-adv-")
        alf.ensure_layout(self.q)

    def tearDown(self):
        shutil.rmtree(self.q, ignore_errors=True)

    def test_safe_id_rejects_dangerous_and_accepts_valid(self):
        bad = ["../etc", "a/b", "a\\b", "..", ".", "", "C:x", "\\\\h\\s",
               "a\x00b", "-lead", ".hidden", "a b", "a/../b", "..\\x"]
        for v in bad:
            with self.assertRaises(alf.AlfError):
                alf.safe_id(v)
        for v in ("obs-abc123", "ALF-0001", "run-1", "index.jsonl", "a.b_c-d"):
            self.assertEqual(alf.safe_id(v), v)

    def test_path_helpers_reject_traversal(self):
        for fn, arg in [(alf.observation_file, "../x"),
                        (syn.finding_head_path, "../../x"),
                        (syn.finding_history_path, "a/b"),
                        (dlt.delta_path, "..\\x"),
                        (dlt.snapshot_path, "C:evil")]:
            with self.assertRaises(alf.AlfError):
                fn(self.q, arg)

    def test_render_spec_rejects_bad_entry_and_version(self):
        with self.assertRaises(alf.AlfError):
            rev.render_spec(self.q, "../escape")
        # even a valid-shaped id needs a real finding, but a bad version is rejected first
        with self.assertRaises(alf.AlfError):
            rev.render_spec(self.q, "ALF-0001", version="1; rm -rf /")

    def test_operation_rejects_traversal_target_rel(self):
        op = alf.Operation(self.q, "test", ["x"])
        for bad in ("../escape.jsonl", "a/../../escape.json", "a\\b.jsonl",
                    "/abs.json", "C:x.json"):
            with self.assertRaises(alf.AlfError):
                op.append_line(bad, {"a": 1})
            with self.assertRaises(alf.AlfError):
                op.replace_file(bad, {"a": 1})

    def test_recovery_rejects_traversal_journal_target(self):
        # A journal op_begin whose staged target_rel escapes alf/ must fail closed
        # during recovery, never write outside the subtree.
        jpath = alf.journal_path(self.q)
        sdir = alf.staged_dir(self.q, "op-evil0001")
        os.makedirs(sdir, exist_ok=True)
        line = alf.canonical_line(alf.chained_record({"x": 1}, alf.SENTINEL))
        sf = "0-" + alf.sha256_hex(line)[:16]
        alf._write_bytes_fsync(os.path.join(sdir, sf), line)
        jp, _ = alf.chain_head(jpath)
        begin = alf.chained_record({
            "op_id": "op-evil0001", "operation_kind": "test", "subject_ids": ["x"],
            "staged_writes": [{"target_path_rel": "../escape.jsonl", "staged_file": sf,
                               "content_sha256": alf.sha256_hex(line),
                               "write_kind": "append_line",
                               "expected_prev_line_sha256": alf.SENTINEL,
                               "expected_chain_position": 1}],
            "at": alf.now_iso(), "event": "op_begin"}, jp)
        alf._append_line_fsync(jpath, alf.canonical_line(begin))
        with self.assertRaises(alf.AlfError):
            alf.recover(self.q)
        self.assertFalse(os.path.exists(os.path.join(alf.alf_root(self.q), "..",
                                                     "escape.jsonl")))

    def test_recovery_rejects_traversal_op_id(self):
        jpath = alf.journal_path(self.q)
        jp, _ = alf.chain_head(jpath)
        begin = alf.chained_record({"op_id": "../evil", "operation_kind": "t",
                                    "subject_ids": ["x"], "staged_writes": [],
                                    "at": alf.now_iso(), "event": "op_begin"}, jp)
        alf._append_line_fsync(jpath, alf.canonical_line(begin))
        with self.assertRaises(alf.AlfError):
            alf.recover(self.q)

    def test_recovery_rejects_traversal_staged_file(self):
        jpath = alf.journal_path(self.q)
        jp, _ = alf.chain_head(jpath)
        begin = alf.chained_record({
            "op_id": "op-ok0001", "operation_kind": "t", "subject_ids": ["x"],
            "staged_writes": [{"target_path_rel": "observations/index.jsonl",
                               "staged_file": "../../evil", "content_sha256": "d" * 64,
                               "write_kind": "append_line",
                               "expected_prev_line_sha256": alf.SENTINEL,
                               "expected_chain_position": 1}],
            "at": alf.now_iso(), "event": "op_begin"}, jp)
        alf._append_line_fsync(jpath, alf.canonical_line(begin))
        with self.assertRaises(alf.AlfError):
            alf.recover(self.q)

    def test_operator_message_id_traversal_rejected(self):
        with self.assertRaises(alf.AlfError):
            rev._read_message(self.q, "../secret")


class TornJournalTest(unittest.TestCase):
    def setUp(self):
        self.q = tempfile.mkdtemp(prefix="alf-torn-")
        alf.ensure_layout(self.q)

    def tearDown(self):
        shutil.rmtree(self.q, ignore_errors=True)

    def _obs(self):
        return alf.build_observation(kind="executor_note", subsystem="cli",
                                     summary="a", run_id="r")

    def test_torn_journal_tail_healed_not_corrupted(self):
        alf.capture(self.q, self._obs())  # one committed op
        jpath = alf.journal_path(self.q)
        with open(jpath, "a", encoding="utf-8") as fh:
            fh.write('{"op_id":"op-torn","event":"op_beg')  # torn partial, no newline
        # a fresh capture must heal the torn tail before appending, not concatenate
        alf.capture(self.q, alf.build_observation(kind="executor_note",
                                                  subsystem="cli", summary="b", run_id="r"))
        self.assertEqual(alf.verify_chain(jpath), [])
        qdir = alf.quarantine_dir(self.q)
        self.assertTrue(os.path.isdir(qdir) and len(os.listdir(qdir)) >= 1)

    def test_torn_target_tail_healed_on_append(self):
        alf.capture(self.q, self._obs())
        occ = alf.occurrences_path(self.q)
        with open(occ, "a", encoding="utf-8") as fh:
            fh.write('{"occurrence_id":"occ-torn","partial')
        alf.capture(self.q, alf.build_observation(kind="executor_note",
                                                  subsystem="cli", summary="c", run_id="r"))
        self.assertEqual(alf.verify_chain(occ), [])
        self.assertTrue(alf.verify_hashes(self.q)["ok"])


class ConcurrencyTest(unittest.TestCase):
    def setUp(self):
        self.q = tempfile.mkdtemp(prefix="alf-conc-")
        alf.ensure_layout(self.q)

    def tearDown(self):
        shutil.rmtree(self.q, ignore_errors=True)

    def test_concurrent_identical_capture_no_duplication(self):
        obs = alf.build_observation(kind="council_outcome", subsystem="council_engine",
                                    summary="race", run_id="r",
                                    source_refs=[{"ref": "c:1", "sha256": "a" * 64,
                                                  "role": "observed_occurrence"}])
        errs = []

        def worker():
            try:
                alf.capture(self.q, obs)
            except Exception as exc:  # noqa: BLE001
                errs.append(repr(exc))

        threads = [threading.Thread(target=worker) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(errs, [])
        self.assertEqual(len(alf.list_observations(self.q)), 1)
        occ, _ = alf._read_valid_lines(alf.occurrences_path(self.q))
        self.assertEqual(len(occ), 1)  # exactly one occurrence despite 8 racers
        self.assertTrue(alf.verify_hashes(self.q)["ok"])

    def test_concurrent_finding_creation_unique_ids(self):
        made = []

        def worker(i):
            made.append(syn.create_finding(self.q, {
                "title": "f{}".format(i), "status": "TRIAGED", "subsystem": "cli",
                "failure_class": "lifecycle_failure", "blast_radius": "single_subsystem"}))

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(6)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(len(set(made)), 6)  # no two creators shared an entry_id
        self.assertTrue(alf.verify_hashes(self.q)["ok"])


class DeltaTamperTest(unittest.TestCase):
    def setUp(self):
        self.q = tempfile.mkdtemp(prefix="alf-dtamper-")
        alf.ensure_layout(self.q)
        self.obs = alf.build_observation(kind="council_outcome",
                                         subsystem="council_engine", summary="f",
                                         run_id="run-x",
                                         source_refs=[{"ref": "c:1", "sha256": "a" * 64,
                                                       "role": "observed_occurrence"}])
        alf.capture(self.q, self.obs)
        syn.create_finding(self.q, {"title": "t", "status": "PRIORITIZED",
                                    "subsystem": "cli", "failure_class": "lifecycle_failure",
                                    "blast_radius": "single_subsystem",
                                    "priority_tier": 1, "priority_score": 1}, run_id="run-x")
        dlt.generate_delta(self.q, "run-x")

    def tearDown(self):
        shutil.rmtree(self.q, ignore_errors=True)

    def test_rerun_noop_when_untouched(self):
        self.assertEqual(dlt.generate_delta(self.q, "run-x")["status"], "noop")

    def test_observation_byte_tamper_fails_closed(self):
        with open(alf.observation_file(self.q, self.obs["observation_id"]), "a",
                  encoding="utf-8") as fh:
            fh.write(" ")
        with self.assertRaises(alf.IntegrityHalt):
            dlt.generate_delta(self.q, "run-x")

    def test_observation_deletion_fails_closed(self):
        os.remove(alf.observation_file(self.q, self.obs["observation_id"]))
        with self.assertRaises(alf.IntegrityHalt):
            dlt.generate_delta(self.q, "run-x")

    def test_missing_delta_file_fails_closed(self):
        os.remove(dlt.delta_path(self.q, "run-x"))  # snapshot remains, delta gone
        with self.assertRaises(alf.AlfError):
            dlt.generate_delta(self.q, "run-x")

    def test_altered_occurrence_field_retained_hash_fails_closed(self):
        # alter a non-hash field but keep the stored line_sha256 (attacker retains the
        # stale hash); re-authentication via verify_chain must catch it.
        occ_path = alf.occurrences_path(self.q)
        recs, _ = alf._read_valid_lines(occ_path)
        recs[0]["captured_at"] = "1999-01-01T00:00:00.000000Z"
        with open(occ_path, "w", encoding="utf-8") as fh:
            for r in recs:
                fh.write(json.dumps(r, sort_keys=True, separators=(",", ":")) + "\n")
        with self.assertRaises(alf.IntegrityHalt):
            dlt.generate_delta(self.q, "run-x")

    def test_duplicate_occurrence_id_fails_closed(self):
        occ_path = alf.occurrences_path(self.q)
        recs, _ = alf._read_valid_lines(occ_path)
        body = {k: v for k, v in recs[0].items()
                if k not in ("prev_line_sha256", "line_sha256")}
        dup = alf.chained_record(body, recs[-1]["line_sha256"])  # valid chain, dup id
        with open(occ_path, "a", encoding="utf-8") as fh:
            fh.write(alf.canonical_line(dup).decode("utf-8"))
        with self.assertRaises(alf.IntegrityHalt):
            dlt.generate_delta(self.q, "run-x")

    def test_duplicate_finding_revision_fails_closed(self):
        hp = syn.finding_history_path(self.q, "ALF-0001")
        recs, _ = alf._read_valid_lines(hp)
        body = {k: v for k, v in recs[-1].items()
                if k not in ("prev_line_sha256", "line_sha256")}
        dup = alf.chained_record(body, recs[-1]["line_sha256"])  # dup revision_no
        with open(hp, "a", encoding="utf-8") as fh:
            fh.write(alf.canonical_line(dup).decode("utf-8"))
        with self.assertRaises(alf.IntegrityHalt):
            dlt.generate_delta(self.q, "run-x")


class BindingAndGateTest(unittest.TestCase):
    def setUp(self):
        self.q = tempfile.mkdtemp(prefix="alf-bind-")
        alf.ensure_layout(self.q)

    def tearDown(self):
        shutil.rmtree(self.q, ignore_errors=True)

    def _promotable(self, **kw):
        f = {"title": "t", "status": "PRIORITIZED", "subsystem": "cli",
             "failure_class": "lifecycle_failure", "blast_radius": "single_subsystem",
             "priority_tier": 1, "priority_score": 43,
             "permanent_resolution": "x", "objective_acceptance_criteria": "y",
             "required_regression_tests": "z", "root_cause_confidence": "0.90",
             "dependencies": [], "blockers": [],
             "evidence_references": [{"ref": "c:1", "sha256": "a" * 64,
                                      "role": "observed_occurrence",
                                      "archived_location": None}]}
        f.update(kw)
        return syn.create_finding(self.q, f)

    def _msg(self, mid, text, at=FUTURE):
        comm = os.path.join(self.q, "communications")
        os.makedirs(comm, exist_ok=True)
        with open(os.path.join(comm, mid + ".json"), "w", encoding="utf-8") as fh:
            json.dump({"message_id": mid, "role": "operator", "direction": "inbound",
                       "at": at, "message": text}, fh)
        return mid

    def test_message_binding_requires_whole_token(self):
        eid = self._promotable()  # ALF-0001
        rev.surface_for_review(self.q, eid)
        # substring match but NOT a distinct token -> refused
        mid = self._msg("m1", "Reject ALF-00012 for reasons")
        with self.assertRaises(alf.AlfError):
            rev.dispose(self.q, eid, "REJECTED", mid)
        # a genuine whole-token mention works
        mid2 = self._msg("m2", "Reject {} now".format(eid))
        self.assertTrue(rev.dispose(self.q, eid, "REJECTED", mid2)["disposed"])

    def test_conf_type_safe(self):
        self.assertTrue(rev._conf_at_least(0.9, 0.5))
        self.assertTrue(rev._conf_at_least("0.90", 0.5))
        self.assertFalse(rev._conf_at_least(None, 0.5))
        self.assertFalse(rev._conf_at_least("abc", 0.5))
        self.assertFalse(rev._conf_at_least(0.3, 0.5))

    def test_promotion_gate_malformed_conf_no_crash(self):
        problems = rev.promotion_gate_problems({
            "permanent_resolution": "x", "objective_acceptance_criteria": "y",
            "required_regression_tests": "z", "dependencies": [], "blockers": [],
            "root_cause_confidence": "not-a-number",
            "evidence_references": [{"role": "observed_occurrence"}]})
        self.assertTrue(any("root_cause_confidence" in p for p in problems))


if __name__ == "__main__":
    unittest.main()
