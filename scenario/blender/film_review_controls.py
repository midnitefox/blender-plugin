# SPDX-FileCopyrightText: 2026 Scenario Inc.
# SPDX-License-Identifier: GPL-3.0-or-later
"""Native Film review preparation and separate build approval; drawing stays read-only."""

import textwrap

import bpy
from bpy.props import BoolProperty, StringProperty

from ..core.jobs.store import StoreError
from . import film_jobs, runtime
from .film_scene_controls import _error

# Selection, edits and Undo/Redo revoke the scene's captured origin before a build.
_UNCHANGED = "Selecting, editing or Undo here discards it"


def commands(*, create=True):
    if create:
        return runtime.ensure_film_jobs().session.film_review
    session = runtime.state.job_session
    if session is None or not session.active or not runtime.catalog_selection_matches():
        return None
    return session.film_review


def _readable(operator, error, fallback):
    # Command preconditions raise ValueError with first-party wording only.
    return _error(operator, str(error) if type(error) is ValueError and str(error) else fallback)


class SCENARIO_OT_prepare_film_review(bpy.types.Operator):
    bl_idname = "scenario.prepare_film_review"
    bl_label = "Prepare review"
    bl_description = "Copy and measure saved Film media privately before a separate build approval, without downloading or spending"
    include_master: BoolProperty(
        name="Include saved master as muted alternate", default=False, options={"SKIP_SAVE"}
    )

    def invoke(self, context, event):
        try:
            self._owner = commands()
            self._scene, self._binding = context.scene, film_jobs.snapshot(context.scene)
            self._mode = context.window_manager.scenario_film_review_mode
            # Read the saved store here, never while drawing.
            self._master = self._owner.master_available(context.scene, self._mode)
            self._scene_name = context.scene.name
        except StoreError as error:
            # Store errors carry first-party wording about saved jobs, not the recipe.
            return _error(self, f"Inspect saved jobs before preparing: {error}")
        except Exception:
            return _error(self, "Load a valid Film recipe in the current scene first")
        self.include_master = False
        return context.window_manager.invoke_props_dialog(self, width=540)

    def draw(self, context):
        layout = self.layout
        layout.label(text=f"{self._scene_name}: {self._mode.title()} review")
        row = layout.row()
        row.enabled = self._master
        row.prop(self, "include_master")
        if not self._master:
            layout.label(text="No saved master video for this recipe and mode")
        layout.label(text="Copies up to 2 GiB of saved media into private storage.")
        layout.label(text="Measures the copies with installed ffprobe.")
        layout.label(text="No download, upload or generation. Building asks again.")

    def execute(self, context):
        try:
            owner = commands()
            mode = context.window_manager.scenario_film_review_mode
            invoked = getattr(self, "_owner", None)
            if invoked is not None and (
                invoked is not owner
                or context.scene != self._scene
                or film_jobs.snapshot(context.scene) != self._binding
                or mode != self._mode
            ):
                raise RuntimeError("The Film review destination changed")
            include = bool(self.include_master) and (invoked is None or self._master)
            owner.prepare(context.scene, mode=mode, include_master=include)
        except Exception as error:
            return _readable(
                self, error, "Check the recipe, saved sources and active local media work"
            )
        return {"FINISHED"}


class SCENARIO_OT_build_film_review(bpy.types.Operator):
    bl_idname = "scenario.build_film_review"
    bl_label = "Build review scene"
    bl_description = "Create one new review scene from the prepared copies and mark generated sources applied, without spending credits"
    # Approval is consumed once: like the timeline build, no REGISTER (redo panel, Info log).
    bl_options = {"UNDO"}
    review_id: StringProperty(options={"HIDDEN", "SKIP_SAVE"})

    @classmethod
    def poll(cls, context):
        if context.window is None or context.mode != "OBJECT":
            cls.poll_message_set("Switch to Object Mode in a Blender window to build a review")
            return False
        return True

    def invoke(self, context, event):
        try:
            self._owner = commands()
            self._owner.poll()
            self._status = self._owner.status(self.review_id)
            if self._status["phase"] != "READY":
                raise ValueError("The review is not ready")
        except Exception:
            return _error(self, "Prepare a current Film review first")
        return context.window_manager.invoke_props_dialog(self, width=560)

    def draw(self, context):
        status, layout = self._status, self.layout
        layout.label(text=f"Recipe scene: {status['scene']}")
        layout.label(
            text=f"{status['mode'].title()} review: {status['frames']} frames at {status['fps']} fps"
        )
        layout.label(
            text=f"{status['shots']} shots; {status['sources']} sources; "
            f"{status['audio_segments']} audio segments"
        )
        layout.label(
            text=f"{status['bytes'] / 1048576:.1f} MiB of private copies; "
            f"saved master: {'yes' if status['master'] else 'no'}"
        )
        layout.label(text="Creates a new scene; the working scene stays selected.")
        layout.label(text="Marks generated sources as applied in saved jobs.")
        layout.label(text="Undo removes the scene, not saved receipts or private copies.")

    def execute(self, context):
        try:
            owner = commands()
            invoked = getattr(self, "_owner", None)
            if invoked is not None and invoked is not owner:
                raise RuntimeError("The Film connection changed")
            status = owner.approve(self.review_id)
        except Exception as error:
            return _readable(self, error, "The review changed; prepare it again")
        if status["phase"] == "ERROR":
            return _error(self, status["error"])
        if status["phase"] == "UNCERTAIN":
            self.report({"WARNING"}, status["error"])
        else:
            self.report({"INFO"}, f"Built {status['review_scene']}")
        return {"FINISHED"}


class SCENARIO_OT_cancel_film_review(bpy.types.Operator):
    bl_idname = "scenario.cancel_film_review"
    bl_label = "Cancel preparation"
    bl_description = "Stop local review preparation and delete its unused private copies"
    review_id: StringProperty(options={"HIDDEN", "SKIP_SAVE"})

    def execute(self, context):
        try:
            commands().cancel(self.review_id)
        except Exception:
            return _error(self, "The review preparation has already stopped; inspect its status")
        return {"FINISHED"}


class SCENARIO_OT_discard_film_review(bpy.types.Operator):
    bl_idname = "scenario.discard_film_review"
    bl_label = "Discard review"
    bl_description = "Retire this unbuilt review and delete its private copies; saved jobs, source media and scenes remain"
    review_id: StringProperty(options={"HIDDEN", "SKIP_SAVE"})

    def invoke(self, context, event):
        return context.window_manager.invoke_confirm(self, event)

    def execute(self, context):
        try:
            commands().discard(self.review_id)
        except Exception:
            return _error(self, "Wait for active review work to stop before discarding")
        return {"FINISHED"}


class SCENARIO_OT_save_film_review_receipt(bpy.types.Operator):
    bl_idname = "scenario.save_film_review_receipt"
    bl_label = "Save review receipt"
    bl_description = (
        "Retry only the known saved application outcome without rebuilding or copying media"
    )
    review_id: StringProperty(options={"HIDDEN", "SKIP_SAVE"})

    def execute(self, context):
        try:
            status = commands().retry_receipt(self.review_id)
            if status["receipt_retry_available"] or status["inspection_required"]:
                return _error(self, "The saved outcome still needs inspection; do not rebuild")
        except Exception:
            return _error(self, "Could not save the review receipt; inspect saved jobs")
        return {"FINISHED"}


class SCENARIO_OT_dismiss_film_review(bpy.types.Operator):
    bl_idname = "scenario.dismiss_film_review"
    bl_label = "Acknowledge inspection"
    bl_description = "Dismiss this uncertain review after inspecting scenes and saved jobs; saved claims stay unchanged"
    review_id: StringProperty(options={"HIDDEN", "SKIP_SAVE"})
    inspected: BoolProperty(name="I inspected the scenes and saved jobs", default=False)

    def invoke(self, context, event):
        self.inspected = False
        return context.window_manager.invoke_props_dialog(self, width=540)

    def draw(self, context):
        self.layout.label(text="Dismisses only this review. Saved job state stays unchanged.")
        self.layout.label(text="Private copies and any partial scene remain for inspection.")
        self.layout.prop(self, "inspected")

    def execute(self, context):
        try:
            commands().dismiss_uncertain(self.review_id, inspected=self.inspected)
        except Exception:
            return _error(self, "Confirm inspection and save any pending review receipt first")
        return {"FINISHED"}


class SCENARIO_OT_film_review_error(bpy.types.Operator):
    bl_idname = "scenario.film_review_error"
    bl_label = "Review error details"
    bl_description = "Read or copy the complete Film review error"
    review_id: StringProperty(options={"HIDDEN", "SKIP_SAVE"})

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(
            self, width=540, title="Film review", confirm_text="Close"
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


class SCENARIO_PT_film_review(bpy.types.Panel):
    bl_label = "Review"
    bl_parent_id = "SCENARIO_PT_film"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Open Studio"

    @classmethod
    def poll(cls, context):
        return bool(context.scene.scenario_film.recipe_json)

    def draw(self, context):
        from .panels import equal_segments

        layout, props = self.layout, context.window_manager
        equal_segments(
            layout.row(align=True), props, "scenario_film_review_mode", ("final", "previs")
        )
        owner = commands(create=False)
        status = owner.current(context.scene, props.scenario_film_review_mode) if owner else None
        phase = status["phase"] if status else ""
        identifier = status["review_id"] if status else ""
        box = layout.box()
        box.label(text="Assemble review", icon="SEQUENCE")
        if phase in {"PREPARING", "CANCELLING", "WAITING", "BUILDING"}:
            box.label(
                text={
                    "PREPARING": "Copying and measuring saved media...",
                    "CANCELLING": "Stopping preparation...",
                    "WAITING": "Scene left; reselecting it needs a new preparation",
                    "BUILDING": "Building the review scene...",
                }[phase]
            )
            if phase == "PREPARING":
                box.label(text=_UNCHANGED)
        elif phase == "READY":
            box.label(text=f"{status['frames']} frames at {status['fps']} fps")
            box.label(text=f"{status['shots']} shots; {status['sources']} sources")
            box.label(text=_UNCHANGED)
            box.operator("scenario.build_film_review", icon="SCENE_DATA").review_id = identifier
        elif phase == "UNCERTAIN":
            box.label(text="Review build needs inspection", icon="ERROR")
            if status["receipt_retry_available"]:
                box.operator(
                    "scenario.save_film_review_receipt", icon="FILE_TICK"
                ).review_id = identifier
            else:
                box.operator(
                    "scenario.dismiss_film_review", icon="CHECKMARK"
                ).review_id = identifier
            box.operator("scenario.inspect_saved_jobs", icon="FILE_REFRESH")
        else:
            if phase == "BUILT":
                built = status["review_scene"] or "scene no longer available"
                box.label(text=f"Built: {built}", icon="CHECKMARK")
            elif phase == "CANCELLED":
                box.label(text="Preparation cancelled")
            elif phase == "ERROR":
                box.label(text="Review needs a fresh preparation", icon="ERROR")
            box.operator("scenario.prepare_film_review", icon="VIEWZOOM")
            if not status:
                box.label(text="Uses downloaded results and retained upload files.")
                box.label(text="Requires installed ffprobe and matching frame rates.")
        if phase in {"PREPARING", "WAITING"}:
            box.operator("scenario.cancel_film_review", icon="CANCEL").review_id = identifier
        elif phase in {"READY", "ERROR", "CANCELLED"}:
            box.operator("scenario.discard_film_review", icon="X").review_id = identifier
        if status and status["error"]:
            box.operator(
                "scenario.film_review_error", text="Error details", icon="ERROR"
            ).review_id = identifier


CLASSES = (
    SCENARIO_OT_prepare_film_review,
    SCENARIO_OT_build_film_review,
    SCENARIO_OT_cancel_film_review,
    SCENARIO_OT_discard_film_review,
    SCENARIO_OT_save_film_review_receipt,
    SCENARIO_OT_dismiss_film_review,
    SCENARIO_OT_film_review_error,
    SCENARIO_PT_film_review,
)
