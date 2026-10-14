# SPDX-FileCopyrightText: 2026 Open Studio contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Native assistant mesh roundtrip, stale targets and explicit application policies."""

import hashlib
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import bpy
from helpers import FIXTURES, animated_glb, parts_glb, reset_scene, submodule
from mathutils import Matrix


class AgentMeshTests(unittest.TestCase):
    def setUp(self):
        reset_scene()
        self.tools = submodule("mcp.tools_agent_mesh")
        self.owner = submodule("blender.assistant_mesh")
        self.mesh = submodule("blender.mesh_application")
        self.application = submodule("blender.mesh_result_application")
        self.model = submodule("blender.model_application")
        self.registered = self.owner._clear in bpy.app.handlers.load_pre
        self.owner.register()
        self.scene = bpy.context.scene
        data = bpy.data.meshes.new("Original geometry")
        data.from_pydata([(0, 0, 0), (3, 0, 0), (0, 3, 0)], [], [(0, 1, 2)])
        data.update()
        self.source = bpy.data.objects.new("Exact source", data)
        self.scene.collection.objects.link(self.source)
        self.other = bpy.data.objects.new("Preserve selection", None)
        self.scene.collection.objects.link(self.other)
        self.other.select_set(True)
        bpy.context.view_layer.objects.active = self.other
        self.inbox = submodule("mcp.tools_agent")._inbox()
        self.path = self.inbox / "mesh-edit-test.glb"
        self.path.write_bytes((FIXTURES / "synthetic/static-triangle.glb").read_bytes())

    def tearDown(self):
        self.owner._clear()
        if not self.registered:
            self.owner.unregister()
        self.path.unlink(missing_ok=True)
        reset_scene()

    def export(self):
        return self.tools.export_agent_mesh({"object_name": self.source.name})

    def apply(self, exported, policy="REMESH", matrix=None, keep_original=True):
        return self.tools.apply_agent_mesh(
            {
                "handle": exported["handle"],
                "filename": self.path.name,
                "sha256": hashlib.sha256(self.path.read_bytes()).hexdigest(),
                "policy": policy,
                "result_to_source": [list(row) for row in (matrix or Matrix.Identity(4))],
                "keep_original": keep_original,
            }
        )

    def matching_source(self):
        result = submodule("mcp.tools_agent_models").import_agent_model(
            {"filename": self.path.name}
        )
        primary = next(
            bpy.data.objects[name]
            for name in result["objects"]
            if bpy.data.objects[name].type == "MESH"
        )
        self.source.data = primary.data.copy()
        return self.source.data

    def blockout_source(self):
        blockout = submodule("blender.assistant_blockout")
        plan = {
            "name": "Mesh handoff",
            "elements": [
                {
                    "id": "wall",
                    "name": "Wall",
                    "primitive": "box",
                    "category": "wall",
                    "position": [0, 0, 0],
                    "size": [2, 2, 2],
                }
            ],
        }
        built = blockout.build(plan)
        self.source = next(obj for obj in self.scene.objects if obj.get(blockout.ELEMENT) == "wall")
        return blockout, plan, built

    def test_blockout_mesh_handoff_preserves_edit_guard_after_remesh(self):
        blockout, plan, built = self.blockout_source()
        original = self.source.data
        exported = self.export()
        self.assertTrue(blockout.inspect_plan()["editable"])
        self.assertEqual(self.apply(exported, keep_original=False)["policy"], "REMESH")
        self.assertNotEqual(self.source.data, original)
        self.assertFalse(blockout.inspect_plan()["editable"])
        before = self.model._snapshot()
        with self.assertRaises(ValueError):
            blockout.build(plan, built["revision"])
        with self.assertRaises(ValueError):
            blockout.clear(built["revision"])
        self.assertEqual(self.model._snapshot(), before)
        self.assertIn(self.other.name, self.scene.objects)

    def test_blockout_marker_opt_in_is_exact_and_default_callers_remain_strict(self):
        blockout, _, _ = self.blockout_source()
        with self.assertRaises(self.mesh.MeshApplicationError):
            self.mesh.capture_target(self.scene, self.source)
        self.source.data["Unrelated user property"] = "keep me"
        with self.assertRaises(self.mesh.MeshApplicationError):
            self.export()
        del self.source.data["Unrelated user property"]
        exported = self.export()
        owner = self.source.data[blockout.OWNER]
        self.source.data[blockout.OWNER] = "changed after export"
        with self.assertRaises(self.mesh.MeshApplicationError):
            self.apply(exported)
        self.source.data[blockout.OWNER] = owner
        del self.source.data[blockout.OWNER]
        with self.assertRaises(self.mesh.MeshApplicationError):
            self.apply(exported)
        self.source.data[blockout.OWNER] = owner
        self.mesh.validate_target(self.owner._exports[exported["handle"]].target)
        # A copied marker outside the scene's registered root grants no opt-in.
        foreign = bpy.data.objects.new("Foreign marked mesh", self.source.data.copy())
        self.scene.collection.objects.link(foreign)
        foreign[blockout.OWNER] = owner
        foreign[blockout.ELEMENT] = "wall"
        with self.assertRaises(self.mesh.MeshApplicationError):
            self.tools.export_agent_mesh({"object_name": foreign.name})

    def test_blockout_retexture_rollback_preserves_marker_and_recipe(self):
        blockout, _, _ = self.blockout_source()
        original = self.source.data
        exported = self.export()
        self.path.write_bytes(Path(exported["path"]).read_bytes())
        before = self.model._snapshot()
        with (
            patch.object(
                self.application,
                "_release_import",
                side_effect=RuntimeError("Synthetic cleanup failure"),
            ),
            self.assertRaises(self.application.MeshResultApplicationError),
        ):
            self.apply(exported, "RETEXTURE", keep_original=False)
        self.assertEqual(self.source.data, original)
        self.assertEqual(self.model._snapshot(), before)
        self.assertTrue(blockout.inspect_plan()["editable"])
        self.mesh.validate_target(self.owner._exports[exported["handle"]].target)

    def test_export_selects_exact_named_mesh_and_preserves_scene_context(self):
        parent = bpy.data.objects.new("Source parent", None)
        self.scene.collection.objects.link(parent)
        parent.location = (4, 5, 6)
        self.source.parent = parent
        self.source.location = (2, 3, 4)
        self.source.scale = (2, 3, 4)
        bpy.context.view_layer.update()
        target = self.mesh.capture_target(self.scene, self.source)
        exported = self.export()
        body = Path(exported["path"]).read_bytes()
        document = submodule("core.scene.glb").inspect_glb(body)
        self.assertEqual(len(document["meshes"]), 1)
        self.assertEqual(hashlib.sha256(body).hexdigest(), exported["sha256"])
        self.assertEqual(len(body), exported["bytes"])
        self.assertEqual(
            Matrix(exported["source_world_to_local"]), self.source.matrix_world.inverted()
        )
        self.mesh.validate_target(target)
        self.assertEqual(set(bpy.context.selected_objects), {self.other})
        self.assertEqual(bpy.context.view_layer.objects.active, self.other)
        self.assertFalse(submodule("prefs").get_prefs().mcp_allow_python)

    def test_remesh_keeps_original_transform_parent_selection_and_shared_mesh(self):
        alias = bpy.data.objects.new("Shared mesh owner", self.source.data)
        self.scene.collection.objects.link(alias)
        original = self.source.data
        self.source.location = (4, 5, 6)
        exported = self.export()
        transform = self.source.matrix_world.copy()
        result = self.apply(exported)
        self.assertEqual(alias.data, original)
        self.assertEqual(bpy.data.objects[result["original"]].data, original)
        self.assertNotEqual(self.source.data, original)
        self.assertEqual(self.source.matrix_world, transform)
        self.assertEqual(min(vertex.co.x for vertex in self.source.data.vertices), 2)
        self.assertEqual(bpy.context.view_layer.objects.active, self.other)
        self.assertEqual(set(bpy.context.selected_objects), {self.other})
        self.assertFalse(Path(exported["path"]).exists())
        self.assertTrue(result["handle_consumed"])
        self.assertFalse(result["restore_available"])
        with self.assertRaises(ValueError):
            self.apply(exported)

    def test_uv_replaces_only_uvs_with_explicit_coordinate_mapping(self):
        self.matching_source()
        expected = [tuple(loop.uv) for loop in self.source.data.uv_layers.active.data]
        material = bpy.data.materials.new("Keep source material")
        self.source.data.materials.clear()
        self.source.data.materials.append(material)
        for loop in self.source.data.uv_layers.active.data:
            loop.uv = (0.25, 0.25)
        geometry = [tuple(vertex.co) for vertex in self.source.data.vertices]
        result = self.apply(self.export(), "UV", Matrix.Translation((-2, 0, 0)))
        self.assertEqual(result["policy"], "UV")
        self.assertEqual([tuple(vertex.co) for vertex in self.source.data.vertices], geometry)
        self.assertEqual(
            [tuple(loop.uv) for loop in self.source.data.uv_layers.active.data], expected
        )
        self.assertEqual(list(self.source.data.materials), [material])

    def test_retexture_keeps_geometry_and_attributes_and_packs_material(self):
        self.matching_source()
        material = bpy.data.materials.new("Original material")
        self.source.data.materials.clear()
        self.source.data.materials.append(material)
        attribute = self.source.data.attributes.new("Keep attribute", "FLOAT", "POINT")
        attribute.data[0].value = 0.25
        geometry = [tuple(vertex.co) for vertex in self.source.data.vertices]
        result = self.apply(self.export(), "RETEXTURE", Matrix.Translation((-2, 0, 0)))
        self.assertEqual([tuple(vertex.co) for vertex in self.source.data.vertices], geometry)
        self.assertEqual(self.source.data.attributes["Keep attribute"].data[0].value, 0.25)
        self.assertEqual(list(bpy.data.objects[result["original"]].data.materials), [material])
        images = [
            node.image
            for node in self.source.data.materials[0].node_tree.nodes
            if node.type == "TEX_IMAGE"
        ]
        self.assertTrue(images)
        self.assertTrue(all(image.packed_file for image in images))

    def test_parts_keep_source_as_parent_and_preserve_unrelated_child(self):
        child = bpy.data.objects.new("Existing child", None)
        self.scene.collection.objects.link(child)
        child.parent = self.source
        child.location = (1, 2, 3)
        bpy.context.view_layer.update()
        before = child.matrix_world.copy()
        self.path.write_bytes(parts_glb())
        result = self.apply(self.export(), "PARTS")
        self.assertEqual(len(result["parts"]), 2)
        self.assertEqual(len(self.source.data.vertices), 0)
        self.assertEqual(child.matrix_world, before)
        for name in result["parts"]:
            part = bpy.data.objects[name]
            self.assertEqual(part.parent, self.source)
            self.assertTrue(part.data.polygons)
            self.assertTrue(part.data.uv_layers)
        self.assertEqual(bpy.context.view_layer.objects.active, self.other)

    def test_rig_attaches_real_weights_and_animated_deformation(self):
        for vertex, position in zip(
            self.source.data.vertices, [(0, 0, 0), (1, 0, 0), (0, 0, 1)], strict=True
        ):
            vertex.co = position
        self.source.data.update()
        self.path.write_bytes(animated_glb(morph=False))
        material = bpy.data.materials.new("Keep rig source material")
        self.source.data.materials.append(material)
        result = self.apply(self.export(), "RIG")
        rig = bpy.data.objects[result["rig"]]
        self.assertEqual(len(rig.data.bones), 2)
        self.assertEqual(self.source.modifiers[0].object, rig)
        self.assertEqual(self.source.vertex_groups[0].name, "RootJoint")
        self.assertEqual(self.source.data.vertices[0].groups[0].weight, 1)
        self.assertEqual(list(self.source.data.materials), [material])
        self.scene.frame_set(0)
        first = (
            self.source.evaluated_get(bpy.context.evaluated_depsgraph_get())
            .data.vertices[0]
            .co.copy()
        )
        self.scene.frame_set(self.scene.render.fps)
        last = (
            self.source.evaluated_get(bpy.context.evaluated_depsgraph_get())
            .data.vertices[0]
            .co.copy()
        )
        self.assertAlmostEqual(last.x - first.x, 2, places=4)

    def test_target_edit_deleted_replacement_and_different_scene_refuse_application(self):
        exported = self.export()
        self.source.data.vertices[0].co.x += 1
        before = self.model._snapshot()
        with self.assertRaises(self.mesh.MeshApplicationError):
            self.apply(exported)
        self.assertEqual(self.model._snapshot(), before)
        exported = self.export()
        name = self.source.name
        bpy.data.objects.remove(self.source, do_unlink=True)
        data = bpy.data.meshes.new("Replacement")
        data.from_pydata([(0, 0, 0), (1, 0, 0), (0, 1, 0)], [], [(0, 1, 2)])
        data.update()
        replacement = bpy.data.objects.new(name, data)
        self.scene.collection.objects.link(replacement)
        with self.assertRaises(self.mesh.MeshApplicationError):
            self.apply(exported)
        self.source = replacement
        exported = self.export()
        other = bpy.data.scenes.new("Other scene")
        bpy.context.window.scene = other
        with self.assertRaises(self.mesh.MeshApplicationError):
            self.apply(exported)

    def test_corrupt_result_and_native_rollback_preserve_target_and_handle(self):
        exported = self.export()
        before = self.model._snapshot()
        body = self.path.read_bytes()
        self.path.write_bytes(b"broken")
        with self.assertRaises(ValueError):
            self.apply(exported)
        self.path.write_bytes(body)
        with (
            patch.object(
                self.application,
                "_release_import",
                side_effect=RuntimeError("Synthetic cleanup failure"),
            ),
            self.assertRaises(self.application.MeshResultApplicationError),
        ):
            self.apply(exported)
        self.assertEqual(self.model._snapshot(), before)
        self.assertIn(exported["handle"], self.owner._exports)
        self.mesh.validate_target(self.owner._exports[exported["handle"]].target)
        self.assertEqual(self.apply(exported)["policy"], "REMESH")

    def test_load_and_disable_invalidate_handles_and_remove_exports(self):
        exported = self.export()
        reset_scene()
        self.assertFalse(Path(exported["path"]).exists())
        with self.assertRaises(ValueError):
            self.apply(exported)
        self.assertFalse(self.owner._exports)
        self.owner.unregister()
        self.assertNotIn(self.owner._clear, bpy.app.handlers.load_pre)
        self.owner.register()

    def test_reexport_supersedes_prior_handle(self):
        first = self.export()
        second = self.export()
        self.assertNotEqual(first["handle"], second["handle"])
        self.assertFalse(Path(first["path"]).exists())
        self.assertTrue(Path(second["path"]).exists())
        with self.assertRaises(ValueError):
            self.apply(first)

    def test_native_undo_redo_restores_exact_geometry_and_expires_pending_handles(self):
        prefs = bpy.context.preferences.edit
        previous = prefs.use_global_undo, prefs.undo_steps
        prefs.use_global_undo, prefs.undo_steps = True, 32
        try:
            exported = self.export()
            name = self.source.name
            before = [tuple(vertex.co) for vertex in self.source.data.vertices]
            # Exercise real native memfile history. Production creates these
            # automatic checkpoints only on desktop; this runner is background.
            with patch.object(self.application, "_undo_enabled", return_value=True):
                result = self.apply(exported)
            self.assertTrue(result["undo_available"])
            after = [tuple(vertex.co) for vertex in self.source.data.vertices]
            pending = self.export()
            self.assertEqual(bpy.ops.ed.undo(), {"FINISHED"})
            self.assertEqual(
                [tuple(vertex.co) for vertex in bpy.data.objects[name].data.vertices], before
            )
            self.assertNotIn(result["original"], bpy.data.objects)
            self.assertFalse(Path(pending["path"]).exists())
            self.assertFalse(self.owner._exports)
            self.assertEqual(bpy.ops.ed.redo(), {"FINISHED"})
            self.assertEqual(
                [tuple(vertex.co) for vertex in bpy.data.objects[name].data.vertices], after
            )
            self.assertIn(result["original"], bpy.data.objects)
            self.assertFalse(self.owner._exports)
        finally:
            prefs.use_global_undo, prefs.undo_steps = previous

    def test_retexture_survives_save_reopen_without_source_files(self):
        self.matching_source()
        self.apply(self.export(), "RETEXTURE", Matrix.Translation((-2, 0, 0)))
        name = self.source.name
        self.path.unlink()
        with tempfile.TemporaryDirectory(prefix="mesh-roundtrip-") as directory:
            path = Path(directory) / "edited.blend"
            bpy.ops.wm.save_as_mainfile(filepath=str(path), check_existing=False)
            bpy.ops.wm.open_mainfile(filepath=str(path))
            source = bpy.data.objects[name]
            images = [
                node.image
                for node in source.data.materials[0].node_tree.nodes
                if node.type == "TEX_IMAGE"
            ]
            self.assertTrue(images)
            self.assertTrue(all(image.packed_file for image in images))
            reset_scene()
