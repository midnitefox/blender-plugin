# SPDX-FileCopyrightText: 2026 Open Studio contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Bounded local GLB handoffs from the connected assistant's modeling tools."""

import logging
import math
import tempfile
import threading
from pathlib import Path

import bpy

from ..blender import file_state, model_application
from ..core.agent_artifacts import read_inbox_file
from ..core.jobs.store import ResultAsset, StoredResult
from ..core.jobs.transfers import DownloadedResult
from ..core.scene.glb import MAX_GLB_BYTES, inspect_glb
from .protocol import ToolSpec
from .tools_agent import _inbox, _schema

_log = logging.getLogger("open_studio.models")
_DIGEST = "open_studio_model_sha256"


def _destination(args):
    if threading.current_thread() is not threading.main_thread():
        raise ValueError("Import assistant models on Blender's main thread")
    scene = bpy.context.scene
    location = args.get("location", tuple(scene.cursor.location))
    if (
        not isinstance(location, (list, tuple))
        or len(location) != 3
        or any(type(value) not in {int, float} or not math.isfinite(value) for value in location)
    ):
        raise ValueError("Use three finite numbers for the model's bottom-center location")
    name = args.get("name", "Model")
    if (
        not isinstance(name, str)
        or not name.strip()
        or len(name) > 128
        or any(ord(character) < 32 for character in name)
    ):
        raise ValueError("Use a model name containing 1 to 128 visible characters")
    model_application.validate_destination(scene, location)
    return scene, tuple(location), name.strip()


def import_agent_model(args):
    """Import a new group each time, preserving the original scene context."""
    scene, location, name = _destination(args)
    inbox = _inbox()
    snapshot = read_inbox_file(
        inbox,
        args.get("filename", ""),
        args.get("sha256"),
        suffixes={".glb"},
        max_bytes=MAX_GLB_BYTES,
    )
    # Refuse external URIs and oversized geometry before Blender sees the file.
    inspect_glb(snapshot.data, static_only=False)
    directory = tempfile.TemporaryDirectory(prefix="model-", dir=inbox)
    try:
        path = Path(directory.name) / "model.glb"
        path.write_bytes(snapshot.data)
        item = StoredResult(
            ResultAsset(
                "assistant-" + snapshot.sha256,
                path.name,
                "model/gltf-binary",
                len(snapshot.data),
                snapshot.sha256,
            ),
            DownloadedResult(path.name, len(snapshot.data), snapshot.sha256),
        )
        previous_actions = set(bpy.data.actions)
        try:
            application = model_application.apply_model(scene, item, path, cursor=location)
        except model_application.ModelApplicationError:
            # The shared primitive verifies rollback before reporting this class.
            raise
        except Exception:
            file_state.mark_modified("Open Studio model import needs inspection")
            raise
        try:
            application.collection.name = "Open Studio " + name
            application.root.name = "Open Studio " + name
            application.root[_DIGEST] = snapshot.sha256
            application.collection[_DIGEST] = snapshot.sha256
            return {
                "collection": application.collection.name,
                "root": application.root.name,
                "objects": [obj.name for obj in application.objects],
                "actions": sorted(
                    action.name for action in set(bpy.data.actions) - previous_actions
                ),
                "sha256": snapshot.sha256,
                "format": "GLB",
                "packed_textures": True,
                "location": list(location),
                "created_new_group": True,
            }
        finally:
            # Publication succeeded even if reporting is interrupted. Never imply
            # that retrying an interrupted import is safe from duplication.
            file_state.mark_modified("Open Studio assistant model")
    finally:
        try:
            directory.cleanup()
        except OSError:
            _log.warning("Assistant model imported; its temporary copy could not be removed")


SPECS = [
    ToolSpec(
        "import_agent_model",
        "Import a self-contained GLB 2.0 created by the connected assistant's modeling tools. "
        "Copy it to agent_workspace's inbox and pass its basename. Maximum 256 MiB; external "
        "file or URL references are refused. Meshes, hierarchy, materials, packed textures, "
        "rigs, skin weights, shape keys and animation clips are retained. A new group is placed "
        "with its frame-zero bottom center at the explicit location, or the current 3D cursor. "
        "Existing selection, current frame, timeline range and scene frame rate stay unchanged. "
        "Rigged imports require a Blender window context. This imports an existing generated "
        "model; it does not generate or rig one. Every call creates another group; inspect the "
        "scene after an interrupted call before retrying. Arbitrary Python execution may stay off.",
        _schema(
            {
                "filename": {"type": "string", "description": "GLB basename in the inbox"},
                "sha256": {"type": "string", "description": "Optional SHA-256 of the GLB"},
                "location": {
                    "type": "array",
                    "items": {"type": "number"},
                    "minItems": 3,
                    "maxItems": 3,
                    "description": "World-space bottom-center location; defaults to the 3D cursor",
                },
                "name": {"type": "string", "minLength": 1, "maxLength": 128, "default": "Model"},
            },
            ["filename"],
        ),
        import_agent_model,
        {"destructiveHint": False, "idempotentHint": False, "openWorldHint": False},
    ),
]
