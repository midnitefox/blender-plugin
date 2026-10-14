# SPDX-FileCopyrightText: 2026 Scenario Inc.
# SPDX-License-Identifier: GPL-3.0-or-later
"""Add-on preferences: credentials, output folder, composer, MCP."""

import bpy
from bpy.props import (
    BoolProperty,
    CollectionProperty,
    EnumProperty,
    FloatProperty,
    IntProperty,
    StringProperty,
)

from .blender import provider_setup, updates

PORTAL_KEYS_URL = "https://app.scenario.com/team"


def credentials_changed(self, context):
    from .blender import runtime

    runtime.state.account_label = ""
    runtime.sync_catalog_context()


class ScenarioPreferences(bpy.types.AddonPreferences):
    bl_idname = __package__

    provider_mode: EnumProperty(
        name="AI setup",
        items=(
            (
                "AGENT",
                "Codex / external assistant",
                "Use your assistant's own AI tools and import results",
            ),
            ("PROFILES", "AI provider", "Choose a saved local or hosted provider connection"),
            ("COMFYUI", "Local ComfyUI", "Default local connection without a saved profile"),
            ("SCENARIO", "Legacy Scenario account", "Use the original saved Scenario credentials"),
        ),
        default="AGENT",
        update=provider_setup.provider_changed,
    )
    provider_profiles: CollectionProperty(type=provider_setup.SCENARIO_PG_ai_profile)
    active_provider_index: IntProperty(
        name="Selected AI profile", default=0, update=provider_setup.provider_changed
    )
    credential_source: EnumProperty(
        name="Credentials",
        items=[
            ("PREFERENCES", "Saved in Blender", "Use the API key and secret entered below"),
            (
                "ENVIRONMENT",
                "Environment",
                "Use SCENARIO_API_KEY and SCENARIO_API_SECRET from Blender's launch environment",
            ),
        ],
        default="PREFERENCES",
        update=credentials_changed,
    )
    api_key: StringProperty(
        update=credentials_changed,
        name="API Key",
        subtype="PASSWORD",
        description="Scenario API key (Project or Team scoped). Created in the Scenario portal",
    )
    api_secret: StringProperty(
        update=credentials_changed,
        name="API Secret",
        subtype="PASSWORD",
        description="Shown once when the key is created",
    )
    project_id: StringProperty(
        name="Project ID (optional)",
        description="Leave blank to use the API key's default scope; otherwise use this project",
        default="",
        update=credentials_changed,
    )
    output_dir: StringProperty(
        name="Output Folder",
        subtype="DIR_PATH",
        default="~/Downloads/Open Studio",
        description="Generated files are saved here, one subfolder per kind",
    )
    composer_enabled: BoolProperty(
        name="Floating composer in the viewport",
        default=True,
        update=lambda self, context: __import__(
            __package__ + ".blender.composer", fromlist=["set_enabled"]
        ).set_enabled(self.composer_enabled),
    )
    composer_offset_x: FloatProperty(
        name="Composer offset X",
        default=0.0,
        description="Where the floating composer was dragged to, from its default bottom-centre spot (pixels)",
    )
    composer_offset_y: FloatProperty(
        name="Composer offset Y",
        default=0.0,
        description="Where the floating composer was dragged to, from its default bottom-centre spot (pixels)",
    )
    composer_width: IntProperty(
        name="Composer width",
        default=0,
        min=0,
        description="Width of the expanded composer in pixels (0 = default)",
    )
    mcp_port: IntProperty(name="MCP Port", default=9876, min=1024, max=65535)
    mcp_allow_python: BoolProperty(
        name="Allow connected agents to run Python",
        default=False,
        description="Off by default: connected agents keep the read and scene tools; turn on only to let them run arbitrary bpy code in this Blender",
    )
    log_level: EnumProperty(
        name="Log Level",
        items=[("INFO", "Info", ""), ("DEBUG", "Debug", "")],
        default="INFO",
        update=lambda self, context: __import__(
            __package__ + ".blender.registry", fromlist=["apply_log_level"]
        ).apply_log_level(self.log_level),
    )

    def draw(self, context):
        layout = self.layout
        provider_setup.draw_provider_setup(layout, context, self)
        layout.prop(self, "output_dir")
        layout.prop(self, "composer_enabled")
        row = layout.row(align=True)
        row.enabled = self.composer_enabled
        row.label(text="Composer placement", icon="ARROW_LEFTRIGHT")
        row.prop(self, "composer_offset_x", text="X")
        row.prop(self, "composer_offset_y", text="Y")
        row.prop(self, "composer_width", text="Width")
        row.operator("scenario.composer_reset_layout", text="", icon="LOOP_BACK")
        box = layout.box()
        box.label(text="MCP server (agents)", icon="PLUGIN")
        box.prop(self, "mcp_port")
        box.prop(self, "mcp_allow_python")
        layout.prop(self, "log_level")
        updates.draw(layout, context)


def get_prefs(context=None):
    context = context or bpy.context
    entry = context.preferences.addons.get(__package__)
    return entry.preferences if entry else None


CLASSES = (*updates.CLASSES, provider_setup.SCENARIO_PG_ai_profile, ScenarioPreferences)
