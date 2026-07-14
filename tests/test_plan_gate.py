"""Plan-gate enforcement (commit 1 of the hardening PR).

A plan/incident council that ends operator_required creates a durable unresolved
gate on the work item; while it is unresolved the governed workflow is fail-
closed (progress, council, complete refuse with EXIT_GATE); proceeding requires a
durable inbound operator message created AFTER the gate that names the work item
or council and authorizes proceeding; the original request never qualifies.
"""
import contextlib
import io
import json
import os
import shutil
import sys
import tempfile
import time
import unittest
from argparse import Namespace

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(REPO_ROOT, "apps", "control-plane"))
sys.path.insert(0, os.path.join(REPO_ROOT, "tools"))
import server  # noqa: E402
import clearwright_message as cwm  # noqa: E402
import clearwright_gate as cwg  # noqa: E402
import clearwright_use_cw as ucw  # noqa: E402
import clearwright_work as cww  # noqa: E402


def run(func, **kw):
    kw.setdefault("json", True)
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        code = func(Namespace(**kw))
    return json.loads(buf.getvalue().strip().splitlines()[-1]), code


def operator_msg(root, thread_id, work_item_id, body, at=None):
    msg = cwm.build_message("OPERATOR-0001", body, role="operator",
                            thread_id=thread_id, direction="inbound",
                            status="posted", source="operator-console",
                            work_item_id=work_item_id)
    if at:
        msg["at"] = at
    cwm.write_message(root, msg)
    return msg


class CanonicalSubjectTests(unittest.TestCase):
    def test_message_subject_is_itself(self):
        self.assertEqual(cwg.canonical_subject("message:msg-1"), "message:msg-1")

    def test_all_packet_aliases_resolve_to_one_subject(self):
        for alias in ("packet:cw-9:cta", "in_progress:cw-9", "rfi:cw-9",
                      "packet:cw-9", "cw-9"):
            self.assertEqual(cwg.canonical_subject(alias), "packet:cw-9", alias)

    def test_gate_filename_is_full_sha256_and_asserts_subject(self):
        base = tempfile.mkdtemp(prefix="gate_")
        self.addCleanup(shutil.rmtree, base, ignore_errors=True)
        cwg.create_gate(base, "message:msg-x", "cw-council-1", "plan",
                        "operator_required")
        files = os.listdir(os.path.join(base, "gate" + "s"))
        self.assertEqual(len(files), 1)
        self.assertEqual(len(files[0]), 64 + len(".json"))
        with open(os.path.join(base, "gates", files[0]), encoding="utf-8") as fh:
            rec = json.load(fh)
        self.assertEqual(rec["subject"], "message:msg-x")


class TokenAndPhraseTests(unittest.TestCase):
    def test_id_token_boundary(self):
        self.assertTrue(cwg.id_token_present("proceed on message:msg-1 now", "message:msg-1"))
        self.assertFalse(cwg.id_token_present("message:msg-12 is different", "message:msg-1"))

    def test_intent_safe_phrase_rejects_negation_and_quotes(self):
        self.assertTrue(cwg.phrase_authorizes("I authorize proceeding.", cwg.PROCEED_PHRASES))
        self.assertFalse(cwg.phrase_authorizes("do not authorize proceeding", cwg.PROCEED_PHRASES))
        self.assertFalse(cwg.phrase_authorizes('the phrase "authorize proceeding" was quoted',
                                               cwg.PROCEED_PHRASES))


class GateLifecycleTests(unittest.TestCase):
    def setUp(self):
        base = tempfile.mkdtemp(prefix="gate_wf_")
        self.addCleanup(shutil.rmtree, base, ignore_errors=True)
        self.root, *_ = server.resolve_queue(base)
        # A real actionable work item so find_work_item resolves.
        self.op = operator_msg(self.root, None, None, "Do the governed task X.")
        self.thread = self.op["thread_id"]
        self.wid = "message:" + self.op["message_id"]

    def _gate(self):
        return cwg.create_gate(self.root, self.wid, "cw-council-9", "plan",
                               "operator_required")

    def test_progress_and_complete_refuse_while_gated(self):
        self._gate()
        res, code = run(ucw.cmd_progress, queue_root=self.root,
                        work_item_id=self.wid, message="a note", message_file=None)
        self.assertEqual(code, ucw.EXIT_GATE)
        self.assertEqual(res["error"], "unresolved_gate")
        res, code = run(ucw.cmd_complete, queue_root=self.root, work_item_id=self.wid,
                        packet_id=None, result="done", result_file=None)
        self.assertEqual(code, ucw.EXIT_GATE)

    def test_agent_respond_refused_operator_permitted(self):
        self._gate()
        blocked = cww.respond_work_item(self.root, self.wid, "claude", "hi")
        self.assertEqual(blocked.get("error"), "unresolved_gate")
        ok = cww.respond_work_item(self.root, self.wid, "OPERATOR-0001", "operator note")
        self.assertTrue(ok.get("ok"))

    def test_pre_gate_and_wrong_target_authority_rejected(self):
        # A message posted BEFORE the gate never qualifies (the original request).
        early = operator_msg(self.root, self.thread, self.wid,
                             "I authorize proceeding on " + self.wid,
                             at="2000-01-01T00:00:00.000000Z")
        gate = self._gate()
        res, code = run(ucw.cmd_grant_proceed, queue_root=self.root,
                        work_item_id=self.wid,
                        operator_message_id=early["message_id"])
        self.assertEqual(code, ucw.EXIT_AUTHORITY)
        self.assertEqual(res["error"], "authority_not_after_gate")
        # A post-gate message that does not name the target is rejected.
        time.sleep(0.01)
        wrong = operator_msg(self.root, self.thread, self.wid,
                             "I authorize proceeding on something else")
        res, code = run(ucw.cmd_grant_proceed, queue_root=self.root,
                        work_item_id=self.wid,
                        operator_message_id=wrong["message_id"])
        self.assertEqual(code, ucw.EXIT_AUTHORITY)
        self.assertEqual(res["error"], "authority_missing_target_id")

    def test_valid_post_gate_grant_resolves_and_unblocks(self):
        gate = self._gate()
        time.sleep(0.01)
        auth = operator_msg(self.root, self.thread, self.wid,
                            "I authorize proceeding on work item " + self.wid +
                            " (council cw-council-9).")
        res, code = run(ucw.cmd_grant_proceed, queue_root=self.root,
                        work_item_id=self.wid,
                        operator_message_id=auth["message_id"])
        self.assertEqual(code, ucw.EXIT_OK)
        self.assertEqual(res["status"], "resolved")
        self.assertIsNone(cwg.active_gate(self.root, self.wid))
        # Progress now works.
        res, code = run(ucw.cmd_progress, queue_root=self.root,
                        work_item_id=self.wid, message="resumed", message_file=None)
        self.assertEqual(code, ucw.EXIT_OK)

    def test_later_gate_requires_new_authority(self):
        gate1 = self._gate()
        time.sleep(0.01)
        auth1 = operator_msg(self.root, self.thread, self.wid,
                             "I authorize proceeding on " + self.wid)
        run(ucw.cmd_grant_proceed, queue_root=self.root, work_item_id=self.wid,
            operator_message_id=auth1["message_id"])
        # A second gate: the earlier authority must NOT resolve it.
        time.sleep(0.01)
        gate2 = self._gate()
        self.assertNotEqual(gate1["gate_id"], gate2["gate_id"])
        res, code = run(ucw.cmd_grant_proceed, queue_root=self.root,
                        work_item_id=self.wid,
                        operator_message_id=auth1["message_id"])
        self.assertEqual(code, ucw.EXIT_AUTHORITY)
        self.assertEqual(res["error"], "authority_not_after_gate")

    def test_single_active_gate_per_subject(self):
        self._gate()
        with self.assertRaises(cwg.GateError):
            self._gate()

    def test_canonical_summary_reflects_gate_and_authority(self):
        gate = self._gate()
        summary = ucw.build_canonical_summary(self.root, self.wid, "operator_required")
        self.assertIsNotNone(summary["gate"])
        self.assertEqual(summary["gate"]["gate_id"], gate["gate_id"])
        self.assertEqual(summary["gate"]["disposition"], "unresolved")
        self.assertIsNone(summary["gate"]["authority"])

        time.sleep(0.01)
        auth = operator_msg(self.root, self.thread, self.wid,
                            "I authorize proceeding on " + self.wid)
        run(ucw.cmd_grant_proceed, queue_root=self.root, work_item_id=self.wid,
            operator_message_id=auth["message_id"])
        summary2 = ucw.build_canonical_summary(self.root, self.wid, "in_progress")
        self.assertEqual(summary2["gate"]["disposition"], "resolved")
        self.assertEqual(summary2["gate"]["authority"]["message_id"], auth["message_id"])


if __name__ == "__main__":
    unittest.main()
