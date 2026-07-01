"""Stdlib unittest coverage for tools/clearwright_lifecycle.py.

These tests build temporary queue directories with tempfile and invoke the
lifecycle tool as a subprocess. They never touch the live clearance queue
directories or any runtime packet file, require no network, Discord, bot, or
environment configuration.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LIFECYCLE = os.path.join(REPO_ROOT, "tools", "clearwright_lifecycle.py")

# A lease well in the past and one well in the future, so time-based staleness
# is deterministic regardless of when the tests run.
PAST = "2000-01-01T00:00:00Z"
FUTURE = "2999-01-01T00:00:00Z"


def base_packet(status, qdir, fname, **extra):
    packet = {
        "packet_id": "cw-lifecycle-test",
        "packet_type": "docs_change",
        "title": "test packet",
        "requesting_agent": "agent/test",
        "created_at": "2026-06-30T00:00:00Z",
        "updated_at": "2026-06-30T00:00:00Z",
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


class LifecycleTests(unittest.TestCase):

    def make_root(self):
        """Create a temp orchestrator/ with all four queue dirs. Cleaned up."""
        root = tempfile.mkdtemp(prefix="lifecycle_test_")
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
            [sys.executable, LIFECYCLE, *args],
            capture_output=True, encoding="utf-8", errors="replace", env=env,
        )
        self._last_stdout = result.stdout or ""
        return result.returncode, (result.stdout or "") + (result.stderr or "")

    def run_json(self, *args, env=None):
        """Run the tool and parse the JSON summary from stdout only.

        Read-only scans emit their JSON summary on stdout and any per-file parse
        notes on stderr, so JSON is parsed from stdout alone.
        """
        code, _out = self.run_tool(*args, env=env)
        return code, json.loads(self._last_stdout)

    # ------------------------------------------------------------------ inspect

    def test_inspect_valid_in_progress(self):
        orch = self.make_root()
        src = self.write_packet(
            orch, "clearance_in_progress", "p.json", "IN_PROGRESS",
            clearance_expires_at=FUTURE, claim_expires_at=FUTURE,
        )
        code, out = self.run_tool("inspect", src)
        self.assertEqual(code, 0, out)
        self.assertIn("Status: IN_PROGRESS", out)
        self.assertIn("Stale: no", out)

    def test_inspect_stale_in_progress(self):
        orch = self.make_root()
        src = self.write_packet(
            orch, "clearance_in_progress", "p.json", "IN_PROGRESS",
            clearance_expires_at=FUTURE, claim_expires_at=PAST,
        )
        code, out = self.run_tool("inspect", src)
        self.assertEqual(code, 1, out)
        self.assertIn("Stale: yes", out)

    def test_inspect_bad_json_exit_2(self):
        orch = self.make_root()
        src = self.write_packet(orch, "clearance_in_progress", "p.json", None,
                                raw="{ not json ")
        code, out = self.run_tool("inspect", src)
        self.assertEqual(code, 2, out)

    # ----------------------------------------------------------------- complete

    def test_complete_positive(self):
        orch = self.make_root()
        src = self.write_packet(
            orch, "clearance_in_progress", "p.json", "IN_PROGRESS",
            clearance_expires_at=FUTURE,
        )
        dest = os.path.join(orch, "clearance_done", "p.json")
        code, out = self.run_tool("complete", src)
        self.assertEqual(code, 0, out)
        self.assertFalse(os.path.exists(src), "source should be removed")
        self.assertTrue(os.path.exists(dest), "destination should exist")
        moved = load(dest)
        self.assertEqual(moved["status"], "DONE")
        self.assertEqual(moved["source_path"], "clearance_done/p.json")
        self.assertEqual(moved["packet_hash"], "sha256:ORIGINALHASH",
                         "packet_hash is intentionally left unchanged")
        self.assertEqual(moved["audit_json"]["events"][-1]["event"], "DONE")

    def test_complete_dry_run_no_op(self):
        orch = self.make_root()
        src = self.write_packet(
            orch, "clearance_in_progress", "p.json", "IN_PROGRESS",
            clearance_expires_at=FUTURE,
        )
        dest = os.path.join(orch, "clearance_done", "p.json")
        code, out = self.run_tool("complete", "--dry-run", src)
        self.assertEqual(code, 0, out)
        self.assertTrue(os.path.exists(src), "dry-run must not move the source")
        self.assertFalse(os.path.exists(dest), "dry-run must not create a destination")

    def test_complete_refuses_non_in_progress(self):
        # A DONE packet placed (wrongly) in clearance_in_progress is refused, and
        # more importantly a DTA/DONE/SUPERSEDED is never routed anywhere by this
        # tool. Here we also assert status-in-dir mismatch is refused.
        orch = self.make_root()
        src = self.write_packet(
            orch, "clearance_in_progress", "p.json", "DONE",
        )
        code, out = self.run_tool("complete", src)
        self.assertEqual(code, 1, out)
        self.assertTrue(os.path.exists(src), "source must remain")

    def test_complete_refuses_outside_in_progress(self):
        orch = self.make_root()
        src = self.write_packet(
            orch, "clearance_outbox", "p.json", "CTA", clearance_expires_at=FUTURE,
        )
        code, out = self.run_tool("complete", src)
        self.assertEqual(code, 1, out)
        self.assertTrue(os.path.exists(src))

    def test_complete_refuses_destination_overwrite(self):
        orch = self.make_root()
        src = self.write_packet(
            orch, "clearance_in_progress", "p.json", "IN_PROGRESS",
            clearance_expires_at=FUTURE,
        )
        dest = os.path.join(orch, "clearance_done", "p.json")
        with open(dest, "w", encoding="utf-8") as fh:
            fh.write("PREEXISTING")
        code, out = self.run_tool("complete", src)
        self.assertEqual(code, 1, out)
        with open(dest, encoding="utf-8") as fh:
            self.assertEqual(fh.read(), "PREEXISTING", "must not overwrite")
        self.assertTrue(os.path.exists(src), "source must remain")

    # --------------------------------------------------------------------- fail

    def test_fail_positive(self):
        orch = self.make_root()
        src = self.write_packet(
            orch, "clearance_in_progress", "p.json", "IN_PROGRESS",
            clearance_expires_at=FUTURE,
        )
        dest = os.path.join(orch, "clearance_failed", "p.json")
        code, out = self.run_tool("fail", src, "--reason", "execution failed")
        self.assertEqual(code, 0, out)
        self.assertFalse(os.path.exists(src))
        self.assertTrue(os.path.exists(dest))
        moved = load(dest)
        self.assertEqual(moved["status"], "FAILED")
        self.assertEqual(moved["source_path"], "clearance_failed/p.json")
        event = moved["audit_json"]["events"][-1]
        self.assertEqual(event["event"], "FAILED")
        self.assertEqual(event["reason"], "execution failed")

    def test_fail_dry_run_no_op(self):
        orch = self.make_root()
        src = self.write_packet(
            orch, "clearance_in_progress", "p.json", "IN_PROGRESS",
            clearance_expires_at=FUTURE,
        )
        dest = os.path.join(orch, "clearance_failed", "p.json")
        code, out = self.run_tool("fail", "--dry-run", src, "--reason", "x")
        self.assertEqual(code, 0, out)
        self.assertTrue(os.path.exists(src))
        self.assertFalse(os.path.exists(dest))

    def test_fail_requires_reason(self):
        orch = self.make_root()
        src = self.write_packet(
            orch, "clearance_in_progress", "p.json", "IN_PROGRESS",
            clearance_expires_at=FUTURE,
        )
        # Missing --reason: argparse rejects with exit code 2.
        code, _ = self.run_tool("fail", src)
        self.assertEqual(code, 2)
        self.assertTrue(os.path.exists(src))

    def test_fail_refuses_blank_reason(self):
        orch = self.make_root()
        src = self.write_packet(
            orch, "clearance_in_progress", "p.json", "IN_PROGRESS",
            clearance_expires_at=FUTURE,
        )
        dest = os.path.join(orch, "clearance_failed", "p.json")
        code, out = self.run_tool("fail", src, "--reason", "   ")
        self.assertEqual(code, 1, out)
        self.assertTrue(os.path.exists(src))
        self.assertFalse(os.path.exists(dest))

    def test_fail_refuses_non_in_progress(self):
        orch = self.make_root()
        src = self.write_packet(orch, "clearance_in_progress", "p.json", "DONE")
        code, out = self.run_tool("fail", src, "--reason", "nope")
        self.assertEqual(code, 1, out)
        self.assertTrue(os.path.exists(src))

    def test_fail_refuses_destination_overwrite(self):
        orch = self.make_root()
        src = self.write_packet(
            orch, "clearance_in_progress", "p.json", "IN_PROGRESS",
            clearance_expires_at=FUTURE,
        )
        dest = os.path.join(orch, "clearance_failed", "p.json")
        with open(dest, "w", encoding="utf-8") as fh:
            fh.write("PREEXISTING")
        code, out = self.run_tool("fail", src, "--reason", "boom")
        self.assertEqual(code, 1, out)
        with open(dest, encoding="utf-8") as fh:
            self.assertEqual(fh.read(), "PREEXISTING")
        self.assertTrue(os.path.exists(src))

    # ------------------------------------------ doctrine: DTA/SUPERSEDED safety

    def test_dta_never_failed_or_completed(self):
        # A DTA belongs in clearance_done. Even if one is (wrongly) sitting in
        # clearance_in_progress, neither complete nor fail will move it: it is not
        # IN_PROGRESS. The DTA is left exactly where it is.
        orch = self.make_root()
        src = self.write_packet(orch, "clearance_in_progress", "p.json", "DTA")
        before = read_bytes(src)
        for verb, extra in (("complete", []), ("fail", ["--reason", "x"])):
            code, out = self.run_tool(verb, src, *extra)
            self.assertEqual(code, 1, "{}: {}".format(verb, out))
            self.assertTrue(os.path.exists(src))
        self.assertEqual(read_bytes(src), before, "DTA packet must be untouched")
        self.assertFalse(os.path.exists(os.path.join(orch, "clearance_failed", "p.json")))
        self.assertFalse(os.path.exists(os.path.join(orch, "clearance_done", "p.json")))

    def test_superseded_never_failed(self):
        orch = self.make_root()
        src = self.write_packet(orch, "clearance_in_progress", "p.json", "SUPERSEDED")
        code, out = self.run_tool("fail", src, "--reason", "x")
        self.assertEqual(code, 1, out)
        self.assertTrue(os.path.exists(src))
        self.assertFalse(os.path.exists(os.path.join(orch, "clearance_failed", "p.json")))

    # -------------------------------------------------------------------- stale

    def test_stale_detects_expired_claim(self):
        orch = self.make_root()
        self.write_packet(
            orch, "clearance_in_progress", "p.json", "IN_PROGRESS",
            clearance_expires_at=FUTURE, claim_expires_at=PAST,
        )
        d = os.path.join(orch, "clearance_in_progress")
        code, out = self.run_tool("stale", d)
        self.assertEqual(code, 1, out)
        self.assertIn("STALE", out)
        self.assertIn("claim_expires_at", out)

    def test_stale_detects_expired_clearance(self):
        orch = self.make_root()
        self.write_packet(
            orch, "clearance_in_progress", "p.json", "IN_PROGRESS",
            clearance_expires_at=PAST,
        )
        d = os.path.join(orch, "clearance_in_progress")
        code, out = self.run_tool("stale", d)
        self.assertEqual(code, 1, out)
        self.assertIn("clearance_expires_at", out)

    def test_stale_ignores_valid_active_lease(self):
        orch = self.make_root()
        self.write_packet(
            orch, "clearance_in_progress", "p.json", "IN_PROGRESS",
            clearance_expires_at=FUTURE, claim_expires_at=FUTURE,
        )
        d = os.path.join(orch, "clearance_in_progress")
        code, out = self.run_tool("stale", d)
        self.assertEqual(code, 0, out)
        self.assertNotIn("STALE", out)

    def test_stale_is_read_only(self):
        orch = self.make_root()
        src = self.write_packet(
            orch, "clearance_in_progress", "p.json", "IN_PROGRESS",
            clearance_expires_at=FUTURE, claim_expires_at=PAST,
        )
        before = read_bytes(src)
        d = os.path.join(orch, "clearance_in_progress")
        self.run_tool("stale", d)
        self.assertTrue(os.path.exists(src))
        self.assertEqual(read_bytes(src), before, "stale scan must not mutate")

    def test_stale_ignores_gitkeep(self):
        orch = self.make_root()
        with open(os.path.join(orch, "clearance_in_progress", ".gitkeep"), "w") as fh:
            fh.write("")
        d = os.path.join(orch, "clearance_in_progress")
        code, out = self.run_tool("stale", d)
        self.assertEqual(code, 0, out)

    def test_stale_not_a_directory_exit_2(self):
        orch = self.make_root()
        code, _ = self.run_tool("stale", os.path.join(orch, "nope"))
        self.assertEqual(code, 2)

    # ------------------------------------------------------------------- status

    def test_status_counts_all_four_dirs(self):
        orch = self.make_root()
        self.write_packet(orch, "clearance_outbox", "a.json", "CTA",
                          clearance_expires_at=FUTURE)
        self.write_packet(orch, "clearance_in_progress", "b.json", "IN_PROGRESS",
                          clearance_expires_at=FUTURE)
        self.write_packet(orch, "clearance_done", "c.json", "DONE")
        self.write_packet(orch, "clearance_done", "d.json", "DTA")
        self.write_packet(orch, "clearance_failed", "e.json", "FAILED")
        code, summary = self.run_json("status", orch, "--json")
        self.assertEqual(code, 0, summary)
        self.assertEqual(summary["counts"]["clearance_outbox"], 1)
        self.assertEqual(summary["counts"]["clearance_in_progress"], 1)
        self.assertEqual(summary["counts"]["clearance_done"], 2)
        self.assertEqual(summary["counts"]["clearance_failed"], 1)
        self.assertEqual(summary["total"], 5)
        self.assertEqual(summary["stale_in_progress"], 0)
        self.assertEqual(summary["invalid_path_status"], 0)

    def test_status_reports_stale_and_invalid(self):
        orch = self.make_root()
        # Stale in-progress packet.
        self.write_packet(orch, "clearance_in_progress", "stale.json", "IN_PROGRESS",
                          clearance_expires_at=PAST)
        # Invalid: status not valid in clearance_done (strict-path mismatch).
        self.write_packet(orch, "clearance_done", "bad.json", "IN_PROGRESS",
                          clearance_expires_at=FUTURE)
        code, summary = self.run_json("status", orch, "--json")
        self.assertEqual(code, 1, summary)
        self.assertEqual(summary["stale_in_progress"], 1)
        self.assertGreaterEqual(summary["invalid_path_status"], 1)

    def test_status_reports_malformed(self):
        orch = self.make_root()
        self.write_packet(orch, "clearance_done", "bad.json", None, raw="{ nope ")
        code, summary = self.run_json("status", orch, "--json")
        self.assertEqual(code, 1, summary)
        self.assertEqual(summary["malformed_json"], 1)

    def test_status_not_a_directory_exit_2(self):
        code, _ = self.run_tool("status", os.path.join(REPO_ROOT, "no_such_root"))
        self.assertEqual(code, 2)

    def test_status_fail_on_stale_scopes_exit(self):
        orch = self.make_root()
        # Only an invalid packet, no stale. --fail-on-stale should exit 0.
        self.write_packet(orch, "clearance_done", "bad.json", "IN_PROGRESS",
                          clearance_expires_at=FUTURE)
        code, out = self.run_tool("status", orch, "--fail-on-stale")
        self.assertEqual(code, 0, out)


if __name__ == "__main__":
    unittest.main()
