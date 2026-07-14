"""Tests for PR #19: active-run view, tool ergonomics (--repo/--server-url,
no shell cd), and Codex telemetry visibility.

Tool behavior is tested through the importable functions and each tool's --help;
the Active Run data assembly and Codex-telemetry parsing are tested server-side
(pure functions). No real Codex is invoked.
"""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timezone, timedelta

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APP_DIR = os.path.join(REPO_ROOT, "apps", "control-plane")
TOOLS_DIR = os.path.join(REPO_ROOT, "tools")
STATIC = os.path.join(APP_DIR, "static")
DOCS = os.path.join(REPO_ROOT, "docs")
PROOF = os.path.join(TOOLS_DIR, "clearwright_proof.py")
CODEX = os.path.join(TOOLS_DIR, "clearwright_codex_review.py")

sys.path.insert(0, APP_DIR)
sys.path.insert(0, TOOLS_DIR)
import server  # noqa: E402
import clearwright_work as cww  # noqa: E402
import clearwright_message as cwm  # noqa: E402
import clearwright_agent_event as cwae  # noqa: E402
import clearwright_proof as prf  # noqa: E402
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


def help_text(tool):
    return subprocess.run([sys.executable, tool, "--help"],
                          capture_output=True, text=True, encoding="utf-8", errors="replace").stdout


def operator_queue(prefix, tc):
    base = tempfile.mkdtemp(prefix=prefix)
    tc.addCleanup(shutil.rmtree, base, ignore_errors=True)
    root, *_ = server.resolve_queue(base)
    return root


class ProofErgonomicsTests(unittest.TestCase):

    def test_proof_accepts_repo_and_server_url(self):
        text = help_text(PROOF)
        self.assertIn("--repo", text)
        self.assertIn("--server-url", text)

    def test_proof_uses_subprocess_cwd_not_shell_cd(self):
        src = read(PROOF)
        self.assertIn("cwd=repo", src)
        self.assertNotRegex(src, r'"cd ')  # no shell cd usage

    def test_preflight_failure_is_clear_and_writes_nothing(self):
        root = operator_queue("ar_pf_", self)
        res = prf.run_proof(root, "should not post", server_url="http://127.0.0.1:9")
        self.assertFalse(res["ok"])
        self.assertEqual(res["error"], "server preflight failed")
        self.assertIs(res["server"]["alive"], False)
        self.assertEqual(cwm.read_messages(root), [], "nothing written when server is down")

    def test_run_proof_reports_ids_and_repo_clean(self):
        root = operator_queue("ar_run_", self)
        res = prf.run_proof(root, "PR #19 proof unit test.", repo=REPO_ROOT)
        self.assertTrue(res["ok"], res)
        self.assertTrue(res["thread_id"])
        self.assertTrue(res["work_item_id"])
        self.assertIn("repo_clean_before", res)
        self.assertIn("repo_clean_after", res)
        self.assertEqual(res["repo"], REPO_ROOT)


class CodexErgonomicsTests(unittest.TestCase):

    def setUp(self):
        self.root = operator_queue("ar_cx_", self)
        server.do_message(self.root, {"actor": "OPERATOR-0001", "role": "operator",
                                      "message": "Review under CW."})
        self.wid = cww.derive_work_items(self.root)[0]["work_item_id"]
        cww.claim_work_item(self.root, self.wid, "claude")

    def _codex_msgs(self):
        return [m for m in cwm.read_messages(self.root) if m.get("actor") == "codex"]

    def test_codex_accepts_repo(self):
        self.assertIn("--repo", help_text(CODEX))

    def test_codex_runs_from_repo_cwd(self):
        src = read(CODEX)
        self.assertIn("cwd=cwd", src)       # run_codex passes cwd through
        self.assertIn("cwd=args.repo", src)  # main uses --repo

    def test_unavailable_timeout_do_not_post_codex(self):
        r1 = ccr.review(self.root, self.wid, available_fn=lambda: False)
        self.assertEqual(r1["classification"], "unavailable")
        r2 = ccr.review(self.root, self.wid, available_fn=lambda: True,
                        runner=lambda p, t, cwd=None: ("", ccr.build_telemetry("", None, float(t), timed_out=True)))
        self.assertEqual(r2["classification"], "timeout")
        self.assertEqual(self._codex_msgs(), [])

    def test_successful_mock_posts_codex_with_telemetry_footer(self):
        runner = lambda p, t, cwd=None: (
            "A substantive Codex review with plenty of content to pass the check.",
            ccr.build_telemetry("x" * 90, 0, 2.0))
        r = ccr.review(self.root, self.wid, runner=runner, available_fn=lambda: True)
        self.assertEqual(r["classification"], "review")
        self.assertTrue(r["codex_posted"])
        codex = self._codex_msgs()[0]
        self.assertEqual(codex["source"], "codex-cli")
        # The footer is machine-parseable by the server parser.
        tel = server.parse_codex_telemetry(codex["message"])
        self.assertEqual(tel["exit_code"], 0)
        self.assertEqual(tel["classification"], "review")


class TelemetryParserTests(unittest.TestCase):

    def test_parse_full_footer(self):
        text = ("Codex CLI read-only review (codex-cli). Telemetry: exit=0, "
                "elapsed=30.234s, bytes=46955, lines=933, timed_out=false, "
                "classification=review.\n\nbody")
        t = server.parse_codex_telemetry(text)
        self.assertEqual(t["exit_code"], 0)
        self.assertEqual(t["elapsed_seconds"], 30.234)
        self.assertEqual(t["bytes"], 46955)
        self.assertEqual(t["lines"], 933)
        self.assertIs(t["timed_out"], False)
        self.assertEqual(t["classification"], "review")  # trailing period stripped

    def test_parse_no_footer(self):
        self.assertIsNone(server.parse_codex_telemetry("a normal message"))
        self.assertIsNone(server.parse_codex_telemetry(""))


class ActiveRunTests(unittest.TestCase):

    def setUp(self):
        self.root = operator_queue("ar_view_", self)

    def test_empty_active_run(self):
        ar = server.build_active_run(self.root)
        self.assertIsNone(ar["thread_id"])
        self.assertEqual(ar["messages"], [])

    def test_active_run_reports_ids_and_telemetry(self):
        server.do_message(self.root, {"actor": "OPERATOR-0001", "role": "operator",
                                      "message": "Active run.", "packet_id": "cw-harness-301"})
        wid = cww.derive_work_items(self.root)[0]["work_item_id"]
        cww.claim_work_item(self.root, wid, "claude")
        runner = lambda p, t, cwd=None: (
            "A real substantive Codex review body with enough content to pass the classifier.",
            ccr.build_telemetry("x" * 90, 0, 1.1))
        rr = ccr.review(self.root, wid, runner=runner, available_fn=lambda: True)
        self.assertTrue(rr["codex_posted"], rr)
        ar = server.build_active_run(self.root)
        self.assertTrue(ar["thread_id"])
        self.assertEqual(ar["work_item_id"], wid)
        self.assertEqual(ar["packet_id"], "cw-harness-301")
        self.assertGreaterEqual(len(ar["messages"]), 3)
        self.assertIsNotNone(ar["codex_telemetry"])
        self.assertEqual(ar["codex_telemetry"]["exit_code"], 0)

    def test_active_run_picks_most_recent_thread(self):
        server.do_message(self.root, {"actor": "a", "message": "old thread"})
        server.do_message(self.root, {"actor": "b", "message": "new thread"})
        ar = server.build_active_run(self.root)
        # The most recent single message thread is the active one.
        self.assertEqual(ar["messages"][-1]["message"], "new thread")


class UiTests(unittest.TestCase):

    def setUp(self):
        self.html = read(os.path.join(STATIC, "index.html"))
        self.appjs = read(os.path.join(STATIC, "app.js"))

    def test_task_workspace_replaces_active_run_view(self):
        # The Active Run top-level view was folded into the unified Work page:
        # one selected task binds the workspace, driven by /api/task-state.
        self.assertIn('id="center-work"', self.html)
        self.assertIn('id="nav-work"', self.html)
        self.assertIn("function refreshTaskState", self.appjs)
        self.assertIn("/api/task-state", self.appjs)

    def test_active_run_renders_ids_and_telemetry(self):
        self.assertIn("run.thread_id", self.appjs)
        self.assertIn("run.work_item_id", self.appjs)
        self.assertIn("telemetryBadges", self.appjs)

    def test_copy_helpers_exist(self):
        self.assertIn("function copyText", self.appjs)
        self.assertIn("navigator.clipboard", self.appjs)
        self.assertIn("copy-btn", self.appjs)

    def test_queue_grouping_replaces_run_filters(self):
        # Run filters became queue groups: Attention / Active / Recent /
        # Archived, plus the topbar Attention chip acting as a filter.
        self.assertIn("function buildQueueGroups", self.appjs)
        for group in ("attention", "active", "recent", "archived"):
            self.assertIn(group, self.appjs)
        self.assertIn('id="attention-chip"', self.html)

    def test_placeholder_and_comms_and_history_intact(self):
        self.assertIn('placeholder="Send Agents a Message (Shift+Enter for a new line, Ctrl+Enter to send)"', self.html)
        self.assertIn('id="comms"', self.html)         # Local communications intact
        self.assertIn('id="history-view"', self.html)  # History intact
        self.assertIn("state.pulse", self.appjs)       # pulse still server-driven

    def test_operator_hides_sim_and_demo_labels_simulation(self):
        self.assertIn('id="convo-panel"', self.html)
        self.assertIn("simulated agents", self.html)
        self.assertIn("Simulated demo", self.html)

    def test_pulse_is_scoped_to_the_selected_task(self):
        # The pulse inspector reflects only the selected task; activity on
        # other tasks must never animate the stepper or the inspector.
        self.assertIn('id="pulse-inspector"', self.html)
        self.assertIn("activity on another task does not drive this display",
                      self.appjs)


class RegressionTests(unittest.TestCase):

    def test_core_endpoints_and_pulse(self):
        root = operator_queue("ar_reg_", self)
        self.assertTrue(server.do_message(root, {"actor": "a", "message": "m"})["ok"])
        self.assertTrue(server.do_agent_event(root, {"actor": "a", "message": "e"})["ok"])
        wid = cww.derive_work_items(root)[0]["work_item_id"]
        cww.claim_work_item(root, wid, "claude")
        self.assertTrue(cww.progress_work_item(root, wid, "claude", "p")["ok"])
        state = server.build_state(root)
        self.assertIn("pulse", state)

    def test_stale_done_does_not_pulse(self):
        root = operator_queue("ar_stale_", self)
        old = (datetime.now(timezone.utc) - timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
        with open(os.path.join(root, "clearance_done", "cw-old.json"), "w", encoding="utf-8") as fh:
            json.dump({"packet_id": "cw-old", "status": "DONE",
                       "audit_json": {"events": [{"event": "complete", "at": old}]}}, fh)
        self.assertFalse(server.compute_pulse(root)["done"])

    def test_request_to_done_with_results(self):
        root = operator_queue("ar_flow_", self)
        self.assertTrue(server.do_request(root, dict(REQUEST_FIELDS))["ok"])
        fn = [f for f in os.listdir(os.path.join(root, "clearance_outbox")) if f.endswith(".json")][0]
        self.assertTrue(server.do_action(root, "cta", fn)["ok"])
        self.assertTrue(server.do_action(root, "claim", fn)["ok"])
        done = server.do_action(root, "complete", fn, "", {
            "summary": "done", "verification": "ok",
            "changed_files": ["app/status.py"], "findings": "none"})
        self.assertTrue(done["ok"], done)
        path, lane = server.find_packet(root, fn)
        self.assertEqual((lane, server.load_json(path)["status"]), ("clearance_done", "DONE"))


class NamingAndPrivacyTests(unittest.TestCase):

    def test_no_private_target_or_retired_terms(self):
        _wr = "w" + "rit"
        retired = re.compile("|".join([r"\b" + _wr + r"\b", "vol" + "tex"]), re.I)
        private = re.compile("|".join([r"\b" + "pl" + "ex" + r"\b",
                                       "d:" + re.escape("\\") + "dev"]), re.I)
        targets = [PROOF, CODEX, os.path.join(APP_DIR, "server.py"),
                   os.path.join(STATIC, "index.html"), os.path.join(STATIC, "app.js"),
                   os.path.join(STATIC, "style.css"), os.path.join(DOCS, "WORKER_RUNBOOK.md")]
        for path in targets:
            with self.subTest(file=os.path.relpath(path, REPO_ROOT)):
                text = read(path)
                self.assertIsNone(retired.search(text))
                self.assertIsNone(private.search(text))


if __name__ == "__main__":
    unittest.main()
