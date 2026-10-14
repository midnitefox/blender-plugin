# SPDX-FileCopyrightText: 2026 Open Studio contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Verified assistant media files and non-overwriting sequencer insertion."""

import hashlib
import json
import math
import os
import stat
import threading
from pathlib import Path

import bpy

from ..core.agent_artifacts import read_inbox_file
from . import file_state, runtime

MAX_MEDIA_BYTES = 512 * 1024 * 1024
AUDIO_SUFFIXES = frozenset({".wav", ".mp3", ".ogg"})
VIDEO_SUFFIXES = frozenset({".mp4", ".webm"})
_REQUEST = "open_studio_media_request"
_DIGEST = "open_studio_media_sha256"


def _container_matches(data, suffix):
    if suffix == ".mp4":
        return len(data) >= 12 and data[4:8] == b"ftyp"
    if suffix == ".webm":
        return data.startswith(b"\x1a\x45\xdf\xa3")
    if suffix == ".wav":
        return data[:4] == b"RIFF" and data[8:12] == b"WAVE"
    if suffix == ".ogg":
        return data.startswith(b"OggS")
    return data.startswith(b"ID3") or (
        len(data) >= 2 and data[0] == 0xFF and data[1] & 0xE6 == 0xE2
    )


def _asset(snapshot):
    directory = runtime.paths().state_dir / "assistant-media"
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    info = directory.lstat()
    if not stat.S_ISDIR(info.st_mode) or getattr(info, "st_file_attributes", 0) & getattr(
        stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400
    ):
        raise ValueError("Assistant media storage must be a private directory")
    path = directory / (snapshot.sha256 + snapshot.suffix)
    try:
        with path.open("xb") as output:
            output.write(snapshot.data)
            output.flush()
            os.fsync(output.fileno())
    except FileExistsError:
        pass
    verified = read_inbox_file(
        directory,
        path.name,
        snapshot.sha256,
        suffixes={snapshot.suffix},
        max_bytes=MAX_MEDIA_BYTES,
    )
    if verified.data != snapshot.data:
        raise ValueError("The saved media no longer matches its verified bytes")
    return path


def _insert(editor, path, *, kind, frame, channel, name, fps):
    options = dict(name=name, filepath=str(path), channel=channel, frame_start=frame)
    if kind == "VIDEO":
        strip = editor.strips.new_movie(**options, fit_method="FIT")
        if (
            not strip.elements
            or strip.elements[0].orig_width <= 0
            or strip.elements[0].orig_height <= 0
            or not math.isfinite(strip.fps)
            or strip.fps <= 0
        ):
            raise ValueError("Blender could not decode the video")
        duration = max(1, round(strip.frame_duration * fps / strip.fps))
        if duration != strip.frame_duration:
            _safe_retiming(editor, ignored=strip)
            # new_movie uses source frames. Retiming this new strip preserves
            # source seconds without changing the scene's FPS or other strips.
            strip.retiming_keys.add(timeline_frame=frame)
            if len(strip.retiming_keys) != 2:
                raise ValueError("Blender could not preserve the video's playback duration")
            strip.retiming_keys[-1].timeline_frame = frame + duration
            if strip.frame_final_duration != duration:
                raise ValueError("Blender could not preserve the video's playback duration")
    else:
        strip = editor.strips.new_sound(**options)
        # Blender may create a one-frame SOUND placeholder for a movie that
        # contains no audio. A non-null Sound alone does not prove decoding.
        if strip.sound is None or strip.sound.samplerate <= 0 or strip.sound.channels == "INVALID":
            raise ValueError("Blender could not decode the audio stream")
    if strip.frame_duration <= 0 or strip.frame_final_duration <= 0:
        raise ValueError("Blender could not decode the media duration")
    strip.select = False
    return strip


def _safe_retiming(editor, ignored=None):
    # Blender 5.2's key-owner lookup can mistake a zero-key strip for the
    # edited movie and crash. Never add keys to unrelated user strips to work
    # around this. Review builders can stage all retimed movies first.
    if editor is not None and any(
        strip != ignored and not len(getattr(strip, "retiming_keys", ()))
        for strip in editor.strips_all
    ):
        raise ValueError(
            "Blender cannot safely retime video beside strips without retiming keys; "
            "use matching-FPS media or build a new assistant review scene"
        )


def _description(strip):
    result = {
        "name": strip.name,
        "type": strip.type,
        "channel": strip.channel,
        "start_frame": strip.frame_final_start,
        "end_frame": strip.frame_final_end,
        "duration_frames": strip.frame_final_duration,
    }
    if strip.type == "MOVIE":
        result["source_fps"] = strip.fps
    return result


def _structure(strip):
    """Actual media/timing, independent of the custom request markers."""
    result = {key: value for key, value in _description(strip).items() if key != "name"}
    result.update(
        frame_start=strip.frame_start,
        offset_start=strip.frame_offset_start,
        offset_end=strip.frame_offset_end,
        mute=strip.mute,
        retiming=tuple(key.timeline_frame for key in strip.retiming_keys),
    )
    if strip.type == "MOVIE":
        result.update(
            path=str(Path(bpy.path.abspath(strip.filepath)).resolve()),
            width=strip.elements[0].orig_width if strip.elements else 0,
            height=strip.elements[0].orig_height if strip.elements else 0,
            trim_start=strip.animation_offset_start,
            trim_end=strip.animation_offset_end,
            reverse=strip.use_reverse_frames,
        )
    elif strip.type == "SOUND":
        result.update(
            path=str(Path(bpy.path.abspath(strip.sound.filepath)).resolve()) if strip.sound else "",
            sample_rate=strip.sound.samplerate if strip.sound else 0,
            audio_channels=strip.sound.channels if strip.sound else "INVALID",
            sound_offset=strip.sound_offset,
            trim_start=strip.animation_offset_start,
            trim_end=strip.animation_offset_end,
            volume=strip.volume,
            pan=strip.pan,
        )
    return result


def _probe(scene, path, requests, fps):
    """Decode in a disposable scene before changing the destination editor."""
    temporary = bpy.data.scenes.new("Open Studio media validation")
    sounds = set(bpy.data.sounds)
    try:
        temporary.render.fps, temporary.render.fps_base = scene.render.fps, scene.render.fps_base
        editor = temporary.sequence_editor_create()
        return [_structure(_insert(editor, path, **request, fps=fps)) for request in requests]
    except Exception:
        raise ValueError("Blender could not decode the requested media streams") from None
    finally:
        bpy.data.scenes.remove(temporary)
        for sound in set(bpy.data.sounds) - sounds:
            if sound.users == 0:
                bpy.data.sounds.remove(sound)


def _free(editor, expected, ignored=()):
    if editor is None:
        return
    for item in expected:
        channel = item["channel"]
        if channel < len(editor.channels):
            state = editor.channels[channel]
            if state.lock or state.mute:
                raise ValueError("The requested sequencer channel is locked or muted")
        if any(
            strip not in ignored
            and strip.channel == channel
            and strip.frame_final_start < item["end_frame"]
            and item["start_frame"] < strip.frame_final_end
            for strip in editor.strips
        ):
            raise ValueError(
                "The requested channel and time range are occupied; choose a free range"
            )


def _repeat(editor, expected, request_key, digest):
    if editor is None:
        return None
    matches = []
    for structure in expected:
        candidates = [
            strip
            for strip in editor.strips
            if strip.get(_REQUEST) == request_key
            and strip.get(_DIGEST) == digest
            and strip.type == structure["type"]
            and _structure(strip) == structure
        ]
        if len(candidates) != 1:
            return None
        matches.extend(candidates)
    _free(editor, expected, matches)
    return matches


def _integer(value, label, minimum, maximum):
    if type(value) is not int or not minimum <= value <= maximum:
        raise ValueError(f"{label} must be an integer from {minimum} to {maximum}")
    return value


def apply_media(snapshot, args):
    """Insert exact streams on explicit free channels; preserve existing editing."""
    if threading.current_thread() is not threading.main_thread():
        raise ValueError("Import media on Blender's main thread")
    scene = bpy.context.scene
    if scene is None or not scene.is_editable:
        raise ValueError("Choose an editable local scene")
    kind = args.get("kind")
    if kind not in {"AUDIO", "VIDEO"}:
        raise ValueError("Choose AUDIO or VIDEO")
    suffixes = AUDIO_SUFFIXES if kind == "AUDIO" else VIDEO_SUFFIXES
    if snapshot.suffix not in suffixes or not _container_matches(snapshot.data, snapshot.suffix):
        raise ValueError("Media contents must match the requested kind and file extension")
    frame = _integer(args.get("start_frame", 1), "Start frame", -1048574, 1048574)
    channel = _integer(args.get("channel", 1), "Channel", 1, 128)
    include_audio = args.get("include_audio", False)
    if type(include_audio) is not bool or (include_audio and kind != "VIDEO"):
        raise ValueError("include_audio is a boolean option for VIDEO")
    name = args.get("name", Path(snapshot.name).stem)
    if not isinstance(name, str) or not name.strip() or len(name) > 128 or "\0" in name:
        raise ValueError("Use a nonempty strip name of at most 128 characters")
    requests = [dict(kind=kind, frame=frame, channel=channel, name=name)]
    if include_audio:
        audio_channel = _integer(args.get("audio_channel", 2), "Audio channel", 1, 128)
        if channel == audio_channel:
            raise ValueError("Video and audio need different sequencer channels")
        requests.append(
            dict(kind="AUDIO", frame=frame, channel=audio_channel, name=name + " audio")
        )
    fps = scene.render.fps / scene.render.fps_base
    if not math.isfinite(fps) or fps <= 0:
        raise ValueError("The scene needs a valid frame rate")
    path = _asset(snapshot)
    expected = _probe(scene, path, requests, fps)
    if any(item["end_frame"] > 1048574 for item in expected):
        raise ValueError("The media would extend past Blender's supported frame range")
    request_key = hashlib.sha256(
        json.dumps([snapshot.sha256, requests, fps], sort_keys=True).encode()
    ).hexdigest()
    editor = scene.sequence_editor
    existing = _repeat(editor, expected, request_key, snapshot.sha256)
    if existing is not None:
        strips, reused = existing, True
    else:
        _free(editor, expected)
        if any(item["type"] == "MOVIE" and item["retiming"] for item in expected):
            _safe_retiming(editor)
        previous_editor = editor
        before = {strip.as_pointer() for strip in editor.strips} if editor else set()
        sounds = set(bpy.data.sounds)
        try:
            editor = editor or scene.sequence_editor_create()
            strips = [_insert(editor, path, **request, fps=fps) for request in requests]
            if [_structure(strip) for strip in strips] != expected:
                raise ValueError("Media placement changed during import")
            for strip in strips:
                strip[_REQUEST], strip[_DIGEST] = request_key, snapshot.sha256
        except Exception:
            # Roll back only newly inserted data, including an insertion that
            # failed before returning its strip to this function.
            try:
                if editor is not None:
                    for strip in list(editor.strips):
                        if strip.as_pointer() not in before:
                            editor.strips.remove(strip)
                    if previous_editor is None and not editor.strips:
                        scene.sequence_editor_clear()
                for sound in set(bpy.data.sounds) - sounds:
                    if sound.users:
                        raise RuntimeError("Media rollback needs inspection")
                    bpy.data.sounds.remove(sound)
            except Exception:
                file_state.mark_modified("Open Studio partial media import")
                raise RuntimeError("Media import rollback needs inspection") from None
            raise ValueError("Media import failed; existing strips were preserved") from None
        file_state.mark_modified("Open Studio assistant media")
        reused = False
    return {
        "kind": kind,
        "sha256": snapshot.sha256,
        "asset_path": str(path),
        "reused": reused,
        "strips": [_description(strip) for strip in strips],
        "scene_fps": fps,
        "packed": False,
        "portability": (
            "The blend file references this persistent local asset. Keep it for playback. "
            "To move the project to another computer, copy the media with the blend file "
            "and relink the sequencer strip paths; video and audio strips are not packed."
        ),
    }
