# Changelog

All notable changes are listed here. Versions follow [Semantic Versioning](https://semver.org/).

## [Unreleased]

First public release. Windows only.

- A list of DeckLink outputs per scene, each with its own device, video mode, camera,
  shading, overlays, compositor and pre-roll.
- **Offscreen** capture at the output resolution (Workbench and EEVEE), and **Mirror
  Viewport** capture of a 3D viewport from the screen (any engine, Cycles included).
- Fit, fill and distort reframing of the camera's render frame onto the output raster.
- Card-clocked scheduled playback: the newest picture repeats, so the signal never
  stalls.
- 8-bit BGRA output, with a BT.709/BT.601 4:2:2 conversion for modes without BGRA.
- **Update: On Change** mode and a capture-rate cap to save GPU time.
- `decklink_overlay(scene, depsgraph, width, height)` hook for other add-ons.
- Capture pauses during F12 renders. Outputs saved as enabled open disabled unless
  the *Start Outputs When a File Opens* preference is on.
