# SPDX-FileCopyrightText: 2026 Scenario Inc.
# SPDX-License-Identifier: GPL-3.0-or-later
"""Explicit expanded creation view over the existing native controls and jobs."""

from types import SimpleNamespace
from weakref import WeakKeyDictionary

import bpy
from bpy.props import EnumProperty, PointerProperty

from . import (
    film,
    film_capture_controls,
    film_case_study_controls,
    film_composition_controls,
    film_export_controls,
    film_review_controls,
    film_scene_controls,
    film_timeline_controls,
    label_wrap,
    library_view,
    panels,
    runtime,
    workflow_controls,
)

_popups = WeakKeyDictionary()


def track_popup(operator, region):
    """Refresh `operator`'s open popup `region` from the pump; ownership stays weak."""
    if region is not None:
        _popups[operator] = region


def redraw_popups():
    """Temporary popup regions are not among an area's normal regions."""
    for operator, region in list(_popups.items()):
        try:
            region.tag_refresh_ui()
        except ReferenceError:
            del _popups[operator]


PAGES = (
    ("CREATE", "Create", "Use the same model, prompt, references and settings as the composer"),
    (
        "FILM",
        "Film",
        "Prepare Film tasks, shots, captures, timeline, composition, review and export",
    ),
    ("WORKFLOWS", "Workflows", "Choose a workflow, edit its inputs and approve its exact price"),
    ("LIBRARY", "Library", "Browse and search assets in the selected connection"),
    ("JOBS", "Jobs", "Inspect running and saved jobs in the selected connection"),
    ("RESULTS", "Results", "Inspect results, reuse references and explicitly apply saved assets"),
    ("CONNECTION", "Connection", "Inspect account/project selection and local agent setup"),
)
FILM_PAGES = (
    ("TASKS", "Tasks", "Load a recipe and approve individual tasks"),
    ("SHOTS", "Shots", "Prepare and build shot scenes"),
    ("CAPTURE", "Capture", "Review local rendering and separately approve capture upload"),
    ("TIMELINE", "Timeline", "Build an editable sequence from selected shot scenes"),
    ("COMPOSITION", "Composition", "Review and approve final or previs composition"),
    (
        "REVIEW",
        "Review",
        "Assemble saved media into a review scene and export a video or a case-study bundle",
    ),
)
# Each page draws its panels in order; Review also offers both exports.
FILM_PANELS = {
    "TASKS": (film.SCENARIO_PT_film,),
    "SHOTS": (film_scene_controls.SCENARIO_PT_film_shots,),
    "CAPTURE": (film_capture_controls.SCENARIO_PT_film_capture,),
    "TIMELINE": (film_timeline_controls.SCENARIO_PT_film_timeline,),
    "COMPOSITION": (film_composition_controls.SCENARIO_PT_film_composition,),
    "REVIEW": (
        film_review_controls.SCENARIO_PT_film_review,
        film_export_controls.SCENARIO_PT_film_export,
        film_case_study_controls.SCENARIO_PT_film_case_study,
    ),
}


class ScenarioStudioView(bpy.types.PropertyGroup):
    # Navigation must not tag a scene and invalidate its quote or Film review.
    page: EnumProperty(items=PAGES, default="CREATE", options={"SKIP_SAVE"})
    film_page: EnumProperty(items=FILM_PAGES, default="TASKS", options={"SKIP_SAVE"})


def popup_width(context):
    """Bound the requested logical width by the invoking area and current DPI."""
    scale = max(1.0, context.preferences.system.ui_scale)
    available = min(context.area.width, context.window.width) / scale
    return max(160, min(960, int(available - 48)))


def prepare_view(context):
    """Flush the focused composer only into its unchanged original form."""
    state = runtime.state.composer
    if state is not None:
        state.flush_focused_prompt(context.scene)


def draw_panel(layout, context, panel):
    """Reuse the actual sidebar draw contract; it owns no separate form state."""
    panel.draw(SimpleNamespace(layout=layout), context)


def draw_connection(layout, context):
    box = layout.box()
    box.label(text="Account and project", icon="PREFERENCES")
    prefs = runtime.prefs()
    source = getattr(prefs, "credential_source", "PREFERENCES")
    box.label(text="Credentials: " + ("Environment" if source == "ENVIRONMENT" else "Preferences"))
    project = getattr(prefs, "project_id", "").strip()
    box.label(text="Project: " + (project or "API key default scope"))
    box.operator(
        "preferences.addon_show", text="Open Scenario Preferences", icon="PREFERENCES"
    ).module = runtime.PACKAGE
    box.label(text="Changing credentials or project requires fresh estimates")
    draw_panel(layout, context, panels.SCENARIO_PT_agents)


def draw_view(layout, context, *, width):
    """Draw the selected page in a popup laid out ``width`` interface units wide.

    Wrapped labels measure against that width: Blender lays the popup out before
    it sizes the popup's region, which is 0 px wide on the first draw.
    """
    with label_wrap.popup_layout(width):
        _draw_page(layout, context, width)


def _draw_page(layout, context, width):
    view = context.window_manager.scenario_studio_view
    header = layout.row()
    header.label(text="Open Studio", icon="SHADERFX")
    header.label(text=context.scene.name, icon="SCENE_DATA")
    layout.label(text="Press Esc or click outside to return to the viewport")
    identifiers = tuple(item[0] for item in PAGES)
    rows = (
        (identifiers,)
        if width >= 720
        else tuple(identifiers[i : i + 3] for i in range(0, len(identifiers), 3))
    )
    panels.draw_enum_tabs(layout, view, "page", rows)
    layout.separator(factor=0.5)
    if view.page == "CREATE":
        draw_panel(layout, context, panels.SCENARIO_PT_main)
    elif view.page == "FILM":
        # Studio reuses panel bodies only, so it draws the sidebar header's status itself.
        film.draw_experimental(layout)
        identifiers = tuple(item[0] for item in FILM_PAGES)
        rows = (
            (identifiers,)
            if width >= 720
            else tuple(identifiers[i : i + 3] for i in range(0, len(identifiers), 3))
        )
        panels.draw_enum_tabs(layout, view, "film_page", rows)
        for index, panel in enumerate(FILM_PANELS[view.film_page]):
            layout.separator(factor=0.5 if index == 0 else 1.0)
            draw_panel(layout, context, panel)
    elif view.page == "JOBS":
        draw_panel(layout, context, panels.SCENARIO_PT_jobs)
    elif view.page == "WORKFLOWS":
        workflow_controls.draw(layout, context)
    elif view.page == "LIBRARY":
        library_view.draw(layout, context)
    elif view.page == "RESULTS":
        draw_panel(layout, context, panels.SCENARIO_PT_generations)
    else:
        draw_connection(layout, context)


class SCENARIO_OT_open_studio(bpy.types.Operator):
    bl_idname = "scenario.open_studio"
    bl_label = "Open Studio workspace"
    bl_description = "Open expanded creation, Film, jobs and results using the current shared form and connection"
    bl_options = {"INTERNAL"}

    @classmethod
    def poll(cls, context):
        return (
            not bpy.app.background
            and context.window is not None
            and context.area is not None
            and context.area.type == "VIEW_3D"
        )

    def invoke(self, context, event):
        try:
            prepare_view(context)
        except RuntimeError as error:
            self.report({"WARNING"}, str(error))
            return {"CANCELLED"}
        self._width = popup_width(context)
        return context.window_manager.invoke_popup(self, width=self._width)

    def draw(self, context):
        track_popup(self, context.region_popup)
        draw_view(self.layout, context, width=self._width)

    def execute(self, context):
        return self.invoke(context, None)


CLASSES = (ScenarioStudioView, SCENARIO_OT_open_studio)


def register():
    for cls in CLASSES:
        bpy.utils.register_class(cls)
    bpy.types.WindowManager.scenario_studio_view = PointerProperty(
        type=ScenarioStudioView, options={"SKIP_SAVE"}
    )


def unregister():
    _popups.clear()
    del bpy.types.WindowManager.scenario_studio_view
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)
