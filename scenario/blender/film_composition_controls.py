# SPDX-FileCopyrightText: 2026 Scenario Inc.
# SPDX-License-Identifier: GPL-3.0-or-later
"""Native composition preparation, exact price review and separate generation approval."""

import textwrap

import bpy
from bpy.props import StringProperty

from ..core.ui.costs import format_cu
from . import runtime
from .film_scene_controls import _error


def commands(*, create=True):
    if create:
        return runtime.ensure_film_jobs().compositions
    owner = runtime.state.film_jobs
    if (
        owner is None
        or not owner.session.active
        or owner.session is not runtime.state.job_session
        or not runtime.catalog_selection_matches()
    ):
        return None
    return owner.compositions


class SCENARIO_OT_prepare_film_composition(bpy.types.Operator):
    bl_idname = "scenario.prepare_film_composition"
    bl_label = "Prepare composition"
    bl_description = (
        "Inspect saved media for the selected composition without uploading or spending"
    )

    def execute(self, context):
        try:
            commands().prepare(
                context.scene, mode=context.window_manager.scenario_film_composition_mode
            )
        except Exception:
            return _error(self, "Check the recipe, saved sources and existing composition reviews")
        return {"FINISHED"}


class SCENARIO_OT_estimate_film_composition(bpy.types.Operator):
    bl_idname = "scenario.estimate_film_composition"
    bl_label = "Request price"
    bl_description = (
        "Request the exact Scenario price for the verified composition without generating"
    )
    review_id: StringProperty(options={"HIDDEN", "SKIP_SAVE"})

    def execute(self, context):
        try:
            commands().estimate(self.review_id)
        except Exception:
            return _error(self, "Enable online access and prepare a current composition first")
        return {"FINISHED"}


class SCENARIO_OT_generate_film_composition(bpy.types.Operator):
    bl_idname = "scenario.generate_film_composition"
    bl_label = "Generate master"
    bl_description = "Approve the displayed exact price and submit this composition once"
    review_id: StringProperty(options={"HIDDEN", "SKIP_SAVE"})

    def invoke(self, context, event):
        try:
            self._owner = commands()
            self._owner.poll()
            self._status = self._owner.status(self.review_id)
            if self._status["phase"] != "QUOTED":
                raise ValueError("Request a fresh price")
            self._cost = self._status["cu_cost_exact"]
        except Exception:
            return _error(self, "Request a current composition price first")
        return context.window_manager.invoke_props_dialog(self, width=540)

    def draw(self, context):
        status = self._status
        self.layout.label(text=f"{status['scene']}: {status['mode'].title()} master")
        self.layout.label(text=f"{status['frames']} frames at {status['fps']} fps")
        self.layout.label(text=f"{status['sources']} verified sources; {status['layers']} layers")
        self.layout.label(text=f"Exact price: {self._cost} CU")
        self.layout.label(text="Saves one master job. The recipe and Blender scene stay unchanged.")
        self.layout.label(
            text="Inspect saved jobs after submission; never repeat an uncertain attempt."
        )

    def execute(self, context):
        try:
            if commands() is not self._owner:
                raise ValueError("Connection changed")
            self._owner.approve(self.review_id, approved_cost=self._cost)
        except Exception:
            return _error(self, "Composition needs review; inspect saved jobs before continuing")
        return {"FINISHED"}


class SCENARIO_OT_cancel_film_composition(bpy.types.Operator):
    bl_idname = "scenario.cancel_film_composition"
    bl_label = "Cancel preparation"
    bl_description = (
        "Stop local inspection or discard the pending price without cancelling saved jobs"
    )
    review_id: StringProperty(options={"HIDDEN", "SKIP_SAVE"})

    def execute(self, context):
        try:
            commands().cancel(self.review_id)
        except Exception:
            return _error(self, "The composition action has already stopped; inspect its status")
        return {"FINISHED"}


class SCENARIO_OT_discard_film_composition(bpy.types.Operator):
    bl_idname = "scenario.discard_film_composition"
    bl_label = "Discard review"
    bl_description = "Retire this review without deleting media, recipes or saved generation jobs"
    review_id: StringProperty(options={"HIDDEN", "SKIP_SAVE"})

    def invoke(self, context, event):
        return context.window_manager.invoke_confirm(self, event)

    def execute(self, context):
        try:
            commands().discard(self.review_id)
        except Exception:
            return _error(self, "Wait for active composition work to stop before discarding")
        return {"FINISHED"}


class SCENARIO_OT_film_composition_error(bpy.types.Operator):
    bl_idname = "scenario.film_composition_error"
    bl_label = "Composition error details"
    bl_description = "Read or copy the complete composition review error"
    message: StringProperty(options={"HIDDEN", "SKIP_SAVE"})

    @classmethod
    def description(cls, context, props):
        return props.message or cls.bl_description

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self, width=540)

    def draw(self, context):
        for line in textwrap.wrap(self.message, 70):
            self.layout.label(text=line)
        self.layout.operator(
            "scenario.copy_text", text="Copy error", icon="COPYDOWN"
        ).text = self.message

    def execute(self, context):
        return {"FINISHED"}


class SCENARIO_PT_film_composition(bpy.types.Panel):
    bl_label = "Composition"
    bl_parent_id = "SCENARIO_PT_film"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Scenario"

    @classmethod
    def poll(cls, context):
        return bool(context.scene.scenario_film.recipe_json)

    def draw(self, context):
        from .panels import equal_segments

        layout = self.layout
        props = context.window_manager
        equal_segments(
            layout.row(align=True), props, "scenario_film_composition_mode", ("final", "previs")
        )
        owner = commands(create=False)
        status = (
            owner.current(context.scene, props.scenario_film_composition_mode) if owner else None
        )
        if not status:
            layout.operator("scenario.prepare_film_composition", icon="SEQUENCE")
            layout.label(text="Uses downloaded results and retained upload files.")
            layout.label(text="Requires installed ffprobe.")
            return
        phase, identifier = status["phase"], status["review_id"]
        if phase == "READY":
            layout.label(text=f"{status['frames']} frames; {status['layers']} layers")
            layout.operator(
                "scenario.estimate_film_composition", icon="SORTTIME"
            ).review_id = identifier
        elif phase == "QUOTED":
            row = layout.row()
            row.scale_y = 1.5
            row.operator(
                "scenario.generate_film_composition",
                text=f"Generate ({format_cu(status['cu_cost_exact'])} CU)",
                icon="PLAY",
            ).review_id = identifier
        elif phase in {"PREPARING", "QUOTING", "WAITING", "CANCELLING"}:
            layout.label(
                text={
                    "PREPARING": "Inspecting saved media...",
                    "QUOTING": "Requesting price...",
                    "WAITING": "Waiting for the original scene...",
                    "CANCELLING": "Stopping preparation...",
                }[phase]
            )
        elif phase == "SUBMITTED":
            layout.label(text="Master submission saved", icon="CHECKMARK")
        elif phase == "SUBMISSION_REVIEW":
            layout.label(text="Inspect saved jobs before continuing", icon="ERROR")
        elif phase == "CANCELLED":
            layout.label(text="Preparation cancelled")
        if status["error"]:
            layout.operator(
                "scenario.film_composition_error", text="Error details", icon="ERROR"
            ).message = status["error"]
        if phase in {"PREPARING", "QUOTING", "WAITING"}:
            layout.operator(
                "scenario.cancel_film_composition", icon="CANCEL"
            ).review_id = identifier
        elif phase != "CANCELLING":
            layout.operator("scenario.discard_film_composition", icon="X").review_id = identifier
        if phase in {"SUBMITTED", "SUBMISSION_REVIEW"}:
            layout.operator("scenario.inspect_saved_jobs", icon="FILE_REFRESH")


CLASSES = (
    SCENARIO_OT_prepare_film_composition,
    SCENARIO_OT_estimate_film_composition,
    SCENARIO_OT_generate_film_composition,
    SCENARIO_OT_cancel_film_composition,
    SCENARIO_OT_discard_film_composition,
    SCENARIO_OT_film_composition_error,
    SCENARIO_PT_film_composition,
)
