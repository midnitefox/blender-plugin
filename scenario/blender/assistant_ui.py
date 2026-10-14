# SPDX-FileCopyrightText: 2026 Open Studio contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Assistant connection, imported images and local camera controls."""

import textwrap

import bpy
from bpy.props import StringProperty

from . import mcp_service, provider_setup, runtime, shot_planner


class OPENSTUDIO_OT_assistant_start(bpy.types.Operator):
    bl_idname = "scenario.mcp_start"
    bl_label = "Start assistant connection"
    bl_description = "Allow your connected assistant to work in this Blender session"

    def execute(self, context):
        if mcp_service.start() is None:
            self.report({"ERROR"}, runtime.state.mcp_error or "Could not start the connection")
            return {"CANCELLED"}
        self.report({"INFO"}, "Assistant connection is ready")
        return {"FINISHED"}


class OPENSTUDIO_OT_assistant_stop(bpy.types.Operator):
    bl_idname = "scenario.mcp_stop"
    bl_label = "Stop assistant connection"

    def execute(self, context):
        mcp_service.stop()
        return {"FINISHED"}


class OPENSTUDIO_OT_assistant_copy(bpy.types.Operator):
    bl_idname = "scenario.mcp_copy"
    bl_label = "Copy assistant setup"
    kind: StringProperty(default="codex")

    def execute(self, context):
        setup = mcp_service.client_configs().get(self.kind)
        if not setup:
            self.report({"ERROR"}, "Choose a supported assistant connection format")
            return {"CANCELLED"}
        context.window_manager.clipboard = setup
        self.report({"INFO"}, "Assistant setup copied")
        return {"FINISHED"}


class OPENSTUDIO_PT_assistant(bpy.types.Panel):
    bl_idname = "OPENSTUDIO_PT_assistant"
    bl_label = "Open Studio"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Open Studio"

    def draw(self, context):
        provider_setup.draw_provider_setup(self.layout, context, runtime.prefs())
        if mcp_service.status()["running"]:
            self.layout.operator("scenario.mcp_stop", icon="PAUSE")
        message = runtime.message_visible()
        if message:
            for line in textwrap.wrap(message, 42):
                self.layout.label(text=line)


class OPENSTUDIO_PT_images(bpy.types.Panel):
    bl_idname = "OPENSTUDIO_PT_images"
    bl_label = "Assistant images"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Open Studio"

    def draw(self, context):
        layout = self.layout
        images = [image for image in bpy.data.images if image.get("open_studio_image_sha256")]
        if not images:
            layout.label(text="Ask your assistant to create an image.", icon="IMAGE_DATA")
            layout.label(text="It can add a reference or texture here.")
        else:
            for image in images[:12]:
                layout.label(text=image.name, icon="PACKAGE" if image.packed_file else "IMAGE_DATA")
            if len(images) > 12:
                layout.label(text=f"{len(images) - 12} more images in this project")
            layout.label(text="Save the project to keep packed images.", icon="FILE_TICK")


class OPENSTUDIO_PT_camera(bpy.types.Panel):
    bl_idname = "OPENSTUDIO_PT_camera"
    bl_label = "Camera path"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Open Studio"
    bl_options = {"DEFAULT_CLOSED"}

    def draw(self, context):
        shot_planner.draw_shot_planner(self.layout, context)


CLASSES = (
    OPENSTUDIO_OT_assistant_start,
    OPENSTUDIO_OT_assistant_stop,
    OPENSTUDIO_OT_assistant_copy,
    OPENSTUDIO_PT_assistant,
    OPENSTUDIO_PT_images,
    OPENSTUDIO_PT_camera,
)


def register():
    for cls in CLASSES:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)
