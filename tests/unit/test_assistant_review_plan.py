# SPDX-FileCopyrightText: 2026 Open Studio contributors
# SPDX-License-Identifier: GPL-3.0-or-later

from copy import deepcopy

import pytest

from scenario.core.scene.assistant_review_plan import canonical, segments, validate_plan


def item(**kwargs):
    return dict(
        filename="clip.mp4",
        sha256="a" * 64,
        kind="VIDEO",
        start_frame=1,
        frame_count=60,
        channel=1,
        **kwargs,
    )


def test_canonical_recipe_is_deterministic_and_leaves_input_unchanged():
    source = dict(name="Review", fps=30, pictures=[item()])
    before = deepcopy(source)
    plan = validate_plan(source)
    assert source == before
    assert plan["pictures"][0]["source_trim_seconds"] == 0
    assert canonical(plan) == canonical(validate_plan(plan))


@pytest.mark.parametrize(
    "key,value",
    [
        ("filename", "../source.mp4"),
        ("sha256", "x" * 64),
        ("start_frame", True),
        ("frame_count", 0),
        ("channel", 129),
        ("source_trim_seconds", float("nan")),
        ("source_trim_seconds", 0.01),
        ("loop", 1),
        ("volume", -1),
        ("unknown", True),
    ],
)
def test_bad_item_refuses_without_coercion(key, value):
    source = item()
    source[key] = value
    with pytest.raises(ValueError):
        validate_plan(dict(name="Review", pictures=[source]))


def test_image_rules_and_explicit_embedded_audio_channel():
    for change in (
        dict(kind="IMAGE", loop=True),
        dict(kind="IMAGE", source_trim_seconds=1),
        dict(include_audio=True),
        dict(include_audio=True, audio_channel=1),
        dict(audio_channel=2),
    ):
        source = item()
        source.update(change)
        with pytest.raises(ValueError):
            validate_plan(dict(name="Review", pictures=[source]))


def test_collision_includes_embedded_audio_but_allows_adjacent_cuts():
    first = item(include_audio=True, audio_channel=2)
    second = item()
    second.update(start_frame=61)
    validate_plan(dict(name="Review", pictures=[first, second]))
    second.update(start_frame=60)
    with pytest.raises(ValueError, match="overlap"):
        validate_plan(dict(name="Review", pictures=[first, second]))
    audio = item()
    audio.pop("kind")
    audio["channel"] = 2
    with pytest.raises(ValueError, match="overlap"):
        validate_plan(dict(name="Review", pictures=[first], audio=[audio]))


def test_trimmed_loop_segments_and_insufficient_source():
    source = validate_plan(
        dict(name="Review", fps=30, pictures=[item(source_trim_seconds=1, loop=True)])
    )["pictures"][0]
    assert segments(source, 48, 30) == [
        dict(start_frame=1, frame_count=18, trim_frames=30),
        dict(start_frame=19, frame_count=18, trim_frames=30),
        dict(start_frame=37, frame_count=18, trim_frames=30),
        dict(start_frame=55, frame_count=6, trim_frames=30),
    ]
    source["loop"] = False
    with pytest.raises(ValueError, match="cover"):
        segments(source, 48, 30)
    source["loop"] = True
    with pytest.raises(ValueError, match="cover"):
        segments(source, 30, 30)


@pytest.mark.parametrize(
    "change",
    [
        dict(fps=True),
        dict(fps=121),
        dict(width=65),
        dict(height=3000),
        dict(pictures=[]),
        dict(name=""),
        dict(extra=True),
    ],
)
def test_bounded_complete_recipes(change):
    source = dict(name="Review", pictures=[item()])
    source.update(change)
    with pytest.raises(ValueError):
        validate_plan(source)
