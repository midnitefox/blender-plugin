# SPDX-FileCopyrightText: 2026 Scenario Inc.
# SPDX-License-Identifier: GPL-3.0-or-later
"""Offline tests for the native runner's isolation and artifact boundaries."""

import importlib.util
import json
import os
import runpy
import subprocess
import sys
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest

from tools import blender_env

ROOT = Path(__file__).resolve().parents[2]


def archive(path, extra=None):
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("blender_manifest.toml", 'id = "scenario"\nversion = "0.9.9"\n')
        z.writestr("__init__.py", "# fixture")
        z.writestr("LICENSE", "fixture license")
        if extra:
            entry = zipfile.ZipInfo()
            # ZipInfo's constructor normalizes backslashes on Windows. Preserve
            # malicious input exactly as another operating system can write it.
            entry.filename = extra[0]
            z.writestr(entry, extra[1])
    return path


def test_environment_discards_credentials_and_blender_overrides(tmp_path):
    original = {
        "PATH": "/usr/bin",
        "SCENARIO_TEST_API_KEY": "private",
        "SCENARIO_API_SECRET": "private",
        "BLENDER_USER_SCRIPTS": "/real/scripts",
        "BLENDER_USER_RESOURCES": "/real/profile",
        "PYTHONPATH": "/source",
    }
    env = blender_env.isolated_environment(tmp_path / "profile", tmp_path / "tmp", original)
    assert env["PATH"] == original["PATH"]
    assert env["BLENDER_USER_RESOURCES"] == str(tmp_path / "profile")
    assert env["TMPDIR"] == str(tmp_path / "tmp")
    assert "PYTHONPATH" not in env
    assert "BLENDER_USER_SCRIPTS" not in env
    assert not any(k.startswith("SCENARIO_") for k in env)
    assert original["BLENDER_USER_RESOURCES"] == "/real/profile"


def test_explicit_blender_is_authoritative(tmp_path, monkeypatch):
    binary = tmp_path / "Blender with spaces"
    binary.write_text("fixture")
    binary.chmod(0o755)
    monkeypatch.setenv("BLENDER", str(binary))
    assert blender_env.find_blender() == binary
    with pytest.raises(ValueError, match="Blender not found"):
        blender_env.find_blender(str(tmp_path / "missing"))


@pytest.mark.parametrize("selection", ["argument", "environment", "argument_overrides_environment"])
def test_locator_cli_prints_selected_path_without_executing_it(tmp_path, selection):
    binary = tmp_path / "Blender with spaces"
    # Not an executable program: discovery must inspect the path without running it.
    binary.write_text("fixture")
    binary.chmod(0o755)
    env = dict(os.environ, BLENDER=str(tmp_path / "missing"))
    args = []
    if selection == "environment":
        env["BLENDER"] = str(binary)
    else:
        args = ["--blender", str(binary)]
        if selection == "argument":
            env.pop("BLENDER")
    result = subprocess.run(
        [sys.executable, str(ROOT / "tools/blender_env.py"), *args],
        env=env,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == str(binary.resolve())
    assert result.stderr == ""


@pytest.mark.parametrize("selection", ["argument", "environment"])
def test_locator_cli_missing_selected_binary_fails_without_fallback(tmp_path, selection):
    args = ["--blender", str(tmp_path / "missing")] if selection == "argument" else []
    env = dict(os.environ, BLENDER=sys.executable if args else str(tmp_path / "missing"))
    result = subprocess.run(
        [sys.executable, str(ROOT / "tools/blender_env.py"), *args],
        env=env,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 1
    assert result.stdout == ""
    assert "Blender not found: set BLENDER=" in result.stderr
    assert "Traceback" not in result.stderr


def test_locator_cli_help_does_not_require_blender(tmp_path):
    result = subprocess.run(
        [sys.executable, str(ROOT / "tools/blender_env.py"), "--help"],
        env=dict(os.environ, BLENDER=str(tmp_path / "missing")),
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert "--blender" in result.stdout


@pytest.mark.parametrize("change", ["modified", "missing", "extra", "symlink"])
def test_installed_files_must_match_candidate(tmp_path, change):
    zip_path = archive(tmp_path / "candidate.zip")
    installed = tmp_path / "installed"
    with zipfile.ZipFile(zip_path) as z:
        z.extractall(installed)
    blender_env.verify_installed(zip_path, installed)
    if change == "modified":
        (installed / "__init__.py").write_text("stale")
    elif change == "missing":
        (installed / "LICENSE").unlink()
    elif change == "extra":
        (installed / "stale.py").write_text("old")
    else:
        (installed / "link.py").symlink_to(installed / "__init__.py")
    with pytest.raises(ValueError):
        blender_env.verify_installed(zip_path, installed)


@pytest.mark.parametrize("name", ["../escape", "/absolute", "C:/outside", "dir\\escape"])
def test_unsafe_archives_are_rejected(tmp_path, name):
    with pytest.raises(ValueError, match="Unsafe"):
        blender_env.inspect_zip(archive(tmp_path / "bad.zip", (name, "payload")))


def test_duplicate_zip_members_are_rejected(tmp_path):
    with pytest.warns(UserWarning, match="Duplicate"):
        path = archive(tmp_path / "duplicate.zip", ("__init__.py", "second"))
    with pytest.raises(ValueError, match="duplicate"):
        blender_env.inspect_zip(path)


@pytest.mark.parametrize("profile", ["absent", "unmanaged", "corrupt", "array", "different"])
def test_unmanaged_test_execution_is_refused_without_blender_import(tmp_path, profile):
    env = {k: v for k, v in os.environ.items() if not k.startswith("BLENDER_")}
    if profile != "absent":
        env["BLENDER_USER_RESOURCES"] = str(tmp_path / "profile")
    if profile in {"corrupt", "array", "different"}:
        content = {"corrupt": "{", "array": "[]", "different": '{"profile":"other"}'}[profile]
        (tmp_path / "runner.json").write_text(content)
    result = subprocess.run(
        [sys.executable, str(ROOT / "tests/blender/run_all.py")],
        env=env,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 2
    assert "use make test-blender" in result.stderr
    assert "No module named 'bpy'" not in result.stderr


def test_normal_profile_snapshot_detects_writes(tmp_path):
    before = blender_env.profile_snapshot(tmp_path)
    (tmp_path / "userpref.blend").write_bytes(b"fixture")
    assert blender_env.profile_snapshot(tmp_path) != before


@pytest.fixture
def runner(monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT / "tools"))
    spec = importlib.util.spec_from_file_location(
        "runner_test_subject", ROOT / "tools/test_blender.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    # Runner tests exercise process/artifact boundaries; bundle verification has
    # separate synthetic tests and must never download dependencies here.
    monkeypatch.setattr(module, "prepare_source", lambda source, destination: source)
    return module


def test_failed_blender_exit_is_preserved_and_existing_profiles_survive(
    tmp_path, monkeypatch, runner
):
    old_profile = tmp_path / "existing-profile"
    old_profile.mkdir()
    sentinel = old_profile / "keep"
    sentinel.write_text("user state")
    monkeypatch.setenv("BLENDER_USER_RESOURCES", str(old_profile))
    monkeypatch.setattr(runner, "find_blender", lambda _: Path("blender"))
    monkeypatch.setattr(runner, "normal_profile_root", lambda: old_profile)

    def failure(*args, **kwargs):
        assert kwargs["env"]["BLENDER_USER_RESOURCES"] != str(old_profile)
        raise subprocess.CalledProcessError(7, "fixture-failure")

    monkeypatch.setattr(runner, "run_step", failure)
    args = SimpleNamespace(
        blender=None,
        artifacts=tmp_path / "artifacts",
        suite="baseline",
        timeout=2,
        keep_profile=False,
    )
    assert runner.run(args) == 7
    assert sentinel.read_text() == "user state"
    report = json.loads(next(args.artifacts.glob("run-*/result.json")).read_text())
    assert report["status"] == "failed"
    assert "exited 7" in report["error"]
    assert next(args.artifacts.glob("run-*/profile")).is_dir()


def test_native_guard_blocks_source_fallback_and_external_network():
    code = """
import runpy, sys, socket
module = runpy.run_path(sys.argv[1])
sys.meta_path.insert(0, module["NoSourceImports"]())
try:
    import scenario.core.config
except ImportError as error:
    assert "Source-checkout" in str(error)
else:
    raise AssertionError("source checkout was accepted")
violations = module["install_network_guard"]()
# Test audit events without making any actual network connection.
with socket.socket() as sock:
    sys.audit("socket.connect", sock, ("127.0.0.1", 1234))
    try:
        sys.audit("socket.connect", sock, ("203.0.113.1", 443))
    except RuntimeError as error:
        assert "External network" in str(error)
    else:
        raise AssertionError("external connection was accepted")
assert violations == ["socket.connect"]
"""
    result = subprocess.run(
        [sys.executable, "-c", code, str(ROOT / "tests/blender/run_all.py")],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("version,exit_code", [([5, 2, 1], 7), ([5, 2, 0], 1)])
def test_expected_version_checks_numeric_release_not_lts_label(
    tmp_path, monkeypatch, runner, version, exit_code
):
    monkeypatch.setattr(runner, "find_blender", lambda _: Path("blender"))
    monkeypatch.setattr(runner, "normal_profile_root", lambda: tmp_path / "normal")
    steps = []

    def probe_then_stop(*args, **kwargs):
        steps.append(kwargs["name"])
        if kwargs["name"] == "probe":
            log = tmp_path / "probe.log"
            log.write_text(
                "SCENARIO_ENV=" + json.dumps({"blender": "5.2.1 LTS", "version": version})
            )
            return log
        raise subprocess.CalledProcessError(7, "build")

    monkeypatch.setattr(runner, "run_step", probe_then_stop)
    args = SimpleNamespace(
        blender=None,
        artifacts=tmp_path / "artifacts",
        suite="baseline",
        timeout=2,
        keep_profile=False,
        expected_version="5.2.1",
        zip=None,
    )
    assert runner.run(args) == exit_code
    assert steps == (["probe", "validate-source"] if exit_code == 7 else ["probe"])


@pytest.mark.parametrize("manifest", ["not TOML", 'name = "Missing identity"\n'])
def test_invalid_source_stops_runner_before_staging_and_keeps_validator_status(
    tmp_path, monkeypatch, runner, manifest
):
    checkout = tmp_path / "checkout"
    (checkout / "scenario").mkdir(parents=True)
    (checkout / "scenario/blender_manifest.toml").write_text(manifest)
    monkeypatch.setattr(runner, "ROOT", checkout)
    monkeypatch.setattr(runner, "find_blender", lambda _: Path("blender"))
    monkeypatch.setattr(runner, "normal_profile_root", lambda: tmp_path / "normal")
    monkeypatch.setattr(
        runner, "prepare_source", lambda *args: pytest.fail("Must not stage wheels")
    )
    steps = []

    def step(binary, command, **kwargs):
        name = kwargs["name"]
        steps.append(name)
        log = kwargs["directory"] / f"{name}.log"
        if name == "probe":
            log.write_text("SCENARIO_ENV=" + json.dumps({"blender": "5.0.1", "version": [5, 0, 1]}))
            return log
        assert command == [
            "--offline-mode",
            "--command",
            "extension",
            "validate",
            str(checkout / "scenario"),
        ]
        log.write_text("Invalid manifest fixture")
        raise subprocess.CalledProcessError(19, command)

    monkeypatch.setattr(runner, "run_step", step)
    args = SimpleNamespace(
        blender=None,
        artifacts=tmp_path / "artifacts",
        suite="baseline",
        timeout=2,
        keep_profile=False,
        expected_version=None,
        zip=None,
    )
    assert runner.run(args) == 19
    assert steps == ["probe", "validate-source"]
    directory = next(args.artifacts.glob("run-*"))
    report = json.loads((directory / "result.json").read_text())
    assert report["status"] == "failed" and "exited 19" in report["error"]
    assert (directory / "validate-source.log").read_text() == "Invalid manifest fixture"
    assert list((directory / "tmp").iterdir()) == []
    assert not list(directory.glob("*.zip"))
    assert not (tmp_path / "normal").exists()


@pytest.mark.parametrize("supplied", [False, True])
@pytest.mark.parametrize(
    "minimum,checkout_minimum,expected_exit", [("5.0.0", "5.2.0", 7), ("5.1.0", "5.0.0", 1)]
)
def test_candidate_minimum_is_checked_before_validation(
    tmp_path, monkeypatch, runner, supplied, minimum, checkout_minimum, expected_exit
):
    checkout = tmp_path / "checkout"
    (checkout / "scenario").mkdir(parents=True)
    (checkout / "scenario/blender_manifest.toml").write_text(
        f'id = "scenario"\nversion = "0.9.9"\nblender_version_min = "{checkout_minimum}"\n'
    )
    for path in (checkout / "LICENSE", checkout / "scenario/LICENSE"):
        path.write_text("fixture license")
    monkeypatch.setattr(runner, "ROOT", checkout)
    monkeypatch.setattr(runner, "find_blender", lambda _: Path("blender"))
    monkeypatch.setattr(runner, "normal_profile_root", lambda: tmp_path / "normal")

    def candidate(path):
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr(
                "blender_manifest.toml",
                f'id = "scenario"\nversion = "0.9.9"\nblender_version_min = "{minimum}"\n',
            )
            archive.writestr("LICENSE", "fixture license")
            archive.writestr("__init__.py", "# fixture")

    supplied_zip = tmp_path / "supplied.zip"
    candidate(supplied_zip)
    steps = []

    def step(binary, command, **kwargs):
        name = kwargs["name"]
        steps.append(name)
        if name == "probe":
            log = tmp_path / "probe.log"
            log.write_text("SCENARIO_ENV=" + json.dumps({"blender": "5.0.1", "version": [5, 0, 1]}))
            return log
        if name == "validate-source":
            return tmp_path / "validate-source.log"
        if name == "build":
            candidate(Path(command[command.index("--output-filepath") + 1]))
            return tmp_path / "build.log"
        raise subprocess.CalledProcessError(7, name)

    monkeypatch.setattr(runner, "run_step", step)
    args = SimpleNamespace(
        blender=None,
        artifacts=tmp_path / "artifacts",
        suite="baseline",
        timeout=2,
        keep_profile=False,
        expected_version=None,
        zip=supplied_zip if supplied else None,
    )
    assert runner.run(args) == expected_exit
    assert steps == ["probe"] + ([] if supplied else ["validate-source", "build"]) + (
        ["validate"] if expected_exit == 7 else []
    )
    if expected_exit == 1:
        report = json.loads(next(args.artifacts.glob("run-*/result.json")).read_text())
        assert "candidate ZIP minimum 5.1.0" in report["error"]


@pytest.mark.parametrize("failure,keep", [(False, False), (False, True), (True, False)])
@pytest.mark.parametrize("selection", [[], ["test_provider_setup", "test_agent_artifacts"]])
def test_staging_cleanup_keeps_artifacts_and_failure_diagnostics(
    tmp_path, monkeypatch, runner, failure, keep, selection
):
    monkeypatch.setattr(runner, "find_blender", lambda _: Path("blender"))
    monkeypatch.setattr(runner, "normal_profile_root", lambda: tmp_path / "normal")
    staged = []

    def stage(source, destination):
        destination.mkdir(parents=True)
        (destination / "fixture.whl").write_bytes(b"wheel")
        staged.append(destination)
        return destination

    def step(*args, **kwargs):
        directory, name = kwargs["directory"], kwargs["name"]
        log = directory / f"{name}.log"
        log.write_text("fixture")
        if name == "probe":
            log.write_text('SCENARIO_ENV={"blender":"5.0.1","version":[5,0,1]}')
        elif name == "build":
            command = args[1]
            candidate = Path(command[command.index("--output-filepath") + 1])
            with zipfile.ZipFile(candidate, "w") as archive:
                for member in ("blender_manifest.toml", "LICENSE", "__init__.py"):
                    archive.write(ROOT / "scenario" / member, member)
        elif name == "tests":
            command = args[1]
            actual = [
                command[index + 1] for index, value in enumerate(command) if value == "--test"
            ]
            assert actual == selection
            if failure:
                raise subprocess.CalledProcessError(7, "tests")
            candidate = next(directory.glob("*.zip"))
            (directory / "tests.json").write_text(
                json.dumps(
                    {
                        "zip_sha256": runner.sha256(candidate),
                        "success": True,
                        "tests_run": 1,
                    }
                )
            )
        return log

    monkeypatch.setattr(runner, "prepare_source", stage)
    monkeypatch.setattr(runner, "validate_bundle", lambda _: None)
    monkeypatch.setattr(runner, "run_step", step)
    args = SimpleNamespace(
        blender=None,
        artifacts=tmp_path / "artifacts",
        suite="baseline",
        test=selection,
        timeout=2,
        keep_profile=keep,
        expected_version=None,
        zip=None,
    )
    assert runner.run(args) == (7 if failure else 0)
    directory = next(args.artifacts.glob("run-*"))
    assert staged == [directory / "tmp/source"]
    assert staged[0].exists() == (failure or keep)
    assert (directory / "profile").exists() == (failure or keep)
    assert (directory / "result.json").is_file()
    assert (directory / "tests.log").is_file()
    assert len(list(directory.glob("*.zip"))) == 1
    report = json.loads((directory / "result.json").read_text())
    assert report["suite"] == ("focused" if selection else "baseline")
    assert report["selected_tests"] == selection


def test_native_focused_selection_uses_only_requested_names(monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT / "tools"))
    native = runpy.run_path(str(ROOT / "tests/blender/run_all.py"))
    from unittest.mock import Mock

    loader = Mock()
    selection = ["test_provider_setup", "test_agent_artifacts.AgentArtifactTests"]
    suite = native["load_suite"](loader, "baseline", selection)
    loader.loadTestsFromNames.assert_called_once_with(selection)
    loader.discover.assert_not_called()
    assert suite is loader.loadTestsFromNames.return_value
