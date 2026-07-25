"""ALF-0005 tests: authoritative pre-allocation dispatch eligibility.

Covers production signal derivation, refusal BEFORE council-id/reviewer-attempt
allocation, durability of the normalized reason, rejection of untrusted
caller-supplied signals, and unchanged behaviour for eligible packets.
"""
import io
import json
import os
import shutil
import sys
import tempfile
import unittest
from contextlib import redirect_stdout

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "tools"))
import clearwright_dispatch_preflight as cwdp   # noqa: E402
import clearwright_review_council as cwrc       # noqa: E402
import clearwright_use_cw as ucw                # noqa: E402

OK = dict(dispatch_lane="user", review_profile="code", artifact_count=0,
          lineage_bound=True, raw_provenance_standard=True, tripwire_clear=True)

# Constructed from its code point so this source file stays pure ASCII and the
# character under test is unambiguous to any reader (including a reviewer reading
# a sanitized packet). U+2014 EM DASH is inside the guard's confusable range.
EM_DASH = "\u2014"
# Whitespace that str.strip() removes (U+2028 LINE SEPARATOR and U+3000
# IDEOGRAPHIC SPACE), built from code points so this file stays ASCII.
WHITESPACE_ONLY = "\u2028" + "\u3000" + " \t"


def sig(**over):
    kw = dict(OK)
    kw.update(over)
    return cwdp.production_signals(**kw)


def elig(**over):
    return cwdp.dispatch_eligibility(sig(**over))


class ProductionSignalDerivationTest(unittest.TestCase):
    def test_fully_eligible_passes(self):
        self.assertEqual(elig(), (True, None))

    def test_tripwire_hit_refuses_on_every_lane(self):
        for lane in ("user", "internal_technical"):
            self.assertEqual(elig(dispatch_lane=lane, tripwire_clear=False),
                             (False, "tripwire_refusal"))

    def test_readiness_signals_are_deliberately_not_produced(self):
        """Provider readiness and credential presence are DYNAMIC environmental
        conditions, not deterministic content properties. Refusing on them could
        newly deny a packet that would otherwise dispatch (e.g. through an
        injected or differently-resolved adapter), which would break the
        can-only-refuse-earlier invariant. They must never appear here."""
        for lane in ("user", "internal_technical"):
            s = sig(dispatch_lane=lane)
            self.assertNotIn("provider_ready", s)
            self.assertNotIn("auth_ok", s)
        for name in ("provider_ready", "auth_ok"):
            self.assertNotIn(name, cwdp.production_signals.__code__.co_varnames)

    def test_its_lane_refuses_artifacts_and_non_code_profile(self):
        self.assertEqual(elig(dispatch_lane="internal_technical", artifact_count=1),
                         (False, "policy_denial"))
        self.assertEqual(elig(dispatch_lane="internal_technical",
                              review_profile="editorial"), (False, "policy_denial"))

    def test_its_lane_refuses_unbound_composition_and_provenance(self):
        self.assertEqual(elig(dispatch_lane="internal_technical",
                              lineage_bound=False),
                         (False, "composition_or_hash_mismatch"))
        self.assertEqual(elig(dispatch_lane="internal_technical",
                              raw_provenance_standard=False),
                         (False, "provenance_unresolved"))

    def test_its_only_signals_are_omitted_on_the_user_lane(self):
        """The user lane must not acquire a blocker from a check it does not
        perform, or this would refuse packets that dispatch fine today."""
        s = sig(dispatch_lane="user", artifact_count=5, review_profile="editorial",
                lineage_bound=False, raw_provenance_standard=False)
        for k in ("lane_authorized", "composition_bound", "provenance_resolved"):
            self.assertNotIn(k, s)
        self.assertEqual(cwdp.dispatch_eligibility(s), (True, None))

    def test_its_signals_present_on_the_its_lane(self):
        s = sig(dispatch_lane="internal_technical")
        for k in ("lane_authorized", "composition_bound", "provenance_resolved"):
            self.assertIn(k, s)


class UntrustedSignalTest(unittest.TestCase):
    """Caller-supplied signals must never be able to ENABLE a dispatch."""

    def test_eligibility_can_only_refuse_never_authorize(self):
        # every attacker-friendly value still leaves a real blocker refusing
        for planted in ({}, {"tripwire_clear": True}, {"provider_ready": True},
                        {"lane_authorized": True}, {"auth_ok": "yes"}):
            merged = dict(planted)
            merged.update(sig(tripwire_clear=False))
            self.assertEqual(cwdp.dispatch_eligibility(merged)[0], False)

    def test_production_signals_ignores_any_caller_map(self):
        """production_signals takes explicit keyword facts only; there is no
        pathway for a council record field to reach it."""
        self.assertNotIn("preallocation_signals",
                         cwdp.production_signals.__code__.co_varnames)
        s = sig(tripwire_clear=False)
        self.assertFalse(s["tripwire_clear"])

    def test_malformed_signal_values_fail_closed(self):
        self.assertEqual(cwdp.dispatch_eligibility({"tripwire_clear": 0})[0], False)
        self.assertEqual(cwdp.dispatch_eligibility({"tripwire_clear": ""})[0], False)
        self.assertEqual(cwdp.dispatch_eligibility({"tripwire_clear": None})[0], False)


class LaneSingleSourceOfTruthTest(unittest.TestCase):
    """resolve_lane must agree with the lane create_council actually records, or
    a packet could be judged against a different lane than it dispatches on."""

    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="cw-lane-")

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)

    def test_lane_matches_created_council(self):
        for declared in ("standard", "internal_technical", "sensitive", "", None,
                         "SOMETHING-ELSE", "  Standard  "):
            c = cwrc.create_council(self.root, thread_id="thr-x",
                                    data_sensitivity=declared)
            ds, lane = cwrc.resolve_lane(declared)
            self.assertEqual((c["data_sensitivity"], c["dispatch_lane"]), (ds, lane))


class PreAllocationRefusalIntegrationTest(unittest.TestCase):
    """A deterministic blocker must refuse BEFORE any council id or reviewer
    attempt is allocated, and must persist its normalized reason."""

    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="cw-prealloc-")
        self.work = tempfile.mkdtemp(prefix="cw-prealloc-w-")

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)
        shutil.rmtree(self.work, ignore_errors=True)

    def _plan(self, text):
        p = os.path.join(self.work, "packet.md")
        with open(p, "w", encoding="utf-8") as fh:
            fh.write(text)
        return p

    def _run(self, plan_path):
        args = ucw.build_parser().parse_args(
            ["council", self.root, "--thread-id", "thr-prealloc",
             "--plan-file", plan_path, "--json"])
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = args.func(args)
        return code, buf.getvalue()

    def _council_count(self):
        d = cwrc.councils_root(self.root)
        return len(os.listdir(d)) if os.path.isdir(d) else 0

    def test_confusable_refuses_before_allocation_and_is_durable(self):
        # U+2014 EM DASH is inside the guard's unicode-confusable tripwire range,
        # so this packet is guaranteed to be blocked at send.
        code, out = self._run(self._plan("review this " + EM_DASH + " carefully\n"))
        payload = json.loads(out.strip().splitlines()[-1])
        self.assertEqual(payload.get("error"), "dispatch_ineligible")
        self.assertEqual(payload.get("normalized_reason"), "tripwire_refusal")
        self.assertIsNone(payload.get("council_id"))
        self.assertEqual(payload.get("attempts"), {})
        # ZERO council ids allocated
        self.assertEqual(self._council_count(), 0)
        # normalized reason persisted durably, with no reviewer attempt consumed
        log = os.path.join(self.root, "invocation_log.jsonl")
        self.assertTrue(os.path.exists(log))
        with open(log, encoding="utf-8") as fh:
            recs = [json.loads(l) for l in fh if l.strip()]
        refusals = [r for r in recs
                    if r.get("command") == "dispatch-refused-preallocation"]
        self.assertEqual(len(refusals), 1)
        self.assertEqual(refusals[0]["normalized_reason"], "tripwire_refusal")
        self.assertEqual(refusals[0]["attempt"], 0)
        # log_invocation drops null fields, so NO council id recorded is exactly
        # the "zero council ids consumed" evidence.
        self.assertNotIn("council_id", refusals[0])

    def test_context_file_path_is_also_scanned(self):
        """The scan must cover whichever content flag supplies the packet, not
        just --plan-file."""
        p = os.path.join(self.work, "ctx.md")
        with open(p, "w", encoding="utf-8") as fh:
            fh.write("context via the other flag " + EM_DASH + "\n")
        args = ucw.build_parser().parse_args(
            ["council", self.root, "--thread-id", "thr-ctx",
             "--context-file", p, "--json"])
        buf = io.StringIO()
        with redirect_stdout(buf):
            args.func(args)
        payload = json.loads(buf.getvalue().strip().splitlines()[-1])
        self.assertEqual(payload.get("normalized_reason"), "tripwire_refusal")
        self.assertEqual(self._council_count(), 0)

    def test_scan_decision_does_not_depend_on_a_content_transformation(self):
        """Whether to scan must depend on LOAD SUCCESS, never on str.strip().
        strip() removes Unicode whitespace and could erase tripwire-relevant
        characters, letting a context bypass the pre-allocation scan entirely."""
        seen = {}
        import clearwright_egress_guard as eg
        orig = eg.classify

        def _spy(text, *a, **kw):
            seen["text"] = text
            return orig(text, *a, **kw)

        eg.classify = _spy
        try:
            # content that str.strip() reduces to empty must STILL be classified
            self._run(self._plan(WHITESPACE_ONLY))
        finally:
            eg.classify = orig
        self.assertIn("text", seen)
        self.assertNotEqual(seen["text"].strip(), seen["text"])

    def test_any_classifier_failure_fails_closed(self):
        """Not just EgressBlocked: ANY classification exception must refuse."""
        import clearwright_egress_guard as eg

        class Boom(Exception):
            pass

        orig = eg.classify
        eg.classify = lambda *a, **kw: (_ for _ in ()).throw(Boom("scanner blew up"))
        try:
            code, out = self._run(self._plan("perfectly ordinary ascii packet\n"))
        finally:
            eg.classify = orig
        payload = json.loads(out.strip().splitlines()[-1])
        self.assertEqual(payload.get("normalized_reason"), "tripwire_refusal")
        self.assertEqual(self._council_count(), 0)

    def test_eligible_packet_still_reaches_council_creation(self):
        """The gate must not block a packet that would dispatch today."""
        called = {}

        def _boom(*a, **kw):
            called["yes"] = True
            raise RuntimeError("reached create_council")

        orig = cwrc.create_council
        cwrc.create_council = _boom
        try:
            with self.assertRaises(RuntimeError):
                self._run(self._plan("plain ascii packet, no tripwire\n"))
        finally:
            cwrc.create_council = orig
        self.assertTrue(called.get("yes"))


if __name__ == "__main__":
    unittest.main()
