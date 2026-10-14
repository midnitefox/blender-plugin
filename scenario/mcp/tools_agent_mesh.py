# SPDX-FileCopyrightText: 2026 Open Studio contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Assistant mesh-edit export and explicit application without a generation service."""

from ..blender import assistant_mesh
from .protocol import ToolSpec
from .tools_agent import _inbox, _schema


def export_agent_mesh(args):
    return assistant_mesh.export_mesh(args.get("object_name"))


def apply_agent_mesh(args):
    return assistant_mesh.apply_mesh(args, _inbox())


SPECS = [
    ToolSpec(
        "export_agent_mesh",
        "Export exactly one named static editable mesh as a local embedded GLB and return its "
        "path, SHA-256, source-world-to-local matrix and session handle. Existing selection is "
        "preserved. The source must be local to this scene, in Object Mode, without modifiers, "
        "constraints, vertex groups, animation, shape keys, object material overrides or custom "
        "mesh properties other than this extension's verified blockout ownership marker. Transform "
        "must be invertible and orientation preserving. A new export "
        "supersedes this mesh's previous handle; pending handles expire on file load, Undo/Redo "
        "and extension disable. Up to 64 pending exports are retained. Edit the exported file "
        "with the assistant's available tools, then return a self-contained GLB through the "
        "inbox and apply_agent_mesh. This does not invoke a generation service or spend credits.",
        _schema({"object_name": {"type": "string"}}, ["object_name"]),
        export_agent_mesh,
        {"destructiveHint": False, "idempotentHint": False, "openWorldHint": False},
    ),
    ToolSpec(
        "apply_agent_mesh",
        "Apply a returned embedded GLB (up to 256 MiB) from the assistant inbox to the exact "
        "unchanged target captured by export_agent_mesh. REMESH replaces geometry, UVs and "
        "materials; UV replaces active UVs; RETEXTURE replaces UV/material assignments; PARTS "
        "replaces the source mesh with 2 to 128 parts parented to the source; RIG attaches a "
        "compatible returned armature and weights. UV/RETEXTURE/RIG require matching indexed "
        "geometry after the explicit mapping. RIG accepts animation clips but rejects morphs; "
        "other policies require static meshes. result_to_source is an explicit orientation-"
        "preserving affine 4x4 matrix from returned Blender scene coordinates (after glTF axis "
        "conversion) into source-local coordinates. Never guess alignment. keep_original "
        "explicitly chooses whether to preserve a separate original object. Success consumes "
        "the handle, preserves source transforms/parent/selection, and reports native Undo "
        "availability. No separate restore command. On an uncertain failure inspect the scene "
        "before exporting again. Works with arbitrary Python execution disabled.",
        _schema(
            {
                "handle": {"type": "string"},
                "filename": {"type": "string", "description": "Returned GLB basename in the inbox"},
                "sha256": {"type": "string"},
                "policy": {"type": "string", "enum": ["REMESH", "UV", "RETEXTURE", "PARTS", "RIG"]},
                "result_to_source": {
                    "type": "array",
                    "minItems": 4,
                    "maxItems": 4,
                    "items": {
                        "type": "array",
                        "minItems": 4,
                        "maxItems": 4,
                        "items": {"type": "number"},
                    },
                },
                "keep_original": {"type": "boolean"},
            },
            ["handle", "filename", "policy", "result_to_source", "keep_original"],
        ),
        apply_agent_mesh,
        {"destructiveHint": True, "idempotentHint": False, "openWorldHint": False},
    ),
]
