# Live Camera Output for Blackmagic Design DeckLink

A Blender add-on that sends a scene camera's picture live to a Blackmagic Design DeckLink
(or Intensity / UltraStudio) card, so you can see the camera on a monitor, a projector or a
video switcher while you work.

- Several outputs at once, each with its own device, video mode, camera and shading.
- **Offscreen** capture renders the camera at the output resolution, independent of any
  viewport. **Mirror Viewport** capture sends exactly what a 3D viewport shows, Cycles
  included.
- The card runs on its own clock and repeats the newest picture, so the signal never
  stalls, whatever Blender's frame rate. The output resolution and frame rate do not have
  to match the scene.
- Fit, fill or distort the camera frame onto the output raster.
- Other add-ons can draw over the output through a small hook.

The controls are in **Properties > Output > Live Camera Output**.

## Requirements

| | |
|---|---|
| Blender | 5.0 or newer (developed and tested on 5.2) |
| OS | Windows 10 or 11, 64-bit (macOS and Linux not yet supported, [help wanted](CONTRIBUTING.md)) |
| Driver | [Blackmagic Desktop Video](https://www.blackmagicdesign.com/support/family/capture-and-playback) |
| Hardware | A Blackmagic DeckLink, Intensity or UltraStudio device with playback |

## Install

1. Download `blender_decklink_output-<version>-windows-x64.zip` from the
   [Releases](../../releases) page. Don't unzip it.
2. In Blender, open **Edit > Preferences > Get Extensions**, click the **⌄** menu at the
   top right and choose **Install from Disk…**, then pick the zip.
3. Open **Properties > Output** and expand **Live Camera Output**.

[docs/installation.md](docs/installation.md) has more detail, along with how to update,
uninstall and troubleshoot.

## Documentation

- [Installation](docs/installation.md)
- [Usage](docs/usage.md): every option, explained
- [Building from source](docs/building.md): the native DLL, packaging and CI
- [How it works](docs/architecture.md): design notes and the overlay hook for other add-ons
- [Contributing](CONTRIBUTING.md)

## Repository layout

```
blender_decklink_output/   the add-on (Python; bin/ receives the built DLL)
native/                    C++ bridge to the DeckLink API, build.bat, test_output.py
third_party/decklink-sdk/  DeckLink SDK headers (Win/include) used by the build
scripts/                   package.py (extension zip), install.py (developer install),
                           bump_version.py (release version bump)
.github/workflows/         CI: builds the DLL and the zip; drafts a release on tags
docs/                      documentation
```

## Platform support

**Windows only for now.** The native bridge uses the DeckLink COM API and DXGI desktop
duplication, both Windows-specific. Blackmagic's SDK also supports macOS and Linux, and the
Python side of the add-on is platform-neutral, so a port is very possible.
**Contributions are welcome**: macOS and Linux support, 10-bit, key or audio output, or
fixes and testing on other DeckLink models. See [CONTRIBUTING.md](CONTRIBUTING.md).

## Known limits

- Output is 8-bit and fill only, with no key, audio or 10-bit.
- In Offscreen capture, Cycles *Rendered* falls back to Material Preview. Use Mirror
  Viewport for Cycles.
- The viewport compositor only works with Mirror Viewport capture.
- Mirror Viewport needs the viewport visible on a monitor and not covered by other
  windows.

## Licence

The add-on and its native bridge are licensed under the
[GNU General Public License v3.0 or later](LICENSE), with an
[additional permission](LICENSE-EXCEPTION.txt) to use Blackmagic Design's proprietary
DeckLink runtime. The DeckLink SDK headers in `third_party/decklink-sdk` are © Blackmagic
Design and keep their own licence. See [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).

Blackmagic Design, DeckLink, Intensity and UltraStudio are trademarks of Blackmagic Design
Pty. Ltd. Blender is a trademark of the Blender Foundation. This is an independent project,
not affiliated with or endorsed by either.
