# SPDX-FileCopyrightText: 2026 Open Studio contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Real GPU animation handoff probe, launched only by its isolated runner."""

import hashlib
import importlib
import json
import os
import shutil
import sys
import traceback
from dataclasses import asdict
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import bpy

OUTPUT, INSTALLED = map(Path, sys.argv[sys.argv.index("--") + 1 :])


def state(capture):
    scene = bpy.context.scene
    views = []
    for window in bpy.context.window_manager.windows:
        for area in window.screen.areas:
            if area.type != "VIEW_3D":
                continue
            space = area.spaces.active
            view = space.region_3d
            views.append(
                dict(
                    overlays=space.overlay.show_overlays,
                    gizmos=space.show_gizmo,
                    shading=space.shading.type,
                    color=space.shading.color_type,
                    perspective=view.view_perspective,
                    location=list(view.view_location),
                    rotation=list(view.view_rotation),
                    distance=view.view_distance,
                    zoom=view.view_camera_zoom,
                    offset=list(view.view_camera_offset),
                    use_local_camera=space.use_local_camera,
                    local_camera=space.camera.name if space.camera else None,
                )
            )
    return dict(
        render=asdict(capture.RenderSettings.snapshot(scene)),
        fps=scene.render.fps,
        fps_base=scene.render.fps_base,
        frame_step=scene.frame_step,
        preview_start=scene.frame_preview_start,
        preview_end=scene.frame_preview_end,
        views=views,
        camera=scene.camera.name if scene.camera else None,
        filepath=bpy.data.filepath,
    )


def camera_choice(capture, tools):
    """Distinguish the requested red view from a viewport-pinned blue view."""
    scene = bpy.context.scene
    for obj in list(scene.objects):
        bpy.data.objects.remove(obj, do_unlink=True)
    cameras = []
    for label, x, color in (("Requested", -4, (1, 0, 0, 1)), ("Pinned", 4, (0, 0, 1, 1))):
        bpy.ops.mesh.primitive_plane_add(size=2, location=(x, 0, 0))
        bpy.context.object.color = color
        bpy.ops.object.camera_add(location=(x, 0, 6))
        camera = bpy.context.object
        camera.name = label
        camera.data.type, camera.data.ortho_scale = "ORTHO", 3
        cameras.append(camera)
    requested, pinned = cameras
    scene.camera = requested
    for window in bpy.context.window_manager.windows:
        for area in window.screen.areas:
            if area.type == "VIEW_3D":
                space = area.spaces.active
                space.shading.type, space.shading.color_type = "SOLID", "OBJECT"
                space.shading.light = "FLAT"
                space.use_local_camera = True
                space.camera = pinned
                space.region_3d.view_perspective = "CAMERA"
                space.region_3d.view_rotation = pinned.rotation_euler.to_quaternion()
    before = state(capture)
    colors = {}
    for source in ("CAMERA", "VIEWPORT"):
        path = OUTPUT / (source.lower() + "-framing.png")
        capture.capture_still(
            bpy.context, path, source=source, camera=requested, width=320, height=180
        )
        if state(capture) != before:
            (OUTPUT / "framing-state-diff.json").write_text(
                json.dumps(dict(before=before, after=state(capture)), indent=2), encoding="utf-8"
            )
            raise AssertionError(f"Pinned camera settings changed after {source} still")
        with bpy.data.temp_data() as data:
            image = data.images.load(str(path))
            width, height = image.size
            center = ((height // 2) * width + width // 2) * 4
            colors[source] = list(image.pixels[center : center + 3])
    clip = tools.capture_agent_animation(
        dict(
            source="CAMERA",
            camera_name=requested.name,
            start_frame=1,
            end_frame=2,
            width=320,
            height=180,
        )
    )
    retained = OUTPUT / "camera-framing.mp4"
    shutil.copyfile(clip["path"], retained)
    clip["evidence_path"] = str(retained)
    if state(capture) != before:
        raise AssertionError("Pinned camera settings changed after animation")
    original = bpy.ops.render.opengl

    def interrupted(**kwargs):
        original(**kwargs)
        raise TimeoutError("Synthetic interruption inside camera override")

    render_bpy = SimpleNamespace(
        data=bpy.data, ops=SimpleNamespace(render=SimpleNamespace(opengl=interrupted))
    )
    with patch.object(capture, "bpy", render_bpy):
        try:
            capture.capture_still(
                bpy.context,
                OUTPUT / "interrupted-framing.png",
                source="CAMERA",
                camera=requested,
                width=320,
                height=180,
            )
        except TimeoutError:
            pass
        else:
            raise AssertionError("Camera override did not receive the interruption")
    if state(capture) != before:
        raise AssertionError("Pinned camera settings changed after interrupted render")
    return dict(center_rgb=colors, settings_restored=True, failure_settings_restored=True), clip


def verify():
    if (
        bpy.app.background
        or bpy.app.online_access
        or os.environ.get("OPEN_STUDIO_CAPTURE_PROBE") != "1"
    ):
        raise RuntimeError("Use the isolated offline GUI runner")
    name = next(
        n for n in bpy.context.preferences.addons.keys() if n.endswith("." + INSTALLED.name)
    )
    addon = importlib.import_module(name)
    if Path(addon.__file__).resolve().parent != INSTALLED.resolve():
        raise RuntimeError("Capture extension differs from the verified installation")
    tools = importlib.import_module(name + ".mcp.tools_agent_capture")
    assistant = importlib.import_module(name + ".blender.assistant_capture")
    capture = importlib.import_module(name + ".blender.capture")
    scene = bpy.context.scene
    cube = scene.objects.get("Cube")
    if cube is None:
        bpy.ops.mesh.primitive_cube_add()
        cube = bpy.context.active_object
    cube.rotation_euler.z = 0
    cube.keyframe_insert("rotation_euler", frame=1)
    cube.rotation_euler.z = 1.2
    cube.keyframe_insert("rotation_euler", frame=12)
    camera = next(obj for obj in scene.objects if obj.type == "CAMERA")
    scene.camera = None
    scene.frame_start, scene.frame_end = 1, 48
    scene.frame_set(7, subframe=0.5)
    scene.frame_step = 3
    scene.use_preview_range = True
    scene.frame_preview_start, scene.frame_preview_end = 3, 8
    scene.render.fps, scene.render.fps_base = 24, 1.0
    scene.render.resolution_x, scene.render.resolution_y = 777, 555
    scene.render.resolution_percentage = 50
    scene.render.filepath = "//original-output/"
    scene.render.use_stamp = True
    for window in bpy.context.window_manager.windows:
        for area in window.screen.areas:
            if area.type == "VIEW_3D":
                space = area.spaces.active
                space.shading.type, space.shading.color_type = "SOLID", "MATERIAL"
                space.overlay.show_overlays, space.show_gizmo = True, True
                space.region_3d.view_perspective = "PERSP"
    before = state(capture)
    outputs = []
    for label, args in (
        (
            "camera",
            dict(
                source="CAMERA",
                camera_name=camera.name,
                start_frame=1,
                end_frame=12,
                grey_clay=True,
            ),
        ),
        ("viewport", dict(source="VIEWPORT", start_frame=4, end_frame=11)),
    ):
        dimensions = {} if label == "camera" else dict(width=320, height=180)
        result = tools.capture_agent_animation(dict(**dimensions, **args))
        if state(capture) != before:
            raise AssertionError(f"Capture changed settings: {label}")
        source = Path(result["path"])
        data = source.read_bytes()
        if hashlib.sha256(data).hexdigest() != result["sha256"]:
            raise AssertionError("Handoff digest mismatch")
        retained = OUTPUT / (label + ".mp4")
        shutil.copyfile(source, retained)
        result["evidence_path"] = str(retained)
        outputs.append(result)
    root = assistant._outbox()
    existing = set(root.iterdir())
    original_runner = capture._default_runner

    def fail_after_real_render(*args):
        original_runner(*args)
        raise TimeoutError("Synthetic post-render interruption")

    with patch.object(capture, "_default_runner", side_effect=fail_after_real_render):
        try:
            tools.capture_agent_animation(
                dict(
                    source="CAMERA",
                    start_frame=1,
                    end_frame=2,
                    width=320,
                    height=180,
                    grey_clay=True,
                )
            )
        except TimeoutError:
            pass
        else:
            raise AssertionError("Interruption was not reported")
    if set(root.iterdir()) != existing or state(capture) != before:
        raise AssertionError("Interrupted real capture leaked files or settings")
    after = state(capture)
    bpy.ops.screen.screenshot(filepath=str(OUTPUT / "restored-viewport.png"))
    framing, clip = camera_choice(capture, tools)
    outputs.append(clip)
    return dict(
        status="passed",
        blender=bpy.app.version_string,
        online_access=bpy.app.online_access,
        outputs=outputs,
        settings_restored=True,
        failure_cleanup=True,
        before=before,
        after=after,
        camera_choice=framing,
    )


def run():
    try:
        result = verify()
    except Exception:
        result = dict(status="failed", error=traceback.format_exc())
        traceback.print_exc()
    (OUTPUT / "gui.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    bpy.ops.wm.quit_blender()
    return None


bpy.app.timers.register(run, first_interval=4.0)
