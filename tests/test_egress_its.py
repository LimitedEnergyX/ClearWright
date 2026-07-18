"""INTERNAL_TECHNICAL_STANDARD (ITS) lane — guard foundation tests (Phase 1).
The lane lets ClearWright run rich multi-round technical self-review only when
every packet component is a hash-bound derived artifact rooted exclusively in
verified STANDARD/ITS sources, scanned, unmutated, and byte-bound to the
recorded set. SYNTHETIC fixtures only.
"""
import os
import sys
import unittest

HERE = os.path.dirname(__file__)
sys.path.insert(0, os.path.join(HERE, "..", "tools"))
import clearwright_egress_guard as guard  # noqa: E402

guard.register_adapter("clearwright_gpt_review")

POLICY = guard.load_policy()
_SCAFFOLD_V = "council-scaffold-v1"
guard.register_its_scaffold(_SCAFFOLD_V, "FIXED COUNCIL SCAFFOLD v1: review honestly.")


def _std(g, nid="src"):
    g.add(nid, guard.CLASS_RAW,
          provenance={"class": "approved_repo_file", "path_rel": "tools/x.py",
                      "sha256": "0" * 64})
    return nid


class ItsResolution(unittest.TestCase):
    def test_scaffold_root_is_its(self):
        g = guard.LineageGraph()
        g.add("scaffold", guard.CLASS_RAW, provenance={"class": "fixed_scaffold"})
        self.assertEqual(g.resolve_sensitivity("scaffold"), guard.SENSITIVITY_ITS)

    def test_scanned_generated_over_standard_is_its(self):
        g = guard.LineageGraph()
        _std(g)
        g.add("finding", guard.CLASS_MACHINE, source_ids=["src"],
              derived={"scan_passed": True, "content_hash": "a" * 64,
                       "producer": "gpt", "round": 1})
        self.assertEqual(g.resolve_sensitivity("finding"), guard.SENSITIVITY_ITS)

    def test_unscanned_generated_is_sensitive(self):
        g = guard.LineageGraph()
        _std(g)
        g.add("finding", guard.CLASS_MACHINE, source_ids=["src"],
              derived={"scan_passed": None})  # scan not recorded
        self.assertEqual(g.resolve_sensitivity("finding"), guard.SENSITIVITY_SENSITIVE)

    def test_scan_failed_generated_is_sensitive(self):
        g = guard.LineageGraph()
        _std(g)
        g.add("finding", guard.CLASS_MACHINE, source_ids=["src"],
              derived={"scan_passed": False})
        self.assertEqual(g.resolve_sensitivity("finding"), guard.SENSITIVITY_SENSITIVE)

    def test_generated_over_sensitive_ancestor_is_sensitive(self):
        g = guard.LineageGraph()
        g.add("upload", guard.CLASS_RAW, provenance={"class": "user_upload"})
        g.add("summary", guard.CLASS_MACHINE, source_ids=["upload"],
              derived={"scan_passed": True})
        self.assertEqual(g.resolve_sensitivity("summary"), guard.SENSITIVITY_SENSITIVE)

    def test_pure_git_assembly_is_standard_not_its(self):
        g = guard.LineageGraph()
        _std(g)
        g.add("packet", guard.CLASS_MACHINE, source_ids=["src"])  # no derived
        self.assertEqual(g.resolve_sensitivity("packet"), guard.SENSITIVITY_STANDARD)

    def test_candidate_with_scaffold_and_findings_is_its(self):
        g = guard.LineageGraph()
        _std(g)
        g.add("scaffold", guard.CLASS_RAW, provenance={"class": "fixed_scaffold"})
        g.add("finding", guard.CLASS_MACHINE, source_ids=["src"],
              derived={"scan_passed": True})
        g.add("packet", guard.CLASS_MACHINE, source_ids=["src", "scaffold", "finding"])
        self.assertEqual(g.resolve_sensitivity("packet"), guard.SENSITIVITY_ITS)

    def test_mixed_sensitive_ancestor_forces_sensitive(self):
        g = guard.LineageGraph()
        _std(g)
        g.add("scaffold", guard.CLASS_RAW, provenance={"class": "fixed_scaffold"})
        g.add("upload", guard.CLASS_RAW, provenance={"class": "user_upload"})
        g.add("packet", guard.CLASS_MACHINE, source_ids=["src", "scaffold", "upload"])
        self.assertEqual(g.resolve_sensitivity("packet"), guard.SENSITIVITY_SENSITIVE)


class ItsLaneGate(unittest.TestCase):
    def _its_candidate(self):
        g = guard.LineageGraph()
        _std(g)
        g.add("scaffold", guard.CLASS_RAW, provenance={"class": "fixed_scaffold"})
        g.add("finding", guard.CLASS_MACHINE, source_ids=["src"],
              derived={"scan_passed": True})
        g.add("packet", guard.CLASS_MACHINE, source_ids=["src", "scaffold", "finding"])
        return g

    def test_its_blocked_in_user_lane(self):
        g = self._its_candidate()
        ctx = guard.EgressContext("standard", graph=g, candidate_id="packet",
                                  require_graph=True, lane="user")
        with self.assertRaises(guard.EgressBlocked) as cm:
            ctx.resolve()
        self.assertEqual(cm.exception.reason, "its_lane_not_authorized")

    def test_its_allowed_in_internal_technical_lane(self):
        g = self._its_candidate()
        ctx = guard.EgressContext("internal_technical", graph=g, candidate_id="packet",
                                  require_graph=True, lane="internal_technical")
        decision = ctx.resolve()
        self.assertEqual(decision["tier"], "internal_technical")

    def test_no_override_relabels_sensitive_as_its(self):
        g = guard.LineageGraph()
        g.add("upload", guard.CLASS_RAW, provenance={"class": "user_upload"})
        g.add("packet", guard.CLASS_MACHINE, source_ids=["upload"],
              derived={"scan_passed": True})
        # even in the ITS lane, a sensitive ancestor cannot be relabeled ITS
        ctx = guard.EgressContext("internal_technical", graph=g, candidate_id="packet",
                                  require_graph=True, lane="internal_technical")
        with self.assertRaises(guard.EgressBlocked):
            ctx.resolve()


class ItsComposition(unittest.TestCase):
    def test_build_and_verify_roundtrip(self):
        packet, manifest = guard.build_its_packet(_SCAFFOLD_V, [
            {"id": "src:tools/x.py", "text": "def f(): return 1\n"},
            {"id": "gpt:r1", "text": "Finding: rename f -> compute\n"}])
        result = guard.verify_its_composition(packet, manifest)
        self.assertEqual(result["component_count"], 2)

    def test_tampered_component_content_blocks(self):
        packet, manifest = guard.build_its_packet(_SCAFFOLD_V, [
            {"id": "c1", "text": "original\n"}])
        tampered = packet.replace("original", "SMUGGLED PHI 123-45-6789")
        with self.assertRaises(guard.EgressBlocked):
            guard.verify_its_composition(tampered, manifest)

    def test_stray_bytes_outside_frames_block(self):
        packet, manifest = guard.build_its_packet(_SCAFFOLD_V, [{"id": "c1", "text": "x\n"}])
        with self.assertRaises(guard.EgressBlocked):
            guard.verify_its_composition(packet + "extra sneaky trailer", manifest)

    def test_extra_undeclared_component_blocks(self):
        packet, manifest = guard.build_its_packet(_SCAFFOLD_V, [
            {"id": "c1", "text": "a\n"}, {"id": "c2", "text": "b\n"}])
        # drop c2 from the manifest -> packet has an undeclared component
        manifest["components"] = manifest["components"][:1]
        with self.assertRaises(guard.EgressBlocked):
            guard.verify_its_composition(packet, manifest)

    def test_unregistered_scaffold_blocks(self):
        packet, manifest = guard.build_its_packet(_SCAFFOLD_V, [{"id": "c1", "text": "a\n"}])
        manifest["scaffold_sha256"] = "f" * 64
        with self.assertRaises(guard.EgressBlocked):
            guard.verify_its_composition(packet, manifest)

    def test_marker_injection_in_component_is_safe(self):
        # A component whose content embeds the frame marker must not be able to
        # forge a second frame (length-delimited parsing).
        evil = guard._ITS_MARK + "component\x1fevil\x1f5\x1f" + ("0" * 64) + "\x1f\nBADXX\n"
        packet, manifest = guard.build_its_packet(_SCAFFOLD_V, [
            {"id": "c1", "text": evil}])
        result = guard.verify_its_composition(packet, manifest)
        self.assertEqual(result["component_count"], 1)  # the marker was DATA


class ItsEnforceFailClosed(unittest.TestCase):
    def test_its_dispatch_without_composition_fails_closed(self):
        g = guard.LineageGraph()
        _std(g)
        g.add("scaffold", guard.CLASS_RAW, provenance={"class": "fixed_scaffold"})
        g.add("finding", guard.CLASS_MACHINE, source_ids=["src"], derived={"scan_passed": True})
        g.add("packet", guard.CLASS_MACHINE, source_ids=["src", "scaffold", "finding"])
        ctx = guard.EgressContext("internal_technical", graph=g, candidate_id="packet",
                                  require_graph=True, lane="internal_technical")
        import json
        body = json.dumps({"model": "m", "input": [
            {"role": "developer", "content": "x"},
            {"role": "user", "content": "clean technical review"}],
            "max_output_tokens": 10}).encode("utf-8")
        with self.assertRaises(guard.EgressBlocked) as cm:
            guard.gpt_send(body, 5, context=ctx, key_getter=lambda: "k",
                           transport=lambda *a: (200, "{}"),
                           caller="clearwright_gpt_review")
        self.assertEqual(cm.exception.reason, "its_composition_unbound")


if __name__ == "__main__":
    unittest.main()
