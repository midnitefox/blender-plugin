# SPDX-FileCopyrightText: 2026 Scenario Inc.
# SPDX-License-Identifier: GPL-3.0-or-later
"""Installed folder, file and link buttons open the system application without credentials.

Each button is drawn into a recording layout and its operator then runs; it must
reach core.jobs.playback's filtered launcher with the drawn path or link. The
launchers, or the process they start, are recorded and never started. Nothing
contacts Scenario.
"""

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import bpy
from helpers import addon_name, submodule

SECRETS = {
    "SCENARIO_API_KEY": "launch-key",
    "SCENARIO_API_SECRET": "fixture",
    "OPEN_STUDIO_TOKEN": "assistant-fixture",
    "PYTHONPATH": "start-up",
    "PYTHONHOME": "home",
    "HTTPS_PROXY": "http://proxy.invalid",
    "no_proxy": "*",
}
OPENERS = {"scenario.open_path": "filepath", "scenario.open_url": "url"}


class Layout:
    """Records every operator button drawn under it, nested rows and boxes included."""

    def __init__(self, buttons=None):
        self.buttons = [] if buttons is None else buttons

    def operator(self, idname, **options):
        button = SimpleNamespace(idname=idname, text=options.get("text"))
        self.buttons.append(button)
        return button

    def __getattr__(self, name):
        return lambda *args, **kwargs: Layout(self.buttons)

    def opens(self):
        """(text, path or link) of each folder, file and link button, in drawing order."""
        return [
            (button.text, getattr(button, OPENERS[button.idname]))
            for button in self.buttons
            if button.idname in OPENERS
        ]

    def blender_openers(self):
        return [b.idname for b in self.buttons if b.idname.startswith(("wm.path", "wm.url"))]


class Launcher:
    """Stands in for an opener process: running until ``code`` is set."""

    def __init__(self, command=(), code=None, **options):
        self.command, self.options, self.code = list(command), options, code

    def poll(self):
        return self.code


class Preferences:
    """The add-on's real preferences, drawn into a recording layout."""

    def __init__(self, preferences, layout):
        self.__dict__.update(_preferences=preferences, layout=layout)

    def __getattr__(self, name):
        return getattr(self._preferences, name)


class DesktopOpenTests(unittest.TestCase):
    def setUp(self):
        self.desktop = submodule("blender.desktop")
        self.playback = submodule("core.jobs.playback")
        self.runtime = submodule("blender.runtime")
        self.enterContext(patch.object(self.runtime, "state", self.runtime.RuntimeState()))
        self.root = Path(tempfile.mkdtemp(prefix="scenario-open-")).resolve()
        self.addCleanup(shutil.rmtree, self.root, True)
        self.opened = []

    def tearDown(self):
        if bpy.app.timers.is_registered(self.desktop._reap):
            bpy.app.timers.unregister(self.desktop._reap)
        self.desktop._launchers.clear()

    def record(self, **launcher):
        """Replace both filtered launchers; each records its target and returns a process."""

        def recorder(kind):
            def launch(target, **options):
                self.assertEqual(options, {})
                self.opened.append((kind, os.fspath(target)))
                return Launcher(**launcher)

            return launch

        self.enterContext(patch.object(self.playback, "system_open", side_effect=recorder("path")))
        self.enterContext(
            patch.object(self.playback, "system_open_url", side_effect=recorder("url"))
        )

    def press(self, layout):
        """Run every drawn folder, file and link button's operator, as a click would."""
        for button in layout.buttons:
            if button.idname == "scenario.open_path":
                self.assertEqual(bpy.ops.scenario.open_path(filepath=button.filepath), {"FINISHED"})
            elif button.idname == "scenario.open_url":
                self.assertEqual(bpy.ops.scenario.open_url(url=button.url), {"FINISHED"})

    def test_local_folder_opens_through_the_filtered_launcher(self):
        self.record()
        output = os.path.normpath(str(self.root))
        self.assertEqual(bpy.ops.scenario.open_path(filepath=output), {"FINISHED"})
        self.assertEqual(self.opened, [("path", output)])
        self.assertTrue(Path(output).is_dir())

    def test_film_capture_buttons_open_the_capture_and_its_files(self):
        controls = submodule("blender.film_capture_controls")
        capture = self.root / "capture.mp4"
        capture.write_bytes(b"fixture")
        files = self.root / "capture-files"
        files.mkdir()
        status = {
            "scene": "Shot",
            "phase": "CAPTURED",
            "review_id": "review",
            "path": str(capture),
            "error": "",
            "directory": str(files),
        }
        owner = SimpleNamespace(
            session=SimpleNamespace(
                film_capture=SimpleNamespace(current=lambda scene, shot: status)
            )
        )
        layout = Layout()
        with (
            patch.object(controls, "commands", return_value=owner),
            patch.object(controls, "selected", return_value="shot"),
        ):
            controls.SCENARIO_PT_film_capture.draw(
                SimpleNamespace(layout=layout), SimpleNamespace(scene=bpy.context.scene)
            )
        self.assertEqual(
            layout.opens(),
            [("Open capture", str(capture)), ("Open capture files", str(files))],
        )
        self.assertEqual(layout.blender_openers(), [])
        self.record()
        self.press(layout)
        self.assertEqual(
            self.opened,
            [("path", os.path.normpath(str(capture))), ("path", os.path.normpath(str(files)))],
        )

    def test_film_export_and_case_study_open_their_destination_folder(self):
        destination = self.root / "Prepared cut - Final Review.mp4"
        destination.write_bytes(b"fixture")
        status = {
            "phase": "EXPORTED",
            "review_id": "review",
            "destination": str(destination),
            "sha256": "0" * 64,
            "directory": "",
            "error": "",
        }
        self.record()
        for name, panel in (
            ("film_export_controls", "SCENARIO_PT_film_export"),
            ("film_case_study_controls", "SCENARIO_PT_film_case_study"),
        ):
            with self.subTest(name):
                controls = submodule(f"blender.{name}")
                layout = Layout()
                owner = SimpleNamespace(current=lambda scene: status)
                with patch.object(controls, "commands", return_value=owner):
                    getattr(controls, panel).draw(
                        SimpleNamespace(layout=layout), SimpleNamespace(scene=bpy.context.scene)
                    )
                self.assertEqual(layout.opens(), [("Open folder", str(self.root))])
                self.assertEqual(layout.blender_openers(), [])
                self.opened.clear()
                self.press(layout)
                self.assertEqual(self.opened, [("path", os.path.normpath(str(self.root)))])

    def test_preference_links_open_through_the_filtered_launcher(self):
        updates = submodule("blender.updates")
        preferences = bpy.context.preferences.addons[addon_name()].preferences
        layout = Layout()
        # The assistant preferences expose only the fork's guide and releases.
        type(preferences).draw(Preferences(preferences, layout), bpy.context)
        links = [
            ("Open Studio releases", updates.RELEASES_URL),
            ("Setup guide", updates.GUIDE_URL),
        ]
        self.assertEqual(layout.opens(), links)
        self.assertEqual(layout.blender_openers(), [])
        self.record()
        self.press(layout)
        self.assertEqual(self.opened, [("url", url) for _, url in links])

    def test_the_started_opener_gets_no_credentials_python_start_up_or_proxies(self):
        launched = []

        def launch(command, **options):
            launched.append(Launcher(command, **options))
            return launched[-1]

        link = "https://app.scenario.com/team"
        # The real launchers with only their process faked: nothing starts.
        with (
            patch.object(subprocess, "Popen", side_effect=launch),
            patch.dict(os.environ, SECRETS),
        ):
            self.assertEqual(bpy.ops.scenario.open_path(filepath=str(self.root)), {"FINISHED"})
            self.assertEqual(bpy.ops.scenario.open_url(url=link), {"FINISHED"})
        folder, page = launched
        self.assertEqual(folder.command[-1], os.path.normpath(str(self.root)))
        self.assertEqual(page.command[-1], link)
        for process in launched:
            if sys.platform == "darwin":
                self.assertEqual(process.command[:-1], ["/usr/bin/open"])
            elif sys.platform.startswith("win"):
                self.assertEqual(process.command[:3], [sys.executable, "-I", "-S"])
            else:
                self.assertEqual(process.command[:-1], ["xdg-open"])
            env = process.options["env"]
            for name in SECRETS:
                self.assertNotIn(name, env)
            self.assertFalse(
                [
                    key
                    for key in env
                    if key.upper().startswith(("SCENARIO_", "OPEN_STUDIO_", "PYTHON"))
                    or key.upper() in {"HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY"}
                ]
            )
            self.assertIn("PATH", env)
            for stream in ("stdin", "stdout", "stderr"):
                self.assertIs(process.options[stream], subprocess.DEVNULL)
        # Both launchers are watched until they exit.
        self.assertEqual(len(self.desktop._launchers), 2)
        self.assertTrue(bpy.app.timers.is_registered(self.desktop._reap))

    def test_refusals_are_reported_before_anything_starts(self):
        with patch.object(subprocess, "Popen", side_effect=AssertionError("Launched")):
            with self.assertRaisesRegex(RuntimeError, "File path was not set"):
                bpy.ops.scenario.open_path(filepath="")
            with self.assertRaisesRegex(RuntimeError, "File or folder not found"):
                bpy.ops.scenario.open_path(filepath=str(self.root / "absent"))
            for url in ("", "file:///etc/hosts", "http://app.scenario.com/team", "-a"):
                with self.assertRaisesRegex(RuntimeError, "Only https links"):
                    bpy.ops.scenario.open_url(url=url)
        self.assertEqual(self.desktop._launchers, [])

    def test_a_missing_opener_is_reported(self):
        with (
            patch.object(self.playback, "system_open", side_effect=FileNotFoundError("xdg")),
            patch.object(self.playback, "system_open_url", side_effect=FileNotFoundError("xdg")),
        ):
            with self.assertRaisesRegex(RuntimeError, "could not open this file or folder"):
                bpy.ops.scenario.open_path(filepath=str(self.root))
            with self.assertRaisesRegex(RuntimeError, "could not open this link"):
                bpy.ops.scenario.open_url(url="https://app.scenario.com/team")
        self.assertEqual(self.desktop._launchers, [])

    def test_a_launcher_that_exits_with_an_error_is_reported_on_the_status_line(self):
        self.record()
        bpy.ops.scenario.open_path(filepath=str(self.root))
        bpy.ops.scenario.open_url(url="https://app.scenario.com/team")
        folder, page = (entry[0] for entry in self.desktop._launchers)
        self.assertEqual(self.desktop._reap(), self.desktop.POLL_INTERVAL)
        self.assertEqual(self.runtime.state.last_message, "")
        folder.code = 0
        self.assertEqual(self.desktop._reap(), self.desktop.POLL_INTERVAL)
        self.assertEqual(self.runtime.state.last_message, "")
        page.code = 1
        self.assertIsNone(self.desktop._reap())
        self.assertEqual(self.runtime.state.last_message, self.desktop.URL_FAILURE)
        self.assertEqual(self.desktop._launchers, [])
        self.record(code=2)
        bpy.ops.scenario.open_path(filepath=str(self.root))
        self.assertIsNone(self.desktop._reap())
        self.assertEqual(self.runtime.state.last_message, self.desktop.PATH_FAILURE)

    def test_a_launcher_that_keeps_running_is_forgotten_without_a_report(self):
        self.record()
        with patch.object(self.desktop, "POLL_LIMIT", 2):
            bpy.ops.scenario.open_url(url="https://app.scenario.com/team")
        self.assertEqual(self.desktop._reap(), self.desktop.POLL_INTERVAL)
        self.assertIsNone(self.desktop._reap())
        self.assertEqual(self.desktop._launchers, [])
        self.assertEqual(self.runtime.state.last_message, "")


if __name__ == "__main__":
    unittest.main()
