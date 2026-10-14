# SPDX-FileCopyrightText: 2026 Open Studio contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Real assistant GLB handoffs, including packed textures, rigs and clip persistence."""

import hashlib
import json
import struct
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

import bpy
from helpers import FIXTURES, animated_glb, reset_scene, submodule
from mathutils import Vector


def _external_texture(data):
    length = struct.unpack_from("<I", data, 12)[0]
    document = json.loads(data[20 : 20 + length])
    document["images"][0] = {"uri": "https://example.invalid/texture.png"}
    encoded = json.dumps(document).encode()
    encoded += b" " * (-len(encoded) % 4)
    binary_chunk = data[20 + length :]
    return (
        struct.pack("<4sII", b"glTF", 2, 20 + len(encoded) + len(binary_chunk))
        + struct.pack("<I4s", len(encoded), b"JSON")
        + encoded
        + binary_chunk
    )


class AgentModelTests(unittest.TestCase):
    def setUp(self):
        reset_scene()
        self.tools = submodule("mcp.tools_agent_models")
        self.application = submodule("blender.model_application")
        self.inbox = submodule("mcp.tools_agent")._inbox()
        self.path = self.inbox / "assistant-model-test.glb"
        self.path.write_bytes((FIXTURES / "synthetic/static-triangle.glb").read_bytes())
        self.scene = bpy.context.scene
        self.existing = bpy.data.objects.new("Preserve this selection", None)
        self.scene.collection.objects.link(self.existing)
        self.existing.select_set(True)
        bpy.context.view_layer.objects.active = self.existing

    def tearDown(self):
        self.path.unlink(missing_ok=True)
        reset_scene()

    def _call(self, **kwargs):
        return self.tools.import_agent_model({"filename": self.path.name, **kwargs})

    def _snapshot(self):
        return {name: set(getattr(bpy.data, name)) for name in self.application._TRACKED_DATA}

    def _assert_packed(self, mesh):
        material = mesh.data.materials[0]
        image = next(node.image for node in material.node_tree.nodes if node.type == "TEX_IMAGE")
        self.assertIsNotNone(image.packed_file)
        self.assertTrue(image.packed_file.data)
        self.assertEqual(image.filepath, "")
        return image

    def test_textured_hierarchy_is_packed_at_cursor_without_selection_changes(self):
        self.scene.cursor.location = (4, 5, 6)
        result = self._call(
            name="Assistant chair", sha256=hashlib.sha256(self.path.read_bytes()).hexdigest()
        )
        collection = bpy.data.collections[result["collection"]]
        root = bpy.data.objects[result["root"]]
        mesh = next(
            bpy.data.objects[name]
            for name in result["objects"]
            if bpy.data.objects[name].type == "MESH"
        )
        self.assertEqual(root.name, "Open Studio Assistant chair")
        self.assertEqual(collection.name, "Open Studio Assistant chair")
        self.assertEqual(mesh.parent.parent, root)
        self._assert_packed(mesh)
        points = [mesh.matrix_world @ Vector(corner) for corner in mesh.bound_box]
        low = [min(point[index] for point in points) for index in range(3)]
        high = [max(point[index] for point in points) for index in range(3)]
        self.assertEqual(((low[0] + high[0]) / 2, (low[1] + high[1]) / 2, low[2]), (4, 5, 6))
        self.assertEqual(set(bpy.context.selected_objects), {self.existing})
        self.assertEqual(bpy.context.view_layer.objects.active, self.existing)
        self.assertTrue(result["created_new_group"])
        self.assertFalse(submodule("prefs").get_prefs().mcp_allow_python)

    def test_explicit_location_overrides_cursor_and_repeat_creates_new_group(self):
        self.scene.cursor.location = (99, 99, 99)
        first = self._call(location=[1, 2, 3])
        second = self._call(location=[1, 2, 3])
        self.assertNotEqual(first["collection"], second["collection"])
        self.assertNotEqual(first["root"], second["root"])
        self.assertEqual(first["location"], [1, 2, 3])
        self.assertEqual(tuple(self.scene.cursor.location), (99, 99, 99))
        self.assertTrue(set(first["objects"]).isdisjoint(second["objects"]))
        self.assertFalse(self.tools.SPECS[0].annotations["idempotentHint"])

    def test_skin_weights_morphs_and_multiple_clips_keep_destination_timing(self):
        self.path.write_bytes(animated_glb())
        self.scene.render.fps, self.scene.render.fps_base = 30, 1.001
        self.scene.frame_start, self.scene.frame_end = 12, 160
        self.scene.frame_set(7, subframe=0.25)
        result = self._call(location=[4, 5, 6])
        objects = [bpy.data.objects[name] for name in result["objects"]]
        rig = next(obj for obj in objects if obj.type == "ARMATURE")
        mesh = next(obj for obj in objects if obj.type == "MESH")
        self.assertEqual(len(rig.data.bones), 2)
        self.assertEqual(rig.data.bones["TipJoint"].parent.name, "RootJoint")
        self.assertEqual(mesh.modifiers[0].object, rig)
        self.assertEqual(mesh.vertex_groups[0].name, "RootJoint")
        self.assertEqual(mesh.data.vertices[0].groups[0].weight, 1)
        self.assertEqual(len(mesh.data.shape_keys.key_blocks), 2)
        for owner in (rig, mesh.data.shape_keys):
            self.assertEqual(len(owner.animation_data.nla_tracks), 2)
            for track in owner.animation_data.nla_tracks:
                self.assertAlmostEqual(track.strips[0].action.frame_range[1], 30 / 1.001, places=3)
        self.assertEqual((self.scene.frame_start, self.scene.frame_end), (12, 160))
        self.assertEqual(self.scene.frame_current, 7)
        self.assertAlmostEqual(self.scene.frame_subframe, 0.25)
        self.assertEqual(self.scene.render.fps, 30)
        self.assertAlmostEqual(self.scene.render.fps_base, 1.001, places=5)
        self.assertEqual(bpy.context.view_layer.objects.active, self.existing)
        self.scene.frame_set(0)
        first = mesh.evaluated_get(bpy.context.evaluated_depsgraph_get()).data.vertices[0].co.copy()
        self.scene.frame_set(30)
        last = mesh.evaluated_get(bpy.context.evaluated_depsgraph_get()).data.vertices[0].co.copy()
        self.assertAlmostEqual(last.x - first.x, 2, places=4)
        self.assertGreater((last - first).length, 2)

    def test_rig_textures_and_clips_survive_source_removal_and_save_reopen(self):
        self.path.write_bytes(animated_glb())
        result = self._call(name="Saved character")
        objects = [bpy.data.objects[name] for name in result["objects"]]
        rig_name = next(obj.name for obj in objects if obj.type == "ARMATURE")
        mesh_name = next(obj.name for obj in objects if obj.type == "MESH")
        digest = hashlib.sha256(
            self._assert_packed(bpy.data.objects[mesh_name]).packed_file.data
        ).hexdigest()
        self.path.unlink()
        with tempfile.TemporaryDirectory(prefix="assistant-model-") as directory:
            destination = Path(directory) / "character.blend"
            bpy.ops.wm.save_as_mainfile(filepath=str(destination), check_existing=False)
            bpy.ops.wm.open_mainfile(filepath=str(destination))
            mesh, rig = bpy.data.objects[mesh_name], bpy.data.objects[rig_name]
            self.assertEqual(mesh.modifiers[0].object, rig)
            self.assertEqual(len(mesh.data.shape_keys.key_blocks), 2)
            self.assertEqual(len(rig.animation_data.nla_tracks), 2)
            self.assertEqual(
                hashlib.sha256(self._assert_packed(mesh).packed_file.data).hexdigest(), digest
            )
            self.assertIn(result["root"], bpy.data.collections[result["collection"]].objects)
            reset_scene()

    def test_corrupt_or_external_resource_glb_refused_before_native_import(self):
        original = self.path.read_bytes()
        for data in (b"glTFcorrupt", _external_texture(original)):
            with self.subTest(data=data[:12]):
                self.path.write_bytes(data)
                before = self._snapshot()
                with (
                    patch.object(self.application, "_import") as importer,
                    self.assertRaises(ValueError),
                ):
                    self._call()
                importer.assert_not_called()
                self.assertEqual(self._snapshot(), before)

    def test_invalid_name_location_and_digest_leave_scene_untouched(self):
        before = self._snapshot()
        for options in (
            {"name": ""},
            {"name": "x" * 129},
            {"location": [1, 2]},
            {"location": [True, 2, 3]},
            {"location": [float("nan"), 2, 3]},
            {"sha256": "0" * 64},
        ):
            with self.subTest(options=options), self.assertRaises(ValueError):
                self._call(**options)
            self.assertEqual(self._snapshot(), before)

    def test_native_failure_rolls_back_and_does_not_mark_modified(self):
        before = self._snapshot()
        original = self.application._publish

        def fail(*args):
            original(*args)
            raise RuntimeError("Synthetic failure after model publication")

        with (
            patch.object(self.application, "_publish", side_effect=fail),
            patch.object(self.tools.file_state, "mark_modified") as marked,
            self.assertRaises(self.application.ModelApplicationError),
        ):
            self._call()
        self.assertEqual(self._snapshot(), before)
        self.assertEqual(bpy.context.view_layer.objects.active, self.existing)
        marked.assert_not_called()

    def test_worker_refused_before_inbox_or_blender_import(self):
        with ThreadPoolExecutor(max_workers=1) as pool, patch.object(self.tools, "_inbox") as inbox:
            with self.assertRaises(ValueError):
                pool.submit(self._call).result(5)
            inbox.assert_not_called()
