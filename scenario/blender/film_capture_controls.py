# SPDX-FileCopyrightText: 2026 Scenario Inc.
# SPDX-License-Identifier: GPL-3.0-or-later
"""Explicit local Film capture and separate upload controls; drawing stays read-only."""

import textwrap

import bpy
from bpy.props import CollectionProperty, EnumProperty, IntProperty, StringProperty

from . import film_jobs, runtime
from .film_scene_controls import _error, commands, selected
from .film_timeline_controls import ScenarioFilmSceneChoice


def owner():
    return runtime.ensure_film_jobs().session.film_capture


class SCENARIO_OT_capture_film_shot(bpy.types.Operator):
    bl_idname = "scenario.capture_film_shot"
    bl_label = "Render capture"
    bl_description = (
        "Review one local shot scene and render a still or silent editorial clip without uploading"
    )
    choices: CollectionProperty(type=ScenarioFilmSceneChoice)
    choice_index: IntProperty(min=0)
    kind: EnumProperty(
        items=[("VIDEO", "Video", "Exact editorial range"), ("STILL", "Still", "First shot frame")]
    )
    color_type: EnumProperty(
        items=[
            ("MATERIAL", "Material colors", ""),
            ("TEXTURE", "Textures", ""),
            ("OBJECT", "Object colors", ""),
        ]
    )
    width: IntProperty(name="Width", default=1280, min=64, max=4096)
    height: IntProperty(name="Height", default=720, min=64, max=4096)

    def invoke(self, context, event):
        try:
            self._owner, self._scene = owner(), context.scene
            self._binding, self._shot = film_jobs.snapshot(context.scene), selected(context.scene)
            context.view_layer.update()
            self._origin = self._owner.session.capture(context.scene)
            result = self._owner.inspect(context.scene, shot_id=self._shot)
            self._frames, self._fps = result["frames"], result["fps"]
            self._source_duration, self._source_trim = (
                result["source_duration"],
                result["source_trim"],
            )
            self.choices.clear()
            for choice in result["choices"]:
                row = self.choices.add()
                row.name, row.source_id = choice["scene"], choice["source_id"]
            self._used = False
        except Exception:
            return _error(self, "Inspect the Film recipe and completed shot scenes")
        if not self.choices:
            return _error(self, "Build a matching scene for the selected Film shot first")
        return context.window_manager.invoke_props_dialog(self, width=520)

    def draw(self, context):
        layout = self.layout
        layout.template_list(
            "SCENARIO_UL_film_scene_choices", "", self, "choices", self, "choice_index", rows=2
        )
        layout.prop(self, "kind")
        layout.prop(self, "color_type")
        row = layout.row(align=True)
        row.prop(self, "width")
        row.prop(self, "height")
        frames = 1 if self.kind == "STILL" else self._frames
        layout.label(text=f"{frames} frames at {self._fps} fps; no padding or retiming")
        if self.kind == "VIDEO":
            layout.label(text="Requires installed ffmpeg and ffprobe; no audio.")
        layout.label(
            text=f"Generated source: {self._source_duration}s; editorial trim: {self._source_trim}s"
        )
        layout.label(text="Local capture only. Review the output before uploading.")

    def execute(self, context):
        if not 0 <= self.choice_index < len(self.choices):
            return _error(self, "Select a matching shot scene before rendering a capture")
        try:
            if owner() is not self._owner or context.scene != self._scene or self._used:
                raise ValueError("Capture owner changed")
            if film_jobs.snapshot(context.scene) != self._binding:
                raise ValueError("Recipe changed")
            self._owner.session.validate_destination(self._origin)
            self._used = True
            review = self._owner.prepare(
                context.scene,
                shot_id=self._shot,
                source_id=self.choices[self.choice_index].source_id,
                kind=self.kind,
                width=self.width,
                height=self.height,
                color_type=self.color_type,
            )
            result = self._owner.approve(review["review_id"])
            if result["phase"] == "ERROR":
                return _error(self, result["error"])
        except Exception:
            return _error(
                self, "Check the shot, dimensions and installed media tools; review capture again"
            )
        return {"FINISHED"}


class SCENARIO_OT_render_film_capture(bpy.types.Operator):
    bl_idname = "scenario.render_film_capture"
    bl_label = "Render prepared capture"
    bl_description = "Approve the exact prepared local capture without uploading or generating"
    review_id: StringProperty(options={"HIDDEN", "SKIP_SAVE"})

    def invoke(self, context, event):
        try:
            self._owner = owner()
            self._status = self._owner.status(self.review_id)
            if self._status["phase"] != "READY":
                raise ValueError("Capture is not ready")
        except Exception:
            return _error(self, "Prepare a current Film capture first")
        return context.window_manager.invoke_props_dialog(self, width=520)

    def draw(self, context):
        status = self._status
        self.layout.label(text=status["scene"])
        self.layout.label(
            text=f"{status['kind']}: {status['frames']} frames at {status['fps']} fps"
        )
        self.layout.label(text=f"{status['width']} x {status['height']}; {status['color_type']}")
        self.layout.label(text="Local capture only. No upload or generation.")

    def execute(self, context):
        try:
            if owner() is not self._owner:
                raise ValueError("Connection changed")
            result = self._owner.approve(self.review_id)
            if result["phase"] == "ERROR":
                return _error(self, result["error"])
        except Exception:
            return _error(self, "The capture or connection changed; prepare another review")
        return {"FINISHED"}


class SCENARIO_OT_cancel_film_capture(bpy.types.Operator):
    bl_idname = "scenario.cancel_film_capture"
    bl_label = "Cancel capture"
    bl_description = "Stop this local render and retain completed frames for inspection"
    review_id: StringProperty(options={"HIDDEN", "SKIP_SAVE"})

    def invoke(self, context, event):
        return context.window_manager.invoke_confirm(self, event)

    def execute(self, context):
        try:
            owner().cancel(self.review_id)
        except Exception:
            return _error(self, "The local capture has already stopped; inspect its status")
        return {"FINISHED"}


class SCENARIO_OT_discard_film_capture(bpy.types.Operator):
    bl_idname = "scenario.discard_film_capture"
    bl_label = "Discard capture"
    bl_description = (
        "Delete this capture's local snapshot, output, frames and logs; saved uploads remain"
    )
    review_id: StringProperty(options={"HIDDEN", "SKIP_SAVE"})

    def invoke(self, context, event):
        return context.window_manager.invoke_confirm(self, event)

    def execute(self, context):
        try:
            owner().discard(self.review_id)
        except Exception:
            return _error(self, "Wait for active work or inspect local capture cleanup")
        return {"FINISHED"}


class SCENARIO_OT_upload_film_capture(bpy.types.Operator):
    bl_idname = "scenario.upload_film_capture"
    bl_label = "Upload capture"
    bl_description = "Upload the reviewed capture bytes, then explicitly associate the saved upload with a Film task"
    review_id: StringProperty(options={"HIDDEN", "SKIP_SAVE"})

    def invoke(self, context, event):
        try:
            self._owner = owner()
            self._status = self._owner.status(self.review_id)
            if self._status["phase"] != "CAPTURED":
                raise ValueError("Capture is not ready")
        except Exception:
            return _error(self, "Review a completed local capture first")
        return context.window_manager.invoke_props_dialog(self, width=520)

    def draw(self, context):
        status = self._status
        self.layout.label(text=f"{status['scene']}: {status['width']} x {status['height']}")
        self.layout.label(
            text=f"{status['frames']} frames at {status['fps']} fps; {status['size']} bytes"
        )
        for line in textwrap.wrap(status["sha256"], 40):
            self.layout.label(text=line)
        self.layout.label(text="Upload these bytes to the selected Scenario connection.")
        self.layout.label(text="No generation or automatic Film task association.")

    def execute(self, context):
        try:
            if owner() is not self._owner:
                raise ValueError("Connection changed")
            self._owner.upload(self.review_id, runtime.ensure_reference_uploads())
        except Exception:
            return _error(self, "Upload could not start; inspect capture and saved upload progress")
        return {"FINISHED"}


class SCENARIO_PT_film_capture(bpy.types.Panel):
    bl_label = "Capture"
    bl_parent_id = "SCENARIO_PT_film"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Open Studio"

    @classmethod
    def poll(cls, context):
        return bool(context.scene.scenario_film.recipe_json)

    def draw(self, context):
        source = commands(create=False)
        try:
            shot = selected(context.scene)
        except ValueError:
            self.layout.label(text="Select a Film shot first")
            return
        status = source.session.film_capture.current(context.scene, shot) if source else None
        phase = status["phase"] if status else ""
        layout = self.layout
        if not status:
            layout.operator("scenario.capture_film_shot", icon="RENDER_ANIMATION")
            return
        layout.label(text=f"{status['scene']}: {phase.replace('_', ' ').title()}")
        if phase == "READY":
            layout.operator(
                "scenario.render_film_capture", icon="RENDER_ANIMATION"
            ).review_id = status["review_id"]
        elif phase in {"RENDERING", "WAITING"}:
            layout.operator("scenario.cancel_film_capture", icon="CANCEL").review_id = status[
                "review_id"
            ]
        elif phase == "CANCELLING":
            layout.label(text="Stopping the owned render process...")
        elif phase == "CAPTURED":
            layout.operator(
                "scenario.open_path", text="Open capture", icon="FILE_FOLDER"
            ).filepath = status["path"]
            layout.operator("scenario.upload_film_capture", icon="EXPORT").review_id = status[
                "review_id"
            ]
        elif phase in {"UPLOADING", "UPLOADED", "UPLOAD_REVIEW"}:
            layout.operator("scenario.inspect_uploads", icon="FILE_REFRESH")
            layout.label(text="Select a Film upload task.")
            layout.label(text="Then choose Use saved upload.")
        if status["error"]:
            for line in textwrap.wrap(status["error"], 36):
                layout.label(text=line)
        if status["directory"]:
            layout.operator(
                "scenario.open_path", text="Open capture files", icon="FILE_FOLDER"
            ).filepath = status["directory"]
        if phase not in {"RENDERING", "WAITING", "CANCELLING", "UPLOADING"}:
            layout.operator("scenario.discard_film_capture", icon="TRASH").review_id = status[
                "review_id"
            ]


CLASSES = (
    SCENARIO_OT_capture_film_shot,
    SCENARIO_OT_render_film_capture,
    SCENARIO_OT_cancel_film_capture,
    SCENARIO_OT_discard_film_capture,
    SCENARIO_OT_upload_film_capture,
    SCENARIO_PT_film_capture,
)
