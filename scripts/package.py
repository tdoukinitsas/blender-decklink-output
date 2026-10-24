# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Thomas Doukinitsas

"""Package the add-on as a Blender extension zip.

    python scripts/package.py                 dist/blender_decklink_output-<version>-windows-x64.zip
    python scripts/package.py --tag v1.0.0    also check that the tag matches the manifest
    python scripts/package.py --check         only validate the manifest and sources

The zip has blender_manifest.toml at its root, as Blender's Install from Disk expects, and
carries the native DLL (build it first with native/build.bat) plus the licence files. Prints the zip path on the last line.
"""

import argparse
import compileall
import os
import re
import sys
import tomllib
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SOURCE = ROOT / "blender_decklink_output"
MANIFEST = SOURCE / "blender_manifest.toml"
DLL = Path("bin") / "bdo_decklink.dll"
EXTRA = ("LICENSE", "LICENSE-EXCEPTION.txt", "THIRD_PARTY_NOTICES.md")
REQUIRED = ("schema_version", "id", "version", "name", "tagline", "maintainer", "type",
            "blender_version_min", "license")


def fail(message):
    raise SystemExit("[package] error: " + message)


def read_manifest():
    with open(MANIFEST, "rb") as handle:
        manifest = tomllib.load(handle)
    missing = [key for key in REQUIRED if key not in manifest]
    if missing:
        fail("manifest is missing " + ", ".join(missing))
    if manifest["id"] != SOURCE.name:
        fail("manifest id %r does not match the folder %r" % (manifest["id"], SOURCE.name))
    if not re.fullmatch(r"\d+\.\d+\.\d+", manifest["version"]):
        fail("version %r is not MAJOR.MINOR.PATCH" % manifest["version"])
    if len(manifest["tagline"]) > 64 or manifest["tagline"].endswith((".", "!", "?")):
        fail("tagline must be at most 64 characters without closing punctuation")
    return manifest


def check_sources():
    if not compileall.compile_dir(str(SOURCE), quiet=1, force=True, legacy=False,
                                  ddir=SOURCE.name):
        fail("Python sources do not compile")


def source_files():
    for path in sorted(SOURCE.rglob("*")):
        relative = path.relative_to(SOURCE)
        if path.is_dir() or "__pycache__" in relative.parts or path.suffix == ".pyc":
            continue
        yield path, relative.as_posix()


def build_zip(manifest):
    if not (SOURCE / DLL).is_file():
        fail("%s is missing: run native\\build.bat first" % (SOURCE / DLL))
    platform = "-".join(manifest.get("platforms", ["any"]))
    target = ROOT / "dist" / ("%s-%s-%s.zip" % (manifest["id"], manifest["version"], platform))
    target.parent.mkdir(exist_ok=True)
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as archive:
        for path, name in source_files():
            archive.write(path, name)
        for name in EXTRA:
            archive.write(ROOT / name, name)
    return target


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--tag", help="git tag being released, e.g. v1.0.0")
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    manifest = read_manifest()
    if args.tag and args.tag.removeprefix("v") != manifest["version"]:
        fail("tag %s does not match manifest version %s" % (args.tag, manifest["version"]))
    check_sources()
    print("[package] %s %s OK" % (manifest["id"], manifest["version"]))
    if args.check:
        return
    target = build_zip(manifest)
    github_output = os.environ.get("GITHUB_OUTPUT")
    if github_output:
        with open(github_output, "a", encoding="utf-8") as handle:
            handle.write("zip=%s\nname=%s\nversion=%s\n"
                         % (target.as_posix(), target.name, manifest["version"]))
    print(target)


if __name__ == "__main__":
    sys.exit(main())
