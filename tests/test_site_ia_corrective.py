"""Focused regression coverage for the corrective Local Council site IA pass.

Operator visual acceptance of the PR #35 site work found the information-
architecture scope only partially implemented. This suite pins the corrected
scope, one class per requirement area:

  1.  Compact phase stepper replaces the workflow canvas.
  2.  Persistent selected-task header binds every primary panel.
  3.  Compact grouped work queue (Attention / Active / Recent / Archived).
  4.  Three-region desktop layout (queue | task workspace | operator panel).
  5.  Unified Work page replaces Conversations and Active Run.
  6-10. Work tabs: Overview, Conversation, Councils, Evidence, Audit.
  11. Incoming clearance collapses to a compact line when empty.
  12. Clearance lanes live under History, not the command center.
  13. Unified single-ledger History with filters and row detail.
  14. Navigation: Command Center / Work / History + Attention count/filter.
  15. Tool Log hidden by default behind a developer control / shortcut.
  16. Responsive behavior without nested horizontal scrolling.

Server-side, /api/task-state, /api/ledger, and /api/archive-index are the
read-only models behind the stepper, header, and History; their derivations
are exercised against a real temp queue (no mocks, no network).
"""
import os
import shutil
import sys
import tempfile
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APP_DIR = os.path.join(REPO_ROOT, "apps", "control-plane")
TOOLS_DIR = os.path.join(REPO_ROOT, "tools")
STATIC = os.path.join(APP_DIR, "static")

sys.path.insert(0, APP_DIR)
sys.path.insert(0, TOOLS_DIR)
import server  # noqa: E402
import clearwright_work as cww  # noqa: E402


def read(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def operator_queue(prefix, tc):
    base = tempfile.mkdtemp(prefix=prefix)
    tc.addCleanup(shutil.rmtree, base, ignore_errors=True)
    # Nest the queue so archive_root() (queue-parent + "archive") stays
    # inside the temp dir instead of the shared system temp parent.
    root, *_ = server.resolve_queue(os.path.join(base, "active"))
    return root


class SourceTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.html = read(os.path.join(STATIC, "index.html"))
        cls.appjs = read(os.path.join(STATIC, "app.js"))
        cls.css = read(os.path.join(STATIC, "style.css"))


class PhaseStepperTests(SourceTestCase):
    """Req 1: the stepper replaces the canvas and animates only the selected
    task's current phase; operator-required renders amber and static."""

    def test_stepper_exists_and_canvas_is_gone(self):
        self.assertIn('id="phase-stepper"', self.html)
        self.assertIn("function renderPhaseStepper", self.appjs)
        self.assertIn("PHASE_LABELS", self.appjs)
        for gone in ("renderWorkflow", "GRAPH_NODES", "GRAPH_EDGES",
                     'id="workflow-canvas"'):
            self.assertNotIn(gone, self.appjs)
            self.assertNotIn(gone, self.html)

    def test_six_phases_in_order(self):
        self.assertEqual(server.TASK_PHASES,
                         ("request", "plan_review", "authority", "execute",
                          "verify", "complete"))

    def test_state_classes_current_attention_future(self):
        for cls_token in ("step-current", "step-attention", "step-future",
                          "step-done"):
            self.assertIn(cls_token, self.appjs)
            self.assertIn("." + cls_token, self.css)

    def test_only_current_phase_animates(self):
        # The pulse animation binds to the current step's dot only; the
        # attention (operator-required) state is amber and deliberately static.
        self.assertIn(".step-current .step-dot", self.css)
        attention_idx = self.css.index(".step-attention .step-dot")
        attention_rule = self.css[attention_idx:self.css.index("}", attention_idx)]
        self.assertNotIn("animation:", attention_rule)

    def test_historical_tasks_never_animate_the_display(self):
        # Global pulse activity from another thread must not drive the
        # selected task's stepper/inspector.
        self.assertIn("p.source_thread_id === selectedConvThread", self.appjs)
        self.assertIn("activity on another task does not drive this display",
                      self.appjs)


class TaskHeaderTests(SourceTestCase):
    """Req 2: a persistent selected-task header; every panel binds to the
    same selection."""

    def test_header_renders_identity_and_state(self):
        self.assertIn('id="task-header"', self.html)
        self.assertIn("function renderTaskHeader", self.appjs)
        for field in ("work_item_id", "status", "phase", "current_council",
                      "gate", "claim", "next_action"):
            self.assertIn(field, self.appjs)

    def test_single_shared_selection(self):
        # One selection variable feeds task state, conversation, and queue
        # highlighting; there is no per-view selection to drift.
        self.assertIn("selectedConvThread", self.appjs)
        self.assertNotIn("selectedRunThread", self.appjs)
        self.assertIn("/api/task-state?thread_id=", self.appjs)


class WorkQueueTests(SourceTestCase):
    """Req 3: compact grouped queue rows (title, status, phase, age,
    attention reason); the full request text lives in Overview."""

    def test_groups_and_row_fields(self):
        self.assertIn("function buildQueueGroups", self.appjs)
        for group in ("attention", "active", "recent", "archived"):
            self.assertIn(group, self.appjs)
        self.assertIn("relativeAge", self.appjs)
        self.assertIn("queuePhaseHint", self.appjs)

    def test_attention_rows_carry_a_reason(self):
        self.assertIn("CTA decision required", self.appjs)
        self.assertIn("RFI awaiting clarification", self.appjs)
        self.assertIn("council escalated: operator required", self.appjs)

    def test_recent_group_loads_on_every_view(self):
        # Live post-archive regression: conversations feed the queue's Recent
        # group, but they only loaded when the Work view opened, so a fresh
        # Command Center load showed no Recent group at all.
        wire_at = self.appjs.index("function wire()")
        boot = self.appjs[wire_at:self.appjs.index("setInterval(refresh,", wire_at)]
        self.assertIn("loadConversations()", boot)
        self.assertIn('currentView !== "work"', self.appjs)

    def test_superseded_escalation_never_flags_a_thread_forever(self):
        # Live-acceptance regression: two plan councils escalated
        # operator_required, the gate was resolved, and a third council
        # agreed -- yet the queue kept the thread under Attention while the
        # header/stepper said Execute. Only the LATEST council per thread may
        # drive the Attention grouping and the Authority phase hint.
        self.assertIn("function latestCouncilFor", self.appjs)
        gated_at = self.appjs.index("const gatedThreads")
        gated_expr = self.appjs[gated_at:self.appjs.index(";", gated_at)]
        self.assertIn("latestCouncilFor", gated_expr)
        hint_at = self.appjs.index("function queuePhaseHint")
        hint_body = self.appjs[hint_at:self.appjs.index("\n}", hint_at)]
        self.assertIn("latestCouncilFor", hint_body)
        self.assertNotIn('councils.some((c) => c.outcome === "operator_required")',
                         hint_body)


class ThreeRegionLayoutTests(SourceTestCase):
    """Req 4: left queue ~260-300px, center workspace, right operator panel
    ~340-400px as a real three-region desktop grid."""

    def test_regions_exist(self):
        for rid in ('id="queue-region"', 'id="task-region"',
                    'id="operator-region"'):
            self.assertIn(rid, self.html)

    def test_grid_widths_match_the_spec(self):
        self.assertIn("minmax(260px, 300px)", self.css)
        self.assertIn("minmax(340px, 400px)", self.css)


class UnifiedWorkPageTests(SourceTestCase):
    """Req 5: one Work page with Overview | Conversation | Councils |
    Evidence | Audit; Conversations and Active Run are gone as top-level
    paths."""

    def test_tabs_present(self):
        for tab in ("buildOverviewTab", "buildConversationTab",
                    "buildCouncilsTab", "buildEvidenceTab", "buildAuditTab"):
            self.assertIn("function " + tab, self.appjs)

    def test_old_top_level_views_removed(self):
        for gone in ('id="conversations-view"', 'id="active-run-view"',
                     'id="conversations-btn"', 'id="active-run-btn"'):
            self.assertIn(gone not in self.html, (True,),
                          msg=gone + " should be removed")
        for gone in ("function openConversations", "function openActiveRun",
                     "function renderRunList", "function renderConvList"):
            self.assertNotIn(gone, self.appjs)


class OverviewTabTests(SourceTestCase):
    """Req 6: Overview shows status/phase, next action, approved scope, plan
    summary, blockers, latest reconciliation, completion criteria."""

    def test_overview_fields(self):
        for token in ("approved_scope", "latest_reconciliation", "blockers",
                      "completion_criteria", "next_action"):
            self.assertIn(token, self.appjs)


class ConversationTabTests(SourceTestCase):
    """Req 7: readable timeline, fixed composer, participant filters, unread
    marker, jump to latest, visible destination."""

    def test_conversation_ergonomics(self):
        self.assertIn("conv-filterbar", self.appjs)
        self.assertIn("data-participant", self.appjs)
        self.assertIn("conv-unread-divider", self.appjs)
        self.assertIn("New since you last read this thread", self.appjs)
        self.assertIn("data-jump-latest", self.appjs)
        self.assertIn("function placeWorkComposer", self.appjs)
        # The composer keeps its explicit destination line.
        self.assertIn("conv-target-hint", self.html)

    def test_composer_template_stays_hidden_until_placed(self):
        # Live-acceptance regression: .composer's display:flex defeated the
        # [hidden] attribute, so the Work composer template leaked into the
        # bottom of the Command Center view.
        self.assertIn(".composer[hidden]", self.css)

    def test_composer_docks_sticky_at_the_viewport_bottom(self):
        # Live-acceptance regression: the composer sat below the fold after a
        # long timeline. "Fixed composer" means docked sticky so replying
        # never requires scrolling past the conversation.
        self.assertIn('id="work-composer-dock"', self.html)
        dock_at = self.css.index("#work-composer-dock")
        dock_rule = self.css[dock_at:self.css.index("}", dock_at)]
        self.assertIn("sticky", dock_rule)
        self.assertIn('getElementById("work-composer-dock")', self.appjs)


class CouncilsTabTests(SourceTestCase):
    """Req 8: grouped by council and round; verdict + reconciliation first;
    telemetry demoted under Technical details."""

    def test_council_grouping_and_technical_details(self):
        self.assertIn("council-group", self.appjs)
        self.assertIn("council-round", self.appjs)
        self.assertIn("Technical details", self.appjs)
        self.assertIn("council-tech", self.appjs)


class EvidenceTabTests(SourceTestCase):
    """Req 9: artifacts with hashes, diffs, tests, CI, browser evidence."""

    def test_evidence_sources(self):
        self.assertIn("ev-artifacts", self.appjs)
        self.assertIn("sha256", self.appjs)
        for kind in ("diff", "test", "ci", "browser"):
            self.assertIn(kind, self.appjs.lower())


class AuditTabTests(SourceTestCase):
    """Req 10: state transitions, gates, authority records, invocations,
    archived status."""

    def test_audit_sections(self):
        self.assertIn("audit-authority", self.appjs)
        self.assertIn("invocations", self.appjs)
        self.assertIn("ARCHIVED (resolved via the archive index)", self.appjs)
        self.assertIn("gates", self.appjs)


class ClearanceZeroStateTests(SourceTestCase):
    """Req 11-12: incoming clearance collapses to a compact line at zero;
    the clearance lanes live under History, not the command center."""

    def test_compact_zero_state(self):
        self.assertIn("clearance-card", self.html)
        self.assertIn("is-empty", self.appjs)

    def test_lanes_moved_under_history(self):
        lanes_at = self.html.index('id="lanes-panel"')
        history_at = self.html.index('id="history-view"')
        history_end = self.html.index("</section>", history_at)
        self.assertTrue(history_at < lanes_at < history_end,
                        "clearance lanes must render inside the History view")
        self.assertIn('id="board"', self.html)


class HistoryLedgerTests(SourceTestCase):
    """Req 13: one unified ledger (time, type, work item, actor, event,
    status) with filters and row-click detail."""

    def test_filters_and_detail(self):
        for fid in ("lf-scope", "lf-type", "lf-actor", "lf-status", "lf-date",
                    "lf-workitem", "lf-council", "lf-text"):
            self.assertIn('id="' + fid + '"', self.html)
        self.assertIn('id="ledger-body"', self.html)
        self.assertIn('id="ledger-detail"', self.html)
        self.assertIn("function openLedgerDetail", self.appjs)
        self.assertIn("ledgerRowMatches", self.appjs)
        self.assertIn("/api/ledger", self.appjs)


class NavigationTests(SourceTestCase):
    """Req 14: Command Center / Work / History navigation; Attention is a
    count/filter, not a page."""

    def test_nav_and_attention_chip(self):
        for nav in ("nav-command", "nav-work", "nav-history"):
            self.assertIn('id="' + nav + '"', self.html)
        self.assertIn("function showView", self.appjs)
        self.assertIn('id="attention-chip"', self.html)
        self.assertIn("queueAttentionOnly", self.appjs)


class ToolLogTests(SourceTestCase):
    """Req 15: the Tool Log is hidden by default, reachable via a developer
    control and Ctrl+Shift+L."""

    def test_hidden_by_default_with_dev_toggle(self):
        footer_at = self.html.index('id="activity-footer"')
        tag = self.html[self.html.rindex("<", 0, footer_at):
                        self.html.index(">", footer_at)]
        self.assertIn("hidden", tag)
        self.assertIn('id="tool-log-toggle"', self.html)
        self.assertIn("function toggleToolLog", self.appjs)
        self.assertIn('e.key.toLowerCase() === "l" && e.ctrlKey && e.shiftKey',
                      self.appjs)


class ResponsiveTests(SourceTestCase):
    """Req 16: desktop-width usage with clean stacking and no nested
    horizontal scrolling."""

    def test_breakpoints_stack_regions(self):
        self.assertIn("@media (max-width: 1180px)", self.css)
        self.assertIn("@media (max-width: 820px)", self.css)

    def test_page_never_scrolls_horizontally(self):
        # Live-acceptance regression: the off-canvas drawer and edge tooltips
        # gave the page a horizontal scrollbar.
        body_at = self.css.index("body {")
        body_rule = self.css[body_at:self.css.index("}", body_at)]
        self.assertIn("overflow-x: hidden", body_rule)

    def test_wide_tables_scroll_in_their_own_container(self):
        # The ledger table scrolls inside its wrap; the page body never
        # scrolls horizontally.
        self.assertIn("ledger-table-wrap", self.html)
        wrap_at = self.css.index(".ledger-table-wrap")
        wrap_rule = self.css[wrap_at:self.css.index("}", wrap_at)]
        self.assertIn("overflow", wrap_rule)


class TaskStateModelTests(unittest.TestCase):
    """/api/task-state derivation against a real temp queue."""

    def setUp(self):
        self.root = operator_queue("ia_ts_", self)

    def test_request_phase_and_next_action(self):
        server.do_message(self.root, {"actor": "OPERATOR-0001",
                                      "role": "operator",
                                      "source": "operator-ui",
                                      "message": "Please review the docs"})
        tid = server.build_active_run(self.root)["thread_id"]
        ts = server.build_task_state(self.root, tid)
        self.assertTrue(ts["found"])
        self.assertEqual(ts["phase"], "request")
        self.assertFalse(ts["phase_attention"])
        self.assertEqual(ts["phases"], list(server.TASK_PHASES))
        self.assertIn("Claim the work item", ts["next_action"])

    def test_claimed_next_action_is_plan_council(self):
        server.do_message(self.root, {"actor": "OPERATOR-0001",
                                      "role": "operator",
                                      "source": "operator-ui",
                                      "message": "Actionable request"})
        wid = cww.derive_work_items(self.root)[0]["work_item_id"]
        cww.claim_work_item(self.root, wid, "claude")
        tid = server.build_active_run(self.root)["thread_id"]
        ts = server.build_task_state(self.root, tid)
        self.assertEqual(ts["phase"], "request")
        self.assertTrue(ts["claim"]["claimed"])
        self.assertEqual(ts["claim"]["claimed_by"], "claude")
        self.assertIn("plan council", ts["next_action"])

    def test_unknown_thread_reports_not_found(self):
        ts = server.build_task_state(self.root, "thr-does-not-exist")
        self.assertFalse(ts.get("found"))


class LedgerModelTests(unittest.TestCase):
    """/api/ledger unified rows and scope filtering."""

    def setUp(self):
        self.root = operator_queue("ia_lg_", self)

    def test_messages_and_events_share_one_ledger(self):
        server.do_message(self.root, {"actor": "OPERATOR-0001",
                                      "role": "operator",
                                      "source": "operator-ui",
                                      "message": "ledger message row"})
        server.do_agent_event(self.root, {"actor": "agent/worker",
                                          "message": "ledger event row"})
        led = server.build_ledger(self.root, scope="active")
        types = {r["type"] for r in led["rows"]}
        self.assertIn("message", types)
        self.assertIn("agent_event", types)
        for row in led["rows"]:
            for key in ("at", "type", "actor", "event", "status", "archived"):
                self.assertIn(key, row)
            self.assertFalse(row["archived"])

    def test_archived_scope_is_empty_without_an_archive(self):
        led = server.build_ledger(self.root, scope="archived")
        self.assertEqual(led["rows"], [])
        self.assertEqual(led["scope"], "archived")

    def test_rows_sorted_newest_first(self):
        server.do_message(self.root, {"actor": "a", "message": "first"})
        server.do_message(self.root, {"actor": "b", "message": "second"})
        led = server.build_ledger(self.root, scope="all")
        ats = [r["at"] for r in led["rows"] if r["at"]]
        self.assertEqual(ats, sorted(ats, reverse=True))


class ArchiveIndexSummaryTests(unittest.TestCase):
    def test_empty_archive_summary(self):
        root = operator_queue("ia_ax_", self)
        summary = server.build_archive_index_summary(root)
        self.assertEqual(summary.get("archived"), [])


if __name__ == "__main__":
    unittest.main()
