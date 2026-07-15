"""Capped-council mandatory-gate integrity (break-glass hotfix for work item
message:msg-20260715T162133277367).

The defect: a plan/incident council reaching its round cap records
operator_required, but gate creation crashed on council["rounds"] (round
NUMBERS treated as dicts), so the mandatory gate was silently never created
and exit-9 enforcement was absent. These tests pin the repaired contract:
capped councils create exactly ONE durable gate; a missing gate is HEALED
before every governed advancement (CLI and control-plane/library surfaces);
malformed durable records fail CLOSED with a durable notice and can never
report success; and normal behavior (agreement councils, grant-proceed,
operator exemption, unknown ids) is unchanged.

The module doubles as the live-verification fixture:
  python -m tests.test_capped_council_gate --live-fixture <queue-root>
  python -m tests.test_capped_council_gate --hold-exclusive <queue-root>
      --seconds N --ready-file <path>
"""
import io
import json
import os
import subprocess
import contextlib
import shutil
import sys
import tempfile
import time
import unittest
from argparse import Namespace

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APP_DIR = os.path.join(REPO_ROOT, "apps", "control-plane")
TOOLS_DIR = os.path.join(REPO_ROOT, "tools")
sys.path.insert(0, APP_DIR)
sys.path.insert(0, TOOLS_DIR)
import server  # noqa: E402
import clearwright_work as cww  # noqa: E402
import clearwright_message as cwm  # noqa: E402
import clearwright_gate as cwg  # noqa: E402
import clearwright_review_council as cwrc  # noqa: E402
import clearwright_writer_lock as cwl  # noqa: E402
import clearwright_use_cw as ucw  # noqa: E402


def queue(prefix, tc):
    base = tempfile.mkdtemp(prefix=prefix)
    tc.addCleanup(shutil.rmtree, base, ignore_errors=True)
    root, *_ = server.resolve_queue(base)
    return root


def run(func, **kw):
    kw.setdefault("json", True)
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        code = func(Namespace(**kw))
    return json.loads(buf.getvalue().strip().splitlines()[-1]), code


def mock_verdict(reviewer, verdict="revise", conf=0.9):
    return {"reviewer": reviewer, "verdict": verdict, "confidence": conf,
            "risk_level": "low", "blocking_findings": [], "required_changes": [],
            "nonblocking_findings": [], "disagreements": [], "assumptions": [],
            "questions": [], "recommended_plan": [],
            "summary": "A substantive review."}


def mock_reviewer(reviewer, source, verdict="revise"):
    def fn(root, context, **kw):
        return {"ok": True, "posted": True, "reviewer": reviewer,
                "verdict": mock_verdict(reviewer, verdict), "validated": True,
                "source": source, "telemetry": {"reviewer": reviewer},
                "message_id": reviewer[0]}
    return fn


def patch_reviewers(tc, verdict="revise"):
    orig = cwrc.run_round
    def patched(root, council, context, **kw):
        kw.pop("gpt_fn", None)
        kw.pop("codex_fn", None)
        allowed = {k: v for k, v in kw.items()
                   if k in ("model", "repo", "timeout", "artifact_ids")}
        return orig(root, council, context,
                    gpt_fn=mock_reviewer("gpt", "openai-api", verdict),
                    codex_fn=mock_reviewer("codex", "codex-cli", verdict),
                    sleep=lambda *_: None, **allowed)
    cwrc.run_round = patched
    tc.addCleanup(setattr, cwrc, "run_round", orig)


def make_item(root, text="Governed request."):
    r = server.do_message(root, {"actor": "OPERATOR-0001", "role": "operator",
                                 "source": "use-cw", "intent": "request",
                                 "message": text})
    m = r["message"]
    return "message:" + m["message_id"], m["thread_id"]


def council_args(root, thread, wid, **kw):
    base = dict(queue_root=root, phase="plan", council_id=None, thread_id=thread,
                work_item_id=wid, packet_id=None, repo=None, plan_file=None,
                context_file=None, prompt="review this plan",
                reconciliation_file=None, stage="review", dry_run=False,
                model=None, approved_scope="operator approved scope",
                min_rounds=2, max_rounds=2, grant_attempts=None,
                operator_message_id=None, timeout=30, json=True)
    base.update(kw)
    return base


def gates_for(root, wid):
    return cwg.load_gates(root, wid)


def gate_notices(root, wid):
    return [m for m in cwm.read_messages(root)
            if m.get("source") == "use-cw-gate" and m.get("work_item_id") == wid
            and "gate creation failed" in (m.get("message") or "")]


def operator_msg(root, thread_id, wid, body, source="operator-console"):
    msg = cwm.build_message("OPERATOR-0001", body, role="operator",
                            thread_id=thread_id, direction="inbound",
                            status="posted", source=source, work_item_id=wid)
    cwm.write_message(root, msg)
    return msg


def fabricated_round(n, phase="plan", substantive=True, verdict="revise"):
    return {"round": n, "phase": phase, "at": cwm._now_iso(),
            "substantive": substantive, "context_sha256": "f" * 64,
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


def fabricate_capped_council(root, thread, wid, phase="plan", rounds=(1, 2),
                             save_outcome=True):
    """A real capped council written with the cwrc primitives: recorded rounds
    at the cap, persisted operator_required outcome, NO gate."""
    c = cwrc.create_council(root, thread_id=thread, work_item_id=wid,
                            phase=phase, min_rounds=2, max_rounds=2,
                            approved_scope="operator approved scope")
    for n in rounds:
        cwrc.save_round(root, c, fabricated_round(n, phase=phase))
        c = cwrc.load_council(root, c["council_id"])
    if save_outcome:
        outcome = cwrc.evaluate(c, cwrc.load_rounds(root, c["council_id"]))
        cwrc.save_outcome(root, c["council_id"], outcome)
    return cwrc.load_council(root, c["council_id"])


class DriveHelpers(unittest.TestCase):
    """Shared drivers; every test class below inherits these."""

    def drive_capped(self, root, thread, wid, phase="plan"):
        """Drive a REAL capped council through the wrapper: round 1 -> exit 2,
        round 2 -> operator_required. Returns (council_id, final_res, code)."""
        patch_reviewers(self, "revise")
        res1, code1 = run(lambda a: ucw._council(a, phase),
                          **council_args(root, thread, wid, phase=phase))
        self.assertEqual(code1, ucw.EXIT_REVISION)
        cid = res1["council_id"]
        res2, code2 = run(lambda a: ucw._council(a, phase),
                          **council_args(root, thread, wid, phase=phase,
                                         council_id=cid))
        return cid, res2, code2

    def failure_window(self, root, thread, wid, phase="plan", keep_fault=False):
        """A REAL failure window: capped council whose gate creation FAILED
        (injected fault at the outcome-time call site). Returns council_id.
        The fault is removed afterwards unless keep_fault=True."""
        orig = cwg.ensure_gate
        def boom(*a, **k):
            raise OSError("injected gate-creation fault")
        cwg.ensure_gate = boom
        try:
            cid, res, code = self.drive_capped(root, thread, wid, phase)
            self.assertEqual(code, ucw.EXIT_RUNTIME)
            self.assertFalse(res["ok"])
            self.assertEqual(res["error"], "gate_creation_failed")
            self.assertEqual(res["error_code"], "gate_creation_failed")
        finally:
            if not keep_fault:
                cwg.ensure_gate = orig
            else:
                self.addCleanup(setattr, cwg, "ensure_gate", orig)
        with open(os.path.join(cwrc.council_dir(root, cid), "outcome.json"),
                  encoding="utf-8") as fh:
            outcome = json.load(fh)
        self.assertEqual(outcome["outcome"], "operator_required")
        self.assertEqual(len(gates_for(root, wid)), 0)
        return cid


class CappedCouncilCreatesGateTests(DriveHelpers):
    """1/2: a capped plan or incident council creates exactly one durable gate."""

    def setUp(self):
        self.root = queue("ccg_cap_", self)
        self.wid, self.thread = make_item(self.root)

    def test_capped_plan_council_creates_exactly_one_gate(self):
        cid, res, code = self.drive_capped(self.root, self.thread, self.wid, "plan")
        self.assertEqual(code, ucw.EXIT_OPERATOR)
        self.assertEqual(res["outcome"], "operator_required")
        gates = gates_for(self.root, self.wid)
        self.assertEqual(len(gates), 1)
        self.assertEqual(gates[0]["council_id"], cid)
        self.assertEqual(gates[0]["phase"], "plan")
        self.assertEqual(gates[0]["subject"], self.wid)
        self.assertEqual(gates[0]["disposition"], "unresolved")

    def test_capped_incident_council_creates_exactly_one_gate(self):
        cid, res, code = self.drive_capped(self.root, self.thread, self.wid,
                                           "incident")
        self.assertEqual(code, ucw.EXIT_OPERATOR)
        gates = gates_for(self.root, self.wid)
        self.assertEqual(len(gates), 1)
        self.assertEqual(gates[0]["council_id"], cid)
        self.assertEqual(gates[0]["phase"], "incident")

    def test_round_numbers_and_substantive_flags_processed_correctly(self):
        # A fabricated capped council with one substantive and one audit round:
        # the derived count must be 1 (ints resolved through round FILES).
        c = fabricate_capped_council(self.root, self.thread, self.wid,
                                     save_outcome=False)
        cwrc.save_round(self.root, c, dict(fabricated_round(2),
                                           substantive=False))
        c = cwrc.load_council(self.root, c["council_id"])
        outcome = {"outcome": "operator_required", "phase": "plan"}
        res = cwg.record_escalation_gate(self.root, c["council_id"], outcome,
                                         {"work_item_id": self.wid,
                                          "thread_id": self.thread})
        self.assertTrue(res["ok"])
        # Re-ensuring with the hand-computed substantive count (1) must DEDUP,
        # proving the wrapper derived the same count from the round files.
        again = cwg.ensure_gate(self.root, self.wid, c["council_id"], "plan",
                                "operator_required", 1,
                                c.get("approved_scope_sha256") or "none", "i2")
        self.assertTrue(again["deduplicated"])
        self.assertEqual(len(gates_for(self.root, self.wid)), 1)

    def test_noncapped_agreement_council_unchanged_no_gate(self):
        patch_reviewers(self, "approve")
        res1, code1 = run(lambda a: ucw._council(a, "plan"),
                          **council_args(self.root, self.thread, self.wid,
                                         max_rounds=5))
        self.assertEqual(code1, ucw.EXIT_REVISION)
        cid = res1["council_id"]
        run(lambda a: ucw._council(a, "plan"),
            **council_args(self.root, self.thread, self.wid, max_rounds=5,
                           council_id=cid))
        recon = {"accepted_findings": ["ok"], "rejected_findings": [],
                 "required_plan_changes": [], "revised_plan": ["do it"],
                 "unresolved_blockers": [], "resolutions": [],
                 "ready_to_proceed": True, "summary": "reconciled the reviews"}
        rf = os.path.join(self.root, "recon.json")
        with open(rf, "w", encoding="utf-8") as fh:
            json.dump(recon, fh)
        res, code = run(lambda a: ucw._council(a, "plan"),
                        **council_args(self.root, self.thread, self.wid,
                                       max_rounds=5, stage="reconcile",
                                       council_id=cid, reconciliation_file=rf))
        self.assertEqual(code, ucw.EXIT_OK)
        self.assertEqual(res["outcome"], "agreement_threshold_met")
        self.assertEqual(len(gates_for(self.root, self.wid)), 0)


class IdempotencyTests(DriveHelpers):
    """3: exactly-one-gate across repeated calls and process restarts."""

    def setUp(self):
        self.root = queue("ccg_idem_", self)
        self.wid, self.thread = make_item(self.root)
        self.cid, _, code = self.drive_capped(self.root, self.thread, self.wid)
        self.assertEqual(code, ucw.EXIT_OPERATOR)

    def test_repeated_commands_recognize_the_existing_gate(self):
        for _ in range(2):
            res, code = run(ucw.cmd_progress, queue_root=self.root,
                            work_item_id=self.wid, message="note",
                            message_file=None)
            self.assertEqual(code, ucw.EXIT_GATE)
            self.assertEqual(res["error"], "unresolved_gate")
        gates = gates_for(self.root, self.wid)
        self.assertEqual(len(gates), 1)
        self.assertLessEqual(len(gates[0].get("deduplicated_events", [])), 1)

    def test_restart_determinism_across_a_process_boundary(self):
        code = ("import sys; sys.path.insert(0, %r); sys.path.insert(0, %r); "
                "import clearwright_gate as cwg; "
                "r = cwg.heal_escalation_gates(%r, %r); "
                "print(r['ok'], len(r['healed']))"
                % (TOOLS_DIR, APP_DIR, self.root, self.wid))
        out = subprocess.run([sys.executable, "-c", code], capture_output=True,
                             text=True, encoding="utf-8")
        self.assertEqual(out.returncode, 0, out.stderr)
        self.assertIn("True 1", out.stdout)
        self.assertEqual(len(gates_for(self.root, self.wid)), 1)


class MalformedDataTests(DriveHelpers):
    """5/6/13b: fail-closed refusal on malformed durable data; failure can
    never report success; the durable notice is posted exactly once."""

    def setUp(self):
        self.root = queue("ccg_mal_", self)
        self.wid, self.thread = make_item(self.root)

    def test_missing_round_file_fails_closed_no_gate_no_summary(self):
        c = fabricate_capped_council(self.root, self.thread, self.wid,
                                     save_outcome=False)
        os.remove(cwrc._round_path(self.root, c["council_id"], 1))
        patch_reviewers(self, "revise")
        # The cap guard undercounts past the missing file, dispatches one more
        # round, then the outcome-time gate creation hits the invariant.
        res, code = run(lambda a: ucw._council(a, "plan"),
                        **council_args(self.root, self.thread, self.wid,
                                       council_id=c["council_id"]))
        self.assertEqual(code, ucw.EXIT_RUNTIME)
        self.assertFalse(res["ok"])
        self.assertEqual(res["error"], "gate_creation_failed")
        self.assertEqual(res["error_code"], "gate_creation_failed")
        self.assertEqual(res["invariant"], "round_records_unreadable")
        self.assertEqual(res["work_item_id"], self.wid)
        self.assertEqual(res["council_id"], c["council_id"])
        self.assertIn("1", str(res["detail"]))
        self.assertEqual(len(gates_for(self.root, self.wid)), 0)
        mid = self.wid.split(":", 1)[1]
        self.assertFalse(os.path.isfile(
            os.path.join(self.root, "summaries", mid + ".json")))
        self.assertEqual(len(gate_notices(self.root, self.wid)), 1)

    def test_corrupt_round_file_fails_closed(self):
        c = fabricate_capped_council(self.root, self.thread, self.wid)
        with open(cwrc._round_path(self.root, c["council_id"], 1), "w",
                  encoding="utf-8") as fh:
            fh.write("{not json")
        res = cwg.record_escalation_gate(
            self.root, c["council_id"],
            {"outcome": "operator_required", "phase": "plan"},
            {"work_item_id": self.wid, "thread_id": self.thread})
        self.assertFalse(res["ok"])
        self.assertEqual(res["invariant"], "round_records_unreadable")
        self.assertEqual(len(gates_for(self.root, self.wid)), 0)

    def test_malformed_outcome_fails_closed_both_layers_idempotent_notice(self):
        c = fabricate_capped_council(self.root, self.thread, self.wid)
        opath = os.path.join(cwrc.council_dir(self.root, c["council_id"]),
                             "outcome.json")
        with open(opath, "w", encoding="utf-8") as fh:
            fh.write("{broken")
        res, code = run(ucw.cmd_progress, queue_root=self.root,
                        work_item_id=self.wid, message="x", message_file=None)
        self.assertEqual(code, ucw.EXIT_RUNTIME)
        self.assertEqual(res["invariant"], "outcome_record_unreadable")
        self.assertEqual(res["council_id"], c["council_id"])
        lib = cww.progress_work_item(self.root, self.wid, "claude", "x")
        self.assertFalse(lib["ok"])
        self.assertEqual(lib["error"], "gate_creation_failed")
        self.assertEqual(server.wi_status_code(lib), 500)
        self.assertEqual(len(gates_for(self.root, self.wid)), 0)
        self.assertEqual(len(gate_notices(self.root, self.wid)), 1)

    def test_structurally_invalid_outcome_fails_closed(self):
        c = fabricate_capped_council(self.root, self.thread, self.wid)
        opath = os.path.join(cwrc.council_dir(self.root, c["council_id"]),
                             "outcome.json")
        with open(opath, "w", encoding="utf-8") as fh:
            json.dump({"outcome": 5}, fh)
        res, code = run(ucw.cmd_progress, queue_root=self.root,
                        work_item_id=self.wid, message="x", message_file=None)
        self.assertEqual(code, ucw.EXIT_RUNTIME)
        self.assertEqual(res["invariant"], "outcome_record_unreadable")

    def test_absent_outcome_blocks_nothing(self):
        fabricate_capped_council(self.root, self.thread, self.wid,
                                 save_outcome=False)
        res, code = run(ucw.cmd_progress, queue_root=self.root,
                        work_item_id=self.wid, message="fine",
                        message_file=None)
        self.assertEqual(code, ucw.EXIT_OK)

    def test_malformed_council_record_fails_closed(self):
        c = fabricate_capped_council(self.root, self.thread, self.wid)
        cpath = os.path.join(cwrc.council_dir(self.root, c["council_id"]),
                             "council.json")
        with open(cpath, "w", encoding="utf-8") as fh:
            fh.write("][")
        res, code = run(ucw.cmd_progress, queue_root=self.root,
                        work_item_id=self.wid, message="x", message_file=None)
        self.assertEqual(code, ucw.EXIT_RUNTIME)
        self.assertEqual(res["invariant"], "council_record_unreadable")

    def test_recognition_predicate_ignores_non_council_dirs(self):
        os.makedirs(os.path.join(cwrc.councils_root(self.root), "tmp-junk"),
                    exist_ok=True)
        res, code = run(ucw.cmd_progress, queue_root=self.root,
                        work_item_id=self.wid, message="fine",
                        message_file=None)
        self.assertEqual(code, ucw.EXIT_OK)

    def test_empty_recognized_dir_is_an_archived_remnant_and_skipped(self):
        # The archiver moves a council's files and leaves the emptied source
        # directory behind (zero-deletion policy). Such a directory contains
        # no files, cannot hide an escalation, and must NOT brick the queue.
        os.makedirs(os.path.join(cwrc.councils_root(self.root),
                                 "cw-council-20260101T000000000000"),
                    exist_ok=True)
        res, code = run(ucw.cmd_progress, queue_root=self.root,
                        work_item_id=self.wid, message="fine",
                        message_file=None)
        self.assertEqual(code, ucw.EXIT_OK)

    def test_recognized_dir_with_files_but_no_council_json_fails_closed(self):
        cdir = os.path.join(cwrc.councils_root(self.root),
                            "cw-council-20260101T000000000001")
        os.makedirs(cdir, exist_ok=True)
        with open(os.path.join(cdir, "round-01.json"), "w",
                  encoding="utf-8") as fh:
            fh.write("{}")
        res, code = run(ucw.cmd_progress, queue_root=self.root,
                        work_item_id=self.wid, message="x", message_file=None)
        self.assertEqual(code, ucw.EXIT_RUNTIME)
        self.assertEqual(res["invariant"], "council_record_unreadable")

    def test_capped_verify_council_creates_no_gate(self):
        # Verify councils use the completion gate; a capped verify council
        # keeps the existing verification_incomplete flow and NEVER creates
        # an escalation gate.
        c = fabricate_capped_council(self.root, self.thread, self.wid,
                                     phase="verify")
        self.assertEqual(len(gates_for(self.root, self.wid)), 0)
        res = cwg.record_escalation_gate(
            self.root, c["council_id"],
            {"outcome": "operator_required", "phase": "verify"},
            {"work_item_id": self.wid, "thread_id": self.thread})
        self.assertIsNone(res)
        self.assertEqual(len(gates_for(self.root, self.wid)), 0)
        res2, code2 = run(ucw.cmd_progress, queue_root=self.root,
                          work_item_id=self.wid, message="fine",
                          message_file=None)
        self.assertEqual(code2, ucw.EXIT_OK)

    def test_unbound_capped_council_creates_no_gate_record(self):
        c = cwrc.create_council(self.root, thread_id=self.thread,
                                phase="plan", min_rounds=2, max_rounds=2,
                                approved_scope="scope")
        for n in (1, 2):
            cwrc.save_round(self.root, c, fabricated_round(n))
            c = cwrc.load_council(self.root, c["council_id"])
        gates_dir = os.path.join(self.root, "gates")
        before = sorted(os.listdir(gates_dir)) if os.path.isdir(gates_dir) else []
        res = cwg.record_escalation_gate(
            self.root, c["council_id"],
            {"outcome": "operator_required", "phase": "plan"}, {})
        self.assertIsNone(res)
        after = sorted(os.listdir(gates_dir)) if os.path.isdir(gates_dir) else []
        self.assertEqual(before, after)

    def test_heal_matches_canonical_subject_aliases(self):
        # A council bound to one alias of a packet-derived id must heal when
        # the caller uses another alias of the same canonical subject.
        c = cwrc.create_council(self.root, thread_id=self.thread,
                                work_item_id="in_progress:pkt-77",
                                phase="plan", min_rounds=2, max_rounds=2,
                                approved_scope="scope")
        for n in (1, 2):
            cwrc.save_round(self.root, c, fabricated_round(n))
            c = cwrc.load_council(self.root, c["council_id"])
        outcome = cwrc.evaluate(c, cwrc.load_rounds(self.root, c["council_id"]))
        cwrc.save_outcome(self.root, c["council_id"], outcome)
        healed = cwg.heal_escalation_gates(self.root, "packet:pkt-77")
        self.assertTrue(healed["ok"])
        self.assertEqual(len(healed["healed"]), 1)
        self.assertEqual(len(cwg.load_gates(self.root, "packet:pkt-77")), 1)

    def test_distinct_failure_details_each_get_a_durable_notice(self):
        # Same invariant, different detail (round 1 repaired, round 2 now
        # corrupt) must post a SECOND notice, not silently collide on the key.
        c = fabricate_capped_council(self.root, self.thread, self.wid)
        p1 = cwrc._round_path(self.root, c["council_id"], 1)
        p2 = cwrc._round_path(self.root, c["council_id"], 2)
        saved = open(p1, encoding="utf-8").read()
        os.remove(p1)
        cww.progress_work_item(self.root, self.wid, "claude", "x")
        with open(p1, "w", encoding="utf-8") as fh:
            fh.write(saved)
        os.remove(p2)
        cww.progress_work_item(self.root, self.wid, "claude", "x")
        self.assertEqual(len(gate_notices(self.root, self.wid)), 2)

    def test_dry_run_reconcile_on_gated_item_still_refuses_read_only(self):
        c = fabricate_capped_council(self.root, self.thread, self.wid)
        healed = cwg.heal_escalation_gates(self.root, self.wid)
        self.assertTrue(healed["ok"])
        msgs_before = len(cwm.read_messages(self.root))
        rf = os.path.join(self.root, "dr.json")
        with open(rf, "w", encoding="utf-8") as fh:
            json.dump({"accepted_findings": ["x"], "rejected_findings": [],
                       "required_plan_changes": [], "revised_plan": ["y"],
                       "unresolved_blockers": [], "resolutions": [],
                       "ready_to_proceed": True, "summary": "s"}, fh)
        res, code = run(lambda a: ucw._council(a, "plan"),
                        **council_args(self.root, self.thread, self.wid,
                                       stage="reconcile", dry_run=True,
                                       council_id=c["council_id"],
                                       reconciliation_file=rf))
        self.assertEqual(code, ucw.EXIT_GATE)
        self.assertEqual(res["error"], "unresolved_gate")
        self.assertEqual(len(cwm.read_messages(self.root)), msgs_before)

    def test_injected_fault_and_maintenance_passthrough(self):
        fabricate_capped_council(self.root, self.thread, self.wid)
        orig = cwg.ensure_gate
        cwg.ensure_gate = lambda *a, **k: (_ for _ in ()).throw(OSError("boom"))
        try:
            res, code = run(ucw.cmd_progress, queue_root=self.root,
                            work_item_id=self.wid, message="x",
                            message_file=None)
            self.assertEqual(code, ucw.EXIT_RUNTIME)
            self.assertEqual(res["error"], "gate_creation_failed")
        finally:
            cwg.ensure_gate = orig
        def mip(*a, **k):
            raise cwl.MaintenanceInProgress()
        cwg.ensure_gate = mip
        try:
            res, code = run(ucw.cmd_progress, queue_root=self.root,
                            work_item_id=self.wid, message="x",
                            message_file=None)
            self.assertEqual(res["error"], "maintenance_in_progress")
            self.assertNotEqual(res.get("error_code"), "gate_creation_failed")
        finally:
            cwg.ensure_gate = orig


class PerSurfaceHealingTests(DriveHelpers):
    """6b/6c: every first-class surface independently heals (or fails closed);
    a new council can never bypass the failure window."""

    def window(self):
        root = queue("ccg_win_", self)
        wid, thread = make_item(root)
        cid = self.failure_window(root, thread, wid)
        return root, thread, wid, cid

    def assert_healed_and_refused(self, root, wid, cid, res, code):
        self.assertEqual(code, ucw.EXIT_GATE)
        self.assertEqual(res["error"], "unresolved_gate")
        gates = gates_for(root, wid)
        self.assertEqual(len(gates), 1)
        self.assertEqual(gates[0]["council_id"], cid)

    def test_reconcile_heals_and_refuses(self):
        root, thread, wid, cid = self.window()
        recon = {"accepted_findings": ["x"], "rejected_findings": [],
                 "required_plan_changes": [], "revised_plan": ["y"],
                 "unresolved_blockers": [], "resolutions": [],
                 "ready_to_proceed": True, "summary": "attempted reconcile"}
        rf = os.path.join(root, "r.json")
        with open(rf, "w", encoding="utf-8") as fh:
            json.dump(recon, fh)
        res, code = run(lambda a: ucw._council(a, "plan"),
                        **council_args(root, thread, wid, stage="reconcile",
                                       council_id=cid, reconciliation_file=rf))
        self.assert_healed_and_refused(root, wid, cid, res, code)

    def test_review_on_existing_council_heals_and_refuses(self):
        root, thread, wid, cid = self.window()
        res, code = run(lambda a: ucw._council(a, "plan"),
                        **council_args(root, thread, wid, council_id=cid))
        self.assert_healed_and_refused(root, wid, cid, res, code)

    def test_progress_heals_and_refuses(self):
        root, thread, wid, cid = self.window()
        res, code = run(ucw.cmd_progress, queue_root=root, work_item_id=wid,
                        message="note", message_file=None)
        self.assert_healed_and_refused(root, wid, cid, res, code)

    def test_complete_heals_and_refuses(self):
        root, thread, wid, cid = self.window()
        res, code = run(ucw.cmd_complete, queue_root=root, work_item_id=wid,
                        packet_id=None, result="done", result_file=None)
        self.assert_healed_and_refused(root, wid, cid, res, code)

    def test_verify_heals_and_refuses(self):
        root, thread, wid, cid = self.window()
        res, code = run(lambda a: ucw._council(a, "verify"),
                        **council_args(root, thread, wid, phase="verify"))
        self.assert_healed_and_refused(root, wid, cid, res, code)

    def test_new_council_cannot_bypass_the_window(self):
        root, thread, wid, cid = self.window()
        before = sorted(os.listdir(cwrc.councils_root(root)))
        orig = cwrc.run_round
        def never(*a, **k):
            raise AssertionError("reviewers must not be invoked")
        cwrc.run_round = never
        self.addCleanup(setattr, cwrc, "run_round", orig)
        res, code = run(lambda a: ucw._council(a, "plan"),
                        **council_args(root, thread, wid))
        self.assert_healed_and_refused(root, wid, cid, res, code)
        self.assertEqual(before, sorted(os.listdir(cwrc.councils_root(root))))

    def test_close_heals_then_existing_authority_semantics_apply(self):
        root, thread, wid, cid = self.window()
        pre_auth = operator_msg(root, thread, wid,
                                "I authorize closure of " + wid +
                                ". Please close it.")
        res, code = run(ucw.cmd_close, queue_root=root, work_item_id=wid,
                        operator_message_id=pre_auth["message_id"],
                        operator="OPERATOR-0001", reason="cancelling")
        # The heal materialized the gate; the pre-heal authority now correctly
        # predates it, so close refuses under EXISTING semantics.
        self.assertEqual(code, ucw.EXIT_USAGE)
        self.assertIn("predates", res["error"])
        self.assertEqual(len(gates_for(root, wid)), 1)
        time.sleep(0.02)
        post_auth = operator_msg(root, thread, wid,
                                 "I authorize closure of " + wid +
                                 ". Close it as cancelled.")
        res, code = run(ucw.cmd_close, queue_root=root, work_item_id=wid,
                        operator_message_id=post_auth["message_id"],
                        operator="OPERATOR-0001", reason="cancelling")
        self.assertEqual(code, ucw.EXIT_OK)
        self.assertEqual(res["status"], "closed_by_operator")
        gates = gates_for(root, wid)
        self.assertEqual(len(gates), 1)
        self.assertEqual(gates[0]["disposition"], "closed_unresolved")

    def test_library_surfaces_heal_and_refuse(self):
        for fn, extra in ((cww.claim_work_item, ()),
                          (cww.progress_work_item, ("note",)),
                          (cww.respond_work_item, ("answer",))):
            root = queue("ccg_lib_", self)
            wid, thread = make_item(root)
            cid = self.failure_window(root, thread, wid)
            res = fn(root, wid, "claude", *extra)
            self.assertFalse(res["ok"])
            self.assertEqual(res["error"], "unresolved_gate")
            gates = gates_for(root, wid)
            self.assertEqual(len(gates), 1)
            self.assertEqual(gates[0]["council_id"], cid)

    def test_operator_actor_heals_but_is_not_blocked(self):
        root, thread, wid, cid = self.window()
        res = cww.progress_work_item(root, wid, "OPERATOR-0001",
                                     "operator note", role="operator")
        self.assertTrue(res["ok"])
        self.assertEqual(len(gates_for(root, wid)), 1)

    def test_fault_retained_every_surface_fails_closed(self):
        root = queue("ccg_fault_", self)
        wid, thread = make_item(root)
        self.failure_window(root, thread, wid, keep_fault=True)
        res, code = run(ucw.cmd_progress, queue_root=root, work_item_id=wid,
                        message="x", message_file=None)
        self.assertEqual(code, ucw.EXIT_RUNTIME)
        self.assertEqual(res["error"], "gate_creation_failed")
        lib = cww.respond_work_item(root, wid, "claude", "x")
        self.assertFalse(lib["ok"])
        self.assertEqual(lib["error"], "gate_creation_failed")
        self.assertEqual(server.wi_status_code(lib), 500)
        self.assertEqual(len(gates_for(root, wid)), 0)


class EnforcementAndAuthorityTests(DriveHelpers):
    """7-11: gated refusals, authoritative subject, grant-proceed unchanged."""

    def setUp(self):
        self.root = queue("ccg_enf_", self)
        self.wid, self.thread = make_item(self.root)
        self.cid, _, code = self.drive_capped(self.root, self.thread, self.wid)
        self.assertEqual(code, ucw.EXIT_OPERATOR)

    def test_council_id_only_reconcile_refuses_via_durable_subject(self):
        rf = os.path.join(self.root, "r.json")
        with open(rf, "w", encoding="utf-8") as fh:
            json.dump({"accepted_findings": ["x"], "rejected_findings": [],
                       "required_plan_changes": [], "revised_plan": ["y"],
                       "unresolved_blockers": [], "resolutions": [],
                       "ready_to_proceed": True, "summary": "s"}, fh)
        res, code = run(lambda a: ucw._council(a, "plan"),
                        **council_args(self.root, self.thread, None,
                                       stage="reconcile", council_id=self.cid,
                                       reconciliation_file=rf))
        self.assertEqual(code, ucw.EXIT_GATE)
        self.assertEqual(res["error"], "unresolved_gate")

    def test_wrong_work_item_id_fails_closed_before_any_mutation(self):
        rounds_before = sorted(os.listdir(cwrc.council_dir(self.root, self.cid)))
        gates_before = json.dumps(gates_for(self.root, self.wid), sort_keys=True)
        for stage in ("reconcile", "review"):
            kw = council_args(self.root, self.thread, "message:msg-other",
                              stage=stage, council_id=self.cid)
            if stage == "reconcile":
                rf = os.path.join(self.root, "r2.json")
                with open(rf, "w", encoding="utf-8") as fh:
                    json.dump({"accepted_findings": ["x"],
                               "rejected_findings": [],
                               "required_plan_changes": [],
                               "revised_plan": ["y"],
                               "unresolved_blockers": [], "resolutions": [],
                               "ready_to_proceed": True, "summary": "s"}, fh)
                kw["reconciliation_file"] = rf
            res, code = run(lambda a: ucw._council(a, "plan"), **kw)
            self.assertEqual(code, ucw.EXIT_USAGE)
            self.assertEqual(res["error"], "work_item_id_mismatch")
            self.assertEqual(res["council_bound"], self.wid)
        self.assertEqual(rounds_before,
                         sorted(os.listdir(cwrc.council_dir(self.root, self.cid))))
        self.assertEqual(gates_before,
                         json.dumps(gates_for(self.root, self.wid), sort_keys=True))

    def test_grant_proceed_still_works_after_a_valid_gate_exists(self):
        time.sleep(0.02)
        auth = operator_msg(self.root, self.thread, self.wid,
                            "I authorize proceeding on work item " + self.wid +
                            " (council " + self.cid + ").")
        res, code = run(ucw.cmd_grant_proceed, queue_root=self.root,
                        work_item_id=self.wid,
                        operator_message_id=auth["message_id"])
        self.assertEqual(code, ucw.EXIT_OK)
        self.assertEqual(res["status"], "resolved")
        res, code = run(ucw.cmd_progress, queue_root=self.root,
                        work_item_id=self.wid, message="resumed",
                        message_file=None)
        self.assertEqual(code, ucw.EXIT_OK)

    def test_grant_proceed_in_the_failure_window_has_nothing_to_grant(self):
        root = queue("ccg_gp_", self)
        wid, thread = make_item(root)
        self.failure_window(root, thread, wid)
        auth = operator_msg(root, thread, wid,
                            "I authorize proceeding on work item " + wid + ".")
        res, code = run(ucw.cmd_grant_proceed, queue_root=root,
                        work_item_id=wid,
                        operator_message_id=auth["message_id"])
        self.assertEqual(code, ucw.EXIT_USAGE)
        self.assertEqual(res["error"], "no_unresolved_gate")
        self.assertEqual(len(gates_for(root, wid)), 0)


class PreservationTests(DriveHelpers):
    """12/13/13c + payload preservation + status mapping."""

    def setUp(self):
        self.root = queue("ccg_pres_", self)

    def test_unrelated_records_untouched(self):
        wid1, thread1 = make_item(self.root, "Item one.")
        wid2, thread2 = make_item(self.root, "Item two.")
        cwg.create_gate(self.root, wid2, "cw-council-x", "plan",
                        "operator_required")
        before = json.dumps(gates_for(self.root, wid2), sort_keys=True)
        cid, res, code = self.drive_capped(self.root, thread1, wid1)
        self.assertEqual(code, ucw.EXIT_OPERATOR)
        self.assertEqual(before,
                         json.dumps(gates_for(self.root, wid2), sort_keys=True))

    def test_unknown_id_no_gate_no_write(self):
        wid, thread = make_item(self.root)
        def snapshot():
            files = []
            for dirpath, _, names in os.walk(self.root):
                for n in sorted(names):
                    p = os.path.join(dirpath, n)
                    files.append((os.path.relpath(p, self.root),
                                  os.path.getsize(p)))
            return files
        before = snapshot()
        for fn, extra in ((cww.claim_work_item, ()),
                          (cww.progress_work_item, ("x",)),
                          (cww.respond_work_item, ("x",))):
            res = fn(self.root, "message:msg-19990101T000000000000", "claude",
                     *extra)
            self.assertFalse(res["ok"])
            self.assertEqual(res["error"], "work_item_not_found")
        self.assertEqual(before, snapshot())

    def test_full_payload_preserved_at_both_commands(self):
        wid, thread = make_item(self.root)
        c = fabricate_capped_council(self.root, thread, wid)
        opath = os.path.join(cwrc.council_dir(self.root, c["council_id"]),
                             "outcome.json")
        with open(opath, "w", encoding="utf-8") as fh:
            fh.write("{broken")
        lib = cww.progress_work_item(self.root, wid, "claude", "x")
        res, code = run(ucw.cmd_progress, queue_root=self.root,
                        work_item_id=wid, message="x", message_file=None)
        self.assertEqual(code, ucw.EXIT_RUNTIME)
        for key in ("error", "error_code", "error_class", "invariant",
                    "council_id", "work_item_id", "phase", "outcome", "detail"):
            self.assertIn(key, res)
            self.assertEqual(res[key], lib[key])
        res2, code2 = run(ucw.cmd_complete, queue_root=self.root,
                          work_item_id=wid, packet_id=None, result="r",
                          result_file=None)
        self.assertEqual(code2, ucw.EXIT_RUNTIME)
        self.assertEqual(res2["invariant"], lib["invariant"])

    def test_wi_status_code_mappings(self):
        self.assertEqual(server.wi_status_code({"ok": True}), 200)
        self.assertEqual(server.wi_status_code(
            {"ok": False, "error": "work_item_not_found"}), 404)
        self.assertEqual(server.wi_status_code(
            {"ok": False, "error": "maintenance_in_progress",
             "error_code": "maintenance_in_progress"}), 503)
        self.assertEqual(server.wi_status_code(
            {"ok": False, "error": "maintenance_in_progress"}), 503)
        self.assertEqual(server.wi_status_code(
            {"ok": False, "error": "gate_creation_failed",
             "error_code": "gate_creation_failed"}), 500)
        self.assertEqual(server.wi_status_code(
            {"ok": False, "error": "unresolved_gate"}), 400)


class ExclusiveHolderTests(unittest.TestCase):
    """Lock-holder semantics for the live T5 procedure: maintenance surfaces
    while held; cleanup is guaranteed even when the test body fails."""

    def setUp(self):
        self.root = queue("ccg_hold_", self)
        self.wid, self.thread = make_item(self.root)

    def test_maintenance_while_exclusive_held_then_released(self):
        flag = cwl.acquire_exclusive(self.root, "test-hold")
        try:
            res = cww.progress_work_item(self.root, self.wid, "claude", "x")
            self.assertFalse(res["ok"])
            self.assertEqual(res["error"], "maintenance_in_progress")
            self.assertEqual(server.wi_status_code(res), 503)
        finally:
            cwl.release_exclusive(self.root, flag["opid"], flag["nonce"])
        res = cww.progress_work_item(self.root, self.wid, "claude", "after")
        self.assertTrue(res["ok"])

    def test_maintenance_during_heal_surfaces_through_gate_block(self):
        # With a QUALIFYING escalated council present, the healing path itself
        # hits the writer token and _gate_block's declared translation fires.
        fabricate_capped_council(self.root, self.thread, self.wid)
        flag = cwl.acquire_exclusive(self.root, "test-hold-2")
        try:
            res = cww.progress_work_item(self.root, self.wid, "claude", "x")
            self.assertFalse(res["ok"])
            self.assertEqual(res["error"], "maintenance_in_progress")
            self.assertEqual(server.wi_status_code(res), 503)
            self.assertEqual(len(gates_for(self.root, self.wid)), 0)
        finally:
            cwl.release_exclusive(self.root, flag["opid"], flag["nonce"])
        # After the window: the same call heals the gate and refuses.
        res = cww.progress_work_item(self.root, self.wid, "claude", "after")
        self.assertFalse(res["ok"])
        self.assertEqual(res["error"], "unresolved_gate")
        self.assertEqual(len(gates_for(self.root, self.wid)), 1)

    def test_holder_subprocess_cleanup_even_on_failure(self):
        ready = os.path.join(os.path.dirname(self.root), "lock.ready")
        holder = subprocess.Popen(
            [sys.executable, "-m", "tests.test_capped_council_gate",
             "--hold-exclusive", self.root, "--seconds", "3",
             "--ready-file", ready],
            cwd=REPO_ROOT, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        failed = None
        try:
            deadline = time.monotonic() + 15
            while not os.path.isfile(ready):
                if time.monotonic() > deadline:
                    raise AssertionError("holder never signaled ready")
                time.sleep(0.1)
            res = cww.progress_work_item(self.root, self.wid, "claude", "x")
            self.assertEqual(res.get("error"), "maintenance_in_progress")
            raise RuntimeError("simulated mid-test failure")
        except RuntimeError as exc:
            failed = exc  # the cleanup below must still run and succeed
        finally:
            try:
                holder.wait(timeout=30)
            except subprocess.TimeoutExpired:
                holder.kill()
                holder.wait()
        self.assertIsNotNone(failed)
        self.assertFalse(os.path.isfile(ready))
        res = cww.progress_work_item(self.root, self.wid, "claude", "after")
        self.assertTrue(res["ok"], res)


# --------------------------------------------------------------------------- #
# Live-verification fixture entries (T1/T5 of the deployment procedure)
# --------------------------------------------------------------------------- #

def _live_fixture(queue_root):
    root, *_ = server.resolve_queue(queue_root)
    manifest = {}
    for key in ("item1", "item2", "item3"):
        wid, thread = make_item(root, "live fixture " + key)
        c = fabricate_capped_council(root, thread, wid)
        manifest[key] = {"work_item_id": wid,
                         "council_id": c["council_id"],
                         "thread_id": thread}
    path = os.path.join(os.path.dirname(os.path.abspath(root)), "manifest.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(manifest, fh, indent=2)
    print(json.dumps({"ok": True, "manifest": path}))
    return 0


def _hold_exclusive(queue_root, seconds, ready_file):
    root, *_ = server.resolve_queue(queue_root)
    flag = cwl.acquire_exclusive(root, "gatefix-live-hold")
    with open(ready_file, "w", encoding="utf-8") as fh:
        fh.write("held")
    try:
        time.sleep(float(seconds))
    finally:
        cwl.release_exclusive(root, flag["opid"], flag["nonce"])
        try:
            os.remove(ready_file)
        except OSError:
            pass
    return 0


def _main(argv):
    if "--live-fixture" in argv:
        return _live_fixture(argv[argv.index("--live-fixture") + 1])
    if "--hold-exclusive" in argv:
        root = argv[argv.index("--hold-exclusive") + 1]
        seconds = argv[argv.index("--seconds") + 1] if "--seconds" in argv else "30"
        ready = argv[argv.index("--ready-file") + 1]
        return _hold_exclusive(root, seconds, ready)
    unittest.main()
    return 0


if __name__ == "__main__":
    sys.exit(_main(sys.argv[1:]))
