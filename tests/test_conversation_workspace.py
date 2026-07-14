"""Tests for PR #23: the Conversation Workspace and GET /api/conversations.

Conversations are the durable message threads, presented conversation-first:
/api/conversations reuses the same derivation as the run registry (one summary
per thread, no new store), the selected thread is retrieved via
/api/active-run?thread_id=, and the composer continues a selected thread or
starts a new one. Target hints are intent labels only - participation is real
only when a worker posts back. No real Codex is invoked here.
"""
import json
import os
import re
import shutil
import sys
import tempfile
import unittest
from datetime import datetime, timezone, timedelta

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
import clearwright_agent_event as cwae  # noqa: E402
import clearwright_codex_review as ccr  # noqa: E402

REQUEST_FIELDS = {
    "title": "Add a status endpoint to the sample web application",
    "packet_type": "code_change",
    "requesting_agent": "agent/worker",
    "requested_action": "Add a read-only status endpoint. Findings only.",
    "target_label": "sample web application",
}


def read(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def operator_queue(prefix, tc):
    base = tempfile.mkdtemp(prefix=prefix)
    tc.addCleanup(shutil.rmtree, base, ignore_errors=True)
    root, *_ = server.resolve_queue(base)
    return root


class ConversationsEndpointTests(unittest.TestCase):

    def setUp(self):
        self.root = operator_queue("cv_", self)
        # Conversation 1: full cycle with a mocked Codex review.
        server.do_message(self.root, {"actor": "OPERATOR-0001", "role": "operator",
                                      "source": "operator-ui",
                                      "message": "Conv one: please review."})
        w1 = cww.derive_work_items(self.root)[0]["work_item_id"]
        cww.claim_work_item(self.root, w1, "claude")
        rr = ccr.review(self.root, w1,
                        runner=lambda p, t, cwd=None: (
                            "A substantive Codex review body with plenty of content to pass.",
                            ccr.build_telemetry("x" * 90, 0, 1.5)),
                        available_fn=lambda: True)
        assert rr["codex_posted"]
        cww.respond_work_item(self.root, w1, "claude", "Conv one answered.")
        self.t1 = cwm.read_messages(self.root)[0]["thread_id"]
        # Conversation 2: claimed only.
        server.do_message(self.root, {"actor": "OPERATOR-0001", "role": "operator",
                                      "source": "operator-ui", "message": "Conv two: pending."})
        w2 = [i for i in cww.derive_work_items(self.root) if i["kind"] == "message"][0]["work_item_id"]
        cww.claim_work_item(self.root, w2, "claude")
        # Conversation 3: open.
        server.do_message(self.root, {"actor": "OPERATOR-0001", "role": "operator",
                                      "source": "claude-code-relay", "message": "Conv three: open."})

    def convs(self, **kw):
        return server.build_runs(self.root, **kw)  # same derivation the endpoint serves

    def test_returns_thread_summaries_with_fields(self):
        convs = self.convs()
        self.assertEqual(len(convs), 3)
        c = [x for x in convs if x["title"].startswith("Conv one")][0]
        for key in ("thread_id", "title", "first_timestamp", "last_timestamp",
                    "message_count", "actors", "sources", "status",
                    "has_codex_review", "latest_message_preview",
                    "work_item_id", "packet_id"):
            self.assertIn(key, c)
        self.assertEqual(c["message_count"], 4)
        self.assertIn("OPERATOR-0001", c["actors"])
        self.assertIn("operator-ui", c["sources"])

    def test_status_detection_and_codex_flag(self):
        by = {c["title"][:8]: c for c in self.convs()}
        self.assertEqual(by["Conv one"]["status"], "responded")
        self.assertEqual(by["Conv two"]["status"], "claimed")
        self.assertEqual(by["Conv thr"]["status"], "open")
        self.assertTrue(by["Conv one"]["has_codex_review"])
        self.assertFalse(by["Conv thr"]["has_codex_review"])

    def test_selected_thread_returns_ordered_messages(self):
        run = server.build_active_run(self.root, thread_id=self.t1)
        self.assertEqual(run["thread_id"], self.t1)
        msgs = [m["message"] for m in run["messages"]]
        self.assertEqual(msgs[0], "Conv one: please review.")
        self.assertEqual(msgs[-1], "Conv one answered.")
        ats = [m["at"] for m in run["messages"]]
        self.assertEqual(ats, sorted(ats))

    def test_continuing_thread_preserves_thread_id(self):
        res = server.do_message(self.root, {
            "actor": "OPERATOR-0001", "role": "operator", "source": "operator-ui",
            "direction": "inbound", "message": "Follow-up on conv one.",
            "thread_id": self.t1})
        self.assertTrue(res["ok"], res)
        self.assertEqual(res["thread_id"], self.t1)
        run = server.build_active_run(self.root, thread_id=self.t1)
        self.assertEqual(run["messages"][-1]["message"], "Follow-up on conv one.")

    def test_new_operator_message_creates_new_thread(self):
        before = {c["thread_id"] for c in self.convs()}
        res = server.do_message(self.root, {
            "actor": "OPERATOR-0001", "role": "operator", "source": "operator-ui",
            "direction": "inbound", "message": "Brand new conversation."})
        self.assertTrue(res["ok"], res)
        self.assertNotIn(res["thread_id"], before)
        self.assertEqual(len(self.convs()), 4)

    def test_query_filters(self):
        self.assertEqual(len(self.convs(status="open")), 1)
        self.assertEqual(len(self.convs(has_codex=True)), 1)
        self.assertEqual(len(self.convs(limit=2)), 2)
        self.assertEqual(len(self.convs(source="claude-code-relay")), 1)

    def test_conversations_route_is_wired(self):
        src = read(os.path.join(APP_DIR, "server.py"))
        self.assertIn('"/api/conversations"', src)
        self.assertIn('"conversations" if path == "/api/conversations" else "runs"', src)


class UiTests(unittest.TestCase):

    def setUp(self):
        self.html = read(os.path.join(STATIC, "index.html"))
        self.appjs = read(os.path.join(STATIC, "app.js"))
        self.css = read(os.path.join(STATIC, "style.css"))

    def test_conversations_button_and_view_exist(self):
        self.assertIn('id="conversations-btn"', self.html)
        self.assertIn('id="conversations-view"', self.html)
        self.assertIn('id="conv-list"', self.html)
        self.assertIn('id="conv-detail"', self.html)
        self.assertIn("function renderConvList", self.appjs)
        self.assertIn("function renderConvDetail", self.appjs)
        self.assertIn("/api/conversations", self.appjs)

    def test_composer_placeholder_exact_and_thread_behavior(self):
        self.assertIn('id="conv-composer"', self.html)
        self.assertEqual(self.html.count('placeholder="Send Agents a Message (Shift+Enter for a new line, Ctrl+Enter to send)"'), 2)
        # Continuing a selected thread, adopting the new thread id on create.
        self.assertIn("if (selectedConvThread) return { thread_id: selectedConvThread };", self.appjs)
        self.assertIn("if (!selectedConvThread && result.thread_id) selectedConvThread = result.thread_id;",
                      self.appjs)

    def test_copy_and_escalation_sources(self):
        self.assertIn("copy thread_id", self.appjs)
        self.assertIn("data-copy-summary", self.appjs)
        self.assertIn('data-action="escalate"', self.appjs)
        self.assertIn('data-action="workitem"', self.appjs)
        self.assertIn('data-action="ack"', self.appjs)
        self.assertIn('id="escalate-modal"', self.html)
        self.assertIn("Operator acknowledged this conversation.", self.appjs)

    def test_target_hint_is_honest_and_nonbinding(self):
        self.assertIn('id="conv-target"', self.html)
        self.assertIn("intent hint only", self.html)
        self.assertIn("participation is real only when a worker posts back", self.html)
        # The hint only prefixes the message text; it never posts as an agent.
        self.assertIn('actor: "OPERATOR-0001"', self.appjs)

    def test_operator_mode_stays_real_only(self):
        self.assertIn('id="convo-panel"', self.html)   # simulated convo stays demo-only
        self.assertIn("simulated agents", self.html)
        self.assertIn("nothing here is simulated", self.html)

    def test_other_views_intact(self):
        for token in ('id="active-run-view"', 'id="history-view"', 'id="health-chip"',
                      'id="pulse-inspector"', 'id="work-items"', 'id="comms"'):
            self.assertIn(token, self.html)


class RegressionTests(unittest.TestCase):

    def setUp(self):
        self.root = operator_queue("cv_reg_", self)

    def test_builders_still_work(self):
        server.do_message(self.root, {"actor": "a", "message": "m"})
        server.do_agent_event(self.root, {"actor": "a", "message": "e"})
        state = server.build_state(self.root)
        self.assertIn("pulse", state)
        self.assertIn("active_phase", state["pulse"])
        self.assertEqual(len(server.build_runs(self.root)), 1)
        self.assertIsNotNone(server.build_active_run(self.root)["thread_id"])
        self.assertIsInstance(cww.derive_work_items(self.root), list)
        self.assertEqual(len(cwm.read_messages(self.root)), 1)
        self.assertEqual(len(cwae.read_events(self.root)), 1)
        h = server.build_health(self.root, mode="operator", durable=True,
                                codex_check=lambda: True)
        self.assertIn(h["status"], ("green", "yellow"))

    def test_archive_flag_still_works(self):
        old = (datetime.now(timezone.utc) - timedelta(days=2)).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
        with open(os.path.join(self.root, "clearance_done", "cw-old.json"), "w", encoding="utf-8") as fh:
            json.dump({"packet_id": "cw-old", "status": "DONE",
                       "audit_json": {"events": [{"event": "complete", "at": old}]}}, fh)
        state = server.build_state(self.root, mode="operator", durable=True)
        self.assertTrue(state["lanes"]["clearance_done"][0]["archived"])
        self.assertFalse(server.compute_pulse(self.root)["done"])

    def test_request_to_done_with_results(self):
        self.assertTrue(server.do_request(self.root, dict(REQUEST_FIELDS))["ok"])
        fn = [f for f in os.listdir(os.path.join(self.root, "clearance_outbox"))
              if f.endswith(".json")][0]
        self.assertTrue(server.do_action(self.root, "cta", fn)["ok"])
        self.assertTrue(server.do_action(self.root, "claim", fn)["ok"])
        done = server.do_action(self.root, "complete", fn, "", {
            "summary": "done", "verification": "ok",
            "changed_files": ["app/status.py"], "findings": "none"})
        self.assertTrue(done["ok"], done)
        path, lane = server.find_packet(self.root, fn)
        self.assertEqual((lane, server.load_json(path)["status"]), ("clearance_done", "DONE"))


class NamingAndPrivacyTests(unittest.TestCase):

    def test_no_private_target_or_retired_terms(self):
        _wr = "w" + "rit"
        retired = re.compile("|".join([r"\b" + _wr + r"\b", "vol" + "tex"]), re.I)
        private = re.compile("|".join([r"\b" + "pl" + "ex" + r"\b",
                                       "d:" + re.escape("\\") + "dev"]), re.I)
        targets = [os.path.join(APP_DIR, "server.py"),
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
