# SPDX-FileCopyrightText: 2026 Scenario Inc.
# SPDX-FileCopyrightText: 2026 Open Studio contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Portable local projects using the retained offline copy/rewrite/verify primitives."""

import hashlib
import json
import tempfile
import threading
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

from . import local_case_study as bundle
from .local_export import LocalExportError, check_unused, portable_name
from .local_render import RenderCancelled, _environment, blender_path, digest

DOCUMENTS = ("Project Recipe.json", "Project Manifest.json", "Open Project.md")


@dataclass(frozen=True)
class ProjectSpec:
    directory: Path
    binary: Path
    worker: Path
    snapshot_sha256: str
    scenes: tuple[str, ...]
    media: tuple[bundle.BundleMedia, ...]
    recipes: str = field(repr=False)
    top: str = "Open Studio Project"
    blend: str = "Project.blend"
    child_timeout: int = 1800

    def __post_init__(self):
        if any(
            not isinstance(path, Path) or not path.is_absolute()
            for path in (self.directory, self.binary, self.worker)
        ):
            raise ValueError("Use owned absolute project paths")
        if (
            not isinstance(self.snapshot_sha256, str)
            or len(self.snapshot_sha256) != 64
            or any(c not in "0123456789abcdef" for c in self.snapshot_sha256)
        ):
            raise ValueError("Bind the exact project snapshot")
        if (
            not isinstance(self.scenes, tuple)
            or not 1 <= len(self.scenes) <= bundle.MAX_SCENES
            or any(
                not isinstance(name, str) or not name or "\0" in name or len(name) > 1024
                for name in self.scenes
            )
            or len(set(self.scenes)) != len(self.scenes)
        ):
            raise ValueError("Choose between one and 256 distinct scenes")
        if (
            not isinstance(self.media, tuple)
            or len(self.media) > bundle.MAX_MEDIA
            or any(not isinstance(item, bundle.BundleMedia) for item in self.media)
            or self.media_bytes > bundle.MAX_MEDIA_BYTES
            or len({item.stamp.path for item in self.media}) != len(self.media)
            or len({item.name.casefold() for item in self.media}) != len(self.media)
        ):
            raise ValueError("Package at most 2,000 files and 16 GiB of media")
        if (
            not portable_name(self.top)
            or not portable_name(self.blend)
            or not self.blend.endswith(".blend")
        ):
            raise ValueError("Use portable project names")
        if (
            not isinstance(self.recipes, str)
            or len(self.recipes.encode()) > bundle.MAX_DOCUMENT_BYTES
        ):
            raise ValueError("Keep the local recipe within 16 MiB")
        if not isinstance(json.loads(self.recipes), dict):
            raise ValueError("Use a local recipe object")
        if type(self.child_timeout) is not int or not 1 <= self.child_timeout <= 21600:
            raise ValueError("Use a bounded project timeout")

    @property
    def root(self):
        return self.directory / bundle.BUNDLE

    @property
    def media_bytes(self):
        return sum(item.stamp.size for item in self.media)

    def parameters(self):
        return {
            "mode": "bundle",
            "scenes": list(self.scenes),
            "media": [
                {"path": str(item.stamp.path), "name": item.name, "kind": item.kind}
                for item in self.media
            ],
            "output": blender_path(self.root / self.blend),
            "report": blender_path(self.directory / "bundle.json"),
        }


def build(spec, *, cancel=None, progress=None):
    """Create a ZIP only after an independent Blender reopen and archive readback."""
    if not isinstance(spec, ProjectSpec):
        raise TypeError("Use a local project snapshot")
    cancel = cancel if cancel is not None else threading.Event()
    progress = progress if progress is not None else (lambda *_: None)
    directory, root = spec.directory, spec.root
    snapshot, output = root / bundle.SNAPSHOT, root / spec.blend
    for path in (directory, root):
        if path.is_symlink() or not path.is_dir():
            raise LocalExportError("The owned project staging directory is unavailable")
    for path in (snapshot, output, spec.worker, spec.binary, directory / "verified.json"):
        blender_path(path)
    for item in spec.media:
        blender_path(root / bundle.MEDIA_FOLDER / item.name)
    if not spec.binary.is_file() or not spec.worker.is_file():
        raise LocalExportError("The bundled project worker is unavailable")
    check_unused(spec)
    if digest(snapshot) != spec.snapshot_sha256:
        raise LocalExportError("The project snapshot changed")
    if cancel.is_set():
        raise RenderCancelled("Project packaging cancelled")
    parameters = directory / "started.json"
    with parameters.open("x", encoding="utf-8") as stream:
        json.dump(spec.parameters(), stream)
    receipts = bundle._copy_media(spec, root / bundle.MEDIA_FOLDER, cancel, progress)
    with tempfile.TemporaryDirectory(prefix="worker-", dir=directory) as owned:
        profile, temporary = Path(owned) / "profile", Path(owned) / "tmp"
        profile.mkdir()
        temporary.mkdir()
        environment = _environment(profile, temporary)
        progress("rewrite", 0, 1)
        bundle._blender(spec, snapshot, parameters, directory / "bundle.log", environment, cancel)
        report = bundle._read_report(directory / "bundle.json", "Project rewrite report missing")
        bundle.check_bundle(spec, report)
        if (
            output.is_symlink()
            or not output.is_file()
            or not 0 < output.stat().st_size <= bundle.MAX_BLEND_BYTES
        ):
            raise LocalExportError("Project blend file is unavailable or exceeds 4 GiB")
        verify = directory / "verify.json"
        verify.write_text(
            json.dumps(
                {
                    "mode": "verify",
                    "scenes": list(spec.scenes),
                    "media": [{"name": item.name, "kind": item.kind} for item in spec.media],
                    "report": blender_path(directory / "verified.json"),
                }
            ),
            encoding="utf-8",
        )
        progress("reopen", 0, 1)
        bundle._blender(spec, output, verify, directory / "verify.log", environment, cancel)
        bundle.check_verified(
            spec,
            bundle._read_report(directory / "verified.json", "Project verification report missing"),
        )
    blend_size = output.stat().st_size
    blend_digest = digest(output, maximum=bundle.MAX_BLEND_BYTES, cancel=cancel)
    kinds = {item.name: item.kind for item in spec.media}
    manifest = {
        "format": "open-studio-project-v1",
        "scenes": list(spec.scenes),
        "blend": {"file": spec.blend, "size": blend_size, "sha256": blend_digest},
        "media": [
            {
                "file": "Media/" + item.name,
                "kind": kinds[item.name],
                "size": item.size,
                "sha256": item.sha256,
            }
            for item in receipts
        ],
        "verification": "Offline Blender rewrite, independent reopen, relative media checks and ZIP readback",
    }
    documents = {
        DOCUMENTS[0]: spec.recipes,
        DOCUMENTS[1]: json.dumps(manifest, indent=2, ensure_ascii=False),
        DOCUMENTS[
            2
        ]: "# Open Studio project\n\nExtract the entire ZIP, then open Project.blend in Blender. "
        "Keep the Media folder beside it. The scenes, shot cameras, animation, materials and timeline remain editable. "
        "Open Studio and a generation service are not required to open the project. "
        "Project Recipe.json records the local scene recipes; the blend file contains the captured edits.\n",
    }
    members = [
        (spec.top + "/" + spec.blend, output, blend_size, blend_digest, zipfile.ZIP_DEFLATED)
    ]
    for name, text in documents.items():
        data = text.encode("utf-8")
        target = root / name
        with target.open("xb") as stream:
            stream.write(data)
        members.append(
            (
                spec.top + "/" + name,
                target,
                len(data),
                hashlib.sha256(data).hexdigest(),
                zipfile.ZIP_DEFLATED,
            )
        )
    members += [
        (
            spec.top + "/Media/" + item.name,
            root / "Media" / item.name,
            item.size,
            item.sha256,
            zipfile.ZIP_STORED,
        )
        for item in receipts
    ]
    archive = directory / "output" / "project.zip"
    archive.parent.mkdir(mode=0o700)
    bundle._archive(archive, members, cancel, progress)
    bundle.check_archive(archive, members, cancel, progress)
    size = archive.stat().st_size
    if not 0 < size <= bundle.MAX_ARCHIVE_BYTES:
        raise LocalExportError("Project archive exceeds the size limit")
    sha256 = digest(archive, maximum=bundle.MAX_ARCHIVE_BYTES, cancel=cancel)
    if cancel.is_set():
        raise RenderCancelled("Project packaging cancelled")
    return bundle.StagedBundle(
        directory, archive, sha256, size, spec.scenes, blend_digest, receipts
    )
