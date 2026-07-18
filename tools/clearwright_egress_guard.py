"""Sensitive-data egress guard: the single sanctioned boundary between
ClearWright and every council reviewer (GPT over HTTPS, Codex over stdin).

Authority: operator message msg-20260718T182514346423, locked to the Decision
1A / 2A terms of msg-20260718T185302215080 (plan council
cw-council-20260718T183358730364; gate gate-20260718T185018981643 resolved by
msg-20260718T185450073544).

Contract (locked):
  - Three outcomes only: SANITIZED_OK (dispatch), LOCAL_ONLY (ClearWright
    performs no provider call, transmits nothing, records no new content),
    STOP (refuse; error_class "egress_blocked"). Fail-closed: any policy,
    scanner, decode, or provenance error is STOP, never dispatch.
  - STANDARD tier is provenance-defined, never merely declared: packets may
    be assembled only from git-tracked files under the policy's approved
    repository paths, machine-generated analysis written in the current
    governed run's work directory, and synthetic test fixtures. Absent or
    ambiguous provenance resolves to SENSITIVE.
  - SENSITIVE material reaches a council only as a machine-constructed
    closed-schema derivative (sanitized_derivative-v1). The final serialized
    provider bytes must parse back into that schema with only permitted
    values (construction proof) — detectors are never the guarantee.
  - The exact serialized provider request is validated (GPT: the request
    body bytes; Codex: the exact stdin prompt), not an earlier draft.
  - No user or operator override may authorize raw PII or PHI transmission:
    this module exposes no bypass parameter, reads no bypass environment
    variable, and treats any instruction embedded in scanned content as data.
  - Logs and errors never echo blocked content: every refusal carries only
    category names, counts, hashes, and policy identifiers.

HONEST LIMITATION (Decision 2A): this is an application-level boundary. It
does not and cannot defend against a malicious process already executing
under the same Windows user identity, which could read credentials or invoke
providers directly. An OS-enforced perimeter (restricted broker identity,
firewall egress rules) is a separately governed future item (2B) and is NOT
implemented here; no system-security settings are touched.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
import time
import urllib.request

TOOLS_DIR = os.path.dirname(os.path.abspath(__file__))
POLICY_PATH = os.path.join(TOOLS_DIR, "egress_policy.json")

OUTCOME_SANITIZED_OK = "sanitized_ok"
OUTCOME_LOCAL_ONLY = "local_only"   # == NO_DISPATCH: ClearWright transmits nothing
OUTCOME_STOP = "stop"

ERROR_CLASS = "egress_blocked"

# --- Sensitivity lattice (monotonic): STANDARD < SENSITIVE. SANITIZED_OK is a
# SEPARATE derivative classification, not a point on this axis. ---
SENSITIVITY_STANDARD = "standard"
SENSITIVITY_SENSITIVE = "sensitive"
_SENS_ORDER = {SENSITIVITY_STANDARD: 0, SENSITIVITY_SENSITIVE: 1}

# Lineage node classifications.
CLASS_RAW = "raw"
CLASS_MACHINE = "machine_generated"
CLASS_SANITIZED_OK = "sanitized_ok"

# Provenance classes that establish a RAW node as STANDARD. Anything else
# (including None / unknown) is SENSITIVE (fail-closed). NOTE: there is NO
# "machine_generated_in_run" or "created_by_clearwright" class here — a file is
# never STANDARD merely because ClearWright/Claude produced it or because it
# lives under runtime/work, the cwd, or a plan/generated path. A machine-
# generated artifact earns STANDARD only through CLASS_MACHINE with a complete
# STANDARD ancestry of git-verified sources.
_STANDARD_PROVENANCE = ("approved_repo_file", "synthetic_fixture")

# The ONE approved sanitizer + the ONE approved closed-schema domain. Non-
# clinical sensitive content has no approved schema yet => NO_DISPATCH.
SANITIZER_ID = "clearwright_clinical_sanitizer/v1"
CLINICAL_DOMAIN = "clinical"
NON_CLINICAL_DOMAINS = ("legal", "financial", "employment", "personnel", "other")

# Reason codes are the ONLY vocabulary refusals may use (content-safe).
REASONS = (
    "policy_missing", "policy_unreadable", "policy_invalid",
    "policy_hash_mismatch", "policy_version_unknown", "scanner_exception",
    "undecodable_bytes", "partial_scan", "tripwire_hit",
    "provenance_unverified", "provenance_outside_allowlist",
    "provenance_untracked_file", "paste_suspected",
    "construction_parse_failed", "construction_schema_violation",
    "construction_value_not_permitted", "context_missing",
    "sensitive_requires_derivative", "caller_not_registered",
    # lineage invariant
    "lineage_source_missing", "lineage_cycle", "lineage_unverifiable",
    "lineage_standard_over_sensitive", "lineage_ambiguous",
    "sanitized_not_from_sanitizer", "sanitized_policy_stale",
    "sanitized_no_construction_proof", "sensitivity_downgrade_forbidden",
    "domain_unsupported", "provider_key_missing", "bytes_mutated_after_validation",
    # live provenance / lineage enforcement
    "lineage_missing", "candidate_missing", "source_symlink",
    "source_traversal", "source_outside_repo", "source_not_a_file",
    "source_mutated_after_verification", "repo_unresolvable",
)


class EgressBlocked(Exception):
    """Raised on every refusal. Carries a content-free, machine-readable
    summary only — never matched text, never source bytes."""

    def __init__(self, reason, summary=None):
        self.reason = reason
        self.summary = dict(summary or {})
        self.summary.setdefault("reason", reason)
        self.summary.setdefault("error_class", ERROR_CLASS)
        super().__init__("egress blocked: {}".format(reason))


# --------------------------------------------------------------------------- #
# Policy loading (fail-closed matrix)
# --------------------------------------------------------------------------- #

_KNOWN_POLICY_VERSIONS = ("1.0.0",)


def _sha256_bytes(data):
    return hashlib.sha256(data).hexdigest()


def load_policy(path=POLICY_PATH, expected_sha=None):
    """Load and validate the egress policy. EVERY failure raises EgressBlocked
    (STOP): missing, unreadable, invalid schema, unknown version, or an
    expected-hash mismatch. Returns {policy, policy_sha256}."""
    if not os.path.isfile(path):
        raise EgressBlocked("policy_missing")
    try:
        with open(path, "rb") as fh:
            raw = fh.read()
    except OSError:
        raise EgressBlocked("policy_unreadable")
    sha = _sha256_bytes(raw)
    if expected_sha and sha != expected_sha:
        raise EgressBlocked("policy_hash_mismatch",
                            {"expected_sha256": expected_sha, "actual_sha256": sha})
    try:
        policy = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError):
        raise EgressBlocked("policy_invalid")
    if not isinstance(policy, dict):
        raise EgressBlocked("policy_invalid")
    version = policy.get("policy_version")
    if version not in _KNOWN_POLICY_VERSIONS:
        raise EgressBlocked("policy_version_unknown", {"policy_version": version})
    for key in ("tripwires", "contextual_terms", "identity_signals",
                "approved_repo_roots", "approved_repo_paths",
                "synthetic_fixture_paths",
                "controlled_vocabulary", "derivative_templates"):
        if key not in policy:
            raise EgressBlocked("policy_invalid", {"missing_key": key})
    return {"policy": policy, "policy_sha256": sha, "policy_version": version}


# --------------------------------------------------------------------------- #
# Detectors. Two INDEPENDENT compilations: classify() (pre-packet) and
# final_scan() (exact outbound bytes) build their own pattern objects from
# the policy so a defect in one table cannot silently disable the other.
# --------------------------------------------------------------------------- #

def _compile(policy):
    tripwires = {name: re.compile(pat, re.IGNORECASE)
                 for name, pat in policy["tripwires"].items()}
    contextual = re.compile("|".join(policy["contextual_terms"]), re.IGNORECASE) \
        if policy["contextual_terms"] else None
    identity = re.compile("|".join(policy["identity_signals"]), re.IGNORECASE) \
        if policy["identity_signals"] else None
    return tripwires, contextual, identity


_CONFUSABLE = re.compile(r"[а-яΑ-ω‐-―！-～]")
_CO_OCCUR_WINDOW = 240  # chars: contextual term + identity signal proximity


def _scan_text(text, policy):
    """Shared detector core. Returns a content-free findings summary:
    {category: count}. Never returns matched text."""
    tripwires, contextual, identity = _compile(policy)
    findings = {}
    for name, pat in tripwires.items():
        n = len(pat.findall(text))
        if n:
            findings[name] = n
    if contextual is not None and identity is not None:
        for m in contextual.finditer(text):
            lo = max(0, m.start() - _CO_OCCUR_WINDOW)
            hi = min(len(text), m.end() + _CO_OCCUR_WINDOW)
            if identity.search(text[lo:hi]):
                findings["contextual_identity"] = findings.get(
                    "contextual_identity", 0) + 1
    if _CONFUSABLE.search(text):
        findings["unicode_confusable"] = len(_CONFUSABLE.findall(text))
    return findings


def classify(text, loaded=None):
    """Pre-packet classification. Returns {findings, verdict} where verdict is
    "clear" or "hit". Any scanner exception is re-raised as STOP."""
    loaded = loaded or load_policy()
    try:
        findings = _scan_text(text, loaded["policy"])
    except EgressBlocked:
        raise
    except Exception:
        raise EgressBlocked("scanner_exception")
    return {"findings": findings, "verdict": "hit" if findings else "clear",
            "policy_version": loaded["policy_version"],
            "policy_sha256": loaded["policy_sha256"],
            "input_sha256": _sha256_bytes(text.encode("utf-8", "replace"))}


def final_scan(data_bytes, loaded=None):
    """Second, independent scan of the EXACT outbound bytes. Independent
    compilation; decode failure, partial scan, or any exception is STOP."""
    loaded = loaded or load_policy()
    if not isinstance(data_bytes, (bytes, bytearray)):
        raise EgressBlocked("scanner_exception", {"detail": "bytes required"})
    try:
        text = bytes(data_bytes).decode("utf-8")
    except UnicodeDecodeError:
        raise EgressBlocked("undecodable_bytes")
    try:
        findings = _scan_text(text, loaded["policy"])
    except EgressBlocked:
        raise
    except Exception:
        raise EgressBlocked("scanner_exception")
    if len(text.encode("utf-8")) != len(bytes(data_bytes)):
        raise EgressBlocked("partial_scan")
    return {"findings": findings, "verdict": "hit" if findings else "clear",
            "policy_version": loaded["policy_version"],
            "policy_sha256": loaded["policy_sha256"],
            "input_sha256": _sha256_bytes(bytes(data_bytes))}


# --------------------------------------------------------------------------- #
# Provenance (STANDARD tier is provenance-DEFINED, never declared)
# --------------------------------------------------------------------------- #

_PASTE_BLOCK = re.compile(r"(^|\n)\s*(>{1,3}\s|\"{3}|'{3})", re.MULTILINE)
_QUOTED_RUN = re.compile(r'"[^"\n]{240,}"')


def _git_tracked(repo, rel_path):
    try:
        proc = subprocess.run(
            ["git", "-C", repo, "ls-files", "--error-unmatch", "--", rel_path],
            capture_output=True, text=True, timeout=30)
        return proc.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def _git_head(repo):
    try:
        r = subprocess.run(["git", "-C", repo, "rev-parse", "HEAD"],
                           capture_output=True, text=True, timeout=30)
        return (r.stdout or "").strip() if r.returncode == 0 else None
    except (OSError, subprocess.TimeoutExpired):
        return None


def _git_unmodified(repo, rel_path):
    """True only when the working-tree file is IDENTICAL to its committed
    content at HEAD, using git's own (autocrlf/eol-aware) comparison. A locally
    modified, staged-but-uncommitted, or otherwise-diverged tracked file returns
    False (its content is not provably the committed STANDARD content). Any git
    error is fail-closed (False)."""
    try:
        r = subprocess.run(["git", "-C", repo, "diff", "--quiet", "HEAD",
                            "--", rel_path], capture_output=True, timeout=30)
        return r.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def _sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _under(root_real, target_real):
    return target_real == root_real or target_real.startswith(root_real + os.sep)


def classify_source(path, repo, loaded=None):
    """Classify ONE declared source file's provenance for live lineage. Returns
    a content-free provenance record whose ``class`` is a member of
    _STANDARD_PROVENANCE (approved_repo_file / synthetic_fixture) ONLY when the
    file is provably a clean, git-tracked (or approved synthetic) file under an
    approved repository path, with its content hash captured. EVERYTHING else —
    a symlink, a path that escapes the repo root by traversal or an alternate
    reference, a file outside approved paths, an untracked/ignored file, a
    missing file, or an unresolvable repo — yields a SENSITIVE class (so the
    candidate resolves SENSITIVE and dispatch fails closed). Never returns a
    raw path or content; approved repo-relative paths are public and are kept
    for TOCTOU re-verification, all other identifiers are hashed.

    Path-confinement is checked on the REAL (symlink-resolved) path so a symlink
    or junction inside the repo cannot point outside the approved roots, and a
    symlink is refused outright (it is not itself a tracked source)."""
    loaded = loaded or load_policy()
    policy = loaded["policy"]
    approved = [p.replace("\\", "/") for p in policy["approved_repo_paths"]]
    fixtures = [p.replace("\\", "/") for p in policy["synthetic_fixture_paths"]]
    if not repo:
        return {"class": "sensitive_source", "reason": "repo_unresolvable"}
    try:
        repo_real = os.path.realpath(repo)
    except OSError:
        return {"class": "sensitive_source", "reason": "repo_unresolvable"}
    # Repo IDENTITY binding: the repo must be one of the policy's approved
    # absolute roots. A caller cannot point --repo at an attacker-controlled
    # clone to mint approved_repo_file classifications. Empty allowlist => fail
    # closed. Case-normalized on Windows.
    roots = []
    for r in (policy.get("approved_repo_roots") or []):
        try:
            roots.append(os.path.normcase(os.path.realpath(r)))
        except OSError:
            continue
    if os.path.normcase(repo_real) not in roots:
        return {"class": "sensitive_source", "reason": "repo_unresolvable"}
    lex = os.path.abspath(path)
    # A symlink source is never a tracked source (it is an alternate reference).
    if os.path.islink(path) or os.path.islink(lex):
        return {"class": "sensitive_source", "reason": "source_symlink",
                "path_sha16": _sha256_bytes(lex.encode())[:16]}
    try:
        real = os.path.realpath(lex)
    except OSError:
        return {"class": "sensitive_source", "reason": "source_not_a_file"}
    # Confinement is enforced on BOTH the lexical and the real path so neither a
    # ".." traversal nor a symlink target can escape the repo root.
    if not (_under(repo_real, real) and _under(repo_real, lex)):
        return {"class": "sensitive_source", "reason": "source_outside_repo",
                "path_sha16": _sha256_bytes(real.encode())[:16]}
    if not os.path.isfile(real):
        return {"class": "sensitive_source", "reason": "source_not_a_file"}
    rel = os.path.relpath(real, repo_real).replace("\\", "/")
    if ".." in rel.split("/"):
        return {"class": "sensitive_source", "reason": "source_traversal"}
    in_fixtures = any(rel.startswith(a) or (a.startswith("*.") and rel.endswith(a[1:]))
                      for a in fixtures)
    in_approved = any(rel.startswith(a) or (a.startswith("*.") and rel.endswith(a[1:]))
                      for a in approved)
    if not (in_fixtures or in_approved):
        return {"class": "sensitive_source", "reason": "source_outside_repo",
                "path_sha16": _sha256_bytes(rel.encode())[:16]}
    sha = _sha256_file(real)
    # Content-free provenance: NO absolute path is persisted (only the public
    # repo-relative path, the content hash, and the class/reason). The runtime
    # abspath needed for TOCTOU re-reads is reconstructed from repo + path_rel.
    if in_fixtures and not in_approved:
        return {"class": "synthetic_fixture", "path_rel": rel,
                "sha256": sha, "repo": repo_real}
    # approved repo file must be git-tracked at the current commit
    if not _git_tracked(repo_real, rel):
        return {"class": "sensitive_source", "reason": "source_untracked",
                "path_sha16": _sha256_bytes(rel.encode())[:16]}
    # ...and its working-tree content must be IDENTICAL to the committed content
    # at HEAD. A locally-modified/uncommitted tracked file carries unverifiable
    # content and is therefore SENSITIVE — only committed content is provably
    # STANDARD. (git's own comparison so autocrlf/eol filters do not false-flag.)
    if not _git_unmodified(repo_real, rel):
        return {"class": "sensitive_source", "reason": "source_uncommitted",
                "path_sha16": _sha256_bytes(rel.encode())[:16]}
    return {"class": "approved_repo_file", "path_rel": rel,
            "sha256": sha, "repo": repo_real, "repo_commit": _git_head(repo_real)}


def build_candidate_graph(source_paths, repo, *, candidate_id="cand",
                          domain=None, inline_unverified=False, loaded=None):
    """Assemble the live lineage that BINDS the outbound packet to its content
    sources: one RAW node per content-bearing source (the packet text file(s)
    AND every inlined artifact — classified by classify_source), plus a
    machine_generated candidate over them. ``inline_unverified`` adds an
    un-provenanced RAW SENSITIVE node (used when the packet carries inline
    prompt text or has no content file), so inline/pasted content forces
    SENSITIVE. Returns (graph, candidate_id, standard_bindings) where each
    binding is {repo, path_rel, sha256} of a verified-STANDARD source (the
    abspath is reconstructed at the TOCTOU re-check, never persisted).
    Fail-closed: no sources, any sensitive/failed source, or inline content =>
    the candidate resolves SENSITIVE via the graph."""
    loaded = loaded or load_policy()
    g = LineageGraph()
    bindings = []
    src_ids = []
    for i, p in enumerate(source_paths or []):
        rec = classify_source(p, repo, loaded)
        # persist only content-free provenance (no abspath)
        prov = {k: v for k, v in rec.items() if k != "abspath"}
        nid = "src-{}-{}".format(i, (rec.get("path_rel") and
                                      _sha256_bytes(rec["path_rel"].encode())[:12])
                                 or rec.get("path_sha16") or _sha256_bytes(str(p).encode())[:12])
        g.add(nid, CLASS_RAW, provenance=prov)
        src_ids.append(nid)
        if rec.get("class") in _STANDARD_PROVENANCE and rec.get("path_rel") and rec.get("repo"):
            bindings.append({"repo": rec["repo"], "path_rel": rec["path_rel"],
                             "sha256": rec["sha256"]})
    if inline_unverified:
        g.add("inline-unverified", CLASS_RAW,
              provenance={"class": "sensitive_source", "reason": "inline_content"})
        src_ids.append("inline-unverified")
    g.add(candidate_id, CLASS_MACHINE, source_ids=src_ids, domain=domain)
    return g, candidate_id, bindings


def paste_suspicion(text):
    """Heuristic: long quoted runs or block-quote markers suggest pasted
    external material, which is categorically SENSITIVE. Content-free result."""
    hits = 0
    hits += len(_PASTE_BLOCK.findall(text))
    hits += len(_QUOTED_RUN.findall(text))
    return hits


# --------------------------------------------------------------------------- #
# Construction proof (SENSITIVE tier): the exact provider bytes must BE a
# valid sanitized_derivative-v1 envelope — permitted values only.
# --------------------------------------------------------------------------- #

_TOKEN = re.compile(r"^(PERSON|CLINICIAN|FACILITY|PLACE)-\d{1,4}$")
_OFFSET = re.compile(r"^T[+-]\d{1,5}d$")
_BUCKET = re.compile(r"^[a-z0-9_]+:[a-z0-9_\-]+$")


def construction_proof(payload_text, loaded=None):
    """Parse the sensitive-tier packet text back into sanitized_derivative-v1
    and verify EVERY string is a template string, vocabulary member, neutral
    token, relative offset, or bucket label. Any deviation is STOP. This is
    the guarantee for sensitive dispatch — detectors are only backstop."""
    loaded = loaded or load_policy()
    policy = loaded["policy"]
    vocab = set(policy["controlled_vocabulary"])
    templates = policy["derivative_templates"]
    # The CLOSED set of permitted bucket labels comes from the policy; the regex
    # shape is necessary but NOT sufficient (a regex-only bucket is a free-text
    # covert channel). Every bucket value must be a member of this allowlist.
    allowed_buckets = set()
    for vals in (policy.get("buckets") or {}).values():
        allowed_buckets.update(vals or [])
    try:
        doc = json.loads(payload_text)
    except ValueError:
        raise EgressBlocked("construction_parse_failed")
    if not isinstance(doc, dict) or doc.get("schema") != "sanitized_derivative-v1":
        raise EgressBlocked("construction_schema_violation")
    if doc.get("policy_version") != loaded["policy_version"]:
        raise EgressBlocked("construction_schema_violation",
                            {"field": "policy_version"})
    template_id = doc.get("template_id")
    if template_id not in templates:
        raise EgressBlocked("construction_schema_violation",
                            {"field": "template_id"})
    fields = doc.get("fields")
    if not isinstance(fields, list) or not fields:
        # An empty field list is not a valid clinical derivative (and an empty
        # derivative wrapped in raw content is a known cover trick).
        raise EgressBlocked("construction_schema_violation", {"field": "fields"})
    for i, f in enumerate(fields):
        if not isinstance(f, dict):
            raise EgressBlocked("construction_schema_violation", {"index": i})
        extra = set(f) - {"code", "token", "offset", "bucket", "flag"}
        if extra:
            raise EgressBlocked("construction_schema_violation",
                                {"index": i, "unexpected_keys": sorted(extra)})
        if "code" not in f or f["code"] not in vocab:
            raise EgressBlocked("construction_value_not_permitted",
                                {"index": i, "field": "code"})
        if "token" in f and not _TOKEN.match(str(f["token"])):
            raise EgressBlocked("construction_value_not_permitted",
                                {"index": i, "field": "token"})
        if "offset" in f and not _OFFSET.match(str(f["offset"])):
            raise EgressBlocked("construction_value_not_permitted",
                                {"index": i, "field": "offset"})
        if "bucket" in f and (not _BUCKET.match(str(f["bucket"]))
                              or str(f["bucket"]) not in allowed_buckets):
            raise EgressBlocked("construction_value_not_permitted",
                                {"index": i, "field": "bucket"})
        if "flag" in f and not isinstance(f["flag"], bool):
            raise EgressBlocked("construction_value_not_permitted",
                                {"index": i, "field": "flag"})
    allowed_top = {"schema", "policy_version", "template_id", "fields"}
    if set(doc) - allowed_top:
        raise EgressBlocked("construction_schema_violation",
                            {"unexpected_keys": sorted(set(doc) - allowed_top)})
    return {"template_id": template_id, "field_count": len(fields),
            "policy_version": loaded["policy_version"],
            "policy_sha256": loaded["policy_sha256"],
            "input_sha256": _sha256_bytes(payload_text.encode("utf-8"))}


# --------------------------------------------------------------------------- #
# Sensitivity-lineage invariant (non-bypassable, monotonic, fail-closed).
#
# A lineage graph is {node_id -> node}. Each node is content-free:
#   {"id", "classification" (raw|machine_generated|sanitized_ok),
#    "source_ids" [..], "provenance" {"class": ...}|None,
#    "escalated" bool (operator STANDARD->SENSITIVE only), "domain" str|None,
#    "sanitizer" {"sanitizer_id","policy_version","policy_sha256",
#                 "construction_proof": {...}}|None }
#
# Rules (operator-mandated):
#  - Sensitivity is monotonic: STANDARD < SENSITIVE; a node is SENSITIVE if ANY
#    ancestor is SENSITIVE, if operator-escalated, or if its own provenance is
#    missing/unverified/ambiguous.
#  - Machine-generated is STANDARD only when EVERY direct source resolves
#    independently to STANDARD.
#  - Missing source, unverifiable provenance, a cycle, or a STANDARD claim over
#    a SENSITIVE ancestor is fail-closed.
#  - The operator may escalate STANDARD->SENSITIVE, never the reverse.
# --------------------------------------------------------------------------- #

class LineageGraph(object):
    """A content-free lineage graph. Add nodes, then resolve_sensitivity or
    decide_outcome. All failures raise EgressBlocked (STOP)."""

    def __init__(self):
        self._nodes = {}

    def add(self, node_id, classification, *, source_ids=None, provenance=None,
            escalated=False, domain=None, sanitizer=None):
        if classification not in (CLASS_RAW, CLASS_MACHINE, CLASS_SANITIZED_OK):
            raise EgressBlocked("lineage_unverifiable",
                                {"detail": "bad classification"})
        self._nodes[node_id] = {
            "id": node_id, "classification": classification,
            "source_ids": list(source_ids or []), "provenance": provenance,
            "escalated": bool(escalated), "domain": domain, "sanitizer": sanitizer}
        return node_id

    def get(self, node_id):
        return self._nodes.get(node_id)

    def to_records(self):
        """Content-free node list, durable and rebuildable. Provenance keeps
        only classifications/hashes/commits/reason codes and (for public
        approved repo files) the repo-relative path — never content."""
        return [dict(n) for n in self._nodes.values()]

    @classmethod
    def from_records(cls, records):
        g = cls()
        for n in records or []:
            g.add(n["id"], n["classification"], source_ids=n.get("source_ids"),
                  provenance=n.get("provenance"), escalated=n.get("escalated", False),
                  domain=n.get("domain"), sanitizer=n.get("sanitizer"))
        return g

    def resolve_sensitivity(self, node_id, _stack=None):
        """Monotonic, fail-closed resolution. Returns SENSITIVITY_STANDARD or
        SENSITIVITY_SENSITIVE; raises EgressBlocked on any lineage defect."""
        _stack = _stack or ()
        if node_id in _stack:
            raise EgressBlocked("lineage_cycle", {"node_sha16": _h(node_id)})
        node = self._nodes.get(node_id)
        if node is None:
            raise EgressBlocked("lineage_source_missing", {"node_sha16": _h(node_id)})
        if node["escalated"]:
            return SENSITIVITY_SENSITIVE
        cls = node["classification"]
        if cls == CLASS_RAW:
            prov = node.get("provenance") or {}
            pclass = prov.get("class")
            # Monotonic: a RAW node that declares standard provenance still
            # inherits the MAX sensitivity of any declared sources. A raw leaf
            # normally has none; a raw node carrying sources cannot launder a
            # sensitive ancestor to standard by asserting a standard class.
            if node["source_ids"]:
                worst = self._max_sources(node_id, _stack)
                if pclass in _STANDARD_PROVENANCE:
                    return worst
                return SENSITIVITY_SENSITIVE
            if pclass in _STANDARD_PROVENANCE:
                return SENSITIVITY_STANDARD
            # missing / unknown / ambiguous provenance is SENSITIVE
            return SENSITIVITY_SENSITIVE
        if cls == CLASS_SANITIZED_OK:
            # A sanitized_ok node's own sensitivity for lattice purposes is the
            # max of its sources (its source stays SENSITIVE); dispatchability
            # is decided separately in decide_outcome. Resolving it here still
            # enforces source integrity.
            return self._max_sources(node_id, _stack)
        # machine_generated: STANDARD only if EVERY source is STANDARD.
        if not node["source_ids"]:
            # A generated artifact with no declared sources is ambiguous.
            raise EgressBlocked("lineage_ambiguous", {"node_sha16": _h(node_id)})
        return self._max_sources(node_id, _stack)

    def _max_sources(self, node_id, _stack):
        node = self._nodes[node_id]
        worst = SENSITIVITY_STANDARD
        for sid in node["source_ids"]:
            s = self.resolve_sensitivity(sid, _stack + (node_id,))
            if _SENS_ORDER[s] > _SENS_ORDER[worst]:
                worst = s
        return worst

    def assert_no_standard_over_sensitive(self, node_id):
        """Explicit fail-closed check: a node CLAIMING standard (via a standard
        provenance class or an asserted standard sensitivity) while any ancestor
        resolves SENSITIVE."""
        node = self._nodes.get(node_id)
        if node is None:
            raise EgressBlocked("lineage_source_missing", {"node_sha16": _h(node_id)})
        claims_standard = (node.get("provenance") or {}).get("class") in _STANDARD_PROVENANCE
        # Any node with sources that resolves SENSITIVE but claims a standard
        # provenance/sensitivity is a laundering attempt (covers both machine-
        # generated derivations and raw nodes that carry sources).
        if node["classification"] in (CLASS_MACHINE, CLASS_RAW) and node["source_ids"]:
            resolved = self._max_sources(node_id, (node_id,))
            asserted = (node.get("provenance") or {}).get("asserted_sensitivity")
            if resolved == SENSITIVITY_SENSITIVE and (claims_standard or asserted == SENSITIVITY_STANDARD):
                raise EgressBlocked("lineage_standard_over_sensitive",
                                    {"node_sha16": _h(node_id)})

    def decide_outcome(self, node_id, loaded=None):
        """The dispatch decision for an outbound candidate. Returns one of
        OUTCOME_SANITIZED_OK / (a "standard" marker) / OUTCOME_LOCAL_ONLY, or
        raises EgressBlocked. Fail-closed throughout."""
        loaded = loaded or load_policy()
        node = self._nodes.get(node_id)
        if node is None:
            raise EgressBlocked("lineage_source_missing", {"node_sha16": _h(node_id)})
        self.assert_no_standard_over_sensitive(node_id)
        if node["classification"] == CLASS_SANITIZED_OK:
            san = node.get("sanitizer") or {}
            if san.get("sanitizer_id") != SANITIZER_ID:
                raise EgressBlocked("sanitized_not_from_sanitizer")
            if san.get("policy_sha256") != loaded["policy_sha256"] or \
                    san.get("policy_version") != loaded["policy_version"]:
                raise EgressBlocked("sanitized_policy_stale")
            if not san.get("construction_proof"):
                raise EgressBlocked("sanitized_no_construction_proof")
            if node.get("domain") != CLINICAL_DOMAIN:
                raise EgressBlocked("domain_unsupported", {"domain": node.get("domain")})
            # verify the source integrity still resolves (sources may be sensitive)
            self._max_sources(node_id, (node_id,))
            return {"outcome": OUTCOME_SANITIZED_OK, "tier": "sensitive"}
        eff = self.resolve_sensitivity(node_id)
        if eff == SENSITIVITY_STANDARD:
            return {"outcome": "standard_ok", "tier": "standard"}
        # SENSITIVE raw/machine content cannot dispatch directly.
        domain = node.get("domain")
        if domain in NON_CLINICAL_DOMAINS:
            return {"outcome": OUTCOME_LOCAL_ONLY, "tier": "sensitive",
                    "reason": "domain_unsupported"}
        # clinical (or unknown) sensitive content must be sanitized first.
        raise EgressBlocked("sensitive_requires_derivative", {"node_sha16": _h(node_id)})


def _h(s):
    return _sha256_bytes(str(s).encode("utf-8"))[:16]


# --------------------------------------------------------------------------- #
# Approved clinical sanitizer: the ONLY producer of SANITIZED_OK artifacts.
# Input is an ALREADY-STRUCTURED clinical field set (built inside the trusted
# local boundary from the raw source); the sanitizer validates it into the
# closed schema, construction-proves it, and registers a content-free
# sanitized_ok lineage node. It never sees or emits raw prose, filenames,
# metadata, or reversible identity maps.
# --------------------------------------------------------------------------- #

def sanitize_clinical(structured_fields, *, template_id, source_node_id,
                      graph, domain=CLINICAL_DOMAIN, loaded=None):
    """Produce a SANITIZED_OK derivative + lineage node from a structured
    clinical field set. Refuses any non-clinical domain (NO_DISPATCH belongs to
    those). Returns (derivative_json_text, sanitized_node_id, proof). Does NOT
    alter the source node's sensitivity."""
    loaded = loaded or load_policy()
    if domain != CLINICAL_DOMAIN:
        raise EgressBlocked("domain_unsupported", {"domain": domain})
    if graph.get(source_node_id) is None:
        raise EgressBlocked("lineage_source_missing")
    if template_id not in loaded["policy"]["derivative_templates"]:
        raise EgressBlocked("construction_schema_violation", {"field": "template_id"})
    doc = {"schema": "sanitized_derivative-v1",
           "policy_version": loaded["policy_version"],
           "template_id": template_id,
           "fields": structured_fields}
    payload = json.dumps(doc, separators=(",", ":"), sort_keys=True)
    # Construction proof is REQUIRED for SANITIZED_OK.
    proof = construction_proof(payload, loaded)
    sanitizer = {"sanitizer_id": SANITIZER_ID,
                 "policy_version": loaded["policy_version"],
                 "policy_sha256": loaded["policy_sha256"],
                 "construction_proof": proof}
    node_id = "san-" + _sha256_bytes(payload.encode("utf-8"))[:16]
    graph.add(node_id, CLASS_SANITIZED_OK, source_ids=[source_node_id],
              domain=CLINICAL_DOMAIN, sanitizer=sanitizer)
    return payload, node_id, proof


# --------------------------------------------------------------------------- #
# Egress context: dispatch metadata the transports REQUIRE (fail-closed).
# --------------------------------------------------------------------------- #

class EgressContext(object):
    """Content-free dispatch context. ``tier`` is "standard" or "sensitive".
    When a lineage ``graph`` + ``candidate_id`` are supplied, the transport
    derives the TRUE outcome from lineage (monotonic, fail-closed) and enforces
    that a declared tier can never be LESS sensitive than the resolved one — a
    declared "standard" over sensitive lineage is refused."""

    def __init__(self, tier, provenance=None, work_item_id=None,
                 graph=None, candidate_id=None, domain=None,
                 source_bindings=None, require_graph=False):
        if tier not in ("standard", "sensitive"):
            raise EgressBlocked("context_missing", {"detail": "bad tier"})
        self.tier = tier
        self.provenance = provenance
        self.work_item_id = work_item_id
        self.graph = graph
        self.candidate_id = candidate_id
        self.domain = domain
        # {abspath, sha256} of verified-STANDARD sources, for the TOCTOU re-check
        # at dispatch (any source mutation after verification blocks the send).
        self.source_bindings = list(source_bindings or [])
        # The LIVE production path sets require_graph=True: a missing graph or
        # candidate id is fail-closed, never a fallback to the declared tier.
        self.require_graph = bool(require_graph)

    def resolve(self, loaded=None):
        """Return the enforced {outcome, tier} for this dispatch. With a lineage
        graph, the graph DECIDES and the declared tier may only ESCALATE it.
        On the live path (require_graph) a missing graph/candidate fails closed;
        there is no fallback to a declared tier."""
        if self.graph is not None and self.candidate_id is not None:
            decision = self.graph.decide_outcome(self.candidate_id, loaded)
            # Escalation-only (rule 4): an explicit SENSITIVE declaration forces
            # SENSITIVE even over standard-resolving lineage — the item may then
            # dispatch only as a construction-proven derivative, never as a plain
            # standard packet.
            if self.tier == "sensitive" and decision["tier"] == "standard":
                return {"outcome": OUTCOME_SANITIZED_OK, "tier": "sensitive",
                        "escalated": True}
            # A declared standard tier can NEVER override a resolved sensitive
            # outcome (no downgrade).
            if decision["tier"] == "sensitive" and self.tier == "standard":
                raise EgressBlocked("sensitivity_downgrade_forbidden")
            return decision
        if self.require_graph:
            raise EgressBlocked("lineage_missing" if self.graph is None
                                else "candidate_missing")
        return {"outcome": ("sanitized_ok" if self.tier == "sensitive"
                            else "standard_ok"), "tier": self.tier}

    def verify_source_bindings(self):
        """TOCTOU: re-hash every verified-STANDARD source and refuse dispatch on
        any change or disappearance since verification. The abspath is
        reconstructed from the content-free {repo, path_rel} binding (or a raw
        abspath if one was supplied directly in a test)."""
        for b in self.source_bindings:
            ap = b.get("abspath")
            if not ap and b.get("repo") and b.get("path_rel"):
                ap = os.path.join(b["repo"], b["path_rel"])
            try:
                cur = _sha256_file(ap)
            except (OSError, TypeError):
                raise EgressBlocked("source_mutated_after_verification",
                                    {"detail": "missing"})
            if cur != b.get("sha256"):
                raise EgressBlocked("source_mutated_after_verification")


_REGISTERED_CALLERS = set()


def register_adapter(module_name):
    """Approved adapter modules register at import. The transports refuse
    callers that never registered (accident prevention, not a security
    boundary — see the module docstring's honest limitation)."""
    _REGISTERED_CALLERS.add(module_name)


def _require_registered(caller):
    if caller not in _REGISTERED_CALLERS:
        raise EgressBlocked("caller_not_registered", {"caller": str(caller)})


# --------------------------------------------------------------------------- #
# Guard-owned provider egress. THE ONLY sanctioned dispatch paths. The guard
# owns the provider URL, the Authorization header, credential resolution, and
# the wire transport. Adapters never see any of them (Decision 2A).
# --------------------------------------------------------------------------- #

OPENAI_RESPONSES_URL = "https://api.openai.com/v1/responses"


def _real_transport(url, headers, body_bytes, timeout):
    """The one wire transport. Lives ONLY in the guard; no adapter retains a
    provider transport."""
    import urllib.error
    req = urllib.request.Request(url, data=bytes(body_bytes), headers=headers,
                                 method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.getcode(), resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        try:
            body = exc.read().decode("utf-8", "replace")
        except Exception:  # noqa: BLE001
            body = ""
        return exc.code, body


# The guard OWNS the fixed reviewer instruction for sensitive dispatch. Because
# the sensitive outbound is validated by byte-equality against a guard-built
# canonical form, this scaffolding text is fixed and cannot become a smuggling
# channel.
SENSITIVE_INSTRUCTION = (
    "Independent clinical-evidence reviewer. The user message is a de-identified,"
    " closed-schema derivative (category codes, neutral tokens, relative offsets,"
    " bucket labels only). Review it and respond with the standard structured"
    " verdict JSON. Do not infer or request identities; there are none.")


def build_sensitive_gpt_body(model, derivative_text, max_output_tokens):
    """The CANONICAL sensitive GPT request bytes: a fixed scaffold plus the
    construction-proven derivative as the ONLY user content. The guard is the
    sole author of this shape, so nothing can be wrapped around the derivative."""
    body = {"model": model,
            "input": [{"role": "developer", "content": SENSITIVE_INSTRUCTION},
                      {"role": "user", "content": derivative_text}],
            "max_output_tokens": max_output_tokens}
    # ensure_ascii=False so the wire bytes carry the REAL characters and the
    # guard's final_scan (incl. the Unicode-confusable detector) sees exactly
    # what the model will read, not \\uXXXX escapes.
    return json.dumps(body, ensure_ascii=False).encode("utf-8")


def build_sensitive_codex_prompt(derivative_text):
    """The CANONICAL sensitive Codex stdin: a fixed scaffold plus exactly one
    construction-proven derivative block, and nothing else."""
    return (SENSITIVE_INSTRUCTION + "\nBEGIN_DERIVATIVE\n" + derivative_text
            + "\nEND_DERIVATIVE\n")


def _enforce(data_bytes, context, loaded, *, codex_prompt=None):
    """Resolve the enforced outcome from the context (lineage-driven when a
    graph is present) and validate the EXACT outbound bytes for that outcome.
    NO_DISPATCH/local_only never transmits. Fail-closed. Returns the decision.

    The full-bytes tripwire scan gates BOTH tiers. On the sensitive tier the
    guarantee is byte-equality against a guard-built canonical form, so no free
    text (Codex stdin outside the block; GPT top-level fields, extra roles, or
    list-form content) can accompany the construction-proven derivative."""
    if context is None or not isinstance(context, EgressContext):
        raise EgressBlocked("context_missing")
    decision = context.resolve(loaded)
    if decision["outcome"] == OUTCOME_LOCAL_ONLY:
        raise EgressBlocked(decision.get("reason") or "domain_unsupported",
                            {"no_dispatch": True})
    # TOCTOU: verified sources must be byte-identical to when they were
    # classified; any post-verification mutation blocks the send.
    context.verify_source_bindings()
    # Tripwire over the FULL outbound bytes — enforced for standard AND sensitive.
    scan = final_scan(data_bytes, loaded)
    if scan["verdict"] == "hit":
        raise EgressBlocked("tripwire_hit", {"category_counts": scan["findings"]})
    if decision["tier"] == "sensitive":
        if codex_prompt is not None:
            blocks = re.findall(r"BEGIN_DERIVATIVE\n(.*?)\nEND_DERIVATIVE",
                                codex_prompt, re.DOTALL)
            if len(blocks) != 1:
                raise EgressBlocked("sensitive_requires_derivative",
                                    {"blocks": len(blocks)})
            construction_proof(blocks[0], loaded)
            # Byte-equality: the whole stdin must be the canonical scaffold+block,
            # so nothing can be wrapped around the derivative.
            if codex_prompt != build_sensitive_codex_prompt(blocks[0]):
                raise EgressBlocked("sensitive_requires_derivative",
                                    {"detail": "noncanonical_prompt"})
        else:
            try:
                body = json.loads(bytes(data_bytes).decode("utf-8"))
            except Exception:  # noqa: BLE001
                raise EgressBlocked("construction_parse_failed")
            users = [it.get("content") for it in (body.get("input") or [])
                     if isinstance(it, dict) and it.get("role") == "user"
                     and isinstance(it.get("content"), str)]
            if len(users) != 1:
                raise EgressBlocked("sensitive_requires_derivative",
                                    {"user_items": len(users)})
            construction_proof(users[0], loaded)
            # Byte-equality against the guard's canonical body: any extra
            # top-level field (e.g. "instructions"), extra input item, non-user
            # role carrying content, or list-form content makes the bytes differ.
            canonical = build_sensitive_gpt_body(
                body.get("model"), users[0], body.get("max_output_tokens"))
            if bytes(data_bytes) != canonical:
                raise EgressBlocked("sensitive_requires_derivative",
                                    {"detail": "noncanonical_body"})
    return decision


def _extract_user_content(body):
    """Pull the user-content strings out of a provider request body (OpenAI
    Responses API shape) so the construction proof can run on exactly what
    the reviewer model will read as source material."""
    out = []
    for item in body.get("input", []) or []:
        if isinstance(item, dict) and item.get("role") == "user":
            content = item.get("content")
            if isinstance(content, str):
                out.append(content)
    return out


def provider_key_available(key_getter=None):
    """True if a provider credential is resolvable. Used by the adapter for the
    'no key -> no participation' hard gate WITHOUT the adapter ever seeing the
    value (it gets only a bool)."""
    try:
        key = (key_getter or resolve_provider_key)()
    except Exception:  # noqa: BLE001
        return False
    return bool(key and str(key).strip())


def provider_key_status(env_get=os.environ.get, user_scope_get=None):
    """Diagnostic status for preflight: (present_bool, source_or_None). Resolves
    the credential INSIDE the guard and returns only whether one is present and
    from where — never the value. Callers (e.g. the CLI preflight) get a bool +
    source string, so credential resolution stays solely in the guard."""
    key = env_get("OPENAI_API_KEY")
    if key and str(key).strip():
        return True, "process_env"
    resolved = resolve_provider_key(env_get=env_get, user_scope_get=user_scope_get)
    if resolved and str(resolved).strip():
        return True, "windows_user_scope"
    return False, None


def gpt_send(body_bytes, timeout, *, context, key_getter=None, transport=None,
             caller="clearwright_gpt_review"):
    """Sole sanctioned GPT egress. The guard validates the EXACT body_bytes,
    resolves the credential, builds the Authorization header, knows the URL,
    owns the transport, and proves the bytes are not mutated between validation
    and the wire. Returns (status, text). Adapters pass only body_bytes; they
    never see the URL, header, key, or a transport. ``transport`` is a
    TEST-ONLY injection of the wire call."""
    _require_registered(caller)
    loaded = load_policy()
    _enforce(body_bytes, context, loaded)
    validated_sha = _sha256_bytes(bytes(body_bytes))
    key = (key_getter or resolve_provider_key)()
    if not key or not str(key).strip():
        raise EgressBlocked("provider_key_missing")
    headers = {"Authorization": "Bearer " + str(key).strip(),
               "Content-Type": "application/json"}
    send = transport or _real_transport

    def _checked(url, hdrs, b, t):
        # Byte-mutation proof: exactly what was validated is what is sent.
        if _sha256_bytes(bytes(b)) != validated_sha:
            raise EgressBlocked("bytes_mutated_after_validation")
        return send(url, hdrs, b, t)

    return _checked(OPENAI_RESPONSES_URL, headers, body_bytes, timeout)


def codex_launch(cmd, prompt, timeout, *, context, cwd=None,
                 caller="clearwright_codex_review"):
    """Sole sanctioned Codex egress: stdin-only. Enforces the lineage-driven
    outcome, validates the EXACT stdin bytes, refuses any prompt that references
    CW trees by absolute path, and runs with an empty temp working directory
    (never a CW tree). The prompt is encoded UTF-8 explicitly so the bytes on
    the wire are exactly the bytes validated (no locale re-encoding)."""
    _require_registered(caller)
    loaded = load_policy()
    data = (prompt or "").encode("utf-8")
    _enforce(data, context, loaded, codex_prompt=(prompt or ""))
    validated_sha = _sha256_bytes(data)
    lowered = (prompt or "").lower()
    for marker in ("review_artifacts", "egress_local", "\\runtime\\", "/runtime/"):
        if marker in lowered:
            raise EgressBlocked("provenance_outside_allowlist",
                                {"detail": "cw_path_in_prompt"})
    import tempfile
    run_cwd = cwd or tempfile.mkdtemp(prefix="cw-egress-")
    try:
        # Send BINARY stdin (the exact validated bytes). Text mode would wrap the
        # child's stdin in a newline-translating TextIOWrapper (on Windows every
        # \n -> \r\n), so the wire bytes would differ from the validated bytes.
        # Binary input disables that translation, so what is sent == what was
        # validated (== validated_sha). Decode the captured output ourselves.
        if _sha256_bytes(data) != validated_sha:
            raise EgressBlocked("bytes_mutated_after_validation")
        proc = subprocess.run(cmd, input=data, capture_output=True,
                              timeout=timeout, cwd=run_cwd)
        if isinstance(proc.stdout, (bytes, bytearray)):
            proc.stdout = bytes(proc.stdout).decode("utf-8", "replace")
        if isinstance(proc.stderr, (bytes, bytearray)):
            proc.stderr = bytes(proc.stderr).decode("utf-8", "replace")
        return proc
    finally:
        try:
            if cwd is None:
                os.rmdir(run_cwd)
        except OSError:
            pass


# --------------------------------------------------------------------------- #
# Provider credential resolution (moved OUT of adapters; Decision 2A)
# --------------------------------------------------------------------------- #

def resolve_provider_key(env_get=os.environ.get, user_scope_get=None):
    """The only production resolver for the GPT API key (process env, then
    the Windows user-scope environment). Adapters call this; they no longer
    resolve credentials themselves. Never logged, never returned in errors."""
    key = env_get("OPENAI_API_KEY")
    if key:
        return key
    if user_scope_get is None and os.name == "nt":
        def user_scope_get():
            try:
                import winreg
                with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as h:
                    val, _ = winreg.QueryValueEx(h, "OPENAI_API_KEY")
                    return val
            except OSError:
                return None
    if user_scope_get is not None:
        return user_scope_get()
    return None


# --------------------------------------------------------------------------- #
# Residue scan for reviewer outputs before persistence (verdict echo control)
# --------------------------------------------------------------------------- #

def redact_for_persistence(text, loaded=None):
    """Scan reviewer output before it is written to a durable store. On a
    tripwire hit the text is REPLACED by a content-free findings notice (the
    original is discarded, not stored). Returns (safe_text, findings)."""
    loaded = loaded or load_policy()
    try:
        findings = _scan_text(text or "", loaded["policy"])
    except Exception:
        return ("[reviewer output withheld: residue scan failed closed]",
                {"scanner_exception": 1})
    hard = {k: v for k, v in findings.items() if k != "unicode_confusable"}
    if hard:
        return ("[reviewer output withheld: residual-sensitive-data scan hit "
                "{} category(ies); findings recorded content-free]".format(len(hard)),
                findings)
    return (text, findings)


# --------------------------------------------------------------------------- #
# Startup self-test (server refuses council dispatch until this passes)
# --------------------------------------------------------------------------- #

def self_test(repo=None):
    """Content-free readiness report. ok=False must cause the control plane
    to refuse council dispatch endpoints."""
    report = {"ok": True, "checks": {}}

    def _fail(name, detail=None):
        report["ok"] = False
        report["checks"][name] = {"ok": False, "detail": detail}

    try:
        loaded = load_policy()
        report["checks"]["policy"] = {"ok": True,
                                      "policy_version": loaded["policy_version"],
                                      "policy_sha256": loaded["policy_sha256"]}
    except EgressBlocked as exc:
        _fail("policy", exc.reason)
        return report
    try:
        probe = final_scan(b'{"probe": "clean"}', loaded)
        report["checks"]["final_scan"] = {"ok": probe["verdict"] == "clear"}
        if probe["verdict"] != "clear":
            _fail("final_scan", "probe_not_clear")
    except EgressBlocked as exc:
        _fail("final_scan", exc.reason)
    try:
        import clearwright_gpt_review as g
        import clearwright_codex_review as c
        wired = (getattr(g, "GUARDED", False) is True
                 and getattr(c, "GUARDED", False) is True)
        report["checks"]["adapters_guarded"] = {"ok": wired}
        if not wired:
            _fail("adapters_guarded", "unguarded_adapter_active")
    except Exception:
        _fail("adapters_guarded", "import_failed")
    return report


def main():
    import argparse
    parser = argparse.ArgumentParser(prog="clearwright_egress_guard")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("self-test", help="Content-free readiness report.")
    p_scan = sub.add_parser("scan-file", help="Detector scan of a file "
                            "(content-free category counts only).")
    p_scan.add_argument("path")
    args = parser.parse_args()
    if args.command == "self-test":
        report = self_test()
        print(json.dumps(report, indent=2))
        sys.exit(0 if report["ok"] else 1)
    if args.command == "scan-file":
        loaded = load_policy()
        with open(args.path, "rb") as fh:
            data = fh.read()
        try:
            result = final_scan(data, loaded)
            print(json.dumps({"ok": True, "verdict": result["verdict"],
                              "category_counts": result["findings"]}, indent=2))
            sys.exit(0 if result["verdict"] == "clear" else 2)
        except EgressBlocked as exc:
            print(json.dumps({"ok": False, **exc.summary}, indent=2))
            sys.exit(3)


if __name__ == "__main__":
    main()
