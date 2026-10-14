# SPDX-FileCopyrightText: 2026 Scenario Inc.
# SPDX-FileCopyrightText: 2026 Open Studio contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Session-owned film exports and portable packages, without a service runtime."""

import json
import os
import tempfile
import threading
import uuid
from concurrent.futures import CancelledError, ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path

import bpy
from bpy.app.handlers import persistent

from ..core.jobs import assistant_project_bundle, local_case_study, local_export
from ..core.jobs.local_render import RenderCancelled, blender_path, digest
from ..core.jobs.transfers import TransferError, _root, validate_result_name
from . import local_capture
from .assistant_capture import _outbox
from .film_case_study_worker import strip_file

_operations = {}
_executor = None
_epoch = 0
_enabled = False
_TERMINAL = {"DONE", "ERROR", "CANCELLED"}
_PRIORITY = {"movie": 0, "image": 1, "sound": 2, "font": 3}


@dataclass(eq=False)
class _Operation:
    identifier: str
    epoch: int
    kind: str
    destination: Path
    spec: object = field(repr=False)
    sources: list
    cancel: object = field(default_factory=threading.Event, repr=False)
    lock: object = field(default_factory=threading.RLock, repr=False)
    future: object = field(default=None, repr=False)
    phase: str = "QUEUED"
    progress: tuple = ("queued", 0, 1)
    result: dict | None = None
    error: str = ""


def _main():
    if threading.current_thread() is not threading.main_thread():
        raise RuntimeError("Inspect and snapshot film operations on Blender's main thread")
    if bpy.context.mode != "OBJECT":
        raise ValueError("Export films in Object Mode")


def _scene(name, identity=None):
    if not isinstance(name, str) or not name or len(name) > 1024:
        raise ValueError("Provide an exact scene name")
    scene = bpy.data.scenes.get(name)
    if scene is None or scene.library or scene.override_library:
        raise ValueError("Choose a live local scene")
    if identity is not None and (
        not isinstance(identity, str)
        or not identity
        or scene.get("open_studio_film_id") != identity
    ):
        raise ValueError("The named scene identity changed; inspect it again")
    if (
        identity is not None
        and sum(item.get("open_studio_film_id") == identity for item in bpy.data.scenes) != 1
    ):
        raise ValueError("The scene identity is duplicated; inspect the scene again")
    return scene


def _scenes(scenes):
    """Validate scene-strip camera semantics and reject recursive scene graphs."""
    visited = set()

    def visit(scene, parents):
        if scene in parents or len(parents) >= 16:
            raise ValueError("Scene strips cannot recursively reference their own timeline")
        if scene in visited:
            return
        if scene.library or scene.override_library:
            raise ValueError("Make scene-strip sources local before export")
        visited.add(scene)
        if len(visited) > 256:
            raise ValueError("Use at most 256 scenes")
        for strip in tuple(scene.sequence_editor.strips_all) if scene.sequence_editor else ():
            if strip.type in {"MASK", "MOVIECLIP"}:
                raise ValueError("Movie clip and mask strips are unsupported")
            if strip.type == "SCENE":
                if strip.scene is None or (
                    strip.scene_input == "CAMERA"
                    and (strip.scene_camera or strip.scene.camera) is None
                ):
                    raise ValueError("Scene strips need a source and its selected camera")
                visit(strip.scene, {*parents, scene})

    for scene in scenes:
        visit(scene, set())
    return visited


def _collect(scenes, *, portable):
    """Read the library dependency closure and stamp supported external resources."""
    _scenes(scenes)
    reachable = set(scenes)
    users = bpy.data.user_map()
    changed = True
    while changed:
        changed = False
        for item, owners in users.items():
            if item not in reachable and not owners.isdisjoint(reachable):
                reachable.add(item)
                changed = True
        if len(reachable) > 100_000:
            raise ValueError("The project has too many dependent data blocks")
    found, names = {}, set()

    def add(raw, kind):
        path = Path(os.path.normpath(bpy.path.abspath(raw)))
        if not path.is_absolute():
            raise ValueError("Save or resolve relative media paths before export")
        found.setdefault(path, set()).add(kind)

    for item in reachable:
        if item.library or item.override_library:
            raise ValueError("Make linked and overridden data local before export")
        if isinstance(item, (bpy.types.MovieClip, bpy.types.CacheFile, bpy.types.Volume)):
            raise ValueError("Movie clips, cache files and volumes are unsupported")
        if isinstance(item, bpy.types.Image):
            if item.source in {"GENERATED", "VIEWER"} or item.type not in {"IMAGE", "MULTILAYER"}:
                continue
            if item.packed_file:
                if len(item.packed_files) != 1:
                    raise ValueError("Use single-file packed images")
            elif item.source == "FILE":
                add(item.filepath_raw, "image")
            else:
                raise ValueError("Image sequences and tiled textures are unsupported")
        elif isinstance(item, bpy.types.Sound):
            if not item.packed_file:
                add(item.filepath, "sound")
        elif isinstance(item, bpy.types.VectorFont):
            if not item.packed_file and item.filepath != "<builtin>":
                add(item.filepath, "font")
        elif isinstance(item, bpy.types.Scene):
            names.add(item.name)
            for strip in tuple(item.sequence_editor.strips_all) if item.sequence_editor else ():
                if strip.type in {"MASK", "MOVIECLIP"}:
                    raise ValueError("Movie clip and mask strips are unsupported")
                if strip.type == "MOVIE":
                    add(strip.filepath, "movie")
                elif strip.type == "IMAGE":
                    for element in strip.elements:
                        add(strip_file(strip.directory, element.filename), "image")
                elif strip.type == "SOUND" and portable:
                    if strip.sound is None or strip.sound.packed_file:
                        raise ValueError("Portable sound strips need unpacked audio")
                    if len(strip.modifiers) or len(getattr(strip, "retiming_keys", ())):
                        raise ValueError(
                            "Sound strip modifiers and retiming are unsupported in packages"
                        )
    if len(names) > 256 or len(found) > local_export.MAX_MEDIA:
        raise ValueError("Use at most 256 scenes and 2,000 media files")
    entries = sorted(
        ((min(kinds, key=_PRIORITY.__getitem__), path) for path, kinds in found.items()),
        key=lambda pair: (_PRIORITY[pair[0]], str(pair[1])),
    )
    media = tuple(
        local_case_study.BundleMedia(
            local_export.media_stamp(path), local_case_study.media_name(index, kind, path), kind
        )
        for index, (kind, path) in enumerate(entries, 1)
    )
    if sum(item.stamp.size for item in media) > local_export.MAX_MEDIA_BYTES:
        raise ValueError("Use at most 16 GiB of external media")
    return tuple(sorted(names)), media


def _destination(filename, suffix):
    try:
        validate_result_name(filename)
    except TransferError as error:
        raise ValueError(str(error)) from None
    if not filename.lower().endswith(suffix):
        raise ValueError("Use an output basename ending in " + suffix)
    destination = local_export.validate_destination(_outbox() / filename, suffix=suffix)
    if any(
        item.destination == destination and item.phase not in _TERMINAL
        for item in _operations.values()
    ):
        raise ValueError("An operation already owns this output filename")
    return destination


def _audible(scene):
    """Follow audible sequencer-input scene strips, honoring nested mute state."""
    editor = scene.sequence_editor
    if editor is None:
        return False
    for strip in editor.strips_all:
        if local_capture._muted(editor, strip):
            continue
        if strip.type == "SOUND" and strip.sound is not None:
            return True
        if strip.type == "SCENE" and strip.scene_input == "SEQUENCER" and _audible(strip.scene):
            return True
    return False


def _cleanup(item):
    # The path was allocated by tempfile beneath our outbox before admission.
    # Worker callbacks may call this; no bpy or path discovery happens here.
    with item.lock:
        directory = item.spec.directory
        if directory.exists():
            local_export.discard_staging(directory)


def _run(item):
    def progress(phase, done, total):
        with item.lock:
            item.progress = (phase, done, total)

    with item.lock:
        if not item.cancel.is_set():
            item.phase = "RUNNING"
    if item.cancel.is_set():
        raise RenderCancelled("Operation cancelled")
    staged = (
        local_export.render(item.spec, cancel=item.cancel, progress=progress)
        if item.kind == "film"
        else assistant_project_bundle.build(item.spec, cancel=item.cancel, progress=progress)
    )
    # Copy/hash publication remains on the worker, but only into owned staging.
    # The main-thread pump alone commits the prepared file to the public outbox.
    ready = item.spec.directory / ("ready.mp4" if item.kind == "film" else "ready.zip")
    published = local_export.publish(staged, ready, cancel=item.cancel, progress=progress)
    progress("verify-publication", 0, 1)
    stamp = local_export.media_stamp(ready)
    if digest(ready, maximum=staged.size, cancel=item.cancel) != staged.sha256:
        raise ValueError("Prepared output changed after verification")
    if not local_export._same(stamp, ready.lstat()) or ready.is_symlink():
        raise ValueError("Prepared output identity changed after verification")
    progress("verify-publication", 1, 1)
    return ready, staged, published, stamp


def _retired(item, _future):
    if item.cancel.is_set():
        try:
            _cleanup(item)
        except (OSError, local_export.LocalExportError):
            pass


def _admit(kind, destination, spec, sources):
    global _executor
    if not _enabled:
        raise ValueError("The local film operation owner is disabled")
    if sum(item.phase not in _TERMINAL for item in _operations.values()) >= 4:
        raise ValueError("Finish or cancel an existing film operation first")
    while len(_operations) >= 32:
        completed = next(
            (key for key, item in _operations.items() if item.phase in _TERMINAL), None
        )
        if completed is None:
            raise ValueError("Too many local operations")
        del _operations[completed]
    item = _Operation(uuid.uuid4().hex, _epoch, kind, destination, spec, sources)
    if _executor is None:
        _executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="open-studio-export")
    _operations[item.identifier] = item
    try:
        item.future = _executor.submit(_run, item)
    except BaseException:
        del _operations[item.identifier]
        raise
    item.future.add_done_callback(lambda future: _retired(item, future))
    return _description(item)


def _description(item):
    with item.lock:
        return {
            "operation_id": item.identifier,
            "kind": item.kind,
            "phase": item.phase,
            "progress": {
                "stage": item.progress[0],
                "done": item.progress[1],
                "total": item.progress[2],
            },
            "sources": item.sources,
            "result": item.result,
            "error": item.error,
        }


def _finish(item):
    if item.phase in _TERMINAL or not item.future.done():
        return
    try:
        ready, staged, published, stamp = item.future.result()
        if not _enabled or item.epoch != _epoch or item.cancel.is_set():
            raise RenderCancelled("The file context changed or the operation was cancelled")
        info = ready.lstat()
        if ready.is_symlink() or not local_export._same(stamp, info):
            raise ValueError("Prepared output changed before publication")
        # Atomic no-overwrite commit on the main thread. Staging and destination
        # share an outbox filesystem. Undo/load/disable cannot interleave here.
        result = {
            "path": str(item.destination),
            "sha256": staged.sha256,
            "size": staged.size,
            "sources": item.sources,
        }
        if item.kind == "film":
            result.update(
                frames=published.frames,
                fps=published.fps,
                width=published.width,
                height=published.height,
                audio=published.audio,
                verification=published.verification,
            )
        else:
            result.update(
                scenes=list(published.scenes),
                blend_sha256=published.blend_sha256,
                verification="rewrite, reopen, relative paths and complete archive readback",
            )
        os.link(ready, item.destination)
        item.result, item.phase = result, "DONE"
    except (RenderCancelled, CancelledError):
        item.phase, item.error = "CANCELLED", "Operation cancelled; no output was published"
    except Exception as error:
        item.phase = "ERROR"
        detail = (
            str(error)
            if isinstance(error, (ValueError, local_export.LocalExportError))
            else "Local film operation failed"
        )
        # Retained offline primitives keep diagnostics for their legacy callers;
        # this session owner removes private staging on every terminal outcome.
        detail = detail.split("; inspect", 1)[0]
        item.error = detail.rstrip(". ") + "; no output was published"
    finally:
        try:
            _cleanup(item)
        except (OSError, local_export.LocalExportError):
            item.error += " Private staging cleanup needs attention."


def _pump():
    for item in tuple(_operations.values()):
        _finish(item)
    return 0.2 if _enabled else None


def get_operation(identifier):
    _main()
    item = _operations.get(identifier)
    if item is None:
        raise ValueError("This session operation is unavailable")
    _finish(item)
    return _description(item)


def cancel_operation(identifier):
    _main()
    item = _operations.get(identifier)
    if item is None:
        raise ValueError("This session operation is unavailable")
    if item.phase not in _TERMINAL:
        item.cancel.set()
        item.phase = "CANCELLING"
        _finish(item)
    return _description(item)


def export_film(args):
    _main()
    scene = _scene(args.get("scene_name"), args.get("scene_id"))
    destination = _destination(args.get("filename"), ".mp4")
    if scene.sequence_editor is None or not tuple(scene.sequence_editor.strips_all):
        raise ValueError("Choose a director timeline or media review with sequencer strips")
    if ("start_frame" in args) != ("end_frame" in args):
        raise ValueError("Provide both start_frame and end_frame")
    names, media = _collect((scene,), portable=False)
    render = scene.render
    if render.fps_base != 1 or not bpy.app.ffmpeg.supported:
        raise ValueError("Use integer scene FPS and a Blender build with FFmpeg")
    parameters = dict(
        directory=_root(_outbox()),
        binary=Path(bpy.app.binary_path).resolve(),
        worker=Path(__file__).with_name("assistant_film_export_worker.py").resolve(),
        scene_name=scene.name,
        snapshot_sha256="0" * 64,
        frame_start=args.get("start_frame", scene.frame_start),
        frame_end=args.get("end_frame", scene.frame_end),
        fps=render.fps,
        width=args.get(
            "width",
            local_export.default_dimension(render.resolution_x, render.resolution_percentage),
        ),
        height=args.get(
            "height",
            local_export.default_dimension(render.resolution_y, render.resolution_percentage),
        ),
        audio=args.get("audio", _audible(scene)),
        media=tuple(item.stamp for item in media),
    )
    local_export.ExportSpec(**parameters)
    for name in names:
        for layer in bpy.data.scenes[name].view_layers:
            layer.update()
    directory, sha256 = local_capture._write_snapshot(
        scene, parameters["directory"], "film-export-agent-"
    )
    try:
        parameters.update(directory=directory, snapshot_sha256=sha256)
        spec = local_export.ExportSpec(**parameters)
        local_export.check_space(spec, destination)
        return _admit(
            "film",
            destination,
            spec,
            [{"scene_name": scene.name, "scene_id": scene.get("open_studio_film_id")}],
        )
    except BaseException:
        local_export.discard_staging(directory)
        raise


def package_project(args):
    _main()
    choices = args.get("scenes")
    if not isinstance(choices, list) or not 1 <= len(choices) <= 256:
        raise ValueError("List between one and 256 explicit scenes")
    if any(
        not isinstance(choice, dict) or set(choice) - {"scene_name", "scene_id"}
        for choice in choices
    ):
        raise ValueError("Each scene needs its exact name and optional identity")
    scenes = tuple(_scene(choice.get("scene_name"), choice.get("scene_id")) for choice in choices)
    if len(set(scenes)) != len(scenes):
        raise ValueError("Choose each scene exactly once")
    destination = _destination(args.get("filename"), ".zip")
    names, media = _collect(scenes, portable=True)
    root = _root(_outbox())
    directory = Path(tempfile.mkdtemp(prefix="film-export-project-", dir=root))
    try:
        bundle = directory / "bundle"
        bundle.mkdir(mode=0o700)
        snapshot = bundle / "snapshot.blend"
        recipes = {}
        for name in names:
            scene = bpy.data.scenes[name]
            raw = scene.get("open_studio_film_recipe", "{}")
            if not isinstance(raw, str) or len(raw.encode()) > 2_000_000:
                raise ValueError("Scene recipe is too large or invalid")
            recipes[name] = {
                "scene_id": scene.get("open_studio_film_id"),
                "recipe": json.loads(raw),
            }
            for layer in scene.view_layers:
                layer.update()
        previous = bpy.data.filepath
        bpy.data.libraries.write(
            blender_path(snapshot), set(scenes), path_remap="ABSOLUTE", compress=True
        )
        if bpy.data.filepath != previous:
            raise RuntimeError("Snapshot changed the working file")
        spec = assistant_project_bundle.ProjectSpec(
            directory,
            Path(bpy.app.binary_path).resolve(),
            Path(__file__).with_name("assistant_project_worker.py").resolve(),
            digest(snapshot),
            names,
            media,
            json.dumps({"scenes": recipes}, ensure_ascii=False),
        )
        local_case_study.check_space(spec, destination)
        return _admit("project", destination, spec, list(choices))
    except BaseException:
        local_export.discard_staging(directory)
        raise


@persistent
def _clear(*_):
    global _epoch
    _epoch += 1
    for item in tuple(_operations.values()):
        if item.phase not in _TERMINAL:
            item.cancel.set()
            item.phase = "CANCELLING"
            if item.future.done():
                _finish(item)


def register():
    global _enabled
    _enabled = True
    for handlers in (
        bpy.app.handlers.load_pre,
        bpy.app.handlers.undo_pre,
        bpy.app.handlers.redo_pre,
    ):
        if _clear not in handlers:
            handlers.append(_clear)
    if not bpy.app.timers.is_registered(_pump):
        bpy.app.timers.register(_pump, first_interval=0.2, persistent=True)


def unregister():
    global _enabled, _executor
    _enabled = False
    _clear()
    if bpy.app.timers.is_registered(_pump):
        bpy.app.timers.unregister(_pump)
    for handlers in (
        bpy.app.handlers.load_pre,
        bpy.app.handlers.undo_pre,
        bpy.app.handlers.redo_pre,
    ):
        if _clear in handlers:
            handlers.remove(_clear)
    if _executor is not None:
        _executor.shutdown(wait=False, cancel_futures=True)
        _executor = None
