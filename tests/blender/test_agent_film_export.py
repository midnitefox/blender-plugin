# SPDX-FileCopyrightText: 2026 Open Studio contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Local director renders, media reviews, operation retirement and portable reopening."""

import hashlib
import json
import shutil
import struct
import subprocess
import tempfile
import time
import unittest
import uuid
import zipfile
from pathlib import Path
from unittest.mock import patch

import bpy
from helpers import FIXTURES, reset_scene, submodule

REOPEN = r"""
import json, os, sys
import bpy
report, frame = sys.argv[sys.argv.index("--") + 1:]
timeline = bpy.data.scenes["Director"]
paths = list(bpy.utils.blend_paths(absolute=False, packed=False, local=False))
sound = next(s for s in timeline.sequence_editor.strips_all if s.type == "SOUND")
picture = bpy.data.images["Packed texture"]
bpy.context.window.scene = timeline
timeline.render.image_settings.file_format = "PNG"
timeline.render.filepath = frame
timeline.frame_set(1)
bpy.ops.render.render(write_still=True, scene=timeline.name)
result = {
    "scenes": sorted(s.name for s in bpy.data.scenes),
    "paths": paths, "resolved": [os.path.isfile(bpy.path.abspath(p)) for p in paths],
    "packed": picture.packed_file is not None,
    "packed_sha256": __import__("hashlib").sha256(bytes(picture.packed_file.data)).hexdigest(),
    "sound": [sound.volume, sound.frame_final_start, sound.frame_final_end,
              sound.animation_offset_start, sound.animation_offset_end],
    "vertices": len(bpy.data.objects["Red plane"].data.vertices),
    "recipes": {s.name: s.get("open_studio_film_recipe") for s in bpy.data.scenes},
    "identities": [s.get("open_studio_film_id") for s in bpy.data.scenes],
}
open(report, "w", encoding="utf-8").write(json.dumps(result))
"""


class AgentFilmExportTests(unittest.TestCase):
    def setUp(self):
        reset_scene()
        self.module = submodule("blender.assistant_film_export")
        self.tools = submodule("mcp.tools_agent_film_export")
        self.module.register()
        self.root = Path(self.enterContext(tempfile.TemporaryDirectory(prefix="film-local-")))
        self.outbox = self.root / "outbox"
        self.outbox.mkdir()
        self.enterContext(patch.object(self.module, "_outbox", return_value=self.outbox))
        self.working = bpy.context.scene
        self.working.frame_set(11)
        self.media = self.root / "source.mp4"
        self.media.write_bytes((FIXTURES / "synthetic/film-four-seconds-audio.mp4").read_bytes())
        self.created = []

    def tearDown(self):
        self.module._clear()
        deadline = time.monotonic() + 30
        while any(not op.future.done() for op in self.module._operations.values()):
            if time.monotonic() > deadline:
                raise AssertionError("A local export worker survived cancellation")
            time.sleep(0.05)
        self.module._pump()
        self.module._operations.clear()
        bpy.context.window.scene = self.working
        for scene in reversed(self.created):
            if scene in tuple(bpy.data.scenes):
                bpy.data.scenes.remove(scene)

    def scene(self, name, frames=8):
        scene = bpy.data.scenes.new(name)
        self.created.append(scene)
        scene["open_studio_film_id"] = uuid.uuid4().hex
        scene["open_studio_film_recipe"] = json.dumps({"name": name, "frames": frames})
        scene.render.fps = 24
        scene.render.resolution_x = scene.render.resolution_y = 64
        scene.render.resolution_percentage = 100
        scene.frame_end = frames
        scene.view_settings.view_transform = "Standard"
        return scene

    def shot(self, name, color):
        scene = self.scene(name, 4)
        scene["open_studio_film_kind"] = "shot"
        scene.render.engine = "BLENDER_WORKBENCH"
        scene.display.shading.light = "FLAT"
        scene.display.shading.color_type = "OBJECT"
        camera = bpy.data.objects.new(name + " camera", bpy.data.cameras.new(name + " camera"))
        scene.collection.objects.link(camera)
        camera.location = (0, 0, 5)
        camera.data.type = "ORTHO"
        camera.data.ortho_scale = 2
        scene.camera = camera
        mesh = bpy.data.meshes.new(name + " mesh")
        mesh.from_pydata([(-2, -2, 0), (2, -2, 0), (2, 2, 0), (-2, 2, 0)], [], [(0, 1, 2, 3)])
        plane = bpy.data.objects.new(name + " plane", mesh)
        plane.color = (*color, 1)
        scene.collection.objects.link(plane)
        return scene

    def director(self, *, audio=False):
        red = self.shot("Red", (1, 0, 0))
        blue = self.shot("Blue", (0, 0, 1))
        timeline = self.scene("Director")
        timeline["open_studio_film_kind"] = "timeline"
        editor = timeline.sequence_editor_create()
        for index, source in enumerate((red, blue)):
            strip = editor.strips.new_scene(source.name, source, 1, 1 + index * 4)
            strip.scene_input = "CAMERA"
        if audio:
            sound = editor.strips.new_sound("Soundtrack", str(self.media), 2, 1)
            sound.frame_final_end = 9
            sound.volume = 0.4
        return timeline

    def review(self):
        scene = self.scene("Media review")
        scene["open_studio_film_kind"] = "review"
        editor = scene.sequence_editor_create()
        editor.strips.new_movie("Picture", str(self.media), 1, 1)
        editor.strips.new_sound("Soundtrack", str(self.media), 2, 1)
        return scene

    def start(self, scene, filename="film.mp4", **options):
        return self.tools.export_agent_film(
            {
                "scene_name": scene.name,
                "scene_id": scene["open_studio_film_id"],
                "filename": filename,
                **options,
            }
        )

    def wait(self, operation, *, success=True, pump=True):
        identifier = operation["operation_id"]
        deadline = time.monotonic() + 120
        while True:
            if pump:
                state = self.tools.get_agent_operation({"operation_id": identifier})
                done = state["phase"] in self.module._TERMINAL
            else:
                done = self.module._operations[identifier].future.done()
                state = self.module._description(self.module._operations[identifier])
            if done:
                break
            self.assertLess(time.monotonic(), deadline, str(state))
            time.sleep(0.05)
        if success and pump:
            self.assertEqual(state["phase"], "DONE", state)
        return state

    def decode(self, path):
        binary = shutil.which("ffmpeg")
        self.assertIsNotNone(binary, "Native export acceptance requires ffmpeg decode")
        completed = subprocess.run(
            [
                binary,
                "-v",
                "error",
                "-i",
                str(path),
                "-map",
                "0:v:0",
                "-f",
                "rawvideo",
                "-pix_fmt",
                "rgb24",
                "-",
            ],
            capture_output=True,
            timeout=30,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr.decode(errors="replace"))
        return completed.stdout

    def state(self):
        return (
            bpy.data.filepath,
            bpy.context.scene.name,
            self.working.frame_current,
            [
                (
                    s.name,
                    s.frame_current,
                    s.render.engine,
                    s.render.resolution_x,
                    s.render.resolution_y,
                    s.render.filepath,
                    s.frame_end,
                )
                for s in bpy.data.scenes
            ],
        )

    def test_director_scene_strip_cuts_decode_and_source_context_is_unchanged(self):
        scene = self.director(audio=True)
        before = self.state()
        result = self.wait(self.start(scene))["result"]
        self.assertEqual(before, self.state())
        self.assertEqual((result["frames"], result["fps"], result["audio"]), (8, 24, True))
        video = Path(result["path"])
        self.assertEqual(hashlib.sha256(video.read_bytes()).hexdigest(), result["sha256"])
        frames = self.decode(video)
        self.assertEqual(len(frames), 8 * 64 * 64 * 3)
        first = frames[(32 * 64 + 32) * 3 :][:3]
        last = frames[(7 * 64 * 64 + 32 * 64 + 32) * 3 :][:3]
        self.assertGreater(first[0], first[2] + 100)
        self.assertGreater(last[2], last[0] + 100)
        self.assertEqual(list(self.outbox.iterdir()), [video])

    def test_returned_media_review_exports_aac_and_h264(self):
        scene = self.review()
        result = self.wait(self.start(scene, "review.mp4", start_frame=2, end_frame=6))["result"]
        self.assertEqual((result["frames"], result["audio"]), (5, True))
        self.assertEqual(len(self.decode(result["path"])), 5 * 64 * 64 * 3)

    def test_private_worker_mutes_nested_compositor_file_outputs(self):
        scene = self.director()
        shot = bpy.data.scenes["Red"]
        if hasattr(shot, "compositing_node_group"):
            tree = bpy.data.node_groups.new("Export compositor", "CompositorNodeTree")
            shot.compositing_node_group = tree
        else:
            shot.use_nodes = True
            tree = shot.node_tree
        nested = bpy.data.node_groups.new("Nested export compositor", "CompositorNodeTree")
        group = tree.nodes.new("CompositorNodeGroup")
        group.node_tree = nested
        outputs = [
            tree.nodes.new("CompositorNodeOutputFile"),
            nested.nodes.new("CompositorNodeOutputFile"),
        ]
        outside = self.root / "must-not-write"
        for output in outputs:
            if hasattr(output, "directory"):
                output.directory = str(outside)
            else:
                output.base_path = str(outside)
        self.wait(self.start(scene))
        self.assertFalse(outside.exists())
        self.assertFalse(any(output.mute for output in outputs))
        # Test the worker's exact recursive preparation on native node objects.
        worker = submodule("blender.assistant_film_export_worker")
        worker._mute_file_outputs(shot)
        self.assertTrue(all(output.mute for output in outputs))

    def test_nested_scene_audio_defaults_follow_strip_and_channel_mutes(self):
        child = self.review()
        camera = bpy.data.objects.new("Audio camera", bpy.data.cameras.new("Audio camera"))
        child.collection.objects.link(camera)
        child.camera = camera
        child.render.engine = "BLENDER_WORKBENCH"
        parent = self.scene("Nested review")
        editor = parent.sequence_editor_create()
        strip = editor.strips.new_scene("Nested picture and audio", child, 1, 1)
        strip.scene_input = "CAMERA"
        self.assertFalse(self.module._audible(parent))
        camera_result = self.wait(self.start(parent, "camera.mp4"))["result"]
        self.assertFalse(camera_result["audio"])
        strip.scene_input = "SEQUENCER"
        for mode in ("SEQUENCER",):
            self.assertTrue(self.module._audible(parent))
            result = self.wait(self.start(parent, mode.lower() + ".mp4"))["result"]
            self.assertTrue(result["audio"])
            completed = subprocess.run(
                [
                    shutil.which("ffmpeg"),
                    "-v",
                    "error",
                    "-i",
                    result["path"],
                    "-map",
                    "0:a:0",
                    "-f",
                    "s16le",
                    "-",
                ],
                capture_output=True,
                timeout=30,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            samples = struct.unpack("<" + "h" * (len(completed.stdout) // 2), completed.stdout)
            self.assertGreater(max(map(abs, samples)), 100, mode + " soundtrack is silent")
        strip.mute = True
        self.assertFalse(self.module._audible(parent))
        strip.mute = False
        editor.channels[strip.channel].mute = True
        self.assertFalse(self.module._audible(parent))
        editor.channels[strip.channel].mute = False
        sound = next(s for s in child.sequence_editor.strips_all if s.type == "SOUND")
        sound.mute = True
        self.assertFalse(self.module._audible(parent))

    def test_explicit_identity_bounds_and_no_overwrite_refuse_before_snapshot(self):
        scene = self.director()
        existing = self.outbox / "existing.mp4"
        existing.write_bytes(b"keep me")
        with patch.object(self.module.local_capture, "_write_snapshot") as snapshot:
            for arguments in (
                {"filename": "../escape.mp4"},
                {"filename": "existing.mp4"},
                {"width": 65},
                {"start_frame": 2},
                {"scene_id": "wrong"},
            ):
                args = {"scene_name": scene.name, "filename": "bad.mp4", **arguments}
                with (
                    self.subTest(args=args),
                    self.assertRaises((ValueError, self.module.local_export.LocalExportError)),
                ):
                    self.tools.export_agent_film(args)
            snapshot.assert_not_called()
        self.assertEqual(existing.read_bytes(), b"keep me")

    def test_native_worker_cancel_and_disable_clean_staging_without_publication(self):
        scene = self.director()
        operation = self.start(scene, end_frame=1000, start_frame=1)
        state = self.tools.cancel_agent_operation({"operation_id": operation["operation_id"]})
        self.assertIn(state["phase"], {"CANCELLING", "CANCELLED"})
        self.assertEqual(self.wait(operation, success=False)["phase"], "CANCELLED")
        self.assertEqual(list(self.outbox.iterdir()), [])
        operation = self.start(scene, "disable.mp4")
        self.module.unregister()
        try:
            self.assertEqual(self.wait(operation, success=False)["phase"], "CANCELLED")
            self.assertEqual(list(self.outbox.iterdir()), [])
        finally:
            self.module.register()

    def test_completed_worker_cannot_publish_after_native_undo_and_redo(self):
        scene = self.review()
        working_name, scene_name = self.working.name, scene.name
        prefs = bpy.context.preferences.edit
        previous = prefs.use_global_undo, prefs.undo_steps
        prefs.use_global_undo, prefs.undo_steps = True, 32
        try:
            bpy.ops.ed.undo_push(message="Before local export")
            self.working["history_probe"] = 1
            bpy.ops.ed.undo_push(message="After local edit")
            for action in (bpy.ops.ed.undo, bpy.ops.ed.redo):
                operation = self.start(bpy.data.scenes[scene_name])
                self.wait(operation, pump=False)
                self.assertFalse((self.outbox / "film.mp4").exists())
                self.assertEqual(action(), {"FINISHED"})
                self.assertEqual(self.wait(operation, success=False)["phase"], "CANCELLED")
                self.assertEqual(list(self.outbox.iterdir()), [])
        finally:
            prefs.use_global_undo, prefs.undo_steps = previous
            self.working = bpy.data.scenes[working_name]
            self.created = [bpy.data.scenes[scene_name]]

    def test_completed_worker_cannot_publish_into_a_newly_loaded_file(self):
        operation = self.start(self.review())
        self.wait(operation, pump=False)
        reset_scene()
        self.working = bpy.context.scene
        self.created = []
        self.assertEqual(self.wait(operation, success=False)["phase"], "CANCELLED")
        self.assertEqual(list(self.outbox.iterdir()), [])

    def test_publication_race_preserves_the_file_that_arrived_after_snapshot(self):
        scene = self.review()
        operation = self.start(scene)
        self.wait(operation, pump=False)
        destination = self.outbox / "film.mp4"
        destination.write_bytes(b"another output owns this name")
        result = self.wait(operation, success=False)
        self.assertEqual(result["phase"], "ERROR")
        self.assertEqual(destination.read_bytes(), b"another output owns this name")
        self.assertEqual(list(self.outbox.iterdir()), [destination])

    def test_same_length_prepared_output_change_refuses_publication(self):
        operation = self.start(self.review())
        self.wait(operation, pump=False)
        ready = self.module._operations[operation["operation_id"]].spec.directory / "ready.mp4"
        original = ready.read_bytes()
        ready.write_bytes(bytes([original[0] ^ 1]) + original[1:])
        result = self.wait(operation, success=False)
        self.assertEqual(result["phase"], "ERROR")
        self.assertIn("changed", result["error"])
        self.assertEqual(list(self.outbox.iterdir()), [])

    def test_portable_project_relocates_and_reopens_without_original_media(self):
        timeline = self.director(audio=True)
        sound = next(s for s in timeline.sequence_editor.strips_all if s.type == "SOUND")
        sound.animation_offset_start, sound.animation_offset_end = 4, 2
        sound.frame_start, sound.frame_final_end = 1, 9
        image = bpy.data.images.new("Packed texture", 2, 2)
        image.generated_color = (0.2, 0.5, 0.9, 1)
        image.filepath_raw = str(self.root / "texture.png")
        image.file_format = "PNG"
        image.save()
        image.pack()
        image_digest = hashlib.sha256(bytes(image.packed_file.data)).hexdigest()
        material = bpy.data.materials.new("Packed material")
        material.node_tree.nodes.new("ShaderNodeTexImage").image = image
        bpy.data.objects["Red plane"].data.materials.append(material)
        before = self.state()
        operation = self.tools.package_agent_project(
            {
                "scenes": [
                    {"scene_name": timeline.name, "scene_id": timeline["open_studio_film_id"]}
                ],
                "filename": "project.zip",
            }
        )
        result = self.wait(operation)["result"]
        self.assertEqual(before, self.state())
        self.assertEqual(set(result["scenes"]), {"Director", "Red", "Blue"})
        moved = self.root / "relocated"
        with zipfile.ZipFile(result["path"]) as archive:
            self.assertIn("Open Studio Project/Project.blend", archive.namelist())
            self.assertFalse(
                any("Ledger" in name or "Case Study" in name for name in archive.namelist())
            )
            archive.extractall(moved)
        self.media.unlink()
        (self.root / "texture.png").unlink()
        project = moved / "Open Studio Project" / "Project.blend"
        self.assertEqual(hashlib.sha256(project.read_bytes()).hexdigest(), result["blend_sha256"])
        script = self.root / "reopen.py"
        script.write_text(REOPEN, encoding="utf-8")
        report, frame = self.root / "reopened.json", self.root / "reopened.png"
        profile, temporary = self.root / "profile", self.root / "tmp"
        profile.mkdir()
        temporary.mkdir()
        environment = submodule("core.jobs.local_render")._environment(profile, temporary)
        completed = subprocess.run(
            [
                bpy.app.binary_path,
                "--quiet",
                "--offline-mode",
                "--factory-startup",
                "--disable-autoexec",
                "--background",
                str(project),
                "--python-exit-code",
                "1",
                "--python",
                str(script),
                "--",
                str(report),
                str(frame),
            ],
            capture_output=True,
            env=environment,
            timeout=60,
        )
        self.assertEqual(completed.returncode, 0, completed.stdout.decode(errors="replace"))
        value = json.loads(report.read_text())
        self.assertEqual(value["scenes"], ["Blue", "Director", "Red"])
        self.assertTrue(all(value["resolved"]))
        self.assertTrue(all(path.startswith("//Media") for path in value["paths"]))
        self.assertTrue(value["packed"])
        self.assertEqual(value["packed_sha256"], image_digest)
        self.assertEqual(value["vertices"], 4)
        self.assertAlmostEqual(value["sound"][0], 0.4)
        self.assertEqual(value["sound"][1:], [1, 9, 4, 2])
        self.assertTrue(all(value["identities"]))
        self.assertTrue(all(value["recipes"].values()))
        self.assertTrue(frame.is_file())


if __name__ == "__main__":
    unittest.main()
