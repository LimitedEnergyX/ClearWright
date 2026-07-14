"""Tests for PR #21: the read-only system health/readiness endpoint
(GET /api/health via build_health) and the operator health chip/panel.

Health is readiness guidance, not compliance: red = problem, yellow =
attention, green = ready. The Codex check is an injectable capability probe so
these tests are deterministic on any machine and provably never invoke real
Codex. Health must never mutate the queue.
"""
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

sys.path.insert(0, APP_DIR)
sys.path.insert(0, TOOLS_DIR)
import server  # noqa: E402
import clearwright_work as cww  # noqa: E402
import clearwright_message as cwm  # noqa: E402
import clearwright_agent_event as cwae  # noqa: E402

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


def snapshot(root):
    files = {}
    for dirpath, _dirs, names in os.walk(root):
        for n in names:
            p = os.path.join(dirpath, n)
            files[p] = os.path.getmtime(p)
    return files


def health(root, mode="operator", durable=True, codex=True):
    return server.build_health(root, mode=mode, durable=durable,
                               codex_check=lambda: codex)


class HealthEndpointTests(unittest.TestCase):

    def setUp(self):
        base = tempfile.mkdtemp(prefix="hl_")
        self.addCleanup(shutil.rmtree, base, ignore_errors=True)
        self.root, *_ = server.resolve_queue(base)  # operator, empty

    def test_core_fields_present(self):
        h = health(self.root)
        for key in ("ok", "status", "server_time", "mode", "durable", "queue_root",
                    "queue_root_exists", "packet_counts", "message_count",
                    "agent_event_count", "work_items_total", "work_items_open",
                    "work_items_claimed", "run_count", "latest_run_timestamp",
                    "pulse", "capabilities", "warnings", "errors"):
            self.assertIn(key, h)
        self.assertEqual(h["mode"], "operator")
        self.assertTrue(h["durable"])
        self.assertEqual(h["queue_root"], self.root)

    def test_packet_counts_by_lane(self):
        server.do_request(self.root, dict(REQUEST_FIELDS))
        h = health(self.root)
        self.assertEqual(h["packet_counts"]["clearance_outbox"], 1)
        for lane in server.LANES:
            self.assertIn(lane, h["packet_counts"])

    def test_message_and_event_counts(self):
        server.do_message(self.root, {"actor": "a", "message": "m"})
        server.do_agent_event(self.root, {"actor": "a", "message": "e"})
        h = health(self.root)
        self.assertEqual(h["message_count"], 1)
        self.assertEqual(h["agent_event_count"], 1)

    def test_work_item_run_counts_and_latest_timestamp(self):
        server.do_message(self.root, {"actor": "OPERATOR-0001", "role": "operator",
                                      "message": "Open request."})
        h = health(self.root)
        self.assertEqual(h["work_items_open"], 1)
        self.assertEqual(h["work_items_total"], 1)
        self.assertEqual(h["run_count"], 1)
        self.assertIsNotNone(h["latest_run_timestamp"])
        self.assertIsInstance(h["pulse"], dict)
        self.assertIn("done", h["pulse"])

    def test_green_for_normal_operator_state(self):
        h = health(self.root)
        self.assertEqual(h["status"], "green")
        self.assertTrue(h["ok"])
        self.assertEqual(h["warnings"], [])
        self.assertEqual(h["errors"], [])

    def test_yellow_when_open_work_items(self):
        server.do_message(self.root, {"actor": "OPERATOR-0001", "role": "operator",
                                      "message": "Open request."})
        h = health(self.root)
        self.assertEqual(h["status"], "yellow")
        self.assertTrue(h["ok"])
        self.assertTrue(any("open work item" in w for w in h["warnings"]))

    def test_red_when_failed_packets_exist(self):
        with open(os.path.join(self.root, "clearance_failed", "cw-f.json"),
                  "w", encoding="utf-8") as fh:
            json.dump({"packet_id": "cw-f", "status": "FAILED"}, fh)
        h = health(self.root)
        self.assertEqual(h["status"], "red")
        self.assertFalse(h["ok"])
        self.assertTrue(any("failed packet" in e for e in h["errors"]))

    def test_red_when_lane_or_root_missing(self):
        shutil.rmtree(os.path.join(self.root, "clearance_done"))
        self.assertEqual(health(self.root)["status"], "red")
        self.assertEqual(health(os.path.join(self.root, "nope"))["status"], "red")

    def test_codex_unavailable_is_capability_warning_not_participation(self):
        h = health(self.root, codex=False)
        self.assertEqual(h["status"], "yellow")
        self.assertIs(h["capabilities"]["codex_cli_on_path"], False)
        warning = next(w for w in h["warnings"] if "Codex CLI" in w)
        self.assertIn("not participation", warning)

    def test_demo_mode_is_a_warning(self):
        h = health(self.root, mode="demo", durable=False)
        self.assertEqual(h["status"], "yellow")
        self.assertTrue(any("demo mode" in w for w in h["warnings"]))

    def test_health_never_invokes_codex_or_subprocess(self):
        # The builder and its default probe reference no subprocess machinery;
        # the probe is a PATH lookup only.
        self.assertNotIn("subprocess", server.build_health.__code__.co_names)
        self.assertNotIn("subprocess", server._codex_cli_on_path.__code__.co_names)
        self.assertIn("which", server._codex_cli_on_path.__code__.co_names)
        # And the injected check is what gets used.
        calls = []
        server.build_health(self.root, mode="operator", durable=True,
                            codex_check=lambda: calls.append(1) or True)
        self.assertEqual(calls, [1])

    def test_health_does_not_mutate_queue(self):
        server.do_message(self.root, {"actor": "OPERATOR-0001", "role": "operator",
                                      "message": "Open request."})
        server.do_request(self.root, dict(REQUEST_FIELDS))
        before = snapshot(self.root)
        health(self.root)
        self.assertEqual(snapshot(self.root), before)

    def test_endpoint_is_wired(self):
        src = read(os.path.join(APP_DIR, "server.py"))
        self.assertIn('if path == "/api/health":', src)
        self.assertIn("build_health(QUEUE_ROOT)", src)


class UiTests(unittest.TestCase):

    def setUp(self):
        self.html = read(os.path.join(STATIC, "index.html"))
        self.appjs = read(os.path.join(STATIC, "app.js"))
        self.css = read(os.path.join(STATIC, "style.css"))

    def test_health_indicator_exists(self):
        self.assertIn('id="health-chip"', self.html)
        self.assertIn('id="health-panel"', self.html)
        self.assertIn("/api/health", self.appjs)
        self.assertIn("function refreshHealth", self.appjs)

    def test_indicator_exposes_three_states(self):
        for state in ("health-green", "health-yellow", "health-red"):
            self.assertIn(state, self.appjs)
            self.assertIn(state, self.css)
        for label in ("Healthy", "Attention", "Problem"):
            self.assertIn(label, self.appjs)

    def test_details_include_key_facts(self):
        self.assertIn("Queue root", self.appjs)
        self.assertIn("Work items", self.appjs)
        self.assertIn("Codex", self.appjs)
        self.assertIn("read-only", self.html)
        self.assertIn("never proof of participation", self.html)

    def test_operator_stays_real_only(self):
        self.assertIn('id="convo-panel"', self.html)  # simulated convo demo-only
        self.assertIn("simulated agents", self.html)
        self.assertIn('placeholder="Send Agents a Message (Shift+Enter for a new line, Ctrl+Enter to send)"', self.html)


class RegressionTests(unittest.TestCase):

    def setUp(self):
        base = tempfile.mkdtemp(prefix="hl_reg_")
        self.addCleanup(shutil.rmtree, base, ignore_errors=True)
        self.root, *_ = server.resolve_queue(base)

    def test_core_builders_still_work(self):
        server.do_message(self.root, {"actor": "a", "message": "m"})
        server.do_agent_event(self.root, {"actor": "a", "message": "e"})
        self.assertIn("pulse", server.build_state(self.root))
        self.assertEqual(len(server.build_runs(self.root)), 1)
        self.assertIsNotNone(server.build_active_run(self.root)["thread_id"])
        self.assertIsInstance(cww.derive_work_items(self.root), list)
        self.assertEqual(len(cwm.read_messages(self.root)), 1)
        self.assertEqual(len(cwae.read_events(self.root)), 1)

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
