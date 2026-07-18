"""Tests for the reliability pass (PR 1 of the acceptance-hardening design).

Maps to the acceptance-run failure matrix: structured task envelopes (the
classifier no longer reads operator guardrails as risk), preflight, the schema
command and reconciliation --dry-run (zero reviewer cost), the wrapper-side
round clamp, and the metadata-only invocation log that records aborted
invocations. Reviewer transports are never exercised here; adapter and engine
behavior is covered in test_review_council.py.
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
import clearwright_review_council as cwrc  # noqa: E402
import clearwright_use_cw as ucw  # noqa: E402


def queue(prefix, tc):
    base = tempfile.mkdtemp(prefix=prefix)
    tc.addCleanup(shutil.rmtree, base, ignore_errors=True)
    root, *_ = server.resolve_queue(base)
    return root


def stub_preflight(tc):
    """start runs an implicit preflight (key present, codex on PATH). CI has
    neither, so tests exercising start stub the probes; preflight behavior
    itself is tested separately with explicit injections."""
    import clearwright_egress_guard as guard_mod
    import clearwright_codex_review as ccr_mod
    orig_key, orig_exe = guard_mod.provider_key_status, ccr_mod.codex_executable
    guard_mod.provider_key_status = lambda *a, **k: (True, "process_env")
    ccr_mod.codex_executable = lambda: "codex-stub"
    tc.addCleanup(setattr, guard_mod, "provider_key_status", orig_key)
    tc.addCleanup(setattr, ccr_mod, "codex_executable", orig_exe)


def run(func, **kw):
    kw.setdefault("json", True)
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        code = func(Namespace(**kw))
    return json.loads(buf.getvalue().strip().splitlines()[-1]), code


def start_args(root, **kw):
    base = dict(queue_root=root, envelope_file=None, request=None, request_file=None,
                kind=None, thread_id=None, packet_id=None, approved_scope=None,
                actor="claude", json=True)
    base.update(kw)
    return base


ENVELOPE = {
    "envelope_version": 1,
    "task_kind": "analysis",
    "request": "Review the live page and produce a prioritised findings report.",
    "approved_scope": "Read-only review of the live page and pinned source. No changes.",
    "intended_actions": ["fetch page", "inspect source", "produce report"],
    "excluded_actions": ["edit files", "deploy", "publish", "change dns or hosting"],
    "operator_authority_source": "operator instruction of 2026-07-14",
}


class EnvelopeClassificationTests(unittest.TestCase):

    def setUp(self):
        self.root = queue("cwrel_env_", self)
        stub_preflight(self)

    def _envelope_file(self, env):
        path = os.path.join(self.root, "envelope.json")
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(env, fh)
        return path

    def test_guardrails_in_excluded_actions_never_raise_risk(self):
        # The acceptance-run regression: 'deploy'/'publish'/'dns' listed under
        # excluded_actions must classify as declared (analysis), no conflict.
        res, code = run(ucw.cmd_start, **start_args(
            self.root, envelope_file=self._envelope_file(dict(ENVELOPE))))
        self.assertEqual(code, ucw.EXIT_OK)
        self.assertEqual(res["kind"], "analysis")
        self.assertEqual(res["classification_method"], "envelope")
        self.assertFalse(res["classification_conflict"])
        self.assertFalse(res["requires_clearance"])

    def test_intended_action_conflicting_with_scope_is_operator_required(self):
        env = dict(ENVELOPE, intended_actions=["fetch page", "publish the updated page"])
        res, code = run(ucw.cmd_start, **start_args(
            self.root, envelope_file=self._envelope_file(env)))
        self.assertEqual(code, ucw.EXIT_OPERATOR)
        self.assertTrue(res["classification_conflict"])
        self.assertIn("governed", res["conflict_detail"])
        self.assertIn("operator must resolve", res["conflict_detail"])
        # The work item is still recorded so the conflict is auditable.
        self.assertTrue(res["thread_id"])

    def test_envelope_missing_fields_is_usage_error(self):
        env = {k: v for k, v in ENVELOPE.items() if k != "approved_scope"}
        res, code = run(ucw.cmd_start, **start_args(
            self.root, envelope_file=self._envelope_file(env)))
        self.assertEqual(code, ucw.EXIT_USAGE)
        self.assertIn("approved_scope", res["error"])

    def test_envelope_is_persisted_verbatim_with_audit_fields(self):
        res, _ = run(ucw.cmd_start, **start_args(
            self.root, envelope_file=self._envelope_file(dict(ENVELOPE))))
        mid = res["work_item_id"].split(":", 1)[1]
        path = os.path.join(self.root, "task_envelopes", mid + ".json")
        self.assertTrue(os.path.isfile(path))
        stored = json.load(open(path, encoding="utf-8"))
        for field in ENVELOPE:
            self.assertEqual(stored[field], ENVELOPE[field])  # verbatim
        audit = stored["_audit"]
        self.assertEqual(audit["classification"], "analysis")
        self.assertIn("verification_required", audit)
        self.assertEqual(audit["envelope_sha256"], res["envelope_sha256"])

    def test_verification_required_defaults_and_clamps(self):
        # analysis defaults False; actionable defaults True; governed clamps True
        # even when declared False.
        res, _ = run(ucw.cmd_start, **start_args(
            self.root, envelope_file=self._envelope_file(dict(ENVELOPE))))
        self.assertFalse(res["verification_required"])
        env2 = dict(ENVELOPE, task_kind="actionable")
        res2, _ = run(ucw.cmd_start, **start_args(
            self.root, envelope_file=self._envelope_file(env2)))
        self.assertTrue(res2["verification_required"])
        self.assertEqual(res2["verification_required_source"], "default")
        env3 = dict(ENVELOPE, task_kind="governed", verification_required=False)
        res3, _ = run(ucw.cmd_start, **start_args(
            self.root, envelope_file=self._envelope_file(env3)))
        self.assertTrue(res3["verification_required"])
        self.assertEqual(res3["verification_required_source"], "clamped")

    def test_lexical_fallback_strips_exclusion_sections(self):
        text = ("Review the live page and produce a findings report.\n\n"
                "Out of scope:\n- Any modification, deployment, or dns/hosting change.\n"
                "- publish anything.\n\nDeliverable: a prioritised list.")
        res, code = run(ucw.cmd_start, **start_args(self.root, request=text))
        self.assertEqual(code, ucw.EXIT_OK)
        self.assertEqual(res["kind"], "actionable")  # NOT high_risk/governed
        self.assertEqual(res["classification_method"], "lexical_fallback")

    def test_lexical_fallback_still_flags_real_risk(self):
        res, _ = run(ucw.cmd_start, **start_args(
            self.root, request="Deploy the new build to production now."))
        self.assertEqual(res["kind"], "governed")
        self.assertTrue(res["requires_clearance"])


class PreflightTests(unittest.TestCase):

    def setUp(self):
        self.root = queue("cwrel_pf_", self)

    def test_preflight_reports_safe_booleans_and_never_a_key_value(self):
        pf = ucw._preflight_checks(self.root, implicit=False,
                                   key_resolver=lambda: ("sk-CANARY-000000000000000000", "process_env"),
                                   codex_which=lambda: "C:/somewhere/codex.exe")
        payload = json.dumps(pf)
        self.assertNotIn("sk-CANARY", payload)
        self.assertTrue(pf["checks"]["openai_api_key"]["present"])
        self.assertEqual(pf["checks"]["openai_api_key"]["source"], "process_env")
        self.assertEqual(pf["remediation"], [])
        self.assertIn("round_bounds", pf["checks"])
        self.assertIn("attempt_budget", pf["checks"])

    def test_preflight_missing_key_and_codex_yield_remediation(self):
        pf = ucw._preflight_checks(self.root, implicit=False,
                                   key_resolver=lambda: (None, None),
                                   codex_which=lambda: None)
        self.assertEqual(len(pf["remediation"]), 2)
        self.assertIn("USER environment", pf["remediation"][0])
        self.assertIn("codex", pf["remediation"][1].lower())

    def test_start_runs_implicit_preflight_and_creates_nothing_on_failure(self):
        import clearwright_egress_guard as guard_mod
        orig = guard_mod.provider_key_status
        guard_mod.provider_key_status = lambda *a, **k: (False, None)
        self.addCleanup(setattr, guard_mod, "provider_key_status", orig)
        res, code = run(ucw.cmd_start, **start_args(self.root, request="Do a thing."))
        self.assertEqual(code, ucw.EXIT_HARD_GATE)
        self.assertEqual(res["error"], "preflight_failed")
        # Nothing was created: no messages, no work items, no envelopes.
        import clearwright_message as cwm
        self.assertEqual(cwm.read_messages(self.root), [])
        self.assertFalse(os.path.isdir(os.path.join(self.root, "task_envelopes")))


class SchemaAndDryRunTests(unittest.TestCase):

    def setUp(self):
        self.root = queue("cwrel_schema_", self)
        stub_preflight(self)
        res, _ = run(ucw.cmd_start, **start_args(
            self.root, request="Plan a change.", kind="actionable",
            approved_scope="scope"))
        self.thread = res["thread_id"]
        self.wid = res["work_item_id"]

    def test_schema_command_prints_all_three(self):
        for name in ("envelope", "verdict", "reconciliation"):
            res, code = run(ucw.cmd_schema, name=name, queue_root=".")
            self.assertEqual(code, ucw.EXIT_OK)
            self.assertIn("rules", res["schema"])

    def _council_args(self, **kw):
        base = dict(queue_root=self.root, phase="plan", council_id=None,
                    thread_id=self.thread, work_item_id=self.wid, packet_id=None,
                    repo=None, plan_file=None, context_file=None, prompt="ctx",
                    reconciliation_file=None, stage="review", dry_run=False,
                    model=None, approved_scope="scope", min_rounds=2, max_rounds=5,
                    grant_attempts=None, operator_message_id=None, timeout=30, json=True)
        base.update(kw)
        return base

    def _committed_council(self):
        def mock(reviewer, source):
            def fn(root, context, **kw):
                return {"ok": True, "posted": True, "reviewer": reviewer,
                        "verdict": {"reviewer": reviewer, "verdict": "approve",
                                    "confidence": 0.9, "risk_level": "low",
                                    "blocking_findings": [], "required_changes": ["fix A"],
                                    "nonblocking_findings": [], "disagreements": [],
                                    "assumptions": [], "questions": [],
                                    "recommended_plan": [], "summary": "A substantive review."},
                        "validated": True, "source": source,
                        "telemetry": {"reviewer": reviewer}, "message_id": reviewer[0]}
            return fn
        c = cwrc.create_council(self.root, thread_id=self.thread,
                                work_item_id=self.wid, approved_scope="scope")
        cwrc.run_round(self.root, c, "ctx", sleep=lambda *_: None,
                       gpt_fn=mock("gpt", "openai-api"), codex_fn=mock("codex", "codex-cli"))
        return cwrc.load_council(self.root, c["council_id"])

    def _dry_run(self, council_id, recon_obj):
        rf = os.path.join(self.root, "recon_dry.json")
        with open(rf, "w", encoding="utf-8") as fh:
            json.dump(recon_obj, fh)
        return run(lambda a: ucw._council(a, "plan"),
                   **self._council_args(stage="reconcile", dry_run=True,
                                        council_id=council_id, reconciliation_file=rf))

    def test_dry_run_catches_all_four_historical_failure_modes(self):
        c = self._committed_council()
        good = {"accepted_findings": ["ok"], "rejected_findings": [],
                "required_plan_changes": [], "revised_plan": ["do"],
                "unresolved_blockers": [],
                "resolutions": [{"ref": "gpt.required_changes[0]", "disposition": "accepted"},
                                {"ref": "codex.required_changes[0]", "disposition": "accepted"}],
                "ready_to_proceed": True, "summary": "a substantive reconciliation"}
        # 1. bad disposition enum
        bad1 = json.loads(json.dumps(good))
        bad1["resolutions"][0]["disposition"] = "acknowledged"
        res, code = self._dry_run(c["council_id"], bad1)
        self.assertEqual(code, ucw.EXIT_USAGE)
        self.assertIn("disposition", res["error"])
        # 2. annotated/composite ref fails to bind
        bad2 = json.loads(json.dumps(good))
        bad2["resolutions"][0]["ref"] = "gpt.required_changes[0] (the evaluator one)"
        res, code = self._dry_run(c["council_id"], bad2)
        self.assertEqual(code, ucw.EXIT_USAGE)
        self.assertIn("gpt.required_changes[0]", res["unbound_refs"])
        # 3. rejected without evidence
        bad3 = json.loads(json.dumps(good))
        bad3["rejected_findings"] = [{"finding": "x", "reason": "wrong", "evidence": []}]
        res, code = self._dry_run(c["council_id"], bad3)
        self.assertEqual(code, ucw.EXIT_USAGE)
        # 4. evidence as a string, not an array
        bad4 = json.loads(json.dumps(good))
        bad4["rejected_findings"] = [{"finding": "x", "reason": "wrong", "evidence": "test output"}]
        res, code = self._dry_run(c["council_id"], bad4)
        self.assertEqual(code, ucw.EXIT_USAGE)
        # The valid reconciliation passes, still without submitting anything.
        res, code = self._dry_run(c["council_id"], good)
        self.assertEqual(code, ucw.EXIT_OK)
        self.assertTrue(res["dry_run"])
        rounds = cwrc.load_rounds(self.root, c["council_id"])
        self.assertIsNone(rounds[-1].get("reconciliation"))  # nothing attached

    def test_wrapper_round_clamp_is_exit_7(self):
        for mn, mx in ((1, 3), (2, 6)):
            res, code = run(lambda a: ucw._council(a, "plan"),
                            **self._council_args(min_rounds=mn, max_rounds=mx))
            self.assertEqual(code, ucw.EXIT_USAGE)
            self.assertIn("min_rounds", res["error"])


class InvocationLogTests(unittest.TestCase):

    def setUp(self):
        self.root = queue("cwrel_log_", self)
        stub_preflight(self)

    def _log_lines(self):
        path = os.path.join(self.root, "invocation_log.jsonl")
        if not os.path.isfile(path):
            return []
        with open(path, encoding="utf-8") as fh:
            return [json.loads(line) for line in fh if line.strip()]

    def test_dispatch_attempts_are_logged_with_metadata_only(self):
        res, _ = run(ucw.cmd_start, **start_args(self.root, request="Plan a change.",
                                                 kind="actionable", approved_scope="s"))
        c = cwrc.create_council(self.root, thread_id=res["thread_id"],
                                approved_scope="s")
        secret_context = "review this plan please"
        def failing(root, context, **kw):
            return {"ok": False, "posted": False, "reviewer": "codex",
                    "classification": "timeout", "telemetry": {}}
        def good(root, context, **kw):
            return {"ok": True, "posted": True, "reviewer": "gpt",
                    "verdict": {"reviewer": "gpt", "verdict": "approve",
                                "confidence": 0.9, "risk_level": "low",
                                "blocking_findings": [], "required_changes": [],
                                "nonblocking_findings": [], "disagreements": [],
                                "assumptions": [], "questions": [],
                                "recommended_plan": [], "summary": "A substantive review."},
                    "validated": True, "source": "openai-api",
                    "telemetry": {"actual_input_tokens": 111, "actual_output_tokens": 22},
                    "message_id": "g"}
        cwrc.run_round(self.root, c, secret_context, sleep=lambda *_: None,
                       gpt_fn=good, codex_fn=failing)
        lines = [l for l in self._log_lines() if l.get("command") == "council-dispatch"]
        # 1 gpt attempt + 2 codex attempts (initial + retry) = 3 logged attempts.
        self.assertEqual(len(lines), 3)
        codex_lines = [l for l in lines if l["reviewer"] == "codex"]
        self.assertEqual([l["attempt"] for l in codex_lines], [1, 2])
        self.assertTrue(all(l.get("error_class") == "timeout" for l in codex_lines))
        gpt_line = [l for l in lines if l["reviewer"] == "gpt"][0]
        self.assertEqual(gpt_line["actual_input_tokens"], 111)
        # Metadata only: the packet content never appears in the log.
        raw = open(os.path.join(self.root, "invocation_log.jsonl"), encoding="utf-8").read()
        self.assertNotIn(secret_context, raw)

    def test_failed_command_invocations_are_logged(self):
        # An aborted invocation (usage error) still leaves a metadata line —
        # the acceptance run had eight invisible failures.
        parser = ucw.build_parser()
        argv = ["progress", self.root, "--work-item-id", "message:nope",
                "--message", "x", "--json"]
        args = parser.parse_args(argv)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            import time as _t
            t0 = _t.monotonic()
            code = args.func(args)
            cwrc.log_invocation(self.root, {
                "command": args.command, "duration_s": round(_t.monotonic() - t0, 3),
                "exit_code": code,
                "error_class": "validation_error" if code == ucw.EXIT_USAGE else None})
        lines = self._log_lines()
        self.assertTrue(any(l.get("command") == "progress"
                            and l.get("error_class") == "validation_error"
                            for l in lines))


class SkillWordingTests(unittest.TestCase):

    def test_skill_documents_real_commands_not_phantom_health_check(self):
        path = os.path.join(REPO_ROOT, ".claude", "skills", "use-cw", "SKILL.md")
        text = open(path, encoding="utf-8").read()
        # The phantom check ("GET /api/health (or `status`)") is gone; the skill
        # points at the real preflight command and documents the full exit table.
        self.assertNotIn("(or `status`)", text)
        self.assertIn("preflight", text)
        self.assertIn("7  usage or validation error", text)
        self.assertIn("--dry-run", text)
        self.assertIn("--envelope-file", text)
        self.assertIn("excluded_actions", text)
        self.assertIn("--grant-attempts", text)
        # verify's undocumented requirements are now documented.
        self.assertIn("requires `--thread-id`", text)


class NamingAndPrivacyTests(unittest.TestCase):

    def test_no_private_target_or_retired_terms(self):
        _wr = "w" + "rit"
        retired = re.compile("|".join([r"\b" + _wr + r"\b", "vol" + "tex"]), re.I)
        private = re.compile("|".join([r"\b" + "pl" + "ex" + r"\b",
                                       "d:" + re.escape("\\") + "dev"]), re.I)
        targets = [os.path.join(TOOLS_DIR, "clearwright_use_cw.py"),
                   os.path.join(TOOLS_DIR, "clearwright_review_council.py"),
                   os.path.join(TOOLS_DIR, "clearwright_gpt_review.py"),
                   os.path.join(TOOLS_DIR, "clearwright_codex_review.py"),
                   os.path.abspath(__file__)]
        for path in targets:
            with self.subTest(file=os.path.relpath(path, REPO_ROOT)):
                text = open(path, encoding="utf-8").read()
                self.assertIsNone(retired.search(text))
                self.assertIsNone(private.search(text))


if __name__ == "__main__":
    unittest.main()
