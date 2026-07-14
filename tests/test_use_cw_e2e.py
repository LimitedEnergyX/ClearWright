"""End-to-end regression for the exact Desktop "Use CW" invocation sequence
that passed acceptance on 2026-07-14 (see docs/ACCEPTANCE.md): start with an
envelope -> plan council (two rounds, reconcile between) -> agreement -> verify
council (two rounds) -> agreement -> complete -> DONE, with round-start digests
in the timeline and exactly one canonical summary message per governance state.

Reviewers are mocked (the live fixture used real ones); everything else is the
real wrapper and engine.
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
import clearwright_review_council as cwrc  # noqa: E402
import clearwright_use_cw as ucw  # noqa: E402


def run(func, **kw):
    kw.setdefault("json", True)
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        code = func(Namespace(**kw))
    return json.loads(buf.getvalue().strip().splitlines()[-1]), code


def reviewer(name, source, verdict="approve_with_changes", conf=0.9, req=None):
    def fn(root, context, **kw):
        return {"ok": True, "posted": True, "reviewer": name,
                "verdict": {"reviewer": name, "verdict": verdict, "confidence": conf,
                            "risk_level": "low", "blocking_findings": [],
                            "required_changes": list(req or []),
                            "nonblocking_findings": [], "disagreements": [],
                            "assumptions": [], "questions": [],
                            "recommended_plan": [], "summary": "A substantive review."},
                "validated": True, "source": source,
                "telemetry": {"reviewer": name}, "message_id": name[0]}
    return fn


class DesktopInvocationE2ETests(unittest.TestCase):

    def setUp(self):
        base = tempfile.mkdtemp(prefix="ucw_e2e_")
        self.addCleanup(shutil.rmtree, base, ignore_errors=True)
        self.root, *_ = server.resolve_queue(base)
        # start's implicit preflight: stub the environment probes (CI-safe).
        import clearwright_gpt_review as gpt_mod
        import clearwright_codex_review as ccr_mod
        self.addCleanup(setattr, gpt_mod, "resolve_api_key", gpt_mod.resolve_api_key)
        self.addCleanup(setattr, ccr_mod, "codex_executable", ccr_mod.codex_executable)
        gpt_mod.resolve_api_key = lambda *a, **k: ("test-key-present", "process_env")
        ccr_mod.codex_executable = lambda: "codex-stub"
        # The council engine dispatches through mocked reviewers.
        self._orig_run_round = cwrc.run_round
        def patched(root, council, context, **kw):
            kw.pop("gpt_fn", None), kw.pop("codex_fn", None)
            return self._orig_run_round(
                root, council, context, sleep=lambda *_: None,
                gpt_fn=reviewer("gpt", "openai-api"),
                codex_fn=reviewer("codex", "codex-cli"), **{
                    k: v for k, v in kw.items() if k in ("model", "repo", "timeout",
                                                         "artifact_ids")})
        cwrc.run_round = patched
        self.addCleanup(setattr, cwrc, "run_round", self._orig_run_round)

    def _council_args(self, phase, **kw):
        base = dict(queue_root=self.root, phase=phase, council_id=None,
                    thread_id=self.thread, work_item_id=self.wid, packet_id=None,
                    repo=None, plan_file=None, context_file=None, prompt=None,
                    reconciliation_file=None, stage="review", dry_run=False,
                    model=None, approved_scope=self.scope, min_rounds=2, max_rounds=5,
                    grant_attempts=None, operator_message_id=None, grant_count=1,
                    artifact=None, artifact_id=None, timeout=30, json=True)
        base.update(kw)
        return base

    def _reconcile(self, phase, council_id, ready):
        recon = {"accepted_findings": ["reviewer guidance incorporated"],
                 "rejected_findings": [], "required_plan_changes": [],
                 "revised_plan": ["revised per reviewer findings"],
                 "unresolved_blockers": [],
                 "resolutions": [{"ref": "gpt.required_changes[0]",
                                  "disposition": "accepted", "note": "done"},
                                 {"ref": "codex.required_changes[0]",
                                  "disposition": "accepted", "note": "done"}],
                 "ready_to_proceed": ready,
                 "summary": "reconciled both reviews against the source"}
        rf = os.path.join(self.root, "recon-{}-{}.json".format(phase, ready))
        with open(rf, "w", encoding="utf-8") as fh:
            json.dump(recon, fh)
        return run(lambda a: ucw._council(a, phase),
                   **self._council_args(phase, stage="reconcile",
                                        council_id=council_id,
                                        reconciliation_file=rf))

    def test_full_desktop_sequence_to_done(self):
        self.scope = ("Read-only inspection of the operator interface; recommend one "
                      "small usability improvement; no product changes.")
        # 1. start with the structured envelope (the skill's primary path).
        env = {"envelope_version": 1, "task_kind": "actionable",
               "request": "Inspect the operator interface and recommend one small "
                          "usability improvement.",
               "approved_scope": self.scope,
               "intended_actions": ["inspect UI source", "run plan council",
                                    "run verification council", "record result"],
               "excluded_actions": ["edit files", "deploy", "publish"],
               "operator_authority_source": "operator Use CW instruction",
               "verification_required": True}
        ef = os.path.join(self.root, "envelope.json")
        with open(ef, "w", encoding="utf-8") as fh:
            json.dump(env, fh)
        res, code = run(ucw.cmd_start, queue_root=self.root, envelope_file=ef,
                        request=None, request_file=None, kind=None, thread_id=None,
                        packet_id=None, approved_scope=None, actor="claude")
        self.assertEqual(code, ucw.EXIT_OK)
        self.thread, self.wid = res["thread_id"], res["work_item_id"]
        self.assertTrue(res["claimed"] and res["verification_required"])

        # 2. plan council: round 1 -> reconcile -> round 2 -> reconcile -> agreement.
        r1, code = run(lambda a: ucw._council(a, "plan"),
                       **self._council_args("plan", prompt="plan packet round one"))
        self.assertEqual(code, ucw.EXIT_REVISION)
        plan_id = r1["council_id"]
        self._reconcile("plan", plan_id, ready=False)
        r2, code = run(lambda a: ucw._council(a, "plan"),
                       **self._council_args("plan", council_id=plan_id,
                                            prompt="plan packet round two"))
        self.assertEqual(code, ucw.EXIT_REVISION)
        out, code = self._reconcile("plan", plan_id, ready=True)
        self.assertEqual(code, ucw.EXIT_OK)
        self.assertEqual(out["outcome"], "agreement_threshold_met")

        # 3. verify council: two rounds to agreement.
        v1, code = run(lambda a: ucw._council(a, "verify"),
                       **self._council_args("verify", prompt="verification evidence round one"))
        self.assertEqual(code, ucw.EXIT_REVISION)
        verify_id = v1["council_id"]
        self._reconcile("verify", verify_id, ready=False)
        run(lambda a: ucw._council(a, "verify"),
            **self._council_args("verify", council_id=verify_id,
                                 prompt="verification evidence round two"))
        out, code = self._reconcile("verify", verify_id, ready=True)
        self.assertEqual(code, ucw.EXIT_OK)
        self.assertEqual(out["outcome"], "agreement_threshold_met")

        # 4. complete -> DONE (verification passed, gate satisfied).
        res, code = run(ucw.cmd_complete, queue_root=self.root, work_item_id=self.wid,
                        packet_id=None, result="Recommendation recorded; no product "
                        "changes made.", result_file=None)
        self.assertEqual(code, ucw.EXIT_OK)
        self.assertEqual(res["status"], "done")

        # 5. The timeline is the complete exchange: request, claim, round-start
        # digests (the plan IS visible in the conversation), reviewer messages,
        # reconciliations, and the canonical summary.
        msgs = cwm.read_messages(self.root, thread_id=self.thread)
        bodies = [(m.get("actor"), m.get("source"), m.get("message", "")[:40]) for m in msgs]
        round_starts = [m for m in msgs if m.get("source") == "review-council"
                        and m.get("message", "").startswith("Review Council round")]
        self.assertEqual(len(round_starts), 4, bodies)  # 2 plan + 2 verify rounds
        recons = [m for m in msgs if m.get("source") == "review-council"
                  and m.get("message", "").startswith("Claude reconciliation")]
        self.assertEqual(len(recons), 4)
        # Reviewer participation lives in the council records (mocked reviewers
        # do not post thread messages; real adapters do): both councils carry
        # two substantive rounds with validated gpt + codex results each.
        for cid in (plan_id, verify_id):
            rounds = [r for r in cwrc.load_rounds(self.root, cid)
                      if r.get("substantive", True)]
            self.assertEqual(len(rounds), 2)
            for r in rounds:
                self.assertEqual(cwrc._reviewer_status(r["gpt"], "gpt"), "review")
                self.assertEqual(cwrc._reviewer_status(r["codex"], "codex"), "review")

        # 6. Summary posting is idempotent per governance state: plan agreement,
        # verify agreement, and done each post once; a repeat emit posts nothing.
        summaries = [m for m in msgs if m.get("source") == "use-cw-summary"]
        count_before = len(summaries)
        summary = ucw.load_summary(self.root, self.wid)
        again = ucw.persist_and_post_summary(self.root, self.wid, dict(summary))
        self.assertFalse(again["message_posted"])
        msgs2 = cwm.read_messages(self.root, thread_id=self.thread)
        self.assertEqual(len([m for m in msgs2 if m.get("source") == "use-cw-summary"]),
                         count_before)
        self.assertEqual(summary["status"], "done")


class SkillDefaultsTests(unittest.TestCase):

    def test_skill_codifies_the_standing_defaults(self):
        text = open(os.path.join(REPO_ROOT, ".claude", "skills", "use-cw", "SKILL.md"),
                    encoding="utf-8").read()
        self.assertIn("Standing defaults", text)
        self.assertIn("do NOT re-ask", text)
        # Read-only semantics: governance records and runtime evidence permitted.
        self.assertIn("governance records", text)
        self.assertIn("ALWAYS permitted", text)
        # Operator interface defaults to the web UI.
        self.assertIn("web UI", text)
        # Transport is an implementation detail.
        self.assertIn("implementation detail", text)
        self.assertIn("Desktop Commander", text)


class NamingAndPrivacyTests(unittest.TestCase):

    def test_no_private_target_or_retired_terms(self):
        _wr = "w" + "rit"
        retired = re.compile("|".join([r"\b" + _wr + r"\b", "vol" + "tex"]), re.I)
        private = re.compile("|".join([r"\b" + "pl" + "ex" + r"\b",
                                       "d:" + re.escape("\\") + "dev"]), re.I)
        targets = [os.path.join(REPO_ROOT, "docs", "ACCEPTANCE.md"),
                   os.path.join(REPO_ROOT, ".claude", "skills", "use-cw", "SKILL.md"),
                   os.path.abspath(__file__)]
        for path in targets:
            with self.subTest(file=os.path.relpath(path, REPO_ROOT)):
                text = open(path, encoding="utf-8").read()
                self.assertIsNone(retired.search(text))
                self.assertIsNone(private.search(text))


if __name__ == "__main__":
    unittest.main()
