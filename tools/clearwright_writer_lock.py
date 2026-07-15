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

ROLLOUT / MIGRATION NOTE (one-way): the ``registry.lock`` mutex here is an OS
region lock on a PERSISTENT file. The prior protocol (<= 7173ab1) used file
EXISTENCE (O_CREAT|O_EXCL, then delete on release) and holds no open handle.
The two protocols DO NOT mutually exclude: a new-code process takes the region
lock on a file an old-code process does not treat as held, so both could enter
the critical section at once; and once new code has written the persistent
file, old code can no longer parse it and spins to a 5s ``registry_lock_
unavailable`` on every acquisition. Therefore every old-code process on a queue
root (control-plane server AND any CLI) MUST be stopped before any new-code
process performs its first acquisition on that root. This is a deploy-ordering
requirement, not something either version can detect at runtime.
"""
import ctypes
import hashlib
import json
import os
import re
import socket
import sys
import time
import uuid
from datetime import datetime, timezone

if sys.platform == "win32":
    import msvcrt
else:
    import fcntl

LOCKS_DIR = "locks"
TOKENS_DIR = "tokens"
REGISTRY_LOCK = "registry.lock"
EXCLUSIVE_FLAG = "exclusive.flag"
RECOVERY_DIR = "recovery"

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
    try:
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(obj, fh, indent=2)
            fh.write("\n")
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except OSError:
        # Leave no partial temp file behind on any failure (best-effort);
        # preserve the original error for the caller.
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise
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


_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
_ERROR_ACCESS_DENIED = 5
_ERROR_INVALID_PARAMETER = 87
_KERNEL32 = None


def _kernel32():
    global _KERNEL32
    if _KERNEL32 is None:
        # use_last_error=True so ctypes.get_last_error() is reliable right
        # after a failed call (ctypes.windll does not capture it dependably).
        _KERNEL32 = ctypes.WinDLL("kernel32", use_last_error=True)
    return _KERNEL32


def _win_process_times(pid):
    """Return (creation_filetime, exit_filetime) as 64-bit ints, or None when
    the process cannot be queried. A live process reports exit_filetime 0; a
    terminated process whose kernel object lingers (open handles keep the PID
    assigned) reports a NONZERO exit_filetime -- the kernel's own record that
    the process is dead even though OpenProcess still succeeds."""
    try:
        kernel32 = _kernel32()
    except OSError:
        return None

    class _FILETIME(ctypes.Structure):
        _fields_ = [("low", ctypes.c_uint32), ("high", ctypes.c_uint32)]

    handle = kernel32.OpenProcess(_PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
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
        return ((ft_create.high << 32) | ft_create.low,
                (ft_exit.high << 32) | ft_exit.low)
    finally:
        kernel32.CloseHandle(handle)


def _win_process_start_time(pid):
    times = _win_process_times(pid)
    return None if times is None else times[0]


def _process_start_time(pid):
    if not pid:
        return None
    return _win_process_start_time(pid) if sys.platform == "win32" \
        else _posix_process_start_time(pid)


_WAIT_OBJECT_0 = 0x0
_WAIT_TIMEOUT = 0x102
_SYNCHRONIZE = 0x00100000


def _win_process_signaled(pid):
    """Documented death signal: a process object becomes signaled when the
    process terminates. WaitForSingleObject(handle, 0) == WAIT_OBJECT_0 means
    exited (defined behavior, unlike GetProcessTimes lpExitTime which MSDN
    leaves undefined for a running process). Returns True (exited), False (not
    exited), or None (cannot determine -> caller stays conservative)."""
    try:
        kernel32 = _kernel32()
    except OSError:
        return None
    try:
        handle = kernel32.OpenProcess(
            _PROCESS_QUERY_LIMITED_INFORMATION | _SYNCHRONIZE, False, pid)
    except (ctypes.ArgumentError, OverflowError):
        return None
    if not handle:
        return None
    try:
        result = kernel32.WaitForSingleObject(handle, 0)
    finally:
        kernel32.CloseHandle(handle)
    if result == _WAIT_OBJECT_0:
        return True
    if result == _WAIT_TIMEOUT:
        return False
    return None


def _win_pid_exists(pid):
    try:
        kernel32 = _kernel32()
    except OSError:
        return None
    try:
        handle = kernel32.OpenProcess(
            _PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    except (ctypes.ArgumentError, OverflowError):
        # An out-of-C-range pid cannot name a real process, but the ctypes
        # marshalling raises rather than failing the call -- treat as
        # indeterminate so a corrupt flag never throws on the hottest path.
        return None
    if not handle:
        # Discriminate WHY OpenProcess failed: a live process the caller lacks
        # rights to query (cross-user/service) fails with ACCESS_DENIED and
        # must read as "exists" (mirroring the POSIX PermissionError branch);
        # only INVALID_PARAMETER confirms the PID is gone. Anything else is
        # indeterminate -- never confirmed dead.
        err = ctypes.get_last_error()
        if err == _ERROR_INVALID_PARAMETER:
            return False
        if err == _ERROR_ACCESS_DENIED:
            return True
        return None
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
    except (OSError, OverflowError):
        # OverflowError: an out-of-range pid cannot name a process, but is not
        # a confirmed-gone signal -- stay indeterminate, never throw.
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
    if sys.platform == "win32":
        # Terminated-but-lingering: open handles keep the PID assigned and the
        # creation time unchanged, so the start-time comparison below would
        # misread a dead holder as live. The AUTHORITATIVE death signal is the
        # process object being signaled (documented); the exit FILETIME is used
        # only as corroboration (MSDN leaves lpExitTime undefined for a running
        # process, so it must never be the sole basis for a destructive sweep).
        signaled = _win_process_signaled(pid)
        if signaled is True:
            times = _win_process_times(pid)
            if times is not None and times[1] != 0:
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
    on either side is atomic with respect to the other.

    Implemented as an exclusive OS region lock on a PERSISTENT file: the
    kernel releases the lock when the holding process dies (however it dies),
    so there is no stale-lock state and no steal path -- the create/delete
    file mutex this replaces carried a check-then-remove race in its stale
    steal that could delete a live holder's lock. Byte 0 is the dedicated
    lock byte; owner info after it is informational/diagnostic only. The fd
    is non-inheritable (PEP 446 default for os.open), so a child spawned by a
    holder can never prolong the lock. Non-reentrant; one acquisition per
    instance."""

    def __init__(self, root, purpose):
        self.root = root
        self.purpose = purpose
        self.path = os.path.join(_locks_dir(root), REGISTRY_LOCK)
        self._fd = None

    def __enter__(self):
        os.makedirs(_locks_dir(self.root), exist_ok=True)
        fd = os.open(self.path, os.O_RDWR | os.O_CREAT, 0o644)
        try:
            if os.fstat(fd).st_size == 0:
                try:
                    os.write(fd, b"\0")
                    os.fsync(fd)
                except OSError:
                    # A racing initializer already wrote+locked byte 0 (on
                    # Windows the write to a region another process locked
                    # raises): the spin loop below then waits for it. Any other
                    # I/O error resolves the same way -- successful acquisition
                    # or the controlled registry_lock_unavailable on deadline.
                    pass
            deadline = time.monotonic() + _LOCK_SPIN_MAX_WAIT
            while True:
                try:
                    # msvcrt.locking operates at the CURRENT offset: seek to 0
                    # immediately before both lock and unlock, same 1-byte range.
                    os.lseek(fd, 0, os.SEEK_SET)
                    if sys.platform == "win32":
                        msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
                    else:
                        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except OSError:
                    if time.monotonic() >= deadline:
                        raise WriterLockError("registry_lock_unavailable")
                    time.sleep(_LOCK_SPIN_SECONDS)
        except Exception:
            os.close(fd)
            raise
        self._fd = fd
        # Diagnostic owner info, best-effort: written after byte 0, prior
        # (possibly longer) payload truncated away, offset reset to 0 so the
        # unlock range matches the lock range. Never fails the mutex.
        try:
            pid, host, proc_start = _self_owner()
            payload = json.dumps({"pid": pid, "host": host,
                                  "proc_start": proc_start,
                                  "created_at": _now_iso(),
                                  "purpose": self.purpose}).encode("utf-8")
            os.ftruncate(fd, 1)
            os.lseek(fd, 1, os.SEEK_SET)
            os.write(fd, payload)
            os.fsync(fd)
            os.lseek(fd, 0, os.SEEK_SET)
        except OSError:
            pass
        return self

    def __exit__(self, exc_type, exc, tb):
        fd, self._fd = self._fd, None
        if fd is not None:
            try:
                os.lseek(fd, 0, os.SEEK_SET)
                if sys.platform == "win32":
                    msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
                else:
                    fcntl.flock(fd, fcntl.LOCK_UN)
            except OSError:
                pass
            finally:
                os.close(fd)
        return False


# --------------------------------------------------------------------------- #
# Stale-exclusive recovery: identity-proven, never age-based, fail-closed on
# any uncertainty. Runs ONLY inside a held _RegistryLock critical section.
# --------------------------------------------------------------------------- #

_HOLDER_KEY_RE = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
_PENDING_BASENAME_RE = re.compile(r"^pending-.+\.json$")
# Windows caps PIDs well under 2**32; POSIX pid_t is a 32-bit signed int. A
# value above this cannot name a real process and is out of the range the
# OS query APIs accept, so it is rejected as malformed pre-liveness.
_PID_MAX = 0x7FFFFFFF
# Sentinel distinguishing an ABSENT pending_path key (legacy pre-upgrade flag)
# from a PRESENT value that must validate -- truthiness/None never decides it.
_ABSENT = object()


def _recovery_dir(root):
    return os.path.join(_locks_dir(root), RECOVERY_DIR)


def _stamp():
    # Colon-free compact stamp: ':' is illegal in Windows filenames. The full
    # ISO timestamp lives INSIDE the record, not in the name.
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")


def _holder_key(rec):
    """Filename-safe dedup key for a flag record. The canonical nonce
    (uuid4().hex) is used verbatim; anything non-conforming -- including a
    missing nonce on a fabricated/corrupt flag -- falls back to a hash of
    repr-normalized identity fields, so the key is always defined and raw
    untrusted content never reaches a filename."""
    nonce = rec.get("nonce")
    if isinstance(nonce, str) and _HOLDER_KEY_RE.fullmatch(nonce):
        return nonce
    basis = "|".join(repr(rec.get(k)) for k in ("pid", "proc_start", "created_at"))
    return hashlib.sha256(basis.encode("utf-8")).hexdigest()[:12]


def _archive_root_for(root):
    # Mirrors clearwright_archive.archive_root(queue_root) exactly (the
    # sibling "archive" directory next to the queue root); duplicated here
    # because the dependency direction is archive -> writer_lock, never the
    # reverse.
    parent = os.path.dirname(os.path.normpath(os.path.abspath(root)))
    return os.path.join(parent, "archive")


def _flag_identity_ok(rec):
    """Strict pre-liveness identity schema: pid is a positive int and NOT a
    bool (bool subclasses int -- JSON true would otherwise evaluate as PID 1),
    host and proc_start are non-empty strings (the exact representation
    liveness consumes). liveness() is consulted only after this passes, so a
    type-corrupt flag can never be judged dead or throw on this path."""
    pid = rec.get("pid")
    if (isinstance(pid, bool) or not isinstance(pid, int)
            or pid <= 0 or pid > _PID_MAX):
        # Upper bound: a pid above the OS ceiling cannot name a real process
        # and would raise in the ctypes/os.kill marshalling -- classify as
        # malformed here so liveness is never consulted on it.
        return False
    host = rec.get("host")
    if not isinstance(host, str) or not host:
        return False
    proc_start = rec.get("proc_start")
    if not isinstance(proc_start, str) or not proc_start:
        return False
    return True


def _pending_path_state(root, rec):
    """Classify the flag's pending_path claim.

    Returns 'absent' (no key -- legacy pre-upgrade flag), 'outstanding' (valid
    claim, journal file still present), 'resolved' (valid claim, journal gone),
    or 'invalid' (malformed claim -- fail closed, never honored). A PRESENT key
    is validated regardless of value: present null, non-string, relative,
    wrong-basename, or out-of-tree all classify 'invalid' (truthiness/None
    never decides validity, mirroring the D1 round-list contract). Validity
    requires: opid a non-empty string; pending_path an ABSOLUTE string whose
    basename is exactly pending-<opid>.json and whose resolved path is
    contained in the queue's archive tree (commonpath equality after
    realpath+normcase -- never a string-prefix test, which a sibling like
    archive-evil/ would defeat; ValueError from commonpath is invalid)."""
    pending = rec.get("pending_path", _ABSENT)
    if pending is _ABSENT:
        return "absent"
    opid = rec.get("opid")
    if not isinstance(pending, str) or not pending:
        return "invalid"
    if not isinstance(opid, str) or not opid:
        return "invalid"
    if not os.path.isabs(pending):
        return "invalid"
    if os.path.basename(pending) != "pending-{}.json".format(opid):
        return "invalid"
    archive_root = os.path.normcase(os.path.realpath(_archive_root_for(root)))
    candidate = os.path.normcase(os.path.realpath(pending))
    try:
        if os.path.commonpath([archive_root, candidate]) != archive_root:
            return "invalid"
    except ValueError:
        return "invalid"
    return "outstanding" if os.path.isfile(pending) else "resolved"


def _archive_has_pending_journal(root):
    """True if any unresolved pending-*.json journal exists under the archive
    tree. Used for legacy (pre-upgrade) exclusive flags that predate the
    pending_path field: a dead old-archive holder that crashed mid-move leaves
    a real journal the flag cannot name, so the archive tree itself is the
    source of truth. A two-level walk mirrors clearwright_archive.recover_pending
    (dependency direction is archive -> writer_lock, so it is not imported)."""
    aroot = _archive_root_for(root)
    if not os.path.isdir(aroot):
        return False
    try:
        months = os.listdir(aroot)
    except OSError:
        return True  # unreadable archive tree -> fail closed (assume pending)
    for month in months:
        mdir = os.path.join(aroot, month)
        if not os.path.isdir(mdir):
            continue
        try:
            names = os.listdir(mdir)
        except OSError:
            return True
        if any(_PENDING_BASENAME_RE.match(n) for n in names):
            return True
    return False


def _write_recovery_record(root, kind, holder, state, boundary, recovered):
    """Durably write one recovery/refusal record BEFORE any flag mutation,
    deduplicated per holder_key within its kind. Returns True on success
    (including dedup-skip); False on any OSError -- the caller must then
    ABORT the recovery attempt controlled (flag retained, standard refusal).
    Records use the module's atomic-write pattern; a jsonl append would not
    be torn-write-safe."""
    key = _holder_key(holder)
    try:
        directory = _recovery_dir(root)
        os.makedirs(directory, exist_ok=True)
        _fsync_dir(_locks_dir(root))
        suffix = "-{}.json".format(key)
        for name in os.listdir(directory):
            if name.startswith(kind + "-") and name.endswith(suffix):
                return True
        record = {"recovered": recovered, "state": state, "boundary": boundary,
                  "holder": holder,
                  "recovered_by": dict(zip(("pid", "host", "proc_start"),
                                           _self_owner())),
                  "recovered_at": _now_iso()}
        path = os.path.join(directory,
                            "{}-{}-{}.json".format(kind, _stamp(), key))
        _atomic_write(path, record)
        return True
    except OSError:
        return False


def _recover_stale_exclusive_locked(root, boundary):
    """Evaluate (and possibly recover) the exclusive flag. MUST be called with
    the registry lock held. Decision is identity-only -- never age -- and a
    pure function of the durable flag record plus live OS process state, so it
    is deterministic across process restarts and idempotent on repeats.

    Returns {"recovered": bool, "state": one of absent | malformed | live |
    indeterminate | interrupted_operation | dead}. Every non-"absent",
    non-recovered outcome leaves the flag byte-identical."""
    flag_path = os.path.join(_locks_dir(root), EXCLUSIVE_FLAG)
    if not os.path.isfile(flag_path):
        return {"recovered": False, "state": "absent"}
    rec = _read_json(flag_path)
    if not isinstance(rec, dict) or not _flag_identity_ok(rec):
        return {"recovered": False, "state": "malformed"}
    pending_state = _pending_path_state(root, rec)
    if pending_state == "invalid":
        return {"recovered": False, "state": "malformed",
                "detail": "pending_path_invalid"}
    state = liveness(rec.get("pid"), rec.get("host"), rec.get("proc_start"))
    if state != "dead":
        return {"recovered": False, "state": state}
    # Confirmed-dead holder with an unresolved archive journal: retention is
    # DELIBERATE fail-closed containment (auto-clearing would expose
    # mid-operation archive state). A flag that names its own pending journal
    # (pending_state 'outstanding') is authoritative; a LEGACY flag with no
    # pending_path key predates the field, so the archive tree is scanned
    # directly -- a crashed old-code archiver's real journal must not be
    # cleared just because the flag couldn't record it. The refusal record
    # makes the retained state diagnosable; resolution is archive-side
    # recover_pending or the audited operator override.
    if pending_state == "outstanding" or (
            pending_state == "absent" and _archive_has_pending_journal(root)):
        _write_recovery_record(root, "refusal", rec, "interrupted_operation",
                               boundary, False)
        return {"recovered": False, "state": "interrupted_operation"}
    # Confirmed dead, no outstanding journal: evidence FIRST (durable record
    # before the flag -- its only source -- is destroyed), then removal. A
    # record-write failure aborts controlled: flag retained, standard refusal.
    if not _write_recovery_record(root, "recovered", rec, "dead", boundary,
                                  True):
        return {"recovered": False, "state": "indeterminate",
                "detail": "recovery_record_unwritable"}
    try:
        os.remove(flag_path)
    except OSError:
        return {"recovered": False, "state": "indeterminate",
                "detail": "flag_remove_failed"}
    _fsync_dir(_locks_dir(root))
    return {"recovered": True, "state": "dead"}


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
        _recover_stale_exclusive_locked(root, "write_token")
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


def acquire_exclusive(root, opid, deadline_seconds=DEFAULT_DRAIN_DEADLINE_SECONDS,
                      pending_path=None):
    """Acquire the archive-exclusive flag: sweep only CONFIRMED-dead tokens,
    then require zero remaining (live or indeterminate) tokens before setting
    the flag, retrying until deadline_seconds. Fails safe (no flag left) on
    timeout -- never sweeps a live or indeterminate token to force progress.

    ``pending_path`` (optional): the caller's pending-journal path, recorded
    in the flag so boundary recovery can distinguish a dead holder with an
    unresolved resumable operation (retained fail-closed) from a plainly dead
    holder (recovered). Stored as an absolute path; identity decisions never
    consult it."""
    nonce = uuid.uuid4().hex
    started = time.monotonic()
    while True:
        with _RegistryLock(root, "archive_acquire"):
            _recover_stale_exclusive_locked(root, "acquire_exclusive")
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
                if pending_path is not None:
                    flag["pending_path"] = os.path.abspath(pending_path)
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
    explicit, audited operator override (see clearwright_archive).

    Thin public wrapper over the shared boundary-recovery primitive: returns
    True only when a confirmed-dead holder's flag was actually removed (a
    durable recovery record is written first); False for absent, malformed,
    live, indeterminate, and interrupted_operation (flag retained while the
    dead holder's pending journal is unresolved)."""
    with _RegistryLock(root, "exclusive_recovery"):
        result = _recover_stale_exclusive_locked(root, "clear_stale_exclusive")
    return bool(result.get("recovered"))


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
