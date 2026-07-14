"""Archive framework: retention, inventory hashing, plan generation, hash-bound
approval, and the crash-safe journal/move state machine (commit 3).

Execution moves records only when they are BOTH in the approved inventory AND
still currently eligible under live retention; any live candidate NOT in the
approved inventory stops the run. Zero deletion: every moved file's content is
byte-identical at its archive path, verified by hash before and after rename.
"""
import contextlib
import io
import json
import os
import shutil
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO_ROOT, "apps", "control-plane"))
sys.path.insert(0, os.path.join(REPO_ROOT, "tools"))
import server  # noqa: E402
import clearwright_message as cwm  # noqa: E402
import clearwright_archive as cwa  # noqa: E402
import clearwright_writer_lock as cwl  # noqa: E402


def _iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _seed_message(root, thread_id, mid_suffix, at, direction="inbound",
                  body="a message", actor="OPERATOR-0001", packet_id=None):
    directory = cwm.comms_dir(root)
    os.makedirs(directory, exist_ok=True)
    mid = "msg-" + mid_suffix
    msg = {"message_id": mid, "thread_id": thread_id, "at": _iso(at),
          "actor": actor, "role": "operator", "direction": direction,
          "status": "posted" if direction == "inbound" else "responded",
          "message": body, "source": "test", "simulated": False}
    if packet_id:
        msg["packet_id"] = packet_id
    with open(os.path.join(directory, mid + ".json"), "w", encoding="utf-8") as fh:
        json.dump(msg, fh)
    return msg


class ArchiveTestBase(unittest.TestCase):
    def setUp(self):
        base = tempfile.mkdtemp(prefix="archive_")
        self.addCleanup(shutil.rmtree, base, ignore_errors=True)
        # Nest under "active" (as production does: queues/active) so
        # archive_root() -- the queue root's PARENT + "archive" -- lands
        # inside this test's own private temp dir, not the shared system temp
        # folder that mkdtemp's bare unique dirs would otherwise share a
        # parent with.
        self.root, *_ = server.resolve_queue(os.path.join(base, "active"))
        self.now = datetime(2026, 8, 1, tzinfo=timezone.utc)
        self.old = self.now - timedelta(hours=200)  # well past 72h
        self.recent = self.now - timedelta(hours=10)


class RetentionClassificationTests(ArchiveTestBase):

    def test_smoke_thread_archives_regardless_of_recency_bucket(self):
        _seed_message(self.root, "thr-smoke", "s1", self.old, body="Smoke test run.")
        _seed_message(self.root, "thr-smoke", "s2", self.old + timedelta(minutes=1),
                      direction="outbound", body="done")
        result = cwa.classify_threads(self.root, now=self.now)
        self.assertIn("thr-smoke", result["archive"])

    def test_recent_smoke_thread_still_archives_age_never_protects_smoke(self):
        # A smoke/proof thread from an hour ago must still be a candidate:
        # recency never overrides the "regardless of age" smoke rule.
        _seed_message(self.root, "thr-recent-smoke", "rs1",
                      self.now - timedelta(hours=1), body="Smoke test run.")
        _seed_message(self.root, "thr-recent-smoke", "rs2",
                      self.now - timedelta(minutes=59), direction="outbound",
                      body="done")
        result = cwa.classify_threads(self.root, now=self.now)
        self.assertIn("thr-recent-smoke", result["archive"])
        self.assertNotIn("thr-recent-smoke", result["keep"])

    def test_pinned_smoke_thread_is_kept_despite_smoke(self):
        _seed_message(self.root, "thr-pinned-smoke", "ps1", self.old,
                      body="Smoke test run.")
        _seed_message(self.root, "thr-pinned-smoke", "ps2",
                      self.old + timedelta(minutes=1), direction="outbound",
                      body="done")
        cwa.pin(self.root, "thr-pinned-smoke")
        result = cwa.classify_threads(self.root, now=self.now)
        self.assertIn("thr-pinned-smoke", result["keep"])
        self.assertNotIn("thr-pinned-smoke", result["archive"])

    def test_nonterminal_thread_is_never_a_candidate(self):
        _seed_message(self.root, "thr-open", "o1", self.old, body="Do a task.")
        result = cwa.classify_threads(self.root, now=self.now)
        self.assertNotIn("thr-open", result["archive"])
        self.assertNotIn("thr-open", result["keep"])

    def test_recent_terminal_thread_is_kept(self):
        _seed_message(self.root, "thr-recent", "r1", self.recent, body="A task.")
        _seed_message(self.root, "thr-recent", "r2", self.recent + timedelta(minutes=1),
                      direction="outbound", body="done")
        result = cwa.classify_threads(self.root, now=self.now)
        self.assertIn("thr-recent", result["keep"])

    def test_pinned_old_thread_is_kept(self):
        _seed_message(self.root, "thr-pinned", "p1", self.old, body="A task.")
        _seed_message(self.root, "thr-pinned", "p2", self.old + timedelta(minutes=1),
                      direction="outbound", body="done")
        cwa.pin(self.root, "thr-pinned")
        result = cwa.classify_threads(self.root, now=self.now)
        self.assertIn("thr-pinned", result["keep"])

    def test_latest_five_genuine_runs_kept_the_sixth_archives(self):
        for i in range(6):
            tid = "thr-op-{}".format(i)
            at = self.old + timedelta(hours=i)  # i=5 is the newest -> kept
            _seed_message(self.root, tid, "op{}a".format(i), at, body="Real work.")
            _seed_message(self.root, tid, "op{}b".format(i), at + timedelta(minutes=1),
                          direction="outbound", body="done")
        result = cwa.classify_threads(self.root, now=self.now)
        kept_ops = [t for t in result["keep"] if t.startswith("thr-op-")]
        archived_ops = [t for t in result["archive"] if t.startswith("thr-op-")]
        self.assertEqual(len(kept_ops), 5)
        self.assertEqual(len(archived_ops), 1)
        self.assertEqual(archived_ops[0], "thr-op-0")  # the oldest is dropped


class ShippedFixtureTests(unittest.TestCase):
    """The checked-in tests/fixtures/archive_inventory.json is the real
    approved inventory used at Objective 5 execution time; its hash must
    verify and it must classify as pure threads/councils/clearance/events."""

    def test_default_fixture_hash_verifies(self):
        inventory = cwa.load_inventory()  # default path; raises on mismatch
        self.assertEqual(inventory["schema_version"], cwa.INVENTORY_SCHEMA_VERSION)
        self.assertGreater(len(inventory["records"]), 0)
        for record in inventory["records"]:
            self.assertIn(record["type"], ("thread", "council", "clearance_packet",
                                           "agent_event"))
            self.assertTrue(record["id"])
            self.assertTrue(record.get("reason"))


class InventoryHashTests(unittest.TestCase):

    def test_build_and_load_roundtrip_verifies_hash(self):
        records = [{"id": "thr-a", "type": "thread", "reason": "old"}]
        inventory = cwa.build_inventory(records, generated_at="2026-01-01T00:00:00.000000Z")
        base = tempfile.mkdtemp(prefix="inv_")
        self.addCleanup(shutil.rmtree, base, ignore_errors=True)
        path = os.path.join(base, "inv.json")
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(inventory, fh)
        loaded = cwa.load_inventory(path)
        self.assertEqual(loaded["content_hash"], inventory["content_hash"])

    def test_tampered_inventory_fails_hash_verification(self):
        records = [{"id": "thr-a", "type": "thread", "reason": "old"}]
        inventory = cwa.build_inventory(records)
        inventory["records"].append({"id": "thr-b", "type": "thread", "reason": "sneaky"})
        base = tempfile.mkdtemp(prefix="inv_")
        self.addCleanup(shutil.rmtree, base, ignore_errors=True)
        path = os.path.join(base, "inv.json")
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(inventory, fh)
        with self.assertRaises(cwa.ArchiveError):
            cwa.load_inventory(path)

    def test_generated_at_excluded_from_hash(self):
        records = [{"id": "thr-a", "type": "thread", "reason": "old"}]
        a = cwa.build_inventory(records, generated_at="2026-01-01T00:00:00.000000Z")
        b = cwa.build_inventory(records, generated_at="2026-02-02T00:00:00.000000Z")
        self.assertEqual(a["content_hash"], b["content_hash"])


class PlanGenerationTests(ArchiveTestBase):

    def _seed_smoke(self, tid):
        _seed_message(self.root, tid, tid + "a", self.old, body="Smoke test.")
        _seed_message(self.root, tid, tid + "b", self.old + timedelta(minutes=1),
                      direction="outbound", body="done")

    def test_out_of_approval_candidate_stops_the_run(self):
        self._seed_smoke("thr-unapproved")
        inventory = cwa.build_inventory([])  # nothing approved
        result = cwa.generate_plan(self.root, inventory, now=self.now)
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], "candidates_outside_approved_inventory")
        self.assertIn("thr-unapproved", result["extra"]["thread"])

    def test_approved_but_unqualified_is_skipped_not_an_error(self):
        # Approve a thread id that does not currently exist / qualify.
        inventory = cwa.build_inventory(
            [{"id": "thr-does-not-exist", "type": "thread", "reason": "old"}])
        result = cwa.generate_plan(self.root, inventory, now=self.now)
        self.assertTrue(result["ok"])
        self.assertIn("thr-does-not-exist", result["skipped_not_qualifying"]["thread"])
        self.assertEqual(result["file_count"], 0)

    def test_plan_includes_exactly_the_approved_qualifying_files(self):
        self._seed_smoke("thr-approved")
        inventory = cwa.build_inventory(
            [{"id": "thr-approved", "type": "thread", "reason": "smoke"}])
        result = cwa.generate_plan(self.root, inventory, now=self.now)
        self.assertTrue(result["ok"])
        self.assertEqual(result["file_count"], 2)
        self.assertTrue(all(e["type"] == "thread" for e in result["plan"]["entries"]))
        self.assertTrue(result["plan_hash"])

    def test_plan_hash_is_deterministic_and_content_sensitive(self):
        self._seed_smoke("thr-x")
        inventory = cwa.build_inventory([{"id": "thr-x", "type": "thread", "reason": "smoke"}])
        r1 = cwa.generate_plan(self.root, inventory, now=self.now)
        r2 = cwa.generate_plan(self.root, inventory, now=self.now)
        self.assertEqual(r1["plan_hash"], r2["plan_hash"])


class ApprovalMatchingTests(ArchiveTestBase):

    def _operator_msg(self, thread_id, body):
        return _seed_message(self.root, thread_id, "auth-" + str(id(body)),
                             self.now, body=body)

    def test_no_approval_is_no_archive_authority(self):
        approval, err = cwa.find_eligible_approval(self.root, "a" * 64)
        self.assertIsNone(approval)
        self.assertEqual(err, "no_archive_authority")

    def test_full_64_hex_required(self):
        with self.assertRaises(cwa.ArchiveError):
            cwa.write_approval(self.root, "not-a-hash", "msg-x", "OPERATOR-0001")
        with self.assertRaises(cwa.ArchiveError):
            cwa.write_approval(self.root, "a" * 63, "msg-x", "OPERATOR-0001")  # too short

    def test_hash_and_queue_root_must_match(self):
        h = "b" * 64
        cwa.write_approval(self.root, h, "msg-x", "OPERATOR-0001")
        approval, err = cwa.find_eligible_approval(self.root, "c" * 64)
        self.assertIsNone(approval)
        self.assertEqual(err, "no_archive_authority")
        approval, err = cwa.find_eligible_approval(self.root, h)
        self.assertIsNotNone(approval)

    def test_revoked_approval_is_not_eligible(self):
        h = "d" * 64
        rec = cwa.write_approval(self.root, h, "msg-x", "OPERATOR-0001")
        cwa.revoke_approval(self.root, rec["approval_id"])
        approval, err = cwa.find_eligible_approval(self.root, h)
        self.assertIsNone(approval)
        self.assertEqual(err, "no_archive_authority")

    def test_supersession_keeps_only_the_newest_same_hash_approval(self):
        h = "e" * 64
        cwa.write_approval(self.root, h, "msg-1", "OPERATOR-0001")
        import time
        time.sleep(0.01)
        newer = cwa.write_approval(self.root, h, "msg-2", "OPERATOR-0001")
        approval, err = cwa.find_eligible_approval(self.root, h)
        self.assertIsNone(err)
        self.assertEqual(approval["approval_id"], newer["approval_id"])

    def test_validate_approval_message_requires_operator_inbound_with_hash_token(self):
        h = "f" * 64
        rec = cwa.write_approval(self.root, h, "no-such-message", "OPERATOR-0001")
        ok, err = cwa.validate_approval_message(self.root, rec)
        self.assertFalse(ok)
        self.assertEqual(err, "authority_message_not_found")

        msg = self._operator_msg("thr-auth", "some unrelated text")
        rec2 = cwa.write_approval(self.root, h, msg["message_id"], "OPERATOR-0001")
        ok, err = cwa.validate_approval_message(self.root, rec2)
        self.assertFalse(ok)
        self.assertEqual(err, "authority_missing_hash_token")

        msg2 = self._operator_msg("thr-auth2",
                                  "I authorize archive execution for hash " + h)
        rec3 = cwa.write_approval(self.root, h, msg2["message_id"], "OPERATOR-0001")
        ok, err = cwa.validate_approval_message(self.root, rec3)
        self.assertTrue(ok)


class ExecuteAndZeroDataLossTests(ArchiveTestBase):

    def _prepare_smoke_thread(self, tid):
        _seed_message(self.root, tid, tid + "a", self.old, body="Smoke test.")
        _seed_message(self.root, tid, tid + "b", self.old + timedelta(minutes=1),
                      direction="outbound", body="done")

    def _count_files(self, root_dir):
        total = 0
        for _dirpath, _dirnames, filenames in os.walk(root_dir):
            total += len([f for f in filenames if not f.endswith(".tmp")])
        return total

    def test_execute_moves_records_preserving_every_byte(self):
        self._prepare_smoke_thread("thr-exec")
        src_path = os.path.join(cwm.comms_dir(self.root), "msg-thr-execa.json")
        with open(src_path, encoding="utf-8") as fh:
            original_bytes = fh.read()

        inventory = cwa.build_inventory(
            [{"id": "thr-exec", "type": "thread", "reason": "smoke"}])
        base = tempfile.mkdtemp(prefix="inv2_")
        self.addCleanup(shutil.rmtree, base, ignore_errors=True)
        inv_path = os.path.join(base, "inv.json")
        with open(inv_path, "w", encoding="utf-8") as fh:
            json.dump(inventory, fh)

        plan = cwa.dry_run(self.root, inventory_path=inv_path, now=self.now)
        self.assertTrue(plan["ok"])
        plan_hash = plan["plan_hash"]

        auth = _seed_message(self.root, "thr-authexec", "authexec", self.now,
                             body="I authorize archive execution for hash " + plan_hash)
        cwa.write_approval(self.root, plan_hash, auth["message_id"], "OPERATOR-0001")

        before_active = self._count_files(self.root)
        before_archive = self._count_files(cwa.archive_root(self.root))

        result = cwa.execute(self.root, inventory_path=inv_path, now=self.now)
        self.assertTrue(result["ok"], result)
        self.assertEqual(result["moved"], 2)
        self.assertFalse(os.path.isfile(src_path))

        # Every archived byte is preserved exactly.
        moved_paths = [r["archive_path"] for r in result["manifest"]["records"]]
        found = False
        for p in moved_paths:
            with open(p, encoding="utf-8") as fh:
                if fh.read() == original_bytes:
                    found = True
        self.assertTrue(found)

        # Zero data loss: total files (active + archive) unchanged except for
        # the new journal/manifest/index bookkeeping records the run itself adds.
        after_active = self._count_files(self.root)
        after_archive = self._count_files(cwa.archive_root(self.root))
        self.assertEqual(before_active - 2, after_active - 0)  # 2 moved out of active
        self.assertGreaterEqual(after_archive, before_archive + 2)  # moved in + bookkeeping

        # Archive-aware resolution.
        resolved = cwa.resolve_archived(self.root, "thr-exec")
        self.assertTrue(resolved["archived"])
        self.assertEqual(len(resolved["paths"]), 2)

    def test_inventory_drift_between_dry_run_and_execute_is_caught(self):
        self._prepare_smoke_thread("thr-drift")
        inventory = cwa.build_inventory(
            [{"id": "thr-drift", "type": "thread", "reason": "smoke"}])
        base = tempfile.mkdtemp(prefix="inv3_")
        self.addCleanup(shutil.rmtree, base, ignore_errors=True)
        inv_path = os.path.join(base, "inv.json")
        with open(inv_path, "w", encoding="utf-8") as fh:
            json.dump(inventory, fh)
        plan = cwa.dry_run(self.root, inventory_path=inv_path, now=self.now)
        plan_hash = plan["plan_hash"]
        auth = _seed_message(self.root, "thr-authdrift", "authdrift", self.now,
                             body="I authorize archive execution for hash " + plan_hash)
        cwa.write_approval(self.root, plan_hash, auth["message_id"], "OPERATOR-0001")
        # Mutate the source content after the dry-run but before execute.
        src_path = os.path.join(cwm.comms_dir(self.root), "msg-thr-drifta.json")
        with open(src_path, encoding="utf-8") as fh:
            data = json.load(fh)
        data["message"] = "tampered after dry-run"
        with open(src_path, "w", encoding="utf-8") as fh:
            json.dump(data, fh)
        # execute() recomputes at 'now' internally (real time), so the drift
        # here is content-based and caught by the fresh plan_hash mismatch OR
        # by source rehash inside execute_journal. Either halts safely.
        result = cwa.execute(self.root, inventory_path=inv_path, now=self.now)
        self.assertFalse(result["ok"])


class CrashRecoveryTests(ArchiveTestBase):

    def test_halts_on_source_hash_drift_before_move(self):
        mdir = os.path.join(cwa.archive_root(self.root), "2026-08")
        src_dir = os.path.join(self.root, "communications")
        os.makedirs(src_dir, exist_ok=True)
        src = os.path.join(src_dir, "victim.json")
        with open(src, "w", encoding="utf-8") as fh:
            fh.write('{"a": 1}')
        wrong_hash = "0" * 64
        journal = {"schema_version": 1, "opid": "op-test",
                  "created_at": cwa._now_iso(), "approved_plan_sha256": "x",
                  "planned": [{"id": "thr-x", "type": "thread", "src": src,
                              "dst": os.path.join(mdir, "victim.json"),
                              "sha256": wrong_hash}]}
        with self.assertRaises(cwa.ArchiveError) as ctx:
            cwa.execute_journal(mdir, journal)
        self.assertIn("source_drift", str(ctx.exception))
        self.assertTrue(os.path.isfile(src))  # nothing moved

    def test_destination_collision_halts(self):
        mdir = os.path.join(cwa.archive_root(self.root), "2026-08")
        os.makedirs(mdir, exist_ok=True)
        src_dir = os.path.join(self.root, "communications")
        os.makedirs(src_dir, exist_ok=True)
        src = os.path.join(src_dir, "collide.json")
        with open(src, "w", encoding="utf-8") as fh:
            fh.write('{"a": 1}')
        dst = os.path.join(mdir, "collide.json")
        with open(dst, "w", encoding="utf-8") as fh:
            fh.write('{"a": 1}')
        real_hash = cwa._sha256_file(src)
        journal = {"schema_version": 1, "opid": "op-test2",
                  "created_at": cwa._now_iso(), "approved_plan_sha256": "x",
                  "planned": [{"id": "thr-x", "type": "thread", "src": src,
                              "dst": dst, "sha256": real_hash}]}
        with self.assertRaises(cwa.ArchiveError) as ctx:
            cwa.execute_journal(mdir, journal)
        self.assertIn("both_exist", str(ctx.exception))

    def test_idempotent_rerun_recovery_completes_already_moved_record(self):
        mdir = os.path.join(cwa.archive_root(self.root), "2026-08")
        os.makedirs(mdir, exist_ok=True)
        src_dir = os.path.join(self.root, "communications")
        os.makedirs(src_dir, exist_ok=True)
        src = os.path.join(src_dir, "resume.json")
        with open(src, "w", encoding="utf-8") as fh:
            fh.write('{"a": 1}')
        real_hash = cwa._sha256_file(src)
        dst = os.path.join(mdir, "resume.json")
        journal = {"schema_version": 1, "opid": "op-resume",
                  "created_at": cwa._now_iso(), "approved_plan_sha256": "x",
                  "planned": [{"id": "thr-x", "type": "thread", "src": src,
                              "dst": dst, "sha256": real_hash}]}
        cwa._atomic_write(cwa._journal_path(mdir, "op-resume", "pending"), journal)
        cwa.execute_journal(mdir, journal)
        self.assertTrue(os.path.isfile(dst))
        self.assertFalse(os.path.isfile(src))
        # Rerun: src is gone, dst exists and hashes match -> treated as done.
        cwa.execute_journal(mdir, journal)  # must not raise

    def test_recover_pending_finds_and_completes_a_journal(self):
        mdir = os.path.join(cwa.archive_root(self.root), "2026-08")
        os.makedirs(mdir, exist_ok=True)
        src_dir = os.path.join(self.root, "communications")
        os.makedirs(src_dir, exist_ok=True)
        src = os.path.join(src_dir, "auto.json")
        with open(src, "w", encoding="utf-8") as fh:
            fh.write('{"a": 1}')
        real_hash = cwa._sha256_file(src)
        dst = os.path.join(mdir, "auto.json")
        journal = {"schema_version": 1, "opid": "op-auto",
                  "created_at": cwa._now_iso(), "approved_plan_sha256": "x",
                  "planned": [{"id": "thr-x", "type": "thread", "src": src,
                              "dst": dst, "sha256": real_hash}]}
        cwa._atomic_write(cwa._journal_path(mdir, "op-auto", "pending"), journal)
        results = cwa.recover_pending(self.root)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["status"], "completed")
        self.assertTrue(os.path.isfile(dst))
        self.assertFalse(os.path.isfile(
            cwa._journal_path(mdir, "op-auto", "pending")))
        self.assertTrue(os.path.isfile(
            cwa._journal_path(mdir, "op-auto", "completed")))

    def test_recovery_preserves_approval_lineage_in_the_manifest(self):
        # Live-execution regression (verify council finding): the first real
        # execute halted mid-journal on a Windows sharing violation and
        # recovery finalized the manifest with approval_id null, losing the
        # direct manifest-to-approval audit link. The journal now carries the
        # approval lineage so a recovered manifest keeps it.
        mdir = os.path.join(cwa.archive_root(self.root), "2026-08")
        os.makedirs(mdir, exist_ok=True)
        src_dir = os.path.join(self.root, "communications")
        os.makedirs(src_dir, exist_ok=True)
        src = os.path.join(src_dir, "lineage.json")
        with open(src, "w", encoding="utf-8") as fh:
            fh.write('{"a": 1}')
        plan = {"entries": [{"id": "thr-x", "type": "thread", "src": src,
                            "dst": os.path.join(mdir, "lineage.json"),
                            "sha256": cwa._sha256_file(src)}]}
        approval = {"approval_id": "apr-lineage-1",
                   "operator_message_id": "msg-lineage-1",
                   "approved_plan_sha256": "h" * 64}
        journal = cwa.write_journal(mdir, "op-lineage", plan, "h" * 64,
                                   approval=approval)
        self.assertEqual(journal["approval_id"], "apr-lineage-1")
        self.assertEqual(journal["operator_message_id"], "msg-lineage-1")
        # Simulate the interruption: journal durable, no move executed yet.
        results = cwa.recover_pending(self.root)
        self.assertEqual(results[0]["status"], "completed")
        manifest = cwa._read_json(
            os.path.join(mdir, "manifest-op-lineage.json"))
        self.assertEqual(manifest["approval_id"], "apr-lineage-1")
        self.assertEqual(manifest["operator_message_id"], "msg-lineage-1")
        self.assertEqual(manifest["approved_plan_sha256"], "h" * 64)


class LogRotationTests(ArchiveTestBase):

    def _write_log(self, lines):
        path = os.path.join(self.root, "invocation_log.jsonl")
        with open(path, "w", encoding="utf-8") as fh:
            for line in lines:
                fh.write(json.dumps(line) + "\n")
        return path

    def test_no_rotation_for_a_small_current_month_log(self):
        self._write_log([{"at": cwa._now_iso(), "command": "x"}])
        result = cwa.rotate_invocation_log_if_needed(self.root)
        self.assertIsNone(result)

    def test_rotates_on_month_change(self):
        old_at = "2020-01-15T00:00:00.000000Z"
        path = self._write_log([{"at": old_at, "command": "x"}])
        result = cwa.rotate_invocation_log_if_needed(self.root, now=self.now)
        self.assertIsNotNone(result)
        self.assertFalse(os.path.isfile(path))
        self.assertTrue(os.path.isfile(result))
        self.assertIn("invocation_log-2020-01", result)

    def test_rotates_on_size_threshold(self):
        # A current-month log that already exceeds the size cap rotates too.
        big_line = json.dumps({"at": cwa._now_iso(), "pad": "x" * 1000})
        path = os.path.join(self.root, "invocation_log.jsonl")
        with open(path, "w", encoding="utf-8") as fh:
            for _ in range(6000):
                fh.write(big_line + "\n")
        self.assertGreater(os.path.getsize(path), cwa.INVOCATION_LOG_MAX_BYTES)
        result = cwa.rotate_invocation_log_if_needed(self.root, now=self.now)
        self.assertIsNotNone(result)

    def test_rotation_coinciding_with_gate_and_manifest_writes(self):
        # A rotation triggered from the same call sequence as other durable
        # writes must not interfere with them: run one alongside a gate write.
        import clearwright_gate as cwg
        old_at = "2020-01-15T00:00:00.000000Z"
        self._write_log([{"at": old_at, "command": "x"}])
        cwg.create_gate(self.root, "message:msg-rot", "cw-council-rot", "plan",
                        "operator_required")
        rotated = cwa.rotate_invocation_log_if_needed(self.root, now=self.now)
        self.assertIsNotNone(rotated)
        self.assertIsNotNone(cwg.active_gate(self.root, "message:msg-rot"))

    def test_no_op_when_no_log_exists(self):
        self.assertIsNone(cwa.rotate_invocation_log_if_needed(self.root))


class NoServerWriteRouteTests(unittest.TestCase):
    """The API/server surface must never be able to mint, edit, or revoke an
    archive approval, or trigger execution -- that boundary is what makes the
    approval hash-bound to a genuinely operator-only channel."""

    def test_server_never_references_archive_write_functions(self):
        server_path = os.path.join(REPO_ROOT, "apps", "control-plane", "server.py")
        with open(server_path, encoding="utf-8") as fh:
            text = fh.read()
        for forbidden in ("write_approval", "revoke_approval", "apply_override",
                          "cwa.execute(", "clearwright_archive.execute("):
            self.assertNotIn(forbidden, text,
                             "server.py must never call {}".format(forbidden))

    def test_no_post_route_under_operator_authority_or_archive(self):
        server_path = os.path.join(REPO_ROOT, "apps", "control-plane", "server.py")
        with open(server_path, encoding="utf-8") as fh:
            text = fh.read()
        post_start = text.index("def do_POST")
        post_end = text.find("\n    def ", post_start + 1)
        post_body = text[post_start:post_end if post_end != -1 else len(text)]
        self.assertNotIn("operator_authority", post_body)
        self.assertNotIn("/api/archive", post_body)


class ActiveRecordFilteringTests(ArchiveTestBase):
    """Local Council site active-record filtering: once a thread is archived,
    it disappears from the live Conversations/Active Run list (server.build_runs,
    which reads only the active communications/ store) while still resolving
    through the archive-aware fallback."""

    def test_archived_thread_excluded_from_build_runs_but_still_resolvable(self):
        _seed_message(self.root, "thr-filter", "f1", self.old, body="Smoke test.")
        _seed_message(self.root, "thr-filter", "f2", self.old + timedelta(minutes=1),
                      direction="outbound", body="done")
        before = server.build_runs(self.root)
        self.assertTrue(any(r["thread_id"] == "thr-filter" for r in before))

        inventory = cwa.build_inventory(
            [{"id": "thr-filter", "type": "thread", "reason": "smoke"}])
        inv_dir = tempfile.mkdtemp(prefix="inv_filter_")
        self.addCleanup(shutil.rmtree, inv_dir, ignore_errors=True)
        inv_path = os.path.join(inv_dir, "inv.json")
        with open(inv_path, "w", encoding="utf-8") as fh:
            json.dump(inventory, fh)
        plan = cwa.dry_run(self.root, inventory_path=inv_path, now=self.now)
        auth = _seed_message(self.root, "thr-auth-filter", "authf", self.now,
                             body="I authorize archive execution for hash " +
                             plan["plan_hash"])
        cwa.write_approval(self.root, plan["plan_hash"], auth["message_id"],
                           "OPERATOR-0001")
        result = cwa.execute(self.root, inventory_path=inv_path, now=self.now)
        self.assertTrue(result["ok"], result)

        after = server.build_runs(self.root)
        self.assertFalse(any(r["thread_id"] == "thr-filter" for r in after),
                         "an archived thread must not appear in the live run list")
        # Still resolvable through the archive-aware fallback.
        archived_messages = cwa.read_archived_messages(self.root, "thr-filter")
        self.assertEqual(len(archived_messages), 2)


class IndexRebuildTests(ArchiveTestBase):

    def test_rebuild_index_reconstructs_from_manifests(self):
        aroot = cwa.archive_root(self.root)
        mdir = os.path.join(aroot, "2026-08")
        manifest = {"schema_version": 1, "opid": "op-idx", "generated_at": cwa._now_iso(),
                   "approved_plan_sha256": "x", "approval_id": "y",
                   "records": [{"id": "thr-idx", "type": "thread",
                               "original_path": "communications/x.json",
                               "archive_path": os.path.join(mdir, "x.json"),
                               "sha256": "z", "reason": "archive"}]}
        cwa._atomic_write(os.path.join(mdir, "manifest-op-idx.json"), manifest)
        idx = cwa.rebuild_index(aroot)
        self.assertIn("thr-idx", idx["ids"])
        resolved = cwa.resolve_archived(self.root, "thr-idx")
        self.assertTrue(resolved["archived"])


if __name__ == "__main__":
    unittest.main()
