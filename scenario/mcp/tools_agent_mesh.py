# SPDX-FileCopyrightText: 2026 Open Studio contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Assistant mesh-edit export and explicit application without a generation service."""

from ..blender import assistant_mesh
from .protocol import ToolSpec
from .tools_agent import _inbox, _schema


def export_agent_mesh(args):
    return assistant_mesh.export_mesh(
        args.get("object_name"),
        object_names=args.get("object_names"),
        mode=args.get("mode", "EDIT"),
        coordinates=args.get("coordinates", "WORLD"),
    )


def apply_agent_mesh(args):
    return assistant_mesh.apply_mesh(args, _inbox())


SPECS = [
    ToolSpec(
        "export_agent_mesh",
        "Export named meshes as a local embedded GLB with SHA-256, coordinate metadata and adjacent manifest. "
        "mode EDIT (default) grants a session edit handle for exactly one named static mesh. "
        "Its source must be local to this scene, in Object Mode, without modifiers, "
        "constraints, vertex groups, animation, shape keys, object material overrides or custom "
        "mesh properties other than this extension's verified blockout ownership marker. "
        "coordinates WORLD (default) preserves scene placement and requires an invertible orientation-preserving "
        "source transform for EDIT. Choose LOCAL to export source-local geometry and edit mirrored or singular "
        "sources without changing their transforms; the suggested result_to_source is identity. "
        "mode REFERENCE exports evaluated current-frame geometry with supported modifiers, current rig pose "
        "and shape keys baked into static meshes. It never grants edit authority or exports animation. "
        "Use object_name for one source, or object_names for 1-64 distinct REFERENCE/WORLD sources. "
        "WORLD reference geometry bakes transforms, correcting reflected face winding and corner normals. "
        "Use LOCAL for a singular reference transform. Realized surface meshes are required; temporary copies "
        "are limited to 2,000,000 geometry/attribute components and the GLB to 256 MiB. "
        "Selection, active scene, frame and source data are preserved. A new export "
        "supersedes this mesh's previous handle; pending handles expire on file load, Undo/Redo "
        "and extension disable. Up to 64 pending exports are retained. Edit the exported file "
        "with the assistant's available tools. For EDIT, return a self-contained GLB through the "
        "inbox and apply_agent_mesh. For REFERENCE, import returned assets separately. No animation retargeting "
        "or generation service is invoked.",
        _schema(
            {
                "object_name": {"type": "string"},
                "object_names": {
                    "type": "array",
                    "minItems": 1,
                    "maxItems": 64,
                    "items": {"type": "string"},
                },
                "mode": {"type": "string", "enum": ["EDIT", "REFERENCE"], "default": "EDIT"},
                "coordinates": {"type": "string", "enum": ["WORLD", "LOCAL"], "default": "WORLD"},
            }
        ),
        export_agent_mesh,
        {"destructiveHint": False, "idempotentHint": False, "openWorldHint": False},
    ),
    ToolSpec(
        "apply_agent_mesh",
        "Apply a returned embedded GLB (up to 256 MiB) from the assistant inbox to the exact "
        "unchanged EDIT target captured by export_agent_mesh. REFERENCE exports cannot be applied in place. "
        "A LOCAL export can target a mirrored source because its mapping stays in source-local coordinates; "
        "source reflection is not baked into the returned edit. REMESH replaces geometry, UVs and "
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
