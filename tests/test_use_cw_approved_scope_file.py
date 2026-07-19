"""--approved-scope-file resolution in clearwright_use_cw (medglitch #4).

One shared resolver folds --approved-scope-file into args.approved_scope:
mutually exclusive with --approved-scope (EXIT_USAGE); the file is read with
interior newlines preserved verbatim and exactly one terminal newline stripped
so a scope FILE hashes identically to the same text passed inline; any read
failure is EXIT_USAGE.
"""
import contextlib
import hashlib
import io
import os
import sys
import tempfile
import unittest
from argparse import Namespace

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOOLS_DIR = os.path.join(REPO_ROOT, "tools")
sys.path.insert(0, TOOLS_DIR)
import clearwright_use_cw as ucw  # noqa: E402


def _resolve(scope=None, scope_file=None):
    args = Namespace(approved_scope=scope, approved_scope_file=scope_file, json=True)
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        code = ucw._resolve_approved_scope(args)
    return code, args, buf.getvalue()


def _write(tmpdir, name, data):
    path = os.path.join(tmpdir, name)
    with open(path, "w", encoding="utf-8", newline="") as fh:
        fh.write(data)
    return path


class ApprovedScopeFileTest(unittest.TestCase):
    def setUp(self):
        self.d = tempfile.mkdtemp(prefix="cw-scope-file-")
        self.addCleanup(__import__("shutil").rmtree, self.d, ignore_errors=True)

    def test_no_file_is_noop(self):
        code, args, _ = _resolve(scope="inline text")
        self.assertIsNone(code)
        self.assertEqual(args.approved_scope, "inline text")

    def test_sha_parity_with_inline(self):
        text = "Read-only review of the live page and pinned source."
        path = _write(self.d, "scope.txt", text + "\n")  # files normally end in \n
        code, args, _ = _resolve(scope_file=path)
        self.assertIsNone(code)
        self.assertEqual(args.approved_scope, text)
        self.assertEqual(
            hashlib.sha256(args.approved_scope.encode("utf-8")).hexdigest(),
            hashlib.sha256(text.encode("utf-8")).hexdigest())

    def test_both_supplied_exit_usage(self):
        path = _write(self.d, "scope.txt", "x\n")
        code, args, out = _resolve(scope="inline", scope_file=path)
        self.assertEqual(code, ucw.EXIT_USAGE)
        self.assertIn("approved_scope_conflict", out)

    def test_unreadable_exit_usage(self):
        missing = os.path.join(self.d, "nope.txt")
        code, args, out = _resolve(scope_file=missing)
        self.assertEqual(code, ucw.EXIT_USAGE)
        self.assertIn("approved_scope_file_unreadable", out)

    def test_interior_crlf_preserved_terminal_crlf_stripped(self):
        path = _write(self.d, "scope.txt", "a\r\nb\r\n")
        code, args, _ = _resolve(scope_file=path)
        self.assertIsNone(code)
        self.assertEqual(args.approved_scope, "a\r\nb")  # interior kept, one terminal stripped

    def test_terminal_lf_stripped_only_once(self):
        path = _write(self.d, "scope.txt", "a\nb\n\n")
        code, args, _ = _resolve(scope_file=path)
        self.assertIsNone(code)
        self.assertEqual(args.approved_scope, "a\nb\n")  # only ONE terminal newline removed


if __name__ == "__main__":
    unittest.main()
