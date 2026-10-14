# SPDX-FileCopyrightText: 2026 Open Studio contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Bounded GUI animation captures retained for the connected assistant."""

import math
import os
import shutil
import stat
import tempfile
import threading
from pathlib import Path

import bpy

from ..core.agent_artifacts import read_inbox_file
from . import capture, runtime

MAX_SECONDS = 20
MAX_FRAMES = 600
MAX_CAPTURE_BYTES = 512 * 1024 * 1024


def _integer(value, label, lower, upper):
    if type(value) is not int or not lower <= value <= upper:
        raise ValueError(f"{label} must be an integer from {lower} to {upper}")
    return value


def _plan(context, args):
    source = args.get("source", "CAMERA")
    if source not in {"CAMERA", "VIEWPORT"}:
        raise ValueError("Choose CAMERA or VIEWPORT capture")
    width = _integer(args.get("width", 1280), "Width", 64, 1920)
    height = _integer(args.get("height", 720), "Height", 64, 1080)
    if width % 2 or height % 2:
        raise ValueError("Use even width and height for the MP4 capture")
    grey_clay = args.get("grey_clay", False)
    if type(grey_clay) is not bool:
        raise ValueError("grey_clay must be a boolean")
    scene = context.scene
    fps = scene.render.fps / scene.render.fps_base
    if not math.isfinite(fps) or not 1 <= fps <= 240:
        raise ValueError("Use a scene frame rate between 1 and 240 FPS")
    if ("start_frame" in args) != ("end_frame" in args):
        raise ValueError("Provide both start_frame and end_frame, or use the current scene range")
    defaults = (
        (scene.frame_preview_start, scene.frame_preview_end)
        if scene.use_preview_range
        else (scene.frame_start, scene.frame_end)
    )
    start = _integer(args.get("start_frame", defaults[0]), "Start frame", -1048574, 1048574)
    end = _integer(args.get("end_frame", defaults[1]), "End frame", -1048574, 1048574)
    frames = end - start + 1
    if frames < 1 or frames > MAX_FRAMES or frames / fps > MAX_SECONDS:
        raise ValueError("Choose an ordered range of at most 20 seconds and 600 frames")
    camera = None
    camera_name = args.get("camera_name")
    if camera_name is not None and (
        source != "CAMERA" or not isinstance(camera_name, str) or not camera_name
    ):
        raise ValueError("camera_name must name a camera for CAMERA capture")
    if source == "CAMERA":
        camera = (
            scene.objects.get(camera_name)
            if camera_name is not None
            else scene.camera or next((obj for obj in scene.objects if obj.type == "CAMERA"), None)
        )
        if camera is None or camera.type != "CAMERA" or camera.name not in scene.objects:
            raise ValueError("Choose a camera in the current scene")
    return (
        dict(
            source=source,
            camera=camera,
            width=width,
            height=height,
            frame_start=start,
            frame_end=end,
            force_solid=grey_clay,
        ),
        fps,
        frames,
    )


def _gui(context):
    if bpy.app.background:
        raise RuntimeError("Animation capture needs the Blender GUI and an open 3D viewport")
    window, area, region = capture._view3d(context)
    if window is None or area is None or region is None:
        raise RuntimeError("Open a 3D viewport before capturing animation")


def _outbox():
    root = runtime.paths().state_dir / "assistant-outbox"
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    info = root.lstat()
    if not stat.S_ISDIR(info.st_mode) or getattr(info, "st_file_attributes", 0) & getattr(
        stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400
    ):
        raise ValueError("Assistant outbox must be a private directory")
    return root.resolve(strict=True)


def _verify_video(path, plan, frames, fps):
    """Reject partial/cancelled output instead of reporting the requested length."""
    scene = bpy.data.scenes.new("Open Studio capture validation")
    try:
        strip = scene.sequence_editor_create().strips.new_movie(
            "Capture validation", str(path), 1, 1
        )
        if (
            not strip.elements
            or strip.elements[0].orig_width != plan["width"]
            or strip.elements[0].orig_height != plan["height"]
            or strip.frame_duration != frames
            or not math.isclose(strip.fps, fps, rel_tol=0.0001)
        ):
            raise RuntimeError("The capture is incomplete or its dimensions/timing differ")
    finally:
        bpy.data.scenes.remove(scene)


def capture_animation(args):
    if threading.current_thread() is not threading.main_thread():
        raise RuntimeError("Capture animation on Blender's main thread")
    context = bpy.context
    plan, fps, frames = _plan(context, args)
    _gui(context)
    root = _outbox()
    directory = Path(tempfile.mkdtemp(prefix="animation-", dir=root)).resolve()
    path = directory / "animation.mp4"
    scene = context.scene
    original_timing = scene.render.fps, scene.render.fps_base, scene.frame_step
    original_view_context = capture._view_context["value"]
    try:
        if os.name == "nt" and len(str(path).encode("utf-16-le")) // 2 > 258:
            raise ValueError("Use a shorter extension data path for animation captures on Windows")
        scene.frame_step = 1
        try:
            info = capture.capture_playblast(context, path, **plan)
        finally:
            scene.render.fps, scene.render.fps_base, scene.frame_step = original_timing
            capture._view_context["value"] = original_view_context
        snapshot = read_inbox_file(
            directory, path.name, suffixes={".mp4"}, max_bytes=MAX_CAPTURE_BYTES
        )
        if len(snapshot.data) < 12 or snapshot.data[4:8] != b"ftyp":
            raise RuntimeError("Blender did not produce a usable MP4 capture")
        _verify_video(path, plan, frames, fps)
        return {
            "path": str(path),
            "sha256": snapshot.sha256,
            "bytes": len(snapshot.data),
            "source": plan["source"],
            "camera": plan["camera"].name if plan["camera"] else None,
            "frame_start": info["frame_start"],
            "frame_end": info["frame_end"],
            "frame_count": frames,
            "fps": fps,
            "duration_seconds": frames / fps,
            "width": plan["width"],
            "height": plan["height"],
            "audio": False,
            "retained": True,
            "instructions": (
                "Use this silent MP4 with your assistant's video-generation or editing tool. "
                "It is retained in the local assistant outbox. Copy it to your project before "
                "removing extension data. Blender does not invoke a generation service."
            ),
        }
    except BaseException:
        # This unique directory belongs to this call. Partial captures have no
        # successful handoff and must not be mistaken for completed references.
        if directory.parent != root:
            raise RuntimeError("Capture cleanup target is outside the assistant outbox") from None
        shutil.rmtree(directory)
        raise
