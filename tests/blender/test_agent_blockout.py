# SPDX-FileCopyrightText: 2026 Open Studio contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Real local blockout building, persistence, replacement and edit protection."""

import copy
import math
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import bpy
from helpers import reset_scene, submodule


def plan():
    return {
        "name": "Courtyard",
        "elements": [
            {
                "id": "ground",
                "name": "Ground",
                "primitive": "plane",
                "category": "floor",
                "position": [0, 0, -0.05],
                "size": [12, 10, 0.1],
            },
            {
                "id": "well",
                "name": "Well",
                "primitive": "cylinder",
                "category": "water",
                "position": [2, 3, 0.75],
                "size": [2, 2, 1.5],
                "rotation_degrees": [0, 0, 30],
                "group": "Center",
            },
            {
                "id": "roof",
                "name": "Roof",
                "primitive": "wedge",
                "category": "structure",
                "position": [4, 0, 3],
                "size": [3, 2, 1],
                "color": [0.2, 0.4, 0.6],
            },
        ],
    }


class AgentBlockoutTests(unittest.TestCase):
    def setUp(self):
        reset_scene()
        self.tools = submodule("mcp.tools_agent_blockout")
        self.builder = submodule("blender.assistant_blockout")
        self.scene = bpy.context.scene

    def build(self, recipe=None, revision=None):
        return self.tools.build_agent_blockout(
            {"plan": recipe or plan(), "expected_revision": revision}
        )

    def root(self):
        return self.builder._root(self.scene)

    def objects(self):
        return {obj[self.builder.ELEMENT]: obj for obj in self.root().all_objects}

    def counts(self):
        return {
            name: len(getattr(bpy.data, name))
            for name in ("collections", "objects", "meshes", "materials")
        }

    def test_build_places_real_primitives_with_colors_groups_and_full_transforms(self):
        result = self.build()
        self.assertTrue(result["editable"])
        self.assertIsNone(self.scene.get("scenario_blockout"))
        objects = self.objects()
        self.assertEqual(set(objects), {"ground", "well", "roof"})
        well = objects["well"]
        self.assertEqual(tuple(well.location), (2, 3, 0.75))
        self.assertEqual(tuple(well.scale), (2, 2, 1.5))
        self.assertAlmostEqual(well.rotation_euler.z, math.radians(30))
        self.assertGreater(len(well.data.vertices), 8)
        self.assertIn("Center", [collection.name for collection in self.root().children])
        self.assertEqual(len(objects["roof"].data.vertices), 6)
        self.assertEqual(
            tuple(round(value, 3) for value in objects["roof"].color), (0.2, 0.4, 0.6, 1)
        )
        material = objects["roof"].active_material
        shader = next(node for node in material.node_tree.nodes if node.type == "BSDF_PRINCIPLED")
        self.assertEqual(
            tuple(round(value, 3) for value in shader.inputs["Base Color"].default_value),
            (0.2, 0.4, 0.6, 1),
        )

    def test_all_supported_shapes_build_and_inspect_guidance(self):
        recipe = plan()
        element = recipe["elements"][0]
        recipe["elements"] = [
            dict(element, id=primitive, primitive=primitive, name=primitive)
            for primitive in self.builder.core.PRIMITIVES
        ]
        self.build(recipe)
        result = self.tools.blockout_plan({})
        self.assertEqual(len(self.objects()), 6)
        self.assertTrue(result["editable"])
        self.assertEqual(result["limits"]["elements"], 200)
        self.assertIn("refine", result["guidance"])

    def test_refine_repeat_and_clear_preserve_unrelated_scene_content(self):
        bpy.ops.mesh.primitive_cube_add()
        unrelated = bpy.context.object
        unrelated.name = "Keep my existing work"
        other_collection = bpy.data.collections.new("Open Studio Courtyard")
        self.scene.collection.children.link(other_collection)
        first = self.build()
        old = set(self.root().all_objects)
        count = self.counts()
        repeat = self.build(revision=first["revision"])
        self.assertTrue(repeat["reused"])
        self.assertEqual(repeat["revision"], first["revision"])
        self.assertEqual(self.counts(), count)
        revised = plan()
        revised["elements"][1]["position"][0] = -4
        second = self.build(revised, first["revision"])
        self.assertNotEqual(first["revision"], second["revision"])
        self.assertEqual(self.objects()["well"].location.x, -4)
        self.assertFalse(old & set(bpy.data.objects))
        self.assertEqual(bpy.context.object, unrelated)
        cleared = self.tools.clear_agent_blockout({"expected_revision": second["revision"]})
        self.assertEqual(cleared["removed_objects"], 3)
        self.assertIn(unrelated, tuple(bpy.data.objects))
        self.assertIn(other_collection, tuple(bpy.data.collections))
        self.assertIsNone(self.tools.blockout_plan({})["plan"])

    def test_partial_invalid_and_stale_requests_preserve_the_complete_previous_build(self):
        first = self.build()
        root = self.root()
        count = self.counts()
        for recipe, revision in (
            ({"name": "Partial", "elements": [{"name": "Broken"}]}, first["revision"]),
            (plan(), "stale"),
            (plan(), None),
        ):
            with self.assertRaises(ValueError):
                self.build(recipe, revision)
            self.assertEqual(self.root(), root)
            self.assertEqual(self.counts(), count)
        with self.assertRaisesRegex(ValueError, "stale"):
            self.tools.clear_agent_blockout({"expected_revision": "stale"})

    def test_failed_staging_rolls_back_all_new_datablocks(self):
        first = self.build()
        root, count = self.root(), self.counts()
        original = self.builder._mesh
        calls = 0

        def fail_second(primitive):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise RuntimeError("Synthetic geometry construction failure")
            return original(primitive)

        changed = plan()
        changed["name"] = "Changed"
        with (
            patch.object(self.builder, "_mesh", side_effect=fail_second),
            self.assertRaises(RuntimeError),
        ):
            self.build(changed, first["revision"])
        self.assertEqual(self.root(), root)
        self.assertEqual(self.counts(), count)
        self.assertTrue(self.tools.blockout_plan({})["editable"])

    def test_user_transform_mesh_material_and_custom_edits_refuse_replacement(self):
        changes = (
            lambda obj: setattr(obj.location, "x", 17),
            lambda obj: setattr(obj.data.vertices[0].co, "x", 13),
            lambda obj: setattr(obj.active_material, "diffuse_color", (0.1, 0.2, 0.3, 1)),
            lambda obj: obj.__setitem__("artist_note", "Keep this"),
            lambda obj: obj.modifiers.new("User modifier", "BEVEL"),
            lambda obj: obj.hide_set(True),
        )
        for change in changes:
            reset_scene()
            self.scene = bpy.context.scene
            first = self.build()
            obj = self.objects()["well"]
            change(obj)
            count = self.counts()
            self.assertFalse(self.tools.blockout_plan({})["editable"])
            with self.assertRaises(ValueError):
                self.tools.clear_agent_blockout({"expected_revision": first["revision"]})
            with self.assertRaises(ValueError):
                self.build(revision=first["revision"])
            self.assertEqual(self.counts(), count)

    def test_unrelated_added_objects_and_external_references_are_never_deleted(self):
        for kind in ("added", "shared_object", "shared_mesh", "child", "constraint", "other_scene"):
            with self.subTest(kind=kind):
                reset_scene()
                self.scene = bpy.context.scene
                first = self.build()
                owned = self.objects()["well"]
                foreign = bpy.data.objects.new("User work", None)
                self.scene.collection.objects.link(foreign)
                if kind == "added":
                    self.root().objects.link(foreign)
                elif kind == "shared_object":
                    self.scene.collection.objects.link(owned)
                elif kind == "shared_mesh":
                    foreign = bpy.data.objects.new("User mesh", owned.data)
                    self.scene.collection.objects.link(foreign)
                elif kind == "child":
                    foreign.parent = owned
                elif kind == "constraint":
                    foreign.constraints.new("COPY_LOCATION").target = owned
                else:
                    other = bpy.data.scenes.new("Other scene")
                    other.collection.children.link(self.root())
                count = self.counts()
                with self.assertRaises(ValueError):
                    self.tools.clear_agent_blockout({"expected_revision": first["revision"]})
                self.assertEqual(self.counts(), count)
                self.assertIn(foreign, tuple(bpy.data.objects))

    def test_plan_revision_and_edit_guard_survive_save_reopen(self):
        first = self.build()
        with tempfile.TemporaryDirectory(prefix="assistant-blockout-") as directory:
            path = Path(directory) / "blockout.blend"
            bpy.ops.wm.save_as_mainfile(filepath=str(path), check_existing=False)
            bpy.ops.wm.open_mainfile(filepath=str(path))
            self.scene = bpy.context.scene
            saved = self.tools.blockout_plan({})
            self.assertEqual(saved["plan"], first["plan"])
            self.assertEqual(saved["revision"], first["revision"])
            self.assertTrue(saved["editable"], saved.get("warning"))
            refined = copy.deepcopy(saved["plan"])
            refined["elements"][0]["size"][0] = 15
            result = self.build(refined, saved["revision"])
            self.tools.clear_agent_blockout({"expected_revision": result["revision"]})
            reset_scene()

    @unittest.skipIf(bpy.app.background, "Undo/redo requires a native GUI session")
    def test_native_undo_redo_restores_saved_revision_and_owned_geometry(self):
        first = self.build()
        first_revision = first["revision"]
        refined = plan()
        refined["elements"][1]["position"][0] = -4
        second = self.build(refined, first_revision)
        bpy.ops.ed.undo()
        self.scene = bpy.context.scene
        self.assertEqual(self.tools.blockout_plan({})["revision"], first_revision)
        self.assertEqual(self.objects()["well"].location.x, 2)
        bpy.ops.ed.redo()
        self.scene = bpy.context.scene
        self.assertEqual(self.tools.blockout_plan({})["revision"], second["revision"])
        self.assertEqual(self.objects()["well"].location.x, -4)
        self.assertTrue(self.tools.blockout_plan({})["editable"])
        self.tools.clear_agent_blockout({"expected_revision": second["revision"]})
        self.assertIsNone(self.tools.blockout_plan({})["plan"])
        bpy.ops.ed.undo()
        self.scene = bpy.context.scene
        self.assertEqual(self.tools.blockout_plan({})["revision"], second["revision"])
        self.assertTrue(self.tools.blockout_plan({})["editable"])
        bpy.ops.ed.redo()
        self.scene = bpy.context.scene
        self.assertIsNone(self.tools.blockout_plan({})["plan"])
