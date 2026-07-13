"""Tests for PR #25: the automated GPT + Codex Review Council.

GPT and Codex are ALWAYS mocked here (injected transport / injected runner);
no real network call and no real Codex process ever run in the test suite. The
separately authorized live smokes are run by hand, not by unittest.

Coverage: the shared structured-verdict contract, the GPT adapter (key safety,
validation, retries), the Codex structured adapter, the council engine
(independence, reconciliation, the deterministic agreement rule, storage and
reload), the read-only API, the UI, and health capability booleans, plus PR #24
regression and the naming/privacy gates (no retired or private target terms).
"""
import json
import os
import re
import shutil
import sys
import tempfile
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APP_DIR = os.path.join(REPO_ROOT, "apps", "control-plane")
TOOLS_DIR = os.path.join(REPO_ROOT, "tools")
STATIC = os.path.join(APP_DIR, "static")
DOCS = os.path.join(REPO_ROOT, "docs")

sys.path.insert(0, APP_DIR)
sys.path.insert(0, TOOLS_DIR)
import server  # noqa: E402
import clearwright_message as cwm  # noqa: E402
import clearwright_work as cww  # noqa: E402
import clearwright_verdict as cwv  # noqa: E402
import clearwright_gpt_review as gpt  # noqa: E402
import clearwright_codex_review as ccr  # noqa: E402
import clearwright_review_council as council  # noqa: E402

# A canary "key" value: it must NEVER appear in any result, record, or file.
FAKE_KEY = "sk-CANARY_must_never_leak_0123456789abcdefABCDEF"


def read(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def queue(prefix, tc):
    base = tempfile.mkdtemp(prefix=prefix)
    tc.addCleanup(shutil.rmtree, base, ignore_errors=True)
    root, *_ = server.resolve_queue(base)
    return root


def make_verdict(reviewer, verdict="approve", confidence=0.9, risk="low",
                 blocking=None, summary="A substantive review with reasoning."):
    return {
        "reviewer": reviewer, "verdict": verdict, "confidence": confidence,
        "risk_level": risk, "blocking_findings": blocking or [],
        "required_changes": [], "nonblocking_findings": [], "disagreements": [],
        "assumptions": [], "questions": [], "recommended_plan": [], "summary": summary,
    }


def gpt_transport(verdict_obj=None, *, status=200, model="gpt-5.6-terra",
                  resp_id="resp_test123", body_text=None, calls=None):
    """A fake OpenAI transport. Records calls (to count retries) if `calls` is a
    list. Returns the given verdict as the model's structured output."""
    if body_text is None:
        payload = {"id": resp_id, "model": model,
                   "output": [{"type": "message",
                               "content": [{"type": "output_text",
                                            "text": json.dumps(verdict_obj) if verdict_obj is not None else ""}]}]}
        body_text = json.dumps(payload)

    def transport(url, headers, body_bytes, timeout):
        if calls is not None:
            calls.append({"auth_present": "Authorization" in headers})
        return status, body_text
    return transport


def codex_runner(verdict_obj=None, *, timed_out=False, exit_code=0, raw=None):
    """A fake Codex runner matching run_codex's (prompt, timeout, cwd) -> (output,
    telemetry) contract."""
    text = raw if raw is not None else (json.dumps(verdict_obj) if verdict_obj is not None else "")

    def runner(prompt, timeout, cwd=None):
        tel = ccr.build_telemetry(text, None if timed_out else exit_code,
                                  float(timeout), timed_out=timed_out)
        return text, tel
    return runner


# --------------------------------------------------------------------------- #

class VerdictContractTests(unittest.TestCase):

    def test_valid_verdict_normalizes(self):
        v = cwv.validate_verdict(make_verdict("gpt"), reviewer="gpt")
        self.assertEqual(v["verdict"], "approve")
        self.assertEqual(v["reviewer"], "gpt")

    def test_invalid_enum_rejected(self):
        with self.assertRaises(cwv.VerdictError):
            cwv.validate_verdict(make_verdict("gpt", verdict="lgtm"))

    def test_invalid_confidence_rejected(self):
        with self.assertRaises(cwv.VerdictError):
            cwv.validate_verdict(make_verdict("gpt", confidence=1.7))
        with self.assertRaises(cwv.VerdictError):
            cwv.validate_verdict(make_verdict("gpt", confidence="high"))

    def test_non_array_field_rejected(self):
        bad = make_verdict("gpt")
        bad["required_changes"] = "not a list"
        with self.assertRaises(cwv.VerdictError):
            cwv.validate_verdict(bad)

    def test_empty_summary_rejected(self):
        with self.assertRaises(cwv.VerdictError):
            cwv.validate_verdict(make_verdict("gpt", summary="x"))

    def test_reviewer_mismatch_rejected(self):
        with self.assertRaises(cwv.VerdictError):
            cwv.validate_verdict(make_verdict("codex"), reviewer="gpt")

    def test_extract_json_from_fenced_and_prose(self):
        obj = cwv.extract_json_object('```json\n{"a": 1}\n```')
        self.assertEqual(obj["a"], 1)
        obj2 = cwv.extract_json_object('Here is my review: {"a": 2} thanks')
        self.assertEqual(obj2["a"], 2)

    def test_reconciliation_requires_evidence_for_rejections(self):
        good = {"accepted_findings": [], "rejected_findings": [
            {"finding": "f", "reason": "wrong because", "evidence": ["test output X"]}],
            "required_plan_changes": [], "revised_plan": [], "unresolved_blockers": [],
            "ready_to_proceed": True, "summary": "reconciled with evidence"}
        self.assertTrue(cwv.validate_reconciliation(good)["ready_to_proceed"])
        bad = dict(good, rejected_findings=[{"finding": "f", "reason": "no", "evidence": []}])
        with self.assertRaises(cwv.VerdictError):
            cwv.validate_reconciliation(bad)

    def test_reconciliation_ready_must_be_bool(self):
        with self.assertRaises(cwv.VerdictError):
            cwv.validate_reconciliation({"ready_to_proceed": "yes", "summary": "xxxxxxxx",
                                         "accepted_findings": [], "rejected_findings": [],
                                         "required_plan_changes": [], "revised_plan": [],
                                         "unresolved_blockers": []})


class GptAdapterTests(unittest.TestCase):

    def setUp(self):
        self.root = queue("cw_gpt_", self)
        res = server.do_message(self.root, {"actor": "OPERATOR-0001", "role": "operator",
                                            "message": "Plan to review", "intent": "request"})
        self.thread = res["thread_id"]

    def _gpt(self, **kw):
        base = dict(thread_id=self.thread, key_getter=lambda: FAKE_KEY,
                    sleep=lambda *_a, **_k: None, note_on_failure=False, model="gpt-5.6-terra")
        base.update(kw)
        return gpt.review(self.root, base.pop("context", "please review"), **base)

    def test_missing_key_fails_safe_and_posts_no_reviewer_message(self):
        res = self._gpt(key_getter=lambda: None, note_on_failure=False)
        self.assertFalse(res["ok"])
        self.assertEqual(res["error"], "missing_openai_api_key")
        self.assertTrue(res["hard_gate"])
        msgs = cwm.read_messages(self.root)
        self.assertFalse([m for m in msgs if m.get("actor") == "gpt"])

    def test_missing_key_never_creates_gpt_message_even_with_note(self):
        self._gpt(key_getter=lambda: None, note_on_failure=True)
        self.assertFalse([m for m in cwm.read_messages(self.root) if m.get("actor") == "gpt"])

    def test_reviewer_self_label_is_coerced_to_gpt(self):
        # A real HTTP 200 review whose model wrote a non-"gpt" reviewer label
        # (e.g. "GPT-5.6-terra") must still post: identity is authoritative from
        # the adapter, not the model's self-label. Regression for the JARVIS run.
        v = make_verdict("gpt")
        v["reviewer"] = "GPT-5.6-terra Reviewer"
        res = self._gpt(transport=gpt_transport(v))
        self.assertTrue(res["ok"] and res["posted"])
        self.assertEqual(res["verdict"]["reviewer"], "gpt")
        gpt_msgs = [m for m in cwm.read_messages(self.root) if m.get("actor") == "gpt"]
        self.assertEqual(len(gpt_msgs), 1)

    def test_still_rejects_a_genuinely_invalid_verdict(self):
        # Coercing the reviewer must not paper over other schema violations.
        v = make_verdict("gpt", verdict="ship_it")
        v["reviewer"] = "whatever"
        res = self._gpt(transport=gpt_transport(v))
        self.assertFalse(res["ok"])
        self.assertEqual(res["error"], "invalid_verdict")

    def test_successful_review_posts_and_records_actual_model(self):
        res = self._gpt(transport=gpt_transport(make_verdict("gpt"), model="gpt-5.6-terra-2026"))
        self.assertTrue(res["ok"] and res["posted"])
        self.assertEqual(res["telemetry"]["actual_model"], "gpt-5.6-terra-2026")
        self.assertEqual(res["telemetry"]["requested_model"], "gpt-5.6-terra")
        gpt_msgs = [m for m in cwm.read_messages(self.root) if m.get("actor") == "gpt"]
        self.assertEqual(len(gpt_msgs), 1)
        self.assertEqual(gpt_msgs[0]["source"], "openai-api")
        self.assertEqual(gpt_msgs[0]["role"], "reviewer")

    def test_empty_output_rejected(self):
        res = self._gpt(transport=gpt_transport(body_text=json.dumps(
            {"id": "r", "model": "m", "output": []})))
        self.assertFalse(res["ok"])
        self.assertEqual(res["error"], "empty_output")
        self.assertFalse([m for m in cwm.read_messages(self.root) if m.get("actor") == "gpt"])

    def test_malformed_json_rejected(self):
        res = self._gpt(transport=gpt_transport(body_text=json.dumps(
            {"id": "r", "model": "m", "output": [{"type": "message",
             "content": [{"type": "output_text", "text": "not json at all"}]}]})))
        self.assertFalse(res["ok"])
        self.assertIn(res["error"], ("malformed_output", "invalid_verdict"))
        self.assertFalse([m for m in cwm.read_messages(self.root) if m.get("actor") == "gpt"])

    def test_invalid_verdict_rejected(self):
        res = self._gpt(transport=gpt_transport(make_verdict("gpt", verdict="ship_it")))
        self.assertFalse(res["ok"])
        self.assertEqual(res["error"], "invalid_verdict")

    def test_transient_retry_limited_to_two(self):
        calls = []
        res = self._gpt(transport=gpt_transport(make_verdict("gpt"), status=500, calls=calls))
        self.assertFalse(res["ok"])
        # 1 initial attempt + 2 retries = 3 transport calls, no more.
        self.assertEqual(len(calls), 3)

    def test_transport_exception_is_bounded_and_safe(self):
        def boom(url, headers, body_bytes, timeout):
            raise TimeoutError("network timed out")
        res = self._gpt(transport=boom)
        self.assertFalse(res["ok"])
        self.assertEqual(res["error"], "transport_error")

    def test_model_unavailable_is_hard_gate(self):
        body = json.dumps({"error": {"code": "model_not_found", "type": "invalid_request_error"}})
        res = self._gpt(transport=gpt_transport(status=404, body_text=body))
        self.assertFalse(res["ok"])
        self.assertEqual(res["error"], "model_unavailable")
        self.assertTrue(res["hard_gate"])

    def test_key_is_never_returned_persisted_or_in_records(self):
        res = self._gpt(transport=gpt_transport(make_verdict("gpt")))
        # Never in the returned result.
        self.assertNotIn(FAKE_KEY, json.dumps(res))
        # Never in any durable message.
        for m in cwm.read_messages(self.root):
            self.assertNotIn(FAKE_KEY, json.dumps(m))
        # Never written anywhere under the queue root.
        for dirpath, _dirs, files in os.walk(self.root):
            for name in files:
                self.assertNotIn(FAKE_KEY, read(os.path.join(dirpath, name)))

    def test_key_not_in_exception_output(self):
        def boom(url, headers, body_bytes, timeout):
            raise RuntimeError("failure with headers " + str(headers.get("Content-Type")))
        res = self._gpt(transport=boom)
        self.assertNotIn(FAKE_KEY, json.dumps(res))


class CodexStructuredTests(unittest.TestCase):

    def setUp(self):
        self.root = queue("cw_codex_", self)
        res = server.do_message(self.root, {"actor": "OPERATOR-0001", "role": "operator",
                                            "message": "Plan", "intent": "request"})
        self.thread = res["thread_id"]

    def _run(self, runner, **kw):
        return ccr.review_structured(self.root, thread_id=self.thread,
                                     runner=runner, available_fn=lambda: True,
                                     note_on_failure=False, **kw)

    def test_codex_cmd_skips_git_repo_check(self):
        # Regression: Codex must run read-only against any directory, including
        # a non-git / untrusted target (the JARVIS run fast-exited without this).
        cmd = ccr.build_codex_cmd("some prompt")
        self.assertIn("--skip-git-repo-check", cmd)
        self.assertIn("read-only", cmd)
        self.assertEqual(cmd[-1], "some prompt")

    def test_codex_reviewer_self_label_is_coerced(self):
        v = make_verdict("codex")
        v["reviewer"] = "Codex CLI"
        res = self._run(codex_runner(v))
        self.assertTrue(res["ok"] and res["posted"])
        self.assertEqual(res["verdict"]["reviewer"], "codex")

    def test_structured_review_posts_correctly(self):
        res = self._run(codex_runner(make_verdict("codex", verdict="approve_with_changes")))
        self.assertTrue(res["ok"] and res["posted"])
        self.assertEqual(res["verdict"]["reviewer"], "codex")
        codex_msgs = [m for m in cwm.read_messages(self.root) if m.get("actor") == "codex"]
        self.assertEqual(len(codex_msgs), 1)
        self.assertEqual(codex_msgs[0]["source"], "codex-cli")

    def test_timeout_rejected(self):
        res = self._run(codex_runner(make_verdict("codex"), timed_out=True))
        self.assertFalse(res["posted"])
        self.assertEqual(res["classification"], "timeout")
        self.assertFalse([m for m in cwm.read_messages(self.root) if m.get("actor") == "codex"])

    def test_non_substantive_rejected(self):
        res = self._run(codex_runner(raw="   "))
        self.assertFalse(res["posted"])
        self.assertEqual(res["classification"], "non_substantive")

    def test_malformed_output_rejected(self):
        res = self._run(codex_runner(raw="This is a long prose review without any JSON object at all, definitely."))
        self.assertFalse(res["posted"])
        self.assertIn(res["classification"], ("malformed_output", "invalid_verdict"))

    def test_unavailable_posts_no_codex(self):
        res = ccr.review_structured(self.root, thread_id=self.thread,
                                    runner=codex_runner(make_verdict("codex")),
                                    available_fn=lambda: False, note_on_failure=False)
        self.assertFalse(res["posted"])
        self.assertEqual(res["classification"], "unavailable")

    def test_existing_codex_review_still_works(self):
        # PR #18 behavior must remain green: substantive run posts a codex review.
        runner = lambda p, t, cwd=None: ("A real, substantive review with findings.",
                                          ccr.build_telemetry("x" * 90, 0, 1.0))
        wid = cww.derive_work_items(self.root)[0]["work_item_id"]
        res = ccr.review(self.root, wid, runner=runner, available_fn=lambda: True)
        self.assertTrue(res["codex_posted"])


class CouncilEngineTests(unittest.TestCase):

    def setUp(self):
        self.root = queue("cw_council_", self)
        res = server.do_message(self.root, {"actor": "OPERATOR-0001", "role": "operator",
                                            "message": "Plan to review", "intent": "request"})
        self.thread = res["thread_id"]
        self.wid = cww.derive_work_items(self.root)[0]["work_item_id"]

    def gpt_fn(self, verdict="approve", conf=0.9, posted=True, error=None,
               hard_gate=False, capture=None, req=None):
        def fn(root, context, **kw):
            if capture is not None:
                capture.append(("gpt", context))
            if not posted:
                return {"ok": False, "posted": False, "reviewer": "gpt",
                        "error": error or "empty_output", "hard_gate": hard_gate, "telemetry": {}}
            v = make_verdict("gpt", verdict, conf)
            if req is not None:
                v["required_changes"] = list(req)
            return {"ok": True, "posted": True, "reviewer": "gpt", "verdict": v,
                    "validated": True, "source": "openai-api",
                    "telemetry": {"reviewer": "gpt"}, "message_id": "g"}
        return fn

    def codex_fn(self, verdict="approve", conf=0.9, posted=True, capture=None, req=None):
        def fn(root, context, **kw):
            if capture is not None:
                capture.append(("codex", context))
            if not posted:
                return {"ok": True, "posted": False, "reviewer": "codex",
                        "classification": "timeout", "telemetry": {}}
            v = make_verdict("codex", verdict, conf)
            if req is not None:
                v["required_changes"] = list(req)
            return {"ok": True, "posted": True, "reviewer": "codex", "verdict": v,
                    "validated": True, "source": "codex-cli",
                    "telemetry": {"reviewer": "codex"}, "message_id": "c"}
        return fn

    def new_council(self, phase="plan", approved_scope="operator-approved test scope", **kw):
        return council.create_council(self.root, thread_id=self.thread,
                                      work_item_id=self.wid, phase=phase,
                                      approved_scope=approved_scope, **kw)

    def ready_recon(self, ready=True, blockers=None):
        return {"accepted_findings": ["ok"], "rejected_findings": [],
                "required_plan_changes": [], "revised_plan": ["do the thing"],
                "unresolved_blockers": blockers or [], "ready_to_proceed": ready,
                "summary": "reconciled the two reviews with evidence"}

    def test_round_one_is_independent(self):
        cap = []
        c = self.new_council()
        council.run_round(self.root, c, "BASE CONTEXT", gpt_fn=self.gpt_fn(capture=cap),
                          codex_fn=self.codex_fn(capture=cap))
        contexts = {who: ctx for who, ctx in cap}
        # Both reviewers saw the same base context; neither saw the other's output.
        self.assertEqual(contexts["gpt"], "BASE CONTEXT")
        self.assertEqual(contexts["codex"], "BASE CONTEXT")

    def test_reconciliation_is_persisted_and_needs_evidence(self):
        c = self.new_council()
        council.run_round(self.root, c, "ctx", gpt_fn=self.gpt_fn(), codex_fn=self.codex_fn())
        c = council.load_council(self.root, c["council_id"])
        council.attach_reconciliation(self.root, c, self.ready_recon())
        rounds = council.load_rounds(self.root, c["council_id"])
        self.assertTrue(rounds[-1]["reconciliation"]["ready_to_proceed"])
        # A durable reconciliation note is posted into the thread.
        self.assertTrue([m for m in cwm.read_messages(self.root)
                         if m.get("source") == "review-council"])
        with self.assertRaises(cwv.VerdictError):
            council.attach_reconciliation(self.root, c, {
                "ready_to_proceed": True, "summary": "missing evidence here",
                "accepted_findings": [], "required_plan_changes": [], "revised_plan": [],
                "unresolved_blockers": [], "rejected_findings": [
                    {"finding": "x", "reason": "no", "evidence": []}]})

    def _round(self, c, gv="approve", cv="approve", gconf=0.9, cconf=0.9,
               recon=None, gpost=True, cpost=True, ghard=False, greq=None, creq=None):
        council.run_round(self.root, c, "ctx",
                          gpt_fn=self.gpt_fn(gv, gconf, posted=gpost, hard_gate=ghard, req=greq),
                          codex_fn=self.codex_fn(cv, cconf, posted=cpost, req=creq))
        c2 = council.load_council(self.root, c["council_id"])
        if recon is not None:
            council.attach_reconciliation(self.root, c2, recon)
        return council.evaluate(c2, council.load_rounds(self.root, c["council_id"]))

    def test_min_two_rounds_enforced(self):
        c = self.new_council()
        out = self._round(c, recon=self.ready_recon())  # one round, ready recon
        self.assertEqual(out["outcome"], "needs_revision")  # min 2 not met

    def test_two_approving_rounds_reach_agreement(self):
        c = self.new_council()
        self._round(c)  # round 1, no recon
        out = self._round(c, recon=self.ready_recon())  # round 2 + ready recon
        self.assertEqual(out["outcome"], "agreement_threshold_met")
        self.assertTrue(out["ready_to_proceed"])

    def test_revise_prevents_agreement(self):
        c = self.new_council()
        self._round(c)
        out = self._round(c, gv="revise", recon=self.ready_recon())
        self.assertEqual(out["outcome"], "needs_revision")

    def test_block_prevents_agreement(self):
        c = self.new_council()
        self._round(c)
        out = self._round(c, cv="block", recon=self.ready_recon())
        self.assertEqual(out["outcome"], "needs_revision")

    def test_unresolved_blocker_prevents_agreement(self):
        c = self.new_council()
        self._round(c)
        out = self._round(c, recon=self.ready_recon(blockers=["still open: schema risk"]))
        self.assertEqual(out["outcome"], "needs_revision")
        self.assertIn("still open: schema risk", out["unresolved_blockers"])

    def test_low_confidence_prevents_agreement(self):
        c = self.new_council()
        self._round(c)
        out = self._round(c, gconf=0.5, recon=self.ready_recon())
        self.assertEqual(out["outcome"], "needs_revision")

    def test_missing_reviewer_is_reviewer_unavailable(self):
        c = self.new_council()
        self._round(c)
        out = self._round(c, cpost=False, recon=self.ready_recon())
        self.assertEqual(out["outcome"], "reviewer_unavailable")

    def test_hard_gate_short_circuits(self):
        c = self.new_council()
        out = self._round(c, gpost=False, ghard=True)
        self.assertEqual(out["outcome"], "hard_gate")
        self.assertTrue(out["hard_gate"])

    def test_max_rounds_forces_operator_required(self):
        c = self.new_council(min_rounds=2, max_rounds=3)
        # 3 rounds that never agree (revise), no reconciliation resolving it.
        for _ in range(3):
            out = self._round(c, gv="revise")
        self.assertEqual(out["outcome"], "operator_required")
        self.assertTrue(out["operator_required"])

    def test_secret_in_context_is_hard_gate_and_calls_no_reviewer(self):
        cap = []
        c = self.new_council()
        rd = council.run_round(self.root, c, "here is a leak sk-abcdef0123456789abcd token",
                               gpt_fn=self.gpt_fn(capture=cap), codex_fn=self.codex_fn(capture=cap))
        self.assertTrue(rd.get("hard_gate"))
        self.assertEqual(cap, [])  # no reviewer was ever called
        out = council.evaluate(council.load_council(self.root, c["council_id"]),
                               council.load_rounds(self.root, c["council_id"]))
        self.assertEqual(out["outcome"], "hard_gate")

    def test_incident_and_verify_phases_record(self):
        for phase in ("incident", "verify"):
            c = self.new_council(phase=phase)
            self._round(c)
            full = council.get_council(self.root, c["council_id"])
            self.assertEqual(full["council"]["phase"], phase)
            self.assertEqual(len(full["rounds"]), 1)

    def test_council_reloads_after_restart(self):
        c = self.new_council()
        self._round(c, recon=self.ready_recon())
        cid = c["council_id"]
        # Simulate a restart: read purely from disk with no in-memory state.
        full = council.get_council(self.root, cid)
        self.assertEqual(full["council"]["council_id"], cid)
        self.assertEqual(len(full["rounds"]), 1)
        self.assertTrue(full["rounds"][0]["reconciliation"])

    def test_evaluator_rejects_posted_but_not_ok(self):
        # A round record that looks posted but is marked ok=false must not count
        # as a real review (honesty enforced at the evaluator, not the adapter).
        c = self.new_council()
        council.run_round(self.root, c, "ctx", gpt_fn=self.gpt_fn(), codex_fn=self.codex_fn())
        c = council.load_council(self.root, c["council_id"])
        rounds = council.load_rounds(self.root, c["council_id"])
        rounds[-1]["gpt"]["ok"] = False  # tamper: claims posted but not ok
        self.assertEqual(council._reviewer_status(rounds[-1]["gpt"], "gpt"), "unavailable")

    def test_evaluator_rejects_result_with_ok_omitted(self):
        # A record shaped like a posted, validated review but with ok OMITTED
        # must not pass the trust boundary.
        sneaky = {"posted": True, "reviewer": "gpt", "validated": True,
                  "source": "openai-api", "verdict": make_verdict("gpt"), "message_id": "x"}
        self.assertEqual(council._reviewer_status(sneaky, "gpt"), "unavailable")

    def test_evaluator_rejects_reviewer_identity_mismatch(self):
        # A verdict whose reviewer is codex must not satisfy the gpt slot.
        result = {"ok": True, "posted": True, "reviewer": "gpt",
                  "verdict": make_verdict("codex"), "message_id": "x"}
        self.assertEqual(council._reviewer_status(result, "gpt"), "invalid_verdict")

    def test_evaluator_rejects_unvalidatable_verdict(self):
        result = {"ok": True, "posted": True, "reviewer": "gpt",
                  "verdict": {"reviewer": "gpt", "verdict": "ship"}, "message_id": "x"}
        self.assertEqual(council._reviewer_status(result, "gpt"), "invalid_verdict")

    def test_evaluator_requires_provenance_marker_and_matching_source(self):
        # A valid-looking verdict without validated=true is not trusted.
        no_prov = {"ok": True, "posted": True, "reviewer": "gpt",
                   "verdict": make_verdict("gpt"), "message_id": "x"}
        self.assertEqual(council._reviewer_status(no_prov, "gpt"), "unvalidated")
        # A validated result whose source does not match the reviewer is rejected.
        bad_src = dict(no_prov, validated=True, source="codex-cli")
        self.assertEqual(council._reviewer_status(bad_src, "gpt"), "source_mismatch")
        good = dict(no_prov, validated=True, source="openai-api")
        self.assertEqual(council._reviewer_status(good, "gpt"), "review")

    def _recon_with_resolutions(self, refs, ready=True):
        r = self.ready_recon(ready=ready)
        r["resolutions"] = [{"ref": ref, "disposition": "accepted",
                             "note": "handled " + ref} for ref in refs]
        return r

    def test_required_changes_need_per_item_ref_resolution(self):
        # Unrelated dispositions that do not name each finding's ref are not enough.
        c = self.new_council()
        self._round(c, greq=["change A", "change B"])
        unrelated = self.ready_recon()  # has accepted/revised_plan but no resolutions
        out = self._round(c, greq=["change A", "change B"], recon=unrelated)
        self.assertEqual(out["outcome"], "needs_revision")
        self.assertTrue(any("unresolved in reconciliation resolutions" in b
                            for b in out["unresolved_blockers"]))
        # A partial map (one of two refs) is still insufficient.
        c2 = self.new_council()
        self._round(c2, greq=["change A", "change B"])
        partial = self._recon_with_resolutions(["gpt.required_changes[0]"])
        out2 = self._round(c2, greq=["change A", "change B"], recon=partial)
        self.assertEqual(out2["outcome"], "needs_revision")
        # A full per-item map (both refs) lets agreement be reached.
        c3 = self.new_council()
        self._round(c3, greq=["change A", "change B"])
        full = self._recon_with_resolutions(
            ["gpt.required_changes[0]", "gpt.required_changes[1]"])
        out3 = self._round(c3, greq=["change A", "change B"], recon=full)
        self.assertEqual(out3["outcome"], "agreement_threshold_met")

    def test_rejected_resolution_requires_evidence(self):
        with self.assertRaises(cwv.VerdictError):
            cwv.validate_reconciliation({
                "ready_to_proceed": True, "summary": "reconciled the reviews",
                "accepted_findings": [], "rejected_findings": [],
                "required_plan_changes": [], "revised_plan": [], "unresolved_blockers": [],
                "resolutions": [{"ref": "gpt.required_changes[0]", "disposition": "rejected",
                                 "evidence": []}]})

    def test_missing_approved_scope_prevents_executable_agreement(self):
        # Reviewers agree, reconciliation is ready, but no approved_scope recorded.
        c = self.new_council(approved_scope=None)
        self._round(c)
        out = self._round(c, recon=self.ready_recon())
        self.assertEqual(out["outcome"], "needs_revision")
        self.assertIn("approved_scope not recorded (required before executable agreement)",
                      out["unresolved_blockers"])
        # Recording the scope (via the CLI-facing setter) then allows agreement.
        c2 = council.load_council(self.root, c["council_id"])
        council.set_approved_scope(self.root, c2, "operator approved this scope")
        out2 = council.evaluate(council.load_council(self.root, c["council_id"]),
                                council.load_rounds(self.root, c["council_id"]))
        self.assertEqual(out2["outcome"], "agreement_threshold_met")
        self.assertIsNotNone(out2["approved_scope_sha256"])

    def test_secret_value_never_stored_across_a_full_run(self):
        # A full mocked council run must never write the canary key anywhere.
        c = self.new_council()
        self._round(c)
        self._round(c, recon=self.ready_recon())
        for dirpath, _dirs, files in os.walk(self.root):
            for name in files:
                self.assertNotIn(FAKE_KEY, read(os.path.join(dirpath, name)))


class CouncilApiTests(unittest.TestCase):

    def setUp(self):
        self.root = queue("cw_api_", self)
        res = server.do_message(self.root, {"actor": "OPERATOR-0001", "role": "operator",
                                            "message": "Plan", "intent": "request"})
        self.thread = res["thread_id"]
        self.c = council.create_council(self.root, thread_id=self.thread, phase="plan")

    def test_list_and_get_are_read_only_and_work(self):
        listing = council.list_councils(self.root)
        self.assertEqual(len(listing), 1)
        self.assertEqual(listing[0]["council_id"], self.c["council_id"])
        filtered = council.list_councils(self.root, thread_id=self.thread)
        self.assertEqual(len(filtered), 1)
        self.assertEqual(council.list_councils(self.root, thread_id="thr-nope"), [])
        full = council.get_council(self.root, self.c["council_id"])
        self.assertEqual(full["council"]["council_id"], self.c["council_id"])
        self.assertIsNone(council.get_council(self.root, "cw-council-nope"))

    def test_routes_wired_in_server(self):
        src = read(os.path.join(APP_DIR, "server.py"))
        self.assertIn('"/api/review-councils"', src)
        self.assertIn('"/api/review-council"', src)
        self.assertIn("cwrc.list_councils", src)
        self.assertIn("cwrc.get_council", src)


class HealthCapabilityTests(unittest.TestCase):

    def setUp(self):
        self.root = queue("cw_hcap_", self)

    def test_health_exposes_safe_capability_indicators(self):
        h = server.build_health(self.root, mode="operator", durable=True,
                                codex_check=lambda: True, key_check=lambda: True)
        caps = h["capabilities"]
        for key in ("gpt_helper", "openai_api_key_configured", "configured_gpt_model",
                    "codex_helper", "codex_cli_on_path", "council_available"):
            self.assertIn(key, caps)
        self.assertIs(caps["openai_api_key_configured"], True)
        self.assertTrue(caps["gpt_helper"])
        self.assertTrue(caps["council_available"])
        self.assertEqual(caps["configured_gpt_model"], gpt.DEFAULT_GPT_MODEL)

    def test_health_reports_key_absent_as_false_and_never_a_value(self):
        h = server.build_health(self.root, mode="operator", durable=True,
                                codex_check=lambda: True, key_check=lambda: False)
        self.assertIs(h["capabilities"]["openai_api_key_configured"], False)
        # The health payload must never carry a key value.
        self.assertNotIn("sk-", json.dumps(h))

    def test_health_does_not_invoke_reviewers(self):
        # A key_check/codex_check that would explode if treated as a reviewer
        # call proves health only probes cheap booleans.
        called = {"n": 0}
        def probe():
            called["n"] += 1
            return True
        server.build_health(self.root, mode="operator", durable=True,
                            codex_check=probe, key_check=probe)
        # Each probe is a single cheap boolean call, not a model invocation.
        self.assertLessEqual(called["n"], 2)


class UiTests(unittest.TestCase):

    def setUp(self):
        self.appjs = read(os.path.join(STATIC, "app.js"))
        self.css = read(os.path.join(STATIC, "style.css"))

    def test_app_renders_council_state(self):
        self.assertIn("function councilCard", self.appjs)
        self.assertIn("REVIEW COUNCIL", self.appjs)
        self.assertIn("/api/review-councils", self.appjs)
        self.assertIn("/api/review-council?id=", self.appjs)

    def test_council_css_present(self):
        self.assertIn(".council-card", self.css)
        self.assertIn(".council-badge", self.css)


class RegressionTests(unittest.TestCase):

    def setUp(self):
        self.root = queue("cw_reg25_", self)

    def test_pr24_chat_still_non_actionable(self):
        server.do_message(self.root, {"actor": "OPERATOR-0001", "role": "operator",
                                      "direction": "inbound", "intent": "chat", "message": "hi"})
        self.assertEqual(cww.derive_work_items(self.root), [])

    def test_request_still_actionable(self):
        server.do_message(self.root, {"actor": "OPERATOR-0001", "role": "operator",
                                      "direction": "inbound", "intent": "request", "message": "do it"})
        self.assertEqual(len(cww.derive_work_items(self.root)), 1)

    def test_existing_builders_and_apis_work(self):
        server.do_message(self.root, {"actor": "a", "message": "m", "intent": "request"})
        self.assertEqual(len(server.build_runs(self.root)), 1)
        self.assertIsNotNone(server.build_active_run(self.root)["thread_id"])
        self.assertIn("pulse", server.build_state(self.root))

    def test_packet_lifecycle_still_works(self):
        fields = {"title": "Add a status endpoint to the sample web application",
                  "packet_type": "code_change", "requesting_agent": "agent/worker",
                  "requested_action": "Add a read-only status endpoint. Findings only.",
                  "target_label": "sample web application"}
        self.assertTrue(server.do_request(self.root, fields)["ok"])
        fn = [f for f in os.listdir(os.path.join(self.root, "clearance_outbox")) if f.endswith(".json")][0]
        self.assertTrue(server.do_action(self.root, "cta", fn)["ok"])


class NamingAndPrivacyTests(unittest.TestCase):

    def test_no_private_target_or_retired_terms(self):
        _wr = "w" + "rit"
        retired = re.compile("|".join([r"\b" + _wr + r"\b", "vol" + "tex"]), re.I)
        private = re.compile("|".join([r"\b" + "pl" + "ex" + r"\b",
                                       "d:" + re.escape("\\") + "dev"]), re.I)
        targets = [os.path.join(TOOLS_DIR, "clearwright_verdict.py"),
                   os.path.join(TOOLS_DIR, "clearwright_gpt_review.py"),
                   os.path.join(TOOLS_DIR, "clearwright_codex_review.py"),
                   os.path.join(TOOLS_DIR, "clearwright_review_council.py"),
                   os.path.join(APP_DIR, "server.py"),
                   os.path.join(STATIC, "app.js"),
                   os.path.join(STATIC, "style.css"),
                   os.path.abspath(__file__)]
        for path in targets:
            with self.subTest(file=os.path.relpath(path, REPO_ROOT)):
                text = read(path)
                self.assertIsNone(retired.search(text))
                self.assertIsNone(private.search(text))


if __name__ == "__main__":
    unittest.main()
