"""Handler._safe_write swallows client-disconnect errors only (medglitch #2).

A client that closes the socket mid-response must not surface as a server
fault, but genuine write faults (anything other than the three disconnect
types) must still propagate. send_response/headers/body construction are
outside the guard by design and are not exercised here.
"""
import os
import sys
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APP_DIR = os.path.join(REPO_ROOT, "apps", "control-plane")
sys.path.insert(0, APP_DIR)
import server  # noqa: E402


class _FakeWfile:
    def __init__(self, exc=None):
        self.exc = exc
        self.written = []

    def write(self, body):
        if self.exc is not None:
            raise self.exc
        self.written.append(body)


def _handler(wfile):
    # Bypass BaseHTTPRequestHandler.__init__ (which needs a live socket): we are
    # unit-testing a single method, so construct a bare instance and attach wfile.
    h = server.Handler.__new__(server.Handler)
    h.wfile = wfile
    return h


class SafeWriteTest(unittest.TestCase):
    def test_disconnect_types_swallowed(self):
        for exc in (ConnectionAbortedError(), ConnectionResetError(),
                    BrokenPipeError()):
            with self.subTest(exc=type(exc).__name__):
                h = _handler(_FakeWfile(exc=exc))
                self.assertIsNone(h._safe_write(b"body"))  # returns, no raise

    def test_non_disconnect_propagates(self):
        h = _handler(_FakeWfile(exc=ValueError("not a disconnect")))
        with self.assertRaises(ValueError):
            h._safe_write(b"body")

    def test_normal_write_passes_through(self):
        wf = _FakeWfile()
        h = _handler(wf)
        h._safe_write(b"payload")
        self.assertEqual(wf.written, [b"payload"])


if __name__ == "__main__":
    unittest.main()
