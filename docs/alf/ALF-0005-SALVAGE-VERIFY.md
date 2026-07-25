# ALF-0005 pre-allocation dispatch eligibility -- protected verification

## Mechanical manifest (derived from committed state; nothing hand-authored)

repository        ClearWright
branch            operator/alf-dispatch-preallocation-salvage
commit            ad83361a3244e3b81a1f21337ff5cc2f71c224fe
parent            b6d346cccef1a5b922094f40a29b53cd3b948c39
tree              f05b76c421becedd94e3fa63c855ad673897904c
work item         message:msg-20260725T025421940761
CTA packet        alf-stab-cta-20260725 (IN_PROGRESS, BRANCH_CODE, OPERATOR-0001)
CTA lease expires 2026-07-26T02:57:18Z
dispatch lane     internal_technical
data sensitivity  internal_technical
round             round 2 (bounded correction round; convergence limit reached after this round)
full suite        1184 at 794350f; the round-2 correction adds 6 tests verified by focused and neighborhood suites, with the full suite re-run before merge tests OK, 1 pre-existing skip
ASCII status      0 character(s) replaced in the diff below
line endings      all changed files LF-only (crlf counts below)
tripwire status   no forbidden ClearWright path or config marker present

changed files (2):

  tests/test_preallocation_production.py      18990 bytes  crlf=0 nonascii=0  sha256 890eeb3e35cedd95762179f6cfacd004cea8ec3db1709c8c5a4c9d6e145f740b
  tools/clearwright_use_cw.py                 92026 bytes  crlf=0 nonascii=60  sha256 d90dab0c7eb519e4486be0828e34221bc7248b3e65b5c6122b246ae4483b8073

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

## Committed diff (b6d346c..ad83361)

```diff
diff --git a/tests/test_preallocation_production.py b/tests/test_preallocation_production.py
index e92e02c..60ff10b 100644
--- a/tests/test_preallocation_production.py
+++ b/tests/test_preallocation_production.py
@@ -245,6 +245,79 @@ class PreAllocationRefusalIntegrationTest(unittest.TestCase):
         self.assertEqual(payload.get("normalized_reason"), "tripwire_refusal")
         self.assertEqual(self._council_count(), 0)
 
+    def test_non_confusable_hit_category_is_also_refused(self):
+        """Codex round-1 blocking finding: the gate collapses ANY non-clear
+        classify() verdict into tripwire_refusal. That is SOUND, not
+        over-refusal, because egress_guard.authorize() raises
+        EgressBlocked("tripwire_hit") for any non-clear verdict over the FULL
+        outbound bytes with NO branching on finding category, and before the
+        sensitive-tier branch, so it applies on every lane. This pins that
+        behaviour for a category other than unicode_confusable."""
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
+        for fn in (_verdict, _raise):
+            eg.classify = fn
+            try:
+                code, out = self._run(self._plan("ordinary ascii packet\n"))
+            finally:
+                eg.classify = orig
+            payload = json.loads(out.strip().splitlines()[-1])
+            self.assertEqual(payload.get("normalized_reason"), "tripwire_refusal")
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
     def test_eligible_packet_still_reaches_council_creation(self):
         """The gate must not block a packet that would dispatch today."""
         called = {}
@@ -263,5 +336,83 @@ class PreAllocationRefusalIntegrationTest(unittest.TestCase):
         self.assertTrue(called.get("yes"))
 
 
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
+    def test_refusal_record_is_content_free(self):
+        self._run()
+        rec = self._refusals()[0]
+        blob = json.dumps(rec)
+        self.assertNotIn("packet", blob.lower().replace("packet.md", ""))
+        self.assertIn(rec["normalized_reason"], cwdp.NORMALIZED_FAILURE_CLASSES)
+        self.assertEqual(rec["dispatch_lane"], "internal_technical")
+
+
 if __name__ == "__main__":
     unittest.main()
diff --git a/tools/clearwright_use_cw.py b/tools/clearwright_use_cw.py
index 1ac7ffa..788f395 100644
--- a/tools/clearwright_use_cw.py
+++ b/tools/clearwright_use_cw.py
@@ -592,6 +592,26 @@ def _council_body(args, phase, root, stage):
         # bytes. A hit here PROVES a hit at send (never a false refusal); a clear
         # context does NOT prove the outbound bytes are clear. The egress guard
         # remains the complete authoritative check over the exact bytes.
+        #
+        # WHY ANY NON-CLEAR VERDICT IS TREATED AS AN UNCONDITIONAL BLOCK (proof):
+        # clearwright_egress_guard.authorize() computes final_scan() over the FULL
+        # outbound bytes and raises EgressBlocked("tripwire_hit") whenever the
+        # verdict is "hit", with NO branching on the finding CATEGORY and BEFORE
+        # the sensitive-tier branch -- so it applies on every lane. classify() and
+        # final_scan() share the identical _scan_text detector core and policy.
+        # Therefore ANY non-clear classification of the context implies an
+        # unconditional block at send, and refusing here cannot over-refuse. The
+        # normalized reason is reported as tripwire_refusal for every hit category
+        # because every hit category produces the same unconditional outcome.
+        #
+        # SUBSET PROPERTY (scope of the claim): the bytes scanned here are the
+        # SAME text later bound into the outbound composition -- stamp_context()
+        # records sha256 of exactly this context and run_round refuses a stale or
+        # missing stamp, so the dispatched packet provably contains these bytes.
+        # The claim is therefore limited and honest: a hit here implies a hit at
+        # send. It does NOT claim the converse, because the outbound bytes also
+        # include scaffold and derived components not available before a council
+        # exists. The guard remains the complete check over the exact bytes.
         _tripwire_clear = True
         if _pre_ctx_loaded:
             try:

```
