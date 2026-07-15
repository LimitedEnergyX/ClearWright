"""Tests for the worker command bridge (tools/clearwright_worker.py and the
shared progress_work_item / find_work_item / worker_status helpers, plus
GET /api/worker-status).

The worker bridge is thin orchestration over the existing work-item and message
functions: the same work_item_id format, claim semantics, thread and packet
preservation, and durable message files. These tests cover the shared helpers,
the CLI (next / claim / progress / respond / status) end to end, that the bridge
needs no browser, that the runbook documents the "use CW" behavior and the
GPT/Codex honesty rule, that the existing work-items / messages / agent-events
endpoints and the request -> CTA -> claim -> DONE flow still work, that operator
mode stays real-only while demo mode labels simulation, and that nothing here
names the private demo target or uses retired terms.
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
WORKER_TOOL = os.path.join(TOOLS_DIR, "clearwright_worker.py")

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


class SharedHelperTests(unittest.TestCase):

    def setUp(self):
        self.base = tempfile.mkdtemp(prefix="wb_help_")
        self.addCleanup(shutil.rmtree, self.base, ignore_errors=True)
        self.root, *_ = server.resolve_queue(self.base)  # operator

    def _post_and_wid(self):
        server.do_message(self.root, {"actor": "OPERATOR-0001", "role": "operator", "intent": "request",
                                      "source": "operator-ui", "message": "Review this repo under CW."})
        return cww.derive_work_items(self.root)[0]["work_item_id"]

    def test_find_work_item(self):
        wid = self._post_and_wid()
        self.assertIsNotNone(cww.find_work_item(self.root, wid))
        self.assertIsNone(cww.find_work_item(self.root, "message:msg-nope"))

    def test_progress_writes_internal_message_same_thread(self):
        wid = self._post_and_wid()
        claim = cww.claim_work_item(self.root, wid, "claude")
        res = cww.progress_work_item(self.root, wid, "claude", "Inspecting repository state.")
        self.assertTrue(res["ok"], res)
        self.assertEqual(res["message"]["direction"], "internal")
        self.assertEqual(res["message"]["status"], "posted")
        self.assertEqual(res["thread_id"], claim["thread_id"])
        self.assertEqual(res["message"]["work_item_id"], wid)

    def test_worker_status_reports_counts(self):
        server.do_request(self.root, dict(REQUEST_FIELDS))
        self._post_and_wid()
        st = cww.worker_status(self.root)
        for key in ("work_items_total", "work_items_open", "work_items_claimed",
                    "work_items_by_kind", "packets_by_lane", "messages_total",
                    "agent_events_total"):
            self.assertIn(key, st)
        self.assertGreaterEqual(st["work_items_open"], 1)
        self.assertGreaterEqual(st["messages_total"], 1)

    def test_worker_status_survives_a_bad_message_file(self):
        self._post_and_wid()
        bad = os.path.join(self.root, cwm.COMMS_DIR, "msg-bad.json")
        with open(bad, "w", encoding="utf-8") as fh:
            fh.write("{ not json")
        st = cww.worker_status(self.root)  # must not raise
        self.assertIn("messages_total", st)


class WorkerCliTests(unittest.TestCase):

    def setUp(self):
        self.base = tempfile.mkdtemp(prefix="wb_cli_")
        self.addCleanup(shutil.rmtree, self.base, ignore_errors=True)
        self.root, *_ = server.resolve_queue(self.base)  # operator

    def _run(self, *args):
        return subprocess.run([sys.executable, WORKER_TOOL, *args],
                              capture_output=True, encoding="utf-8", errors="replace")

    def test_next_lists_open_work_and_full_cycle(self):
        server.do_message(self.root, {"actor": "OPERATOR-0001", "role": "operator", "intent": "request",
                                      "source": "operator-ui", "message": "Review this repo under CW."})
        # next
        nxt = self._run("next", self.root, "--actor", "claude")
        self.assertEqual(nxt.returncode, 0, nxt.stderr)
        item = json.loads(nxt.stdout)["work_item"]
        self.assertEqual(item["kind"], "message")
        wid = item["work_item_id"]
        # claim
        claimed = self._run("claim", self.root, "--work-item-id", wid,
                            "--actor", "claude", "--role", "orchestrator")
        self.assertEqual(claimed.returncode, 0, claimed.stderr)
        self.assertTrue(json.loads(claimed.stdout)["ok"])
        # progress
        prog = self._run("progress", self.root, "--work-item-id", wid,
                        "--actor", "claude", "--message", "Inspecting repository state.")
        self.assertEqual(prog.returncode, 0, prog.stderr)
        self.assertEqual(json.loads(prog.stdout)["message"]["direction"], "internal")
        # respond
        resp = self._run("respond", self.root, "--work-item-id", wid,
                        "--actor", "claude", "--message", "Review complete. Findings posted through CW.")
        self.assertEqual(resp.returncode, 0, resp.stderr)
        self.assertEqual(json.loads(resp.stdout)["message"]["status"], "responded")
        # the full thread is durable on disk
        thread = cwm.read_messages(self.root, thread_id=item["thread_id"])
        self.assertEqual([m["status"] for m in thread],
                         ["posted", "claimed", "posted", "responded"])
        self.assertTrue(all(m["source"] in ("operator-ui", "worker-bridge") for m in thread))

    def test_status_command_reports_json(self):
        server.do_message(self.root, {"actor": "OPERATOR-0001", "role": "operator", "intent": "request",
                                      "message": "hi"})
        out = self._run("status", self.root)
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertIn("work_items_total", json.loads(out.stdout))

    def test_next_with_no_work_is_clean(self):
        out = self._run("next", self.root, "--actor", "claude")
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertIsNone(json.loads(out.stdout)["work_item"])

    def test_missing_queue_is_clear_error(self):
        out = self._run("status", os.path.join(self.base, "nope"))
        self.assertEqual(out.returncode, 1)
        self.assertIn("does not exist", out.stderr)

    def test_claim_unknown_work_item_is_clear_error(self):
        out = self._run("claim", self.root, "--work-item-id", "message:msg-nope",
                        "--actor", "claude")
        self.assertEqual(out.returncode, 1)
        self.assertIn("no open work item", out.stderr)

    def test_missing_message_is_argument_error(self):
        out = self._run("progress", self.root, "--work-item-id", "x", "--actor", "claude")
        self.assertEqual(out.returncode, 2)


class NoBrowserTests(unittest.TestCase):

    def test_worker_bridge_needs_no_browser(self):
        # No browser-automation or network dependency is imported; the bridge is
        # orchestration over the local work-item functions only. (The docstring
        # may mention ChromeMCP to say it is NOT used, so match imports here.)
        src = read(WORKER_TOOL)
        for imp in ("import selenium", "import webdriver", "import playwright",
                    "import requests", "from selenium", "webdriver."):
            self.assertNotIn(imp, src)
        self.assertIn("import clearwright_work", src)


class ExistingBehaviorTests(unittest.TestCase):

    def setUp(self):
        self.base = tempfile.mkdtemp(prefix="wb_ex_")
        self.addCleanup(shutil.rmtree, self.base, ignore_errors=True)
        self.root, *_ = server.resolve_queue(self.base)

    def test_work_items_messages_events_still_work(self):
        self.assertTrue(server.do_message(self.root, {"actor": "a", "message": "m"})["ok"])
        self.assertEqual(len(cwm.read_messages(self.root)), 1)
        self.assertTrue(server.do_agent_event(self.root, {"actor": "a", "message": "e"})["ok"])
        self.assertEqual(len(cwae.read_events(self.root)), 1)
        self.assertIsInstance(cww.derive_work_items(self.root), list)

    def test_request_to_done_with_results_still_works(self):
        self.assertTrue(server.do_request(self.root, dict(REQUEST_FIELDS))["ok"])
        fn = [f for f in os.listdir(os.path.join(self.root, "clearance_outbox"))
              if f.endswith(".json")][0]
        self.assertTrue(server.do_action(self.root, "cta", fn)["ok"])
        self.assertTrue(server.do_action(self.root, "claim", fn)["ok"])
        done = server.do_action(self.root, "complete", fn, "", {
            "summary": "done", "verification": "tests pass",
            "changed_files": ["app/status.py"], "findings": "none"})
        self.assertTrue(done["ok"], done)
        path, lane = server.find_packet(self.root, fn)
        self.assertEqual(lane, "clearance_done")
        self.assertEqual(server.load_json(path)["status"], "DONE")


class UiAndDocsTests(unittest.TestCase):

    def test_ui_hints_mention_worker_bridge_and_use_cw(self):
        html = read(os.path.join(STATIC, "index.html"))
        self.assertIn("clearwright_worker.py", html)
        self.assertIn("use CW", html)
        # Operator mode stays real-only; simulation is labeled in demo mode.
        appjs = read(os.path.join(STATIC, "app.js"))
        self.assertIn("convo-panel", appjs)  # simulated conversation hidden in operator mode
        self.assertIn("simulated agents", html)

    def test_runbook_documents_use_cw_behavior(self):
        doc = read(os.path.join(DOCS, "WORKER_RUNBOOK.md"))
        low = doc.lower()
        self.assertIn("use CW", doc)
        self.assertIn("review with CW", doc)
        self.assertIn("clearwright_worker.py", doc)
        # Integration path and honest boundaries.
        self.assertIn("not the integration", low)
        self.assertIn("discord", low)
        self.assertIn("early alpha", low)
        self.assertIn("not intended for production", low)
        self.assertNotIn("production-" + "ready", low)

    def test_runbook_warns_against_faking_model_participation(self):
        low = read(os.path.join(DOCS, "WORKER_RUNBOOK.md")).lower()
        self.assertIn("gpt", low)
        self.assertIn("codex", low)
        self.assertIn("participated unless", low)


class NamingAndPrivacyTests(unittest.TestCase):

    def test_no_private_target_or_retired_terms(self):
        _wr = "w" + "rit"
        retired = re.compile("|".join([r"\b" + _wr + r"\b", "vol" + "tex"]), re.I)
        private = re.compile("|".join([r"\b" + "pl" + "ex" + r"\b",
                                       "d:" + re.escape("\\") + "dev"]), re.I)
        targets = [
            WORKER_TOOL,
            os.path.join(TOOLS_DIR, "clearwright_work.py"),
            os.path.join(APP_DIR, "server.py"),
            os.path.join(STATIC, "index.html"),
            os.path.join(DOCS, "WORKER_RUNBOOK.md"),
        ]
        for path in targets:
            with self.subTest(file=os.path.relpath(path, REPO_ROOT)):
                text = read(path)
                self.assertIsNone(retired.search(text))
                self.assertIsNone(private.search(text))


if __name__ == "__main__":
    unittest.main()
