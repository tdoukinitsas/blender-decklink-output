# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Thomas Doukinitsas

import bpy
from bpy.props import EnumProperty

from . import engine, native


def _settings(context):
    return context.scene.decklink_output


def _active(context):
    settings = _settings(context)
    if 0 <= settings.active_index < len(settings.outputs):
        return settings.outputs[settings.active_index]
    return None


def _default_signal(scene):
    """First device with output modes, and its 1080 mode closest to the scene rate."""
    fps = scene.render.fps / scene.render.fps_base
    for device in native.Devices.get()["devices"]:
        modes = device["modes"]
        if not modes:
            continue
        def score(mode):
            return (mode["height"] != 1080, mode["field"] != "p",
                    abs(native.mode_fps(mode) - fps))
        return device["name"], min(modes, key=score)["name"]
    return "", ""


class BDO_OT_output_add(bpy.types.Operator):
    bl_idname = "decklink_output.output_add"
    bl_label = "Add DeckLink Output"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        settings = _settings(context)
        out = settings.outputs.add()
        out.uid = engine.new_uid()
        out.name = "Output %d" % len(settings.outputs)
        out.device, out.mode = _default_signal(context.scene)
        settings.active_index = len(settings.outputs) - 1
        return {'FINISHED'}


class BDO_OT_output_remove(bpy.types.Operator):
    bl_idname = "decklink_output.output_remove"
    bl_label = "Remove DeckLink Output"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return _active(context) is not None

    def execute(self, context):
        settings = _settings(context)
        out = settings.outputs[settings.active_index]
        rt = engine.State.runtimes.pop(out.uid, None)
        if rt is not None:
            rt.close()
        engine.State.assignments.pop(out.uid, None)
        settings.outputs.remove(settings.active_index)
        settings.active_index = min(settings.active_index, len(settings.outputs) - 1)
        return {'FINISHED'}


class BDO_OT_refresh_devices(bpy.types.Operator):
    bl_idname = "decklink_output.refresh_devices"
    bl_label = "Refresh Devices"
    bl_description = "Enumerate DeckLink devices and modes again, and retry failed outputs"

    def execute(self, context):
        data = native.Devices.refresh()
        for rt in engine.State.runtimes.values():
            rt.failed_key = None
        if not data["available"]:
            self.report({'WARNING'}, data.get("error") or "DeckLink API unavailable")
        else:
            self.report({'INFO'}, "%d DeckLink device(s)" % len(data["devices"]))
        return {'FINISHED'}


def _viewport_items(self, context):
    items = []
    for index, window, area in engine.view3d_areas(context.scene):
        items.append((str(area.as_pointer()), engine.area_label(index, window, area), ""))
    return items or [("0", "No 3D viewports", "")]


class BDO_OT_mirror_assign(bpy.types.Operator):
    bl_idname = "decklink_output.mirror_assign"
    bl_label = "Mirror Viewport"
    bl_description = "Choose the 3D viewport this output mirrors"
    bl_property = "viewport"

    viewport: EnumProperty(name="Viewport", items=_viewport_items)

    @classmethod
    def poll(cls, context):
        return _active(context) is not None

    def execute(self, context):
        out = _active(context)
        pointer = int(self.viewport)
        if pointer:
            engine.State.assignments[out.uid] = pointer
            rt = engine.State.runtimes.get(out.uid)
            if rt is not None:
                rt.applied = None
        return {'FINISHED'}


class BDO_OT_mirror_new_window(bpy.types.Operator):
    bl_idname = "decklink_output.mirror_new_window"
    bl_label = "New Mirror Window"
    bl_description = ("Open a separate window with a single, clean 3D viewport for this output "
                      "to mirror. Size it to at least the output resolution for a sharp picture")

    @classmethod
    def poll(cls, context):
        return _active(context) is not None

    def execute(self, context):
        out = _active(context)
        source = next(((w, a) for _i, w, a in engine.view3d_areas(context.scene)
                       if w == context.window), None)
        source = source or next(((w, a) for _i, w, a in engine.view3d_areas(context.scene)), None)
        if source is None:
            self.report({'ERROR'}, "Needs a 3D viewport in this scene to copy")
            return {'CANCELLED'}
        window, area = source
        region = next(r for r in area.regions if r.type == 'WINDOW')
        before = {w.as_pointer() for w in context.window_manager.windows}
        try:
            with context.temp_override(window=window, area=area, region=region):
                bpy.ops.screen.area_dupli('INVOKE_DEFAULT')
        except RuntimeError as err:
            self.report({'ERROR'}, "Could not open a window: %s" % err)
            return {'CANCELLED'}
        new = [w for w in context.window_manager.windows if w.as_pointer() not in before]
        if not new:
            self.report({'ERROR'}, "Blender did not open a window")
            return {'CANCELLED'}
        mirror = next((a for a in new[0].screen.areas if a.type == 'VIEW_3D'), None)
        if mirror is None:
            return {'CANCELLED'}
        space = mirror.spaces.active
        for name in ("show_region_header", "show_region_tool_header", "show_region_toolbar",
                     "show_region_ui", "show_region_hud"):
            try:
                setattr(space, name, False)
            except (AttributeError, TypeError):
                pass
        engine.State.assignments[out.uid] = mirror.as_pointer()
        rt = engine.State.runtimes.get(out.uid)
        if rt is not None:
            rt.applied = None
        else:
            engine.apply_to_viewport(context.scene, out, new[0], mirror)
        return {'FINISHED'}


class BDO_OT_mirror_apply(bpy.types.Operator):
    bl_idname = "decklink_output.mirror_apply"
    bl_label = "Apply to Viewport"
    bl_description = ("Set the mirrored viewport to this output's camera, shading, overlays and "
                      "compositor, and fit the camera frame")

    @classmethod
    def poll(cls, context):
        return _active(context) is not None

    def execute(self, context):
        out = _active(context)
        target = engine.mirror_target(out.uid, context.scene)
        if target is None:
            self.report({'ERROR'}, "No 3D viewport to apply to")
            return {'CANCELLED'}
        engine.apply_to_viewport(context.scene, out, target[1], target[2])
        rt = engine.State.runtimes.get(out.uid)
        if rt is not None:
            rt.applied = engine.mirror_settings_key(out)
        return {'FINISHED'}


CLASSES = (BDO_OT_output_add, BDO_OT_output_remove, BDO_OT_refresh_devices,
           BDO_OT_mirror_assign, BDO_OT_mirror_new_window, BDO_OT_mirror_apply)


def register():
    for cls in CLASSES:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)
