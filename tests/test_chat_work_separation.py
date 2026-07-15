"""Tests for PR #24: separating plain conversation from actionable work.

Chat is not work. A message may carry an optional intent: "chat" is plain
durable conversation that never derives a work item and never raises an
Attention state, and "request" is an actionable ask. When intent is absent a
message stays actionable, so every existing tool, relay, and script keeps its
behavior unchanged. The console composer defaults to Message (chat); Ask agent
and Create work item post actionable requests; Request clearance files an RTA.
"""
import os
import re
import subprocess
import sys
import shutil
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
import clearwright_work as cww  # noqa: E402
import clearwright_message as cwm  # noqa: E402


def read(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def queue(prefix, tc):
    base = tempfile.mkdtemp(prefix=prefix)
    tc.addCleanup(shutil.rmtree, base, ignore_errors=True)
    root, *_ = server.resolve_queue(base)
    return root


def chat(root, message, **kw):
    return server.do_message(root, dict(
        {"actor": "OPERATOR-0001", "role": "operator", "source": "operator-ui",
         "direction": "inbound", "intent": "chat", "message": message}, **kw))


def request(root, message, **kw):
    return server.do_message(root, dict(
        {"actor": "OPERATOR-0001", "role": "operator", "source": "operator-ui",
         "direction": "inbound", "intent": "request", "message": message}, **kw))


class MessageIntentTests(unittest.TestCase):

    def test_intent_is_stored_when_set(self):
        m = cwm.build_message("op", "hi", intent="chat")
        self.assertEqual(m["intent"], "chat")
        m2 = cwm.build_message("op", "do it", intent="request")
        self.assertEqual(m2["intent"], "request")

    def test_intent_absent_is_omitted_for_backward_compatibility(self):
        m = cwm.build_message("op", "hi")
        self.assertNotIn("intent", m)
        # An empty/blank intent is treated as absent, not an error.
        self.assertNotIn("intent", cwm.build_message("op", "hi", intent="  "))

    def test_invalid_intent_is_rejected(self):
        with self.assertRaises(ValueError):
            cwm.build_message("op", "hi", intent="urgent")

    def test_cli_exposes_intent_flag(self):
        out = subprocess.run(
            [sys.executable, os.path.join(TOOLS_DIR, "clearwright_message.py"),
             "post", "--help"],
            capture_output=True, text=True, check=True)
        self.assertIn("--intent", out.stdout)
        self.assertIn("chat", out.stdout)


class WorkItemSeparationTests(unittest.TestCase):

    def setUp(self):
        self.root = queue("cw_sep_", self)

    def test_chat_message_derives_no_work_item(self):
        self.assertTrue(chat(self.root, "Just chatting about the design.")["ok"])
        self.assertEqual(cww.derive_work_items(self.root), [])

    def test_request_message_derives_a_work_item(self):
        self.assertTrue(request(self.root, "Please review the repo.")["ok"])
        items = cww.derive_work_items(self.root)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["kind"], "message")
        self.assertEqual(items[0]["status"], "open")

    def test_v2_message_needs_explicit_request_intent(self):
        # Stabilization: the identity cutover closes the origin rule. A v2
        # message (identity_version >= 2) with no intent is NOT an origin, so
        # authority/no-intent messages can never become work items or titles.
        server.do_message(self.root, {"actor": "claude", "role": "orchestrator",
                                      "source": "cli", "direction": "inbound",
                                      "message": "No-intent v2 message."})
        self.assertEqual(len(cww.derive_work_items(self.root)), 0)
        # An explicit request intent derives one work item.
        server.do_message(self.root, {"actor": "claude", "role": "orchestrator",
                                      "source": "cli", "direction": "inbound",
                                      "intent": "request",
                                      "message": "Explicit request."})
        self.assertEqual(len(cww.derive_work_items(self.root)), 1)

    def test_chat_thread_becomes_work_when_actionable_followup_posted(self):
        first = chat(self.root, "Let's talk about the plan.")
        tid = first["thread_id"]
        self.assertEqual(cww.derive_work_items(self.root), [])
        # An actionable follow-up into the same thread escalates it to work.
        request(self.root, "Now actually do it.", thread_id=tid)
        items = cww.derive_work_items(self.root)
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["thread_id"], tid)

    def test_worker_status_ignores_chat(self):
        chat(self.root, "chatter one")
        chat(self.root, "chatter two")
        status = cww.worker_status(self.root)
        self.assertEqual(status["work_items_open"], 0)
        self.assertEqual(status["work_items_total"], 0)


class RunStatusAndSelectionTests(unittest.TestCase):

    def setUp(self):
        self.root = queue("cw_status_", self)

    def test_chat_only_thread_reports_chat_status(self):
        chat(self.root, "A quiet conversation.")
        runs = server.build_runs(self.root)
        self.assertEqual(len(runs), 1)
        self.assertEqual(runs[0]["status"], "chat")

    def test_request_thread_reports_open_status(self):
        request(self.root, "An actionable ask.")
        self.assertEqual(server.build_runs(self.root)[0]["status"], "open")

    def test_build_runs_filter_by_chat_status(self):
        chat(self.root, "chat one")
        request(self.root, "work one")
        self.assertEqual(len(server.build_runs(self.root, status="chat")), 1)
        self.assertEqual(len(server.build_runs(self.root, status="open")), 1)

    def test_default_active_run_skips_chat_and_picks_actionable(self):
        request(self.root, "Older actionable request.")
        # A newer chat thread must NOT steal the default Active Run selection.
        chat(self.root, "Newer plain chat.")
        run = server.build_active_run(self.root)
        self.assertEqual(run["messages"][0]["message"], "Older actionable request.")

    def test_default_active_run_falls_back_to_chat_when_only_chat(self):
        chat(self.root, "Only a chat here.")
        run = server.build_active_run(self.root)
        self.assertEqual(run["messages"][0]["message"], "Only a chat here.")

    def test_chat_thread_selectable_explicitly(self):
        res = chat(self.root, "Pick me by id.")
        run = server.build_active_run(self.root, thread_id=res["thread_id"])
        self.assertEqual(run["thread_id"], res["thread_id"])
        self.assertEqual(run["messages"][0]["message"], "Pick me by id.")


class HealthTests(unittest.TestCase):

    def setUp(self):
        self.root = queue("cw_health_", self)

    def test_chat_does_not_turn_health_yellow(self):
        chat(self.root, "normal chat, no action needed")
        h = server.build_health(self.root, mode="operator", durable=True,
                                codex_check=lambda: True)
        self.assertEqual(h["work_items_open"], 0)
        self.assertEqual(h["status"], "green")

    def test_request_turns_health_yellow(self):
        request(self.root, "please act on this")
        h = server.build_health(self.root, mode="operator", durable=True,
                                codex_check=lambda: True)
        self.assertEqual(h["work_items_open"], 1)
        self.assertEqual(h["status"], "yellow")


class ServerPassThroughTests(unittest.TestCase):

    def test_do_message_passes_intent_through(self):
        root = queue("cw_pass_", self)
        res = server.do_message(root, {"actor": "op", "message": "hi",
                                       "direction": "inbound", "intent": "chat"})
        self.assertTrue(res["ok"])
        self.assertEqual(cwm.read_messages(root)[0]["intent"], "chat")

    def test_do_message_rejects_bad_intent(self):
        root = queue("cw_pass2_", self)
        res = server.do_message(root, {"actor": "op", "message": "hi",
                                       "intent": "nope"})
        self.assertFalse(res["ok"])
        self.assertIn("intent", res["error"])


class UiTests(unittest.TestCase):

    def setUp(self):
        self.html = read(os.path.join(STATIC, "index.html"))
        self.appjs = read(os.path.join(STATIC, "app.js"))
        self.css = read(os.path.join(STATIC, "style.css"))

    def test_composer_mode_selector_exists_with_message_default(self):
        self.assertIn('id="conv-mode"', self.html)
        for label in ("Message", "Ask agent", "Create work item", "Request clearance"):
            self.assertIn(">" + label + "<", self.html)
        # Message is the default selected option, not a work item.
        self.assertIn('<option value="chat" selected>Message</option>', self.html)

    def test_composer_sets_intent_by_mode(self):
        self.assertIn('intent: mode === "chat" ? "chat" : "request"', self.appjs)
        self.assertIn('if (mode === "clearance")', self.appjs)

    def test_quick_operator_chat_posts_chat_intent(self):
        # The compact quick box is normal chat, never a work item. The
        # composer's buildFields callback is what stamps intent: "chat" onto
        # every send (see createComposer / initOperatorChatComposer).
        self.assertIn('intent: "chat"', self.appjs)
        self.assertIn("initOperatorChatComposer", self.appjs)

    def test_work_items_panel_declares_actionable_only(self):
        self.assertIn("Actionable work only", self.html)
        self.assertIn("Normal chat stays in the Conversation tab", self.html)

    def test_chat_badge_style_present(self):
        self.assertIn(".run-status-chat", self.css)

    def test_honesty_caption_updated(self):
        self.assertIn("never a work item and never an Attention flag", self.html)

    def test_other_views_intact(self):
        # Conversations and Active Run merged into the unified Work page;
        # the queue region replaced the work-items panel.
        for token in ('id="center-work"', 'id="queue-region"',
                      'id="history-view"', 'id="health-chip"',
                      'placeholder="Send Agents a Message (Shift+Enter for a new line, Ctrl+Enter to send)"'):
            self.assertIn(token, self.html)


class DocsTests(unittest.TestCase):

    def test_docs_describe_chat_vs_work(self):
        opmode = read(os.path.join(DOCS, "OPERATOR_MODE.md"))
        comms = read(os.path.join(DOCS, "LOCAL_COMMUNICATIONS.md"))
        self.assertIn("intent", comms)
        self.assertIn("chat", comms.lower())
        self.assertIn("Chat is not work", opmode + comms)


class NamingAndPrivacyTests(unittest.TestCase):

    def test_no_private_target_or_retired_terms(self):
        _wr = "w" + "rit"
        retired = re.compile("|".join([r"\b" + _wr + r"\b", "vol" + "tex"]), re.I)
        private = re.compile("|".join([r"\b" + "pl" + "ex" + r"\b",
                                       "d:" + re.escape("\\") + "dev"]), re.I)
        targets = [os.path.join(TOOLS_DIR, "clearwright_message.py"),
                   os.path.join(TOOLS_DIR, "clearwright_work.py"),
                   os.path.join(APP_DIR, "server.py"),
                   os.path.join(STATIC, "index.html"),
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
