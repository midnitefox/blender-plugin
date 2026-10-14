# SPDX-FileCopyrightText: 2026 Open Studio contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Portable operation preflight rejects changed inputs before invoking Blender."""

import sys
import threading
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import pytest

from scenario.core.jobs import assistant_project_bundle as project
from scenario.core.jobs import local_case_study, local_export
from scenario.core.jobs.local_render import RenderCancelled, digest


@pytest.fixture
def spec(tmp_path):
    directory = tmp_path / "film-export-project-test"
    root = directory / "bundle"
    root.mkdir(parents=True)
    snapshot = root / "snapshot.blend"
    snapshot.write_bytes(b"captured editable project")
    media = tmp_path / "picture.png"
    media.write_bytes(b"captured picture")
    worker = tmp_path / "worker.py"
    worker.write_text("# owned fixture", encoding="utf-8")
    return project.ProjectSpec(
        directory,
        Path(sys.executable).resolve(),
        worker,
        digest(snapshot),
        ("Shot",),
        (local_case_study.BundleMedia(local_export.media_stamp(media), "001 image.png", "image"),),
        '{"scenes":{"Shot":{"recipe":{}}}}',
    )


def test_changed_snapshot_never_invokes_a_worker(spec):
    (spec.root / "snapshot.blend").write_bytes(b"changed snapshot")
    with patch.object(local_case_study, "_blender") as child:
        with pytest.raises(local_export.LocalExportError, match="snapshot changed"):
            project.build(spec)
        child.assert_not_called()
    assert not (spec.directory / "started.json").exists()


def test_changed_media_never_invokes_a_worker_or_builds_an_archive(spec):
    spec.media[0].stamp.path.write_bytes(b"changed after snapshot")
    with patch.object(local_case_study, "_blender") as child:
        with pytest.raises(local_export.LocalExportError, match="changed after"):
            project.build(spec)
        child.assert_not_called()
    assert not (spec.directory / "output").exists()


def test_cancelled_project_does_not_copy_or_start_a_worker(spec):
    event = threading.Event()
    event.set()
    with patch.object(local_case_study, "_blender") as child:
        with pytest.raises(RenderCancelled):
            project.build(spec, cancel=event)
        child.assert_not_called()
    assert not (spec.root / "Media").exists()


def test_duplicate_source_aliases_and_oversized_budgets_are_rejected(spec):
    alias = replace(spec.media[0], name="002 image.png")
    with pytest.raises(ValueError, match="2,000"):
        replace(spec, media=(*spec.media, alias))
    huge = replace(spec.media[0], stamp=replace(spec.media[0].stamp, size=16 * 1024**3 + 1))
    with pytest.raises(ValueError, match="16 GiB"):
        replace(spec, media=(huge,))
