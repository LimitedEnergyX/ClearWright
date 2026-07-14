"""Public-repository naming gate: no retired terms and no private downstream
target names anywhere docs, the skill, or this mission's new source/test files
could plausibly have picked one up. Complements the narrower checks already in
tests/test_use_cw_e2e.py and tests/test_archive.py.
"""
import os
import re
import unittest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DOCS = os.path.join(REPO_ROOT, "docs")

# Built from fragments (not literal), matching the convention already used
# elsewhere in this suite, so the pattern itself never appears as plain text.
_WR = "w" + "rit"
RETIRED = re.compile("|".join([r"\b" + _WR + r"\b", "vol" + "tex"]), re.I)
PRIVATE = re.compile("|".join([r"\b" + "pl" + "ex" + r"\b",
                               "d:" + re.escape("\\") + "dev"]), re.I)


def _all_markdown_files(directory):
    out = []
    for root, _dirs, names in os.walk(directory):
        for name in names:
            if name.endswith(".md"):
                out.append(os.path.join(root, name))
    return out


class DocsNamingGateTests(unittest.TestCase):

    def test_every_doc_is_clean(self):
        for path in _all_markdown_files(DOCS):
            with self.subTest(file=os.path.relpath(path, REPO_ROOT)):
                with open(path, encoding="utf-8") as fh:
                    text = fh.read()
                self.assertIsNone(RETIRED.search(text), path)
                self.assertIsNone(PRIVATE.search(text), path)

    def test_changelog_is_clean(self):
        # A prior entry referenced the private target by name (predating this
        # gate); this scan is what keeps that class of leak from recurring.
        path = os.path.join(REPO_ROOT, "CHANGELOG.md")
        with open(path, encoding="utf-8") as fh:
            text = fh.read()
        self.assertIsNone(RETIRED.search(text), path)
        self.assertIsNone(PRIVATE.search(text), path)

    def test_skill_is_clean(self):
        path = os.path.join(REPO_ROOT, ".claude", "skills", "use-cw", "SKILL.md")
        with open(path, encoding="utf-8") as fh:
            text = fh.read()
        self.assertIsNone(RETIRED.search(text))
        self.assertIsNone(PRIVATE.search(text))


class Commit5SourceNamingGateTests(unittest.TestCase):
    """The new tools/tests from this mission specifically (gate, writer-lock,
    archive, message-integrity, UI) -- a targeted check in addition to the
    docs sweep above."""

    FILES = [
        "tools/clearwright_gate.py", "tools/clearwright_writer_lock.py",
        "tools/clearwright_archive.py", "tools/clearwright_message.py",
        "tools/clearwright_review_council.py", "tools/clearwright_use_cw.py",
        "tools/clearwright_work.py", "apps/control-plane/server.py",
        "apps/control-plane/static/app.js", "apps/control-plane/static/index.html",
        "apps/control-plane/static/style.css",
        "tests/test_plan_gate.py", "tests/test_writer_lock.py",
        "tests/test_archive.py", "tests/test_message_integrity.py",
        "tests/test_commit4_ui.py", "tests/test_council_profiles.py",
        "tests/fixtures/archive_inventory.json",
        "docs/ARCHIVE_OPERATION.md",
    ]

    def test_every_file_is_clean(self):
        for rel in self.FILES:
            path = os.path.join(REPO_ROOT, *rel.split("/"))
            with self.subTest(file=rel):
                with open(path, encoding="utf-8") as fh:
                    text = fh.read()
                self.assertIsNone(RETIRED.search(text), rel)
                self.assertIsNone(PRIVATE.search(text), rel)


if __name__ == "__main__":
    unittest.main()
