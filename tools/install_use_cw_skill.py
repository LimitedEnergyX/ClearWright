#!/usr/bin/env python3
"""
tools/install_use_cw_skill.py: safely install the Use CW personal skill.

Copies the repository skill (.claude/skills/use-cw/SKILL.md) to the user's
personal skills directory (~/.claude/skills/use-cw/SKILL.md by default). The
install is safe: it backs up any existing version first, writes atomically,
never touches unrelated skills, prints the installed path and version, and
verifies the installed file matches the repository version.

Exit codes: 0 installed (or already current), 1 verification failed, 2 argument
or IO error.
"""
import argparse
import json
import os
import re
import shutil
import sys
from datetime import datetime, timezone

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SOURCE = os.path.join(REPO_ROOT, ".claude", "skills", "use-cw", "SKILL.md")


def parse_version(text):
    m = re.search(r"^version:\s*([^\s]+)\s*$", text or "", re.M)
    return m.group(1) if m else "unknown"


def default_target():
    return os.path.join(os.path.expanduser("~"), ".claude", "skills", "use-cw", "SKILL.md")


def _atomic_write(path, text):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)


def install(source=SOURCE, target=None, dry_run=False, stamp=None):
    """Install the skill. Returns a result dict. Backs up an existing, differing
    target first; skips the write when the target is already current."""
    target = target or default_target()
    if not os.path.isfile(source):
        return {"ok": False, "error": "source skill not found: {}".format(source)}
    with open(source, encoding="utf-8") as fh:
        source_text = fh.read()
    version = parse_version(source_text)

    existing = None
    backup = None
    if os.path.isfile(target):
        with open(target, encoding="utf-8") as fh:
            existing = fh.read()
        if existing == source_text:
            return {"ok": True, "installed_path": target, "version": version,
                    "status": "already_current", "backup": None, "verified": True}
        # Back up the differing existing version (never delete unrelated files).
        stamp = stamp or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S")
        backup = target + ".bak-" + stamp
        if not dry_run:
            shutil.copy2(target, backup)

    if dry_run:
        return {"ok": True, "installed_path": target, "version": version,
                "status": "dry_run", "backup": backup, "verified": False}

    _atomic_write(target, source_text)
    with open(target, encoding="utf-8") as fh:
        verified = fh.read() == source_text
    return {"ok": bool(verified), "installed_path": target, "version": version,
            "status": "installed" if verified else "verification_failed",
            "backup": backup, "verified": verified}


def main():
    parser = argparse.ArgumentParser(
        prog="install_use_cw_skill",
        description="Safely install the Use CW personal skill (backup, atomic, verify).")
    parser.add_argument("--target", default=None,
                        help="Target SKILL.md path (default: ~/.claude/skills/use-cw/SKILL.md).")
    parser.add_argument("--dry-run", action="store_true", help="Show what would happen; write nothing.")
    parser.add_argument("--json", action="store_true", help="Print compact JSON only.")
    args = parser.parse_args()

    result = install(target=args.target, dry_run=args.dry_run)
    if args.json:
        print(json.dumps(result))
    else:
        if not result.get("ok"):
            print("REFUSED: {}".format(result.get("error", "verification failed")), file=sys.stderr)
        else:
            print("Skill: use-cw  version {}".format(result.get("version")))
            print("Installed path: {}".format(result.get("installed_path")))
            print("Status: {}".format(result.get("status")))
            if result.get("backup"):
                print("Backed up prior version to: {}".format(result["backup"]))
            print("Verified match: {}".format(result.get("verified")))
    if not result.get("ok"):
        return 1 if "error" not in result else 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
