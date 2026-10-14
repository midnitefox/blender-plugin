# SPDX-FileCopyrightText: 2026 Open Studio contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Native setup for the local assistant connection."""

import textwrap

import bpy


def _wrapped(layout, message, icon="INFO"):
    region = getattr(bpy.context, "region", None)
    width = max(24, min(70, int(getattr(region, "width", 560) / 8)))
    for index, line in enumerate(textwrap.wrap(message, width)):
        layout.label(text=line, icon=icon if index == 0 else "NONE")


def draw_provider_setup(layout, context, prefs):
    """Draw the one supported assistant workflow without changing preferences."""
    from . import mcp_service, runtime

    box = layout.box()
    box.label(text="Connect your assistant", icon="PLUGIN")
    _wrapped(box, "Ask Codex to create an image with ImageGen and import it into Blender.")
    _wrapped(
        box,
        "Click Copy Codex setup, paste it into Codex, and ask it to connect. Keep Blender open while you work.",
    )
    status = mcp_service.status()
    row = box.row(align=True)
    if status["running"]:
        row.label(text="Assistant connection running", icon="CHECKMARK")
    else:
        row.operator("scenario.mcp_start", text="Start assistant connection", icon="PLAY")
    box.row().operator("scenario.mcp_copy", text="Copy Codex setup", icon="COPYDOWN").kind = "codex"
    box.label(text="Other assistants")
    for label, kind in (
        ("Claude Desktop", "claude_desktop"),
        ("Claude Code", "claude_code"),
        ("Cursor", "cursor"),
    ):
        box.row().operator("scenario.mcp_copy", text=label, icon="COPYDOWN").kind = kind
    if status.get("error"):
        _wrapped(box, status["error"], "ERROR")
    if not runtime.online():
        _wrapped(
            box,
            "Enable Allow Online Access in Blender's System preferences for the local assistant connection.",
        )
    _wrapped(
        box,
        "Your assistant creates the media using its available tools. Blender handles scene setup, cameras and importing the result.",
    )
    box.operator("wm.save_userpref", text="Save preferences", icon="FILE_TICK")


class SCENARIO_OT_choose_ai(bpy.types.Operator):
    bl_idname = "scenario.choose_ai"
    bl_label = "Assistant setup"
    bl_description = "Connect Codex or another assistant to this Blender session"

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self, width=660)

    def draw(self, context):
        from . import runtime

        draw_provider_setup(self.layout, context, runtime.prefs())

    def execute(self, context):
        return {"FINISHED"}


CLASSES = (SCENARIO_OT_choose_ai,)


def register():
    for cls in CLASSES:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)
