# SPDX-FileCopyrightText: 2026 Open Studio contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Native assistant mesh roundtrip, stale targets and explicit application policies."""

import hashlib
import json
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

    def export(self, **options):
        return self.tools.export_agent_mesh({"object_name": self.source.name, **options})

    def import_export(self, exported):
        self.path.write_bytes(Path(exported["path"]).read_bytes())
        result = submodule("mcp.tools_agent_models").import_agent_model(
            {"filename": self.path.name}
        )
        return [
            bpy.data.objects[name]
            for name in result["objects"]
            if bpy.data.objects[name].type == "MESH"
        ]

    def returned_local(self):
        data = self.source.data.copy()
        obj = bpy.data.objects.new("Returned local edit", data)
        self.scene.collection.objects.link(obj)
        expected = [(0.125, 0.25), (0.875, 0.25), (0.125, 0.75)]
        try:
            if not data.uv_layers:
                data.uv_layers.new()
            for loop, uv in zip(data.uv_layers.active.data, expected, strict=True):
                loop.uv = uv
            submodule("blender.mesh_export").export_glb(bpy.context, [obj], str(self.path))
        finally:
            bpy.data.objects.remove(obj, do_unlink=True)
            bpy.data.meshes.remove(data)
        return expected

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
        second_scene = bpy.data.scenes.new("Unrelated export scene")
        second_scene.frame_set(7, subframe=0.5)
        self.scene.frame_set(27, subframe=0.375)
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
        self.assertEqual(len(document["scenes"]), 1)
        self.assertEqual(bpy.context.scene, self.scene)
        self.assertEqual((self.scene.frame_current, self.scene.frame_subframe), (27, 0.375))
        self.assertEqual((second_scene.frame_current, second_scene.frame_subframe), (7, 0.5))
        self.assertEqual(hashlib.sha256(body).hexdigest(), exported["sha256"])
        self.assertEqual(len(body), exported["bytes"])
        self.assertEqual(
            Matrix(exported["source_world_to_local"]), self.source.matrix_world.inverted()
        )
        self.mesh.validate_target(target)
        self.assertEqual(set(bpy.context.selected_objects), {self.other})
        self.assertEqual(bpy.context.view_layer.objects.active, self.other)
        self.assertFalse(submodule("prefs").get_prefs().mcp_allow_python)

    def test_evaluated_reference_bakes_modifier_without_edit_authority(self):
        modifier = self.source.modifiers.new("Thickness", "SOLIDIFY")
        modifier.thickness = 2
        original = self.source.data
        before = self.model._snapshot()
        with self.assertRaises(self.mesh.MeshApplicationError):
            self.export()
        exported = self.export(mode="REFERENCE", coordinates="LOCAL")
        self.assertEqual(self.model._snapshot(), before)
        self.assertEqual(self.source.data, original)
        self.assertEqual(len(self.source.modifiers), 1)
        self.assertEqual(len(original.vertices), 3)
        self.assertGreater(exported["geometry"][0]["vertices"], 3)
        self.assertIsNone(exported["handle"])
        self.assertFalse(exported["application_available"])
        self.assertIsNone(exported["result_to_source"])
        manifest = json.loads(Path(exported["manifest_path"]).read_text())
        self.assertEqual((manifest["mode"], manifest["coordinates"]), ("REFERENCE", "LOCAL"))
        self.assertNotIn("handle", manifest)
        with self.assertRaisesRegex(ValueError, "no edit authority"):
            self.tools.apply_agent_mesh({"handle": exported["export_id"]})
        document = submodule("core.scene.glb").inspect_glb(Path(exported["path"]).read_bytes())
        self.assertFalse(document.get("skins"))
        self.assertFalse(document.get("animations"))
        (imported,) = self.import_export(exported)
        self.assertGreater(len(imported.data.vertices), 3)
        self.assertAlmostEqual(
            max(v.co.z for v in imported.data.vertices)
            - min(v.co.z for v in imported.data.vertices),
            2,
        )
        self.assertEqual(len(imported.modifiers), 0)

    def test_reference_resolves_object_material_override_without_changing_shared_data(self):
        shared = bpy.data.materials.new("Shared blue material")
        override = bpy.data.materials.new("Visible red override")
        for material, color in ((shared, (0, 0, 1, 1)), (override, (1, 0, 0, 1))):
            material.use_nodes = True
            material.node_tree.nodes.get("Principled BSDF").inputs[
                "Base Color"
            ].default_value = color
        self.source.data.materials.append(shared)
        self.source.material_slots[0].link = "OBJECT"
        self.source.material_slots[0].material = override
        alias = bpy.data.objects.new("Shared geometry with blue material", self.source.data)
        self.scene.collection.objects.link(alias)
        self.source.modifiers.new("Evaluated material snapshot", "SOLIDIFY")
        original = self.source.data
        for coordinates in ("WORLD", "LOCAL"):
            with self.subTest(coordinates=coordinates):
                exported = self.export(mode="REFERENCE", coordinates=coordinates)
                self.assertEqual(self.source.data, original)
                self.assertEqual(self.source.material_slots[0].link, "OBJECT")
                self.assertEqual(self.source.material_slots[0].material, override)
                self.assertEqual(alias.material_slots[0].material, shared)
                (snapshot,) = self.import_export(exported)
                material = snapshot.data.materials[0]
                self.assertTrue(material.name.startswith(override.name))
                color = (
                    material.node_tree.nodes.get("Principled BSDF")
                    .inputs["Base Color"]
                    .default_value
                )
                self.assertEqual(tuple(color), (1, 0, 0, 1))

    def test_local_singular_source_and_reference_name_contracts(self):
        self.source.scale.x = 0
        with self.assertRaisesRegex(ValueError, "LOCAL"):
            self.export()
        exported = self.export(coordinates="LOCAL")
        self.assertIsNone(exported["source_world_to_local"])
        self.assertEqual(Matrix(exported["result_to_source"]), Matrix.Identity(4))
        self.returned_local()
        self.apply(exported, keep_original=False)
        self.assertEqual(self.source.scale.x, 0)
        for args in (
            {"object_names": [self.source.name]},
            {"mode": "REFERENCE", "coordinates": "LOCAL", "object_names": [self.source.name]},
            {"mode": "REFERENCE", "object_names": [self.source.name] * 65},
            {"mode": "REFERENCE", "object_names": [self.source.name] * 2},
            {
                "mode": "REFERENCE",
                "object_name": self.source.name,
                "object_names": [self.source.name],
            },
        ):
            with self.subTest(args=args), self.assertRaises(ValueError):
                self.tools.export_agent_mesh(args)

    def test_custom_normals_are_guarded_and_reflected_result_mappings_still_refuse(self):
        self.source.data.normals_split_custom_set([(0, 0, 1)] * 3)
        self.source.scale.x = -1
        exported = self.export(coordinates="LOCAL")
        before = self.model._snapshot()
        reflected = Matrix.Diagonal((-1, 1, 1, 1))
        with self.assertRaisesRegex(self.mesh.MeshApplicationError, "orientation-preserving"):
            self.apply(exported, matrix=reflected)
        self.assertEqual(self.model._snapshot(), before)
        self.source.data.normals_split_custom_set([(0, 1, 1)] * 3)
        with self.assertRaisesRegex(self.mesh.MeshApplicationError, "source mesh changed"):
            self.apply(exported)

    def test_reference_bakes_current_rig_pose_and_shape_keys_preserving_source(self):
        self.path.write_bytes(animated_glb(morph=True))
        imported = submodule("mcp.tools_agent_models").import_agent_model(
            {"filename": self.path.name}
        )
        self.source = next(
            bpy.data.objects[name]
            for name in imported["objects"]
            if bpy.data.objects[name].type == "MESH"
        )
        self.scene.frame_set(self.scene.render.fps, subframe=0.25)
        graph = bpy.context.evaluated_depsgraph_get()
        expected = sorted(
            tuple(round(c, 5) for c in v.co) for v in self.source.evaluated_get(graph).data.vertices
        )
        original_mesh = self.source.data
        original_keys = self.source.data.shape_keys
        original_modifiers = tuple(self.source.modifiers)
        before = self.model._snapshot()
        exported = self.export(mode="REFERENCE", coordinates="LOCAL")
        self.assertEqual(self.model._snapshot(), before)
        self.assertEqual(
            (self.scene.frame_current, self.scene.frame_subframe), (self.scene.render.fps, 0.25)
        )
        self.assertEqual(self.source.data, original_mesh)
        self.assertEqual(self.source.data.shape_keys, original_keys)
        self.assertEqual(tuple(self.source.modifiers), original_modifiers)
        self.assertEqual(bpy.context.view_layer.objects.active, self.other)
        (snapshot,) = self.import_export(exported)
        actual = sorted(tuple(round(c, 5) for c in v.co) for v in snapshot.data.vertices)
        self.assertEqual(actual, expected)
        self.assertIsNone(snapshot.data.shape_keys)
        self.assertFalse(snapshot.vertex_groups)
        self.assertFalse(snapshot.modifiers)
        self.assertIsNone(snapshot.animation_data)

    def test_world_reference_handles_reflection_shear_normals_uvs_and_multiple_meshes(self):
        self.matching_source()
        parent = bpy.data.objects.new("Sheared reference parent", None)
        self.scene.collection.objects.link(parent)
        parent.scale = (1, 2, 3)
        parent.rotation_euler.z = 0.4
        self.source.parent = parent
        self.source.rotation_euler.y = 0.5
        self.source.scale = (-2, 3, 1)
        self.source.location = (2, 3, 4)
        second = bpy.data.objects.new("Second reference", self.source.data.copy())
        self.scene.collection.objects.link(second)
        second.location = (-4, -2, 1)
        bpy.context.view_layer.update()
        matrix = self.source.matrix_world.copy()
        expected_positions = sorted(
            tuple(round(c, 4) for c in matrix @ v.co) for v in self.source.data.vertices
        )
        expected_uvs = {
            tuple(
                round(c, 4) for c in matrix @ self.source.data.vertices[loop.vertex_index].co
            ): tuple(round(c, 5) for c in self.source.data.uv_layers.active.data[loop.index].uv)
            for loop in self.source.data.loops
        }
        expected_normal = (
            matrix.inverted().transposed().to_3x3() @ self.source.data.polygons[0].normal
        ).normalized()
        before = self.mesh.capture_target(self.scene, self.source)
        exported = self.tools.export_agent_mesh(
            {
                "object_names": [self.source.name, second.name],
                "mode": "REFERENCE",
                "coordinates": "WORLD",
            }
        )
        self.mesh.validate_target(before)
        self.assertEqual(len(exported["geometry"]), 2)
        self.assertTrue(exported["geometry"][0]["source_reflected"])
        snapshots = self.import_export(exported)
        mirrored = next(obj for obj in snapshots if obj.name.startswith(self.source.name))
        self.assertEqual(
            sorted(tuple(round(c, 4) for c in v.co) for v in mirrored.data.vertices),
            expected_positions,
        )
        self.assertGreater(mirrored.data.polygons[0].normal.dot(expected_normal), 0.999)
        for loop in mirrored.data.loops:
            position = tuple(round(c, 4) for c in mirrored.data.vertices[loop.vertex_index].co)
            uv = tuple(round(c, 5) for c in mirrored.data.uv_layers.active.data[loop.index].uv)
            self.assertEqual(uv, expected_uvs[position])
        self.assertTrue(
            all(
                normal.vector.dot(expected_normal) > 0.999
                for normal in mirrored.data.corner_normals
            )
        )

    def test_mirrored_local_edit_preserves_handedness_normals_uvs_and_original(self):
        self.matching_source()
        self.source.scale = (-2, 3, 4)
        self.source.location = (4, 5, 6)
        bpy.context.view_layer.update()
        transform = self.source.matrix_world.copy()
        with self.assertRaisesRegex(ValueError, "LOCAL"):
            self.export()
        for policy in ("UV", "RETEXTURE", "REMESH"):
            with self.subTest(policy=policy):
                original = self.source.data
                positions = [tuple(v.co) for v in original.vertices]
                normals = [tuple(face.normal) for face in original.polygons]
                exported = self.export(coordinates="LOCAL")
                self.assertEqual(Matrix(exported["result_to_source"]), Matrix.Identity(4))
                expected_uvs = self.returned_local()
                result = self.apply(exported, policy)
                self.assertEqual(result["export_coordinates"], "LOCAL")
                self.assertEqual(self.source.matrix_world, transform)
                self.assertLess(self.source.matrix_world.determinant(), 0)
                self.assertEqual([tuple(v.co) for v in self.source.data.vertices], positions)
                self.assertEqual(
                    [tuple(face.normal) for face in self.source.data.polygons], normals
                )
                self.assertEqual(
                    [tuple(loop.uv) for loop in self.source.data.uv_layers.active.data],
                    expected_uvs,
                )
                self.assertEqual(bpy.data.objects[result["original"]].data, original)
                self.assertEqual(set(bpy.context.selected_objects), {self.other})

    def test_mirrored_local_edits_keep_stale_target_and_rollback_guards(self):
        self.matching_source()
        self.source.scale = (-1, 2, 3)
        exported = self.export(coordinates="LOCAL")
        self.returned_local()
        original = self.source.data
        before = self.model._snapshot()
        with (
            patch.object(
                self.application, "_release_import", side_effect=RuntimeError("Synthetic failure")
            ) as release,
            self.assertRaises(self.application.MeshResultApplicationError),
        ):
            self.apply(exported, "UV")
        release.assert_called_once()
        self.assertEqual(self.model._snapshot(), before)
        self.assertEqual(self.source.data, original)
        self.assertIn(exported["handle"], self.owner._exports)
        self.source.scale.x = -2
        with self.assertRaises(self.mesh.MeshApplicationError):
            self.apply(exported, "UV")
        self.assertEqual(self.source.data, original)

    def test_reference_export_failure_cleans_only_private_staging_and_keeps_edit_guard(self):
        editable = self.export()
        self.source.modifiers.new("Preview", "SOLIDIFY")
        before = self.model._snapshot()
        with (
            patch.object(
                submodule("blender.mesh_export"),
                "export_glb",
                side_effect=RuntimeError("Synthetic export failure"),
            ),
            self.assertRaisesRegex(RuntimeError, "Synthetic export failure"),
        ):
            self.export(mode="REFERENCE", coordinates="LOCAL")
        self.assertEqual(self.model._snapshot(), before)
        self.assertTrue(Path(editable["path"]).exists())
        self.assertIn(editable["handle"], self.owner._exports)
        self.assertEqual(bpy.context.scene, self.scene)
        self.assertEqual(bpy.context.view_layer.objects.active, self.other)
        self.assertEqual(set(bpy.context.selected_objects), {self.other})

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

    def test_mirrored_local_parts_preserve_source_frame_and_original(self):
        self.source.scale = (-2, 3, 4)
        self.source.location = (1, 2, 3)
        bpy.context.view_layer.update()
        transform = self.source.matrix_world.copy()
        original = self.source.data
        exported = self.export(coordinates="LOCAL")
        self.path.write_bytes(parts_glb())
        result = self.apply(exported, "PARTS")
        self.assertEqual(self.source.matrix_world, transform)
        self.assertEqual(bpy.data.objects[result["original"]].data, original)
        self.assertEqual(len(result["parts"]), 2)
        for name in result["parts"]:
            part = bpy.data.objects[name]
            self.assertEqual(part.parent, self.source)
            self.assertEqual(part.matrix_world, transform)
            self.assertTrue(part.data.polygons)
        self.assertEqual(set(bpy.context.selected_objects), {self.other})

    def test_mirrored_local_rig_deforms_in_preserved_source_frame(self):
        for vertex, position in zip(
            self.source.data.vertices, [(0, 0, 0), (1, 0, 0), (0, 0, 1)], strict=True
        ):
            vertex.co = position
        self.source.data.update()
        self.source.scale = (-2, 3, 4)
        self.source.location = (1, 2, 3)
        exported = self.export(coordinates="LOCAL")
        original = self.source.data
        transform = self.source.matrix_world.copy()
        self.path.write_bytes(animated_glb(morph=False))
        result = self.apply(exported, "RIG")
        rig = bpy.data.objects[result["rig"]]
        self.assertEqual(self.source.modifiers[0].object, rig)
        self.assertEqual(self.source.matrix_world, transform)
        self.assertEqual(bpy.data.objects[result["original"]].data, original)
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
        self.assertAlmostEqual((transform @ last).x - (transform @ first).x, -4, places=4)

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
