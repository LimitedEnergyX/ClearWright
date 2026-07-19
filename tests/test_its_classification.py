"""Governed internal_technical classification correction (ITS council-enablement
repair — classification half; authority msg-20260719T185647356093).

Restores a governed work item's ability to select the internal_technical (ITS)
egress lane when its envelope EXPLICITLY declared internal_technical, on TWO
separate axes that must stay independent:

  * governance (task_kind=governed -> clearance/gates/authority/verification), and
  * content sensitivity (the egress lane).

The correction is a COARSE declaration gate only. It does NOT infer
internal_technical from governed status, never enables high_risk, and never
grants dispatch: repository-identity / provenance / ancestry / composition /
exact-final-byte tripwire enforcement still prove eligibility independently at
dispatch (covered by tests/test_egress_its.py and friends, unaffected here).
Existing items re-resolve DYNAMICALLY from their preserved requested envelope
value with NO historical rewrite.
"""
import contextlib
import hashlib
import io
import json
import os
import shutil
import sys
import tempfile
import unittest
from argparse import Namespace

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOOLS_DIR = os.path.join(REPO_ROOT, "tools")
APP_DIR = os.path.join(REPO_ROOT, "apps", "control-plane")
sys.path.insert(0, APP_DIR)
sys.path.insert(0, TOOLS_DIR)
import clearwright_use_cw as ucw  # noqa: E402


def _env(task_kind, data_sensitivity=None):
    e = {"task_kind": task_kind, "request": "r", "approved_scope": "s",
         "intended_actions": ["a"], "excluded_actions": ["nothing out of scope"],
         "operator_authority_source": "test", "verification_required": True}
    if data_sensitivity is not None:
        e["data_sensitivity"] = data_sensitivity
    return e


class ResolveDataSensitivity(unittest.TestCase):
    """The declaration-time coarse eligibility gate (_resolve_data_sensitivity)."""

    def test_governed_explicit_internal_technical_is_eligible(self):
        self.assertEqual(ucw._resolve_data_sensitivity(_env("governed", "internal_technical")),
                         ("internal_technical", "declared"))

    def test_analysis_and_actionable_internal_technical_unchanged(self):
        for k in ("analysis", "actionable"):
            self.assertEqual(ucw._resolve_data_sensitivity(_env(k, "internal_technical")),
                             ("internal_technical", "declared"))

    def test_governed_without_declaration_is_sensitive(self):
        self.assertEqual(ucw._resolve_data_sensitivity(_env("governed")),
                         ("sensitive", "default_failclosed"))

    def test_governed_ambiguous_declaration_is_sensitive(self):
        self.assertEqual(ucw._resolve_data_sensitivity(_env("governed", "secret-ish"))[0],
                         "sensitive")

    def test_governed_explicit_sensitive_is_sensitive(self):
        self.assertEqual(ucw._resolve_data_sensitivity(_env("governed", "sensitive")),
                         ("sensitive", "declared"))

    def test_governed_standard_is_standard(self):
        self.assertEqual(ucw._resolve_data_sensitivity(_env("governed", "standard")),
                         ("standard", "declared"))

    def test_high_risk_internal_technical_stays_excluded(self):
        # high_risk must NOT be enabled by this correction (fail-closed).
        self.assertEqual(ucw._resolve_data_sensitivity(_env("high_risk", "internal_technical")),
                         ("sensitive", "ineligible_failclosed"))

    def test_chat_internal_technical_stays_excluded(self):
        self.assertEqual(ucw._resolve_data_sensitivity(_env("chat", "internal_technical"))[0],
                         "sensitive")

    def test_none_envelope_is_fail_closed(self):
        self.assertEqual(ucw._resolve_data_sensitivity(None), ("sensitive", "default_failclosed"))


class DataSensitivityReadPath(unittest.TestCase):
    """The read path (_data_sensitivity) re-derives from the PRESERVED requested
    envelope value, never the stale persisted _audit, never mutating history."""

    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="cw-its-cls-")
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)

    def _persist(self, mid, env, audit):
        return ucw._persist_envelope(self.root, mid, env, audit)

    def test_existing_governed_it_reresolves_without_mutation(self):
        # An item persisted by the OLD (stricter) resolver: preserved requested
        # internal_technical, but _audit stuck at sensitive/ineligible_failclosed.
        mid = "msg-existing-governed-it"
        env = _env("governed", "internal_technical")
        stale = {"classification": "governed", "data_sensitivity": "sensitive",
                 "data_sensitivity_source": "ineligible_failclosed"}
        path = self._persist(mid, env, stale)
        before = hashlib.sha256(open(path, "rb").read()).hexdigest()
        self.assertEqual(ucw._data_sensitivity(self.root, "message:" + mid),
                         "internal_technical")
        after = hashlib.sha256(open(path, "rb").read()).hexdigest()
        self.assertEqual(before, after, "durable envelope must NOT be mutated")
        rec = json.load(open(path, encoding="utf-8"))
        # History intact: _audit still the original resolved value; top-level
        # requested value untouched.
        self.assertEqual(rec["_audit"]["data_sensitivity"], "sensitive")
        self.assertEqual(rec["_audit"]["data_sensitivity_source"], "ineligible_failclosed")
        self.assertEqual(rec["data_sensitivity"], "internal_technical")

    def test_unspecified_governed_not_upgraded(self):
        mid = "msg-governed-unspecified"
        self._persist(mid, _env("governed"), {"data_sensitivity": "sensitive"})
        self.assertEqual(ucw._data_sensitivity(self.root, "message:" + mid), "sensitive")

    def test_sensitive_declaration_not_upgraded(self):
        mid = "msg-governed-sensitive"
        self._persist(mid, _env("governed", "sensitive"), {"data_sensitivity": "sensitive"})
        self.assertEqual(ucw._data_sensitivity(self.root, "message:" + mid), "sensitive")

    def test_high_risk_not_upgraded(self):
        mid = "msg-highrisk-it"
        self._persist(mid, _env("high_risk", "internal_technical"), {"data_sensitivity": "sensitive"})
        self.assertEqual(ucw._data_sensitivity(self.root, "message:" + mid), "sensitive")

    def test_standard_preserved(self):
        mid = "msg-standard"
        self._persist(mid, _env("analysis", "standard"), {"data_sensitivity": "standard"})
        self.assertEqual(ucw._data_sensitivity(self.root, "message:" + mid), "standard")

    def test_missing_envelope_fail_closed(self):
        self.assertEqual(ucw._data_sensitivity(self.root, "message:msg-nope"), "sensitive")

    def test_malformed_work_item_id_fail_closed(self):
        self.assertEqual(ucw._data_sensitivity(self.root, "not-a-ref"), "sensitive")
        self.assertEqual(ucw._data_sensitivity(self.root, ""), "sensitive")


class GovernedInternalTechnicalStart(unittest.TestCase):
    """End-to-end forward-fix through cmd_start: a NEW governed + internal_technical
    item classifies governed, still requires clearance, and persists a coherent
    _audit (data_sensitivity=internal_technical) — governance and lane stay
    independent."""

    def setUp(self):
        import server  # noqa: E402  (heavy import; only the start-path tests need it)
        self.server = server
        base = tempfile.mkdtemp(prefix="cw-its-start-")
        self.addCleanup(shutil.rmtree, base, ignore_errors=True)
        self.root, *_ = server.resolve_queue(base)
        import clearwright_egress_guard as guard_mod
        import clearwright_codex_review as ccr_mod
        ok, oe = guard_mod.provider_key_status, ccr_mod.codex_executable
        guard_mod.provider_key_status = lambda *a, **k: (True, "process_env")
        ccr_mod.codex_executable = lambda: "codex-stub"
        self.addCleanup(setattr, guard_mod, "provider_key_status", ok)
        self.addCleanup(setattr, ccr_mod, "codex_executable", oe)

    def _start(self, env):
        d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        p = os.path.join(d, "env.json")
        with open(p, "w", encoding="utf-8") as fh:
            json.dump(env, fh)
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = ucw.cmd_start(Namespace(
                queue_root=self.root, envelope_file=p, request=None, request_file=None,
                kind=None, thread_id=None, packet_id=None, approved_scope=None,
                actor="claude", json=True))
        return json.loads(buf.getvalue().strip().splitlines()[-1]), code

    def _only_envelope(self):
        d = os.path.join(self.root, "task_envelopes")
        files = [f for f in os.listdir(d) if f.endswith(".json")]
        self.assertEqual(len(files), 1)
        return json.load(open(os.path.join(d, files[0]), encoding="utf-8"))

    def test_new_governed_it_requires_clearance_and_persists_internal_technical(self):
        res, _ = self._start(_env("governed", "internal_technical"))
        self.assertEqual(res["kind"], "governed")
        self.assertTrue(res["requires_clearance"])
        rec = self._only_envelope()
        self.assertEqual(rec["_audit"]["classification"], "governed")
        self.assertEqual(rec["_audit"]["data_sensitivity"], "internal_technical")
        self.assertEqual(rec["_audit"]["data_sensitivity_source"], "declared")

    def test_new_governed_unspecified_still_sensitive_and_requires_clearance(self):
        res, _ = self._start(_env("governed"))
        self.assertEqual(res["kind"], "governed")
        self.assertTrue(res["requires_clearance"])
        rec = self._only_envelope()
        self.assertEqual(rec["_audit"]["data_sensitivity"], "sensitive")


if __name__ == "__main__":
    unittest.main()
