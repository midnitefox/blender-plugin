# SPDX-FileCopyrightText: 2026 Open Studio contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Real render-result handoff and GUI grey-clay still capture acceptance."""

import base64
import hashlib
import json
import shutil
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

import bpy
from helpers import FIXTURES, reset_scene, submodule
from mathutils import Vector


class AgentStillInputTests(unittest.TestCase):
    def setUp(self):
        reset_scene()
        self.tools = submodule("mcp.tools_blender")
        self.capture = submodule("blender.capture")
        self.decode = submodule("blender.preview_decode").decode
        self.scene = bpy.context.scene
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory(prefix="still-test-")))
        self.enterContext(patch.object(self.tools, "_still_outbox", return_value=self.root))
        self.scene.frame_set(7, subframe=0.25)
        self.scene.render.filepath = "//user-output/"
        self.scene.render.image_settings.file_format = "PNG"
        self.scene.render.image_settings.color_depth = "16"
        self.scene.render.image_settings.color_mode = "RGBA"
        self.scene.render.image_settings.compression = 63
        self.scene.render.use_stamp = True

    def state(self):
        spaces = []
        for window in bpy.context.window_manager.windows:
            for area in window.screen.areas:
                if area.type == "VIEW_3D":
                    space = area.spaces.active
                    spaces.append(
                        (
                            space.overlay.show_overlays,
                            space.show_gizmo,
                            space.shading.type,
                            space.shading.color_type,
                            tuple(space.shading.single_color),
                            space.shading.light,
                            space.shading.studio_light,
                            space.use_local_camera,
                            space.camera.name if space.camera else None,
                            space.region_3d.view_perspective,
                            tuple(space.region_3d.view_rotation),
                            tuple(space.region_3d.view_location),
                            space.region_3d.view_distance,
                        )
                    )
        return (
            self.capture.RenderSettings.snapshot(self.scene),
            spaces,
            self.capture._view_context["value"],
            bpy.data.filepath,
            (self.scene.render.fps, self.scene.render.fps_base, self.scene.frame_step),
            (
                self.scene.view_settings.view_transform,
                self.scene.view_settings.look,
                self.scene.view_settings.exposure,
                self.scene.view_settings.gamma,
            ),
        )

    def metadata(self, response):
        self.assertEqual([item["type"] for item in response], ["text", "image"])
        data = json.loads(response[0]["text"])
        path = Path(data["path"])
        self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), data["sha256"])
        self.assertEqual(path.stat().st_size, data["bytes"])
        self.assertTrue(base64.b64decode(response[1]["data"]).startswith(b"\x89PNG\r\n\x1a\n"))
        self.assertTrue(data["retained"])
        return data

    def color(self, path):
        (width, height), pixels = self.decode(path, edge=32, limit=lambda w, h: True)
        offset = (width * (height // 2) + width // 2) * 4
        return pixels[offset : offset + 3]

    def existing_render(self):
        self.scene.render.resolution_x = self.scene.render.resolution_y = 64
        self.scene.render.resolution_percentage = 100
        self.scene.render.use_stamp = False
        self.scene.view_settings.view_transform = "Standard"
        self.scene.view_settings.look = "None"
        strip = self.scene.sequence_editor_create().strips.new_effect(
            "Rendered red", "COLOR", 1, 1, length=30
        )
        strip.color = (1, 0, 0)
        bpy.ops.render.render(scene=self.scene.name)
        strip.color = (0, 0, 1)
        self.scene.render.image_settings.color_depth = "16"
        self.scene.render.use_stamp = True
        return strip

    def test_render_result_snapshot_uses_existing_pixels_without_a_new_render(self):
        self.existing_render()
        before = self.state()
        with patch.object(self.capture, "_default_runner") as runner:
            first = self.metadata(self.tools.snapshot_render_result({}))
            second = self.metadata(self.tools.snapshot_render_result({}))
        runner.assert_not_called()
        self.assertEqual(first["source"], "RENDER_RESULT")
        self.assertEqual((first["width"], first["height"]), (64, 64))
        self.assertNotEqual(first["path"], second["path"])
        self.assertEqual(first["sha256"], second["sha256"])
        red, green, blue = self.color(first["path"])
        self.assertGreater(red, 0.9)
        self.assertLess(max(green, blue), 0.01)
        self.assertEqual(self.state(), before)
        self.assertEqual(bpy.data.images["Render Result"].filepath, "")

    def test_missing_render_result_and_options_refuse_before_outbox(self):
        image = bpy.data.images.get("Render Result")
        if image:
            bpy.data.images.remove(image)
        before = self.state()
        with patch.object(self.tools, "_still_outbox") as outbox:
            with self.assertRaisesRegex(ValueError, "Render Result"):
                self.tools.snapshot_render_result({})
            with self.assertRaisesRegex(ValueError, "no options"):
                self.tools.snapshot_render_result({"render": True})
        outbox.assert_not_called()
        self.assertEqual(self.state(), before)

    def test_invalid_capture_values_refuse_before_outbox_or_scene_mutation(self):
        bpy.ops.object.camera_add()
        camera = bpy.context.view_layer.objects.active
        before = self.state()
        for values in (
            {"width": True},
            {"width": 0},
            {"width": 3841},
            {"height": 2161},
            {"width": "1280"},
            {"source": "RENDER_RESULT"},
            {"grey_clay": "yes"},
            {"camera_name": "Missing"},
            {"camera_name": 123},
            {"source": "VIEWPORT", "camera_name": camera.name},
            {"path": "arbitrary.png"},
        ):
            with self.subTest(values=values), patch.object(self.tools, "_still_outbox") as outbox:
                with self.assertRaises(ValueError):
                    self.tools.render_still(values)
                outbox.assert_not_called()
                self.assertEqual(self.state(), before)

    def test_headless_capture_and_worker_thread_refuse(self):
        if bpy.app.background:
            with self.assertRaisesRegex(RuntimeError, "GUI"):
                self.tools.render_still({"source": "VIEWPORT"})
        with ThreadPoolExecutor(max_workers=1) as executor:
            for handler in (self.tools.render_still, self.tools.snapshot_render_result):
                with self.assertRaisesRegex(RuntimeError, "main thread"):
                    executor.submit(handler, {}).result(timeout=10)
        self.assertEqual(list(self.root.iterdir()), [])

    def test_render_result_failure_restores_settings_and_partial_output_is_removed(self):
        self.existing_render()
        before = self.state()
        with patch.object(
            self.tools, "_still_payload", side_effect=TimeoutError("Interrupted snapshot")
        ):
            with self.assertRaises(TimeoutError):
                self.tools.snapshot_render_result({})
        self.assertEqual(self.state(), before)
        fixture = FIXTURES / "synthetic/reference-toadstool-512.png"
        for draw in (
            lambda path: path.write_bytes(b"not PNG"),
            lambda path: shutil.copyfile(fixture, path),
        ):
            with self.assertRaises((ValueError, RuntimeError)):
                self.tools._still_payload(draw, source="VIEWPORT", expected_size=(64, 64))
            self.assertEqual(list(self.root.iterdir()), [])

    @unittest.skipIf(bpy.app.background, "Real OpenGL stills require the isolated GUI")
    def test_real_grey_clay_and_camera_stills_restore_view_and_failure_state(self):
        bpy.ops.mesh.primitive_cube_add()
        cube = bpy.context.view_layer.objects.active
        cube.color = (1, 0.01, 0.01, 1)
        bpy.ops.object.camera_add(location=(4, -6, 3))
        camera = bpy.context.view_layer.objects.active
        camera.rotation_euler = (
            (Vector((0, 0, 0)) - camera.location).to_track_quat("-Z", "Y").to_euler()
        )
        self.scene.camera = None
        for window in bpy.context.window_manager.windows:
            for area in window.screen.areas:
                if area.type == "VIEW_3D":
                    space = area.spaces.active
                    space.shading.type, space.shading.color_type, space.shading.light = (
                        "SOLID",
                        "OBJECT",
                        "FLAT",
                    )
                    space.shading.single_color = (0.9, 0.1, 0.3)
        before = self.state()
        options = dict(source="CAMERA", camera_name=camera.name, width=128, height=96)
        colored = self.metadata(self.tools.render_still(options))
        grey = self.metadata(self.tools.render_still(dict(options, grey_clay=True)))
        self.assertEqual(self.state(), before)
        red, green, blue = self.color(colored["path"])
        self.assertGreater(red, max(green, blue) + 0.4)
        values = self.color(grey["path"])
        self.assertLess(max(values) - min(values), 0.01)
        self.assertGreater(min(values), 0.1)
        self.assertTrue(grey["grey_clay"])
        directories = set(self.root.iterdir())

        def interrupted(kind, context, scene):
            Path(scene.render.filepath).write_bytes(b"partial")
            scene.frame_set(90)
            raise TimeoutError("Interrupted still")

        with patch.object(self.capture, "_default_runner", side_effect=interrupted):
            with self.assertRaises(TimeoutError):
                self.tools.render_still(dict(options, grey_clay=True))
        self.assertEqual(self.state(), before)
        self.assertEqual(set(self.root.iterdir()), directories)
