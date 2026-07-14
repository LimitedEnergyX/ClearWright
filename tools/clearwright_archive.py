#!/usr/bin/env python3
"""
tools/clearwright_archive.py: durable archive layer for the Local Council site.

Moves old terminal records (and smoke/proof runs regardless of age) out of the
active queue into ``runtime/queues/archive/<YYYY-MM>/`` so the operator console
shows only live and recent work, while EVERY byte is preserved and every id
stays resolvable through the archive-aware read fallback. Zero deletion: the
code contains no unlink-of-a-source path outside the move step itself, and a
move only completes after its destination hash is re-verified.

Retention (see docs/ARCHIVE_OPERATION.md for the full policy):
  keep    - nonterminal work, terminal work younger than 72h, the latest five
            genuine completed operational runs, operator-pinned records
  archive - older terminal work; smoke/proof runs regardless of age; the
            records named in the checked-in approved inventory

The approved inventory (tests/fixtures/archive_inventory.json) is a hash-bound
UPPER BOUND: at ``dry-run`` and ``execute`` time, retention is recomputed from
LIVE state; a record moves only when it is BOTH in the approved inventory and
still currently eligible. Any live-computed candidate NOT in the approved
inventory stops the run for operator review rather than silently expanding
scope.

``execute`` is destructive and requires, in order: (1) the committed inventory
artifact's hash to match; (2) a matching approval file under
``operator_authority/archive-approvals/`` -- created only by ``approve`` or by
direct operator filesystem placement, NEVER via any HTTP route -- naming the
EXACT full SHA-256 hash of the freshly recomputed dry-run plan; (3) the
approval's referenced durable inbound operator message to resolve and contain
that hash; (4) the plan recomputed AFTER acquiring archive exclusivity to match
byte-for-byte (no drift); only then does the journal exist and moves begin.

Honest boundary: the API/server surface cannot create, edit, revoke, or select
an archive approval (no server route exists under operator_authority/, proven
in tests/test_archive.py). This is enforced. It does not defend against a
process already running under the same Windows user with local filesystem or
CLI access -- that residual is the accepted operator threat-model decision
recorded in docs/ARCHIVE_OPERATION.md, not a completed OS-level control.
"""
import argparse
import hashlib
import json
import os
import re
import sys
import uuid
from datetime import datetime, timedelta, timezone

import clearwright_message as cwm
import clearwright_work as cww
import clearwright_review_council as cwrc
import clearwright_writer_lock as cwl

RETENTION_WINDOW_HOURS = 72
LATEST_RUNS_KEPT = 5
SMOKE_KEYWORDS = ("smoke", "proof", "e2e", "harness check", "adapter-fix")

INVENTORY_SCHEMA_VERSION = 1
JOURNAL_SCHEMA_VERSION = 1
APPROVAL_SCHEMA_VERSION = 1

_HASH_RE = re.compile(r"^[0-9a-f]{64}$")
_TYPE_TO_COMPUTED_KEY = {
    "thread": "threads", "council": "councils",
    "clearance_packet": "clearance_packets", "agent_event": "agent_events",
}

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_INVENTORY_PATH = os.path.join(
    _REPO_ROOT, "tests", "fixtures", "archive_inventory.json")


class ArchiveError(Exception):
    pass


# --------------------------------------------------------------------------- #
# Paths and atomic I/O (mirrors clearwright_writer_lock's discipline)
# --------------------------------------------------------------------------- #

def _now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _fsync_dir(path):
    try:
        fd = os.open(path, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)


def _atomic_write(path, obj):
    directory = os.path.dirname(path)
    os.makedirs(directory, exist_ok=True)
    tmp = path + ".tmp-" + uuid.uuid4().hex[:8]
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, indent=2)
        fh.write("\n")
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)
    _fsync_dir(directory)


def _read_json(path):
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


def _parse_iso(text):
    if not text:
        return None
    for fmt in ("%Y-%m-%dT%H:%M:%S.%fZ", "%Y-%m-%dT%H:%M:%SZ"):
        try:
            return datetime.strptime(text, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def _sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def _canonical_bytes(obj, exclude_keys=()):
    filtered = {k: v for k, v in obj.items() if k not in exclude_keys}
    return json.dumps(filtered, sort_keys=True, separators=(",", ":"),
                      ensure_ascii=False).encode("utf-8")


def archive_root(queue_root):
    parent = os.path.dirname(os.path.normpath(os.path.abspath(queue_root)))
    return os.path.join(parent, "archive")


def month_dir(queue_root, at=None):
    at = at or datetime.now(timezone.utc)
    return os.path.join(archive_root(queue_root), at.strftime("%Y-%m"))


# --------------------------------------------------------------------------- #
# Pins (operator-pinned records always stay active)
# --------------------------------------------------------------------------- #

def _pins_path(root):
    return os.path.join(root, "pins.json")


def pinned_ids(root):
    return set((_read_json(_pins_path(root)) or {}).get("pinned", []))


def pin(root, record_id):
    with cwl.write_token(root, purpose="pin"):
        ids = pinned_ids(root)
        ids.add(record_id)
        _atomic_write(_pins_path(root), {"pinned": sorted(ids)})
    return sorted(ids)


def unpin(root, record_id):
    with cwl.write_token(root, purpose="pin"):
        ids = pinned_ids(root)
        ids.discard(record_id)
        _atomic_write(_pins_path(root), {"pinned": sorted(ids)})
    return sorted(ids)


# --------------------------------------------------------------------------- #
# Retention classification (live state)
# --------------------------------------------------------------------------- #

def _message_files(root):
    directory = cwm.comms_dir(root)
    if not os.path.isdir(directory):
        return []
    out = []
    for name in sorted(os.listdir(directory)):
        if not name.endswith(".json"):
            continue
        path = os.path.join(directory, name)
        data = _read_json(path)
        if data is not None:
            out.append((path, data))
    return out


def _open_thread_ids(root):
    ids = set()
    for item in cww.derive_work_items(root):
        if item.get("kind") == "message" and item.get("status") in ("open", "claimed"):
            tid = item.get("thread_id")
            if tid:
                ids.add(tid)
    return ids


def _nonterminal_packet_ids(root):
    ids = set()
    for item in cww.derive_work_items(root):
        if item.get("kind") in ("packet", "rfi", "in_progress"):
            pid = item.get("packet_id")
            if pid:
                ids.add(pid)
    return ids


def _terminal_thread_ids(root):
    open_ids = _open_thread_ids(root)
    nonterminal_packets = _nonterminal_packet_ids(root)
    by_thread = {}
    for _path, data in _message_files(root):
        tid = data.get("thread_id")
        if tid:
            by_thread.setdefault(tid, []).append(data)
    terminal = set()
    for tid, msgs in by_thread.items():
        if tid in open_ids:
            continue
        if any(m.get("packet_id") in nonterminal_packets for m in msgs):
            continue
        terminal.add(tid)
    return terminal, by_thread


def _thread_last_at(msgs):
    return max((m.get("at") or "" for m in msgs), default="")


def _thread_origin(msgs):
    ordered = sorted(msgs, key=lambda m: m.get("at") or "")
    return ordered[0] if ordered else None


def _is_smoke_origin(origin):
    if not origin:
        return False
    low = (origin.get("message") or "").lower()
    return any(re.search(r"\b" + re.escape(k) + r"\b", low) for k in SMOKE_KEYWORDS)


def classify_threads(root, now=None):
    """Return {'keep': {thread_id...}, 'archive': {thread_id...}} for terminal
    threads only (nonterminal threads are never candidates and are not in
    either set). Smoke/proof threads archive REGARDLESS OF AGE -- recency
    never protects them, only a pin does. Non-smoke terminal threads within
    the retention window stay kept unconditionally; older non-smoke threads
    keep only the latest five genuine operational runs."""
    now = now or datetime.now(timezone.utc)
    pins = pinned_ids(root)
    terminal, by_thread = _terminal_thread_ids(root)
    cutoff = now - timedelta(hours=RETENTION_WINDOW_HOURS)
    rows = []
    for tid in terminal:
        msgs = by_thread[tid]
        last_dt = _parse_iso(_thread_last_at(msgs))
        recent = last_dt is None or last_dt >= cutoff
        rows.append({
            "thread_id": tid, "last_at": _thread_last_at(msgs),
            "smoke": _is_smoke_origin(_thread_origin(msgs)),
            "recent": recent, "pinned": tid in pins,
        })
    keep = {r["thread_id"] for r in rows if r["pinned"]}
    archive = {r["thread_id"] for r in rows if r["smoke"] and not r["pinned"]}
    non_smoke = [r for r in rows if not r["smoke"] and not r["pinned"]]
    keep |= {r["thread_id"] for r in non_smoke if r["recent"]}
    old_sorted = sorted((r for r in non_smoke if not r["recent"]),
                        key=lambda r: r["last_at"], reverse=True)
    keep |= {r["thread_id"] for r in old_sorted[:LATEST_RUNS_KEPT]}
    archive |= {r["thread_id"] for r in old_sorted[LATEST_RUNS_KEPT:]}
    return {"keep": keep, "archive": archive}


def compute_live_candidates(root, now=None):
    """The full live-computed candidate set, by type. This is the RETENTION
    RULE's output; execution intersects it with the approved inventory."""
    now = now or datetime.now(timezone.utc)
    pins = pinned_ids(root)
    threads = classify_threads(root, now=now)
    archive_threads = threads["archive"]

    councils = sorted(c["council_id"] for c in cwrc.list_councils(root)
                      if c.get("thread_id") in archive_threads)

    cutoff = now - timedelta(hours=RETENTION_WINDOW_HOURS)
    clearance = []
    for p in cww._read_packets(root):
        if p["lane"] != "clearance_done" or p["packet_id"] in pins:
            continue
        try:
            mtime = datetime.fromtimestamp(os.path.getmtime(p["path"]), tz=timezone.utc)
        except OSError:
            continue
        if mtime < cutoff:
            clearance.append(p["packet_id"])

    events = []
    try:
        import clearwright_agent_event as cwae
        for e in cwae.read_events(root):
            eid = e.get("event_id")
            if not eid or eid in pins:
                continue
            at = _parse_iso(e.get("at") or "")
            if at is not None and at < cutoff:
                events.append(eid)
    except Exception:
        pass

    return {
        "threads": sorted(archive_threads),
        "councils": councils,
        "clearance_packets": sorted(clearance),
        "agent_events": sorted(events),
        "keep_threads": sorted(threads["keep"]),
    }


def diff_against_approved(computed, approved_records):
    """extra = computed but not approved (stop the run); missing = approved
    but not currently qualifying (skip those with a reason, not an error)."""
    approved_by_type = {}
    for r in approved_records:
        approved_by_type.setdefault(r["type"], set()).add(r["id"])
    extra, missing = {}, {}
    for rtype, key in _TYPE_TO_COMPUTED_KEY.items():
        computed_ids = set(computed.get(key, []))
        approved_ids = approved_by_type.get(rtype, set())
        ex = computed_ids - approved_ids
        if ex:
            extra[rtype] = sorted(ex)
        miss = approved_ids - computed_ids
        if miss:
            missing[rtype] = sorted(miss)
    return {"extra": extra, "missing": missing}


# --------------------------------------------------------------------------- #
# Approved inventory artifact
# --------------------------------------------------------------------------- #

def compute_inventory_hash(inventory):
    return hashlib.sha256(
        _canonical_bytes(inventory, exclude_keys=("content_hash", "generated_at"))
    ).hexdigest()


def load_inventory(path=DEFAULT_INVENTORY_PATH):
    with open(path, encoding="utf-8") as fh:
        inventory = json.load(fh)
    expected = inventory.get("content_hash")
    actual = compute_inventory_hash(inventory)
    if expected != actual:
        raise ArchiveError("inventory_hash_mismatch: expected {} computed {}".format(
            expected, actual))
    return inventory


def build_inventory(records, generated_at=None):
    """Build (without writing) a schema-versioned inventory artifact from a
    list of {"id","type","reason"} rows, with content_hash computed over the
    canonical form (content_hash and generated_at excluded from the hash)."""
    inventory = {"schema_version": INVENTORY_SCHEMA_VERSION,
                "generated_at": generated_at or _now_iso(),
                "records": sorted(records, key=lambda r: (r["type"], r["id"]))}
    inventory["content_hash"] = compute_inventory_hash(inventory)
    return inventory


# --------------------------------------------------------------------------- #
# Dry-run plan generation
# --------------------------------------------------------------------------- #

def resolve_record_files(root, record):
    """Return the absolute source paths belonging to one inventory record."""
    rtype, rid = record["type"], record["id"]
    out = []
    if rtype == "thread":
        for path, data in _message_files(root):
            if data.get("thread_id") != rid:
                continue
            out.append(path)
            mid = data.get("message_id")
            if mid:
                env_path = os.path.join(root, "task_envelopes", mid + ".json")
                if os.path.isfile(env_path):
                    out.append(env_path)
                sum_path = os.path.join(root, "summaries", mid + ".json")
                if os.path.isfile(sum_path):
                    out.append(sum_path)
    elif rtype == "council":
        cdir = cwrc.council_dir(root, rid)
        if os.path.isdir(cdir):
            for name in sorted(os.listdir(cdir)):
                out.append(os.path.join(cdir, name))
    elif rtype == "clearance_packet":
        for p in cww._read_packets(root):
            if p["packet_id"] == rid:
                out.append(p["path"])
    elif rtype == "agent_event":
        path = os.path.join(root, "agent_events", rid + ".json")
        if os.path.isfile(path):
            out.append(path)
    return out


def generate_plan(root, inventory, now=None):
    """The canonical dry-run move plan: every file that would move, its
    destination, and its current sha256. Fails (ok: False) with the exact
    out-of-approval candidates when live state computes a record the approved
    inventory does not name."""
    computed = compute_live_candidates(root, now=now)
    diff = diff_against_approved(computed, inventory["records"])
    if diff["extra"]:
        return {"ok": False, "error": "candidates_outside_approved_inventory",
               "extra": diff["extra"]}
    mdir = month_dir(root, at=now)
    entries = []
    for record in inventory["records"]:
        rtype, rid = record["type"], record["id"]
        qualifying_ids = computed.get(_TYPE_TO_COMPUTED_KEY.get(rtype, ""), [])
        if rid not in qualifying_ids:
            continue
        for src in resolve_record_files(root, record):
            rel = os.path.relpath(src, root)
            entries.append({
                "id": rid, "type": rtype, "src": src, "rel_path": rel,
                "dst": os.path.join(mdir, rel), "sha256": _sha256_file(src),
            })
    entries.sort(key=lambda e: (e["type"], e["id"], e["rel_path"]))
    plan = {"schema_version": JOURNAL_SCHEMA_VERSION,
           "queue_root": os.path.realpath(root), "entries": entries}
    plan_hash = compute_plan_hash(plan)
    record_count = len({(e["type"], e["id"]) for e in entries})
    return {"ok": True, "plan": plan, "plan_hash": plan_hash,
           "skipped_not_qualifying": diff["missing"],
           "record_count": record_count, "file_count": len(entries)}


def compute_plan_hash(plan):
    filtered = {"schema_version": plan["schema_version"],
               "queue_root": plan["queue_root"],
               "entries": [{"id": e["id"], "type": e["type"],
                            "rel_path": e["rel_path"], "sha256": e["sha256"]}
                          for e in plan["entries"]]}
    return hashlib.sha256(_canonical_bytes(filtered)).hexdigest()


# --------------------------------------------------------------------------- #
# Approval (hash-bound; NO server write route; see tests/test_archive.py)
# --------------------------------------------------------------------------- #

def operator_authority_dir(root):
    return os.path.join(root, "operator_authority")


def approvals_dir(root):
    return os.path.join(operator_authority_dir(root), "archive-approvals")


def _normalize_queue_root(root):
    p = os.path.realpath(root)
    return p.lower() if sys.platform == "win32" else p


def write_approval(root, plan_hash, operator_message_id, operator):
    plan_hash = str(plan_hash or "").strip().lower()
    if not _HASH_RE.match(plan_hash):
        raise ArchiveError("approved_plan_sha256 must be a full 64-hex-character sha256")
    approval_id = uuid.uuid4().hex
    record = {"schema_version": APPROVAL_SCHEMA_VERSION, "approval_id": approval_id,
             "queue_root": _normalize_queue_root(root),
             "archive_operation": "archive_execute",
             "approved_plan_sha256": plan_hash,
             "operator_message_id": operator_message_id,
             "created_at": _now_iso(), "operator": operator, "revoked": False}
    _atomic_write(os.path.join(approvals_dir(root), approval_id + ".json"), record)
    return record


def revoke_approval(root, approval_id):
    path = os.path.join(approvals_dir(root), approval_id + ".json")
    rec = _read_json(path)
    if rec is None:
        raise ArchiveError("approval not found: " + approval_id)
    rec["revoked"] = True
    _atomic_write(path, rec)
    return rec


def _list_approvals(root):
    directory = approvals_dir(root)
    if not os.path.isdir(directory):
        return []
    out = []
    for name in sorted(os.listdir(directory)):
        if name.endswith(".json"):
            rec = _read_json(os.path.join(directory, name))
            if rec:
                out.append(rec)
    return out


def find_eligible_approval(root, plan_hash):
    """Exactly one eligible approval -> return it; none -> no_archive_authority;
    an equal-timestamp tie among the newest same-hash approvals fails closed as
    ambiguous_archive_authority (deliberately rare -- normal re-approval simply
    supersedes by a later created_at)."""
    norm_root = _normalize_queue_root(root)
    plan_hash = str(plan_hash or "").strip().lower()
    candidates = []
    for rec in _list_approvals(root):
        if rec.get("schema_version") != APPROVAL_SCHEMA_VERSION:
            continue
        if rec.get("revoked"):
            continue
        if rec.get("archive_operation") != "archive_execute":
            continue
        if rec.get("queue_root") != norm_root:
            continue
        if str(rec.get("approved_plan_sha256") or "").lower() != plan_hash:
            continue
        created = _parse_iso(rec.get("created_at") or "")
        if created is None:
            continue
        candidates.append((created, rec))
    if not candidates:
        return None, "no_archive_authority"
    candidates.sort(key=lambda pair: pair[0])
    newest_time = candidates[-1][0]
    newest = [rec for created, rec in candidates if created == newest_time]
    if len(newest) > 1:
        return None, "ambiguous_archive_authority"
    return newest[0], None


def validate_approval_message(root, approval):
    mid = approval.get("operator_message_id")
    msg = next((m for m in cwm.read_messages(root) if m.get("message_id") == mid), None)
    if msg is None:
        return False, "authority_message_not_found"
    if msg.get("actor") != "OPERATOR-0001" or msg.get("direction") != "inbound":
        return False, "authority_not_operator_inbound"
    phash = approval.get("approved_plan_sha256", "")
    if phash not in (msg.get("message") or ""):
        return False, "authority_missing_hash_token"
    return True, None


def apply_override(root, operator_message_id, reason):
    """Record a durable, audited override authorizing forced clearance of a
    stuck exclusive flag whose owner is NOT confirmed live. Refuses outright if
    the owner is confirmed live -- this is never a way to preempt a running
    archive."""
    msg = next((m for m in cwm.read_messages(root)
               if m.get("message_id") == operator_message_id), None)
    if msg is None or msg.get("actor") != "OPERATOR-0001" or msg.get("direction") != "inbound":
        raise ArchiveError("override authority message not found or not operator-inbound")
    flag = cwl.current_exclusive(root)
    if flag is None:
        raise ArchiveError("no exclusive flag is currently held")
    state = cwl.liveness(flag.get("pid"), flag.get("host"), flag.get("proc_start"))
    if state == "live":
        raise ArchiveError("refused: the exclusive owner is confirmed live")
    record = {"schema_version": 1, "override_id": uuid.uuid4().hex, "at": _now_iso(),
             "operator_message_id": operator_message_id, "reason": reason,
             "flag": flag, "liveness_at_override": state}
    _atomic_write(os.path.join(operator_authority_dir(root), "overrides",
                               record["override_id"] + ".json"), record)
    cwl.force_clear_exclusive_for_override(root)
    return record


# --------------------------------------------------------------------------- #
# Journal / move state machine / recovery
# --------------------------------------------------------------------------- #

def new_opid():
    return "op-" + datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%f") + \
        "-" + uuid.uuid4().hex[:6]


def _journal_path(mdir, opid, kind="pending"):
    return os.path.join(mdir, "{}-{}.json".format(kind, opid))


def _completion_log_path(mdir, opid):
    return os.path.join(mdir, "pending-{}.log".format(opid))


def write_journal(mdir, opid, plan, plan_hash):
    os.makedirs(mdir, exist_ok=True)
    planned = [{"id": e["id"], "type": e["type"], "src": e["src"],
               "dst": e["dst"], "sha256": e["sha256"]} for e in plan["entries"]]
    journal = {"schema_version": JOURNAL_SCHEMA_VERSION, "opid": opid,
              "created_at": _now_iso(), "approved_plan_sha256": plan_hash,
              "planned": planned}
    _atomic_write(_journal_path(mdir, opid, "pending"), journal)
    return journal


def _completed_rel_paths(mdir, opid):
    path = _completion_log_path(mdir, opid)
    done = set()
    if not os.path.isfile(path):
        return done
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except ValueError:
                continue  # a truncated trailing line is ignored, not fatal
            if rec.get("dst"):
                done.add(rec["dst"])
    return done


def _append_completion(mdir, opid, item):
    path = _completion_log_path(mdir, opid)
    line = json.dumps({"id": item["id"], "dst": item["dst"], "at": _now_iso()})
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(line + "\n")
        fh.flush()
        os.fsync(fh.fileno())


def execute_journal(mdir, journal):
    """Move every planned record not yet completed. HALTS on any hash
    mismatch, missing source with no destination, or a destination collision,
    leaving the journal in place for a safe, idempotent rerun. Zero deletion:
    a source is renamed, never removed outright, and only after its
    destination hash re-verifies."""
    opid = journal["opid"]
    completed = _completed_rel_paths(mdir, opid)
    for item in journal["planned"]:
        dst = item["dst"]
        if dst in completed:
            continue
        src = item["src"]
        src_exists = os.path.isfile(src)
        dst_exists = os.path.isfile(dst)
        if src_exists and dst_exists:
            raise ArchiveError("archive_halt_both_exist: " + dst)
        if not src_exists and dst_exists:
            if _sha256_file(dst) != item["sha256"]:
                raise ArchiveError("archive_halt_hash_mismatch_recovery: " + dst)
            _append_completion(mdir, opid, item)
            continue
        if not src_exists and not dst_exists:
            raise ArchiveError("archive_halt_missing_source: " + src)
        if _sha256_file(src) != item["sha256"]:
            raise ArchiveError("archive_halt_source_drift: " + src)
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        os.rename(src, dst)
        if _sha256_file(dst) != item["sha256"]:
            raise ArchiveError("archive_halt_post_move_hash_mismatch: " + dst)
        _append_completion(mdir, opid, item)
    return True


def build_manifest(root, journal, approval):
    rows = [{
        "id": item["id"], "type": item["type"],
        "original_path": os.path.relpath(item["src"], root),
        "archive_path": item["dst"], "sha256": item["sha256"],
        "reason": "archive",
    } for item in journal["planned"]]
    return {"schema_version": JOURNAL_SCHEMA_VERSION, "opid": journal["opid"],
           "generated_at": _now_iso(),
           "approved_plan_sha256": approval.get("approved_plan_sha256"),
           "approval_id": approval.get("approval_id"), "records": rows}


def index_path(aroot):
    return os.path.join(aroot, "index.json")


def _merge_manifest_into_index(idx, manifest):
    for row in manifest["records"]:
        entry = idx["ids"].setdefault(row["id"], {"type": row["type"], "paths": []})
        if row["archive_path"] not in entry["paths"]:
            entry["paths"].append(row["archive_path"])


def merge_index(aroot, manifest):
    idx = _read_json(index_path(aroot)) or \
        {"schema_version": JOURNAL_SCHEMA_VERSION, "ids": {}}
    _merge_manifest_into_index(idx, manifest)
    _atomic_write(index_path(aroot), idx)
    return idx


def rebuild_index(aroot):
    """Rebuild index.json from every completed manifest -- self-heals a torn
    or missing index. Idempotent."""
    idx = {"schema_version": JOURNAL_SCHEMA_VERSION, "ids": {}}
    if os.path.isdir(aroot):
        for month in sorted(os.listdir(aroot)):
            mdir = os.path.join(aroot, month)
            if not os.path.isdir(mdir):
                continue
            for name in sorted(os.listdir(mdir)):
                if name.startswith("manifest-") and name.endswith(".json"):
                    manifest = _read_json(os.path.join(mdir, name))
                    if manifest:
                        _merge_manifest_into_index(idx, manifest)
    _atomic_write(index_path(aroot), idx)
    return idx


def finalize_journal(root, mdir, journal, approval):
    opid = journal["opid"]
    manifest = build_manifest(root, journal, approval)
    _atomic_write(os.path.join(mdir, "manifest-{}.json".format(opid)), manifest)
    merge_index(archive_root(root), manifest)
    os.replace(_journal_path(mdir, opid, "pending"),
              _journal_path(mdir, opid, "completed"))
    return manifest


def recover_pending(root):
    """Find every pending-*.json journal under the archive tree and resume it
    forward-only. A missing/truncated journal is treated as untouched (nothing
    moves without a durable journal). Returns a list of per-opid results."""
    aroot = archive_root(root)
    results = []
    if not os.path.isdir(aroot):
        return results
    for month in sorted(os.listdir(aroot)):
        mdir = os.path.join(aroot, month)
        if not os.path.isdir(mdir):
            continue
        for name in sorted(os.listdir(mdir)):
            if not (name.startswith("pending-") and name.endswith(".json")):
                continue
            journal = _read_json(os.path.join(mdir, name))
            if not journal:
                continue
            try:
                execute_journal(mdir, journal)
                approval = {"approved_plan_sha256": journal.get("approved_plan_sha256"),
                           "approval_id": None}
                finalize_journal(root, mdir, journal, approval)
                results.append({"opid": journal["opid"], "status": "completed"})
            except ArchiveError as exc:
                results.append({"opid": journal["opid"], "status": "halted",
                                "error": str(exc)})
    return results


INVOCATION_LOG_MAX_BYTES = 5 * 1024 * 1024


def _log_creation_month(path):
    """Best-effort: the timestamp of the FIRST line in the log, so rotation
    triggers on the log's true age rather than its last-modified time."""
    try:
        with open(path, encoding="utf-8") as fh:
            first_line = fh.readline()
        rec = json.loads(first_line)
        at = _parse_iso(rec.get("at") or "")
        return at.strftime("%Y-%m") if at else None
    except (OSError, ValueError):
        return None


def rotate_invocation_log_if_needed(root, now=None):
    """If invocation_log.jsonl exists and either its calendar month has
    changed or it exceeds 5MB, rotate it into
    archive/<YYYY-MM>/invocation_log-<YYYY-MM>[-n].jsonl (an exclusive-create
    placeholder claims the name, then the real content replaces it) and leave
    a fresh log for the next write. No manual one-off rotation path exists;
    this is the only rotation mechanism. Returns the archive path if rotated,
    else None. Never raises -- a rotation failure must never block logging."""
    now = now or datetime.now(timezone.utc)
    path = os.path.join(root, "invocation_log.jsonl")
    if not os.path.isfile(path):
        return None
    try:
        stat = os.stat(path)
    except OSError:
        return None
    created_month = _log_creation_month(path) or \
        datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).strftime("%Y-%m")
    current_month = now.strftime("%Y-%m")
    if stat.st_size < INVOCATION_LOG_MAX_BYTES and created_month == current_month:
        return None
    mdir = os.path.join(archive_root(root), current_month)
    try:
        os.makedirs(mdir, exist_ok=True)
        for suffix in range(1000):
            name = "invocation_log-{}{}.jsonl".format(
                created_month, "" if suffix == 0 else "-{}".format(suffix))
            dest = os.path.join(mdir, name)
            try:
                fd = os.open(dest, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.close(fd)
            except FileExistsError:
                continue
            os.replace(path, dest)
            return dest
    except OSError:
        return None
    return None


def resolve_archived(root, record_id):
    """Archive-aware lookup: {'archived': True, 'type':..., 'paths': [...]}
    or None. Used by the server's archive-aware read fallback."""
    idx = _read_json(index_path(archive_root(root)))
    if not idx:
        return None
    entry = idx.get("ids", {}).get(record_id)
    return {"archived": True, "type": entry["type"], "paths": entry["paths"]} \
        if entry else None


def read_archived_messages(root, thread_id):
    """Archive-aware fallback for GET /api/messages: every archived message
    file for this thread, read directly from its archive path. Active records
    always win (the caller checks the active store first); this only runs on
    an active miss. Every returned record carries archived: true."""
    resolved = resolve_archived(root, thread_id)
    if not resolved or resolved["type"] != "thread":
        return []
    out = []
    for path in resolved["paths"]:
        if os.sep + "communications" + os.sep not in path:
            continue  # envelope/summary files, not message records
        data = _read_json(path)
        if data is not None:
            out.append(dict(data, archived=True, source_archive_path=path))
    out.sort(key=lambda m: m.get("at") or "")
    return out


def read_archived_council(root, council_id):
    """Archive-aware fallback for GET /api/review-council: the archived
    council's council.json/round-*.json/outcome.json, read directly from
    their archive paths, in the same shape cwrc.get_council would build for an
    active council. Returns None if not archived."""
    resolved = resolve_archived(root, council_id)
    if not resolved or resolved["type"] != "council":
        return None
    by_name = {}
    for path in resolved["paths"]:
        name = os.path.basename(path)
        data = _read_json(path)
        if data is not None:
            by_name[name] = data
    if "council.json" not in by_name:
        return None
    rounds = sorted(
        (v for k, v in by_name.items() if k.startswith("round-") and k.endswith(".json")),
        key=lambda r: r.get("round", 0))
    return {"archived": True, "council": by_name["council.json"], "rounds": rounds,
           "outcome": by_name.get("outcome.json")}


def read_archived_clearance_packet(root, filename):
    """Archive-aware fallback for GET /api/audit: locate an archived
    clearance-lane packet by its original filename. Returns (path, data) or
    (None, None)."""
    filename = os.path.basename(filename or "")
    idx = _read_json(index_path(archive_root(root))) or {"ids": {}}
    for entry in idx.get("ids", {}).values():
        if entry.get("type") != "clearance_packet":
            continue
        for path in entry.get("paths", []):
            if os.path.basename(path) == filename:
                data = _read_json(path)
                if data is not None:
                    return path, data
    return None, None


def read_archived_summary(root, mid):
    """Archive-aware fallback for GET /api/work-summary: the archived
    summaries/<mid>.json, read from its archive path. Returns None if the
    originating thread was not archived or carried no summary file."""
    for record_id in list((_read_json(index_path(archive_root(root))) or {})
                          .get("ids", {}).keys()):
        resolved = resolve_archived(root, record_id)
        if not resolved:
            continue
        for path in resolved["paths"]:
            if os.path.basename(path) == mid + ".json" and \
                    os.sep + "summaries" + os.sep in path:
                data = _read_json(path)
                if data is not None:
                    return dict(data, archived=True)
    return None


# --------------------------------------------------------------------------- #
# Top-level operations
# --------------------------------------------------------------------------- #

def dry_run(root, inventory_path=DEFAULT_INVENTORY_PATH, now=None):
    inventory = load_inventory(inventory_path)
    return generate_plan(root, inventory, now=now)


def execute(root, inventory_path=DEFAULT_INVENTORY_PATH,
           deadline_seconds=cwl.DEFAULT_DRAIN_DEADLINE_SECONDS, now=None):
    """Destructive. Requires a matching hash-bound approval to already exist
    (write_approval / archive-approve is a SEPARATE, prior step). Recovers any
    interrupted prior run first. ``now`` exists for test determinism; normal
    callers omit it and get the real current time at every recomputation."""
    recover_pending(root)
    inventory = load_inventory(inventory_path)
    result = generate_plan(root, inventory, now=now)
    if not result.get("ok"):
        return result
    plan_hash = result["plan_hash"]
    approval, err = find_eligible_approval(root, plan_hash)
    if err:
        return {"ok": False, "error": err, "plan_hash": plan_hash}
    ok, verr = validate_approval_message(root, approval)
    if not ok:
        return {"ok": False, "error": verr, "plan_hash": plan_hash}
    opid = new_opid()
    mdir = month_dir(root, at=now)
    flag = cwl.acquire_exclusive(root, opid, deadline_seconds=deadline_seconds)
    try:
        fresh = generate_plan(root, inventory, now=now)
        if not fresh.get("ok") or fresh["plan_hash"] != plan_hash:
            return {"ok": False, "error": "inventory_drifted_since_dry_run",
                    "plan_hash": plan_hash,
                    "fresh_plan_hash": fresh.get("plan_hash")}
        journal = write_journal(mdir, opid, fresh["plan"], plan_hash)
        execute_journal(mdir, journal)
        manifest = finalize_journal(root, mdir, journal, approval)
        return {"ok": True, "opid": opid, "moved": len(manifest["records"]),
                "manifest": manifest, "approval_id": approval["approval_id"]}
    finally:
        cwl.release_exclusive(root, opid, flag["nonce"])


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #

def _emit(result, code, as_json=True):
    print(json.dumps(result) if as_json else json.dumps(result, indent=2))
    return code


def cli_dry_run(args):
    try:
        result = dry_run(args.queue_root, inventory_path=args.inventory_file)
    except ArchiveError as exc:
        return _emit({"ok": False, "command": "dry-run", "error": str(exc)}, 1, args.json)
    return _emit(dict(result, command="dry-run"), 0 if result.get("ok") else 1, args.json)


def cli_execute(args):
    try:
        result = execute(args.queue_root, inventory_path=args.inventory_file)
    except ArchiveError as exc:
        return _emit({"ok": False, "command": "execute", "error": str(exc)}, 1, args.json)
    except cwl.WriterLockError as exc:
        return _emit({"ok": False, "command": "execute", "error": str(exc)}, 1, args.json)
    return _emit(dict(result, command="execute"), 0 if result.get("ok") else 1, args.json)


def cli_approve(args):
    try:
        record = write_approval(args.queue_root, args.plan_hash,
                                args.operator_message_id, args.operator)
    except ArchiveError as exc:
        return _emit({"ok": False, "command": "approve", "error": str(exc)}, 1, args.json)
    return _emit({"ok": True, "command": "approve", "approval": record}, 0, args.json)


def cli_revoke(args):
    try:
        record = revoke_approval(args.queue_root, args.approval_id)
    except ArchiveError as exc:
        return _emit({"ok": False, "command": "revoke", "error": str(exc)}, 1, args.json)
    return _emit({"ok": True, "command": "revoke", "approval": record}, 0, args.json)


def cli_pin(args):
    return _emit({"ok": True, "command": "pin", "pinned": pin(args.queue_root, args.id)},
                 0, args.json)


def cli_unpin(args):
    return _emit({"ok": True, "command": "unpin", "pinned": unpin(args.queue_root, args.id)},
                 0, args.json)


def cli_status(args):
    return _emit({"ok": True, "command": "status",
                  "exclusive": cwl.current_exclusive(args.queue_root),
                  "pinned": sorted(pinned_ids(args.queue_root))}, 0, args.json)


def build_parser():
    parser = argparse.ArgumentParser(
        prog="clearwright_archive",
        description="Durable archive layer: dry-run and execute retention "
                    "moves under mandatory dry-run, execution-time "
                    "recomputation, and a hash-bound operator approval.")
    subs = parser.add_subparsers(dest="command", required=True)

    p_dry = subs.add_parser("dry-run", help="Non-destructive: compute the "
                            "current move plan and its full SHA-256 hash.")
    p_dry.add_argument("queue_root")
    p_dry.add_argument("--inventory-file", default=DEFAULT_INVENTORY_PATH)
    p_dry.add_argument("--json", action="store_true")
    p_dry.set_defaults(func=cli_dry_run)

    p_exec = subs.add_parser("execute", help="DESTRUCTIVE: move the records "
                             "in the current plan. Requires a matching "
                             "hash-bound approval (see 'approve').")
    p_exec.add_argument("queue_root")
    p_exec.add_argument("--inventory-file", default=DEFAULT_INVENTORY_PATH)
    p_exec.add_argument("--json", action="store_true")
    p_exec.set_defaults(func=cli_execute)

    p_appr = subs.add_parser("approve", help="Operator-only: record a "
                             "hash-bound approval for the exact plan hash "
                             "from a prior dry-run.")
    p_appr.add_argument("queue_root")
    p_appr.add_argument("--plan-hash", required=True, metavar="SHA256")
    p_appr.add_argument("--operator-message-id", required=True, metavar="ID")
    p_appr.add_argument("--operator", default="OPERATOR-0001")
    p_appr.add_argument("--json", action="store_true")
    p_appr.set_defaults(func=cli_approve)

    p_rev = subs.add_parser("revoke", help="Operator-only: revoke an approval.")
    p_rev.add_argument("queue_root")
    p_rev.add_argument("--approval-id", required=True)
    p_rev.add_argument("--json", action="store_true")
    p_rev.set_defaults(func=cli_revoke)

    p_pin = subs.add_parser("pin", help="Keep a record active regardless of "
                            "retention age.")
    p_pin.add_argument("queue_root")
    p_pin.add_argument("--id", required=True)
    p_pin.add_argument("--json", action="store_true")
    p_pin.set_defaults(func=cli_pin)

    p_unpin = subs.add_parser("unpin", help="Remove a pin.")
    p_unpin.add_argument("queue_root")
    p_unpin.add_argument("--id", required=True)
    p_unpin.add_argument("--json", action="store_true")
    p_unpin.set_defaults(func=cli_unpin)

    p_status = subs.add_parser("status", help="Read-only: current exclusive "
                               "flag and pinned ids.")
    p_status.add_argument("queue_root")
    p_status.add_argument("--json", action="store_true")
    p_status.set_defaults(func=cli_status)

    return parser


def main():
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            try:
                reconfigure(encoding="utf-8", errors="backslashreplace")
            except (ValueError, OSError):
                pass
    args = build_parser().parse_args()
    sys.exit(args.func(args))


if __name__ == "__main__":
    main()
