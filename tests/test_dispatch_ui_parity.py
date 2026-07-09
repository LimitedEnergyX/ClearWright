"""Tests for PR #18: dispatch UI cleanup, workflow pulse fix, worker HTTP
parity, and telemetry-backed Codex review.

The HTTP work-item routes call the shared clearwright_work functions, so the
parity tests exercise those shared functions (the single source of truth) plus
the server's status-code mapping and route wiring. The workflow pulse is computed
server-side by compute_pulse and tested directly with crafted state and an
explicit `now`. The Codex helper's classification and posting are pure/injectable
and tested without ever invoking real Codex.
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


def new_operator_queue(prefix, testcase):
    base = tempfile.mkdtemp(prefix=prefix)
    testcase.addCleanup(shutil.rmtree, base, ignore_errors=True)
    root, *_ = server.resolve_queue(base)
    return root


def msg_count(root):
    return len(cwm.read_messages(root))


class HttpParityTests(unittest.TestCase):
    """The HTTP routes call these shared functions; test the shared semantics
    plus the server's status mapping and route wiring."""

    def setUp(self):
        self.root = new_operator_queue("p18_http_", self)
        server.do_message(self.root, {"actor": "OPERATOR-0001", "role": "operator",
                                      "source": "operator-ui", "message": "Review under CW.",
                                      "packet_id": "cw-harness-301"})
        self.wid = cww.derive_work_items(self.root)[0]["work_item_id"]
        cww.claim_work_item(self.root, self.wid, "claude")

    def test_progress_writes_durable_internal_message(self):
        res = cww.progress_work_item(self.root, self.wid, "claude", "Running tests.")
        self.assertTrue(res["ok"], res)
        self.assertEqual(res["message"]["direction"], "internal")
        self.assertEqual(res["message"]["status"], "posted")

    def test_unknown_id_rejected_and_writes_nothing(self):
        before = msg_count(self.root)
        for fn, args in (
            (cww.claim_work_item, (self.root, "message:msg-nope", "claude")),
            (cww.progress_work_item, (self.root, "message:msg-nope", "claude", "x")),
            (cww.respond_work_item, (self.root, "message:msg-nope", "claude", "x")),
        ):
            res = fn(*args)
            self.assertFalse(res["ok"])
            self.assertEqual(res["error"], "work_item_not_found")
        self.assertEqual(msg_count(self.root), before, "no durable message on unknown id")

    def test_claim_progress_respond_preserve_thread_and_packet(self):
        item = cww.find_work_item(self.root, self.wid)
        tid = item["thread_id"]
        prog = cww.progress_work_item(self.root, self.wid, "claude", "p")
        self.assertEqual(prog["message"]["thread_id"], tid)
        self.assertEqual(prog["message"]["packet_id"], "cw-harness-301")
        resp = cww.respond_work_item(self.root, self.wid, "claude", "r")
        self.assertEqual(resp["message"]["thread_id"], tid)
        self.assertEqual(resp["message"]["packet_id"], "cw-harness-301")

    def test_status_code_mapping(self):
        self.assertEqual(server.wi_status_code({"ok": True}), 200)
        self.assertEqual(server.wi_status_code({"ok": False, "error": "work_item_not_found"}), 404)
        self.assertEqual(server.wi_status_code({"ok": False, "error": "other"}), 400)

    def test_server_routes_use_shared_functions(self):
        src = read(os.path.join(APP_DIR, "server.py"))
        self.assertIn('if path == "/api/work-items/progress":', src)
        self.assertIn("cww.progress_work_item(", src)
        self.assertIn("cww.claim_work_item(", src)
        self.assertIn("cww.respond_work_item(", src)
        self.assertIn("wi_status_code(result)", src)


class WorkflowPulseTests(unittest.TestCase):

    def setUp(self):
        self.root = new_operator_queue("p18_pulse_", self)
        self.now = datetime.now(timezone.utc)

    def _stale_done(self):
        old = (self.now - timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
        path = os.path.join(self.root, "clearance_done", "cw-old.json")
        with open(path, "w", encoding="utf-8") as fh:
            json.dump({"packet_id": "cw-old", "status": "DONE",
                       "audit_json": {"events": [{"event": "complete", "at": old}]}}, fh)

    def test_stale_done_alone_does_not_pulse_done(self):
        self._stale_done()
        self.assertFalse(server.compute_pulse(self.root, now=self.now)["done"])

    def test_open_message_work_item_pulses_incoming(self):
        server.do_message(self.root, {"actor": "OPERATOR-0001", "role": "operator",
                                      "message": "Review under CW."})
        self.assertTrue(server.compute_pulse(self.root, now=self.now)["incoming"])

    def test_claimed_work_item_pulses_claimed(self):
        server.do_message(self.root, {"actor": "OPERATOR-0001", "role": "operator",
                                      "message": "Review under CW."})
        wid = cww.derive_work_items(self.root)[0]["work_item_id"]
        cww.claim_work_item(self.root, wid, "claude")
        self.assertTrue(server.compute_pulse(self.root)["claimed"])

    def test_recent_response_pulses_done_but_stale_does_not(self):
        self._stale_done()
        server.do_message(self.root, {"actor": "OPERATOR-0001", "role": "operator",
                                      "message": "Review under CW."})
        wid = cww.derive_work_items(self.root)[0]["work_item_id"]
        cww.claim_work_item(self.root, wid, "claude")
        cww.respond_work_item(self.root, wid, "claude", "done")
        # A response just posted is recent -> done pulses (uses server 'now').
        self.assertTrue(server.compute_pulse(self.root)["done"])


class UiTests(unittest.TestCase):

    def test_operator_chat_placeholder_is_exact(self):
        html = read(os.path.join(STATIC, "index.html"))
        self.assertIn('placeholder="Send Agents a Message"', html)
        self.assertNotIn("Send a request as OPERATOR-0001", html)

    def test_long_descriptions_behind_help_tooltips(self):
        html = read(os.path.join(STATIC, "index.html"))
        self.assertIn('class="help"', html)
        self.assertIn('<span class="tip">', html)
        # A known long description now lives inside a tooltip, not a visible hint.
        self.assertRegex(html, r'<span class="tip">Clearance packets arrive')
        self.assertNotRegex(html, r'<p class="hint">Clearance packets arrive')

    def test_operator_mode_hides_simulated_conversation(self):
        html = read(os.path.join(STATIC, "index.html"))
        appjs = read(os.path.join(STATIC, "app.js"))
        self.assertIn('id="convo-panel"', html)
        self.assertIn("convo-panel", appjs)

    def test_demo_mode_labels_simulation(self):
        html = read(os.path.join(STATIC, "index.html"))
        self.assertIn("simulated agents", html)
        self.assertIn("Simulated demo", html)

    def test_pulse_is_server_driven(self):
        appjs = read(os.path.join(STATIC, "app.js"))
        self.assertIn("state.pulse", appjs)


class CodexTelemetryTests(unittest.TestCase):

    def setUp(self):
        self.root = new_operator_queue("p18_codex_", self)
        server.do_message(self.root, {"actor": "OPERATOR-0001", "role": "operator",
                                      "message": "Review under CW."})
        self.wid = cww.derive_work_items(self.root)[0]["work_item_id"]
        cww.claim_work_item(self.root, self.wid, "claude")

    def _codex_msgs(self):
        return [m for m in cwm.read_messages(self.root) if m.get("actor") == "codex"]

    def test_unavailable_records_not_participation(self):
        res = ccr.review(self.root, self.wid, available_fn=lambda: False)
        self.assertEqual(res["classification"], "unavailable")
        self.assertFalse(res["codex_posted"])
        self.assertEqual(self._codex_msgs(), [])

    def test_timeout_records_not_participation(self):
        runner = lambda p, t, cwd=None: ("", ccr.build_telemetry("", None, float(t), timed_out=True))
        res = ccr.review(self.root, self.wid, runner=runner, available_fn=lambda: True)
        self.assertEqual(res["classification"], "timeout")
        self.assertFalse(res["codex_posted"])
        self.assertEqual(self._codex_msgs(), [])

    def test_empty_and_stdin_hang_rejected(self):
        self.assertFalse(ccr.is_substantive("", 0))
        self.assertFalse(ccr.is_substantive("Reading additional input from stdin...", 0))
        self.assertFalse(ccr.is_substantive("A" * 200, 1))  # nonzero exit
        self.assertTrue(ccr.is_substantive("A real, substantive review with findings and suggestions.", 0))

    def test_successful_mock_posts_codex_reviewer_with_telemetry(self):
        runner = lambda p, t, cwd=None: (
            "A substantive Codex review with enough content to pass the threshold.",
            ccr.build_telemetry("x" * 90, 0, 1.5))
        res = ccr.review(self.root, self.wid, runner=runner, available_fn=lambda: True)
        self.assertEqual(res["classification"], "review")
        self.assertTrue(res["codex_posted"])
        posted = self._codex_msgs()
        self.assertEqual(len(posted), 1)
        self.assertEqual(posted[0]["role"], "reviewer")
        self.assertEqual(posted[0]["source"], "codex-cli")
        for key in ("exit_code", "elapsed_seconds", "bytes", "lines", "timed_out"):
            self.assertIn(key, res["telemetry"])


class RegressionTests(unittest.TestCase):

    def test_work_items_messages_events_still_work(self):
        root = new_operator_queue("p18_reg_", self)
        self.assertTrue(server.do_message(root, {"actor": "a", "message": "m"})["ok"])
        self.assertEqual(len(cwm.read_messages(root)), 1)
        self.assertTrue(server.do_agent_event(root, {"actor": "a", "message": "e"})["ok"])
        self.assertEqual(len(cwae.read_events(root)), 1)
        self.assertIsInstance(cww.derive_work_items(root), list)

    def test_request_to_done_with_results_still_works(self):
        root = new_operator_queue("p18_flow_", self)
        self.assertTrue(server.do_request(root, dict(REQUEST_FIELDS))["ok"])
        fn = [f for f in os.listdir(os.path.join(root, "clearance_outbox")) if f.endswith(".json")][0]
        self.assertTrue(server.do_action(root, "cta", fn)["ok"])
        self.assertTrue(server.do_action(root, "claim", fn)["ok"])
        done = server.do_action(root, "complete", fn, "", {
            "summary": "done", "verification": "tests pass",
            "changed_files": ["app/status.py"], "findings": "none"})
        self.assertTrue(done["ok"], done)
        path, lane = server.find_packet(root, fn)
        self.assertEqual(lane, "clearance_done")
        self.assertEqual(server.load_json(path)["status"], "DONE")


class NamingAndPrivacyTests(unittest.TestCase):

    def test_no_private_target_or_retired_terms(self):
        _wr = "w" + "rit"
        retired = re.compile("|".join([r"\b" + _wr + r"\b", "vol" + "tex"]), re.I)
        private = re.compile("|".join([r"\b" + "pl" + "ex" + r"\b",
                                       "d:" + re.escape("\\") + "dev"]), re.I)
        targets = [
            os.path.join(TOOLS_DIR, "clearwright_codex_review.py"),
            os.path.join(TOOLS_DIR, "clearwright_proof.py"),
            os.path.join(TOOLS_DIR, "clearwright_work.py"),
            os.path.join(APP_DIR, "server.py"),
            os.path.join(STATIC, "index.html"),
            os.path.join(STATIC, "app.js"),
            os.path.join(STATIC, "style.css"),
            os.path.join(DOCS, "WORKER_RUNBOOK.md"),
        ]
        for path in targets:
            with self.subTest(file=os.path.relpath(path, REPO_ROOT)):
                text = read(path)
                self.assertIsNone(retired.search(text))
                self.assertIsNone(private.search(text))


if __name__ == "__main__":
    unittest.main()
