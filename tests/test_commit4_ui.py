"""Local Council site redesign and composer integrity (commit 4): task
workspace tabs bound to one selected item, popover dismissal behavior, and the
composer's client-side contract. These are source-presence checks matching
this repo's existing UI-test style; tests/test_message_integrity.py covers the
server-side behavior with real HTTP requests, and a live browser smoke
supplements both before merge.
"""
import os
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATIC = os.path.join(REPO_ROOT, "apps", "control-plane", "static")


def read(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


class ComposerContractTests(unittest.TestCase):
    def setUp(self):
        self.appjs = read(os.path.join(STATIC, "app.js"))
        self.html = read(os.path.join(STATIC, "index.html"))

    def test_both_composers_are_multiline_textareas(self):
        self.assertIn('id="operator-chat-input" class="composer-textarea"', self.html)
        self.assertIn('id="conv-input" class="composer-textarea"', self.html)
        self.assertNotIn('id="operator-chat-input" type="text"', self.html)
        self.assertNotIn('id="conv-input" type="text"', self.html)

    def test_shift_enter_newline_ctrl_enter_send(self):
        self.assertIn('e.key === "Enter" && (e.ctrlKey || e.metaKey)', self.appjs)
        self.assertIn("Plain Enter and Shift+Enter both insert a newline", self.appjs)

    def test_byte_limit_matches_server_constant(self):
        self.assertIn("const MESSAGE_MAX_BYTES = 65536;", self.appjs)

    def test_canonical_content_normalizes_newlines(self):
        self.assertIn('replace(/\\r\\n/g, "\\n").replace(/\\r/g, "\\n").trim()', self.appjs)

    def test_client_rejects_oversized_before_network(self):
        self.assertIn("bytes > MESSAGE_MAX_BYTES", self.appjs)
        self.assertIn("over the \" + MESSAGE_MAX_BYTES +", self.appjs)

    def test_no_draft_clear_on_failure_only_on_verified_success(self):
        self.assertIn("clearDraft(draftKey())", self.appjs)
        self.assertIn("Post-write re-read: success is shown ONLY after", self.appjs)
        self.assertIn("stored.message !== canonical", self.appjs)

    def test_idempotency_key_generated_and_reused_in_draft(self):
        self.assertIn("genIdempotencyKey", self.appjs)
        self.assertIn("draft.idempotencyKey = draft.idempotencyKey || genIdempotencyKey();", self.appjs)

    def test_draft_persisted_and_restored_via_sessionstorage(self):
        self.assertIn("sessionStorage.setItem(draftStorageKey(name)", self.appjs)
        self.assertIn("sessionStorage.getItem(draftStorageKey(name))", self.appjs)
        self.assertIn("function restoreDraft", self.appjs)

    def test_destination_banner_always_present(self):
        self.assertIn('id="operator-chat-banner"', self.html)
        self.assertIn('id="conv-banner"', self.html)
        self.assertIn("function updateBanner", self.appjs)

    def test_banner_does_not_claim_continuing_before_a_confirmed_send(self):
        # A pre-allocated (retry-safety) thread id must not be shown as
        # "continuing" a conversation before anything has actually been sent.
        self.assertIn("isConfirmedTarget", self.appjs)
        self.assertIn("const confirmed = !isConfirmedTarget || isConfirmedTarget();", self.appjs)
        self.assertIn("isConfirmedTarget: () => !!selectedConvThread", self.appjs)

    def test_send_disabled_while_in_flight(self):
        self.assertIn("sendBtn.disabled = true;", self.appjs)
        self.assertIn("sendBtn.disabled = false;", self.appjs)

    def test_auto_grow_present(self):
        self.assertIn("function autoGrow", self.appjs)
        self.assertIn("textarea.style.height", self.appjs)


class TaskWorkspaceTabTests(unittest.TestCase):
    def setUp(self):
        self.appjs = read(os.path.join(STATIC, "app.js"))
        self.css = read(os.path.join(STATIC, "style.css"))

    def test_five_tabs_defined(self):
        for label in ("Overview", "Conversation", "Councils", "Evidence", "Audit"):
            self.assertIn('"' + label + '"', self.appjs)

    def test_tabs_bind_to_the_same_selected_run(self):
        # Every tab builder takes the SAME `run` object; there is no separate
        # fetch per tab that could show a different task's data.
        self.assertIn("function buildOverviewTab(run)", self.appjs)
        self.assertIn("function buildConversationTab(run)", self.appjs)
        self.assertIn("function buildEvidenceTab(run)", self.appjs)
        self.assertIn("function buildAuditTab(run)", self.appjs)
        self.assertIn("renderConvTabPanel(run)", self.appjs)

    def test_switching_tabs_does_not_refetch(self):
        self.assertIn("function switchConvTab(tabId)", self.appjs)
        self.assertIn("if (convDetailRun) renderConvTabPanel(convDetailRun);", self.appjs)

    def test_tab_css_present(self):
        self.assertIn(".conv-tabs", self.css)
        self.assertIn(".conv-tab.is-active", self.css)


class PopoverBehaviorTests(unittest.TestCase):
    def setUp(self):
        self.appjs = read(os.path.join(STATIC, "app.js"))

    def test_outside_click_closes(self):
        self.assertIn("p.panel.contains(e.target) || p.trigger.contains(e.target)", self.appjs)
        self.assertIn("closePopover(id,", self.appjs)

    def test_escape_closes(self):
        self.assertIn('if (e.key === "Escape") closeAllPopovers();', self.appjs)

    def test_navigation_closes_popovers(self):
        # The unified view switcher is the single navigation path; it clears
        # popovers on every Command Center / Work / History transition.
        idx = self.appjs.index("function showView")
        body_start = self.appjs.index("{", idx)
        body_end = self.appjs.index("\n}", body_start)
        self.assertIn("closeAllPopovers()", self.appjs[body_start:body_end + 2],
                      "showView must close popovers on navigation")

    def test_only_one_popover_open_at_a_time(self):
        self.assertIn("Only one peer popover stays open: close every other one first.",
                      self.appjs)

    def test_focus_restored_after_dismissal(self):
        self.assertIn("p.trigger.focus();", self.appjs)

    def test_reopen_does_not_require_double_click(self):
        self.assertIn("function togglePopover(id)", self.appjs)
        self.assertIn("isPopoverOpen(id) ? closePopover(id) : openPopover(id);", self.appjs)

    def test_health_panel_registered_as_a_popover(self):
        self.assertIn('registerPopover("health"', self.appjs)


class ToolLogAndDiagnosticLaneTests(unittest.TestCase):
    def setUp(self):
        self.html = read(os.path.join(STATIC, "index.html"))

    def test_tool_log_closed_by_default(self):
        idx = self.html.index('class="activity-details"')
        tag_start = self.html.rindex("<details", 0, idx)
        tag_end = self.html.index(">", idx)
        self.assertNotIn(" open", self.html[tag_start:tag_end])

    def test_live_agent_feed_moved_to_a_collapsed_disclosure(self):
        idx = self.html.index('id="feed-panel"')
        tag_start = self.html.rindex("<details", 0, idx)
        tag_end = self.html.index(">", idx)
        self.assertNotIn(" open", self.html[tag_start:tag_end])


class EmptyIncomingRequestCollapseTests(unittest.TestCase):
    def setUp(self):
        self.appjs = read(os.path.join(STATIC, "app.js"))
        self.css = read(os.path.join(STATIC, "style.css"))

    def test_empty_state_toggles_a_compact_class(self):
        self.assertIn('panel.classList.add("is-empty");', self.appjs)
        self.assertIn('panel.classList.remove("is-empty");', self.appjs)
        self.assertIn(".operator-panel.is-empty", self.css)


if __name__ == "__main__":
    unittest.main()
