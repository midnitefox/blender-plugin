# SPDX-FileCopyrightText: 2026 Open Studio contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""The assistant handoff reads only bounded image files in its explicit inbox."""

import hashlib
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from scenario.core.agent_artifacts import _verify_file, read_inbox_image

PNG = b"\x89PNG\r\n\x1a\n" + b"synthetic-image"


def test_verified_image_snapshot(tmp_path):
    (tmp_path / "concept.png").write_bytes(PNG)
    image = read_inbox_image(tmp_path, "concept.png", hashlib.sha256(PNG).hexdigest())
    assert image.data == PNG
    assert image.suffix == ".png"
    assert image.sha256 == hashlib.sha256(PNG).hexdigest()


@pytest.mark.parametrize(
    "name", ["../outside.png", "sub/image.png", "C:\\image.png", "CON.png", "a.txt"]
)
def test_invalid_names_refused(tmp_path, name):
    with pytest.raises(ValueError):
        read_inbox_image(tmp_path, name)


def test_hash_and_format_checked_before_use(tmp_path):
    (tmp_path / "concept.png").write_bytes(PNG)
    with pytest.raises(ValueError, match="changed"):
        read_inbox_image(tmp_path, "concept.png", "0" * 64)
    (tmp_path / "secret.png").write_bytes(b"not an image")
    with pytest.raises(ValueError, match="supported image"):
        read_inbox_image(tmp_path, "secret.png")


def test_size_limit(tmp_path):
    (tmp_path / "concept.png").write_bytes(PNG)
    with pytest.raises(ValueError, match="size"):
        read_inbox_image(tmp_path, "concept.png", max_bytes=8)


def test_symlink_cannot_reach_outside_inbox(tmp_path):
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    outside = tmp_path / "outside.png"
    outside.write_bytes(PNG)
    try:
        (inbox / "link.png").symlink_to(outside)
    except OSError:
        pytest.skip("Symlinks unavailable for this user")
    with pytest.raises(ValueError):
        read_inbox_image(inbox, "link.png")


@pytest.mark.parametrize("replacement", ["symlink", "regular"])
def test_file_replaced_before_open_is_rejected(tmp_path, monkeypatch, replacement):
    inbox = tmp_path / "inbox"
    inbox.mkdir()
    chosen = inbox / "chosen.png"
    chosen.write_bytes(PNG)
    outside = tmp_path / "outside.png"
    outside.write_bytes(PNG + b"outside")
    original_open = os.open
    triggered = []

    def replace_then_open(path, flags, *args, **kwargs):
        if Path(path).name == "chosen.png":
            chosen.unlink()
            if replacement == "symlink":
                try:
                    chosen.symlink_to(outside)
                except OSError:
                    pytest.skip("Symlinks unavailable for this user")
            else:
                outside.replace(chosen)
            triggered.append(True)
        return original_open(path, flags, *args, **kwargs)

    # Windows has no O_NOFOLLOW: identity must also be verified on that platform.
    monkeypatch.setattr(os, "O_NOFOLLOW", 0, raising=False)
    monkeypatch.setattr(os, "open", replace_then_open)
    with pytest.raises(ValueError, match="changed|regular"):
        read_inbox_image(inbox, "chosen.png")
    assert triggered == [True]


def test_same_size_rewrite_during_read_is_rejected(tmp_path, monkeypatch):
    chosen = tmp_path / "chosen.png"
    chosen.write_bytes(PNG)
    original_fdopen = os.fdopen

    class RewritingReader:
        def __init__(self, source):
            self.source = source

        def __enter__(self):
            self.source.__enter__()
            return self

        def __exit__(self, *args):
            return self.source.__exit__(*args)

        def fileno(self):
            return self.source.fileno()

        def read(self, size):
            result = self.source.read(size)
            before = chosen.stat()
            chosen.write_bytes(PNG[:-1] + b"x")
            os.utime(chosen, ns=(before.st_atime_ns, before.st_mtime_ns + 1000000000))
            return result

    monkeypatch.setattr(os, "fdopen", lambda *a, **kw: RewritingReader(original_fdopen(*a, **kw)))
    with pytest.raises(ValueError, match="changed"):
        read_inbox_image(tmp_path, "chosen.png")


@pytest.mark.skipif(os.name != "nt", reason="Windows Python 3.13 file timestamp semantics")
def test_windows_open_change_time_does_not_replace_stable_file_identity(tmp_path):
    chosen = tmp_path / "chosen.png"
    chosen.write_bytes(PNG)
    observed = chosen.lstat()
    fields = {
        name: getattr(observed, name)
        for name in (
            "st_mode",
            "st_nlink",
            "st_dev",
            "st_ino",
            "st_size",
            "st_mtime_ns",
            "st_ctime_ns",
        )
    }
    original = SimpleNamespace(**fields, st_birthtime_ns=1234)
    opened = SimpleNamespace(
        **(fields | {"st_ctime_ns": observed.st_ctime_ns + 1000000}), st_birthtime_ns=1234
    )
    _verify_file(opened, original)
    replaced = SimpleNamespace(**(fields | {"st_ino": observed.st_ino + 1}), st_birthtime_ns=1234)
    with pytest.raises(ValueError, match="changed"):
        _verify_file(replaced, original)
