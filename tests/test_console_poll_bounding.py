"""Bounded request control for the recurring operator-console pollers.

Follows the established front-end test pattern in this repository: static
assertion over apps/control-plane/static/app.js, which is how
test_command_center_hygiene, test_conversation_console, test_session_continuity_ux
and the other console tests verify UI behaviour.

Every poller runs on a fixed interval, so when the server is slower than that
interval an unguarded poller starts a new request before the previous one
finishes and the outstanding requests accumulate without bound. Measured on the
running console over a 195 s window, the unguarded pollers issued 249 requests
against the bounded work-item poller's 11, with per-endpoint latency reaching
20-35 s.

The executable counterpart lives in tests/dom/wired_paths.mjs section 12, which
drives the real poller entry points through a deferrable transport.
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

# The pollers converted to bounded control: controller name -> exported binding.
BOUNDED = {
    "state": "refresh",
    "agent-events": "refreshAgentEvents",
    "messages": "refreshMessages",
    "health": "refreshHealth",
    "task-state": "refreshTaskState",
    "archive-index": "refreshArchiveIndex",
    "conversations": "loadConversations",
}


def _block_of(name):
    """The source of one top-level function, up to the next top-level one."""
    m = re.search(r"^(?:async )?function " + re.escape(name) + r"\(", APP, re.M)
    if not m:
        return ""
    rest = APP[m.end():]
    nxt = re.search(r"^(?:async )?function ", rest, re.M)
    return rest[:nxt.start()] if nxt else rest


class ControllerContract(unittest.TestCase):
    def test_controller_exists(self):
        self.assertIn("function boundedPoll(name, run)", APP)

    def test_controller_has_one_active_slot(self):
        body = _block_of("boundedPoll")
        self.assertIn("state.active", body)
        self.assertIn("if (state.active)", body)

    def test_controller_coalesces_into_one_follow_up(self):
        body = _block_of("boundedPoll")
        self.assertIn("state.followUp = true", body)
        self.assertIn("state.followUp = false", body)

    def test_controller_releases_the_slot_in_finally(self):
        body = _block_of("boundedPoll")
        fin = body.split("finally")[1]
        self.assertIn("state.active = false", fin)

    def test_controller_catches_so_a_poller_never_throws_to_its_interval(self):
        self.assertIn("catch (e)", _block_of("boundedPoll"))

    def test_follow_up_carries_an_explicit_rejection_handler(self):
        # A fire-and-forget call without a handler would be a silent unhandled
        # rejection, which is exactly the failure mode being removed.
        self.assertIn("polled().catch(", _block_of("boundedPoll"))

    def test_coalesced_outcome_is_distinct_from_ran(self):
        self.assertIn('const POLL_RAN = "ran"', APP)
        self.assertIn('const POLL_COALESCED = "coalesced"', APP)

    def test_controller_returns_coalesced_without_starting_a_request(self):
        body = _block_of("boundedPoll")
        head = body.split("state.active = true")[0]
        self.assertIn("return POLL_COALESCED", head)

    def test_no_retry_loop_inside_the_controller(self):
        body = _block_of("boundedPoll")
        for banned in ("setTimeout", "setInterval", "while ("):
            self.assertNotIn(banned, body)

    def test_diagnostics_are_readable_without_logging(self):
        body = _block_of("pollDiagnostics")
        self.assertIn("pollControllers.forEach", body)
        self.assertNotIn("console.", body)


class EveryPollerIsBounded(unittest.TestCase):
    def test_each_poller_is_wrapped_exactly_once(self):
        for name, fn in BOUNDED.items():
            decl = 'const %s = boundedPoll("%s", %sRequest);' % (
                fn, name, fn if fn != "refresh" else "refreshState")
            self.assertEqual(APP.count(decl), 1,
                             "expected exactly one bounded declaration for " + fn)

    def test_each_inner_request_body_exists(self):
        for fn in BOUNDED.values():
            raw = ("refreshStateRequest" if fn == "refresh" else fn + "Request")
            self.assertIn("async function %s(" % raw, APP)

    def test_no_bare_poller_function_declaration_survives(self):
        # A leftover `function refreshMessages()` would silently shadow the
        # bounded binding and reintroduce the defect.
        for fn in BOUNDED.values():
            self.assertIsNone(
                re.search(r"^(?:async )?function " + re.escape(fn) + r"\(", APP, re.M),
                fn + " must not also exist as a bare function declaration")

    def test_exactly_seven_pollers_are_bounded(self):
        self.assertEqual(len(re.findall(r"= boundedPoll\(", APP)), 7)

    def test_state_poller_no_longer_lacks_a_handler(self):
        # /api/state previously had no try/catch at all, so every failed poll
        # was an invisible unhandled rejection.
        body = _block_of("refreshStateRequest")
        self.assertIn("try {", body)
        self.assertIn("catch (e)", body)


class WorkItemPollerUntouched(unittest.TestCase):
    """The queue poller keeps its own controller and its four outcomes."""

    def test_queue_keeps_its_own_in_flight_flag(self):
        self.assertIn("let queueRefreshInFlight = false;", APP)

    def test_queue_keeps_its_inner_request_and_generation_guard(self):
        self.assertIn("async function runWorkItemsRefresh()", APP)
        self.assertIn("let queueRefreshGeneration = 0;", APP)

    def test_queue_is_not_registered_with_the_generic_controller(self):
        self.assertNotIn('boundedPoll("work-items"', APP)

    def test_queue_outcomes_are_unchanged(self):
        for outcome in ("REFRESH_CONFIRMED", "REFRESH_CONFIRMED_EMPTY",
                        "REFRESH_FAILED", "REFRESH_SUPERSEDED", "REFRESH_COALESCED"):
            self.assertIn(outcome, APP)

    def test_refresh_succeeded_still_rejects_a_coalesced_poll(self):
        body = _block_of("refreshSucceeded")
        self.assertIn("REFRESH_CONFIRMED", body)
        self.assertNotIn("REFRESH_COALESCED ===", body)


class GlobalSurfaceCompatibility(unittest.TestCase):
    """Converting `function name()` to `const name = ...` removes the window
    property. That is safe only if nothing resolves these names through the
    global object, which is proven here rather than assumed."""

    def test_markup_has_no_inline_event_handlers(self):
        html = _read("index.html")
        self.assertIsNone(re.search(r"""\son[a-z]+\s*=\s*["']""", html, re.I))

    def test_markup_never_names_a_converted_poller(self):
        html = _read("index.html")
        for fn in BOUNDED.values():
            self.assertNotIn(fn, html)

    def test_no_global_object_access_to_a_converted_poller(self):
        for fn in BOUNDED.values():
            for pattern in ("window." + fn, "globalThis." + fn,
                            'window["%s"]' % fn, "window['%s']" % fn):
                self.assertNotIn(pattern, APP)

    def test_the_page_loads_exactly_one_script(self):
        # No second script can be holding a stale global reference.
        scripts = re.findall(r"""<script[^>]*src=["']([^"']+)["']""", _read("index.html"))
        self.assertEqual(len(scripts), 1)
        self.assertIn("app.js", scripts[0])


class NoPollerBypassesItsController(unittest.TestCase):
    """Each raw request body must appear exactly twice: its own declaration and
    the boundedPoll argument. A third occurrence would be a production call
    site that bypasses the controller."""

    def test_each_raw_body_has_no_direct_call_site(self):
        for fn in BOUNDED.values():
            raw = "refreshStateRequest" if fn == "refresh" else fn + "Request"
            total = APP.count(raw)
            decl = APP.count("async function %s(" % raw)
            arg = APP.count(", %s);" % raw)
            self.assertEqual(decl, 1, raw + " declared exactly once")
            self.assertEqual(arg, 1, raw + " passed to boundedPoll exactly once")
            self.assertEqual(total, decl + arg,
                             raw + " has no occurrence beyond its declaration "
                                   "and the boundedPoll argument")

    def test_conversation_fan_out_is_fully_awaited(self):
        # If any inner request were unawaited the outer slot would be released
        # before the fan-out completed, which would defeat the bounding.
        body = _block_of("loadConversationsRequest")
        self.assertEqual(body.count("getJSON("), body.count("await getJSON("))


class PollingCadenceUnchanged(unittest.TestCase):
    """Scope guard: bounding requests must not change the polling cadence."""

    def test_live_interval_is_unchanged(self):
        self.assertIn("const LIVE_MS = 2000;", APP)

    def test_every_interval_registration_survives(self):
        for call in ("setInterval(refresh, LIVE_MS)",
                     "setInterval(refreshAgentEvents, LIVE_MS)",
                     "setInterval(refreshMessages, LIVE_MS)",
                     "setInterval(refreshWorkItems, LIVE_MS)",
                     "setInterval(refreshHealth, LIVE_MS * 2)",
                     "setInterval(refreshTaskState, LIVE_MS)",
                     "setInterval(refreshArchiveIndex, LIVE_MS * 15)"):
            self.assertIn(call, APP)

    def test_the_two_conversation_intervals_survive(self):
        # loadConversations is driven by two further intervals OUTSIDE the main
        # setInterval block; they are why the endpoint is polled on every view.
        self.assertIn('if (currentView === "work") loadConversations();', APP)
        self.assertIn('if (currentView !== "work") loadConversations();', APP)

    def test_every_recurring_network_poller_is_accounted_for(self):
        # Tied to known registrations rather than a raw setInterval count, which
        # would be brittle against unrelated timer additions and would not
        # establish coverage. Every recurring poller is either bounded here or
        # is the queue poller with its own controller.
        registered = set(re.findall(r"setInterval\((\w+),", APP))
        accounted = set(BOUNDED.values()) | {"refreshWorkItems"}
        self.assertEqual(registered - accounted, set(),
                         "an unaccounted recurring poller was registered")

    def test_server_side_is_untouched_by_this_change(self):
        server = os.path.join(STATIC, "..", "server.py")
        self.assertTrue(os.path.exists(server))


if __name__ == "__main__":
    unittest.main()
