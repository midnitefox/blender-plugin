# SPDX-FileCopyrightText: 2026 Open Studio contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Bounded local asset snapshots from an assistant's explicit inbox."""

import hashlib
import os
import re
import stat
from dataclasses import dataclass, field
from pathlib import Path

from .jobs.transfers import TransferError, _root, validate_result_name

MAX_IMAGE_BYTES = 64 * 1024 * 1024
MAX_ASSET_BYTES = 512 * 1024 * 1024
IMAGE_SUFFIXES = frozenset({".png", ".jpg", ".jpeg", ".webp"})


@dataclass(frozen=True)
class InboxFile:
    name: str
    suffix: str
    sha256: str
    data: bytes = field(repr=False)


# Preserve the image snapshot type name for existing callers.
InboxImage = InboxFile


def _regular_file(info):
    # Windows reparse points include more than symlinks, and O_NOFOLLOW is absent.
    reparse = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    return (
        stat.S_ISREG(info.st_mode)
        and info.st_nlink == 1
        and not getattr(info, "st_file_attributes", 0) & reparse
    )


def _file_version(info):
    # Windows 3.13 exposes change time separately from creation time. Opening a
    # freshly copied file can update change time between lstat and fstat without
    # changing its contents. Creation time plus file ID stays stable across open.
    # Older Windows Python exposes creation time in st_ctime_ns.
    identity_time = (
        getattr(info, "st_birthtime_ns", info.st_ctime_ns) if os.name == "nt" else info.st_ctime_ns
    )
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, identity_time


def _verify_file(info, original):
    if not _regular_file(info) or _file_version(info) != _file_version(original):
        raise ValueError("File changed during the handoff; inspect it again")


def read_inbox_file(root, name, expected_sha256=None, *, suffixes, max_bytes):
    """Snapshot one allowed local file; its consumer validates format before use.

    Each tool supplies its own explicit suffix and size limits. The assistant
    cannot turn this into an arbitrary file reader by supplying an absolute path,
    traversal, a link, or a different filename while the file is being read.
    """
    try:
        validate_result_name(name)
    except TransferError:
        raise ValueError("Use a file basename inside the assistant inbox") from None
    suffix = Path(name).suffix.lower()
    if not suffixes or suffix not in suffixes:
        raise ValueError("Use a supported file extension in the assistant inbox")
    if expected_sha256 is not None and (
        not isinstance(expected_sha256, str) or not re.fullmatch(r"[a-f0-9]{64}", expected_sha256)
    ):
        raise ValueError("Use the file's SHA-256 digest")
    if type(max_bytes) is not int or not 0 < max_bytes <= MAX_ASSET_BYTES:
        raise ValueError("Use a bounded asset size")
    root = _root(root)
    path = root / name
    try:
        original = path.lstat()
        if not _regular_file(original) or path.resolve().parent != root.resolve():
            raise ValueError("File must be a regular file inside the assistant inbox")
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0)
        with os.fdopen(os.open(path, flags), "rb") as source:
            info = os.fstat(source.fileno())
            _verify_file(info, original)
            if not 0 < info.st_size <= max_bytes:
                raise ValueError("File exceeds the permitted size")
            data = source.read(max_bytes + 1)
            _verify_file(os.fstat(source.fileno()), original)
            _verify_file(path.lstat(), original)
        if len(data) != info.st_size or len(data) > max_bytes:
            raise ValueError("File size changed during the handoff")
    except OSError:
        raise ValueError("Place the generated asset in the assistant inbox first") from None
    digest = hashlib.sha256(data).hexdigest()
    if expected_sha256 is not None and digest != expected_sha256:
        raise ValueError("File changed since it was selected; inspect it again")
    return InboxFile(name, suffix, digest, data)


def read_inbox_image(root, name, expected_sha256=None, *, max_bytes=MAX_IMAGE_BYTES):
    """Return supported image bytes; Blender still decodes before scene changes."""
    if type(max_bytes) is not int or not 0 < max_bytes <= MAX_IMAGE_BYTES:
        raise ValueError("Use a bounded image size")
    snapshot = read_inbox_file(
        root, name, expected_sha256, suffixes=IMAGE_SUFFIXES, max_bytes=max_bytes
    )
    suffix, data = snapshot.suffix, snapshot.data
    supported = (
        (suffix == ".png" and data.startswith(b"\x89PNG\r\n\x1a\n"))
        or (suffix in {".jpg", ".jpeg"} and data.startswith(b"\xff\xd8\xff"))
        or (suffix == ".webp" and data[:4] == b"RIFF" and data[8:12] == b"WEBP")
    )
    if not supported:
        raise ValueError("File is not a supported image matching its extension")
    return snapshot
