"""Stdlib unittest coverage for tools/clearwright_request.py.

These tests build temporary queue directories with tempfile and invoke the
request tool as a subprocess. They never touch any live clearance queue
directories or runtime packet file, and require no network or external services.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REQUEST = os.path.join(REPO_ROOT, "tools", "clearwright_request.py")
VALIDATE = os.path.join(REPO_ROOT, "tools", "clearwright_validate.py")

REQUIRED_ARGS = ["--title", "test request", "--type", "analysis",
                 "--action", "Analyze the sample software project."]


class RequestTests(unittest.TestCase):

    def make_root(self):
        root = tempfile.mkdtemp(prefix="request_test_")
        self.addCleanup(shutil.rmtree, root, ignore_errors=True)
        orch = os.path.join(root, "orchestrator")
        for qdir in ("clearance_outbox", "clearance_in_progress",
                     "clearance_done", "clearance_failed"):
            os.makedirs(os.path.join(orch, qdir))
        return orch

    def run_tool(self, *args):
        result = subprocess.run(
            [sys.executable, REQUEST, *args],
            capture_output=True, encoding="utf-8", errors="replace",
        )
        return result.returncode, (result.stdout or "") + (result.stderr or "")

    def outbox_files(self, orch):
        return sorted(os.listdir(os.path.join(orch, "clearance_outbox")))

    # ------------------------------------------------------------- creation

    def test_creates_valid_rta_in_outbox(self):
        orch = self.make_root()
        code, out = self.run_tool(orch, *REQUIRED_ARGS, "--id", "cw-req-test-001")
        self.assertEqual(code, 0, out)
        path = os.path.join(orch, "clearance_outbox", "cw-req-test-001.json")
        self.assertTrue(os.path.isfile(path))
        with open(path, encoding="utf-8") as fh:
            p = json.load(fh)
        self.assertEqual(p["status"], "RTA")
        self.assertEqual(p["title"], "test request")
        self.assertEqual(p["packet_type"], "analysis")
        self.assertEqual(p["requesting_agent"], "agent/worker")
        self.assertEqual(p["source_path"], "clearance_outbox/cw-req-test-001.json")
        self.assertEqual(p["inputs_json"]["target_project"], "sample software project")
        self.assertEqual(p["audit_json"]["events"][0]["event"], "RTA")
        # The created packet must pass strict-path validation.
        r = subprocess.run([sys.executable, VALIDATE, "--strict-path", path],
                           capture_output=True, encoding="utf-8", errors="replace")
        self.assertEqual(r.returncode, 0, (r.stdout or "") + (r.stderr or ""))

    def test_optional_fields_recorded_when_given(self):
        orch = self.make_root()
        code, out = self.run_tool(
            orch, *REQUIRED_ARGS, "--id", "cw-req-test-002",
            "--scope", "read-only analysis", "--test-command", "python -m unittest",
            "--risk", "Low risk.", "--target-label", "local test project",
            "--authority", "OPERATOR", "--clearance", "HUMAN_REQUIRED",
            "--priority", "HIGH",
        )
        self.assertEqual(code, 0, out)
        with open(os.path.join(orch, "clearance_outbox", "cw-req-test-002.json"),
                  encoding="utf-8") as fh:
            p = json.load(fh)
        self.assertEqual(p["inputs_json"]["allowed_scope"], "read-only analysis")
        self.assertEqual(p["inputs_json"]["target_project"], "local test project")
        self.assertEqual(p["risk_notes"], "Low risk.")
        self.assertEqual(p["authority_class"], "OPERATOR")
        self.assertEqual(p["clearance_class"], "HUMAN_REQUIRED")
        self.assertEqual(p["priority_class"], "HIGH")

    def test_created_rta_is_decidable(self):
        # The intake output must feed the decide tool cleanly.
        orch = self.make_root()
        self.run_tool(orch, *REQUIRED_ARGS, "--id", "cw-req-test-003")
        decide = os.path.join(REPO_ROOT, "tools", "clearwright_decide.py")
        path = os.path.join(orch, "clearance_outbox", "cw-req-test-003.json")
        r = subprocess.run([sys.executable, decide, "cta", path],
                           capture_output=True, encoding="utf-8", errors="replace")
        self.assertEqual(r.returncode, 0, (r.stdout or "") + (r.stderr or ""))
        with open(path, encoding="utf-8") as fh:
            self.assertEqual(json.load(fh)["status"], "CTA")

    # ------------------------------------------------------------- refusals

    def test_missing_required_arg_exits_2_and_writes_nothing(self):
        orch = self.make_root()
        code, _ = self.run_tool(orch, "--type", "analysis",
                                "--action", "x")  # no --title
        self.assertEqual(code, 2)
        self.assertEqual(self.outbox_files(orch), [])

    def test_blank_title_refused(self):
        orch = self.make_root()
        code, out = self.run_tool(orch, "--title", "   ", "--type", "analysis",
                                  "--action", "x")
        self.assertEqual(code, 1, out)
        self.assertEqual(self.outbox_files(orch), [])

    def test_duplicate_packet_id_refused_safely(self):
        orch = self.make_root()
        code1, _ = self.run_tool(orch, *REQUIRED_ARGS, "--id", "cw-req-dup")
        self.assertEqual(code1, 0)
        dup_path = os.path.join(orch, "clearance_outbox", "cw-req-dup.json")
        with open(dup_path, "rb") as fh:
            before = fh.read()
        code2, out2 = self.run_tool(orch, "--title", "another", "--type", "analysis",
                                    "--action", "y", "--id", "cw-req-dup")
        self.assertEqual(code2, 1, out2)
        with open(dup_path, "rb") as fh:
            after = fh.read()
        self.assertEqual(before, after, "original packet must be untouched")

    def test_missing_outbox_dir_refused(self):
        root = tempfile.mkdtemp(prefix="request_noq_")
        self.addCleanup(shutil.rmtree, root, ignore_errors=True)
        code, out = self.run_tool(root, *REQUIRED_ARGS)
        self.assertEqual(code, 1, out)

    def test_invalid_enum_choice_exits_2(self):
        orch = self.make_root()
        code, _ = self.run_tool(orch, *REQUIRED_ARGS, "--authority", "NOT_A_CLASS")
        self.assertEqual(code, 2)
        self.assertEqual(self.outbox_files(orch), [])

    # ------------------------------------------------------------- dry-run

    def test_dry_run_writes_nothing(self):
        orch = self.make_root()
        code, out = self.run_tool(orch, *REQUIRED_ARGS, "--dry-run")
        self.assertEqual(code, 0, out)
        self.assertEqual(self.outbox_files(orch), [])


if __name__ == "__main__":
    unittest.main()
