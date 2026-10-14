# SPDX-FileCopyrightText: 2026 Scenario Inc.
# SPDX-License-Identifier: GPL-3.0-or-later
"""Optional native Film task panel; all service actions use shared FilmJobs."""

import json
import uuid
from pathlib import Path

import bpy
from bpy.props import CollectionProperty, EnumProperty, IntProperty, PointerProperty, StringProperty
from bpy_extras.io_utils import ImportHelper

from ..core.jobs.upload_store import UploadState
from ..core.ui.costs import format_approved_cost
from . import (
    film_capture_controls,
    film_case_study_controls,
    film_composition_controls,
    film_export_controls,
    film_jobs,
    film_review_controls,
    film_scene_controls,
    film_timeline_controls,
    runtime,
)


class ScenarioFilmTask(bpy.types.PropertyGroup):
    title: StringProperty()
    kind: StringProperty()
    model_id: StringProperty()


class ScenarioFilmShot(bpy.types.PropertyGroup):
    title: StringProperty()


class ScenarioFilm(bpy.types.PropertyGroup):
    production_id: StringProperty(options={"HIDDEN"})
    recipe_json: StringProperty(options={"HIDDEN"})
    title: StringProperty()
    tasks: CollectionProperty(type=ScenarioFilmTask)
    task_index: IntProperty(min=0)
    shots: CollectionProperty(type=ScenarioFilmShot)
    shot_index: IntProperty(min=0)


def selected(scene):
    props = scene.scenario_film
    if not 0 <= props.task_index < len(props.tasks):
        raise ValueError("Select a Film task first")
    return props.tasks[props.task_index].name


def _error(operator, message):
    operator.report({"WARNING"}, message)
    return {"CANCELLED"}


def draw_experimental(layout):
    """Film's explicit release status for the sidebar header and Studio; drawing only."""
    layout.label(text="Experimental", icon="EXPERIMENTAL")


class SCENARIO_OT_load_film(bpy.types.Operator, ImportHelper):
    bl_idname = "scenario.load_film"
    bl_label = "Load recipe"
    bl_description = (
        "Validate and load a Film JSON recipe while preserving this production's identity"
    )
    # Undo records the loaded recipe and marks the file modified, so Blender asks to save it.
    bl_options = {"UNDO"}
    filename_ext = ".json"
    filter_glob: StringProperty(default="*.json", options={"HIDDEN"})

    def execute(self, context):
        try:
            with Path(self.filepath).open("rb") as stream:
                data = stream.read(2_000_001)
            if len(data) > 2_000_000:
                raise ValueError("Recipe exceeds 2 MB")
            film_jobs.load_recipe(context.scene, json.loads(data))
        except Exception:
            return _error(self, "Could not load this recipe; use a valid Film JSON file up to 2 MB")
        return {"FINISHED"}


class SCENARIO_OT_new_film_production(bpy.types.Operator):
    bl_idname = "scenario.new_film_production"
    bl_label = "New production"
    bl_description = (
        "Start a distinct production using this recipe while keeping previous saved jobs"
    )
    bl_options = {"UNDO"}

    def invoke(self, context, event):
        return context.window_manager.invoke_confirm(self, event)

    def execute(self, context):
        context.scene.scenario_film.production_id = uuid.uuid4().hex
        return {"FINISHED"}


class SCENARIO_OT_quote_film(bpy.types.Operator):
    bl_idname = "scenario.quote_film"
    bl_label = "Estimate task"
    bl_description = "Request the selected Film task's exact price without submitting it"

    def execute(self, context):
        try:
            runtime.ensure_film_jobs().quote(context.scene, selected(context.scene))
        except Exception:
            return _error(
                self, "Could not estimate this task; inspect saved work, recipe and connection"
            )
        return {"FINISHED"}


class SCENARIO_OT_discard_film_quote(bpy.types.Operator):
    bl_idname = "scenario.discard_film_quote"
    bl_label = "Discard estimate"
    bl_description = "Release this unsubmitted Film estimate without changing saved jobs"
    quote_id: StringProperty(options={"HIDDEN"})

    def execute(self, context):
        try:
            runtime.ensure_film_jobs().discard(self.quote_id, context.scene)
        except Exception:
            return _error(self, "The estimate changed; inspect Film tasks again")
        return {"FINISHED"}


class SCENARIO_OT_approve_film(bpy.types.Operator):
    bl_idname = "scenario.approve_film"
    bl_label = "Generate Film task"
    bl_description = (
        "Spend the displayed exact CU price once on this Film task; results remain saved"
    )
    quote_id: StringProperty(options={"HIDDEN"})
    approved_cost: StringProperty(options={"HIDDEN"})
    task_label: StringProperty(options={"HIDDEN"})

    def invoke(self, context, event):
        try:
            owner = runtime.ensure_film_jobs()
            item = owner.actions[self.quote_id]
            owner.finish(item, context.scene)
            if item.phase != "READY" or item.cost != self.approved_cost:
                raise ValueError("Estimate changed")
            self.task_label = item.task_id
        except Exception:
            return _error(self, "The estimate changed; review the task again")
        return context.window_manager.invoke_props_dialog(self, width=480)

    def draw(self, context):
        self.layout.label(text=f"Task: {self.task_label}")
        self.layout.label(text=f"Cost: {format_approved_cost(self.approved_cost)}")
        self.layout.label(text="One submission. Results stay saved for explicit application.")

    def execute(self, context):
        try:
            runtime.ensure_film_jobs().approve(
                self.quote_id, context.scene, approved_cost=self.approved_cost
            )
        except film_jobs.FilmApprovalUnavailable:
            return _error(self, "The estimate changed or is unavailable; review the task again")
        except Exception:
            return _error(
                self, "Submission needs review; inspect saved Film tasks before continuing"
            )
        return {"FINISHED"}


class ScenarioFilmUpload(bpy.types.PropertyGroup):
    request_id: StringProperty()
    revision: IntProperty()
    asset_id: StringProperty()
    kind: StringProperty()


class SCENARIO_UL_film_uploads(bpy.types.UIList):
    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index):
        layout.label(text=f"{item.kind}: {item.asset_id}", icon="FILE")


class SCENARIO_OT_bind_film_upload(bpy.types.Operator):
    bl_idname = "scenario.bind_film_upload"
    bl_label = "Use saved upload"
    bl_description = (
        "Associate one unchanged imported upload with this Film task without sending bytes"
    )
    uploads: CollectionProperty(type=ScenarioFilmUpload)
    upload_index: IntProperty(min=0)
    task_id: StringProperty(options={"HIDDEN"})

    def invoke(self, context, event):
        try:
            owner = runtime.ensure_film_jobs()
            self._owner, self._scene = owner, context.scene
            self._binding = film_jobs.snapshot(context.scene)
            self.task_id = selected(context.scene)
            self.uploads.clear()
            for item in owner.session.upload_recovery_plan():
                record = item.record
                if record.state != UploadState.IMPORTED or record.intent.kind == "model":
                    continue
                row = self.uploads.add()
                row.request_id, row.revision = record.intent.request_id, record.revision
                row.asset_id, row.kind = record.asset_id, record.intent.kind
            if not self.uploads:
                raise ValueError("No imported uploads")
        except Exception:
            return _error(self, "Import a reference first, then choose its saved upload")
        return context.window_manager.invoke_props_dialog(self, width=520)

    def draw(self, context):
        self.layout.label(text=f"Film task: {self.task_id}")
        self.layout.template_list(
            "SCENARIO_UL_film_uploads", "", self, "uploads", self, "upload_index", rows=6
        )
        self.layout.label(text="Saves this association. To change it later, name a new task.")

    def execute(self, context):
        try:
            owner = runtime.ensure_film_jobs()
            if (
                owner is not self._owner
                or context.scene != self._scene
                or film_jobs.snapshot(context.scene) != self._binding
                or selected(context.scene) != self.task_id
            ):
                raise ValueError("Destination changed")
            upload = self.uploads[self.upload_index]
            owner.bind_upload(
                context.scene,
                self.task_id,
                request_id=upload.request_id,
                expected_revision=upload.revision,
            )
        except Exception:
            return _error(self, "The upload or Film destination changed; choose it again")
        return {"FINISHED"}


class SCENARIO_UL_film_tasks(bpy.types.UIList):
    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index):
        layout.label(text=item.title, icon="FILE" if item.kind == "upload" else "RENDER_ANIMATION")


class SCENARIO_PT_film(bpy.types.Panel):
    bl_label = "Film"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Open Studio"
    bl_options = {"DEFAULT_CLOSED"}
    bl_order = 20

    def draw_header_preset(self, context):
        # Drawn in this parent header: visible while collapsed and above the nested Film panels.
        draw_experimental(self.layout)

    def draw(self, context):
        layout = self.layout
        props = context.scene.scenario_film
        box = layout.box()
        box.label(text="Recipe", icon="TEXT")
        row = box.row(align=True)
        row.operator("scenario.load_film", icon="FILE_FOLDER")
        row.operator("scenario.new_film_production", icon="DUPLICATE")
        if not props.recipe_json:
            box.label(text="Load a recipe to prepare individual Film tasks")
            return
        box.label(text=props.title)
        box.template_list("SCENARIO_UL_film_tasks", "", props, "tasks", props, "task_index", rows=5)
        if not 0 <= props.task_index < len(props.tasks):
            return
        task = props.tasks[props.task_index]
        box = layout.box()
        box.label(text=task.name, icon="SETTINGS")
        owner = runtime.state.film_jobs
        item = owner.current(context.scene, task.name) if owner is not None else None
        if item is not None and item.phase in {"QUOTING", "BINDING"}:
            box.label(text="Estimating..." if item.phase == "QUOTING" else "Saving association...")
        elif item is not None and item.phase == "SUBMITTED":
            box.label(text="Submission saved", icon="CHECKMARK")
        elif item is not None and item.phase == "BOUND":
            box.label(text="Upload associated", icon="CHECKMARK")
        elif task.kind == "upload":
            box.operator("scenario.bind_film_upload", icon="LINKED")
        elif item is not None and item.phase == "READY":
            row = box.row()
            row.scale_y = 1.5
            op = row.operator(
                "scenario.approve_film",
                text=f"Generate ({format_approved_cost(item.cost)})",
                icon="PLAY",
            )
            op.quote_id, op.approved_cost = item.identifier, item.cost
            box.operator("scenario.discard_film_quote", icon="X").quote_id = item.identifier
        else:
            box.operator("scenario.quote_film", icon="SORTTIME")
        if item is not None and item.error:
            box.label(text="Action needs review. Inspect saved jobs.", icon="ERROR")
        layout.operator("scenario.inspect_saved_jobs", icon="FILE_REFRESH")
        layout.label(text="Assemble saved media under Review; export a video under Export")


CLASSES = (
    ScenarioFilmTask,
    ScenarioFilmShot,
    ScenarioFilm,
    ScenarioFilmUpload,
    SCENARIO_OT_load_film,
    SCENARIO_OT_new_film_production,
    SCENARIO_OT_quote_film,
    SCENARIO_OT_discard_film_quote,
    SCENARIO_OT_approve_film,
    SCENARIO_UL_film_uploads,
    SCENARIO_OT_bind_film_upload,
    SCENARIO_UL_film_tasks,
    SCENARIO_PT_film,
    *film_scene_controls.CLASSES,
    *film_timeline_controls.CLASSES,
    *film_capture_controls.CLASSES,
    *film_composition_controls.CLASSES,
    *film_review_controls.CLASSES,
    *film_export_controls.CLASSES,
    *film_case_study_controls.CLASSES,
)


_scene_modes = {"composition": {}, "review": {}}


def _scene_mode(key):
    modes = _scene_modes[key]

    def get(_owner):
        scene = bpy.context.scene
        return modes.get(scene.session_uid, 0) if scene is not None else 0

    def set_mode(_owner, value):
        # WindowManager stores navigation without tagging scene revisions. Session UIDs
        # keep scenes (including deleted/recreated names) independent across windows.
        live = {scene.session_uid for scene in bpy.data.scenes}
        for identifier in tuple(modes):
            if identifier not in live:
                del modes[identifier]
        scene = bpy.context.scene
        if scene is not None:
            modes[scene.session_uid] = value

    return get, set_mode


def register():
    for cls in CLASSES:
        bpy.utils.register_class(cls)
    bpy.types.Scene.scenario_film = PointerProperty(type=ScenarioFilm)
    # Navigation must not tag the recipe scene and invalidate its retained quotes or reviews.
    get, set_mode = _scene_mode("composition")
    bpy.types.WindowManager.scenario_film_composition_mode = EnumProperty(
        items=[
            ("final", "Final", "Include editorial audio and source trims"),
            ("previs", "Previs", "Review the previs cut without editorial audio"),
        ],
        options={"SKIP_SAVE"},
        get=get,
        set=set_mode,
    )
    get, set_mode = _scene_mode("review")
    bpy.types.WindowManager.scenario_film_review_mode = EnumProperty(
        items=[
            ("final", "Final", "Assemble the final cut with editorial audio and source trims"),
            ("previs", "Previs", "Assemble the previs takes without editorial audio"),
        ],
        options={"SKIP_SAVE"},
        get=get,
        set=set_mode,
    )


def unregister():
    for modes in _scene_modes.values():
        modes.clear()
    del bpy.types.WindowManager.scenario_film_review_mode
    del bpy.types.WindowManager.scenario_film_composition_mode
    del bpy.types.Scene.scenario_film
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)
