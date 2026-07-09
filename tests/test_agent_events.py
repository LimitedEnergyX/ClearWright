"""Tests for the local agent event adapter (tools/clearwright_agent_event.py
and the control plane server's /api/agent-events endpoints).

Agent events are a durable log alongside the clearance queue, distinct from
clearance packets. These tests cover the shared build/write/read logic (used by
both the CLI and the server), the server's POST/GET handlers via do_agent_event
and read_events, event persistence and packet association, safe failure on
missing fields, the simulated flag, and that the UI/docs present the local
adapter (not browser automation) as the integration surface.
"""
import json
import os
import shutil
import sys
import tempfile
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APP_DIR = os.path.join(REPO_ROOT, "apps", "control-plane")
TOOLS_DIR = os.path.join(REPO_ROOT, "tools")
STATIC = os.path.join(APP_DIR, "static")

sys.path.insert(0, APP_DIR)
sys.path.insert(0, TOOLS_DIR)
import clearwright_agent_event as cwae  # noqa: E402
import server  # noqa: E402


def read(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


class AgentEventTests(unittest.TestCase):

    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="agent_events_")
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)

    def files(self):
        d = os.path.join(self.root, cwae.AGENT_EVENTS_DIR)
        return sorted(os.listdir(d)) if os.path.isdir(d) else []

    # ------------------------------------------------------------- build/write

    def test_build_event_shape_and_defaults(self):
        ev = cwae.build_event("claude", "Reviewed the harness.")
        self.assertTrue(ev["event_id"].startswith("evt-"))
        self.assertTrue(ev["at"])
        self.assertEqual(ev["actor"], "claude")
        self.assertEqual(ev["role"], "agent")
        self.assertEqual(ev["message"], "Reviewed the harness.")
        self.assertEqual(ev["severity"], "info")
        self.assertIs(ev["simulated"], False)
        self.assertNotIn("packet_id", ev)  # omitted when not provided

    def test_post_writes_valid_event_and_get_returns_it(self):
        # Server path: do_agent_event mirrors POST /api/agent-events.
        res = server.do_agent_event(self.root, {
            "actor": "claude", "role": "orchestrator",
            "message": "Sent through the local adapter, not Chrome.",
        })
        self.assertTrue(res["ok"], res)
        got = cwae.read_events(self.root)  # mirrors GET /api/agent-events
        self.assertEqual(len(got), 1)
        self.assertEqual(got[0]["actor"], "claude")
        self.assertEqual(got[0]["role"], "orchestrator")
        self.assertEqual(got[0]["source"], "local-http")

    def test_events_returned_in_order(self):
        for i in range(3):
            cwae.write_event(self.root, cwae.build_event("a", "msg {}".format(i)))
        msgs = [e["message"] for e in cwae.read_events(self.root)]
        self.assertEqual(msgs, ["msg 0", "msg 1", "msg 2"])

    def test_events_persist_on_disk_under_queue_root(self):
        cwae.write_event(self.root, cwae.build_event("script", "hello"))
        d = os.path.join(self.root, cwae.AGENT_EVENTS_DIR)
        self.assertTrue(os.path.isdir(d))
        self.assertEqual(len(self.files()), 1)
        with open(os.path.join(d, self.files()[0]), encoding="utf-8") as fh:
            self.assertEqual(json.load(fh)["message"], "hello")

    def test_packet_association_and_filter(self):
        cwae.write_event(self.root, cwae.build_event("a", "m1", packet_id="cw-1"))
        cwae.write_event(self.root, cwae.build_event("a", "m2", packet_id="cw-2"))
        cwae.write_event(self.root, cwae.build_event("a", "m3"))
        only1 = cwae.read_events(self.root, packet_id="cw-1")
        self.assertEqual([e["message"] for e in only1], ["m1"])
        self.assertEqual(len(cwae.read_events(self.root)), 3)

    def test_limit_returns_most_recent(self):
        for i in range(5):
            cwae.write_event(self.root, cwae.build_event("a", "m{}".format(i)))
        recent = cwae.read_events(self.root, limit=2)
        self.assertEqual([e["message"] for e in recent], ["m3", "m4"])

    # ------------------------------------------------------------- safe failure

    def test_missing_message_or_actor_fails_safely(self):
        for payload in ({"actor": "claude"}, {"message": "hi"},
                        {"actor": "  ", "message": "hi"},
                        {"actor": "claude", "message": "   "}):
            with self.subTest(payload=payload):
                res = server.do_agent_event(self.root, payload)
                self.assertFalse(res["ok"], res)
        self.assertEqual(self.files(), [], "no event file written on failure")

    def test_build_event_raises_on_missing_fields(self):
        with self.assertRaises(ValueError):
            cwae.build_event("", "msg")
        with self.assertRaises(ValueError):
            cwae.build_event("actor", "")

    # ------------------------------------------------------------- simulated

    def test_simulated_flag_preserved_and_labeled(self):
        cwae.write_event(self.root, cwae.build_event("demo", "seed", simulated=True))
        cwae.write_event(self.root, cwae.build_event("claude", "real"))
        events = cwae.read_events(self.root)
        by_msg = {e["message"]: e for e in events}
        self.assertIs(by_msg["seed"]["simulated"], True)
        self.assertIs(by_msg["real"]["simulated"], False)

    def test_read_empty_store_returns_empty_list(self):
        self.assertEqual(cwae.read_events(self.root), [])

    # ------------------------------------------------- integration surface text

    def test_ui_and_docs_present_local_adapter_not_browser(self):
        html = read(os.path.join(STATIC, "index.html"))
        # The feed distinguishes real local events from simulated demo.
        self.assertIn("Local events", html)
        self.assertIn("Simulated demo", html)
        self.assertIn("/api/agent-events", html)
        # No copy/paste and no browser automation required to drive it.
        self.assertIn("no browser automation is needed", html.lower())

        docs = read(os.path.join(REPO_ROOT, "docs", "CONTROL_PLANE_DEMO.md")).lower()
        self.assertIn("local http", docs)
        self.assertIn("agent event", docs)
        # Browser automation must be presented as visual/auth only, not the
        # integration method.
        self.assertIn("not the integration", docs)

    # --------------------------------------------------------------- naming

    def test_no_retired_naming_in_new_files(self):
        import re
        retired = re.compile("|".join([r"\b" + "w" + "rit" + r"\b", "vol" + "tex"]), re.I)
        for path in (os.path.join(TOOLS_DIR, "clearwright_agent_event.py"),
                     os.path.join(STATIC, "index.html"),
                     os.path.join(STATIC, "app.js"),
                     os.path.abspath(__file__)):
            with self.subTest(file=os.path.basename(path)):
                self.assertIsNone(retired.search(read(path)))


if __name__ == "__main__":
    unittest.main()
