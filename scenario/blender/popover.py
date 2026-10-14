# SPDX-FileCopyrightText: 2026 Scenario Inc.
# SPDX-License-Identifier: GPL-3.0-or-later
"""The Scenario button in the 3D viewport header: opens the full panel in the sidebar.

The floating composer is the quick path (prompt + Generate); every setting, the jobs and the generations live
in the sidebar tab, so the header button leads there instead of opening a third, smaller form."""

import bpy


def open_sidebar(area):
    """Show the sidebar of a VIEW_3D area on the Scenario tab. Returns True when the area qualified."""
    if area is None or area.type != "VIEW_3D":
        return False
    space = area.spaces.active
    space.show_region_ui = True

    def _select_tab():
        for region in area.regions:
            if region.type == "UI":
                try:
                    region.active_panel_category = "Open Studio"
                    region.tag_redraw()
                    return True
                except (AttributeError, TypeError, ReferenceError):
                    return False
        return False

    if not _select_tab() and not bpy.app.background:
        # the category is read-only until the region has drawn once (a sidebar that was hidden): retry after a redraw
        bpy.app.timers.register(lambda: None if _select_tab() else None, first_interval=0.1)
    area.tag_redraw()
    return True


def _view3d_area(context):
    area = getattr(context, "area", None)
    if area is not None and area.type == "VIEW_3D":
        return area
    screen = getattr(context, "screen", None)
    if screen is not None:
        return next((a for a in screen.areas if a.type == "VIEW_3D"), None)
    return None


class SCENARIO_OT_open_panel(bpy.types.Operator):
    bl_idname = "scenario.open_panel"
    bl_label = "Open Studio"
    bl_description = (
        "Open the Scenario panel in the sidebar: models, references, settings, jobs and generations"
    )
    bl_options = {"INTERNAL"}

    def execute(self, context):
        if not open_sidebar(_view3d_area(context)):
            self.report({"WARNING"}, "No 3D viewport to open the panel in")
            return {"CANCELLED"}
        return {"FINISHED"}


def draw_header_button(self, context):
    if context.space_data is None or context.space_data.type != "VIEW_3D":
        return
    self.layout.operator(SCENARIO_OT_open_panel.bl_idname, text="Open Studio", icon="SHADERFX")
    self.layout.operator("scenario.open_studio", text="Studio", icon="FULLSCREEN_ENTER")


def register():
    bpy.utils.register_class(SCENARIO_OT_open_panel)
    bpy.types.VIEW3D_HT_header.append(draw_header_button)


def unregister():
    bpy.types.VIEW3D_HT_header.remove(draw_header_button)
    bpy.utils.unregister_class(SCENARIO_OT_open_panel)
