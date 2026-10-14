# SPDX-FileCopyrightText: 2026 Open Studio contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Real assistant audio/video handoffs in the installed, isolated extension."""

import hashlib
import io
import shutil
import tempfile
import unittest
import wave
from pathlib import Path
from unittest.mock import patch

import bpy
from helpers import FIXTURES, reset_scene, submodule


def wav_bytes(seconds=2.5):
    stream = io.BytesIO()
    with wave.open(stream, "wb") as output:
        output.setnchannels(2)
        output.setsampwidth(2)
        output.setframerate(48000)
        output.writeframes(b"\0\0\0\0" * int(48000 * seconds))
    return stream.getvalue()


class AgentMediaTests(unittest.TestCase):
    def setUp(self):
        reset_scene()
        self.tools = submodule("mcp.tools_agent_media")
        self.media = submodule("blender.assistant_media")
        self.agent = submodule("mcp.tools_agent")
        self.inbox = Path(self.agent.agent_workspace({})["inbox"])
        self.paths = []
        self.scene = bpy.context.scene
        self.scene.sequence_editor_clear()
        self.scene.render.fps, self.scene.render.fps_base = 24, 1.0

    def tearDown(self):
        for path in self.paths:
            path.unlink(missing_ok=True)

    def source(self, name="sound.wav", data=None):
        path = self.inbox / ("agent-test-" + name)
        if data is not None:
            path.write_bytes(data)
        elif name.endswith(".wav"):
            path.write_bytes(wav_bytes())
        else:
            shutil.copyfile(FIXTURES / "synthetic" / name, path)
        self.paths.append(path)
        return path

    def import_media(self, path, kind="AUDIO", **kwargs):
        return self.tools.import_agent_media(dict(filename=path.name, kind=kind, **kwargs))

    def timing(self):
        return (
            self.scene.frame_start,
            self.scene.frame_end,
            self.scene.frame_current,
            self.scene.render.fps,
            self.scene.render.fps_base,
        )

    def test_audio_preserves_existing_strips_selection_and_scene_timing(self):
        editor = self.scene.sequence_editor_create()
        existing = editor.strips.new_effect("Existing", "COLOR", 1, 1, length=12)
        existing.select = True
        editor.active_strip = existing
        before = self.timing()
        source = self.source()
        with patch.object(self.media.file_state, "mark_modified") as mark:
            result = self.import_media(source, start_frame=17, channel=1)
        mark.assert_called_once()
        self.assertEqual(result["strips"][0]["duration_frames"], 60)
        self.assertEqual(result["strips"][0]["start_frame"], 17)
        self.assertEqual(result["strips"][0]["channel"], 1)
        self.assertEqual(existing.frame_final_start, 1)
        self.assertEqual(existing.frame_final_end, 13)
        self.assertEqual(editor.active_strip, existing)
        self.assertTrue(existing.select)
        self.assertEqual(self.timing(), before)
        self.assertEqual(Path(result["asset_path"]).read_bytes(), source.read_bytes())
        self.assertNotEqual(Path(result["asset_path"]), source)
        self.assertFalse(result["packed"])
        self.assertIn("relink", result["portability"])

    def test_supported_compressed_audio_and_video_are_decoded(self):
        for channel, (name, kind, expected_type) in enumerate(
            (
                ("audio-silence.mp3", "AUDIO", "SOUND"),
                ("audio-silence.ogg", "AUDIO", "SOUND"),
                ("video-six-frames.mp4", "VIDEO", "MOVIE"),
                ("video-six-frames.webm", "VIDEO", "MOVIE"),
            ),
            1,
        ):
            with self.subTest(name=name):
                result = self.import_media(self.source(name), kind, channel=channel)
                strip = result["strips"][0]
                self.assertEqual(strip["type"], expected_type)
                self.assertGreater(strip["duration_frames"], 0)
                self.assertEqual(strip["channel"], channel)
                if kind == "VIDEO":
                    self.assertGreater(strip["source_fps"], 0)

    def test_video_audio_preserve_seconds_when_scene_and_source_fps_differ(self):
        self.scene.render.fps = 30
        before = self.timing()
        result = self.import_media(
            self.source("film-four-seconds-audio.mp4"),
            "VIDEO",
            start_frame=17,
            channel=3,
            include_audio=True,
            audio_channel=4,
        )
        movie, audio = result["strips"]
        self.assertEqual(movie["source_fps"], 24)
        self.assertEqual(result["scene_fps"], 30)
        self.assertEqual(movie["duration_frames"], 120)
        self.assertEqual(audio["duration_frames"], 120)
        self.assertEqual(movie["start_frame"], audio["start_frame"])
        self.assertEqual(movie["end_frame"], 137)
        self.assertEqual(self.timing(), before)
        self.assertEqual(movie["channel"], 3)
        self.assertEqual(audio["channel"], 4)
        strip = self.scene.sequence_editor.strips[movie["name"]]
        self.assertEqual([key.timeline_frame for key in strip.retiming_keys], [17, 137])

    def test_repeat_checks_actual_stream_path_and_timing(self):
        source = self.source("film-four-seconds-audio.mp4")
        args = dict(channel=3, include_audio=True, audio_channel=4)
        first = self.import_media(source, "VIDEO", **args)
        with patch.object(self.media.file_state, "mark_modified") as mark:
            repeat = self.import_media(source, "VIDEO", **args)
        mark.assert_not_called()
        self.assertTrue(repeat["reused"])
        self.assertEqual(first["strips"], repeat["strips"])
        editor = self.scene.sequence_editor
        self.assertEqual(len(editor.strips), 2)
        movie = editor.strips[first["strips"][0]["name"]]
        movie.frame_offset_end = 1
        with self.assertRaisesRegex(ValueError, "occupied"):
            self.import_media(source, "VIDEO", **args)
        self.assertEqual(len(editor.strips), 2)
        movie.frame_offset_end = 0
        movie.filepath = str(FIXTURES / "synthetic" / "video-six-frames.mp4")
        with self.assertRaisesRegex(ValueError, "occupied"):
            self.import_media(source, "VIDEO", **args)
        self.assertEqual(len(editor.strips), 2)

    def test_occupied_locked_and_muted_channels_refuse_without_shifting_content(self):
        editor = self.scene.sequence_editor_create()
        existing = editor.strips.new_effect("Existing", "COLOR", 3, 1, length=60)
        editor.channels[4].lock = True
        editor.channels[5].mute = True
        source = self.source()
        before = self.timing()
        for channel in (3, 4, 5):
            with self.subTest(channel=channel):
                with patch.object(self.media.file_state, "mark_modified") as mark:
                    with self.assertRaises(ValueError):
                        self.import_media(source, channel=channel)
                mark.assert_not_called()
                self.assertEqual(len(editor.strips), 1)
                self.assertEqual(existing.channel, 3)
                self.assertEqual(existing.frame_final_start, 1)
                self.assertEqual(existing.frame_final_end, 61)
                self.assertEqual(self.timing(), before)

    def test_repeat_refuses_audio_content_shift_with_unchanged_timeline_range(self):
        source = self.source()
        first = self.import_media(source, start_frame=17, channel=3)
        editor = self.scene.sequence_editor
        audio = editor.strips[first["strips"][0]["name"]]
        before = audio.frame_final_start, audio.frame_final_end, audio.channel
        audio.sound_offset = 0.125
        self.assertEqual((audio.frame_final_start, audio.frame_final_end, audio.channel), before)
        with patch.object(self.media.file_state, "mark_modified") as mark:
            with self.assertRaisesRegex(ValueError, "occupied"):
                self.import_media(source, start_frame=17, channel=3)
        mark.assert_not_called()
        self.assertEqual(len(editor.strips), 1)
        self.assertAlmostEqual(audio.sound_offset, 0.125)
        self.assertEqual((audio.frame_final_start, audio.frame_final_end, audio.channel), before)

    def test_corrupt_files_and_missing_video_audio_leave_target_editor_absent(self):
        sources = (
            (self.source("invalid.wav", b"not audio"), "AUDIO", {}),
            (self.source("invalid.mp4", b"\0\0\0\x18ftypisom" + b"\0" * 24), "VIDEO", {}),
            (self.source("video-six-frames.mp4"), "VIDEO", {"include_audio": True}),
        )
        sounds, scenes = set(bpy.data.sounds), set(bpy.data.scenes)
        for source, kind, args in sources:
            with self.subTest(source=source.name):
                with self.assertRaises(ValueError):
                    self.import_media(source, kind, **args)
                self.assertIsNone(self.scene.sequence_editor)
                self.assertEqual(set(bpy.data.sounds), sounds)
                self.assertEqual(set(bpy.data.scenes), scenes)

    def test_second_stream_collision_and_insertion_failure_are_atomic(self):
        editor = self.scene.sequence_editor_create()
        existing = editor.strips.new_effect("Existing", "COLOR", 2, 1, length=30)
        source = self.source("film-four-seconds-audio.mp4")
        with self.assertRaisesRegex(ValueError, "occupied"):
            self.import_media(source, "VIDEO", include_audio=True)
        self.assertEqual(list(editor.strips), [existing])
        insert = self.media._insert
        count = 0

        def fail_second_target(*args, **kwargs):
            nonlocal count
            strip = insert(*args, **kwargs)
            count += 1
            if count == 4:  # Two probe streams followed by two destination streams.
                raise RuntimeError("Synthetic failure after insertion")
            return strip

        sounds = set(bpy.data.sounds)
        with patch.object(self.media, "_insert", side_effect=fail_second_target):
            with self.assertRaisesRegex(ValueError, "existing strips were preserved"):
                self.import_media(source, "VIDEO", channel=3, include_audio=True, audio_channel=4)
        self.assertEqual(list(editor.strips), [existing])
        self.assertEqual(set(bpy.data.sounds), sounds)

    def test_save_reopen_preserves_decodable_media_after_inbox_cleanup(self):
        source = self.source("film-four-seconds-audio.mp4")
        self.scene.render.fps = 30
        result = self.import_media(source, "VIDEO", start_frame=5, include_audio=True)
        path = Path(result["asset_path"])
        digest = hashlib.sha256(source.read_bytes()).hexdigest()
        with tempfile.TemporaryDirectory(prefix="assistant-media-") as directory:
            blend = Path(directory) / "media.blend"
            bpy.ops.wm.save_as_mainfile(filepath=str(blend), check_existing=False)
            source.unlink()
            bpy.ops.wm.open_mainfile(filepath=str(blend))
            self.scene = bpy.context.scene
            self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), digest)
            editor = self.scene.sequence_editor
            self.assertEqual(len(editor.strips), 2)
            for item in result["strips"]:
                strip = editor.strips[item["name"]]
                self.assertEqual(strip.frame_final_duration, 120)
                self.assertEqual(strip.frame_final_start, 5)
                self.assertEqual(strip.channel, item["channel"])
                media_path = strip.filepath if strip.type == "MOVIE" else strip.sound.filepath
                self.assertEqual(Path(bpy.path.abspath(media_path)).resolve(), path.resolve())
            movie = editor.strips[result["strips"][0]["name"]]
            self.assertEqual(movie.fps, 24)
            self.assertGreater(movie.elements[0].orig_width, 0)
            # A fresh decoder checks the persisted video and audio bytes after reopen.
            expected = self.media._probe(
                self.scene,
                path,
                [
                    dict(kind="VIDEO", frame=5, channel=1, name="Video check"),
                    dict(kind="AUDIO", frame=5, channel=2, name="Audio check"),
                ],
                30,
            )
            self.assertEqual([item["duration_frames"] for item in expected], [120, 120])

    def test_corrupted_persistent_copy_is_not_trusted_for_repeat(self):
        source = self.source("unique.wav", wav_bytes(1.25))
        result = self.import_media(source)
        path = Path(result["asset_path"])
        original = path.read_bytes()
        try:
            path.write_bytes(original[:-8] + b"changed!")
            with self.assertRaises(ValueError):
                self.import_media(source)
            self.assertEqual(len(self.scene.sequence_editor.strips), 1)
        finally:
            path.write_bytes(original)

    def test_explicit_destinations_are_validated_before_target_mutation(self):
        source = self.source()
        for args in (
            dict(start_frame=True),
            dict(channel=0),
            dict(channel=129),
            dict(start_frame=1048574),
            dict(include_audio=True),
            dict(name=""),
        ):
            with self.subTest(args=args):
                with self.assertRaises(ValueError):
                    self.import_media(source, **args)
                self.assertIsNone(self.scene.sequence_editor)
