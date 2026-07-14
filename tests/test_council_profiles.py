"""Council efficiency + review profiles (commit 2 of the hardening PR).

The scoped-round guidance and role lanes are PROMPT-ONLY: they change the
reviewer context string but never touch evaluate(), the verdict schema, or the
reconciliation validation, and the deterministic agreement rule is unchanged.
review_profile is code (default) or editorial (default max 3 rounds).
"""
import os
import sys
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO_ROOT, "tools"))
import clearwright_review_council as cwrc  # noqa: E402


def _verdict(reviewer, verdict="approve", conf=0.95):
    return {"reviewer": reviewer, "verdict": verdict, "confidence": conf,
            "risk_level": "low", "blocking_findings": [], "required_changes": [],
            "nonblocking_findings": [], "disagreements": [], "assumptions": [],
            "questions": [], "recommended_plan": [],
            "summary": "A substantive review of the plan with no blocking issues."}


def _round(n, gv, cv, recon=None):
    return {"round": n, "phase": "plan", "substantive": True,
            "gpt": {"ok": True, "posted": True, "validated": True,
                    "source": "openai-api", "verdict": gv},
            "codex": {"ok": True, "posted": True, "validated": True,
                      "source": "codex-cli", "verdict": cv},
            "reconciliation": recon}


class GuidanceIsPromptOnlyTests(unittest.TestCase):

    def test_guidance_header_only_changes_the_prompt_string(self):
        # Round 1 code: no guidance header (unchanged behavior).
        self.assertEqual(cwrc._guidance_header("code", 1), "")
        # Round 1 editorial: role lanes only.
        h1 = cwrc._guidance_header("editorial", 1)
        self.assertIn("role lanes", h1.lower())
        self.assertNotIn("follow-up round", h1.lower())
        # Round 2+: adds scoped-round guidance for both profiles.
        h2 = cwrc._guidance_header("code", 2)
        self.assertIn("follow-up round", h2.lower())
        self.assertIn("blocking only for safety", h2.lower())

    def test_evaluate_is_identical_with_or_without_guidance(self):
        # The evaluator sees round records, never the prompt string. Feeding the
        # SAME two-round approving record yields the SAME outcome regardless of
        # whatever guidance was prepended to the dispatched context.
        council = {"council_id": "c1", "phase": "plan", "min_rounds": 2,
                   "max_rounds": 5, "approved_scope": "scope",
                   "approved_scope_sha256": cwrc._scope_hash("scope"),
                   "rounds": [1, 2]}
        recon_ready = {"accepted_findings": [], "rejected_findings": [],
                       "required_plan_changes": [], "revised_plan": ["done"],
                       "unresolved_blockers": [], "resolutions": [],
                       "ready_to_proceed": True, "summary": "agreed"}
        rounds = [_round(1, _verdict("gpt"), _verdict("codex"),
                         {**recon_ready, "ready_to_proceed": False}),
                  _round(2, _verdict("gpt"), _verdict("codex"), recon_ready)]
        outcome = cwrc.evaluate(council, rounds)
        self.assertEqual(outcome["outcome"], "agreement_threshold_met")
        # The council carrying a review_profile evaluates identically: the
        # profile is not an evaluator input.
        outcome2 = cwrc.evaluate({**council, "review_profile": "editorial"}, rounds)
        self.assertEqual(outcome["outcome"], outcome2["outcome"])
        self.assertEqual(outcome["ready_to_proceed"], outcome2["ready_to_proceed"])


class ProfileTests(unittest.TestCase):

    def test_create_council_records_profile_and_defaults_code(self):
        import shutil
        import tempfile
        base = tempfile.mkdtemp(prefix="prof_")
        self.addCleanup(shutil.rmtree, base, ignore_errors=True)
        c = cwrc.create_council(base, thread_id="t", work_item_id="message:m",
                                phase="plan")
        self.assertEqual(c["review_profile"], "code")
        c2 = cwrc.create_council(base, thread_id="t2", work_item_id="message:m2",
                                 phase="plan", review_profile="editorial")
        self.assertEqual(c2["review_profile"], "editorial")
        # An unknown profile falls back to code.
        c3 = cwrc.create_council(base, thread_id="t3", work_item_id="message:m3",
                                 phase="plan", review_profile="nonsense")
        self.assertEqual(c3["review_profile"], "code")

    def test_editorial_default_max_rounds_is_three(self):
        self.assertEqual(cwrc.EDITORIAL_DEFAULT_MAX_ROUNDS, 3)
        self.assertGreaterEqual(cwrc.EDITORIAL_DEFAULT_MAX_ROUNDS,
                                cwrc.MIN_ROUNDS_FLOOR)


if __name__ == "__main__":
    unittest.main()
