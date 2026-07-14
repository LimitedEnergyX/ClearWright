#!/usr/bin/env python3
"""
tools/clearwright_writer_lock.py: writer/archive mutual exclusion.

Shared primitives so a normal durable write and an active archive operation can
never race: a writer acquires a short-lived TOKEN before mutating and releases
it when done; the archive operation acquires EXCLUSIVITY only when no live-or-
indeterminate token remains, and holds it while it recomputes state and moves
records. Both sides check-and-set under the SAME short-held ``registry.lock``
mutex, so neither can observe a stale view of the other -- a writer already
past its check cannot be surprised by a mid-mutation archive, and archive
cannot begin while a writer holds a token.

Liveness is conservative by construction: a token or the exclusive flag is
swept ONLY on CONFIRMED process non-liveness (same host, PID confirmed dead, or
the PID was reused by a different process per a process-start mismatch). Age
alone never sweeps anything -- a long-running writer keeps its protection for
as long as it is actually alive, at the cost of an archive operation needing to
wait for it. Indeterminate liveness (cross-host, or the OS query failed) always
fails safe: retained, never swept, archive waits then aborts without a flag.

All state lives under ``<root>/locks/`` and is written atomically
(temp file + fsync + os.replace + parent-directory fsync where supported).
"""
import ctypes
import json
import os
import socket
import sys
import time
import uuid
from datetime import datetime, timezone

LOCKS_DIR = "locks"
TOKENS_DIR = "tokens"
REGISTRY_LOCK = "registry.lock"
EXCLUSIVE_FLAG = "exclusive.flag"

DEFAULT_DRAIN_DEADLINE_SECONDS = 60
_LOCK_SPIN_SECONDS = 0.02
_LOCK_SPIN_MAX_WAIT = 5.0


class WriterLockError(Exception):
    pass


class MaintenanceInProgress(WriterLockError):
    """Raised by acquire_write_token while an archive exclusive is active."""

    def __init__(self):
        super().__init__("maintenance_in_progress")


# --------------------------------------------------------------------------- #
# Paths and atomic I/O
# --------------------------------------------------------------------------- #

def _locks_dir(root):
    return os.path.join(root, LOCKS_DIR)


def _tokens_dir(root):
    return os.path.join(_locks_dir(root), TOKENS_DIR)


def _now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _this_host():
    return socket.gethostname()


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


# --------------------------------------------------------------------------- #
# Liveness: confirmed-live / confirmed-dead / indeterminate
# --------------------------------------------------------------------------- #

def _posix_process_start_time(pid):
    try:
        with open("/proc/{}/stat".format(pid), encoding="utf-8") as fh:
            text = fh.read()
    except OSError:
        return None
    try:
        # comm (field 2) is parenthesized and may itself contain spaces/parens;
        # split on the LAST ')' so we always land after it. starttime is field
        # 22 overall, i.e. the 20th field after the comm field.
        after = text.rsplit(")", 1)[1].split()
        return after[19]
    except (IndexError, ValueError):
        return None


def _win_process_start_time(pid):
    try:
        kernel32 = ctypes.windll.kernel32
    except (AttributeError, OSError):
        return None
    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000

    class _FILETIME(ctypes.Structure):
        _fields_ = [("low", ctypes.c_uint32), ("high", ctypes.c_uint32)]

    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return None
    try:
        ft_create, ft_exit, ft_kernel, ft_user = (_FILETIME(), _FILETIME(),
                                                   _FILETIME(), _FILETIME())
        ok = kernel32.GetProcessTimes(handle, ctypes.byref(ft_create),
                                      ctypes.byref(ft_exit),
                                      ctypes.byref(ft_kernel),
                                      ctypes.byref(ft_user))
        if not ok:
            return None
        return (ft_create.high << 32) | ft_create.low
    finally:
        kernel32.CloseHandle(handle)


def _process_start_time(pid):
    if not pid:
        return None
    return _win_process_start_time(pid) if sys.platform == "win32" \
        else _posix_process_start_time(pid)


def _win_pid_exists(pid):
    try:
        kernel32 = ctypes.windll.kernel32
    except (AttributeError, OSError):
        return None
    PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
    handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return False
    kernel32.CloseHandle(handle)
    return True


def _pid_exists(pid):
    if not pid:
        return None
    if sys.platform == "win32":
        return _win_pid_exists(pid)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return None
    return True


def liveness(pid, host, proc_start):
    """Return 'live' | 'dead' | 'indeterminate' for a recorded owner. Callers
    sweep ONLY on 'dead'; both 'live' and 'indeterminate' fail safe (retained).
    A cross-host owner is always 'indeterminate' (never confirmed non-live)."""
    if not pid or host != _this_host():
        return "indeterminate"
    exists = _pid_exists(pid)
    if exists is None:
        return "indeterminate"
    if not exists:
        return "dead"
    current_start = _process_start_time(pid)
    if current_start is None or proc_start is None:
        # The process exists but identity cannot be confirmed against the
        # recorded proc_start (permissions, unsupported platform detail, or
        # the recorder never captured one) -- resist PID reuse by refusing to
        # call this 'live' without confirmation.
        return "indeterminate"
    return "dead" if str(current_start) != str(proc_start) else "live"


def _self_owner():
    pid = os.getpid()
    return pid, _this_host(), str(_process_start_time(pid))


# --------------------------------------------------------------------------- #
# registry.lock: a short-held mutex around the critical section
# --------------------------------------------------------------------------- #

class _RegistryLock(object):
    """Context manager for the short critical section used by both writer
    token acquisition and archive exclusivity acquisition, so a check-and-set
    on either side is atomic with respect to the other."""

    def __init__(self, root, purpose):
        self.root = root
        self.purpose = purpose
        self.path = os.path.join(_locks_dir(root), REGISTRY_LOCK)

    def __enter__(self):
        os.makedirs(_locks_dir(self.root), exist_ok=True)
        pid, host, proc_start = _self_owner()
        content = {"pid": pid, "host": host, "proc_start": proc_start,
                  "created_at": _now_iso(), "purpose": self.purpose}
        deadline = time.monotonic() + _LOCK_SPIN_MAX_WAIT
        while True:
            try:
                fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
            except FileExistsError:
                if self._steal_if_stale():
                    continue
                if time.monotonic() >= deadline:
                    raise WriterLockError("registry_lock_unavailable")
                time.sleep(_LOCK_SPIN_SECONDS)
                continue
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(content, fh)
                fh.flush()
                os.fsync(fh.fileno())
            return self

    def _steal_if_stale(self):
        rec = _read_json(self.path)
        if not rec:
            return False
        if liveness(rec.get("pid"), rec.get("host"), rec.get("proc_start")) != "dead":
            return False
        try:
            os.remove(self.path)
        except OSError:
            pass
        return True

    def __exit__(self, exc_type, exc, tb):
        try:
            os.remove(self.path)
        except OSError:
            pass
        return False


# --------------------------------------------------------------------------- #
# Writer tokens
# --------------------------------------------------------------------------- #

def acquire_write_token(root, purpose="write"):
    """Acquire a durable write token; raises MaintenanceInProgress while an
    archive exclusive is active. The token is fully durable BEFORE
    registry.lock releases. Call release_write_token in a finally block."""
    token_id = uuid.uuid4().hex
    path = os.path.join(_tokens_dir(root), token_id + ".tok")
    pid, host, proc_start = _self_owner()
    with _RegistryLock(root, "write:" + purpose):
        if os.path.isfile(os.path.join(_locks_dir(root), EXCLUSIVE_FLAG)):
            raise MaintenanceInProgress()
        content = {"token_id": token_id, "pid": pid, "host": host,
                  "proc_start": proc_start, "created_at": _now_iso(),
                  "heartbeat_at": _now_iso(), "purpose": purpose}
        _atomic_write(path, content)
    return token_id


def release_write_token(root, token_id):
    if not token_id:
        return
    try:
        os.remove(os.path.join(_tokens_dir(root), token_id + ".tok"))
    except OSError:
        pass


def heartbeat_write_token(root, token_id):
    """Renew a long-running writer's token under registry.lock. created_at is
    immutable; heartbeat_at is updated. Returns False if the token no longer
    exists (already swept) -- the caller should abort its mutation."""
    path = os.path.join(_tokens_dir(root), token_id + ".tok")
    with _RegistryLock(root, "heartbeat"):
        rec = _read_json(path)
        if rec is None or rec.get("token_id") != token_id:
            return False
        rec["heartbeat_at"] = _now_iso()
        _atomic_write(path, rec)
    return True


class write_token(object):
    """Context manager: acquire on enter, release on exit (finally-safe)."""

    def __init__(self, root, purpose="write"):
        self.root = root
        self.purpose = purpose
        self.token_id = None

    def __enter__(self):
        self.token_id = acquire_write_token(self.root, self.purpose)
        return self

    def __exit__(self, exc_type, exc, tb):
        release_write_token(self.root, self.token_id)
        return False


# --------------------------------------------------------------------------- #
# Archive exclusivity
# --------------------------------------------------------------------------- #

def _list_tokens(root):
    directory = _tokens_dir(root)
    if not os.path.isdir(directory):
        return []
    out = []
    for name in sorted(os.listdir(directory)):
        if name.endswith(".tok"):
            rec = _read_json(os.path.join(directory, name))
            if rec:
                out.append(rec)
    return out


def acquire_exclusive(root, opid, deadline_seconds=DEFAULT_DRAIN_DEADLINE_SECONDS):
    """Acquire the archive-exclusive flag: sweep only CONFIRMED-dead tokens,
    then require zero remaining (live or indeterminate) tokens before setting
    the flag, retrying until deadline_seconds. Fails safe (no flag left) on
    timeout -- never sweeps a live or indeterminate token to force progress."""
    nonce = uuid.uuid4().hex
    started = time.monotonic()
    while True:
        with _RegistryLock(root, "archive_acquire"):
            flag_path = os.path.join(_locks_dir(root), EXCLUSIVE_FLAG)
            if os.path.isfile(flag_path):
                raise WriterLockError("exclusive_already_held")
            remaining = []
            for rec in _list_tokens(root):
                state = liveness(rec.get("pid"), rec.get("host"), rec.get("proc_start"))
                if state == "dead":
                    try:
                        os.remove(os.path.join(_tokens_dir(root),
                                               rec["token_id"] + ".tok"))
                    except OSError:
                        pass
                    continue
                remaining.append(rec)
            if not remaining:
                pid, host, proc_start = _self_owner()
                flag = {"opid": opid, "nonce": nonce, "pid": pid, "host": host,
                        "proc_start": proc_start, "created_at": _now_iso()}
                _atomic_write(flag_path, flag)
                return flag
        if time.monotonic() - started >= deadline_seconds:
            raise WriterLockError("archive_drain_timeout")
        time.sleep(_LOCK_SPIN_SECONDS)


def release_exclusive(root, opid, nonce):
    """Release the exclusive flag; requires an owner-token (opid+nonce) match."""
    path = os.path.join(_locks_dir(root), EXCLUSIVE_FLAG)
    with _RegistryLock(root, "archive_release"):
        rec = _read_json(path)
        if rec and rec.get("opid") == opid and rec.get("nonce") == nonce:
            try:
                os.remove(path)
            except OSError:
                pass
            return True
    return False


def current_exclusive(root):
    return _read_json(os.path.join(_locks_dir(root), EXCLUSIVE_FLAG))


def clear_stale_exclusive(root):
    """Clear exclusive.flag ONLY on confirmed owner non-liveness -- e.g. an
    archive process that crashed before completing a resumable operation.
    Never clears a live or indeterminate owner; that path is a separate,
    explicit, audited operator override (see clearwright_archive)."""
    path = os.path.join(_locks_dir(root), EXCLUSIVE_FLAG)
    with _RegistryLock(root, "exclusive_recovery"):
        rec = _read_json(path)
        if not rec:
            return False
        if liveness(rec.get("pid"), rec.get("host"), rec.get("proc_start")) != "dead":
            return False
        try:
            os.remove(path)
        except OSError:
            pass
        return True


def force_clear_exclusive_for_override(root):
    """Remove exclusive.flag unconditionally. Callers MUST have already
    validated a durable, audited operator override record naming this action
    (see clearwright_archive.apply_override) -- this function performs no
    authorization itself."""
    path = os.path.join(_locks_dir(root), EXCLUSIVE_FLAG)
    try:
        os.remove(path)
        return True
    except OSError:
        return False
