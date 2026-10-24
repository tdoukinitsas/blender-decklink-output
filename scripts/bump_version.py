# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Thomas Doukinitsas

"""Bump the add-on version and date the changelog.

    python scripts/bump_version.py patch      1.0.0 -> 1.0.1
    python scripts/bump_version.py minor      1.0.0 -> 1.1.0
    python scripts/bump_version.py major      1.0.0 -> 2.0.0
    python scripts/bump_version.py 1.2.3      set an exact version
    python scripts/bump_version.py patch -n   show what would change, write nothing

Sets `version` in blender_manifest.toml and renames the `## [Unreleased]` section of
CHANGELOG.md to `## [X.Y.Z] - YYYY-MM-DD`, with a fresh empty `## [Unreleased]` above it.
An exact version may equal the current one, which only dates the changelog (used for the
first release). It does not commit or tag.
"""

import argparse
import datetime
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
MANIFEST = ROOT / "blender_decklink_output" / "blender_manifest.toml"
CHANGELOG = ROOT / "CHANGELOG.md"
VERSION_LINE = re.compile(r'^version = "(\d+)\.(\d+)\.(\d+)"$', re.MULTILINE)
UNRELEASED = re.compile(r"^## \[Unreleased\][^\n]*$", re.MULTILINE)


def fail(message):
    raise SystemExit("[bump] error: " + message)


def next_version(current, part):
    major, minor, patch = current
    if part == "major":
        return major + 1, 0, 0
    if part == "minor":
        return major, minor + 1, 0
    if part == "patch":
        return major, minor, patch + 1
    match = re.fullmatch(r"v?(\d+)\.(\d+)\.(\d+)", part)
    if not match:
        fail("%r is not major, minor, patch or MAJOR.MINOR.PATCH" % part)
    target = tuple(int(n) for n in match.groups())
    if target < current:
        fail("%s is lower than the current version %s" % (dotted(target), dotted(current)))
    return target


def dotted(version):
    return "%d.%d.%d" % version


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("part", help="major, minor, patch or an exact version like 1.2.3")
    parser.add_argument("-n", "--dry-run", action="store_true")
    args = parser.parse_args()

    manifest = MANIFEST.read_text(encoding="utf-8")
    match = VERSION_LINE.search(manifest)
    if not match:
        fail("no MAJOR.MINOR.PATCH version line in %s" % MANIFEST.name)
    current = tuple(int(n) for n in match.groups())
    target = next_version(current, args.part.lower())
    if target == current and args.part.lower() in ("major", "minor", "patch"):
        fail("version did not change")
    version = dotted(target)

    changelog = CHANGELOG.read_text(encoding="utf-8")
    if re.search(r"^## \[%s\]" % re.escape(version), changelog, re.MULTILINE):
        fail("CHANGELOG.md already has a %s section" % version)
    if not UNRELEASED.search(changelog):
        fail("CHANGELOG.md has no '## [Unreleased]' section to release")
    heading = "## [%s] - %s" % (version, datetime.date.today().isoformat())
    changelog = UNRELEASED.sub("## [Unreleased]\n\n" + heading, changelog, count=1)
    manifest = VERSION_LINE.sub('version = "%s"' % version, manifest, count=1)

    print("[bump] %s -> %s" % (dotted(current), version))
    print("[bump] CHANGELOG.md: %s" % heading)
    if args.dry_run:
        print("[bump] dry run, nothing written")
        return
    MANIFEST.write_text(manifest, encoding="utf-8", newline="\n")
    CHANGELOG.write_text(changelog, encoding="utf-8", newline="\n")
    print("[bump] next: commit, then git tag v%s && git push origin main v%s"
          % (version, version))


if __name__ == "__main__":
    sys.exit(main())
