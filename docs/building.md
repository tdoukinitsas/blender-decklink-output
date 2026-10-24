# Building from source

The add-on is Python, plus one native DLL (`bdo_decklink.dll`) that talks to the DeckLink
COM API and does the screen capture for Mirror Viewport. The Python side loads it with
`ctypes`, so it doesn't depend on Blender's Python version.

## Prerequisites

- Windows 10 or 11, x64
- Visual Studio 2022 (or its Build Tools) with the **Desktop development with C++**
  workload. `build.bat` finds it with `vswhere`.
- Python 3.11 or newer, for the packaging and install scripts
- Blackmagic Desktop Video, to run and test the result

The DeckLink SDK headers (`Win/include`, SDK 15.3) are vendored in
`third_party/decklink-sdk`, so you don't need to download the SDK. To build against a
different SDK version, set `DECKLINK_SDK_DIR` to the folder that contains
`Win\include\DeckLinkAPI.idl`:

```bat
set DECKLINK_SDK_DIR=C:\SDKs\Blackmagic DeckLink SDK 15.3
```

## Build the DLL

```bat
native\build.bat
```

It compiles the SDK's IDL with MIDL into `native\build\`, builds `bdo_decklink.dll` with
the static runtime (`/MT`, so there's no VC++ redistributable to install), and copies it
to `blender_decklink_output\bin\`.

## Try it without Blender

`native\test_output.py` drives the DLL directly through the add-on's `ctypes` wrapper:

```bat
python native\test_output.py                              :: list devices and modes
python native\test_output.py "Intensity Pro 4K" 1080p25 10
python native\test_output.py "Intensity Pro 4K" 1080p25 10 --push-fps 7
```

The second command sends moving colour bars for 10 seconds and prints the card's frame
counters. The third pushes slower than the card runs. The output should keep running
smoothly with no late or dropped frames.

## Install into your Blender (development)

```bat
python scripts\install.py               :: copy into every Blender 5.x user folder
python scripts\install.py --build       :: rebuild the DLL first
python scripts\install.py --blender 5.2 :: one version only
python scripts\install.py --uninstall
```

This copies `blender_decklink_output\` to
`%APPDATA%\Blender Foundation\Blender\<version>\extensions\user_default\`. Enable it once
under **Preferences > Add-ons**. Restart Blender to load a rebuilt DLL.

## Package the extension zip

```bat
python scripts\package.py
```

This validates the manifest, byte-compiles the Python sources as a syntax check, and
writes `dist\blender_decklink_output-<version>-windows-x64.zip` with
`blender_manifest.toml` at the zip root, the DLL, `LICENSE`, `LICENSE-EXCEPTION.txt` and
`THIRD_PARTY_NOTICES.md`. To check the zip with Blender's own validator as well, run:

```bat
blender --command extension validate dist\blender_decklink_output-1.0.0-windows-x64.zip
```

## Continuous integration

[.github/workflows/build.yml](../.github/workflows/build.yml) runs on every push to
`main`, on pull requests and on demand (**Actions > Build > Run workflow**). On a
`windows-2022` runner it:

1. checks the manifest and Python sources (`package.py --check`)
2. builds the DLL with `native\build.bat` against the vendored SDK headers
3. packages the zip and uploads it as a workflow artifact

To download a build, open the workflow run and use the zip under **Artifacts**. GitHub
wraps artifacts in a second zip, so extract that once to get the extension zip.

## Making a release

1. List the changes under `## [Unreleased]` in `CHANGELOG.md` as you go.
2. Bump the version:

   ```bat
   python scripts\bump_version.py patch   :: or minor, major, or an exact version like 1.2.3
   ```

   This sets `version` in `blender_decklink_output/blender_manifest.toml` and renames the
   `## [Unreleased]` section to `## [X.Y.Z] - YYYY-MM-DD`, leaving a new empty
   `## [Unreleased]` above it. Add `-n` to preview without writing. For the first release,
   run it with `1.0.0`: the manifest is already at 1.0.0, so only the changelog is dated.
3. Commit, then tag and push:

   ```bat
   git tag vX.Y.Z
   git push origin main vX.Y.Z
   ```

4. The workflow checks that the tag matches the manifest version, builds the zip and
   creates a **draft** GitHub release with the zip attached. The release notes come from
   the changelog section plus the install steps in `.github/release-footer.md`.
5. Review the draft on the Releases page and publish it.

## Native API version

`native/bdo_decklink.h` defines `BDO_API_VERSION`, and `blender_decklink_output/native.py`
checks it when the DLL loads. Bump both together whenever the C ABI changes (functions,
structs or flags), so a stale DLL is reported instead of crashing.
