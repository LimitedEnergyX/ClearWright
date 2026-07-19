"""Production-path live-lineage regression tests (SDEG finding D).
STANDARD is DERIVED from git-verified provenance, never merely declared, and
nothing is STANDARD just because ClearWright/Claude created it. SYNTHETIC
fixtures only.
"""
import json
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(__file__)
REPO = os.path.abspath(os.path.join(HERE, ".."))
sys.path.insert(0, os.path.join(REPO, "tools"))
import clearwright_egress_guard as guard  # noqa: E402

guard.register_adapter("clearwright_gpt_review")
guard.register_adapter("clearwright_codex_review")


def _git_clean(path):
    import subprocess
    try:
        r = subprocess.run(["git", "-C", REPO, "diff", "--quiet", "HEAD", "--", path],
                           capture_output=True, timeout=30)
        return r.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False

# A real git-tracked file under an approved repo path, COMMITTED and unmodified
# in the worktree (so its content matches the HEAD blob). Files under active
# edit would classify SENSITIVE (uncommitted) by design.
TRACKED = os.path.join(REPO, "tools", "clearwright_identity.py")
if not _git_clean(TRACKED):
    TRACKED = os.path.join(REPO, "tools", "clearwright_message.py")


def _gpt_body(user_text):
    return json.dumps({"model": "m", "input": [
        {"role": "developer", "content": "x"},
        {"role": "user", "content": user_text}], "max_output_tokens": 10}
    ).encode("utf-8")


# The canonical repository IDENTITY the committed policy declares. Derived from
# the policy itself (never hardcoded here) so the fixture cannot drift from it.
CANONICAL_IDENTITY = guard.load_policy()["approved_repo_identity"]


class _ApprovedRepoEnvCase(unittest.TestCase):
    """Portable REPO approval. STANDARD provenance now requires a MACHINE-LOCAL
    approved absolute root supplied by an uncommitted runtime config (identity-
    bound to the policy). setUp writes a TEMP config approving REPO under the
    canonical identity and points CLEARWRIGHT_EGRESS_LOCAL_CONFIG at it; tearDown
    removes the temp file and restores the prior env value. This lets the real-
    repo lineage tests bind REPO on ANY machine (Windows local, Linux CI) with NO
    committed absolute path. REPO is derived as the module already does
    (os.path.abspath(HERE/..))."""

    def setUp(self):
        super().setUp()
        fd, self._local_cfg = tempfile.mkstemp(suffix=".json",
                                               prefix="cw-egress-local-")
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump({"approved_repo_identity": CANONICAL_IDENTITY,
                       "approved_repo_roots": [REPO]}, fh)
        self._prev_local_cfg = os.environ.get("CLEARWRIGHT_EGRESS_LOCAL_CONFIG")
        os.environ["CLEARWRIGHT_EGRESS_LOCAL_CONFIG"] = self._local_cfg

    def tearDown(self):
        if self._prev_local_cfg is None:
            os.environ.pop("CLEARWRIGHT_EGRESS_LOCAL_CONFIG", None)
        else:
            os.environ["CLEARWRIGHT_EGRESS_LOCAL_CONFIG"] = self._prev_local_cfg
        try:
            os.remove(self._local_cfg)
        except OSError:
            pass
        super().tearDown()


class DerivedStandard(_ApprovedRepoEnvCase):
    def test_approved_git_tracked_clean_source_resolves_standard(self):
        rec = guard.classify_source(TRACKED, REPO)
        self.assertEqual(rec["class"], "approved_repo_file")
        g, cand, binds = guard.build_candidate_graph([TRACKED], REPO)
        self.assertEqual(g.resolve_sensitivity(cand), guard.SENSITIVITY_STANDARD)
        self.assertEqual(g.decide_outcome(cand)["tier"], "standard")
        self.assertTrue(binds and binds[0]["sha256"] and binds[0]["path_rel"]
                        and "abspath" not in binds[0])

    def test_runtime_work_file_is_not_automatically_standard(self):
        # A file that ClearWright created outside the repo (e.g. under
        # runtime/work) must NOT be STANDARD merely because it exists.
        fd, p = tempfile.mkstemp(suffix=".md", prefix="cw-runtime-work-")
        os.close(fd)
        self.addCleanup(os.remove, p)
        rec = guard.classify_source(p, REPO)
        self.assertNotIn(rec["class"], guard._STANDARD_PROVENANCE)
        g, cand, _ = guard.build_candidate_graph([p], REPO)
        with self.assertRaises(guard.EgressBlocked):
            g.decide_outcome(cand)

    def test_untracked_repo_file_fails_closed(self):
        # A file physically inside the repo + approved path but NOT git-tracked.
        p = os.path.join(REPO, "tools", "_lineage_untracked_probe.py")
        with open(p, "w", encoding="utf-8") as fh:
            fh.write("# untracked probe\n")
        self.addCleanup(lambda: os.path.exists(p) and os.remove(p))
        rec = guard.classify_source(p, REPO)
        self.assertEqual(rec.get("reason"), "source_untracked")
        self.assertNotIn(rec["class"], guard._STANDARD_PROVENANCE)

    def test_file_outside_approved_paths_is_sensitive(self):
        # A tracked file NOT under an approved path (README-style outside the
        # allowlist). Use a repo-root file that is not in tools/apps/tests/docs.
        # The pyproject/config-style path is not approved.
        p = os.path.join(REPO, "runtimehint_not_approved.cfg")
        with open(p, "w", encoding="utf-8") as fh:
            fh.write("x=1\n")
        self.addCleanup(lambda: os.path.exists(p) and os.remove(p))
        rec = guard.classify_source(p, REPO)
        self.assertNotIn(rec["class"], guard._STANDARD_PROVENANCE)


class OperatorDeclarationCannotDowngrade(_ApprovedRepoEnvCase):
    def test_declared_standard_but_source_sensitive_blocks(self):
        # runtime/work source => sensitive candidate; a declared STANDARD tier
        # must NOT override it.
        fd, p = tempfile.mkstemp(prefix="cw-user-upload-")
        os.close(fd)
        self.addCleanup(os.remove, p)
        g, cand, binds = guard.build_candidate_graph([p], REPO)
        ctx = guard.EgressContext("standard", graph=g, candidate_id=cand,
                                  source_bindings=binds, require_graph=True)
        with self.assertRaises(guard.EgressBlocked):
            ctx.resolve()

    def test_machine_output_inherits_sensitive_ancestor(self):
        g = guard.LineageGraph()
        g.add("upload", guard.CLASS_RAW, provenance={"class": "user_upload"})
        g.add("summary", guard.CLASS_MACHINE, source_ids=["upload"])
        g.add("packet", guard.CLASS_MACHINE, source_ids=["summary"])
        self.assertEqual(g.resolve_sensitivity("packet"),
                         guard.SENSITIVITY_SENSITIVE)

    def test_missing_declaration_defaults_sensitive_failclosed(self):
        # No graph on the live path => fail closed.
        ctx = guard.EgressContext("standard", require_graph=True)
        with self.assertRaises(guard.EgressBlocked) as cm:
            ctx.resolve()
        self.assertEqual(cm.exception.reason, "lineage_missing")

    def test_explicit_sensitive_forces_sensitive(self):
        # A graph that resolves STANDARD is escalated by an explicit SENSITIVE
        # declaration: the effective outcome is sensitive (needs a derivative),
        # never a plain standard dispatch.
        g, cand, binds = guard.build_candidate_graph([TRACKED], REPO)
        self.assertEqual(g.decide_outcome(cand)["tier"], "standard")
        ctx = guard.EgressContext("sensitive", graph=g, candidate_id=cand,
                                  source_bindings=binds, require_graph=True)
        decision = ctx.resolve()
        self.assertEqual(decision["tier"], "sensitive")
        self.assertTrue(decision.get("escalated"))


class FailClosed(unittest.TestCase):
    def test_unknown_source_fails_closed(self):
        g = guard.LineageGraph()
        g.add("c", guard.CLASS_MACHINE, source_ids=["ghost"])
        with self.assertRaises(guard.EgressBlocked) as cm:
            g.resolve_sensitivity("c")
        self.assertEqual(cm.exception.reason, "lineage_source_missing")

    def test_cycle_fails_closed(self):
        g = guard.LineageGraph()
        g.add("a", guard.CLASS_MACHINE, source_ids=["b"])
        g.add("b", guard.CLASS_MACHINE, source_ids=["a"])
        with self.assertRaises(guard.EgressBlocked) as cm:
            g.resolve_sensitivity("a")
        self.assertEqual(cm.exception.reason, "lineage_cycle")


class PathConfusion(_ApprovedRepoEnvCase):
    def test_traversal_cannot_escape_repo(self):
        # A path that lexically sits in the repo but resolves outside via "..".
        escaping = os.path.join(REPO, "tools", "..", "..", "..", "outside.py")
        rec = guard.classify_source(escaping, REPO)
        self.assertNotIn(rec["class"], guard._STANDARD_PROVENANCE)
        self.assertIn(rec.get("reason"),
                      ("source_outside_repo", "source_not_a_file", "source_traversal"))

    def test_symlink_source_is_sensitive(self):
        outside_fd, outside = tempfile.mkstemp(suffix=".py", prefix="cw-outside-")
        os.close(outside_fd)
        self.addCleanup(os.remove, outside)
        link = os.path.join(REPO, "tools", "_lineage_symlink_probe.py")
        try:
            os.symlink(outside, link)
        except (OSError, NotImplementedError, AttributeError):
            self.skipTest("symlink creation not permitted on this host")
        self.addCleanup(lambda: os.path.exists(link) and os.remove(link))
        rec = guard.classify_source(link, REPO)
        self.assertNotIn(rec["class"], guard._STANDARD_PROVENANCE)


class TOCTOU(unittest.TestCase):
    def test_source_mutation_after_verification_blocks(self):
        fd, p = tempfile.mkstemp(suffix=".py", prefix="cw-toctou-")
        os.write(fd, b"original content\n")
        os.close(fd)
        self.addCleanup(os.remove, p)
        sha = guard._sha256_file(p)
        ctx = guard.EgressContext("standard", source_bindings=[{"abspath": p, "sha256": sha}])
        ctx.verify_source_bindings()  # unchanged: passes
        with open(p, "w", encoding="utf-8") as fh:
            fh.write("MUTATED after verification\n")
        with self.assertRaises(guard.EgressBlocked) as cm:
            ctx.verify_source_bindings()
        self.assertEqual(cm.exception.reason, "source_mutated_after_verification")


class GptAndCodexParity(unittest.TestCase):
    def _sensitive_ctx(self):
        fd, p = tempfile.mkstemp(prefix="cw-upload-")
        os.close(fd)
        self.addCleanup(os.remove, p)
        g, cand, binds = guard.build_candidate_graph([p], REPO)
        return guard.EgressContext("standard", graph=g, candidate_id=cand,
                                   source_bindings=binds, require_graph=True)

    def test_gpt_blocks_sensitive_lineage(self):
        ctx = self._sensitive_ctx()
        with self.assertRaises(guard.EgressBlocked):
            guard.gpt_send(_gpt_body("clean text"), 5, context=ctx,
                           key_getter=lambda: "k", transport=lambda *a: (200, "{}"),
                           caller="clearwright_gpt_review")

    def test_codex_blocks_sensitive_lineage(self):
        ctx = self._sensitive_ctx()
        with self.assertRaises(guard.EgressBlocked):
            guard.codex_launch(["codex"], "clean text", 5, context=ctx,
                               caller="clearwright_codex_review")


class ContentBinding(_ApprovedRepoEnvCase):
    def test_decoy_source_with_sensitive_content_blocks(self):
        # THE round-3 finding: a clean git source cannot bless a packet that
        # also carries a sensitive content source. Content sources ARE lineage.
        fd, upload = tempfile.mkstemp(prefix="cw-upload-")
        os.close(fd)
        self.addCleanup(os.remove, upload)
        g, cand, binds = guard.build_candidate_graph([TRACKED, upload], REPO,
                                                     candidate_id="packet")
        with self.assertRaises(guard.EgressBlocked):
            g.decide_outcome(cand)

    def test_inline_content_forces_sensitive(self):
        # A packet with inline prompt text (no content file) has no provenance.
        g, cand, _ = guard.build_candidate_graph([TRACKED], REPO,
                                                 candidate_id="packet",
                                                 inline_unverified=True)
        with self.assertRaises(guard.EgressBlocked):
            g.decide_outcome(cand)

    def test_only_committed_git_content_resolves_standard(self):
        g, cand, binds = guard.build_candidate_graph([TRACKED], REPO,
                                                     candidate_id="packet")
        self.assertEqual(g.decide_outcome(cand)["tier"], "standard")
        # binding is content-free: repo + repo-relative path, no abspath
        self.assertTrue(binds and "abspath" not in binds[0]
                        and binds[0].get("path_rel"))


class RepoIdentity(unittest.TestCase):
    def test_repo_spoofing_is_rejected(self):
        # A file under an attacker-controlled repo (not in approved_repo_roots)
        # cannot mint approved_repo_file.
        fake = tempfile.mkdtemp(prefix="cw-fake-repo-")
        self.addCleanup(lambda: __import__("shutil").rmtree(fake, ignore_errors=True))
        os.makedirs(os.path.join(fake, "tools"))
        fpath = os.path.join(fake, "tools", "x.py")
        with open(fpath, "w", encoding="utf-8") as fh:
            fh.write("print('x')\n")
        rec = guard.classify_source(fpath, fake)
        self.assertEqual(rec["class"], "sensitive_source")
        self.assertEqual(rec.get("reason"), "repo_unresolvable")


class Uncommitted(_ApprovedRepoEnvCase):
    def test_locally_modified_tracked_file_is_sensitive(self):
        # A tracked approved file whose working-tree content diverges from the
        # committed blob is SENSITIVE (only committed content is provably std).
        with open(TRACKED, "r", encoding="utf-8") as fh:
            original = fh.read()

        def _restore():
            with open(TRACKED, "w", encoding="utf-8") as fh:
                fh.write(original)
        self.addCleanup(_restore)
        with open(TRACKED, "a", encoding="utf-8") as fh:
            fh.write("\n# local uncommitted modification\n")
        rec = guard.classify_source(TRACKED, REPO)
        self.assertNotIn(rec["class"], guard._STANDARD_PROVENANCE)
        self.assertEqual(rec.get("reason"), "source_uncommitted")


class GitIndexBitBypass(_ApprovedRepoEnvCase):
    def test_assume_unchanged_modified_file_is_sensitive(self):
        # A tracked approved file marked assume-unchanged then modified must NOT
        # classify STANDARD (git diff --quiet lies; blob-id comparison catches).
        import subprocess
        with open(TRACKED, "r", encoding="utf-8") as fh:
            original = fh.read()
        rel = os.path.relpath(os.path.realpath(TRACKED), os.path.realpath(REPO)).replace("\\", "/")

        def _restore():
            try:
                subprocess.run(["git", "-C", REPO, "update-index", "--no-assume-unchanged", "--", rel],
                               capture_output=True, timeout=30)
            except Exception:
                pass
            with open(TRACKED, "w", encoding="utf-8") as fh:
                fh.write(original)
        self.addCleanup(_restore)
        r = subprocess.run(["git", "-C", REPO, "update-index", "--assume-unchanged", "--", rel],
                           capture_output=True, timeout=30)
        if r.returncode != 0:
            self.skipTest("could not set assume-unchanged")
        with open(TRACKED, "a", encoding="utf-8") as fh:
            fh.write("\n# attacker content under assume-unchanged\n")
        rec = guard.classify_source(TRACKED, REPO)
        self.assertNotIn(rec["class"], guard._STANDARD_PROVENANCE)
        self.assertEqual(rec.get("reason"), "source_uncommitted")


class PerRoundRebuild(_ApprovedRepoEnvCase):
    def test_lineage_records_are_content_free(self):
        g, cand, binds = guard.build_candidate_graph([TRACKED], REPO,
                                                     candidate_id="packet")
        for rec in g.to_records():
            prov = rec.get("provenance") or {}
            self.assertNotIn("abspath", prov)
            self.assertNotIn("repo", prov)  # no absolute path persisted

    def test_set_lineage_rebinds_council(self):
        import tempfile
        import clearwright_review_council as cwrc
        root = tempfile.mkdtemp(prefix="cw-relineage-")
        self.addCleanup(lambda: __import__("shutil").rmtree(root, ignore_errors=True))
        # round-1 STANDARD lineage
        g1, c1, b1 = guard.build_candidate_graph([TRACKED], REPO, candidate_id="packet")
        council = cwrc.create_council(root, thread_id="t", data_sensitivity="standard",
                                      lineage=g1.to_records(), lineage_candidate=c1,
                                      source_bindings=b1)
        # round-2 inline/sensitive content rebinds the council to SENSITIVE
        g2 = guard.LineageGraph()
        g2.add("inline", guard.CLASS_RAW,
               provenance={"class": "sensitive_source", "reason": "inline_content"})
        g2.add("packet", guard.CLASS_MACHINE, source_ids=["inline"])
        council = cwrc.set_lineage(root, council, g2.to_records(), "packet", [])
        rebuilt = guard.LineageGraph.from_records(council["lineage"])
        self.assertEqual(rebuilt.resolve_sensitivity("packet"),
                         guard.SENSITIVITY_SENSITIVE)


class SanitizedDoesNotReclassifySource(unittest.TestCase):
    def test_source_stays_sensitive_after_sanitize(self):
        g = guard.LineageGraph()
        g.add("phi", guard.CLASS_RAW, provenance={"class": "user_upload"})
        guard.sanitize_clinical([{"code": "symptom_pain"}],
                                template_id="clinical_review_v1",
                                source_node_id="phi", graph=g)
        self.assertEqual(g.resolve_sensitivity("phi"), guard.SENSITIVITY_SENSITIVE)
        self.assertEqual(g.get("phi")["classification"], guard.CLASS_RAW)


if __name__ == "__main__":
    unittest.main()
