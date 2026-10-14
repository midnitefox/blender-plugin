# SPDX-FileCopyrightText: 2026 Scenario Inc.
# SPDX-License-Identifier: GPL-3.0-or-later
"""Native Film video export: a destination choice, then a separate render approval.

Drawing reads cached handle status only; the maintenance pump owns progress.
"""

import os
import textwrap
from pathlib import Path

import bpy
from bpy.props import BoolProperty, CollectionProperty, EnumProperty, IntProperty, StringProperty
from bpy_extras.io_utils import ExportHelper

from ..core.jobs import local_export
from . import film_jobs, runtime
from .film_export import default_size_supported
from .film_scene_controls import _error

# Selection, edits, Undo/Redo and reselecting a scene revoke the captured origins.
_UNCHANGED = "Selecting, editing or Undo here discards it"
_STAGES = {
    "queued": "Starting the export...",
    "media": "Checking media",
    "render": "Rendering",
    "verify": "Verifying the video...",
    "publish": "Saving the file",
}


def commands(*, create=True):
    if create:
        return runtime.ensure_film_jobs().session.film_export
    session = runtime.state.job_session
    if session is None or not session.active or not runtime.catalog_selection_matches():
        return None
    return session.film_export


def film_folder():
    """The default export folder, ``<Output Folder>/Film``; reading it creates nothing."""
    return Path(runtime.paths().output_dir) / "Film"


def suggested_path(folder, name):
    """Suggest a destination in ``folder`` that does not exist yet; never reserves it."""
    path, stem, number = Path(folder) / name, name[: -len(".mp4")], 1
    # A folder holds finitely many entries, so counting on always finds a free name.
    while os.path.lexists(path):
        number += 1
        path = Path(folder) / f"{stem} {number}.mp4"
    return path


def progress_text(status):
    progress = status["progress"]
    text = _STAGES.get(progress["stage"], "Exporting...")
    if progress["stage"] == "render" and progress["total"]:
        return f"{text} {progress['done']}/{progress['total']} frames ({status['percent']}%)"
    if progress["stage"] in {"media", "publish"} and progress["total"]:
        return f"{text} ({status['percent']}%)"
    return text


def _readable(operator, error, fallback):
    # Command preconditions raise first-party text without private paths.
    known = type(error) in {ValueError, local_export.LocalExportError}
    return _error(operator, str(error) if known and str(error) else fallback)


def _summary(layout, status):
    layout.label(
        text=f"{status['frames']} frames at {status['fps']} fps ({status['duration']:g} s); "
        f"{status['width']} x {status['height']}"
    )
    track = "AAC stereo audio" if status["audio"] else "no audio track"
    layout.label(text=f"H.264 MP4 with {track}; {status['media']} media files")


def _lines(layout, text, width=80):
    for line in textwrap.wrap(text, width) or [""]:
        layout.label(text=line)


class ScenarioFilmExportChoice(bpy.types.PropertyGroup):
    source_id: StringProperty()
    kind: StringProperty()
    frames: IntProperty()
    fps: IntProperty()
    width: IntProperty()
    height: IntProperty()
    media: IntProperty()
    audible: BoolProperty()
    blocked: StringProperty()
    default_name: StringProperty()


class SCENARIO_UL_film_export_sources(bpy.types.UIList):
    def draw_item(self, context, layout, data, item, icon, active_data, active_propname, index):
        layout.label(
            text=f"{item.name} ({item.kind.title()})", icon="ERROR" if item.blocked else "SEQUENCE"
        )


class SCENARIO_OT_prepare_film_export(bpy.types.Operator, ExportHelper):
    bl_idname = "scenario.prepare_film_export"
    bl_label = "Choose video file"
    bl_description = "Choose a built review scene and a new MP4 file; rendering needs a separate approval and spends no credits"
    filename_ext = ".mp4"
    filter_glob: StringProperty(default="*.mp4", options={"HIDDEN"})
    choices: CollectionProperty(type=ScenarioFilmExportChoice, options={"SKIP_SAVE"})
    choice_index: IntProperty(min=0, options={"SKIP_SAVE"})
    audio_track: EnumProperty(
        name="Audio",
        items=(
            ("AUTO", "Match sound strips", "Add an AAC track only when a sound strip is audible"),
            ("ON", "Include", "Always add a stereo AAC track"),
            ("OFF", "None", "Export the picture without an audio track"),
        ),
        default="AUTO",
        options={"SKIP_SAVE"},
    )
    scene_size: BoolProperty(name="Use review scene size", default=True, options={"SKIP_SAVE"})
    width: IntProperty(name="Width", default=1920, min=64, max=4096, options={"SKIP_SAVE"})
    height: IntProperty(name="Height", default=1080, min=64, max=4096, options={"SKIP_SAVE"})

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
            row.width, row.height = source["width"], source["height"]
            row.audible, row.blocked = source["audible"], source["blocked_reason"]
            row.default_name = source["default_name"]
        self.choice_index = next(
            (index for index, row in enumerate(self.choices) if not row.blocked), 0
        )
        chosen = self.choices[self.choice_index]
        # A scene size outside the bounds opens on the nearest explicit size instead.
        self.audio_track = "AUTO"
        self.scene_size = default_size_supported(chosen.width, chosen.height)
        self.width, self.height = (
            min(local_export.MAX_DIMENSION, max(local_export.MIN_DIMENSION, value))
            for value in (chosen.width, chosen.height)
        )
        try:
            # The explicit Export action opens the configured output folder, as Open output folder does.
            folder.mkdir(parents=True, exist_ok=True)
        except OSError:
            folder = folder.parent
        self.filepath = str(suggested_path(folder, chosen.default_name))
        return ExportHelper.invoke(self, context, event)

    def draw(self, context):
        layout = self.layout
        layout.label(text="Built review scene", icon="SEQUENCE")
        layout.template_list(
            "SCENARIO_UL_film_export_sources", "", self, "choices", self, "choice_index", rows=3
        )
        if not 0 <= self.choice_index < len(self.choices):
            return
        chosen = self.choices[self.choice_index]
        if chosen.blocked:
            for line in textwrap.wrap(chosen.blocked, 36):
                layout.label(text=line, icon="ERROR")
        layout.label(text=f"{chosen.frames} frames at {chosen.fps} fps")
        layout.label(text=f"Scene size {chosen.width} x {chosen.height}")
        if not default_size_supported(chosen.width, chosen.height):
            layout.label(
                text=f"Outside {local_export.MIN_DIMENSION} to {local_export.MAX_DIMENSION}"
                " pixels; set an export size",
                icon="INFO",
            )
        layout.label(text=f"{chosen.media} media files")
        layout.label(text="Sound strips audible" if chosen.audible else "No audible sound strips")
        layout.prop(self, "audio_track")
        layout.prop(self, "scene_size")
        column = layout.column(align=True)
        column.enabled = not self.scene_size
        column.prop(self, "width")
        column.prop(self, "height")
        layout.label(text="Nothing renders yet.")
        layout.label(text="Render video asks again.")

    def execute(self, context):
        if not 0 <= self.choice_index < len(self.choices):
            return _error(self, "Choose a built review scene to export")
        chosen = self.choices[self.choice_index]
        try:
            owner = commands()
            invoked = getattr(self, "_owner", None)
            if invoked is not None and (
                invoked is not owner
                or context.scene != self._scene
                or film_jobs.snapshot(context.scene) != self._binding
            ):
                raise RuntimeError("The Film export destination changed")
            owner.prepare(
                context.scene,
                source_id=chosen.source_id,
                destination=self.filepath,
                width=None if self.scene_size else self.width,
                height=None if self.scene_size else self.height,
                # Match sound strips follows the chosen source, as MCP does without include_audio.
                audio=None if self.audio_track == "AUTO" else self.audio_track == "ON",
            )
        except Exception as error:
            return _readable(self, error, "Check the review scene and file, then choose again")
        self.report({"INFO"}, "Review the export settings, then choose Render video")
        return {"FINISHED"}


class SCENARIO_OT_render_film_export(bpy.types.Operator):
    bl_idname = "scenario.render_film_export"
    bl_label = "Render video"
    bl_description = "Render the prepared review scene to the shown new MP4 file in an offline Blender process, without spending credits"
    review_id: StringProperty(options={"HIDDEN", "SKIP_SAVE"})
    approved_destination: StringProperty(options={"HIDDEN", "SKIP_SAVE"})

    @classmethod
    def poll(cls, context):
        if context.mode != "OBJECT":
            cls.poll_message_set("Switch to Object Mode to export video")
            return False
        return True

    def invoke(self, context, event):
        from .film_export import verifier

        try:
            self._owner = commands()
            self._owner.poll()
            self._status = self._owner.status(self.review_id)
            if self._status["phase"] != "READY":
                raise ValueError("The export is not ready")
            self._verifier = verifier()
        except Exception:
            return _error(self, "Prepare a current Film export first")
        # The dialog shows this exact destination; confirming approves only it.
        self.approved_destination = self._status["destination"]
        return context.window_manager.invoke_props_dialog(self, width=600)

    def draw(self, context):
        status, layout = self._status, self.layout
        layout.label(text=f"Review scene: {status['source_scene']}")
        _summary(layout, status)
        layout.label(text="New file:")
        _lines(layout, status["destination"])
        layout.label(text="Renders in an offline Blender process; never overwrites a file.")
        layout.label(text="Working file and scenes stay unchanged; no credits are spent.")
        if self._verifier == "ffprobe":
            layout.label(text="Installed ffprobe verifies every frame before saving.")
        else:
            layout.label(text="No ffprobe: Blender checks the file, a weaker test.")

    def execute(self, context):
        try:
            owner = commands()
            invoked = getattr(self, "_owner", None)
            if invoked is not None and invoked is not owner:
                raise RuntimeError("The Film connection changed")
            status = owner.approve(self.review_id, approved_destination=self.approved_destination)
        except Exception as error:
            return _readable(self, error, "The export changed; prepare it again")
        if status["phase"] == "ERROR":
            return _error(self, status["error"])
        self.report({"INFO"}, "Exporting the video in the background")
        return {"FINISHED"}


class SCENARIO_OT_cancel_film_export(bpy.types.Operator):
    bl_idname = "scenario.cancel_film_export"
    bl_label = "Cancel export"
    bl_description = "Stop this video export; the destination file is not written"
    review_id: StringProperty(options={"HIDDEN", "SKIP_SAVE"})

    def invoke(self, context, event):
        return context.window_manager.invoke_confirm(self, event)

    def execute(self, context):
        try:
            commands().cancel(self.review_id)
        except Exception:
            return _error(self, "The export has already stopped; inspect its status")
        return {"FINISHED"}


class SCENARIO_OT_discard_film_export(bpy.types.Operator):
    bl_idname = "scenario.discard_film_export"
    bl_label = "Discard export"
    bl_description = "Delete this export's private snapshot, staged video and logs; an exported file stays where it was saved"
    review_id: StringProperty(options={"HIDDEN", "SKIP_SAVE"})

    def invoke(self, context, event):
        return context.window_manager.invoke_confirm(self, event)

    def execute(self, context):
        try:
            commands().discard(self.review_id)
        except Exception as error:
            return _readable(self, error, "Wait for the export to stop before discarding")
        return {"FINISHED"}


class SCENARIO_OT_choose_film_export_copy(bpy.types.Operator, ExportHelper):
    bl_idname = "scenario.choose_film_export_copy"
    bl_label = "Choose new file"
    bl_description = "Choose another new MP4 file for the verified video after saving failed; nothing is copied yet"
    filename_ext = ".mp4"
    filter_glob: StringProperty(default="*.mp4", options={"HIDDEN"})
    review_id: StringProperty(options={"HIDDEN", "SKIP_SAVE"})

    def invoke(self, context, event):
        try:
            status = commands().status(self.review_id)
            if not status["republish_available"]:
                raise ValueError("No verified video to copy")
        except Exception:
            return _error(self, "Only a verified export whose saving failed can be copied")
        folder = Path(status["destination"]).parent
        name = Path(status["destination"]).name
        self.filepath = str(suggested_path(folder, name))
        return ExportHelper.invoke(self, context, event)

    def draw(self, context):
        self.layout.label(text="Copies the verified video only.")
        self.layout.label(text="Nothing renders again.")
        self.layout.label(text="Saving asks again.")

    def execute(self, context):
        try:
            commands().prepare_republish(self.review_id, self.filepath)
        except Exception as error:
            return _readable(self, error, "Choose a new file outside Blender storage")
        return {"FINISHED"}


class SCENARIO_OT_publish_film_export_copy(bpy.types.Operator):
    bl_idname = "scenario.publish_film_export_copy"
    bl_label = "Save video copy"
    bl_description = "Copy the verified video to the shown new file without rendering again"
    review_id: StringProperty(options={"HIDDEN", "SKIP_SAVE"})
    approved_destination: StringProperty(options={"HIDDEN", "SKIP_SAVE"})

    def invoke(self, context, event):
        try:
            self._status = commands().status(self.review_id)
            if not self._status["pending_destination"]:
                raise ValueError("No new destination")
        except Exception:
            return _error(self, "Choose a new file for the verified video first")
        self.approved_destination = self._status["pending_destination"]
        return context.window_manager.invoke_props_dialog(self, width=600)

    def draw(self, context):
        layout = self.layout
        layout.label(text="Copy the verified video to this new file:")
        _lines(layout, self._status["pending_destination"])
        layout.label(text="Nothing renders again; an existing file is never overwritten.")

    def execute(self, context):
        try:
            status = commands().republish(
                self.review_id, approved_destination=self.approved_destination
            )
        except Exception as error:
            return _readable(self, error, "The verified video changed; inspect the export")
        if status["phase"] != "PUBLISHING":
            return _error(self, status["error"] or "The copy could not start")
        return {"FINISHED"}


class SCENARIO_OT_film_export_error(bpy.types.Operator):
    bl_idname = "scenario.film_export_error"
    bl_label = "Export error details"
    bl_description = "Read or copy the complete Film export error"
    review_id: StringProperty(options={"HIDDEN", "SKIP_SAVE"})

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(
            self, width=540, title="Film export", confirm_text="Close"
        )

    def draw(self, context):
        owner = commands(create=False)
        try:
            message = owner.status(self.review_id)["error"] if owner else ""
        except ValueError:
            message = ""
        message = message or "This export is no longer available"
        _lines(self.layout, message, 65)
        self.layout.operator(
            "scenario.copy_text", text="Copy error", icon="COPYDOWN"
        ).text = message

    def execute(self, context):
        return {"FINISHED"}


class SCENARIO_PT_film_export(bpy.types.Panel):
    bl_label = "Export"
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
        box.label(text="Export video", icon="FILE_MOVIE")
        if phase == "READY":
            box.label(text=f"{status['source_scene']}: {status['kind'].title()}")
            box.label(text=f"{status['frames']} frames at {status['fps']} fps")
            box.label(text=f"To {Path(status['destination']).name}")
            box.label(text=_UNCHANGED)
            box.operator(
                "scenario.render_film_export", icon="RENDER_ANIMATION"
            ).review_id = identifier
            box.operator("scenario.discard_film_export", icon="X").review_id = identifier
        elif phase in {"EXPORTING", "PUBLISHING"}:
            box.label(text=progress_text(status), icon="TIME")
            box.operator("scenario.cancel_film_export", icon="CANCEL").review_id = identifier
        elif phase == "CANCELLING":
            box.label(text="Stopping the export...", icon="TIME")
        elif phase == "PUBLISH_FAILED":
            box.label(text="Video verified but not saved", icon="ERROR")
            if status["pending_destination"]:
                box.label(text=f"New file: {Path(status['pending_destination']).name}")
                box.operator(
                    "scenario.publish_film_export_copy", icon="FILE_TICK"
                ).review_id = identifier
            box.operator(
                "scenario.choose_film_export_copy", text="Choose new file...", icon="FILEBROWSER"
            ).review_id = identifier
            box.operator("scenario.discard_film_export", icon="X").review_id = identifier
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
                box.label(text="Export cancelled; the file was not written")
            elif phase == "ERROR":
                box.label(text="Export needs a fresh preparation", icon="ERROR")
            box.operator("scenario.prepare_film_export", text="Export video...", icon="EXPORT")
            if not status:
                box.label(text="Renders a built review scene to a new MP4.")
                box.label(text="Offline Blender process; never overwrites.")
            elif status["directory"]:
                box.operator(
                    "scenario.discard_film_export", text="Discard private files", icon="TRASH"
                ).review_id = identifier
        if status and status["error"]:
            box.operator(
                "scenario.film_export_error", text="Error details", icon="ERROR"
            ).review_id = identifier


CLASSES = (
    ScenarioFilmExportChoice,
    SCENARIO_UL_film_export_sources,
    SCENARIO_OT_prepare_film_export,
    SCENARIO_OT_render_film_export,
    SCENARIO_OT_cancel_film_export,
    SCENARIO_OT_discard_film_export,
    SCENARIO_OT_choose_film_export_copy,
    SCENARIO_OT_publish_film_export_copy,
    SCENARIO_OT_film_export_error,
    SCENARIO_PT_film_export,
)
