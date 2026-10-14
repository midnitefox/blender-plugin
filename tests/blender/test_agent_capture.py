# SPDX-FileCopyrightText: 2026 Open Studio contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Capture validation/restoration; real GPU evidence uses the separate GUI probe."""

import hashlib
import shutil
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

import bpy
from helpers import FIXTURES, reset_scene, submodule


class AgentCaptureTests(unittest.TestCase):
    def setUp(self):
        reset_scene()
        self.module = submodule("blender.assistant_capture")
        self.tools = submodule("mcp.tools_agent_capture")
        self.capture = submodule("blender.capture")
        self.scene = bpy.context.scene
        bpy.ops.object.camera_add()
        self.camera = bpy.context.active_object
        self.scene.camera = None
        self.scene.frame_start, self.scene.frame_end = 1, 96
        self.scene.frame_set(17, subframe=0.5)
        self.scene.frame_step = 3
        self.scene.render.fps, self.scene.render.fps_base = 24, 1.0
        self.scene.render.resolution_x, self.scene.render.resolution_y = 777, 555
        self.scene.render.filepath = "//original-output/"
        self.scene.render.use_stamp = True
        self.scene.use_preview_range = True
        self.scene.frame_preview_start, self.scene.frame_preview_end = 10, 80
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory(prefix="agent-capture-")))
        self.enterContext(patch.object(self.module, "_outbox", return_value=self.root))
        self.original = self.capture.RenderSettings.snapshot(self.scene)
        self.fixture = FIXTURES / "synthetic" / "film-four-seconds.mp4"

    def overlays(self):
        return [
            (s.overlay.show_overlays, s.show_gizmo, s.shading.type, s.shading.color_type)
            for window in bpy.context.window_manager.windows
            for area in window.screen.areas
            if area.type == "VIEW_3D"
            for s in (area.spaces.active,)
        ]

    def runner(self, kind, context, scene):
        self.assertEqual(kind, "animation")
        self.assertEqual(scene.camera, self.camera)
        self.assertEqual(scene.frame_step, 1)
        self.assertEqual((scene.frame_start, scene.frame_end), (1, 96))
        self.assertEqual(scene.render.ffmpeg.audio_codec, "NONE")
        self.assertEqual(scene.render.resolution_percentage, 100)
        shutil.copyfile(self.fixture, scene.render.filepath)
        scene.frame_set(96)

    def call(self, **kwargs):
        return self.tools.capture_agent_animation(
            dict(source="CAMERA", start_frame=1, end_frame=96, **kwargs)
        )

    def test_headless_refusal_happens_before_capture_or_outbox_creation(self):
        if not bpy.app.background:
            self.skipTest("Headless refusal is specific to background Blender")
        with patch.object(self.module, "_outbox") as outbox:
            with self.assertRaisesRegex(RuntimeError, "GUI"):
                self.call()
        outbox.assert_not_called()
        self.assertEqual(self.capture.RenderSettings.snapshot(self.scene), self.original)

    def test_invalid_range_size_camera_and_types_refuse_before_capture(self):
        for args in (
            {"start_frame": 1},
            {"start_frame": 20, "end_frame": 1},
            {"start_frame": 1, "end_frame": 481},
            {"start_frame": True, "end_frame": 1},
            {"width": 1922},
            {"width": 65},
            {"height": 1082},
            {"grey_clay": "yes"},
            {"source": "SCENE"},
            {"camera_name": "Missing"},
            {"source": "VIEWPORT", "camera_name": self.camera.name},
        ):
            with self.subTest(args=args):
                with patch.object(self.module, "_gui") as gui:
                    with self.assertRaises(ValueError):
                        self.tools.capture_agent_animation(args)
                gui.assert_not_called()
                self.assertEqual(self.capture.RenderSettings.snapshot(self.scene), self.original)
                self.assertFalse(list(self.root.iterdir()))

    def test_preview_default_and_explicit_named_camera_are_planned_without_mutation(self):
        plan, fps, frames = self.module._plan(bpy.context, {"camera_name": self.camera.name})
        self.assertEqual((plan["frame_start"], plan["frame_end"]), (10, 80))
        self.assertEqual(plan["camera"], self.camera)
        self.assertEqual((fps, frames), (24, 71))
        self.assertEqual(self.capture.RenderSettings.snapshot(self.scene), self.original)

    def test_success_retains_unique_handoff_and_restores_camera_subframe_and_view(self):
        before_overlays = self.overlays()
        before_view = self.capture._view_context["value"]
        with (
            patch.object(self.module, "_gui"),
            patch.object(self.capture, "_default_runner", side_effect=self.runner),
            patch.object(self.module, "_verify_video") as verify,
        ):
            first, second = self.call(grey_clay=True), self.call(grey_clay=True)
        self.assertEqual(verify.call_count, 2)
        self.assertNotEqual(first["path"], second["path"])
        self.assertEqual(first["sha256"], hashlib.sha256(self.fixture.read_bytes()).hexdigest())
        self.assertEqual(first["duration_seconds"], 4)
        self.assertEqual(first["frame_count"], 96)
        self.assertFalse(first["audio"])
        self.assertTrue(first["retained"])
        for item in (first, second):
            self.assertEqual(Path(item["path"]).read_bytes(), self.fixture.read_bytes())
            self.assertEqual(Path(item["path"]).parent.parent, self.root)
        self.assertEqual(self.capture.RenderSettings.snapshot(self.scene), self.original)
        self.assertIsNone(self.scene.camera)
        self.assertEqual(self.scene.frame_step, 3)
        self.assertEqual(self.overlays(), before_overlays)
        self.assertEqual(self.capture._view_context["value"], before_view)

    def test_timeout_restores_all_settings_and_removes_partial_capture(self):
        before_overlays = self.overlays()
        before_view = self.capture._view_context["value"]

        def fail(kind, context, scene):
            self.runner(kind, context, scene)
            scene.render.fps, scene.render.fps_base = 30, 1.001
            raise TimeoutError("Synthetic interrupted capture")

        with (
            patch.object(self.module, "_gui"),
            patch.object(self.capture, "_default_runner", side_effect=fail),
        ):
            with self.assertRaises(TimeoutError):
                self.call(grey_clay=True)
        self.assertEqual(self.capture.RenderSettings.snapshot(self.scene), self.original)
        self.assertEqual((self.scene.render.fps, self.scene.render.fps_base), (24, 1.0))
        self.assertEqual(self.scene.frame_step, 3)
        self.assertEqual(self.overlays(), before_overlays)
        self.assertEqual(self.capture._view_context["value"], before_view)
        self.assertFalse(list(self.root.iterdir()))

    def test_actual_decoder_rejects_partial_or_wrong_size_output_and_leaves_no_scene(self):
        before = set(bpy.data.scenes)
        self.module._verify_video(self.fixture, dict(width=32, height=32), 96, 24)
        for plan, frames, fps in (
            (dict(width=64, height=64), 96, 24),
            (dict(width=32, height=32), 24, 24),
            (dict(width=32, height=32), 96, 30),
        ):
            with self.subTest(plan=plan, frames=frames, fps=fps):
                with self.assertRaises(RuntimeError):
                    self.module._verify_video(self.fixture, plan, frames, fps)
        self.assertEqual(set(bpy.data.scenes), before)
        with (
            patch.object(self.module, "_gui"),
            patch.object(self.capture, "_default_runner", side_effect=self.runner),
        ):
            with self.assertRaisesRegex(RuntimeError, "incomplete"):
                self.call()
        self.assertFalse(list(self.root.iterdir()))
        self.assertEqual(self.capture.RenderSettings.snapshot(self.scene), self.original)

    def test_worker_thread_refuses_before_reading_blender_state(self):
        with ThreadPoolExecutor(max_workers=1) as pool:
            with self.assertRaisesRegex(RuntimeError, "main thread"):
                pool.submit(self.tools.capture_agent_animation, {}).result(5)

    def test_camera_view_overrides_and_restores_local_camera_on_success_and_failure(self):
        _, area, _ = self.capture._view3d(bpy.context)
        self.assertIsNotNone(area)
        space = area.spaces.active
        original_local, original_camera = space.use_local_camera, space.camera
        pinned = bpy.data.objects.new("Pinned", bpy.data.cameras.new("Pinned"))
        self.scene.collection.objects.link(pinned)
        try:
            for local in (False, True):
                for fail in (False, True):
                    with self.subTest(local=local, fail=fail):
                        space.use_local_camera = True
                        space.camera = pinned
                        space.use_local_camera = local
                        self.scene.camera = self.camera
                        saved = (space.use_local_camera, space.camera)
                        try:
                            with self.capture._CameraView(area, self.scene):
                                self.assertTrue(space.use_local_camera)
                                self.assertEqual(space.camera, self.camera)
                                self.assertEqual(self.scene.camera, self.camera)
                                if fail:
                                    raise TimeoutError("Inside camera override")
                        except TimeoutError:
                            self.assertTrue(fail)
                        self.assertEqual((space.use_local_camera, space.camera), saved)
                        self.assertEqual(self.scene.camera, self.camera)
        finally:
            space.use_local_camera = True
            space.camera = original_camera
            space.use_local_camera = original_local
            self.scene.camera = None
