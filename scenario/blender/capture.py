# SPDX-FileCopyrightText: 2026 Scenario Inc.
# SPDX-License-Identifier: GPL-3.0-or-later
"""Viewport and camera captures (stills and playblasts). Main thread only; the real runner needs the GUI."""

import logging
import time
from dataclasses import dataclass

import bpy

from ..core.scene import capture_plan
from . import runtime

log = logging.getLogger("scenario.capture")
_view_context = {"value": True}


def capture_dir():
    path = runtime.paths().cache_dir / "captures"
    path.mkdir(parents=True, exist_ok=True)
    return path


def new_capture_path(prefix, ext):
    return str(capture_dir() / f"{prefix}_{int(time.time() * 1000)}.{ext}")


@dataclass
class RenderSettings:
    resolution_x: int
    resolution_y: int
    resolution_percentage: int
    file_format: str
    color_mode: str
    filepath: str
    use_stamp: bool
    frame_start: int
    frame_end: int
    use_preview_range: bool
    ffmpeg_format: str
    ffmpeg_codec: str
    ffmpeg_audio: str
    film_transparent: bool
    camera_name: str
    media_type: str = ""
    frame_current: int = 1
    frame_subframe: float = 0.0

    @classmethod
    def snapshot(cls, scene):
        r = scene.render
        return cls(
            r.resolution_x,
            r.resolution_y,
            r.resolution_percentage,
            r.image_settings.file_format,
            r.image_settings.color_mode,
            r.filepath,
            r.use_stamp,
            scene.frame_start,
            scene.frame_end,
            scene.use_preview_range,
            r.ffmpeg.format,
            r.ffmpeg.codec,
            r.ffmpeg.audio_codec,
            r.film_transparent,
            scene.camera.name if scene.camera else "",
            getattr(r.image_settings, "media_type", ""),
            scene.frame_current,
            scene.frame_subframe,
        )

    def restore(self, scene):
        r = scene.render
        r.resolution_x, r.resolution_y, r.resolution_percentage = (
            self.resolution_x,
            self.resolution_y,
            self.resolution_percentage,
        )
        if self.media_type and hasattr(r.image_settings, "media_type"):
            r.image_settings.media_type = self.media_type
        if self.media_type != "VIDEO":
            r.image_settings.file_format = self.file_format
        try:
            r.image_settings.color_mode = self.color_mode
        except TypeError:
            pass
        r.filepath, r.use_stamp = self.filepath, self.use_stamp
        scene.frame_start, scene.frame_end = self.frame_start, self.frame_end
        _set_preview_range(scene, self.use_preview_range)
        r.ffmpeg.format, r.ffmpeg.codec, r.ffmpeg.audio_codec = (
            self.ffmpeg_format,
            self.ffmpeg_codec,
            self.ffmpeg_audio,
        )
        r.film_transparent = self.film_transparent
        if self.camera_name and bpy.data.objects.get(self.camera_name) is not None:
            scene.camera = bpy.data.objects[self.camera_name]
        elif not self.camera_name:
            scene.camera = None
        if (scene.frame_current, scene.frame_subframe) != (self.frame_current, self.frame_subframe):
            scene.frame_set(self.frame_current, subframe=self.frame_subframe)


def _set_preview_range(scene, enabled):
    """Write the preview-range toggle only when it changes.

    Its RNA update sends a frame notifier even for a same-value write. In the
    GUI, Blender answers after the capturing operator or timer returns with a
    same-frame update that runs frame_change_pre, which invalidates any origin
    captured in between, such as the upload origin of this very capture.
    """
    if scene.use_preview_range != enabled:
        scene.use_preview_range = enabled


def set_video_output(render):
    """Blender 4.x uses file_format FFMPEG; 4.5+/5.x use image_settings.media_type VIDEO."""
    settings = render.image_settings
    if hasattr(settings, "media_type"):
        settings.media_type = "VIDEO"
    else:
        settings.file_format = "FFMPEG"


def set_image_output(render, file_format):
    settings = render.image_settings
    if hasattr(settings, "media_type"):
        settings.media_type = "IMAGE"
    settings.file_format = file_format


def _view3d(context):
    area = getattr(context, "area", None)
    if area is not None and area.type == "VIEW_3D" and getattr(context, "window", None) is not None:
        region = getattr(context, "region", None)
        if region is None or region.type != "WINDOW":
            region = next((r for r in area.regions if r.type == "WINDOW"), None)
        return context.window, area, region
    for window in context.window_manager.windows:
        for area in window.screen.areas:
            if area.type == "VIEW_3D":
                region = next((r for r in area.regions if r.type == "WINDOW"), None)
                return window, area, region
    return None, None, None


class _CameraView:
    """Look through the scene camera in the active viewport for the duration of a capture, then restore the view.

    `render.opengl(view_context=False)` renders with the scene's Workbench settings: flat grey whatever the viewport
    shows, which is why "Camera clip" came out as clay even with Grey clay capture off. Capturing the viewport in
    camera view keeps its shading (Material Preview, Rendered) and still frames exactly what the camera sees."""

    def __init__(self, area, scene):
        self.space = area.spaces.active if area is not None else None
        self.scene = scene
        self.saved = None
        self.saved_camera = None

    def __enter__(self):
        r3d = getattr(self.space, "region_3d", None)
        if r3d is not None:
            self.saved_camera = (self.space.use_local_camera, self.space.camera)
            # A viewport-pinned camera otherwise overrides the requested scene camera.
            # Set the local flag first: assigning space.camera while it is false
            # also assigns the scene camera.
            self.space.use_local_camera = True
            self.space.camera = self.scene.camera
            self.saved = (
                r3d.view_perspective,
                r3d.view_matrix.copy(),
                r3d.view_distance,
                r3d.view_location.copy(),
                r3d.view_rotation.copy(),
                getattr(r3d, "view_camera_zoom", 0.0),
                getattr(r3d, "view_camera_offset", (0.0, 0.0))[:],
            )
            r3d.view_perspective = "CAMERA"
            try:
                r3d.view_camera_zoom = 29.0  # the camera frame fills the region; render.opengl crops to the camera anyway
                r3d.view_camera_offset = (0.0, 0.0)
            except (AttributeError, TypeError):
                pass
        return self

    def __exit__(self, *exc):
        r3d = getattr(self.space, "region_3d", None)
        if r3d is not None and self.saved is not None:
            local, camera = self.saved_camera
            self.space.camera = camera
            self.space.use_local_camera = local
            perspective, matrix, distance, location, rotation, zoom, offset = self.saved
            r3d.view_perspective = perspective
            if perspective != "CAMERA":
                r3d.view_location = location
                r3d.view_rotation = rotation
                r3d.view_distance = distance
            try:
                r3d.view_camera_zoom = zoom
                r3d.view_camera_offset = offset
            except (AttributeError, TypeError):
                pass
        return False


def _default_runner(kind, context, scene):
    window, area, region = _view3d(context)
    if window is None:
        raise RuntimeError("No 3D viewport available for the capture")
    with context.temp_override(window=window, area=area, region=region, scene=scene):
        if _view_context["value"]:
            bpy.ops.render.opengl(
                animation=(kind == "animation"), view_context=True, write_still=(kind == "still")
            )
        else:
            # camera source: capture through the viewport in camera view so materials and lighting come along
            with _CameraView(area, scene):
                bpy.ops.render.opengl(
                    animation=(kind == "animation"),
                    view_context=True,
                    write_still=(kind == "still"),
                )


class _Overlays:
    """Hide overlays and gizmos (and optionally force solid shading) while a capture runs."""

    def __init__(self, context, force_solid):
        self.spaces = []
        for window in context.window_manager.windows:
            for area in window.screen.areas:
                if area.type == "VIEW_3D":
                    self.spaces.append(area.spaces.active)
        self.force_solid = force_solid
        self.saved = []

    def __enter__(self):
        for space in self.spaces:
            self.saved.append(
                (
                    space.overlay.show_overlays,
                    space.show_gizmo,
                    space.shading.type,
                    space.shading.color_type,
                )
            )
            space.overlay.show_overlays = False
            space.show_gizmo = False
            if self.force_solid:
                space.shading.type = "SOLID"
                space.shading.color_type = "SINGLE"
        return self

    def __exit__(self, *exc):
        for space, (overlays, gizmo, shading, color) in zip(self.spaces, self.saved, strict=False):
            space.overlay.show_overlays = overlays
            space.show_gizmo = gizmo
            space.shading.type = shading
            space.shading.color_type = color
        return False


def _prepare_source(scene, source, camera):
    if source == "CAMERA":
        cam = camera or scene.camera or next((o for o in scene.objects if o.type == "CAMERA"), None)
        if cam is None:
            raise RuntimeError("Camera capture needs a camera in the scene")
        scene.camera = cam
    _view_context["value"] = source != "CAMERA"


def capture_still(
    context,
    path,
    source="VIEWPORT",
    camera=None,
    width=None,
    height=None,
    force_solid=False,
    runner=None,
):
    scene = context.scene
    saved = RenderSettings.snapshot(scene)
    runner = runner or _default_runner
    try:
        _prepare_source(scene, source, camera)
        r = scene.render
        if width and height:
            r.resolution_x, r.resolution_y = int(width), int(height)
        r.resolution_percentage = 100
        set_image_output(r, "PNG")
        r.image_settings.color_mode = "RGB"
        r.use_stamp = False
        r.filepath = str(path)
        with _Overlays(context, force_solid):
            runner("still", context, scene)
        return str(path)
    finally:
        saved.restore(scene)


def capture_playblast(
    context,
    path,
    source="VIEWPORT",
    camera=None,
    width=1280,
    height=720,
    frame_start=None,
    frame_end=None,
    force_solid=False,
    runner=None,
):
    scene = context.scene
    saved = RenderSettings.snapshot(scene)
    runner = runner or _default_runner
    fps = scene.render.fps / (scene.render.fps_base or 1.0)
    start, end, seconds = capture_plan.frame_span(
        frame_start if frame_start is not None else scene.frame_start,
        frame_end if frame_end is not None else scene.frame_end,
        fps,
        use_preview=(frame_start is None and scene.use_preview_range),
        preview_start=scene.frame_preview_start,
        preview_end=scene.frame_preview_end,
    )
    try:
        _prepare_source(scene, source, camera)
        r = scene.render
        r.resolution_x, r.resolution_y, r.resolution_percentage = int(width), int(height), 100
        set_video_output(r)
        r.ffmpeg.format, r.ffmpeg.codec, r.ffmpeg.audio_codec = "MPEG4", "H264", "NONE"
        r.use_stamp = False
        # Blender renders an enabled preview range. Keep it when it is the span.
        if (start, end) != (scene.frame_preview_start, scene.frame_preview_end):
            _set_preview_range(scene, False)
        scene.frame_start, scene.frame_end = start, end
        r.filepath = str(path)
        with _Overlays(context, force_solid):
            runner("animation", context, scene)
        return {
            "path": str(path),
            "frame_start": start,
            "frame_end": end,
            "seconds": seconds,
            "fps": fps,
        }
    finally:
        saved.restore(scene)


def first_frame_still(
    context, path, source="VIEWPORT", camera=None, force_solid=False, runner=None
):
    scene = context.scene
    current = scene.frame_current
    start = scene.frame_preview_start if scene.use_preview_range else scene.frame_start
    try:
        scene.frame_set(start)
        return capture_still(
            context,
            path,
            source=source,
            camera=camera,
            width=1280,
            height=720,
            force_solid=force_solid,
            runner=runner,
        )
    finally:
        scene.frame_set(current)
