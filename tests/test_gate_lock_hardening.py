"""Gate Validation and Lock Recovery Hardening (work item
message:msg-20260715T204946669871).

Two corrected defects from verification council cw-council-20260715T201858601794:
F1 -- strict round validation accepted JSON true for round 1 (Python bool
subclasses int), and F2 -- a force-killed exclusive-lock holder left the queue
in permanent maintenance (no liveness consulted at any acquisition boundary;
clear_stale_exclusive had no production caller).

The repaired contracts pinned here: rounds must be actual integers (never
bools/floats/strings; non-object round files and present non-list council
"rounds" values fail closed); the exclusive flag is recovered at BOTH
acquisition boundaries only on identity-PROVEN death (exit-FILETIME-aware and
ACCESS_DENIED-aware on Windows), never by age, never for live/uncertain/
malformed holders, with per-holder durable recovery records written before
removal; a dead holder with an unresolved pending archive journal is retained
fail-closed with a durable refusal record and a token-free escape via
recover_pending; the registry mutex is an OS region lock released by the
kernel on process death (no stale state, no steal path).

The module doubles as the live-verification fixture:
  python -m tests.test_gate_lock_hardening --hold-exclusive <queue-root>
      --ready-file <p> --stop-file <p>
  python -m tests.test_gate_lock_hardening --live-fixture <queue-root>
  python -m tests.test_gate_lock_hardening --liveness-check <queue-root>
"""
import contextlib
import gc
import hashlib
import http.client
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from argparse import Namespace
from http.server import ThreadingHTTPServer

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APP_DIR = os.path.join(REPO_ROOT, "apps", "control-plane")
TOOLS_DIR = os.path.join(REPO_ROOT, "tools")
sys.path.insert(0, APP_DIR)
sys.path.insert(0, TOOLS_DIR)
import server  # noqa: E402
import clearwright_archive as cwa  # noqa: E402
import clearwright_gate as cwg  # noqa: E402
import clearwright_message as cwm  # noqa: E402
import clearwright_review_council as cwrc  # noqa: E402
import clearwright_work as cww  # noqa: E402
import clearwright_writer_lock as cwl  # noqa: E402
import clearwright_use_cw as ucw  # noqa: E402

WIN = sys.platform == "win32"
PY = sys.executable


def queue(prefix, tc):
    # The queue root is nested one level below the temp base so its SIBLING
    # archive root (clearwright_archive.archive_root = parent/archive) is
    # unique per test and reaped with the base -- roots placed directly in
    # %TEMP% would share one archive tree and cross-contaminate.
    base = tempfile.mkdtemp(prefix=prefix)
    tc.addCleanup(shutil.rmtree, base, ignore_errors=True)
    root, *_ = server.resolve_queue(os.path.join(base, "queue"))
    return root


def run(func, **kw):
    kw.setdefault("json", True)
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        code = func(Namespace(**kw))
    return json.loads(buf.getvalue().strip().splitlines()[-1]), code


def make_item(root, text="Governed request."):
    r = server.do_message(root, {"actor": "OPERATOR-0001", "role": "operator",
                                 "source": "use-cw", "intent": "request",
                                 "message": text})
    m = r["message"]
    return "message:" + m["message_id"], m["thread_id"]


def mock_verdict(reviewer, verdict="revise"):
    return {"reviewer": reviewer, "verdict": verdict, "confidence": 0.9,
            "risk_level": "low", "blocking_findings": [], "required_changes": [],
            "nonblocking_findings": [], "disagreements": [], "assumptions": [],
            "questions": [], "recommended_plan": [],
            "summary": "A substantive review."}


def fabricated_round(n, phase="plan", verdict="revise"):
    return {"round": n, "phase": phase, "at": cwm._now_iso(),
            "substantive": True, "context_sha256": "f" * 64,
            "fingerprints": {}, "attempts": {"gpt": 1, "codex": 1},
            "artifact_ids": [], "artifact_hashes": [],
            "delivery": {"gpt": "text_only", "codex": "stdin_prompt"},
            "gpt": {"ok": True, "posted": True, "reviewer": "gpt",
                    "verdict": mock_verdict("gpt", verdict), "validated": True,
                    "source": "openai-api", "telemetry": {}, "message_id": "g"},
            "codex": {"ok": True, "posted": True, "reviewer": "codex",
                      "verdict": mock_verdict("codex", verdict),
                      "validated": True, "source": "codex-cli",
                      "telemetry": {}, "message_id": "c"},
            "reconciliation": None}


def fabricate_capped_council(root, thread, wid, phase="plan"):
    c = cwrc.create_council(root, thread_id=thread, work_item_id=wid,
                            phase=phase, min_rounds=2, max_rounds=2,
                            approved_scope="operator approved scope")
    for n in (1, 2):
        cwrc.save_round(root, c, fabricated_round(n, phase=phase))
        c = cwrc.load_council(root, c["council_id"])
    outcome = cwrc.evaluate(c, cwrc.load_rounds(root, c["council_id"]))
    cwrc.save_outcome(root, c["council_id"], outcome)
    return cwrc.load_council(root, c["council_id"])


def council_dir(root, cid):
    return os.path.dirname(cwrc._round_path(root, cid, 1))


def load_outcome(root, cid):
    with open(os.path.join(council_dir(root, cid), "outcome.json"),
              encoding="utf-8") as fh:
        return json.load(fh)


def set_round_payload(root, cid, n, payload):
    path = cwrc._round_path(root, cid, n)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh) if not isinstance(payload, str) else fh.write(payload)


def set_council_rounds(root, cid, value, remove=False):
    path = os.path.join(council_dir(root, cid), "council.json")
    with open(path, encoding="utf-8") as fh:
        rec = json.load(fh)
    if remove:
        rec.pop("rounds", None)
    else:
        rec["rounds"] = value
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(rec, fh)


def flag_path(root):
    return os.path.join(cwl._locks_dir(root), cwl.EXCLUSIVE_FLAG)


def fab_flag(root, **over):
    """A fabricated exclusive flag; defaults describe a CONFIRMED-DEAD holder
    (this live process's pid with a wrong proc_start = the PID-reuse /
    different-identity signal, deterministic on every platform)."""
    rec = {"opid": "op-test", "nonce": "cafe" * 8, "pid": os.getpid(),
           "host": cwl._this_host(), "proc_start": "not-the-real-start",
           "created_at": cwl._now_iso()}
    rec.update(over)
    for k in [k for k, v in rec.items() if v is _REMOVE]:
        del rec[k]
    cwl._atomic_write(flag_path(root), rec)
    return rec


_REMOVE = object()


def raw_flag(root, text):
    path = flag_path(root)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)


def recovery_records(root, kind):
    directory = os.path.join(cwl._locks_dir(root), cwl.RECOVERY_DIR)
    if not os.path.isdir(directory):
        return []
    out = []
    for name in sorted(os.listdir(directory)):
        if name.startswith(kind + "-") and name.endswith(".json"):
            with open(os.path.join(directory, name), encoding="utf-8") as fh:
                out.append((name, json.load(fh)))
    return out


def assert_record_schema(tc, record, holder, recovered):
    tc.assertIsInstance(record["recovered"], bool)
    tc.assertEqual(record["recovered"], recovered)
    tc.assertIsInstance(record["state"], str)
    tc.assertIsInstance(record["boundary"], str)
    tc.assertEqual(record["holder"], holder)
    rb = record["recovered_by"]
    tc.assertIsInstance(rb["pid"], int)
    tc.assertIsInstance(rb["host"], str)
    tc.assertIsInstance(rb["proc_start"], str)
    tc.assertRegex(record["recovered_at"], r"^\d{4}-\d{2}-\d{2}T")


SNAPSHOT_EXCLUDE = (cwl.EXCLUSIVE_FLAG, cwl.RECOVERY_DIR, cwl.REGISTRY_LOCK)


def snapshot(root):
    out = {}
    for dirpath, _dirs, files in os.walk(root):
        for name in files:
            full = os.path.join(dirpath, name)
            rel = os.path.relpath(full, root)
            if any(part in SNAPSHOT_EXCLUDE for part in rel.split(os.sep)):
                continue
            with open(full, "rb") as fh:
                out[rel] = hashlib.sha256(fh.read()).hexdigest()
    return out


def start_holder(tc, root, hold_registry=False):
    """A REAL child process holding the exclusive flag (or registry lock),
    with a ready-file handshake and a cooperative stop-file; try/finally in
    the harness guarantees reaping even on assertion failure."""
    base = tempfile.mkdtemp(prefix="holder_")
    tc.addCleanup(shutil.rmtree, base, ignore_errors=True)
    ready, stop = os.path.join(base, "ready"), os.path.join(base, "stop")
    mode = "--hold-registry" if hold_registry else "--hold-exclusive"
    proc = subprocess.Popen(
        [PY, "-m", "tests.test_gate_lock_hardening", mode, root,
         "--ready-file", ready, "--stop-file", stop],
        cwd=REPO_ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

    def reap():
        if proc.poll() is None:
            proc.kill()
            proc.wait(timeout=30)
    tc.addCleanup(reap)
    deadline = time.monotonic() + 30
    while not os.path.isfile(ready):
        if proc.poll() is not None:
            raise AssertionError("holder exited before ready")
        if time.monotonic() > deadline:
            raise AssertionError("holder never became ready")
        time.sleep(0.05)
    return proc, stop


def release_child_handle(proc):
    """After kill()+wait(), drop every harness handle so the OS can retire the
    PID (on Windows a retained Popen handle keeps the process object alive)."""
    if WIN and getattr(proc, "_handle", None) is not None:
        proc._handle.Close()
        proc._handle = None
    gc.collect()


# --------------------------------------------------------------------------- #
# D1: strict round validation (packet tests 1-4, 17)
# --------------------------------------------------------------------------- #

class RoundValidationTests(unittest.TestCase):

    def both_families(self, mutate, expect_invariant, expect_detail=None):
        """Run one malformed-state mutation through BOTH path families:
        outcome-time (record_escalation_gate on the persisted outcome) and the
        healing sweep (heal_escalation_gates). Fresh fixture per family."""
        for family in ("outcome_time", "healing"):
            with self.subTest(family=family):
                root = queue("rv_", self)
                wid, thread = make_item(root)
                c = fabricate_capped_council(root, thread, wid)
                cid = c["council_id"]
                mutate(root, cid)
                if family == "outcome_time":
                    res = cwg.record_escalation_gate(
                        root, cid, load_outcome(root, cid),
                        {"work_item_id": wid, "thread_id": thread})
                else:
                    res = cwg.heal_escalation_gates(root, wid)
                self.assertFalse(res.get("ok"), res)
                self.assertEqual(res.get("error"), "gate_creation_failed")
                self.assertEqual(res.get("invariant"), expect_invariant)
                if expect_detail is not None:
                    self.assertIn(expect_detail, res.get("detail") or "", res)
                self.assertEqual(cwg.load_gates(root, wid), [])

    def test_round_true_rejected_both_families(self):
        # detail must render True distinctly (not normalized to 1).
        def mutate(root, cid):
            payload = fabricated_round(1)
            payload["round"] = True
            set_round_payload(root, cid, 1, payload)
        self.both_families(mutate, "round_records_inconsistent",
                           expect_detail="round=True")

    def test_round_false_rejected_both_families(self):
        def mutate(root, cid):
            payload = fabricated_round(1)
            payload["round"] = False
            set_round_payload(root, cid, 1, payload)
        self.both_families(mutate, "round_records_inconsistent",
                           expect_detail="round=False")

    def test_valid_integer_rounds_create_exactly_one_gate(self):
        root = queue("rv_", self)
        wid, thread = make_item(root)
        c = fabricate_capped_council(root, thread, wid)
        res = cwg.record_escalation_gate(root, c["council_id"],
                                         load_outcome(root, c["council_id"]),
                                         {"work_item_id": wid,
                                          "thread_id": thread})
        self.assertTrue(res.get("ok"), res)
        gates = cwg.load_gates(root, wid)
        self.assertEqual(len(gates), 1)
        self.assertEqual(gates[0]["outcome"], "operator_required")
        again = cwg.record_escalation_gate(root, c["council_id"],
                                           load_outcome(root, c["council_id"]),
                                           {"work_item_id": wid,
                                            "thread_id": thread})
        self.assertTrue(again.get("ok"))
        self.assertTrue(again.get("deduplicated"))
        self.assertEqual(len(cwg.load_gates(root, wid)), 1)

    def test_round_value_matrix_rejected_both_families(self):
        for bad in ("1", 1.0, None):
            detail = "round=None" if bad is None else "round={!r}".format(bad)

            def mutate(root, cid, bad=bad):
                payload = fabricated_round(1)
                if bad is None:
                    payload.pop("round")
                else:
                    payload["round"] = bad
                set_round_payload(root, cid, 1, payload)
            with self.subTest(bad=repr(bad)):
                self.both_families(mutate, "round_records_inconsistent",
                                   expect_detail=detail)

    def test_non_object_round_files_rejected_both_families(self):
        # Valid-JSON non-object payloads are classified by the shared strict
        # reader (_strict_json returns 'unreadable' for any non-dict), so they
        # fail closed as round_records_unreadable -- deterministic, never an
        # AttributeError. Declared as amendment L-A2: the plan named
        # round_records_inconsistent for this class; the shipped (pre-existing)
        # classification already satisfies the substance of the requirement.
        for text in ('"a string"', '[1, 2]'):
            def mutate(root, cid, text=text):
                set_round_payload(root, cid, 1, text)
            with self.subTest(payload=text):
                self.both_families(mutate, "round_records_unreadable")

    def test_missing_round_file_stays_unreadable(self):
        def mutate(root, cid):
            os.remove(cwrc._round_path(root, cid, 1))
        self.both_families(mutate, "round_records_unreadable")

    def test_present_nonlist_rounds_values_rejected_both_families(self):
        for bad in (False, 0, "", {}, None, [True], [1, 1]):
            def mutate(root, cid, bad=bad):
                set_council_rounds(root, cid, bad)
            with self.subTest(bad=repr(bad)):
                self.both_families(mutate, "round_records_inconsistent")

    def test_absent_rounds_key_is_the_supported_legacy_shape(self):
        root = queue("rv_", self)
        wid, thread = make_item(root)
        c = fabricate_capped_council(root, thread, wid)
        set_council_rounds(root, c["council_id"], None, remove=True)
        res = cwg.record_escalation_gate(root, c["council_id"],
                                         load_outcome(root, c["council_id"]),
                                         {"work_item_id": wid,
                                          "thread_id": thread})
        self.assertTrue(res.get("ok"), res)
        gates = cwg.load_gates(root, wid)
        self.assertEqual(len(gates), 1)
        self.assertEqual(gates[0]["outcome"], "operator_required")


# --------------------------------------------------------------------------- #
# D3: recovery decision matrix on fabricated records (packet tests 5, 8-11)
# --------------------------------------------------------------------------- #

class RecoveryDecisionTests(unittest.TestCase):

    def test_normal_acquire_release_leaves_no_recovery_state(self):
        root = queue("rd_", self)
        before = snapshot(root)
        flag = cwl.acquire_exclusive(root, "op-normal", deadline_seconds=5)
        self.assertTrue(os.path.isfile(flag_path(root)))
        self.assertTrue(cwl.release_exclusive(root, "op-normal", flag["nonce"]))
        self.assertFalse(os.path.isfile(flag_path(root)))
        self.assertEqual(recovery_records(root, "recovered"), [])
        self.assertEqual(recovery_records(root, "refusal"), [])
        self.assertEqual(snapshot(root), before)

    def test_dead_identity_recovers_with_schema_valid_record(self):
        root = queue("rd_", self)
        before = snapshot(root)
        holder = fab_flag(root)
        token = cwl.acquire_write_token(root)
        try:
            self.assertFalse(os.path.isfile(flag_path(root)))
            recs = recovery_records(root, "recovered")
            self.assertEqual(len(recs), 1)
            assert_record_schema(self, recs[0][1], holder, True)
            self.assertEqual(recs[0][1]["state"], "dead")
            self.assertEqual(recs[0][1]["boundary"], "write_token")
        finally:
            cwl.release_write_token(root, token)
        self.assertEqual(snapshot(root), before)

    def test_live_matching_holder_never_cleared(self):
        root = queue("rd_", self)
        pid, host, proc_start = cwl._self_owner()
        fab_flag(root, pid=pid, host=host, proc_start=proc_start)
        with open(flag_path(root), "rb") as fh:
            before_bytes = fh.read()
        with self.assertRaises(cwl.MaintenanceInProgress):
            cwl.acquire_write_token(root)
        with open(flag_path(root), "rb") as fh:
            self.assertEqual(fh.read(), before_bytes)
        self.assertEqual(recovery_records(root, "recovered"), [])

    @unittest.skipUnless(WIN, "Windows PID/error-code semantics")
    def test_access_denied_live_pid_is_indeterminate_not_dead(self):
        # PID 4 (System) exists but a non-elevated caller cannot open it:
        # pre-E2 this read as dead and would have cleared a live holder.
        self.assertIs(cwl._win_pid_exists(4), True)
        root = queue("rd_", self)
        fab_flag(root, pid=4, proc_start="1")
        before = snapshot(root)
        with self.assertRaises(cwl.MaintenanceInProgress):
            cwl.acquire_write_token(root)
        self.assertTrue(os.path.isfile(flag_path(root)))
        self.assertEqual(recovery_records(root, "recovered"), [])
        self.assertEqual(snapshot(root), before)

    def test_own_live_process_reads_live_not_dead(self):
        # E1/E2 corroboration through the documented death signal: THIS process
        # is alive, so liveness must never call it dead regardless of platform.
        pid, host, proc_start = cwl._self_owner()
        self.assertEqual(cwl.liveness(pid, host, proc_start), "live")
        if WIN:
            self.assertIs(cwl._win_process_signaled(pid), False)

    @unittest.skipUnless(WIN, "Windows PID/error-code semantics")
    def test_ghost_pid_reads_dead_and_recovers(self):
        child = subprocess.Popen([PY, "-c", "pass"])
        child.wait(timeout=30)
        pid = child.pid
        release_child_handle(child)
        if cwl._win_pid_exists(pid) is not False:
            self.skipTest("pid reused between reap and check")
        root = queue("rd_", self)
        fab_flag(root, pid=pid, proc_start="12345")
        token = cwl.acquire_write_token(root)
        try:
            self.assertEqual(len(recovery_records(root, "recovered")), 1)
        finally:
            cwl.release_write_token(root, token)

    def test_cross_host_is_uncertain_and_refused(self):
        root = queue("rd_", self)
        fab_flag(root, host="some-other-host")
        before = snapshot(root)
        with self.assertRaises(cwl.MaintenanceInProgress):
            cwl.acquire_write_token(root)
        self.assertTrue(os.path.isfile(flag_path(root)))
        self.assertEqual(recovery_records(root, "recovered"), [])
        self.assertEqual(recovery_records(root, "refusal"), [])
        self.assertEqual(snapshot(root), before)

    def test_malformed_metadata_matrix_refuses_fail_closed(self):
        cases = [
            ("non-json", lambda root: raw_flag(root, "{not json")),
            ("json-string", lambda root: raw_flag(root, '"x"')),
            ("json-array", lambda root: raw_flag(root, '[1]')),
            ("missing-proc-start",
             lambda root: fab_flag(root, proc_start=_REMOVE)),
            ("pid-true", lambda root: fab_flag(root, pid=True)),
            ("pid-string", lambda root: fab_flag(root, pid="123")),
            ("pid-list", lambda root: fab_flag(root, pid=[1])),
            ("pid-null", lambda root: fab_flag(root, pid=None)),
            ("pid-out-of-range", lambda root: fab_flag(root, pid=2 ** 62)),
            ("host-int", lambda root: fab_flag(root, host=1)),
            ("host-empty", lambda root: fab_flag(root, host="")),
            ("proc-start-null", lambda root: fab_flag(root, proc_start=None)),
            ("proc-start-int", lambda root: fab_flag(root, proc_start=7)),
        ]
        for name, build in cases:
            with self.subTest(case=name):
                root = queue("rd_", self)
                build(root)
                before = snapshot(root)
                with open(flag_path(root), "rb") as fh:
                    before_bytes = fh.read()
                with self.assertRaises(cwl.MaintenanceInProgress):
                    cwl.acquire_write_token(root)
                with open(flag_path(root), "rb") as fh:
                    self.assertEqual(fh.read(), before_bytes)
                self.assertEqual(recovery_records(root, "recovered"), [])
                self.assertEqual(recovery_records(root, "refusal"), [])
                self.assertEqual(snapshot(root), before)

    def probe_state(self, root):
        """Directly observe the primitive's classification (the pending_path_
        invalid diagnostic is not surfaced by any production caller, so pin it
        here). _RegistryLock is non-reentrant but the boundary call already
        released it."""
        with cwl._RegistryLock(root, "test_probe"):
            return cwl._recover_stale_exclusive_locked(root, "test_probe")

    def test_invalid_pending_path_matrix_refuses_fail_closed(self):
        outside = tempfile.mkdtemp(prefix="foreign_")
        self.addCleanup(shutil.rmtree, outside, ignore_errors=True)
        foreign = os.path.join(outside, "pending-op-test.json")
        with open(foreign, "w", encoding="utf-8") as fh:
            fh.write("{}")

        def sibling_escape(root):
            evil = cwa.archive_root(root) + "-evil"
            os.makedirs(evil, exist_ok=True)
            p = os.path.join(evil, "pending-op-test.json")
            with open(p, "w", encoding="utf-8") as fh:
                fh.write("{}")
            return p

        def traversal(root):
            # Lexically inside the archive tree but resolves outside via '..';
            # distinguishes realpath-before-commonpath from naive containment.
            p = os.path.join(cwa.archive_root(root), "..", "outside",
                             "pending-op-test.json")
            os.makedirs(os.path.dirname(p), exist_ok=True)
            with open(p, "w", encoding="utf-8") as fh:
                fh.write("{}")
            return p

        # (name, pending_path builder, opid-override). A present but invalid
        # pending_path must classify malformed/pending_path_invalid and be
        # retained -- never honored as an interrupted-operation claim.
        cases = [
            ("foreign-abs", lambda root: foreign, "op-test"),
            ("relative", lambda root: "pending-op-test.json", "op-test"),
            ("wrong-basename", lambda root: os.path.join(
                cwa.month_dir(root), "pending-other.json"), "op-test"),
            ("sibling-escape", sibling_escape, "op-test"),
            ("traversal", traversal, "op-test"),
            ("present-null", lambda root: None, "op-test"),
            ("present-null-no-opid", lambda root: None, _REMOVE),
            ("missing-opid", lambda root: os.path.join(
                cwa.month_dir(root), "pending-op-test.json"), _REMOVE),
        ]
        for name, build, opid in cases:
            with self.subTest(case=name):
                root = queue("rd_", self)
                fab_flag(root, opid=opid, pending_path=build(root))
                before = snapshot(root)
                with open(flag_path(root), "rb") as fh:
                    before_bytes = fh.read()
                with self.assertRaises(cwl.MaintenanceInProgress):
                    cwl.acquire_write_token(root)
                with open(flag_path(root), "rb") as fh:
                    self.assertEqual(fh.read(), before_bytes)
                self.assertEqual(recovery_records(root, "recovered"), [])
                self.assertEqual(recovery_records(root, "refusal"), [])
                self.assertEqual(snapshot(root), before)
                # Pin the exact committed classification + diagnostic.
                self.assertEqual(self.probe_state(root),
                                 {"recovered": False, "state": "malformed",
                                  "detail": "pending_path_invalid"})

    def test_idempotency_and_crash_window_replay(self):
        root = queue("rd_", self)
        holder = fab_flag(root)
        before = snapshot(root)
        token = cwl.acquire_write_token(root)
        cwl.release_write_token(root, token)
        self.assertEqual(len(recovery_records(root, "recovered")), 1)
        token = cwl.acquire_write_token(root)
        cwl.release_write_token(root, token)
        self.assertEqual(len(recovery_records(root, "recovered")), 1)
        # Crash-window replay: record already durable, flag still present
        # (simulating death between record write and flag removal).
        cwl._atomic_write(flag_path(root), holder)
        token = cwl.acquire_write_token(root)
        cwl.release_write_token(root, token)
        self.assertFalse(os.path.isfile(flag_path(root)))
        self.assertEqual(len(recovery_records(root, "recovered")), 1)
        # The only durable deltas are the (removed) flag and the recovery dir,
        # both excluded from the snapshot; nothing else in the tree moved.
        self.assertEqual(snapshot(root), before)

    def test_record_write_failure_aborts_controlled(self):
        # Fault-inject os.replace for the recovery record so the tmp file is
        # actually created before failure -- exercises the real partial-write
        # path (a wholesale _atomic_write patch would raise before any tmp
        # exists and pass vacuously). Proves controlled abort AND that no
        # partial .tmp survives.
        root = queue("rd_", self)
        holder = fab_flag(root)
        before = snapshot(root)
        orig_replace = os.replace

        def failing_replace(src, dst, *a, **k):
            if cwl.RECOVERY_DIR in os.path.dirname(dst).split(os.sep):
                raise OSError("simulated os.replace failure")
            return orig_replace(src, dst, *a, **k)
        os.replace = failing_replace
        try:
            with self.assertRaises(cwl.MaintenanceInProgress):
                cwl.acquire_write_token(root)
        finally:
            os.replace = orig_replace
        self.assertTrue(os.path.isfile(flag_path(root)))
        self.assertEqual(recovery_records(root, "recovered"), [])
        directory = os.path.join(cwl._locks_dir(root), cwl.RECOVERY_DIR)
        if os.path.isdir(directory):
            self.assertEqual(os.listdir(directory), [],
                             "partial temp/record left behind")
        self.assertEqual(snapshot(root), before)
        # After the fault clears, recovery completes normally.
        token = cwl.acquire_write_token(root)
        cwl.release_write_token(root, token)
        self.assertFalse(os.path.isfile(flag_path(root)))
        self.assertEqual(len(recovery_records(root, "recovered")), 1)
        self.assertEqual(recovery_records(root, "recovered")[0][1]["holder"],
                         holder)

    def test_clear_stale_exclusive_contract(self):
        root = queue("rd_", self)
        self.assertFalse(cwl.clear_stale_exclusive(root))  # absent
        raw_flag(root, '"x"')
        self.assertFalse(cwl.clear_stale_exclusive(root))  # malformed
        os.remove(flag_path(root))
        pid, host, proc_start = cwl._self_owner()
        fab_flag(root, pid=pid, host=host, proc_start=proc_start)
        self.assertFalse(cwl.clear_stale_exclusive(root))  # live
        os.remove(flag_path(root))
        fab_flag(root)
        self.assertTrue(cwl.clear_stale_exclusive(root))   # dead -> removed
        self.assertFalse(os.path.isfile(flag_path(root)))
        self.assertEqual(len(recovery_records(root, "recovered")), 1)

    def _fresh_subprocess_clear(self, root):
        code = ("import sys; sys.path.insert(0, r'{tools}');"
                "import clearwright_writer_lock as cwl;"
                "print(cwl.clear_stale_exclusive(r'{root}'))"
                ).format(tools=TOOLS_DIR, root=root)
        return subprocess.run([PY, "-c", code], capture_output=True,
                              text=True, timeout=60)

    def test_restart_determinism_fresh_subprocess(self):
        # Fabricated cases 8 (identity mismatch) and 9 (cross-host).
        for name, over, expect in [("dead", dict(), "True"),
                                   ("cross-host",
                                    dict(host="some-other-host"), "False")]:
            with self.subTest(case=name):
                root = queue("rd_", self)
                fab_flag(root, **over)
                out = self._fresh_subprocess_clear(root)
                self.assertEqual(out.stdout.strip(), expect, out.stderr)
                if expect == "True":
                    self.assertFalse(os.path.isfile(flag_path(root)))
                    self.assertEqual(
                        len(recovery_records(root, "recovered")), 1)
                else:
                    self.assertTrue(os.path.isfile(flag_path(root)))

    def test_restart_determinism_real_killed_holder(self):
        # Packet case 7: a genuinely force-killed real holder re-judged by a
        # FRESH subprocess (PID-gone on POSIX; on win32 the E1 exit-FILETIME
        # branch, evaluated before this harness releases the Popen handle).
        root = queue("rd_", self)
        proc, _stop = start_holder(self, root)
        proc.kill()
        proc.wait(timeout=30)
        self.assertTrue(os.path.isfile(flag_path(root)))
        try:
            out = self._fresh_subprocess_clear(root)
            self.assertEqual(out.stdout.strip(), "True", out.stderr)
            self.assertFalse(os.path.isfile(flag_path(root)))
            self.assertEqual(len(recovery_records(root, "recovered")), 1)
        finally:
            release_child_handle(proc)


# --------------------------------------------------------------------------- #
# Real-process holders: live protection and force-kill recovery
# (packet tests 6, 7, 15)
# --------------------------------------------------------------------------- #

class RealHolderTests(unittest.TestCase):

    def test_active_holder_refused_then_clean_cooperative_release(self):
        root = queue("rh_", self)
        proc, stop = start_holder(self, root)
        after_hold = snapshot(root)
        with open(flag_path(root), "rb") as fh:
            before_bytes = fh.read()
        with self.assertRaises(cwl.MaintenanceInProgress):
            cwl.acquire_write_token(root)
        with open(flag_path(root), "rb") as fh:
            self.assertEqual(fh.read(), before_bytes)
        self.assertEqual(recovery_records(root, "recovered"), [])
        self.assertEqual(snapshot(root), after_hold)
        with open(stop, "w", encoding="utf-8") as fh:
            fh.write("stop")
        self.assertEqual(proc.wait(timeout=30), 0)
        self.assertFalse(os.path.isfile(flag_path(root)))
        self.assertEqual(recovery_records(root, "recovered"), [])
        token = cwl.acquire_write_token(root)
        cwl.release_write_token(root, token)

    def test_force_killed_holder_recovered(self):
        root = queue("rh_", self)
        before = snapshot(root)
        proc, _stop = start_holder(self, root)
        proc.kill()
        proc.wait(timeout=30)
        self.assertTrue(os.path.isfile(flag_path(root)),
                        "kill must strand the flag (finally skipped)")
        if WIN:
            # E1 regression: the harness still HOLDS the Popen handle, so the
            # dead PID stays assigned and pre-E1 liveness read it as live.
            token = cwl.acquire_write_token(root)
            cwl.release_write_token(root, token)
        else:
            release_child_handle(proc)
            token = cwl.acquire_write_token(root)
            cwl.release_write_token(root, token)
        self.assertFalse(os.path.isfile(flag_path(root)))
        recs = recovery_records(root, "recovered")
        self.assertEqual(len(recs), 1)
        self.assertEqual(recs[0][1]["holder"]["pid"], proc.pid)
        release_child_handle(proc)
        self.assertEqual(snapshot(root), before)

    def test_failure_path_cleanup_is_guaranteed(self):
        root = queue("rh_", self)
        proc = None
        cleaned = {"reaped": False}
        try:
            try:
                proc, _stop = start_holder(self, root)
                raise RuntimeError("simulated mid-test failure")
            finally:
                if proc is not None and proc.poll() is None:
                    proc.kill()
                    proc.wait(timeout=30)
                release_child_handle(proc)
                cleaned["reaped"] = True
        except RuntimeError:
            pass
        self.assertTrue(cleaned["reaped"])
        self.assertIsNotNone(proc.returncode)
        # The stranded flag from the killed holder is recoverable, proving the
        # failure path leaves no unrecoverable lock state behind.
        token = cwl.acquire_write_token(root)
        cwl.release_write_token(root, token)
        self.assertFalse(os.path.isfile(flag_path(root)))


# --------------------------------------------------------------------------- #
# D2: region-lock mutex (packet test 19) and timeout classes (test 20)
# --------------------------------------------------------------------------- #

class RegionLockTests(unittest.TestCase):

    def test_two_process_mutual_exclusion_no_interval_overlap(self):
        root = queue("rl_", self)
        base = tempfile.mkdtemp(prefix="ivl_")
        self.addCleanup(shutil.rmtree, base, ignore_errors=True)
        outs = [os.path.join(base, "a.txt"), os.path.join(base, "b.txt")]
        procs = [subprocess.Popen(
            [PY, "-m", "tests.test_gate_lock_hardening", "--registry-interval",
             root, "--out", out, "--iters", "25", "--hold-ms", "4"],
            cwd=REPO_ROOT) for out in outs]
        for p in procs:
            self.assertEqual(p.wait(timeout=120), 0)
        intervals = []
        for out in outs:
            with open(out, encoding="utf-8") as fh:
                for line in fh:
                    t0, t1 = line.strip().split(",")
                    intervals.append((int(t0), int(t1)))
        intervals.sort()
        for (a0, a1), (b0, b1) in zip(intervals, intervals[1:]):
            self.assertLessEqual(a1, b0,
                                 "critical sections overlapped: "
                                 "({}, {}) vs ({}, {})".format(a0, a1, b0, b1))

    def test_force_killed_registry_holder_releases_by_kernel(self):
        root = queue("rl_", self)
        proc, _stop = start_holder(self, root, hold_registry=True)
        proc.kill()
        proc.wait(timeout=30)
        with cwl._RegistryLock(root, "after-kill"):
            pass  # acquiring at all proves the kernel released the region

    def test_deadline_expiry_raises_unavailable_without_mutation(self):
        root = queue("rl_", self)
        proc, stop = start_holder(self, root, hold_registry=True)
        orig = cwl._LOCK_SPIN_MAX_WAIT
        cwl._LOCK_SPIN_MAX_WAIT = 0.4
        self.addCleanup(setattr, cwl, "_LOCK_SPIN_MAX_WAIT", orig)
        with self.assertRaises(cwl.WriterLockError) as ctx:
            with cwl._RegistryLock(root, "contender"):
                pass
        self.assertEqual(str(ctx.exception), "registry_lock_unavailable")
        self.assertFalse(os.path.isfile(flag_path(root)))
        self.assertEqual(recovery_records(root, "recovered"), [])
        with open(stop, "w", encoding="utf-8") as fh:
            fh.write("stop")
        self.assertEqual(proc.wait(timeout=30), 0)

    def test_lockfile_existence_never_blocks_and_fd_not_inheritable(self):
        root = queue("rl_", self)
        with cwl._RegistryLock(root, "first") as lock:
            self.assertFalse(os.get_inheritable(lock._fd))
        lock_file = os.path.join(cwl._locks_dir(root), cwl.REGISTRY_LOCK)
        self.assertTrue(os.path.isfile(lock_file))
        with cwl._RegistryLock(root, "second"):
            pass  # persistent file alone never blocks

    def test_killed_holder_with_inheriting_grandchild_releases(self):
        # Packet 19e (reworked per review): the discriminating scenario is a
        # holder force-killed WITHOUT unlock while a grandchild holds a
        # genuinely INHERITED descriptor (close_fds=False). On POSIX (CI) an
        # inherited flock could prolong the lock if the fd were inheritable;
        # PEP-446 non-inheritability plus kernel release-on-death must let a
        # fresh process acquire while the grandchild still runs. The holder
        # runs as a __main__ mode (no embedded paths -> no escaping hazards).
        root = queue("rl_", self)
        base = tempfile.mkdtemp(prefix="inh_")
        self.addCleanup(shutil.rmtree, base, ignore_errors=True)
        ready = os.path.join(base, "ready")
        holder = subprocess.Popen(
            [PY, "-m", "tests.test_gate_lock_hardening",
             "--hold-registry-inherit", root, "--ready-file", ready],
            cwd=REPO_ROOT)
        try:
            deadline = time.monotonic() + 30
            while not os.path.isfile(ready):
                if holder.poll() is not None:
                    self.fail("holder exited before ready")
                if time.monotonic() > deadline:
                    self.fail("holder never became ready")
                time.sleep(0.05)
            holder.kill()
            holder.wait(timeout=30)
            # A grandchild spawned with close_fds=False still runs and may hold
            # an inherited copy of the lock fd; a fresh process must still
            # acquire within the spin deadline.
            out = self._fresh_registry_acquire(root)
            self.assertEqual(out.stdout.strip(), "acquired", out.stderr)
        finally:
            if holder.poll() is None:
                holder.kill()
                holder.wait(timeout=30)

    def _fresh_registry_acquire(self, root):
        return subprocess.run(
            [PY, "-m", "tests.test_gate_lock_hardening",
             "--try-registry", root],
            capture_output=True, text=True, timeout=30, cwd=REPO_ROOT)

    def test_registry_lock_init_race_is_controlled(self):
        # F6/F10: two processes initializing a fresh registry.lock concurrently
        # must never let a raw OSError escape __enter__ (the byte-0 init write
        # can hit a lock violation on Windows). Stress many rounds of two
        # concurrent first-acquisitions on fresh roots and assert every failure
        # is the deterministic WriterLockError, never a bare OSError.
        base = tempfile.mkdtemp(prefix="race_")
        self.addCleanup(shutil.rmtree, base, ignore_errors=True)
        out = os.path.join(base, "errs.txt")
        procs = [subprocess.Popen(
            [PY, "-m", "tests.test_gate_lock_hardening", "--init-race",
             base, "--out", out + ".{}".format(i), "--rounds", "40"],
            cwd=REPO_ROOT) for i in range(2)]
        for p in procs:
            self.assertEqual(p.wait(timeout=120), 0)
        for i in range(2):
            with open(out + ".{}".format(i), encoding="utf-8") as fh:
                for line in fh:
                    line = line.strip()
                    if line:
                        self.assertIn(line, ("ok", "registry_lock_unavailable"),
                                      "uncontrolled error: " + line)

    def test_drain_timeout_leaves_no_flag_and_no_records(self):
        root = queue("rl_", self)
        token = cwl.acquire_write_token(root)
        try:
            with self.assertRaises(cwl.WriterLockError) as ctx:
                cwl.acquire_exclusive(root, "op-timeout", deadline_seconds=0.3)
            self.assertEqual(str(ctx.exception), "archive_drain_timeout")
            self.assertFalse(os.path.isfile(flag_path(root)))
            self.assertEqual(recovery_records(root, "recovered"), [])
            self.assertEqual(recovery_records(root, "refusal"), [])
        finally:
            cwl.release_write_token(root, token)


# --------------------------------------------------------------------------- #
# D3 case 5: interrupted archive operations (packet tests 21, 21b, 21c)
# --------------------------------------------------------------------------- #

class InterruptedOperationTests(unittest.TestCase):

    def make_pending(self, root, opid="op-int", corrupt=False):
        mdir = cwa.month_dir(root)
        os.makedirs(mdir, exist_ok=True)
        src = os.path.join(root, "communications", "msg-pending-src.json")
        os.makedirs(os.path.dirname(src), exist_ok=True)
        body = b'{"message_id": "msg-pending-src"}\n'
        with open(src, "wb") as fh:
            fh.write(body)
        digest = hashlib.sha256(body).hexdigest()
        dst = os.path.join(mdir, "communications", "msg-pending-src.json")
        plan = {"entries": [{"id": "msg-pending-src", "type": "message",
                             "src": src, "dst": dst, "sha256": digest}]}
        cwa.write_journal(mdir, opid, plan, "e" * 64)
        pending = os.path.join(mdir, "pending-{}.json".format(opid))
        self.assertTrue(os.path.isfile(pending))
        if corrupt:
            with open(pending, "w", encoding="utf-8") as fh:
                fh.write("{corrupt")
        return pending, src, dst

    def test_recoverable_journal_end_to_end_escape(self):
        root = queue("io_", self)
        pending, src, dst = self.make_pending(root)
        holder = fab_flag(root, opid="op-int", pending_path=pending)
        self.assertTrue(os.path.isabs(holder["pending_path"]))
        with self.assertRaises(cwl.MaintenanceInProgress):
            cwl.acquire_write_token(root)
        self.assertTrue(os.path.isfile(flag_path(root)))
        refusals = recovery_records(root, "refusal")
        self.assertEqual(len(refusals), 1)
        self.assertEqual(refusals[0][1]["state"], "interrupted_operation")
        assert_record_schema(self, refusals[0][1], holder, False)
        results = cwa.recover_pending(root)
        self.assertEqual([r["status"] for r in results], ["completed"])
        self.assertFalse(os.path.isfile(pending))
        self.assertTrue(os.path.isfile(dst))
        self.assertFalse(os.path.isfile(src))
        token = cwl.acquire_write_token(root)
        cwl.release_write_token(root, token)
        self.assertFalse(os.path.isfile(flag_path(root)))
        self.assertEqual(len(recovery_records(root, "recovered")), 1)
        self.assertEqual(len(recovery_records(root, "refusal")), 1)

    def test_unrecoverable_journal_retained_fail_closed(self):
        root = queue("io_", self)
        pending, _src, _dst = self.make_pending(root, corrupt=True)
        fab_flag(root, opid="op-int", pending_path=pending)
        with self.assertRaises(cwl.MaintenanceInProgress):
            cwl.acquire_write_token(root)
        results = cwa.recover_pending(root)
        self.assertEqual(results, [])  # unreadable journal skipped, untouched
        self.assertTrue(os.path.isfile(pending))
        with self.assertRaises(cwl.MaintenanceInProgress):
            cwl.acquire_write_token(root)
        self.assertTrue(os.path.isfile(flag_path(root)))
        self.assertEqual(len(recovery_records(root, "refusal")), 1)
        self.assertEqual(recovery_records(root, "recovered"), [])
        # Resolution routes from here are archive-side: repair/complete the
        # journal per docs/ARCHIVE_OPERATION.md, or the pre-existing audited
        # operator override (force_clear_exclusive_for_override) -- untouched
        # by this change.

    def test_legacy_flag_with_real_pending_journal_retained(self):
        # First-deploy: a pre-upgrade exclusive flag has NO pending_path key,
        # but a dead old-code archiver may have left a REAL pending journal.
        # The archive tree is the source of truth: recovery must retain
        # fail-closed (interrupted_operation), not auto-clear.
        root = queue("io_", self)
        pending, _src, _dst = self.make_pending(root, opid="op-legacy")
        holder = fab_flag(root)  # legacy shape: no pending_path key
        self.assertNotIn("pending_path", holder)
        with self.assertRaises(cwl.MaintenanceInProgress):
            cwl.acquire_write_token(root)
        self.assertTrue(os.path.isfile(flag_path(root)))
        refusals = recovery_records(root, "refusal")
        self.assertEqual(len(refusals), 1)
        self.assertEqual(refusals[0][1]["state"], "interrupted_operation")
        self.assertEqual(recovery_records(root, "recovered"), [])
        # Once the archive journal is resolved, the legacy flag auto-recovers.
        self.assertEqual([r["status"] for r in cwa.recover_pending(root)],
                         ["completed"])
        self.assertFalse(os.path.isfile(pending))
        token = cwl.acquire_write_token(root)
        cwl.release_write_token(root, token)
        self.assertFalse(os.path.isfile(flag_path(root)))
        self.assertEqual(len(recovery_records(root, "recovered")), 1)

    def test_clear_stale_exclusive_returns_false_for_interrupted_operation(self):
        root = queue("io_", self)
        pending, _src, _dst = self.make_pending(root)
        holder = fab_flag(root, opid="op-int", pending_path=pending)
        self.assertFalse(cwl.clear_stale_exclusive(root))
        self.assertTrue(os.path.isfile(flag_path(root)))
        refusals = recovery_records(root, "refusal")
        self.assertEqual(len(refusals), 1)
        self.assertEqual(refusals[0][1]["state"], "interrupted_operation")
        assert_record_schema(self, refusals[0][1], holder, False)
        self.assertEqual(recovery_records(root, "recovered"), [])
        # Idempotent: a second call neither clears nor duplicates the record.
        self.assertFalse(cwl.clear_stale_exclusive(root))
        self.assertEqual(len(recovery_records(root, "refusal")), 1)

    def test_concurrent_recover_pending_is_safe(self):
        # Contract (amendment L-A3): concurrent recover_pending callers never
        # corrupt or duplicate records and at least one completes; the LOSING
        # racer either also completes (idempotent), halts safely via
        # ArchiveError, or -- on Windows, where an open handle can block the
        # winner's rename/verify window -- raises a plain OSError. Concurrency
        # pre-exists this change (execute() has always called recover_pending
        # unconditionally); hardening the loser to a recorded halt is archive-
        # side work outside this item's authorized surface.
        root = queue("io_", self)
        pending, src, dst = self.make_pending(root)
        results = [None, None]
        errors = [None, None]

        def call(i):
            try:
                results[i] = cwa.recover_pending(root)
            except BaseException as exc:  # noqa: BLE001 - contract check below
                errors[i] = exc
        threads = [threading.Thread(target=call, args=(i,)) for i in (0, 1)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=60)
        for t in threads:
            self.assertFalse(t.is_alive(), "recover_pending worker hung")
        # The committed contract: any escaped exception is ArchiveError/OSError.
        for exc in errors:
            self.assertTrue(exc is None or isinstance(exc, (cwa.ArchiveError,
                                                            OSError)), repr(exc))
        self.assertFalse(os.path.isfile(pending))
        self.assertTrue(os.path.isfile(dst))
        self.assertFalse(os.path.isfile(src))
        with open(dst, "rb") as fh:
            self.assertEqual(hashlib.sha256(fh.read()).hexdigest(),
                             hashlib.sha256(
                                 b'{"message_id": "msg-pending-src"}\n'
                             ).hexdigest())
        # Every returned result is for the single journal, terminal status.
        for res in results:
            for r in (res or []):
                self.assertEqual(r["opid"], "op-int")
                self.assertIn(r["status"], ("completed", "halted"))
        statuses = [r["status"] for res in results if res for r in res]
        self.assertIn("completed", statuses)


# --------------------------------------------------------------------------- #
# Entry paths inherit recovery (packet tests 13, 14, 18) and payload
# preservation (test 16)
# --------------------------------------------------------------------------- #

class EntryPathTests(unittest.TestCase):

    def seed_bystander(self, root):
        """A second unrelated work item WITH prior council + gate records, per
        test 18's entry-path preservation rule. Returns the byte-snapshot of
        exactly those records."""
        other_wid, other_thread = make_item(root, "Unrelated bystander item.")
        c = fabricate_capped_council(root, other_thread, other_wid)
        cwg.record_escalation_gate(root, c["council_id"],
                                   load_outcome(root, c["council_id"]),
                                   {"work_item_id": other_wid,
                                    "thread_id": other_thread})
        key = other_wid.split(":", 1)[1]
        cid = c["council_id"]
        return {p: h for p, h in snapshot(root).items()
                if key in p or cid in p or "gate" in p.lower()}

    def assert_bystander_intact(self, root, bystander):
        after = snapshot(root)
        for p, h in bystander.items():
            self.assertEqual(after.get(p), h,
                             "bystander record changed: {}".format(p))

    def test_library_claim_recovers_and_proceeds(self):
        root = queue("ep_", self)
        wid, _thread = make_item(root)
        bystander = self.seed_bystander(root)
        self.assertTrue(bystander)
        fab_flag(root)
        res = cww.claim_work_item(root, wid, "claude")
        self.assertTrue(res.get("ok"), res)
        self.assertNotEqual(res.get("error"), "maintenance_in_progress")
        self.assertEqual(len(recovery_records(root, "recovered")), 1)
        self.assert_bystander_intact(root, bystander)

    def test_wrapper_progress_recovers_and_proceeds(self):
        root = queue("ep_", self)
        wid, _thread = make_item(root)
        self.assertTrue(cww.claim_work_item(root, wid, "claude").get("ok"))
        bystander = self.seed_bystander(root)
        fab_flag(root)
        res, code = run(ucw.cmd_progress, queue_root=root, work_item_id=wid,
                        message="progress after recovery", message_file=None)
        self.assertEqual(code, ucw.EXIT_OK, res)
        self.assertEqual(len(recovery_records(root, "recovered")), 1)
        self.assert_bystander_intact(root, bystander)

    def test_payload_preservation_per_field_both_commands_both_errors(self):
        root = queue("ep_", self)
        wid, _thread = make_item(root)
        self.assertTrue(cww.claim_work_item(root, wid, "claude").get("ok"))
        payloads = {
            "gate_creation_failed": {
                "ok": False, "error": "gate_creation_failed",
                "error_code": "gate_creation_failed",
                "error_class": "governance_integrity",
                "invariant": "round_records_unreadable",
                "council_id": "cw-council-x", "work_item_id": wid,
                "phase": "plan", "outcome": "operator_required",
                "detail": "recorded rounds missing/unreadable: [1]"},
            "maintenance_in_progress": {
                "ok": False, "error": "maintenance_in_progress",
                "error_code": "maintenance_in_progress"},
        }
        commands = {
            "progress": (ucw.cmd_progress, "progress_work_item",
                         dict(queue_root=root, work_item_id=wid,
                              message="m", message_file=None)),
            "complete": (ucw.cmd_complete, "respond_work_item",
                         dict(queue_root=root, work_item_id=wid, result="r",
                              result_file=None, packet_id=None)),
        }
        for err, payload in payloads.items():
            for cname, (cmd, target, kwargs) in commands.items():
                with self.subTest(command=cname, error=err):
                    orig = getattr(cww, target)
                    setattr(cww, target, lambda *a, **k: dict(payload))
                    try:
                        res, code = run(cmd, **kwargs)
                    finally:
                        setattr(cww, target, orig)
                    self.assertEqual(code, ucw.EXIT_RUNTIME)
                    for key, value in payload.items():
                        self.assertEqual(res.get(key), value,
                                         "field {!r} not preserved".format(key))
                    self.assertEqual(res.get("command"), cname)


class HttpEntryPathTests(unittest.TestCase):
    """Real HTTP per the HttpFramingTests pattern; module globals restored in
    tearDownClass (the existing precedent omits the restore -- not copied)."""

    @classmethod
    def setUpClass(cls):
        cls._saved = (server.QUEUE_ROOT, server.DURABLE, server.MODE)
        cls._base = tempfile.mkdtemp(prefix="http_")
        root, durable, mode, _ = server.resolve_queue(cls._base)
        server.QUEUE_ROOT, server.DURABLE, server.MODE = root, durable, mode
        cls.root = root
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        cls.port = cls.httpd.server_address[1]
        cls.thread = threading.Thread(target=cls.httpd.serve_forever,
                                      daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()
        cls.thread.join(timeout=10)
        server.QUEUE_ROOT, server.DURABLE, server.MODE = cls._saved
        shutil.rmtree(cls._base, ignore_errors=True)

    def post(self, path, body):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=30)
        try:
            conn.request("POST", path, json.dumps(body),
                         {"Content-Type": "application/json"})
            resp = conn.getresponse()
            return resp.status, json.loads(resp.read().decode("utf-8"))
        finally:
            conn.close()

    def test_control_plane_claim_recovers_instead_of_503(self):
        wid, wthread = make_item(self.root)
        # Bystander unrelated item with prior council/gate records (test 18).
        other_wid, other_thread = make_item(self.root, "HTTP bystander item.")
        c = fabricate_capped_council(self.root, other_thread, other_wid)
        cwg.record_escalation_gate(self.root, c["council_id"],
                                   load_outcome(self.root, c["council_id"]),
                                   {"work_item_id": other_wid,
                                    "thread_id": other_thread})
        key = other_wid.split(":", 1)[1]
        bystander = {p: h for p, h in snapshot(self.root).items()
                     if key in p or c["council_id"] in p}
        self.assertTrue(bystander)
        before_recovered = len(recovery_records(self.root, "recovered"))
        fab_flag(self.root)
        status, body = self.post("/api/work-items/claim",
                                 {"work_item_id": wid, "actor": "claude"})
        self.assertNotEqual(status, 503, body)
        self.assertNotEqual(body.get("error"), "maintenance_in_progress")
        self.assertEqual(len(recovery_records(self.root, "recovered")),
                         before_recovered + 1)
        after = snapshot(self.root)
        for p, h in bystander.items():
            self.assertEqual(after.get(p), h,
                             "bystander record changed: {}".format(p))

    def test_control_plane_503_for_live_holder_unchanged(self):
        wid, _thread = make_item(self.root)
        pid, host, proc_start = cwl._self_owner()
        fab_flag(self.root, pid=pid, host=host, proc_start=proc_start)
        try:
            status, body = self.post("/api/work-items/claim",
                                     {"work_item_id": wid, "actor": "claude"})
            self.assertEqual(status, 503)
            self.assertEqual(body.get("error"), "maintenance_in_progress")
        finally:
            os.remove(flag_path(self.root))


# --------------------------------------------------------------------------- #
# Existing behavior unchanged (packet test 17)
# --------------------------------------------------------------------------- #

class ExistingBehaviorTests(unittest.TestCase):

    def test_capped_council_gate_flow_and_grant_proceed_unchanged(self):
        root = queue("eb_", self)
        wid, thread = make_item(root)
        c = fabricate_capped_council(root, thread, wid)
        res = cwg.record_escalation_gate(root, c["council_id"],
                                         load_outcome(root, c["council_id"]),
                                         {"work_item_id": wid,
                                          "thread_id": thread})
        self.assertTrue(res.get("ok"), res)
        gates = cwg.load_gates(root, wid)
        self.assertEqual(len(gates), 1)
        self.assertEqual(gates[0]["disposition"], "unresolved")
        auth = cwm.build_message(
            "OPERATOR-0001",
            "I authorize proceeding on {} for council {}.".format(
                wid, c["council_id"]),
            role="operator", thread_id=thread, direction="inbound",
            status="posted", source="operator-console", work_item_id=wid)
        cwm.write_message(root, auth)
        res, code = run(ucw.cmd_grant_proceed, queue_root=root,
                        work_item_id=wid,
                        operator_message_id=auth["message_id"])
        self.assertEqual(code, ucw.EXIT_OK, res)
        self.assertEqual(cwg.load_gates(root, wid)[0]["disposition"],
                         "resolved")


# --------------------------------------------------------------------------- #
# __main__ helpers: cooperative holders and the live-verification fixture
# --------------------------------------------------------------------------- #

def _hold(root, ready_file, stop_file, hold_registry):
    if hold_registry:
        lock = cwl._RegistryLock(root, "hold-registry")
        lock.__enter__()
        try:
            with open(ready_file, "w", encoding="utf-8") as fh:
                fh.write("ready")
            while not os.path.isfile(stop_file):
                time.sleep(0.1)
        finally:
            lock.__exit__(None, None, None)
        return 0
    flag = cwl.acquire_exclusive(root, "hold-exclusive", deadline_seconds=30)
    try:
        with open(ready_file, "w", encoding="utf-8") as fh:
            fh.write("ready")
        while not os.path.isfile(stop_file):
            time.sleep(0.1)
    finally:
        cwl.release_exclusive(root, "hold-exclusive", flag["nonce"])
    return 0


def _hold_registry_inherit(root, ready_file):
    # Acquire the registry lock, spawn a lingering grandchild that INHERITS
    # open descriptors (close_fds=False), signal ready, then sleep to be
    # force-killed WITHOUT unlocking (no finally). No embedded paths in the
    # grandchild command -> no cross-platform escaping hazards.
    lock = cwl._RegistryLock(root, "hold-inherit")
    lock.__enter__()
    subprocess.Popen([PY, "-c", "import time; time.sleep(60)"],
                     close_fds=False)
    with open(ready_file, "w", encoding="utf-8") as fh:
        fh.write("ready")
    time.sleep(60)
    return 0


def _try_registry(root):
    with cwl._RegistryLock(root, "try"):
        pass
    print("acquired")
    return 0


def _registry_interval(root, out, iters, hold_ms):
    lines = []
    for _ in range(iters):
        with cwl._RegistryLock(root, "interval"):
            t0 = time.time_ns()
            time.sleep(hold_ms / 1000.0)
            t1 = time.time_ns()
        lines.append("{},{}".format(t0, t1))
    with open(out, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")
    return 0


def _init_race(base, out, rounds):
    # Repeatedly first-acquire a FRESH registry.lock concurrently with a peer;
    # record "ok" or the deterministic failure code, never a raw traceback.
    results = []
    for i in range(rounds):
        root = os.path.join(base, "r{}".format(i))
        os.makedirs(os.path.join(root, "locks"), exist_ok=True)
        try:
            with cwl._RegistryLock(root, "race"):
                pass
            results.append("ok")
        except cwl.WriterLockError as exc:
            results.append(str(exc))
        except Exception as exc:  # noqa: BLE001 - the point is to catch leaks
            results.append("UNCONTROLLED:" + type(exc).__name__)
    with open(out, "w", encoding="utf-8") as fh:
        fh.write("\n".join(results) + "\n")
    return 0


def _live_fixture(root):
    wid, thread = make_item(root, "Live lock-recovery verification item.")
    print(json.dumps({"work_item_id": wid, "thread_id": thread}))
    return 0


def _liveness_check(root):
    rec = None
    path = flag_path(root)
    if os.path.isfile(path):
        with open(path, encoding="utf-8") as fh:
            rec = json.load(fh)
    if not rec:
        print(json.dumps({"flag": False}))
        return 0
    state = cwl.liveness(rec.get("pid"), rec.get("host"),
                         rec.get("proc_start"))
    print(json.dumps({"flag": True, "pid": rec.get("pid"),
                      "liveness": state}))
    return 0


def _main(argv):
    def val(name, default=None):
        return argv[argv.index(name) + 1] if name in argv else default
    if "--hold-exclusive" in argv or "--hold-registry" in argv:
        registry = "--hold-registry" in argv
        root = val("--hold-registry" if registry else "--hold-exclusive")
        return _hold(root, val("--ready-file"), val("--stop-file"), registry)
    if "--registry-interval" in argv:
        return _registry_interval(val("--registry-interval"), val("--out"),
                                  int(val("--iters", "10")),
                                  int(val("--hold-ms", "2")))
    if "--hold-registry-inherit" in argv:
        return _hold_registry_inherit(val("--hold-registry-inherit"),
                                      val("--ready-file"))
    if "--try-registry" in argv:
        return _try_registry(val("--try-registry"))
    if "--init-race" in argv:
        return _init_race(val("--init-race"), val("--out"),
                          int(val("--rounds", "40")))
    if "--live-fixture" in argv:
        return _live_fixture(val("--live-fixture"))
    if "--liveness-check" in argv:
        return _liveness_check(val("--liveness-check"))
    unittest.main(module="tests.test_gate_lock_hardening")
    return 0


if __name__ == "__main__":
    sys.exit(_main(sys.argv[1:]))
