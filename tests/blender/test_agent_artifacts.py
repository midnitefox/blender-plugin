# SPDX-FileCopyrightText: 2026 Open Studio contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Assistant image handoff in the installed extension, with provider keys empty."""

import hashlib
import shutil
import tempfile
import unittest
from pathlib import Path

import bpy
from helpers import FIXTURES, reset_scene, submodule


class AgentArtifactTests(unittest.TestCase):
    def setUp(self):
        reset_scene()
        self.tools = submodule("mcp.tools_agent")
        self.prefs = submodule("prefs").get_prefs()
        self.previous_mode = self.prefs.provider_mode
        self.prefs.provider_mode = "AGENT"
        self.inbox = Path(self.tools.agent_workspace({})["inbox"])
        self.source = self.inbox / "agent-native-test.png"
        shutil.copyfile(FIXTURES / "patina-copper-512" / "albedo.png", self.source)

    def tearDown(self):
        self.source.unlink(missing_ok=True)
        self.prefs.provider_mode = self.previous_mode

    def test_reference_is_packed_and_repeat_reuses_it(self):
        digest = hashlib.sha256(self.source.read_bytes()).hexdigest()
        first = self.tools.import_agent_image({"filename": self.source.name, "sha256": digest})
        second = self.tools.import_agent_image({"filename": self.source.name})
        self.assertEqual(first["object"], second["object"])
        image = bpy.data.images[first["image"]]
        self.assertEqual(tuple(image.size), (512, 512))
        self.assertTrue(image.packed_file)
        self.source.unlink()
        self.assertTrue(image.packed_file)
        self.assertEqual(bpy.data.objects[first["object"]].data, image)

    def test_texture_preserves_shared_mesh_other_object_material(self):
        bpy.ops.mesh.primitive_cube_add()
        target = bpy.context.active_object
        original = bpy.data.materials.new("Original")
        target.data.materials.append(original)
        other = bpy.data.objects.new("Other", target.data)
        bpy.context.scene.collection.objects.link(other)
        result = self.tools.import_agent_image(
            {
                "filename": self.source.name,
                "mode": "TEXTURE",
                "object_name": target.name,
            }
        )
        self.assertEqual(other.active_material, original)
        self.assertNotEqual(target.active_material, original)
        self.assertEqual(result["material"], target.active_material.name)
        node = next(n for n in target.active_material.node_tree.nodes if n.type == "TEX_IMAGE")
        self.assertTrue(node.image.packed_file)

    def test_reference_and_texture_survive_save_reopen_without_inbox_source(self):
        bpy.ops.mesh.primitive_cube_add()
        target = bpy.context.active_object
        reference = self.tools.import_agent_image({"filename": self.source.name})
        texture = self.tools.import_agent_image(
            {"filename": self.source.name, "mode": "TEXTURE", "object_name": target.name}
        )
        digest = reference["sha256"]
        with tempfile.TemporaryDirectory(prefix="open-studio-packed-") as directory:
            path = Path(directory) / "image-handoff.blend"
            bpy.ops.wm.save_as_mainfile(filepath=str(path), check_existing=False)
            self.source.unlink()
            bpy.ops.wm.open_mainfile(filepath=str(path))
            image = bpy.data.images[reference["image"]]
            self.assertEqual(hashlib.sha256(image.packed_file.data).hexdigest(), digest)
            self.assertEqual(tuple(image.size), (512, 512))
            self.assertEqual(bpy.data.objects[reference["object"]].data, image)
            material = bpy.data.objects[texture["object"]].active_material
            self.assertEqual(material.name, texture["material"])
            node = next(n for n in material.node_tree.nodes if n.type == "TEX_IMAGE")
            self.assertEqual(node.image, image)
            # Release the open file before the temporary directory is removed.
            reset_scene()

    def test_bad_image_does_not_create_scene_objects(self):
        self.source.write_bytes(b"\x89PNG\r\n\x1a\ninvalid")
        before = set(bpy.data.objects)
        with self.assertRaises(ValueError):
            self.tools.import_agent_image({"filename": self.source.name})
        self.assertEqual(set(bpy.data.objects), before)

    def test_tools_available_in_agent_mode_without_credentials(self):
        registry = submodule("blender.mcp_service").build_registry()
        self.assertIn("agent_workspace", registry.names())
        self.assertIn("import_agent_image", registry.names())
        self.assertFalse(self.tools.agent_workspace({})["generation_provider_required"])
        self.assertFalse(submodule("blender.runtime").credentials().valid)
