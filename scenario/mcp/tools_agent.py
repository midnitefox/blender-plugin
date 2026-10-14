# SPDX-FileCopyrightText: 2026 Open Studio contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Local assistant-generated images: no AI account, uploads or Python execution."""

import hashlib
import math
import tempfile
from pathlib import Path

import bpy

from ..blender import file_state, runtime
from ..core.agent_artifacts import IMAGE_SUFFIXES, MAX_IMAGE_BYTES, read_inbox_image
from .protocol import ToolSpec

_DIGEST = "open_studio_image_sha256"


def _inbox():
    path = runtime.paths().state_dir / "assistant-inbox"
    if path.is_symlink():
        raise ValueError("Assistant inbox must be a private directory")
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    return path.resolve(strict=True)


def agent_workspace(args):
    return {
        "inbox": str(_inbox()),
        "supported_images": sorted(IMAGE_SUFFIXES),
        "max_image_bytes": MAX_IMAGE_BYTES,
        "generation_provider_required": False,
        "instructions": (
            "Use your own image-generation tool, such as Codex ImageGen. Save or copy its "
            "generated image to this inbox, then call import_agent_image with its basename. "
            "Use REFERENCE for concept art, TEXTURE for a named mesh's active material slot, "
            "or IMAGE to load it for later work. Images are packed into the blend file. "
            "This extension cannot invoke your assistant's image-generation tool itself."
        ),
    }


def _load_image(snapshot, inbox):
    for image in bpy.data.images:
        if (
            image.get(_DIGEST) == snapshot.sha256
            and image.packed_file
            and not image.is_dirty
            and hashlib.sha256(image.packed_file.data).hexdigest() == snapshot.sha256
        ):
            return image, False
    with tempfile.TemporaryDirectory(prefix="decode-", dir=inbox) as directory:
        path = Path(directory) / ("image" + snapshot.suffix)
        path.write_bytes(snapshot.data)
        image = None
        try:
            image = bpy.data.images.load(str(path), check_existing=False)
            width, height = image.size
            if width < 1 or height < 1 or width * height > 64 * 1024 * 1024:
                raise ValueError("Generated image has no usable pixels or exceeds the pixel limit")
            image.name = Path(snapshot.name).stem
            image.pack()
            image[_DIGEST] = snapshot.sha256
            # Packed data survives inbox cleanup and the next save/reopen.
            image.use_fake_user = True
            return image, True
        except Exception:
            if image is not None:
                bpy.data.images.remove(image)
            raise ValueError("Blender could not decode and pack the generated image") from None


def import_agent_image(args):
    """Decode first; apply on the main thread, reusing identical packed content."""
    mode = args.get("mode", "REFERENCE")
    if mode not in {"IMAGE", "REFERENCE", "TEXTURE"}:
        raise ValueError("Choose IMAGE, REFERENCE or TEXTURE")
    target = None
    if mode == "TEXTURE":
        target = bpy.context.scene.objects.get(args.get("object_name", ""))
        if target is None or target.type != "MESH":
            raise ValueError("Name a mesh in the current scene for its active material slot")
        if not target.is_editable:
            raise ValueError("Choose an editable local mesh for the image texture")
        if not target.data.uv_layers:
            raise ValueError(
                "The target mesh needs UV coordinates before applying an image texture"
            )
    size = args.get("display_size", 5.0)
    if type(size) not in {int, float} or not math.isfinite(size) or not 0 < size <= 10000:
        raise ValueError("Use a positive reference display size up to 10000")
    inbox = _inbox()
    snapshot = read_inbox_image(inbox, args.get("filename", ""), args.get("sha256"))
    image, created = _load_image(snapshot, inbox)
    result = {"image": image.name, "sha256": snapshot.sha256, "packed": True, "mode": mode}
    changed = created
    try:
        if mode == "REFERENCE":
            reference = next(
                (
                    obj
                    for obj in bpy.context.scene.objects
                    if obj.type == "EMPTY" and obj.data == image
                ),
                None,
            )
            if reference is None:
                reference = bpy.data.objects.new(image.name + " reference", None)
                changed = True
                reference.empty_display_type = "IMAGE"
                reference.data = image
                reference.empty_display_size = size
                reference.location = bpy.context.scene.cursor.location
                bpy.context.scene.collection.objects.link(reference)
                changed = True
            result["object"] = reference.name
        elif mode == "TEXTURE":
            material = target.active_material
            matches = bool(
                material
                and material.use_nodes
                and material.get(_DIGEST) == snapshot.sha256
                and any(
                    link.to_node.type == "BSDF_PRINCIPLED"
                    and link.to_socket.name == "Base Color"
                    and link.from_node.type == "TEX_IMAGE"
                    and link.from_node.image == image
                    for link in material.node_tree.links
                )
            )
            if not matches:
                material = bpy.data.materials.new(image.name + " texture")
                changed = True
                material.use_nodes = True
                nodes = material.node_tree.nodes
                shader = next(node for node in nodes if node.type == "BSDF_PRINCIPLED")
                texture = nodes.new("ShaderNodeTexImage")
                texture.image = image
                material.node_tree.links.new(texture.outputs["Color"], shader.inputs["Base Color"])
                material[_DIGEST] = snapshot.sha256
                if not target.material_slots:
                    if target.data.users > 1:
                        target.data = target.data.copy()
                    target.data.materials.append(material)
                else:
                    slot = target.material_slots[target.active_material_index]
                    slot.link = "OBJECT"
                    slot.material = material
                changed = True
            result.update(object=target.name, material=material.name)
    finally:
        if changed:
            file_state.mark_modified("Open Studio assistant image")
    return result


def _schema(properties, required=()):
    return {
        "type": "object",
        "properties": properties,
        "required": list(required),
        "additionalProperties": False,
    }


SPECS = [
    ToolSpec(
        "agent_workspace",
        "Get the local image inbox for Codex ImageGen or another assistant's generated assets. "
        "Copy a generated PNG/JPEG/WebP into the returned inbox, then use import_agent_image. "
        "Use the assistant's own image-generation tool; Blender cannot invoke it directly.",
        _schema({}),
        agent_workspace,
        {"readOnlyHint": True, "openWorldHint": False},
    ),
    ToolSpec(
        "import_agent_image",
        "Import an assistant-generated image from the agent_workspace inbox. "
        "IMAGE loads packed pixels; REFERENCE creates a viewport reference at the 3D cursor; "
        "TEXTURE applies a new base-color material to the named mesh's active slot, requiring UVs. "
        "This does not generate a full PBR material. Existing other-object materials are preserved. "
        "Packed pixels survive inbox cleanup. Repeating the same image and destination reuses it. "
        "This local import works with arbitrary Python execution disabled.",
        _schema(
            {
                "filename": {
                    "type": "string",
                    "description": "Image basename inside the assistant inbox",
                },
                "mode": {
                    "type": "string",
                    "enum": ["IMAGE", "REFERENCE", "TEXTURE"],
                    "default": "REFERENCE",
                },
                "object_name": {"type": "string", "description": "Required for TEXTURE"},
                "sha256": {
                    "type": "string",
                    "description": "Optional digest to verify the exact generated image",
                },
                "display_size": {"type": "number", "minimum": 0.001, "maximum": 10000},
            },
            ["filename"],
        ),
        import_agent_image,
        {"destructiveHint": False, "idempotentHint": True, "openWorldHint": False},
    ),
]
