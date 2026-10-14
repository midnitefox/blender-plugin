# SPDX-FileCopyrightText: 2026 Open Studio contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Bounded local film exports, portable projects and their session operations."""

from ..blender import assistant_film_export
from .protocol import ToolSpec
from .tools_agent import _schema


def export_agent_film(args):
    return assistant_film_export.export_film(args)


def package_agent_project(args):
    return assistant_film_export.package_project(args)


def get_agent_operation(args):
    return assistant_film_export.get_operation(args.get("operation_id"))


def cancel_agent_operation(args):
    return assistant_film_export.cancel_operation(args.get("operation_id"))


_IDENTITY = {"scene_name": {"type": "string"}, "scene_id": {"type": "string"}}
_OPERATION = _schema({"operation_id": {"type": "string"}}, ["operation_id"])

SPECS = [
    ToolSpec(
        "export_agent_film",
        "Snapshot the current contents of one explicitly named director timeline or media review and render H.264 MP4 locally in an isolated Blender worker. Supports bounded SCENE strips and their selected camera or sequencer input. Optional scene_id guards identity, not revisions. Writes a new basename.mp4 in the assistant outbox; never overwrites. Use integer scene FPS 1-120, even dimensions 64-4096 and at most 15 minutes. Supply both frame bounds or neither. Audio defaults to audible timeline audio. Returns a session operation_id; poll get_agent_operation for progress and the verified output path/digest. File load, Undo, Redo or extension disable cancel unpublished operations. Does not change the working scene or its render settings.",
        _schema(
            {
                **_IDENTITY,
                "filename": {"type": "string"},
                "start_frame": {"type": "integer", "minimum": 1},
                "end_frame": {"type": "integer", "minimum": 1},
                "width": {"type": "integer", "minimum": 64, "maximum": 4096},
                "height": {"type": "integer", "minimum": 64, "maximum": 4096},
                "audio": {"type": "boolean"},
            },
            ["scene_name", "filename"],
        ),
        export_agent_film,
        {"destructiveHint": False, "idempotentHint": False, "openWorldHint": False},
    ),
    ToolSpec(
        "package_agent_project",
        "Snapshot explicit local scenes and their dependencies into a portable editable project ZIP in the assistant outbox. Each scene entry uses scene_name and optional scene_id. The new basename.zip contains Project.blend, copied relative Media, local recipes, a manifest and opening instructions. Verifies the rewritten blend by reopening it in an isolated Blender process and reads back the entire ZIP. Preserves packed images. Refuses linked/overridden data, movie clips, caches, volumes, tiled/sequence textures, and packed, retimed or modified sound strips. Limits: 256 scenes, 2000 external media files, 16 GiB media, 4 GiB blend. Returns operation_id for get_agent_operation; never overwrites and cancels unpublished work on load/Undo/Redo/disable. Stored recipes are build inputs; the blend contains current edits. No generation service is required to open the extracted project.",
        _schema(
            {
                "scenes": {
                    "type": "array",
                    "minItems": 1,
                    "maxItems": 256,
                    "items": _schema(_IDENTITY, ["scene_name"]),
                },
                "filename": {"type": "string"},
            },
            ["scenes", "filename"],
        ),
        package_agent_project,
        {"destructiveHint": False, "idempotentHint": False, "openWorldHint": False},
    ),
    ToolSpec(
        "get_agent_operation",
        "Read a local film export or project package operation from this Blender session. Returns phase, progress, sources, result and error. DONE includes the verified local output path, SHA-256 and size. Reading may finalize a completed worker's prepared file in the outbox. Operation IDs do not survive restarting Blender.",
        _OPERATION,
        get_agent_operation,
        {"destructiveHint": False, "openWorldHint": False},
    ),
    ToolSpec(
        "cancel_agent_operation",
        "Request cancellation of a pending local film export or project package. The worker stops and its private staging is removed; unpublished output cannot appear later. A completed published file remains available. Poll get_agent_operation until CANCELLED, ERROR or DONE.",
        _OPERATION,
        cancel_agent_operation,
        {"destructiveHint": False, "idempotentHint": True, "openWorldHint": False},
    ),
]
