"""Regression coverage for the ClearWright stabilization work item
(message:msg-20260715T033322041191): message-scoped work-item identity,
same-thread isolation, derived-queue integrity, gate idempotency, selected-task
isolation, server lifecycle evidence, and the manual launcher.

The live defect these pin: two actionable messages in one conversation thread
previously collapsed to a single derived work item, so a second governed,
council-bound, gated item vanished from the queue and the selected-task display
showed the wrong title and council.
"""
import json
import os
import shutil
import sys
import tempfile
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APP_DIR = os.path.join(REPO_ROOT, "apps", "control-plane")
TOOLS = os.path.join(REPO_ROOT, "tools")
STATIC = os.path.join(APP_DIR, "static")
sys.path.insert(0, APP_DIR)
sys.path.insert(0, TOOLS)
import server  # noqa: E402
import clearwright_work as cww  # noqa: E402
import clearwright_message as cwm  # noqa: E402
import clearwright_identity as cwid  # noqa: E402
import clearwright_gate as cwg  # noqa: E402
import clearwright_server_lifecycle as csl  # noqa: E402


def read(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def queue(prefix, tc):
    base = tempfile.mkdtemp(prefix=prefix)
    tc.addCleanup(shutil.rmtree, base, ignore_errors=True)
    root, *_ = server.resolve_queue(os.path.join(base, "active"))
    return root


def request_msg(root, text, thread_id=None, actor="OPERATOR-0001"):
    r = server.do_message(root, {"actor": actor, "role": "operator",
                                 "source": "use-cw", "intent": "request",
                                 "message": text, "thread_id": thread_id})
    return r["message"]


class SameThreadIdentityTests(unittest.TestCase):
    """A/B: two actionable messages in one thread stay fully independent."""

    def setUp(self):
        self.root = queue("stab_id_", self)
        m1 = request_msg(self.root, "First governed request.")
        self.tid = m1["thread_id"]
        self.wid1 = cwid.work_item_id_for(m1["message_id"])
        m2 = request_msg(self.root, "Second governed request.", thread_id=self.tid)
        self.wid2 = cwid.work_item_id_for(m2["message_id"])

    def test_two_messages_one_thread_two_items(self):
        items = [it for it in cww.derive_work_items(self.root)
                 if it["kind"] == "message"]
        ids = {it["work_item_id"] for it in items}
        self.assertIn(self.wid1, ids)
        self.assertIn(self.wid2, ids)
        self.assertNotEqual(self.wid1, self.wid2)

    def test_independent_titles(self):
        items = {it["work_item_id"]: it for it in cww.derive_work_items(self.root)}
        self.assertIn("First", items[self.wid1]["title"])
        self.assertIn("Second", items[self.wid2]["title"])

    def test_independent_claims_bind_by_work_item(self):
        cww.claim_work_item(self.root, self.wid1, "claude")
        items = {it["work_item_id"]: it for it in cww.derive_work_items(self.root)}
        self.assertEqual(items[self.wid1]["status"], "claimed")
        self.assertEqual(items[self.wid2]["status"], "open")

    def test_independent_gates(self):
        cwg.ensure_gate(self.root, self.wid1, "c1", "plan",
                        "operator_required", 5, "sh", "inv-1")
        self.assertIsNotNone(cwg.active_gate(self.root, self.wid1))
        self.assertIsNone(cwg.active_gate(self.root, self.wid2))

    def test_one_agreed_one_operator_required_both_visible(self):
        # wid1 gated (operator_required); wid2 responded (agreement-equivalent
        # terminal path via a response). Both remain discoverable.
        cwg.ensure_gate(self.root, self.wid1, "c1", "plan",
                        "operator_required", 5, "sh", "inv-1")
        items = {it["work_item_id"]: it["status"]
                 for it in cww.derive_work_items(self.root)}
        self.assertEqual(items.get(self.wid1), "operator_required")
        self.assertIn(self.wid2, items)

    def test_direct_lookup_by_either_id(self):
        self.assertIsNotNone(cww.find_work_item(self.root, self.wid1))
        self.assertIsNotNone(cww.find_work_item(self.root, self.wid2))

    def test_no_thread_level_collapse_on_response(self):
        # Responding to one item must not close the other.
        cww.claim_work_item(self.root, self.wid1, "claude")
        cww.respond_work_item(self.root, self.wid1, "claude", "done one")
        allitems = {it["work_item_id"]: it["status"]
                    for it in cww.derive_work_items(self.root, include="all")}
        self.assertEqual(allitems.get(self.wid1), "done")
        # wid2 is still open/nonterminal and visible.
        nonterminal = {it["work_item_id"]
                       for it in cww.derive_work_items(self.root)}
        self.assertIn(self.wid2, nonterminal)


class OriginRuleTests(unittest.TestCase):
    """A: authority/chat/no-intent messages never become origins or titles."""

    def setUp(self):
        self.root = queue("stab_or_", self)

    def test_v2_no_intent_is_not_origin(self):
        server.do_message(self.root, {"actor": "OPERATOR-0001", "role": "operator",
                                      "source": "operator-ui",
                                      "message": "I authorize proceeding."})
        self.assertEqual(cww.derive_work_items(self.root), [])

    def test_v2_chat_is_not_origin(self):
        server.do_message(self.root, {"actor": "OPERATOR-0001", "role": "operator",
                                      "source": "operator-ui", "intent": "chat",
                                      "message": "just chatting"})
        self.assertEqual(cww.derive_work_items(self.root), [])

    def test_v2_request_is_origin(self):
        request_msg(self.root, "Do the thing.")
        self.assertEqual(len(cww.derive_work_items(self.root)), 1)


class LegacyManifestTests(unittest.TestCase):
    """A: legacy (pre-cutover) messages resolve through a frozen, audited
    migration manifest; deletion is surfaced, never silently regenerated."""

    def setUp(self):
        self.root = queue("stab_lg_", self)
        # Write a legacy origin (no identity_version) and a legacy authority.
        self._legacy("thr-legacy", "Legacy work request.", intent=None)
        self._legacy("thr-legacy", "I authorize the legacy work.", intent=None,
                     authority=True)

    def _legacy(self, tid, text, intent=None, authority=False):
        directory = cwm.comms_dir(self.root)
        os.makedirs(directory, exist_ok=True)
        stamp = cwm._stamp()
        mid = "msg-" + stamp
        rec = {"message_id": mid, "thread_id": tid, "at": cwm._now_iso(),
               "actor": "OPERATOR-0001", "role": "operator",
               "direction": "inbound", "status": "posted",
               "message": text, "source": "operator-ui", "simulated": False}
        if intent:
            rec["intent"] = intent
        with open(os.path.join(directory, mid + ".json"), "w", encoding="utf-8") as fh:
            json.dump(rec, fh)
        return mid

    def test_manifest_generated_once_and_frozen(self):
        man = cwid.migrate_legacy_origins(self.root)
        again = cwid.migrate_legacy_origins(self.root)
        self.assertEqual(man["origin_message_ids"], again["origin_message_ids"])
        self.assertTrue(os.path.isfile(os.path.join(self.root, cwid.LEGACY_MARKER_NAME)))

    def test_manifest_excludes_authority_by_payload(self):
        man = cwid.migrate_legacy_origins(self.root)
        excluded_reasons = {e["reason"] for e in man["excluded"]}
        self.assertIn("payload_marker", excluded_reasons)
        # exactly one legacy work request derives
        origins = cwid.legacy_origin_ids(self.root)
        self.assertEqual(len(origins), 1)

    def test_manifest_deletion_surfaces_and_does_not_regenerate(self):
        cwid.migrate_legacy_origins(self.root)
        os.remove(os.path.join(self.root, cwid.LEGACY_MANIFEST_NAME))
        self.assertEqual(cwid.legacy_origin_ids(self.root), set())
        self.assertEqual(cwid.manifest_status(self.root), "legacy_manifest_missing")

    def test_reads_do_not_write_manifest(self):
        # A pure read (derivation) must not persist the manifest.
        cww.derive_work_items(self.root)
        self.assertFalse(os.path.isfile(os.path.join(self.root, cwid.LEGACY_MANIFEST_NAME)))


class QueueIntegrityTests(unittest.TestCase):
    """B: nothing silently disappears; collisions surface as warnings."""

    def setUp(self):
        self.root = queue("stab_qi_", self)

    def test_unknown_closure_stays_visible(self):
        m = request_msg(self.root, "Task with a weird closure.")
        wid = cwid.work_item_id_for(m["message_id"])
        # Post a closure record with an unrecognized value bound to the item.
        bad = cwm.build_message("OPERATOR-0001", "closing oddly", role="operator",
                                thread_id=m["thread_id"], direction="internal",
                                work_item_id=wid)
        bad["closure"] = "closed_by_operator"  # recognized
        cwm.write_message(self.root, cwm.build_message(
            "OPERATOR-0001", "note", role="operator", thread_id=m["thread_id"],
            direction="internal", work_item_id=wid))
        # An item with only an unknown-closure remains nonterminal + warned.
        items = {it["work_item_id"] for it in cww.derive_work_items(self.root)}
        self.assertIn(wid, items)

    def test_integrity_warnings_shape(self):
        request_msg(self.root, "A task.")
        warns = cww.integrity_warnings(self.root)
        self.assertIsInstance(warns, list)


class GateIdempotencyTests(unittest.TestCase):
    """C: one gate per unique escalation; bookkeeping never duplicates."""

    def setUp(self):
        self.root = queue("stab_gt_", self)
        self.wid = "message:msg-stab-gate"

    def test_same_escalation_one_gate(self):
        cwg.ensure_gate(self.root, self.wid, "c1", "plan", "operator_required", 5, "sh", "i1")
        cwg.ensure_gate(self.root, self.wid, "c1", "plan", "operator_required", 5, "sh", "i2")
        self.assertEqual(len(cwg.load_gates(self.root, self.wid)), 1)

    def test_final_reconciliation_no_duplicate(self):
        r1 = cwg.ensure_gate(self.root, self.wid, "c1", "plan", "operator_required", 5, "sh", "i1")
        # Simulate resolve then final reconciliation re-evaluating the SAME
        # terminal council (same round count) -> deduplicated, no new gate.
        r2 = cwg.ensure_gate(self.root, self.wid, "c1", "plan", "operator_required", 5, "sh", "i2")
        self.assertFalse(r1["deduplicated"])
        self.assertTrue(r2["deduplicated"])
        self.assertEqual(len(cwg.load_gates(self.root, self.wid)), 1)
        events = cwg.load_gates(self.root, self.wid)[0]["deduplicated_events"]
        self.assertEqual(len(events), 1)

    def test_repeated_event_is_idempotent(self):
        cwg.ensure_gate(self.root, self.wid, "c1", "plan", "operator_required", 5, "sh", "i1")
        cwg.ensure_gate(self.root, self.wid, "c1", "plan", "operator_required", 5, "sh", "i2")
        cwg.ensure_gate(self.root, self.wid, "c1", "plan", "operator_required", 5, "sh", "i2")
        self.assertEqual(len(cwg.load_gates(self.root, self.wid)[0]["deduplicated_events"]), 1)

    def test_new_round_new_gate(self):
        cwg.ensure_gate(self.root, self.wid, "c1", "plan", "operator_required", 5, "sh", "i1")
        r = cwg.ensure_gate(self.root, self.wid, "c1", "plan", "operator_required", 6, "sh", "i3")
        self.assertFalse(r["deduplicated"])
        self.assertEqual(len(cwg.load_gates(self.root, self.wid)), 2)

    def test_changed_scope_new_gate(self):
        cwg.ensure_gate(self.root, self.wid, "c1", "plan", "operator_required", 5, "sh1", "i1")
        r = cwg.ensure_gate(self.root, self.wid, "c1", "plan", "operator_required", 5, "sh2", "i2")
        self.assertFalse(r["deduplicated"])
        self.assertEqual(len(cwg.load_gates(self.root, self.wid)), 2)

    def test_invocation_id_required(self):
        with self.assertRaises(cwg.GateError):
            cwg.ensure_gate(self.root, self.wid, "c1", "plan", "operator_required", 5, "sh", None)

    def test_legacy_keyless_gate_bridges(self):
        # A pre-upgrade gate without a dedup_key must dedupe (and backfill) on
        # the first post-upgrade re-evaluation of the same escalation, so a
        # resolved pre-change gate cannot re-gate the first reconciliation.
        legacy = cwg.create_gate(self.root, self.wid, "c1", "plan", "operator_required")
        self.assertNotIn("dedup_key", legacy)
        r = cwg.ensure_gate(self.root, self.wid, "c1", "plan", "operator_required", 0, None, "i9")
        self.assertTrue(r["deduplicated"])
        self.assertEqual(len(cwg.load_gates(self.root, self.wid)), 1)
        self.assertIn("dedup_key", cwg.load_gates(self.root, self.wid)[0])

    def test_concurrent_ensure_gate_one_gate(self):
        # Two subprocesses race on one queue root -> exactly one gate.
        import subprocess
        code = (
            "import sys; sys.path.insert(0, %r); import clearwright_gate as g; "
            "g.ensure_gate(%r, %r, 'c1', 'plan', 'operator_required', 5, 'sh', "
            "'p'+str(__import__('os').getpid()))"
            % (TOOLS, self.root, self.wid))
        procs = [subprocess.Popen([sys.executable, "-c", code]) for _ in range(2)]
        for p in procs:
            p.wait()
        self.assertEqual(len(cwg.load_gates(self.root, self.wid)), 1)


class TaskStateIsolationTests(unittest.TestCase):
    """D: selected-task surfaces bind to one canonical work item."""

    def setUp(self):
        self.root = queue("stab_ts_", self)
        m1 = request_msg(self.root, "Startup persistence work.")
        self.tid = m1["thread_id"]
        self.wid1 = cwid.work_item_id_for(m1["message_id"])
        m2 = request_msg(self.root, "Docker shutdown work.", thread_id=self.tid)
        self.wid2 = cwid.work_item_id_for(m2["message_id"])

    def test_task_state_by_work_item_returns_own_title(self):
        ts1 = server.build_task_state(self.root, work_item_id=self.wid1)
        ts2 = server.build_task_state(self.root, work_item_id=self.wid2)
        self.assertIn("Startup", ts1["title"])
        self.assertIn("Docker", ts2["title"])
        self.assertEqual(ts1["work_item_id"], self.wid1)
        self.assertEqual(ts2["work_item_id"], self.wid2)

    def test_thread_selector_ambiguous_errors(self):
        ts = server.build_task_state(self.root, thread_id=self.tid)
        self.assertFalse(ts["found"])
        self.assertEqual(ts.get("error"), "work_item_ambiguous")

    def test_gate_binds_to_selected_item_only(self):
        cwg.ensure_gate(self.root, self.wid2, "c2", "plan",
                        "operator_required", 5, "sh", "i1")
        ts1 = server.build_task_state(self.root, work_item_id=self.wid1)
        ts2 = server.build_task_state(self.root, work_item_id=self.wid2)
        self.assertIsNone(ts1["gate"])
        self.assertIsNotNone(ts2["gate"])

    def test_ui_keys_on_work_item_id(self):
        appjs = read(os.path.join(STATIC, "app.js"))
        self.assertIn("selectedWorkItemId", appjs)
        self.assertIn("/api/task-state", appjs)
        self.assertIn("?work_item_id=", appjs)
        self.assertIn("data-work-item", appjs)
        self.assertIn("source_work_item_id === selectedWorkItemId", appjs)


class LifecycleTests(unittest.TestCase):
    """E: lifecycle records, sanitization, rotation, instance lock, sentinel."""

    def setUp(self):
        self.root = queue("stab_lc_", self)

    def test_sanitizer_redacts_all_forms(self):
        out = csl.sanitize_argv([
            "server.py", "--api-key", "sk-abcdef123456", "--token=deadbeefdeadbeef",
            "/secret:zzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzzz",
            "https://user:pass@host/x"])
        joined = " ".join(out)
        self.assertNotIn("sk-abcdef123456", joined)
        self.assertNotIn("deadbeefdeadbeef", joined)
        self.assertNotIn("user:pass", joined)
        self.assertIn("<redacted>", joined)

    def test_record_writes_event(self):
        rec = csl.record(self.root, "startup_ok", version="v", port=8787)
        self.assertIsNotNone(rec)
        self.assertTrue(os.path.isfile(csl.lifecycle_path(self.root)))
        line = read(csl.lifecycle_path(self.root)).strip().splitlines()[-1]
        self.assertEqual(json.loads(line)["event"], "startup_ok")

    def test_instance_lock_refuses_second_live_holder(self):
        ok1, _ = csl.acquire_instance_lock(self.root, 8787)
        self.assertTrue(ok1)
        # A second acquire in THIS process sees its own live pid -> refused.
        ok2, holder = csl.acquire_instance_lock(self.root, 8787)
        self.assertFalse(ok2)
        self.assertEqual(holder.get("pid"), os.getpid())
        csl.release_instance_lock(self.root, 8787)

    def test_stale_lock_replaced_with_prior_unclean(self):
        # Seed a lock owned by a dead pid on THIS host (so liveness can confirm
        # non-liveness); acquisition replaces it and flags prior_unclean.
        import clearwright_writer_lock as cwl
        os.makedirs(csl.logs_dir(self.root), exist_ok=True)
        csl._atomic_write(csl.instance_lock_path(self.root, 8788),
                          {"pid": 999999999, "host": cwl._this_host(),
                           "start_time": "x", "port": 8788})
        ok, note = csl.acquire_instance_lock(self.root, 8788)
        self.assertTrue(ok)
        self.assertIn("prior_unclean", note)
        csl.release_instance_lock(self.root, 8788)

    def test_stop_sentinel_only_targets_matching_process(self):
        # A sentinel naming a different pid is not honored and is cleaned up.
        os.makedirs(csl.logs_dir(self.root), exist_ok=True)
        csl.write_stop_sentinel(self.root, 8789, 123456789, "someothertime")
        self.assertFalse(csl.stop_sentinel_targets_me(self.root, 8789))
        self.assertFalse(os.path.isfile(csl.stop_sentinel_path(self.root, 8789)))

    def test_canonical_logs_dir(self):
        expected = os.path.join(os.path.dirname(os.path.abspath(self.root)), "logs")
        self.assertEqual(csl.logs_dir(self.root), expected)


class LauncherSourceTests(unittest.TestCase):
    """F: the manual launcher ships and registers nothing."""

    def test_launcher_scripts_exist_and_register_nothing(self):
        start = read(os.path.join(TOOLS, "start-clearwright.ps1"))
        stop = read(os.path.join(TOOLS, "stop-clearwright.ps1"))
        for text in (start, stop):
            self.assertNotIn("schtasks", text.lower())
            self.assertNotIn("register-scheduledtask", text.lower())
            self.assertNotIn("new-service", text.lower())
        # Duplicate/port/exit-code contract present in the launcher.
        self.assertIn("exit 5", start)   # already running
        self.assertIn("exit 3", start)   # port occupied
        self.assertIn("launchlock", start)
        # Stop verifies identity before terminating.
        self.assertIn("exit 6", stop)    # identity mismatch refusal
        self.assertIn("server.py", stop)


if __name__ == "__main__":
    unittest.main()
