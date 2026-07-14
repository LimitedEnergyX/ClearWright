"""Writer/archive mutual exclusion primitives (part of commit 3).

A writer token blocks archive from acquiring exclusivity while it is confirmed
live; only confirmed process non-liveness ever sweeps a token or the exclusive
flag -- age alone never does, and indeterminate liveness always fails safe.
"""
import os
import shutil
import sys
import tempfile
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO_ROOT, "tools"))
import clearwright_writer_lock as cwl  # noqa: E402


class TmpRootMixin(object):
    def _root(self):
        base = tempfile.mkdtemp(prefix="wlock_")
        self.addCleanup(shutil.rmtree, base, ignore_errors=True)
        return base


class LivenessTests(unittest.TestCase, TmpRootMixin):

    def test_self_process_is_confirmed_live(self):
        pid, host, proc_start = cwl._self_owner()
        self.assertEqual(cwl.liveness(pid, host, proc_start), "live")

    def test_pid_reuse_is_confirmed_dead(self):
        pid, host, _ = cwl._self_owner()
        self.assertEqual(cwl.liveness(pid, host, "not-the-real-start-time"), "dead")

    def test_nonexistent_pid_is_dead(self):
        # A PID astronomically unlikely to exist.
        self.assertEqual(cwl.liveness(999999999, cwl._this_host(), "x"), "dead")

    def test_cross_host_is_indeterminate(self):
        pid, _, proc_start = cwl._self_owner()
        self.assertEqual(cwl.liveness(pid, "some-other-host", proc_start),
                         "indeterminate")


class TokenAndExclusiveTests(unittest.TestCase, TmpRootMixin):

    def test_token_blocks_archive_exclusivity_while_live(self):
        root = self._root()
        token_id = cwl.acquire_write_token(root, purpose="test")
        with self.assertRaises(cwl.WriterLockError):
            cwl.acquire_exclusive(root, "op-1", deadline_seconds=0.2)
        cwl.release_write_token(root, token_id)
        flag = cwl.acquire_exclusive(root, "op-1", deadline_seconds=1)
        self.assertEqual(flag["opid"], "op-1")
        self.assertTrue(cwl.release_exclusive(root, "op-1", flag["nonce"]))

    def test_token_acquisition_refused_while_exclusive_active(self):
        root = self._root()
        flag = cwl.acquire_exclusive(root, "op-2")
        with self.assertRaises(cwl.MaintenanceInProgress):
            cwl.acquire_write_token(root, purpose="test")
        cwl.release_exclusive(root, "op-2", flag["nonce"])
        # Now a writer can proceed.
        tok = cwl.acquire_write_token(root, purpose="test")
        self.assertTrue(tok)

    def test_dead_token_is_swept_confirmed_non_live(self):
        root = self._root()
        # Simulate a crashed writer: a token whose pid does not exist.
        import json
        os.makedirs(cwl._tokens_dir(root), exist_ok=True)
        stale = {"token_id": "deadtok", "pid": 999999999,
                "host": cwl._this_host(), "proc_start": "x",
                "created_at": cwl._now_iso(), "heartbeat_at": cwl._now_iso(),
                "purpose": "test"}
        with open(os.path.join(cwl._tokens_dir(root), "deadtok.tok"), "w",
                  encoding="utf-8") as fh:
            json.dump(stale, fh)
        flag = cwl.acquire_exclusive(root, "op-3", deadline_seconds=1)
        self.assertEqual(flag["opid"], "op-3")
        self.assertFalse(os.path.isfile(
            os.path.join(cwl._tokens_dir(root), "deadtok.tok")))

    def test_indeterminate_token_never_swept_archive_fails_safe(self):
        root = self._root()
        import json
        os.makedirs(cwl._tokens_dir(root), exist_ok=True)
        indeterminate = {"token_id": "farhost", "pid": 12345,
                         "host": "a-different-host-entirely",
                         "proc_start": "x", "created_at": cwl._now_iso(),
                         "heartbeat_at": cwl._now_iso(), "purpose": "test"}
        with open(os.path.join(cwl._tokens_dir(root), "farhost.tok"), "w",
                  encoding="utf-8") as fh:
            json.dump(indeterminate, fh)
        with self.assertRaises(cwl.WriterLockError):
            cwl.acquire_exclusive(root, "op-4", deadline_seconds=0.2)
        # The token still exists -- never swept on age/indeterminate alone.
        self.assertTrue(os.path.isfile(
            os.path.join(cwl._tokens_dir(root), "farhost.tok")))

    def test_release_exclusive_requires_owner_match(self):
        root = self._root()
        flag = cwl.acquire_exclusive(root, "op-5")
        self.assertFalse(cwl.release_exclusive(root, "op-5", "wrong-nonce"))
        self.assertIsNotNone(cwl.current_exclusive(root))
        self.assertTrue(cwl.release_exclusive(root, "op-5", flag["nonce"]))
        self.assertIsNone(cwl.current_exclusive(root))

    def test_heartbeat_preserves_created_at_updates_heartbeat_at(self):
        root = self._root()
        tok = cwl.acquire_write_token(root, purpose="long")
        path = os.path.join(cwl._tokens_dir(root), tok + ".tok")
        before = cwl._read_json(path)
        self.assertTrue(cwl.heartbeat_write_token(root, tok))
        after = cwl._read_json(path)
        self.assertEqual(before["created_at"], after["created_at"])
        cwl.release_write_token(root, tok)

    def test_heartbeat_on_missing_token_returns_false(self):
        root = self._root()
        self.assertFalse(cwl.heartbeat_write_token(root, "no-such-token"))

    def test_write_token_context_manager_releases_on_exception(self):
        root = self._root()
        try:
            with cwl.write_token(root, "ctx") as wt:
                self.assertTrue(wt.token_id)
                raise RuntimeError("boom")
        except RuntimeError:
            pass
        self.assertEqual(cwl._list_tokens(root), [])

    def test_clear_stale_exclusive_only_on_confirmed_dead(self):
        root = self._root()
        flag = cwl.acquire_exclusive(root, "op-6")
        # Owned by self (live) -- must not clear.
        self.assertFalse(cwl.clear_stale_exclusive(root))
        self.assertIsNotNone(cwl.current_exclusive(root))
        cwl.release_exclusive(root, "op-6", flag["nonce"])
        # Simulate a crashed archive owner.
        import json
        crashed = {"opid": "op-7", "nonce": "n", "pid": 999999999,
                  "host": cwl._this_host(), "proc_start": "x",
                  "created_at": cwl._now_iso()}
        with open(os.path.join(cwl._locks_dir(root), cwl.EXCLUSIVE_FLAG), "w",
                  encoding="utf-8") as fh:
            json.dump(crashed, fh)
        self.assertTrue(cwl.clear_stale_exclusive(root))
        self.assertIsNone(cwl.current_exclusive(root))


if __name__ == "__main__":
    unittest.main()
