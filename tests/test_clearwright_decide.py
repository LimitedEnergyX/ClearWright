"""Stdlib unittest coverage for tools/clearwright_decide.py.

These tests build temporary queue directories with tempfile and invoke the decide
tool as a subprocess. They never touch any live clearance queue directories or
runtime packet file, and require no network or external services.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DECIDE = os.path.join(REPO_ROOT, "tools", "clearwright_decide.py")

FUTURE = "2999-01-01T00:00:00Z"


def base_packet(status, qdir, fname, **extra):
    packet = {
        "packet_id": "cw-decide-test",
        "packet_type": "docs_change",
        "title": "test packet",
        "requesting_agent": "agent/test",
        "created_at": "2026-07-01T00:00:00Z",
        "updated_at": "2026-07-01T00:00:00Z",
        "status": status,
        "source_path": "{}/{}".format(qdir, fname),
        "packet_hash": "sha256:ORIGINALHASH",
    }
    packet.update(extra)
    return packet


def load(path):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def read_bytes(path):
    with open(path, "rb") as fh:
        return fh.read()


class DecideTests(unittest.TestCase):

    def make_root(self):
        root = tempfile.mkdtemp(prefix="decide_test_")
        self.addCleanup(shutil.rmtree, root, ignore_errors=True)
        orch = os.path.join(root, "orchestrator")
        for qdir in ("clearance_outbox", "clearance_in_progress",
                     "clearance_done", "clearance_failed"):
            os.makedirs(os.path.join(orch, qdir))
        return orch

    def write_packet(self, orch, qdir, fname, status, raw=None, **extra):
        path = os.path.join(orch, qdir, fname)
        with open(path, "w", encoding="utf-8") as fh:
            if raw is not None:
                fh.write(raw)
            else:
                json.dump(base_packet(status, qdir, fname, **extra), fh)
        return path

    def run_tool(self, *args, env=None):
        result = subprocess.run(
            [sys.executable, DECIDE, *args],
            capture_output=True, encoding="utf-8", errors="replace", env=env,
        )
        return result.returncode, (result.stdout or "") + (result.stderr or "")

    # ------------------------------------------------------------------ CTA

    def test_cta_positive_stays_in_outbox(self):
        orch = self.make_root()
        src = self.write_packet(orch, "clearance_outbox", "p.json", "RTA")
        code, out = self.run_tool("cta", src, "--actor", "OPERATOR-0001")
        self.assertEqual(code, 0, out)
        self.assertTrue(os.path.exists(src), "CTA packet stays in clearance_outbox")
        p = load(src)
        self.assertEqual(p["status"], "CTA")
        self.assertEqual(p["source_path"], "clearance_outbox/p.json")
        self.assertEqual(p["cleared_by"], "OPERATOR-0001")
        self.assertTrue(p.get("clearance_expires_at"), "CTA sets a clearance lease")
        self.assertEqual(p["decision_json"]["decision"], "CTA")
        self.assertEqual(p["decision_json"]["decided_by"], "OPERATOR-0001")
        self.assertEqual(p["audit_json"]["events"][-1]["event"], "CTA")
        self.assertEqual(p["packet_hash"], "sha256:ORIGINALHASH",
                         "packet_hash intentionally unchanged")

    def test_cta_from_in_review(self):
        orch = self.make_root()
        src = self.write_packet(orch, "clearance_outbox", "p.json", "IN_REVIEW")
        code, out = self.run_tool("cta", src)
        self.assertEqual(code, 0, out)
        self.assertEqual(load(src)["status"], "CTA")

    def test_cta_default_actor_is_operator_0001(self):
        orch = self.make_root()
        src = self.write_packet(orch, "clearance_outbox", "p.json", "RTA")
        code, out = self.run_tool("cta", src)
        self.assertEqual(code, 0, out)
        self.assertEqual(load(src)["cleared_by"], "OPERATOR-0001")

    def test_cta_result_is_claimable_strict_path(self):
        # A cleared packet must still validate as a CTA in clearance_outbox.
        orch = self.make_root()
        src = self.write_packet(orch, "clearance_outbox", "p.json", "RTA")
        self.run_tool("cta", src)
        validate = os.path.join(REPO_ROOT, "tools", "clearwright_validate.py")
        r = subprocess.run([sys.executable, validate, "--strict-path", src],
                           capture_output=True, encoding="utf-8", errors="replace")
        self.assertEqual(r.returncode, 0, (r.stdout or "") + (r.stderr or ""))

    # ------------------------------------------------------------------ DTA

    def test_dta_positive_moves_to_done(self):
        orch = self.make_root()
        src = self.write_packet(orch, "clearance_outbox", "p.json", "RTA")
        done = os.path.join(orch, "clearance_done", "p.json")
        failed = os.path.join(orch, "clearance_failed", "p.json")
        code, out = self.run_tool("dta", src, "--reason", "out of scope",
                                  "--actor", "OPERATOR-0001")
        self.assertEqual(code, 0, out)
        self.assertFalse(os.path.exists(src), "source removed from outbox")
        self.assertTrue(os.path.exists(done), "DTA archived to clearance_done")
        self.assertFalse(os.path.exists(failed), "DTA never goes to clearance_failed")
        p = load(done)
        self.assertEqual(p["status"], "DTA")
        self.assertEqual(p["source_path"], "clearance_done/p.json")
        self.assertEqual(p["denied_by"], "OPERATOR-0001")
        self.assertEqual(p["decision_json"]["decision"], "DTA")
        self.assertEqual(p["decision_json"]["rationale"], "out of scope")
        self.assertEqual(p["audit_json"]["events"][-1]["event"], "DTA")

    def test_dta_requires_reason(self):
        orch = self.make_root()
        src = self.write_packet(orch, "clearance_outbox", "p.json", "RTA")
        code, _ = self.run_tool("dta", src)  # argparse rejects missing --reason
        self.assertEqual(code, 2)
        self.assertTrue(os.path.exists(src))
        self.assertEqual(load(src)["status"], "RTA")

    def test_dta_refuses_blank_reason(self):
        orch = self.make_root()
        src = self.write_packet(orch, "clearance_outbox", "p.json", "RTA")
        code, out = self.run_tool("dta", src, "--reason", "   ")
        self.assertEqual(code, 1, out)
        self.assertEqual(load(src)["status"], "RTA")

    def test_dta_refuses_destination_overwrite(self):
        orch = self.make_root()
        src = self.write_packet(orch, "clearance_outbox", "p.json", "RTA")
        with open(os.path.join(orch, "clearance_done", "p.json"), "w") as fh:
            fh.write("PREEXISTING")
        code, out = self.run_tool("dta", src, "--reason", "dupe")
        self.assertEqual(code, 1, out)
        self.assertTrue(os.path.exists(src), "source kept when destination exists")

    # ------------------------------------------------------------------ RFI

    def test_rfi_positive_stays_in_outbox(self):
        orch = self.make_root()
        src = self.write_packet(orch, "clearance_outbox", "p.json", "RTA")
        code, out = self.run_tool("rfi", src, "--reason", "which files change?")
        self.assertEqual(code, 0, out)
        self.assertTrue(os.path.exists(src), "RFI packet stays in clearance_outbox")
        p = load(src)
        self.assertEqual(p["status"], "RFI_PENDING")
        self.assertEqual(p["source_path"], "clearance_outbox/p.json")
        self.assertEqual(p["rfi_json"]["question"], "which files change?")
        self.assertEqual(p["audit_json"]["events"][-1]["event"], "RFI_PENDING")

    def test_rfi_requires_reason(self):
        orch = self.make_root()
        src = self.write_packet(orch, "clearance_outbox", "p.json", "RTA")
        code, _ = self.run_tool("rfi", src)
        self.assertEqual(code, 2)

    # ------------------------------------------------------- refusals & scope

    def test_refuses_non_decidable_status(self):
        orch = self.make_root()
        for status in ("CTA", "RFI_PENDING"):
            with self.subTest(status=status):
                extra = {"clearance_expires_at": FUTURE} if status == "CTA" else {}
                src = self.write_packet(orch, "clearance_outbox",
                                        "{}.json".format(status), status, **extra)
                code, out = self.run_tool("cta", src)
                self.assertEqual(code, 1, out)
                self.assertEqual(load(src)["status"], status, "packet unchanged")

    def test_refuses_terminal_in_done(self):
        orch = self.make_root()
        src = self.write_packet(orch, "clearance_done", "p.json", "DONE")
        code, out = self.run_tool("cta", src)
        self.assertEqual(code, 1, out)
        self.assertTrue(os.path.exists(src))

    def test_refuses_packet_not_in_outbox(self):
        orch = self.make_root()
        src = self.write_packet(orch, "clearance_in_progress", "p.json", "IN_PROGRESS",
                                clearance_expires_at=FUTURE)
        code, out = self.run_tool("cta", src)
        self.assertEqual(code, 1, out)

    def test_bad_json_exit_2(self):
        orch = self.make_root()
        src = self.write_packet(orch, "clearance_outbox", "p.json", None, raw="{ nope ")
        code, out = self.run_tool("cta", src)
        self.assertEqual(code, 2, out)

    # ------------------------------------------------------------- dry-run

    def test_cta_dry_run_no_op(self):
        orch = self.make_root()
        src = self.write_packet(orch, "clearance_outbox", "p.json", "RTA")
        before = read_bytes(src)
        code, out = self.run_tool("cta", "--dry-run", src)
        self.assertEqual(code, 0, out)
        self.assertEqual(read_bytes(src), before, "dry-run must not modify the packet")

    def test_dta_dry_run_no_move(self):
        orch = self.make_root()
        src = self.write_packet(orch, "clearance_outbox", "p.json", "RTA")
        code, out = self.run_tool("dta", "--dry-run", src, "--reason", "x")
        self.assertEqual(code, 0, out)
        self.assertTrue(os.path.exists(src), "dry-run must not move the source")
        self.assertFalse(os.path.exists(os.path.join(orch, "clearance_done", "p.json")))


if __name__ == "__main__":
    unittest.main()
