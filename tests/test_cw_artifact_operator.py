"""Tests for the artifact & operator layer (PR 2 of the acceptance-hardening
design): artifact registration/provenance, capability-aware reviewer delivery,
the blocked_by_capability disposition invariants, the completion gate and
CLOSED_BY_OPERATOR closure, and the harness-generated canonical summary.

Reviewers are always mocked; no network and no real Codex run here.
"""
import contextlib
import io
import json
import os
import re
import shutil
import sys
import tempfile
import unittest
from argparse import Namespace

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APP_DIR = os.path.join(REPO_ROOT, "apps", "control-plane")
TOOLS_DIR = os.path.join(REPO_ROOT, "tools")

sys.path.insert(0, APP_DIR)
sys.path.insert(0, TOOLS_DIR)
import server  # noqa: E402
import clearwright_message as cwm  # noqa: E402
import clearwright_verdict as cwv  # noqa: E402
import clearwright_artifacts as cwa  # noqa: E402
import clearwright_review_council as cwrc  # noqa: E402
import clearwright_use_cw as ucw  # noqa: E402


def queue(prefix, tc):
    base = tempfile.mkdtemp(prefix=prefix)
    tc.addCleanup(shutil.rmtree, base, ignore_errors=True)
    root, *_ = server.resolve_queue(base)
    return root


def stub_preflight(tc):
    import clearwright_gpt_review as gpt_mod
    import clearwright_codex_review as ccr_mod
    orig_key, orig_exe = gpt_mod.resolve_api_key, ccr_mod.codex_executable
    gpt_mod.resolve_api_key = lambda *a, **k: ("test-key-present", "process_env")
    ccr_mod.codex_executable = lambda: "codex-stub"
    tc.addCleanup(setattr, gpt_mod, "resolve_api_key", orig_key)
    tc.addCleanup(setattr, ccr_mod, "codex_executable", orig_exe)


def run(func, **kw):
    kw.setdefault("json", True)
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        code = func(Namespace(**kw))
    return json.loads(buf.getvalue().strip().splitlines()[-1]), code


def make_verdict(reviewer, verdict="approve", conf=0.9, req=None):
    return {"reviewer": reviewer, "verdict": verdict, "confidence": conf,
            "risk_level": "low", "blocking_findings": [],
            "required_changes": list(req or []), "nonblocking_findings": [],
            "disagreements": [], "assumptions": [], "questions": [],
            "recommended_plan": [], "summary": "A substantive review."}


def reviewer_fn(reviewer, source, capture=None, verdict="approve", req=None):
    def fn(root, context, **kw):
        if capture is not None:
            capture.append(context)
        return {"ok": True, "posted": True, "reviewer": reviewer,
                "verdict": make_verdict(reviewer, verdict, req=req),
                "validated": True, "source": source,
                "telemetry": {"reviewer": reviewer}, "message_id": reviewer[0]}
    return fn


class ArtifactRegistryTests(unittest.TestCase):

    def setUp(self):
        self.root = queue("cwart_", self)

    def _file(self, content, name="sample.html"):
        path = os.path.join(self.root, name)
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(content)
        return path

    def test_register_pins_with_full_sha_identity(self):
        meta = cwa.register(self.root, self._file("<html>line one</html>\nline two"))
        self.assertEqual(len(meta["sha256"]), 64)  # FULL sha is the identity
        self.assertTrue(meta["artifact_id"].startswith("art-"))
        self.assertTrue(os.path.isfile(meta["pinned_path"]))
        # Content-addressed: same content re-registers as a no-op.
        again = cwa.register(self.root, self._file("<html>line one</html>\nline two", "copy.html"))
        self.assertEqual(again["artifact_id"], meta["artifact_id"])

    def test_verify_detects_tampering(self):
        meta = cwa.register(self.root, self._file("original bytes"))
        self.assertEqual(cwa.verify(self.root, meta["artifact_id"])["sha256"], meta["sha256"])
        with open(meta["pinned_path"], "w", encoding="utf-8") as fh:
            fh.write("tampered bytes")
        with self.assertRaises(cwa.ArtifactError):
            cwa.verify(self.root, meta["artifact_id"])

    def test_derived_renderings_carry_their_own_linked_hashes(self):
        meta = cwa.register(self.root, self._file("alpha\nbeta\ngamma"))
        text, rec = cwa.inline_rendering(self.root, meta["artifact_id"])
        self.assertEqual(rec["derived_from"], meta["sha256"])
        self.assertNotEqual(rec["sha256"], meta["sha256"])
        self.assertIn(rec["sha256"], text)  # header names the derived hash
        self.assertIn("     1  alpha", text)
        pack, prec = cwa.excerpt_pack(self.root, meta["artifact_id"], 4000)
        self.assertEqual(prec["derived_from"], meta["sha256"])
        self.assertIn("ONLY EVIDENCE YOU MAY RELY ON", pack)

    def test_codex_reference_block_names_path_and_hash(self):
        meta = cwa.register(self.root, self._file("payload"))
        block = cwa.codex_reference_block(self.root, [meta["artifact_id"]])
        self.assertIn(meta["pinned_path"], block)
        self.assertIn(meta["sha256"], block)
        self.assertIn("artifact_id:line", block)


class PackagingTests(unittest.TestCase):

    def setUp(self):
        self.root = queue("cwpack_", self)
        res = server.do_message(self.root, {"actor": "OPERATOR-0001", "role": "operator",
                                            "message": "Review this", "intent": "request"})
        self.thread = res["thread_id"]

    def _artifact(self, size_lines):
        path = os.path.join(self.root, "doc.html")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write("\n".join("content line {} {}".format(i, "x" * 50)
                               for i in range(size_lines)))
        return cwa.register(self.root, path)

    def test_small_artifact_is_inlined_for_gpt_and_pathed_for_codex(self):
        meta = self._artifact(50)
        gcap, ccap = [], []
        c = cwrc.create_council(self.root, thread_id=self.thread, approved_scope="s")
        report = cwrc.run_round(
            self.root, c, "review the artifact", sleep=lambda *_: None,
            artifact_ids=[meta["artifact_id"]],
            gpt_fn=reviewer_fn("gpt", "openai-api", capture=gcap),
            codex_fn=reviewer_fn("codex", "codex-cli", capture=ccap))
        self.assertTrue(report["committed"])
        self.assertEqual(report["gpt_delivery"], "inline_full")
        # GPT: capability statement + full line-numbered artifact inline.
        self.assertIn("CANNOT access local files", gcap[0])
        self.assertIn("FULL, line-numbered", gcap[0])
        self.assertIn(meta["sha256"], gcap[0])
        # Codex: path + expected hash, artifact NOT inlined into the prompt.
        self.assertIn(meta["pinned_path"], ccap[0])
        self.assertIn(meta["sha256"], ccap[0])
        self.assertNotIn("FULL, line-numbered", ccap[0])
        # The committed round records delivery + artifact provenance.
        rounds = cwrc.load_rounds(self.root, c["council_id"])
        self.assertEqual(rounds[-1]["artifact_hashes"], [meta["sha256"]])
        self.assertEqual(rounds[-1]["delivery"]["gpt"], "inline_full")

    def test_oversized_artifact_falls_back_to_excerpt_pack(self):
        # ~40K lines * ~65 chars ≈ 2.6 MB — far over the 32K-token plan budget.
        meta = self._artifact(40000)
        gcap = []
        c = cwrc.create_council(self.root, thread_id=self.thread, approved_scope="s")
        report = cwrc.run_round(
            self.root, c, "review the artifact", sleep=lambda *_: None,
            artifact_ids=[meta["artifact_id"]],
            gpt_fn=reviewer_fn("gpt", "openai-api", capture=gcap),
            codex_fn=reviewer_fn("codex", "codex-cli"))
        self.assertTrue(report["committed"])
        self.assertEqual(report["gpt_delivery"], "excerpt_pack")
        self.assertIn("EXCERPT PACK", gcap[0])
        self.assertIn("ONLY EVIDENCE YOU MAY RELY ON", gcap[0])
        self.assertIn(meta["sha256"], gcap[0])  # full-artifact hash in manifest
        # The excerpt-pack packet itself respects the phase budget.
        self.assertLessEqual(cwrc.estimate_tokens(len(gcap[0])),
                             cwrc.phase_input_budget("plan"))

    def test_tampered_artifact_is_a_hard_stop_before_dispatch(self):
        meta = self._artifact(10)
        with open(meta["pinned_path"], "w", encoding="utf-8") as fh:
            fh.write("tampered")
        cap = []
        c = cwrc.create_council(self.root, thread_id=self.thread, approved_scope="s")
        report = cwrc.run_round(self.root, c, "ctx", sleep=lambda *_: None,
                                artifact_ids=[meta["artifact_id"]],
                                gpt_fn=reviewer_fn("gpt", "openai-api", capture=cap),
                                codex_fn=reviewer_fn("codex", "codex-cli", capture=cap))
        self.assertTrue(report.get("hard_gate"))
        self.assertIn("artifact", report["reason"])
        self.assertEqual(cap, [])  # nothing dispatched

    def test_artifacts_are_remembered_on_the_council(self):
        meta = self._artifact(10)
        c = cwrc.create_council(self.root, thread_id=self.thread, approved_scope="s")
        cwrc.run_round(self.root, c, "ctx", sleep=lambda *_: None,
                       artifact_ids=[meta["artifact_id"]],
                       gpt_fn=reviewer_fn("gpt", "openai-api"),
                       codex_fn=reviewer_fn("codex", "codex-cli"))
        c2 = cwrc.load_council(self.root, c["council_id"])
        self.assertEqual(c2["artifact_ids"], [meta["artifact_id"]])
        # Round two reuses them without re-passing.
        gcap = []
        cwrc.run_round(self.root, c2, "ctx round two", sleep=lambda *_: None,
                       gpt_fn=reviewer_fn("gpt", "openai-api", capture=gcap),
                       codex_fn=reviewer_fn("codex", "codex-cli"))
        self.assertIn(meta["sha256"], gcap[0])


class BlockedByCapabilityTests(unittest.TestCase):

    def setUp(self):
        self.root = queue("cwblk_", self)
        res = server.do_message(self.root, {"actor": "OPERATOR-0001", "role": "operator",
                                            "message": "Plan", "intent": "request"})
        self.thread = res["thread_id"]

    def _council_with_round(self, req=("make the artifact readable",)):
        c = cwrc.create_council(self.root, thread_id=self.thread,
                                approved_scope="scope")
        cwrc.run_round(self.root, c, "ctx", sleep=lambda *_: None,
                       gpt_fn=reviewer_fn("gpt", "openai-api", req=req),
                       codex_fn=reviewer_fn("codex", "codex-cli"))
        return cwrc.load_council(self.root, c["council_id"])

    def _blocked_recon(self, ready=False):
        return {"accepted_findings": [], "rejected_findings": [],
                "required_plan_changes": [], "revised_plan": [],
                "unresolved_blockers": [], "ready_to_proceed": ready,
                "resolutions": [{"ref": "gpt.required_changes[0]",
                                 "disposition": "blocked_by_capability",
                                 "limitation": "the reviewer transport cannot carry this artifact",
                                 "evidence": ["adapter ceiling measured and recorded"]}],
                "summary": "the reviewer is right and the harness cannot satisfy it"}

    def test_blocked_requires_limitation_and_evidence(self):
        with self.assertRaises(cwv.VerdictError):
            cwv.validate_reconciliation({
                "accepted_findings": [], "rejected_findings": [],
                "required_plan_changes": [], "revised_plan": [],
                "unresolved_blockers": [], "ready_to_proceed": False,
                "resolutions": [{"ref": "gpt.required_changes[0]",
                                 "disposition": "blocked_by_capability",
                                 "evidence": []}],
                "summary": "missing limitation and evidence"})

    def test_blocked_cannot_coexist_with_ready_to_proceed(self):
        with self.assertRaises(cwv.VerdictError):
            cwv.validate_reconciliation(self._blocked_recon(ready=True))

    def test_blocked_escalates_operator_required_and_counts_the_round(self):
        c = self._council_with_round()
        cwrc.attach_reconciliation(self.root, c, self._blocked_recon())
        out = cwrc.evaluate(cwrc.load_council(self.root, c["council_id"]),
                            cwrc.load_rounds(self.root, c["council_id"]))
        self.assertEqual(out["outcome"], "operator_required")
        self.assertTrue(out["operator_required"])
        self.assertEqual(out["capability_blocked_refs"], ["gpt.required_changes[0]"])
        # The completed round remains counted (it happened).
        self.assertEqual(out["current_round"], 1)

    def test_blocked_can_never_contribute_to_agreement(self):
        # Even with two committed rounds and everything else satisfied, a
        # capability block forces operator_required, never agreement.
        c = self._council_with_round()
        cwrc.run_round(self.root, c, "ctx", sleep=lambda *_: None,
                       gpt_fn=reviewer_fn("gpt", "openai-api", req=("make it readable",)),
                       codex_fn=reviewer_fn("codex", "codex-cli"))
        c2 = cwrc.load_council(self.root, c["council_id"])
        cwrc.attach_reconciliation(self.root, c2, self._blocked_recon())
        out = cwrc.evaluate(cwrc.load_council(self.root, c["council_id"]),
                            cwrc.load_rounds(self.root, c["council_id"]))
        self.assertEqual(out["outcome"], "operator_required")
        self.assertNotEqual(out["outcome"], "agreement_threshold_met")
        self.assertFalse(out["ready_to_proceed"])


class CompletionAndClosureTests(unittest.TestCase):

    def setUp(self):
        self.root = queue("cwdone_", self)
        stub_preflight(self)
        res, _ = run(ucw.cmd_start, queue_root=self.root, envelope_file=None,
                     request="Fix the widget carefully.", request_file=None,
                     kind="actionable", thread_id=None, packet_id=None,
                     approved_scope="fix the widget", actor="claude")
        self.wid = res["work_item_id"]
        self.thread = res["thread_id"]
        self.assertTrue(res["verification_required"])

    def _verify_council(self, outcome_ok):
        c = cwrc.create_council(self.root, thread_id=self.thread,
                                work_item_id=self.wid, phase="verify",
                                approved_scope="fix the widget")
        for _ in range(2):
            cwrc.run_round(self.root, c, "evidence ctx", sleep=lambda *_: None,
                           gpt_fn=reviewer_fn("gpt", "openai-api"),
                           codex_fn=reviewer_fn("codex", "codex-cli"))
            c = cwrc.load_council(self.root, c["council_id"])
        recon = {"accepted_findings": ["ok"], "rejected_findings": [],
                 "required_plan_changes": [], "revised_plan": ["done"],
                 "unresolved_blockers": [] if outcome_ok else ["still broken"],
                 "resolutions": [], "ready_to_proceed": outcome_ok,
                 "summary": "verification reconciliation for the fix"}
        cwrc.attach_reconciliation(self.root, c, recon)
        out = cwrc.evaluate(cwrc.load_council(self.root, c["council_id"]),
                            cwrc.load_rounds(self.root, c["council_id"]))
        cwrc.save_outcome(self.root, c["council_id"], out)
        return c["council_id"], out["outcome"]

    def test_required_verification_never_run_refuses_done(self):
        res, code = run(ucw.cmd_complete, queue_root=self.root, work_item_id=self.wid,
                        packet_id=None, result="done!", result_file=None)
        self.assertEqual(code, ucw.EXIT_OPERATOR)
        self.assertEqual(res["status"], "verification_incomplete")
        self.assertIn("never bypasses", res["detail"])
        self.assertTrue(res["summary_posted"])

    def test_unpassed_verify_council_refuses_done(self):
        cid, outcome = self._verify_council(outcome_ok=False)
        self.assertNotEqual(outcome, "agreement_threshold_met")
        res, code = run(ucw.cmd_complete, queue_root=self.root, work_item_id=self.wid,
                        packet_id=None, result="done!", result_file=None)
        self.assertEqual(code, ucw.EXIT_OPERATOR)
        self.assertEqual(res["verify_council_id"], cid)

    def test_passed_verify_council_allows_done(self):
        cid, outcome = self._verify_council(outcome_ok=True)
        self.assertEqual(outcome, "agreement_threshold_met")
        res, code = run(ucw.cmd_complete, queue_root=self.root, work_item_id=self.wid,
                        packet_id=None, result="done and verified", result_file=None)
        self.assertEqual(code, ucw.EXIT_OK)
        self.assertEqual(res["status"], "done")

    def _close(self, operator_message_id, reason="operator accepts as-is"):
        return run(ucw.cmd_close, queue_root=self.root, work_item_id=self.wid,
                   operator="OPERATOR-0001", reason=reason,
                   operator_message_id=operator_message_id)

    def test_close_requires_closure_specific_authority(self):
        cid, _ = self._verify_council(outcome_ok=False)
        # The original task approval is NOT sufficient authority.
        vague = server.do_message(self.root, {
            "actor": "OPERATOR-0001", "role": "operator", "direction": "inbound",
            "intent": "chat", "message": "Task approved, proceed with the fix."})
        res, code = self._close(vague["message"]["message_id"])
        self.assertEqual(code, ucw.EXIT_USAGE)
        self.assertIn("not sufficient", res["error"])
        # A compliant closure record: names the work item, authorizes closing,
        # created after the failed outcome.
        auth = server.do_message(self.root, {
            "actor": "OPERATOR-0001", "role": "operator", "direction": "inbound",
            "intent": "chat",
            "message": "Close work item {} with verification incomplete; I accept "
                       "the result as-is.".format(self.wid)})
        res, code = self._close(auth["message"]["message_id"])
        self.assertEqual(code, ucw.EXIT_OK)
        self.assertEqual(res["status"], "closed_by_operator")
        self.assertEqual(res["outcome"], "accepted_with_verification_incomplete")
        self.assertEqual(res["verify_council_id"], cid)
        self.assertNotEqual(res["status"], "done")  # never presented as DONE
        # The closing message carries the closure marker + meta durably.
        closing = [m for m in cwm.read_messages(self.root)
                   if m.get("closure") == "closed_by_operator"]
        self.assertEqual(len(closing), 1)
        self.assertEqual(closing[0]["closure_meta"]["operator_message_id"],
                         auth["message"]["message_id"])
        # And the canonical summary states it verbatim.
        summary = ucw.load_summary(self.root, self.wid)
        self.assertEqual(summary["status"], "closed_by_operator")
        self.assertEqual(summary["outcome_line"],
                         "Closed by operator with verification incomplete.")

    def test_close_refused_when_verification_passed(self):
        self._verify_council(outcome_ok=True)
        auth = server.do_message(self.root, {
            "actor": "OPERATOR-0001", "role": "operator", "direction": "inbound",
            "intent": "chat", "message": "Close work item {}.".format(self.wid)})
        res, code = self._close(auth["message"]["message_id"])
        self.assertEqual(code, ucw.EXIT_USAGE)
        self.assertIn("use complete", res["error"])


class SummaryAndRetrospectiveTests(unittest.TestCase):

    def setUp(self):
        self.root = queue("cwsum_", self)
        stub_preflight(self)
        res, _ = run(ucw.cmd_start, queue_root=self.root, envelope_file=None,
                     request="Analyze the report structure.", request_file=None,
                     kind="analysis", thread_id=None, packet_id=None,
                     approved_scope="analysis only", actor="claude")
        self.wid = res["work_item_id"]

    def test_complete_posts_harness_summary(self):
        res, code = run(ucw.cmd_complete, queue_root=self.root, work_item_id=self.wid,
                        packet_id=None, result="analysis recorded", result_file=None)
        self.assertEqual(code, ucw.EXIT_OK)
        self.assertTrue(res["summary_posted"])
        summary = ucw.load_summary(self.root, self.wid)
        self.assertEqual(summary["generated_by"], "harness")
        self.assertEqual(summary["status"], "done_unverified_not_required")
        self.assertIn("usage", summary)
        # The durable CW message exists and is harness-authored.
        msgs = [m for m in cwm.read_messages(self.root)
                if m.get("source") == "use-cw-summary"]
        self.assertEqual(len(msgs), 1)
        self.assertTrue(msgs[0]["message"].startswith("CW canonical summary"))

    def test_status_summary_returns_the_record(self):
        run(ucw.cmd_complete, queue_root=self.root, work_item_id=self.wid,
            packet_id=None, result="analysis recorded", result_file=None)
        res, code = run(ucw.cmd_status, queue_root=self.root, council_id=None,
                        thread_id=None, summary=self.wid)
        self.assertEqual(code, ucw.EXIT_OK)
        self.assertEqual(res["summary"]["work_item_id"], self.wid)

    def test_retrospective_reports_usage_and_failures(self):
        cwrc.log_invocation(self.root, {"command": "council-dispatch",
                                        "work_item_id": self.wid, "reviewer": "codex",
                                        "round": 1, "attempt": 2,
                                        "error_class": "timeout",
                                        "estimated_input_tokens": 500})
        res, code = run(ucw.cmd_retrospective, queue_root=self.root,
                        work_item_id=self.wid)
        self.assertEqual(code, ucw.EXIT_OK)
        self.assertEqual(res["usage"]["transport_retries"], 1)
        self.assertEqual(res["failures"][0]["error_class"], "timeout")


class NamingAndPrivacyTests(unittest.TestCase):

    def test_no_private_target_or_retired_terms(self):
        _wr = "w" + "rit"
        retired = re.compile("|".join([r"\b" + _wr + r"\b", "vol" + "tex"]), re.I)
        private = re.compile("|".join([r"\b" + "pl" + "ex" + r"\b",
                                       "d:" + re.escape("\\") + "dev"]), re.I)
        targets = [os.path.join(TOOLS_DIR, "clearwright_artifacts.py"),
                   os.path.join(TOOLS_DIR, "clearwright_use_cw.py"),
                   os.path.join(TOOLS_DIR, "clearwright_review_council.py"),
                   os.path.join(TOOLS_DIR, "clearwright_verdict.py"),
                   os.path.join(TOOLS_DIR, "clearwright_message.py"),
                   os.path.abspath(__file__)]
        for path in targets:
            with self.subTest(file=os.path.relpath(path, REPO_ROOT)):
                text = open(path, encoding="utf-8").read()
                self.assertIsNone(retired.search(text))
                self.assertIsNone(private.search(text))


if __name__ == "__main__":
    unittest.main()
