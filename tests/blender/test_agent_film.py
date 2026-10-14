# SPDX-FileCopyrightText: 2026 Open Studio contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Real scene discovery, editorial inspection and guarded activation."""

import json
import unittest
import uuid
from unittest.mock import patch

import bpy
from helpers import reset_scene, submodule


class AgentFilmTests(unittest.TestCase):
    def setUp(self):
        reset_scene()
        self.core = submodule("blender.assistant_film")
        self.tools = submodule("mcp.tools_agent_film")
        self.original = bpy.context.scene
        self.created = []

    def tearDown(self):
        bpy.context.window.scene = self.original
        for scene in reversed(self.created):
            if scene.name in bpy.data.scenes:
                bpy.data.scenes.remove(scene)

    def scene(self, name, kind="shot"):
        scene = bpy.data.scenes.new(name)
        scene[self.core.KIND] = kind
        scene[self.core.IDENTITY] = uuid.uuid4().hex
        scene[self.core.RECIPE] = json.dumps({"name": name, "frames": 48})
        scene.frame_end = 48
        scene.render.fps = 24
        self.created.append(scene)
        return scene

    def test_inspection_reports_live_contents_separately_from_build_recipe(self):
        shot = self.scene("Editable shot")
        shot.frame_end = 72
        with patch.object(self.core.file_state, "mark_modified") as mark:
            result = self.tools.inspect_agent_film(
                {
                    "scene_name": shot.name,
                    "scene_id": shot[self.core.IDENTITY],
                    "include_recipe": True,
                }
            )
            mark.assert_not_called()
        self.assertEqual(result["frame_end"], 72)
        self.assertEqual(result["recipe"]["frames"], 48)
        self.assertIn("Original build inputs", result["recipe_role"])
        self.assertTrue(result["identity_unique"])
        self.assertEqual(bpy.context.scene, self.original)
        listed = self.tools.inspect_agent_film({})
        self.assertIn(shot.name, [entry["scene_name"] for entry in listed["scenes"]])

    def test_scene_strip_inspection_has_actual_trim_and_identity(self):
        shot = self.scene("Referenced shot")
        timeline = self.scene("Editable timeline", "timeline")
        strip = timeline.sequence_editor_create().strips.new_scene("Cut", shot, 1, 1)
        strip.scene_input = "CAMERA"
        strip.frame_offset_start = 12
        result = self.core.inspect(timeline.name)
        self.assertEqual(result["strip_count"], 1)
        entry = result["strips"][0]
        self.assertEqual(entry["scene_id"], shot[self.core.IDENTITY])
        self.assertEqual(entry["source_frame_offset"], 12)
        self.assertEqual(entry["frame_count"], strip.frame_final_duration)

    def test_activation_preserves_scene_contents_and_marks_only_actual_switch(self):
        shot = self.scene("Switch target")
        bpy.ops.mesh.primitive_cube_add()
        cube = bpy.context.object
        names = {obj.name for obj in self.original.objects}
        with patch.object(self.core.file_state, "mark_modified") as mark:
            result = self.tools.activate_agent_scene(
                {"scene_name": shot.name, "scene_id": shot[self.core.IDENTITY]}
            )
            self.assertTrue(result["changed"])
            self.assertEqual(bpy.context.scene, shot)
            self.assertEqual(result["previous_scene"], self.original.name)
            self.assertFalse(self.core.activate(shot.name)["changed"])
            mark.assert_called_once()
        self.assertEqual(names, {obj.name for obj in self.original.objects})
        self.core.activate(self.original.name)
        self.assertTrue(cube.select_get())

    def test_wrong_or_duplicated_identity_cannot_activate(self):
        shot = self.scene("Identity target")
        with self.assertRaisesRegex(ValueError, "identity changed"):
            self.core.activate(shot.name, uuid.uuid4().hex)
        copied = self.scene("Copied identity")
        copied[self.core.IDENTITY] = shot[self.core.IDENTITY]
        self.assertFalse(self.core.inspect(shot.name)["identity_unique"])
        with self.assertRaisesRegex(ValueError, "duplicated"):
            self.core.activate(shot.name, shot[self.core.IDENTITY])
        self.assertEqual(bpy.context.scene, self.original)

    def test_malformed_recipe_is_reported_without_affecting_inspection(self):
        shot = self.scene("Malformed recipe")
        shot[self.core.RECIPE] = "{broken"
        result = self.core.inspect(shot.name, include_recipe=True)
        self.assertIsNone(result["recipe"])
        self.assertIn("malformed", result["recipe_error"])
        shot[self.core.KIND] = [1, 2]
        shot[self.core.RECIPE] = "x" * (self.core.MAX_RECIPE_BYTES + 1)
        result = self.core.inspect(shot.name, include_recipe=True)
        self.assertEqual(result["kind"], "scene")
        self.assertIn("size", result["recipe_error"])
        self.assertNotIn("recipe", self.core.inspect(shot.name))

    def test_edit_mode_switch_refuses_and_keeps_original_context(self):
        shot = self.scene("Mode target")
        bpy.ops.mesh.primitive_cube_add()
        bpy.ops.object.mode_set(mode="EDIT")
        try:
            with self.assertRaisesRegex(ValueError, "Object Mode"):
                self.core.activate(shot.name)
            self.assertEqual(bpy.context.scene, self.original)
        finally:
            bpy.ops.object.mode_set(mode="OBJECT")


if __name__ == "__main__":
    unittest.main()
