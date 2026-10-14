# SPDX-FileCopyrightText: 2026 Open Studio contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Transactional assistant review scenes with independent, verified local media."""

import os
import stat
import threading
import uuid
from pathlib import Path

import bpy

from ..core.agent_artifacts import (
    IMAGE_SUFFIXES,
    MAX_IMAGE_BYTES,
    read_inbox_file,
    read_inbox_image,
)
from ..core.scene import assistant_review_plan as core
from . import assistant_media as media
from . import file_state, model_application, preview_decode, runtime


def _sources(plan, inbox):
    snapshots = {}
    total = 0
    for item in plan["pictures"] + plan["audio"]:
        key = item["filename"], item["sha256"]
        kind = item.get("kind", "AUDIO")
        suffixes = (
            IMAGE_SUFFIXES
            if kind == "IMAGE"
            else (media.VIDEO_SUFFIXES if kind == "VIDEO" else media.AUDIO_SUFFIXES)
        )
        if key not in snapshots:
            limit = min(
                MAX_IMAGE_BYTES if kind == "IMAGE" else media.MAX_MEDIA_BYTES,
                core.MAX_TOTAL_BYTES - total,
            )
            if limit <= 0:
                raise ValueError("Keep review source files within 512 MiB total")
            if kind == "IMAGE":
                snapshot = read_inbox_image(inbox, *key, max_bytes=limit)
            else:
                snapshot = read_inbox_file(inbox, *key, suffixes=suffixes, max_bytes=limit)
                if not media._container_matches(snapshot.data, snapshot.suffix):
                    raise ValueError("Media signature does not match its file extension")
            snapshots[key] = snapshot
            total += len(snapshot.data)
        if snapshots[key].suffix not in suffixes:
            raise ValueError("Source kind does not match its file extension")
    return snapshots


def _copies(snapshots, directory):
    result = {}
    for key, snapshot in snapshots.items():
        path = directory / (str(len(result)) + snapshot.suffix)
        if not path.exists():
            with path.open("xb") as output:
                output.write(snapshot.data)
                output.flush()
                os.fsync(output.fileno())
        verified = read_inbox_file(
            directory,
            path.name,
            snapshot.sha256,
            suffixes={snapshot.suffix},
            max_bytes=media.MAX_MEDIA_BYTES,
        )
        if verified.data != snapshot.data:
            raise ValueError("Retained review media changed during copying")
        result[key] = path
    return result


def _image(editor, path, item):
    try:
        preview_decode.decode(
            path,
            edge=1,
            limit=lambda w, h: 0 < w <= 16384 and 0 < h <= 16384 and w * h <= 100_000_000,
        )
    except preview_decode.PreviewDecodeError:
        raise ValueError(
            "Use a decodable image within 100 megapixels and 16384 pixels per edge"
        ) from None
    strip = editor.strips.new_image(
        path.stem, str(path), item["channel"], item["start_frame"], fit_method="FIT"
    )
    strip.frame_final_duration = item["frame_count"]
    strip.select = False
    return strip


def _stream(editor, path, kind, channel, frame, fps):
    return media._insert(
        editor,
        path,
        kind=kind,
        channel=channel,
        frame=frame,
        name=path.stem + (" audio" if kind == "AUDIO" else " picture"),
        fps=fps,
    )


def _retimed_sources(plan, paths):
    """Measure each movie alone, so no zero-key strip can confuse native retiming."""
    temporary = bpy.data.scenes.new("Open Studio review source validation")
    retimed = set()
    try:
        temporary.render.fps, temporary.render.fps_base = plan["fps"], 1.0
        editor = temporary.sequence_editor_create()
        measured = set()
        for item in plan["pictures"]:
            key = item["filename"], item["sha256"]
            if item["kind"] != "VIDEO" or key in measured:
                continue
            movie = _stream(editor, paths[key], "VIDEO", 1, 1, plan["fps"])
            if len(movie.retiming_keys):
                retimed.add(key)
            measured.add(key)
            editor.strips.remove(movie)
        return retimed
    finally:
        bpy.data.scenes.remove(temporary)


def _place(strip, segment, channel):
    start, frames, trim = (segment[key] for key in ("start_frame", "frame_count", "trim_frames"))
    # Retiming keys already preserve source seconds. Sequencer offsets are
    # destination frames, so movie and embedded sound share the same source time.
    strip.frame_start = start - trim
    strip.frame_offset_start = trim
    strip.frame_final_duration = frames
    strip.channel = channel
    if (strip.frame_final_start, strip.frame_final_end, strip.channel) != (
        start,
        start + frames,
        channel,
    ):
        raise ValueError("Blender did not preserve the requested editorial placement")


def _add_item(editor, path, item, fps, count, embedded):
    kind = item.get("kind", "AUDIO")
    if kind == "IMAGE":
        if count + 1 > core.MAX_STRIPS:
            raise ValueError("Keep the review within 2048 strips")
        return [_image(editor, path, item)]
    first = _stream(editor, path, kind, item["channel"], item["start_frame"], fps)
    # _insert already normalizes the full duration to destination frames.
    # frame_duration can change after retiming, so it must not be scaled twice.
    source_frames = first.frame_final_duration
    placements = core.segments(item, source_frames, fps)
    streams = 2 if item.get("include_audio") else 1
    if count + len(placements) * streams > core.MAX_STRIPS:
        raise ValueError("Keep the review within 2048 strips")
    result = []
    for index, segment in enumerate(placements):
        strip = (
            first
            if index == 0
            else _stream(editor, path, kind, item["channel"], segment["start_frame"], fps)
        )
        _place(strip, segment, item["channel"])
        if kind == "AUDIO":
            strip.volume = item["volume"]
        result.append(strip)
        if item.get("include_audio"):
            embedded.append((path, item, segment))
    return result


def build(args, inbox):
    if threading.current_thread() is not threading.main_thread():
        raise RuntimeError("Build the review on Blender's main thread")
    if bpy.context.mode != "OBJECT":
        raise ValueError("Build the review in Object Mode")
    plan = core.validate_plan(args)
    snapshots = _sources(plan, inbox)
    identity = uuid.uuid4().hex
    root = runtime.paths().state_dir / "assistant-film-reviews"
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    info = root.lstat()
    if (
        root != root.resolve()
        or not stat.S_ISDIR(info.st_mode)
        or getattr(info, "st_file_attributes", 0)
        & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    ):
        raise ValueError("Use a private review media directory")
    directory = root / identity
    directory.mkdir(mode=0o700)
    original_sounds = set(bpy.data.sounds)
    scene = None
    try:
        paths = _copies(snapshots, directory)
        retimed = _retimed_sources(plan, paths)
        undo = model_application._undo_enabled()
        if undo and not file_state._push("Before Open Studio assistant review"):
            raise ValueError("Blender could not prepare review undo history")
        scene = bpy.data.scenes.new(plan["name"])
        scene.use_fake_user = True
        scene.render.fps, scene.render.fps_base = plan["fps"], 1.0
        scene.render.resolution_x, scene.render.resolution_y = plan["width"], plan["height"]
        scene.render.resolution_percentage = 100
        scene.render.use_sequencer = True
        scene.view_settings.view_transform, scene.view_settings.look = "Standard", "None"
        scene.view_settings.exposure, scene.view_settings.gamma = 0, 1
        scene.frame_start = min(item["start_frame"] for item in plan["pictures"] + plan["audio"])
        scene.frame_end = max(
            item["start_frame"] + item["frame_count"] - 1
            for item in plan["pictures"] + plan["audio"]
        )
        scene.frame_set(scene.frame_start)
        editor = scene.sequence_editor_create()
        strips, embedded = [], []
        # RNA retiming key-owner lookup is unsafe around zero-key strips on
        # Blender 5.2. Build every retimed movie before unchanged-rate movies,
        # images and sounds, independent of the submitted editorial order.
        items = (
            sorted(
                plan["pictures"],
                key=lambda item: 0 if (item["filename"], item["sha256"]) in retimed else 1,
            )
            + plan["audio"]
        )
        for item in items:
            path = paths[item["filename"], item["sha256"]]
            new = _add_item(editor, path, item, plan["fps"], len(strips) + len(embedded), embedded)
            for index, strip in enumerate(new, 1):
                strip.name = Path(item["filename"]).stem[:48] + f" / {strip.type.lower()} {index}"
                strip["open_studio_media_sha256"] = item["sha256"]
            strips.extend(new)
        for path, item, segment in embedded:
            sound = _stream(
                editor, path, "AUDIO", item["audio_channel"], segment["start_frame"], plan["fps"]
            )
            if sound.frame_duration < segment["trim_frames"] + segment["frame_count"]:
                raise ValueError("Embedded audio does not cover the requested picture segment")
            _place(sound, segment, item["audio_channel"])
            sound.volume = item["volume"]
            sound.name = Path(item["filename"]).stem[:48] + " / embedded audio"
            sound["open_studio_media_sha256"] = item["sha256"]
            strips.append(sound)
        scene["open_studio_film_kind"] = "review"
        scene["open_studio_film_id"] = identity
        scene["open_studio_film_recipe"] = core.canonical(plan)
        for layer in scene.view_layers:
            layer.update()
        result = dict(
            scene_name=scene.name,
            scene_id=identity,
            kind="review",
            fps=plan["fps"],
            width=plan["width"],
            height=plan["height"],
            start_frame=scene.frame_start,
            end_frame=scene.frame_end,
            frame_count=scene.frame_end - scene.frame_start + 1,
            strips=[media._description(strip) for strip in strips],
            retained_media=[
                dict(filename=key[0], sha256=key[1], path=str(path)) for key, path in paths.items()
            ],
            packed=False,
            portability="Keep the retained media with this blend file. To move the project, copy these media files and relink the sequencer paths; sequencer media are not packed.",
        )
    except Exception:
        if scene is not None:
            bpy.data.scenes.remove(scene)
        for sound in set(bpy.data.sounds) - original_sounds:
            if sound.users:
                file_state.mark_modified("Open Studio partial review")
                raise RuntimeError("Review rollback needs inspection") from None
            bpy.data.sounds.remove(sound)
        for path in directory.iterdir():
            path.unlink()
        directory.rmdir()
        raise
    marked = file_state.mark_modified("Open Studio assistant review")
    result["undo_available"] = bool(undo and marked)
    return result
