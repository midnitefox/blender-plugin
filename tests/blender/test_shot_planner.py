# SPDX-FileCopyrightText: 2026 Scenario Inc.
# SPDX-License-Identifier: GPL-3.0-or-later
import importlib
import os
import sys
import unittest

import bpy
from helpers import ROOT, reset_scene, submodule
from mathutils import Vector


def load_shot_planner():
    """The installed extension when it ships the module, else (or with SCENARIO_SHOT_SOURCE=1) the source tree."""
    module = None
    if os.environ.get("SCENARIO_SHOT_SOURCE") != "1":
        try:
            module = submodule("blender.shot_planner")
        except ImportError:
            module = None
    if module is None:
        if str(ROOT) not in sys.path:
            sys.path.insert(0, str(ROOT))
        module = importlib.import_module("scenario.blender.shot_planner")
        if hasattr(bpy.types.Scene, "scenario_shot") and not hasattr(
            bpy.types, "SCENARIO_OT_shot_clear_path"
        ):
            # an older installed planner is registered: swap it for the source tree version
            installed = submodule("blender.shot_planner")
            installed.unregister()
    if not hasattr(bpy.types.Scene, "scenario_shot"):
        module.register()
    return module


class ShotPlannerTests(unittest.TestCase):
    def setUp(self):
        reset_scene()
        self.sp = load_shot_planner()
        self.shot_plan = self.sp.shot_plan
        bpy.ops.mesh.primitive_cube_add(size=2.0, location=(0.0, 0.0, 1.0))
        self.scene = bpy.context.scene
        self.props = self.scene.scenario_shot
        self.fps = self.scene.render.fps / (self.scene.render.fps_base or 1.0)

    def _location_keys(self, cam):
        curves = [fc for fc in self.sp.fcurves_of(cam) if fc.data_path == "location"]
        self.assertEqual(len(curves), 3)
        return sorted({int(k.co[0]) for k in curves[0].keyframe_points})

    def _camera_at(self, cam, frame):
        self.scene.frame_set(frame)
        return cam.matrix_world.translation.copy()

    def test_plan_from_description_applies_a_hold_to_the_arrival_marker(self):
        self.props.description = "dolly in, hold 2 s"
        bpy.ops.scenario.shot_from_description()
        markers = self.sp.marker_objects(self.scene)
        self.assertTrue(markers)
        self.assertAlmostEqual(
            float(markers[-1]["scenario_hold"]), 2.0
        )  # the pause lands on the last waypoint

    def test_active_marker_helper_finds_a_selected_shot_marker(self):
        bpy.ops.scenario.shot_add_marker()
        marker = self.sp.marker_objects(self.scene)[0]
        bpy.context.view_layer.objects.active = marker
        self.assertEqual(self.sp.active_marker(bpy.context), marker)
        self.assertIn("scenario_hold", marker)

    def test_markers_are_numbered_cameras_and_build_a_marker_path(self):
        self.scene.cursor.location = (4.0, -4.0, 2.0)
        bpy.ops.scenario.shot_add_marker()
        self.scene.cursor.location = (-4.0, -4.0, 3.0)
        bpy.ops.scenario.shot_add_marker()
        markers = self.sp.marker_objects(self.scene)
        self.assertEqual([m.name for m in markers], ["Shot 1", "Shot 2"])
        self.assertEqual([int(m["scenario_shot_index"]) for m in markers], [1, 2])
        self.assertTrue(all(m.type == "CAMERA" and m.show_name for m in markers))
        self.assertIn(markers[0].name, bpy.data.collections["Open Studio Shots"].objects)
        self.assertEqual(self.props.markers_source, "markers")
        self.props.duration = 5.0
        bpy.ops.scenario.shot_build_path()
        cam = self.sp.shot_camera(self.scene)
        self.assertIsNotNone(cam)
        self.assertEqual(self.scene.camera, cam)
        frames = self._location_keys(cam)
        self.assertEqual(frames[0], 1)
        self.assertEqual(frames[-1], round(5.0 * self.fps))
        self.assertEqual(self.scene.frame_end, frames[-1])
        self.assertEqual(self.scene.frame_start, 1)
        self.assertEqual(cam["scenario_shot_source"], "markers")
        self.assertEqual(cam["scenario_shot_frames"], round(5.0 * self.fps))
        # the path starts at marker 1 and ends at marker 2 (hand-placed markers do not loop)
        self.assertFalse(self.props.closed_loop)
        self.assertAlmostEqual(
            (self._camera_at(cam, 1) - markers[0].matrix_world.translation).length, 0.0, places=4
        )
        self.assertAlmostEqual(
            (self._camera_at(cam, frames[-1]) - markers[1].matrix_world.translation).length,
            0.0,
            places=4,
        )
        constraint = cam.constraints.get("Open Studio Aim")
        self.assertIsNotNone(constraint)
        self.assertEqual(constraint.target.name, "Shot Target")

    def test_build_without_markers_places_them_from_the_move(self):
        self.props.preset = "orbit"
        self.props.duration = 4.0
        self.props.focal = 35.0
        bpy.ops.scenario.shot_build_path()
        markers = self.sp.marker_objects(self.scene)
        self.assertEqual(
            len(markers), 12
        )  # an orbit gives 12 editable markers, the loop closes at build time
        self.assertEqual([m.name for m in markers][:3], ["Shot 1", "Shot 2", "Shot 3"])
        self.assertTrue(self.props.closed_loop)
        self.assertEqual(self.props.markers_source, "orbit")
        cam = self.sp.shot_camera(self.scene)
        frames = self._location_keys(cam)
        self.assertEqual(len(frames), 13)  # 12 markers + the closing key
        self.assertEqual(frames[-1], round(4.0 * self.fps))
        self.assertEqual(cam["scenario_shot_source"], "orbit")
        target = bpy.data.objects["Shot Target"]
        self.assertAlmostEqual(target.location.z, 1.0, places=4)
        first = self._camera_at(cam, 1)
        self.assertAlmostEqual((self._camera_at(cam, frames[-1]) - first).length, 0.0, places=3)
        self.assertGreater((self._camera_at(cam, frames[6]) - first).length, 1.0)

    def test_every_closed_move_returns_to_its_start_through_markers(self):
        for name in self.shot_plan.CLOSED_PRESETS:
            self.props.preset = name
            self.props.duration = 5.0
            bpy.ops.scenario.shot_place_markers()
            markers = self.sp.marker_objects(self.scene)
            self.assertGreaterEqual(len(markers), 3, name)
            self.assertTrue(self.props.closed_loop, name)
            bpy.ops.scenario.shot_build_path()
            cam = self.sp.shot_camera(self.scene)
            first = self._camera_at(cam, self.scene.frame_start)
            last = self._camera_at(cam, self.scene.frame_end)
            self.assertAlmostEqual((last - first).length, 0.0, places=3, msg=name)
            # the first marker is where the path starts
            self.assertAlmostEqual(
                (first - markers[0].matrix_world.translation).length, 0.0, places=3, msg=name
            )

    def test_open_move_places_markers_without_a_loop(self):
        self.props.preset = "dolly_in"
        bpy.ops.scenario.shot_place_markers()
        self.assertFalse(self.props.closed_loop)
        markers = self.sp.marker_objects(self.scene)
        self.assertEqual(
            len(markers), len(self.shot_plan.preset_waypoints("dolly_in", (-1, -1, 0), (1, 1, 2)))
        )
        bpy.ops.scenario.shot_build_path()
        cam = self.sp.shot_camera(self.scene)
        first = self._camera_at(cam, self.scene.frame_start)
        last = self._camera_at(cam, self.scene.frame_end)
        self.assertGreater((last - first).length, 0.5)

    def test_editing_a_placed_marker_changes_the_built_path(self):
        self.props.preset = "orbit"
        self.props.duration = 6.0
        bpy.ops.scenario.shot_place_markers()
        markers = self.sp.marker_objects(self.scene)
        bpy.ops.scenario.shot_build_path()
        cam = self.sp.shot_camera(self.scene)
        frames = self._location_keys(cam)
        marker = markers[3]
        before = self._camera_at(cam, frames[3])
        self.assertAlmostEqual((before - marker.matrix_world.translation).length, 0.0, places=3)
        marker.location = marker.location + Vector((0.0, 0.0, 3.0))
        bpy.context.view_layer.update()
        bpy.ops.scenario.shot_build_path()
        cam = self.sp.shot_camera(self.scene)
        frames = self._location_keys(cam)  # the schedule follows the new segment lengths
        after = self._camera_at(cam, frames[3])
        self.assertAlmostEqual((after - marker.matrix_world.translation).length, 0.0, places=3)
        self.assertAlmostEqual((after - before).length, 3.0, places=3)
        # the loop still closes on the (unchanged) first marker
        self.assertAlmostEqual(
            (
                self._camera_at(cam, self.scene.frame_end)
                - self._camera_at(cam, self.scene.frame_start)
            ).length,
            0.0,
            places=3,
        )

    def test_start_frame_and_duration_drive_the_frame_range(self):
        self.props.preset = "pan"
        self.props.duration = 6.0
        self.props.start_frame = 11
        start, end, fps = self.sp.frame_range(self.scene)
        self.assertEqual((start, end), (11, 11 + round(6.0 * self.fps) - 1))
        bpy.ops.scenario.shot_build_path()
        cam = self.sp.shot_camera(self.scene)
        self.assertEqual(self.scene.frame_start, 11)
        self.assertEqual(self.scene.frame_end, 11 + round(6.0 * self.fps) - 1)
        frames = self._location_keys(cam)
        self.assertEqual((frames[0], frames[-1]), (11, self.scene.frame_end))
        self.assertEqual(cam["scenario_shot_frames"], round(6.0 * self.fps))

    def test_aim_off_keyframes_rotation_and_no_target(self):
        self.props.aim_at_subject = False
        self.props.preset = "dolly_in"
        bpy.ops.scenario.shot_build_path()
        cam = self.sp.shot_camera(self.scene)
        paths = {fc.data_path for fc in self.sp.fcurves_of(cam)}
        self.assertIn("rotation_euler", paths)
        self.assertIsNone(cam.constraints.get("Open Studio Aim"))
        self.assertIsNone(bpy.data.objects.get("Shot Target"))
        # the camera looks at the cube: its -Z axis points from the camera towards the centre
        self.scene.frame_set(1)
        forward = cam.matrix_world.to_quaternion() @ Vector((0.0, 0.0, -1.0))
        towards = (Vector((0.0, 0.0, 1.0)) - cam.matrix_world.translation).normalized()
        self.assertGreater(forward.dot(towards), 0.99)

    def test_from_description_sets_the_settings_places_markers_and_builds(self):
        self.props.description = "slow push in closer, 8s at 50mm"
        bpy.ops.scenario.shot_from_description()
        self.assertEqual(self.props.preset, "dolly_in")
        self.assertAlmostEqual(self.props.duration, 12.0, places=3)
        self.assertAlmostEqual(self.props.focal, 50.0, places=3)
        self.assertEqual(self.props.markers_source, "dolly_in")
        self.assertGreaterEqual(len(self.sp.marker_objects(self.scene)), 2)
        cam = self.sp.shot_camera(self.scene)
        self.assertIsNotNone(cam)
        self.assertAlmostEqual(cam.data.lens, 50.0, places=3)
        self.assertEqual(self.scene.frame_end, round(12.0 * self.fps))

    def test_marker_lens_is_keyframed_when_markers_differ(self):
        self.scene.cursor.location = (3.0, -3.0, 1.0)
        bpy.ops.scenario.shot_add_marker()
        self.scene.cursor.location = (-3.0, -3.0, 1.0)
        bpy.ops.scenario.shot_add_marker()
        markers = self.sp.marker_objects(self.scene)
        markers[1]["scenario_focal"] = 85.0
        markers[1]["scenario_hold"] = 1.0
        self.props.duration = 6.0
        bpy.ops.scenario.shot_build_path()
        cam = self.sp.shot_camera(self.scene)
        lens_curves = [fc for fc in self.sp.fcurves_of(cam.data) if fc.data_path == "lens"]
        self.assertEqual(len(lens_curves), 1)
        frames = self._location_keys(cam)
        self.assertEqual(len(frames), 3)  # marker 1, marker 2, marker 2 held
        self.assertEqual(frames[-1] - frames[-2], round(1.0 * self.fps))

    def test_remove_clear_and_renumber(self):
        for x in (1.0, 2.0, 3.0):
            self.scene.cursor.location = (x, -3.0, 1.0)
            bpy.ops.scenario.shot_add_marker()
        markers = self.sp.marker_objects(self.scene)
        self.sp.remove_marker(markers[0])
        self.sp.renumber_markers(self.scene)
        self.assertEqual([m.name for m in self.sp.marker_objects(self.scene)], ["Shot 1", "Shot 2"])
        bpy.ops.scenario.shot_remove_last_marker()
        self.assertEqual(len(self.sp.marker_objects(self.scene)), 1)
        bpy.ops.scenario.shot_clear_markers()
        self.assertEqual(self.sp.marker_objects(self.scene), [])
        self.assertEqual([o for o in bpy.data.objects if o.name.startswith("Shot ")], [])

    def test_subject_bbox_falls_back_without_meshes(self):
        for obj in list(bpy.data.objects):
            bpy.data.objects.remove(obj, do_unlink=True)
        self.assertEqual(self.sp.subject_bbox(bpy.context), self.sp.FALLBACK_BOX)

    def test_placing_another_move_replaces_the_markers_and_rebuild_replaces_the_path(self):
        bpy.ops.scenario.shot_build_path()  # orbit, 12 markers
        self.assertEqual(len(self.sp.marker_objects(self.scene)), 12)
        self.props.preset = "pan"
        self.props.duration = 3.0
        bpy.ops.scenario.shot_place_markers()
        markers = self.sp.marker_objects(self.scene)
        self.assertEqual(
            len(markers), len(self.shot_plan.preset_waypoints("pan", (-1, -1, 0), (1, 1, 2)))
        )
        self.assertFalse(self.props.closed_loop)
        self.assertEqual(self.props.markers_source, "pan")
        bpy.ops.scenario.shot_build_path()
        cam = self.sp.shot_camera(self.scene)
        frames = self._location_keys(cam)
        self.assertEqual(len(frames), len(markers))
        self.assertEqual(frames[-1], round(3.0 * self.fps))
        self.assertEqual(cam["scenario_shot_source"], "pan")
        self.assertEqual(len([c for c in cam.constraints if c.name == "Open Studio Aim"]), 1)
        self.assertEqual(
            len(
                [
                    o
                    for o in bpy.data.objects
                    if o.type == "CAMERA" and o.name.startswith("Open Studio Shot Camera")
                ]
            ),
            1,
        )

    def test_clear_path_removes_everything_and_restores_the_camera(self):
        bpy.ops.object.camera_add(location=(5.0, -5.0, 3.0))
        original = bpy.context.active_object
        self.scene.camera = original
        bpy.ops.scenario.shot_build_path()
        self.assertEqual(self.scene.camera.name, "Open Studio Shot Camera")
        self.assertEqual(self.props.previous_camera, original.name)
        self.assertTrue(
            self.sp.path_summary(self.scene).startswith("Open Studio Shot Camera, 12 markers")
        )
        bpy.ops.scenario.shot_clear_path()
        self.assertIsNone(self.sp.shot_camera(self.scene))
        self.assertIsNone(bpy.data.objects.get("Shot Target"))
        self.assertEqual(self.sp.marker_objects(self.scene), [])
        self.assertIsNone(bpy.data.collections.get("Open Studio Shots"))
        self.assertEqual(self.scene.camera, original)
        self.assertEqual(self.sp.path_summary(self.scene), "")
        self.assertEqual(self.props.previous_camera, "")

    def test_operators_confirm_only_in_the_gui(self):
        # in --background the confirmation is skipped and the operator runs: the build above proves it; here the
        # poll of Clear path follows the presence of a path
        self.assertFalse(bpy.ops.scenario.shot_clear_path.poll())
        bpy.ops.scenario.shot_build_path()
        self.assertTrue(bpy.ops.scenario.shot_clear_path.poll())
        self.assertTrue(hasattr(bpy.types, "SCENARIO_OT_shot_place_markers"))
