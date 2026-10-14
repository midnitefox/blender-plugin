# SPDX-FileCopyrightText: 2026 Scenario Inc.
# SPDX-License-Identifier: GPL-3.0-or-later
"""Native explicit shot selection and editable timeline build confirmation."""

import textwrap

import bpy
from bpy.props import BoolProperty, CollectionProperty, IntProperty, StringProperty

from . import film_jobs, runtime
from .film_scene_controls import _error, commands


class ScenarioFilmSceneChoice(bpy.types.PropertyGroup):
    source_id: StringProperty()


class ScenarioFilmTimelineShot(bpy.types.PropertyGroup):
    title: StringProperty()
    choices: CollectionProperty(type=ScenarioFilmSceneChoice)
    choice_index: IntProperty(min=0)


class SCENARIO_UL_film_scene_choices(bpy.types.UIList):
    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index):
        layout.label(text=item.name, icon="SCENE_DATA")


class SCENARIO_OT_build_film_timeline(bpy.types.Operator):
    bl_idname = "scenario.build_film_timeline"
    bl_label = "Build timeline"
    bl_description = "Choose one completed local scene per shot and create a new editable timeline without generating or downloading"
    bl_options = {"UNDO"}
    shots: CollectionProperty(type=ScenarioFilmTimelineShot)
    fps: IntProperty(options={"HIDDEN", "SKIP_SAVE"})
    frames: IntProperty(options={"HIDDEN", "SKIP_SAVE"})

    @classmethod
    def poll(cls, context):
        if context.window is None or context.mode != "OBJECT":
            cls.poll_message_set("Switch to Object Mode in a Blender window to build a timeline")
            return False
        return True

    def invoke(self, context, event):
        self._used = False
        try:
            self._owner = runtime.ensure_film_jobs().session.film_timeline
            self._scene, self._binding = context.scene, film_jobs.snapshot(context.scene)
            context.view_layer.update()
            self._origin = self._owner.session.capture(context.scene)
            result = self._owner.inspect(context.scene)
            self.fps, self.frames = result["fps"], result["total_frames"]
            self.shots.clear()
            for shot in result["shots"]:
                row = self.shots.add()
                row.name, row.title = shot["shot_id"], shot["title"]
                for choice in shot["choices"]:
                    item = row.choices.add()
                    item.name, item.source_id = choice["scene"], choice["source_id"]
        except Exception:
            return _error(self, "Inspect the Film recipe and completed local shot scenes")
        for shot in self.shots:
            if not shot.choices:
                return _error(self, f"Build a matching scene for shot '{shot.title}' first")
        return context.window_manager.invoke_props_dialog(self, width=600)

    def draw(self, context):
        self.layout.label(text=f"{self.frames} frames at {self.fps} fps")
        for shot in self.shots:
            box = self.layout.box()
            box.label(text=shot.title, icon="CAMERA_DATA")
            if shot.choices:
                box.template_list(
                    "SCENARIO_UL_film_scene_choices",
                    shot.name,
                    shot,
                    "choices",
                    shot,
                    "choice_index",
                    rows=2,
                )
            else:
                box.label(text="Build a matching shot scene first", icon="ERROR")
        self.layout.label(text="Creates a new timeline; the working scene stays selected.")
        self.layout.label(text="References these scenes. Later edits to them affect the timeline.")
        self.layout.label(text="No generation, download, render or export.")

    def execute(self, context):
        for shot in self.shots:
            if not 0 <= shot.choice_index < len(shot.choices):
                return _error(self, f"Select a matching scene for shot '{shot.title}' first")
        try:
            owner = runtime.ensure_film_jobs().session.film_timeline
            if (
                owner is not self._owner
                or context.scene != self._scene
                or film_jobs.snapshot(context.scene) != self._binding
            ):
                raise ValueError("The Film destination changed")
            owner.session.validate_destination(self._origin)
            if self._used:
                raise ValueError("This timeline approval was already consumed")
            selections = {
                shot.name: shot.choices[shot.choice_index].source_id for shot in self.shots
            }
            self._used = True
            review = owner.prepare(context.scene, selections=selections)
            result = owner.approve(review["review_id"])
        except Exception:
            return _error(self, "The recipe or selected shots changed; review the timeline again")
        if result["phase"] == "ERROR":
            return _error(self, result["error"])
        if result["phase"] == "UNCERTAIN":
            self.report({"WARNING"}, result["error"])
        else:
            self.report({"INFO"}, f"Built {result['scene']}")
        return {"FINISHED"}


class SCENARIO_PT_film_timeline(bpy.types.Panel):
    bl_label = "Timeline"
    bl_parent_id = "SCENARIO_PT_film"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Open Studio"

    @classmethod
    def poll(cls, context):
        return bool(context.scene.scenario_film.recipe_json)

    def draw(self, context):
        owner = commands(create=False)
        status = owner.session.film_timeline.current(context.scene) if owner else None
        if status and status["phase"] == "DISCARDED":
            status = None
        if status and status["phase"] == "UNCERTAIN":
            self.layout.label(text="Inspect the partial timeline before continuing", icon="ERROR")
        else:
            self.layout.operator("scenario.build_film_timeline", icon="SEQUENCE")
        self.layout.label(text="Choose a completed scene for every shot.")
        if status:
            if status["scene"]:
                self.layout.label(text=f"Built: {status['scene']}", icon="CHECKMARK")
            if status["error"]:
                for line in textwrap.wrap(status["error"], 36):
                    self.layout.label(text=line)
                self.layout.operator(
                    "scenario.copy_text", text="Copy error", icon="COPYDOWN"
                ).text = status["error"]
            if status["phase"] in {"READY", "ERROR", "UNCERTAIN"}:
                self.layout.operator("scenario.discard_film_timeline", icon="X").review_id = status[
                    "review_id"
                ]


class SCENARIO_OT_discard_film_timeline(bpy.types.Operator):
    bl_idname = "scenario.discard_film_timeline"
    bl_label = "Discard timeline review"
    bl_description = "Retire this local review without changing or deleting Blender data"
    review_id: StringProperty(options={"HIDDEN", "SKIP_SAVE"})
    inspected: BoolProperty(name="I inspected any partial timeline data", default=False)
    uncertain: BoolProperty(options={"HIDDEN", "SKIP_SAVE"})

    def invoke(self, context, event):
        try:
            owner = runtime.ensure_film_jobs().session.film_timeline
            self.uncertain = owner.status(self.review_id)["phase"] == "UNCERTAIN"
        except Exception:
            return _error(self, "The timeline review is no longer available")
        self.inspected = False
        return context.window_manager.invoke_props_dialog(self, width=540)

    def draw(self, context):
        self.layout.label(text="Only retires the review. Existing Blender data stays unchanged.")
        if self.uncertain:
            self.layout.prop(self, "inspected")

    def execute(self, context):
        try:
            runtime.ensure_film_jobs().session.film_timeline.discard(
                self.review_id, inspected=self.inspected
            )
        except Exception:
            return _error(self, "Inspect partial timeline data before dismissing its review")
        return {"FINISHED"}


CLASSES = (
    ScenarioFilmSceneChoice,
    ScenarioFilmTimelineShot,
    SCENARIO_UL_film_scene_choices,
    SCENARIO_OT_build_film_timeline,
    SCENARIO_OT_discard_film_timeline,
    SCENARIO_PT_film_timeline,
)
