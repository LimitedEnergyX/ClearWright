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
        # a scanner EXCEPTION is an unresolved classifier, not a tripwire hit
        self.assertEqual(payload.get("normalized_reason"), "classifier_unresolved")
        self.assertEqual(self._council_count(), 0)

    def test_non_confusable_hit_category_is_also_refused(self):
        """A "hit" of ANY finding category is a tripwire refusal, because
        egress_guard.authorize() raises EgressBlocked("tripwire_hit") for a hit
        verdict over the FULL outbound bytes with NO branching on category and
        before the sensitive-tier branch, so it applies on every lane. That is a
        PROVEN guard block. Unknown verdicts do NOT take this path; they are
        classifier_unresolved under a separate fail-closed policy.
        over-refusal, because egress_guard.authorize() raises
        EgressBlocked("tripwire_hit") for any non-clear verdict over the FULL
        outbound bytes with NO branching on finding category, and before the
        sensitive-tier branch, so it applies on every lane. This pins that
        behaviour for a category other than unicode_confusable."""
        import clearwright_egress_guard as eg
        orig = eg.classify
        eg.classify = lambda text, *a, **kw: {
            "findings": {"contextual_identity": 1}, "verdict": "hit",
            "policy_version": "test", "policy_sha256": "x", "input_sha256": "y"}
        try:
            code, out = self._run(self._plan("ordinary ascii packet\n"))
        finally:
            eg.classify = orig
        payload = json.loads(out.strip().splitlines()[-1])
        self.assertEqual(payload.get("normalized_reason"), "tripwire_refusal")
        self.assertEqual(self._council_count(), 0)

    def test_exception_and_structured_hit_are_distinct_paths(self):
        """Codex round-1 required_changes[1]: failing closed on an EXCEPTION and
        refusing on a structured non-clear VERDICT are different code paths.
        Both must refuse, and neither may silently dispatch."""
        import clearwright_egress_guard as eg
        seen = []
        orig = eg.classify

        def _verdict(text, *a, **kw):
            seen.append("verdict")
            return {"findings": {"unicode_confusable": 1}, "verdict": "hit",
                    "policy_version": "t", "policy_sha256": "x", "input_sha256": "y"}

        def _raise(text, *a, **kw):
            seen.append("exception")
            raise RuntimeError("scanner exploded")

        # Under the three-way contract these report DIFFERENT normalized
        # reasons: a structured "hit" is a tripwire refusal, while an exception
        # is an unresolved classifier. Both refuse; neither dispatches.
        for fn, expected in ((_verdict, "tripwire_refusal"),
                             (_raise, "classifier_unresolved")):
            eg.classify = fn
            try:
                code, out = self._run(self._plan("ordinary ascii packet\n"))
            finally:
                eg.classify = orig
            payload = json.loads(out.strip().splitlines()[-1])
            self.assertEqual(payload.get("normalized_reason"), expected)
            self.assertEqual(self._council_count(), 0)
        self.assertEqual(seen, ["verdict", "exception"])

    def test_gate_and_create_council_use_the_same_sensitivity_source(self):
        """GPT round-1 required_changes[1]: prove the pre-allocation path derives
        its lane from EXACTLY the same source and value create_council is given,
        so the judged lane cannot differ from the dispatched lane."""
        captured = {}
        orig_create = cwrc.create_council

        def _spy(root, **kw):
            captured["data_sensitivity"] = kw.get("data_sensitivity")
            raise RuntimeError("stop before allocation")

        cwrc.create_council = _spy
        try:
            with self.assertRaises(RuntimeError):
                self._run(self._plan("plain ascii packet\n"))
        finally:
            cwrc.create_council = orig_create
        # the value handed to create_council is the SAME function+argument the
        # gate uses, for the same work item (None here -> fail-closed sensitive)
        expected = ucw._data_sensitivity(self.root, None)
        self.assertEqual(captured["data_sensitivity"], expected)
        self.assertEqual(cwrc.resolve_lane(captured["data_sensitivity"])[1],
                         cwrc.resolve_lane(expected)[1])

    def test_classifier_verdict_matrix_is_exhaustive(self):
        """Operator-authorized round-3 correction. The classifier contract is
        exactly two KNOWN verdicts; everything else fails closed with a DISTINCT
        reason and is never treated as authorization.

        clear -> eligible | hit -> tripwire_refusal | anything else ->
        classifier_unresolved. Every refusal consumes zero council ids."""
        import clearwright_egress_guard as eg

        def verdict(v):
            return lambda text, *a, **kw: {"findings": {}, "verdict": v,
                                           "policy_version": "t",
                                           "policy_sha256": "x",
                                           "input_sha256": "y"}

        cases = [
            ("hit", verdict("hit"), "tripwire_refusal"),
            ("unknown", verdict("degraded"), "classifier_unresolved"),
            ("empty", verdict(""), "classifier_unresolved"),
            ("none", verdict(None), "classifier_unresolved"),
            ("future", verdict("quarantined"), "classifier_unresolved"),
            ("malformed-no-verdict-key",
             lambda text, *a, **kw: {"findings": {}}, "classifier_unresolved"),
            ("malformed-not-a-dict",
             lambda text, *a, **kw: None, "classifier_unresolved"),
            ("malformed-nonstring-verdict",
             verdict(1), "classifier_unresolved"),
            ("exception",
             lambda text, *a, **kw: (_ for _ in ()).throw(RuntimeError("boom")),
             "classifier_unresolved"),
        ]
        orig = eg.classify
        for label, fn, expected in cases:
            eg.classify = fn
            try:
                code, out = self._run(self._plan("ascii packet\n"))
            finally:
                eg.classify = orig
            payload = json.loads(out.strip().splitlines()[-1])
            self.assertEqual(payload.get("normalized_reason"), expected,
                             "case %s" % label)
            self.assertEqual(payload.get("error"), "dispatch_ineligible",
                             "case %s" % label)
            self.assertIsNone(payload.get("council_id"), "case %s" % label)
            self.assertEqual(payload.get("attempts"), {}, "case %s" % label)
            self.assertEqual(self._council_count(), 0, "case %s" % label)

    def test_every_refusal_reason_is_a_known_normalized_class(self):
        self.assertIn("classifier_unresolved", cwdp.NORMALIZED_FAILURE_CLASSES)
        self.assertIn("tripwire_refusal", cwdp.NORMALIZED_FAILURE_CLASSES)

    def test_unresolved_classifier_is_not_reported_as_a_tripwire(self):
        """An unrecognised verdict must report its OWN reason: mislabelling it a
        tripwire hit would hide a classifier contract change."""
        sig = cwdp.production_signals(
            dispatch_lane="user", review_profile="code", artifact_count=0,
            lineage_bound=True, raw_provenance_standard=True,
            tripwire_clear=True, classifier_resolved=False)
        self.assertEqual(cwdp.dispatch_eligibility(sig),
                         (False, "classifier_unresolved"))
        # and it wins over a simultaneous tripwire failure, by check ordering
        sig2 = cwdp.production_signals(
            dispatch_lane="user", review_profile="code", artifact_count=0,
            lineage_bound=True, raw_provenance_standard=True,
            tripwire_clear=False, classifier_resolved=False)
        self.assertEqual(cwdp.dispatch_eligibility(sig2),
                         (False, "classifier_unresolved"))

    def test_clear_verdict_still_reaches_dispatch_unchanged(self):
        """The eligible path must be untouched by the three-way branch."""
        sig = cwdp.production_signals(
            dispatch_lane="user", review_profile="code", artifact_count=0,
            lineage_bound=True, raw_provenance_standard=True,
            tripwire_clear=True, classifier_resolved=True)
        self.assertEqual(cwdp.dispatch_eligibility(sig), (True, None))

    def test_caller_input_cannot_convert_a_refusal_into_authorization(self):
        """No caller-controlled value may turn any refusal into an allow."""
        for planted in ({}, {"classifier_resolved": True},
                        {"tripwire_clear": True}, {"lane_authorized": True},
                        {"classifier_resolved": "yes"}, {"tripwire_clear": 1}):
            for real in (cwdp.production_signals(
                             dispatch_lane="user", review_profile="code",
                             artifact_count=0, lineage_bound=True,
                             raw_provenance_standard=True, tripwire_clear=True,
                             classifier_resolved=False),
                         cwdp.production_signals(
                             dispatch_lane="user", review_profile="code",
                             artifact_count=0, lineage_bound=True,
                             raw_provenance_standard=True, tripwire_clear=False,
                             classifier_resolved=True)):
                merged = dict(planted)
                merged.update(real)          # authoritative facts win
                self.assertFalse(cwdp.dispatch_eligibility(merged)[0])

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


class StrictFactValidationTest(unittest.TestCase):
    """Operator-authorized round-4 item 3: no permissive coercion. An
    authoritative fact is accepted ONLY as an exact bool (or exact non-negative
    int for artifact_count). A malformed or truthy non-boolean value must fail
    closed as classifier_unresolved and must NEVER become allow-shaped."""

    MALFORMED = ("false", "yes", "true", "", 1, 0, 1.0, [], {}, None, object())

    def test_truthy_non_boolean_never_authorizes(self):
        for bad in self.MALFORMED:
            for key in ("tripwire_clear", "classifier_resolved",
                        "lineage_bound", "raw_provenance_standard"):
                s = sig(**{key: bad})
                ok, reason = cwdp.dispatch_eligibility(s)
                self.assertFalse(ok, "%s=%r must not authorize" % (key, bad))
                self.assertEqual(reason, "classifier_unresolved",
                                 "%s=%r" % (key, bad))

    def test_malformed_artifact_count_fails_closed(self):
        for bad in ("0", 1.0, -1, True, False, None, [], object()):
            ok, reason = cwdp.dispatch_eligibility(sig(artifact_count=bad))
            self.assertFalse(ok, "artifact_count=%r must not authorize" % (bad,))
            self.assertEqual(reason, "classifier_unresolved")

    def test_exact_booleans_still_behave_normally(self):
        self.assertEqual(cwdp.dispatch_eligibility(sig()), (True, None))
        self.assertEqual(cwdp.dispatch_eligibility(sig(tripwire_clear=False)),
                         (False, "tripwire_refusal"))
        self.assertEqual(cwdp.dispatch_eligibility(sig(classifier_resolved=False)),
                         (False, "classifier_unresolved"))

    def test_malformed_fact_emits_no_allow_shaped_signal(self):
        s = sig(tripwire_clear="yes")
        self.assertIs(s["classifier_resolved"], False)
        self.assertIs(s["tripwire_clear"], False)
        for v in s.values():
            self.assertIsInstance(v, bool)


class ItsLaneBlockerEndToEndTest(unittest.TestCase):
    """GPT round-1 required_changes[0]: prove the FULL production refusal path
    (no council directory entry, no reviewer attempt, one durable normalized
    record) for each internal_technical blocker, not only for tripwire."""

    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="cw-its-")
        self.work = tempfile.mkdtemp(prefix="cw-its-w-")
        self.mid = "msg-20260725T000000000000"
        env = {"envelope_version": 1, "task_kind": "governed",
               "data_sensitivity": "internal_technical", "review_profile": "code",
               "verification_required": True, "request": "r",
               "approved_scope": "s", "intended_actions": [],
               "excluded_actions": [], "operator_authority_source": "o"}
        ucw._persist_envelope(self.root, self.mid, env,
                              {"classification": "governed",
                               "data_sensitivity": "internal_technical"})

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)
        shutil.rmtree(self.work, ignore_errors=True)

    def _plan(self, text="ascii packet for the internal lane\n"):
        p = os.path.join(self.work, "packet.md")
        with open(p, "w", encoding="utf-8") as fh:
            fh.write(text)
        return p

    def _run(self, extra=()):
        argv = ["council", self.root, "--thread-id", "thr-its",
                "--work-item-id", "message:" + self.mid,
                "--plan-file", self._plan(), "--json"] + list(extra)
        args = ucw.build_parser().parse_args(argv)
        buf = io.StringIO()
        with redirect_stdout(buf):
            args.func(args)
        return json.loads(buf.getvalue().strip().splitlines()[-1])

    def _councils(self):
        d = cwrc.councils_root(self.root)
        return len(os.listdir(d)) if os.path.isdir(d) else 0

    def _refusals(self):
        log = os.path.join(self.root, "invocation_log.jsonl")
        if not os.path.exists(log):
            return []
        with open(log, encoding="utf-8") as fh:
            return [json.loads(l) for l in fh if l.strip()
                    and json.loads(l).get("command") == "dispatch-refused-preallocation"]

    def test_lane_resolves_internal_technical(self):
        self.assertEqual(
            cwrc.resolve_lane(ucw._data_sensitivity(self.root, "message:" + self.mid))[1],
            "internal_technical")

    def test_unresolved_provenance_refuses_before_allocation(self):
        """The packet lives outside any approved repository, so its RAW node
        cannot carry STANDARD provenance on the internal_technical lane."""
        payload = self._run()
        self.assertEqual(payload.get("normalized_reason"), "provenance_unresolved")
        self.assertEqual(payload.get("council_id"), None)
        self.assertEqual(payload.get("attempts"), {})
        self.assertEqual(self._councils(), 0)
        refusals = self._refusals()
        self.assertEqual(len(refusals), 1)
        self.assertEqual(refusals[0]["normalized_reason"], "provenance_unresolved")
        self.assertEqual(refusals[0]["attempt"], 0)
        self.assertNotIn("council_id", refusals[0])

    def test_artifact_and_profile_blockers_end_to_end(self):
        """Round-4 item 2 / GPT round-3 required_changes[0]: the remaining
        internal_technical blockers, proven on the production path."""
        art = os.path.join(self.work, "a.md")
        with open(art, "w", encoding="utf-8") as fh:
            fh.write("artifact body\n")
        payload = self._run(["--artifact", art])
        self.assertEqual(payload.get("normalized_reason"), "policy_denial")
        self.assertIsNone(payload.get("council_id"))
        self.assertEqual(payload.get("attempts"), {})
        self.assertEqual(self._councils(), 0)
        recs = self._refusals()
        self.assertEqual(len(recs), 1)
        self.assertEqual(recs[0]["normalized_reason"], "policy_denial")
        self.assertEqual(recs[0]["attempt"], 0)
        self.assertEqual(recs[0]["dispatch_lane"], "internal_technical")

    def test_refusal_record_is_content_free(self):
        self._run()
        rec = self._refusals()[0]
        blob = json.dumps(rec)
        self.assertNotIn("packet", blob.lower().replace("packet.md", ""))
        self.assertIn(rec["normalized_reason"], cwdp.NORMALIZED_FAILURE_CLASSES)
        self.assertEqual(rec["dispatch_lane"], "internal_technical")


if __name__ == "__main__":
    unittest.main()
