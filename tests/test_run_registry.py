"""Tests for PR #20: the run registry (GET /api/runs derived from durable
message threads) and the Active Run selector (GET /api/active-run?thread_id=).

Runs are derived summaries, not a new store: build_runs groups the real
(non-simulated) messages by thread and reports status, actors, sources,
timestamps, counts, and Codex telemetry. No real Codex is invoked here; the
codex/reviewer message is produced through the helper with an injected runner.
"""
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

sys.path.insert(0, APP_DIR)
sys.path.insert(0, TOOLS_DIR)
import server  # noqa: E402
import clearwright_work as cww  # noqa: E402
import clearwright_message as cwm  # noqa: E402
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


class RunRegistryTests(unittest.TestCase):

    def setUp(self):
        base = tempfile.mkdtemp(prefix="rr_")
        self.addCleanup(shutil.rmtree, base, ignore_errors=True)
        self.root, *_ = server.resolve_queue(base)  # operator, empty

        # Run 1: full cycle with a (mocked) Codex review, tied to a packet.
        server.do_message(self.root, {"actor": "OPERATOR-0001", "role": "operator",
                                      "source": "operator-ui",
                                      "message": "Run one: review the repo.",
                                      "packet_id": "cw-1"})
        self.w1 = cww.derive_work_items(self.root)[0]["work_item_id"]
        cww.claim_work_item(self.root, self.w1, "claude")
        rr = ccr.review(self.root, self.w1,
                        runner=lambda p, t, cwd=None: (
                            "A substantive Codex review body with plenty of content to pass.",
                            ccr.build_telemetry("x" * 90, 0, 2.2)),
                        available_fn=lambda: True)
        assert rr["codex_posted"]
        cww.respond_work_item(self.root, self.w1, "claude", "Run one complete.")
        self.t1 = cwm.read_messages(self.root)[0]["thread_id"]

        # Run 2: claimed but not answered.
        server.do_message(self.root, {"actor": "OPERATOR-0001", "role": "operator",
                                      "source": "operator-ui",
                                      "message": "Run two: pending work."})
        w2 = [i for i in cww.derive_work_items(self.root) if i["kind"] == "message"][0]["work_item_id"]
        cww.claim_work_item(self.root, w2, "claude")

        # Run 3: open, untouched.
        server.do_message(self.root, {"actor": "OPERATOR-0001", "role": "operator",
                                      "source": "claude-desktop-relay",
                                      "message": "Run three: untouched."})

    def by_title(self):
        return {r["title"][:9]: r for r in server.build_runs(self.root)}

    def test_runs_returns_all_thread_summaries(self):
        self.assertEqual(len(server.build_runs(self.root)), 3)

    def test_run_fields_present(self):
        run = self.by_title()["Run one: "]
        for key in ("thread_id", "work_item_id", "packet_id", "title",
                    "first_timestamp", "last_timestamp", "message_count",
                    "actors", "sources", "status", "has_codex_review",
                    "codex_telemetry", "latest_message_preview"):
            self.assertIn(key, run)
        self.assertEqual(run["message_count"], 4)
        self.assertEqual(run["packet_id"], "cw-1")
        self.assertEqual(run["work_item_id"], self.w1)
        self.assertIn("claude", run["actors"])
        self.assertIn("OPERATOR-0001", run["actors"])
        self.assertIn("operator-ui", run["sources"])
        self.assertTrue(run["first_timestamp"] <= run["last_timestamp"])

    def test_status_detection(self):
        runs = self.by_title()
        self.assertEqual(runs["Run one: "]["status"], "responded")
        self.assertEqual(runs["Run two: "]["status"], "claimed")
        self.assertEqual(runs["Run three"]["status"], "open")

    def test_codex_flag_and_telemetry(self):
        runs = self.by_title()
        self.assertTrue(runs["Run one: "]["has_codex_review"])
        self.assertEqual(runs["Run one: "]["codex_telemetry"]["exit_code"], 0)
        self.assertFalse(runs["Run three"]["has_codex_review"])
        self.assertIsNone(runs["Run three"]["codex_telemetry"])

    def test_sorted_newest_first_and_filters(self):
        runs = server.build_runs(self.root)
        self.assertEqual(runs[0]["title"][:9], "Run three")
        self.assertEqual(len(server.build_runs(self.root, status="open")), 1)
        self.assertEqual(len(server.build_runs(self.root, has_codex=True)), 1)
        self.assertEqual(len(server.build_runs(self.root, has_codex=False)), 2)
        self.assertEqual(len(server.build_runs(self.root, packet_id="cw-1")), 1)
        self.assertEqual(len(server.build_runs(self.root, actor="codex")), 1)
        self.assertEqual(len(server.build_runs(self.root, source="claude-desktop-relay")), 1)
        self.assertEqual(len(server.build_runs(self.root, limit=2)), 2)

    def test_active_run_selection_by_thread_id(self):
        sel = server.build_active_run(self.root, thread_id=self.t1)
        self.assertEqual(sel["thread_id"], self.t1)
        self.assertEqual(len(sel["messages"]), 4)
        self.assertIsNotNone(sel["codex_telemetry"])

    def test_active_run_default_is_most_recent(self):
        default = server.build_active_run(self.root)
        self.assertEqual(default["messages"][0]["message"], "Run three: untouched.")

    def test_active_run_unknown_thread_is_empty(self):
        ar = server.build_active_run(self.root, thread_id="thr-nope")
        self.assertIsNone(ar["thread_id"])
        self.assertEqual(ar["messages"], [])

    def test_simulated_messages_excluded_from_runs(self):
        cwm.write_message(self.root, cwm.build_message(
            "demo", "simulated line", simulated=True, thread_id="thr-sim-only"))
        tids = [r["thread_id"] for r in server.build_runs(self.root)]
        self.assertNotIn("thr-sim-only", tids)


class UiTests(unittest.TestCase):

    def setUp(self):
        self.html = read(os.path.join(STATIC, "index.html"))
        self.appjs = read(os.path.join(STATIC, "app.js"))

    def test_run_selector_exists(self):
        self.assertIn('id="run-list"', self.html)
        self.assertIn("function renderRunList", self.appjs)
        self.assertIn("/api/runs", self.appjs)
        self.assertIn("selectedRunThread", self.appjs)
        self.assertIn("thread_id=", self.appjs)  # selection loads a specific run

    def test_copy_controls_and_filters_still_present(self):
        self.assertIn("copy-btn", self.appjs)
        self.assertIn("data-copy-summary", self.appjs)
        self.assertIn("runSummaryText", self.appjs)
        self.assertIn('id="run-filter"', self.html)
        self.assertIn('data-filter="active"', self.html)

    def test_history_view_intact(self):
        self.assertIn('id="history-view"', self.html)
        self.assertIn("loadHistory", self.appjs)


class RegressionTests(unittest.TestCase):

    def setUp(self):
        base = tempfile.mkdtemp(prefix="rr_reg_")
        self.addCleanup(shutil.rmtree, base, ignore_errors=True)
        self.root, *_ = server.resolve_queue(base)

    def test_messages_work_items_and_pulse_still_work(self):
        self.assertTrue(server.do_message(self.root, {"actor": "a", "message": "m"})["ok"])
        self.assertEqual(len(cwm.read_messages(self.root)), 1)
        self.assertIsInstance(cww.derive_work_items(self.root), list)
        self.assertIn("pulse", server.build_state(self.root))

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
