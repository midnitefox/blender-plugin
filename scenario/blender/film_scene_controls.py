# SPDX-FileCopyrightText: 2026 Scenario Inc.
# SPDX-License-Identifier: GPL-3.0-or-later
"""Native Film shot review controls over the shared application commands."""

import textwrap

import bpy
from bpy.props import BoolProperty, CollectionProperty, IntProperty, StringProperty

from . import film_jobs, runtime


def selected(scene):
    props = scene.scenario_film
    if not 0 <= props.shot_index < len(props.shots):
        raise ValueError("Select a Film shot")
    return props.shots[props.shot_index].name


def commands(*, create=True):
    if create:
        return runtime.ensure_film_jobs().session.film_shots
    session = runtime.state.job_session
    if session is None or not session.active or not runtime.catalog_selection_matches():
        return None
    return session.film_shots


def _error(operator, message):
    operator.report({"WARNING"}, message)
    return {"CANCELLED"}


class ScenarioFilmAssetChoice(bpy.types.PropertyGroup):
    asset_id: StringProperty()


class ScenarioFilmHeroChoice(bpy.types.PropertyGroup):
    request_id: StringProperty()
    revision: IntProperty()
    assets: CollectionProperty(type=ScenarioFilmAssetChoice)
    asset_index: IntProperty(min=0)


class SCENARIO_UL_film_assets(bpy.types.UIList):
    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index):
        layout.label(text=item.name, icon="MESH_DATA")


class SCENARIO_OT_prepare_film_shot(bpy.types.Operator):
    bl_idname = "scenario.prepare_film_shot"
    bl_label = "Prepare shot"
    bl_description = (
        "Choose saved hero models and verify their files before a separate build approval"
    )
    heroes: CollectionProperty(type=ScenarioFilmHeroChoice)
    shot_id: StringProperty(options={"HIDDEN", "SKIP_SAVE"})

    def invoke(self, context, event):
        try:
            self._owner = commands()
            self._scene = context.scene
            self._binding = film_jobs.snapshot(context.scene)
            self.shot_id = selected(context.scene)
            bpy.context.view_layer.update()
            self._origin = self._owner.session.capture(context.scene)
            self.heroes.clear()
            for item in self._owner.inspect(context.scene, shot_id=self.shot_id):
                row = self.heroes.add()
                row.name, row.request_id, row.revision = (
                    item["hero_id"],
                    item["request_id"],
                    item["revision"],
                )
                for asset in item["assets"]:
                    choice = row.assets.add()
                    choice.name, choice.asset_id = asset["name"], asset["asset_id"]
        except Exception:
            return _error(
                self,
                "Download the shot's hero models first, then inspect its recipe and connection",
            )
        return context.window_manager.invoke_props_dialog(self, width=560)

    def draw(self, context):
        self.layout.label(text=f"Shot: {self.shot_id}")
        if not self.heroes:
            self.layout.label(text="This shot has no saved hero models")
        for hero in self.heroes:
            box = self.layout.box()
            box.label(text=hero.name, icon="OUTLINER_OB_ARMATURE")
            box.template_list(
                "SCENARIO_UL_film_assets", hero.name, hero, "assets", hero, "asset_index", rows=2
            )
        self.layout.label(
            text="Checks local files. Build shot will ask for confirmation afterward."
        )

    def execute(self, context):
        try:
            owner = commands()
            if (
                owner is not self._owner
                or context.scene != self._scene
                or film_jobs.snapshot(context.scene) != self._binding
                or selected(context.scene) != self.shot_id
            ):
                raise ValueError("Shot selection changed")
            owner.session.validate_destination(self._origin)
            selections = {
                hero.name: {
                    "request_id": hero.request_id,
                    "revision": hero.revision,
                    "asset_id": hero.assets[hero.asset_index].asset_id,
                }
                for hero in self.heroes
            }
            owner.prepare(context.scene, shot_id=self.shot_id, selections=selections)
        except Exception:
            return _error(self, "The shot or selected models changed; prepare it again")
        return {"FINISHED"}


class SCENARIO_OT_build_film_shot(bpy.types.Operator):
    bl_idname = "scenario.build_film_shot"
    bl_label = "Build shot"
    bl_description = "Create one new shot scene from the reviewed recipe and verified hero files without spending credits"
    bl_options = {"REGISTER", "UNDO"}
    review_id: StringProperty(options={"HIDDEN", "SKIP_SAVE"})
    shot_label: StringProperty(options={"HIDDEN", "SKIP_SAVE"})
    scene_label: StringProperty(options={"HIDDEN", "SKIP_SAVE"})
    hero_count: IntProperty(options={"HIDDEN", "SKIP_SAVE"})

    def invoke(self, context, event):
        try:
            owner = commands()
            owner.poll()
            status = owner.status(self.review_id)
            if status["phase"] != "READY":
                raise ValueError("Review is not ready")
            self.shot_label, self.hero_count = status["shot_id"], status["hero_count"]
            self.scene_label = context.scene.name
        except Exception:
            return _error(self, "Prepare this shot again before building")
        return context.window_manager.invoke_props_dialog(self, width=540)

    def draw(self, context):
        self.layout.label(text=f"Shot: {self.shot_label}")
        self.layout.label(text=f"Recipe scene: {self.scene_label}")
        self.layout.label(text=f"Saved hero models: {self.hero_count}")
        self.layout.label(text="Creates a new scene and keeps the working scene selected.")
        self.layout.label(
            text="No generation or download. Undo affects scene data, not saved jobs."
        )

    def execute(self, context):
        try:
            status = commands().approve(self.review_id)
        except Exception:
            return _error(self, "The review changed; inspect the shot and saved jobs")
        if status["phase"] == "ERROR":
            return _error(self, status["error"])
        if status["phase"] == "UNCERTAIN":
            self.report({"WARNING"}, status["error"])
        else:
            self.report({"INFO"}, f"Built {status['scene']}")
        return {"FINISHED"}


class SCENARIO_OT_discard_film_shot(bpy.types.Operator):
    bl_idname = "scenario.discard_film_shot"
    bl_label = "Discard review"
    bl_description = "Discard this unapproved shot review without changing scenes or saved jobs"
    review_id: StringProperty(options={"HIDDEN", "SKIP_SAVE"})

    def invoke(self, context, event):
        return context.window_manager.invoke_confirm(self, event)

    def execute(self, context):
        try:
            commands().discard(self.review_id)
        except Exception:
            return _error(self, "The review is unavailable; inspect the shot again")
        return {"FINISHED"}


class SCENARIO_OT_dismiss_film_shot(bpy.types.Operator):
    bl_idname = "scenario.dismiss_film_shot"
    bl_label = "Acknowledge inspection"
    bl_description = "Dismiss this review after inspecting the scene and saved jobs; unresolved claims remain blocked"
    review_id: StringProperty(options={"HIDDEN", "SKIP_SAVE"})
    inspected: BoolProperty(name="I inspected the scene and saved jobs", default=False)

    def invoke(self, context, event):
        self.inspected = False
        return context.window_manager.invoke_props_dialog(self, width=540)

    def draw(self, context):
        self.layout.label(text="Dismisses only this review. Saved job state stays unchanged.")
        self.layout.label(text="Unresolved claims still prevent another build.")
        self.layout.prop(self, "inspected")

    def execute(self, context):
        try:
            commands().dismiss_uncertain(self.review_id, inspected=self.inspected)
        except Exception:
            return _error(self, "Confirm inspection and save any pending build receipts first")
        return {"FINISHED"}


class SCENARIO_OT_save_film_shot_receipt(bpy.types.Operator):
    bl_idname = "scenario.save_film_shot_receipt"
    bl_label = "Save build receipt"
    bl_description = (
        "Retry only the known application receipt without rebuilding or changing the shot"
    )
    review_id: StringProperty(options={"HIDDEN", "SKIP_SAVE"})

    def execute(self, context):
        try:
            status = commands().retry_receipts(self.review_id)
            if status["receipt_retry_available"] or status["inspection_required"]:
                return _error(self, "The saved outcome still needs inspection; do not rebuild")
        except Exception:
            return _error(self, "Could not save the build receipt; inspect saved jobs")
        return {"FINISHED"}


class SCENARIO_OT_film_shot_error(bpy.types.Operator):
    bl_idname = "scenario.film_shot_error"
    bl_label = "Shot error details"
    bl_description = "Read the full saved shot review error"
    review_id: StringProperty(options={"HIDDEN", "SKIP_SAVE"})

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(
            self, width=540, title="Shot review", confirm_text="Close"
        )

    def draw(self, context):
        owner = commands(create=False)
        try:
            message = owner.status(self.review_id)["error"] if owner else ""
        except ValueError:
            message = ""
        message = message or "This review is no longer available"
        for line in textwrap.wrap(message, 65):
            self.layout.label(text=line)
        self.layout.operator(
            "scenario.copy_text", text="Copy error", icon="COPYDOWN"
        ).text = message

    def execute(self, context):
        return {"FINISHED"}


class SCENARIO_UL_film_shots(bpy.types.UIList):
    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index):
        layout.label(text=item.title, icon="CAMERA_DATA")


class SCENARIO_PT_film_shots(bpy.types.Panel):
    bl_label = "Shots"
    bl_parent_id = "SCENARIO_PT_film"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Open Studio"

    @classmethod
    def poll(cls, context):
        return bool(context.scene.scenario_film.recipe_json)

    def draw(self, context):
        props, layout = context.scene.scenario_film, self.layout
        layout.template_list(
            "SCENARIO_UL_film_shots", "", props, "shots", props, "shot_index", rows=3
        )
        if not 0 <= props.shot_index < len(props.shots):
            layout.label(text="Reload the recipe to restore its shot list")
            return
        owner = commands(create=False)
        status = owner.current(context.scene, props.shots[props.shot_index].name) if owner else None
        phase = status["phase"] if status else ""
        box = layout.box()
        box.label(text="Shot scene", icon="SCENE_DATA")
        if phase == "VERIFYING":
            box.label(text="Checking saved model files...")
        elif phase == "READY":
            box.operator("scenario.build_film_shot", icon="SCENE_DATA").review_id = status[
                "review_id"
            ]
        elif phase == "UNCERTAIN":
            box.label(text="Build outcome needs inspection", icon="ERROR")
            if status["receipt_retry_available"]:
                box.operator(
                    "scenario.save_film_shot_receipt", icon="FILE_TICK"
                ).review_id = status["review_id"]
            else:
                box.operator("scenario.dismiss_film_shot", icon="CHECKMARK").review_id = status[
                    "review_id"
                ]
            box.operator("scenario.inspect_saved_jobs", icon="FILE_REFRESH")
        else:
            if phase == "BUILT":
                box.label(text=f"Built: {status['scene']}", icon="CHECKMARK")
            elif phase == "ERROR":
                box.label(text="Review failed; inspect saved jobs", icon="ERROR")
            box.operator("scenario.prepare_film_shot", icon="VIEWZOOM")
        if phase in {"VERIFYING", "READY", "ERROR"}:
            box.operator("scenario.discard_film_shot", icon="X").review_id = status["review_id"]
        if status and status["error"]:
            box.operator("scenario.film_shot_error", icon="ERROR").review_id = status["review_id"]


CLASSES = (
    ScenarioFilmAssetChoice,
    ScenarioFilmHeroChoice,
    SCENARIO_UL_film_assets,
    SCENARIO_OT_prepare_film_shot,
    SCENARIO_OT_build_film_shot,
    SCENARIO_OT_discard_film_shot,
    SCENARIO_OT_dismiss_film_shot,
    SCENARIO_OT_save_film_shot_receipt,
    SCENARIO_OT_film_shot_error,
    SCENARIO_UL_film_shots,
    SCENARIO_PT_film_shots,
)
