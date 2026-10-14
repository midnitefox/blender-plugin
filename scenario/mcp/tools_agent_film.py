# SPDX-FileCopyrightText: 2026 Open Studio contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Discover editable film scenes and explicitly select the scene to work on."""

from ..blender import assistant_film
from .protocol import ToolSpec
from .tools_agent import _schema


def inspect_agent_film(args):
    return assistant_film.inspect(
        args.get("scene_name"), args.get("scene_id"), args.get("include_recipe", False)
    )


def activate_agent_scene(args):
    return assistant_film.activate(args.get("scene_name"), args.get("scene_id"))


SPECS = [
    ToolSpec(
        "inspect_agent_film",
        "List local Blender scenes and film identities, or inspect one named scene's actual timing, camera, sequencer strips and optional saved build recipe. Scene IDs establish identity, not content revisions. Manual Blender edits are authoritative; the stored recipe records original build inputs. Inspection does not open external media or verify that its files still exist. Lists are bounded and report truncation. Pass scene_id with scene_name to detect a replaced or duplicated identity.",
        _schema(
            {
                "scene_name": {"type": "string"},
                "scene_id": {"type": "string"},
                "include_recipe": {"type": "boolean", "default": False},
            }
        ),
        inspect_agent_film,
        {"readOnlyHint": True, "openWorldHint": False},
    ),
    ToolSpec(
        "activate_agent_scene",
        "Switch the current Blender window to an explicitly named local scene in Object Mode. Use the scene_id returned by a film builder or inspect_agent_film to guard scene identity. This lets scene tools and animation capture operate on a new shot while Python execution stays disabled. Preserves the geometry, timeline and selection stored in each scene. Reports whether the active scene changed.",
        _schema(
            {"scene_name": {"type": "string"}, "scene_id": {"type": "string"}},
            ["scene_name"],
        ),
        activate_agent_scene,
        {"destructiveHint": False, "openWorldHint": False},
    ),
]
