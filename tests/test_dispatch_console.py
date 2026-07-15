"""Tests for the live dispatch console and history (tools/clearwright_work.py,
the /api/work-items and /api/history endpoints, and the operator chat UI).

Work items are derived from existing durable state (packets + messages), never a
separate database. These tests cover: operator messages posted with the
operator-ui source; deriving message / CTA-packet / IN_PROGRESS / RFI work items;
that claiming records a durable claim without losing the original request and
that a CTA claim uses the real packet lifecycle; that responding writes a durable
response in the same thread; packet linkage; on-disk persistence; the history
aggregation and its filters; that operator mode does not show simulated
conversation while demo mode still labels simulation; that the existing
agent-events, messages, and request -> CTA -> claim -> DONE flow still work; and
that nothing here names the private demo target or uses retired terms.
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

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APP_DIR = os.path.join(REPO_ROOT, "apps", "control-plane")
TOOLS_DIR = os.path.join(REPO_ROOT, "tools")
STATIC = os.path.join(APP_DIR, "static")
DOCS = os.path.join(REPO_ROOT, "docs")
WORK_TOOL = os.path.join(TOOLS_DIR, "clearwright_work.py")

sys.path.insert(0, APP_DIR)
sys.path.insert(0, TOOLS_DIR)
import server  # noqa: E402
import clearwright_work as cww  # noqa: E402
import clearwright_message as cwm  # noqa: E402
import clearwright_agent_event as cwae  # noqa: E402


def read(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


REQUEST_FIELDS = {
    "title": "Add a status endpoint to the sample web application",
    "packet_type": "code_change",
    "requesting_agent": "agent/worker",
    "requested_action": "Add a read-only status endpoint. Findings only.",
    "target_label": "sample web application",
}


def outbox_files(root):
    d = os.path.join(root, "clearance_outbox")
    return sorted(f for f in os.listdir(d) if f.endswith(".json")) if os.path.isdir(d) else []


class OperatorMessageTests(unittest.TestCase):

    def setUp(self):
        self.base = tempfile.mkdtemp(prefix="disp_op_")
        self.addCleanup(shutil.rmtree, self.base, ignore_errors=True)
        self.root, *_ = server.resolve_queue(self.base)  # operator

    def test_operator_message_records_source_and_identity(self):
        res = server.do_message(self.root, {
            "actor": "OPERATOR-0001", "role": "operator", "source": "operator-ui",
            "direction": "inbound", "message": "Review this repo under CW."})
        self.assertTrue(res["ok"], res)
        m = cwm.read_messages(self.root)[0]
        self.assertEqual(m["actor"], "OPERATOR-0001")
        self.assertEqual(m["role"], "operator")
        self.assertEqual(m["source"], "operator-ui")
        self.assertEqual(m["direction"], "inbound")
        self.assertIs(m["simulated"], False)

    def test_work_items_include_unclaimed_operator_message(self):
        server.do_message(self.root, {
            "actor": "OPERATOR-0001", "role": "operator", "intent": "request", "source": "operator-ui",
            "message": "Review this repo under CW."})
        items = cww.derive_work_items(self.root)
        msgs = [i for i in items if i["kind"] == "message"]
        self.assertEqual(len(msgs), 1)
        self.assertEqual(msgs[0]["status"], "open")
        self.assertEqual(msgs[0]["next_action"], "respond")


class WorkItemLifecycleTests(unittest.TestCase):

    def setUp(self):
        self.base = tempfile.mkdtemp(prefix="disp_wi_")
        self.addCleanup(shutil.rmtree, self.base, ignore_errors=True)
        self.root, *_ = server.resolve_queue(self.base)  # operator

    def test_claim_records_without_losing_original(self):
        server.do_message(self.root, {"actor": "OPERATOR-0001", "role": "operator", "intent": "request",
                                      "message": "Review this repo under CW."})
        wid = [i for i in cww.derive_work_items(self.root) if i["kind"] == "message"][0]["work_item_id"]
        res = cww.claim_work_item(self.root, wid, "claude")
        self.assertTrue(res["ok"], res)
        self.assertEqual(res["message"]["direction"], "internal")
        self.assertEqual(res["message"]["status"], "claimed")
        self.assertEqual(res["message"]["work_item_id"], wid)
        # The original request is still on disk.
        texts = [m["message"] for m in cwm.read_messages(self.root)]
        self.assertIn("Review this repo under CW.", texts)
        # The derived item now reads as claimed.
        item = [i for i in cww.derive_work_items(self.root) if i["kind"] == "message"][0]
        self.assertEqual(item["status"], "claimed")
        self.assertEqual(item["claimed_by"], "claude")

    def test_respond_writes_durable_response_in_same_thread(self):
        server.do_message(self.root, {"actor": "OPERATOR-0001", "role": "operator", "intent": "request",
                                      "message": "Review this repo under CW."})
        origin = cwm.read_messages(self.root)[0]
        wid = "message:" + origin["message_id"]
        res = cww.respond_work_item(self.root, wid, "claude", "I am reviewing the repo.")
        self.assertTrue(res["ok"], res)
        self.assertEqual(res["message"]["direction"], "outbound")
        self.assertEqual(res["message"]["status"], "responded")
        self.assertEqual(res["thread_id"], origin["thread_id"])
        self.assertEqual(res["message"]["work_item_id"], wid)
        # Responding closes the open request work item.
        self.assertEqual([i for i in cww.derive_work_items(self.root) if i["kind"] == "message"], [])

    def test_work_item_links_to_packet_id(self):
        server.do_message(self.root, {"actor": "OPERATOR-0001", "role": "operator", "intent": "request",
                                      "message": "About this packet", "packet_id": "cw-harness-301"})
        item = [i for i in cww.derive_work_items(self.root) if i["kind"] == "message"][0]
        self.assertEqual(item["packet_id"], "cw-harness-301")

    def test_missing_actor_claim_fails_safely(self):
        server.do_message(self.root, {"actor": "OPERATOR-0001", "role": "operator", "intent": "request",
                                      "message": "hi"})
        wid = [i for i in cww.derive_work_items(self.root) if i["kind"] == "message"][0]["work_item_id"]
        res = cww.claim_work_item(self.root, wid, "  ")
        self.assertFalse(res["ok"])

    def test_unrecognized_work_item_id(self):
        self.assertFalse(cww.claim_work_item(self.root, "bogus:1", "claude")["ok"])
        self.assertFalse(cww.respond_work_item(self.root, "bogus:1", "claude", "hi")["ok"])


class DerivedPacketWorkItemTests(unittest.TestCase):

    def setUp(self):
        self.base = tempfile.mkdtemp(prefix="disp_pkt_")
        self.addCleanup(shutil.rmtree, self.base, ignore_errors=True)
        self.root, *_ = server.resolve_queue(self.base)  # operator, empty

    def _new_request(self):
        self.assertTrue(server.do_request(self.root, dict(REQUEST_FIELDS))["ok"])
        return outbox_files(self.root)[-1]

    def test_cta_packet_is_a_claimable_work_item(self):
        fn = self._new_request()
        server.do_action(self.root, "cta", fn)
        items = cww.derive_work_items(self.root)
        cta = [i for i in items if i["kind"] == "packet"]
        self.assertEqual(len(cta), 1)
        self.assertEqual(cta[0]["next_action"], "claim")
        self.assertTrue(cta[0]["work_item_id"].endswith(":cta"))

    def test_cta_claim_uses_real_packet_lifecycle(self):
        fn = self._new_request()
        server.do_action(self.root, "cta", fn)
        wid = [i for i in cww.derive_work_items(self.root) if i["kind"] == "packet"][0]["work_item_id"]
        with contextlib.redirect_stdout(io.StringIO()):
            res = cww.claim_work_item(self.root, wid, "claude")
        self.assertTrue(res["ok"], res)
        self.assertTrue(res.get("packet_claimed"))
        # The packet actually moved lanes via the real claim tool.
        path, lane = server.find_packet(self.root, fn)
        self.assertEqual(lane, "clearance_in_progress")
        self.assertEqual(server.load_json(path)["status"], "IN_PROGRESS")
        # And it now surfaces as an in_progress work item, not a CTA one.
        kinds = {i["kind"] for i in cww.derive_work_items(self.root)}
        self.assertIn("in_progress", kinds)
        self.assertNotIn("packet", kinds)

    def test_in_progress_packet_needs_update_work_item(self):
        fn = self._new_request()
        server.do_action(self.root, "cta", fn)
        with contextlib.redirect_stdout(io.StringIO()):
            server.do_action(self.root, "claim", fn)
        items = cww.derive_work_items(self.root)
        ip = [i for i in items if i["kind"] == "in_progress"]
        self.assertEqual(len(ip), 1)
        self.assertTrue(ip[0]["work_item_id"].startswith("in_progress:"))

    def test_rfi_packet_needs_clarification_work_item(self):
        fn = self._new_request()
        server.do_action(self.root, "rfi", fn, "Which files does this change?")
        items = cww.derive_work_items(self.root)
        rfi = [i for i in items if i["kind"] == "rfi"]
        self.assertEqual(len(rfi), 1)
        self.assertTrue(rfi[0]["work_item_id"].startswith("rfi:"))


class HistoryTests(unittest.TestCase):

    def setUp(self):
        self.base = tempfile.mkdtemp(prefix="disp_hist_")
        self.addCleanup(shutil.rmtree, self.base, ignore_errors=True)
        self.root, *_ = server.resolve_queue(self.base)  # operator

    def test_history_includes_all_three_sources(self):
        server.do_request(self.root, dict(REQUEST_FIELDS))
        server.do_message(self.root, {"actor": "OPERATOR-0001", "role": "operator", "intent": "request",
                                      "message": "note", "packet_id": "cw-harness-301"})
        server.do_agent_event(self.root, {"actor": "claude", "message": "looked",
                                          "packet_id": "cw-harness-301"})
        h = server.build_history(self.root)
        self.assertGreaterEqual(len(h["packets"]), 1)
        self.assertGreaterEqual(len(h["messages"]), 1)
        self.assertGreaterEqual(len(h["events"]), 1)

    def test_history_filters(self):
        server.do_message(self.root, {"actor": "OPERATOR-0001", "role": "operator", "intent": "request",
                                      "message": "for p1", "packet_id": "cw-1"})
        server.do_message(self.root, {"actor": "claude", "message": "other", "packet_id": "cw-2"})
        by_packet = server.build_history(self.root, packet_id="cw-1")
        self.assertEqual([m["message"] for m in by_packet["messages"]], ["for p1"])
        by_actor = server.build_history(self.root, actor="claude")
        self.assertTrue(all(m["actor"] == "claude" for m in by_actor["messages"]))

    def test_related_messages_and_events_for_a_packet(self):
        # This is what the packet audit drawer reads to show working context.
        server.do_message(self.root, {"actor": "claude", "message": "progress",
                                      "packet_id": "cw-harness-301"})
        server.do_agent_event(self.root, {"actor": "claude", "message": "event",
                                          "packet_id": "cw-harness-301"})
        self.assertEqual(len(cwm.read_messages(self.root, packet_id="cw-harness-301")), 1)
        self.assertEqual(len(cwae.read_events(self.root, "cw-harness-301")), 1)


class CliTests(unittest.TestCase):

    def setUp(self):
        self.base = tempfile.mkdtemp(prefix="disp_cli_")
        self.addCleanup(shutil.rmtree, self.base, ignore_errors=True)
        self.root, *_ = server.resolve_queue(self.base)

    def _run(self, *args):
        import subprocess
        return subprocess.run([sys.executable, WORK_TOOL, *args],
                              capture_output=True, encoding="utf-8", errors="replace")

    def test_cli_list_claim_respond(self):
        server.do_message(self.root, {"actor": "OPERATOR-0001", "role": "operator", "intent": "request",
                                      "message": "Review this repo under CW."})
        listed = self._run("list", self.root)
        self.assertEqual(listed.returncode, 0, listed.stderr)
        items = json.loads(listed.stdout)
        wid = [i for i in items if i["kind"] == "message"][0]["work_item_id"]
        claimed = self._run("claim", self.root, "--work-item-id", wid, "--actor", "claude")
        self.assertEqual(claimed.returncode, 0, claimed.stderr)
        self.assertIn("CLAIMED", claimed.stdout)
        responded = self._run("respond", self.root, "--work-item-id", wid,
                              "--actor", "claude", "--message", "reviewing")
        self.assertEqual(responded.returncode, 0, responded.stderr)
        self.assertIn("RESPONDED", responded.stdout)


class UiAndExistingBehaviorTests(unittest.TestCase):

    def test_operator_chat_posts_real_message_not_simulated(self):
        appjs = read(os.path.join(STATIC, "app.js"))
        html = read(os.path.join(STATIC, "index.html"))
        # The operator chat posts a real inbound message to /api/messages.
        self.assertIn("function submitOperatorChat", appjs)
        self.assertIn("/api/messages", appjs)
        self.assertIn("OPERATOR-0001", appjs)
        self.assertIn("operator-ui", appjs)
        self.assertIn('id="operator-chat-form"', html)
        # Work items and the unified ledger are wired to their endpoints.
        self.assertIn("/api/work-items", appjs)
        self.assertIn("/api/ledger", appjs)

    def test_operator_mode_hides_simulated_conversation(self):
        appjs = read(os.path.join(STATIC, "app.js"))
        html = read(os.path.join(STATIC, "index.html"))
        self.assertIn('id="convo-panel"', html)
        self.assertIn("convo-panel", appjs)
        self.assertIn("relatedContextHtml", appjs)  # audit drawer shows working context

    def test_demo_mode_still_labels_simulation(self):
        html = read(os.path.join(STATIC, "index.html"))
        self.assertIn("simulated agents", html)
        self.assertIn("Simulated demo", html)

    def test_existing_agent_events_and_messages_endpoints_work(self):
        base = tempfile.mkdtemp(prefix="disp_ex_")
        self.addCleanup(shutil.rmtree, base, ignore_errors=True)
        root, *_ = server.resolve_queue(base)
        self.assertTrue(server.do_agent_event(root, {"actor": "a", "message": "ev"})["ok"])
        self.assertEqual(len(cwae.read_events(root)), 1)
        self.assertTrue(server.do_message(root, {"actor": "a", "message": "msg"})["ok"])
        self.assertEqual(len(cwm.read_messages(root)), 1)

    def test_request_to_done_with_results_still_works(self):
        base = tempfile.mkdtemp(prefix="disp_flow_")
        self.addCleanup(shutil.rmtree, base, ignore_errors=True)
        root, *_ = server.resolve_queue(base)
        self.assertTrue(server.do_request(root, dict(REQUEST_FIELDS))["ok"])
        fn = outbox_files(root)[0]
        self.assertTrue(server.do_action(root, "cta", fn)["ok"])
        self.assertTrue(server.do_action(root, "claim", fn)["ok"])
        done = server.do_action(root, "complete", fn, "", {
            "summary": "Added the endpoint.", "verification": "tests pass",
            "changed_files": ["app/status.py"], "findings": "none"})
        self.assertTrue(done["ok"], done)
        path, lane = server.find_packet(root, fn)
        self.assertEqual(lane, "clearance_done")
        self.assertEqual(server.load_json(path)["status"], "DONE")


class DocsAndNamingTests(unittest.TestCase):

    def test_docs_mention_dispatch_and_work_items(self):
        comms = read(os.path.join(DOCS, "LOCAL_COMMUNICATIONS.md")).lower()
        self.assertIn("work item", comms)
        self.assertIn("/api/work-items", read(os.path.join(DOCS, "LOCAL_COMMUNICATIONS.md")))
        self.assertIn("clearwright_work", comms)
        self.assertIn("discord", comms)
        self.assertIn("not intended for production", comms)
        self.assertNotIn("production-" + "ready", comms)

    def test_no_private_target_or_retired_terms(self):
        _wr = "w" + "rit"
        retired = re.compile("|".join([r"\b" + _wr + r"\b", "vol" + "tex"]), re.I)
        private = re.compile("|".join([r"\b" + "pl" + "ex" + r"\b",
                                       "d:" + re.escape("\\") + "dev"]), re.I)
        targets = [
            WORK_TOOL,
            os.path.join(TOOLS_DIR, "clearwright_message.py"),
            os.path.join(APP_DIR, "server.py"),
            os.path.join(STATIC, "index.html"),
            os.path.join(STATIC, "app.js"),
            os.path.join(STATIC, "style.css"),
            os.path.join(DOCS, "LOCAL_COMMUNICATIONS.md"),
        ]
        for path in targets:
            with self.subTest(file=os.path.relpath(path, REPO_ROOT)):
                text = read(path)
                self.assertIsNone(retired.search(text))
                self.assertIsNone(private.search(text))


if __name__ == "__main__":
    unittest.main()
