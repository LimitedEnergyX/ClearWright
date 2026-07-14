"""Tests for PR #22: archive-aware durable record and the pulse inspector.

Old terminal packets (DONE/DTA/SUPERSEDED past the 24h recent-terminal window)
are flagged archived in /api/state so the console can collapse them - files are
never touched, and History/runs still show everything. The pulse object gains
inspector metadata (active_phase, reason, source ids, expires_at /
seconds_remaining); pulse booleans stay recency-driven so stale completed
packets never look active. Failed packets are never archived and keep health
red.
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

sys.path.insert(0, APP_DIR)
sys.path.insert(0, TOOLS_DIR)
import server  # noqa: E402
import clearwright_work as cww  # noqa: E402
import clearwright_message as cwm  # noqa: E402

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


def write_done_packet(root, packet_id, status, event_at):
    path = os.path.join(root, "clearance_done", packet_id + ".json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump({"packet_id": packet_id, "status": status,
                   "audit_json": {"events": [{"event": "complete", "at": event_at}]}}, fh)
    return path


class ArchiveAwareTests(unittest.TestCase):

    def setUp(self):
        self.root = operator_queue("arc_", self)
        self.now = datetime.now(timezone.utc)
        self.old = (self.now - timedelta(days=2)).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
        self.fresh = self.now.strftime("%Y-%m-%dT%H:%M:%S.%fZ")

    def test_stale_done_still_in_state_and_history(self):
        write_done_packet(self.root, "cw-old", "DONE", self.old)
        state = server.build_state(self.root, mode="operator", durable=True)
        cards = state["lanes"]["clearance_done"]
        self.assertEqual(len(cards), 1)  # still present, never deleted
        hist = server.build_history(self.root)
        self.assertEqual(len(hist["packets"]), 1)

    def test_stale_terminal_is_flagged_archived(self):
        write_done_packet(self.root, "cw-old", "DONE", self.old)
        write_done_packet(self.root, "cw-dta", "DTA", self.old)
        state = server.build_state(self.root, mode="operator", durable=True)
        for card in state["lanes"]["clearance_done"]:
            self.assertTrue(card["archived"], card["packet_id"])

    def test_recent_terminal_is_not_archived(self):
        write_done_packet(self.root, "cw-new", "DONE", self.fresh)
        state = server.build_state(self.root, mode="operator", durable=True)
        self.assertFalse(state["lanes"]["clearance_done"][0]["archived"])

    def test_failed_packets_are_never_archived_and_keep_health_red(self):
        path = os.path.join(self.root, "clearance_failed", "cw-f.json")
        with open(path, "w", encoding="utf-8") as fh:
            json.dump({"packet_id": "cw-f", "status": "FAILED",
                       "audit_json": {"events": [{"event": "fail", "at": self.old}]}}, fh)
        state = server.build_state(self.root, mode="operator", durable=True)
        card = state["lanes"]["clearance_failed"][0]
        self.assertNotIn("archived", card)
        h = server.build_health(self.root, mode="operator", durable=True,
                                codex_check=lambda: True)
        self.assertEqual(h["status"], "red")

    def test_archive_is_ui_flag_only_no_files_touched(self):
        path = write_done_packet(self.root, "cw-old", "DONE", self.old)
        before = os.path.getmtime(path)
        server.build_state(self.root, mode="operator", durable=True)
        self.assertEqual(os.path.getmtime(path), before)
        self.assertTrue(os.path.isfile(path))


class PulseMetadataTests(unittest.TestCase):

    def setUp(self):
        self.root = operator_queue("pm_", self)
        self.now = datetime.now(timezone.utc)
        self.old = (self.now - timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%S.%fZ")

    def test_idle_state_is_clear(self):
        p = server.compute_pulse(self.root)
        self.assertEqual(p["active_phase"], "idle")
        self.assertEqual(p["reason"], "no recent activity")
        self.assertIsNone(p["seconds_remaining"])
        self.assertFalse(any(p[k] for k in ("incoming", "claimed", "verify", "done")))

    def test_stale_done_packet_does_not_pulse_or_set_reason(self):
        write_done_packet(self.root, "cw-old", "DONE", self.old)
        p = server.compute_pulse(self.root)
        self.assertFalse(p["done"])
        self.assertEqual(p["active_phase"], "idle")

    def test_open_item_sets_incoming_phase_with_sources(self):
        server.do_message(self.root, {"actor": "OPERATOR-0001", "role": "operator",
                                      "message": "Please review.", "packet_id": "cw-9"})
        p = server.compute_pulse(self.root)
        self.assertTrue(p["incoming"])
        self.assertEqual(p["active_phase"], "incoming request")
        self.assertIn("open work item", p["reason"])
        self.assertTrue(p["source_thread_id"].startswith("thr-"))
        self.assertTrue(p["source_work_item_id"].startswith("message:"))
        self.assertEqual(p["source_packet_id"], "cw-9")

    def _open_and_claim(self):
        server.do_message(self.root, {"actor": "OPERATOR-0001", "role": "operator",
                                      "message": "Please review."})
        wid = cww.derive_work_items(self.root)[0]["work_item_id"]
        cww.claim_work_item(self.root, wid, "claude")
        return wid

    def test_claim_sets_claimed_phase(self):
        self._open_and_claim()
        p = server.compute_pulse(self.root)
        self.assertTrue(p["claimed"])
        self.assertEqual(p["active_phase"], "claimed work")
        self.assertIn("claimed", p["reason"])

    def test_progress_sets_verification_with_expiry(self):
        wid = self._open_and_claim()
        cww.progress_work_item(self.root, wid, "claude", "Working.")
        p = server.compute_pulse(self.root)
        self.assertTrue(p["verify"])
        self.assertEqual(p["active_phase"], "verification")
        self.assertEqual(p["reason"], "recent internal progress message")
        self.assertIsNotNone(p["expires_at"])
        self.assertTrue(0 <= p["seconds_remaining"] <= server.PULSE_RECENCY_SECONDS)

    def test_reviewer_message_sets_verification(self):
        wid = self._open_and_claim()
        msg = cwm.build_message("codex", "A substantive reviewer note.", role="reviewer",
                                direction="inbound", source="codex-cli", work_item_id=wid)
        cwm.write_message(self.root, msg)
        p = server.compute_pulse(self.root)
        self.assertTrue(p["verify"])
        self.assertEqual(p["active_phase"], "verification")
        self.assertEqual(p["reason"], "recent reviewer message")

    def test_response_sets_done_briefly(self):
        wid = self._open_and_claim()
        cww.respond_work_item(self.root, wid, "claude", "All finished.")
        p = server.compute_pulse(self.root)
        self.assertTrue(p["done"])
        self.assertEqual(p["active_phase"], "done")
        self.assertEqual(p["reason"], "recent final response")
        self.assertIsNotNone(p["expires_at"])

    def test_packet_lifecycle_fallback_phases(self):
        server.do_request(self.root, dict(REQUEST_FIELDS))
        p = server.compute_pulse(self.root)
        self.assertTrue(p["decision"])
        self.assertEqual(p["active_phase"], "operator decision")
        fn = [f for f in os.listdir(os.path.join(self.root, "clearance_outbox"))
              if f.endswith(".json")][0]
        server.do_action(self.root, "cta", fn)
        p = server.compute_pulse(self.root)
        self.assertTrue(p["cta"])
        self.assertEqual(p["active_phase"], "cleared to act")

    def test_metadata_keys_always_present(self):
        p = server.compute_pulse(self.root)
        for key in ("active_phase", "reason", "source_thread_id",
                    "source_work_item_id", "source_packet_id",
                    "expires_at", "seconds_remaining"):
            self.assertIn(key, p)


class UiTests(unittest.TestCase):

    def setUp(self):
        self.html = read(os.path.join(STATIC, "index.html"))
        self.appjs = read(os.path.join(STATIC, "app.js"))
        self.css = read(os.path.join(STATIC, "style.css"))

    def test_pulse_css_longer_and_brighter(self):
        self.assertIn("2.85s", self.css)
        self.assertIn("rgba(45, 212, 191, 0.9)", self.css)
        self.assertIn("30%, 70%", self.css)  # long high-brightness hold
        self.assertIn("prefers-reduced-motion", self.css)

    def test_pulse_inspector_exists(self):
        self.assertIn('id="pulse-inspector"', self.html)
        self.assertIn("function renderPulseInspector", self.appjs)
        self.assertIn("Expires in", self.appjs)
        self.assertIn("active_phase", self.appjs)

    def test_graph_uses_boolean_pulse_keys_only(self):
        self.assertIn("PULSE_NODE_KEYS", self.appjs)
        self.assertIn("state.pulse", self.appjs)

    def test_archive_toggle_and_default_hiding(self):
        self.assertIn("Show completed", self.appjs)
        self.assertIn("archived completed packet", self.appjs)
        self.assertIn("c.archived", self.appjs)  # default filter hides archived

    def test_durable_record_wording_clarified(self):
        self.assertIn("not the active work list", self.html)
        self.assertIn("Work items and Active Run", self.html)

    def test_operator_real_only_and_views_intact(self):
        self.assertIn('id="convo-panel"', self.html)
        self.assertIn("simulated agents", self.html)
        self.assertIn('id="active-run-view"', self.html)
        self.assertIn('id="history-view"', self.html)
        self.assertIn('placeholder="Send Agents a Message (Shift+Enter for a new line, Ctrl+Enter to send)"', self.html)

    def test_health_chip_reason_source(self):
        self.assertIn("chip.title", self.appjs)


class RegressionTests(unittest.TestCase):

    def test_builders_and_flow_still_work(self):
        root = operator_queue("arc_reg_", self)
        server.do_message(root, {"actor": "a", "message": "m"})
        self.assertIn("pulse", server.build_state(root))
        self.assertEqual(len(server.build_runs(root)), 1)
        self.assertIsNotNone(server.build_active_run(root)["thread_id"])
        h = server.build_health(root, mode="operator", durable=True,
                                codex_check=lambda: True)
        self.assertIn(h["status"], ("green", "yellow"))
        self.assertTrue(server.do_request(root, dict(REQUEST_FIELDS))["ok"])
        fn = [f for f in os.listdir(os.path.join(root, "clearance_outbox"))
              if f.endswith(".json")][0]
        self.assertTrue(server.do_action(root, "cta", fn)["ok"])
        self.assertTrue(server.do_action(root, "claim", fn)["ok"])
        done = server.do_action(root, "complete", fn, "", {
            "summary": "done", "verification": "ok",
            "changed_files": ["app/status.py"], "findings": "none"})
        self.assertTrue(done["ok"], done)
        path, lane = server.find_packet(root, fn)
        self.assertEqual((lane, server.load_json(path)["status"]), ("clearance_done", "DONE"))
        # The just-completed packet is recent terminal: visible, not archived.
        state = server.build_state(root, mode="operator", durable=True)
        self.assertFalse(state["lanes"]["clearance_done"][0]["archived"])


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
