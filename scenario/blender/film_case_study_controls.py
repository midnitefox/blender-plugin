# SPDX-FileCopyrightText: 2026 Scenario Inc.
# SPDX-License-Identifier: GPL-3.0-or-later
"""Native Film case-study bundle: an archive choice, then a separate build approval.

Drawing reads cached handle status only; the maintenance pump owns progress.
"""

import os
import textwrap
from pathlib import Path

import bpy
from bpy.props import BoolProperty, CollectionProperty, IntProperty, StringProperty
from bpy_extras.io_utils import ExportHelper

from ..core.ui.costs import format_cu
from . import film_jobs, runtime
from .film_export_controls import _UNCHANGED, _lines, _readable, film_folder
from .film_scene_controls import _error

_STAGES = {
    "queued": "Starting the case study...",
    "media": "Copying media",
    "bundle": "Writing the Blender library...",
    "verify": "Checking the library...",
    "scan": "Checking the library for private data",
    "archive": "Packing the archive",
    "check": "Checking the archive",
    "publish": "Saving the file",
}


def commands(*, create=True):
    if create:
        return runtime.ensure_film_jobs().session.film_case_study
    session = runtime.state.job_session
    if session is None or not session.active or not runtime.catalog_selection_matches():
        return None
    return session.film_case_study


def suggested_path(folder, name):
    """Suggest a destination in ``folder`` that does not exist yet; never reserves it."""
    path, stem, suffix, number = Path(folder) / name, Path(name).stem, Path(name).suffix, 1
    # A folder holds finitely many entries, so counting on always finds a free name.
    while os.path.lexists(path):
        number += 1
        path = Path(folder) / f"{stem} {number}{suffix}"
    return path


def progress_text(status):
    progress = status["progress"]
    text = _STAGES.get(progress["stage"], "Exporting...")
    if progress["stage"] in {"media", "scan", "archive", "check", "publish"} and progress["total"]:
        return f"{text} ({status['percent']}%)"
    return text


def _size(value):
    return f"{value / 1024**2:.1f} MiB"


def _count(number, noun):
    return f"{number} {noun}" if number == 1 else f"{number} {noun}s"


def _ledger(status):
    tasks = _count(status["approved_tasks"], "approved task")
    if status["quoted_cost_total"] is None:
        return f"Ledger: {tasks}, no saved quotes"
    return (
        f"Ledger: {tasks}; {format_cu(status['quoted_cost_total'])} CU quoted"
        f" for {_count(status['priced_tasks'], 'task')}"
    )


def _contents(status):
    return (
        f"{_count(status['scenes'], 'scene')}; {_count(status['media'], 'media file')}"
        f" ({_size(status['media_bytes'])})"
    )


class ScenarioFilmCaseStudyChoice(bpy.types.PropertyGroup):
    source_id: StringProperty()
    kind: StringProperty()
    frames: IntProperty()
    fps: IntProperty()
    media: IntProperty()
    blocked: StringProperty()


class ScenarioFilmCaseStudyTimeline(bpy.types.PropertyGroup):
    timeline_id: StringProperty()
    shots: IntProperty()
    blocked: StringProperty()


class SCENARIO_UL_film_case_study_sources(bpy.types.UIList):
    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index):
        layout.label(
            text=f"{item.name} ({item.kind.title()})", icon="ERROR" if item.blocked else "SEQUENCE"
        )


class SCENARIO_UL_film_case_study_timelines(bpy.types.UIList):
    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index):
        layout.label(
            text=f"{item.name} ({_count(item.shots, 'shot')})",
            icon="ERROR" if item.blocked else "SEQUENCE",
        )


class SCENARIO_OT_prepare_film_case_study(bpy.types.Operator, ExportHelper):
    bl_idname = "scenario.prepare_film_case_study"
    bl_label = "Choose case-study file"
    bl_description = "Choose a built review scene, an optional director timeline and a new ZIP file; building needs a separate approval and spends no credits"
    filename_ext = ".zip"
    filter_glob: StringProperty(default="*.zip", options={"HIDDEN"})
    choices: CollectionProperty(type=ScenarioFilmCaseStudyChoice, options={"SKIP_SAVE"})
    choice_index: IntProperty(min=0, options={"SKIP_SAVE"})
    timelines: CollectionProperty(type=ScenarioFilmCaseStudyTimeline, options={"SKIP_SAVE"})
    timeline_index: IntProperty(min=0, options={"SKIP_SAVE"})
    include_timeline: BoolProperty(
        name="Include director timeline",
        description="Add the editable timeline and its shot scenes to the bundle",
        default=True,
        options={"SKIP_SAVE"},
    )

    def invoke(self, context, event):
        try:
            self._owner = commands()
            self._scene, self._binding = context.scene, film_jobs.snapshot(context.scene)
            result = self._owner.inspect(context.scene)
            folder = film_folder()
        except Exception as error:
            return _readable(self, error, "Load a valid Film recipe in the current scene first")
        if not result["sources"]:
            return _error(self, "Build a review scene under Review first")
        self.choices.clear()
        for source in result["sources"]:
            row = self.choices.add()
            row.name, row.source_id, row.kind = source["scene"], source["source_id"], source["kind"]
            row.frames, row.fps, row.media = source["frames"], source["fps"], source["media"]
            row.blocked = source["blocked_reason"]
        self.timelines.clear()
        for timeline in result["timelines"]:
            row = self.timelines.add()
            row.name, row.timeline_id = timeline["scene"], timeline["timeline_id"]
            row.shots, row.blocked = timeline["shots"], timeline["blocked_reason"]
        self.choice_index = next(
            (index for index, row in enumerate(self.choices) if not row.blocked), 0
        )
        self.timeline_index = next(
            (index for index, row in enumerate(self.timelines) if not row.blocked), 0
        )
        self.include_timeline = any(not row.blocked for row in self.timelines)
        try:
            # The explicit Export action opens the configured output folder, as video export does.
            folder.mkdir(parents=True, exist_ok=True)
        except OSError:
            folder = folder.parent
        self.filepath = str(suggested_path(folder, result["default_name"]))
        return ExportHelper.invoke(self, context, event)

    def draw(self, context):
        layout = self.layout
        layout.label(text="Built review scene", icon="SEQUENCE")
        layout.template_list(
            "SCENARIO_UL_film_case_study_sources", "", self, "choices", self, "choice_index", rows=3
        )
        if 0 <= self.choice_index < len(self.choices):
            chosen = self.choices[self.choice_index]
            for line in _wrap(chosen.blocked):
                layout.label(text=line, icon="ERROR")
            layout.label(text=f"{chosen.frames} frames at {chosen.fps} fps")
            layout.label(text=_count(chosen.media, "media file"))
        if self.timelines:
            layout.prop(self, "include_timeline")
            column = layout.column()
            column.enabled = self.include_timeline
            column.template_list(
                "SCENARIO_UL_film_case_study_timelines",
                "",
                self,
                "timelines",
                self,
                "timeline_index",
                rows=2,
            )
            if self.include_timeline and 0 <= self.timeline_index < len(self.timelines):
                for line in _wrap(self.timelines[self.timeline_index].blocked):
                    column.label(text=line, icon="ERROR")
        else:
            layout.label(text="No director timeline")
        layout.label(text="Nothing is written yet.")
        layout.label(text="Save case study asks again.")

    def execute(self, context):
        if not 0 <= self.choice_index < len(self.choices):
            return _error(self, "Choose a built review scene for the case study")
        chosen = self.choices[self.choice_index]
        timeline = None
        if self.include_timeline and 0 <= self.timeline_index < len(self.timelines):
            timeline = self.timelines[self.timeline_index].timeline_id
        try:
            owner = commands()
            invoked = getattr(self, "_owner", None)
            if invoked is not None and (
                invoked is not owner
                or context.scene != self._scene
                or film_jobs.snapshot(context.scene) != self._binding
            ):
                raise RuntimeError("The Film case-study destination changed")
            owner.prepare(
                context.scene,
                source_id=chosen.source_id,
                timeline_id=timeline,
                destination=self.filepath,
            )
        except Exception as error:
            return _readable(self, error, "Check the review scene and file, then choose again")
        self.report({"INFO"}, "Review the case study, then choose Save case study")
        return {"FINISHED"}


def _wrap(text, width=36):
    return textwrap.wrap(text, width) if text else []


class SCENARIO_OT_save_film_case_study(bpy.types.Operator):
    bl_idname = "scenario.save_film_case_study"
    bl_label = "Save case study"
    bl_description = "Build the prepared case-study bundle and save it to the shown new ZIP file in offline Blender processes, without spending credits"
    review_id: StringProperty(options={"HIDDEN", "SKIP_SAVE"})
    approved_destination: StringProperty(options={"HIDDEN", "SKIP_SAVE"})

    @classmethod
    def poll(cls, context):
        if context.mode != "OBJECT":
            cls.poll_message_set("Switch to Object Mode to export a case study")
            return False
        return True

    def invoke(self, context, event):
        try:
            self._owner = commands()
            self._owner.poll()
            self._status = self._owner.status(self.review_id)
            if self._status["phase"] != "READY":
                raise ValueError("The case study is not ready")
        except Exception:
            return _error(self, "Prepare a current Film case study first")
        # The dialog shows this exact destination; confirming approves only it.
        self.approved_destination = self._status["destination"]
        return context.window_manager.invoke_props_dialog(self, width=600)

    def draw(self, context):
        status, layout = self._status, self.layout
        layout.label(text=f"Review scene: {status['source_scene']} ({status['kind'].title()})")
        if status["timeline_scene"]:
            layout.label(
                text=f"Timeline: {status['timeline_scene']} ({_count(status['shots'], 'shot')})"
            )
        else:
            layout.label(text="No director timeline")
        layout.label(text=_contents(status))
        layout.label(text=_ledger(status))
        layout.label(text="New file:")
        _lines(layout, status["destination"])
        layout.label(text="Builds in offline Blender processes; never overwrites a file.")
        layout.label(text="Working file and scenes stay unchanged; no credits are spent.")
        layout.label(text="Documents omit credentials, URLs, service IDs and local paths.")

    def execute(self, context):
        try:
            owner = commands()
            invoked = getattr(self, "_owner", None)
            if invoked is not None and invoked is not owner:
                raise RuntimeError("The Film connection changed")
            status = owner.approve(self.review_id, approved_destination=self.approved_destination)
        except Exception as error:
            return _readable(self, error, "The case study changed; prepare it again")
        if status["phase"] == "ERROR":
            return _error(self, status["error"])
        self.report({"INFO"}, "Building the case study in the background")
        return {"FINISHED"}


class SCENARIO_OT_cancel_film_case_study(bpy.types.Operator):
    bl_idname = "scenario.cancel_film_case_study"
    bl_label = "Cancel case study"
    bl_description = "Stop building this case study; the destination file is not written"
    review_id: StringProperty(options={"HIDDEN", "SKIP_SAVE"})

    def invoke(self, context, event):
        return context.window_manager.invoke_confirm(self, event)

    def execute(self, context):
        try:
            commands().cancel(self.review_id)
        except Exception:
            return _error(self, "The case study has already stopped; inspect its status")
        return {"FINISHED"}


class SCENARIO_OT_discard_film_case_study(bpy.types.Operator):
    bl_idname = "scenario.discard_film_case_study"
    bl_label = "Discard case study"
    bl_description = "Delete this case study's private snapshot, staged archive and logs; a saved archive stays where it was saved"
    review_id: StringProperty(options={"HIDDEN", "SKIP_SAVE"})

    def invoke(self, context, event):
        return context.window_manager.invoke_confirm(self, event)

    def execute(self, context):
        try:
            commands().discard(self.review_id)
        except Exception as error:
            return _readable(self, error, "Wait for the case study to stop before discarding")
        return {"FINISHED"}


class SCENARIO_OT_choose_film_case_study_copy(bpy.types.Operator, ExportHelper):
    bl_idname = "scenario.choose_film_case_study_copy"
    bl_label = "Choose new file"
    bl_description = "Choose another new ZIP file for the verified case study after saving failed; nothing is copied yet"
    filename_ext = ".zip"
    filter_glob: StringProperty(default="*.zip", options={"HIDDEN"})
    review_id: StringProperty(options={"HIDDEN", "SKIP_SAVE"})

    def invoke(self, context, event):
        try:
            status = commands().status(self.review_id)
            if not status["republish_available"]:
                raise ValueError("No verified case study to copy")
        except Exception:
            return _error(self, "Only a verified case study whose saving failed can be copied")
        destination = Path(status["destination"])
        self.filepath = str(suggested_path(destination.parent, destination.name))
        return ExportHelper.invoke(self, context, event)

    def draw(self, context):
        self.layout.label(text="Copies the verified archive only.")
        self.layout.label(text="Nothing is built again.")
        self.layout.label(text="Saving asks again.")

    def execute(self, context):
        try:
            commands().prepare_republish(self.review_id, self.filepath)
        except Exception as error:
            return _readable(self, error, "Choose a new file outside Blender storage")
        return {"FINISHED"}


class SCENARIO_OT_publish_film_case_study_copy(bpy.types.Operator):
    bl_idname = "scenario.publish_film_case_study_copy"
    bl_label = "Save case-study copy"
    bl_description = (
        "Copy the verified case-study archive to the shown new file without building it again"
    )
    review_id: StringProperty(options={"HIDDEN", "SKIP_SAVE"})
    approved_destination: StringProperty(options={"HIDDEN", "SKIP_SAVE"})

    def invoke(self, context, event):
        try:
            self._status = commands().status(self.review_id)
            if not self._status["pending_destination"]:
                raise ValueError("No new destination")
        except Exception:
            return _error(self, "Choose a new file for the verified case study first")
        self.approved_destination = self._status["pending_destination"]
        return context.window_manager.invoke_props_dialog(self, width=600)

    def draw(self, context):
        layout = self.layout
        layout.label(text="Copy the verified case-study archive to this new file:")
        _lines(layout, self._status["pending_destination"])
        layout.label(text="Nothing is built again; an existing file is never overwritten.")

    def execute(self, context):
        try:
            status = commands().republish(
                self.review_id, approved_destination=self.approved_destination
            )
        except Exception as error:
            return _readable(self, error, "The verified case study changed; inspect it")
        if status["phase"] != "PUBLISHING":
            return _error(self, status["error"] or "The copy could not start")
        return {"FINISHED"}


class SCENARIO_OT_film_case_study_error(bpy.types.Operator):
    bl_idname = "scenario.film_case_study_error"
    bl_label = "Case-study error details"
    bl_description = "Read or copy the complete Film case-study error"
    review_id: StringProperty(options={"HIDDEN", "SKIP_SAVE"})

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(
            self, width=540, title="Film case study", confirm_text="Close"
        )

    def draw(self, context):
        owner = commands(create=False)
        try:
            message = owner.status(self.review_id)["error"] if owner else ""
        except ValueError:
            message = ""
        message = message or "This case study is no longer available"
        _lines(self.layout, message, 65)
        self.layout.operator(
            "scenario.copy_text", text="Copy error", icon="COPYDOWN"
        ).text = message

    def execute(self, context):
        return {"FINISHED"}


class SCENARIO_PT_film_case_study(bpy.types.Panel):
    bl_label = "Case study"
    bl_parent_id = "SCENARIO_PT_film"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Open Studio"

    @classmethod
    def poll(cls, context):
        return bool(context.scene.scenario_film.recipe_json)

    def draw(self, context):
        owner = commands(create=False)
        status = owner.current(context.scene) if owner else None
        phase = status["phase"] if status else ""
        identifier = status["review_id"] if status else ""
        box = self.layout.box()
        box.label(text="Case-study bundle", icon="PACKAGE")
        if phase == "READY":
            box.label(text=f"{status['source_scene']}: {status['kind'].title()}")
            box.label(
                text=f"Timeline: {status['timeline_scene']}"
                if status["timeline_scene"]
                else "No director timeline"
            )
            box.label(text=_contents(status))
            box.label(text=f"To {Path(status['destination']).name}")
            box.label(text=_UNCHANGED)
            box.operator("scenario.save_film_case_study", icon="FILE_TICK").review_id = identifier
            box.operator("scenario.discard_film_case_study", icon="X").review_id = identifier
        elif phase in {"EXPORTING", "PUBLISHING"}:
            box.label(text=progress_text(status), icon="TIME")
            box.operator("scenario.cancel_film_case_study", icon="CANCEL").review_id = identifier
        elif phase == "CANCELLING":
            box.label(text="Stopping the case study...", icon="TIME")
        elif phase == "PUBLISH_FAILED":
            box.label(text="Case study verified but not saved", icon="ERROR")
            if status["pending_destination"]:
                box.label(text=f"New file: {Path(status['pending_destination']).name}")
                box.operator(
                    "scenario.publish_film_case_study_copy", icon="FILE_TICK"
                ).review_id = identifier
            box.operator(
                "scenario.choose_film_case_study_copy",
                text="Choose new file...",
                icon="FILEBROWSER",
            ).review_id = identifier
            box.operator("scenario.discard_film_case_study", icon="X").review_id = identifier
        else:
            if phase == "EXPORTED":
                destination = Path(status["destination"])
                box.label(text=f"Exported {destination.name}", icon="CHECKMARK")
                row = box.row(align=True)
                row.operator(
                    "scenario.open_path", text="Open folder", icon="FILE_FOLDER"
                ).filepath = str(destination.parent)
                copy = row.operator("scenario.copy_text", text="Copy SHA-256", icon="COPYDOWN")
                copy.text, copy.what = status["sha256"], "SHA-256"
            elif phase == "CANCELLED":
                box.label(text="Case study cancelled; the file was not written")
            elif phase == "ERROR":
                box.label(text="Case study needs a fresh preparation", icon="ERROR")
            box.operator(
                "scenario.prepare_film_case_study", text="Export case study...", icon="EXPORT"
            )
            if not status:
                box.label(text="Packs a review, timeline, media, recipe and ledger")
                box.label(text="into a new ZIP; never overwrites.")
            elif status["directory"]:
                box.operator(
                    "scenario.discard_film_case_study", text="Discard private files", icon="TRASH"
                ).review_id = identifier
        if status and status["error"]:
            box.operator(
                "scenario.film_case_study_error", text="Error details", icon="ERROR"
            ).review_id = identifier


CLASSES = (
    ScenarioFilmCaseStudyChoice,
    ScenarioFilmCaseStudyTimeline,
    SCENARIO_UL_film_case_study_sources,
    SCENARIO_UL_film_case_study_timelines,
    SCENARIO_OT_prepare_film_case_study,
    SCENARIO_OT_save_film_case_study,
    SCENARIO_OT_cancel_film_case_study,
    SCENARIO_OT_discard_film_case_study,
    SCENARIO_OT_choose_film_case_study_copy,
    SCENARIO_OT_publish_film_case_study_copy,
    SCENARIO_OT_film_case_study_error,
    SCENARIO_PT_film_case_study,
)
