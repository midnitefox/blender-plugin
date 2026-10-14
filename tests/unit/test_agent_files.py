# SPDX-FileCopyrightText: 2026 Open Studio contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Shared assistant file snapshots preserve bounded, explicit inbox access."""

import hashlib

import pytest

from scenario.core.agent_artifacts import MAX_ASSET_BYTES, read_inbox_file


def test_binary_snapshot_matches_expected_bytes_and_digest(tmp_path):
    data = b"glTF\x00\xff\x00fixture"
    (tmp_path / "model.glb").write_bytes(data)
    digest = hashlib.sha256(data).hexdigest()
    result = read_inbox_file(tmp_path, "model.glb", digest, suffixes={".glb"}, max_bytes=1024)
    assert (result.name, result.suffix, result.sha256, result.data) == (
        "model.glb",
        ".glb",
        digest,
        data,
    )


@pytest.mark.parametrize("name", ["../model.glb", "C:\\model.glb", "model.blend", "CON.glb"])
def test_tools_cannot_read_outside_their_explicit_inbox_contract(tmp_path, name):
    with pytest.raises(ValueError):
        read_inbox_file(tmp_path, name, suffixes={".glb"}, max_bytes=1024)


@pytest.mark.parametrize("limit", [0, True, MAX_ASSET_BYTES + 1])
def test_consumer_cannot_disable_asset_size_bound(tmp_path, limit):
    (tmp_path / "clip.wav").write_bytes(b"RIFFfixture")
    with pytest.raises(ValueError, match="size"):
        read_inbox_file(tmp_path, "clip.wav", suffixes={".wav"}, max_bytes=limit)


def test_wrong_digest_and_oversized_input_are_refused(tmp_path):
    (tmp_path / "clip.wav").write_bytes(b"RIFFfixture")
    with pytest.raises(ValueError, match="changed"):
        read_inbox_file(tmp_path, "clip.wav", "0" * 64, suffixes={".wav"}, max_bytes=1024)
    with pytest.raises(ValueError, match="size"):
        read_inbox_file(tmp_path, "clip.wav", suffixes={".wav"}, max_bytes=4)
