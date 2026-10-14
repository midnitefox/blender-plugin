# SPDX-FileCopyrightText: 2026 Scenario Inc.
# SPDX-License-Identifier: GPL-3.0-or-later
"""Native popup for the job the composer strip shows: its status and explicit result actions.

It reuses the sidebar's Jobs and Generations drawing, so the same view offers the same
saved-job controls everywhere. Drawing reads only the displayed views; every action is the
existing operator with its own confirmation or destination review."""

import bpy
from bpy.props import StringProperty

from .. import label_wrap, panels, runtime, studio

CARD_WIDTH = 440  # requested logical width; bounded by the area, window and UI scale
MISSING = "This job is no longer shown for this connection"


def find_view(request_id):
    """The job view for `request_id` in this connection, or None; never reads storage.

    The shared job owner's view comes first: an agent's submission is drawn there before the
    next poll lists it in the panels. A retired connection's views are gone from both."""
    jobs = runtime.state.model_jobs
    view = jobs.views.get(request_id) if jobs is not None else None
    if view is not None:
        return view
    return next((view for view in runtime.state.jobs_view if view.local_id == request_id), None)


def draw_job(layout, request_id):
    """An unfinished job as a Jobs row, a finished one as an expanded Generations result."""
    view = find_view(request_id) if request_id else None
    if view is None:
        layout.label(text=MISSING, icon="INFO")
        layout.operator(
            "scenario.inspect_saved_jobs", text="Inspect saved jobs", icon="FILE_REFRESH"
        )
        return
    if view.is_terminal:
        panels.draw_result(layout, view, collapsible=False)
    else:
        panels.draw_active_job(layout, view)
    layout.operator("scenario.open_panel", text="Show all jobs in the sidebar", icon="MENU_PANEL")


class SCENARIO_OT_composer_job_card(bpy.types.Operator):
    bl_idname = "scenario.composer_job_card"
    bl_label = "Open Studio job"
    bl_description = (
        "Inspect this job's status and its explicit result actions without generating again"
    )
    bl_options = {"INTERNAL"}

    request_id: StringProperty(options={"HIDDEN", "SKIP_SAVE"})

    def invoke(self, context, event):
        self._width = min(CARD_WIDTH, studio.popup_width(context))
        return context.window_manager.invoke_popup(self, width=self._width)

    def draw(self, context):
        # The pump refreshes this temporary region, so status and progress update while it is open.
        studio.track_popup(self, context.region_popup)
        # Blender lays a popup out before sizing it; wrapped lines measure the requested width.
        with label_wrap.popup_layout(getattr(self, "_width", CARD_WIDTH)):
            draw_job(self.layout, self.request_id)

    def execute(self, context):
        return {"FINISHED"}
