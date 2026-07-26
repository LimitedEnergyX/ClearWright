"""Run the dependency-free DOM runtime harness for the session-continuity UX.

Both verification reviewers observed, correctly, that static assertions over
app.js cannot catch the defect classes this slice actually hit: a scroll
listener bound to an element that never scrolls, a rank bucket nothing can
produce, and a composer target shape the server cannot validate. This test
EXECUTES the real app.js in Node against a controllable DOM stub.

It adds no dependency: no package.json, no npm install, no browser driver. When
Node is unavailable the test skips rather than failing, so it can never make the
suite depend on a runtime the project does not otherwise require.
"""
import os
import shutil
import subprocess
import unittest

HARNESS = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                       "dom", "session_ux_runtime.mjs")


class SessionUxRuntimeTest(unittest.TestCase):

    def test_harness_exists(self):
        self.assertTrue(os.path.isfile(HARNESS), HARNESS)

    def test_runtime_behaviour(self):
        node = shutil.which("node")
        if not node:
            self.skipTest("node is not available; runtime DOM checks skipped")
        proc = subprocess.run([node, HARNESS], capture_output=True)
        out = (proc.stdout or b"").decode("utf-8", "replace")
        err = (proc.stderr or b"").decode("utf-8", "replace")
        self.assertEqual(proc.returncode, 0,
                         "runtime DOM checks failed:\n%s\n%s" % (out, err))
        self.assertIn("PASS", out, out + err)


if __name__ == "__main__":
    unittest.main()
