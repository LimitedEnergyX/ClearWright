"""Tests for Command Center Queue Hygiene and Current-State UX.

The deterministic presentation logic lives in tools/clearwright_work.py and is
covered here with real assertions (not token presence): the total presentation
-state function, last-meaningful-activity selection, runner honesty, record-class
separation, current-only filtering, search, sort, counts, and durable-input
byte immutability. Irreducibly-client behaviors (audio, mute, localStorage
seen-set, hash-route navigation, no-write-on-action) are wired-checked here and
verified end-to-end by the pre-merge browser acceptance run.

Everything under test is PRESENTATION-ONLY and additive: no durable governance
record is created, moved, closed, reclassified, or altered.
"""
import hashlib
import os
import re
import shutil
import sys
import tempfile
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APP_DIR = os.path.join(REPO_ROOT, "apps", "control-plane")
TOOLS_DIR = os.path.join(REPO_ROOT, "tools")
STATIC = os.path.join(APP_DIR, "static")
DOCS = os.path.join(REPO_ROOT, "docs")

sys.path.insert(0, APP_DIR)
sys.path.insert(0, TOOLS_DIR)
import server  # noqa: E402
import clearwright_work as cww  # noqa: E402
import clearwright_message as cwm  # noqa: E402

NOW = "2026-07-16T00:00:00.000000Z"
T_RECENT = "2026-07-15T23:50:00.000000Z"   # 10 min ago  (<= RUNNING_WINDOW)
T_HOURS = "2026-07-15T20:00:00.000000Z"    # 4 h ago     (<= STALE_WINDOW)
T_OLD = "2026-07-14T20:00:00.000000Z"      # 28 h ago    (>  STALE_WINDOW)


def read(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def queue(prefix, tc):
    base = tempfile.mkdtemp(prefix=prefix)
    tc.addCleanup(shutil.rmtree, base, ignore_errors=True)
    root, *_ = server.resolve_queue(base)
    return root


def request(root, message, **kw):
    return server.do_message(root, dict(
        {"actor": "OPERATOR-0001", "role": "operator", "source": "operator-ui",
         "direction": "inbound", "intent": "request", "message": message}, **kw))


def chat(root, message, **kw):
    return server.do_message(root, dict(
        {"actor": "OPERATOR-0001", "role": "operator", "source": "operator-ui",
         "direction": "inbound", "intent": "chat", "message": message}, **kw))


def sig(**kw):
    base = {"status": "open", "kind": "message", "needs_operator": False,
            "blocked": False, "awaiting_operator": False, "claimed": False,
            "active_runner": False, "last_activity_at": T_RECENT,
            "created_at": T_RECENT}
    base.update(kw)
    return base


def bmsg(mid, at, **kw):
    d = {"message_id": mid, "at": at}
    d.update(kw)
    return d


def plus_seconds(iso, secs):
    from datetime import timedelta
    dt = cww._parse_iso(iso) + timedelta(seconds=secs)
    return dt.strftime("%Y-%m-%dT%H:%M:%S.%f") + "Z"


# --------------------------------------------------------------------------- #
# 1 & 3. Last meaningful activity replaces creation age; labeled fallback;
#        total, order-independent selection with exact timestamp + source id.
# --------------------------------------------------------------------------- #
class LastActivityTests(unittest.TestCase):

    def test_newest_wins_not_creation(self):
        # An older completion + a newer progress -> progress wins (recency).
        bound = [bmsg("m-old", T_HOURS, closure="done"),
                 bmsg("m-new", T_RECENT, status="posted")]
        at, event, sid = cww.last_activity(bound, [], None)
        self.assertEqual(at, T_RECENT)
        self.assertEqual(event, "progress")
        self.assertEqual(sid, "m-new")

    def test_created_fallback_when_no_activity(self):
        at, event, sid = cww.last_activity([], [], None)
        self.assertIsNone(at)
        self.assertEqual(event, "created")
        self.assertIsNone(sid)

    def test_exact_timestamp_and_source_id_exposed(self):
        bound = [bmsg("m-1", T_RECENT, status="claimed")]
        at, event, sid = cww.last_activity(bound, [], None)
        self.assertEqual((at, event, sid), (T_RECENT, "claim", "m-1"))

    def test_tie_break_across_classes_by_precedence(self):
        # Same timestamp: completion (class 1) beats progress (class 5).
        bound = [bmsg("m-prog", T_RECENT, status="posted"),
                 bmsg("m-done", T_RECENT, closure="done")]
        _, event, sid = cww.last_activity(bound, [], None)
        self.assertEqual(event, "completion")
        self.assertEqual(sid, "m-done")

    def test_tie_break_within_class_is_order_independent(self):
        # Same timestamp AND same class -> lexicographically smallest id wins,
        # regardless of input order (the r3 total-order fix).
        a = bmsg("m-aaa", T_RECENT, status="posted")
        b = bmsg("m-bbb", T_RECENT, status="posted")
        _, _, sid1 = cww.last_activity([a, b], [], None)
        _, _, sid2 = cww.last_activity([b, a], [], None)
        self.assertEqual(sid1, "m-aaa")
        self.assertEqual(sid2, "m-aaa")

    def test_council_and_gate_contribute(self):
        councils = [{"council_id": "cw-c1", "created_at": T_RECENT}]
        at, event, sid = cww.last_activity([], councils, None)
        self.assertEqual((at, event, sid), (T_RECENT, "council", "cw-c1"))
        gate = {"gate_id": "gate-1", "created_at": T_RECENT}
        at2, event2, sid2 = cww.last_activity([], [], gate)
        self.assertEqual((at2, event2, sid2), (T_RECENT, "gate", "gate-1"))


class TimestampOrderingTests(unittest.TestCase):
    """Ordering is on PARSED instants, not raw strings (verify-council fix): a
    no-fractional 'Z' string must not sort after an earlier fractional one, and
    equivalent Z vs +00:00 encodings are the same instant."""

    def test_later_no_fractional_wins_over_earlier_fractional(self):
        bound = [bmsg("m-early", "2026-07-16T00:00:00.500000Z", status="posted"),
                 bmsg("m-late", "2026-07-16T00:00:01Z", status="posted")]
        at, _event, sid = cww.last_activity(bound, [], None)
        self.assertEqual(sid, "m-late")           # 00:00:01 > 00:00:00.5
        self.assertEqual(at, "2026-07-16T00:00:01Z")   # original string preserved

    def test_equal_instant_across_encodings_is_order_independent(self):
        z = bmsg("m-a", "2026-07-16T00:00:00.000000Z", status="posted")
        off = bmsg("m-b", "2026-07-16T00:00:00+00:00", status="posted")
        _, _, sid1 = cww.last_activity([z, off], [], None)
        _, _, sid2 = cww.last_activity([off, z], [], None)
        self.assertEqual(sid1, "m-a")             # same instant -> id tie-break
        self.assertEqual(sid2, "m-a")


class LatestClaimTests(unittest.TestCase):
    """claimed_by/claimed_at come from the LATEST claim by the total order, not
    the first record encountered (verify-council fix: input-order independence)."""

    def test_latest_claim_deterministic_under_reversed_input(self):
        older = bmsg("c-old", "2026-07-16T00:00:00.000000Z", status="claimed", actor="alice")
        newer = bmsg("c-new", "2026-07-16T00:05:00.000000Z", status="claimed", actor="bob")
        noise = bmsg("m-x", "2026-07-16T00:03:00.000000Z", status="posted")
        c1 = cww._latest_claim([older, newer, noise])
        c2 = cww._latest_claim([noise, newer, older])
        self.assertEqual(c1["message_id"], "c-new")
        self.assertEqual(c2["message_id"], "c-new")
        self.assertEqual(c1["actor"], "bob")

    def test_equal_timestamp_claims_tie_break_by_id(self):
        a = bmsg("c-aaa", "2026-07-16T00:00:00.000000Z", status="claimed")
        b = bmsg("c-bbb", "2026-07-16T00:00:00.000000Z", status="claimed")
        self.assertEqual(cww._latest_claim([a, b])["message_id"], "c-aaa")
        self.assertEqual(cww._latest_claim([b, a])["message_id"], "c-aaa")

    def test_no_claim_returns_none(self):
        self.assertIsNone(cww._latest_claim(
            [bmsg("m", "2026-07-16T00:00:00.000000Z", status="posted")]))


# --------------------------------------------------------------------------- #
# 5, 6, 8. Deterministic total presentation-state precedence, R1, claimed !=
#          running, terminal-first, awaiting-operator ordering, band boundaries.
# --------------------------------------------------------------------------- #
class PresentationStateTests(unittest.TestCase):

    def ps(self, **kw):
        return cww.presentation_state(sig(**kw), now=NOW)

    def test_superseded_terminal_first_even_with_needs_operator(self):
        self.assertEqual(self.ps(status="superseded", needs_operator=True),
                         "superseded")

    def test_done_recent_vs_historical(self):
        self.assertEqual(self.ps(status="done", last_activity_at=T_HOURS),
                         "recently_completed")
        self.assertEqual(self.ps(status="done", last_activity_at=T_OLD),
                         "historical")

    def test_operator_required_visible_regardless_of_age(self):
        # R1: an old, heartbeat-less operator-required item is needs_operator,
        # never stale.
        self.assertEqual(
            self.ps(status="operator_required", needs_operator=True,
                    last_activity_at=T_OLD, created_at=T_OLD),
            "needs_operator")

    def test_blocked_non_terminal(self):
        self.assertEqual(self.ps(blocked=True), "blocked")

    def test_awaiting_operator_before_claimed_branch(self):
        # A claimed item awaiting an operator reply is waiting_on_operator,
        # not waiting_on_claude (Codex r2 fix).
        self.assertEqual(
            self.ps(claimed=True, awaiting_operator=True,
                    last_activity_at=T_RECENT),
            "waiting_on_operator")

    def test_claimed_not_running_without_active_runner(self):
        self.assertEqual(
            self.ps(claimed=True, active_runner=False, last_activity_at=T_HOURS),
            "waiting_on_claude")

    def test_claimed_running_with_active_runner(self):
        self.assertEqual(
            self.ps(claimed=True, active_runner=True), "running")

    def test_claimed_bands_reachable(self):
        self.assertEqual(self.ps(claimed=True, active_runner=True), "running")
        self.assertEqual(
            self.ps(claimed=True, last_activity_at=T_HOURS), "waiting_on_claude")
        self.assertEqual(
            self.ps(claimed=True, last_activity_at=T_OLD, created_at=T_OLD),
            "stale")

    def test_open_recent_vs_stale(self):
        self.assertEqual(self.ps(last_activity_at=T_HOURS), "waiting_on_claude")
        self.assertEqual(
            self.ps(last_activity_at=T_OLD, created_at=T_OLD), "stale")


# --------------------------------------------------------------------------- #
# 4, 7. Runner honesty -- claimed is not running; missing-heartbeat states.
# --------------------------------------------------------------------------- #
class RunnerStateTests(unittest.TestCase):

    def rs(self, **kw):
        base = dict(claimed=True, claim_at=T_RECENT, active_runner=False,
                    in_council=False, awaiting_operator=False, has_gate=False,
                    status="claimed", now_dt=cww._now_dt(NOW))
        base.update(kw)
        return cww.runner_state(base["claimed"], base["claim_at"],
                                base["active_runner"], base["in_council"],
                                base["awaiting_operator"], base["has_gate"],
                                base["status"], base["now_dt"])

    def test_unowned_when_not_claimed(self):
        self.assertEqual(self.rs(claimed=False), "unowned")

    def test_active_runner(self):
        self.assertEqual(self.rs(active_runner=True), "active_runner")

    def test_claimed_idle_is_not_running(self):
        self.assertEqual(self.rs(active_runner=False, claim_at=T_RECENT),
                         "claimed_idle")

    def test_stale_or_no_heartbeat_when_claim_old(self):
        self.assertEqual(self.rs(active_runner=False, claim_at=T_OLD),
                         "stale_or_no_heartbeat")

    def test_waiting_on_council_and_operator(self):
        self.assertEqual(self.rs(in_council=True), "waiting_on_council")
        self.assertEqual(self.rs(has_gate=True), "waiting_on_operator")


# --------------------------------------------------------------------------- #
# 9. Record-class separation -- structural only.
# --------------------------------------------------------------------------- #
class RecordClassTests(unittest.TestCase):

    def test_chat(self):
        self.assertEqual(cww.classify_record({"intent": "chat"}, False, "open"),
                         "chat")

    def test_governed_work_when_lifecycle(self):
        self.assertEqual(
            cww.classify_record({"role": "operator", "source": "operator-ui"},
                                True, "claimed"),
            "governed_work")

    def test_governed_work_when_request_intent(self):
        self.assertEqual(
            cww.classify_record({"intent": "request", "role": "operator",
                                 "source": "operator-ui"}, False, "open"),
            "governed_work")

    def test_authority_operator_ui_no_lifecycle(self):
        self.assertEqual(
            cww.classify_record({"role": "operator", "source": "operator-ui"},
                                False, "open"),
            "authority")

    def test_note_residual(self):
        self.assertEqual(
            cww.classify_record({"role": "agent", "source": "cli"}, False,
                                "done"),
            "note")


# --------------------------------------------------------------------------- #
# 2, 4, 11, 12, 13, 14. queue_view: current-only default, filters, search, sort.
# --------------------------------------------------------------------------- #
class QueueViewTests(unittest.TestCase):

    def items(self):
        return [
            {"work_item_id": "message:a", "kind": "message",
             "record_class": "governed_work", "presentation_state": "needs_operator",
             "title": "Alpha gate", "last_activity_at": T_HOURS},
            {"work_item_id": "message:b", "kind": "message",
             "record_class": "governed_work", "presentation_state": "running",
             "title": "Bravo run", "last_activity_at": T_RECENT},
            {"work_item_id": "message:c", "kind": "message",
             "record_class": "governed_work", "presentation_state": "stale",
             "title": "Charlie stale", "last_activity_at": T_OLD},
            {"work_item_id": "message:d", "kind": "message",
             "record_class": "governed_work", "presentation_state": "historical",
             "title": "Delta old", "last_activity_at": T_OLD},
            {"work_item_id": "message:e", "kind": "message",
             "record_class": "governed_work", "presentation_state": "superseded",
             "title": "Echo gone", "last_activity_at": T_RECENT},
            {"work_item_id": "message:f", "kind": "message",
             "record_class": "authority", "presentation_state": "needs_operator",
             "title": "Foxtrot authority", "last_activity_at": T_RECENT},
        ]

    def test_current_excludes_stale_historical_superseded_and_messages(self):
        ids = [it["work_item_id"] for it in cww.queue_view(self.items(), "current")]
        self.assertIn("message:a", ids)     # needs_operator
        self.assertIn("message:b", ids)     # running
        self.assertNotIn("message:c", ids)  # stale
        self.assertNotIn("message:d", ids)  # historical
        self.assertNotIn("message:e", ids)  # superseded (always excluded)
        self.assertNotIn("message:f", ids)  # authority (non-governed)

    def test_operator_required_visible_in_current(self):
        ids = [it["work_item_id"] for it in cww.queue_view(self.items(), "current")]
        self.assertEqual(ids[0], "message:a")  # attention-first sort

    def test_all_filter_includes_everything(self):
        ids = [it["work_item_id"] for it in cww.queue_view(self.items(), "all")]
        for wid in ("message:a", "message:c", "message:d", "message:e", "message:f"):
            self.assertIn(wid, ids)

    def test_each_filter_mode(self):
        for mode, wid in (("needs_attention", "message:a"), ("running", "message:b"),
                          ("stale", "message:c"), ("recently_completed", None)):
            got = [it["work_item_id"] for it in cww.queue_view(self.items(), mode)]
            if wid is None:
                self.assertEqual(got, [])
            else:
                self.assertIn(wid, got)

    def test_search_by_title_and_work_item_id(self):
        self.assertEqual(
            [it["work_item_id"] for it in cww.queue_view(self.items(), "all", "bravo")],
            ["message:b"])
        self.assertEqual(
            [it["work_item_id"] for it in cww.queue_view(self.items(), "all", "message:c")],
            ["message:c"])

    def test_attention_first_sort_order(self):
        order = [it["presentation_state"]
                 for it in cww.queue_view(self.items(), "all")]
        # needs_operator (0) before running (1) before stale (5)/historical (7).
        self.assertLess(order.index("needs_operator"), order.index("running"))
        self.assertLess(order.index("running"), order.index("stale"))

    def test_counts(self):
        counts = cww.attention_counts(self.items())
        self.assertEqual(counts["needs_operator"], 1)  # authority one excluded
        self.assertEqual(counts["running"], 1)
        self.assertEqual(counts["stale"], 1)


# --------------------------------------------------------------------------- #
# 9, 10, 26. Integration: messages vs work; chat excluded; message-scoped.
# --------------------------------------------------------------------------- #
class IntegrationTests(unittest.TestCase):

    def setUp(self):
        self.root = queue("cw_cc_", self)

    def test_request_derives_governed_work_with_fields(self):
        request(self.root, "Please review the repo.")
        items = cww.derive_work_items(self.root, now=NOW)
        self.assertEqual(len(items), 1)
        it = items[0]
        self.assertEqual(it["record_class"], "governed_work")
        self.assertIn("presentation_state", it)
        self.assertIn("runner_state", it)
        self.assertEqual(it["last_activity_event"], "created")  # no activity yet

    def test_chat_excluded_from_work(self):
        chat(self.root, "just chatting")
        self.assertEqual(cww.derive_work_items(self.root, now=NOW), [])

    def test_messages_preserved_in_message_tree(self):
        chat(self.root, "kept in the tree")
        self.assertEqual(len(cwm.read_messages(self.root)), 1)

    def test_claim_sets_last_activity_and_runner(self):
        request(self.root, "Do the thing.")
        wid = cww.derive_work_items(self.root, now=NOW)[0]["work_item_id"]
        cww.claim_work_item(self.root, wid, "claude")
        it = cww.derive_work_items(self.root, now=NOW)[0]
        self.assertEqual(it["claimed_by"], "claude")
        self.assertIn(it["last_activity_event"], ("claim", "progress"))
        self.assertNotEqual(it["runner_state"], "unowned")

    def test_active_run_injection(self):
        request(self.root, "Injected run.")
        wid = cww.derive_work_items(self.root, now=NOW)[0]["work_item_id"]
        cww.claim_work_item(self.root, wid, "claude")
        # With an old `now`, recency alone would not mark it running; the
        # injected snapshot does -- and is the only thing that can.
        far = "2027-01-01T00:00:00.000000Z"
        idle = cww.derive_work_items(self.root, now=far)[0]
        self.assertNotEqual(idle["presentation_state"], "running")
        run = cww.derive_work_items(self.root, active_run={wid: True}, now=far)[0]
        self.assertEqual(run["presentation_state"], "running")

    def test_same_thread_items_independent(self):
        first = request(self.root, "First actionable.")
        request(self.root, "Second actionable.", thread_id=first["thread_id"])
        items = cww.derive_work_items(self.root, now=NOW)
        self.assertEqual(len(items), 2)
        self.assertEqual(len({it["work_item_id"] for it in items}), 2)

    # End-to-end wiring of the record -> signal -> presentation_state mapping
    # (not just the pure function): a fresh claim is NOT running, recent
    # non-claim activity IS running, and an old claim is stale.
    def test_fresh_claim_is_not_running_end_to_end(self):
        request(self.root, "Claim only.")
        wid = cww.derive_work_items(self.root, now=NOW)[0]["work_item_id"]
        res = cww.claim_work_item(self.root, wid, "claude")
        just_after = plus_seconds(res["message"]["at"], 60)   # within RUNNING_WINDOW
        it = cww.derive_work_items(self.root, now=just_after)[0]
        self.assertNotEqual(it["presentation_state"], "running")  # claim != running
        self.assertEqual(it["presentation_state"], "waiting_on_claude")
        self.assertEqual(it["runner_state"], "claimed_idle")

    def test_running_requires_recent_nonclaim_activity(self):
        request(self.root, "With progress.")
        wid = cww.derive_work_items(self.root, now=NOW)[0]["work_item_id"]
        cww.claim_work_item(self.root, wid, "claude")
        res = cww.progress_work_item(self.root, wid, "claude", "working on it")
        just_after = plus_seconds(res["message"]["at"], 30)
        it = cww.derive_work_items(self.root, now=just_after)[0]
        self.assertEqual(it["presentation_state"], "running")
        self.assertEqual(it["runner_state"], "active_runner")
        self.assertEqual(it["last_activity_event"], "progress")

    def test_old_claim_is_stale_end_to_end(self):
        request(self.root, "Old claim.")
        wid = cww.derive_work_items(self.root, now=NOW)[0]["work_item_id"]
        cww.claim_work_item(self.root, wid, "claude")
        far = "2027-01-01T00:00:00.000000Z"
        it = cww.derive_work_items(self.root, now=far)[0]
        self.assertEqual(it["presentation_state"], "stale")


# --------------------------------------------------------------------------- #
# 25, 27, 28. Durable-input immutability, gate idempotency, unrelated records.
# --------------------------------------------------------------------------- #
class ImmutabilityTests(unittest.TestCase):

    def setUp(self):
        self.root = queue("cw_imm_", self)

    def _snapshot(self):
        digest = {}
        for base, _dirs, files in os.walk(self.root):
            for name in files:
                path = os.path.join(base, name)
                with open(path, "rb") as fh:
                    digest[os.path.relpath(path, self.root)] = \
                        hashlib.sha256(fh.read()).hexdigest()
        return digest

    def test_derivation_and_view_do_not_mutate_durable_records(self):
        request(self.root, "Alpha.")
        chat(self.root, "Beta chat.")
        request(self.root, "Gamma.")
        before = self._snapshot()
        for _ in range(3):
            items = cww.derive_work_items(self.root, include="all", now=NOW)
            cww.queue_view(items, "current", "a")
            cww.queue_view(items, "all", "")
            cww.attention_counts(items)
            cww.integrity_warnings(self.root)
        after = self._snapshot()
        self.assertEqual(before, after)

    def test_include_all_superset_and_additive_only(self):
        request(self.root, "Only item.")
        default = cww.derive_work_items(self.root, now=NOW)
        allitems = cww.derive_work_items(self.root, include="all", now=NOW)
        self.assertGreaterEqual(len(allitems), len(default))
        # The canonical fields are unchanged; only additive fields were added.
        for it in default:
            for key in ("work_item_id", "kind", "status", "next_action"):
                self.assertIn(key, it)


# --------------------------------------------------------------------------- #
# 3, 12, 13, 15, 16, 17, 19, 20, 21, 22, 23, 24. UI wiring tokens (behavior is
#     proven by the pre-merge browser acceptance run; these guard the wiring).
# --------------------------------------------------------------------------- #
class UiWiringTests(unittest.TestCase):

    def setUp(self):
        self.html = read(os.path.join(STATIC, "index.html"))
        self.appjs = read(os.path.join(STATIC, "app.js"))
        self.css = read(os.path.join(STATIC, "style.css"))

    def test_top_bar_attention_counts_present(self):
        for tok in ("attention-bar", "att-count-running", "att-count-operator",
                    "att-count-blocked", "att-count-stale"):
            self.assertIn(tok, self.html)

    def test_filters_and_search_present(self):
        for mode in ("current", "needs_attention", "running", "blocked",
                     "stale", "recently_completed", "all"):
            self.assertIn('data-filter="' + mode + '"', self.html)
        self.assertIn('id="queue-search"', self.html)

    def test_activity_age_uses_last_activity_with_created_fallback(self):
        self.assertIn("last_activity_at", self.appjs)
        self.assertIn("created · ", self.appjs)   # labeled creation fallback
        self.assertIn("function activityAge", self.appjs)

    def test_presentation_state_and_record_class_drive_rendering(self):
        self.assertIn("presentation_state", self.appjs)
        self.assertIn("record_class", self.appjs)
        self.assertIn("function isGoverned", self.appjs)

    def test_audio_one_shot_and_mute(self):
        self.assertIn("AudioContext", self.appjs)
        self.assertIn("function playAttentionDing", self.appjs)
        self.assertIn("ATT_MUTE_KEY", self.appjs)
        self.assertIn('id="att-mute"', self.html)

    def test_seen_set_dedup(self):
        self.assertIn("ATT_SEEN_KEY", self.appjs)
        self.assertIn("attMarkSeen", self.appjs)

    def test_navigation_and_highlight(self):
        self.assertIn("#work=", self.appjs)
        self.assertIn("function highlightMessage", self.appjs)
        self.assertIn("data-message-id", self.appjs)

    def test_context_actions_present(self):
        self.assertIn("function actionsForState", self.appjs)

    def test_summary_before_raw_records(self):
        # The human summary block is emitted before the collapsed raw records.
        self.assertIn("th-summary", self.appjs)
        self.assertIn("th-raw", self.appjs)
        self.assertLess(self.appjs.index('"th-summary"'),
                        self.appjs.index('th-raw'))

    def test_opening_alert_navigates_only_no_write(self):
        # The attention-open and navigation paths must not issue any write.
        m = re.search(r"function openAttention\([\s\S]*?\n\}", self.appjs)
        self.assertIsNotNone(m)
        body = m.group(0)
        for writer in ("postJSON", "fetch(", '"POST"', "method: \"POST\""):
            self.assertNotIn(writer, body)
        nav = re.search(r"function navigateToWorkItem\([\s\S]*?\n\}", self.appjs)
        self.assertNotIn("postJSON", nav.group(0))


class NamingGateTests(unittest.TestCase):

    def test_no_private_or_retired_terms_in_new_code(self):
        _wr = "w" + "rit"
        retired = re.compile("|".join([r"\b" + _wr + r"\b", "vol" + "tex"]), re.I)
        private = re.compile("|".join([r"\b" + "pl" + "ex" + r"\b",
                                       "d:" + re.escape("\\") + "dev"]), re.I)
        for path in (os.path.join(TOOLS_DIR, "clearwright_work.py"),
                     os.path.join(STATIC, "app.js"),
                     os.path.join(STATIC, "index.html"),
                     os.path.join(STATIC, "style.css"),
                     os.path.abspath(__file__)):
            text = read(path)
            with self.subTest(file=os.path.relpath(path, REPO_ROOT)):
                self.assertIsNone(retired.search(text))
                self.assertIsNone(private.search(text))


if __name__ == "__main__":
    unittest.main()
