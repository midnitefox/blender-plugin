# SPDX-FileCopyrightText: 2026 Open Studio contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Complete assistant recipes must fail closed without partial recovery."""

from copy import deepcopy

import pytest

from scenario.core.scene.assistant_blockout import validate_plan


def plan():
    return {
        "name": "Courtyard",
        "elements": [
            {
                "id": "floor",
                "name": "Ground",
                "primitive": "plane",
                "category": "floor",
                "position": [0, 0, -0.05],
                "size": [12, 10, 0.1],
            }
        ],
    }


def test_normalize_explicit_complete_plan_without_mutating_input():
    source = plan()
    before = deepcopy(source)
    result = validate_plan(source)
    assert source == before
    assert result["elements"][0]["rotation_degrees"] == [0, 0, 0]
    assert result["elements"][0]["color"] == [0.62, 0.62, 0.64]


@pytest.mark.parametrize(
    "key,value",
    [
        ("position", [0, 0, float("nan")]),
        ("size", [1, 0, 1]),
        ("size", [1, True, 1]),
        ("rotation_degrees", [0, 0, 361]),
        ("color", [1, 2, 0]),
        ("primitive", "script"),
        ("category", "guess"),
        ("id", "../floor"),
        ("name", "\0name"),
    ],
)
def test_reject_invalid_fields_without_clamping_or_defaults(key, value):
    source = plan()
    source["elements"][0][key] = value
    with pytest.raises(ValueError):
        validate_plan(source)


def test_reject_duplicate_ids_partial_empty_and_oversized_recipes():
    source = plan()
    for elements in ([], source["elements"] * 2, source["elements"] * 201, [{"id": "partial"}]):
        with pytest.raises(ValueError):
            validate_plan(dict(source, elements=elements))


def test_reject_unknown_fields_and_wrapped_text():
    source = plan()
    with pytest.raises(ValueError):
        validate_plan(dict(source, code="ignored"))
    source["elements"][0]["unknown"] = "ignored"
    with pytest.raises(ValueError):
        validate_plan(source)
    with pytest.raises(ValueError):
        validate_plan("```json\n[]\n```")
