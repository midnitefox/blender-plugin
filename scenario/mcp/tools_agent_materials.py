# SPDX-FileCopyrightText: 2026 Open Studio contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Explicit assistant-created material maps and equirectangular environments."""

from ..blender import assistant_materials, file_state
from ..core.agent_artifacts import read_inbox_file, read_inbox_image
from .protocol import ToolSpec
from .tools_agent import _inbox


def import_agent_material(args):
    maps = args.get("maps")
    if (
        not isinstance(maps, dict)
        or "base_color" not in maps
        or set(maps) - set(assistant_materials.MAP_ROLES)
    ):
        raise ValueError("Supply base_color and only supported explicit material map roles")
    if "roughness" in maps and "smoothness" in maps:
        raise ValueError("Provide either roughness or smoothness, not both")
    if args.get("normal_convention", "OPENGL") != "OPENGL":
        raise ValueError("Supply an OpenGL tangent-space normal map; convert DirectX maps first")
    inbox = _inbox()
    snapshots = {}
    for role, item in maps.items():
        if not isinstance(item, dict) or set(item) - {"filename", "sha256"}:
            raise ValueError("Each map needs a filename and optional sha256")
        filename = item.get("filename", "")
        if isinstance(filename, str) and filename.lower().endswith(".exr"):
            snapshots[role] = read_inbox_file(
                inbox,
                filename,
                item.get("sha256"),
                suffixes={".exr"},
                max_bytes=assistant_materials.MAX_FILE_BYTES,
            )
        else:
            snapshots[role] = read_inbox_image(inbox, filename, item.get("sha256"))
    receipt, changed = assistant_materials.apply_material(
        args.get("object_name", ""),
        snapshots,
        inbox,
        height_distance=args.get("height_distance", 0.05),
    )
    if changed:
        file_state.mark_modified("Open Studio assistant material")
    return {
        "object": receipt.target.name,
        "material": receipt.material.name,
        "maps": {
            role: {"image": image.name, "sha256": snapshots[role].sha256}
            for role, image in zip(
                (r for r in assistant_materials.MAP_ROLES if r in snapshots),
                receipt.images,
                strict=True,
            )
        },
        "packed": True,
        "reused": not changed,
        "restore_available": True,
        "notes": "Base color is interpreted as sRGB, including EXR; all other maps are Non-Color. Smoothness is inverted into roughness. Normal maps use OpenGL tangent space. Height uses bump, without geometric displacement. AO multiplies base color. Edge stays a named packed texture for manual wiring. Restore is available until file load, undo, redo or extension disable, and refuses edited results. A mesh copy needed to isolate a shared mesh stays isolated after restoring its material.",
    }


def restore_agent_material(args):
    name = assistant_materials.restore_material(args.get("object_name", ""))
    file_state.mark_modified("Open Studio restore material")
    return {"object": name, "restored": True}


def import_agent_environment(args):
    inbox = _inbox()
    snapshot = read_inbox_file(
        inbox,
        args.get("filename", ""),
        args.get("sha256"),
        suffixes={".png", ".jpg", ".jpeg", ".exr", ".hdr"},
        max_bytes=128 * 1024 * 1024,
    )
    receipt, changed = assistant_materials.apply_environment(
        snapshot, inbox, args.get("strength", 1.0)
    )
    if changed:
        file_state.mark_modified("Open Studio assistant environment")
    return {
        "world": receipt._world.name,
        "image": receipt._image.name,
        "sha256": snapshot.sha256,
        "width": receipt.info.width,
        "height": receipt.info.height,
        "floating_point": receipt.info.hdr_capable,
        "packed": True,
        "reused": not changed,
        "restore_available": True,
        "notes": "The supplied image must depict an equirectangular panorama; 2:1 dimensions alone cannot prove projection or seamlessness. Restore is available until file load, undo, redo or extension disable, and refuses edited results.",
    }


def restore_agent_environment(args):
    world = assistant_materials.restore_environment()
    file_state.mark_modified("Open Studio restore environment")
    return {"world": world, "restored": True}


def _schema(properties, required=()):
    return {
        "type": "object",
        "properties": properties,
        "required": list(required),
        "additionalProperties": False,
    }


SPECS = [
    ToolSpec(
        "import_agent_material",
        "Apply an assistant-created PBR map set from the local inbox to one named editable mesh with UVs. Supply base_color and explicit optional roughness OR smoothness, metallic, normal, height, ambient_occlusion and edge roles, each with filename and optional sha256. Maps accept PNG/JPEG/WebP or single-part non-deep scanline EXR, with matching dimensions and at most 8192 pixels per edge. Limits: 64 MiB per PNG/JPEG/WebP, 128 MiB per EXR, 256 MiB combined bytes, 32 megapixels combined and 128 million decoded channel samples. Creates a new packed material in the active slot while preserving other objects and slots. Base color is interpreted as sRGB including EXR; other maps are Non-Color. Smoothness is inverted into Roughness; Normal uses OpenGL tangent space; height uses bump; AO multiplies base color; edge remains a named packed node for manual wiring. Repeating an unchanged set in this session reuses it. Does not create maps or establish their physical accuracy.",
        _schema(
            {
                "object_name": {"type": "string"},
                "maps": {
                    "type": "object",
                    "description": "Explicit role to {filename, sha256?}; base_color is required",
                    "properties": {
                        "base_color": {
                            "type": "object",
                            "properties": {
                                "filename": {"type": "string"},
                                "sha256": {"type": "string"},
                            },
                            "required": ["filename"],
                            "additionalProperties": False,
                        },
                        "roughness": {
                            "type": "object",
                            "properties": {
                                "filename": {"type": "string"},
                                "sha256": {"type": "string"},
                            },
                            "required": ["filename"],
                            "additionalProperties": False,
                        },
                        "smoothness": {
                            "type": "object",
                            "properties": {
                                "filename": {"type": "string"},
                                "sha256": {"type": "string"},
                            },
                            "required": ["filename"],
                            "additionalProperties": False,
                        },
                        "metallic": {
                            "type": "object",
                            "properties": {
                                "filename": {"type": "string"},
                                "sha256": {"type": "string"},
                            },
                            "required": ["filename"],
                            "additionalProperties": False,
                        },
                        "normal": {
                            "type": "object",
                            "properties": {
                                "filename": {"type": "string"},
                                "sha256": {"type": "string"},
                            },
                            "required": ["filename"],
                            "additionalProperties": False,
                        },
                        "height": {
                            "type": "object",
                            "properties": {
                                "filename": {"type": "string"},
                                "sha256": {"type": "string"},
                            },
                            "required": ["filename"],
                            "additionalProperties": False,
                        },
                        "ambient_occlusion": {
                            "type": "object",
                            "properties": {
                                "filename": {"type": "string"},
                                "sha256": {"type": "string"},
                            },
                            "required": ["filename"],
                            "additionalProperties": False,
                        },
                        "edge": {
                            "type": "object",
                            "properties": {
                                "filename": {"type": "string"},
                                "sha256": {"type": "string"},
                            },
                            "required": ["filename"],
                            "additionalProperties": False,
                        },
                    },
                    "required": ["base_color"],
                    "additionalProperties": False,
                },
                "normal_convention": {"type": "string", "enum": ["OPENGL"], "default": "OPENGL"},
                "height_distance": {
                    "type": "number",
                    "minimum": 0,
                    "maximum": 1000,
                    "default": 0.05,
                },
            },
            ["object_name", "maps"],
        ),
        import_agent_material,
        {"destructiveHint": False, "idempotentHint": True, "openWorldHint": False},
    ),
    ToolSpec(
        "restore_agent_material",
        "Restore the named mesh's material slot from the most recent assistant PBR application in this live file session. Refuses changed graphs, maps, destinations or copied geometry. A mesh copy created to isolate a shared mesh stays isolated. Restores only while the original data still exists; unavailable after load, undo, redo or extension disable.",
        _schema({"object_name": {"type": "string"}}, ["object_name"]),
        restore_agent_material,
        {"destructiveHint": False, "openWorldHint": False},
    ),
    ToolSpec(
        "import_agent_environment",
        "Apply an assistant-supplied equirectangular PNG/JPEG/EXR/Radiance HDR from the local inbox to the current scene. Requires actual 2:1 dimensions, at least 4x2, at most 32 megapixels and 128 MiB. Packs the image and creates a separate Environment Texture World. The previous World stays untouched and can be restored in this live file session. Repeat unchanged input and strength reuses it. Dimensions do not establish correct panorama projection, seams or high dynamic range.",
        _schema(
            {
                "filename": {"type": "string"},
                "sha256": {"type": "string"},
                "strength": {"type": "number", "minimum": 0, "maximum": 1000, "default": 1},
            },
            ["filename"],
        ),
        import_agent_environment,
        {"destructiveHint": False, "idempotentHint": True, "openWorldHint": False},
    ),
    ToolSpec(
        "restore_agent_environment",
        "Restore the previous World after the most recent assistant environment application in this live file session. Refuses an edited or replaced World or image. Unavailable after load, undo, redo or extension disable.",
        _schema({}),
        restore_agent_environment,
        {"destructiveHint": False, "openWorldHint": False},
    ),
]
