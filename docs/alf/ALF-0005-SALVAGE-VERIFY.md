# ALF-0005 pre-allocation dispatch eligibility -- protected verification

## Mechanical manifest (derived from committed state; nothing hand-authored)

repository        ClearWright
branch            operator/alf-dispatch-preallocation-salvage
commit            00cf2ea72e5327d1acd3dd6b7cfbe3ccfe49bfaf
parent            7d97a707fc8662f1f167199cf7aeb821b5441dd9
tree              2652d279d48354d14c5494ce7dff34501cf0306d
work item         message:msg-20260725T025421940761
CTA packet        alf-stab-cta-20260725 (IN_PROGRESS, BRANCH_CODE, OPERATOR-0001)
CTA lease expires 2026-07-26T02:57:18Z
dispatch lane     internal_technical
data sensitivity  internal_technical
round             round 5 (final authorized round under msg-20260725T134126713904)
full suite        1202 tests OK, 1 pre-existing skip
ASCII status      0 character(s) replaced in the diff below
line endings      all changed files LF-only (crlf counts below)
tripwire status   no forbidden ClearWright path or config marker present

changed files (4):

  tests/test_preallocation_production.py      30400 bytes  crlf=0 nonascii=0  sha256 0c6de583fdb6ec3d37bb3e67b0a8b8cc53095a7fd24827c5bacd1db7057aaa2d
  tools/clearwright_dispatch_preflight.py     12698 bytes  crlf=0 nonascii=0  sha256 9c9ca34c7436f538176911bd83baa425225febf84d4799c8534cf8b8c1f92c22
  tools/clearwright_review_council.py        101910 bytes  crlf=0 nonascii=51  sha256 8052fb595536c139feb1c4bb273a3a40cd94328202c0b32e4353b8f1be52841b
  tools/clearwright_use_cw.py                 92300 bytes  crlf=0 nonascii=60  sha256 eec18e5bd49859c6d51de3921f0c8773795e3b22343273837ec5143281b56166

## Scope of THIS patch

ACCEPTED SCOPE: the independently valid ALF-0005 correction only -- authoritative
pre-allocation dispatch eligibility computed from production preflight outputs and
enforced BEFORE council id or reviewer attempt allocation, with a durable
normalized refusal reason.

EXCLUDED SCOPE, explicitly NOT in this patch: ALF-0008 is NOT fixed. There is no
TRIAGED to PRIORITIZED transition, no operator-review surfacing, no run-membership
establishment, validation, repair or migration, no membership roster, no run-bound
lifecycle or cycle finalization, and NO change to the durable ALF data model.

WHY THE SPLIT: an earlier branch combined this correction with a run-membership
architecture. Protected review of that branch DID NOT PASS: nine substantive
rounds repeatedly found the same defect family, because run membership was
INFERRED from run-stamped finding revisions, so any guarded write could create the
fact its guard was validating. That architecture is deferred to separate governed
planning. This patch carries none of it.

## The change

BEFORE: dispatch_eligibility() was reachable only from run_round via
council["preallocation_signals"], a key no production path ever wrote. It always
received an empty map, so no deterministic blocker reached it -- zero
pre-allocation refusals across 1189 durable invocation-log entries. The check also
ran after a council id had already been allocated.

AFTER: the authoritative check runs in the production council path in
clearwright_use_cw.py, after _assemble_lineage (which produces the authoritative
lineage, candidate and bindings) and BEFORE create_council.

INVARIANT INTRODUCED: a deterministic dispatch blocker refuses before allocation,
consuming ZERO council ids and ZERO reviewer attempts, and persists a normalized,
content-free reason to the durable invocation log.

SAFETY PROPERTIES TO ATTACK:

1. The gate can only refuse EARLIER, never newly refuse. Every signal mirrors an
   EXISTING UNCONDITIONAL refusal: lane_authorized mirrors run_round's
   internal_technical refusal of artifacts and non-code profiles;
   composition_bound mirrors its refusal of a missing lineage graph or candidate;
   provenance_resolved mirrors its refusal of a RAW node without STANDARD
   provenance; tripwire_clear mirrors the guard's unconditional tripwire_hit block
   over the outbound bytes.
2. Provider readiness and credential presence are DELIBERATELY EXCLUDED. They are
   dynamic environmental conditions, not deterministic content properties, so
   refusing on them could newly deny a packet that would otherwise dispatch
   through an injected or differently-resolved adapter. A test pins their absence.
3. Lane-specific signals are OMITTED on lanes that do not perform those checks;
   dispatch_eligibility treats an absent signal as eligible, so the user lane
   cannot acquire an internal_technical-only blocker.
4. Nothing caller-supplied is authoritative. production_signals() takes explicit
   keyword FACTS computed from production preflight outputs. The pre-existing
   run_round check still reads council["preallocation_signals"], but
   dispatch_eligibility can only REFUSE and never authorize, so a planted field
   can only deny a dispatch, never enable one.
5. Tripwire scope is ONE-DIRECTIONAL by construction. Only the packet context is
   available before a council exists, and it is a SUBSET of the outbound bytes, so
   a context hit PROVES a hit at send (never a false refusal) while a clear
   context does NOT prove the outbound bytes are clear. The egress guard remains
   the complete authoritative check over the exact bytes at send.
6. ANY classification exception fails closed. Whether content is scanned depends
   on LOAD SUCCESS, never on a transformation such as strip(), which could
   otherwise erase tripwire-relevant characters and skip the scan.
7. resolve_lane() is the single source of truth shared with create_council, so the
   lane a packet is judged against cannot drift from the lane it dispatches on. It
   is fail-closed: anything not explicitly standard or internal_technical is
   sensitive on the user lane.
8. No existing egress, provenance, approved-root, composition, exact-byte,
   tripwire, provider-readiness or clearance control is weakened or bypassed.

## Prior reviewer findings carried into this patch

These were raised in protected review of the experimental branch and ACCEPTED;
each correction is present here:

- codex: provider_ready and auth_ok mirror dynamic environmental conditions and
  could newly refuse a dispatchable packet -> both REMOVED, absence pinned by test.
- gpt: only EgressBlocked was caught, so another classification failure would not
  fail closed -> ANY exception now fails closed.
- gpt: the scan decision depended on strip(), so content reducible to empty could
  bypass the scan entirely -> load success is now tracked separately.
- gpt: the packet claimed equivalence between the context scan and the guard's
  outbound-byte scan -> equivalence is no longer claimed; the one-directional
  relationship is stated in code and above.

This patch has NOT yet been verified as a unit. It is submitted for fresh review.

## Live evidence that the gate works

During protected review of the experimental branch this gate fired in production:
a dispatch was refused with normalized_reason "provenance_unresolved" before
allocating anything, because the launching shell was missing the guard's
local-configuration environment variable so the approved-root binding could not
resolve. The refusal was correct, and under the previous code it would have
consumed a council id and up to four reviewer attempts while reporting an opaque
reviewer_unavailable.

## Review questions

1. Can this gate refuse any packet that would have dispatched successfully before
   the change? Consider both lanes and the absent-signal default.
2. Can any caller- or attacker-controlled input reach production_signals(), or
   make dispatch_eligibility return eligible when a real deterministic blocker
   exists?
3. Does the refusal path provably consume zero council ids and zero reviewer
   attempts, and is the persisted reason durable and content-free?
4. Is the one-directional tripwire scope stated accurately, and is the residual
   completeness limit acceptable given the guard remains the complete check?
5. Does resolve_lane() change the lane recorded for any existing council?
6. Any HIGH or CRITICAL defect within the approved threat model that must block
   deployment? Phase 2 filesystem hardening (an attacker with filesystem write
   access to the queue root) is EXCLUDED by planning packet section 8.

## Committed diff (7d97a70..00cf2ea)

```diff
diff --git a/tests/test_preallocation_production.py b/tests/test_preallocation_production.py
new file mode 100644
index 0000000..ecc4851
--- /dev/null
+++ b/tests/test_preallocation_production.py
@@ -0,0 +1,631 @@
+"""ALF-0005 tests: authoritative pre-allocation dispatch eligibility.
+
+Covers production signal derivation, refusal BEFORE council-id/reviewer-attempt
+allocation, durability of the normalized reason, rejection of untrusted
+caller-supplied signals, and unchanged behaviour for eligible packets.
+"""
+import io
+import json
+import os
+import shutil
+import sys
+import tempfile
+import unittest
+from contextlib import redirect_stdout
+
+sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "tools"))
+import clearwright_dispatch_preflight as cwdp   # noqa: E402
+import clearwright_review_council as cwrc       # noqa: E402
+import clearwright_use_cw as ucw                # noqa: E402
+
+OK = dict(dispatch_lane="user", review_profile="code", artifact_count=0,
+          lineage_bound=True, raw_provenance_standard=True, tripwire_clear=True)
+
+# Constructed from its code point so this source file stays pure ASCII and the
+# character under test is unambiguous to any reader (including a reviewer reading
+# a sanitized packet). U+2014 EM DASH is inside the guard's confusable range.
+EM_DASH = "\u2014"
+# Whitespace that str.strip() removes (U+2028 LINE SEPARATOR and U+3000
+# IDEOGRAPHIC SPACE), built from code points so this file stays ASCII.
+WHITESPACE_ONLY = "\u2028" + "\u3000" + " \t"
+
+
+def sig(**over):
+    kw = dict(OK)
+    kw.update(over)
+    return cwdp.production_signals(**kw)
+
+
+def elig(**over):
+    return cwdp.dispatch_eligibility(sig(**over))
+
+
+class ProductionSignalDerivationTest(unittest.TestCase):
+    def test_fully_eligible_passes(self):
+        self.assertEqual(elig(), (True, None))
+
+    def test_tripwire_hit_refuses_on_every_lane(self):
+        for lane in ("user", "internal_technical"):
+            self.assertEqual(elig(dispatch_lane=lane, tripwire_clear=False),
+                             (False, "tripwire_refusal"))
+
+    def test_readiness_signals_are_deliberately_not_produced(self):
+        """Provider readiness and credential presence are DYNAMIC environmental
+        conditions, not deterministic content properties. Refusing on them could
+        newly deny a packet that would otherwise dispatch (e.g. through an
+        injected or differently-resolved adapter), which would break the
+        can-only-refuse-earlier invariant. They must never appear here."""
+        for lane in ("user", "internal_technical"):
+            s = sig(dispatch_lane=lane)
+            self.assertNotIn("provider_ready", s)
+            self.assertNotIn("auth_ok", s)
+        for name in ("provider_ready", "auth_ok"):
+            self.assertNotIn(name, cwdp.production_signals.__code__.co_varnames)
+
+    def test_its_lane_refuses_artifacts_and_non_code_profile(self):
+        self.assertEqual(elig(dispatch_lane="internal_technical", artifact_count=1),
+                         (False, "policy_denial"))
+        self.assertEqual(elig(dispatch_lane="internal_technical",
+                              review_profile="editorial"), (False, "policy_denial"))
+
+    def test_its_lane_refuses_unbound_composition_and_provenance(self):
+        self.assertEqual(elig(dispatch_lane="internal_technical",
+                              lineage_bound=False),
+                         (False, "composition_or_hash_mismatch"))
+        self.assertEqual(elig(dispatch_lane="internal_technical",
+                              raw_provenance_standard=False),
+                         (False, "provenance_unresolved"))
+
+    def test_its_only_signals_are_omitted_on_the_user_lane(self):
+        """The user lane must not acquire a blocker from a check it does not
+        perform, or this would refuse packets that dispatch fine today."""
+        s = sig(dispatch_lane="user", artifact_count=5, review_profile="editorial",
+                lineage_bound=False, raw_provenance_standard=False)
+        for k in ("lane_authorized", "composition_bound", "provenance_resolved"):
+            self.assertNotIn(k, s)
+        self.assertEqual(cwdp.dispatch_eligibility(s), (True, None))
+
+    def test_its_signals_present_on_the_its_lane(self):
+        s = sig(dispatch_lane="internal_technical")
+        for k in ("lane_authorized", "composition_bound", "provenance_resolved"):
+            self.assertIn(k, s)
+
+
+class UntrustedSignalTest(unittest.TestCase):
+    """Caller-supplied signals must never be able to ENABLE a dispatch."""
+
+    def test_eligibility_can_only_refuse_never_authorize(self):
+        # every attacker-friendly value still leaves a real blocker refusing
+        for planted in ({}, {"tripwire_clear": True}, {"provider_ready": True},
+                        {"lane_authorized": True}, {"auth_ok": "yes"}):
+            merged = dict(planted)
+            merged.update(sig(tripwire_clear=False))
+            self.assertEqual(cwdp.dispatch_eligibility(merged)[0], False)
+
+    def test_production_signals_ignores_any_caller_map(self):
+        """production_signals takes explicit keyword facts only; there is no
+        pathway for a council record field to reach it."""
+        self.assertNotIn("preallocation_signals",
+                         cwdp.production_signals.__code__.co_varnames)
+        s = sig(tripwire_clear=False)
+        self.assertFalse(s["tripwire_clear"])
+
+    def test_malformed_signal_values_fail_closed(self):
+        self.assertEqual(cwdp.dispatch_eligibility({"tripwire_clear": 0})[0], False)
+        self.assertEqual(cwdp.dispatch_eligibility({"tripwire_clear": ""})[0], False)
+        self.assertEqual(cwdp.dispatch_eligibility({"tripwire_clear": None})[0], False)
+
+
+class LaneSingleSourceOfTruthTest(unittest.TestCase):
+    """resolve_lane must agree with the lane create_council actually records, or
+    a packet could be judged against a different lane than it dispatches on."""
+
+    def setUp(self):
+        self.root = tempfile.mkdtemp(prefix="cw-lane-")
+
+    def tearDown(self):
+        shutil.rmtree(self.root, ignore_errors=True)
+
+    def test_lane_matches_created_council(self):
+        for declared in ("standard", "internal_technical", "sensitive", "", None,
+                         "SOMETHING-ELSE", "  Standard  "):
+            c = cwrc.create_council(self.root, thread_id="thr-x",
+                                    data_sensitivity=declared)
+            ds, lane = cwrc.resolve_lane(declared)
+            self.assertEqual((c["data_sensitivity"], c["dispatch_lane"]), (ds, lane))
+
+
+class PreAllocationRefusalIntegrationTest(unittest.TestCase):
+    """A deterministic blocker must refuse BEFORE any council id or reviewer
+    attempt is allocated, and must persist its normalized reason."""
+
+    def setUp(self):
+        self.root = tempfile.mkdtemp(prefix="cw-prealloc-")
+        self.work = tempfile.mkdtemp(prefix="cw-prealloc-w-")
+
+    def tearDown(self):
+        shutil.rmtree(self.root, ignore_errors=True)
+        shutil.rmtree(self.work, ignore_errors=True)
+
+    def _plan(self, text):
+        p = os.path.join(self.work, "packet.md")
+        with open(p, "w", encoding="utf-8") as fh:
+            fh.write(text)
+        return p
+
+    def _run(self, plan_path):
+        args = ucw.build_parser().parse_args(
+            ["council", self.root, "--thread-id", "thr-prealloc",
+             "--plan-file", plan_path, "--json"])
+        buf = io.StringIO()
+        with redirect_stdout(buf):
+            code = args.func(args)
+        return code, buf.getvalue()
+
+    def _council_count(self):
+        d = cwrc.councils_root(self.root)
+        return len(os.listdir(d)) if os.path.isdir(d) else 0
+
+    def test_confusable_refuses_before_allocation_and_is_durable(self):
+        # U+2014 EM DASH is inside the guard's unicode-confusable tripwire range,
+        # so this packet is guaranteed to be blocked at send.
+        code, out = self._run(self._plan("review this " + EM_DASH + " carefully\n"))
+        payload = json.loads(out.strip().splitlines()[-1])
+        self.assertEqual(payload.get("error"), "dispatch_ineligible")
+        self.assertEqual(payload.get("normalized_reason"), "tripwire_refusal")
+        self.assertIsNone(payload.get("council_id"))
+        self.assertEqual(payload.get("attempts"), {})
+        # ZERO council ids allocated
+        self.assertEqual(self._council_count(), 0)
+        # normalized reason persisted durably, with no reviewer attempt consumed
+        log = os.path.join(self.root, "invocation_log.jsonl")
+        self.assertTrue(os.path.exists(log))
+        with open(log, encoding="utf-8") as fh:
+            recs = [json.loads(l) for l in fh if l.strip()]
+        refusals = [r for r in recs
+                    if r.get("command") == "dispatch-refused-preallocation"]
+        self.assertEqual(len(refusals), 1)
+        self.assertEqual(refusals[0]["normalized_reason"], "tripwire_refusal")
+        self.assertEqual(refusals[0]["attempt"], 0)
+        # log_invocation drops null fields, so NO council id recorded is exactly
+        # the "zero council ids consumed" evidence.
+        self.assertNotIn("council_id", refusals[0])
+
+    def test_context_file_path_is_also_scanned(self):
+        """The scan must cover whichever content flag supplies the packet, not
+        just --plan-file."""
+        p = os.path.join(self.work, "ctx.md")
+        with open(p, "w", encoding="utf-8") as fh:
+            fh.write("context via the other flag " + EM_DASH + "\n")
+        args = ucw.build_parser().parse_args(
+            ["council", self.root, "--thread-id", "thr-ctx",
+             "--context-file", p, "--json"])
+        buf = io.StringIO()
+        with redirect_stdout(buf):
+            args.func(args)
+        payload = json.loads(buf.getvalue().strip().splitlines()[-1])
+        self.assertEqual(payload.get("normalized_reason"), "tripwire_refusal")
+        self.assertEqual(self._council_count(), 0)
+
+    def test_scan_decision_does_not_depend_on_a_content_transformation(self):
+        """Whether to scan must depend on LOAD SUCCESS, never on str.strip().
+        strip() removes Unicode whitespace and could erase tripwire-relevant
+        characters, letting a context bypass the pre-allocation scan entirely."""
+        seen = {}
+        import clearwright_egress_guard as eg
+        orig = eg.classify
+
+        def _spy(text, *a, **kw):
+            seen["text"] = text
+            return orig(text, *a, **kw)
+
+        eg.classify = _spy
+        try:
+            # content that str.strip() reduces to empty must STILL be classified
+            self._run(self._plan(WHITESPACE_ONLY))
+        finally:
+            eg.classify = orig
+        self.assertIn("text", seen)
+        self.assertNotEqual(seen["text"].strip(), seen["text"])
+
+    def test_any_classifier_failure_fails_closed(self):
+        """Not just EgressBlocked: ANY classification exception must refuse."""
+        import clearwright_egress_guard as eg
+
+        class Boom(Exception):
+            pass
+
+        orig = eg.classify
+        eg.classify = lambda *a, **kw: (_ for _ in ()).throw(Boom("scanner blew up"))
+        try:
+            code, out = self._run(self._plan("perfectly ordinary ascii packet\n"))
+        finally:
+            eg.classify = orig
+        payload = json.loads(out.strip().splitlines()[-1])
+        # a scanner EXCEPTION is an unresolved classifier, not a tripwire hit
+        self.assertEqual(payload.get("normalized_reason"), "classifier_unresolved")
+        self.assertEqual(self._council_count(), 0)
+
+    def test_non_confusable_hit_category_is_also_refused(self):
+        """A "hit" of ANY finding category is a tripwire refusal. That MIRRORS an
+        existing unconditional refusal: egress_guard.authorize() raises
+        EgressBlocked("tripwire_hit") on a hit verdict over the FULL outbound
+        bytes, with no branching on finding category and before the
+        sensitive-tier branch, so it applies on every lane.
+
+        Unknown verdicts do NOT take this path. They are classifier_unresolved
+        under a separate, intentional new fail-closed policy, which is never
+        described as a mirrored tripwire refusal."""
+        import clearwright_egress_guard as eg
+        orig = eg.classify
+        eg.classify = lambda text, *a, **kw: {
+            "findings": {"contextual_identity": 1}, "verdict": "hit",
+            "policy_version": "test", "policy_sha256": "x", "input_sha256": "y"}
+        try:
+            code, out = self._run(self._plan("ordinary ascii packet\n"))
+        finally:
+            eg.classify = orig
+        payload = json.loads(out.strip().splitlines()[-1])
+        self.assertEqual(payload.get("normalized_reason"), "tripwire_refusal")
+        self.assertEqual(self._council_count(), 0)
+
+    def test_exception_and_structured_hit_are_distinct_paths(self):
+        """Codex round-1 required_changes[1]: failing closed on an EXCEPTION and
+        refusing on a structured non-clear VERDICT are different code paths.
+        Both must refuse, and neither may silently dispatch."""
+        import clearwright_egress_guard as eg
+        seen = []
+        orig = eg.classify
+
+        def _verdict(text, *a, **kw):
+            seen.append("verdict")
+            return {"findings": {"unicode_confusable": 1}, "verdict": "hit",
+                    "policy_version": "t", "policy_sha256": "x", "input_sha256": "y"}
+
+        def _raise(text, *a, **kw):
+            seen.append("exception")
+            raise RuntimeError("scanner exploded")
+
+        # Under the three-way contract these report DIFFERENT normalized
+        # reasons: a structured "hit" is a tripwire refusal, while an exception
+        # is an unresolved classifier. Both refuse; neither dispatches.
+        for fn, expected in ((_verdict, "tripwire_refusal"),
+                             (_raise, "classifier_unresolved")):
+            eg.classify = fn
+            try:
+                code, out = self._run(self._plan("ordinary ascii packet\n"))
+            finally:
+                eg.classify = orig
+            payload = json.loads(out.strip().splitlines()[-1])
+            self.assertEqual(payload.get("normalized_reason"), expected)
+            self.assertEqual(self._council_count(), 0)
+        self.assertEqual(seen, ["verdict", "exception"])
+
+    def test_gate_and_create_council_use_the_same_sensitivity_source(self):
+        """GPT round-1 required_changes[1]: prove the pre-allocation path derives
+        its lane from EXACTLY the same source and value create_council is given,
+        so the judged lane cannot differ from the dispatched lane."""
+        captured = {}
+        orig_create = cwrc.create_council
+
+        def _spy(root, **kw):
+            captured["data_sensitivity"] = kw.get("data_sensitivity")
+            raise RuntimeError("stop before allocation")
+
+        cwrc.create_council = _spy
+        try:
+            with self.assertRaises(RuntimeError):
+                self._run(self._plan("plain ascii packet\n"))
+        finally:
+            cwrc.create_council = orig_create
+        # the value handed to create_council is the SAME function+argument the
+        # gate uses, for the same work item (None here -> fail-closed sensitive)
+        expected = ucw._data_sensitivity(self.root, None)
+        self.assertEqual(captured["data_sensitivity"], expected)
+        self.assertEqual(cwrc.resolve_lane(captured["data_sensitivity"])[1],
+                         cwrc.resolve_lane(expected)[1])
+
+    def test_classifier_verdict_matrix_is_exhaustive(self):
+        """Operator-authorized round-3 correction. The classifier contract is
+        exactly two KNOWN verdicts; everything else fails closed with a DISTINCT
+        reason and is never treated as authorization.
+
+        clear -> eligible | hit -> tripwire_refusal | anything else ->
+        classifier_unresolved. Every refusal consumes zero council ids."""
+        import clearwright_egress_guard as eg
+
+        def verdict(v):
+            return lambda text, *a, **kw: {"findings": {}, "verdict": v,
+                                           "policy_version": "t",
+                                           "policy_sha256": "x",
+                                           "input_sha256": "y"}
+
+        cases = [
+            ("hit", verdict("hit"), "tripwire_refusal"),
+            ("unknown", verdict("degraded"), "classifier_unresolved"),
+            ("empty", verdict(""), "classifier_unresolved"),
+            ("none", verdict(None), "classifier_unresolved"),
+            ("future", verdict("quarantined"), "classifier_unresolved"),
+            ("malformed-no-verdict-key",
+             lambda text, *a, **kw: {"findings": {}}, "classifier_unresolved"),
+            ("malformed-not-a-dict",
+             lambda text, *a, **kw: None, "classifier_unresolved"),
+            ("malformed-nonstring-verdict",
+             verdict(1), "classifier_unresolved"),
+            ("exception",
+             lambda text, *a, **kw: (_ for _ in ()).throw(RuntimeError("boom")),
+             "classifier_unresolved"),
+        ]
+        orig = eg.classify
+        for label, fn, expected in cases:
+            eg.classify = fn
+            try:
+                code, out = self._run(self._plan("ascii packet\n"))
+            finally:
+                eg.classify = orig
+            payload = json.loads(out.strip().splitlines()[-1])
+            self.assertEqual(payload.get("normalized_reason"), expected,
+                             "case %s" % label)
+            self.assertEqual(payload.get("error"), "dispatch_ineligible",
+                             "case %s" % label)
+            self.assertIsNone(payload.get("council_id"), "case %s" % label)
+            self.assertEqual(payload.get("attempts"), {}, "case %s" % label)
+            self.assertEqual(self._council_count(), 0, "case %s" % label)
+
+    def test_every_refusal_reason_is_a_known_normalized_class(self):
+        self.assertIn("classifier_unresolved", cwdp.NORMALIZED_FAILURE_CLASSES)
+        self.assertIn("tripwire_refusal", cwdp.NORMALIZED_FAILURE_CLASSES)
+
+    def test_unresolved_classifier_is_not_reported_as_a_tripwire(self):
+        """An unrecognised verdict must report its OWN reason: mislabelling it a
+        tripwire hit would hide a classifier contract change."""
+        sig = cwdp.production_signals(
+            dispatch_lane="user", review_profile="code", artifact_count=0,
+            lineage_bound=True, raw_provenance_standard=True,
+            tripwire_clear=True, classifier_resolved=False)
+        self.assertEqual(cwdp.dispatch_eligibility(sig),
+                         (False, "classifier_unresolved"))
+        # and it wins over a simultaneous tripwire failure, by check ordering
+        sig2 = cwdp.production_signals(
+            dispatch_lane="user", review_profile="code", artifact_count=0,
+            lineage_bound=True, raw_provenance_standard=True,
+            tripwire_clear=False, classifier_resolved=False)
+        self.assertEqual(cwdp.dispatch_eligibility(sig2),
+                         (False, "classifier_unresolved"))
+
+    def test_clear_verdict_still_reaches_dispatch_unchanged(self):
+        """The eligible path must be untouched by the three-way branch."""
+        sig = cwdp.production_signals(
+            dispatch_lane="user", review_profile="code", artifact_count=0,
+            lineage_bound=True, raw_provenance_standard=True,
+            tripwire_clear=True, classifier_resolved=True)
+        self.assertEqual(cwdp.dispatch_eligibility(sig), (True, None))
+
+    def test_caller_input_cannot_convert_a_refusal_into_authorization(self):
+        """No caller-controlled value may turn any refusal into an allow."""
+        for planted in ({}, {"classifier_resolved": True},
+                        {"tripwire_clear": True}, {"lane_authorized": True},
+                        {"classifier_resolved": "yes"}, {"tripwire_clear": 1}):
+            for real in (cwdp.production_signals(
+                             dispatch_lane="user", review_profile="code",
+                             artifact_count=0, lineage_bound=True,
+                             raw_provenance_standard=True, tripwire_clear=True,
+                             classifier_resolved=False),
+                         cwdp.production_signals(
+                             dispatch_lane="user", review_profile="code",
+                             artifact_count=0, lineage_bound=True,
+                             raw_provenance_standard=True, tripwire_clear=False,
+                             classifier_resolved=True)):
+                merged = dict(planted)
+                merged.update(real)          # authoritative facts win
+                self.assertFalse(cwdp.dispatch_eligibility(merged)[0])
+
+    def test_eligible_packet_still_reaches_council_creation(self):
+        """The gate must not block a packet that would dispatch today."""
+        called = {}
+
+        def _boom(*a, **kw):
+            called["yes"] = True
+            raise RuntimeError("reached create_council")
+
+        orig = cwrc.create_council
+        cwrc.create_council = _boom
+        try:
+            with self.assertRaises(RuntimeError):
+                self._run(self._plan("plain ascii packet, no tripwire\n"))
+        finally:
+            cwrc.create_council = orig
+        self.assertTrue(called.get("yes"))
+
+
+class StrictFactValidationTest(unittest.TestCase):
+    """Operator-authorized round-4 item 3: no permissive coercion. An
+    authoritative fact is accepted ONLY as an exact bool (or exact non-negative
+    int for artifact_count). A malformed or truthy non-boolean value must fail
+    closed as classifier_unresolved and must NEVER become allow-shaped."""
+
+    MALFORMED = ("false", "yes", "true", "", 1, 0, 1.0, [], {}, None, object())
+
+    def test_truthy_non_boolean_never_authorizes(self):
+        for bad in self.MALFORMED:
+            for key in ("tripwire_clear", "classifier_resolved",
+                        "lineage_bound", "raw_provenance_standard"):
+                s = sig(**{key: bad})
+                ok, reason = cwdp.dispatch_eligibility(s)
+                self.assertFalse(ok, "%s=%r must not authorize" % (key, bad))
+                self.assertEqual(reason, "classifier_unresolved",
+                                 "%s=%r" % (key, bad))
+
+    def test_malformed_artifact_count_fails_closed(self):
+        for bad in ("0", 1.0, -1, True, False, None, [], object()):
+            ok, reason = cwdp.dispatch_eligibility(sig(artifact_count=bad))
+            self.assertFalse(ok, "artifact_count=%r must not authorize" % (bad,))
+            self.assertEqual(reason, "classifier_unresolved")
+
+    def test_exact_booleans_still_behave_normally(self):
+        self.assertEqual(cwdp.dispatch_eligibility(sig()), (True, None))
+        self.assertEqual(cwdp.dispatch_eligibility(sig(tripwire_clear=False)),
+                         (False, "tripwire_refusal"))
+        self.assertEqual(cwdp.dispatch_eligibility(sig(classifier_resolved=False)),
+                         (False, "classifier_unresolved"))
+
+    def test_malformed_fact_emits_no_allow_shaped_signal(self):
+        s = sig(tripwire_clear="yes")
+        self.assertIs(s["classifier_resolved"], False)
+        self.assertIs(s["tripwire_clear"], False)
+        for v in s.values():
+            self.assertIsInstance(v, bool)
+
+
+class ItsLaneBlockerEndToEndTest(unittest.TestCase):
+    """GPT round-1 required_changes[0]: prove the FULL production refusal path
+    (no council directory entry, no reviewer attempt, one durable normalized
+    record) for each internal_technical blocker, not only for tripwire."""
+
+    def setUp(self):
+        self.root = tempfile.mkdtemp(prefix="cw-its-")
+        self.work = tempfile.mkdtemp(prefix="cw-its-w-")
+        self.mid = "msg-20260725T000000000000"
+        env = {"envelope_version": 1, "task_kind": "governed",
+               "data_sensitivity": "internal_technical", "review_profile": "code",
+               "verification_required": True, "request": "r",
+               "approved_scope": "s", "intended_actions": [],
+               "excluded_actions": [], "operator_authority_source": "o"}
+        ucw._persist_envelope(self.root, self.mid, env,
+                              {"classification": "governed",
+                               "data_sensitivity": "internal_technical"})
+
+    def tearDown(self):
+        shutil.rmtree(self.root, ignore_errors=True)
+        shutil.rmtree(self.work, ignore_errors=True)
+
+    def _plan(self, text="ascii packet for the internal lane\n"):
+        p = os.path.join(self.work, "packet.md")
+        with open(p, "w", encoding="utf-8") as fh:
+            fh.write(text)
+        return p
+
+    def _run(self, extra=()):
+        argv = ["council", self.root, "--thread-id", "thr-its",
+                "--work-item-id", "message:" + self.mid,
+                "--plan-file", self._plan(), "--json"] + list(extra)
+        args = ucw.build_parser().parse_args(argv)
+        buf = io.StringIO()
+        with redirect_stdout(buf):
+            args.func(args)
+        return json.loads(buf.getvalue().strip().splitlines()[-1])
+
+    def _councils(self):
+        d = cwrc.councils_root(self.root)
+        return len(os.listdir(d)) if os.path.isdir(d) else 0
+
+    def _refusals(self):
+        log = os.path.join(self.root, "invocation_log.jsonl")
+        if not os.path.exists(log):
+            return []
+        with open(log, encoding="utf-8") as fh:
+            return [json.loads(l) for l in fh if l.strip()
+                    and json.loads(l).get("command") == "dispatch-refused-preallocation"]
+
+    def test_lane_resolves_internal_technical(self):
+        self.assertEqual(
+            cwrc.resolve_lane(ucw._data_sensitivity(self.root, "message:" + self.mid))[1],
+            "internal_technical")
+
+    def test_unresolved_provenance_refuses_before_allocation(self):
+        """The packet lives outside any approved repository, so its RAW node
+        cannot carry STANDARD provenance on the internal_technical lane."""
+        payload = self._run()
+        self.assertEqual(payload.get("normalized_reason"), "provenance_unresolved")
+        self.assertEqual(payload.get("council_id"), None)
+        self.assertEqual(payload.get("attempts"), {})
+        self.assertEqual(self._councils(), 0)
+        refusals = self._refusals()
+        self.assertEqual(len(refusals), 1)
+        self.assertEqual(refusals[0]["normalized_reason"], "provenance_unresolved")
+        self.assertEqual(refusals[0]["attempt"], 0)
+        self.assertNotIn("council_id", refusals[0])
+
+    def test_artifact_and_profile_blockers_end_to_end(self):
+        """Round-4 item 2 / GPT round-3 required_changes[0]: the remaining
+        internal_technical blockers, proven on the production path."""
+        art = os.path.join(self.work, "a.md")
+        with open(art, "w", encoding="utf-8") as fh:
+            fh.write("artifact body\n")
+        payload = self._run(["--artifact", art])
+        self.assertEqual(payload.get("normalized_reason"), "policy_denial")
+        self.assertIsNone(payload.get("council_id"))
+        self.assertEqual(payload.get("attempts"), {})
+        self.assertEqual(self._councils(), 0)
+        recs = self._refusals()
+        self.assertEqual(len(recs), 1)
+        self.assertEqual(recs[0]["normalized_reason"], "policy_denial")
+        self.assertEqual(recs[0]["attempt"], 0)
+        self.assertEqual(recs[0]["dispatch_lane"], "internal_technical")
+
+    def test_non_code_profile_blocker_end_to_end(self):
+        """Round-5 item 3: a non-code review profile on the internal_technical
+        lane refuses before allocation on the PRODUCTION path."""
+        mid = "msg-20260725T000000000001"
+        env = {"envelope_version": 1, "task_kind": "governed",
+               "data_sensitivity": "internal_technical",
+               "review_profile": "editorial", "verification_required": True,
+               "request": "r", "approved_scope": "s", "intended_actions": [],
+               "excluded_actions": [], "operator_authority_source": "o"}
+        # _review_profile reads the AUDIT block, not the top-level envelope
+        ucw._persist_envelope(self.root, mid, env,
+                              {"classification": "governed",
+                               "data_sensitivity": "internal_technical",
+                               "review_profile": "editorial"})
+        self.assertEqual(ucw._review_profile(self.root, "message:" + mid),
+                         "editorial")
+        args = ucw.build_parser().parse_args(
+            ["council", self.root, "--thread-id", "thr-its2",
+             "--work-item-id", "message:" + mid,
+             "--plan-file", self._plan(), "--json"])
+        buf = io.StringIO()
+        with redirect_stdout(buf):
+            args.func(args)
+        payload = json.loads(buf.getvalue().strip().splitlines()[-1])
+        self.assertEqual(payload.get("normalized_reason"), "policy_denial")
+        self.assertIsNone(payload.get("council_id"))
+        self.assertEqual(payload.get("attempts"), {})
+        self.assertEqual(self._councils(), 0)
+        recs = self._refusals()
+        self.assertEqual(len(recs), 1)
+        self.assertEqual(recs[0]["normalized_reason"], "policy_denial")
+        self.assertEqual(recs[0]["attempt"], 0)
+        self.assertNotIn("council_id", recs[0])
+
+    def test_missing_composition_blocker_end_to_end(self):
+        """Round-5 item 3: a missing lineage graph or candidate refuses before
+        allocation on the PRODUCTION path with composition_or_hash_mismatch."""
+        orig = ucw._assemble_lineage
+        ucw._assemble_lineage = lambda args: ([], None, [])
+        try:
+            payload = self._run()
+        finally:
+            ucw._assemble_lineage = orig
+        self.assertEqual(payload.get("normalized_reason"),
+                         "composition_or_hash_mismatch")
+        self.assertIsNone(payload.get("council_id"))
+        self.assertEqual(payload.get("attempts"), {})
+        self.assertEqual(self._councils(), 0)
+        recs = self._refusals()
+        self.assertEqual(len(recs), 1)
+        self.assertEqual(recs[0]["normalized_reason"],
+                         "composition_or_hash_mismatch")
+        self.assertEqual(recs[0]["attempt"], 0)
+        self.assertNotIn("council_id", recs[0])
+
+    def test_refusal_record_is_content_free(self):
+        self._run()
+        rec = self._refusals()[0]
+        blob = json.dumps(rec)
+        self.assertNotIn("packet", blob.lower().replace("packet.md", ""))
+        self.assertIn(rec["normalized_reason"], cwdp.NORMALIZED_FAILURE_CLASSES)
+        self.assertEqual(rec["dispatch_lane"], "internal_technical")
+
+
+if __name__ == "__main__":
+    unittest.main()
diff --git a/tools/clearwright_dispatch_preflight.py b/tools/clearwright_dispatch_preflight.py
index 848ff9b..8273df9 100644
--- a/tools/clearwright_dispatch_preflight.py
+++ b/tools/clearwright_dispatch_preflight.py
@@ -10,11 +10,13 @@ These are ADDITIVE, fail-closed-preserving helpers used by the council engine:
      secrets or raw provider bodies - only one of NORMALIZED_FAILURE_CLASSES.
 
   B. dispatch_eligibility(): a DETERMINISTIC pre-allocation check over signals
-     that are known before any adapter call. It can only REFUSE earlier and more
-     informatively than the downstream egress guard - it never authorizes a
-     dispatch the guard would block, so no fail-closed control is weakened. When
-     it refuses, the caller records the normalized reason and consumes NO council
-     id or reviewer attempt.
+     that are known before any adapter call. It never authorizes a dispatch the
+     guard would block, so no fail-closed control is weakened. For MIRRORED
+     signals it refuses earlier than the guard and can never newly deny; the
+     separate classifier_unresolved policy is an intentional NEW fail-closed
+     denial accepted by the operator (see production_signals). When it refuses,
+     the caller records the normalized reason and consumes NO council id or
+     reviewer attempt.
 
 Pure module: no imports from the council engine (avoids a cycle); the engine
 imports these.
@@ -24,7 +26,8 @@ NORMALIZED_FAILURE_CLASSES = (
     "policy_denial", "repo_not_approved", "provenance_unresolved",
     "sensitive_content_prohibited", "tripwire_refusal",
     "composition_or_hash_mismatch", "provider_unavailable", "auth_failure",
-    "rate_limit", "timeout", "malformed_response", "adapter_failure", "unknown",
+    "rate_limit", "timeout", "malformed_response", "adapter_failure",
+    "classifier_unresolved", "unknown",
 )
 
 # Ordered (specific -> general) keyword rules over the safe signal text. Each rule
@@ -103,6 +106,9 @@ _ELIGIBILITY_CHECKS = (
     ("sensitive_prohibited", False, "sensitive_content_prohibited"),
     ("composition_bound", True, "composition_or_hash_mismatch"),
     ("exact_bytes_ok", True, "composition_or_hash_mismatch"),
+    # ordered BEFORE tripwire_clear: an unresolved classifier must report its own
+    # distinct reason and must never be reported as a tripwire hit.
+    ("classifier_resolved", True, "classifier_unresolved"),
     ("tripwire_clear", True, "tripwire_refusal"),
     ("provider_ready", True, "provider_unavailable"),
     ("auth_ok", True, "auth_failure"),
@@ -122,6 +128,108 @@ def dispatch_eligibility(signals):
     return (True, None)
 
 
+def production_signals(*, dispatch_lane, review_profile, artifact_count,
+                       lineage_bound, raw_provenance_standard, tripwire_clear,
+                       classifier_resolved=True):
+    """Derive AUTHORITATIVE pre-allocation signals from production preflight
+    outputs. Callers pass already-computed facts; this function invents nothing.
+
+    TWO CLASSES OF SIGNAL, deliberately not conflated:
+
+    (A) MIRRORED signals. Each of these mirrors an EXISTING UNCONDITIONAL,
+        DETERMINISTIC refusal that the engine or the egress guard already
+        performs, so refusing on them can only refuse EARLIER and can never deny
+        a packet that would otherwise have dispatched:
+
+      - lane_authorized      mirrors run_round's internal_technical refusals of
+                             artifacts and of any non-code review profile;
+      - composition_bound    mirrors run_round's refusal of a missing lineage
+                             graph or candidate on that lane;
+      - provenance_resolved  mirrors run_round's refusal of a RAW node without
+                             STANDARD provenance on that lane;
+      - tripwire_clear       mirrors the guard's unconditional tripwire_hit block
+                             for a "hit" verdict. authorize() computes
+                             final_scan() over the FULL outbound bytes and raises
+                             EgressBlocked("tripwire_hit") on a hit with NO
+                             branching on finding category and BEFORE the
+                             sensitive-tier branch, and classify() shares
+                             final_scan()'s _scan_text detector core.
+
+    (B) A NEW FAIL-CLOSED POLICY, explicitly accepted by the operator and NOT a
+        mirrored refusal:
+
+      - classifier_resolved  the classifier returned a verdict this gate
+                             UNDERSTANDS. Exactly two verdicts are known,
+                             "clear" and "hit". Anything else -- unresolved,
+                             malformed, missing, non-string, non-dict, an
+                             exception, or a verdict added in future -- sets this
+                             False and refuses with the DISTINCT reason
+                             classifier_unresolved.
+
+        This CAN newly refuse: the gate does not claim the send-time guard would
+        also have blocked an unrecognised verdict. It is an intentional new
+        pre-allocation denial, chosen because an unknown verdict must never be
+        treated as authorization. It is deliberately NOT described as a mirrored
+        tripwire refusal, and it reports its own reason so that a classifier
+        contract change cannot hide behind a proven-looking one.
+
+    Accordingly the can-only-refuse-earlier property is scoped to class (A) and
+    is NOT claimed for class (B).
+
+    STRICT FACTS: every boolean fact must be an exact bool and artifact_count an
+    exact non-negative int. There is NO permissive coercion, so a truthy
+    non-boolean such as "false", "yes", 1 or an arbitrary object cannot become an
+    allow-shaped signal; any malformed fact fails closed as classifier_unresolved.
+
+    DELIBERATELY EXCLUDED: provider readiness and credential presence. Those are
+    DYNAMIC ENVIRONMENTAL conditions, not deterministic content properties: a
+    dispatch may legitimately proceed through an injected or differently-resolved
+    adapter, so refusing on them could newly deny a packet that would otherwise
+    dispatch. That would break the invariant above. Readiness is already gated by
+    the start-time preflight, and a genuinely absent provider still surfaces as a
+    normal reviewer_unavailable outcome.
+
+    TRIPWIRE SCOPE, narrowed to what is actually proven: the caller can only
+    scan the packet CONTEXT it loaded, because the complete outbound byte set is
+    not assembled until after a council exists. This module therefore claims only
+    that a "hit" on that loaded content implies the guard would block at send,
+    which follows from the shared detector core and the unconditional raise
+    above. It does NOT claim that the scanned bytes are byte-identical to, or
+    provably contained in, the final outbound packet: that relationship is the
+    intended construction but is not verified here or by the current tests. The
+    egress guard remains the complete and authoritative check over the exact
+    outbound bytes at send.
+
+    The internal_technical-only signals are OMITTED on other lanes. An absent
+    signal is treated as eligible by dispatch_eligibility, so a lane that does
+    not perform a given check never acquires a new blocker from it.
+    """
+    # STRICT fact validation (no permissive coercion). An authoritative signal is
+    # accepted ONLY as an exact bool; a malformed or truthy non-boolean value is
+    # an unresolved state, never allow-shaped authorization. Note bool is a
+    # subclass of int, so artifact_count is checked for exact int-ness too.
+    facts = {"lineage_bound": lineage_bound,
+             "raw_provenance_standard": raw_provenance_standard,
+             "tripwire_clear": tripwire_clear,
+             "classifier_resolved": classifier_resolved}
+    malformed = [k for k, v in facts.items() if v is not True and v is not False]
+    if (type(artifact_count) is not int) or artifact_count < 0:
+        malformed.append("artifact_count")
+    if malformed:
+        # Fail closed with the DISTINCT unresolved reason. tripwire_clear is also
+        # set refusing so the outcome holds even if the check order changed.
+        return {"classifier_resolved": False, "tripwire_clear": False}
+
+    signals = {"tripwire_clear": tripwire_clear,
+               "classifier_resolved": classifier_resolved}
+    if dispatch_lane == "internal_technical":
+        signals["lane_authorized"] = (artifact_count == 0
+                                      and review_profile == "code")
+        signals["composition_bound"] = lineage_bound
+        signals["provenance_resolved"] = raw_provenance_standard
+    return signals
+
+
 def refused_dispatch_record(*, phase, dispatch_lane, normalized_reason, detail=""):
     """A safe, durable record for a pre-allocation refusal - no council id and no
     reviewer attempt were consumed. Content-free beyond the normalized reason and
diff --git a/tools/clearwright_review_council.py b/tools/clearwright_review_council.py
index 021c211..b2f8547 100644
--- a/tools/clearwright_review_council.py
+++ b/tools/clearwright_review_council.py
@@ -327,6 +327,23 @@ def _guidance_header(review_profile, round_no):
            "=== End review guidance ===\n\n"
 
 
+def resolve_lane(data_sensitivity):
+    """Fail-closed content-class -> (data_sensitivity, dispatch_lane) mapping.
+
+    SINGLE source of truth, shared by create_council and the pre-allocation
+    eligibility check, so the lane a packet is judged against can never drift
+    from the lane it is dispatched on. Anything other than an explicit
+    "standard" or "internal_technical" is "sensitive" and dispatches on the
+    "user" lane.
+    """
+    _ds = str(data_sensitivity or "").strip().lower()
+    if _ds == "standard":
+        return "standard", "user"
+    if _ds == "internal_technical":
+        return "internal_technical", "internal_technical"
+    return "sensitive", "user"
+
+
 def create_council(root, *, thread_id, work_item_id=None, packet_id=None,
                    phase="plan", min_rounds=DEFAULT_MIN_ROUNDS,
                    max_rounds=DEFAULT_MAX_ROUNDS, model=None, council_id=None,
@@ -344,13 +361,7 @@ def create_council(root, *, thread_id, work_item_id=None, packet_id=None,
     # other class dispatches in the "user" lane and can NEVER carry ITS-resolved
     # content. The lane only ENABLES ITS for clean technical ancestry; it never
     # relabels SENSITIVE ancestry (the guard re-enforces at send).
-    _ds = str(data_sensitivity or "").strip().lower()
-    if _ds == "standard":
-        data_sensitivity, dispatch_lane = "standard", "user"
-    elif _ds == "internal_technical":
-        data_sensitivity, dispatch_lane = "internal_technical", "internal_technical"
-    else:
-        data_sensitivity, dispatch_lane = "sensitive", "user"
+    data_sensitivity, dispatch_lane = resolve_lane(data_sensitivity)
     council_id = council_id or new_council_id()
     council = {
         "council_id": council_id,
diff --git a/tools/clearwright_use_cw.py b/tools/clearwright_use_cw.py
index 21ef38c..4bbd420 100644
--- a/tools/clearwright_use_cw.py
+++ b/tools/clearwright_use_cw.py
@@ -560,6 +560,97 @@ def _council_body(args, phase, root, stage):
             return _emit({"ok": False, "command": "council",
                           "error": "lineage_assembly_failed",
                           "reason": exc.reason}, EXIT_HARD_GATE, args.json)
+        # ALF-0005: AUTHORITATIVE pre-allocation dispatch eligibility.
+        #
+        # Signals are computed HERE from production preflight outputs -- the
+        # lineage just assembled above, the lane resolved through the SAME single
+        # source of truth create_council uses, the registered artifacts, and the
+        # readiness preflight. Nothing caller-supplied is treated as
+        # authoritative: council["preallocation_signals"] is never read on this
+        # path, and the second check inside run_round can only ADD a refusal
+        # (dispatch_eligibility never authorizes), so a planted field cannot
+        # enable a dispatch. The egress guard still independently re-enforces
+        # every rule over the exact outbound bytes at send.
+        #
+        # A deterministic blocker refuses BEFORE create_council, so it consumes
+        # ZERO council ids and ZERO reviewer attempts, and its normalized reason
+        # is persisted to the durable invocation log.
+        import clearwright_dispatch_preflight as cwdp
+        _ds_class, _lane = cwrc.resolve_lane(_data_sensitivity(root, args.work_item_id))
+        # Track LOAD SUCCESS separately from the content's shape. Whether to scan
+        # must never depend on a TRANSFORMATION of the content: str.strip()
+        # removes Unicode whitespace and could erase tripwire-relevant characters,
+        # letting a context bypass the scan entirely.
+        try:
+            _pre_ctx = _load(args.prompt, args.context_file or args.plan_file)
+            _pre_ctx_loaded = True
+        except Exception:
+            # absent/unreadable context is handled by the existing check below
+            _pre_ctx, _pre_ctx_loaded = "", False
+        # TWO DISTINCT JUSTIFICATIONS, deliberately not conflated:
+        #
+        # (a) "hit" -> tripwire_refusal MIRRORS an existing unconditional refusal.
+        #     clearwright_egress_guard.authorize() computes final_scan() over the
+        #     FULL outbound bytes and raises EgressBlocked("tripwire_hit") on a
+        #     hit verdict, with NO branching on finding CATEGORY and BEFORE the
+        #     sensitive-tier branch, so it applies on every lane; classify() and
+        #     final_scan() share the identical _scan_text detector core and
+        #     policy. Refusing here for a hit therefore cannot newly deny.
+        #
+        # (b) any OTHER verdict -> classifier_unresolved is an INTENTIONAL NEW
+        #     FAIL-CLOSED PRE-ALLOCATION POLICY, explicitly accepted by the
+        #     operator. It is NOT a mirrored tripwire refusal and is deliberately
+        #     not described as one. It CAN newly refuse, because the gate does
+        #     not claim the send-time guard would also have blocked an
+        #     unrecognised verdict. That is the point: an unknown verdict must
+        #     never be treated as authorization, and reporting its own distinct
+        #     reason keeps a classifier contract change from hiding behind a
+        #     proven-looking one.
+        #
+        # SCOPE OF THE SCANNED BYTES, narrowed to what is proven: only the
+        # context loaded here can be scanned, because the complete outbound byte
+        # set is not assembled until after a council exists. Nothing below binds
+        # or compares these bytes to the context later stamped by stamp_context,
+        # so no identity or containment relationship with the final outbound
+        # packet is claimed. That relationship is the intended construction, not
+        # a verified invariant. The egress guard remains the complete and
+        # authoritative check over the exact outbound bytes at send.
+        _tripwire_clear = True
+        _classifier_resolved = True
+        if _pre_ctx_loaded:
+            try:
+                _verdict = (_egress.classify(_pre_ctx) or {}).get("verdict")
+            except Exception:
+                _verdict = None
+            if _verdict == "clear":
+                _tripwire_clear = True
+            elif _verdict == "hit":
+                _tripwire_clear = False
+            else:
+                _classifier_resolved = False
+        _raw_provenance_ok = all(
+            (_r.get("provenance") or {}).get("class") in _egress._STANDARD_PROVENANCE
+            for _r in (lineage_records or [])
+            if _r.get("classification") == _egress.CLASS_RAW)
+        _elig_ok, _elig_reason = cwdp.dispatch_eligibility(cwdp.production_signals(
+            dispatch_lane=_lane, review_profile=profile,
+            artifact_count=(len(getattr(args, "artifact", None) or [])
+                            + len(getattr(args, "artifact_id", None) or [])),
+            lineage_bound=bool(lineage_records) and _cand is not None,
+            raw_provenance_standard=_raw_provenance_ok,
+            tripwire_clear=_tripwire_clear,
+            classifier_resolved=_classifier_resolved))
+        if not _elig_ok:
+            cwrc.log_invocation(root, cwdp.refused_dispatch_record(
+                phase=phase, dispatch_lane=_lane, normalized_reason=_elig_reason,
+                detail="authoritative pre-allocation eligibility"))
+            return _emit({"ok": False, "command": "council",
+                          "error": "dispatch_ineligible", "outcome": "hard_gate",
+                          "hard_gate": True, "council_id": None, "attempts": {},
+                          "normalized_reason": _elig_reason,
+                          "reason": "deterministic dispatch blocker refused before "
+                                    "allocation: " + _elig_reason},
+                         EXIT_HARD_GATE, args.json)
         try:
             council = cwrc.create_council(
                 root, thread_id=args.thread_id, work_item_id=args.work_item_id,

```
