# SPDX-FileCopyrightText: 2026 Scenario Inc.
# SPDX-License-Identifier: GPL-3.0-or-later
"""Capture evidence cannot mask failed GUI setup or a changed installed package."""

import importlib
import json
import runpy
import subprocess
import sys
import zipfile
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest


@pytest.mark.parametrize(
    "failure",
    [
        None,
        "setup",
        "missing_png",
        "changed_install",
        "version",
        "online",
        "timeout",
        "minimum",
        "discovery",
        "snapshot",
        "cleanup",
        "output",
        "build",
    ],
)
def test_capture_evidence_and_cleanup(tmp_path, monkeypatch, failure):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[2] / "tools"))
    capture = importlib.import_module("capture_gui")

    def find(_):
        if failure == "discovery":
            raise ValueError("fixture missing executable")
        return Path("blender")

    monkeypatch.setattr(capture, "find_blender", find)
    normal = tmp_path / "normal"
    normal.mkdir()
    (normal / "keep").write_text("normal profile")
    monkeypatch.setattr(capture, "normal_profile_root", lambda: normal)
    monkeypatch.setenv("SCENARIO_API_KEY", "must-not-be-inherited")
    candidate = tmp_path / "input.zip"
    with zipfile.ZipFile(candidate, "w") as archive:
        archive.writestr(
            "blender_manifest.toml", 'id="scenario"\nversion="0.9.9"\nblender_version_min="5.0.0"'
        )
        archive.writestr("LICENSE", "fixture")
        archive.writestr("__init__.py", "# installed fixture")
    monkeypatch.setattr(capture, "validate", lambda session, path: capture.inspect_zip(path)[0])
    if failure == "snapshot":

        def snapshot(_):
            raise PermissionError("fixture unreadable profile")

        monkeypatch.setattr(capture, "profile_snapshot", snapshot)
    if failure == "cleanup":

        def cleanup(_):
            raise PermissionError("fixture locked profile")

        monkeypatch.setattr(capture.Session, "cleanup", cleanup)
    build_tools = importlib.import_module("build")
    monkeypatch.setattr(build_tools, "validate", lambda session, path: capture.inspect_zip(path)[0])

    def stage(source, destination):
        assert destination.parent.name == "tmp"
        destination.mkdir()
        (destination / "fixture.whl").write_bytes(b"staged wheel")
        return destination

    monkeypatch.setattr(build_tools, "prepare_source", stage)
    calls = []

    def step(session, name, command):
        calls.append(name)
        assert session.env["BLENDER_USER_RESOURCES"] == str(session.profile)
        assert "SCENARIO_API_KEY" not in session.env
        if name == "build":
            output = Path(command[command.index("--output-filepath") + 1])
            output.write_bytes(candidate.read_bytes())
            return None
        assert "--offline-mode" in command
        if name == "prepare":
            log = session.directory / "prepare.log"
            version = [4, 3, 2] if failure == "minimum" else [5, 0, 1]
            log.write_text("CAPTURE_VERSION=" + json.dumps(version))
            return log
        installed = session.profile / "extensions/user_default/scenario"
        if name == "install":
            with zipfile.ZipFile(candidate) as archive:
                archive.extractall(installed)
        if name == "capture":
            assert session.env["SCENARIO_GUI_PROBE"] == "1"
            assert command[command.index("--gpu-backend") + 1] == "opengl"
            if failure == "timeout":
                raise subprocess.TimeoutExpired("fixture GUI", 10)
            evidence = {
                "status": "failed" if failure == "setup" else "captured",
                "blender_version": [5, 1, 2] if failure == "version" else [5, 0, 1],
                "online_access": failure == "online",
            }
            (session.directory / "gui.json").write_text(json.dumps(evidence))
            if failure != "missing_png":
                (session.directory / "plugin.png").write_bytes(b"\x89PNG\r\n\x1a\nfixture")
            if failure == "changed_install":
                (installed / "__init__.py").write_text("# changed")
        return None

    monkeypatch.setattr(capture.Session, "step", step)
    args = SimpleNamespace(
        blender=None,
        output=tmp_path / "captures",
        timeout=20,
        zip=None if failure == "build" else candidate,
        view="sidebar",
        lane="image",
        fixture="form",
        delay=8,
        label="fixture milestone",
        gpu_backend="opengl",
        capture_backend="blender",
    )
    if failure == "output":
        args.output.write_text("not a directory")
    failed = failure not in (None, "build")
    assert capture.capture(args) == (1 if failed else 0)
    if failure == "output":
        assert calls == []
        return
    directory = next(args.output.iterdir())
    report = json.loads((directory / "report.json").read_text())
    expected = "cleanup_failed" if failure == "cleanup" else "failed" if failed else "captured"
    assert report["status"] == expected
    if failure in ("discovery", "snapshot"):
        assert calls == []
        assert "error" in report
    if failure == "cleanup":
        assert report["cleanup_error"] == "fixture locked profile"
        assert report["png_sha256"] == capture.sha256(directory / "plugin.png")
        assert report["normal_profile_unchanged"]
    assert report["label"] == "fixture milestone"
    assert (directory / "profile").exists() == failed
    assert (normal / "keep").read_text() == "normal profile"
    if failure == "minimum":
        assert calls == ["prepare"]
    if not failed:
        assert not (directory / "tmp").exists()
        assert not (directory / "source").exists()
        assert len(list(directory.rglob("*.zip"))) == 1
        assert not list(directory.rglob("*.whl"))
        assert report["normal_profile_unchanged"]
        assert report["zip_sha256"] == capture.sha256(candidate)
        assert report["png_sha256"] == capture.sha256(directory / "plugin.png")


def test_audio_composer_is_rejected_before_launch(tmp_path):
    import sys

    result = subprocess.run(
        [
            sys.executable,
            str(Path(__file__).resolve().parents[2] / "tools/capture_gui.py"),
            "--view",
            "composer",
            "--lane",
            "audio",
            "--output",
            str(tmp_path / "captures"),
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 2
    assert "composer has no audio lane" in result.stderr
    assert "Traceback" not in result.stderr
    assert not (tmp_path / "captures").exists()


@pytest.mark.parametrize("blank", [True, False])
def test_blank_permissions_capture_cannot_report_success(tmp_path, monkeypatch, blank):
    bpy = Mock()
    area = SimpleNamespace(type="PREFERENCES")
    bpy.context.window_manager.windows = [SimpleNamespace(screen=SimpleNamespace(areas=[area]))]
    bpy.context.temp_override.side_effect = lambda **_: nullcontext()
    bpy.ops.screen.screenshot.return_value = {"FINISHED"}
    pixels = [0, 0, 0, 1, 0, 0, 0, 1] if blank else [0, 0, 0, 1, 1, 1, 1, 1]
    shot = SimpleNamespace(size=[2, 1], pixels=pixels)
    bpy.data.images.load.return_value = shot
    monkeypatch.setitem(sys.modules, "bpy", bpy)
    monkeypatch.setitem(sys.modules, "gpu", Mock())
    monkeypatch.setattr(
        sys,
        "argv",
        ["blender", "--", str(tmp_path), "scenario", "sidebar", "image", "8", "form", "blender"],
    )
    scene = runpy.run_path(str(Path(__file__).resolve().parents[2] / "tools/capture_gui_scene.py"))
    assert not (tmp_path / "gui.json").exists()
    scene["guarded"](lambda: scene["capture_permissions"]({"view": "sidebar"}))()
    report = json.loads((tmp_path / "gui.json").read_text())
    assert report["status"] == ("failed" if blank else "captured")
    if blank:
        assert "Screenshot is blank" in report["error"]
    bpy.data.images.load.assert_called_once_with(
        str(tmp_path / "permissions.png"), check_existing=False
    )
    bpy.data.images.remove.assert_called_once_with(shot)
    bpy.ops.wm.quit_blender.assert_called_once()


@pytest.mark.parametrize("failure", [None, "screenshot", "missing_file"])
def test_preferences_complete_only_after_assistant_capture(tmp_path, monkeypatch, failure):
    timers = []
    bpy = Mock()
    bpy.context.preferences.addons = {"fixture.scenario": SimpleNamespace()}
    bpy.context.temp_override.side_effect = lambda **_: nullcontext()
    bpy.app.timers.register.side_effect = lambda callback, **_: timers.append(callback)

    def screenshot(*, filepath):
        if failure == "screenshot":
            raise RuntimeError("fixture screenshot failure")
        if failure != "missing_file":
            Path(filepath).write_bytes(b"fixture pixels")
        return {"FINISHED"}

    bpy.ops.screen.screenshot.side_effect = screenshot
    monkeypatch.setitem(sys.modules, "bpy", bpy)
    monkeypatch.setitem(sys.modules, "gpu", Mock())
    monkeypatch.setattr(
        sys,
        "argv",
        ["blender", "--", str(tmp_path), "scenario", "sidebar", "image", "8", "empty", "blender"],
    )
    scene = runpy.run_path(str(Path(__file__).resolve().parents[2] / "tools/capture_gui_scene.py"))
    timers.clear()
    complete = Mock()
    scene["capture_assistant_preferences"](Mock(), Mock(), complete)
    assert len(timers) == 1
    complete.assert_not_called()
    timers.pop(0)()
    if failure:
        complete.assert_not_called()
        assert json.loads((tmp_path / "gui.json").read_text())["status"] == "failed"
    else:
        complete.assert_called_once_with()
        assert (tmp_path / "preferences-assistant.png").is_file()
