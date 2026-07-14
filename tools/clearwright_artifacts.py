#!/usr/bin/env python3
"""
tools/clearwright_artifacts.py: artifact registration and reviewer delivery.

An artifact is the thing under review when the deliverable is a document or a
served page rather than a diff. CW — not any reviewer — owns provenance:

  - registration copies the file into the queue's registry and computes the
    FULL sha256, which is the internal identity (the short `art-<12hex>` id is a
    display alias with collision detection, never the authority);
  - the pinned copy is re-verified against its hash before every dispatch, so a
    tampered or drifted artifact is a hard stop, not a silent review of the
    wrong bytes;
  - derived renderings (the line-numbered inline text and the bounded excerpt
    pack for text-only reviewers) are derived artifacts with their own sha256
    linked to the original via `derived_from`.

Reviewer delivery is capability-aware:
  - Codex reads the pinned file from disk by absolute path (verified at
    implementation time: the read-only sandbox restricts writes, not reads, so
    no extra permission is granted) and is instructed to cite the artifact id.
  - GPT receives text only. Under the phase input budget the full line-numbered
    artifact is inlined; over budget it receives a bounded excerpt pack plus a
    manifest stating plainly that the excerpts are the only evidence it may
    rely on, and that it cannot access local files.

Nothing here invokes a reviewer or a network call.
"""
import hashlib
import json
import os

ARTIFACTS_DIR = "review_artifacts"


class ArtifactError(ValueError):
    """Registration or verification failed (missing file, alias collision,
    hash mismatch)."""


def _sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def artifacts_root(root):
    return os.path.join(root, ARTIFACTS_DIR)


def _alias(sha256, width=12):
    return "art-" + sha256[:width]


def register(root, path):
    """Register (pin) an artifact. Content-addressed: the FULL sha256 is the
    identity; re-registering identical content is a no-op. A short-alias
    collision with different content extends the alias rather than trusting 12
    hex chars. Returns the meta dict."""
    if not os.path.isfile(path):
        raise ArtifactError("artifact not found: {!r}".format(path))
    sha = _sha256_file(path)
    width = 12
    while True:
        alias = _alias(sha, width)
        adir = os.path.join(artifacts_root(root), alias)
        meta_path = os.path.join(adir, "meta.json")
        if not os.path.isdir(adir):
            break
        existing = _read_meta(meta_path)
        if existing and existing.get("sha256") == sha:
            return existing  # identical content already pinned
        width += 8  # alias collision with different content: extend, never trust
        if width > 64:
            raise ArtifactError("alias collision could not be resolved")

    ext = os.path.splitext(path)[1] or ".bin"
    os.makedirs(adir, exist_ok=True)
    pinned = os.path.join(adir, "pinned" + ext)
    with open(path, "rb") as src, open(pinned + ".tmp", "wb") as dst:
        for chunk in iter(lambda: src.read(1024 * 1024), b""):
            dst.write(chunk)
    os.replace(pinned + ".tmp", pinned)
    if _sha256_file(pinned) != sha:
        raise ArtifactError("pinned copy hash mismatch after registration")

    with open(pinned, "rb") as fh:
        line_count = fh.read().count(b"\n") + 1
    meta = {
        "artifact_id": alias,
        "sha256": sha,
        "bytes": os.path.getsize(pinned),
        "line_count": line_count,
        "original_path": os.path.abspath(path),
        "pinned_path": os.path.abspath(pinned),
        "registered_at": _now(),
    }
    tmp = meta_path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(meta, fh, indent=2)
        fh.write("\n")
    os.replace(tmp, meta_path)
    return meta


def _now():
    import clearwright_message as cwm
    return cwm._now_iso()


def _read_meta(meta_path):
    try:
        with open(meta_path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


def get(root, artifact_id):
    """Load one registered artifact's meta, or None."""
    return _read_meta(os.path.join(artifacts_root(root), artifact_id, "meta.json"))


def verify(root, artifact_id):
    """Re-verify the pinned copy against its registered FULL sha256. Returns the
    meta dict; raises ArtifactError on any mismatch (a tampered artifact must be
    a hard stop, never a silent review of the wrong bytes)."""
    meta = get(root, artifact_id)
    if not meta:
        raise ArtifactError("artifact {!r} is not registered".format(artifact_id))
    pinned = meta.get("pinned_path")
    if not pinned or not os.path.isfile(pinned):
        raise ArtifactError("pinned copy for {!r} is missing".format(artifact_id))
    actual = _sha256_file(pinned)
    if actual != meta.get("sha256"):
        raise ArtifactError(
            "artifact {!r} failed hash verification (pinned copy no longer matches "
            "its registered sha256)".format(artifact_id))
    return meta


def _read_text(meta):
    with open(meta["pinned_path"], encoding="utf-8", errors="replace") as fh:
        return fh.read()


def _numbered(text, start=1):
    return "\n".join("{:>6}  {}".format(i + start, line)
                     for i, line in enumerate(text.splitlines()))


def _derived_record(root, meta, kind, text):
    """Persist a derived rendering with its own hash linked to the original."""
    sha = hashlib.sha256(text.encode("utf-8")).hexdigest()
    adir = os.path.join(artifacts_root(root), meta["artifact_id"])
    path = os.path.join(adir, "derived-{}.txt".format(kind))
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(text)
    os.replace(tmp, path)
    rec = {"kind": kind, "sha256": sha, "derived_from": meta["sha256"],
           "bytes": len(text.encode("utf-8")), "path": os.path.abspath(path)}
    idx_path = os.path.join(adir, "derived.json")
    idx = _read_meta(idx_path) or {}
    idx[kind] = rec
    tmp = idx_path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(idx, fh, indent=2)
        fh.write("\n")
    os.replace(tmp, idx_path)
    return rec


GPT_CAPABILITY_STATEMENT = (
    "REVIEWER CAPABILITY: you receive text only. You CANNOT access local files, "
    "paths, URLs, or repositories, and requesting file access cannot be "
    "satisfied. Evidence not supplied in this packet does not exist for your "
    "purposes; judge only what is provided and say so when that limits a "
    "conclusion.")


def inline_rendering(root, artifact_id):
    """Full line-numbered rendering for inline (text-only) delivery, recorded as
    a derived artifact. Returns (header+text, derived_record)."""
    meta = verify(root, artifact_id)
    text = _numbered(_read_text(meta))
    rec = _derived_record(root, meta, "inline-linenumbered", text)
    header = ("=== ARTIFACT {} (FULL, line-numbered) ===\n"
              "sha256 {} · {} bytes · {} lines · derived rendering sha256 {}\n"
              ).format(meta["artifact_id"], meta["sha256"], meta["bytes"],
                       meta["line_count"], rec["sha256"])
    return header + text, rec


def excerpt_pack(root, artifact_id, max_chars):
    """Bounded, line-numbered excerpt pack for a text-only reviewer when the
    full artifact exceeds the phase budget: manifest + head + evenly sampled
    windows + tail, hard-capped at max_chars. Recorded as a derived artifact."""
    meta = verify(root, artifact_id)
    lines = _read_text(meta).splitlines()
    total = len(lines)

    def window(start, count):
        seg = lines[start:start + count]
        return "{}\n".format("-" * 8) + "\n".join(
            "{:>6}  {}".format(start + i + 1, ln) for i, ln in enumerate(seg))

    budget = max(2000, max_chars)
    head = window(0, min(120, total))
    tail = window(max(0, total - 60), 60) if total > 200 else ""
    samples = []
    used = len(head) + len(tail)
    n_windows = 6
    for k in range(1, n_windows + 1):
        start = int(total * k / (n_windows + 1))
        w = window(start, 40)
        if used + len(w) > budget:
            break
        samples.append(w)
        used += len(w)
    body = "\n".join([head] + samples + ([tail] if tail else []))
    if len(body) > budget:
        body = body[:budget] + "\n[excerpt pack truncated at budget]"
    rec = _derived_record(root, meta, "excerpt-pack", body)
    manifest = (
        "=== ARTIFACT {} (EXCERPT PACK — NOT the full artifact) ===\n"
        "Full artifact: sha256 {} · {} bytes · {} lines (pinned by CW).\n"
        "This pack is a derived artifact (sha256 {}, derived_from the hash above).\n"
        "THE LINE-NUMBERED EXCERPTS BELOW ARE THE ONLY EVIDENCE YOU MAY RELY ON. "
        "Do not assume unexcerpted regions; say when a conclusion would require "
        "them.\n").format(meta["artifact_id"], meta["sha256"], meta["bytes"],
                          meta["line_count"], rec["sha256"])
    return manifest + body, rec


def codex_reference_block(root, artifact_ids):
    """The evidence block for the file-capable reviewer: absolute pinned paths
    plus expected hashes, with the citation instruction. CW verified each hash
    immediately before dispatch."""
    parts = ["=== PINNED ARTIFACTS (read these files directly) ==="]
    for aid in artifact_ids:
        meta = verify(root, aid)
        parts.append("artifact_id {} · expected sha256 {} · {} bytes · {} lines\n"
                     "path: {}".format(meta["artifact_id"], meta["sha256"],
                                       meta["bytes"], meta["line_count"],
                                       meta["pinned_path"]))
    parts.append("Read the pinned file(s) above from disk. Cite evidence as "
                 "artifact_id:line. Report which artifact(s) you inspected.")
    return "\n".join(parts)
