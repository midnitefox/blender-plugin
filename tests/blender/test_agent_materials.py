# SPDX-FileCopyrightText: 2026 Open Studio contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Offline map and panorama handoffs through the installed extension."""

import hashlib
import tempfile
import unittest
from pathlib import Path

import bpy
from helpers import reset_scene, submodule


class AgentMaterialTests(unittest.TestCase):
    def setUp(self):
        reset_scene()
        self.tools = submodule("mcp.tools_agent_materials")
        self.helper = submodule("blender.assistant_materials")
        self.helper._clear()
        self.inbox = submodule("mcp.tools_agent")._inbox()
        self.files = []
        bpy.ops.mesh.primitive_cube_add()
        self.target = bpy.context.object

    def tearDown(self):
        self.helper._clear()
        for path in self.files:
            path.unlink(missing_ok=True)

    def image(self, name="map.png", width=8, height=8, file_format="PNG"):
        path = self.inbox / name
        self.files.append(path)
        image = bpy.data.images.new(
            "Synthetic map",
            width=width,
            height=height,
            float_buffer=file_format in {"OPEN_EXR", "HDR"},
        )
        image.pixels[:] = [0.5, 0.5, 1.0, 1.0] * (width * height)
        image.file_format = file_format
        image.filepath_raw = str(path)
        image.save()
        bpy.data.images.remove(image)
        return path

    def material(self, maps=None):
        if maps is None:
            path = self.image()
            maps = {role: {"filename": path.name} for role in self.helper.MAP_ROLES}
        return self.tools.import_agent_material({"object_name": self.target.name, "maps": maps})

    def test_complete_map_graph_colorspaces_shared_mesh_repeat_and_restore(self):
        original = bpy.data.materials.new("Original")
        spare = bpy.data.materials.new("Spare slot")
        self.target.data.materials.append(original)
        self.target.data.materials.append(spare)
        other = bpy.data.objects.new("Shared mesh", self.target.data)
        bpy.context.scene.collection.objects.link(other)
        path = self.image()
        maps = {role: {"filename": path.name} for role in self.helper.MAP_ROLES}
        result = self.material(maps)
        material = self.target.active_material
        self.assertEqual(other.active_material, original)
        self.assertEqual(self.target.material_slots[1].material, spare)
        images = {role: bpy.data.images[item["image"]] for role, item in result["maps"].items()}
        self.assertEqual(len(set(images.values())), 6)
        for role, image in images.items():
            self.assertEqual(
                image.colorspace_settings.name, "sRGB" if role == "base_color" else "Non-Color"
            )
            self.assertTrue(image.packed_file)
        tree = material.node_tree
        shader = next(n for n in tree.nodes if n.type == "BSDF_PRINCIPLED")
        self.assertEqual(shader.inputs["Normal"].links[0].from_node.type, "BUMP")
        bump = shader.inputs["Normal"].links[0].from_node
        self.assertEqual(bump.inputs["Normal"].links[0].from_node.type, "NORMAL_MAP")
        self.assertEqual(shader.inputs["Base Color"].links[0].from_node.blend_type, "MULTIPLY")
        self.assertEqual(shader.inputs["Roughness"].links[0].from_node.image, images["roughness"])
        self.assertEqual(shader.inputs["Metallic"].links[0].from_node.image, images["metallic"])
        before = len(bpy.data.materials), len(bpy.data.images)
        repeated = self.material(maps)
        self.assertTrue(repeated["reused"])
        self.assertEqual(before, (len(bpy.data.materials), len(bpy.data.images)))
        self.tools.restore_agent_material({"object_name": self.target.name})
        self.assertEqual(self.target.active_material, original)
        self.assertEqual(self.target.material_slots[0].link, "DATA")
        self.assertEqual(other.active_material, original)

    def test_empty_shared_mesh_is_copied_and_restored(self):
        original = self.target.data
        other = bpy.data.objects.new("Shared mesh", original)
        bpy.context.scene.collection.objects.link(other)
        self.material()
        self.assertNotEqual(self.target.data, original)
        self.assertEqual(other.data, original)
        self.assertEqual(len(other.material_slots), 0)
        isolated = self.target.data
        self.tools.restore_agent_material({"object_name": self.target.name})
        self.assertEqual(self.target.data, isolated)
        self.assertEqual(other.data, original)
        self.assertEqual(len(self.target.material_slots), 0)

    def test_restore_preserves_changed_shared_material_and_isolated_custom_data(self):
        original = bpy.data.materials.new("Original")
        replacement = bpy.data.materials.new("Other object's edit")
        self.target.data.materials.append(original)
        other = bpy.data.objects.new("Other object", self.target.data)
        bpy.context.scene.collection.objects.link(other)
        self.material()
        other.data.materials[0] = replacement
        with self.assertRaisesRegex(ValueError, "shared mesh material changed"):
            self.tools.restore_agent_material({"object_name": self.target.name})
        self.assertEqual(other.active_material, replacement)
        self.target.data.materials.clear()
        self.material()
        isolated = self.target.data
        isolated["custom_edit"] = "Preserve this data"
        self.tools.restore_agent_material({"object_name": self.target.name})
        self.assertEqual(self.target.data, isolated)
        self.assertEqual(isolated["custom_edit"], "Preserve this data")

    def test_changed_active_slot_receives_separate_material(self):
        self.target.data.materials.append(bpy.data.materials.new("First"))
        self.target.data.materials.append(bpy.data.materials.new("Second"))
        path = self.image()
        maps = {"base_color": {"filename": path.name}}
        first = self.material(maps)
        self.target.active_material_index = 1
        second = self.material(maps)
        self.assertFalse(second["reused"])
        self.assertNotEqual(first["material"], second["material"])
        self.assertEqual(self.target.material_slots[0].material.name, first["material"])

    def test_failed_map_set_preserves_target_and_allocates_nothing(self):
        path = self.image()
        bad = self.image("wrong-size.png", width=4, height=4)
        before = (
            set(bpy.data.materials),
            set(bpy.data.images),
            self.target.data,
            len(self.target.material_slots),
        )
        for maps in (
            {"normal": {"filename": path.name}},
            {"base_color": {"filename": path.name}, "roughness": {"filename": bad.name}},
            {"base_color": {"filename": path.name}, "height": {"filename": "missing.png"}},
            {"base_color": {"filename": path.name, "sha256": "0" * 64}},
        ):
            with self.subTest(maps=maps), self.assertRaises(ValueError):
                self.material(maps)
            self.assertEqual(
                before,
                (
                    set(bpy.data.materials),
                    set(bpy.data.images),
                    self.target.data,
                    len(self.target.material_slots),
                ),
            )

    def test_material_restore_refuses_edited_graph_and_copied_geometry(self):
        self.material()
        material = self.target.active_material
        next(n for n in material.node_tree.nodes if n.type == "BSDF_PRINCIPLED").inputs[
            "IOR"
        ].default_value = 2.0
        with self.assertRaisesRegex(ValueError, "changed"):
            self.tools.restore_agent_material({"object_name": self.target.name})
        self.assertEqual(self.target.active_material, material)
        self.target.data.materials.clear()
        shared = bpy.data.objects.new("Shared", self.target.data)
        bpy.context.scene.collection.objects.link(shared)
        self.material()
        self.target.data.vertices[0].co.x += 1
        with self.assertRaisesRegex(ValueError, "copied mesh"):
            self.tools.restore_agent_material({"object_name": self.target.name})

    def test_packed_maps_and_environment_survive_source_removal_save_reopen(self):
        result = self.material()
        panorama = self.image("panorama.png", width=16, height=8)
        environment = self.tools.import_agent_environment({"filename": panorama.name})
        expected = {item["image"]: item["sha256"] for item in result["maps"].values()}
        expected[environment["image"]] = environment["sha256"]
        with tempfile.TemporaryDirectory(prefix="assistant-materials-") as directory:
            path = Path(directory) / "packed.blend"
            bpy.ops.wm.save_as_mainfile(filepath=str(path), check_existing=False)
            for source in self.files:
                source.unlink(missing_ok=True)
            bpy.ops.wm.open_mainfile(filepath=str(path))
            for name, digest in expected.items():
                image = bpy.data.images[name]
                self.assertEqual(hashlib.sha256(image.packed_file.data).hexdigest(), digest)
            self.assertEqual(bpy.context.scene.world.name, environment["world"])
            self.assertEqual(
                bpy.data.objects[result["object"]].active_material.name, result["material"]
            )
            with self.assertRaisesRegex(ValueError, "live file session"):
                self.tools.restore_agent_environment({})
            reset_scene()

    def test_environment_formats_packing_reuse_shared_world_and_restore(self):
        original = bpy.context.scene.world
        other = bpy.data.scenes.new("Other scene")
        other.world = original
        for suffix, file_format in (
            (".png", "PNG"),
            (".jpg", "JPEG"),
            (".exr", "OPEN_EXR"),
            (".hdr", "HDR"),
        ):
            with self.subTest(format=file_format):
                path = self.image("panorama" + suffix, width=16, height=8, file_format=file_format)
                result = self.tools.import_agent_environment(
                    {"filename": path.name, "strength": 0.5}
                )
                self.assertEqual(other.world, original)
                world = bpy.context.scene.world
                env = next(n for n in world.node_tree.nodes if n.type == "TEX_ENVIRONMENT")
                self.assertEqual(env.projection, "EQUIRECTANGULAR")
                self.assertTrue(env.image.packed_file)
                self.assertEqual(len(world.node_tree.links), 2)
                self.assertEqual(result["floating_point"], file_format in {"OPEN_EXR", "HDR"})
                self.assertTrue(
                    self.tools.import_agent_environment({"filename": path.name, "strength": 0.5})[
                        "reused"
                    ]
                )
                self.tools.restore_agent_environment({})
                self.assertEqual(bpy.context.scene.world, original)

    def test_environment_refuses_non_panorama_mismatched_suffix_and_edits(self):
        original = bpy.context.scene.world
        square = self.image()
        before = set(bpy.data.worlds), set(bpy.data.images)
        with self.assertRaises(ValueError):
            self.tools.import_agent_environment({"filename": square.name})
        self.assertEqual(before, (set(bpy.data.worlds), set(bpy.data.images)))
        self.assertEqual(bpy.context.scene.world, original)
        panorama = self.image("panorama.png", width=16, height=8)
        false_exr = self.inbox / "false.exr"
        false_exr.write_bytes(panorama.read_bytes())
        self.files.append(false_exr)
        with self.assertRaisesRegex(ValueError, "actual image format"):
            self.tools.import_agent_environment({"filename": false_exr.name})
        self.tools.import_agent_environment({"filename": panorama.name})
        world = bpy.context.scene.world
        next(n for n in world.node_tree.nodes if n.type == "BACKGROUND").inputs[
            "Strength"
        ].default_value = 2
        with self.assertRaisesRegex(RuntimeError, "edited"):
            self.tools.restore_agent_environment({})
        self.assertEqual(bpy.context.scene.world, world)

    def test_hdr_header_dimensions_and_truncated_scanlines_are_rejected(self):
        original = bpy.context.scene.world
        path = self.image("panorama.hdr", width=16, height=8, file_format="HDR")
        good = path.read_bytes()
        for data in (
            good[:-3],
            good.replace(b"-Y 8 +X 16", b"-Y 8 +X 17"),
            good.replace(b"32-bit_rle_rgbe", b"32-bit_rle_xyze"),
        ):
            path.write_bytes(data)
            with self.assertRaises(ValueError):
                self.tools.import_agent_environment({"filename": path.name})
            self.assertEqual(bpy.context.scene.world, original)

    def test_handlers_remove_restore_authority(self):
        self.material()
        for handlers in (
            bpy.app.handlers.load_pre,
            bpy.app.handlers.undo_pre,
            bpy.app.handlers.redo_pre,
        ):
            self.assertIn(self.helper._clear, handlers)
        self.helper._clear()
        with self.assertRaisesRegex(ValueError, "live file session"):
            self.tools.restore_agent_material({"object_name": self.target.name})
