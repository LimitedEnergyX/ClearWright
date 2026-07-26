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

# Single backslash, built from its code point so the source stays
# ASCII-safe and the regex literals below assemble unambiguously.
BS = chr(92)

STATIC = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                      "..", "apps", "control-plane", "static")


def _read(name):
    with open(os.path.join(STATIC, name), encoding="utf-8") as fh:
        return fh.read()


APP = _read("app.js")
HTML = _read("index.html")
CSS = _read("style.css")



RE_CARD = r"function queueCard\([\s\S]{0,8000}?\n}"
RE_PARSE = r"function parseWorkRoute[\s\S]{0,4000}?\n\}"


def _block_of(html, elem_id):
    """Return the balanced-div source of the element carrying elem_id.

    Used so containment assertions test the real tree rather than the byte
    order of two ids in the file.
    """
    i = html.index('id="%s"' % elem_id)
    start = html.rindex("<div", 0, i)
    depth = 0
    for m in re.finditer(r"<div" + chr(92) + "b|</div>", html[start:]):
        depth += 1 if m.group(0) != "</div>" else -1
        if depth == 0:
            return html[start:start + m.end()]
    raise AssertionError("unbalanced markup around " + elem_id)


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
        m = re.search(r"function persistSelection[\s\S]{0,4000}?\n\}", APP)
        self.assertIn("catch", m.group(0))
        m2 = re.search(r"function readPersistedSelection[\s\S]{0,4000}?\n\}", APP)
        self.assertIn("catch", m2.group(0))

    def test_restore_runs_at_boot_after_the_queue_loads(self):
        """Scoped to wire(), because the retry path contains a textually
        similar call and would satisfy a whole-file assertion without proving
        anything about boot."""
        self.assertIn("restoreActiveSelection", APP)
        i = APP.index("function wire()")
        j = APP.index("function handleOperatorAction")
        boot = APP[i:j]
        self.assertIn("refreshWorkItems()", boot)
        self.assertIn("restoreActiveSelection()", boot)
        self.assertIn("initJumpToLatest();", boot)
        # Restoration must follow a SUCCESSFUL load, not run unconditionally.
        self.assertIn("refreshWorkItems().then(", boot)

    def test_explicit_deep_link_wins_over_stored_selection(self):
        m = re.search(r"function restoreActiveSelection[\s\S]{0,4000}?\n\}", APP)
        body = m.group(0)
        self.assertIn("location.hash", body)
        # The deep-link branch must short-circuit BEFORE any fallback ranking,
        # so a shared link never lands the operator on a different item.
        head = body.split("rankActiveWorkItems")[0]
        self.assertIn("if (deep)", head)
        self.assertIn("return;", head.split("if (deep)")[1])

    def test_stale_stored_selection_is_not_used(self):
        """A stored id must be validated against the live queue AND activity."""
        m = re.search(r"function restoreActiveSelection[\s\S]{0,4000}?\n\}", APP)
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
            "waiting_for_operator", "paused", "executor_active",
            "in_council", "claimed", "blocked"])

    def test_operator_message_posted_is_removed_as_unreachable(self):
        """Proven against the real server value domain: last_activity_event is
        emitted only as created|completion|verification|council|gate|progress|
        claim|response|evidence, so no item can ever reach this rank."""
        m = re.search(r"ACTIVE_RANK = " + BS + r"[(.*?)" + BS + r"]", APP, re.S)
        self.assertNotIn("operator_message_posted", m.group(1))
        self.assertNotIn('=== "operator_message"', APP,
                         "the dead event alias must be gone, not just unranked")
        self.assertNotIn('ev === "message"', APP)

    def test_wake_pending_is_deferred_not_simulated(self):
        """No durable field can establish wake_pending before the wake bridge.

        Keeping it in the executable order would advertise a priority bucket
        that nothing can ever fall into, which is what made the first round of
        this ranking inert.
        """
        m = re.search(r"ACTIVE_RANK = \[(.*?)\]", APP, re.S)
        self.assertNotIn("wake_pending", m.group(1))
        head = APP[:APP.index("const ACTIVE_RANK")]
        self.assertIn("wake_pending", head[-900:],
                      "the deferral must be documented where the rank is defined")

    def test_every_ranked_state_is_reachable_from_the_mapping(self):
        """A rank nothing can produce is a silent mis-ordering."""
        m = re.search(r"ACTIVE_RANK = \[(.*?)\]", APP, re.S)
        ranked = set(re.findall(r'"([a-z_]+)"', m.group(1)))
        tm = re.search(r"function truthfulExecutionState[\s\S]{0,4000}?\n\}", APP)
        produced = set(re.findall(r'return "([a-z_]+)"', tm.group(0)))
        self.assertEqual(ranked - produced, set(),
                         "ACTIVE_RANK contains states the mapping never returns")

    def test_ranking_filters_to_active_items_only(self):
        m = re.search(r"function rankActiveWorkItems[\s\S]{0,4000}?\n\}", APP)
        self.assertIn("filter(isActiveItem)", m.group(0))

    def test_completed_items_are_never_auto_selected(self):
        m = re.search(r"INACTIVE_STATES = \[(.*?)\]", APP, re.S)
        self.assertIsNotNone(m)
        for terminal in ("recently_completed", "complete", "superseded", "historical"):
            self.assertIn(terminal, m.group(1))

    def test_ranking_is_stable_by_recent_activity(self):
        m = re.search(r"function rankActiveWorkItems[\s\S]{0,4000}?\n\}", APP)
        self.assertIn("last_activity_at", m.group(0))

    def test_ranking_delegates_to_the_single_truthful_mapping(self):
        """Two mappings drifted: the ranking copy never returned
        operator_message_posted and defaulted unknowns to in_council."""
        m = re.search(r"function activeStateOf[\s\S]{0,4000}?\n\}", APP)
        body = m.group(0)
        self.assertIn("return truthfulExecutionState(it)", body)
        self.assertNotIn('return "in_council"', body)

    def test_unknown_states_are_not_guessed(self):
        tm = re.search(r"function truthfulExecutionState[\s\S]{0,4000}?\n\}", APP)
        self.assertIn('return ""', tm.group(0))

    def test_ranking_has_a_deterministic_final_key(self):
        m = re.search(r"function rankActiveWorkItems[\s\S]{0,4000}?\n\}", APP)
        self.assertIn("work_item_id", m.group(0).split("last_activity_at")[-1])


class EmptyStateTest(unittest.TestCase):
    """Item 1/5: the empty state is legitimate ONLY with no active work."""

    def test_no_active_work_clears_the_selection_and_returns(self):
        m = re.search(r"function restoreActiveSelection[\s\S]{0,4000}?\n\}", APP)
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
        m = re.search(r"function navigateToWorkItem[\s\S]{0,4000}?\n\}", APP)
        self.assertIn("openConversationTab()", m.group(0))

    def test_navigation_lands_on_the_latest_message(self):
        m = re.search(r"function navigateToWorkItem[\s\S]{0,4000}?\n\}", APP)
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
        m = re.search(r"function convComposerTarget[\s\S]{0,4000}?\n\}", APP)
        self.assertIsNotNone(m, "convComposerTarget not found")
        body = m.group(0)
        self.assertIn("selectedWorkItemId", body)
        self.assertIn("work_item_id: selectedWorkItemId", body)

    def test_work_item_id_is_only_sent_with_a_durable_thread(self):
        """The server refuses an unbound thread/work-item pair; never invent one."""
        m = re.search(r"function convComposerTarget[\s\S]{0,4000}?\n\}", APP)
        body = m.group(0)
        # The thread now comes ONLY from the live queue record, so a
        # remembered thread can no longer keep a removed item sendable.
        self.assertIn("liveQueueRecord(selectedWorkItemId)", body)
        self.assertIn("(live && live.thread_id) || null", body)
        # The bare-work-item shape is an explicit fail-closed marker.
        self.assertIn("if (thread) return {", body)
        self.assertIn("unresolved: true", body)

    def test_selection_change_refreshes_the_destination(self):
        m = re.search(r"function selectTask\([^)]*\)\s*\{(.{0,900})", APP, re.S)
        self.assertIn("convComposer.updateBanner()", m.group(1))

    def test_navigation_binds_the_real_durable_thread(self):
        """selectTask(null, id) would drop the thread and mint a new one."""
        m = re.search(r"function navigateToWorkItem[\s\S]{0,4000}?\n\}", APP)
        body = m.group(0)
        self.assertNotIn("selectTask(null, workItemId)", body)
        self.assertIn("thread_id", body)

    def test_deep_link_binds_the_thread_the_same_way(self):
        m = re.search(r"function applyWorkHashRoute[\s\S]{0,4000}?\n\}", APP)
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


class RouteParsingTest(unittest.TestCase):
    """decodeURIComponent raises URIError on malformed percent-encoding.

    Unguarded at boot, that exception propagates out of wire() BEFORE
    restoration, status reporting and the refresh timers are installed, so one
    bad URL would disable the console instead of being reported.
    """

    def test_one_guarded_parser_is_shared(self):
        self.assertIn("function parseWorkRoute", APP)
        m = re.search(RE_PARSE, APP)
        body = m.group(0)
        self.assertIn("try {", body)
        self.assertIn("catch", body)
        self.assertIn("malformed: true", body)

    def test_no_unguarded_decode_remains_on_the_route_paths(self):
        for fn in ("applyWorkHashRoute", "restoreActiveSelection"):
            m = re.search(r"function " + fn + r"[\s\S]{0,4000}?\n\}", APP)
            self.assertNotIn("decodeURIComponent", m.group(0),
                             fn + " must go through parseWorkRoute")

    def test_boot_route_reports_a_malformed_link(self):
        m = re.search(r"function applyWorkHashRoute[\s\S]{0,4000}?\n\}", APP)
        body = m.group(0)
        self.assertIn("route.malformed", body)
        self.assertIn("showRestoreStatus", body)
        self.assertIn("clearWorkRoute()", body)

    def test_a_bad_message_fragment_does_not_discard_a_valid_work_id(self):
        m = re.search(RE_PARSE, APP)
        body = m.group(0)
        after = body.split("let msg = null;")[1]
        self.assertIn("catch", after)
        self.assertIn("msg = null", after)


class RouteErrorPersistenceTest(unittest.TestCase):
    """Clearing the hash makes the bad route invisible to the restoration that
    follows, so the explanation must not be wiped by the success path."""

    def test_a_reported_route_error_is_not_transient(self):
        self.assertIn("let routeErrorReported = false;", APP)
        self.assertIn("function clearTransientRestoreStatus", APP)
        m = re.search(r"function clearTransientRestoreStatus[\s\S]{0,4000}?\n\}", APP)
        self.assertIn("if (routeErrorReported) return;", m.group(0))

    def test_boot_uses_the_transient_clear(self):
        i = APP.index("function wire()")
        j = APP.index("function handleOperatorAction")
        self.assertIn("clearTransientRestoreStatus();", APP[i:j])
        self.assertNotIn('showRestoreStatus("");', APP[i:j])

    def test_malformed_boot_route_clears_the_selection(self):
        m = re.search(r"function applyWorkHashRoute[\s\S]{0,4000}?\n\}", APP)
        branch = m.group(0).split("route.malformed")[1][:600]
        self.assertIn("selectTask(null);", branch)
        self.assertIn("persistSelection(null);", branch)
        self.assertIn("routeErrorReported = true;", branch)

    def test_the_boot_message_does_not_contradict_restoration(self):
        """Restoration DOES continue after a malformed boot route, so the
        message must not claim nothing is selected."""
        m = re.search(r"function applyWorkHashRoute[\s\S]{0,4000}?\n\}", APP)
        branch = m.group(0).split("route.malformed")[1]
        branch = branch[:branch.index("return;")]
        # Assert on the OPERATOR-FACING string, not the surrounding commentary,
        # which legitimately quotes the phrase being avoided.
        call = branch[branch.index("showRestoreStatus("):]
        # The message must not promise ANY outcome: restoration may legitimately
        # find no active work at all, so it states only what already happened.
        self.assertNotIn("highest-priority", call)
        self.assertNotIn("Restoring your active work", call)
        self.assertIn("could not be read", call)


class UnifiedActivationTest(unittest.TestCase):
    """Council finding: mouse and keyboard activation diverged.

    The click handler called selectTask() directly and never wrote the hash,
    while keyboard activation used navigateToWorkItem(). Ordinary mouse
    selection therefore left the PREVIOUS route in the URL -- exactly the
    stale-hash symptom this correction exists to remove -- and the four-way
    destination agreement could not be established for the common interaction.
    """

    def test_mouse_activation_goes_through_navigation(self):
        i = APP.index('getElementById("queue-groups").addEventListener("click"')
        handler = APP[i:i + 900]
        self.assertIn("navigateToWorkItem(workItem)", handler)
        self.assertNotIn("selectTask(thread, workItem)", handler,
                         "the direct path skipped writing the canonical route")

    def test_there_is_one_navigation_operation(self):
        """Only navigateToWorkItem writes the route, and every activation
        path routes through it."""
        self.assertEqual(APP.count("location.hash = " + '"#work="'), 1,
                         "exactly one place may write the work route")
        m = re.search(r"function navigateToWorkItem[" + BS + r"s" + BS + r"S]{0,4000}?" + BS + r"n}", APP)
        self.assertIn('location.hash = "#work="', m.group(0))

    def test_keyboard_uses_native_button_semantics(self):
        """A real <button> activates on Enter and Space and fires exactly one
        click, so there is NO queue key handler at all. An earlier version
        called preventDefault() on Space, which suppressed the very activation
        the control exists to provide."""
        for m in re.finditer(r'addEventListener\("keydown"', APP):
            window = APP[m.start():m.start() + 500]
            self.assertNotIn("q-open", window,
                             "no keydown handler may intercept the queue button")
            self.assertNotIn("navigateToWorkItem", window,
                             "no key handler may create a second activation path")


class StrictRouteProofTest(unittest.TestCase):
    """Council finding: absent and malformed routes bypassed the check.

    An unreadable URL is not evidence that the URL agrees with the selection,
    yet both cases previously fell through and permitted the send.
    """

    def test_an_absent_route_refuses(self):
        m = re.search(r"function destinationDisagreement[" + BS + r"s" + BS + r"S]{0,4000}?" + BS + r"n  }", APP)
        body = m.group(0)
        self.assertIn("if (!route)", body)
        self.assertIn("carries no work route", body)

    def test_a_malformed_route_refuses(self):
        m = re.search(r"function destinationDisagreement[" + BS + r"s" + BS + r"S]{0,4000}?" + BS + r"n  }", APP)
        body = m.group(0)
        self.assertIn("route.malformed", body)
        self.assertIn("unreadable work route", body)

    def test_the_check_no_longer_requires_a_non_malformed_route_to_apply(self):
        m = re.search(r"function destinationDisagreement[" + BS + r"s" + BS + r"S]{0,4000}?" + BS + r"n  }", APP)
        self.assertNotIn("!route.malformed && route.work_item_id", m.group(0))


class QueueReconciliationTest(unittest.TestCase):
    """Correction 1: polling must not destroy DOM identity or focus.

    renderQueue rewrote innerHTML on every poll, roughly every two seconds,
    which removed the focused control before a human could press a key. That
    made keyboard operation of the queue impossible.
    """

    def test_a_no_change_poll_does_not_touch_the_dom(self):
        self.assertIn("let lastQueueSignature = null;", APP)
        m = re.search(r"function renderQueue[" + BS + r"s" + BS + r"S]{0,4000}?" + BS + r"n}", APP)
        body = m.group(0)
        self.assertIn("if (signature === lastQueueSignature) return;", body)
        # The populated path must reach its short circuit without touching the
        # DOM. The empty-queue branch above it writes one message and is itself
        # guarded by its own signature check, so it is excluded here.
        empty_end = body.index("const desired = rows")
        populated = body[empty_end:]
        i = populated.index("if (signature === lastQueueSignature) return;")
        self.assertNotIn("innerHTML", populated[:i],
                         "the DOM must not be written before the no-change check")
        # And the empty branch short-circuits too, rather than rewriting on
        # every poll.
        self.assertIn("if (lastQueueSignature === emptySig) return;", body)

    def test_reconciliation_is_keyed_by_canonical_work_item_id(self):
        self.assertIn("function reconcileQueue", APP)
        m = re.search(r"function reconcileQueue[" + BS + r"s" + BS + r"S]{0,4000}?" + BS + r"n}", APP)
        body = m.group(0)
        self.assertIn("data-work-item", body)
        self.assertIn("data-sig", body)

    def test_an_unchanged_tile_is_reused_not_replaced(self):
        m = re.search(r"function reconcileQueue[" + BS + r"s" + BS + r"S]{0,4000}?" + BS + r"n}", APP)
        body = m.group(0)
        self.assertIn('prev.getAttribute("data-sig") === d.sig', body)
        after = body.split('prev.getAttribute("data-sig") === d.sig')[1][:400]
        self.assertIn("return;", after,
                      "an unchanged tile must be left entirely alone")

    def test_wholesale_replacement_is_gone_from_the_render_path(self):
        m = re.search(r"function renderQueue[" + BS + r"s" + BS + r"S]{0,4000}?" + BS + r"n}", APP)
        body = m.group(0)
        # The only innerHTML write left is the genuinely empty-queue message.
        self.assertEqual(body.count("el.innerHTML ="), 1)
        self.assertIn("queue-empty", body)

    def test_focus_is_captured_and_restored_around_reconciliation(self):
        self.assertIn("function focusedQueueKey", APP)
        self.assertIn("function restoreQueueFocus", APP)
        m = re.search(r"function renderQueue[" + BS + r"s" + BS + r"S]{0,4000}?" + BS + r"n}", APP)
        body = m.group(0)
        i = body.index("const focusKey = focusedQueueKey();")
        j = body.index("reconcileQueue(el, desired);")
        k = body.index("restoreQueueFocus(focusKey, el);")
        self.assertLess(i, j, "focus must be captured before reconciliation")
        self.assertLess(j, k, "focus must be restored after reconciliation")

    def test_lost_focus_moves_predictably_not_to_the_body(self):
        m = re.search(r"function restoreQueueFocus[" + BS + r"s" + BS + r"S]{0,4000}?" + BS + r"n}", APP)
        body = m.group(0)
        self.assertIn('el.querySelector(".q-open")', body)
        self.assertIn("el.focus()", body)

    def test_no_stale_item_is_retained_to_preserve_focus(self):
        m = re.search(r"function reconcileQueue[" + BS + r"s" + BS + r"S]{0,4000}?" + BS + r"n}", APP)
        body = m.group(0)
        self.assertIn("removeChild", body)
        self.assertIn("if (!keep[k]", body)

    def test_the_signature_covers_every_rendered_field(self):
        m = re.search(r"function queueCardSignature[" + BS + r"s" + BS + r"S]{0,4000}?" + BS + r"n}", APP)
        body = m.group(0)
        for field in ("work_item_id", "thread_id", "presentation_state", "status",
                      "runner_state", "claimed_by", "last_activity_at"):
            self.assertIn(field, body,
                          field + " is rendered, so it must affect the signature")


class WiredPathCoverageTest(unittest.TestCase):
    """Correction 3: coverage must run the real wired paths."""

    def test_a_wired_path_harness_exists(self):
        here = os.path.dirname(os.path.abspath(__file__))
        for name in ("wired_paths.mjs", "mini_dom.mjs"):
            self.assertTrue(os.path.isfile(os.path.join(here, "dom", name)), name)

    def test_it_installs_the_real_wire(self):
        here = os.path.dirname(os.path.abspath(__file__))
        src = open(os.path.join(here, "dom", "wired_paths.mjs"), encoding="utf-8").read()
        self.assertIn("ctx.wire()", src)
        self.assertIn("dispatchEvent", src)

    def test_it_dispatches_real_clicks_and_keys(self):
        here = os.path.dirname(os.path.abspath(__file__))
        src = open(os.path.join(here, "dom", "wired_paths.mjs"), encoding="utf-8").read()
        self.assertIn('pressKey(env.doc, key)', src)
        self.assertIn('new MiniEvent("click"', src)

    def test_it_proves_focus_survives_polling(self):
        here = os.path.dirname(os.path.abspath(__file__))
        src = open(os.path.join(here, "dom", "wired_paths.mjs"), encoding="utf-8").read()
        self.assertIn("focus survives repeated unchanged polling", src)
        self.assertIn("ctx.renderQueue()", src)

    def test_it_drives_the_real_send_through_refusals(self):
        here = os.path.dirname(os.path.abspath(__file__))
        src = open(os.path.join(here, "dom", "wired_paths.mjs"), encoding="utf-8").read()
        self.assertIn('convComposer.send()', src)
        for branch in ("unresolved", "different work item", "no work route", "unreadable"):
            self.assertIn(branch, src)

    def test_the_mini_dom_states_its_limitation(self):
        here = os.path.dirname(os.path.abspath(__file__))
        src = open(os.path.join(here, "dom", "mini_dom.mjs"), encoding="utf-8").read()
        self.assertIn("STATED LIMITATION", src)
        self.assertIn("not a browser", src)


class LiveRecordRequiredTest(unittest.TestCase):
    """Correction 1: a send requires a LIVE canonical record.

    The prior gap: destinationDisagreement guarded its thread comparison with
    `known &&`, so when polling removed the selected item the check was skipped
    exactly when it mattered, while convComposerTarget kept the target sendable
    by reading the remembered selectedConvThread.
    """

    def test_a_live_record_helper_exists(self):
        self.assertIn("function liveQueueRecord", APP)
        m = re.search(r"function liveQueueRecord[" + BS + r"s" + BS + r"S]{0,4000}?" + BS + r"n}", APP)
        body = m.group(0)
        self.assertIn("isCanonicalMessageWorkItem", body)
        self.assertIn("lastWorkItems", body)
        self.assertIn("|| null", body)

    def test_the_target_thread_comes_only_from_the_live_record(self):
        m = re.search(r"function convComposerTarget[" + BS + r"s" + BS + r"S]{0,4000}?" + BS + r"n}", APP)
        body = m.group(0)
        self.assertIn("liveQueueRecord(selectedWorkItemId)", body)
        self.assertNotIn("selectedConvThread || ", body,
                         "the remembered thread was the stale-state path")

    def test_the_destination_check_requires_a_live_record(self):
        m = re.search(r"function destinationDisagreement[" + BS + r"s" + BS + r"S]{0,4000}?" + BS + r"n  }", APP)
        body = m.group(0)
        self.assertIn("liveQueueRecord(target.work_item_id)", body)
        self.assertIn("if (!known)", body)
        self.assertIn("no longer in the live queue", body)

    def test_the_thread_comparison_is_no_longer_optional(self):
        m = re.search(r"function destinationDisagreement[" + BS + r"s" + BS + r"S]{0,4000}?" + BS + r"n  }", APP)
        body = m.group(0)
        self.assertNotIn("known && known.thread_id", body,
                         "guarding on existence skipped the check exactly when "
                         "the record was missing")
        self.assertIn("!known.thread_id || known.thread_id !== target.thread_id", body)


class CanonicalDestinationTest(unittest.TestCase):
    """Correction 4: only a message-scoped work item may receive a message."""

    def test_a_canonical_shape_test_exists(self):
        self.assertIn("function isCanonicalMessageWorkItem", APP)
        m = re.search(r"function isCanonicalMessageWorkItem[" + BS + r"s" + BS + r"S]{0,4000}?" + BS + r"n}", APP)
        self.assertIn("message:msg-", m.group(0))

    def test_packet_projections_are_excluded_by_shape(self):
        m = re.search(r"function isCanonicalMessageWorkItem[" + BS + r"s" + BS + r"S]{0,4000}?" + BS + r"n}", APP)
        self.assertIn("^message:msg-", m.group(0),
                      "the pattern must be anchored so in_progress: ids cannot match")

    def test_the_destination_check_rejects_non_canonical_ids(self):
        m = re.search(r"function destinationDisagreement[" + BS + r"s" + BS + r"S]{0,4000}?" + BS + r"n  }", APP)
        body = m.group(0)
        self.assertIn("isCanonicalMessageWorkItem(target.work_item_id)", body)
        self.assertIn("not a message-scoped work item", body)

    def test_reconciliation_skips_records_without_a_canonical_id(self):
        m = re.search(r"function renderQueue[" + BS + r"s" + BS + r"S]{0,4000}?" + BS + r"n}", APP)
        body = m.group(0)
        self.assertIn("rows.filter((it) => it && it.work_item_id)", body)
        self.assertNotIn('key: it.work_item_id || ""', body,
                         "an empty key collapsed several rows together")


class SelectorEscapingTest(unittest.TestCase):
    """Correction 3: one escaping mechanism, correct for its actual context.

    Every dynamic value in app.js is interpolated into a QUOTED ATTRIBUTE
    selector, so the correct operation is CSS string-literal escaping, not
    identifier escaping. CSS.escape is deliberately not used: its output does
    not round-trip inside a quoted string, so a value containing a quote,
    backslash or space would fail to match the very node it names. The
    wired-path harness proves this positively by selecting the intended node.
    """

    def test_a_single_escaper_exists(self):
        self.assertIn("function cssAttrValue", APP)
        self.assertNotIn("function cssEscape", APP,
                         "identifier escaping was wrong for this context")

    def test_identifier_escaping_is_not_used_in_code(self):
        code = "\n".join(l for l in APP.split("\n")
                          if not l.strip().startswith("//"))
        self.assertNotIn("CSS.escape", code)

    def test_every_dynamic_selector_is_escaped(self):
        for probe in ('.q-open[data-work-item="', '[data-message-id="',
                      '.q-group[data-group="'):
            i = APP.index(probe)
            window = APP[i:i + 160]
            self.assertIn("cssAttrValue(", window,
                          "unescaped interpolation into selector " + probe)

    def test_the_escaper_targets_string_literal_semantics(self):
        m = re.search(r"function cssAttrValue[" + BS + r"s" + BS + r"S]{0,4000}?" + BS + r"n}", APP)
        body = m.group(0)
        # Backslash and quote are escaped, and control characters are
        # hex-escaped, because a CSS string token may not contain them raw.
        self.assertIn("codePointAt(0)", body)
        self.assertIn("toString(16)", body)
        self.assertIn("0x7f", body)


class QueueFreshnessTest(unittest.TestCase):
    """Council finding: a stale snapshot is not positive evidence.

    liveQueueRecord looked up the last SUCCESSFUL snapshot, but a failed refresh
    left that array in place with workItemsLoaded still true, so an unrefreshed
    queue kept authorising sends.
    """

    def test_a_confirmation_flag_exists_and_is_separate_from_loaded(self):
        self.assertIn("let queueConfirmed = false;", APP)
        self.assertIn("let workItemsLoaded = false;", APP)

    def test_a_successful_refresh_confirms_the_queue(self):
        m = re.search(r"async function refreshWorkItems[" + BS + r"s" + BS + r"S]{0,4000}?" + BS + r"n}", APP)
        body = m.group(0)
        self.assertIn("queueConfirmed = true;", body)

    def test_a_failed_refresh_withdraws_confirmation(self):
        m = re.search(r"async function refreshWorkItems[" + BS + r"s" + BS + r"S]{0,4000}?" + BS + r"n}", APP)
        body = m.group(0)
        catch = body.split("} catch (e) {")[1]
        self.assertIn("queueConfirmed = false;", catch)
        self.assertIn("showRestoreStatus", catch,
                      "the operator must be told sending is paused")

    def test_the_send_gate_requires_a_confirmed_queue(self):
        m = re.search(r"function destinationDisagreement[" + BS + r"s" + BS + r"S]{0,4000}?" + BS + r"n  }", APP)
        body = m.group(0)
        self.assertIn("if (!queueConfirmed)", body)
        self.assertIn("not currently confirmed", body)

    def test_the_freshness_check_precedes_the_record_lookup(self):
        m = re.search(r"function destinationDisagreement[" + BS + r"s" + BS + r"S]{0,4000}?" + BS + r"n  }", APP)
        body = m.group(0)
        self.assertLess(body.index("if (!queueConfirmed)"),
                        body.index("liveQueueRecord(target.work_item_id)"),
                        "an unconfirmed queue must refuse before any lookup")

    def test_the_previous_content_is_not_blanked(self):
        m = re.search(r"async function refreshWorkItems[" + BS + r"s" + BS + r"S]{0,4000}?" + BS + r"n}", APP)
        catch = m.group(0).split("} catch (e) {")[1]
        self.assertNotIn("lastWorkItems = []", catch,
                         "the operator should not be blanked out on a "
                         "transient failure")


class ReadOnlyProjectionTest(unittest.TestCase):
    """Council finding: non-canonical entries were reconciled as activatable
    tiles even though they can never be destinations.

    Policy, stated explicitly: a packet projection is REAL work the operator
    must still see, so it is not hidden. It renders READ-ONLY, with no
    activation control, so it can never be selected or navigated to.
    """

    def test_canonicality_is_decided_in_the_card(self):
        m = re.search(RE_CARD, APP)
        body = m.group(0)
        self.assertIn("isCanonicalMessageWorkItem(wid)", body)
        self.assertIn("data-canonical=", body)

    def test_only_canonical_records_get_an_activation_control(self):
        m = re.search(RE_CARD, APP)
        body = m.group(0)
        self.assertIn("canonical", body.split("const openStart")[1][:200])
        self.assertIn("q-readonly", body)

    def test_non_canonical_records_remain_visible(self):
        """Hiding a durable record is never the fix."""
        m = re.search(r"function renderQueue[" + BS + r"s" + BS + r"S]{0,4000}?" + BS + r"n}", APP)
        body = m.group(0)
        self.assertNotIn("isCanonicalMessageWorkItem", body,
                         "renderQueue must not filter out non-canonical rows; "
                         "they render read-only instead")

    def test_the_projection_is_labelled(self):
        m = re.search(RE_CARD, APP)
        self.assertIn("packet record", m.group(0))
        self.assertIn(".q-ro-badge", CSS)
        self.assertIn(".q-noncanonical", CSS)

    def test_stale_role_button_css_is_removed(self):
        self.assertNotIn('.q-row[role="button"]', CSS)


class RefreshOutcomeTest(unittest.TestCase):
    """Correction 1: refresh must distinguish four outcomes explicitly.

    refreshWorkItems previously caught its own error and resolved normally, so
    callers could not tell a handled failure from a successful load. The boot
    continuation therefore cleared the status the failure had just rendered.
    """

    def test_the_four_outcomes_are_named_constants(self):
        for c in ("REFRESH_CONFIRMED", "REFRESH_CONFIRMED_EMPTY",
                  "REFRESH_FAILED", "REFRESH_SUPERSEDED"):
            self.assertIn("const " + c + " =", APP)

    def test_confirmed_empty_is_distinct_from_failed_and_unloaded(self):
        m = re.search(r"async function refreshWorkItems[" + BS + r"s" + BS + r"S]{0,6000}?" + BS + r"n}", APP)
        body = m.group(0)
        self.assertIn("lastWorkItems.length ? REFRESH_CONFIRMED : REFRESH_CONFIRMED_EMPTY", body)
        # An empty load is still loaded AND confirmed.
        self.assertIn("workItemsLoaded = true;", body)
        self.assertIn("queueConfirmed = true;", body)

    def test_a_success_helper_gates_the_callers(self):
        self.assertIn("function refreshSucceeded", APP)
        m = re.search(r"function refreshSucceeded[" + BS + r"s" + BS + r"S]{0,1000}?" + BS + r"n}", APP)
        body = m.group(0)
        self.assertIn("REFRESH_CONFIRMED", body)
        self.assertIn("REFRESH_CONFIRMED_EMPTY", body)
        self.assertNotIn("REFRESH_FAILED", body)

    def test_the_boot_path_acts_only_on_a_confirmed_success(self):
        i = APP.index("function wire()")
        j = APP.index("function handleOperatorAction")
        boot = APP[i:j]
        self.assertIn("refreshWorkItems().then((outcome) =>", boot)
        self.assertIn("if (!refreshSucceeded(outcome)) return;", boot)

    def test_the_retry_control_branches_the_same_way(self):
        m = re.search(r"function showRestoreStatus[" + BS + r"s" + BS + r"S]{0,4000}?" + BS + r"n}", APP)
        body = m.group(0)
        self.assertIn("refreshSucceeded(outcome)", body)
        self.assertIn("REFRESH_FAILED", body)

    def test_refresh_does_not_signal_failure_by_throwing(self):
        """It is called from a polling timer, where an unhandled rejection
        would be noise, and it keeps prior content on screen."""
        m = re.search(r"async function refreshWorkItems[" + BS + r"s" + BS + r"S]{0,6000}?" + BS + r"n}", APP)
        body = m.group(0)
        self.assertNotIn("throw", body)
        self.assertIn("return REFRESH_FAILED;", body)


class RefreshFailureVisibilityTest(unittest.TestCase):
    """Correction 1: the explanation must survive every continuation."""

    def test_a_reported_failure_is_tracked_separately(self):
        self.assertIn("let queueFailureReported = false;", APP)

    def test_transient_cleanup_cannot_erase_it(self):
        m = re.search(r"function clearTransientRestoreStatus[" + BS + r"s" + BS + r"S]{0,1200}?" + BS + r"n}", APP)
        body = m.group(0)
        self.assertIn("if (queueFailureReported) return;", body)

    def test_only_a_confirmed_success_clears_it(self):
        m = re.search(r"async function refreshWorkItems[" + BS + r"s" + BS + r"S]{0,6000}?" + BS + r"n}", APP)
        body = m.group(0)
        success = body.split("catch (e)")[0]
        self.assertIn("queueFailureReported = false;", success)
        self.assertIn("queueFailureReported = true;", body.split("catch (e)")[1])

    def test_the_failure_keeps_the_snapshot_visible(self):
        m = re.search(r"async function refreshWorkItems[" + BS + r"s" + BS + r"S]{0,6000}?" + BS + r"n}", APP)
        catch = m.group(0).split("catch (e)")[1]
        self.assertNotIn("lastWorkItems = []", catch)
        self.assertIn("queueConfirmed = false;", catch)
        self.assertIn("showRestoreStatus(", catch)


class RefreshSequencingTest(unittest.TestCase):
    """Correction 2: only the newest generation may alter refresh truth."""

    def test_a_monotonic_generation_exists(self):
        self.assertIn("let queueRefreshGeneration = 0;", APP)
        m = re.search(r"async function refreshWorkItems[" + BS + r"s" + BS + r"S]{0,6000}?" + BS + r"n}", APP)
        self.assertIn("const gen = ++queueRefreshGeneration;", m.group(0))

    def test_both_completion_paths_are_guarded(self):
        m = re.search(r"async function refreshWorkItems[" + BS + r"s" + BS + r"S]{0,6000}?" + BS + r"n}", APP)
        body = m.group(0)
        success, failure = body.split("catch (e)")
        self.assertIn("gen !== queueRefreshGeneration", success,
                      "an older SUCCESS must not restore state")
        self.assertIn("gen !== queueRefreshGeneration", failure,
                      "an older FAILURE must not invalidate newer state")

    def test_a_superseded_completion_touches_no_shared_state(self):
        m = re.search(r"async function refreshWorkItems[" + BS + r"s" + BS + r"S]{0,6000}?" + BS + r"n}", APP)
        body = m.group(0)
        i = body.index("if (gen !== queueRefreshGeneration) return REFRESH_SUPERSEDED;")
        head = body[:i]
        for mutation in ("lastWorkItems =", "queueConfirmed =", "workItemsLoaded ="):
            self.assertNotIn(mutation, head,
                             "no state may change before the generation check")

    def test_the_guard_precedes_the_snapshot_write(self):
        m = re.search(r"async function refreshWorkItems[" + BS + r"s" + BS + r"S]{0,6000}?" + BS + r"n}", APP)
        body = m.group(0)
        self.assertLess(body.index("if (gen !== queueRefreshGeneration)"),
                        body.index("lastWorkItems = data.work_items"))


class CssStringEscapingTest(unittest.TestCase):
    """Council finding: escaping only backslash and quote was incomplete.

    A CSS string token may not contain a raw newline, NUL or other control
    character, so a route-supplied message id or an unexpected presentation
    state could still have produced an invalid selector.
    """

    def test_control_characters_are_escaped(self):
        m = re.search(r"function cssAttrValue[" + BS + r"s" + BS + r"S]{0,4000}?" + BS + r"n}", APP)
        body = m.group(0)
        self.assertIn("0x20", body)
        self.assertIn("0x7f", body)
        self.assertIn("toString(16)", body,
                      "the general escape is a hexadecimal sequence")

    def test_the_comment_no_longer_overclaims(self):
        i = APP.index("function cssAttrValue")
        head = APP[max(0, i - 900):i]
        self.assertNotIn("only the backslash and the quote", head)
        self.assertIn("control characters", head)


class RefreshPayloadValidationTest(unittest.TestCase):
    """Council finding: `data.work_items || []` made a malformed 200 look like
    an authoritative empty queue, and confirmed-empty is load-bearing because
    it makes stale destinations unsendable."""

    def test_the_payload_shape_is_validated(self):
        m = re.search(r"async function refreshWorkItems[" + BS + r"s" + BS + r"S]{0,6000}?" + BS + r"n}", APP)
        body = m.group(0)
        self.assertIn("Array.isArray(data.work_items)", body)
        self.assertNotIn("data.work_items || []", body)

    def test_a_malformed_payload_is_a_failure(self):
        m = re.search(r"async function refreshWorkItems[" + BS + r"s" + BS + r"S]{0,6000}?" + BS + r"n}", APP)
        body = m.group(0)
        branch = body.split("Array.isArray(data.work_items)")[1][:600]
        self.assertIn("queueConfirmed = false;", branch)
        self.assertIn("return REFRESH_FAILED;", branch)
        self.assertIn("unreadable", branch)

    def test_the_previous_snapshot_survives_a_malformed_payload(self):
        m = re.search(r"async function refreshWorkItems[" + BS + r"s" + BS + r"S]{0,6000}?" + BS + r"n}", APP)
        body = m.group(0)
        after = body.split("Array.isArray(data.work_items)")[1]
        branch = after[:after.index("    }")]      # the guard block only
        self.assertNotIn("lastWorkItems =", branch,
                         "a malformed response must not overwrite the snapshot")
        self.assertIn("return REFRESH_FAILED;", branch)


class RecordPolicyDocumentationTest(unittest.TestCase):
    """Both reviewers asked for the record policy to be stated explicitly."""

    def test_the_two_cases_are_documented_where_enforced(self):
        m = re.search(RE_CARD, APP)
        body = m.group(0)
        self.assertIn("POLICY", body)
        low = body.lower()
        self.assertIn("no usable key", low)
        self.assertIn("excluded from reconciliation", low)
        self.assertIn("read-only", low)


class CanonicalIdentityTest(unittest.TestCase):
    """Correction item 1.

    Investigation result: the apparently duplicated tiles are NOT phantoms.
    /api/work-items returns genuinely distinct canonical work items whose titles
    collide because the title is derived from the origin message text, and three
    of them share one thread. Every work_item_id is a real "message:msg-..."
    value. Collapsing them would HIDE durable governed work, so they are
    disambiguated and the shared-thread condition is surfaced instead.
    """

    def test_queue_identity_is_derived_from_the_canonical_work_item_id(self):
        m = re.search(RE_CARD, APP)
        self.assertIsNotNone(m, "queueCard not found")
        body = m.group(0)
        self.assertIn("it.work_item_id", body)
        self.assertIn("originMessageId(wid)", body)

    def test_a_thread_id_is_never_rendered_as_a_work_item(self):
        m = re.search(RE_CARD, APP)
        body = m.group(0)
        # The work-item row must read work_item_id, never fall back to a thread.
        self.assertNotIn("it.work_item_id || it.thread_id", body)
        self.assertIn("Work item", body)
        self.assertIn("Thread", body)

    def test_shared_thread_raises_an_integrity_warning(self):
        self.assertIn("function threadWorkItemIndex", APP)
        self.assertIn("function sharesThreadWithOtherWorkItems", APP)
        m = re.search(RE_CARD, APP)
        body = m.group(0)
        self.assertIn("q-integrity", body)
        self.assertIn("sharesThreadWithOtherWorkItems", body)

    def test_conflicting_records_are_flagged_not_hidden(self):
        """No filtering may drop a durable work item to tidy the view."""
        m = re.search(RE_CARD, APP)
        body = m.group(0)
        for banned in ("filter(", "dedupe", "unique("):
            self.assertNotIn(banned, body,
                             "tiles must not be removed; the records are real")
        self.assertIn("q-ambiguous", body)
        self.assertIn(".q-integrity", CSS)


class QueueTileIdentityTest(unittest.TestCase):
    """Correction item 2: identify the durable object without opening it."""

    def test_tile_shows_work_item_thread_and_origin_ids(self):
        m = re.search(RE_CARD, APP)
        body = m.group(0)
        for label in ("Work item", "Thread", "Origin message"):
            self.assertIn(label, body)

    def test_tile_offers_copy_for_each_identifier(self):
        m = re.search(RE_CARD, APP)
        body = m.group(0)
        self.assertEqual(body.count("copyIdButton("), 3,
                         "work item, thread and origin message each need Copy")
        self.assertIn('copyIdButton(wid, "work-item ID")', body)
        self.assertIn('copyIdButton(tid, "thread ID")', body)

    def test_copy_controls_carry_the_full_id_not_the_abbreviation(self):
        m = re.search(RE_CARD, APP)
        body = m.group(0)
        self.assertIn("copyIdButton(wid", body)
        self.assertNotIn("copyIdButton(abbrevId", body)

    def test_copying_does_not_also_open_the_item(self):
        self.assertIn("function eventTargetsInnerControl", APP)
        # One definition plus exactly two guarded entry points: click and key.
        # Only the click path needs the guard now: the primary control is a
        # real button, so a Copy click never reaches a row-level activation.
        self.assertEqual(APP.count("if (eventTargetsInnerControl(e)) return;"), 1,
                         "the click path must be guarded")
        i = APP.index('getElementById("queue-groups").addEventListener("click"')
        self.assertIn("eventTargetsInnerControl(e)", APP[i:i + 400])


class IdentifierTerminologyTest(unittest.TestCase):
    """Correction item 3: each identifier type is named and explained."""

    def test_matching_suffix_is_explained_rather_than_hidden(self):
        self.assertIn("function sharesSuffix", APP)
        m = re.search(RE_CARD, APP)
        body = m.group(0)
        self.assertIn("sharesSuffix(wid, tid)", body)
        self.assertIn("matching suffix", body)

    def test_the_explanation_says_they_remain_different_identifiers(self):
        m = re.search(RE_CARD, APP)
        self.assertIn("different identifiers", m.group(0))

    def test_identifiers_are_not_case_transformed_on_tiles(self):
        i = CSS.index(".q-idv")
        self.assertIn("text-transform: none", CSS[i:i + 200])


class HistoryColumnsTest(unittest.TestCase):
    """Correction item 4.

    The server already returns work_item_id and thread_id as distinct fields
    (zero ledger rows carry a thread id in the work-item field). The CLIENT
    collapsed them with `work_item_id || thread_id || packet_id`, so the 148
    rows with no work-item binding printed a thr-... under a heading that said
    "Work item" -- a false identity claim.
    """

    def test_history_has_distinct_identifier_columns(self):
        i = HTML.index('<table class="ledger"')
        head = HTML[i:i + 700]
        for col in ("<th>Message</th>", "<th>Work item</th>", "<th>Thread</th>",
                    "<th>Actor</th>", "<th>Event</th>", "<th>Status</th>"):
            self.assertIn(col, head)

    def test_the_substitution_fallback_is_gone(self):
        self.assertNotIn("row.work_item_id || row.thread_id", APP)

    def test_a_missing_binding_is_stated_honestly(self):
        self.assertIn("no work item", APP)
        self.assertIn("ledger-none", APP)
        self.assertIn(".ledger-none", CSS)

    def test_message_id_column_is_only_populated_for_messages(self):
        self.assertIn("function ledgerMessageId", APP)
        m = re.search(r"function ledgerMessageId[" + BS + r"s" + BS + r"S]{0,4000}?" + BS + r"n" + BS + r"}", APP)
        body = m.group(0)
        self.assertIn('row.type === "message"', body)
        self.assertIn('return ""', body)

    def test_colspan_matches_the_new_column_count(self):
        self.assertIn('colspan="8"', HTML)
        self.assertIn('colspan="8"', APP)
        self.assertNotIn('colspan="6"', APP)


class DestinationAgreementTest(unittest.TestCase):
    """Correction item 5: URL, selection, thread and composer must agree."""

    def test_a_disagreement_check_exists(self):
        self.assertIn("function destinationDisagreement", APP)

    def test_it_compares_route_selection_and_thread(self):
        m = re.search(r"function destinationDisagreement[" + BS + r"s" + BS + r"S]{0,4000}?" + BS + r"n  }", APP)
        self.assertIsNotNone(m, "destinationDisagreement not found")
        body = m.group(0)
        self.assertIn("selectedWorkItemId", body)
        self.assertIn("thread_id", body)
        self.assertIn("parseWorkRoute(location.hash)", body)

    def test_no_body_is_built_when_they_disagree(self):
        i = APP.index("const disagreement = destinationDisagreement(preTarget)")
        j = APP.index("const body = Object.assign")
        self.assertLess(i, j, "the check must precede request construction")
        window = APP[i:i + 400]
        self.assertIn("showError", window)
        self.assertIn("return;", window)

    def test_the_operator_is_told_why(self):
        m = re.search(r"function destinationDisagreement[" + BS + r"s" + BS + r"S]{0,4000}?" + BS + r"n  }", APP)
        body = m.group(0)
        for phrase in ("disagree", "does not match", "different work item"):
            self.assertIn(phrase, body)


class LoadedEmptyQueueTest(unittest.TestCase):
    """Round-5 finding: a successful empty load is authoritative."""

    def test_a_loaded_flag_exists_and_is_set_on_success(self):
        self.assertIn("let workItemsLoaded = false;", APP)
        m = re.search(r"async function refreshWorkItems[" + BS + r"s" + BS + r"S]{0,4000}?" + BS + r"n}", APP)
        self.assertIn("workItemsLoaded = true;", m.group(0))

    def test_validation_no_longer_infers_from_length(self):
        m = re.search(r"function bindRouteSelection[" + BS + r"s" + BS + r"S]{0,4000}?" + BS + r"n}", APP)
        body = m.group(0)
        self.assertIn("if (!workItemsLoaded) return null;", body)
        self.assertNotIn("if (!items.length) return null;", body)


class PhaseVersusExecutorTest(unittest.TestCase):
    """Correction item 10: two different facts, two different fields."""

    def test_phase_and_executor_are_separate_functions(self):
        for fn in ("lifecyclePhaseOf", "lifecyclePhaseLabel",
                   "executorRunnerState", "executorStateLabel"):
            self.assertIn("function " + fn, APP)

    def test_phase_comes_from_status_only(self):
        m = re.search(r"function lifecyclePhaseOf[" + BS + r"s" + BS + r"S]{0,4000}?" + BS + r"n}", APP)
        body = m.group(0)
        self.assertIn("it.status", body)
        self.assertNotIn("runner_state", body)

    def test_executor_comes_from_runner_state_only(self):
        m = re.search(r"function executorRunnerState[" + BS + r"s" + BS + r"S]{0,4000}?" + BS + r"n}", APP)
        body = m.group(0)
        self.assertIn("it.runner_state", body)
        self.assertNotIn("last_activity_event", body)

    def test_labels_cover_exactly_the_server_domain(self):
        m = re.search(r"const EXECUTOR_LABELS = {(.*?)};", APP, re.S)
        keys = set(re.findall(r"(\w+):", m.group(1)))
        self.assertEqual(keys, {"active_runner", "waiting_on_council",
                                "waiting_on_operator", "claimed_idle",
                                "stale_or_no_heartbeat", "unowned", "unknown"})

    def test_active_requires_positive_runner_evidence(self):
        m = re.search(r"const EXECUTOR_LABELS = {(.*?)};", APP, re.S)
        self.assertIn('active_runner: "ACTIVE"', m.group(1))
        self.assertNotIn('claimed_idle: "ACTIVE"', m.group(1))

    def test_the_tile_labels_both_separately(self):
        m = re.search(RE_CARD, APP)
        body = m.group(0)
        self.assertIn("Phase ", body)
        self.assertIn("Executor ", body)


class ComposerSubmissionFeedbackTest(unittest.TestCase):
    """Correction item 13: submission must be visible and non-duplicable."""

    def test_the_button_reports_the_in_flight_state(self):
        self.assertIn('sendBtn.textContent = "Sending...";', APP)
        self.assertIn('sendBtn.setAttribute("aria-busy", "true");', APP)

    def test_the_button_is_disabled_while_in_flight(self):
        i = APP.index('sendBtn.textContent = "Sending...";')
        self.assertIn("sendBtn.disabled = true;", APP[i - 200:i])

    def test_the_label_is_captured_not_hardcoded_on_restore(self):
        self.assertIn("const idleLabel = sendBtn.textContent;", APP)
        self.assertIn("sendBtn.textContent = idleLabel;", APP)

    def test_state_is_restored_in_finally_so_it_cannot_strand(self):
        i = APP.index("sendBtn.textContent = idleLabel;")
        window = APP[max(0, i - 400):i]
        self.assertIn("} finally {", window)

    def test_duplicate_submission_is_blocked_for_every_entry_point(self):
        """Click, Enter and Ctrl+Enter all funnel through send()."""
        i = APP.index("async function send() {")
        self.assertIn("if (sending) return;", APP[i:i + 200],
                      "re-entry must be blocked at the top of send()")
        # Ctrl+Enter routes to the same guarded function, not a parallel path.
        self.assertIn("send();", APP[APP.index('e.key === "Enter" && (e.ctrlKey'):][:200])

    def test_the_draft_is_kept_until_durable_success(self):
        i = APP.index("clearDraft(draftKey());")
        before = APP[max(0, i - 1400):i]
        self.assertIn("stored.message !== canonical", before,
                      "the draft may only clear after the durable re-read matches")

    def test_failure_paths_preserve_the_draft(self):
        for msg in ("The draft was kept", "draft was kept"):
            self.assertIn(msg, APP)

    def test_success_shows_the_durable_id_and_destination(self):
        self.assertIn("showPostConfirmation(result);", APP)
        m = re.search(r"function showPostConfirmation[" + BS + r"s" + BS + r"S]{0,4000}?" + BS + r"n}", APP)
        body = m.group(0)
        self.assertIn("message_id", body)
        self.assertIn("thread_id", body)
        self.assertIn("copyIdButton", body)


class UnifiedRoutePolicyTest(unittest.TestCase):
    """Validation and the terminal policy must hold on EVERY route path.

    applyWorkHashRoute() is also the hashchange handler. With the policy living
    only in restoreActiveSelection(), a post-boot link could bind and persist an
    unknown or terminal item with no restoration pass following to correct it.
    """

    def test_one_shared_policy_function_exists(self):
        self.assertIn("function bindRouteSelection", APP)

    def test_the_hashchange_path_validates(self):
        m = re.search(r"function applyWorkHashRoute[\s\S]{0,4000}?\n\}", APP)
        self.assertIn("bindRouteSelection(route.work_item_id)", m.group(0))

    def test_restoration_uses_the_same_policy_not_a_copy(self):
        m = re.search(r"function restoreActiveSelection[\s\S]{0,4000}?\n\}", APP)
        body = m.group(0)
        self.assertIn("bindRouteSelection(deep.work_item_id)", body)
        # The duplicated policy must be gone, or the two can drift again.
        self.assertNotIn("will not be restored on the next refresh", body)

    def test_the_policy_persists_nothing_terminal(self):
        m = re.search(r"function bindRouteSelection[\s\S]{0,4000}?\n\}", APP)
        body = m.group(0)
        self.assertIn("if (!isActiveItem(known))", body)
        self.assertIn("persistSelection(null)", body.split("if (!isActiveItem(known))")[1][:300])

    def test_an_unknown_route_is_cleared_on_every_path(self):
        m = re.search(r"function bindRouteSelection[\s\S]{0,4000}?\n\}", APP)
        branch = m.group(0).split("if (!known)")[1][:500]
        for expected in ("selectTask(null);", "persistSelection(null);", "clearWorkRoute();"):
            self.assertIn(expected, branch)


class RouteErrorLatchTest(unittest.TestCase):
    """Both reviewers flagged the one-way latch: a reported route error must
    not suppress every later transient status for the page lifetime."""

    def test_a_successful_route_resets_the_latch(self):
        m = re.search(r"function bindRouteSelection[\s\S]{0,4000}?\n\}", APP)
        self.assertIn("routeErrorReported = false;", m.group(0))

    def test_explicit_navigation_resets_the_latch(self):
        m = re.search(r"function navigateToWorkItem[\s\S]{0,4000}?\n\}", APP)
        self.assertIn("routeErrorReported = false;", m.group(0))


class EmptyRouteTest(unittest.TestCase):

    def test_empty_work_id_is_invalid_not_absent(self):
        m = re.search(RE_PARSE, APP)
        body = m.group(0)
        self.assertIn("[^&]*", body, "an empty work id must still match")
        self.assertIn("if (!wid) return { malformed: true", body)


class StaleRouteClearingTest(unittest.TestCase):
    """'Clear it and say so' has to be literally true."""

    def test_invalid_route_is_removed_from_the_url(self):
        self.assertIn("function clearWorkRoute", APP)
        m = re.search(r"function clearWorkRoute[\s\S]{0,4000}?\n\}", APP)
        self.assertIn("replaceState", m.group(0))
        for fn in ("restoreActiveSelection", "bindRouteSelection"):
            m2 = re.search(r"function " + fn + r"[\s\S]{0,4000}?\n\}", APP)
            self.assertIn("clearWorkRoute()", m2.group(0),
                          fn + " must drop an unusable route")

    def test_selection_is_cleared_unconditionally(self):
        """A conditional clear could announce 'nothing is selected' while a
        different prior selection survived."""
        r = re.search(r"function bindRouteSelection[\s\S]{0,4000}?\n\}", APP)
        unknown = r.group(0).split("if (!known)")[1][:500]
        self.assertIn("selectTask(null);", unknown)
        self.assertNotIn("selectedWorkItemId === wid", unknown)


class DeepLinkPolicyTest(unittest.TestCase):
    """Both reviewers asked for an explicit terminal-item policy.

    Decision: an EXPLICIT link may open a terminal item, because reviewing
    finished work is the point of sharing a link. That is inspection, not
    active-session restoration, so it is never persisted as the active
    selection. Automatic restoration still excludes terminal items entirely.
    """

    def test_terminal_deep_link_is_not_persisted_as_active(self):
        r = re.search(r"function bindRouteSelection[\s\S]{0,4000}?\n\}", APP)
        body = r.group(0)
        self.assertIn("if (!isActiveItem(known))", body)
        tail = body.split("if (!isActiveItem(known))")[1][:400]
        self.assertIn("persistSelection(null)", tail)
        self.assertIn("showRestoreStatus", tail)

    def test_the_policy_is_documented_where_it_is_enforced(self):
        r = re.search(r"function bindRouteSelection[\s\S]{0,4000}?\n\}", APP)
        self.assertIn("POLICY", r.group(0))

    def test_automatic_restoration_still_excludes_terminal_items(self):
        m = re.search(r"function rankActiveWorkItems[\s\S]{0,4000}?\n\}", APP)
        self.assertIn("filter(isActiveItem)", m.group(0))


class RuntimeCoverageTest(unittest.TestCase):
    """The reviewers asked for coverage that executes, not just source text."""

    def test_a_runtime_harness_exists_and_is_dependency_free(self):
        here = os.path.dirname(os.path.abspath(__file__))
        harness = os.path.join(here, "dom", "session_ux_runtime.mjs")
        self.assertTrue(os.path.isfile(harness))
        src = open(harness, encoding="utf-8").read()
        # Every import must resolve to a Node builtin: no installed package,
        # no browser driver, nothing that needs an install step.
        imports = re.findall(r'from "([^"]+)"', src)
        self.assertTrue(imports, "harness should import something")
        for mod in imports:
            self.assertTrue(mod.startswith("node:"),
                            "non-builtin import would add a dependency: " + mod)
        self.assertNotIn("require(", src)
        here2 = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        self.assertFalse(os.path.exists(os.path.join(here2, "package.json")),
                         "no package manifest may be introduced")

    def test_the_harness_states_its_limitation(self):
        here = os.path.dirname(os.path.abspath(__file__))
        src = open(os.path.join(here, "dom", "session_ux_runtime.mjs"),
                   encoding="utf-8").read()
        self.assertIn("LIMITATION", src)

    def test_the_harness_covers_the_defects_that_actually_occurred(self):
        here = os.path.dirname(os.path.abspath(__file__))
        src = open(os.path.join(here, "dom", "session_ux_runtime.mjs"),
                   encoding="utf-8").read()
        for probe in ("conversationScrollEl", "parseWorkRoute",
                      "convComposerTarget", "rankActiveWorkItems",
                      "wake_pending", "unresolved"):
            self.assertIn(probe, src)


class UnresolvedDestinationTest(unittest.TestCase):
    """A work_item_id WITHOUT a thread_id is the one shape the server's
    target-integrity check cannot validate, because that check compares the
    pair. The UI must fail closed rather than emit it."""

    def test_target_reports_unresolved_instead_of_a_bare_work_item(self):
        m = re.search(r"function convComposerTarget[\s\S]{0,4000}?\n\}", APP)
        body = m.group(0)
        self.assertIn("unresolved: true", body)
        self.assertIn("thread_id: null", body)

    def test_send_is_refused_while_the_destination_is_unresolved(self):
        self.assertIn("preTarget.unresolved", APP)
        i = APP.index("preTarget.unresolved")
        window = APP[i:i + 400]
        self.assertIn("showError", window)
        self.assertIn("return;", window)

    def test_refusal_happens_before_the_body_is_built(self):
        i = APP.index("preTarget.unresolved")
        j = APP.index("const body = Object.assign")
        self.assertLess(i, j)

    def test_banner_does_not_present_an_unresolved_target_as_valid(self):
        self.assertIn("dest-unresolved", APP)
        self.assertIn("dest-unresolved", CSS)


class StaleDeepLinkTest(unittest.TestCase):
    """An unknown deep link must not leave a selected item with no
    queue-backed identity."""

    def test_unknown_deep_link_clears_the_selection(self):
        """The policy now lives in bindRouteSelection(), shared by the boot and
        hashchange paths, so it is asserted where it is implemented."""
        m = re.search(r"function bindRouteSelection[\s\S]{0,4000}?\n\}", APP)
        body = m.group(0)
        self.assertIn("if (!known)", body)
        self.assertIn("selectTask(null)", body)
        self.assertIn("persistSelection(null)", body)

    def test_unknown_deep_link_is_reported_not_silent(self):
        m = re.search(r"function bindRouteSelection[\s\S]{0,4000}?\n\}", APP)
        self.assertIn("showRestoreStatus", m.group(0))


class JumpToLatestWiringTest(unittest.TestCase):
    """The control was rendered but inert: no click handler, no visibility
    logic, and operatorMovedAwayFromLatest was never called."""

    def test_button_is_actually_activated(self):
        m = re.search(r"function initJumpToLatest[\s\S]{0,4000}?\n\}", APP)
        self.assertIsNotNone(m, "initJumpToLatest not found")
        self.assertIn('addEventListener("click", jumpToLatestMessage)', m.group(0))

    def test_visibility_is_driven_by_deliberate_scroll(self):
        m = re.search(r"function initJumpToLatest[\s\S]{0,4000}?\n\}", APP)
        body = m.group(0)
        self.assertIn("operatorMovedAwayFromLatest", body)
        self.assertIn('addEventListener("scroll"', body)

    def test_new_content_does_not_yank_a_deliberate_position(self):
        m = re.search(r"function initJumpToLatest[\s\S]{0,4000}?\n\}", APP)
        body = m.group(0)
        self.assertIn("MutationObserver", body)
        self.assertIn("pill.hidden = false", body)

    def test_it_is_wired_at_boot(self):
        self.assertIn("initJumpToLatest();", APP)

    def test_scroll_target_is_the_element_that_actually_scrolls(self):
        """#conv-detail is overflow-y:visible and grows with its content, so it
        can never report a scroll position. Targeting it made every scroll
        check return false and the pill unreachable."""
        m = re.search(r"function conversationScrollEl[\s\S]{0,4000}?\n\}", APP)
        body = m.group(0)
        self.assertIn("overflowY", body)
        self.assertIn("scrollHeight", body)
        self.assertIn("document.scrollingElement", body)

    def test_content_anchor_and_scroller_are_distinct(self):
        self.assertIn("function conversationAnchorEl", APP)
        m = re.search(r"function initJumpToLatest[\s\S]{0,4000}?\n\}", APP)
        body = m.group(0)
        self.assertIn("conversationAnchorEl()", body)
        self.assertIn("conversationScrollEl()", body)

    def test_scroll_is_observed_by_capture_on_window(self):
        """Scroll does not bubble, but it DOES reach window in the capture
        phase from any target. Binding to the scroller resolved at init went
        stale as soon as layout changed the scrolling ancestor, which is the
        very reason the scroller is resolved lazily inside the handler."""
        m = re.search(r"function initJumpToLatest[\s\S]{0,4000}?\n\}", APP)
        body = m.group(0)
        self.assertIn('window.addEventListener("scroll"', body)
        self.assertIn("}, true);", body)
        self.assertNotIn("scrollEventTargetFor", APP,
                         "the superseded helper must not linger as dead code")


class ObservableFailureTest(unittest.TestCase):
    """Continuity that fails silently is worse than continuity that says so."""

    def test_restoration_failure_is_reported(self):
        self.assertNotIn("refreshWorkItems().then(restoreActiveSelection).catch(() => {});", APP)
        self.assertIn("function showRestoreStatus", APP)
        self.assertIn('id="restore-status"', HTML)

    def test_failure_offers_a_retry(self):
        m = re.search(r"function showRestoreStatus[\s\S]{0,4000}?\n\}", APP)
        body = m.group(0)
        self.assertIn("Retry", body)
        self.assertIn("refreshWorkItems()", body)

    def test_status_is_announced(self):
        i = HTML.index('id="restore-status"')
        window = HTML[max(0, i - 200):i + 200]
        self.assertIn('role="status"', window)
        self.assertIn('aria-live="polite"', window)


class ContextualRailCompletenessTest(unittest.TestCase):
    """No contextual card may render empty when nothing is selected."""

    def test_operator_actions_card_is_inside_the_rail(self):
        """Source ORDER is not containment.

        An earlier version of this change placed the card immediately after the
        rail's closing tag. Every ordering assertion still passed while the
        browser showed the card as a sibling that stayed visible with an empty
        body. This parses the actual element tree instead.
        """
        rail = _block_of(HTML, "session-rail")
        self.assertIn('id="operator-actions-card"', rail)
        self.assertIn('id="next-action-card"', rail)
        self.assertNotIn('id="clearance-card"', rail,
                         "the clearance card is not contextual to a selection")

    def test_only_one_actions_card_exists(self):
        self.assertEqual(HTML.count('id="operator-actions-card"'), 1)


class DeliberateNewConversationTest(unittest.TestCase):
    """The demoted composer must not be a dead end."""

    def test_an_explicit_control_clears_the_selection(self):
        i = APP.index('getElementById("queue-new-btn")')
        self.assertIn("selectTask(null)", APP[i:i + 300])

    def test_the_affordance_is_documented_where_demotion_happens(self):
        head = APP[:APP.index('getElementById("queue-new-btn")')]
        self.assertIn("re-enables the generic composer", head[-700:])

    def test_demotion_is_exactly_reversible(self):
        m = re.search(r"function applyComposerFocus[\s\S]{0,4000}?\n\}", APP)
        self.assertIn("data-prior-tabindex", m.group(0))


class ConversationSurfaceTest(unittest.TestCase):
    """Item 2, behaviour: the conversation surface must actually be revealed.

    Live inspection found openConversationTab() querying a tab control that
    does not exist in this console (the only role="tablist" is the queue filter
    strip), making it a silent no-op. The Work view IS the conversation surface.
    """

    def test_open_conversation_does_not_depend_on_a_nonexistent_tab(self):
        m = re.search(r"function openConversationTab[\s\S]{0,4000}?\n\}", APP)
        body = m.group(0)
        self.assertNotIn('data-tab="conversation"', body)
        self.assertNotIn("#tab-conversation", body)

    def test_open_conversation_reveals_the_work_view(self):
        m = re.search(r"function openConversationTab[\s\S]{0,4000}?\n\}", APP)
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
        m = re.search(r"function applyComposerFocus[\s\S]{0,4000}?\n\}", APP)
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
        m = re.search(r"function messageIdentityRow[\s\S]{0,4000}?\n\}", APP)
        body = m.group(0)
        for field in ("message_id", "thread_id", "work_item_id", "actor", "intent"):
            self.assertIn(field, body)

    def test_copy_controls_are_real_keyboard_reachable_buttons(self):
        m = re.search(r"function copyIdButton[\s\S]{0,4000}?\n\}", APP)
        body = m.group(0)
        self.assertIn('<button type="button"', body)
        self.assertIn("aria-label", body)
        self.assertIn(".copy-id:focus-visible", CSS)

    def test_copy_uses_the_clipboard_api_and_degrades_safely(self):
        m = re.search(r"function copyToClipboard[\s\S]{0,4000}?\n\}", APP)
        body = m.group(0)
        self.assertIn("navigator.clipboard", body)
        self.assertIn("catch", body)

    def test_post_send_confirmation_exposes_the_new_message_id(self):
        self.assertIn("function showPostConfirmation", APP)
        m = re.search(r"function showPostConfirmation[\s\S]{0,4000}?\n\}", APP)
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
        m = re.search(r"function truthfulExecutionState[\s\S]{0,4000}?\n\}", APP)
        body = m.group(0)
        # The event-derived branch is GONE: last_activity_event can never be
        # "message" or "operator_message", so that rank was unreachable.
        self.assertNotIn("operator_message_posted", body)
        self.assertNotIn("last_activity_event", body)
        self.assertNotIn('"running"', body,
                         "a presentation_state of running must not imply an "
                         "executor state; ACTIVE comes from runner_state only")
        # The old ordering assertion (operator-message branch before the
        # running check) no longer applies: BOTH branches are gone. ACTIVE is
        # now derived solely from runner_state === "active_runner", which the
        # server sets only on positive evidence of recent non-claim activity.
        self.assertIn('r === "active_runner"', body)

    def test_unsupported_states_are_not_simulated(self):
        labels = re.search(r"const EXECUTOR_LABELS = \{(.*?)\};", APP, re.S).group(1)
        for deferred in ("EXECUTOR_RESUMED", "MESSAGE_ACKNOWLEDGED", "WAKE_PENDING"):
            self.assertNotIn(deferred, labels,
                             deferred + " requires the Phase 2 wake bridge and "
                             "must not be rendered from current evidence")

    def test_the_obsolete_vocabulary_is_removed_not_left_dead(self):
        """EXECUTION_STATE_LABELS advertised operator_message_posted, a state
        nothing can produce, and executionStateLabel had no remaining caller."""
        self.assertNotIn("EXECUTION_STATE_LABELS", APP)
        self.assertNotIn("function executionStateLabel", APP)
        self.assertNotIn("OPERATOR_MESSAGE_POSTED", APP)

    def test_state_is_actually_RENDERED_on_the_queue_row(self):
        """Scoped to queueCard. Asserting the identifier appeared anywhere in
        app.js was a false positive: the function DECLARATION satisfied it, so
        the test passed while proving nothing about the rendered row."""
        m = re.search(RE_CARD, APP)
        self.assertIsNotNone(m, "queueCard not found")
        body = m.group(0)
        self.assertIn("executorStateLabel(it)", body)
        self.assertIn("lifecyclePhaseLabel(it)", body)
        self.assertIn("Phase ", body)
        self.assertIn("Executor ", body)


class ConsistentDemotionTest(unittest.TestCase):
    """Item 7: demotion must not leave a keyboard trap.

    Live inspection caught aria-hidden="true" on #composer-card while its Send
    button was still tabindex=0 -- reachable by keyboard but absent from the
    accessibility tree.
    """

    def test_demotion_removes_a11y_and_tab_order_together(self):
        m = re.search(r"function applyComposerFocus[\s\S]{0,4000}?\n\}", APP)
        body = m.group(0)
        self.assertIn("inert", body)
        self.assertIn("removeAttribute(\"aria-hidden\")", body)

    def test_fallback_covers_every_focusable_not_just_the_textarea(self):
        m = re.search(r"function applyComposerFocus[\s\S]{0,4000}?\n\}", APP)
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
        # The row is a plain CONTAINER holding a real primary button, because
        # nesting <button> copy controls inside an element that itself claimed
        # role="button" is an invalid interactive pattern.
        m = re.search(RE_CARD, APP)
        body = m.group(0)
        self.assertIn('<button type="button" class="q-open"', body)
        # aria-current, not aria-pressed: activating this NAVIGATES, it does not
        # toggle a state off again.
        self.assertIn("aria-current=", body)
        self.assertNotIn("aria-pressed=", body,
                         "a navigation control must not advertise a toggle "
                         "contract to assistive technology")
        self.assertNotIn('role="button" tabindex="0"', body,
                         "the row must not claim button semantics itself")
        self.assertIn("data-sig=", body,
                      "the tile carries its signature so reconciliation can "
                      "reuse an unchanged node instead of replacing it")

    def test_queue_rows_activate_via_a_native_button(self):
        """Enter and Space come from native <button> semantics.

        An earlier version called preventDefault() on Space to stop page
        scrolling. On a focused button Space's default action IS the
        activation, so that suppressed the very keyboard path the control
        exists to provide. There is now NO queue key handler at all.
        """
        m = re.search(RE_CARD, APP)
        self.assertIn('<button type="button" class="q-open"', m.group(0))
        for k in re.finditer(r'addEventListener\("keydown"', APP):
            window = APP[k.start():k.start() + 500]
            self.assertNotIn("q-open", window,
                             "no keydown handler may intercept the queue button")
        # Space must not be suppressed anywhere for the queue control.
        self.assertNotIn('e.key !== " "', APP,
                         "the Space-suppressing handler must be gone entirely")

    def test_existing_send_shortcuts_are_preserved(self):
        """Ctrl+Enter sends; Shift+Enter still inserts a newline."""
        self.assertIn('e.key === "Enter" && (e.ctrlKey || e.metaKey)', APP)
        self.assertIn("Shift+Enter for a new line, Ctrl+Enter to send", HTML)

    def test_focus_rings_exist_for_new_controls(self):
        for rule in (".copy-id:focus-visible", ".jump-to-latest:focus-visible",
                     '.q-open:focus-visible'):
            self.assertIn(rule, CSS)


class SemanticsPreservedTest(unittest.TestCase):
    """No durable semantics may change for presentation convenience."""

    def test_no_server_side_change_is_required_by_this_slice(self):
        server = os.path.join(STATIC, "..", "server.py")
        self.assertTrue(os.path.exists(server))

    def test_identity_helpers_only_read_existing_fields(self):
        m = re.search(r"function messageIdentityRow[\s\S]{0,4000}?\n\}", APP)
        body = m.group(0)
        for mutator in ("postJSON", "fetch(", "POST"):
            self.assertNotIn(mutator, body)

    def test_restoration_never_mutates_durable_state(self):
        m = re.search(r"function restoreActiveSelection[\s\S]{0,4000}?\n\}", APP)
        body = m.group(0)
        for mutator in ("postJSON", "fetch(", "/api/action"):
            self.assertNotIn(mutator, body)


if __name__ == "__main__":
    unittest.main()
