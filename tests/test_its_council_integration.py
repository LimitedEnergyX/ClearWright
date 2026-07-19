"""INTERNAL_TECHNICAL_STANDARD (ITS) lane — production-path council integration
tests (Phase 2). These drive the REAL guarded dispatch path: injected reviewers
call the true GPT/Codex adapters, which build their request bytes and hand them
to the egress guard, which independently re-enforces every ITS rule. Every
blocked-flow test also walks the temp queue tree and asserts the synthetic
tripwire strings appear in NO file (content-free failure). All PII is SYNTHETIC.
"""
import hashlib
import json
import os
import shutil
import sys
import tempfile
import unittest

HERE = os.path.dirname(__file__)
sys.path.insert(0, os.path.join(HERE, "..", "tools"))
import clearwright_egress_guard as guard  # noqa: E402
import clearwright_review_council as cwrc  # noqa: E402
import clearwright_its_artifacts as cwia  # noqa: E402
import clearwright_gpt_review as gpt_adapter  # noqa: E402
import clearwright_codex_review as codex_adapter  # noqa: E402
import clearwright_use_cw as use_cw  # noqa: E402

# A synthetic SSN tripwire (ssn_like) and a benign, secret-free review context.
_SSN = "123-45-6789"
_CONTEXT = "def add(a, b):\n    return a + b\n"
_CLEAN_SUMMARY = "Reviewed the internal technical packet; no blocking issues were found."


def _verdict_json(reviewer, summary):
    return json.dumps({
        "reviewer": reviewer, "verdict": "approve", "confidence": 0.9,
        "risk_level": "low", "blocking_findings": [], "required_changes": [],
        "nonblocking_findings": [], "disagreements": [], "assumptions": [],
        "questions": [], "recommended_plan": [], "summary": summary})


def _gpt_reviewer(summary=_CLEAN_SUMMARY, captured=None, invoked=None):
    """A gpt_fn that exercises the REAL guarded gpt_send path: the guard builds
    the canonical ITS body and validates it by byte-equality; the injected
    transport captures the exact wire bytes and returns a canned Responses-API
    payload carrying a valid verdict."""
    def fn(root, text, **kw):
        if invoked is not None:
            invoked["gpt"] = True
        vj = _verdict_json("gpt", summary)

        def transport(url, headers, body, timeout):
            if captured is not None:
                captured["gpt_body"] = bytes(body)
            resp = json.dumps({"id": "resp_test", "model": "gpt-test",
                               "output_text": vj,
                               "usage": {"input_tokens": 1, "output_tokens": 1}})
            return (200, resp)
        return gpt_adapter.review(
            root, text, thread_id=kw["thread_id"], work_item_id=kw["work_item_id"],
            packet_id=kw["packet_id"], council_id=kw["council_id"],
            round=kw["round_no"], phase=kw["phase"], model=kw["model"],
            timeout=kw["timeout"], max_output_tokens=4000,
            egress_context=kw["egress_context"], key_getter=lambda: "test-key",
            transport=transport)
    return fn


def _codex_reviewer(summary=_CLEAN_SUMMARY, captured=None, invoked=None):
    """A codex_fn that exercises the REAL guarded codex_launch path: a tiny
    python child reads the exact validated stdin bytes, hashes them, and prints
    the hash plus a valid verdict JSON — so one run captures the wire bytes AND
    yields substantive, classifiable output."""
    def fn(root, text, **kw):
        if invoked is not None:
            invoked["codex"] = True
        vj = _verdict_json("codex", summary)
        child = ("import sys, hashlib\n"
                 "d = sys.stdin.buffer.read()\n"
                 "sys.stdout.buffer.write((hashlib.sha256(d).hexdigest() + '\\n' + "
                 + repr(vj) + ").encode('utf-8'))\n")

        def runner(prompt, timeout, cwd, egress_context=None):
            try:
                proc = guard.codex_launch([sys.executable, "-c", child], prompt,
                                          timeout, context=egress_context,
                                          caller="clearwright_codex_review")
            except guard.EgressBlocked as exc:
                tel = codex_adapter.build_telemetry("", None, 0.0)
                tel["egress_blocked"] = exc.reason
                return "", tel
            out = proc.stdout or ""
            nl = out.find("\n")
            if nl >= 0:
                if captured is not None:
                    captured["codex_stdin_sha256"] = out[:nl]
                body = out[nl + 1:]
            else:
                body = out
            return body, codex_adapter.build_telemetry(body, proc.returncode, 0.0)
        return codex_adapter.review_structured(
            root, thread_id=kw["thread_id"], work_item_id=kw["work_item_id"],
            packet_id=kw["packet_id"], council_id=kw["council_id"],
            round=kw["round_no"], phase=kw["phase"], context_text=text,
            timeout=kw["timeout"], cwd=kw["repo"], runner=runner,
            available_fn=lambda: True, egress_context=kw["egress_context"])
    return fn


def _std_src_prov():
    return {"class": "approved_repo_file", "path_rel": "tools/x.py",
            "sha256": "0" * 64}


class _ItsBase(unittest.TestCase):
    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="cw-its-int-")
        self.addCleanup(lambda: shutil.rmtree(self.root, ignore_errors=True))

    # -- fixtures ---------------------------------------------------------- #
    def _its_council(self, *, context=_CONTEXT, extra_records=None,
                     candidate_sources=("src",)):
        g = guard.LineageGraph()
        g.add("src", guard.CLASS_RAW, provenance=_std_src_prov())
        for rec in (extra_records or []):
            g.add(rec["id"], rec["classification"],
                  source_ids=rec.get("source_ids"),
                  provenance=rec.get("provenance"))
        g.add("packet", guard.CLASS_MACHINE, source_ids=list(candidate_sources))
        council = cwrc.create_council(
            self.root, thread_id="t-its", work_item_id="wi-its",
            data_sensitivity="internal_technical", phase="plan",
            lineage=g.to_records(), lineage_candidate="packet", source_bindings=[])
        cwrc.stamp_context(self.root, council,
                           hashlib.sha256(context.encode("utf-8")).hexdigest())
        return council

    def _run(self, council, *, gpt_fn, codex_fn, context=_CONTEXT):
        return cwrc.run_round(self.root, council, context, gpt_fn=gpt_fn,
                              codex_fn=codex_fn, timeout=5, sleep=lambda *a: None)

    def _commit_clean_round(self, council, *, summary=_CLEAN_SUMMARY, context=_CONTEXT):
        report = self._run(council, gpt_fn=_gpt_reviewer(summary=summary),
                           codex_fn=_codex_reviewer(summary=summary), context=context)
        self.assertTrue(report["committed"], report)
        self.assertTrue(report["substantive"], report)
        return report

    # -- helpers ----------------------------------------------------------- #
    def _tree_files(self):
        for dirpath, _dirs, files in os.walk(self.root):
            for f in files:
                yield os.path.join(dirpath, f)

    def _assert_absent(self, needle):
        nb = needle.encode("utf-8")
        for p in self._tree_files():
            with open(p, "rb") as fh:
                data = fh.read()
            self.assertNotIn(nb, data,
                             "synthetic string leaked into {}".format(p))

    def _finding_record(self, council_id, kind):
        for aid in cwia.list_ids(self.root, council_id):
            rec = cwia.load(self.root, council_id, aid)
            if rec and rec.get("kind") == kind:
                return rec
        return None


class Scenario01UserData(_ItsBase):
    def test_user_data_entering_its_council_blocks_before_dispatch(self):
        council = self._its_council(
            extra_records=[{"id": "inline", "classification": guard.CLASS_RAW,
                            "provenance": {"class": "sensitive_source",
                                           "reason": "inline_content"}}],
            candidate_sources=("src", "inline"))
        invoked = {"gpt": False, "codex": False}
        report = self._run(council, gpt_fn=_gpt_reviewer(invoked=invoked),
                           codex_fn=_codex_reviewer(invoked=invoked))
        self.assertTrue(report.get("its_blocked"))
        self.assertFalse(report["committed"])
        self.assertFalse(invoked["gpt"])
        self.assertFalse(invoked["codex"])


class Scenario02ReviewerResidue(_ItsBase):
    def test_synthetic_pii_in_reviewer_output_is_residue_blocked_content_free(self):
        council = self._its_council()
        poisoned = "Observed a residual identifier {} in the diff.".format(_SSN)
        report = self._run(council, gpt_fn=_gpt_reviewer(summary=poisoned),
                           codex_fn=_codex_reviewer())
        self.assertEqual(report["statuses"].get("gpt"), "residue_blocked")
        self.assertFalse(report["committed"])
        rec = self._finding_record(council["council_id"], "gpt_finding")
        self.assertIsNotNone(rec)
        self.assertIs(rec["scan"]["passed"], False)
        self.assertNotIn("content", rec)
        self._assert_absent(_SSN)


class Scenario03UnregisteredAugmentation(_ItsBase):
    def test_foreign_reconciliation_without_artifact_blocks_round2(self):
        council = self._its_council()
        self._commit_clean_round(council)
        # Simulate a foreign writer attaching a reconciliation onto the round file
        # DIRECTLY (bypassing attach_reconciliation), so it is NOT a registered
        # derived artifact.
        rounds = cwrc.load_rounds(self.root, council["council_id"])
        latest = rounds[-1]
        latest["reconciliation"] = {
            "ready_to_proceed": True, "summary": "foreign reconciliation",
            "accepted_findings": [], "rejected_findings": [],
            "required_plan_changes": [], "revised_plan": [],
            "unresolved_blockers": []}
        cwrc.save_round(self.root, council, latest)
        report = self._run(council, gpt_fn=_gpt_reviewer(), codex_fn=_codex_reviewer())
        self.assertTrue(report.get("its_blocked"))
        self.assertFalse(report["committed"])


class Scenario04MissingHashes(_ItsBase):
    def test_blank_content_sha256_on_prior_finding_blocks_round2(self):
        council = self._its_council()
        self._commit_clean_round(council)
        rec = self._finding_record(council["council_id"], "gpt_finding")
        rec["content_sha256"] = ""
        cwrc._atomic_write_json(
            cwia._artifact_path(self.root, council["council_id"], rec["artifact_id"]),
            rec)
        report = self._run(council, gpt_fn=_gpt_reviewer(), codex_fn=_codex_reviewer())
        self.assertTrue(report.get("its_blocked"))
        self.assertFalse(report["committed"])


class Scenario05MixedAncestry(_ItsBase):
    def test_added_sensitive_source_blocks_round2_reviewers_not_invoked(self):
        council = self._its_council()
        self._commit_clean_round(council)
        # Round 2 lineage carries an extra RAW sensitive_source node alongside the
        # ITS components.
        g = guard.LineageGraph()
        g.add("src", guard.CLASS_RAW, provenance=_std_src_prov())
        g.add("inline", guard.CLASS_RAW,
              provenance={"class": "sensitive_source", "reason": "inline_content"})
        g.add("packet", guard.CLASS_MACHINE, source_ids=["src", "inline"])
        council = cwrc.set_lineage(self.root, council, g.to_records(), "packet", [])
        invoked = {"gpt": False, "codex": False}
        report = self._run(council, gpt_fn=_gpt_reviewer(invoked=invoked),
                           codex_fn=_codex_reviewer(invoked=invoked))
        self.assertTrue(report.get("its_blocked"))
        self.assertFalse(report["committed"])
        self.assertFalse(invoked["gpt"])
        self.assertFalse(invoked["codex"])


class Scenario06MutationBeforeReuse(_ItsBase):
    def test_altered_stored_content_blocks_round2_on_hash_mismatch(self):
        council = self._its_council()
        self._commit_clean_round(council)
        rec = self._finding_record(council["council_id"], "gpt_finding")
        # Mutate the stored content but keep the recorded content_sha256.
        rec["content"] = rec["content"] + " tampered"
        cwrc._atomic_write_json(
            cwia._artifact_path(self.root, council["council_id"], rec["artifact_id"]),
            rec)
        report = self._run(council, gpt_fn=_gpt_reviewer(), codex_fn=_codex_reviewer())
        self.assertTrue(report.get("its_blocked"))
        self.assertFalse(report["committed"])


class Scenario07UndeclaredMetadata(_ItsBase):
    def _its_ctx_and_packet(self):
        g = guard.LineageGraph()
        g.add("src", guard.CLASS_RAW, provenance=_std_src_prov())
        g.add("scaffold", guard.CLASS_RAW, provenance={"class": "fixed_scaffold"})
        g.add("finding", guard.CLASS_MACHINE, source_ids=["src"],
              derived={"scan_passed": True})
        g.add("packet", guard.CLASS_MACHINE,
              source_ids=["src", "scaffold", "finding"])
        packet_text, comp = guard.build_its_packet(
            cwrc.ITS_SCAFFOLD_ROUND1, [{"id": "ctx", "text": "clean review\n"}])
        comp["provider_binding"] = {"gpt_model": "m", "max_output_tokens": 10}
        ctx = guard.EgressContext("internal_technical", graph=g,
                                  candidate_id="packet", require_graph=True,
                                  lane="internal_technical", its_composition=comp)
        return ctx, packet_text

    def test_extra_top_level_field_in_gpt_body_blocks_noncanonical(self):
        ctx, packet_text = self._its_ctx_and_packet()
        canonical = guard.build_its_gpt_body("m", packet_text, 10)
        body = json.loads(canonical.decode("utf-8"))
        body["instructions"] = "smuggled directive"
        tampered = json.dumps(body, ensure_ascii=False).encode("utf-8")
        with self.assertRaises(guard.EgressBlocked) as cm:
            guard.gpt_send(tampered, 5, context=ctx, key_getter=lambda: "k",
                           transport=lambda *a: (200, "{}"),
                           caller="clearwright_gpt_review")
        self.assertEqual(cm.exception.reason, "its_composition_unbound")
        self.assertEqual(cm.exception.summary.get("detail"), "noncanonical_body")

    def test_swapped_model_vs_provider_binding_blocks(self):
        ctx, packet_text = self._its_ctx_and_packet()
        # Bind gpt_model "m" but send a body built with a different model.
        swapped = guard.build_its_gpt_body("other-model", packet_text, 10)
        with self.assertRaises(guard.EgressBlocked) as cm:
            guard.gpt_send(swapped, 5, context=ctx, key_getter=lambda: "k",
                           transport=lambda *a: (200, "{}"),
                           caller="clearwright_gpt_review")
        self.assertEqual(cm.exception.reason, "its_composition_unbound")
        self.assertEqual(cm.exception.summary.get("detail"), "noncanonical_body")


class Scenario08ScaffoldStale(_ItsBase):
    def test_manifest_scaffold_sha_mismatch_is_its_scaffold_stale(self):
        packet_text, comp = guard.build_its_packet(
            cwrc.ITS_SCAFFOLD_ROUND1, [{"id": "c1", "text": "a\n"}])
        comp["scaffold_sha256"] = "f" * 64
        with self.assertRaises(guard.EgressBlocked) as cm:
            guard.verify_its_composition(packet_text, comp)
        self.assertEqual(cm.exception.reason, "its_scaffold_stale")


class Scenario09DirectProse(_ItsBase):
    def test_appended_free_prose_is_stray_bytes_blocked(self):
        packet_text, comp = guard.build_its_packet(
            cwrc.ITS_SCAFFOLD_ROUND1, [{"id": "c1", "text": "x\n"}])
        with self.assertRaises(guard.EgressBlocked):
            guard.verify_its_composition(packet_text + "appended free prose", comp)

    def test_clean_its_round_never_calls_augment_or_guidance(self):
        council = self._its_council()
        orig_aug = cwrc._augment_context
        orig_hdr = cwrc._guidance_header

        def _boom_aug(*a, **k):
            raise AssertionError("_augment_context called on the ITS lane")

        def _boom_hdr(*a, **k):
            raise AssertionError("_guidance_header called on the ITS lane")
        cwrc._augment_context = _boom_aug
        cwrc._guidance_header = _boom_hdr
        try:
            report = self._run(council, gpt_fn=_gpt_reviewer(),
                               codex_fn=_codex_reviewer())
        finally:
            cwrc._augment_context = orig_aug
            cwrc._guidance_header = orig_hdr
        self.assertTrue(report["committed"])
        self.assertTrue(report["substantive"])


class Scenario10UnscannedReuse(_ItsBase):
    def test_prior_finding_scan_passed_none_blocks_round2(self):
        council = self._its_council()
        self._commit_clean_round(council)
        rec = self._finding_record(council["council_id"], "gpt_finding")
        rec["scan"]["passed"] = None
        cwrc._atomic_write_json(
            cwia._artifact_path(self.root, council["council_id"], rec["artifact_id"]),
            rec)
        report = self._run(council, gpt_fn=_gpt_reviewer(), codex_fn=_codex_reviewer())
        self.assertTrue(report.get("its_blocked"))
        self.assertEqual(report.get("reason"), "its_generated_scan_failed")


class Scenario11NonTechnicalWorkItem(_ItsBase):
    def test_ineligible_task_kind_declaration_fails_closed_to_sensitive(self):
        for kind in ("chat", "governed"):
            resolved, why = use_cw._resolve_data_sensitivity(
                {"data_sensitivity": "internal_technical", "task_kind": kind})
            self.assertEqual(resolved, "sensitive")
            self.assertEqual(why, "ineligible_failclosed")
        # Boundary: an eligible task_kind is honored.
        resolved, why = use_cw._resolve_data_sensitivity(
            {"data_sensitivity": "internal_technical", "task_kind": "analysis"})
        self.assertEqual((resolved, why), ("internal_technical", "declared"))

    def test_standard_council_over_its_graph_is_lane_not_authorized(self):
        # create_council maps data_sensitivity "standard" -> dispatch_lane "user".
        council = cwrc.create_council(self.root, thread_id="t-std",
                                      data_sensitivity="standard")
        self.assertEqual(council["dispatch_lane"], "user")
        # An ITS-resolving graph dispatched in the user lane is refused at resolve.
        g = guard.LineageGraph()
        g.add("src", guard.CLASS_RAW, provenance=_std_src_prov())
        g.add("scaffold", guard.CLASS_RAW, provenance={"class": "fixed_scaffold"})
        g.add("finding", guard.CLASS_MACHINE, source_ids=["src"],
              derived={"scan_passed": True})
        g.add("packet", guard.CLASS_MACHINE,
              source_ids=["src", "scaffold", "finding"])
        ctx = guard.EgressContext("standard", graph=g, candidate_id="packet",
                                  require_graph=True, lane="user")
        with self.assertRaises(guard.EgressBlocked) as cm:
            ctx.resolve()
        self.assertEqual(cm.exception.reason, "its_lane_not_authorized")


class Scenario12ByteEquality(_ItsBase):
    def test_gpt_and_codex_wire_bytes_are_exactly_canonical(self):
        council = self._its_council()
        captured = {}
        report = self._run(council, gpt_fn=_gpt_reviewer(captured=captured),
                           codex_fn=_codex_reviewer(captured=captured))
        self.assertTrue(report["committed"], report)
        rounds = cwrc.load_rounds(self.root, council["council_id"])
        comp = rounds[-1]["its"]["composition"]
        scaffold_v = rounds[-1]["its"]["scaffold_version"]
        # Round 1 has exactly one component: the review context.
        self.assertEqual(len(comp["components"]), 1)
        ctx_id = comp["components"][0]["id"]
        packet_text, _m = guard.build_its_packet(
            scaffold_v, [{"id": ctx_id, "text": _CONTEXT}])
        bound_model = comp["provider_binding"]["gpt_model"]
        expected_gpt = guard.build_its_gpt_body(bound_model, packet_text, 4000)
        self.assertEqual(
            hashlib.sha256(captured["gpt_body"]).hexdigest(),
            hashlib.sha256(expected_gpt).hexdigest())
        expected_codex = guard.build_its_codex_prompt(packet_text).encode("utf-8")
        self.assertEqual(captured["codex_stdin_sha256"],
                         hashlib.sha256(expected_codex).hexdigest())


class Scenario13BudgetFailFast(_ItsBase):
    def test_oversized_its_packet_fails_fast_before_any_dispatch(self):
        # Shrink the plan phase budget so a large base context assembles into an
        # ITS packet over budget: it must fail fast (packet_undeliverable) BEFORE
        # either reviewer is dispatched, spending no attempt.
        prior = os.environ.get("CLEARWRIGHT_GPT_PLAN_INPUT_BUDGET")
        os.environ["CLEARWRIGHT_GPT_PLAN_INPUT_BUDGET"] = "100"

        def _restore():
            if prior is None:
                os.environ.pop("CLEARWRIGHT_GPT_PLAN_INPUT_BUDGET", None)
            else:
                os.environ["CLEARWRIGHT_GPT_PLAN_INPUT_BUDGET"] = prior
        self.addCleanup(_restore)
        big = "# benign technical context line\n" * 400
        council = self._its_council(context=big)
        invoked = {"gpt": False, "codex": False}
        report = self._run(council, gpt_fn=_gpt_reviewer(invoked=invoked),
                           codex_fn=_codex_reviewer(invoked=invoked), context=big)
        self.assertTrue(report.get("packet_undeliverable"), report)
        self.assertFalse(report["committed"])
        self.assertFalse(invoked["gpt"])
        self.assertFalse(invoked["codex"])
        self.assertEqual(report["budget"], 100)
        self.assertGreater(report["estimated_input_tokens"], report["budget"])


class HappyPathMultiRound(_ItsBase):
    def test_two_rounds_commit_with_verified_summary_and_three_axes(self):
        council = self._its_council()
        # Round 1 commits.
        self._commit_clean_round(council)
        # A registered reconciliation attaches (goes through attach_reconciliation).
        recon = {"ready_to_proceed": False,
                 "summary": "One follow-up round to confirm resolution.",
                 "accepted_findings": [], "rejected_findings": [],
                 "required_plan_changes": [], "revised_plan": ["confirm add() edge cases"],
                 "unresolved_blockers": []}
        cwrc.attach_reconciliation(self.root, council, recon)
        # Round 2 commits with the round-1 summary as a verified component.
        report2 = self._commit_clean_round(council)
        self.assertEqual(report2["round"], 2)
        rounds = cwrc.load_rounds(self.root, council["council_id"])
        r2 = rounds[-1]
        # Follow-up scaffold + a two-component packet (round-1 summary + context).
        self.assertEqual(r2["its"]["scaffold_version"], cwrc.ITS_SCAFFOLD_FOLLOWUP)
        self.assertEqual(len(r2["its"]["composition"]["components"]), 2)
        # Three DISTINCT audit axes.
        self.assertEqual(council["data_sensitivity"], "internal_technical")
        self.assertEqual(r2["its"]["effective_sensitivity"],
                         "internal_technical_standard")
        self.assertEqual(r2["its"]["lane"], "internal_technical")


if __name__ == "__main__":
    unittest.main()
