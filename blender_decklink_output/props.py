# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Thomas Doukinitsas

import bpy
from bpy.props import (BoolProperty, CollectionProperty, EnumProperty, IntProperty,
                       PointerProperty, StringProperty)

from . import engine, native


def _device_search(self, context, edit_text):
    return [(device["name"], device["model"]) for device in native.Devices.get()["devices"]
            if device["modes"]]


def _mode_search(self, context, edit_text):
    device = native.Devices.device(self.device)
    return [(mode["name"], native.mode_description(mode))
            for mode in (device or {}).get("modes", ())]


def _on_enabled(self, context):
    if engine.State.loading:
        return
    if self.enabled:
        rt = engine.State.runtimes.get(self.uid)
        if rt is not None:
            rt.failed_key = None
        engine.ensure_running()
    else:
        rt = engine.State.runtimes.pop(self.uid, None)
        if rt is not None:
            rt.close()
            engine._update_draw_handler()


def _on_signal(self, context):
    rt = engine.State.runtimes.get(self.uid)
    if rt is not None:
        rt.failed_key = None


def _on_picture(self, context):
    rt = engine.State.runtimes.get(self.uid)
    if rt is not None:
        rt.dirty = True


def _camera_poll(self, obj):
    return obj.type == 'CAMERA'


class BDO_Output(bpy.types.PropertyGroup):
    uid: StringProperty(options={'HIDDEN'})
    name: StringProperty(name="Name", default="Output")
    enabled: BoolProperty(
        name="Enabled", default=False, update=_on_enabled,
        description="Send this output to the DeckLink card")

    device: StringProperty(
        name="Device", search=_device_search, search_options=set(), update=_on_signal,
        description="DeckLink device (as named in Desktop Video Setup)")
    mode: StringProperty(
        name="Video Mode", search=_mode_search, search_options=set(), update=_on_signal,
        description="Output video mode. It does not have to match the scene's resolution or "
                    "frame rate")
    preroll: IntProperty(
        name="Pre-roll Frames", default=3, min=2, max=8, update=_on_signal,
        description="Frames queued on the card. More is steadier, fewer is lower latency")

    capture: EnumProperty(
        name="Capture", default='OFFSCREEN', update=_on_picture,
        items=(('OFFSCREEN', "Offscreen", "Render the camera at the output resolution. "
                "Workbench and EEVEE (Cycles Rendered falls back to Material Preview)", 'RESTRICT_RENDER_OFF', 0),
               ('VIEWPORT', "Mirror Viewport", "Capture a 3D viewport's camera frame from the "
                "screen: exactly what it shows, Cycles included. Keep it visible; its on-screen "
                "size is the source resolution", 'WINDOW', 1)))
    drive_viewport: BoolProperty(
        name="Drive Viewport", default=True, update=_on_picture,
        description="Set the mirrored viewport's camera, shading, overlays and compositor from "
                    "this output, hide its gizmos and fit the camera frame")

    camera_source: EnumProperty(
        name="Camera", default='SCENE', update=_on_picture,
        items=(('SCENE', "Scene Camera", "The scene's active camera (follows camera markers)"),
               ('OBJECT', "Camera Object", "A specific camera")))
    camera: PointerProperty(name="Camera Object", type=bpy.types.Object, poll=_camera_poll,
                            update=_on_picture)

    shading: EnumProperty(
        name="Shading", default='MATERIAL', update=_on_picture,
        items=(('WIREFRAME', "Wireframe", "", 'SHADING_WIRE', 0),
               ('SOLID', "Solid", "", 'SHADING_SOLID', 1),
               ('MATERIAL', "Material Preview", "", 'SHADING_TEXTURE', 2),
               ('RENDERED', "Rendered", "", 'SHADING_RENDERED', 3)))
    show_overlays: BoolProperty(
        name="Overlays", default=False, update=_on_picture,
        description="Include viewport overlays and drawing from other add-ons")
    use_compositor: BoolProperty(
        name="Compositor", default=False, update=_on_picture,
        description="Include the viewport compositor (Mirror Viewport capture, Material Preview and "
                    "Rendered only: it renders black offscreen in Blender 5.2)")

    reframe: EnumProperty(
        name="Reframe", default='FIT', update=_on_picture,
        items=(('FIT', "Fit", "Whole camera frame, black bars where the aspects differ"),
               ('FILL', "Fill", "Fill the output, cropping the camera frame's centre"),
               ('DISTORT', "Distort", "Stretch the camera frame to the output")))

    max_fps: IntProperty(
        name="Max Capture FPS", default=0, min=0, max=240,
        description="Cap on how often a new picture is captured (0 = output frame rate). "
                    "The card keeps its own rate regardless")
    update_mode: EnumProperty(
        name="Update", default='CONTINUOUS',
        items=(('CONTINUOUS', "Continuous", "Capture at the capture rate all the time"),
               ('ON_CHANGE', "On Change", "Offscreen only: capture after scene or frame changes "
                "(plus EEVEE sample settling and once a second), saving GPU time")))


class BDO_Settings(bpy.types.PropertyGroup):
    outputs: CollectionProperty(type=BDO_Output)
    active_index: IntProperty(default=0)


class BDO_Preferences(bpy.types.AddonPreferences):
    bl_idname = engine.ADDON_ID

    start_on_load: BoolProperty(
        name="Start Outputs When a File Opens", default=False,
        description="Outputs saved as enabled start sending as soon as the file is opened. "
                    "Off: they open disabled")

    def draw(self, context):
        self.layout.prop(self, "start_on_load")


CLASSES = (BDO_Output, BDO_Settings, BDO_Preferences)


def register():
    for cls in CLASSES:
        bpy.utils.register_class(cls)
    bpy.types.Scene.decklink_output = PointerProperty(type=BDO_Settings)


def unregister():
    del bpy.types.Scene.decklink_output
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)
