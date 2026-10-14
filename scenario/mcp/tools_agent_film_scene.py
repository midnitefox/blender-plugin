# SPDX-FileCopyrightText: 2026 Open Studio contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Complete local assistant shot recipes and editable director timelines."""

from ..blender import assistant_film_scene
from ..core.scene.assistant_blockout import CATEGORIES, PRIMITIVES
from .protocol import ToolSpec
from .tools_agent import _inbox, _schema


def build_agent_shot(args):
    return assistant_film_scene.build_shot(args.get("plan"), _inbox())


def build_agent_timeline(args):
    return assistant_film_scene.build_timeline(args.get("plan"))


def _vector(low, high):
    return {
        "type": "array",
        "items": {"type": "number", "minimum": low, "maximum": high},
        "minItems": 3,
        "maxItems": 3,
    }


_LABEL = {"type": "string", "minLength": 1, "maxLength": 60}
_ID = {"type": "string", "pattern": "^[A-Za-z0-9_-]{1,60}$"}
_TRANSFORM = {
    "position": _vector(-2000, 2000),
    "rotation_degrees": _vector(-360, 360),
    "scale": _vector(0.001, 500),
}
_MOTION = {
    "type": "array",
    "maxItems": 128,
    "items": _schema(
        {"frame": {"type": "integer", "minimum": 1}, **_TRANSFORM},
        ["frame", "position", "rotation_degrees", "scale"],
    ),
}
_PATH = {
    "type": "array",
    "minItems": 1,
    "maxItems": 128,
    "items": _schema(
        {"frame": {"type": "integer", "minimum": 1}, "position": _vector(-2000, 2000)},
        ["frame", "position"],
    ),
}
_TIMING = {
    "name": _LABEL,
    "fps": {"type": "integer", "minimum": 1, "maximum": 120},
    "width": {"type": "integer", "minimum": 64, "maximum": 4096, "multipleOf": 2},
    "height": {"type": "integer", "minimum": 64, "maximum": 2160, "multipleOf": 2},
}
_SHOT = {
    **_TIMING,
    "frames": {"type": "integer", "minimum": 1, "maximum": 7200},
    "primitives": {
        "type": "array",
        "maxItems": 200,
        "items": _schema(
            {
                "id": _ID,
                "name": _LABEL,
                "primitive": {"type": "string", "enum": list(PRIMITIVES)},
                "category": {"type": "string", "enum": list(CATEGORIES)},
                "position": _vector(-2000, 2000),
                "size": _vector(0.02, 500),
                "rotation_degrees": _vector(-360, 360),
                "color": _vector(0, 1),
                "group": _LABEL,
                "motion": _MOTION,
            },
            ["id", "name", "primitive", "category", "position", "size"],
        ),
    },
    "camera": _schema(
        {
            "lens_mm": {"type": "number", "minimum": 8, "maximum": 400},
            "path": _PATH,
            "target_path": _PATH,
        },
        ["lens_mm", "path", "target_path"],
    ),
    "actors": {
        "type": "array",
        "maxItems": 16,
        "items": _schema(
            {
                "id": _ID,
                "name": _LABEL,
                "filename": {
                    "type": "string",
                    "description": "Exact self-contained GLB filename in agent_workspace inbox",
                },
                "sha256": {"type": "string", "pattern": "^[0-9a-f]{64}$"},
                **_TRANSFORM,
                "motion": _MOTION,
                "clip": _schema(
                    {
                        "animation_index": {"type": "integer", "minimum": 0, "maximum": 127},
                        "mode": {"type": "string", "enum": ["once", "loop", "hold"]},
                        "speed": {"type": "number", "minimum": 0.1, "maximum": 10, "default": 1},
                        "phase": {"type": "number", "minimum": 0, "maximum": 1, "default": 0},
                    },
                    ["animation_index", "mode"],
                ),
            },
            ["id", "name", "filename", "sha256", "position"],
        ),
    },
}
_TIMELINE = {
    **_TIMING,
    "shots": {
        "type": "array",
        "minItems": 1,
        "maxItems": 128,
        "items": _schema(
            {
                "scene_name": {"type": "string", "minLength": 1, "maxLength": 255},
                "scene_id": {"type": "string", "pattern": "^[0-9a-f]{32}$"},
                "start_frame": {"type": "integer", "minimum": 1},
                "end_frame": {"type": "integer", "minimum": 1},
            },
            ["scene_name", "scene_id", "start_frame", "end_frame"],
        ),
    },
}


SPECS = [
    ToolSpec(
        "build_agent_shot",
        "Build a new independent editable shot scene from a complete assistant-authored recipe. "
        "Specify integer FPS, even resolution and 1 to 7200 frames within 120 seconds. "
        "Categorized primitives use blockout center positions/full XYZ sizes, linear RGB colors "
        "and optional grouped labels. Camera/target paths and full actor/primitive transform keys "
        "use strictly increasing integer frames starting at 1 with linear interpolation. "
        "Primitive motion scale multiplies its size; actor transforms use its GLB origin, Blender "
        "Z-up units and XYZ degrees, without automatic fitting. Every GLB actor requires an inbox "
        "filename and SHA256; geometry and rigs import independently and images are packed. "
        "Animated actors require an explicit zero-based animation_index from the source GLB, "
        "with once/loop/hold mode; phase selects a held pose. Builds a new scene, preserves current "
        "scene/selection/frame, and rolls back failed construction. Returns its persistent "
        "scene_id identity, timing and native undo availability; save the blend to retain it.",
        _schema(
            {
                "plan": {
                    "type": "object",
                    "properties": _SHOT,
                    "required": list(_SHOT),
                    "additionalProperties": False,
                }
            },
            ["plan"],
        ),
        build_agent_shot,
        {"openWorldHint": False},
    ),
    ToolSpec(
        "build_agent_timeline",
        "Build a new editable director timeline from ordered explicit shot scene_name/scene_id "
        "references and inclusive source start_frame/end_frame ranges. Uses consecutive scene "
        "strips on channel 1 from timeline frame 1, with cut markers. All sources must be local "
        "shot scenes with matching integer FPS/resolution and a valid camera; duplicate scene "
        "identities and out-of-range trims are refused before mutation. scene_id is identity, "
        "not a content fingerprint: the timeline references each shot's current editable content. "
        "Supports up to 128 cuts within ten minutes and 72000 frames. Does not replace existing "
        "scenes or change current scene/selection/frame. Returns identity, timing and undo availability.",
        _schema(
            {
                "plan": {
                    "type": "object",
                    "properties": _TIMELINE,
                    "required": list(_TIMELINE),
                    "additionalProperties": False,
                }
            },
            ["plan"],
        ),
        build_agent_timeline,
        {"openWorldHint": False},
    ),
]
