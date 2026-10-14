# SPDX-FileCopyrightText: 2026 Scenario Inc.
# SPDX-License-Identifier: GPL-3.0-or-later
"""Preferences for the local assistant connection and its saved output."""

import bpy
from bpy.props import BoolProperty, EnumProperty, IntProperty

from .blender import provider_setup, updates


class ScenarioPreferences(bpy.types.AddonPreferences):
    bl_idname = __package__

    mcp_port: IntProperty(name="Local connection port", default=9876, min=1024, max=65535)
    mcp_allow_python: BoolProperty(
        name="Allow connected assistants to run Python",
        default=False,
        description="Off by default: assistants can use the scene tools. Enable only to allow arbitrary Blender Python commands",
    )
    log_level: EnumProperty(
        name="Log level",
        items=[("INFO", "Info", ""), ("DEBUG", "Debug", "")],
        default="INFO",
        update=lambda self, context: __import__(
            __package__ + ".blender.registry", fromlist=["apply_log_level"]
        ).apply_log_level(self.log_level),
    )

    def draw(self, context):
        layout = self.layout
        provider_setup.draw_provider_setup(layout, context, self)
        box = layout.box()
        box.label(text="Local assistant connection", icon="PLUGIN")
        box.prop(self, "mcp_port")
        box.prop(self, "mcp_allow_python")
        layout.prop(self, "log_level")
        updates.draw(layout, context)


def get_prefs(context=None):
    context = context or bpy.context
    entry = context.preferences.addons.get(__package__)
    return entry.preferences if entry else None


CLASSES = (*updates.CLASSES, ScenarioPreferences)
