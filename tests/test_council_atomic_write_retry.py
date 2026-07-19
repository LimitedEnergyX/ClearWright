"""Retry behaviour of clearwright_review_council._atomic_write_json (medglitch #1).

os.replace over a destination a reader holds open WITHOUT FILE_SHARE_DELETE
raises OSError winerror 5/32 on Windows; the writer retries with short
PRE-attempt backoffs. Transient contention -> success; persistent -> raise the
REAL error with the staged tmp cleaned; non-winerror errors re-raise immediately
(so POSIX, which never sets winerror, is unaffected).

os.replace is shared across every module that imports os, so the mock here is
scoped to replaces whose destination is THIS council file; the writer-lock's own
atomic replaces (a different destination) run for real so token acquisition is
untouched.
"""
import os
import shutil
import sys
import tempfile
import unittest
from unittest import mock

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOOLS_DIR = os.path.join(REPO_ROOT, "tools")
sys.path.insert(0, TOOLS_DIR)
import clearwright_review_council as cwrc  # noqa: E402

RETRY_DELAY_VALUES = {d for d in cwrc._REPLACE_RETRY_DELAYS if d}


def _win_oserror(code):
    exc = OSError(code, "simulated sharing violation")
    exc.winerror = code
    return exc


class AtomicWriteRetryTest(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="cw-atomic-retry-")
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        self.path = os.path.join(cwrc.council_dir(self.root, "cw-council-test"),
                                 "round-1.json")
        self._real_replace = os.replace  # captured before any patch

    def _run(self, behaviors):
        """Drive _atomic_write_json with `behaviors` applied IN ORDER to replaces
        of self.path (an OSError instance is raised, anything else runs the real
        replace). Returns (result, exc, target_attempts, retry_sleeps)."""
        seq = list(behaviors)
        state = {"n": 0}
        real = self._real_replace

        def fake_replace(src, dst):
            if os.path.abspath(dst) == os.path.abspath(self.path):
                i = state["n"]
                state["n"] += 1
                action = seq[i] if i < len(seq) else None
                if isinstance(action, BaseException):
                    raise action
            return real(src, dst)

        sleeps = []
        with mock.patch("clearwright_review_council.os.replace",
                        side_effect=fake_replace), \
             mock.patch("clearwright_review_council.time.sleep",
                        side_effect=sleeps.append):
            try:
                res, exc = cwrc._atomic_write_json(self.path, {"ok": True}), None
            except BaseException as e:  # noqa: BLE001 - test inspects the raised error
                res, exc = None, e
        retry_sleeps = [d for d in sleeps if d in RETRY_DELAY_VALUES]
        return res, exc, state["n"], retry_sleeps

    def test_success_first_attempt_no_sleep(self):
        res, exc, attempts, retry_sleeps = self._run([None])
        self.assertIsNone(exc)
        self.assertEqual(res, self.path)
        self.assertEqual(attempts, 1)
        self.assertEqual(retry_sleeps, [])

    def test_transient_then_success(self):
        res, exc, attempts, retry_sleeps = self._run(
            [_win_oserror(32), _win_oserror(5), None])
        self.assertIsNone(exc)
        self.assertEqual(res, self.path)
        self.assertEqual(attempts, 3)
        self.assertEqual(retry_sleeps, [0.05, 0.1])  # pre-attempt sleeps for tries 2,3

    def test_persistent_raises_real_error_and_cleans_tmp(self):
        tmp = self.path + ".tmp"
        res, exc, attempts, retry_sleeps = self._run([_win_oserror(32)] * 6)
        self.assertIsInstance(exc, OSError)
        self.assertEqual(getattr(exc, "winerror", None), 32)  # the REAL error
        self.assertEqual(attempts, 6)                          # exactly six attempts
        self.assertEqual(retry_sleeps, [0.05, 0.1, 0.2, 0.4, 0.8])  # none after final
        self.assertFalse(os.path.exists(tmp))                  # tmp best-effort removed

    def test_non_winerror_reraises_immediately(self):
        # winerror is None (POSIX-style OSError) -> not retryable -> one attempt.
        res, exc, attempts, retry_sleeps = self._run([OSError(13, "permission denied")])
        self.assertIsInstance(exc, OSError)
        self.assertEqual(attempts, 1)
        self.assertEqual(retry_sleeps, [])


if __name__ == "__main__":
    unittest.main()
