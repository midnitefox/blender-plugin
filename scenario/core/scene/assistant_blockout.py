# SPDX-FileCopyrightText: 2026 Open Studio contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Strict, complete assistant blockout recipes; no Blender or provider imports."""

import json
import math
import re

from .blockout import CATEGORIES, MAX_COORD, MAX_ELEMENTS, MAX_SIZE, MIN_SIZE, PRIMITIVES


def _label(value, label):
    if (
        not isinstance(value, str)
        or not value.strip()
        or len(value) > 60
        or any(ord(c) < 32 for c in value)
    ):
        raise ValueError(f"{label} must contain 1 to 60 visible characters")
    return value.strip()


def _vector(value, label, low, high):
    if (
        not isinstance(value, (list, tuple))
        or len(value) != 3
        or any(
            type(number) not in {float, int}
            or not math.isfinite(number)
            or not low <= number <= high
            for number in value
        )
    ):
        raise ValueError(f"{label} needs three finite numbers between {low} and {high}")
    return list(value)


def validate_plan(plan):
    """Reject partial, ambiguous or out-of-bounds plans instead of repairing them."""
    if not isinstance(plan, dict) or set(plan) != {"name", "elements"}:
        raise ValueError("A complete blockout plan needs name and elements")
    name = _label(plan["name"], "Plan name")
    if not isinstance(plan["elements"], list) or not 1 <= len(plan["elements"]) <= MAX_ELEMENTS:
        raise ValueError(f"A blockout needs 1 to {MAX_ELEMENTS} complete elements")
    elements, ids = [], set()
    required = {"id", "name", "primitive", "category", "position", "size"}
    for element in plan["elements"]:
        if (
            not isinstance(element, dict)
            or not required <= set(element)
            or set(element) - required - {"rotation_degrees", "group", "color"}
        ):
            raise ValueError(
                "Each blockout element needs id, name, primitive, category, position and size"
            )
        identifier = element["id"]
        if (
            not isinstance(identifier, str)
            or not re.fullmatch(r"[A-Za-z0-9_-]{1,60}", identifier)
            or identifier in ids
        ):
            raise ValueError(
                "Each blockout element needs a unique 1 to 60 character letter/digit/_/- id"
            )
        ids.add(identifier)
        primitive, category = element["primitive"], element["category"]
        if (
            not isinstance(primitive, str)
            or not isinstance(category, str)
            or primitive not in PRIMITIVES
            or category not in CATEGORIES
        ):
            raise ValueError("Choose a supported primitive and explicit category")
        elements.append(
            {
                "id": identifier,
                "name": _label(element["name"], "Element name"),
                "primitive": primitive,
                "category": category,
                "position": _vector(element["position"], "Position", -MAX_COORD, MAX_COORD),
                "size": _vector(element["size"], "Size", MIN_SIZE, MAX_SIZE),
                "rotation_degrees": _vector(
                    element.get("rotation_degrees", [0, 0, 0]), "Rotation", -360, 360
                ),
                "group": _label(element.get("group", CATEGORIES[category][0]), "Group"),
                "color": _vector(
                    element.get("color", CATEGORIES[category][1]), "Linear RGB color", 0, 1
                ),
            }
        )
    return {"name": name, "elements": elements}


def serialize(plan):
    return json.dumps(plan, sort_keys=True, separators=(",", ":"), allow_nan=False)
