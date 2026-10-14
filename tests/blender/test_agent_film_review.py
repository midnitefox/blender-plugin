# SPDX-FileCopyrightText: 2026 Open Studio contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Native editorial timing, media persistence and atomic review construction."""

import copy
import hashlib
import io
import json
import shutil
import subprocess
import tempfile
import unittest
import wave
from array import array
from pathlib import Path
from unittest.mock import patch

import bpy
from helpers import FIXTURES, reset_scene, submodule
from test_agent_media import wav_bytes


class AgentFilmReviewTests(unittest.TestCase):
    def setUp(self):
        reset_scene()
        self.tools = submodule("mcp.tools_agent_film_review")
        self.builder = submodule("blender.assistant_film_review")
        self.capture = submodule("blender.capture")
        self.scene = bpy.context.scene
        self.inbox = Path(submodule("mcp.tools_agent").agent_workspace({})["inbox"])
        self.temp = Path(self.enterContext(tempfile.TemporaryDirectory(prefix="review-test-")))
        self.paths = []
        bpy.ops.mesh.primitive_cube_add()
        self.scene.frame_start, self.scene.frame_end = 7, 41
        self.scene.frame_set(19, subframe=0.5)
        self.scene.render.fps, self.scene.render.fps_base = 25, 1.0
        editor = self.scene.sequence_editor_create()
        strip = editor.strips.new_effect("User strip", "COLOR", 1, 7, length=35)
        strip.select = True
        editor.active_strip = strip
        self.before = self.context_state()

    def tearDown(self):
        for path in self.paths:
            path.unlink(missing_ok=True)

    def context_state(self):
        scene = bpy.context.scene
        editor = scene.sequence_editor
        return (
            scene.name,
            scene.frame_start,
            scene.frame_end,
            scene.frame_current,
            scene.frame_subframe,
            scene.render.fps,
            scene.render.fps_base,
            bpy.context.view_layer.objects.active.name,
            sorted(
                obj.name
                for obj in bpy.context.view_layer.objects
                if obj.select_get(view_layer=bpy.context.view_layer)
            ),
            editor.active_strip.name,
            [
                (s.name, s.channel, s.frame_final_start, s.frame_final_end, s.select)
                for s in editor.strips
            ],
        )

    def source(self, name="film-four-seconds-audio.mp4", data=None):
        path = self.inbox / ("review-test-" + name)
        if data is None:
            shutil.copyfile(FIXTURES / "synthetic" / name, path)
        else:
            path.write_bytes(data)
        self.paths.append(path)
        return dict(filename=path.name, sha256=hashlib.sha256(path.read_bytes()).hexdigest())

    def video(self, **kwargs):
        item = dict(
            **self.source(),
            kind="VIDEO",
            start_frame=11,
            frame_count=60,
            channel=3,
            source_trim_seconds=1,
            include_audio=True,
            audio_channel=4,
            volume=0.25,
        )
        item.update(kwargs)
        return item

    def build(self, pictures=None, audio=None, fps=30):
        return self.tools.build_agent_review(
            dict(
                name="Assistant Review",
                fps=fps,
                width=320,
                height=180,
                pictures=pictures or [],
                audio=audio or [],
            )
        )

    def review(self, result):
        return bpy.data.scenes[result["scene_name"]]

    def snapshot(self):
        root = self.builder.runtime.paths().state_dir / "assistant-film-reviews"
        return (
            set(bpy.data.scenes),
            set(bpy.data.sounds),
            set(root.iterdir()) if root.exists() else set(),
        )

    def test_trimmed_24_to_30_fps_video_and_audio_share_source_time_and_preserve_context(self):
        with patch.object(self.builder.file_state, "mark_modified") as mark:
            result = self.build([self.video()])
        mark.assert_called_once()
        self.assertEqual(self.context_state(), self.before)
        scene = self.review(result)
        movie, sound = sorted(scene.sequence_editor.strips, key=lambda strip: strip.channel)
        self.assertEqual(movie.fps, 24)
        for strip in (movie, sound):
            self.assertEqual(
                (strip.frame_final_start, strip.frame_final_end, strip.frame_offset_start),
                (11, 71, 30),
            )
            self.assertEqual(strip.frame_start, -19)
        self.assertAlmostEqual(sound.volume, 0.25)
        self.assertEqual([key.timeline_frame for key in movie.retiming_keys], [-19, 101])
        self.assertEqual((scene.frame_start, scene.frame_end, scene.render.fps), (11, 70, 30))
        self.assertEqual(scene["open_studio_film_kind"], "review")
        self.assertEqual(scene["open_studio_film_id"], result["scene_id"])
        self.assertEqual(len(result["scene_id"]), 32)
        self.assertEqual(
            json.loads(scene["open_studio_film_recipe"])["pictures"][0]["source_trim_seconds"], 1
        )

    def test_trimmed_picture_and_embedded_audio_loop_together_without_phase_drift(self):
        result = self.build([self.video(source_trim_seconds=3, frame_count=75, loop=True)])
        editor = self.review(result).sequence_editor
        for channel in (3, 4):
            strips = sorted(
                (s for s in editor.strips if s.channel == channel),
                key=lambda s: s.frame_final_start,
            )
            self.assertEqual(
                [(s.frame_final_start, s.frame_final_end, s.frame_offset_start) for s in strips],
                [(11, 41, 90), (41, 71, 90), (71, 86, 90)],
            )
        self.assertEqual(self.context_state(), self.before)

    def test_still_hold_and_trimmed_sound_loops_are_editable_with_unique_scene_identity(self):
        picture = dict(
            **self.source("reference-toadstool-512.png"),
            kind="IMAGE",
            start_frame=1,
            frame_count=144,
            channel=1,
        )
        audio = dict(
            **self.source("score.wav", wav_bytes(2.5)),
            start_frame=1,
            frame_count=144,
            channel=2,
            source_trim_seconds=0.5,
            loop=True,
            volume=0.4,
        )
        first, second = (
            self.build([picture], [audio], fps=24),
            self.build([picture], [audio], fps=24),
        )
        self.assertNotEqual(first["scene_id"], second["scene_id"])
        self.assertNotEqual(first["scene_name"], second["scene_name"])
        scene = self.review(first)
        sound = sorted(
            (s for s in scene.sequence_editor.strips if s.type == "SOUND"),
            key=lambda s: s.frame_final_start,
        )
        self.assertEqual(
            [(s.frame_final_start, s.frame_final_end, s.frame_offset_start) for s in sound],
            [(1, 49, 12), (49, 97, 12), (97, 145, 12)],
        )
        self.assertTrue(all(abs(s.volume - 0.4) < 0.000001 for s in sound))
        image = next(s for s in scene.sequence_editor.strips if s.type == "IMAGE")
        self.assertEqual((image.frame_final_start, image.frame_final_end), (1, 145))
        self.assertEqual(self.context_state(), self.before)

    def test_refuse_collision_hash_corruption_short_sources_and_missing_audio_atomically(self):
        valid = self.video()
        wrong_hash = dict(valid, sha256="0" * 64)
        missing_audio = dict(valid, **self.source("film-four-seconds.mp4"))
        corrupt = dict(valid, **self.source("corrupt.mp4", b"\0\0\0\x18ftypisom" + b"\0" * 24))
        for items in (
            [valid, copy.deepcopy(valid)],
            [wrong_hash],
            [missing_audio],
            [corrupt],
            [dict(valid, frame_count=91)],
            [dict(valid, source_trim_seconds=4)],
        ):
            with self.subTest(items=items):
                before = self.snapshot()
                with patch.object(self.builder.file_state, "mark_modified") as mark:
                    with self.assertRaises(ValueError):
                        self.build(items)
                mark.assert_not_called()
                self.assertEqual(self.snapshot(), before)
                self.assertEqual(self.context_state(), self.before)

    def test_insertion_failure_rolls_back_only_new_scene_sounds_and_retained_files(self):
        item = self.video()
        before = self.snapshot()
        original = self.builder._place
        calls = 0

        def interrupted(*args):
            nonlocal calls
            original(*args)
            calls += 1
            if calls == 2:
                raise RuntimeError("Synthetic failure after audio insertion")

        with patch.object(self.builder, "_place", side_effect=interrupted):
            with self.assertRaisesRegex(RuntimeError, "Synthetic"):
                self.build([item])
        self.assertEqual(self.snapshot(), before)
        self.assertEqual(self.context_state(), self.before)

    def test_real_audio_mix_has_trimmed_and_repeated_source_content(self):
        data = io.BytesIO()
        with wave.open(data, "wb") as output:
            output.setnchannels(1)
            output.setsampwidth(2)
            output.setframerate(48000)
            output.writeframes(
                array("h", [0] * 12000 + [16000] * 24000 + [-8000] * 12000).tobytes()
            )
        audio = dict(
            **self.source("phase.wav", data.getvalue()),
            start_frame=1,
            frame_count=36,
            channel=1,
            source_trim_seconds=0.25,
            loop=True,
            volume=0.5,
        )
        scene = self.review(self.build(audio=[audio], fps=24))
        path = self.temp / "mix.mkv"
        scene.sequence_editor.strips.new_effect("Test picture", "COLOR", 2, 1, length=36)
        scene.render.resolution_x = scene.render.resolution_y = 64
        scene.render.filepath = str(path)
        self.capture.set_video_output(scene.render)
        scene.render.ffmpeg.format, scene.render.ffmpeg.codec = "MKV", "H264"
        scene.render.ffmpeg.audio_codec = "PCM"
        scene.render.ffmpeg.audio_mixrate = 48000
        bpy.ops.render.render(animation=True, scene=scene.name)
        ffmpeg = shutil.which("ffmpeg")
        self.assertIsNotNone(ffmpeg, "Audio acceptance requires ffmpeg decode")
        decoded = subprocess.run(
            [
                ffmpeg,
                "-v",
                "error",
                "-i",
                str(path),
                "-map",
                "0:a:0",
                "-ac",
                "1",
                "-ar",
                "48000",
                "-f",
                "s16le",
                "pipe:1",
            ],
            capture_output=True,
            timeout=30,
            check=True,
        )
        samples = array("h", decoded.stdout)
        # Blender's PCM muxer pads the final 1024-sample block.
        self.assertGreaterEqual(len(samples), 72000)
        self.assertLess(len(samples), 72000 + 1024)
        values = [samples[int(seconds * 48000)] for seconds in (0.1, 0.6, 0.85, 1.35)]
        self.assertLessEqual(max(map(abs, samples[72000:]), default=0), 1)
        self.assertGreater(values[0], 2000)
        self.assertLess(values[1], -1000)
        self.assertGreater(values[2], 2000)
        self.assertLess(values[3], -1000)
        self.assertAlmostEqual(values[0] / values[1], -2, places=2)
        self.assertEqual(self.context_state(), self.before)

    def test_review_survives_inbox_deletion_save_and_reopen_with_fresh_decoders(self):
        result = self.build([self.video()])
        identity = result["scene_id"]
        blend = self.temp / "review.blend"
        bpy.ops.wm.save_as_mainfile(filepath=str(blend), check_existing=False)
        for path in self.paths:
            path.unlink()
        bpy.ops.wm.open_mainfile(filepath=str(blend))
        self.assertEqual(self.context_state(), self.before)
        scene = next(s for s in bpy.data.scenes if s.get("open_studio_film_id") == identity)
        self.assertEqual(len(scene.sequence_editor.strips), 2)
        for source in result["retained_media"]:
            path = Path(source["path"])
            self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), source["sha256"])
            measured = self.builder.media._probe(
                scene,
                path,
                [
                    dict(kind="VIDEO", channel=1, frame=1, name="Picture decode"),
                    dict(kind="AUDIO", channel=2, frame=1, name="Sound decode"),
                ],
                30,
            )
            self.assertEqual([s["duration_frames"] for s in measured], [120, 120])

    def test_30_to_24_fps_trim_has_actual_blue_picture_content(self):
        path = self.inbox / "review-test-30fps.mp4"
        self.paths.append(path)
        source = bpy.data.scenes.new("Native 30 fps source")
        try:
            source.render.fps = 30
            source.render.resolution_x = source.render.resolution_y = 32
            source.render.resolution_percentage = 100
            source.frame_start, source.frame_end = 1, 60
            source.render.filepath = str(path)
            self.capture.set_video_output(source.render)
            source.render.ffmpeg.format, source.render.ffmpeg.codec = "MPEG4", "H264"
            source.render.ffmpeg.audio_codec = "NONE"
            source.view_settings.view_transform, source.view_settings.look = "Standard", "None"
            editor = source.sequence_editor_create()
            editor.strips.new_effect("Red first second", "COLOR", 1, 1, length=30).color = (1, 0, 0)
            editor.strips.new_effect("Blue second second", "COLOR", 1, 31, length=30).color = (
                0,
                0,
                1,
            )
            bpy.ops.render.render(animation=True, scene=source.name)
        finally:
            bpy.data.scenes.remove(source)
        item = dict(
            filename=path.name,
            sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
            kind="VIDEO",
            start_frame=1,
            frame_count=24,
            channel=1,
            source_trim_seconds=1,
        )
        image = dict(
            **self.source("reference-toadstool-512.png"),
            kind="IMAGE",
            start_frame=61,
            frame_count=12,
            channel=1,
        )
        same_rate = dict(
            **self.source("film-four-seconds.mp4"),
            kind="VIDEO",
            start_frame=25,
            frame_count=24,
            channel=1,
        )
        # Deliberately submit both zero-key kinds before the retimed movie.
        result = self.build([image, same_rate, item], fps=24)
        scene = self.review(result)
        movie = next(s for s in scene.sequence_editor.strips if s.frame_final_start == 1)
        self.assertEqual(movie.fps, 30)
        self.assertEqual(
            (movie.frame_final_start, movie.frame_final_end, movie.frame_offset_start), (1, 25, 24)
        )
        image_path = self.temp / "trimmed.png"
        self.capture.set_image_output(scene.render, "PNG")
        scene.render.filepath = str(image_path)
        scene.frame_set(1)
        bpy.ops.render.render(write_still=True, scene=scene.name)
        (width, height), pixels = submodule("blender.preview_decode").decode(
            image_path, edge=32, limit=lambda w, h: True
        )
        offset = (width * (height // 2) + width // 2) * 4
        red, green, blue = pixels[offset : offset + 3]
        self.assertGreater(blue, 0.8)
        self.assertLess(max(red, green), 0.1)
        self.assertEqual(self.context_state(), self.before)

    @unittest.skipIf(bpy.app.background, "Undo/redo requires an isolated native GUI session")
    def test_native_undo_redo_restores_review_identity_and_strips(self):
        item = self.video()
        bpy.ops.ed.undo_push(message="Before assistant review")
        bpy.context.scene["user_unrecorded_edit"] = "preserve"
        result = self.build([item])
        self.assertTrue(result["undo_available"])
        identity = result["scene_id"]
        bpy.ops.ed.undo()
        self.assertFalse(any(s.get("open_studio_film_id") == identity for s in bpy.data.scenes))
        self.assertEqual(bpy.context.scene["user_unrecorded_edit"], "preserve")
        self.assertEqual(self.context_state(), self.before)
        bpy.ops.ed.redo()
        scene = next(s for s in bpy.data.scenes if s.get("open_studio_film_id") == identity)
        self.assertEqual(len(scene.sequence_editor.strips), 2)
        self.assertEqual(self.context_state(), self.before)
