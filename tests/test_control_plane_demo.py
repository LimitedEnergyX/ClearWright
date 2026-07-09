"""Tests for the local control plane demo (apps/control-plane).

These drive the demo's core logic (server.py) against a temporary queue seeded
from examples/demo_packets/. Each operator action runs the real ClearWright tools
as subprocesses. Nothing here touches a live queue, the network, or any external
service.

The demo workflow must respect the clearance model: valid statuses, valid queue
lanes, CTA stays in the outbox until a claim, DTA lands in clearance_done,
RFI_PENDING stays in the outbox, and FAILED is never a pre-claim outcome.
"""
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APP_DIR = os.path.join(REPO_ROOT, "apps", "control-plane")
TOOLS_DIR = os.path.join(REPO_ROOT, "tools")
DEMO_PACKETS = os.path.join(REPO_ROOT, "examples", "demo_packets")

sys.path.insert(0, APP_DIR)
sys.path.insert(0, TOOLS_DIR)
import server  # noqa: E402
import clearwright_validate as wpv  # noqa: E402

SEED_1 = "cw-demo-001-healthcheck.json"   # CTA path
SEED_2 = "cw-demo-002-bulk-delete.json"   # DTA path
SEED_3 = "cw-demo-003-auth-config.json"   # RFI path


class ControlPlaneDemoTests(unittest.TestCase):

    def setUp(self):
        self.root = server.make_queue_root()
        server.seed_queue(self.root)
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)

    # helpers ---------------------------------------------------------------

    def lane_status(self, filename):
        path, lane = server.find_packet(self.root, filename)
        if path is None:
            return None, None
        return lane, server.load_json(path).get("status")

    def act(self, action, filename, reason=""):
        return server.do_action(self.root, action, filename, reason)

    def in_lane(self, lane, filename):
        return os.path.isfile(os.path.join(self.root, lane, filename))

    # tests -----------------------------------------------------------------

    def test_demo_packets_validate(self):
        validate = os.path.join(TOOLS_DIR, "clearwright_validate.py")
        for name in sorted(os.listdir(DEMO_PACKETS)):
            if not name.endswith(".json"):
                continue
            with self.subTest(packet=name):
                r = subprocess.run(
                    [sys.executable, validate, os.path.join(DEMO_PACKETS, name)],
                    capture_output=True, encoding="utf-8", errors="replace",
                )
                self.assertEqual(r.returncode, 0, (r.stdout or "") + (r.stderr or ""))

    def test_seed_places_rtas_in_outbox(self):
        state = server.build_state(self.root)
        outbox = {c["filename"]: c["status"] for c in state["lanes"]["clearance_outbox"]}
        self.assertEqual(set(outbox), {SEED_1, SEED_2, SEED_3})
        for status in outbox.values():
            self.assertEqual(status, "RTA")
        for lane in ("clearance_in_progress", "clearance_done", "clearance_failed"):
            self.assertEqual(state["lanes"][lane], [])

    def test_cta_path_stays_in_outbox_until_claim_then_done(self):
        # Grant CTA: packet stays in the outbox as CTA with a bounded lease.
        res = self.act("cta", SEED_1)
        self.assertTrue(res["ok"], res)
        lane, status = self.lane_status(SEED_1)
        self.assertEqual(lane, "clearance_outbox")
        self.assertEqual(status, "CTA")
        path, _ = server.find_packet(self.root, SEED_1)
        self.assertTrue(server.load_json(path).get("clearance_expires_at"),
                        "CTA must carry a bounded clearance lease")

        # Claim: moves to in_progress as IN_PROGRESS.
        res = self.act("claim", SEED_1)
        self.assertTrue(res["ok"], res)
        lane, status = self.lane_status(SEED_1)
        self.assertEqual(lane, "clearance_in_progress")
        self.assertEqual(status, "IN_PROGRESS")

        # Complete: moves to done as DONE.
        res = self.act("complete", SEED_1)
        self.assertTrue(res["ok"], res)
        lane, status = self.lane_status(SEED_1)
        self.assertEqual(lane, "clearance_done")
        self.assertEqual(status, "DONE")

        # Final packet validates, including strict queue-path.
        path, _ = server.find_packet(self.root, SEED_1)
        validate = os.path.join(TOOLS_DIR, "clearwright_validate.py")
        r = subprocess.run(
            [sys.executable, validate, "--strict-path", path],
            capture_output=True, encoding="utf-8", errors="replace",
        )
        self.assertEqual(r.returncode, 0, (r.stdout or "") + (r.stderr or ""))

    def test_claim_refused_before_cta(self):
        # A plain RTA has not been cleared; the demo does not allow claiming it.
        res = self.act("claim", SEED_1)
        self.assertFalse(res["ok"], res)
        lane, status = self.lane_status(SEED_1)
        self.assertEqual((lane, status), ("clearance_outbox", "RTA"))

    def test_dta_lands_in_done_not_failed(self):
        res = self.act("dta", SEED_2, reason="Out of scope and irreversible.")
        self.assertTrue(res["ok"], res)
        lane, status = self.lane_status(SEED_2)
        self.assertEqual(lane, "clearance_done")
        self.assertEqual(status, "DTA")
        self.assertFalse(self.in_lane("clearance_failed", SEED_2),
                         "DTA must never enter clearance_failed")

    def test_rfi_remains_in_outbox(self):
        res = self.act("rfi", SEED_3, reason="Which settings and which environments?")
        self.assertTrue(res["ok"], res)
        lane, status = self.lane_status(SEED_3)
        self.assertEqual(lane, "clearance_outbox")
        self.assertEqual(status, "RFI_PENDING")

    def test_failed_refused_pre_claim(self):
        # FAILED must not be reachable from a pre-claim RTA.
        res = self.act("fail", SEED_1, reason="should not be allowed")
        self.assertFalse(res["ok"], res)
        lane, status = self.lane_status(SEED_1)
        self.assertEqual((lane, status), ("clearance_outbox", "RTA"))
        self.assertFalse(self.in_lane("clearance_failed", SEED_1))

    def test_failed_only_after_claim(self):
        self.assertTrue(self.act("cta", SEED_1)["ok"])
        self.assertTrue(self.act("claim", SEED_1)["ok"])
        res = self.act("fail", SEED_1, reason="Execution broke during the demo run.")
        self.assertTrue(res["ok"], res)
        lane, status = self.lane_status(SEED_1)
        self.assertEqual(lane, "clearance_failed")
        self.assertEqual(status, "FAILED")

    def test_reason_required_for_dta_and_rfi(self):
        self.assertFalse(self.act("dta", SEED_2, reason="")["ok"])
        self.assertFalse(self.act("rfi", SEED_3, reason="   ")["ok"])
        # Unchanged: both still RTA in the outbox.
        self.assertEqual(self.lane_status(SEED_2), ("clearance_outbox", "RTA"))
        self.assertEqual(self.lane_status(SEED_3), ("clearance_outbox", "RTA"))

    def test_all_statuses_and_lanes_valid_after_paths(self):
        # Exercise all three paths, then assert every packet is in a valid lane
        # with a status permitted in that lane.
        self.act("cta", SEED_1)
        self.act("claim", SEED_1)
        self.act("complete", SEED_1)
        self.act("dta", SEED_2, reason="Denied for the demo.")
        self.act("rfi", SEED_3, reason="Needs clarification.")

        for lane in server.LANES:
            lane_dir = os.path.join(self.root, lane)
            for name in os.listdir(lane_dir):
                if not name.endswith(".json"):
                    continue
                status = server.load_json(os.path.join(lane_dir, name)).get("status")
                with self.subTest(lane=lane, packet=name):
                    self.assertIn(status, wpv.ALLOWED_STATUS)
                    self.assertIn(status, wpv.QUEUE_STATUS[lane])

    def test_no_retired_naming_in_new_files(self):
        # Patterns are assembled from fragments so the literal retired tokens
        # never appear in this test source (which CI also scans). Word-boundary
        # matching mirrors the CI naming gate: ordinary words like "written" and
        # the product name itself must not trip the retired-term check.
        import re
        _wr = "w" + "rit"
        _vt = "vol" + "tex"
        retired = re.compile(
            "|".join([
                _wr + " protocol", _wr + "_packet", _wr + "-packet",
                r"\b" + _wr + r"\b",
                _vt,
            ]),
            re.I,
        )
        word_terms = [
            "production-" + "ready", "certified", "compliant",
            "official " + "standard", "open " + "standard",
            "enterprise-" + "grade", "saas", "workflow " + "orchestrator",
        ]
        word_res = [re.compile(r"\b" + re.escape(t) + r"\b", re.I) for t in word_terms]
        substr_terms = ["dispatch_", "dispatch queue"]

        this_file = os.path.abspath(__file__)
        targets = []
        for base in (APP_DIR, DEMO_PACKETS,
                     os.path.join(REPO_ROOT, "examples", "sample_project")):
            for dirpath, _dirs, files in os.walk(base):
                for f in files:
                    targets.append(os.path.join(dirpath, f))
        targets.append(os.path.join(REPO_ROOT, "docs", "CONTROL_PLANE_DEMO.md"))

        for path in targets:
            if os.path.abspath(path) == this_file:
                continue
            if not path.lower().endswith((".py", ".js", ".html", ".css", ".md", ".json", ".txt")):
                continue
            rel = os.path.relpath(path, REPO_ROOT)
            with open(path, encoding="utf-8") as fh:
                text = fh.read()
            lower = text.lower()
            with self.subTest(file=rel, term="retired"):
                self.assertIsNone(retired.search(text), "retired term in {}".format(rel))
            for rx, term in zip(word_res, word_terms):
                with self.subTest(file=rel, term=term):
                    self.assertIsNone(rx.search(text), "'{}' in {}".format(term, rel))
            for term in substr_terms:
                with self.subTest(file=rel, term=term):
                    self.assertNotIn(term, lower)

    # ------------------------------------------------------------- RTA intake

    REQUEST_FIELDS = {
        "title": "Add a status endpoint to the sample web application",
        "packet_type": "code_change",
        "requesting_agent": "agent/worker",
        "requested_action": "Add a read-only status endpoint. Findings only.",
        "target_label": "sample web application",
    }

    def test_request_creates_valid_rta(self):
        res = server.do_request(self.root, dict(self.REQUEST_FIELDS))
        self.assertTrue(res["ok"], res)
        files = [f for f in os.listdir(os.path.join(self.root, "clearance_outbox"))
                 if f.startswith("cw-req-")]
        self.assertEqual(len(files), 1)
        p = server.load_json(os.path.join(self.root, "clearance_outbox", files[0]))
        self.assertEqual(p["status"], "RTA")
        self.assertEqual(p["title"], self.REQUEST_FIELDS["title"])
        self.assertEqual(p["inputs_json"]["target_project"], "sample web application")
        # The new RTA is immediately decidable on the board.
        state = server.build_state(self.root)
        card = [c for c in state["lanes"]["clearance_outbox"]
                if c["filename"] == files[0]][0]
        self.assertEqual(card["allowed_actions"], ["cta", "dta", "rfi"])

    def test_request_missing_required_field_refused(self):
        for missing in ("title", "packet_type", "requesting_agent", "requested_action"):
            with self.subTest(missing=missing):
                fields = dict(self.REQUEST_FIELDS)
                fields[missing] = "   "
                res = server.do_request(self.root, fields)
                self.assertFalse(res["ok"], res)
        extra = [f for f in os.listdir(os.path.join(self.root, "clearance_outbox"))
                 if f.startswith("cw-req-")]
        self.assertEqual(extra, [], "no packet may be created on refusal")

    def test_request_disallowed_label_refused(self):
        fields = dict(self.REQUEST_FIELDS)
        fields["target_label"] = "some private product name"
        res = server.do_request(self.root, fields)
        self.assertFalse(res["ok"], res)
        self.assertIn("generic labels", res["error"])

    def test_intake_metadata_matches_validator_sets(self):
        # The UI is populated from build_state()['intake']; pin it to the
        # validator's allowed sets and the approved generic labels so drift is
        # a conscious test change, never an accident.
        import clearwright_validate as wpv
        intake = server.build_state(self.root)["intake"]
        self.assertEqual(intake["authority_classes"],
                         sorted(wpv.ALLOWED_AUTHORITY_CLASS))
        self.assertEqual(intake["clearance_classes"],
                         sorted(wpv.ALLOWED_CLEARANCE_CLASS))
        self.assertEqual(intake["priority_classes"],
                         sorted(wpv.ALLOWED_PRIORITY_CLASS))
        self.assertEqual(intake["target_labels"], [
            "sample software project",
            "sample web application",
            "demo target project",
            "local test project",
            "private demo target",
        ])

    # ------------------------------------------------------ DONE with results

    def test_full_path_from_intake_to_done_with_results(self):
        res = server.do_request(self.root, dict(self.REQUEST_FIELDS))
        self.assertTrue(res["ok"], res)
        fname = [f for f in os.listdir(os.path.join(self.root, "clearance_outbox"))
                 if f.startswith("cw-req-")][0]

        self.assertTrue(self.act("cta", fname)["ok"])
        self.assertTrue(self.act("claim", fname)["ok"])
        results = {
            "summary": "Added the status endpoint.",
            "verification": "Test command passed.",
            "changed_files": ["app/main.html"],
            "findings": "None beyond the requested change.",
        }
        res = server.do_action(self.root, "complete", fname, "", results)
        self.assertTrue(res["ok"], res)

        p = server.load_json(os.path.join(self.root, "clearance_done", fname))
        self.assertEqual(p["status"], "DONE")
        event = p["audit_json"]["events"][-1]
        self.assertEqual(event["event"], "DONE")
        self.assertEqual(event["results"], results,
                         "results must be ONE nested object on the DONE event")
        self.assertNotIn("results", p)
        self.assertNotIn("results_json", p)

    def test_complete_without_results_still_works(self):
        # Backward compatibility through the server path as well.
        self.assertTrue(self.act("cta", SEED_1)["ok"])
        self.assertTrue(self.act("claim", SEED_1)["ok"])
        res = server.do_action(self.root, "complete", SEED_1)
        self.assertTrue(res["ok"], res)
        event = server.load_json(os.path.join(self.root, "clearance_done", SEED_1))[
            "audit_json"]["events"][-1]
        self.assertNotIn("results", event)


class QueueRootTests(unittest.TestCase):
    """Cover --queue-root / --mode resolution and seeding semantics."""

    LANES = ["clearance_outbox", "clearance_in_progress",
             "clearance_done", "clearance_failed"]

    def _lane_json(self, root, lane):
        d = os.path.join(root, lane)
        return [n for n in os.listdir(d) if n.endswith(".json")] if os.path.isdir(d) else []

    def test_default_temp_queue_is_demo_and_seeded(self):
        root, durable, mode, seeded = server.resolve_queue(None)
        self.addCleanup(shutil.rmtree, root, ignore_errors=True)
        self.assertFalse(durable)
        self.assertEqual(mode, "demo")
        self.assertTrue(seeded)
        for lane in self.LANES:
            self.assertTrue(os.path.isdir(os.path.join(root, lane)))
        self.assertEqual(len(self._lane_json(root, "clearance_outbox")), 3)

    def test_queue_root_defaults_to_operator_and_does_not_seed(self):
        base = tempfile.mkdtemp(prefix="qr_operator_")
        self.addCleanup(shutil.rmtree, base, ignore_errors=True)
        target = os.path.join(base, "active")  # does not exist yet
        root, durable, mode, seeded = server.resolve_queue(target)
        self.assertTrue(durable)
        self.assertEqual(mode, "operator")
        self.assertFalse(seeded)
        for lane in self.LANES:
            self.assertTrue(os.path.isdir(os.path.join(root, lane)))
        # Operator mode never seeds: a fresh operator queue is empty.
        self.assertEqual(self._lane_json(root, "clearance_outbox"), [])

    def test_demo_mode_seeds_empty_durable_queue(self):
        base = tempfile.mkdtemp(prefix="qr_demo_")
        self.addCleanup(shutil.rmtree, base, ignore_errors=True)
        root, durable, mode, seeded = server.resolve_queue(base, "demo")
        self.assertTrue(durable)
        self.assertEqual(mode, "demo")
        self.assertTrue(seeded)
        self.assertEqual(len(self._lane_json(root, "clearance_outbox")), 3)

    def test_nonempty_queue_is_never_seeded_or_overwritten(self):
        base = tempfile.mkdtemp(prefix="qr_nonempty_")
        self.addCleanup(shutil.rmtree, base, ignore_errors=True)
        for lane in self.LANES:
            os.makedirs(os.path.join(base, lane))
        existing = os.path.join(base, "clearance_outbox", "cw-existing-1.json")
        with open(existing, "w", encoding="utf-8") as fh:
            fh.write('{"packet_id": "cw-existing-1", "status": "RTA"}')
        before = open(existing, encoding="utf-8").read()
        # Even demo mode leaves a non-empty queue untouched.
        root, durable, mode, seeded = server.resolve_queue(base, "demo")
        self.assertFalse(seeded)
        self.assertEqual(self._lane_json(root, "clearance_outbox"), ["cw-existing-1.json"])
        self.assertEqual(open(existing, encoding="utf-8").read(), before)

    def test_ensure_lanes_is_nondestructive(self):
        base = tempfile.mkdtemp(prefix="qr_lanes_")
        self.addCleanup(shutil.rmtree, base, ignore_errors=True)
        os.makedirs(os.path.join(base, "clearance_outbox"))
        keep = os.path.join(base, "clearance_outbox", "keep.json")
        with open(keep, "w") as fh:
            fh.write("{}")
        server.ensure_lanes(base)
        for lane in self.LANES:
            self.assertTrue(os.path.isdir(os.path.join(base, lane)))
        self.assertTrue(os.path.isfile(keep))  # not disturbed

    def test_reset_refused_in_operator_mode(self):
        base = tempfile.mkdtemp(prefix="qr_reset_")
        self.addCleanup(shutil.rmtree, base, ignore_errors=True)
        root, durable, mode, seeded = server.resolve_queue(base)  # operator
        gov = os.path.join(root, "clearance_in_progress", "cw-gov-1.json")
        with open(gov, "w") as fh:
            fh.write('{"packet_id":"cw-gov-1","status":"IN_PROGRESS"}')
        res = server.do_reset(root, mode)
        self.assertFalse(res["ok"])
        self.assertIn("operator", res["error"])
        self.assertTrue(os.path.isfile(gov), "operator work must survive")

    def test_reset_allowed_in_demo_mode(self):
        root, durable, mode, seeded = server.resolve_queue(None)  # demo temp
        self.addCleanup(shutil.rmtree, root, ignore_errors=True)
        self.assertTrue(server.do_reset(root, mode)["ok"])


if __name__ == "__main__":
    unittest.main()
