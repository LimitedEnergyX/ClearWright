"""Round-2 verification evidence (reviewer-requested): enabler-wiring behavior in
run_round, classifier no-leak, malformed/attacker preallocation_signals, multi-target
partial-journal recovery, and a deterministic external-effect audit."""
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "tools"))
import clearwright_alf as alf  # noqa: E402
import clearwright_alf_synth as syn  # noqa: E402
import clearwright_alf_delta as dlt  # noqa: E402
import clearwright_dispatch_preflight as cwdp  # noqa: E402
import clearwright_review_council as council  # noqa: E402


def _failed_attempt(classification="timeout"):
    # An unposted (failed) reviewer result: not validated -> counts as a failed
    # attempt, mirroring the codex "not posted" shape in test_review_council.
    return {"ok": True, "posted": False, "classification": classification,
            "telemetry": {}}


class EnablerWiringTest(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="alf-hw-")

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def _council(self, **kw):
        return council.create_council(self.root, thread_id="thr-t", work_item_id="wi-t",
                                      phase="verify", approved_scope="test scope",
                                      data_sensitivity="standard", **kw)

    def test_enabler_b_refuses_before_any_attempt(self):
        c = self._council()
        c["preallocation_signals"] = {"repo_approved": False}

        def _must_not_call(*a, **k):
            raise AssertionError("reviewer called despite pre-allocation refusal")

        r = council.run_round(self.root, c, "ctx", sleep=lambda *_: None,
                              gpt_fn=_must_not_call, codex_fn=_must_not_call)
        self.assertTrue(r.get("preallocation_refused"))
        self.assertEqual(r.get("normalized_reason"), "repo_not_approved")
        self.assertEqual(r["attempts"], {})  # no council id / attempt consumed

    def test_absent_signals_do_not_refuse_and_proceed_to_send_path(self):
        # An ordinary council (no preallocation_signals) must NOT be short-circuited;
        # it proceeds to the attempt/send path where the egress guard is the real gate.
        c = self._council()
        calls = {"n": 0}

        def _f(root, pt, **k):
            calls["n"] += 1
            return _failed_attempt()

        r = council.run_round(self.root, c, "ctx", sleep=lambda *_: None,
                              gpt_fn=_f, codex_fn=_f)
        self.assertFalse(r.get("preallocation_refused"))
        self.assertGreater(calls["n"], 0)

    def test_attacker_signals_can_only_refuse_never_bypass(self):
        # No preallocation_signals value can turn a refusal into an allow at the guard;
        # a positive blocker only refuses (fail-safe direction), and malformed values
        # never crash or invert the decision.
        self.assertEqual(cwdp.dispatch_eligibility({"repo_approved": False})[0], False)
        self.assertEqual(cwdp.dispatch_eligibility({"repo_approved": 0})[0], False)   # falsy
        self.assertEqual(cwdp.dispatch_eligibility({"sensitive_prohibited": "yes"})[0], False)  # truthy -> refuse
        self.assertEqual(cwdp.dispatch_eligibility({"repo_approved": object()})[0], True)  # truthy ok-signal passes
        self.assertEqual(cwdp.dispatch_eligibility({})[0], True)  # absent -> guard decides at SEND

    def test_enabler_a_records_normalized_reason(self):
        c = self._council()

        def _f(root, pt, **k):
            return _failed_attempt("request timed out")

        r = council.run_round(self.root, c, "ctx", sleep=lambda *_: None,
                              gpt_fn=_f, codex_fn=_f)
        self.assertIn("normalized_reasons", r)
        allr = (r["normalized_reasons"].get("gpt", [])
                + r["normalized_reasons"].get("codex", []))
        self.assertIn("timeout", allr)


class ClassifierNoLeakTest(unittest.TestCase):
    def test_never_returns_raw_text(self):
        secret = "sk-live-ABC123 secret token bearer 429 rate limit"
        cls = cwdp.classify_reviewer_failure({"error": secret})
        self.assertIn(cls, cwdp.NORMALIZED_FAILURE_CLASSES)
        self.assertNotIn("sk-live", cls)
        self.assertNotIn("ABC123", cls)

    def test_body_verdict_content_fields_never_read(self):
        # Only safe fields (error/classification/reason/error_class/code) are read;
        # a secret hidden in body/verdict/content cannot influence or leak into output.
        cls = cwdp.classify_reviewer_failure(
            {"body": "sk-secret 429", "verdict": {"x": "timeout"},
             "content": "authorization 401", "stderr": "traceback secret"})
        self.assertEqual(cls, "unknown")

    def test_output_is_always_a_fixed_class(self):
        for probe in (None, {}, {"error": "boom"}, {"reason": "weird"},
                      {"classification": "egress_blocked"}):
            self.assertIn(cwdp.classify_reviewer_failure(probe),
                          cwdp.NORMALIZED_FAILURE_CLASSES)

    def test_refused_record_truncates_and_has_no_council_id(self):
        rec = cwdp.refused_dispatch_record(phase="verify", dispatch_lane="user",
                                           normalized_reason="repo_not_approved",
                                           detail="x" * 1000)
        self.assertIsNone(rec["council_id"])
        self.assertEqual(rec["attempt"], 0)
        self.assertLessEqual(len(rec["detail"]), 200)


class MultiTargetRecoveryTest(unittest.TestCase):
    def setUp(self):
        self.q = tempfile.mkdtemp(prefix="alf-mt-")
        alf.ensure_layout(self.q)

    def tearDown(self):
        shutil.rmtree(self.q, ignore_errors=True)

    def test_multi_target_partial_recovery(self):
        # Two targets staged; the FIRST is applied; crash before op_commit; recovery
        # completes the SECOND exactly once and commits (no double-apply of the first).
        idx, occ = alf.index_path(self.q), alf.occurrences_path(self.q)
        p1, c1 = alf.chain_head(idx)
        rec1 = alf.chained_record({"alf_record_version": 1, "observation_id": "obs-a",
                                   "sha256": "a" * 64, "captured_at": alf.now_iso(),
                                   "run_id": "r", "kind": "executor_note"}, p1)
        line1 = alf.canonical_line(rec1)
        p2, c2 = alf.chain_head(occ)
        rec2 = alf.chained_record({"alf_record_version": 1, "occurrence_id": "occ-a",
                                   "observation_id": "obs-a", "run_id": "r",
                                   "captured_at": alf.now_iso(),
                                   "capture_method": "cli_explicit",
                                   "capturing_actor": "t", "metrics": None}, p2)
        line2 = alf.canonical_line(rec2)
        op_id = "op-multitarget01"
        sdir = alf.staged_dir(self.q, op_id)
        os.makedirs(sdir, exist_ok=True)
        sf1 = "0-" + alf.sha256_hex(line1)[:16]
        sf2 = "1-" + alf.sha256_hex(line2)[:16]
        alf._write_bytes_fsync(os.path.join(sdir, sf1), line1)
        alf._write_bytes_fsync(os.path.join(sdir, sf2), line2)
        alf._append_line_fsync(idx, line1)  # apply ONLY the first target
        jpath = alf.journal_path(self.q)
        jp, _ = alf.chain_head(jpath)
        begin = alf.chained_record({
            "op_id": op_id, "operation_kind": "test", "subject_ids": ["x"],
            "staged_writes": [
                {"target_path_rel": "observations/index.jsonl", "staged_file": sf1,
                 "content_sha256": alf.sha256_hex(line1), "write_kind": "append_line",
                 "expected_prev_line_sha256": p1, "expected_chain_position": c1 + 1},
                {"target_path_rel": "observations/occurrences.jsonl", "staged_file": sf2,
                 "content_sha256": alf.sha256_hex(line2), "write_kind": "append_line",
                 "expected_prev_line_sha256": p2, "expected_chain_position": c2 + 1}],
            "at": alf.now_iso(), "event": "op_begin"}, jp)
        alf._append_line_fsync(jpath, alf.canonical_line(begin))

        report = alf.recover(self.q)
        self.assertIn(op_id, report["recovered"])
        i, _ = alf._read_valid_lines(idx)
        o, _ = alf._read_valid_lines(occ)
        self.assertEqual(sum(1 for r in i if r.get("observation_id") == "obs-a"), 1)
        self.assertEqual(sum(1 for r in o if r.get("occurrence_id") == "occ-a"), 1)
        self.assertEqual(alf.recover(self.q)["recovered"], [])  # idempotent


class ExternalEffectAuditTest(unittest.TestCase):
    ALF_MODULES = ["clearwright_alf.py", "clearwright_alf_synth.py",
                   "clearwright_alf_delta.py", "clearwright_alf_review.py",
                   "clearwright_alf_seed.py", "clearwright_alf_gqfixture.py",
                   "clearwright_dispatch_preflight.py"]
    FORBIDDEN = ["import subprocess", "import socket", "import urllib",
                 "import requests", "os.system(", "os.popen(", "subprocess.",
                 "Popen(", "urlopen(", "api.github", "github.com", "gh api"]

    def test_no_external_effect_calls_in_alf_modules(self):
        tools = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "tools")
        for mod in self.ALF_MODULES:
            with open(os.path.join(tools, mod), encoding="utf-8") as fh:
                src = fh.read()
            for bad in self.FORBIDDEN:
                self.assertNotIn(bad, src, "{} references forbidden {!r}".format(mod, bad))

    def test_all_writes_composed_under_alf_root(self):
        q = tempfile.mkdtemp(prefix="alf-root-")
        try:
            root = alf.alf_root(q)
            for p in (alf.observation_file(q, "obs-x"), alf.index_path(q),
                      alf.occurrences_path(q), alf.ledger_path(q),
                      alf.journal_path(q), alf.checkpoint_path(q),
                      dlt.delta_path(q, "r"), syn.finding_head_path(q, "ALF-0001"),
                      syn.finding_history_path(q, "ALF-0001"), syn.model_path(q)):
                self.assertTrue(os.path.abspath(p).startswith(os.path.abspath(root)),
                                "{} escapes alf root".format(p))
        finally:
            shutil.rmtree(q, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
