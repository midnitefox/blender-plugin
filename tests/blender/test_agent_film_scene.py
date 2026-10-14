# SPDX-FileCopyrightText: 2026 Open Studio contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Independent native shot construction, clip timing, cut ranges and rollback."""

import hashlib
import json
import sys
import tempfile
import unittest
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

import bpy
from helpers import animated_glb, reset_scene, submodule


def recipe():
    return {
        "name": "Assistant shot",
        "fps": 24,
        "width": 640,
        "height": 360,
        "frames": 48,
        "primitives": [
            {
                "id": "ground",
                "name": "Ground",
                "primitive": "plane",
                "category": "floor",
                "position": [0, 0, -0.1],
                "size": [12, 12, 0.2],
            }
        ],
        "camera": {
            "lens_mm": 40,
            "path": [{"frame": 1, "position": [2, -6, 3]}, {"frame": 48, "position": [6, -6, 3]}],
            "target_path": [
                {"frame": 1, "position": [0, 0, 1]},
                {"frame": 48, "position": [2, 0, 1]},
            ],
        },
        "actors": [],
    }


class AgentFilmSceneTests(unittest.TestCase):
    def setUp(self):
        reset_scene()
        self.owner = submodule("blender.assistant_film_scene")
        self.tools = submodule("mcp.tools_agent_film_scene")
        self.original = bpy.context.scene
        self.original.frame_set(17, subframe=0.25)
        self.original.render.fps = 30
        self.keep = bpy.data.objects.new("Keep selected", None)
        self.original.collection.objects.link(self.keep)
        self.keep.select_set(True)
        bpy.context.view_layer.objects.active = self.keep
        self.inbox = submodule("mcp.tools_agent")._inbox()
        self.path = self.inbox / "shot-actor-test.glb"
        self.path.write_bytes(animated_glb(clips=2, morph=True))

    def tearDown(self):
        self.path.unlink(missing_ok=True)
        reset_scene()

    def actor(self, identifier="actor", index=1, **extra):
        return dict(
            {
                "id": identifier,
                "name": identifier,
                "filename": self.path.name,
                "sha256": hashlib.sha256(self.path.read_bytes()).hexdigest(),
                "position": [0, 0, 0],
                "clip": {"animation_index": index, "mode": "once"},
            },
            **extra,
        )

    def shot(self, plan=None):
        return self.tools.build_agent_shot({"plan": plan or recipe()})

    def timeline(self, *shots):
        return {
            "name": "Director cut",
            "fps": 24,
            "width": 640,
            "height": 360,
            "shots": [
                {
                    "scene_name": s["scene_name"],
                    "scene_id": s["scene_id"],
                    "start_frame": 5,
                    "end_frame": 16,
                }
                for s in shots
            ],
        }

    def context_preserved(self):
        self.assertEqual(bpy.context.scene, self.original)
        self.assertEqual(bpy.context.view_layer.objects.active, self.keep)
        self.assertEqual(set(bpy.context.selected_objects), {self.keep})
        self.assertEqual((self.original.frame_current, self.original.frame_subframe), (17, 0.25))
        self.assertEqual(self.original.render.fps, 30)

    def test_complete_shot_has_real_primitives_camera_motion_and_persistent_recipe(self):
        plan = recipe()
        moving = dict(
            plan["primitives"][0],
            id="prop",
            name="Moving prop",
            primitive="box",
            category="prop",
            position=[0, 0, 1],
            size=[2, 2, 2],
        )
        moving["motion"] = [
            {
                "frame": frame,
                "position": position,
                "rotation_degrees": [0, 0, rotation],
                "scale": scale,
            }
            for frame, position, rotation, scale in (
                (1, [0, 0, 1], 0, [1, 1, 1]),
                (48, [4, 0, 2], 90, [2, 1, 1]),
            )
        ]
        plan["primitives"].append(moving)
        result = self.shot(plan)
        self.assertEqual(result["engine"], "BLENDER_EEVEE")
        scene = bpy.data.scenes[result["scene_name"]]
        self.assertEqual(scene[self.owner.IDENTITY], result["scene_id"])
        self.assertEqual(scene[self.owner.KIND], "shot")
        self.assertEqual(json.loads(scene[self.owner.RECIPE])["fps"], 24)
        bpy.context.window.scene = scene
        obj = next(o for o in scene.objects if o.get(self.owner.ELEMENT) == "prop")
        scene.frame_set(48)
        bpy.context.view_layer.update()
        self.assertEqual(tuple(obj.location), (4, 0, 2))
        self.assertEqual(tuple(obj.scale), (4, 2, 2))
        self.assertEqual(tuple(scene.camera.location), (6, -6, 3))
        self.assertEqual(tuple(scene.camera.constraints[0].target.location), (2, 0, 1))
        self.assertAlmostEqual(obj.data.materials[0].diffuse_color[0], 0.85)
        bpy.context.window.scene = self.original
        self.context_preserved()
        self.assertFalse(
            any(
                name.endswith((".sdk_adapter", ".job_session", ".film_jobs"))
                for name in sys.modules
            )
        )

    def test_actors_import_independent_rigs_and_selected_second_clip_with_real_timing(self):
        plan = recipe()
        plan["actors"] = [self.actor("first"), self.actor("second", position=[5, 0, 0])]
        scene = bpy.data.scenes[self.shot(plan)["scene_name"]]
        meshes = [
            o for o in scene.objects if o.type == "MESH" and o.get("open_studio_film_actor_id")
        ]
        rigs = [o for o in scene.objects if o.type == "ARMATURE"]
        self.assertEqual(len(meshes), 2)
        self.assertEqual(len(rigs), 2)
        self.assertNotEqual(meshes[0].data, meshes[1].data)
        self.assertNotEqual(rigs[0].data, rigs[1].data)
        self.assertNotEqual(
            rigs[0].animation_data.nla_tracks[0].strips[0].action,
            rigs[1].animation_data.nla_tracks[0].strips[0].action,
        )
        bpy.context.window.scene = scene
        positions = []
        for frame in (1, 25):
            scene.frame_set(frame)
            bpy.context.view_layer.update()
            graph = bpy.context.evaluated_depsgraph_get()
            positions.append([obj.evaluated_get(graph).data.vertices[0].co.x for obj in meshes])
        for before, after in zip(*positions, strict=True):
            self.assertAlmostEqual(after - before, 4, places=4)
        self.assertTrue(
            all(image.packed_file for image in bpy.data.images if image.type == "IMAGE")
        )
        bpy.context.window.scene = self.original
        self.context_preserved()

    def test_hold_loop_and_actor_transform_motion_are_independent(self):
        plan = recipe()
        plan["frames"] = 60
        plan["actors"] = [
            self.actor("held", clip={"animation_index": 1, "mode": "hold", "phase": 0.25}),
            self.actor("loop", clip={"animation_index": 0, "mode": "loop", "speed": 1}),
        ]
        plan["actors"][1]["motion"] = [
            {
                "frame": frame,
                "position": position,
                "rotation_degrees": [0, 0, 0],
                "scale": [1, 1, 1],
            }
            for frame, position in ((1, [0, 0, 0]), (37, [3, 0, 0]))
        ]
        scene = bpy.data.scenes[self.shot(plan)["scene_name"]]
        bpy.context.window.scene = scene
        meshes = {
            o["open_studio_film_actor_id"]: o
            for o in scene.objects
            if o.type == "MESH" and o.get("open_studio_film_actor_id")
        }
        values = []
        for frame in (13, 37):
            scene.frame_set(frame)
            bpy.context.view_layer.update()
            graph = bpy.context.evaluated_depsgraph_get()
            values.append(
                {key: obj.evaluated_get(graph).data.vertices[0].co.x for key, obj in meshes.items()}
            )
        self.assertAlmostEqual(values[0]["held"], values[1]["held"], places=4)
        self.assertAlmostEqual(values[0]["loop"], values[1]["loop"], places=4)
        self.assertAlmostEqual(values[0]["held"], 1, places=4)
        self.assertAlmostEqual(values[0]["loop"], 1, places=4)
        root = next(obj for obj in scene.objects if obj.get(self.owner.ELEMENT) == "loop")
        self.assertEqual(tuple(root.location), (3, 0, 0))

    def test_invalid_complete_recipe_actor_digest_and_clip_refuse_before_scene_mutation(self):
        before = self.owner._snapshot()
        plan = recipe()
        for actor in (
            self.actor(clip={"animation_index": 10, "mode": "once"}),
            self.actor(clip=None),
            self.actor(sha256="0" * 64),
        ):
            plan["actors"] = [actor]
            with self.assertRaises(ValueError):
                self.shot(plan)
            self.assertEqual(self.owner._snapshot(), before)
        plan = recipe()
        plan["primitives"][0]["size"] = [0, 1, 1]
        with self.assertRaises(ValueError):
            self.shot(plan)
        self.assertEqual(self.owner._snapshot(), before)
        self.context_preserved()

    def test_failed_second_actor_rolls_back_all_created_data_and_context(self):
        before = self.owner._snapshot()
        plan = recipe()
        plan["actors"] = [self.actor("one"), self.actor("two")]
        original = self.owner._actor

        def fail_second(scene, spec, *args):
            if spec["id"] == "two":
                raise ValueError("Synthetic actor failure")
            return original(scene, spec, *args)

        with (
            patch.object(self.owner, "_actor", side_effect=fail_second),
            self.assertRaises(ValueError),
        ):
            self.shot(plan)
        self.assertEqual(self.owner._snapshot(), before)
        self.context_preserved()
        self.assertFalse(list(self.inbox.glob("shot-*/*")))

    def test_timeline_builds_real_trimmed_scene_strips_and_preserves_existing_content(self):
        first, second = self.shot(), self.shot()
        self.assertNotEqual(first["scene_id"], second["scene_id"])
        plan = self.timeline(first, second)
        plan["shots"][1].update(start_frame=20, end_frame=24)
        result = self.tools.build_agent_timeline({"plan": plan})
        timeline = bpy.data.scenes[result["scene_name"]]
        strips = list(timeline.sequence_editor.strips)
        self.assertEqual(
            [(s.frame_final_start, s.frame_final_end) for s in strips], [(1, 13), (13, 18)]
        )
        self.assertEqual([s.animation_offset_start for s in strips], [4, 19])
        self.assertEqual([s.animation_offset_end for s in strips], [32, 24])
        self.assertEqual(
            [s.scene.name for s in strips], [first["scene_name"], second["scene_name"]]
        )
        self.assertEqual([s.scene_input for s in strips], ["CAMERA", "CAMERA"])
        self.assertEqual(result["frames"], 17)
        self.assertEqual([marker.frame for marker in timeline.timeline_markers], [1, 13])
        self.context_preserved()

    def test_timeline_refuses_stale_duplicated_identity_timing_range_and_missing_camera(self):
        first = self.shot()
        source = bpy.data.scenes[first["scene_name"]]
        good = self.timeline(first)
        for field, value in (("scene_id", "0" * 32), ("start_frame", 0), ("end_frame", 49)):
            plan = deepcopy(good)
            plan["shots"][0][field] = value
            before = self.owner._snapshot()
            with self.assertRaises(ValueError):
                self.tools.build_agent_timeline({"plan": plan})
            self.assertEqual(self.owner._snapshot(), before)
        source.render.fps = 25
        with self.assertRaises(ValueError):
            self.tools.build_agent_timeline({"plan": good})
        source.render.fps = 24
        camera, source.camera = source.camera, None
        with self.assertRaises(ValueError):
            self.tools.build_agent_timeline({"plan": good})
        source.camera = camera
        duplicate = source.copy()
        with self.assertRaises(ValueError):
            self.tools.build_agent_timeline({"plan": good})
        bpy.data.scenes.remove(duplicate)
        self.context_preserved()

    def test_failed_second_timeline_strip_rolls_back_without_changing_source_scenes(self):
        first, second = self.shot(), self.shot()
        plan = self.timeline(first, second)
        before = self.owner._snapshot()
        original = self.owner._timeline_strip

        def fail_second(editor, shot, source, cursor, index):
            if index == 2:
                raise ValueError("Synthetic second cut failure")
            return original(editor, shot, source, cursor, index)

        with (
            patch.object(self.owner, "_timeline_strip", side_effect=fail_second),
            self.assertRaises(ValueError),
        ):
            self.tools.build_agent_timeline({"plan": plan})
        self.assertEqual(self.owner._snapshot(), before)
        self.assertEqual(bpy.data.scenes[first["scene_name"]].frame_start, 1)
        self.context_preserved()

    def test_packed_actors_recipe_and_scene_strip_links_survive_save_reopen(self):
        plan = recipe()
        plan["actors"] = [self.actor()]
        shot = self.shot(plan)
        result = self.tools.build_agent_timeline({"plan": self.timeline(shot)})
        self.path.unlink()
        with tempfile.TemporaryDirectory(prefix="film-save-") as directory:
            path = Path(directory) / "film.blend"
            bpy.ops.wm.save_as_mainfile(filepath=str(path), check_existing=False)
            bpy.ops.wm.open_mainfile(filepath=str(path))
            scene = bpy.data.scenes[result["scene_name"]]
            source = scene.sequence_editor.strips[0].scene
            self.assertEqual(source[self.owner.IDENTITY], shot["scene_id"])
            self.assertEqual(scene[self.owner.IDENTITY], result["scene_id"])
            self.assertEqual(
                json.loads(source[self.owner.RECIPE])["actors"][0]["clip"]["animation_index"], 1
            )
            self.assertTrue(any(o.type == "ARMATURE" for o in source.objects))
            self.assertTrue(
                all(image.packed_file for image in bpy.data.images if image.type == "IMAGE")
            )
            reset_scene()

    @unittest.skipIf(bpy.app.background, "Real desktop Undo/Redo acceptance runs in the GUI probe")
    def test_native_undo_redo_removes_only_new_shot_and_timeline(self):
        prefs = bpy.context.preferences.edit
        previous = prefs.use_global_undo, prefs.undo_steps
        prefs.use_global_undo, prefs.undo_steps = True, 32
        try:
            name = self.original.name
            shot = self.shot()
            self.assertTrue(shot["undo_available"])
            self.assertEqual(bpy.ops.ed.undo(), {"FINISHED"})
            self.assertNotIn(shot["scene_name"], bpy.data.scenes)
            self.assertIn(name, bpy.data.scenes)
            self.assertEqual(bpy.ops.ed.redo(), {"FINISHED"})
            self.assertEqual(
                bpy.data.scenes[shot["scene_name"]][self.owner.IDENTITY], shot["scene_id"]
            )
            result = self.tools.build_agent_timeline({"plan": self.timeline(shot)})
            self.assertTrue(result["undo_available"])
            self.assertEqual(bpy.ops.ed.undo(), {"FINISHED"})
            self.assertNotIn(result["scene_name"], bpy.data.scenes)
            self.assertIn(shot["scene_name"], bpy.data.scenes)
            self.assertEqual(bpy.ops.ed.redo(), {"FINISHED"})
            timeline = bpy.data.scenes[result["scene_name"]]
            self.assertEqual(timeline[self.owner.IDENTITY], result["scene_id"])
            self.assertEqual(
                timeline.sequence_editor.strips[0].scene[self.owner.IDENTITY], shot["scene_id"]
            )
        finally:
            prefs.use_global_undo, prefs.undo_steps = previous
