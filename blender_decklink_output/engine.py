# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Thomas Doukinitsas

"""Runtime: one native output per enabled output entry, driven by an app timer.

The card's clock paces the signal (the DLL re-schedules the newest picture on every
completed frame), so this side only has to deliver pictures at up to the output rate.
Offscreen outputs are rendered here in the timer; mirror outputs are captured by a
native thread, and a SpaceView3D draw handler keeps their screen rectangle current."""

import time
import traceback
import uuid

import bpy
from bpy.app.handlers import persistent

from . import capture, native

ADDON_ID = __package__


class Runtime:
    def __init__(self, uid):
        self.uid = uid
        self.native = None
        self.key = None          # (device, mode, preroll) the native output was opened with
        self.failed_key = None   # key that failed to open; not retried until it changes
        self.error = ""
        self.warning = ""
        self.offscreen = None
        self.mirror_on = False
        self.mirror_area = 0
        self.mirror_label = ""
        self.reframe = 'FIT'
        self.max_fps = 0
        self.last_capture = 0.0
        self.next_due = 0.0
        self.capture_ms = 0.0
        self.dirty = True
        self.settle = 0
        self.applied = None      # settings last applied to the mirror viewport

    def close(self):
        if self.offscreen is not None:
            self.offscreen.free()
            self.offscreen = None
        if self.native is not None:
            self.native.close()
            self.native = None
        self.mirror_on = False
        self.mirror_area = 0
        self.applied = None


class State:
    runtimes = {}
    assignments = {}   # output uid -> area.as_pointer() picked for mirroring
    rendering = False
    loading = False
    draw_handle = None
    last_ui = 0.0


def runtime(out):
    return State.runtimes.get(out.uid)


def new_uid():
    return uuid.uuid4().hex


def preferences():
    addon = bpy.context.preferences.addons.get(ADDON_ID)
    return addon.preferences if addon else None


# ---------------------------------------------------------------- scene helpers

def iter_outputs():
    for scene in bpy.data.scenes:
        settings = getattr(scene, "decklink_output", None)
        if settings is None:
            continue
        for out in settings.outputs:
            yield scene, out


def scene_window(scene):
    for window in bpy.context.window_manager.windows:
        if window.scene == scene:
            return window
    return None


def view3d_areas(scene):
    for index, window in enumerate(bpy.context.window_manager.windows):
        if window.scene != scene:
            continue
        for area in window.screen.areas:
            if area.type == 'VIEW_3D':
                yield index, window, area


def area_label(index, window, area):
    space = area.spaces.active
    camera = space.region_3d.view_perspective == 'CAMERA' if space and space.region_3d else False
    return "Window %d: %s, %d x %d%s" % (index + 1, window.screen.name, area.width, area.height,
                                         ", camera view" if camera else "")


def find_area(pointer):
    for index, window in enumerate(bpy.context.window_manager.windows):
        for area in window.screen.areas:
            if area.as_pointer() == pointer:
                return index, window, area
    return None


def mirror_target(uid, scene):
    """The assigned viewport, else the best guess: one in camera view, preferably alone in
    its own window, else the largest."""
    found = find_area(State.assignments.get(uid, 0)) if uid in State.assignments else None
    if found is not None and found[2].type == 'VIEW_3D':
        return found
    best, best_score = None, None
    for index, window, area in view3d_areas(scene):
        space = area.spaces.active
        score = area.width * area.height / 1e7
        if space and space.region_3d and space.region_3d.view_perspective == 'CAMERA':
            score += 10
        if len(window.screen.areas) == 1:
            score += 5
        if best_score is None or score > best_score:
            best, best_score = (index, window, area), score
    return best


def apply_to_viewport(scene, out, window, area):
    """Mirror mode drives its viewport: camera view through the output camera, shading,
    overlays and compositor, gizmos hidden, camera frame fitted to the region."""
    space = area.spaces.active
    region = next((r for r in area.regions if r.type == 'WINDOW'), None)
    if space is None or region is None:
        return
    camera = capture.output_camera(scene, out)
    if out.camera_source == 'OBJECT' and camera is not None:
        space.use_local_camera = True
        space.camera = camera
    else:
        space.use_local_camera = False
    space.region_3d.view_perspective = 'CAMERA'
    space.shading.type = out.shading
    space.overlay.show_overlays = out.show_overlays
    wants_compositor = out.use_compositor and out.shading in {'MATERIAL', 'RENDERED'}
    space.shading.use_compositor = 'CAMERA' if wants_compositor else 'DISABLED'
    space.show_gizmo = False
    try:
        with bpy.context.temp_override(window=window, area=area, region=region):
            bpy.ops.view3d.view_center_camera()
    except RuntimeError:
        pass
    area.tag_redraw()


def mirror_settings_key(out):
    return (out.camera_source, out.camera.name if out.camera else "", out.shading,
            out.show_overlays, out.use_compositor)


# ---------------------------------------------------------------- the loop

def ensure_running():
    if not bpy.app.timers.is_registered(_tick):
        bpy.app.timers.register(_tick, first_interval=0.0, persistent=True)


def stop_all():
    for rt in State.runtimes.values():
        rt.close()
    State.runtimes.clear()
    _update_draw_handler()


def mark_dirty(*_):
    for rt in State.runtimes.values():
        rt.dirty = True


def _open_native(rt, out):
    key = (out.device, out.mode, out.preroll)
    if rt.native is not None and rt.key != key:
        rt.close()
    if rt.native is not None or key == rt.failed_key:
        return
    rt.error = ""
    if not out.device or not out.mode:
        rt.error = "Choose a DeckLink device and video mode"
        rt.failed_key = key
        return
    mode = native.Devices.mode(out.device, out.mode)
    if mode is None:
        native.Devices.refresh()
        mode = native.Devices.mode(out.device, out.mode)
    if mode is None:
        data = native.Devices.get()
        rt.error = data.get("error") or ("'%s' does not offer %s" % (out.device, out.mode)
                                         if native.Devices.device(out.device)
                                         else "DeckLink device '%s' not found" % out.device)
        rt.failed_key = key
        return
    try:
        rt.native = native.NativeOutput(out.device, mode["code"], out.preroll)
        rt.key, rt.failed_key, rt.dirty = key, None, True
    except RuntimeError as err:
        rt.error = str(err)
        rt.failed_key = key


def _service_offscreen(rt, scene, out, now):
    if rt.mirror_on:
        rt.native.mirror(enabled=0)
        rt.mirror_on = False
        rt.mirror_area = 0
    period = 1.0 / min(out.max_fps or rt.native.fps, rt.native.fps)
    if now < rt.next_due - 0.003:
        return
    # Keep a steady cadence; after a stall (render, file load) restart it from now.
    rt.next_due = rt.next_due + period if now - rt.next_due < period else now + period
    if out.update_mode == 'ON_CHANGE' and not (rt.dirty or rt.settle > 0
                                               or now - rt.last_capture >= 1.0):
        return
    window = scene_window(scene)
    if window is None:
        rt.warning = "Scene '%s' is not shown in any window" % scene.name
        return
    camera = capture.output_camera(scene, out)
    if camera is None:
        rt.warning = "No camera" if out.camera_source == 'OBJECT' else "The scene has no camera"
        return
    host = capture.find_host(scene, capture.effective_shading(scene, out))
    if host is None:
        rt.warning = "Offscreen capture needs a 3D viewport open somewhere"
        return
    if rt.offscreen is None:
        rt.offscreen = capture.OffscreenCapture()
    started = time.perf_counter()
    rt.warning = rt.offscreen.capture(scene, window.view_layer, host, camera, out, rt.native)
    rt.capture_ms = (time.perf_counter() - started) * 1000.0
    rt.last_capture = now
    if rt.dirty:
        rt.dirty = False
        samples = 1
        if out.shading in {'MATERIAL', 'RENDERED'} and scene.render.engine != 'CYCLES':
            samples = min(getattr(scene.eevee, "taa_samples", 16) or 16, 64)
        rt.settle = samples
    elif rt.settle > 0:
        rt.settle -= 1


def _service_mirror(rt, scene, out):
    rt.reframe, rt.max_fps = out.reframe, out.max_fps
    if rt.offscreen is not None:
        rt.offscreen.free()
        rt.offscreen = None
    target = mirror_target(rt.uid, scene)
    if target is None:
        rt.warning = "No 3D viewport to mirror (use New Mirror Window)"
        if rt.mirror_on:
            rt.native.mirror(enabled=1, win_w=0, win_h=0, win_x=0, win_y=0, src_x=0, src_y=0,
                             src_w=0, src_h=0, dst_x=0, dst_y=0, dst_w=0, dst_h=0, max_fps=0)
        rt.mirror_area = 0
        return
    index, window, area = target
    pointer = area.as_pointer()
    rt.mirror_label = area_label(index, window, area)
    settings = mirror_settings_key(out)
    if out.drive_viewport and (pointer != rt.mirror_area or settings != rt.applied):
        apply_to_viewport(scene, out, window, area)
        rt.applied = settings
    if pointer != rt.mirror_area:
        rt.mirror_area = pointer
        area.tag_redraw()
    rt.mirror_on = True
    _update_draw_handler()
    rt.warning = rt.native.mirror_error()


def _tick():
    try:
        return _tick_inner()
    except Exception:
        traceback.print_exc()
        return 0.5


def _tick_inner():
    started = time.perf_counter()
    wake = started + 0.25
    seen = set()
    for scene, out in iter_outputs():
        if not out.uid or out.uid in seen:
            out.uid = new_uid()
        seen.add(out.uid)
        rt = State.runtimes.get(out.uid)
        if not out.enabled:
            if rt is not None:
                rt.close()
                del State.runtimes[out.uid]
            continue
        if rt is None:
            rt = State.runtimes[out.uid] = Runtime(out.uid)
        _open_native(rt, out)
        if rt.native is None or State.rendering:
            continue
        try:
            if out.capture == 'VIEWPORT':
                _service_mirror(rt, scene, out)
            else:
                _service_offscreen(rt, scene, out, time.perf_counter())
                wake = min(wake, rt.next_due)
        except Exception as err:
            rt.warning = "capture failed: %s" % err
            traceback.print_exc()
    for uid in [uid for uid in State.runtimes if uid not in seen]:
        State.runtimes.pop(uid).close()
    _update_draw_handler()
    if started - State.last_ui > 0.5:
        State.last_ui = started
        for window in bpy.context.window_manager.windows:
            for area in window.screen.areas:
                if area.type == 'PROPERTIES':
                    area.tag_redraw()
    if not State.runtimes:
        return None
    return max(wake - time.perf_counter(), 0.001)


# ---------------------------------------------------------------- mirror draw handler

def _mirror_draw():
    context = bpy.context
    area = context.area
    if area is None:
        return
    pointer = area.as_pointer()
    for rt in State.runtimes.values():
        if rt.native is None or not rt.mirror_on or rt.mirror_area != pointer:
            continue
        try:
            config = capture.mirror_config(context.window, context.region, context.space_data,
                                           context.region_data, context.scene, rt.native.width,
                                           rt.native.height, rt.reframe, rt.max_fps)
            rt.native.mirror(**config)
        except Exception as err:
            rt.warning = "mirror failed: %s" % err


def _update_draw_handler():
    """Registered only while a mirror runs, and late (after other add-ons' handlers)."""
    wanted = any(rt.mirror_on for rt in State.runtimes.values())
    if wanted and State.draw_handle is None:
        State.draw_handle = bpy.types.SpaceView3D.draw_handler_add(_mirror_draw, (), 'WINDOW',
                                                                   'POST_PIXEL')
    elif not wanted and State.draw_handle is not None:
        bpy.types.SpaceView3D.draw_handler_remove(State.draw_handle, 'WINDOW')
        State.draw_handle = None


# ---------------------------------------------------------------- app handlers

@persistent
def _on_render_init(*_):
    State.rendering = True


@persistent
def _on_render_done(*_):
    State.rendering = False
    mark_dirty()


@persistent
def _on_load_pre(*_):
    stop_all()
    State.assignments.clear()


@persistent
def _on_load_post(*_):
    prefs = preferences()
    if prefs is None or not prefs.start_on_load:
        State.loading = True
        try:
            for _scene, out in iter_outputs():
                if out.enabled:
                    out.enabled = False
        finally:
            State.loading = False
    if any(out.enabled for _scene, out in iter_outputs()):
        ensure_running()


@persistent
def _on_change(*_):
    mark_dirty()


_HANDLERS = (
    ("render_init", _on_render_init),
    ("render_complete", _on_render_done),
    ("render_cancel", _on_render_done),
    ("load_pre", _on_load_pre),
    ("load_post", _on_load_post),
    ("depsgraph_update_post", _on_change),
    ("frame_change_post", _on_change),
)


def _deferred_start():
    # bpy.data is not available while add-ons register; look once the UI is up.
    if any(out.enabled for _scene, out in iter_outputs()):
        ensure_running()
    return None


def register():
    for name, function in _HANDLERS:
        getattr(bpy.app.handlers, name).append(function)
    bpy.app.timers.register(_deferred_start, first_interval=1.0)


def unregister():
    for name, function in _HANDLERS:
        handlers = getattr(bpy.app.handlers, name)
        if function in handlers:
            handlers.remove(function)
    for timer in (_tick, _deferred_start):
        if bpy.app.timers.is_registered(timer):
            bpy.app.timers.unregister(timer)
    stop_all()
