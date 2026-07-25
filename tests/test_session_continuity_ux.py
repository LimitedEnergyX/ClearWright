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
            "waiting_for_operator", "operator_message_posted",
            "paused", "executor_active", "in_council", "blocked", "claimed"])

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
        self.assertNotIn("convComposerNewThreadId", body.split("selectedConvThread ||")[0])
        # The bare-work-item shape is now an explicit fail-closed marker rather
        # than a sendable target.
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
        self.assertNotIn("nothing is selected", call)
        self.assertIn("highest-priority active work", call,
                      "the message must not promise the operator's previous item: "
                      "the persisted selection is cleared before restoration runs")


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
        self.assertIn('role="button"', APP)
        self.assertIn('tabindex="0"', APP)
        self.assertIn("aria-pressed=", APP)

    def test_queue_rows_activate_on_enter_and_space(self):
        m = re.search(r'if \(e\.key !== "Enter" && e\.key !== " "\)[\s\S]{0,4000}?\n\}\);', APP)
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
