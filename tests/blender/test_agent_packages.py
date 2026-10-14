# SPDX-FileCopyrightText: 2026 Open Studio contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Real package import and point-cloud handoffs in the installed extension."""

import hashlib
import json
import shutil
import struct
import tempfile
import unittest
import zlib
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

import bpy
from helpers import (
    reset_scene,
    splat_ply_bytes,
    splat_records,
    spz_bytes,
    submodule,
    textured_model_exports,
)


class AgentPackageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        reset_scene()
        cls.temporary = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.temporary.cleanup)
        cls.exports = textured_model_exports(Path(cls.temporary.name))

    def setUp(self):
        reset_scene()
        self.tools = submodule("mcp.tools_agent_packages")
        self.inbox = submodule("mcp.tools_agent")._inbox()
        self.copied = []
        self.scene = bpy.context.scene
        self.scene.render.fps = 30
        self.scene.frame_set(9)
        self.original = bpy.data.objects.new("Keep existing", None)
        self.scene.collection.objects.link(self.original)
        self.original.select_set(True)
        bpy.context.view_layer.objects.active = self.original

    def tearDown(self):
        for path in self.copied:
            path.unlink(missing_ok=True)
        reset_scene()

    def copy(self, key, role=None):
        source = self.exports[key]
        target = self.inbox / source.name
        shutil.copyfile(source, target)
        self.copied.append(target)
        return {"filename": target.name, **({"role": role} if role else {})}

    def assert_model(self, result):
        self.assertTrue(result["created_new_group"])
        self.assertIn(self.original, tuple(self.scene.objects))
        self.assertEqual(bpy.context.view_layer.objects.active, self.original)
        self.assertEqual(set(bpy.context.selected_objects), {self.original})
        self.assertEqual((self.scene.render.fps, self.scene.frame_current), (30, 9))
        meshes = [
            bpy.data.objects[name]
            for name in result["objects"]
            if bpy.data.objects[name].type == "MESH"
        ]
        self.assertTrue(meshes)
        return meshes

    def test_gltf_resources_are_explicit_packed_and_survive_inbox_removal(self):
        args = self.copy("gltf")
        args["resources"] = [self.copy("bin"), self.copy("png")]
        result = self.tools.import_agent_model_package(args)
        meshes = self.assert_model(result)
        material = meshes[0].data.materials[0]
        image = next(node.image for node in material.node_tree.nodes if node.type == "TEX_IMAGE")
        digest, image_name = hashlib.sha256(image.packed_file.data).hexdigest(), image.name
        for path in self.copied:
            path.unlink()
        with tempfile.TemporaryDirectory() as directory:
            blend = Path(directory) / "package.blend"
            bpy.ops.wm.save_as_mainfile(filepath=str(blend), check_existing=False)
            bpy.ops.wm.open_mainfile(filepath=str(blend))
            self.assertEqual(
                hashlib.sha256(bpy.data.images[image_name].packed_file.data).hexdigest(), digest
            )
            self.assertIn(result["collection"], bpy.data.collections)
            reset_scene()

    def test_same_stem_buffer_and_image_do_not_conflict(self):
        args = self.copy("gltf")
        document = json.loads((self.inbox / args["filename"]).read_text())
        old_image = document["images"][0]["uri"]
        document["images"][0]["uri"] = "cube.png"
        (self.inbox / args["filename"]).write_text(json.dumps(document))
        image_path = self.inbox / "cube.png"
        shutil.copyfile(self.exports["png"], image_path)
        self.copied.append(image_path)
        args["resources"] = [self.copy("bin"), {"filename": "cube.png"}]
        self.assertNotEqual(old_image, "cube.png")
        self.assert_model(self.tools.import_agent_model_package(args))

    def test_obj_explicit_material_and_roles_are_packed(self):
        args = self.copy("obj")
        args["resources"] = [self.copy("mtl"), self.copy("png", "base_color")]
        result = self.tools.import_agent_model_package(args)
        meshes = self.assert_model(result)
        images = [
            node.image
            for node in meshes[0].data.materials[0].node_tree.nodes
            if node.type == "TEX_IMAGE"
        ]
        self.assertTrue(images)
        self.assertTrue(all(image.packed_file for image in images))

    def test_embedded_fbx_retains_animation_and_reports_fps_mismatch(self):
        from bpy_extras import image_utils

        calls = []
        loader = image_utils.load_image
        snapshot_root = Path(self.tools.model_application._model_root()).resolve()

        def confined_loader(filepath, *args, **kwargs):
            path = Path(filepath).resolve()
            calls.append(path)
            self.assertTrue(path.is_relative_to(snapshot_root))
            self.assertTrue(path.relative_to(snapshot_root).parts[0].startswith("package-"))
            self.assertFalse(path.exists(), "Embedded FBX must never read a source image")
            return loader(filepath, *args, **kwargs)

        with patch.object(image_utils, "load_image", side_effect=confined_loader):
            result = self.tools.import_agent_model_package(self.copy("fbx"))
        self.assertTrue(calls, "The real FBX importer must exercise its texture loader")
        meshes = self.assert_model(result)
        self.assertTrue(any(mesh.animation_data for mesh in meshes))
        self.assertTrue(result["frame_rate_mismatch"])
        images = [
            node.image
            for node in meshes[0].data.materials[0].node_tree.nodes
            if node.type == "TEX_IMAGE"
        ]
        self.assertTrue(images)
        self.assertEqual(bytes(images[0].packed_file.data), self.exports["png"].read_bytes())

    def test_package_native_undo_redo_keeps_final_names_and_digest(self):
        previous = bpy.context.preferences.edit.use_global_undo
        bpy.context.preferences.edit.use_global_undo = True
        try:
            bpy.ops.ed.undo_push(message="Before assistant package")
            result = self.tools.import_agent_model_package(
                {**self.copy("ply"), "name": "Undo mesh"}
            )
            self.assertEqual(result["collection"], "Open Studio Undo mesh")
            self.assertEqual(bpy.ops.ed.undo(), {"FINISHED"})
            self.assertNotIn(result["collection"], bpy.data.collections)
            self.assertEqual(bpy.ops.ed.redo(), {"FINISHED"})
            self.assertIn(result["collection"], bpy.data.collections)
            self.assertEqual(
                bpy.data.objects[result["root"]]["open_studio_model_sha256"], result["sha256"]
            )
        finally:
            bpy.context.preferences.edit.use_global_undo = previous

    def test_mesh_ply_imports_with_geometry(self):
        meshes = self.assert_model(self.tools.import_agent_model_package(self.copy("ply")))
        self.assertGreaterEqual(len(meshes[0].data.vertices), 8)
        self.assertGreater(len(meshes[0].data.polygons), 0)

    def test_native_staging_failure_rolls_back_all_new_data_without_marking(self):
        model = self.tools.model_application
        before = model._snapshot(model._PACKAGE_TRACKED)
        with (
            patch.object(model, "_pack_snapshot_images", side_effect=RuntimeError("After import")),
            patch.object(self.tools.file_state, "mark_modified") as mark,
            self.assertRaises(model.ModelApplicationError),
        ):
            self.tools.import_agent_model_package(self.copy("ply"))
        self.assertEqual(model._snapshot(model._PACKAGE_TRACKED), before)
        mark.assert_not_called()

    def test_missing_external_and_ambiguous_resources_refused_before_publication(self):
        args = self.copy("gltf")
        before = set(bpy.data.objects)
        with self.assertRaises(ValueError):
            self.tools.import_agent_model_package(args)
        document = json.loads((self.inbox / args["filename"]).read_text())
        document["buffers"][0]["uri"] = "https://example.invalid/private.bin"
        (self.inbox / args["filename"]).write_text(json.dumps(document))
        with self.assertRaisesRegex(ValueError, "relative"):
            self.tools.import_agent_model_package(args)
        args = self.copy("obj")
        args["resources"] = [self.copy("png")]
        with self.assertRaisesRegex(ValueError, "role-labeled"):
            self.tools.import_agent_model_package(args)
        self.assertEqual(set(bpy.data.objects), before)

    def test_external_fbx_texture_is_refused_before_native_image_access(self):
        from bpy_extras import image_utils

        before = set(bpy.data.objects), set(bpy.data.images)
        with patch.object(image_utils, "load_image") as loader, self.assertRaises(ValueError):
            self.tools.import_agent_model_package(self.copy("external_fbx"))
        loader.assert_not_called()
        self.assertEqual((set(bpy.data.objects), set(bpy.data.images)), before)

    def test_embedded_fbx_unsafe_paths_are_refused_before_native_image_access(self):
        from bpy_extras import image_utils

        args = self.copy("fbx")
        path = self.inbox / args["filename"]
        original = path.read_bytes()
        self.assertIn(b"tex.png", original)
        before = set(bpy.data.objects), set(bpy.data.images)
        for filename in (b"../xpng", b"/xx.png", b"C:x.png", b"NUL.png"):
            with self.subTest(filename=filename):
                # Equal lengths retain every binary FBX record offset.
                path.write_bytes(original.replace(b"tex.png", filename))
                with (
                    patch.object(image_utils, "load_image") as loader,
                    self.assertRaises(ValueError),
                ):
                    self.tools.import_agent_model_package(args)
                loader.assert_not_called()
                self.assertEqual((set(bpy.data.objects), set(bpy.data.images)), before)

    def test_fbx_record_and_expanded_array_bounds_fail_before_import(self):
        def document(property_bytes):
            end = 27 + 13 + 1 + len(property_bytes)
            return (
                b"Kaydara FBX Binary  \0\x1a\0"
                + struct.pack("<I", 7400)
                + struct.pack("<IIIB", end, 1, len(property_bytes), 1)
                + b"A"
                + property_bytes
                + bytes(13)
            )

        limit = self.tools.model_formats.MAX_PACKAGE_BYTES
        compressed = zlib.compress(bytes(16))
        bad_arrays = [
            b"f" + struct.pack("<III", limit // 4 + 1, 1, len(compressed)) + compressed,
            b"f" + struct.pack("<III", 1, 1, len(compressed)) + compressed,
            b"R" + struct.pack("<I", 1024) + b"short",
        ]
        for body in bad_arrays:
            with self.subTest(body=body), self.assertRaises(ValueError):
                self.tools._embedded_fbx(document(body))

    def test_splat_formats_decode_offthread_and_publish_persistent_points(self):
        points = [(0, 0, 0), (1, 2, 3), (2, 4, 6)]
        data = {
            "spz": spz_bytes(points),
            "splat": splat_records(points),
            "ply": splat_ply_bytes([(*point, 0, 0, 0, 3, -2, -2, -2) for point in points]),
        }
        for suffix, content in data.items():
            with self.subTest(format=suffix):
                path = self.inbox / ("points." + suffix)
                path.write_bytes(content)
                self.copied.append(path)
                deferred = self.tools.import_agent_splat(
                    {"filename": path.name, "max_points": 2, "axes": "OPENGL"}
                )
                with ThreadPoolExecutor(1) as pool:
                    decoded = pool.submit(deferred.run).result(timeout=10)
                result = deferred.finish(decoded)
                self.assertEqual((result["points_total"], result["points_kept"]), (3, 2))
                obj = bpy.data.objects[result["objects"][0]]
                self.assertEqual(len(obj.data.vertices), 2)
                self.assertIn("splat_color", obj.data.attributes)
                self.assertEqual(obj.modifiers[0].type, "NODES")
                path.unlink()
        name = obj.name
        with tempfile.TemporaryDirectory() as directory:
            blend = Path(directory) / "points.blend"
            bpy.ops.wm.save_as_mainfile(filepath=str(blend), check_existing=False)
            bpy.ops.wm.open_mainfile(filepath=str(blend))
            self.assertEqual(len(bpy.data.objects[name].data.vertices), 2)
            reset_scene()

    def test_splat_invalid_version_and_changed_scene_do_not_publish(self):
        path = self.inbox / "invalid.spz"
        self.copied.append(path)
        path.write_bytes(spz_bytes([(0, 0, 0)], version=4))
        deferred = self.tools.import_agent_splat({"filename": path.name})
        with self.assertRaises(ValueError):
            deferred.run()
        path.write_bytes(spz_bytes([(0, 0, 0)]))
        deferred = self.tools.import_agent_splat({"filename": path.name})
        decoded = deferred.run()
        other = bpy.data.scenes.new("Other scene")
        try:
            bpy.context.window.scene = other
            with self.assertRaisesRegex(ValueError, "scene changed"):
                deferred.finish(decoded)
            self.assertEqual(len(other.objects), 0)
        finally:
            bpy.context.window.scene = self.scene
            bpy.data.scenes.remove(other)
