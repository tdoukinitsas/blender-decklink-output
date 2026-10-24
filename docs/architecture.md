# How it works

```
blender_decklink_output/
  __init__.py   registers the modules below
  props.py      per-scene output list (Scene.decklink_output), add-on preferences
  ops.py        add/remove outputs, refresh devices, choose or open a mirror viewport
  ui.py         the Output Properties panels
  engine.py     the runtime: an app timer that opens outputs and delivers pictures
  capture.py    offscreen rendering, reframing, mirror rectangles, overlay hooks
  native.py     ctypes wrapper around bin/bdo_decklink.dll (no bpy import)
native/
  bdo_decklink.h/.cpp   the DLL: DeckLink scheduled playback + DXGI screen capture
```

## Native bridge

The DLL exposes a small C ABI (`native/bdo_decklink.h`), so it doesn't depend on Blender's
Python version:

| Function | Purpose |
|---|---|
| `bdo_list_devices` | JSON list of devices and their output modes |
| `bdo_open` / `bdo_close` | start/stop one output in a given mode |
| `bdo_push` | hand in one BGRA/RGBA picture of the output size (never blocks) |
| `bdo_mirror` | start/update/stop the native screen-capture thread for a window rectangle |
| `bdo_get_stats` | frame counters for the status panel |

- **Scheduled playback.** Each output keeps a ring of pre-roll frames on the card. Each
  completed frame is refilled with the newest pushed picture and scheduled again. The
  card's clock sets the pace and pushing never blocks, so Blender can deliver pictures at
  any rate, or stop for a while, without the signal stalling. If the schedule falls behind
  the card, it jumps ahead (a *resync*).
- **COM threading.** Blender's main thread is a single-threaded apartment (STA). Every
  DeckLink COM call except the completion callback's own scheduling runs on a
  short-lived multithreaded-apartment (MTA) thread. The process MTA is kept alive with
  `CoIncrementMTAUsage`.
- **Pixel format.** The card takes 8-bit BGRA and converts it to Rec.709 YUV itself. Modes
  without BGRA support, such as 2160p on some cards, get 8-bit 4:2:2 (`2vuy`) that the
  DLL converts with BT.709 coefficients (BT.601 for SD).
- **Loading.** `native.py` loads a hashed copy of the DLL from
  `%TEMP%\blender_decklink_output`, so the installed file is never locked and the add-on
  can be reinstalled while Blender runs.

## Colour

What goes out is what the viewport shows, with the scene's view transform and look baked
in (display-referred, 8-bit). There's no separate output colour management.

## Offscreen capture

`capture.py` renders the camera with `GPUOffScreen.draw_view3d`, using the camera's own
projection matrix at the size of the reframed camera frame. `draw_view3d` needs a 3D
viewport's `SpaceView3D` for shading settings, so the add-on borrows one. When the
borrowed viewport's shading, overlays or compositor settings differ from the output's, it
switches them for the draw and restores them afterwards. Overlay hooks (below) are drawn
on top. The frame is then placed on a black output-size buffer, read back and pushed.

The engine timer paces each output by its own next-due time. With **Update: On Change**,
`depsgraph_update_post` and `frame_change_post` handlers mark outputs dirty. After a
change, EEVEE outputs keep capturing for as many redraws as the scene's TAA sample count,
up to 64, so the anti-aliased result reaches the card.

## Mirror capture

Python draw handlers only see the overlay layer, not the rendered image, so mirroring
reads the screen instead. A `SpaceView3D` `POST_PIXEL` draw handler works out where the
camera frame is in the window on every redraw and passes the rectangle to the DLL. A
native thread captures that rectangle with DXGI desktop duplication, scales it bilinearly
onto the output raster (fit/fill/distort) and pushes it.

## Rendering and file loads

- `render_init`, `render_complete` and `render_cancel` pause capture during F12 renders.
  The card keeps showing the last picture.
- `load_pre` closes all outputs. `load_post` disables outputs saved as enabled, unless
  the *Start Outputs When a File Opens* preference is on.

## Overlay hook for other add-ons

Any enabled add-on can draw over the output picture (when the output's **Overlays** is on,
in Offscreen capture) by defining a module-level function in its package:

```python
def decklink_overlay(scene, depsgraph, width, height):
    """Draw with the gpu module.

    Called with the camera frame's framebuffer bound and a pixel projection
    (0..width x 0..height, origin bottom-left). Colours are display-referred,
    as in a SpaceView3D POST_PIXEL draw handler.
    """
```

Example, a safe-area frame:

```python
import gpu
from gpu_extras.batch import batch_for_shader


def decklink_overlay(scene, depsgraph, width, height):
    x0, y0, x1, y1 = width * 0.05, height * 0.05, width * 0.95, height * 0.95
    shader = gpu.shader.from_builtin('UNIFORM_COLOR')
    batch = batch_for_shader(shader, 'LINE_LOOP',
                             {"pos": [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]})
    shader.uniform_float("color", (1.0, 1.0, 1.0, 1.0))
    batch.draw(shader)
```

Hooks are discovered every couple of seconds from `sys.modules`. If a hook raises an
exception, the first traceback goes to the console and the hook keeps being called. In Mirror capture, whatever the add-on
draws in the viewport itself is captured with it.
