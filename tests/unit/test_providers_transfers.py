# SPDX-FileCopyrightText: 2026 Open Studio contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Scoped local provider assets use the same verified publication boundary."""

import hashlib
from threading import Event

import pytest

from scenario.core.jobs.transfers import ResultDownloader, TransferError
from scenario.core.providers.base import ProviderAdapter
from scenario.core.providers.config import ProviderProfile
from scenario.core.providers.transfers import ProviderResultDownloader


@pytest.fixture
def setup(tmp_path):
    profile = ProviderProfile("local", "Local", "comfyui", "http://127.0.0.1:8188")
    adapter = ProviderAdapter(profile, online=False, state_dir=tmp_path / "state")
    asset = adapter.save_result("asset", b"verified bytes", "image/png")
    root = tmp_path / "results"
    root.mkdir()
    downloader = ProviderResultDownloader(adapter, online_access=lambda: False)
    yield adapter, downloader, asset["url"], root
    adapter.close()


def test_provider_transfer_is_atomic_verified_and_offline(setup):
    _, downloader, reference, root = setup
    assert isinstance(downloader, ResultDownloader)
    receipt = downloader.download(
        reference,
        root=root,
        name="result.png",
        expected_size=14,
        expected_sha256=hashlib.sha256(b"verified bytes").hexdigest(),
    )
    assert downloader.verify(root, receipt).read_bytes() == b"verified bytes"
    assert list(root.iterdir()) == [root / "result.png"]


@pytest.mark.parametrize(
    "options",
    [
        {"expected_size": 3},
        {"expected_sha256": "a" * 64},
        {"max_bytes": 2},
        {"expected_size": True},
        {"expected_sha256": "bad"},
        {"max_bytes": -1},
        {"allow_size_mismatch": "yes"},
    ],
)
def test_bad_expectations_leave_no_result(setup, options):
    _, downloader, reference, root = setup
    with pytest.raises(TransferError):
        downloader.download(reference, root=root, name="result.png", **options)
    assert list(root.iterdir()) == []


def test_existing_file_is_never_overwritten(setup):
    _, downloader, reference, root = setup
    (root / "result.png").write_bytes(b"previous result")
    with pytest.raises(TransferError):
        downloader.download(reference, root=root, name="result.png")
    assert (root / "result.png").read_bytes() == b"previous result"


def test_cancellation_and_external_urls_are_rejected(setup):
    _, downloader, reference, root = setup
    event = Event()
    event.set()
    with pytest.raises(TransferError):
        downloader.download(reference, root=root, name="result.png", cancel=event)
    with pytest.raises(TransferError):
        downloader.download("https://outside.invalid/result.png", root=root, name="result.png")
    assert list(root.iterdir()) == []


def test_changed_saved_bytes_are_refused(setup):
    adapter, downloader, reference, root = setup
    adapter._path("result", "asset", ".bin").write_bytes(b"tampered")
    with pytest.raises(TransferError):
        downloader.download(reference, root=root, name="result.png")
    assert list(root.iterdir()) == []
