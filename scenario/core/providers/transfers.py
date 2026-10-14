# SPDX-FileCopyrightText: 2026 Open Studio contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Publish scoped provider results with bounded, verified, no-overwrite receipts."""

import hashlib
import os
import re
import tempfile
from pathlib import Path

from ..jobs.transfers import (
    DownloadedResult,
    ResultDownloader,
    TransferError,
    _root,
    validate_result_name,
    verify_download,
)
from .base import MAX_RESULT_BYTES, ProviderAdapter


class ProviderResultDownloader(ResultDownloader):
    """Use an adapter-owned opaque reference; no URL, credential or host guessing.

    Saved provider bytes remain available offline. If an adapter needs network
    access it must enforce its own shared permission before reading; the built-in
    adapters finish saving their provider bytes before publishing asset metadata.
    """

    def __init__(self, adapter, *, online_access):
        if not isinstance(adapter, ProviderAdapter) or not callable(online_access):
            raise TypeError("A selected provider adapter and permission predicate are required")
        self._adapter = adapter
        self._online_access = online_access

    def verify(self, root, receipt):
        return verify_download(root, receipt, max_bytes=MAX_RESULT_BYTES)

    def download(
        self,
        url,
        *,
        root,
        name,
        expected_size=None,
        expected_sha256=None,
        max_bytes=None,
        allow_size_mismatch=False,
        cancel=None,
    ):
        if not isinstance(url, str) or not re.fullmatch(
            r"provider-result:[A-Za-z0-9_-]{1,128}", url
        ):
            raise TransferError("Use a saved result owned by the selected provider")
        if type(allow_size_mismatch) is not bool:
            raise TransferError("Choose an explicit result size policy")
        if cancel is not None and not callable(getattr(cancel, "is_set", None)):
            raise TransferError("Use a cancellation event")
        if max_bytes is not None and (type(max_bytes) is not int or max_bytes < 1):
            raise TransferError("Invalid result byte limit")
        limit = min(MAX_RESULT_BYTES, max_bytes) if max_bytes is not None else MAX_RESULT_BYTES
        if expected_size is not None and (
            type(expected_size) is not int or not 0 <= expected_size <= limit
        ):
            raise TransferError("Invalid expected result size")
        if expected_sha256 is not None and (
            not isinstance(expected_sha256, str)
            or not re.fullmatch(r"[a-fA-F0-9]{64}", expected_sha256)
        ):
            raise TransferError("Invalid expected result digest")

        def check_cancel():
            if cancel is not None and cancel.is_set():
                raise TransferError("Provider result transfer canceled")

        temporary = None
        try:
            root = _root(root)
            destination = root / validate_result_name(name)
            if destination.exists() or destination.is_symlink():
                raise TransferError("Result filename already exists")
            check_cancel()
            data = self._adapter.read_result(url, max_bytes=limit, cancel=cancel)
            check_cancel()
            if not isinstance(data, bytes) or len(data) > limit:
                raise TransferError("Provider result exceeds its byte limit")
            if expected_size is not None and not allow_size_mismatch and len(data) != expected_size:
                raise TransferError("Provider result size does not match")
            digest = hashlib.sha256(data).hexdigest()
            if expected_sha256 is not None and digest != expected_sha256.lower():
                raise TransferError("Provider result digest does not match")
            descriptor, temporary = tempfile.mkstemp(prefix=".open-studio-transfer-", dir=root)
            with os.fdopen(descriptor, "wb") as output:
                for offset in range(0, len(data), 65536):
                    check_cancel()
                    output.write(data[offset : offset + 65536])
                output.flush()
                os.fsync(output.fileno())
            check_cancel()
            receipt = DownloadedResult(name, len(data), digest)
            # Same-filesystem hard-link publication is atomic and refuses an
            # existing destination, even if it appeared after our initial read.
            os.link(temporary, destination)
            return receipt
        except TransferError:
            raise
        except Exception:
            raise TransferError("Provider result transfer failed") from None
        finally:
            if temporary is not None:
                try:
                    Path(temporary).unlink(missing_ok=True)
                except OSError:
                    # A published verified file remains valid if cleanup fails.
                    pass
