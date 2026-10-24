# Contributing

Contributions are welcome: bug reports, testing on other hardware, documentation and code.

## Wanted

- **macOS and Linux support.** The DeckLink SDK covers both. A port needs:
  - a build of `native/` for that platform, using the SDK's `Mac/include` or
    `Linux/include` headers and its dispatch code instead of COM and MIDL
  - a screen-capture path for Mirror Viewport in place of DXGI desktop duplication
    (Offscreen capture is plain Blender `gpu` code and should work unchanged)
  - a `platforms` entry in the manifest and a CI job for the platform
- 10-bit output, key/fill (external keying) and audio
- Testing reports from other DeckLink, Intensity and UltraStudio models. Include the mode
  list printed by `python native\test_output.py`.

## Reporting a bug

Please include:

- Blender version, Windows version, Desktop Video version and device model
- the output's Capture, Shading and Video Mode settings, and the Status lines from the
  panel
- anything printed in Blender's system console (**Window > Toggle System Console**)

## Making changes

1. Fork the repository and create a branch.
2. Build and install from source as described in [docs/building.md](docs/building.md).
3. Keep the style of the surrounding code. Python follows PEP 8 with 100-column lines.
4. If you change the C ABI in `native/bdo_decklink.h`, bump `BDO_API_VERSION` there and
   `API_VERSION` in `blender_decklink_output/native.py` together.
5. Run `python scripts\package.py` to check the manifest and sources, then test in Blender
   with a real device if you can. Say in the pull request what you tested and on which
   hardware.
6. Add a line to `CHANGELOG.md` under the `## [Unreleased]` heading.

CI builds every pull request. The zip it produces is attached to the workflow run, so
reviewers can try it.

## Licence

By contributing, you agree that your contribution is licensed under the project's licence:
GPL-3.0-or-later with the additional permission in [LICENSE-EXCEPTION.txt](LICENSE-EXCEPTION.txt).
Don't submit code you can't license that way, such as Blackmagic SDK sample code, which
its EULA only allows for internal use.
