# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Thomas Doukinitsas

"""Install the add-on from a source checkout into Blender's user extensions.

    python scripts/install.py                  install into every Blender 5.x user folder
    python scripts/install.py --build          rebuild the DLL first
    python scripts/install.py --blender 5.2    install into one Blender version only
    python scripts/install.py --uninstall      remove the installed copies

Builds the DLL first if it is missing. Blender loads a copy of the DLL from %TEMP%, so
installing over a running Blender works; restart Blender (or disable and enable the
add-on) to load the new version. End users should install the release zip instead
(docs/installation.md).
"""

import argparse
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SOURCE = ROOT / "blender_decklink_output"
DLL = SOURCE / "bin" / "bdo_decklink.dll"
MIN_VERSION = (5, 0)
NAME = "Blender Live Camera Output for Blackmagic Design DeckLink"


def log(message):
    print("[install] " + message)


def build():
    result = subprocess.run(["cmd", "/c", str(ROOT / "native" / "build.bat")])
    if result.returncode != 0 or not DLL.is_file():
        raise SystemExit("native build failed")


def targets(only):
    root = Path(os.environ["APPDATA"]) / "Blender Foundation" / "Blender"
    found = []
    for folder in sorted(root.glob("*")) if root.is_dir() else ():
        match = re.fullmatch(r"(\d+)\.(\d+)", folder.name)
        if not match or (int(match[1]), int(match[2])) < MIN_VERSION:
            continue
        if only and folder.name != only:
            continue
        found.append(folder / "extensions" / "user_default" / SOURCE.name)
    return found


def install(only):
    if not DLL.is_file():
        build()
    found = targets(only)
    if not found:
        log("no Blender %s user folder found (start that Blender once first)"
            % (only or "5.x"))
    for target in found:
        if target.exists():
            shutil.rmtree(target)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(SOURCE, target, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        log("installed: %s" % target)
    if found:
        log("  enable it once in Preferences > Add-ons > %s" % NAME)


def uninstall(only):
    for target in targets(only):
        if target.exists():
            shutil.rmtree(target)
            log("removed %s" % target)


def main():
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--build", action="store_true")
    parser.add_argument("--uninstall", action="store_true")
    parser.add_argument("--blender", metavar="VERSION", help="only this Blender version, e.g. 5.2")
    args = parser.parse_args()
    if sys.platform != "win32":
        raise SystemExit("Windows only")
    if args.uninstall:
        uninstall(args.blender)
        return
    if args.build:
        build()
    install(args.blender)


if __name__ == "__main__":
    main()
