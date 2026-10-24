# Usage

The controls are in **Properties > Output > Live Camera Output**. The top line
shows the Desktop Video version and how many devices it found. Use the refresh button
after you plug in a device or change its profile.

## Outputs

Click **+** to add an output. A new output starts with the first device and that device's
1080 mode closest to the scene frame rate. Each output in the list has an enable checkbox,
its name, the video mode and a status icon. Outputs are saved with the scene.

- **Output Enabled** (or the checkbox in the list) starts and stops sending. Outputs saved
  as enabled open disabled, unless you turn on *Start Outputs When a File Opens* in the
  add-on preferences.
- **Device** and **Video Mode** list what Desktop Video reports for the card. The mode
  doesn't have to match the scene's resolution or frame rate. The card runs on its own
  clock and repeats the newest picture, so Blender's playback speed never stalls the
  signal.

## Picture

- **Capture** sets where the picture comes from:
  - **Offscreen** renders the camera at the output resolution, independent of any
    viewport. It supports Wireframe, Solid, Material Preview, and Rendered with EEVEE or
    Workbench. Cycles *Rendered* can't render offscreen, so it falls back to Material
    Preview. Use Mirror for Cycles.
  - **Mirror Viewport** captures a 3D viewport's camera frame from the screen, so it sends
    exactly what that viewport shows, Cycles included. The viewport must stay visible and
    unobscured. Its on-screen size is the source resolution, so make the camera frame at
    least as big as the output. **New Window** opens a clean single-viewport window for
    this, and you can move it to a second monitor. With **Drive Viewport** on, the
    output's camera, shading, overlays and compositor settings are applied to that
    viewport.
- **Camera** is either the scene camera (it follows camera markers) or a specific camera.
  The framing is always the camera's render frame: render resolution, pixel aspect and
  shift.
- **Shading** chooses Wireframe, Solid, Material Preview or Rendered.
- **Overlays** includes viewport overlays and anything other add-ons draw over the camera
  frame (see [the overlay hook](architecture.md#overlay-hook-for-other-add-ons)).
- **Compositor** includes the viewport compositor. It only works with Mirror Viewport, in
  Material Preview and Rendered.
- **Reframe** controls how the camera frame lands on the output raster:
  - **Fit** shows the whole frame with black bars. For example, a 2.39:1 camera on 1080
    HD comes out as about 1920×804 with bars top and bottom.
  - **Fill** crops the centre.
  - **Distort** stretches the frame to fill the raster.

## Timing

- **Max Capture FPS** caps how often a new picture is made. 0 uses the output rate. The
  card keeps its own rate regardless.
- **Update: On Change** (Offscreen only) captures only after edits or frame changes. It
  also keeps capturing while EEVEE samples settle, and once a second so clocks or
  timecode drawn by overlays keep ticking. This saves a lot of GPU time on static scenes.
- **Pre-roll** sets how many frames are queued on the card. The default is 3: fewer means
  lower latency, more means steadier output.

## Status

The status box shows whether the signal is on air, the pixel format in use, and these
frame counters:

- *late* and *dropped* frames are reported by the card.
- the capture time is what the add-on costs Blender's UI thread per picture.

While a render (F12) runs, capture pauses and the card holds the last picture.

## Tips

- Offscreen at 1080p is light: about 8 ms per frame in a simple scene. UHD offscreen costs
  roughly three times as much, so set **Max Capture FPS** if the UI gets sluggish.
- For Cycles, open **New Window**, move the window to a spare monitor, set its shading to
  Rendered, and use Mirror Viewport.
- Offscreen borrows a 3D viewport for settings that aren't exposed per output, such as
  X-ray, Solid lighting, local view and hidden collections. If the borrowed viewport's
  shading differs from the output's, each switch costs it one extra redraw.
