# SPDX-FileCopyrightText: 2026 Scenario Inc.
# SPDX-License-Identifier: GPL-3.0-or-later
"""User-initiated links to this independent fork's release ZIPs."""

import bpy

from .. import __version__

GUIDE_URL = (
    "https://github.com/midnitefox/blender-plugin/blob/feat/provider-choice/docs/USER_GUIDE.md"
)
RELEASES_URL = "https://github.com/midnitefox/blender-plugin/releases"


class SCENARIO_OT_setup_updates(bpy.types.Operator):
    bl_idname = "scenario.setup_updates"
    bl_label = "Open Open Studio releases"
    bl_description = "Open this fork's release page to choose a ZIP update"

    @classmethod
    def poll(cls, context):
        if not bpy.app.online_access:
            cls.poll_message_set("Enable Allow Online Access in Blender's System preferences")
            return False
        return True

    def execute(self, context):
        if not bpy.app.online_access:
            self.report({"WARNING"}, "Allow Online Access is off")
            return {"CANCELLED"}
        return bpy.ops.scenario.open_url(url=RELEASES_URL)


class SCENARIO_OT_check_updates(bpy.types.Operator):
    bl_idname = "scenario.check_updates"
    bl_label = "Open release page"
    bl_description = "Review published Open Studio builds before choosing an update"

    @classmethod
    def poll(cls, context):
        return SCENARIO_OT_setup_updates.poll(context)

    def execute(self, context):
        return SCENARIO_OT_setup_updates.execute(self, context)


def draw(layout, context):
    box = layout.box()
    box.label(text="Open Studio updates", icon="FILE_REFRESH")
    box.label(text=f"Installed version: {__version__}")
    box.label(text="Choose a release ZIP from the fork's GitHub releases", icon="INFO")
    box.label(text="Install the update from disk in Blender preferences")
    box.operator("scenario.open_url", text="Open Studio releases", icon="URL").url = RELEASES_URL
    box.operator("scenario.open_url", text="Setup guide", icon="HELP").url = GUIDE_URL


CLASSES = (SCENARIO_OT_setup_updates, SCENARIO_OT_check_updates)
