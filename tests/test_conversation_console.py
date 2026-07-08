"""Tests for the simulated agent conversation console (apps/control-plane).

The console is SIMULATED: every turn is generated locally by the demo server.
These tests pin that boundary (no real external model integration is claimed),
the bounded round count, the condensed decision fields, and the safety rule
that unsafe wording never condenses into a CTA recommendation.
"""
import os
import sys
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APP_DIR = os.path.join(REPO_ROOT, "apps", "control-plane")
STATIC = os.path.join(APP_DIR, "static")

sys.path.insert(0, APP_DIR)
sys.path.insert(0, os.path.join(REPO_ROOT, "tools"))
import server  # noqa: E402

SAFE_QUESTION = "Should we fix a typo in the README and align the version references?"
DESTRUCTIVE_QUESTION = "Delete all inactive records and wipe the old data store."
AMBIGUOUS_QUESTION = "Change the authentication settings somehow."


def read(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


class ConversationConsoleTests(unittest.TestCase):

    # ------------------------------------------------------------ bounds

    def test_conversation_maxes_at_five_rounds(self):
        for question in (SAFE_QUESTION, DESTRUCTIVE_QUESTION, AMBIGUOUS_QUESTION):
            with self.subTest(question=question):
                conv = server.build_conversation(question)
                self.assertTrue(conv["ok"], conv)
                self.assertEqual(conv["max_rounds"], 5)
                self.assertLessEqual(len(conv["turns"]), 5)

    def test_empty_question_refused(self):
        self.assertFalse(server.build_conversation("   ")["ok"])

    # ------------------------------------------------------- condensation

    def test_summary_has_required_fields(self):
        conv = server.build_conversation(SAFE_QUESTION)
        s = conv["summary"]
        self.assertTrue(s["decision_needed"])
        self.assertIn(s["recommended"], ("CTA", "DTA", "RFI"))
        self.assertTrue(isinstance(s["risks"], list) and s["risks"])
        self.assertTrue(s["proposed_next_action"])
        self.assertTrue(s["scope_boundary"])
        self.assertTrue(s["risk_level"])

    def test_conversation_is_marked_simulated(self):
        conv = server.build_conversation(SAFE_QUESTION)
        self.assertIs(conv["simulated"], True)

    def test_simulated_labels_present_in_ui_and_docs(self):
        # No one reading the UI or docs may think real agents are wired.
        html = read(os.path.join(STATIC, "index.html")).lower()
        self.assertIn("simulated agents", html)
        docs = read(os.path.join(REPO_ROOT, "docs", "CONTROL_PLANE_DEMO.md")).lower()
        self.assertIn("simulated", docs)
        self.assertIn("no real external model integration", docs)

    # ------------------------------------------------------------ safety

    def test_destructive_wording_never_recommends_cta(self):
        conv = server.build_conversation(DESTRUCTIVE_QUESTION)
        self.assertIn(conv["summary"]["recommended"], ("DTA", "RFI"))
        self.assertEqual(conv["summary"]["recommended"], "DTA")
        self.assertEqual(conv["summary"]["risk_level"], "high")
        self.assertIsNone(conv["summary"]["proposed_rta_title"])

    def test_ambiguous_security_wording_recommends_rfi(self):
        conv = server.build_conversation(AMBIGUOUS_QUESTION)
        self.assertEqual(conv["summary"]["recommended"], "RFI")

    def test_safe_improvement_wording_can_recommend_cta(self):
        conv = server.build_conversation(SAFE_QUESTION)
        s = conv["summary"]
        self.assertEqual(s["recommended"], "CTA")
        self.assertEqual(s["risk_level"], "low")
        self.assertTrue(s["scope_boundary"])
        self.assertTrue(s["proposed_rta_title"])
        self.assertTrue(s["proposed_rta_action"])

    # ------------------------------------------------------------- roles

    def test_roles_cover_analysis_challenge_code_and_review(self):
        conv = server.build_conversation(SAFE_QUESTION)
        roles = [t["role"] for t in conv["turns"]]
        self.assertEqual(roles, ["claude", "gpt", "codex", "claude", "gpt"])
        kinds = [t["kind"] for t in conv["turns"]]
        self.assertIn("analysis", kinds)
        self.assertIn("challenge", kinds)
        self.assertIn("code_impact", kinds)
        self.assertIn("final_review", kinds)

    def test_codex_turn_carries_code_impact_only_in_simulated_context(self):
        conv = server.build_conversation(SAFE_QUESTION)
        codex = [t for t in conv["turns"] if t["role"] == "codex"][0]
        self.assertTrue(codex.get("code_impact"))
        # The snippet exists only inside a conversation explicitly marked
        # simulated; that flag is the boundary.
        self.assertIs(conv["simulated"], True)

    # ----------------------------------------------- lifecycle integration

    def test_recommendation_feeds_the_real_lifecycle(self):
        # A CTA recommendation must be usable as a real RTA that travels the
        # existing lifecycle: request -> CTA -> claim -> DONE with results.
        import shutil
        root = server.make_queue_root()
        self.addCleanup(shutil.rmtree, root, ignore_errors=True)
        server.seed_queue(root)

        conv = server.build_conversation(SAFE_QUESTION)
        s = conv["summary"]
        res = server.do_request(root, {
            "title": s["proposed_rta_title"][:140],
            "packet_type": "docs_change",
            "requesting_agent": "agent/worker",
            "requested_action": s["proposed_rta_action"],
            "target_label": "sample software project",
        })
        self.assertTrue(res["ok"], res)
        fname = [f for f in os.listdir(os.path.join(root, "clearance_outbox"))
                 if f.startswith("cw-req-")][0]
        self.assertTrue(server.do_action(root, "cta", fname)["ok"])
        self.assertTrue(server.do_action(root, "claim", fname)["ok"])
        done = server.do_action(root, "complete", fname, "", {
            "summary": "Demo improvement completed.",
            "verification": "Suite passed.",
        })
        self.assertTrue(done["ok"], done)
        packet = server.load_json(os.path.join(root, "clearance_done", fname))
        self.assertEqual(packet["status"], "DONE")
        self.assertEqual(
            packet["audit_json"]["events"][-1]["results"]["summary"],
            "Demo improvement completed.")

    # -------------------------------------------------------------- naming

    def test_no_retired_naming_in_console_code(self):
        # Fragments so the retired tokens never appear in this file.
        import re
        retired = re.compile(
            "|".join([r"\b" + "w" + "rit" + r"\b", "vol" + "tex"]), re.I)
        for path in (os.path.join(APP_DIR, "server.py"),
                     os.path.join(STATIC, "app.js"),
                     os.path.join(STATIC, "index.html"),
                     os.path.join(STATIC, "style.css"),
                     os.path.abspath(__file__)):
            with self.subTest(file=os.path.basename(path)):
                self.assertIsNone(retired.search(read(path)))


if __name__ == "__main__":
    unittest.main()
