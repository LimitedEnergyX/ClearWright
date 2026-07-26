"""Run the wired-path DOM harness for the session-continuity UX.

Both verification reviewers said the previous harness proved helpers rather than
the wired path: it called navigateToWorkItem() directly instead of dispatching a
click through the delegated listener, so an integration regression in the most
common activation path would still pass. This harness installs the real wire(),
renders real tiles, and dispatches genuine events.

It adds no dependency: every import is a Node builtin or the local mini DOM,
there is no package manifest, and the test skips when Node is unavailable so the
suite can never depend on a runtime the project does not otherwise require.
"""
import os
import shutil
import subprocess
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
HARNESS = os.path.join(HERE, "dom", "wired_paths.mjs")
MINI_DOM = os.path.join(HERE, "dom", "mini_dom.mjs")


class WiredPathTest(unittest.TestCase):

    def test_harness_files_exist(self):
        self.assertTrue(os.path.isfile(HARNESS), HARNESS)
        self.assertTrue(os.path.isfile(MINI_DOM), MINI_DOM)

    def test_no_dependency_is_introduced(self):
        """Every import must resolve to a Node builtin or a local file."""
        import re
        for path in (HARNESS, MINI_DOM):
            src = open(path, encoding="utf-8").read()
            for mod in re.findall(r'from "([^"]+)"', src):
                self.assertTrue(mod.startswith("node:") or mod.startswith("./"),
                                "non-builtin import would add a dependency: " + mod)
            self.assertNotIn("require(", src)
        repo = os.path.dirname(HERE)
        self.assertFalse(os.path.exists(os.path.join(repo, "package.json")),
                         "no package manifest may be introduced")

    def test_wired_paths(self):
        node = shutil.which("node")
        if not node:
            self.skipTest("node is not available; wired-path checks skipped")
        proc = subprocess.run([node, HARNESS], capture_output=True)
        out = (proc.stdout or b"").decode("utf-8", "replace")
        err = (proc.stderr or b"").decode("utf-8", "replace")
        self.assertEqual(proc.returncode, 0,
                         "wired-path checks failed:\n%s\n%s" % (out, err))
        self.assertIn("PASS", out, out + err)


if __name__ == "__main__":
    unittest.main()
