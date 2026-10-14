# SPDX-FileCopyrightText: 2026 Open Studio contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Strict local scene recipes and animation clip filtering before native writes."""

import json
import struct
from copy import deepcopy
from pathlib import Path

import pytest

from scenario.core.scene.assistant_film_plan import (
    select_glb_clip,
    validate_shot,
    validate_timeline,
)
from scenario.core.scene.glb import inspect_glb


def shot():
    return {
        "name": "Walk in",
        "fps": 24,
        "width": 640,
        "height": 360,
        "frames": 48,
        "primitives": [
            {
                "id": "floor",
                "name": "Ground",
                "primitive": "plane",
                "category": "floor",
                "position": [0, 0, -0.1],
                "size": [10, 10, 0.2],
            }
        ],
        "camera": {
            "lens_mm": 50,
            "path": [{"frame": 1, "position": [4, -6, 3]}],
            "target_path": [{"frame": 1, "position": [0, 0, 1]}],
        },
        "actors": [],
    }


def test_normalizes_complete_recipe_without_changing_input():
    source = shot()
    before = deepcopy(source)
    result = validate_shot(source)
    assert source == before
    assert result["primitives"][0]["rotation_degrees"] == [0, 0, 0]
    assert result["primitives"][0]["color"] == [0.62, 0.62, 0.64]


@pytest.mark.parametrize(
    "key,value",
    [
        ("fps", 23.976),
        ("fps", True),
        ("width", 641),
        ("frames", 0),
        ("frames", 3000),
        ("actors", [{}]),
        ("primitives", []),
        ("unknown", "ignored"),
    ],
)
def test_incomplete_out_of_bounds_and_unknown_fields_refuse(key, value):
    with pytest.raises(ValueError):
        validate_shot(dict(shot(), **{key: value}))


def test_camera_crossing_target_and_invalid_motion_refuse():
    source = shot()
    source["camera"]["path"] = [
        {"frame": 1, "position": [-1, 0, 1]},
        {"frame": 48, "position": [1, 0, 1]},
    ]
    with pytest.raises(ValueError, match="stay apart"):
        validate_shot(source)
    for keys in (
        [{"frame": 2, "position": [0, 0, 0]}],
        [{"frame": 1, "position": [0, 0, float("nan")]}],
    ):
        source = shot()
        source["camera"]["path"] = keys
        with pytest.raises(ValueError):
            validate_shot(source)


def test_actor_identity_digest_path_and_transform_are_required():
    source = shot()
    actor = {
        "id": "actor",
        "name": "Actor",
        "position": [0, 0, 0],
        "filename": "actor.glb",
        "sha256": "a" * 64,
    }
    assert validate_shot(dict(source, actors=[actor]))["actors"][0]["scale"] == [1, 1, 1]
    for key, value in (
        ("id", "floor"),
        ("filename", "../actor.glb"),
        ("sha256", "A" * 64),
        ("scale", [1, 0, 1]),
        ("clip", {"mode": "auto"}),
    ):
        with pytest.raises(ValueError):
            validate_shot(dict(source, actors=[dict(actor, **{key: value})]))


def test_timeline_keeps_order_and_validates_explicit_ranges_and_identity():
    raw = {
        "name": "Cut",
        "fps": 24,
        "width": 640,
        "height": 360,
        "shots": [
            {"scene_name": "Shot.001", "scene_id": "a" * 32, "start_frame": 4, "end_frame": 18}
        ],
    }
    assert validate_timeline(raw) == raw
    for key, value in (("scene_id", "fake"), ("start_frame", 19), ("end_frame", 1000000)):
        with pytest.raises(ValueError):
            validate_timeline(dict(raw, shots=[dict(raw["shots"][0], **{key: value})]))


def test_clip_selection_keeps_exact_binary_and_refuses_guessing():
    data = (Path(__file__).parents[1] / "fixtures/synthetic/static-triangle.glb").read_bytes()
    length = struct.unpack_from("<I", data, 12)[0]
    document = json.loads(data[20 : 20 + length])
    document["animations"] = [
        {"name": "First", "channels": [], "samplers": []},
        {"name": "Second", "channels": [], "samplers": []},
    ]
    body = json.dumps(document).encode()
    body += b" " * (-len(body) % 4)
    binary = data[20 + length :]
    animated = (
        struct.pack("<4sII", b"glTF", 2, 20 + len(body) + len(binary))
        + struct.pack("<I4s", len(body), b"JSON")
        + body
        + binary
    )
    selected = select_glb_clip(animated, {"animation_index": 1})
    assert inspect_glb(selected, static_only=False)["animations"][0]["name"] == "Second"
    assert selected.endswith(binary)
    assert select_glb_clip(data, None) == data
    for source, clip in (
        (animated, None),
        (animated, {"animation_index": 2}),
        (data, {"animation_index": 0}),
    ):
        with pytest.raises(ValueError):
            select_glb_clip(source, clip)
