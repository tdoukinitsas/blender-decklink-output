# SPDX-License-Identifier: GPL-3.0-or-later
# Copyright (C) 2026 Thomas Doukinitsas

import bpy

from . import capture, engine, native


def _active(context):
    settings = context.scene.decklink_output
    if 0 <= settings.active_index < len(settings.outputs):
        return settings.outputs[settings.active_index]
    return None


def _status_icon(out):
    if not out.enabled:
        return 'BLANK1'
    rt = engine.runtime(out)
    if rt is None or rt.error:
        return 'ERROR'
    if rt.native is None:
        return 'TIME'
    return 'REC' if not rt.warning else 'INFO'


class BDO_UL_outputs(bpy.types.UIList):
    def draw_item(self, context, layout, data, item, icon, active_data, active_property,
                  index=0, flt_flag=0):
        row = layout.row(align=True)
        row.prop(item, "enabled", text="")
        row.prop(item, "name", text="", emboss=False)
        row.label(text=item.mode or "", translate=False)
        row.label(text="", icon=_status_icon(item))


class BDO_PT_output(bpy.types.Panel):
    bl_label = "Live Camera Output"
    bl_space_type = 'PROPERTIES'
    bl_region_type = 'WINDOW'
    bl_context = "output"
    bl_options = {'DEFAULT_CLOSED'}

    def draw(self, context):
        layout = self.layout
        settings = context.scene.decklink_output
        data = native.Devices.get()
        row = layout.row()
        if not data["available"]:
            row.label(text=data.get("error") or "DeckLink API unavailable", icon='ERROR')
        else:
            row.label(text="Desktop Video %s, %d device(s)"
                      % (data.get("api_version", ""), len(data["devices"])), icon='CHECKMARK')
        row.operator("decklink_output.refresh_devices", text="", icon='FILE_REFRESH')

        row = layout.row()
        row.template_list("BDO_UL_outputs", "", settings, "outputs", settings, "active_index",
                          rows=2)
        column = row.column(align=True)
        column.operator("decklink_output.output_add", text="", icon='ADD')
        column.operator("decklink_output.output_remove", text="", icon='REMOVE')

        out = _active(context)
        if out is None:
            return
        layout.use_property_split = True
        layout.use_property_decorate = False
        column = layout.column()
        column.prop(out, "enabled", text="Output Enabled")
        column.prop(out, "device")
        column.prop(out, "mode")
        mode = native.Devices.mode(out.device, out.mode) if out.device else None
        if mode is not None:
            column.label(text=native.mode_description(mode))


class BDO_PT_picture(bpy.types.Panel):
    bl_label = "Picture"
    bl_parent_id = "BDO_PT_output"
    bl_space_type = 'PROPERTIES'
    bl_region_type = 'WINDOW'
    bl_context = "output"

    @classmethod
    def poll(cls, context):
        return _active(context) is not None

    def draw(self, context):
        layout = self.layout
        layout.use_property_split = True
        layout.use_property_decorate = False
        out = _active(context)
        scene = context.scene

        layout.prop(out, "capture", expand=True)
        column = layout.column()
        column.prop(out, "camera_source")
        if out.camera_source == 'OBJECT':
            column.prop(out, "camera")
        camera = capture.output_camera(scene, out)
        render = scene.render
        aspect = capture.camera_aspect(scene)
        column.label(text="%s: %d x %d, %.3f:1" % (camera.name if camera else "no camera",
                                                   render.resolution_x, render.resolution_y,
                                                   aspect),
                     icon='CAMERA_DATA' if camera else 'ERROR')

        column = layout.column()
        mirror = out.capture == 'VIEWPORT'
        if mirror:
            column.prop(out, "drive_viewport")
        sub = column.column()
        sub.active = not mirror or out.drive_viewport
        sub.prop(out, "shading", expand=True)
        if (not mirror and out.shading == 'RENDERED' and scene.render.engine == 'CYCLES'):
            sub.label(text="Cycles: Material Preview offscreen, use Mirror", icon='INFO')
        sub.prop(out, "show_overlays")
        row = sub.row()
        row.active = mirror and out.shading in {'MATERIAL', 'RENDERED'}
        row.prop(out, "use_compositor")
        if not mirror and out.use_compositor:
            sub.label(text=capture.OFFSCREEN_COMPOSITOR_NOTE, icon='INFO')

        layout.prop(out, "reframe", expand=True)
        mode = native.Devices.mode(out.device, out.mode) if out.device else None
        if mode is not None:
            x, y, w, h = capture.frame_rect(aspect, mode["width"], mode["height"], out.reframe)
            layout.label(text="Camera frame on output: %d x %d at %d, %d" % (w, h, x, y))

        if mirror:
            box = layout.box()
            rt = engine.runtime(out)
            target = engine.mirror_target(out.uid, scene)
            label = engine.area_label(*target) if target else "no 3D viewport"
            box.label(text=label, icon='WINDOW')
            row = box.row(align=True)
            row.operator_menu_enum("decklink_output.mirror_assign", "viewport",
                                   text="Choose Viewport", icon='RESTRICT_SELECT_OFF')
            row.operator("decklink_output.mirror_new_window", text="New Window", icon='ADD')
            row.operator("decklink_output.mirror_apply", text="", icon='FILE_REFRESH')
            if target and mode is not None:
                box.label(text="Keep the camera frame at least %d x %d on screen"
                          % (mode["width"], mode["height"]), icon='INFO')


class BDO_PT_advanced(bpy.types.Panel):
    bl_label = "Timing"
    bl_parent_id = "BDO_PT_output"
    bl_space_type = 'PROPERTIES'
    bl_region_type = 'WINDOW'
    bl_context = "output"
    bl_options = {'DEFAULT_CLOSED'}

    @classmethod
    def poll(cls, context):
        return _active(context) is not None

    def draw(self, context):
        layout = self.layout
        layout.use_property_split = True
        layout.use_property_decorate = False
        out = _active(context)
        layout.prop(out, "max_fps")
        row = layout.row()
        row.active = out.capture == 'OFFSCREEN'
        row.prop(out, "update_mode")
        layout.prop(out, "preroll")


class BDO_PT_status(bpy.types.Panel):
    bl_label = "Status"
    bl_parent_id = "BDO_PT_output"
    bl_space_type = 'PROPERTIES'
    bl_region_type = 'WINDOW'
    bl_context = "output"

    @classmethod
    def poll(cls, context):
        out = _active(context)
        return out is not None and out.enabled

    def draw(self, context):
        layout = self.layout
        out = _active(context)
        rt = engine.runtime(out)
        column = layout.column(align=True)
        if rt is None:
            column.label(text="Starting...", icon='TIME')
            return
        if rt.error:
            for index, line in enumerate(_wrap(rt.error, 60)):
                column.label(text=line, icon='ERROR' if index == 0 else 'BLANK1')
            return
        if rt.native is None:
            column.label(text="Starting...", icon='TIME')
            return
        if rt.warning:
            column.label(text=rt.warning, icon='INFO')
        stats = rt.native.stats()
        fmt = "BGRA" if stats.pixel_format == native.FOURCC_BGRA else "YUV 4:2:2"
        column.label(text="On air: %d x %d %.3f fps, %s" % (stats.width, stats.height,
                                                            rt.native.fps, fmt), icon='REC')
        column.label(text="Frames: %d shown, %d late, %d dropped, %d buffered"
                     % (stats.completed, stats.late, stats.dropped, stats.buffered))
        if out.capture == 'VIEWPORT':
            column.label(text="Mirror: %d captures, %.1f ms" % (stats.mirror_frames,
                                                                stats.mirror_us / 1000.0))
        else:
            column.label(text="Offscreen: %d pictures, %.1f ms on Blender's thread"
                         % (stats.pushed, rt.capture_ms))


def _wrap(text, width):
    lines, line = [], ""
    for word in text.split():
        if line and len(line) + 1 + len(word) > width:
            lines.append(line)
            line = word
        else:
            line = (line + " " + word).strip()
    return lines + ([line] if line else [])


CLASSES = (BDO_UL_outputs, BDO_PT_output, BDO_PT_picture, BDO_PT_advanced, BDO_PT_status)


def register():
    for cls in CLASSES:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)
