"""Runtime enforcement tests for the egress boundary (SDEG PR-1):
- no production module outside the guard reaches a provider (source scan that
  mirrors the CI egress gate);
- the orchestration/dispatch path opens no network socket when reviewers are
  injected (a runtime bypass check).
SYNTHETIC fixtures only.
"""
import os
import re
import sys
import unittest

HERE = os.path.dirname(__file__)
TOOLS = os.path.join(HERE, "..", "tools")
APPS = os.path.join(HERE, "..", "apps")
sys.path.insert(0, TOOLS)

# The exact primitives the guard alone may use.
FORBIDDEN = re.compile(
    r"api\.openai\.com|urllib\.request|urlopen\(|http\.client|"
    r"requests\.(get|post|Session)|httpx|aiohttp|socket\.(connect|create_connection)|"
    r"subprocess\.Popen|os\.system\(|Start-Process|codex exec")

# Allowlist: the guard owns provider egress; the codex adapter delegates to it;
# clearwright_proof.py is a localhost-only control-plane state prober
# (127.0.0.1/api/state, never a provider — confirmed by the SDEG inspection).
ALLOWLIST = {"clearwright_egress_guard.py", "clearwright_codex_review.py",
             "clearwright_proof.py"}


def _py_files(root):
    for dirpath, _dirs, files in os.walk(root):
        if "__pycache__" in dirpath:
            continue
        for f in files:
            if f.endswith(".py"):
                yield os.path.join(dirpath, f)


class SourceScanEgressGate(unittest.TestCase):
    def test_no_unguarded_egress_primitive_in_production(self):
        offenders = []
        for path in list(_py_files(TOOLS)) + list(_py_files(APPS)):
            if os.path.basename(path) in ALLOWLIST:
                continue
            with open(path, encoding="utf-8") as fh:
                for n, line in enumerate(fh, 1):
                    if FORBIDDEN.search(line):
                        offenders.append("{}:{}: {}".format(
                            os.path.relpath(path, os.path.join(HERE, "..")),
                            n, line.strip()))
        self.assertEqual(offenders, [],
                         "Unguarded egress primitive(s) found:\n" + "\n".join(offenders))

    def test_guard_actually_uses_the_transport_primitives(self):
        # The allowlisted guard is where they live (guards against a stale
        # allowlist that no longer corresponds to reality).
        with open(os.path.join(TOOLS, "clearwright_egress_guard.py"),
                  encoding="utf-8") as fh:
            src = fh.read()
        self.assertIn("api.openai.com", src)
        self.assertIn("urllib.request", src)


class NoNetworkOnMockedDispatch(unittest.TestCase):
    def test_council_round_opens_no_socket_with_injected_reviewers(self):
        import socket
        import clearwright_review_council as cwrc

        # a queue root
        import tempfile
        root = tempfile.mkdtemp(prefix="cw-egress-enf-")
        self.addCleanup(lambda: __import__("shutil").rmtree(root, ignore_errors=True))

        council = cwrc.create_council(root, thread_id="t",
                                      data_sensitivity="standard")

        def _v(reviewer):
            return {"reviewer": reviewer, "verdict": "approve", "confidence": 0.9,
                    "risk_level": "low", "blocking_findings": [],
                    "required_changes": [], "nonblocking_findings": [],
                    "disagreements": [], "assumptions": [], "questions": [],
                    "recommended_plan": [], "summary": "looks fine"}

        seen = {}

        def fake_gpt(root, packet, **kw):
            seen["gpt_ctx"] = kw.get("egress_context")
            return {"ok": True, "posted": True, "reviewer": "gpt",
                    "validated": True, "verdict": _v("gpt"), "telemetry": {},
                    "message_id": "m1"}

        def fake_codex(root, packet, **kw):
            seen["codex_ctx"] = kw.get("egress_context")
            return {"ok": True, "posted": True, "reviewer": "codex",
                    "validated": True, "verdict": _v("codex"), "telemetry": {},
                    "message_id": "m2"}

        real_socket = socket.socket

        def no_socket(*a, **k):
            raise AssertionError("network socket opened during a mocked dispatch")

        socket.socket = no_socket
        try:
            cwrc.run_round(root, council, "a synthetic technical review packet",
                           gpt_fn=fake_gpt, codex_fn=fake_codex,
                           sleep=lambda *_: None)
        finally:
            socket.socket = real_socket

        # The engine built and passed a guard EgressContext to BOTH reviewers.
        import clearwright_egress_guard as guard
        self.assertIsInstance(seen.get("gpt_ctx"), guard.EgressContext)
        self.assertIsInstance(seen.get("codex_ctx"), guard.EgressContext)
        self.assertEqual(seen["gpt_ctx"].tier, "standard")


if __name__ == "__main__":
    unittest.main()
