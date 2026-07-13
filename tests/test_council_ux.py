"""Tests for the readable, scroll-safe council conversation UX.

The UI is vanilla JS with no DOM in the test environment, so (following the
existing repo convention for UI tests) these assert on the static sources: the
scroll-follow logic, the "new messages" affordance, the conversation-timeline
reviewer/failure tagging, and the supporting CSS. Behavior-level correctness of
the pure helpers is asserted by their presence and structure.
"""
import os
import re
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
STATIC = os.path.join(REPO_ROOT, "apps", "control-plane", "static")


def read(name):
    with open(os.path.join(STATIC, name), encoding="utf-8") as fh:
        return fh.read()


class ScrollFollowTests(unittest.TestCase):

    def setUp(self):
        self.appjs = read("app.js")

    def test_scroll_follow_helpers_exist(self):
        for token in ("function isNearBottom", "function renderFollowing",
                      "function newMessagesPill", "NEAR_BOTTOM_PX"):
            self.assertIn(token, self.appjs)

    def test_near_bottom_uses_scroll_geometry(self):
        # Follow only when within a small threshold of the bottom.
        self.assertIn("scrollHeight - el.scrollTop - el.clientHeight", self.appjs)

    def test_live_panels_use_scroll_follow_not_forced_bottom(self):
        # Local communications and the real events feed route through the
        # scroll-follow wrapper rather than force-scrolling on every poll.
        self.assertIn('renderFollowing(el, "comms"', self.appjs)
        self.assertIn('renderFollowing(el, "feed-real"', self.appjs)
        # The inner renderers must NOT force the panel to the bottom themselves.
        inner = self.appjs.split("function renderMessagesInner", 1)[1].split("\nfunction ", 1)[0]
        self.assertNotIn("el.scrollTop = el.scrollHeight", inner)

    def test_scrolled_up_position_is_preserved(self):
        # renderFollowing preserves the prior scrollTop when the reader is not
        # near the bottom (it does not yank them down).
        self.assertIn("el.scrollTop = prevTop", self.appjs)

    def test_conversation_timeline_preserves_position_across_rebuild(self):
        self.assertIn("wasNear ? box.scrollHeight : oldTop", self.appjs)

    def test_new_messages_pill_appears_and_resumes(self):
        self.assertIn("New messages", self.appjs)
        # Clicking scrolls to bottom; returning to the bottom hides it (resume).
        self.assertIn("el.scrollTop = el.scrollHeight; pill.hidden = true;", self.appjs)
        self.assertIn('el.addEventListener("scroll"', self.appjs)


class TranscriptTaggingTests(unittest.TestCase):

    def setUp(self):
        self.appjs = read("app.js")
        self.css = read("style.css")

    def test_reviewer_and_failure_tagging_exists(self):
        self.assertIn("function conversationEntryTag", self.appjs)
        # A real reviewer message is tagged with its parsed verdict/confidence.
        self.assertIn("verdict=([a-z_]+), confidence=([\\d.]+), risk=([a-z]+)", self.appjs)
        self.assertIn("conv-entry-reviewer", self.appjs)
        self.assertIn("conv-entry-reconcile", self.appjs)

    def test_failed_reviewer_is_not_shown_as_participation(self):
        # A "no participation claimed" note is distinct and carries no reviewer badge.
        self.assertIn("no (GPT|Codex) participation claimed", self.appjs)
        self.assertIn("conv-entry-unavailable", self.appjs)
        self.assertIn("not recorded as participation", self.appjs)

    def test_css_present(self):
        for token in (".newmsg-pill", ".conv-entry-reviewer", ".conv-entry-unavailable",
                      ".conv-entry-reconcile", ".conv-entry-tag"):
            self.assertIn(token, self.css)


class NamingAndPrivacyTests(unittest.TestCase):

    def test_no_private_target_or_retired_terms(self):
        _wr = "w" + "rit"
        retired = re.compile("|".join([r"\b" + _wr + r"\b", "vol" + "tex"]), re.I)
        private = re.compile("|".join([r"\b" + "pl" + "ex" + r"\b",
                                       "d:" + re.escape("\\") + "dev"]), re.I)
        for name in ("app.js", "style.css"):
            with self.subTest(file=name):
                text = read(name)
                self.assertIsNone(retired.search(text))
                self.assertIsNone(private.search(text))
        this = open(os.path.abspath(__file__), encoding="utf-8").read()
        self.assertIsNone(retired.search(this))
        self.assertIsNone(private.search(this))


if __name__ == "__main__":
    unittest.main()
