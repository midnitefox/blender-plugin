# SPDX-FileCopyrightText: 2026 Scenario Inc.
# SPDX-License-Identifier: GPL-3.0-or-later
"""Private player copies of saved video and audio results, offline and unpaid."""

import hashlib
import os
import stat
import subprocess
import threading
import time
from dataclasses import replace
from pathlib import Path

import pytest

from scenario.core.jobs import playback
from scenario.core.jobs.result_previews import PreviewCanceled
from scenario.core.jobs.results import ResultError
from scenario.core.jobs.store import (
    JobIntent,
    JobState,
    ResultAsset,
    StoreConflict,
)
from scenario.core.jobs.transfers import DownloadedResult, _root
from scenario.core.jobs.workers import JobWorkers
from tests.unit.test_result_previews import (
    MP4,
    ORIGIN,
    SCOPE,
    png,
    preview_env,
    wav,
)

WEBM = b"\x1a\x45\xdf\xa3" + b"\x00" * 60


def saved(service, assets, request_id="request"):
    """A READY job whose receipts, named as given, match private local bytes."""
    store = service.store
    current = store.create(
        JobIntent(request_id, SCOPE, ORIGIN, "model", "model", "a" * 64, "b" * 64, "1.0")
    )
    for state in (JobState.SUBMITTING, JobState.REMOTE, JobState.SUCCEEDED):
        current = store.transition(
            request_id,
            expected_revision=current.revision,
            state=state,
            remote_job_id="remote" if state == JobState.REMOTE else None,
        )
    manifest = tuple(
        ResultAsset(asset_id, name, media_type, len(data))
        for asset_id, name, media_type, data in assets
    )
    current = store.set_results(request_id, manifest, expected_revision=current.revision)
    current = store.transition(
        request_id, expected_revision=current.revision, state=JobState.DOWNLOADING
    )
    directory = service.coordinator._results._directory(current)
    for item, (*_, data) in zip(manifest, assets, strict=True):
        (directory / item.name).write_bytes(data)
        receipt = DownloadedResult(item.name, len(data), hashlib.sha256(data).hexdigest())
        current = store.record_download(
            request_id, item.asset_id, receipt, expected_revision=current.revision
        )
    record = store.transition(request_id, expected_revision=current.revision, state=JobState.READY)
    return record, directory


@pytest.fixture
def env(tmp_path):
    with preview_env(tmp_path) as state:
        yield state


@pytest.fixture
def root(env):
    return env.cache.parent / "media-playback"


def copy_of(service, record, asset_id, root, *, cancel=None, **options):
    return service.coordinator.prepare_playback(
        record.intent.request_id,
        expected_revision=record.revision,
        asset_id=asset_id,
        root=root,
        cancel=cancel or threading.Event(),
        **options,
    )


def copies(root):
    return sorted(path.name for path in root.iterdir()) if root.exists() else []


def assert_offline(service):
    assert service.calls == [] and service.downloader.calls == []


def test_copy_has_the_receipt_bytes_a_canonical_name_and_private_modes(env, root):
    record, _ = saved(env, [("clip", "run.command", "video/mp4", MP4)])
    copy = copy_of(env, record, "clip", root)
    assert copy.path.name == "media.mp4" and copy.kind == "video"
    assert copy.path.read_bytes() == MP4 and copy.size == len(MP4)
    assert copy.directory.parent == _root(root) and copy.directory.name.startswith("play-")
    assert copy.key.sha256 == hashlib.sha256(MP4).hexdigest()
    assert (copy.request_id, copy.asset_id, copy.media_type) == ("request", "clip", "video/mp4")
    assert playback.unchanged(copy)
    if os.name != "nt":
        assert stat.S_IMODE(copy.path.stat().st_mode) == 0o400
        assert stat.S_IMODE(copy.directory.stat().st_mode) == 0o700
        assert stat.S_IMODE(root.stat().st_mode) == 0o700
    assert "path" not in repr(copy) and str(copy.directory) not in repr(copy)
    assert_offline(env)
    assert env.store.get("request") == record


@pytest.mark.parametrize(
    ("media_type", "data", "name", "kind"),
    [
        ("video/webm", WEBM, "media.webm", "video"),
        ("audio/wav", wav(), "media.wav", "audio"),
        ("audio/mpeg", b"ID3\x04" + b"\x00" * 60, "media.mp3", "audio"),
        ("audio/flac", b"fLaC" + b"\x00" * 60, "media.flac", "audio"),
        ("video/x-msvideo", b"RIFF\x00\x00\x00\x00AVI " + b"\x00" * 60, "media.avi", "video"),
    ],
)
def test_every_playable_type_gets_its_canonical_suffix(env, root, media_type, data, name, kind):
    record, _ = saved(env, [("asset", "saved.bin", media_type, data)])
    copy = copy_of(env, record, "asset", root)
    assert (copy.path.name, copy.kind) == (name, kind)
    assert copy.path.read_bytes() == data


def test_unsupported_missing_or_undownloaded_results_are_refused(env, root):
    record, _ = saved(
        env,
        [("image", "image.png", "image/png", png(2, 2)), ("clip", "clip.mp4", "video/mp4", MP4)],
    )
    for asset_id in ("image", "absent"):
        with pytest.raises(ResultError, match="video or audio"):
            copy_of(env, record, asset_id, root)
    with pytest.raises(StoreConflict):
        env.coordinator.prepare_playback(
            "request",
            expected_revision=record.revision + 1,
            asset_id="clip",
            root=root,
            cancel=threading.Event(),
        )
    assert copies(root) == []
    assert_offline(env)


def test_a_job_that_is_not_downloaded_has_nothing_to_play(env, root):
    current = env.store.create(
        JobIntent("pending", SCOPE, ORIGIN, "model", "model", "a" * 64, "b" * 64, "1.0")
    )
    with pytest.raises(StoreConflict):
        copy_of(env, current, "clip", root)
    assert_offline(env)


def test_changed_saved_bytes_or_mismatched_contents_leave_no_copy(env, root):
    record, directory = saved(
        env,
        [
            ("clip", "clip.mp4", "video/mp4", MP4),
            ("fake", "fake.mp4", "video/mp4", b"not an iso media file" * 3),
        ],
    )
    with pytest.raises(playback.PlaybackError, match="do not match"):
        copy_of(env, record, "fake", root)
    (directory / "clip.mp4").write_bytes(MP4[:-1] + b"\x01")
    with pytest.raises(playback.PlaybackError, match="receipt"):
        copy_of(env, record, "clip", root)
    (directory / "clip.mp4").unlink()
    with pytest.raises(playback.PlaybackError):
        copy_of(env, record, "clip", root)
    assert copies(root) == []


def test_oversized_receipts_are_refused_before_copying(env, root, monkeypatch):
    record, _ = saved(env, [("clip", "clip.mp4", "video/mp4", MP4)])
    monkeypatch.setattr(playback, "MAX_BYTES", len(MP4) - 1)
    with pytest.raises(playback.PlaybackError, match="too large"):
        copy_of(env, record, "clip", root)
    assert copies(root) == []


def test_an_unchanged_copy_is_reused_and_a_changed_one_replaced(env, root):
    record, _ = saved(env, [("clip", "clip.mp4", "video/mp4", MP4)])
    first = copy_of(env, record, "clip", root)
    assert copy_of(env, record, "clip", root, reuse=first, keep=[first.directory]) is first
    os.chmod(first.path, 0o600)  # Any metadata change invalidates reuse.
    second = copy_of(env, record, "clip", root, reuse=first, keep=[first.directory])
    assert second is not first and second.path.read_bytes() == MP4
    assert first.directory.exists()  # The owner still holds it; only it discards it.
    other, _ = saved(env, [("sound", "sound.wav", "audio/wav", wav())], "other")
    third = copy_of(env, other, "sound", root, reuse=second)
    assert third is not second and third.kind == "audio"


def _changes_during_copy(monkeypatch, store, record):
    """The first read admits ``record``; the recheck after copying reads the next value."""
    reads = [record]

    def get(request_id):
        return reads.pop(0) if len(reads) > 1 else reads[0]

    monkeypatch.setattr(store, "get", get)
    return reads


def test_a_changed_receipt_discards_the_copy_but_an_unrelated_revision_keeps_it(
    env,
    root,
    monkeypatch,
):
    record, _ = saved(env, [("clip", "clip.mp4", "video/mp4", MP4)])
    reads = _changes_during_copy(monkeypatch, env.store, record)
    # A concurrent revision bump, such as a strip application, keeps a read-only copy.
    reads.append(replace(record, revision=record.revision + 1))
    copy = copy_of(env, record, "clip", root)
    assert copy.path.read_bytes() == MP4
    receipt = replace(record.results[0].receipt, sha256="0" * 64)
    reads[:] = [record, replace(record, results=(replace(record.results[0], receipt=receipt),))]
    with pytest.raises(StoreConflict):
        copy_of(env, record, "clip", root, keep=[copy.directory])
    reads[:] = [record, replace(record, state=JobState.SUCCEEDED)]
    with pytest.raises(StoreConflict):
        copy_of(env, record, "clip", root, keep=[copy.directory])
    assert copies(root) == [copy.directory.name]


def test_cancellation_removes_partial_and_finished_copies(env, root, monkeypatch):
    record, _ = saved(env, [("clip", "clip.mp4", "video/mp4", MP4)])
    cancel = threading.Event()
    cancel.set()
    with pytest.raises(playback.PlaybackCanceled):
        copy_of(env, record, "clip", root, cancel=cancel)
    assert copies(root) == []
    late = threading.Event()
    original = playback.make_copy

    def copy_then_cancel(*args, **kwargs):
        result = original(*args, **kwargs)
        late.set()
        return result

    monkeypatch.setattr(playback, "make_copy", copy_then_cancel)
    with pytest.raises(PreviewCanceled):
        copy_of(env, record, "clip", root, cancel=late)
    assert copies(root) == []


def test_a_canceled_reuse_is_never_discarded(env, root):
    record, _ = saved(env, [("clip", "clip.mp4", "video/mp4", MP4)])
    first = copy_of(env, record, "clip", root)
    cancel = threading.Event()
    cancel.set()
    with pytest.raises(PreviewCanceled):
        copy_of(env, record, "clip", root, cancel=cancel, reuse=first, keep=[first.directory])
    assert first.path.read_bytes() == MP4


def test_an_inactive_owner_refuses_before_copying(env, root):
    record, _ = saved(env, [("clip", "clip.mp4", "video/mp4", MP4)])
    env.coordinator.deactivate()
    with pytest.raises(ResultError, match="inactive"):
        copy_of(env, record, "clip", root)
    assert copies(root) == []


def test_owner_discards_and_stale_sweeps_happen_on_the_lane_before_copying(env, root):
    record, _ = saved(env, [("clip", "clip.mp4", "video/mp4", MP4)])
    evicted = copy_of(env, record, "clip", root)
    kept = copy_of(env, record, "clip", root)
    stale = copy_of(env, record, "clip", root)
    old = time.time() - playback.STALE_SECONDS - 10
    for copy in (kept, stale):
        os.utime(copy.directory, (old, old))
    other = _root(root) / "unrelated-old"
    other.mkdir()
    os.utime(other, (old, old))
    latest = copy_of(env, record, "clip", root, discard=[evicted.directory], keep=[kept.directory])
    assert set(copies(root)) == {kept.directory.name, latest.directory.name, "unrelated-old"}
    # A refused request still removes the copies its owner evicted.
    with pytest.raises(StoreConflict):
        env.coordinator.prepare_playback(
            "request",
            expected_revision=record.revision + 1,
            asset_id="clip",
            root=root,
            cancel=threading.Event(),
            discard=[latest.directory],
            keep=[kept.directory],
        )
    assert set(copies(root)) == {kept.directory.name, "unrelated-old"}


def test_discard_and_sweep_touch_only_play_directories_below_the_root(tmp_path):
    root = tmp_path / "media-playback"
    root.mkdir()
    outside = tmp_path / "play-outside"
    outside.mkdir()
    named = root / "keep-me"
    named.mkdir()
    playback.discard(root, outside)
    playback.discard(root, named)
    playback.discard(tmp_path / "absent", root / "play-x")
    assert outside.is_dir() and named.is_dir()
    target = tmp_path / "target"
    target.mkdir()
    (target / "data").write_text("fixture")
    link = root / "play-link"
    try:
        link.symlink_to(target, target_is_directory=True)
    except OSError:
        link = None
    if link is not None:
        os.utime(link, (0, 0), follow_symlinks=False)
        playback.sweep(root, now=time.time())
        playback.discard(root, link)
        assert (target / "data").read_text() == "fixture"
    playback.sweep(tmp_path / "absent")


def test_discard_removes_a_read_only_copy(tmp_path):
    root = tmp_path / "media-playback"
    directory = root / "play-fixture"
    directory.mkdir(parents=True)
    copy = directory / "media.mp4"
    copy.write_bytes(MP4)
    os.chmod(copy, 0o400)
    nested = directory / "nested"
    nested.mkdir()
    playback.discard(root, directory)
    assert not directory.exists()


def test_playback_copies_run_on_the_preview_lane(env, root, monkeypatch):
    record, _ = saved(env, [("clip", "clip.mp4", "video/mp4", MP4)])
    threads, original = [], env.coordinator.prepare_playback

    def recorded(*args, **kwargs):
        threads.append(threading.current_thread().name)
        return original(*args, **kwargs)

    monkeypatch.setattr(env.coordinator, "prepare_playback", recorded)
    workers = JobWorkers(env.coordinator, workers=1)
    try:
        task = workers.prepare_playback(
            "request", expected_revision=record.revision, asset_id="clip", root=root
        )
        copy = task.result(timeout=5)
    finally:
        workers.deactivate()
        workers.shutdown()
    assert threads == ["ScenarioPreview"] and copy.path.read_bytes() == MP4
    assert_offline(env)


def test_kind_and_types_follow_the_film_media_allowlist():
    assert playback.kind("video/mp4") == "video" and playback.kind("audio/aac") == "audio"
    for value in ("image/png", "model/gltf-binary", "video/x-flv", None, 3):
        assert playback.kind(value) is None
    assert set(playback.PLAYBACK_TYPES) == set(playback.FORMATS)
    assert {suffix for _, suffix in playback.PLAYBACK_TYPES.values()} <= set(playback._CHECKS)


def test_players_get_no_credentials_python_start_up_or_proxies():
    environ = {
        "PATH": "/bin",
        "HOME": "/home/fixture",
        "BLENDER_USER_RESOURCES": "/profile",
        "SCENARIO_API_KEY": "secret",
        "OPEN_STUDIO_TOKEN": "assistant-fixture",
        "scenario_api_secret": "secret",
        "PYTHONPATH": "/inject",
        "PYTHONSTARTUP": "/inject.py",
        "HTTPS_PROXY": "http://proxy.invalid",
        "no_proxy": "*",
    }
    assert playback.player_environment(environ) == {
        "PATH": "/bin",
        "HOME": "/home/fixture",
        "BLENDER_USER_RESOURCES": "/profile",
    }
    assert "SCENARIO_API_KEY" not in playback.player_environment(
        {**os.environ, "SCENARIO_API_KEY": "secret"}
    )


SECRETS = {
    "SCENARIO_API_KEY": "launch-key",
    "SCENARIO_API_SECRET": "fixture",
    "Scenario_Project": "project",
    "PYTHONPATH": "/inject",
    "PYTHONHOME": "/home",
    "HTTP_PROXY": "http://proxy.invalid",
}


class Launches:
    """Records launcher processes instead of starting them."""

    def __init__(self):
        self.calls = []

    def __call__(self, command, **options):
        self.calls.append((command, options))
        return "launcher"


@pytest.mark.parametrize(
    ("platform", "launcher"),
    [("darwin", ["/usr/bin/open"]), ("linux", ["xdg-open"]), ("freebsd14", ["xdg-open"])],
)
def test_the_system_player_launcher_gets_no_credentials(tmp_path, platform, launcher):
    path = tmp_path / "play-fixture" / "media.mp4"
    environ = {"PATH": "/usr/bin", "DISPLAY": ":0", "BLENDER_USER_CONFIG": "/config", **SECRETS}
    launches = Launches()
    assert playback.system_open(path, platform=platform, environ=environ, popen=launches) == (
        "launcher"
    )
    ((command, options),) = launches.calls
    assert command == [*launcher, str(path)]
    assert options == {
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
        "env": {"PATH": "/usr/bin", "DISPLAY": ":0", "BLENDER_USER_CONFIG": "/config"},
    }


def test_windows_opens_through_an_isolated_interpreter_with_a_filtered_environment(tmp_path):
    path = tmp_path / "media.wav"
    environ = {"SYSTEMROOT": "C:\\Windows", "PATH": "C:\\Windows", **SECRETS}
    launches = Launches()
    playback.system_open(
        path, platform="win32", environ=environ, executable="python.exe", popen=launches
    )
    ((command, options),) = launches.calls
    # Blender's interpreter, isolated from start-up variables and site packages,
    # calls ShellExecute; the started application inherits this environment.
    assert command == ["python.exe", "-I", "-S", "-c", playback._STARTFILE, str(path)]
    assert options["env"] == {"SYSTEMROOT": "C:\\Windows", "PATH": "C:\\Windows"}
    assert options["creationflags"] == getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
    with pytest.raises(playback.PlaybackError, match="interpreter"):
        playback.system_open(path, platform="win32", environ=environ, executable="", popen=launches)
    assert len(launches.calls) == 1


def test_the_system_player_refuses_relative_paths_before_launching():
    launches = Launches()
    for path in ("media.mp4", "-a", ""):
        with pytest.raises(playback.PlaybackError, match="absolute"):
            playback.system_open(path, platform="linux", environ={}, popen=launches)
    assert launches.calls == []


def test_a_folder_opens_through_the_same_filtered_launcher(tmp_path):
    environ = {"PATH": "/usr/bin", **SECRETS}
    for platform, launcher in (("darwin", "/usr/bin/open"), ("linux", "xdg-open")):
        launches = Launches()
        playback.system_open(tmp_path, platform=platform, environ=environ, popen=launches)
        ((command, options),) = launches.calls
        assert command == [launcher, str(tmp_path)]
        assert options["env"] == {"PATH": "/usr/bin"}


URL = "https://app.scenario.com/team"


@pytest.mark.parametrize(
    ("platform", "launcher"),
    [("darwin", ["/usr/bin/open"]), ("linux", ["xdg-open"]), ("freebsd14", ["xdg-open"])],
)
def test_web_links_open_without_credentials_python_start_up_or_proxies(platform, launcher):
    environ = {"PATH": "/usr/bin", "DISPLAY": ":0", "BROWSER": "firefox", **SECRETS}
    launches = Launches()
    assert playback.system_open_url(URL, platform=platform, environ=environ, popen=launches) == (
        "launcher"
    )
    ((command, options),) = launches.calls
    assert command == [*launcher, URL]
    assert options == {
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
        "env": {"PATH": "/usr/bin", "DISPLAY": ":0", "BROWSER": "firefox"},
    }


def test_windows_opens_web_links_through_the_isolated_interpreter():
    environ = {"SYSTEMROOT": "C:\\Windows", **SECRETS}
    launches = Launches()
    link = "https://blender.scenario.com/#updates"
    playback.system_open_url(
        link, platform="win32", environ=environ, executable="python.exe", popen=launches
    )
    ((command, options),) = launches.calls
    assert command == ["python.exe", "-I", "-S", "-c", playback._STARTFILE, link]
    assert options["env"] == {"SYSTEMROOT": "C:\\Windows"}
    assert options["creationflags"] == getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
    with pytest.raises(playback.PlaybackError, match="interpreter"):
        playback.system_open_url(
            link, platform="win32", environ=environ, executable="", popen=launches
        )
    assert len(launches.calls) == 1


@pytest.mark.parametrize(
    "url",
    [
        "http://app.scenario.com/team",
        "file:///etc/passwd",
        "javascript:alert(1)",
        "-a",
        "",
        "https://",
        "https:///path",
        "https://app.scenario.com/a b",
        "https://app.scenario.com/\n",
        "https://[broken/",
        "app.scenario.com/team",
        None,
        b"https://app.scenario.com/team",
    ],
)
def test_only_https_links_with_a_host_start_a_browser(url):
    launches = Launches()
    for platform in ("darwin", "linux", "win32"):
        with pytest.raises(playback.PlaybackError, match="https"):
            playback.system_open_url(
                url, platform=platform, environ={}, executable="python.exe", popen=launches
            )
    assert launches.calls == []


@pytest.mark.skipif(os.name == "nt", reason="a POSIX shell script stands in for xdg-open")
@pytest.mark.parametrize("kind", ["file", "folder", "link"])
def test_the_launched_application_inherits_no_credentials(tmp_path, kind):
    """A real child process: a stand-in xdg-open records its environment and argument."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    record = tmp_path / "environment.txt"
    target = tmp_path / "argument.txt"
    opener = bin_dir / "xdg-open"
    opener.write_text(f'#!/bin/sh\nenv > "{record}"\nprintf %s "$1" > "{target}"\n')
    opener.chmod(0o700)
    environ = {"PATH": f"{bin_dir}{os.pathsep}/usr/bin{os.pathsep}/bin", **SECRETS}
    if kind == "link":
        expected = URL
        process = playback.system_open_url(URL, platform="linux", environ=environ)
    else:
        expected = str(tmp_path / "media.mp4") if kind == "file" else str(tmp_path)
        process = playback.system_open(expected, platform="linux", environ=environ)
    assert process.wait(10) == 0
    assert target.read_text() == expected
    names = {line.split("=", 1)[0] for line in record.read_text().splitlines() if "=" in line}
    assert "PATH" in names
    assert not {name for name in names if name.upper().startswith(("SCENARIO_", "PYTHON"))}
    assert not {name for name in names if name.upper() in {"HTTP_PROXY", "HTTPS_PROXY"}}


def test_playback_root_requires_an_existing_parent(tmp_path):
    with pytest.raises(playback.PlaybackError, match="unavailable"):
        playback.playback_root(tmp_path / "missing" / "media-playback")
    (tmp_path / "file").write_text("fixture")
    with pytest.raises(playback.PlaybackError, match="unavailable"):
        playback.playback_root(tmp_path / "file")
    assert playback.playback_root(tmp_path / "media-playback") == _root(
        Path(tmp_path / "media-playback")
    )
