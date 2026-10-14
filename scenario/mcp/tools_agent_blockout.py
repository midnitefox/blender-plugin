# SPDX-FileCopyrightText: 2026 Open Studio contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Assistant-designed structured blockout recipes and guarded local rebuilding."""

from ..blender import assistant_blockout
from ..core.scene import assistant_blockout as core
from .protocol import ToolSpec
from .tools_agent import _schema


def blockout_plan(args):
    return dict(
        assistant_blockout.inspect_plan(),
        primitives=list(core.PRIMITIVES),
        categories={
            key: {"label": label, "color": list(color)}
            for key, (label, color) in core.CATEGORIES.items()
        },
        limits={
            "elements": core.MAX_ELEMENTS,
            "max_coordinate": core.MAX_COORD,
            "min_size": core.MIN_SIZE,
            "max_size": core.MAX_SIZE,
        },
        guidance=(
            "Use your own reasoning to translate the user's scene into a complete primitive recipe. "
            "Positions are centers in Blender units, Z up; sizes are full local XYZ extents; "
            "rotation_degrees is XYZ Euler rotation. A grounded shape's center Z is half its height. "
            "A plane is a thin box: specify its thickness in size[2]. Color is linear RGB from 0 to 1. "
            "Give every element a unique stable id, explicit category, primitive, position and size. "
            "To refine, inspect this saved plan and submit the entire updated recipe with this revision. "
            "Rebuild and clear refuse user-edited, shared or externally referenced owned data. "
            "Preserve such work manually before replacing it; no force-delete option is provided."
        ),
    )


def build_agent_blockout(args):
    return assistant_blockout.build(args.get("plan"), args.get("expected_revision"))


def clear_agent_blockout(args):
    return assistant_blockout.clear(args.get("expected_revision"))


SPECS = [
    ToolSpec(
        "blockout_plan",
        "Inspect the current scene's saved assistant blockout recipe, revision and edit safety. Also returns supported primitives, categories, colors, size limits and planning guidance. Use the connected assistant's reasoning to design or refine a complete recipe from the user's request. Reads local state without changing geometry.",
        _schema({}),
        blockout_plan,
        {"readOnlyHint": True, "openWorldHint": False},
    ),
    ToolSpec(
        "build_agent_blockout",
        "Build a complete assistant-designed primitive blockout in its own collection. Creates category-colored boxes, cylinders, thin-box planes, wedges, cones and spheres, grouped by label, with explicit center positions, full XYZ sizes and optional XYZ rotation_degrees. Each element requires a stable unique id. Refinement replaces the entire saved recipe and requires its current expected_revision from blockout_plan. Validates and stages the whole plan before replacing pristine owned content. Refuses user edits, shared data and external references; preserves unrelated scene content. Identical plans with the current revision reuse the existing build. The recipe and revision persist in the blend file.",
        _schema(
            {
                "plan": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string", "minLength": 1, "maxLength": 60},
                        "elements": {
                            "type": "array",
                            "minItems": 1,
                            "maxItems": 200,
                            "items": {
                                "type": "object",
                                "properties": {
                                    "id": {"type": "string", "pattern": "^[A-Za-z0-9_-]{1,60}$"},
                                    "name": {"type": "string", "minLength": 1, "maxLength": 60},
                                    "primitive": {
                                        "type": "string",
                                        "enum": [
                                            "box",
                                            "cylinder",
                                            "plane",
                                            "wedge",
                                            "cone",
                                            "sphere",
                                        ],
                                    },
                                    "category": {
                                        "type": "string",
                                        "enum": [
                                            "floor",
                                            "wall",
                                            "structure",
                                            "prop",
                                            "furniture",
                                            "vegetation",
                                            "vehicle",
                                            "water",
                                            "light",
                                            "other",
                                        ],
                                    },
                                    "position": {
                                        "type": "array",
                                        "items": {
                                            "type": "number",
                                            "minimum": -2000,
                                            "maximum": 2000,
                                        },
                                        "minItems": 3,
                                        "maxItems": 3,
                                    },
                                    "size": {
                                        "type": "array",
                                        "items": {
                                            "type": "number",
                                            "minimum": 0.02,
                                            "maximum": 500,
                                        },
                                        "minItems": 3,
                                        "maxItems": 3,
                                    },
                                    "rotation_degrees": {
                                        "type": "array",
                                        "items": {
                                            "type": "number",
                                            "minimum": -360,
                                            "maximum": 360,
                                        },
                                        "minItems": 3,
                                        "maxItems": 3,
                                    },
                                    "group": {"type": "string", "minLength": 1, "maxLength": 60},
                                    "color": {
                                        "type": "array",
                                        "items": {"type": "number", "minimum": 0, "maximum": 1},
                                        "minItems": 3,
                                        "maxItems": 3,
                                    },
                                },
                                "required": [
                                    "id",
                                    "name",
                                    "primitive",
                                    "category",
                                    "position",
                                    "size",
                                ],
                                "additionalProperties": False,
                            },
                        },
                    },
                    "required": ["name", "elements"],
                    "additionalProperties": False,
                },
                "expected_revision": {
                    "type": "string",
                    "description": "Required for replacing an existing blockout; read blockout_plan first",
                },
            },
            ["plan"],
        ),
        build_agent_blockout,
        {"destructiveHint": True, "openWorldHint": False},
    ),
    ToolSpec(
        "clear_agent_blockout",
        "Remove only the current scene's pristine owned blockout and its saved recipe. Requires the current expected_revision from blockout_plan. Refuses user-edited, shared or externally referenced objects, meshes, materials and collections, preserving them for manual review. Unrelated scene content stays intact.",
        _schema({"expected_revision": {"type": "string"}}, ["expected_revision"]),
        clear_agent_blockout,
        {"destructiveHint": True, "openWorldHint": False},
    ),
]
