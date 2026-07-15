#!/usr/bin/env python3
"""
tools/clearwright_identity.py: canonical message-scoped work-item identity.

Stabilization work item message:msg-20260715T033322041191. Before this module,
work items were derived at most one-per-thread: two actionable messages in one
conversation thread collapsed to a single item, and the second (council-bound,
gated) item vanished from the queue. This module makes identity message-scoped:

  - Every ACTIONABLE message derives its own work item ``message:<message_id>``.
    Thread id is conversation context only.
  - Claims, responses, progress, closures, councils, gates, summaries, and
    verification bind to the canonical work-item id, never to the thread.
  - Legacy records (written before the identity-version cutover) resolve
    through an explicit, persisted migration manifest, not runtime heuristics.
  - Ambiguous legacy state produces a visible integrity warning; nothing is
    ever silently omitted or arbitrarily selected.

Origin eligibility (closed rule):
  - identity_version >= 2 messages: origin iff intent == "request" exactly,
    inbound, no closure, status not in {claimed, responded}. Chat, absent,
    unknown, and future intents are non-origin by construction, so authority,
    grant, and closure texts can never become origins or titles.
  - legacy messages (no identity_version): origin iff the message id is listed
    in the persisted legacy-origins manifest computed once at migration.

State precedence (first match wins): malformed > closed/superseded > done >
operator_required (unresolved gate) > verification > planning > claimed > open.
Only recognized closure values ({done, closed_by_operator}) close an item; an
unknown closure value never closes it (the item stays visible with a warning).
"""
import json
import os

import clearwright_message as cwm

# Sources that post governance RECORDS, never work-request origins.
GOVERNANCE_RECORD_SOURCES = frozenset(
    ("use-cw-gate", "use-cw-annotation", "use-cw-summary"))

# Roles and sources that post reviewer output, never work-request origins.
NON_ORIGIN_ROLES = frozenset(("reviewer",))
REVIEWER_SOURCES = frozenset(
    ("openai-api", "codex-cli", "openai-api-review", "codex-review",
     "openai-responses"))

# Payload markers that identify a legacy authority/closure message (best-effort
# defense, used ONLY at migration time, never at runtime).
_AUTHORITY_MARKERS = (
    "operator authority",
    "authority message",
    "i authorize ",
    "close work item ",
    "close and supersede",
    "overnight clearwright maintenance authorization",
)

RECOGNIZED_CLOSURES = frozenset(("done", "closed_by_operator"))

LEGACY_MANIFEST_NAME = "legacy_origins.json"
LEGACY_MARKER_NAME = "legacy_origins.migrated"
MANIFEST_SCHEMA_VERSION = 1


# --------------------------------------------------------------------------- #
# Canonical id helpers
# --------------------------------------------------------------------------- #

def work_item_id_for(message_id):
    return "message:" + str(message_id)


def message_id_of(work_item_id):
    wid = str(work_item_id or "")
    return wid[len("message:"):] if wid.startswith("message:") else None


def _casefold(value):
    return str(value or "").strip().casefold()


# --------------------------------------------------------------------------- #
# Origin eligibility
# --------------------------------------------------------------------------- #

def is_v2(message):
    return int(message.get("identity_version") or 0) >= 2


def v2_is_origin(message):
    """Closed v2 origin rule. Only an inbound request with no closure and a
    non-terminal status is an origin; every other intent is excluded by
    construction."""
    if message.get("direction") != "inbound":
        return False
    if message.get("intent") != "request":
        return False
    if message.get("closure"):
        return False
    if message.get("status") in ("claimed", "responded"):
        return False
    return True


def _looks_like_authority(message):
    body = _casefold(message.get("message"))
    if any(body.startswith(m) or m in body for m in _AUTHORITY_MARKERS):
        return "payload_marker"
    return None


def _authority_referenced_ids(root):
    """Message ids that appear as authority in any durable governance record
    (gate authority, closure authority). These legacy messages are authority,
    never origins."""
    ids = set()
    gates_dir = os.path.join(root, "gates")
    if os.path.isdir(gates_dir):
        for name in sorted(os.listdir(gates_dir)):
            if not name.endswith(".json"):
                continue
            try:
                recs = json.load(open(os.path.join(gates_dir, name), encoding="utf-8"))
            except (OSError, ValueError):
                continue
            for g in (recs if isinstance(recs, list) else [recs]):
                auth = (g or {}).get("authority") or {}
                mid = auth.get("message_id")
                if mid:
                    ids.add(mid)
    for m in cwm.read_messages(root):
        if m.get("closure"):
            meta = m.get("closure_meta") or {}
            for key in ("operator_message_id", "authority_message_id"):
                if meta.get(key):
                    ids.add(meta[key])
    return ids


# --------------------------------------------------------------------------- #
# Legacy-origins migration manifest
# --------------------------------------------------------------------------- #

def _manifest_path(root):
    return os.path.join(root, LEGACY_MANIFEST_NAME)


def _marker_path(root):
    return os.path.join(root, LEGACY_MARKER_NAME)


def _legacy_convention_origin(message, excluded_sources):
    """Per-message eligibility for a legacy work-request ORIGIN, used ONLY at
    migration time to compute the frozen manifest. Excludes reviewer output,
    governance-record posts, and records already bound to another work item, so
    only genuine operator/worker requests qualify."""
    if message.get("direction") != "inbound":
        return False
    if message.get("intent") == "chat":
        return False
    if message.get("status") in ("claimed", "responded"):
        return False
    if message.get("closure"):
        return False
    if message.get("source") in excluded_sources:
        return False
    if message.get("role") in NON_ORIGIN_ROLES:
        return False
    if message.get("source") in REVIEWER_SOURCES:
        return False
    # A record bound to ANOTHER work item is a bound record (reviewer output,
    # claim, response, summary), never an origin.
    wid = message.get("work_item_id")
    if wid and wid != "message:" + str(message.get("message_id")):
        return False
    return True


def build_legacy_manifest(root, now_iso=None):
    """Compute (without writing) the eligible legacy-origin set with an audit
    trail of exclusions. Legacy = messages with no identity_version."""
    referenced = _authority_referenced_ids(root)
    origins, excluded = [], []
    for m in cwm.read_messages(root):
        if is_v2(m):
            continue  # v2 records use the closed rule, not the manifest
        mid = m.get("message_id")
        if not mid:
            continue
        if not _legacy_convention_origin(m, GOVERNANCE_RECORD_SOURCES):
            continue
        if mid in referenced:
            excluded.append({"id": mid, "reason": "provenance"})
            continue
        marker = _looks_like_authority(m)
        if marker:
            excluded.append({"id": mid, "reason": marker})
            continue
        origins.append(mid)
    return {
        "schema_version": MANIFEST_SCHEMA_VERSION,
        "generated_at": now_iso or cwm._now_iso(),
        "origin_message_ids": sorted(origins),
        "excluded": sorted(excluded, key=lambda e: e["id"]),
    }


def migrate_legacy_origins(root, now_iso=None):
    """Idempotent one-time migration under the writer lock. Writes the frozen
    manifest and a durable marker on first run. Returns the manifest dict."""
    import clearwright_writer_lock as cwl
    with cwl.write_token(root, purpose="legacy-origins"):
        existing = _read_json(_manifest_path(root))
        if existing is not None:
            return existing
        manifest = build_legacy_manifest(root, now_iso=now_iso)
        _atomic_write(_manifest_path(root), manifest)
        _atomic_write(_marker_path(root), {
            "migrated_at": manifest["generated_at"],
            "manifest_count": len(manifest["origin_message_ids"]),
        })
        return manifest


def legacy_origin_ids(root):
    """The legacy-origin id set. READ-PURE: never writes. If the frozen
    manifest exists, its ids are authoritative. If it was deleted after a
    completed migration (marker present, manifest absent), returns EMPTY rather
    than silently regenerating (the deletion surfaces via manifest_status()).
    Before migration has persisted it, the set is computed in memory so read
    paths (derivation, health) never mutate the queue; an explicit
    ensure_migrated()/migrate_legacy_origins() at a write boundary freezes it."""
    manifest = _read_json(_manifest_path(root))
    if manifest is not None:
        return set(manifest.get("origin_message_ids") or [])
    if os.path.isfile(_marker_path(root)):
        return set()  # deleted post-migration; do not silently regenerate
    return set(build_legacy_manifest(root).get("origin_message_ids") or [])


def ensure_migrated(root):
    """Freeze the legacy-origins manifest if it has not been persisted yet.
    Called from write boundaries and server startup, never from read paths."""
    if _read_json(_manifest_path(root)) is None and not os.path.isfile(_marker_path(root)):
        migrate_legacy_origins(root)


def manifest_status(root):
    """Return None normally, or a warning code when the manifest was deleted
    after a completed migration."""
    if _read_json(_manifest_path(root)) is None and os.path.isfile(_marker_path(root)):
        return "legacy_manifest_missing"
    return None


# --------------------------------------------------------------------------- #
# Small durable IO (mirrors the discipline used elsewhere)
# --------------------------------------------------------------------------- #

def _read_json(path):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


def _atomic_write(path, obj):
    import uuid
    tmp = path + ".tmp-" + uuid.uuid4().hex[:8]
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, indent=2)
        fh.write("\n")
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)
