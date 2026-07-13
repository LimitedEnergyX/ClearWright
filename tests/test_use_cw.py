"""Tests for PR #26: the Use CW execution wrapper, skill, and installer.

The wrapper is a thin orchestration layer over the message, work-item, and
Review Council helpers. Council rounds are exercised with mocked reviewers (no
network, no real Codex); the wrapper's own commands (start/progress/complete/
status) and its exit-code mapping are tested directly.
"""
import io
import json
import os
import re
import contextlib
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
import clearwright_work as cww  # noqa: E402
import clearwright_review_council as cwrc  # noqa: E402
import clearwright_use_cw as ucw  # noqa: E402
import install_use_cw_skill as inst  # noqa: E402


def queue(prefix, tc):
    base = tempfile.mkdtemp(prefix=prefix)
    tc.addCleanup(shutil.rmtree, base, ignore_errors=True)
    root, *_ = server.resolve_queue(base)
    return root


def run(func, **kw):
    """Call a wrapper command with a Namespace and capture (result, exit_code)."""
    kw.setdefault("json", True)
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        code = func(Namespace(**kw))
    return json.loads(buf.getvalue().strip().splitlines()[-1]), code


def mock_verdict(reviewer, verdict="approve", conf=0.9):
    return {"reviewer": reviewer, "verdict": verdict, "confidence": conf,
            "risk_level": "low", "blocking_findings": [], "required_changes": [],
            "nonblocking_findings": [], "disagreements": [], "assumptions": [],
            "questions": [], "recommended_plan": [], "summary": "A substantive review."}


def mock_reviewer(reviewer, source):
    def fn(root, context, **kw):
        return {"ok": True, "posted": True, "reviewer": reviewer,
                "verdict": mock_verdict(reviewer), "validated": True, "source": source,
                "telemetry": {"reviewer": reviewer}, "message_id": reviewer[0]}
    return fn


class ClassifyTests(unittest.TestCase):

    def test_classification_heuristics(self):
        self.assertEqual(ucw.classify_request("hi there, just checking in"), "chat")
        self.assertEqual(ucw.classify_request("Refactor the parser for clarity."), "actionable")
        self.assertEqual(ucw.classify_request("Deploy the site to production."), "governed")
        self.assertEqual(ucw.classify_request("Change the access control on the repo."), "high_risk")

    def test_hints_match_on_word_boundaries_not_substrings(self):
        # 'fyi' must not match inside 'clarifying'; 'hi' must not match 'this'.
        self.assertEqual(ucw.classify_request(
            "Add one clarifying comment describing the exit-code contract."), "actionable")
        self.assertEqual(ucw.classify_request(
            "Rename this variable in the parser."), "actionable")
        # 'production' governs, but 'reproduce' must not be read as 'prod'.
        self.assertEqual(ucw.classify_request(
            "Reproduce the parser test locally and tidy it."), "actionable")


class StartTests(unittest.TestCase):

    def setUp(self):
        self.root = queue("ucw_start_", self)

    def test_actionable_creates_and_claims_work_item(self):
        res, code = run(ucw.cmd_start, queue_root=self.root,
                        request="Add a small usability tweak.", request_file=None,
                        kind=None, thread_id=None, packet_id=None,
                        approved_scope="tweak scope", actor="claude")
        self.assertEqual(code, ucw.EXIT_OK)
        self.assertEqual(res["kind"], "actionable")
        self.assertTrue(res["work_item_id"].startswith("message:"))
        self.assertTrue(res["claimed"])
        item = cww.find_work_item(self.root, res["work_item_id"])
        self.assertEqual(item["status"], "claimed")

    def test_chat_creates_no_work_item(self):
        res, code = run(ucw.cmd_start, queue_root=self.root,
                        request="hi, just checking in, thoughts?", request_file=None,
                        kind=None, thread_id=None, packet_id=None,
                        approved_scope=None, actor="claude")
        self.assertEqual(code, ucw.EXIT_OK)
        self.assertEqual(res["kind"], "chat")
        self.assertIsNone(res["work_item_id"])
        self.assertEqual(cww.derive_work_items(self.root), [])

    def test_governed_flags_requires_clearance(self):
        res, _ = run(ucw.cmd_start, queue_root=self.root,
                     request="Publish and deploy the new release to production.",
                     request_file=None, kind=None, thread_id=None, packet_id=None,
                     approved_scope=None, actor="claude")
        self.assertEqual(res["kind"], "governed")
        self.assertTrue(res["requires_clearance"])


class ProgressCompleteTests(unittest.TestCase):

    def setUp(self):
        self.root = queue("ucw_pc_", self)
        res, _ = run(ucw.cmd_start, queue_root=self.root, request="Do a small thing.",
                     request_file=None, kind=None, thread_id=None, packet_id=None,
                     approved_scope="scope", actor="claude")
        self.wid = res["work_item_id"]

    def test_progress_posts_a_note(self):
        res, code = run(ucw.cmd_progress, queue_root=self.root, work_item_id=self.wid,
                        message="Working on it.", message_file=None)
        self.assertEqual(code, ucw.EXIT_OK)
        self.assertTrue(res["ok"])

    def test_progress_unknown_work_item(self):
        res, code = run(ucw.cmd_progress, queue_root=self.root,
                        work_item_id="message:nope", message="x", message_file=None)
        self.assertEqual(code, ucw.EXIT_USAGE)
        self.assertFalse(res["ok"])

    def test_complete_records_done(self):
        res, code = run(ucw.cmd_complete, queue_root=self.root, work_item_id=self.wid,
                        packet_id=None, result="Finished and verified.", result_file=None)
        self.assertEqual(code, ucw.EXIT_OK)
        self.assertEqual(res["status"], "done")

    def test_complete_governed_without_clearance_is_authority_stop(self):
        res, code = run(ucw.cmd_complete, queue_root=self.root, work_item_id=self.wid,
                        packet_id="cw-nonexistent", result="done", result_file=None)
        self.assertEqual(code, ucw.EXIT_AUTHORITY)
        self.assertEqual(res["error"], "required_authority_not_granted")


class CouncilExitCodeTests(unittest.TestCase):

    def setUp(self):
        self.root = queue("ucw_council_", self)
        res, _ = run(ucw.cmd_start, queue_root=self.root, request="Plan a change.",
                     request_file=None, kind=None, thread_id=None, packet_id=None,
                     approved_scope="operator approved scope", actor="claude")
        self.thread = res["thread_id"]
        self.wid = res["work_item_id"]

    def _base(self, **kw):
        base = dict(queue_root=self.root, phase="plan", council_id=None,
                    thread_id=self.thread, work_item_id=self.wid, packet_id=None,
                    repo=None, plan_file=None, context_file=None, prompt="review this plan",
                    reconciliation_file=None, stage="review", model=None,
                    approved_scope="operator approved scope", timeout=30, json=True)
        base.update(kw)
        return base

    def test_review_round_maps_needs_revision_to_exit_2(self):
        # Inject mock reviewers so no network / real Codex runs.
        orig = cwrc.run_round
        def patched(root, council, context, **kw):
            return orig(root, council, context, gpt_fn=mock_reviewer("gpt", "openai-api"),
                        codex_fn=mock_reviewer("codex", "codex-cli"))
        cwrc.run_round = patched
        self.addCleanup(setattr, cwrc, "run_round", orig)

        res, code = run(lambda a: ucw._council(a, "plan"), **self._base())
        self.assertEqual(code, ucw.EXIT_REVISION)  # round 1, min 2 not met
        self.assertEqual(res["outcome"], "needs_revision")
        self.assertEqual(res["gpt_status"], "review")
        self.assertEqual(res["codex_status"], "review")

    def test_full_agreement_maps_to_exit_0(self):
        # Build a two-round agreeing council directly, then reconcile via the wrapper.
        c = cwrc.create_council(self.root, thread_id=self.thread, work_item_id=self.wid,
                                approved_scope="operator approved scope")
        for _ in range(2):
            cwrc.run_round(self.root, c, "ctx", gpt_fn=mock_reviewer("gpt", "openai-api"),
                           codex_fn=mock_reviewer("codex", "codex-cli"))
            c = cwrc.load_council(self.root, c["council_id"])
        recon = {"accepted_findings": ["ok"], "rejected_findings": [],
                 "required_plan_changes": [], "revised_plan": ["do it"],
                 "unresolved_blockers": [], "resolutions": [],
                 "ready_to_proceed": True, "summary": "reconciled the two reviews"}
        rf = os.path.join(self.root, "recon.json")
        with open(rf, "w", encoding="utf-8") as fh:
            json.dump(recon, fh)
        res, code = run(lambda a: ucw._council(a, "plan"),
                        **self._base(stage="reconcile", council_id=c["council_id"],
                                     reconciliation_file=rf))
        self.assertEqual(code, ucw.EXIT_OK)
        self.assertEqual(res["outcome"], "agreement_threshold_met")
        self.assertTrue(res["ready_to_proceed"])

    def test_status_reports_council(self):
        c = cwrc.create_council(self.root, thread_id=self.thread, approved_scope="scope")
        res, code = run(ucw.cmd_status, queue_root=self.root, council_id=c["council_id"],
                        thread_id=None)
        self.assertTrue(res["ok"])
        self.assertEqual(res["council"]["council_id"], c["council_id"])


class SkillAndInstallerTests(unittest.TestCase):

    def setUp(self):
        self.skill = os.path.join(REPO_ROOT, ".claude", "skills", "use-cw", "SKILL.md")

    def test_skill_file_present_and_shaped(self):
        self.assertTrue(os.path.isfile(self.skill))
        text = open(self.skill, encoding="utf-8").read()
        self.assertIn("name: use-cw", text)
        self.assertIn("clearwright_use_cw.py", text)
        for token in ("start", "council", "progress", "incident", "verify", "complete", "status"):
            self.assertIn(token, text)
        # The exit-code contract must be documented for the skill to branch on it.
        self.assertIn("operator required", text)
        self.assertIn("hard gate", text)

    def test_install_is_safe_atomic_and_verified(self):
        tmp = tempfile.mkdtemp(prefix="ucw_skill_")
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        target = os.path.join(tmp, ".claude", "skills", "use-cw", "SKILL.md")
        # Fresh install.
        r1 = inst.install(target=target)
        self.assertTrue(r1["ok"] and r1["verified"])
        self.assertEqual(r1["status"], "installed")
        self.assertTrue(os.path.isfile(target))
        # Idempotent re-install: already current, no backup.
        r2 = inst.install(target=target)
        self.assertEqual(r2["status"], "already_current")
        self.assertIsNone(r2["backup"])
        # A changed target is backed up (never deleted), then replaced.
        with open(target, "w", encoding="utf-8") as fh:
            fh.write("stale local edit\n")
        r3 = inst.install(target=target, stamp="20260101T000000")
        self.assertEqual(r3["status"], "installed")
        self.assertTrue(os.path.isfile(r3["backup"]))
        self.assertEqual(open(r3["backup"], encoding="utf-8").read(), "stale local edit\n")

    def test_installer_reports_version(self):
        r = inst.install(target=os.path.join(tempfile.mkdtemp(prefix="ucw_v_"), "SKILL.md"))
        self.assertRegex(r["version"], r"^\d+\.\d+\.\d+$")


class NamingAndPrivacyTests(unittest.TestCase):

    def test_no_private_target_or_retired_terms(self):
        _wr = "w" + "rit"
        retired = re.compile("|".join([r"\b" + _wr + r"\b", "vol" + "tex"]), re.I)
        private = re.compile("|".join([r"\b" + "pl" + "ex" + r"\b",
                                       "d:" + re.escape("\\") + "dev"]), re.I)
        targets = [os.path.join(TOOLS_DIR, "clearwright_use_cw.py"),
                   os.path.join(TOOLS_DIR, "install_use_cw_skill.py"),
                   os.path.join(REPO_ROOT, ".claude", "skills", "use-cw", "SKILL.md"),
                   os.path.abspath(__file__)]
        for path in targets:
            with self.subTest(file=os.path.relpath(path, REPO_ROOT)):
                text = open(path, encoding="utf-8").read()
                self.assertIsNone(retired.search(text))
                self.assertIsNone(private.search(text))


if __name__ == "__main__":
    unittest.main()
