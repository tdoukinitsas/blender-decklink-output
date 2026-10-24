# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Thomas Doukinitsas

"""Picture sources: offscreen camera render at output resolution, and the maths for the
native screen mirror. Framing is always the camera's render frame (render resolution and
pixel aspect), reframed onto the DeckLink raster by fit / fill / distort."""

import sys
import time
import traceback

import bpy
import gpu
from bpy_extras.view3d_utils import location_3d_to_region_2d
from gpu_extras.batch import batch_for_shader
from mathutils import Matrix

from . import native

# ---------------------------------------------------------------- framing


def output_camera(scene, out):
    camera = out.camera if out.camera_source == 'OBJECT' else scene.camera
    return camera if camera is not None and camera.type == 'CAMERA' else None


def camera_aspect(scene):
    render = scene.render
    return (render.resolution_x * render.pixel_aspect_x) / max(
        render.resolution_y * render.pixel_aspect_y, 1e-6)


def output_pixel_aspect(width, height):
    """SD rasters are 4:3 with non-square pixels; everything else is square."""
    if width == 720 and height in (480, 486, 576):
        return height * 4.0 / 3.0 / width
    return 1.0


def frame_rect(aspect, out_w, out_h, mode):
    """Where the camera frame lands on the output: (x, y, w, h), output pixels, origin
    top-left. FIT letter/pillarboxes, FILL crops the centre, DISTORT stretches."""
    if mode == 'DISTORT':
        return 0, 0, out_w, out_h
    par = output_pixel_aspect(out_w, out_h)
    wider = aspect > out_w * par / out_h
    if (mode == 'FIT') == wider:
        w, h = out_w, out_w * par / aspect
    else:
        w, h = out_h * aspect / par, out_h
    w, h = max(int(round(w)), 1), max(int(round(h)), 1)
    return (out_w - w) // 2, (out_h - h) // 2, w, h


# ---------------------------------------------------------------- overlay hooks

class _Hooks:
    functions = []
    stamp = -1.0
    failed = set()


def overlay_hooks():
    """Add-ons can draw into the DeckLink picture (with Overlays on) by defining a module
    level `decklink_overlay(scene, depsgraph, width, height)`. It is called with the camera
    frame's framebuffer bound and a pixel projection (0..width, 0..height, origin bottom-left)
    and must draw display-referred colours, like a SpaceView3D POST_PIXEL handler."""
    now = time.monotonic()
    if now - _Hooks.stamp > 2.0:
        _Hooks.stamp = now
        functions = []
        for name in bpy.context.preferences.addons.keys():
            module = sys.modules.get(name)
            hook = getattr(module, "decklink_overlay", None)
            if callable(hook):
                functions.append(hook)
        _Hooks.functions = functions
    return _Hooks.functions


def _pixel_projection(width, height):
    return Matrix(((2.0 / width, 0, 0, -1), (0, 2.0 / height, 0, -1), (0, 0, -1, 0), (0, 0, 0, 1)))


def _draw_hooks(scene, depsgraph, width, height):
    hooks = overlay_hooks()
    if not hooks:
        return
    with gpu.matrix.push_pop(), gpu.matrix.push_pop_projection():
        gpu.matrix.load_identity()
        gpu.matrix.load_projection_matrix(_pixel_projection(width, height))
        for hook in hooks:
            try:
                hook(scene, depsgraph, width, height)
            except Exception:
                if hook not in _Hooks.failed:
                    _Hooks.failed.add(hook)
                    traceback.print_exc()
                    print("[decklink] overlay hook %s failed" % getattr(hook, "__module__", hook))
    gpu.state.blend_set('NONE')


# ---------------------------------------------------------------- offscreen

def find_host(scene, shading):
    """A 3D viewport to borrow for GPUOffScreen.draw_view3d (it supplies shading details,
    local view and visibility). One already in the wanted shading avoids toggling it;
    one in Cycles Rendered is avoided, since switching it away restarts its render."""
    best, best_score = None, None
    for window in bpy.context.window_manager.windows:
        if window.scene != scene:
            continue
        for area in window.screen.areas:
            if area.type != 'VIEW_3D':
                continue
            region = next((r for r in area.regions if r.type == 'WINDOW'), None)
            space = area.spaces.active
            if region is None or space is None:
                continue
            current = space.shading.type
            score = 0
            if current == shading:
                score += 4
            if current == 'RENDERED' and scene.render.engine == 'CYCLES':
                score -= 8
            if best_score is None or score > best_score:
                best, best_score = (window, area, space, region), score
    return best


class _Overrides:
    """Temporarily set viewport settings, restored in reverse order."""

    def __init__(self):
        self.changes = []

    def set(self, owner, attribute, value):
        old = getattr(owner, attribute)
        if old != value:
            setattr(owner, attribute, value)
            self.changes.append((owner, attribute, old))

    def restore(self):
        for owner, attribute, old in reversed(self.changes):
            try:
                setattr(owner, attribute, old)
            except (AttributeError, TypeError, ValueError):
                pass
        self.changes.clear()


OFFSCREEN_COMPOSITOR_NOTE = "The viewport compositor only works with Mirror Viewport capture"


def effective_shading(scene, out):
    if out.shading == 'RENDERED' and scene.render.engine == 'CYCLES':
        return 'MATERIAL'
    return out.shading


class OffscreenCapture:
    def __init__(self):
        self.src = None
        self.dst = None
        self.buffer = None
        self.array = None
        self.address = 0
        self.slow_readback = False

    def free(self):
        for offscreen in (self.src, self.dst):
            if offscreen is not None:
                try:
                    offscreen.free()
                except Exception:
                    pass
        self.src = self.dst = self.buffer = self.array = None
        self.address = 0

    def _ensure(self, src_size, dst_size):
        if self.src is None or (self.src.width, self.src.height) != src_size:
            if self.src is not None:
                self.src.free()
            self.src = gpu.types.GPUOffScreen(*src_size)
        if self.dst is None or (self.dst.width, self.dst.height) != dst_size:
            if self.dst is not None:
                self.dst.free()
            self.dst = gpu.types.GPUOffScreen(*dst_size)
            self.buffer = gpu.types.Buffer('UBYTE', dst_size[0] * dst_size[1] * 4)
            self.array, self.address = None, 0
            try:
                import numpy as np
                self.array = np.frombuffer(self.buffer, dtype=np.uint8)  # zero-copy view
                self.address = self.array.ctypes.data
                self.slow_readback = False
            except (TypeError, ValueError, BufferError):
                self.slow_readback = True

    def capture(self, scene, view_layer, host, camera, out, output):
        """Render the camera, reframe it to the output raster and push it. Returns a
        warning string ('' when all is well)."""
        warning = ""
        depsgraph = view_layer.depsgraph
        render = scene.render
        out_w, out_h = output.width, output.height
        x, y, w, h = frame_rect(camera_aspect(scene), out_w, out_h, out.reframe)
        self._ensure((w, h), (out_w, out_h))

        camera_eval = camera.evaluated_get(depsgraph)
        view_matrix = camera_eval.matrix_world.normalized().inverted()
        projection = camera_eval.calc_matrix_camera(
            depsgraph, x=render.resolution_x, y=render.resolution_y,
            scale_x=render.pixel_aspect_x, scale_y=render.pixel_aspect_y)

        shading = effective_shading(scene, out)
        if shading != out.shading:
            warning = "Cycles Rendered needs Mirror Viewport capture; showing Material Preview"
        elif out.use_compositor:
            warning = OFFSCREEN_COMPOSITOR_NOTE
        _window, _area, space, region = host
        overrides = _Overrides()
        try:
            overrides.set(space.shading, "type", shading)
            overrides.set(space.overlay, "show_overlays", out.show_overlays)
            # The viewport compositor renders black in GPUOffScreen.draw_view3d (5.2).
            overrides.set(space.shading, "use_compositor", 'DISABLED')
            self.src.draw_view3d(scene, view_layer, space, region, view_matrix, projection,
                                 do_color_management=True, draw_background=True)
        finally:
            overrides.restore()

        gpu.state.depth_test_set('NONE')
        gpu.state.depth_mask_set(False)
        if out.show_overlays:
            with self.src.bind():
                _draw_hooks(scene, depsgraph, w, h)

        with self.dst.bind():
            framebuffer = gpu.state.active_framebuffer_get()
            framebuffer.clear(color=(0.0, 0.0, 0.0, 1.0))
            bottom = out_h - (y + h)
            x0, x1 = 2.0 * x / out_w - 1.0, 2.0 * (x + w) / out_w - 1.0
            y0, y1 = 2.0 * bottom / out_h - 1.0, 2.0 * (bottom + h) / out_h - 1.0
            with gpu.matrix.push_pop(), gpu.matrix.push_pop_projection():
                gpu.matrix.load_identity()
                gpu.matrix.load_projection_matrix(Matrix.Identity(4))
                gpu.state.blend_set('NONE')
                shader = gpu.shader.from_builtin('IMAGE')
                batch = batch_for_shader(shader, 'TRI_FAN', {
                    "pos": ((x0, y0), (x1, y0), (x1, y1), (x0, y1)),
                    "texCoord": ((0, 0), (1, 0), (1, 1), (0, 1)),
                })
                shader.bind()
                shader.uniform_sampler("image", self.src.texture_color)
                batch.draw(shader)
            framebuffer.read_color(0, 0, out_w, out_h, 4, 0, 'UBYTE', data=self.buffer)

        if self.slow_readback:
            import numpy as np
            self.array = np.asarray(self.buffer.to_list(), dtype=np.uint8).ravel()
            self.address = self.array.ctypes.data
            warning = warning or "slow readback path (gpu.types.Buffer has no buffer protocol)"
        output.push(self.address, out_w, out_h, out_w * 4, native.FLIP_Y | native.RGBA)
        return warning


# ---------------------------------------------------------------- mirror

def viewport_camera(scene, space):
    if space.use_local_camera and space.camera is not None:
        return space.camera
    return scene.camera


def camera_frame_in_region(scene, space, region, rv3d):
    """Camera frame in region pixels (x0, y0, x1, y1), origin bottom-left, or None."""
    if rv3d is None or rv3d.view_perspective != 'CAMERA':
        return None
    camera = viewport_camera(scene, space)
    if camera is None or camera.type != 'CAMERA':
        return None
    matrix = camera.matrix_world.normalized()
    points = [location_3d_to_region_2d(region, rv3d, matrix @ corner)
              for corner in camera.data.view_frame(scene=scene)]
    if any(point is None for point in points):
        return None
    xs, ys = [p.x for p in points], [p.y for p in points]
    return min(xs), min(ys), max(xs), max(ys)


def mirror_config(window, region, space, rv3d, scene, out_w, out_h, reframe, max_fps):
    """The native mirror settings for a viewport: the camera frame on screen (clipped to
    the region, 1 px inside the frame line) and where it lands on the output."""
    config = dict(enabled=1, win_w=window.width, win_h=window.height, win_x=window.x,
                  win_y=window.y, src_x=0, src_y=0, src_w=0, src_h=0, dst_x=0, dst_y=0,
                  dst_w=out_w, dst_h=out_h, max_fps=max_fps)
    frame = camera_frame_in_region(scene, space, region, rv3d)
    if frame is None:
        return config
    fx0, fy0, fx1, fy1 = frame[0] + 1, frame[1] + 1, frame[2] - 1, frame[3] - 1
    fw, fh = fx1 - fx0, fy1 - fy0
    if fw < 4 or fh < 4:
        return config
    cx0, cy0 = max(fx0, 0.0), max(fy0, 0.0)
    cx1, cy1 = min(fx1, float(region.width)), min(fy1, float(region.height))
    if cx1 - cx0 < 2 or cy1 - cy0 < 2:
        return config
    dx, dy, dw, dh = frame_rect(fw / fh, out_w, out_h, reframe)
    u0, u1 = (cx0 - fx0) / fw, (cx1 - fx0) / fw
    v0, v1 = (fy1 - cy1) / fh, (fy1 - cy0) / fh   # from the top
    config.update(
        src_x=int(round(region.x + cx0)),
        src_y=int(round(window.height - (region.y + cy1))),
        src_w=int(round(cx1 - cx0)), src_h=int(round(cy1 - cy0)),
        dst_x=int(round(dx + u0 * dw)), dst_y=int(round(dy + v0 * dh)),
        dst_w=max(int(round((u1 - u0) * dw)), 1), dst_h=max(int(round((v1 - v0) * dh)), 1))
    return config
