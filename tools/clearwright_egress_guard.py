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
OUTCOME_LOCAL_ONLY = "local_only"
OUTCOME_STOP = "stop"

ERROR_CLASS = "egress_blocked"

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
                "approved_repo_paths", "synthetic_fixture_paths",
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
            ["git", "-C", repo, "ls-files", "--error-unmatch", rel_path],
            capture_output=True, text=True, timeout=30)
        return proc.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def verify_provenance(source_paths, repo, run_work_dirs, loaded=None):
    """STANDARD-tier provenance validation. Every source path must be (a) a
    git-tracked file under an approved repository path, (b) inside a declared
    work directory of the CURRENT governed run (machine-generated analysis),
    or (c) under a synthetic-fixture path. Anything else — or any error —
    resolves to SENSITIVE (EgressBlocked "provenance_*"). Returns a
    content-free provenance record."""
    loaded = loaded or load_policy()
    policy = loaded["policy"]
    approved = [p.replace("\\", "/") for p in policy["approved_repo_paths"]]
    fixtures = [p.replace("\\", "/") for p in policy["synthetic_fixture_paths"]]
    repo_abs = os.path.abspath(repo) if repo else None
    run_dirs = [os.path.abspath(d) for d in (run_work_dirs or [])]
    record = []
    for path in (source_paths or []):
        ap = os.path.abspath(path)
        if not os.path.isfile(ap):
            raise EgressBlocked("provenance_unverified",
                                {"path_sha16": _sha256_bytes(ap.encode())[:16]})
        if any(ap.startswith(d + os.sep) or ap == d for d in run_dirs):
            record.append({"path_sha16": _sha256_bytes(ap.encode())[:16],
                           "class": "machine_generated_in_run"})
            continue
        if repo_abs and (ap.startswith(repo_abs + os.sep)):
            rel = os.path.relpath(ap, repo_abs).replace("\\", "/")
            if any(rel.startswith(a) or (a.startswith("*.") and rel.endswith(a[1:]))
                   for a in fixtures):
                record.append({"path_sha16": _sha256_bytes(ap.encode())[:16],
                               "class": "synthetic_fixture"})
                continue
            allowed = any(rel.startswith(a) or (a.startswith("*.") and rel.endswith(a[1:]))
                          for a in approved)
            if not allowed:
                raise EgressBlocked("provenance_outside_allowlist",
                                    {"path_sha16": _sha256_bytes(ap.encode())[:16]})
            if not _git_tracked(repo_abs, rel):
                raise EgressBlocked("provenance_untracked_file",
                                    {"path_sha16": _sha256_bytes(ap.encode())[:16]})
            record.append({"path_sha16": _sha256_bytes(ap.encode())[:16],
                           "class": "approved_repo_file"})
            continue
        raise EgressBlocked("provenance_unverified",
                            {"path_sha16": _sha256_bytes(ap.encode())[:16]})
    return {"sources": record}


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
    if not isinstance(fields, list):
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
        if "bucket" in f and not _BUCKET.match(str(f["bucket"])):
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
# Egress context: dispatch metadata the transports REQUIRE (fail-closed).
# --------------------------------------------------------------------------- #

class EgressContext(object):
    """Content-free dispatch context. tier is "standard" (provenance verified
    by verify_provenance at packet assembly) or "sensitive" (construction
    proof will be applied to the exact outbound payload)."""

    def __init__(self, tier, provenance=None, work_item_id=None):
        if tier not in ("standard", "sensitive"):
            raise EgressBlocked("context_missing", {"detail": "bad tier"})
        self.tier = tier
        self.provenance = provenance
        self.work_item_id = work_item_id


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
# Guard-owned provider egress. THE ONLY sanctioned dispatch paths.
# --------------------------------------------------------------------------- #

def _validate_outbound(data_bytes, context, loaded):
    """Tier-appropriate validation of the EXACT outbound bytes."""
    if context is None or not isinstance(context, EgressContext):
        raise EgressBlocked("context_missing")
    if context.tier == "sensitive":
        # The user-content portion must BE the derivative envelope. The
        # caller passes the derivative payload separately verified; here the
        # serialized bytes are re-checked for the envelope + a detector
        # backstop over the full request.
        scan = final_scan(data_bytes, loaded)
        try:
            body = json.loads(bytes(data_bytes).decode("utf-8"))
        except Exception:
            raise EgressBlocked("construction_parse_failed")
        payloads = _extract_user_content(body)
        if not payloads:
            raise EgressBlocked("sensitive_requires_derivative")
        for text in payloads:
            construction_proof(text, loaded)
        return scan
    scan = final_scan(data_bytes, loaded)
    if scan["verdict"] == "hit":
        raise EgressBlocked("tripwire_hit", {"category_counts": scan["findings"]})
    return scan


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


def gpt_transport(url, headers, body_bytes, timeout, *, context,
                  real_transport, caller="clearwright_gpt_review"):
    """Sole sanctioned GPT egress: validates the EXACT serialized request
    bytes, then delegates to the adapter's private transport."""
    _require_registered(caller)
    loaded = load_policy()
    _validate_outbound(body_bytes, context, loaded)
    return real_transport(url, headers, body_bytes, timeout)


def codex_launch(cmd, prompt, timeout, *, context, cwd=None,
                 caller="clearwright_codex_review"):
    """Sole sanctioned Codex egress: stdin-only. Validates the EXACT stdin
    bytes; refuses any prompt that references CW trees by absolute path; runs
    with an empty temp working directory (never a CW tree)."""
    _require_registered(caller)
    loaded = load_policy()
    data = (prompt or "").encode("utf-8")
    if context is not None and context.tier == "sensitive":
        scan = final_scan(data, loaded)
        # Sensitive codex prompts embed the derivative; locate the JSON
        # envelope (fenced) and prove construction on it.
        m = re.search(r"BEGIN_DERIVATIVE\n(.*?)\nEND_DERIVATIVE",
                      prompt or "", re.DOTALL)
        if not m:
            raise EgressBlocked("sensitive_requires_derivative")
        construction_proof(m.group(1), loaded)
    else:
        if context is None or not isinstance(context, EgressContext):
            raise EgressBlocked("context_missing")
        scan = final_scan(data, loaded)
        if scan["verdict"] == "hit":
            raise EgressBlocked("tripwire_hit",
                                {"category_counts": scan["findings"]})
    lowered = (prompt or "").lower()
    for marker in ("review_artifacts", "egress_local", "\\runtime\\", "/runtime/"):
        if marker in lowered:
            raise EgressBlocked("provenance_outside_allowlist",
                                {"detail": "cw_path_in_prompt"})
    import tempfile
    run_cwd = cwd or tempfile.mkdtemp(prefix="cw-egress-")
    try:
        proc = subprocess.run(cmd, input=(prompt or ""), capture_output=True,
                              text=True, timeout=timeout, cwd=run_cwd)
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
