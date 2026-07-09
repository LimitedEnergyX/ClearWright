"""Tests for operator mode vs demo mode in the local control plane.

Operator mode is the live local operator console. It is the default whenever a
durable --queue-root is given: it never seeds demo packets, disables Reset demo,
does not surface the demo mission, and treats real local agent events as the
primary feed. Demo mode is the walkthrough: the default temporary queue, or an
explicit --mode demo, may seed the demo packets and offers Reset demo.

These tests pin the mode resolution, the seeding and reset gating, the metadata
exposed on /api/state, that real local agent events are visible in operator
mode (and simulated ones stay flagged), that the governed clearance flow still
runs end to end on an operator durable queue, that the UI presents a mode badge
and the operator empty-states while keeping the simulated feed clearly labeled,
that the operator-mode docs explain the modes honestly, and that nothing here
names the private demo target or uses retired terms.
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
import clearwright_agent_event as cwae  # noqa: E402


def read(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def outbox_count(root):
    d = os.path.join(root, "clearance_outbox")
    return len([n for n in os.listdir(d) if n.endswith(".json")]) if os.path.isdir(d) else 0


def outbox_files(root):
    d = os.path.join(root, "clearance_outbox")
    return sorted(n for n in os.listdir(d) if n.endswith(".json")) if os.path.isdir(d) else []


class ModeResolutionTests(unittest.TestCase):
    """--queue-root defaults to operator; no --queue-root defaults to demo;
    an explicit --mode always wins."""

    def _durable(self, prefix="mode_"):
        base = tempfile.mkdtemp(prefix=prefix)
        self.addCleanup(shutil.rmtree, base, ignore_errors=True)
        return base

    def test_queue_root_defaults_to_operator(self):
        root, durable, mode, seeded = server.resolve_queue(self._durable())
        self.assertTrue(durable)
        self.assertEqual(mode, "operator")
        self.assertFalse(seeded)

    def test_no_queue_root_defaults_to_demo(self):
        root, durable, mode, seeded = server.resolve_queue(None)
        self.addCleanup(shutil.rmtree, root, ignore_errors=True)
        self.assertFalse(durable)
        self.assertEqual(mode, "demo")
        self.assertTrue(seeded)

    def test_explicit_demo_mode_on_durable_queue(self):
        root, durable, mode, seeded = server.resolve_queue(self._durable(), "demo")
        self.assertTrue(durable)
        self.assertEqual(mode, "demo")
        self.assertTrue(seeded)

    def test_explicit_operator_mode_on_temp_queue(self):
        root, durable, mode, seeded = server.resolve_queue(None, "operator")
        self.addCleanup(shutil.rmtree, root, ignore_errors=True)
        self.assertFalse(durable)
        self.assertEqual(mode, "operator")
        self.assertFalse(seeded)


class SeedingAndResetTests(unittest.TestCase):
    """Operator mode never seeds demo packets and refuses reset; demo mode
    seeds an empty queue and allows reset."""

    def _durable(self):
        base = tempfile.mkdtemp(prefix="seed_")
        self.addCleanup(shutil.rmtree, base, ignore_errors=True)
        return base

    def test_operator_does_not_seed_empty_durable_queue(self):
        root, _, _, seeded = server.resolve_queue(self._durable())
        self.assertFalse(seeded)
        self.assertEqual(outbox_count(root), 0, "operator mode must start empty")

    def test_demo_seeds_empty_queue(self):
        root, _, _, seeded = server.resolve_queue(self._durable(), "demo")
        self.assertTrue(seeded)
        self.assertEqual(outbox_count(root), 3)

    def test_reset_refused_in_operator_mode(self):
        root, _, mode, _ = server.resolve_queue(self._durable())
        res = server.do_reset(root, mode)
        self.assertFalse(res["ok"])
        self.assertIn("operator", res["error"])

    def test_reset_allowed_in_demo_mode(self):
        root, _, mode, _ = server.resolve_queue(None)
        self.addCleanup(shutil.rmtree, root, ignore_errors=True)
        self.assertTrue(server.do_reset(root, mode)["ok"])


class StateMetadataTests(unittest.TestCase):
    """/api/state (build_state) exposes mode, durable, demo_seeded, and
    queue_root, and only demo mode surfaces the walkthrough mission."""

    def test_operator_state_exposes_metadata_and_hides_mission(self):
        base = tempfile.mkdtemp(prefix="state_op_")
        self.addCleanup(shutil.rmtree, base, ignore_errors=True)
        root, durable, mode, seeded = server.resolve_queue(base)
        state = server.build_state(root, mode=mode, durable=durable, demo_seeded=seeded)
        self.assertEqual(state["mode"], "operator")
        self.assertTrue(state["durable"])
        self.assertFalse(state["demo_seeded"])
        self.assertEqual(state["queue_root"], root)
        self.assertEqual(state["mission"], {}, "operator mode hides the demo mission")

    def test_demo_state_exposes_metadata_and_mission(self):
        root, durable, mode, seeded = server.resolve_queue(None)
        self.addCleanup(shutil.rmtree, root, ignore_errors=True)
        state = server.build_state(root, mode=mode, durable=durable, demo_seeded=seeded)
        self.assertEqual(state["mode"], "demo")
        self.assertFalse(state["durable"])
        self.assertTrue(state["demo_seeded"])
        self.assertEqual(state["mission"], server.read_mission())


class RealAgentEventsTests(unittest.TestCase):
    """Operator mode surfaces real local agent events; simulated events stay
    flagged and distinct."""

    def setUp(self):
        base = tempfile.mkdtemp(prefix="events_")
        self.addCleanup(shutil.rmtree, base, ignore_errors=True)
        self.root, self.durable, self.mode, _ = server.resolve_queue(base)

    def test_real_local_events_visible_in_operator(self):
        res = server.do_agent_event(self.root, {
            "actor": "claude", "role": "orchestrator",
            "message": "Claimed cw-1 through the local adapter, not the browser.",
        })
        self.assertTrue(res["ok"], res)
        events = cwae.read_events(self.root)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["actor"], "claude")
        self.assertIs(events[0]["simulated"], False)

    def test_simulated_events_stay_flagged_distinct_from_real(self):
        cwae.write_event(self.root, cwae.build_event("demo", "seed line", simulated=True))
        server.do_agent_event(self.root, {"actor": "claude", "message": "real line"})
        by_msg = {e["message"]: e for e in cwae.read_events(self.root)}
        self.assertIs(by_msg["seed line"]["simulated"], True)
        self.assertIs(by_msg["real line"]["simulated"], False)


class OperatorGovernedFlowTests(unittest.TestCase):
    """The full request -> CTA -> claim -> DONE-with-results flow still works on
    an operator-mode durable queue that started empty."""

    def setUp(self):
        base = tempfile.mkdtemp(prefix="flow_")
        self.addCleanup(shutil.rmtree, base, ignore_errors=True)
        self.root, _, self.mode, seeded = server.resolve_queue(base)  # operator
        self.assertEqual(self.mode, "operator")
        self.assertFalse(seeded)

    def test_request_to_done_with_results_on_operator_queue(self):
        self.assertEqual(outbox_files(self.root), [], "operator queue starts empty")
        res = server.do_request(self.root, {
            "title": "Add a status endpoint to the sample web application",
            "packet_type": "code_change",
            "requesting_agent": "agent/worker",
            "requested_action": "Add a read-only status endpoint. Findings only.",
            "target_label": "sample web application",
        })
        self.assertTrue(res["ok"], res)
        files = outbox_files(self.root)
        self.assertEqual(len(files), 1)
        fn = files[0]

        self.assertTrue(server.do_action(self.root, "cta", fn)["ok"])
        self.assertTrue(server.do_action(self.root, "claim", fn)["ok"])
        done = server.do_action(self.root, "complete", fn, "", {
            "summary": "Added the read-only status endpoint.",
            "verification": "Ran the sample project tests; all pass.",
            "changed_files": ["app/status.py"],
            "findings": "No issues found.",
        })
        self.assertTrue(done["ok"], done)

        path, lane = server.find_packet(self.root, fn)
        self.assertEqual(lane, "clearance_done")
        self.assertEqual(server.load_json(path)["status"], "DONE")


class UiAndDocsTests(unittest.TestCase):
    """The operator console UI and docs present operator mode honestly."""

    def test_ui_presents_mode_and_operator_empty_states(self):
        html = read(os.path.join(STATIC, "index.html"))
        appjs = read(os.path.join(STATIC, "app.js"))
        css = read(os.path.join(STATIC, "style.css"))

        # A mode badge distinguishes operator vs demo.
        self.assertIn('id="mode-badge"', html)
        self.assertIn("mode-badge", css)

        # The front end reads state.mode and applies the mode.
        self.assertIn("function applyMode", appjs)
        self.assertIn("state.mode", appjs)

        # Operator wording, including the live-local framing.
        self.assertIn("live local operator console", appjs.lower())

        # Operator empty-states, verbatim as specified.
        self.assertIn("No active clearance requests.", appjs)
        self.assertIn(
            "No local agent events yet. Agents and tools can submit events "
            "through the local adapter.", appjs)

        # The simulated feed stays clearly labeled and is a demo-only group.
        self.assertIn("simulated", html.lower())
        self.assertIn('id="feed-sim-group"', html)

    def test_operator_mode_doc_explains_modes_honestly(self):
        doc = read(os.path.join(DOCS, "OPERATOR_MODE.md"))
        low = doc.lower()
        self.assertIn("operator mode", low)
        self.assertIn("demo mode", low)
        self.assertIn("durable", low)
        # Agents and tools drive it over the local adapter, not browser clicks.
        self.assertIn("/api/agent-events", doc)
        self.assertIn("not the integration", low)
        # Honest maturity: early alpha, not for production.
        self.assertIn("early alpha", low)
        self.assertIn("not intended for production", low)
        # Never claims the retired marketing phrase (assembled to avoid scans).
        self.assertNotIn("production-" + "ready", low)


class NamingAndPrivacyTests(unittest.TestCase):
    """Touched files must not name the private demo target or use retired terms."""

    def test_no_private_target_or_retired_terms(self):
        _wr = "w" + "rit"
        retired = re.compile("|".join([r"\b" + _wr + r"\b", "vol" + "tex"]), re.I)
        # The private demo target's product name and its dev path must never leak.
        private = re.compile("|".join([r"\b" + "pl" + "ex" + r"\b",
                                       "d:" + re.escape("\\") + "dev"]), re.I)
        targets = [
            os.path.join(STATIC, "index.html"),
            os.path.join(STATIC, "app.js"),
            os.path.join(STATIC, "style.css"),
            os.path.join(APP_DIR, "server.py"),
            os.path.join(DOCS, "OPERATOR_MODE.md"),
        ]
        for path in targets:
            with self.subTest(file=os.path.relpath(path, REPO_ROOT)):
                text = read(path)
                self.assertIsNone(retired.search(text))
                self.assertIsNone(private.search(text))


if __name__ == "__main__":
    unittest.main()
