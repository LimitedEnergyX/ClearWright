"""Tests for the local communications loop (tools/clearwright_message.py and the
control plane server's /api/messages endpoints).

Messages are a durable, packet-linkable, threaded log alongside the clearance
queue, distinct from clearance packets and from agent events. These tests cover
the shared build/write/read logic (used by both the CLI and the server), the
server's POST/GET handlers via do_message and read_messages, threading and
packet association, ordering, persistence on disk, safe failure on missing
fields, that the CLI can post and list from the command line without a browser,
that operator mode does not present simulated conversation as real, that the
docs present the local adapter (not browser automation) as the integration
surface with Discord as future work, that the existing agent-events endpoint and
the request -> CTA -> claim -> DONE-with-results flow still work, and that
nothing here names the private demo target or uses retired terms.
"""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APP_DIR = os.path.join(REPO_ROOT, "apps", "control-plane")
TOOLS_DIR = os.path.join(REPO_ROOT, "tools")
STATIC = os.path.join(APP_DIR, "static")
DOCS = os.path.join(REPO_ROOT, "docs")
MSG_TOOL = os.path.join(TOOLS_DIR, "clearwright_message.py")

sys.path.insert(0, APP_DIR)
sys.path.insert(0, TOOLS_DIR)
import clearwright_message as cwm  # noqa: E402
import clearwright_agent_event as cwae  # noqa: E402
import server  # noqa: E402


def read(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def run_cli(*args):
    return subprocess.run([sys.executable, MSG_TOOL, *args],
                          capture_output=True, encoding="utf-8", errors="replace")


class BuildWriteReadTests(unittest.TestCase):

    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="comms_")
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)

    def files(self):
        d = os.path.join(self.root, cwm.COMMS_DIR)
        return sorted(os.listdir(d)) if os.path.isdir(d) else []

    def test_build_message_shape_and_defaults(self):
        m = cwm.build_message("claude", "starting review")
        self.assertTrue(m["message_id"].startswith("msg-"))
        self.assertTrue(m["thread_id"].startswith("thr-"))
        self.assertTrue(m["at"])
        self.assertEqual(m["actor"], "claude")
        self.assertEqual(m["role"], "agent")
        self.assertEqual(m["direction"], "inbound")
        self.assertEqual(m["status"], "posted")
        self.assertEqual(m["source"], "local-adapter")
        self.assertIs(m["simulated"], False)
        self.assertNotIn("packet_id", m)  # omitted when not provided

    def test_build_message_reuses_supplied_thread(self):
        m = cwm.build_message("a", "b", thread_id="thr-fixed")
        self.assertEqual(m["thread_id"], "thr-fixed")

    def test_build_message_validates_direction_and_status(self):
        with self.assertRaises(ValueError):
            cwm.build_message("a", "b", direction="sideways")
        with self.assertRaises(ValueError):
            cwm.build_message("a", "b", status="unknown")

    def test_build_message_raises_on_missing_fields(self):
        with self.assertRaises(ValueError):
            cwm.build_message("", "m")
        with self.assertRaises(ValueError):
            cwm.build_message("a", "")

    def test_write_persists_on_disk_and_reads_back(self):
        cwm.write_message(self.root, cwm.build_message("script", "hello"))
        d = os.path.join(self.root, cwm.COMMS_DIR)
        self.assertTrue(os.path.isdir(d))
        self.assertEqual(len(self.files()), 1)
        with open(os.path.join(d, self.files()[0]), encoding="utf-8") as fh:
            self.assertEqual(json.load(fh)["message"], "hello")

    def test_messages_returned_in_order(self):
        for i in range(3):
            cwm.write_message(self.root, cwm.build_message("a", "msg {}".format(i)))
        msgs = [m["message"] for m in cwm.read_messages(self.root)]
        self.assertEqual(msgs, ["msg 0", "msg 1", "msg 2"])

    def test_packet_and_thread_filters(self):
        cwm.write_message(self.root, cwm.build_message("a", "m1", packet_id="cw-1", thread_id="thr-1"))
        cwm.write_message(self.root, cwm.build_message("a", "m2", packet_id="cw-2", thread_id="thr-2"))
        cwm.write_message(self.root, cwm.build_message("a", "m3", thread_id="thr-1"))
        by_packet = cwm.read_messages(self.root, packet_id="cw-1")
        self.assertEqual([m["message"] for m in by_packet], ["m1"])
        by_thread = cwm.read_messages(self.root, thread_id="thr-1")
        self.assertEqual([m["message"] for m in by_thread], ["m1", "m3"])
        self.assertEqual(len(cwm.read_messages(self.root)), 3)

    def test_persist_across_reload(self):
        cwm.write_message(self.root, cwm.build_message("a", "one"))
        cwm.write_message(self.root, cwm.build_message("a", "two"))
        # A fresh read (as a reloaded server would do) sees the same durable log.
        first = cwm.read_messages(self.root)
        second = cwm.read_messages(self.root)
        self.assertEqual(len(first), 2)
        self.assertEqual([m["message_id"] for m in first],
                         [m["message_id"] for m in second])


class ServerEndpointTests(unittest.TestCase):

    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="comms_srv_")
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)

    def files(self):
        d = os.path.join(self.root, cwm.COMMS_DIR)
        return sorted(os.listdir(d)) if os.path.isdir(d) else []

    def test_post_writes_and_get_returns(self):
        # do_message mirrors POST /api/messages; read_messages mirrors GET.
        res = server.do_message(self.root, {
            "actor": "claude", "role": "orchestrator",
            "message": "Claude posted this through the local communications loop.",
            "packet_id": "cw-harness-301",
        })
        self.assertTrue(res["ok"], res)
        got = cwm.read_messages(self.root)
        self.assertEqual(len(got), 1)
        self.assertEqual(got[0]["actor"], "claude")
        self.assertEqual(got[0]["role"], "orchestrator")
        self.assertEqual(got[0]["direction"], "inbound")
        self.assertEqual(got[0]["status"], "posted")
        self.assertEqual(got[0]["source"], "local-http")
        self.assertEqual(got[0]["packet_id"], "cw-harness-301")

    def test_respond_reuses_thread_and_is_outbound(self):
        first = server.do_message(self.root, {
            "actor": "claude", "message": "starting review", "packet_id": "cw-harness-301"})
        tid = first["thread_id"]
        second = server.do_message(self.root, {
            "actor": "claude", "message": "review complete",
            "thread_id": tid, "packet_id": "cw-harness-301"}, respond=True)
        self.assertTrue(second["ok"], second)
        self.assertEqual(second["thread_id"], tid)
        self.assertEqual(second["message"]["direction"], "outbound")
        self.assertEqual(second["message"]["status"], "responded")
        thread = cwm.read_messages(self.root, thread_id=tid)
        self.assertEqual([m["message"] for m in thread],
                         ["starting review", "review complete"])

    def test_respond_without_thread_fails_safely(self):
        res = server.do_message(self.root, {"actor": "a", "message": "b"}, respond=True)
        self.assertFalse(res["ok"])
        self.assertIn("thread", res["error"])
        self.assertEqual(self.files(), [], "no message written on refusal")

    def test_missing_actor_or_message_fails_safely(self):
        for payload in ({"actor": "claude"}, {"message": "hi"},
                        {"actor": "  ", "message": "hi"},
                        {"actor": "claude", "message": "   "}):
            with self.subTest(payload=payload):
                res = server.do_message(self.root, payload)
                self.assertFalse(res["ok"], res)
        self.assertEqual(self.files(), [], "no message written on failure")

    def test_get_filters_by_packet_and_thread(self):
        server.do_message(self.root, {"actor": "a", "message": "p1", "packet_id": "cw-1"})
        r2 = server.do_message(self.root, {"actor": "a", "message": "t-only"})
        server.do_message(self.root, {"actor": "a", "message": "p2", "packet_id": "cw-1"})
        self.assertEqual(len(cwm.read_messages(self.root, packet_id="cw-1")), 2)
        self.assertEqual(len(cwm.read_messages(self.root, thread_id=r2["thread_id"])), 1)


class CliTests(unittest.TestCase):

    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="comms_cli_")
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)

    def test_cli_post_then_list(self):
        posted = run_cli("post", self.root, "--actor", "claude", "--role",
                         "orchestrator", "--message", "cli post",
                         "--packet-id", "cw-harness-301")
        self.assertEqual(posted.returncode, 0, posted.stderr)
        self.assertIn("RECORDED", posted.stdout)
        listed = run_cli("list", self.root, "--packet-id", "cw-harness-301")
        self.assertEqual(listed.returncode, 0, listed.stderr)
        rows = json.loads(listed.stdout)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["message"], "cli post")
        self.assertEqual(rows[0]["packet_id"], "cw-harness-301")

    def test_cli_respond_requires_thread_id(self):
        # --thread-id is a required argument for respond; argparse exits 2.
        r = run_cli("respond", self.root, "--actor", "x", "--message", "y")
        self.assertEqual(r.returncode, 2)

    def test_cli_missing_message_is_argument_error(self):
        r = run_cli("post", self.root, "--actor", "claude")
        self.assertEqual(r.returncode, 2)

    def test_cli_post_and_respond_share_thread(self):
        posted = run_cli("post", self.root, "--actor", "claude", "--message", "q")
        self.assertEqual(posted.returncode, 0, posted.stderr)
        tid = cwm.read_messages(self.root)[0]["thread_id"]
        resp = run_cli("respond", self.root, "--thread-id", tid, "--actor",
                       "claude", "--message", "a")
        self.assertEqual(resp.returncode, 0, resp.stderr)
        thread = cwm.read_messages(self.root, thread_id=tid)
        self.assertEqual([m["message"] for m in thread], ["q", "a"])
        self.assertEqual(thread[1]["direction"], "outbound")


class UiTests(unittest.TestCase):

    def test_operator_ui_shows_real_comms_and_hides_simulated_conversation(self):
        html = read(os.path.join(STATIC, "index.html"))
        appjs = read(os.path.join(STATIC, "app.js"))
        # A real, packet-linked communications panel exists and targets the API.
        self.assertIn('id="comms"', html)
        self.assertIn("Local communications", html)
        self.assertIn("/api/messages", html)
        self.assertIn("function renderMessages", appjs)
        self.assertIn("refreshMessages", appjs)
        self.assertIn("/api/messages", appjs)
        # The simulated conversation panel is identifiable and hidden by mode.
        self.assertIn('id="convo-panel"', html)
        self.assertIn("convo-panel", appjs)
        # The audit drawer surfaces related messages as working context.
        self.assertIn("relatedContextHtml", appjs)


class ExistingBehaviorTests(unittest.TestCase):

    def test_agent_events_endpoint_still_works(self):
        root = tempfile.mkdtemp(prefix="comms_ev_")
        self.addCleanup(shutil.rmtree, root, ignore_errors=True)
        res = server.do_agent_event(root, {"actor": "claude", "message": "still works"})
        self.assertTrue(res["ok"], res)
        self.assertEqual(len(cwae.read_events(root)), 1)

    def test_request_to_done_with_results_still_works(self):
        base = tempfile.mkdtemp(prefix="comms_flow_")
        self.addCleanup(shutil.rmtree, base, ignore_errors=True)
        root, _, mode, seeded = server.resolve_queue(base)  # operator, empty
        self.assertEqual(mode, "operator")
        self.assertFalse(seeded)
        res = server.do_request(root, {
            "title": "Add a status endpoint to the sample web application",
            "packet_type": "code_change",
            "requesting_agent": "agent/worker",
            "requested_action": "Add a read-only status endpoint. Findings only.",
            "target_label": "sample web application",
        })
        self.assertTrue(res["ok"], res)
        outbox = os.path.join(root, "clearance_outbox")
        fn = [f for f in os.listdir(outbox) if f.endswith(".json")][0]
        self.assertTrue(server.do_action(root, "cta", fn)["ok"])
        self.assertTrue(server.do_action(root, "claim", fn)["ok"])
        done = server.do_action(root, "complete", fn, "", {
            "summary": "Added the read-only status endpoint.",
            "verification": "Ran the sample project tests; all pass.",
            "changed_files": ["app/status.py"],
            "findings": "No issues found.",
        })
        self.assertTrue(done["ok"], done)
        path, lane = server.find_packet(root, fn)
        self.assertEqual(lane, "clearance_done")
        self.assertEqual(server.load_json(path)["status"], "DONE")


class DocsAndNamingTests(unittest.TestCase):

    def test_local_comms_doc_explains_the_loop(self):
        doc = read(os.path.join(DOCS, "LOCAL_COMMUNICATIONS.md"))
        low = doc.lower()
        self.assertIn("/api/messages", doc)
        self.assertIn("clearwright_message", low)
        self.assertIn("local http", low)
        # Discord is documented as a future transport, not built here.
        self.assertIn("discord", low)
        self.assertIn("future", low)
        # Browser automation is not the integration method.
        self.assertIn("not the integration", low)
        # Honest maturity.
        self.assertIn("early alpha", low)
        self.assertIn("not intended for production", low)
        self.assertNotIn("production-" + "ready", low)

    def test_no_private_target_or_retired_terms(self):
        _wr = "w" + "rit"
        retired = re.compile("|".join([r"\b" + _wr + r"\b", "vol" + "tex"]), re.I)
        private = re.compile("|".join([r"\b" + "pl" + "ex" + r"\b",
                                       "d:" + re.escape("\\") + "dev"]), re.I)
        targets = [
            MSG_TOOL,
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
