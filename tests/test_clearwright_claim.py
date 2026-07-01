"""Stdlib unittest coverage for tools/clearwright_claim.py.

These tests build temporary queue directories with tempfile and invoke the
claim tool as a subprocess. They never touch the live clearance queue
directories or any runtime packet file.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CLAIM = os.path.join(REPO_ROOT, "tools", "clearwright_claim.py")
VALIDATE = os.path.join(REPO_ROOT, "tools", "clearwright_validate.py")
LEASE = "2026-06-30T02:00:00Z"


def base_packet(status, qdir, fname, **extra):
    packet = {
        "packet_id": "cw-claim-test",
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


def read_text(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def read_bytes(path):
    with open(path, "rb") as fh:
        return fh.read()


class ClaimToolTests(unittest.TestCase):

    def make_queue(self, status, qdir="clearance_outbox", fname="p.json", raw=None, **extra):
        """Create a temp orchestrator/<qdir> plus clearance_in_progress, write a
        packet, and return (src_path, dest_path). Cleaned up after the test."""
        root = tempfile.mkdtemp(prefix="claim_test_")
        self.addCleanup(shutil.rmtree, root, ignore_errors=True)
        orch = os.path.join(root, "orchestrator")
        os.makedirs(os.path.join(orch, qdir))
        os.makedirs(os.path.join(orch, "clearance_in_progress"), exist_ok=True)
        src = os.path.join(orch, qdir, fname)
        with open(src, "w", encoding="utf-8") as fh:
            if raw is not None:
                fh.write(raw)
            else:
                json.dump(base_packet(status, qdir, fname, **extra), fh)
        dest = os.path.join(orch, "clearance_in_progress", fname)
        return src, dest

    def run_claim(self, *args, env=None):
        result = subprocess.run(
            [sys.executable, CLAIM, *args],
            capture_output=True, encoding="utf-8", errors="replace", env=env,
        )
        return result.returncode, (result.stdout or "") + (result.stderr or "")

    def run_validate(self, *args):
        result = subprocess.run(
            [sys.executable, VALIDATE, *args],
            capture_output=True, encoding="utf-8", errors="replace",
        )
        return result.returncode

    # 1. Positive single-packet claim.
    def test_positive_claim_cta(self):
        src, dest = self.make_queue("CTA", clearance_expires_at=LEASE)
        code, out = self.run_claim(src)
        self.assertEqual(code, 0, out)
        self.assertFalse(os.path.exists(src), "source should be removed")
        self.assertTrue(os.path.exists(dest), "destination should exist")
        moved = load(dest)
        self.assertEqual(moved["status"], "IN_PROGRESS")
        self.assertEqual(moved["source_path"], "clearance_in_progress/p.json")
        self.assertEqual(moved["packet_hash"], "sha256:ORIGINALHASH",
                         "packet_hash is intentionally left unchanged")
        self.assertEqual(self.run_validate("--strict-path", dest), 0,
                         "claimed packet should pass strict-path validation")

    # 2. Dry-run no-op.
    def test_dry_run_no_op(self):
        src, dest = self.make_queue("CTA", clearance_expires_at=LEASE)
        code, out = self.run_claim("--dry-run", src)
        self.assertEqual(code, 0, out)
        self.assertTrue(os.path.exists(src), "dry-run must not move the source")
        self.assertFalse(os.path.exists(dest), "dry-run must not create a destination")

    # 3. Refused statuses, including IN_PROGRESS.
    def test_refused_statuses(self):
        for status in ("DTA", "DONE", "FAILED", "SUPERSEDED", "IN_PROGRESS"):
            with self.subTest(status=status):
                extra = {"clearance_expires_at": LEASE} if status == "IN_PROGRESS" else {}
                src, dest = self.make_queue(status, **extra)
                code, out = self.run_claim(src)
                self.assertEqual(code, 1, "{} should be refused: {}".format(status, out))
                self.assertTrue(os.path.exists(src), "source must remain")
                self.assertFalse(os.path.exists(dest), "no destination on refusal")

    # 4. Not-in-outbox refusal.
    def test_not_in_outbox_refused(self):
        src, _ = self.make_queue("DONE", qdir="clearance_done")
        code, out = self.run_claim(src)
        self.assertEqual(code, 1, out)
        self.assertTrue(os.path.exists(src))

    # 5. Destination-exists no-overwrite.
    def test_destination_exists_no_overwrite(self):
        src, dest = self.make_queue("CTA", clearance_expires_at=LEASE)
        with open(dest, "w", encoding="utf-8") as fh:
            fh.write("PREEXISTING")
        code, out = self.run_claim(src)
        self.assertEqual(code, 1, out)
        self.assertEqual(read_text(dest), "PREEXISTING",
                         "existing destination must not be overwritten")
        self.assertTrue(os.path.exists(src), "source must remain")

    # 6. Bad JSON exits cleanly.
    def test_bad_json_exit_2(self):
        src, dest = self.make_queue("CTA", raw="{ not valid json ")
        code, out = self.run_claim(src)
        self.assertEqual(code, 2, out)
        self.assertTrue(os.path.exists(src))
        self.assertFalse(os.path.exists(dest))

    # 7. Bare RTA without clearance_expires_at is refused.
    def test_rta_without_lease_refused(self):
        src, dest = self.make_queue("RTA")
        code, out = self.run_claim(src)
        self.assertEqual(code, 1, out)
        self.assertTrue(os.path.exists(src))
        self.assertFalse(os.path.exists(dest))

    # 8. Failure path leaves the source byte-identical.
    def test_failure_leaves_source_intact(self):
        src, dest = self.make_queue("CTA", clearance_expires_at=LEASE)
        before = read_bytes(src)
        with open(dest, "w", encoding="utf-8") as fh:
            fh.write("PREEXISTING")
        code, _ = self.run_claim(src)
        self.assertEqual(code, 1)
        self.assertEqual(read_bytes(src), before,
                         "source bytes must be unchanged after a refused claim")

    # 9. Non-string status is handled cleanly (no traceback).
    def test_non_string_status_clean(self):
        src, dest = self.make_queue(["RTA"])  # unhashable status
        code, out = self.run_claim(src)
        self.assertEqual(code, 1, out)
        self.assertNotIn("Traceback", out, "must fail cleanly, not crash")
        self.assertTrue(os.path.exists(src))
        self.assertFalse(os.path.exists(dest))

    # 10. Unicode filename claim (guards the stdout-encoding regression).
    def test_unicode_filename_claim(self):
        src, dest = self.make_queue("CTA", fname="snow☃.json", clearance_expires_at=LEASE)
        env = dict(os.environ, PYTHONIOENCODING="cp1252")
        code, out = self.run_claim(src, env=env)
        self.assertEqual(code, 0, out)
        self.assertFalse(os.path.exists(src), "source should be removed")
        self.assertTrue(os.path.exists(dest))
        self.assertEqual(load(dest)["status"], "IN_PROGRESS")

    # 11. --claimant records metadata and preserves/extends audit history.
    def test_claimant_and_audit(self):
        audit = {"events": [{"at": "2026-06-30T00:00:00Z", "event": "RTA_CREATED",
                             "actor": "agent/test", "note": "created"}]}
        src, dest = self.make_queue("CTA", clearance_expires_at=LEASE, audit_json=audit)
        code, out = self.run_claim("--claimant", "agent/worker-1", src)
        self.assertEqual(code, 0, out)
        moved = load(dest)
        self.assertEqual(moved["claimed_by"], "agent/worker-1")
        self.assertTrue(moved.get("claimed_at"))
        events = moved["audit_json"]["events"]
        self.assertEqual(len(events), 2, "existing history preserved plus one new event")
        self.assertEqual(events[0]["event"], "RTA_CREATED")
        self.assertEqual(events[1]["event"], "IN_PROGRESS")


if __name__ == "__main__":
    unittest.main()
