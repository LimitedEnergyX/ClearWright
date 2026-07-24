"""P1c tests: surfacing, operator-message-bound dispositions (all five binding
checks + replay refusal), the promotion gate, illegal-transition refusal, DEFERRED
requirements, and spec rendering (tools/clearwright_alf_review.py, packet s16/s18)."""
import json
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "tools"))
import clearwright_alf as alf  # noqa: E402
import clearwright_alf_synth as syn  # noqa: E402
import clearwright_alf_review as rev  # noqa: E402

FUTURE = "2099-01-01T00:00:00.000000Z"
PAST = "2000-01-01T00:00:00.000000Z"


class ReviewTest(unittest.TestCase):
    def setUp(self):
        self.q = tempfile.mkdtemp(prefix="alf-rv-")
        alf.ensure_layout(self.q)

    def tearDown(self):
        shutil.rmtree(self.q, ignore_errors=True)

    def _promotable(self, status="PRIORITIZED", **kw):
        f = {"title": "t", "status": status, "subsystem": "cli",
             "failure_class": "lifecycle_failure", "blast_radius": "single_subsystem",
             "priority_tier": 1, "priority_score": 43,
             "permanent_resolution": "add preflight",
             "objective_acceptance_criteria": "blocks bad dispatch",
             "required_regression_tests": "dispatch preflight cases",
             "root_cause_confidence": "0.90", "dependencies": [], "blockers": [],
             "evidence_references": [{"ref": "council-outcome:c1", "sha256": "a" * 64,
                                      "role": "observed_occurrence",
                                      "archived_location": None}]}
        f.update(kw)
        return syn.create_finding(self.q, f)

    def _msg(self, mid, text, at=FUTURE, role="operator", direction="inbound"):
        comm = os.path.join(self.q, "communications")
        os.makedirs(comm, exist_ok=True)
        with open(os.path.join(comm, mid + ".json"), "w", encoding="utf-8") as fh:
            json.dump({"message_id": mid, "role": role, "direction": direction,
                       "at": at, "message": text}, fh)
        return mid

    def test_surface_priortized_to_review(self):
        eid = self._promotable()
        self.assertTrue(rev.surface_for_review(self.q, eid)["surfaced"])
        self.assertEqual(syn.load_finding(self.q, eid)["status"], "OPERATOR_REVIEW")
        self.assertIn("surfaced_at", syn.load_finding(self.q, eid))

    def test_approve_for_planning_happy_path(self):
        eid = self._promotable()
        rev.surface_for_review(self.q, eid)
        mid = self._msg("msg-approve1", "Approve {} for planning".format(eid))
        res = rev.dispose(self.q, eid, "APPROVED_FOR_PLANNING", mid)
        self.assertTrue(res["disposed"])
        head = syn.load_finding(self.q, eid)
        self.assertEqual(head["status"], "APPROVED_FOR_PLANNING")
        self.assertTrue(syn.head_equals_rebuild(self.q, eid))
        self.assertTrue(alf.verify_hashes(self.q)["ok"])

    def test_promotion_gate_blocks_incomplete(self):
        eid = self._promotable(permanent_resolution="")
        rev.surface_for_review(self.q, eid)
        mid = self._msg("msg-approve2", "Approve {} for planning".format(eid))
        with self.assertRaises(alf.AlfError):
            rev.dispose(self.q, eid, "APPROVED_FOR_PLANNING", mid)

    def test_illegal_transition_refused(self):
        eid = self._promotable(status="TRIAGED")
        mid = self._msg("msg-x", "Approve {} for planning".format(eid))
        with self.assertRaises(alf.AlfError):
            rev.dispose(self.q, eid, "APPROVED_FOR_PLANNING", mid)

    def test_binding_missing_message(self):
        eid = self._promotable()
        rev.surface_for_review(self.q, eid)
        with self.assertRaises(alf.AlfError):
            rev.dispose(self.q, eid, "REJECTED", "msg-does-not-exist")

    def test_binding_non_operator_refused(self):
        eid = self._promotable()
        rev.surface_for_review(self.q, eid)
        mid = self._msg("msg-w", "Reject {}".format(eid), role="worker")
        with self.assertRaises(alf.AlfError):
            rev.dispose(self.q, eid, "REJECTED", mid)

    def test_binding_must_postdate_revision(self):
        eid = self._promotable()
        rev.surface_for_review(self.q, eid)
        mid = self._msg("msg-old", "Reject {}".format(eid), at=PAST)
        with self.assertRaises(alf.AlfError):
            rev.dispose(self.q, eid, "REJECTED", mid)

    def test_binding_must_name_entry(self):
        eid = self._promotable()
        rev.surface_for_review(self.q, eid)
        mid = self._msg("msg-noname", "Reject something else")
        with self.assertRaises(alf.AlfError):
            rev.dispose(self.q, eid, "REJECTED", mid)

    def test_binding_replay_refused(self):
        a = self._promotable()
        b = self._promotable()
        rev.surface_for_review(self.q, a)
        rev.surface_for_review(self.q, b)
        mid = self._msg("msg-both", "Reject {} and {}".format(a, b))
        rev.dispose(self.q, a, "REJECTED", mid)  # consumes mid
        with self.assertRaises(alf.AlfError):
            rev.dispose(self.q, b, "REJECTED", mid)  # replay refused

    def test_deferred_requires_reason_and_date(self):
        eid = self._promotable()
        rev.surface_for_review(self.q, eid)
        mid = self._msg("msg-def", "Defer {}".format(eid))
        with self.assertRaises(alf.AlfError):
            rev.dispose(self.q, eid, "DEFERRED", mid)
        mid2 = self._msg("msg-def2", "Defer {}".format(eid))
        res = rev.dispose(self.q, eid, "DEFERRED", mid2,
                          deferral_reason="later", review_date="2026-09-01")
        self.assertTrue(res["disposed"])

    def test_render_spec(self):
        eid = self._promotable(problem_statement="dispatch waste")
        out = rev.render_spec(self.q, eid)
        self.assertTrue(os.path.exists(out["spec_path"]))
        with open(out["spec_path"], encoding="utf-8") as fh:
            body = fh.read()
        self.assertIn(eid, body)
        self.assertIn("ALF posts nothing and grants nothing", body)


if __name__ == "__main__":
    unittest.main()
