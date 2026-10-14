# SPDX-FileCopyrightText: 2026 Open Studio contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Saved local assets survive source deletion and reuse through explicit import tools."""

import base64
import hashlib
import json
import shutil
import struct
import tempfile
import threading
import unittest
import zlib
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

import bpy
from helpers import FIXTURES, reset_scene, submodule


class AgentResultsTests(unittest.TestCase):
    def setUp(self):
        reset_scene()
        self.tools = submodule("mcp.tools_agent_results")
        self.images = submodule("mcp.tools_agent")
        self.temp = tempfile.TemporaryDirectory(prefix="os-results-")
        self.root = Path(self.temp.name)
        self.library, self.inbox = self.root / "library", self.root / "inbox"
        self.library.mkdir()
        self.inbox.mkdir()
        self.patches = [
            patch.object(self.tools, "_library", return_value=self.library),
            patch.object(self.tools, "_inbox", return_value=self.inbox),
            patch.object(self.images, "_inbox", return_value=self.inbox),
        ]
        for context in self.patches:
            context.start()
        self.source = self.inbox / "copper.png"
        shutil.copyfile(FIXTURES / "patina-copper-512" / "albedo.png", self.source)

    def tearDown(self):
        reset_scene()
        for context in reversed(self.patches):
            context.stop()
        self.temp.cleanup()

    def finish(self, value):
        deferred = submodule("mcp.protocol").DeferredTool
        while isinstance(value, deferred):
            value = value.finish(value.run())
        return value

    def save(self):
        return self.finish(
            self.tools.save_agent_result(
                {
                    "title": "Copper concept",
                    "kind": "IMAGE",
                    "description": "Fixture supplied locally",
                    "files": [
                        {
                            "filename": self.source.name,
                            "sha256": hashlib.sha256(self.source.read_bytes()).hexdigest(),
                        }
                    ],
                }
            )
        )

    def test_saved_image_survives_reopen_and_explicit_reuse(self):
        result = self.save()
        self.source.unlink()
        bpy.ops.wm.save_as_mainfile(filepath=str(self.root / "empty.blend"), check_existing=False)
        bpy.ops.wm.open_mainfile(filepath=str(self.root / "empty.blend"))
        self.assertEqual(len(bpy.context.scene.objects), 0)
        listing = self.finish(self.tools.list_agent_results({"search": "copper"}))
        self.assertEqual(listing["results"][0]["result_id"], result["result_id"])
        inspected = self.finish(self.tools.inspect_agent_result({"result_id": result["result_id"]}))
        self.assertTrue(inspected["verified"])
        prepared = self.finish(self.tools.prepare_agent_result({"result_id": result["result_id"]}))
        self.assertFalse(prepared["scene_changed"])
        self.assertEqual(len(bpy.context.scene.objects), 0)
        args = {**prepared["import_recipe"]["arguments"], "mode": "REFERENCE"}
        imported = self.images.import_agent_image(args)
        self.assertTrue(bpy.data.images[imported["image"]].packed_file)
        self.assertEqual(imported["sha256"], result["asset"]["files"][0]["sha256"])

    def test_preview_returns_verified_pixels_without_scene_datablocks(self):
        result = self.save()
        before = tuple(len(items) for items in (bpy.data.images, bpy.data.objects, bpy.data.scenes))
        content = self.finish(
            self.tools.inspect_agent_result(
                {
                    "result_id": result["result_id"],
                    "preview_file": self.source.name,
                }
            )
        )
        metadata = json.loads(content[0]["text"])
        self.assertTrue(metadata["preview_file_verified"])
        self.assertEqual(metadata["preview"]["width"], 512)
        self.assertEqual(content[1]["mimeType"], "image/png")
        self.assertTrue(base64.b64decode(content[1]["data"]).startswith(b"\x89PNG\r\n\x1a\n"))
        self.assertEqual(
            before,
            tuple(len(items) for items in (bpy.data.images, bpy.data.objects, bpy.data.scenes)),
        )
        self.assertFalse(list(self.library.glob("result-preview-*")))

    def test_damaged_saved_image_is_refused_before_preview_or_import(self):
        result = self.save()
        saved = self.library / result["result_id"] / "files" / self.source.name
        saved.write_bytes(b"changed")
        before = len(bpy.data.images), len(bpy.data.objects)
        for method, extra in (
            (self.tools.inspect_agent_result, {}),
            (self.tools.inspect_agent_result, {"preview_file": self.source.name}),
            (self.tools.prepare_agent_result, {}),
        ):
            with self.assertRaises(ValueError):
                self.finish(method({"result_id": result["result_id"], **extra}))
        self.assertEqual(before, (len(bpy.data.images), len(bpy.data.objects)))

    def test_reusable_model_is_retained_and_imported_from_original_bytes(self):
        models = submodule("mcp.tools_agent_models")
        source = self.inbox / "triangle.glb"
        shutil.copyfile(FIXTURES / "synthetic" / "static-triangle.glb", source)
        saved = self.finish(
            self.tools.save_agent_result(
                {
                    "title": "Triangle",
                    "kind": "MODEL",
                    "files": [
                        {
                            "filename": source.name,
                            "sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
                        }
                    ],
                }
            )
        )
        source.unlink()
        result = self.finish(self.tools.prepare_agent_result({"result_id": saved["result_id"]}))
        with patch.object(models, "_inbox", return_value=self.inbox):
            imported = models.import_agent_model(result["import_recipe"]["arguments"])
        self.assertTrue(imported["created_new_group"])
        self.assertTrue(imported["objects"])
        self.assertEqual(imported["sha256"], saved["asset"]["files"][0]["sha256"])

    def test_malformed_preview_refuses_without_mutation(self):
        self.source.write_bytes(b"invalid image bytes")
        saved = self.save()
        before = len(bpy.data.images), len(bpy.data.objects)
        with self.assertRaisesRegex(ValueError, "preview layout"):
            self.finish(
                self.tools.inspect_agent_result(
                    {
                        "result_id": saved["result_id"],
                        "preview_file": self.source.name,
                    }
                )
            )
        self.assertEqual(before, (len(bpy.data.images), len(bpy.data.objects)))

    def current_state(self):
        scene, layer = bpy.context.scene, bpy.context.view_layer
        editor = scene.sequence_editor
        return {
            "file": bpy.data.filepath,
            "dirty": bpy.data.is_dirty,
            "scene": (scene.name, scene.as_pointer()),
            "frame": (
                scene.frame_current,
                scene.frame_subframe,
                scene.frame_start,
                scene.frame_end,
            ),
            "render": (
                scene.render.fps,
                scene.render.fps_base,
                scene.render.resolution_x,
                scene.render.resolution_y,
            ),
            "active": layer.objects.active.name if layer.objects.active else None,
            "objects": sorted(
                (obj.name, tuple(obj.location), tuple(obj.scale), obj.select_get(view_layer=layer))
                for obj in layer.objects
            ),
            "strips": [
                (
                    strip.name,
                    strip.channel,
                    strip.frame_final_start,
                    strip.frame_final_end,
                    strip.select,
                )
                for strip in editor.strips
            ]
            if editor
            else [],
            "datablocks": {
                name: sorted((item.name, item.as_pointer()) for item in getattr(bpy.data, name))
                for name in (
                    "scenes",
                    "objects",
                    "collections",
                    "images",
                    "meshes",
                    "materials",
                    "actions",
                    "worlds",
                    "sounds",
                )
            },
        }

    def test_real_worker_preview_finishes_after_file_reload_without_touching_current_scene(self):
        main_thread = threading.get_ident()
        deferred = self.tools.save_agent_result(
            {
                "title": "Worker-retained copper",
                "kind": "IMAGE",
                "files": [
                    {
                        "filename": self.source.name,
                        "sha256": hashlib.sha256(self.source.read_bytes()).hexdigest(),
                    }
                ],
            }
        )

        def run(operation):
            return threading.get_ident(), operation.run()

        with ThreadPoolExecutor(max_workers=1) as executor:
            worker, completed = executor.submit(run, deferred).result(timeout=10)
            self.assertNotEqual(worker, main_thread)
            saved = deferred.finish(completed)
            preview = self.tools.inspect_agent_result(
                {
                    "result_id": saved["result_id"],
                    "preview_file": self.source.name,
                }
            )
            preview_thread, payload = executor.submit(run, preview).result(timeout=10)
        self.assertNotEqual(preview_thread, main_thread)
        # Delivery occurs after a real file load, with a different current scene.
        original_scene = bpy.context.scene.name
        current = bpy.data.scenes.new("Current file after preview request")
        bpy.context.window.scene = current
        bpy.ops.mesh.primitive_cube_add(location=(3, 4, 5))
        bpy.context.view_layer.objects.active.name = "Keep current selection"
        current.frame_start, current.frame_end = 11, 71
        current.frame_set(37, subframe=0.5)
        current.render.fps = 30
        strip = current.sequence_editor_create().strips.new_effect(
            "Keep current strip", "COLOR", 3, 11, length=40
        )
        strip.select = True
        current.sequence_editor.active_strip = strip
        blend = self.root / "current.blend"
        bpy.ops.wm.save_as_mainfile(filepath=str(blend), check_existing=False)
        bpy.ops.wm.open_mainfile(filepath=str(blend))
        self.assertNotEqual(bpy.context.scene.name, original_scene)
        self.source.unlink()
        before = self.current_state()
        self.assertEqual(threading.get_ident(), main_thread)
        content = preview.finish(payload)
        self.assertEqual(self.current_state(), before)
        metadata = json.loads(content[0]["text"])
        self.assertEqual(metadata["result_id"], saved["result_id"])
        self.assertTrue(metadata["preview_file_verified"])
        self.assertEqual(metadata["preview"]["width"], 512)
        self.assertTrue(base64.b64decode(content[1]["data"]).startswith(b"\x89PNG\r\n\x1a\n"))
        self.assertFalse(list(self.library.glob("result-preview-*")))

    def test_corrupt_png_payload_reaches_native_decoder_and_cleans_private_copy(self):
        def chunk(kind, data):
            return (
                struct.pack(">I", len(data))
                + kind
                + data
                + struct.pack(">I", zlib.crc32(kind + data))
            )

        # Preserve the complete valid IHDR, including its CRC. Only the IDAT's
        # compressed payload is invalid, so admission reaches the native codec.
        corrupted = (
            self.source.read_bytes()[:33]
            + chunk(b"IDAT", b"not a zlib stream")
            + chunk(b"IEND", b"")
        )
        self.assertEqual(self.tools.local_image_layout(corrupted), (512, 512, 4))
        self.source.write_bytes(corrupted)
        saved = self.save()
        deferred = self.tools.inspect_agent_result(
            {"result_id": saved["result_id"], "preview_file": self.source.name}
        )
        with ThreadPoolExecutor(max_workers=1) as executor:
            payload = executor.submit(deferred.run).result(timeout=10)
        before = self.current_state()
        decode = self.tools.preview_decode.decode
        seen = []

        def observed(path, *args, **kwargs):
            path = Path(path)
            self.assertEqual(path.parent.parent, self.library)
            self.assertTrue(path.parent.name.startswith("result-preview-"))
            self.assertEqual(path.read_bytes(), corrupted)
            seen.append(path)
            return decode(path, *args, **kwargs)

        with patch.object(self.tools.preview_decode, "decode", side_effect=observed) as native:
            with self.assertRaises(self.tools.preview_decode.PreviewDecodeError):
                deferred.finish(payload)
        native.assert_called_once()
        self.assertEqual(self.current_state(), before)
        self.assertTrue(seen)
        self.assertFalse(seen[0].exists())
        self.assertFalse(list(self.library.glob("result-preview-*")))
