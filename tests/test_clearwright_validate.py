"""Stdlib unittest coverage for tools/clearwright_validate.py.

These tests invoke the validator as a subprocess against temporary packet files
and the committed example. They never touch any live clearance queue directories
or runtime packet file, and require no network or external services.
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VALIDATE = os.path.join(REPO_ROOT, "tools", "clearwright_validate.py")
EXAMPLE = os.path.join(REPO_ROOT, "schema", "examples", "clearance_packet.example.json")


def valid_packet():
    return {
        "packet_id": "cw-validate-test",
        "packet_type": "docs_change",
        "title": "test clearance packet",
        "requesting_agent": "agent/test",
        "created_at": "2026-07-01T00:00:00Z",
        "updated_at": "2026-07-01T00:00:00Z",
        "status": "RTA",
        "source_path": "clearance_outbox/cw-validate-test.json",
        "packet_hash": "sha256:PLACEHOLDER",
    }


class ValidateToolTests(unittest.TestCase):

    def run_validate(self, *args):
        r = subprocess.run(
            [sys.executable, VALIDATE, *args],
            capture_output=True, encoding="utf-8", errors="replace",
        )
        return r.returncode, (r.stdout or "") + (r.stderr or "")

    def write_tmp(self, obj_or_raw):
        fd, path = tempfile.mkstemp(suffix=".json", prefix="cw_validate_")
        os.close(fd)
        self.addCleanup(os.remove, path)
        with open(path, "w", encoding="utf-8") as fh:
            if isinstance(obj_or_raw, str):
                fh.write(obj_or_raw)
            else:
                json.dump(obj_or_raw, fh)
        return path

    def test_committed_example_is_valid(self):
        code, out = self.run_validate(EXAMPLE)
        self.assertEqual(code, 0, out)

    def test_minimal_valid_packet(self):
        code, out = self.run_validate(self.write_tmp(valid_packet()))
        self.assertEqual(code, 0, out)

    def test_missing_required_field_is_invalid(self):
        p = valid_packet()
        del p["packet_id"]
        code, out = self.run_validate(self.write_tmp(p))
        self.assertEqual(code, 1, out)

    def test_unknown_status_is_invalid(self):
        p = valid_packet()
        p["status"] = "NOT_A_STATUS"
        code, out = self.run_validate(self.write_tmp(p))
        self.assertEqual(code, 1, out)

    def test_bad_json_exits_2(self):
        code, out = self.run_validate(self.write_tmp("{ not valid json "))
        self.assertEqual(code, 2, out)

    def test_missing_file_exits_2(self):
        code, _ = self.run_validate(os.path.join(REPO_ROOT, "no_such_packet.json"))
        self.assertEqual(code, 2)


if __name__ == "__main__":
    unittest.main()
