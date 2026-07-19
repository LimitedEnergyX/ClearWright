"""Operator portability matrix for the SDEG approved-repository binding.

STANDARD provenance stays PORTABLE: the committed public policy carries only a
canonical repository IDENTITY (no machine path); the machine-local approved
ABSOLUTE roots come from an EXPLICIT uncommitted runtime config selected by
CLEARWRIGHT_EGRESS_LOCAL_CONFIG and are accepted only when that config declares
the policy's identity. Any missing / malformed / identity-mismatched / non-
absolute config fails closed to SENSITIVE. Approval never derives from cwd, any
git repo, a repo basename, a git remote URL, a CI env var's existence, GitHub
Actions, or a generated directory.

Every fixture here is SYNTHETIC: temp directories turned into throwaway git
repositories. No real repository, no operator machine path, and no committed
absolute path is used or asserted.
"""
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest

HERE = os.path.dirname(__file__)
REPO = os.path.abspath(os.path.join(HERE, ".."))
sys.path.insert(0, os.path.join(REPO, "tools"))
import clearwright_egress_guard as guard  # noqa: E402

POLICY_PATH = os.path.join(REPO, "tools", "egress_policy.json")
_LOADED = guard.load_policy()
IDENTITY = _LOADED["approved_repo_identity"]
POLICY = _LOADED["policy"]
ENV = "CLEARWRIGHT_EGRESS_LOCAL_CONFIG"


# --------------------------------------------------------------------------- #
# Synthetic git-repo helpers (git init + commit). SYNTHETIC ONLY.
# --------------------------------------------------------------------------- #

def _run_git(repo, *args, stdin=None):
    return subprocess.run(["git", "-C", repo] + list(args), capture_output=True,
                          text=True, timeout=60, input=stdin)


def _init_repo(root):
    r = subprocess.run(["git", "init", "-q", root], capture_output=True,
                       text=True, timeout=60)
    if r.returncode != 0:
        raise unittest.SkipTest("git init unavailable on this host")
    # Deterministic identity + no CRLF filtering so committed blob ids are stable
    # across Windows and Linux (the committed-blob comparison must be exact).
    _run_git(root, "config", "user.email", "synthetic@example.invalid")
    _run_git(root, "config", "user.name", "synthetic")
    _run_git(root, "config", "commit.gpgsign", "false")
    _run_git(root, "config", "core.autocrlf", "false")


def _write(root, rel, content):
    p = os.path.join(root, *rel.split("/"))
    d = os.path.dirname(p)
    if d and not os.path.isdir(d):
        os.makedirs(d)
    with open(p, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(content)
    return p


def _commit(root, rel, content, message="commit"):
    p = _write(root, rel, content)
    _run_git(root, "add", "--", rel)
    r = _run_git(root, "commit", "-q", "-m", message)
    if r.returncode != 0:
        raise unittest.SkipTest("git commit unavailable on this host")
    return p


class PortabilityCase(unittest.TestCase):
    """Base: each test starts with the env var UNSET (clean machine), writes only
    synthetic temp configs/repos, and always restores the prior env value and
    removes every temp artifact in tearDown."""

    def setUp(self):
        self._prev = os.environ.get(ENV)
        os.environ.pop(ENV, None)
        self._tmpdirs = []
        self._tmpfiles = []

    def tearDown(self):
        if self._prev is None:
            os.environ.pop(ENV, None)
        else:
            os.environ[ENV] = self._prev
        for f in self._tmpfiles:
            try:
                os.remove(f)
            except OSError:
                pass
        for d in self._tmpdirs:
            shutil.rmtree(d, ignore_errors=True)

    # -- fixtures -----------------------------------------------------------
    def _repo(self, name=None):
        parent = tempfile.mkdtemp(prefix="cw-syn-parent-")
        self._tmpdirs.append(parent)
        root = os.path.join(parent, name) if name else parent
        if name:
            os.makedirs(root)
        # Use the realpath so the source's lexical abspath and the repo's realpath
        # are consistent (temp dirs can carry an 8.3 short-name component on
        # Windows; classify_source enforces confinement on BOTH forms).
        root = os.path.realpath(root)
        _init_repo(root)
        return root

    def _config(self, identity, roots, set_env=True):
        fd, p = tempfile.mkstemp(suffix=".json", prefix="cw-syn-cfg-")
        self._tmpfiles.append(p)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump({"approved_repo_identity": identity,
                       "approved_repo_roots": roots}, fh)
        if set_env:
            os.environ[ENV] = p
        return p

    def _config_raw(self, text, set_env=True):
        fd, p = tempfile.mkstemp(suffix=".json", prefix="cw-syn-cfg-")
        self._tmpfiles.append(p)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(text)
        if set_env:
            os.environ[ENV] = p
        return p


# --------------------------------------------------------------------------- #
# Approved local roots (Windows / Linux / GitHub checkout stand-ins).
# --------------------------------------------------------------------------- #

class ApprovedRoots(PortabilityCase):
    def test_approved_local_root_resolves_standard(self):
        repo = self._repo()
        f = _commit(repo, "tools/x.py", "print('x')\n")
        self._config(IDENTITY, [repo])
        rec = guard.classify_source(f, repo)
        self.assertEqual(rec["class"], "approved_repo_file")
        g, cand, binds = guard.build_candidate_graph([f], repo)
        self.assertEqual(g.resolve_sensitivity(cand), guard.SENSITIVITY_STANDARD)
        self.assertEqual(g.decide_outcome(cand)["tier"], "standard")
        self.assertTrue(binds and binds[0].get("path_rel")
                        and "abspath" not in binds[0])

    def test_github_style_checkout_root_resolves_standard(self):
        # A temp dir standing in for $GITHUB_WORKSPACE; CI env vars are present but
        # do NOT approve anything -- only the explicit config does.
        repo = self._repo()
        f = _commit(repo, "tools/gh.py", "print('gh')\n")
        self.addCleanup(os.environ.pop, "GITHUB_ACTIONS", None)
        self.addCleanup(os.environ.pop, "GITHUB_WORKSPACE", None)
        os.environ["GITHUB_ACTIONS"] = "true"
        os.environ["GITHUB_WORKSPACE"] = repo
        self._config(IDENTITY, [repo])
        rec = guard.classify_source(f, repo)
        self.assertEqual(rec["class"], "approved_repo_file")

    @unittest.skipUnless(os.name == "nt", "Windows drive-letter/normcase case")
    def test_windows_drive_letter_root_normcase(self):
        repo = self._repo()
        f = _commit(repo, "tools/w.py", "print('w')\n")
        # Approve the SAME root but with the drive letter's case flipped: normcase
        # must still bind it (a portable, case-normalized comparison on Windows).
        drive, rest = os.path.splitdrive(repo)
        swapped = (drive.swapcase() + rest) if drive else repo
        self._config(IDENTITY, [swapped])
        rec = guard.classify_source(f, repo)
        self.assertEqual(rec["class"], "approved_repo_file")

    @unittest.skipIf(os.name == "nt", "POSIX-root portability case")
    def test_posix_style_root_resolves_standard(self):
        repo = self._repo()
        self.assertTrue(repo.startswith("/"))  # POSIX absolute root
        f = _commit(repo, "tools/p.py", "print('p')\n")
        self._config(IDENTITY, [repo])
        rec = guard.classify_source(f, repo)
        self.assertEqual(rec["class"], "approved_repo_file")


# --------------------------------------------------------------------------- #
# Missing / malformed config -> [] -> fail closed to SENSITIVE.
# --------------------------------------------------------------------------- #

class MissingAndMalformedConfig(PortabilityCase):
    def _committed_repo(self):
        repo = self._repo()
        f = _commit(repo, "tools/x.py", "print('x')\n")
        return repo, f

    def test_missing_config_env_unset_fails_closed(self):
        repo, f = self._committed_repo()
        # env deliberately unset by setUp
        self.assertEqual(guard.resolve_local_egress_config(POLICY), [])
        rec = guard.classify_source(f, repo)
        self.assertEqual(rec["class"], "sensitive_source")
        self.assertEqual(rec["reason"], "repo_unresolvable")

    def test_unreadable_config_path_fails_closed(self):
        repo, f = self._committed_repo()
        os.environ[ENV] = os.path.join(tempfile.gettempdir(),
                                       "cw-does-not-exist-9f3a.json")
        self.assertEqual(guard.resolve_local_egress_config(POLICY), [])
        self.assertEqual(guard.classify_source(f, repo)["reason"],
                         "repo_unresolvable")

    def test_malformed_json_fails_closed(self):
        repo, f = self._committed_repo()
        self._config_raw("{ this is not json ")
        self.assertEqual(guard.resolve_local_egress_config(POLICY), [])
        self.assertEqual(guard.classify_source(f, repo)["reason"],
                         "repo_unresolvable")

    def test_non_dict_config_fails_closed(self):
        repo, f = self._committed_repo()
        self._config_raw(json.dumps([REPO]))  # a JSON list, not an object
        self.assertEqual(guard.resolve_local_egress_config(POLICY), [])
        self.assertEqual(guard.classify_source(f, repo)["reason"],
                         "repo_unresolvable")

    def test_roots_not_a_list_fails_closed(self):
        repo, f = self._committed_repo()
        self._config_raw(json.dumps({"approved_repo_identity": IDENTITY,
                                     "approved_repo_roots": repo}))  # str not list
        self.assertEqual(guard.resolve_local_egress_config(POLICY), [])
        self.assertEqual(guard.classify_source(f, repo)["reason"],
                         "repo_unresolvable")

    def test_empty_roots_list_fails_closed(self):
        repo, f = self._committed_repo()
        self._config(IDENTITY, [])
        self.assertEqual(guard.resolve_local_egress_config(POLICY), [])
        self.assertEqual(guard.classify_source(f, repo)["reason"],
                         "repo_unresolvable")

    def test_non_absolute_root_fails_closed(self):
        repo, f = self._committed_repo()
        self._config(IDENTITY, ["tools"])  # relative -> whole list rejected
        self.assertEqual(guard.resolve_local_egress_config(POLICY), [])
        self.assertEqual(guard.classify_source(f, repo)["reason"],
                         "repo_unresolvable")

    def test_root_not_a_string_fails_closed(self):
        repo, f = self._committed_repo()
        self._config_raw(json.dumps({"approved_repo_identity": IDENTITY,
                                     "approved_repo_roots": [123]}))
        self.assertEqual(guard.resolve_local_egress_config(POLICY), [])
        self.assertEqual(guard.classify_source(f, repo)["reason"],
                         "repo_unresolvable")


# --------------------------------------------------------------------------- #
# Identity binding: basename / remote URL / mismatched identity never approve.
# --------------------------------------------------------------------------- #

class IdentityBinding(PortabilityCase):
    def test_wrong_identity_fails_closed(self):
        repo = self._repo()
        f = _commit(repo, "tools/x.py", "print('x')\n")
        self._config("someone-else/Fork", [repo])  # roots correct, identity wrong
        self.assertEqual(guard.resolve_local_egress_config(POLICY), [])
        self.assertEqual(guard.classify_source(f, repo)["reason"],
                         "repo_unresolvable")

    def test_matching_basename_wrong_identity_fails_closed(self):
        # Repo directory is literally named "ClearWright" -- basename matches, but
        # the config identity does not equal the policy's, so it fails closed.
        repo = self._repo(name="ClearWright")
        self.assertEqual(os.path.basename(repo), "ClearWright")
        f = _commit(repo, "tools/x.py", "print('x')\n")
        self._config("evil/ClearWright", [repo])
        self.assertEqual(guard.resolve_local_egress_config(POLICY), [])
        self.assertEqual(guard.classify_source(f, repo)["reason"],
                         "repo_unresolvable")

    def test_spoofed_remote_identity_never_approves(self):
        # A repo whose git remote URL is the REAL slug proves nothing: with no
        # config (env unset) AND with a wrong-identity config, it fails closed.
        repo = self._repo()
        f = _commit(repo, "tools/x.py", "print('x')\n")
        _run_git(repo, "remote", "add", "origin",
                 "https://github.com/" + IDENTITY + ".git")
        # (a) env unset -> remote presence alone does not approve
        self.assertEqual(guard.classify_source(f, repo)["reason"],
                         "repo_unresolvable")
        # (b) config with wrong identity, roots correct -> still fails closed
        self._config("attacker/mirror", [repo])
        self.assertEqual(guard.classify_source(f, repo)["reason"],
                         "repo_unresolvable")

    def test_policy_without_identity_never_approves(self):
        # A policy that declares no identity can never be satisfied by any config.
        self._config("anything", [REPO])
        self.assertEqual(guard.resolve_local_egress_config({}), [])
        self.assertEqual(
            guard.resolve_local_egress_config({"approved_repo_identity": ""}), [])


# --------------------------------------------------------------------------- #
# Wrong local root (config approves a DIFFERENT dir than the repo).
# --------------------------------------------------------------------------- #

class WrongRoot(PortabilityCase):
    def test_wrong_local_root_repo_unresolvable(self):
        repo = self._repo()
        f = _commit(repo, "tools/x.py", "print('x')\n")
        other = self._repo()  # a different synthetic dir
        self._config(IDENTITY, [other])
        # The resolver approves `other`, but the source's repo is `repo` -> no bind
        self.assertEqual(guard.classify_source(f, repo)["reason"],
                         "repo_unresolvable")


# --------------------------------------------------------------------------- #
# Source states under an APPROVED root: only committed content is STANDARD.
# --------------------------------------------------------------------------- #

class SourceStatesUnderApprovedRoot(PortabilityCase):
    def _approved_repo(self):
        repo = self._repo()
        _commit(repo, "tools/base.py", "print('base')\n")  # initial commit
        self._config(IDENTITY, [repo])
        return repo

    def test_untracked_source_fails_closed(self):
        repo = self._approved_repo()
        p = _write(repo, "tools/untracked.py", "print('u')\n")
        rec = guard.classify_source(p, repo)
        self.assertEqual(rec["class"], "sensitive_source")
        self.assertEqual(rec["reason"], "source_untracked")

    def test_modified_committed_source_is_uncommitted(self):
        repo = self._approved_repo()
        p = _commit(repo, "tools/mod.py", "print('orig')\n")
        with open(p, "a", encoding="utf-8", newline="\n") as fh:
            fh.write("# local edit\n")
        rec = guard.classify_source(p, repo)
        self.assertEqual(rec["class"], "sensitive_source")
        self.assertEqual(rec["reason"], "source_uncommitted")

    def test_gitignored_source_fails_closed(self):
        repo = self._approved_repo()
        _commit(repo, ".gitignore", "tools/ignored_probe.py\n")
        p = _write(repo, "tools/ignored_probe.py", "print('ignored')\n")
        rec = guard.classify_source(p, repo)
        self.assertEqual(rec["class"], "sensitive_source")
        self.assertEqual(rec["reason"], "source_untracked")

    def test_staged_but_not_committed_is_sensitive(self):
        repo = self._approved_repo()
        p = _write(repo, "tools/staged.py", "print('staged')\n")
        _run_git(repo, "add", "--", "tools/staged.py")  # staged, never committed
        rec = guard.classify_source(p, repo)
        self.assertEqual(rec["class"], "sensitive_source")
        self.assertEqual(rec["reason"], "source_uncommitted")


# --------------------------------------------------------------------------- #
# Path safety: symlink escape / traversal / alternate path -> fail closed.
# --------------------------------------------------------------------------- #

class PathSafety(PortabilityCase):
    def _approved_repo(self):
        repo = self._repo()
        _commit(repo, "tools/base.py", "print('base')\n")
        self._config(IDENTITY, [repo])
        return repo

    def test_symlink_escape_fails_closed(self):
        repo = self._approved_repo()
        outside_fd, outside = tempfile.mkstemp(suffix=".py", prefix="cw-syn-out-")
        os.close(outside_fd)
        self._tmpfiles.append(outside)
        link = os.path.join(repo, "tools", "link.py")
        try:
            os.symlink(outside, link)
        except (OSError, NotImplementedError, AttributeError):
            self.skipTest("symlink creation not permitted on this host")
        rec = guard.classify_source(link, repo)
        self.assertEqual(rec["class"], "sensitive_source")
        self.assertEqual(rec["reason"], "source_symlink")

    def test_path_traversal_fails_closed(self):
        repo = self._approved_repo()
        escaping = os.path.join(repo, "tools", "..", "..", "outside.py")
        rec = guard.classify_source(escaping, repo)
        self.assertEqual(rec["class"], "sensitive_source")
        self.assertIn(rec["reason"],
                      ("source_outside_repo", "source_not_a_file",
                       "source_traversal"))

    def test_alternate_path_outside_root_fails_closed(self):
        repo = self._approved_repo()
        outside_fd, outside = tempfile.mkstemp(suffix=".py", prefix="cw-syn-alt-")
        os.close(outside_fd)
        self._tmpfiles.append(outside)
        rec = guard.classify_source(outside, repo)  # real file, but outside root
        self.assertEqual(rec["class"], "sensitive_source")
        self.assertEqual(rec["reason"], "source_outside_repo")


# --------------------------------------------------------------------------- #
# Approved-repo-path allowlist: approved relative path succeeds; disallowed one
# fails closed even when committed under an approved root.
# --------------------------------------------------------------------------- #

class RepoPathAllowlist(PortabilityCase):
    def test_approved_repo_path_succeeds(self):
        repo = self._repo()
        f = _commit(repo, "tools/ok.py", "print('ok')\n")
        self._config(IDENTITY, [repo])
        self.assertEqual(guard.classify_source(f, repo)["class"],
                         "approved_repo_file")

    def test_disallowed_repo_path_fails_closed(self):
        repo = self._repo()
        f = _commit(repo, "misc/nope.py", "print('nope')\n")  # not an approved path
        self._config(IDENTITY, [repo])
        rec = guard.classify_source(f, repo)
        self.assertEqual(rec["class"], "sensitive_source")
        self.assertEqual(rec["reason"], "source_outside_repo")


# --------------------------------------------------------------------------- #
# CI-env existence never approves.
# --------------------------------------------------------------------------- #

class CiEnvNeverApproves(PortabilityCase):
    def test_ci_env_vars_without_config_return_empty(self):
        repo = self._repo()
        fake_env = {"CI": "true", "GITHUB_ACTIONS": "true",
                    "GITHUB_WORKSPACE": repo, "RUNNER_OS": "Linux",
                    "BUILD_BUILDID": "42"}
        # No CLEARWRIGHT_EGRESS_LOCAL_CONFIG present in the environment mapping.
        self.assertEqual(
            guard.resolve_local_egress_config(POLICY, env_get=fake_env.get), [])

    def test_github_actions_process_env_without_config_fails_closed(self):
        repo = self._repo()
        f = _commit(repo, "tools/x.py", "print('x')\n")
        self.addCleanup(os.environ.pop, "GITHUB_ACTIONS", None)
        self.addCleanup(os.environ.pop, "CI", None)
        os.environ["GITHUB_ACTIONS"] = "true"
        os.environ["CI"] = "true"
        # ENV stays unset (setUp popped it) -> being "in CI" approves nothing.
        self.assertEqual(guard.classify_source(f, repo)["reason"],
                         "repo_unresolvable")


# --------------------------------------------------------------------------- #
# The committed policy is portable: no operator machine path, no roots key.
# --------------------------------------------------------------------------- #

class CommittedPolicyIsPortable(PortabilityCase):
    def test_committed_policy_has_no_operator_machine_path(self):
        with open(POLICY_PATH, "rb") as fh:
            raw = fh.read()
        self.assertNotIn(b"D:/AI-Agents", raw)
        self.assertNotIn(b"D:\\AI-Agents", raw)
        self.assertNotIn(b"D:/", raw)
        self.assertNotIn(b"D:\\", raw)

    def test_committed_policy_has_no_roots_key_and_declares_identity(self):
        with open(POLICY_PATH, "rb") as fh:
            pol = json.loads(fh.read().decode("utf-8"))
        self.assertNotIn("approved_repo_roots", pol)
        self.assertEqual(pol.get("approved_repo_identity"), IDENTITY)


# --------------------------------------------------------------------------- #
# Persisted provenance is content-free: no absolute path in the durable records.
# --------------------------------------------------------------------------- #

class PersistedProvenanceContentFree(PortabilityCase):
    def test_candidate_records_have_no_absolute_path(self):
        repo = self._repo()
        f = _commit(repo, "tools/x.py", "print('x')\n")
        self._config(IDENTITY, [repo])
        g, cand, binds = guard.build_candidate_graph([f], repo,
                                                     candidate_id="packet")
        self.assertEqual(g.decide_outcome(cand)["tier"], "standard")
        text = json.dumps(g.to_records())
        self.assertNotIn(repo, text)
        self.assertNotIn(os.path.realpath(repo), text)
        self.assertNotIn(os.path.normcase(os.path.realpath(repo)), text)
        # No Windows drive-letter absolute path leaked into the records.
        self.assertIsNone(re.search(r"[A-Za-z]:[\\/]", text))
        self.assertTrue(binds and "abspath" not in binds[0]
                        and binds[0].get("path_rel"))


# --------------------------------------------------------------------------- #
# Resolver contract (direct).
# --------------------------------------------------------------------------- #

class ResolverContract(PortabilityCase):
    def test_returns_realpath_normcase_absolute_roots(self):
        repo = self._repo()
        self._config(IDENTITY, [repo])
        self.assertEqual(guard.resolve_local_egress_config(POLICY),
                         [os.path.normcase(os.path.realpath(repo))])

    def test_identity_mismatch_returns_empty(self):
        repo = self._repo()
        self._config("nope/nope", [repo])
        self.assertEqual(guard.resolve_local_egress_config(POLICY), [])

    def test_default_env_get_reads_process_env(self):
        repo = self._repo()
        self._config(IDENTITY, [repo])  # sets os.environ[ENV]
        # Called with no env_get -> uses os.environ.get by default.
        self.assertEqual(guard.resolve_local_egress_config(POLICY),
                         [os.path.normcase(os.path.realpath(repo))])


if __name__ == "__main__":
    unittest.main()
