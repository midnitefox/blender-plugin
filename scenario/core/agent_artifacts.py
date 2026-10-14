# SPDX-FileCopyrightText: 2026 Open Studio contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Bounded local image handoff from an assistant into the extension's own inbox."""

import hashlib
import os
import re
import stat
from dataclasses import dataclass, field
from pathlib import Path

from .jobs.transfers import TransferError, _root, validate_result_name

MAX_IMAGE_BYTES = 64 * 1024 * 1024
IMAGE_SUFFIXES = frozenset({".png", ".jpg", ".jpeg", ".webp"})


@dataclass(frozen=True)
class InboxImage:
    name: str
    suffix: str
    sha256: str
    data: bytes = field(repr=False)


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
        raise ValueError("Image changed during the handoff; inspect it again")


def read_inbox_image(root, name, expected_sha256=None, *, max_bytes=MAX_IMAGE_BYTES):
    """Return a byte snapshot; the consumer decodes it before changing the scene."""
    try:
        validate_result_name(name)
    except TransferError:
        raise ValueError("Use an image basename inside the assistant inbox") from None
    suffix = Path(name).suffix.lower()
    if suffix not in IMAGE_SUFFIXES:
        raise ValueError("Use a PNG, JPEG or WebP image in the assistant inbox")
    if expected_sha256 is not None and (
        not isinstance(expected_sha256, str) or not re.fullmatch(r"[a-f0-9]{64}", expected_sha256)
    ):
        raise ValueError("Use the image's SHA-256 digest")
    if type(max_bytes) is not int or not 0 < max_bytes <= MAX_IMAGE_BYTES:
        raise ValueError("Use a bounded image size")
    root = _root(root)
    path = root / name
    try:
        original = path.lstat()
        if not _regular_file(original) or path.resolve().parent != root.resolve():
            raise ValueError("Image must be a regular file inside the assistant inbox")
        flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_BINARY", 0)
        with os.fdopen(os.open(path, flags), "rb") as source:
            info = os.fstat(source.fileno())
            _verify_file(info, original)
            if not 0 < info.st_size <= max_bytes:
                raise ValueError("Image exceeds the permitted size")
            data = source.read(max_bytes + 1)
            _verify_file(os.fstat(source.fileno()), original)
            _verify_file(path.lstat(), original)
        if len(data) != info.st_size or len(data) > max_bytes:
            raise ValueError("Image size changed during the handoff")
    except OSError:
        raise ValueError("Place the generated image in the assistant inbox first") from None
    supported = (
        (suffix == ".png" and data.startswith(b"\x89PNG\r\n\x1a\n"))
        or (suffix in {".jpg", ".jpeg"} and data.startswith(b"\xff\xd8\xff"))
        or (suffix == ".webp" and data[:4] == b"RIFF" and data[8:12] == b"WEBP")
    )
    if not supported:
        raise ValueError("File is not a supported image matching its extension")
    digest = hashlib.sha256(data).hexdigest()
    if expected_sha256 is not None and digest != expected_sha256:
        raise ValueError("Image changed since it was selected; inspect it again")
    return InboxImage(name, suffix, digest, data)
