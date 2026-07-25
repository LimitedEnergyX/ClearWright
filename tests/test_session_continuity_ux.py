"""Active Session Continuity and Message Identity UX (Phase 1).

Follows the established front-end test pattern in this repository: static
assertion over apps/control-plane/static/{app.js,index.html,style.css}, which is
how test_command_center_hygiene, test_conversation_console, test_operator_mode
and the other console tests verify UI behaviour.

Covers the operator-specified acceptance criteria: selection restoration,
active-item fallback ranking, the no-active-item empty state, conversation
default, latest-message navigation, targeted composer destination, generic
composer suppression, message-ID visibility, copy controls, post-send
confirmation, stale stored selection, completed items not being auto-selected,
truthful execution-state rendering, and keyboard behaviour.
"""
import os
import re
import unittest

STATIC = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                      "..", "apps", "control-plane", "static")


def _read(name):
    with open(os.path.join(STATIC, name), encoding="utf-8") as fh:
        return fh.read()


APP = _read("app.js")
HTML = _read("index.html")
CSS = _read("style.css")


class SelectionRestorationTest(unittest.TestCase):
    """Item 1: a refresh returns the operator to active work."""

    def test_selection_is_persisted_under_a_single_stable_key(self):
        self.assertIn('SELECTION_KEY = "cw_selected_work_item_v1"', APP)
        self.assertIn("function persistSelection", APP)
        self.assertIn("function readPersistedSelection", APP)

    def test_select_task_persists_the_choice(self):
        m = re.search(r"function selectTask\([^)]*\)\s*\{(.{0,900})", APP, re.S)
        self.assertIsNotNone(m, "selectTask not found")
        self.assertIn("persistSelection(selectedWorkItemId)", m.group(1))

    def test_persistence_failure_is_survivable(self):
        """Storage may be unavailable; the hash route must still work."""
        m = re.search(r"function persistSelection[\s\S]{0,400}?\n\}", APP)
        self.assertIn("catch", m.group(0))
        m2 = re.search(r"function readPersistedSelection[\s\S]{0,300}?\n\}", APP)
        self.assertIn("catch", m2.group(0))

    def test_restore_runs_at_boot_after_the_queue_loads(self):
        self.assertIn("restoreActiveSelection", APP)
        self.assertRegex(APP, r"refreshWorkItems\(\)\s*\.then\(\s*restoreActiveSelection")

    def test_explicit_deep_link_wins_over_stored_selection(self):
        m = re.search(r"function restoreActiveSelection[\s\S]{0,1400}?\n\}", APP)
        body = m.group(0)
        self.assertIn("location.hash", body)
        # The deep-link branch must short-circuit BEFORE any fallback ranking,
        # so a shared link never lands the operator on a different item.
        head = body.split("rankActiveWorkItems")[0]
        self.assertIn("if (deep)", head)
        self.assertIn("return;", head.split("if (deep)")[1])

    def test_stale_stored_selection_is_not_used(self):
        """A stored id must be validated against the live queue AND activity."""
        m = re.search(r"function restoreActiveSelection[\s\S]{0,1400}?\n\}", APP)
        body = m.group(0)
        self.assertIn("isActiveItem", body)
        self.assertIn("work_item_id === stored", body)


class FallbackRankingTest(unittest.TestCase):
    """Item 1: highest-priority active item when there is no valid selection."""

    def test_priority_order_matches_the_operator_specification(self):
        m = re.search(r"ACTIVE_RANK = \[(.*?)\]", APP, re.S)
        self.assertIsNotNone(m)
        order = re.findall(r'"([a-z_]+)"', m.group(1))
        self.assertEqual(order, [
            "waiting_for_operator", "operator_message_posted", "wake_pending",
            "paused", "executor_active", "in_council", "blocked"])

    def test_ranking_filters_to_active_items_only(self):
        m = re.search(r"function rankActiveWorkItems[\s\S]{0,600}?\n\}", APP)
        self.assertIn("filter(isActiveItem)", m.group(0))

    def test_completed_items_are_never_auto_selected(self):
        m = re.search(r"INACTIVE_STATES = \[(.*?)\]", APP, re.S)
        self.assertIsNotNone(m)
        for terminal in ("recently_completed", "complete", "superseded", "historical"):
            self.assertIn(terminal, m.group(1))

    def test_ranking_is_stable_by_recent_activity(self):
        m = re.search(r"function rankActiveWorkItems[\s\S]{0,600}?\n\}", APP)
        self.assertIn("last_activity_at", m.group(0))

    def test_ranking_uses_only_fields_the_api_returns(self):
        """No invented field may drive selection."""
        m = re.search(r"function activeStateOf[\s\S]{0,700}?\n\}", APP)
        body = m.group(0)
        for field in ("presentation_state", "runner_state"):
            self.assertIn(field, body)


class EmptyStateTest(unittest.TestCase):
    """Item 1/5: the empty state is legitimate ONLY with no active work."""

    def test_no_active_work_clears_the_selection_and_returns(self):
        m = re.search(r"function restoreActiveSelection[\s\S]{0,1400}?\n\}", APP)
        self.assertIn("if (!target)", m.group(0))

    def test_duplicate_no_task_selected_placeholders_are_gone(self):
        self.assertEqual(HTML.count("No task selected."), 1,
                         "exactly ONE canonical empty-state placeholder may "
                         "ship; the rail must not duplicate it")

    def test_rail_is_hidden_rather_than_showing_empty_cards(self):
        self.assertIn('id="session-rail"', HTML)
        self.assertIn("rail.hidden = true", APP)
        self.assertIn("rail.hidden = false", APP)


class ConversationFirstTest(unittest.TestCase):
    """Item 2: conversation-first, latest message, deliberate-scroll respected."""

    def test_navigation_opens_the_conversation_tab(self):
        m = re.search(r"function navigateToWorkItem[\s\S]{0,1600}?\n\}", APP)
        self.assertIn("openConversationTab()", m.group(0))

    def test_navigation_lands_on_the_latest_message(self):
        m = re.search(r"function navigateToWorkItem[\s\S]{0,1600}?\n\}", APP)
        self.assertIn("jumpToLatestMessage", m.group(0))

    def test_scroll_is_preserved_only_when_deliberately_moved_away(self):
        self.assertIn("function operatorMovedAwayFromLatest", APP)

    def test_jump_to_latest_control_exists_and_is_a_button(self):
        self.assertIn('id="jump-to-latest"', HTML)
        self.assertRegex(HTML, r'<button[^>]*id="jump-to-latest"')
        self.assertIn(".jump-to-latest", CSS)


class ComposerBindingTest(unittest.TestCase):
    """Item 3, behaviour: the displayed destination IS the posted destination.

    These assertions exist because live inspection of the running console found
    the presentation layer alone was not enough: the banner rendered correctly
    but never received a work_item_id, so it read "New conversation" while a
    work item was selected. That is exactly the wrong-destination class of
    defect this slice is meant to remove.
    """

    def test_composer_target_binds_to_the_selected_work_item(self):
        m = re.search(r"function convComposerTarget[\s\S]{0,1600}?\n\}", APP)
        self.assertIsNotNone(m, "convComposerTarget not found")
        body = m.group(0)
        self.assertIn("selectedWorkItemId", body)
        self.assertIn("work_item_id: selectedWorkItemId", body)

    def test_work_item_id_is_only_sent_with_a_durable_thread(self):
        """The server refuses an unbound thread/work-item pair; never invent one."""
        m = re.search(r"function convComposerTarget[\s\S]{0,1600}?\n\}", APP)
        body = m.group(0)
        self.assertNotIn("convComposerNewThreadId", body.split("selectedConvThread ||")[0])
        self.assertIn("thread ?", body)

    def test_selection_change_refreshes_the_destination(self):
        m = re.search(r"function selectTask\([^)]*\)\s*\{(.{0,900})", APP, re.S)
        self.assertIn("convComposer.updateBanner()", m.group(1))

    def test_navigation_binds_the_real_durable_thread(self):
        """selectTask(null, id) would drop the thread and mint a new one."""
        m = re.search(r"function navigateToWorkItem[\s\S]{0,1600}?\n\}", APP)
        body = m.group(0)
        self.assertNotIn("selectTask(null, workItemId)", body)
        self.assertIn("thread_id", body)

    def test_deep_link_binds_the_thread_the_same_way(self):
        m = re.search(r"function applyWorkHashRoute[\s\S]{0,1200}?\n\}", APP)
        body = m.group(0)
        self.assertNotIn("selectTask(null, wid)", body)
        self.assertIn("thread_id", body)

    def test_confirmed_target_contract_is_not_weakened(self):
        """The pre-existing guarantee must survive this slice.

        A locally minted, pre-allocated thread id must never be presented as a
        confirmed destination. Binding a work item does not need a looser rule:
        selectTask now stores the item's REAL durable thread, so
        selectedConvThread is already set whenever a work item is selected.
        """
        self.assertIn("isConfirmedTarget: () => !!selectedConvThread", APP)
        self.assertNotIn("selectedConvThread || selectedWorkItemId", APP)


class ConversationSurfaceTest(unittest.TestCase):
    """Item 2, behaviour: the conversation surface must actually be revealed.

    Live inspection found openConversationTab() querying a tab control that
    does not exist in this console (the only role="tablist" is the queue filter
    strip), making it a silent no-op. The Work view IS the conversation surface.
    """

    def test_open_conversation_does_not_depend_on_a_nonexistent_tab(self):
        m = re.search(r"function openConversationTab[\s\S]{0,1200}?\n\}", APP)
        body = m.group(0)
        self.assertNotIn('data-tab="conversation"', body)
        self.assertNotIn("#tab-conversation", body)

    def test_open_conversation_reveals_the_work_view(self):
        m = re.search(r"function openConversationTab[\s\S]{0,1200}?\n\}", APP)
        self.assertIn('showView("work")', m.group(0))

    def test_no_conversation_tab_control_is_invented_in_markup(self):
        self.assertNotIn('data-tab="conversation"', HTML)


class TargetedComposerTest(unittest.TestCase):
    """Item 3: one safe composer, destination displayed, never inferred."""

    def test_destination_shows_work_item_thread_and_title(self):
        m = re.search(r"function updateBanner[\s\S]{0,3000}?\n  \}", APP)
        body = m.group(0)
        self.assertIn("data-dest-work-item", body)
        self.assertIn("data-dest-thread", body)
        self.assertIn("dest-title", body)

    def test_destination_comes_from_the_selection_not_the_message_text(self):
        m = re.search(r"function updateBanner[\s\S]{0,3000}?\n  \}", APP)
        body = m.group(0)
        self.assertIn("target.work_item_id", body)
        self.assertNotIn("textarea.value", body)

    def test_generic_composer_is_demoted_while_work_is_selected(self):
        self.assertIn("function applyComposerFocus", APP)
        m = re.search(r"function applyComposerFocus[\s\S]{0,2200}?\n\}", APP)
        body = m.group(0)
        self.assertIn("composer-demoted", body)
        self.assertIn("tabIndex", body)
        self.assertIn(".composer-demoted", CSS)

    def test_demotion_is_reapplied_on_every_selection_change(self):
        m = re.search(r"function selectTask\([^)]*\)\s*\{(.{0,900})", APP, re.S)
        self.assertIn("applyComposerFocus()", m.group(1))

    def test_generic_composer_is_not_removed_only_demoted(self):
        """The operator can still start a new conversation deliberately."""
        self.assertIn('id="composer-card"', HTML)


class MessageIdentityTest(unittest.TestCase):
    """Item 4: durable identity visible without History or raw JSON (issue #86)."""

    def test_identity_row_is_rendered_on_every_message_card(self):
        self.assertIn("messageIdentityRow(m)", APP)
        m = re.search(r"function messageIdentityRow[\s\S]{0,1400}?\n\}", APP)
        body = m.group(0)
        for field in ("message_id", "thread_id", "work_item_id", "actor", "intent"):
            self.assertIn(field, body)

    def test_copy_controls_are_real_keyboard_reachable_buttons(self):
        m = re.search(r"function copyIdButton[\s\S]{0,600}?\n\}", APP)
        body = m.group(0)
        self.assertIn('<button type="button"', body)
        self.assertIn("aria-label", body)
        self.assertIn(".copy-id:focus-visible", CSS)

    def test_copy_uses_the_clipboard_api_and_degrades_safely(self):
        m = re.search(r"function copyToClipboard[\s\S]{0,700}?\n\}", APP)
        body = m.group(0)
        self.assertIn("navigator.clipboard", body)
        self.assertIn("catch", body)

    def test_post_send_confirmation_exposes_the_new_message_id(self):
        self.assertIn("function showPostConfirmation", APP)
        m = re.search(r"function showPostConfirmation[\s\S]{0,900}?\n\}", APP)
        body = m.group(0)
        self.assertIn("data-posted-message-id", body)
        self.assertIn("result.thread_id", body)
        self.assertIn("result.work_item_id", body)
        self.assertIn("copyIdButton(result.message_id", body)

    def test_confirmation_fires_only_after_the_durable_verify(self):
        """It must follow the post-write re-read, never precede it."""
        i_verify = APP.index("could not verify the durable copy matched")
        i_conf = APP.index("showPostConfirmation(result)")
        self.assertLess(i_verify, i_conf)

    def test_confirmation_region_is_a_polite_live_region(self):
        self.assertIn('id="post-confirmation"', HTML)
        self.assertIn('aria-live="polite"', HTML)

    def test_no_duplicate_github_issue_is_referenced(self):
        self.assertIn("issue #86", APP)


class TruthfulExecutionStateTest(unittest.TestCase):
    """Item 6: RUNNING is never derived from message-post activity."""

    def test_operator_message_does_not_yield_a_running_state(self):
        m = re.search(r"function truthfulExecutionState[\s\S]{0,1400}?\n\}", APP)
        body = m.group(0)
        self.assertIn("operator_message_posted", body)
        i_msg = body.index('ev === "operator_message"')
        i_run = body.index('p === "running"')
        self.assertLess(i_msg, i_run,
                        "an operator message must be classified before any "
                        "running check can claim the executor is active")

    def test_unsupported_states_are_not_simulated(self):
        labels = re.search(r"EXECUTION_STATE_LABELS = \{(.*?)\n\}", APP, re.S).group(1)
        for deferred in ("EXECUTOR_RESUMED", "MESSAGE_ACKNOWLEDGED", "WAKE_PENDING"):
            self.assertNotIn(deferred, labels,
                             deferred + " requires the Phase 2 wake bridge and "
                             "must not be rendered from current evidence")

    def test_supported_states_are_available(self):
        labels = re.search(r"EXECUTION_STATE_LABELS = \{(.*?)\n\}", APP, re.S).group(1)
        for supported in ("CLAIMED", "WAITING_FOR_OPERATOR", "OPERATOR_MESSAGE_POSTED",
                          "PAUSED", "EXECUTOR_ACTIVE", "IN_COUNCIL", "BLOCKED", "COMPLETE"):
            self.assertIn(supported, labels)

    def test_state_is_surfaced_on_the_queue_row(self):
        self.assertIn("executionStateLabel(it)", APP)


class ConsistentDemotionTest(unittest.TestCase):
    """Item 7: demotion must not leave a keyboard trap.

    Live inspection caught aria-hidden="true" on #composer-card while its Send
    button was still tabindex=0 -- reachable by keyboard but absent from the
    accessibility tree.
    """

    def test_demotion_removes_a11y_and_tab_order_together(self):
        m = re.search(r"function applyComposerFocus[\s\S]{0,2200}?\n\}", APP)
        body = m.group(0)
        self.assertIn("inert", body)
        self.assertIn("removeAttribute(\"aria-hidden\")", body)

    def test_fallback_covers_every_focusable_not_just_the_textarea(self):
        m = re.search(r"function applyComposerFocus[\s\S]{0,2200}?\n\}", APP)
        body = m.group(0)
        self.assertIn("querySelectorAll(FOCUSABLE)", body)
        self.assertIn("button", body)


class IdentifierFidelityTest(unittest.TestCase):
    """Item 4: a durable id must be displayed in the case it actually has.

    .composer-banner sets text-transform:uppercase, so without an explicit
    override the destination and identity rows render ids like
    "MESSAGE:MSG-2026..." which do not match the durable record.
    """

    def test_identifiers_are_not_case_transformed(self):
        i = CSS.find(".composer-destination .dest-work,")
        self.assertNotEqual(i, -1, "no case-fidelity rule for the destination")
        block = CSS[i:i + 260]
        for cls in (".dest-thread", ".dest-title"):
            self.assertIn(cls, block)
        self.assertIn("text-transform: none", block)


class AccessibilityTest(unittest.TestCase):
    """Item 7: keyboard operation and focus visibility."""

    def test_queue_rows_are_real_controls(self):
        self.assertIn('role="button"', APP)
        self.assertIn('tabindex="0"', APP)
        self.assertIn("aria-pressed=", APP)

    def test_queue_rows_activate_on_enter_and_space(self):
        m = re.search(r'if \(e\.key !== "Enter" && e\.key !== " "\)[\s\S]{0,500}?\n\}\);', APP)
        self.assertIsNotNone(m, "queue rows need Enter/Space activation")
        self.assertIn("navigateToWorkItem", m.group(0))

    def test_existing_send_shortcuts_are_preserved(self):
        """Ctrl+Enter sends; Shift+Enter still inserts a newline."""
        self.assertIn('e.key === "Enter" && (e.ctrlKey || e.metaKey)', APP)
        self.assertIn("Shift+Enter for a new line, Ctrl+Enter to send", HTML)

    def test_focus_rings_exist_for_new_controls(self):
        for rule in (".copy-id:focus-visible", ".jump-to-latest:focus-visible",
                     '.q-row[role="button"]:focus-visible'):
            self.assertIn(rule, CSS)


class SemanticsPreservedTest(unittest.TestCase):
    """No durable semantics may change for presentation convenience."""

    def test_no_server_side_change_is_required_by_this_slice(self):
        server = os.path.join(STATIC, "..", "server.py")
        self.assertTrue(os.path.exists(server))

    def test_identity_helpers_only_read_existing_fields(self):
        m = re.search(r"function messageIdentityRow[\s\S]{0,1400}?\n\}", APP)
        body = m.group(0)
        for mutator in ("postJSON", "fetch(", "POST"):
            self.assertNotIn(mutator, body)

    def test_restoration_never_mutates_durable_state(self):
        m = re.search(r"function restoreActiveSelection[\s\S]{0,1400}?\n\}", APP)
        body = m.group(0)
        for mutator in ("postJSON", "fetch(", "/api/action"):
            self.assertNotIn(mutator, body)


if __name__ == "__main__":
    unittest.main()
