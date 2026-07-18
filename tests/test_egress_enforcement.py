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


class LiveLineageWiring(unittest.TestCase):
    def test_run_round_wires_require_graph_context_to_both_reviewers(self):
        import tempfile
        import clearwright_review_council as cwrc
        import clearwright_egress_guard as guard

        root = tempfile.mkdtemp(prefix="cw-lineage-wire-")
        self.addCleanup(lambda: __import__("shutil").rmtree(root, ignore_errors=True))

        # A council whose candidate derives from a SENSITIVE source (user upload).
        g = guard.LineageGraph()
        g.add("upload", guard.CLASS_RAW, provenance={"class": "user_upload"})
        g.add("packet", guard.CLASS_MACHINE, source_ids=["upload"])
        council = cwrc.create_council(
            root, thread_id="t", data_sensitivity="standard",
            lineage=g.to_records(), lineage_candidate="packet", source_bindings=[])

        seen = {}

        def _v(r):
            return {"reviewer": r, "verdict": "approve", "confidence": 0.9,
                    "risk_level": "low", "blocking_findings": [],
                    "required_changes": [], "nonblocking_findings": [],
                    "disagreements": [], "assumptions": [], "questions": [],
                    "recommended_plan": [], "summary": "ok"}

        def fake_gpt(root, packet, **kw):
            seen["gpt"] = kw.get("egress_context")
            return {"ok": True, "posted": True, "reviewer": "gpt",
                    "validated": True, "verdict": _v("gpt"), "telemetry": {},
                    "message_id": "m1"}

        def fake_codex(root, packet, **kw):
            seen["codex"] = kw.get("egress_context")
            return {"ok": True, "posted": True, "reviewer": "codex",
                    "validated": True, "verdict": _v("codex"), "telemetry": {},
                    "message_id": "m2"}

        cwrc.run_round(root, council, "packet text", gpt_fn=fake_gpt,
                       codex_fn=fake_codex, sleep=lambda *_: None)

        for who in ("gpt", "codex"):
            ctx = seen[who]
            self.assertIsInstance(ctx, guard.EgressContext)
            self.assertTrue(ctx.require_graph, who)
            self.assertIsNotNone(ctx.graph, who)
            # The live context resolves the sensitive lineage and a plain
            # standard packet would be blocked (declared-standard cannot
            # override the derived-sensitive candidate).
            with self.assertRaises(guard.EgressBlocked):
                ctx.resolve()

    def test_inlined_artifact_forces_sensitive_in_run_round(self):
        # CRITICAL regression: an artifact inlined via the artifact_id / council-
        # remembered channel must be a SENSITIVE ancestor of the candidate, so a
        # declared-standard council with an artifact resolves SENSITIVE and the
        # standard-tier dispatch fails closed. Exercises the run_round choke.
        import tempfile
        import clearwright_review_council as cwrc
        import clearwright_egress_guard as guard
        import clearwright_artifacts as cwa

        root = tempfile.mkdtemp(prefix="cw-artifact-lineage-")
        self.addCleanup(lambda: __import__("shutil").rmtree(root, ignore_errors=True))

        # A STANDARD lineage (single clean machine candidate over a repo source).
        g = guard.LineageGraph()
        g.add("src", guard.CLASS_RAW,
              provenance={"class": "approved_repo_file", "path_rel": "tools/x.py",
                          "sha256": "0" * 64})
        g.add("packet", guard.CLASS_MACHINE, source_ids=["src"])
        council = cwrc.create_council(root, thread_id="t", data_sensitivity="standard",
                                      lineage=g.to_records(), lineage_candidate="packet",
                                      source_bindings=[])
        # register a sensitive artifact
        fd, up = tempfile.mkstemp(suffix=".txt", prefix="cw-upload-")
        os.write(fd, b"confidential board memo, no PII regex here\n")
        os.close(fd)
        self.addCleanup(os.remove, up)
        art = cwa.register(root, up)
        aid = art["artifact_id"] if isinstance(art, dict) else art

        seen = {}

        def _v(r):
            return {"reviewer": r, "verdict": "approve", "confidence": 0.9,
                    "risk_level": "low", "blocking_findings": [], "required_changes": [],
                    "nonblocking_findings": [], "disagreements": [], "assumptions": [],
                    "questions": [], "recommended_plan": [], "summary": "ok"}

        def fake(r, packet, **kw):
            seen[kw.get("egress_context") and "ctx" or "x"] = kw.get("egress_context")
            return {"ok": True, "posted": True, "reviewer": "gpt", "validated": True,
                    "verdict": _v("gpt"), "telemetry": {}, "message_id": "m"}

        cwrc.run_round(root, council, "packet text", artifact_ids=[aid],
                       gpt_fn=fake, codex_fn=fake, sleep=lambda *_: None)
        ctx = seen.get("ctx")
        self.assertIsNotNone(ctx)
        # With the artifact folded in as a SENSITIVE ancestor, a declared-standard
        # context must now be refused (no downgrade).
        with self.assertRaises(guard.EgressBlocked):
            ctx.resolve()

    def test_missing_lineage_context_fails_closed_on_resolve(self):
        import clearwright_egress_guard as guard
        ctx = guard.EgressContext("standard", require_graph=True)
        with self.assertRaises(guard.EgressBlocked) as cm:
            ctx.resolve()
        self.assertEqual(cm.exception.reason, "lineage_missing")


if __name__ == "__main__":
    unittest.main()
