#!/usr/bin/env python3
"""
tools/clearwright_server_lifecycle.py: durable ClearWright SERVER lifecycle
evidence, a single-instance lock, and a filesystem-local graceful-stop sentinel.

Stabilization work item message:msg-20260715T033322041191. Two hard reboots
during the overnight campaign left ZERO server-side records, and the running
server had been started with a relative path and unredirected streams. This
module gives the control plane durable, append-only lifecycle records plus a
server-authoritative single-instance lock and a graceful-stop sentinel -- with
NO HTTP stop route and NO startup persistence. (The unrelated packet-lifecycle
tool is tools/clearwright_lifecycle.py.)

Canonical namespace: every control artifact lives under ONE directory computed
identically by the server, launcher, and stop helper:

    LOGS_DIR = <dirname(abspath(queue_root))>/logs

holding lifecycle.jsonl, clearwright-<port>.lock, clearwright-<port>.stop, and
the redirected control-plane.{out,err}.log.

Events (append-only, 5 MB rotation, keep 5): startup_ok, startup_refused_port,
startup_refused_duplicate, shutdown_graceful, shutdown_exception,
prior_unclean_shutdown. Secrets are never logged.
"""
import json
import os
import re
import sys

import clearwright_writer_lock as cwl

ROTATE_BYTES = 5 * 1024 * 1024
ROTATE_KEEP = 5
LIFECYCLE_NAME = "lifecycle.jsonl"

_KEY_FLAG = re.compile(r"(?i)(key|token|secret|password|credential|auth)")
_URL_USERINFO = re.compile(r"://[^/\s:@]+:[^/\s@]+@")
_KEYISH = re.compile(r"(?i)^(sk-[A-Za-z0-9]{8,}|[A-Za-z0-9+/=]{32,}|[0-9a-f]{32,})$")


def _now_iso():
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def logs_dir(queue_root):
    """The one canonical control-artifact directory for a queue root."""
    return os.path.join(os.path.dirname(os.path.abspath(queue_root)), "logs")


def lifecycle_path(queue_root):
    return os.path.join(logs_dir(queue_root), LIFECYCLE_NAME)


def instance_lock_path(queue_root, port):
    return os.path.join(logs_dir(queue_root), "clearwright-{}.lock".format(port))


def stop_sentinel_path(queue_root, port):
    return os.path.join(logs_dir(queue_root), "clearwright-{}.stop".format(port))


def _self_start_time():
    try:
        return str(cwl._process_start_time(os.getpid()))
    except Exception:
        return None


def sanitize_argv(argv):
    """Redacted copy of argv: the value of any key/token/secret flag (--k=v,
    /k:v, or the token FOLLOWING -k/--k), URL user-info, and any high-entropy or
    key-shaped token become '<redacted>'. Environment values are never read."""
    out = []
    redact_next = False
    for tok in argv:
        s = str(tok)
        if redact_next:
            out.append("<redacted>")
            redact_next = False
            continue
        m = re.match(r"^(--?[^=:\s]+|/[^=:\s]+)([=:])(.*)$", s)
        if m and _KEY_FLAG.search(m.group(1)):
            out.append(m.group(1) + m.group(2) + "<redacted>")
            continue
        if s.startswith(("-", "/")) and _KEY_FLAG.search(s):
            out.append(s)
            redact_next = True
            continue
        if _URL_USERINFO.search(s):
            out.append(_URL_USERINFO.sub("://<redacted>@", s))
            continue
        if _KEYISH.match(s):
            out.append("<redacted>")
            continue
        out.append(s)
    return out


def _rotate_if_needed(path):
    try:
        if os.path.getsize(path) < ROTATE_BYTES:
            return
    except OSError:
        return
    base, _ = os.path.splitext(path)
    for i in range(ROTATE_KEEP - 1, 0, -1):
        older, newer = "{}-{}.jsonl".format(base, i), "{}-{}.jsonl".format(base, i + 1)
        if os.path.isfile(older):
            try:
                os.replace(older, newer)
            except OSError:
                pass
    try:
        os.replace(path, "{}-1.jsonl".format(base))
    except OSError:
        pass


def record(queue_root, event, *, version=None, git_commit=None, mode=None,
           bind_host=None, port=None, argv=None):
    """Append one lifecycle event under the writer lock (append + rotation are
    lock-guarded so concurrent starters cannot double-rotate). Returns the
    record, or None on write failure (the caller surfaces it to stderr and
    /api/health -- a logging failure never fakes success)."""
    entry = {
        "at_utc": _now_iso(), "event": event, "version": version,
        "git_commit": git_commit or "unknown", "pid": os.getpid(),
        "ppid": _ppid(), "executable": sys.executable,
        "argv_sanitized": sanitize_argv(argv if argv is not None else sys.argv),
        "cwd": os.getcwd(), "queue_root": os.path.abspath(queue_root),
        "mode": mode, "bind_host": bind_host, "port": port,
    }
    directory = logs_dir(queue_root)
    path = lifecycle_path(queue_root)
    try:
        os.makedirs(directory, exist_ok=True)
        with cwl.write_token(queue_root, purpose="lifecycle"):
            _rotate_if_needed(path)
            with open(path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
                fh.flush()
                os.fsync(fh.fileno())
        return entry
    except Exception as exc:
        sys.stderr.write("clearwright lifecycle write failed: {}\n".format(exc))
        return None


def _ppid():
    try:
        return os.getppid()
    except (OSError, AttributeError):
        return None


# --------------------------------------------------------------------------- #
# Server-authoritative single-instance lock
# --------------------------------------------------------------------------- #

def acquire_instance_lock(queue_root, port):
    """Acquire the exclusive single-instance lock for this port. Returns
    (True, note) on success or (False, holder) if a LIVE process holds it. The
    stale-check-and-replace is atomic under the writer lock, so two racing
    starters cannot both replace a dead holder. When the prior holder was
    confirmed dead, note carries {"prior_unclean": holder}."""
    directory = logs_dir(queue_root)
    os.makedirs(directory, exist_ok=True)
    path = instance_lock_path(queue_root, port)
    content = {"pid": os.getpid(), "host": cwl._this_host(),
               "start_time": _self_start_time(), "port": port, "at": _now_iso()}
    with cwl.write_token(queue_root, purpose="instance-lock"):
        holder = _read_json(path)
        if holder is not None:
            live = cwl.liveness(holder.get("pid"), holder.get("host"),
                                holder.get("start_time"))
            if live != "dead":  # live or indeterminate -> fail safe
                return False, holder
            _atomic_write(path, content)
            return True, {"prior_unclean": holder}
        _atomic_write(path, content)
        return True, {}


def release_instance_lock(queue_root, port):
    path = instance_lock_path(queue_root, port)
    try:
        with cwl.write_token(queue_root, purpose="instance-lock"):
            holder = _read_json(path)
            if holder and holder.get("pid") == os.getpid():
                os.remove(path)
    except OSError:
        pass


# --------------------------------------------------------------------------- #
# Graceful-stop sentinel (filesystem-local; NO HTTP route)
# --------------------------------------------------------------------------- #

def write_stop_sentinel(queue_root, port, target_pid, target_start_time):
    """Used by the stop helper: request graceful stop of a specific process."""
    _atomic_write(stop_sentinel_path(queue_root, port),
                  {"pid": target_pid, "start_time": target_start_time,
                   "at": _now_iso()})


def stop_sentinel_targets_me(queue_root, port):
    """True iff a stop sentinel exists AND names this exact process (pid +
    start time). A mismatched or stale sentinel is deleted and NOT honored."""
    path = stop_sentinel_path(queue_root, port)
    data = _read_json(path)
    if data is None:
        return False
    if data.get("pid") == os.getpid() and data.get("start_time") == _self_start_time():
        return True
    try:
        os.remove(path)
        sys.stderr.write("clearwright: removed stale stop sentinel {}\n".format(path))
    except OSError:
        pass
    return False


def clear_stop_sentinel(queue_root, port):
    try:
        os.remove(stop_sentinel_path(queue_root, port))
    except OSError:
        pass


def clear_stale_startup_sentinel(queue_root, port):
    """At startup (after acquiring the lock), delete any pre-existing stop
    sentinel with a logged notice so it cannot stop a fresh instance."""
    path = stop_sentinel_path(queue_root, port)
    if os.path.isfile(path):
        try:
            os.remove(path)
            sys.stderr.write("clearwright: cleared pre-existing stop sentinel at startup\n")
        except OSError:
            pass


def instance_status(queue_root, port):
    """Read-only current-instance descriptor for /api/health's instance block."""
    holder = _read_json(instance_lock_path(queue_root, port))
    return {
        "pid": os.getpid(), "started_at_utc": None,
        "lock_holder_pid": (holder or {}).get("pid"),
        "lifecycle_log": lifecycle_path(queue_root),
    }


# --------------------------------------------------------------------------- #
# small IO
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
        json.dump(obj, fh)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)


def git_commit(repo_dir):
    """Best-effort short commit of repo_dir/HEAD, or 'unknown'."""
    try:
        with open(os.path.join(repo_dir, ".git", "HEAD"), encoding="utf-8") as fh:
            ref = fh.read().strip()
        if ref.startswith("ref:"):
            with open(os.path.join(repo_dir, ".git", ref.split(" ", 1)[1].strip()),
                      encoding="utf-8") as fh:
                return fh.read().strip()[:10]
        return ref[:10]
    except OSError:
        return "unknown"
