# SPDX-FileCopyrightText: 2026 Open Studio contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Local, packed material and environment handoffs with session restore guards."""

import hashlib
import math
import re
import tempfile
from array import array
from dataclasses import dataclass
from pathlib import Path

import bpy
from bpy.app.handlers import persistent

from ..core.agent_artifacts import MAX_IMAGE_BYTES
from ..core.jobs.result_previews import bounded_local_image, local_image_layout
from ..core.scene.panorama import (
    MAX_FILE_BYTES,
    MAX_PIXELS,
    PanoramaInfo,
    inspect_image,
    inspect_panorama,
)
from . import world_application as worlds

MAP_ROLES = (
    "base_color",
    "roughness",
    "smoothness",
    "metallic",
    "normal",
    "height",
    "ambient_occlusion",
    "edge",
)
MAX_MAP_BYTES = 256 * 1024 * 1024
_materials = {}
_environments = {}


def _bounded_number(value, label, minimum, maximum):
    if (
        type(value) not in {float, int}
        or not math.isfinite(value)
        or not minimum <= value <= maximum
    ):
        raise ValueError(f"{label} must be a finite number between {minimum} and {maximum}")
    return value


def _target(name):
    worlds._main_thread()
    scene = bpy.context.scene
    obj = scene.objects.get(name) if isinstance(name, str) else None
    if (
        not scene.is_editable
        or obj is None
        or obj.type != "MESH"
        or obj.mode != "OBJECT"
        or not obj.is_editable
        or obj.library
        or obj.override_library
        or obj.data.library
        or obj.data.override_library
        or len(obj.users_scene) != 1
        or not obj.data.uv_layers.active
        or len(obj.material_slots) > 128
    ):
        raise ValueError("Name a local editable mesh with UVs, in Object Mode, in this scene only")
    return obj


def _slots(obj):
    return tuple((slot.link, slot.material) for slot in obj.material_slots)


def _restore_slot(slot, previous):
    link, material = previous
    if slot.link == "OBJECT" and link == "DATA":
        # Switching link alone leaves an invisible object override with a user
        # on the staged material, preventing its images/material from retiring.
        slot.material = None
    slot.link, slot.material = link, material


def _geometry(mesh):
    """Guard a copied mesh before restoring its former shared datablock."""
    digest = hashlib.sha256()
    for items, prop, width, kind in (
        (mesh.vertices, "co", 3, "f"),
        (mesh.edges, "vertices", 2, "i"),
        (mesh.loops, "vertex_index", 1, "i"),
        (mesh.polygons, "loop_start", 1, "i"),
        (mesh.polygons, "loop_total", 1, "i"),
        (mesh.polygons, "material_index", 1, "i"),
    ):
        if len(items) > 4_000_000:
            raise ValueError("The mesh exceeds the guarded material-assignment size limit")
        values = array(kind, [0]) * (len(items) * width)
        items.foreach_get(prop, values)
        digest.update(values.tobytes())
    for layer in mesh.uv_layers:
        values = array("f", [0]) * (len(layer.data) * 2)
        layer.data.foreach_get("uv", values)
        digest.update(layer.name.encode())
        digest.update(values.tobytes())
    return digest.digest()


def _image_signature(image):
    if not image.packed_file or image.is_dirty:
        raise ValueError("An applied map was edited or unpacked; preserve it before restoring")
    return (
        worlds._settings(image, exclude={"pixels"}),
        worlds._settings(image.colorspace_settings),
        tuple(image.size),
        hashlib.sha256(image.packed_file.data).digest(),
    )


def _signature(material, images):
    # The standalone World primitive records all writable node/socket settings,
    # links, animation and ID properties. Materials expose the same node tree.
    return worlds._fingerprint(material, images[0]), tuple(map(_image_signature, images))


def _discard(material=None, images=(), mesh=None):
    for value, collection in (
        (mesh, bpy.data.meshes),
        (material, bpy.data.materials),
        *((image, bpy.data.images) for image in images),
    ):
        if worlds._present(value, collection) and value.users == 0:
            collection.remove(value)


def _map_info(snapshot):
    """Bound the declared allocation before Blender decodes a material map."""
    formats = {
        ".png": ("PNG", b"\x89PNG\r\n\x1a\n"),
        ".jpg": ("JPEG", b"\xff\xd8\xff"),
        ".jpeg": ("JPEG", b"\xff\xd8\xff"),
        ".webp": ("WEBP", b"RIFF"),
        ".exr": ("OPEN_EXR", b"\x76\x2f\x31\x01"),
    }
    expected = formats.get(snapshot.suffix)
    data = snapshot.data
    limit = MAX_FILE_BYTES if snapshot.suffix == ".exr" else MAX_IMAGE_BYTES
    if not data or len(data) > limit:
        raise ValueError("Material map exceeds its per-file byte limit")
    if (
        expected is None
        or not data.startswith(expected[1])
        or (snapshot.suffix == ".webp" and data[8:12] != b"WEBP")
    ):
        raise ValueError("Material map filename does not match its actual image format")
    layout = local_image_layout(data)
    if layout is None or not bounded_local_image(*layout) or layout[0] * layout[1] > MAX_PIXELS:
        raise ValueError("Material map has unsupported dimensions, channels or pixel limits")
    width, height, channels = layout
    if snapshot.suffix in {".png", ".exr"}:
        info = inspect_image(data)
        if (info.width, info.height) != (width, height) or info.file_format != expected[0]:
            raise ValueError("Material map has conflicting image dimensions or format")
    return PanoramaInfo(expected[0], width, height, snapshot.suffix == ".exr"), channels


def _decode_map(snapshot, inbox, role, info):
    image = None
    try:
        with tempfile.TemporaryDirectory(prefix="map-", dir=inbox) as directory:
            path = Path(directory) / ("map" + snapshot.suffix)
            path.write_bytes(snapshot.data)
            image = bpy.data.images.load(str(path), check_existing=False)
            if (
                tuple(image.size) != (info.width, info.height)
                or image.file_format != info.file_format
                or image.type != "IMAGE"
                or (info.hdr_capable and not image.is_float)
            ):
                raise ValueError("Decoded material map does not match its verified container")
            image.colorspace_settings.name = "sRGB" if role == "base_color" else "Non-Color"
            image.pack()
            if not image.packed_file:
                raise ValueError("Blender could not pack the material map")
            image.name = f"{Path(snapshot.name).stem} {role}"
            image.filepath = ""
            image["open_studio_image_sha256"] = snapshot.sha256
            return image
    except BaseException:
        _discard(images=(image,))
        raise


def _build_material(images, uv_name, height_distance):
    material = bpy.data.materials.new("Open Studio material")
    try:
        material.use_nodes = True
        nodes, links = material.node_tree.nodes, material.node_tree.links
        nodes.clear()
        output = nodes.new("ShaderNodeOutputMaterial")
        shader = nodes.new("ShaderNodeBsdfPrincipled")
        links.new(shader.outputs["BSDF"], output.inputs["Surface"])
        uv = nodes.new("ShaderNodeUVMap")
        uv.uv_map = uv_name
        textures = {}
        for index, (role, image) in enumerate(images.items()):
            node = nodes.new("ShaderNodeTexImage")
            node.image, node.label = image, role
            node.location = (-700, -250 * index)
            links.new(uv.outputs["UV"], node.inputs["Vector"])
            textures[role] = node
        color = textures["base_color"].outputs["Color"]
        if "ambient_occlusion" in textures:
            mix = nodes.new("ShaderNodeMixRGB")
            mix.blend_type = "MULTIPLY"
            mix.inputs["Fac"].default_value = 1.0
            mix.label = "Base color multiplied by ambient occlusion"
            links.new(color, mix.inputs[1])
            links.new(textures["ambient_occlusion"].outputs["Color"], mix.inputs[2])
            color = mix.outputs["Color"]
        links.new(color, shader.inputs["Base Color"])
        for role, socket in (("roughness", "Roughness"), ("metallic", "Metallic")):
            if role in textures:
                links.new(textures[role].outputs["Color"], shader.inputs[socket])
        if "smoothness" in textures:
            invert = nodes.new("ShaderNodeInvert")
            invert.label = "Smoothness to roughness"
            invert.inputs["Fac"].default_value = 1.0
            links.new(textures["smoothness"].outputs["Color"], invert.inputs["Color"])
            links.new(invert.outputs["Color"], shader.inputs["Roughness"])
        # Edge stays a named packed texture with UVs for deliberate artist wiring.
        normal = None
        if "normal" in textures:
            normal = nodes.new("ShaderNodeNormalMap")
            normal.space, normal.uv_map = "TANGENT", uv_name
            links.new(textures["normal"].outputs["Color"], normal.inputs["Color"])
        if "height" in textures:
            bump = nodes.new("ShaderNodeBump")
            bump.inputs["Distance"].default_value = height_distance
            links.new(textures["height"].outputs["Color"], bump.inputs["Height"])
            if normal:
                links.new(normal.outputs["Normal"], bump.inputs["Normal"])
            normal = bump
        if normal:
            links.new(normal.outputs["Normal"], shader.inputs["Normal"])
        return material
    except BaseException:
        _discard(material)
        raise


@dataclass
class MaterialHandoff:
    scene: object
    target: object
    previous_mesh: object
    previous_slots: tuple
    active: int
    material: object
    images: tuple
    assigned_mesh: object
    geometry: bytes
    slots: tuple
    signature: tuple
    key: tuple

    def unchanged(self):
        try:
            return (
                self.scene == bpy.context.scene
                and self.target in tuple(self.scene.objects)
                and self.target.data == self.assigned_mesh
                and self.target.active_material_index == self.active
                and _slots(self.target) == self.slots
                and _signature(self.material, self.images) == self.signature
            )
        except (ReferenceError, ValueError, RuntimeError):
            return False

    def restore(self):
        if not self.unchanged():
            raise ValueError(
                "The applied material or destination changed; review it before restoring"
            )
        if not worlds._present(self.previous_mesh, bpy.data.meshes) or any(
            material is not None and not worlds._present(material, bpy.data.materials)
            for _, material in self.previous_slots
        ):
            raise ValueError("The original mesh or material was removed; restore it manually")
        if self.assigned_mesh != self.previous_mesh:
            if _geometry(self.assigned_mesh) != self.geometry:
                raise ValueError(
                    "The copied mesh was edited; preserve those edits before restoring"
                )
            if self.assigned_mesh.users != 1:
                raise ValueError("The copied mesh is now shared; review it before restoring")
            # Restore only the material assignment. Keep the isolated mesh: swapping
            # datablocks could discard later attributes, shape keys or custom data
            # that a geometry-only fingerprint cannot fully characterize.
            self.assigned_mesh.materials.clear()
        elif not self.previous_slots:
            if self.assigned_mesh.users != 1:
                raise ValueError("The new material slot is now shared; review it before restoring")
            self.assigned_mesh.materials.clear()
        else:
            slot = self.target.material_slots[self.active]
            link, material = self.previous_slots[self.active]
            if link == "DATA" and self.assigned_mesh.materials[self.active] != material:
                raise ValueError("The shared mesh material changed; preserve it before restoring")
            _restore_slot(slot, (link, material))
        _discard(
            self.material,
            self.images,
            self.assigned_mesh if self.assigned_mesh != self.previous_mesh else None,
        )


def apply_material(name, snapshots, inbox, *, height_distance=0.05):
    obj = _target(name)
    _bounded_number(height_distance, "Height distance", 0, 1000)
    if "base_color" not in snapshots or set(snapshots) - set(MAP_ROLES):
        raise ValueError("Provide base_color and only supported explicit map roles")
    if "roughness" in snapshots and "smoothness" in snapshots:
        raise ValueError("Provide either roughness or smoothness, not both")
    if sum(len(item.data) for item in snapshots.values()) > MAX_MAP_BYTES:
        raise ValueError("Material maps exceed the combined 256 MiB byte limit")
    infos = {role: _map_info(snapshot) for role, snapshot in snapshots.items()}
    if sum(info.width * info.height for info, _ in infos.values()) > MAX_PIXELS:
        raise ValueError("Material maps exceed the combined 32 megapixel limit")
    if (
        sum(info.width * info.height * channels for info, channels in infos.values())
        > 4 * MAX_PIXELS
    ):
        raise ValueError("Material maps exceed the combined decoded-channel limit")
    if len({(info.width, info.height) for info, _ in infos.values()}) != 1:
        raise ValueError("Material maps must have matching dimensions")
    key = (
        tuple((role, snapshots[role].sha256) for role in MAP_ROLES if role in snapshots),
        height_distance,
    )
    existing = _materials.get(obj.as_pointer())
    if existing and existing.key == key and existing.unchanged():
        return existing, False
    before_mesh, before_slots, active = obj.data, _slots(obj), obj.active_material_index
    geometry = _geometry(before_mesh)
    images, material, copied = {}, None, None
    try:
        for role in MAP_ROLES:
            if role in snapshots:
                images[role] = _decode_map(snapshots[role], inbox, role, infos[role][0])
        material = _build_material(images, before_mesh.uv_layers.active.name, height_distance)
        if _target(name) != obj or obj.data != before_mesh or _slots(obj) != before_slots:
            raise ValueError("The material destination changed while decoding the maps")
        signature = _signature(material, tuple(images.values()))
        if before_slots:
            slot = obj.material_slots[active]
            slot.link, slot.material = "OBJECT", material
        else:
            if before_mesh.users > 1:
                copied = before_mesh.copy()
                obj.data = copied
            obj.data.materials.append(material)
        receipt = MaterialHandoff(
            bpy.context.scene,
            obj,
            before_mesh,
            before_slots,
            active,
            material,
            tuple(images.values()),
            obj.data,
            geometry,
            _slots(obj),
            signature,
            key,
        )
        _materials[obj.as_pointer()] = receipt
        return receipt, True
    except BaseException:
        if copied is not None and obj.data == copied:
            obj.data = before_mesh
        elif before_slots:
            slot = obj.material_slots[active]
            _restore_slot(slot, before_slots[active])
        elif obj.data == before_mesh:
            obj.data.materials.clear()
        _discard(material, images.values(), copied)
        raise


def restore_material(name):
    obj = _target(name)
    receipt = _materials.get(obj.as_pointer())
    if receipt is None:
        raise ValueError("No material restore is available in this live file session")
    receipt.restore()
    del _materials[obj.as_pointer()]
    return obj.name


def _hdr_info(data):
    # Accept conventional upright RGBE, bounding its header and dimensions before
    # Blender allocates pixels. Alternate axes, XYZE and old RLE are rejected.
    end = data.find(b"\n\n", 0, 16384)
    if end < 0 or not data.startswith((b"#?RADIANCE\n", b"#?RGBE\n")):
        raise ValueError("Use a Radiance RGBE panorama with a bounded standard header")
    header = data[:end].splitlines()
    if [line for line in header if line.startswith(b"FORMAT=")] != [b"FORMAT=32-bit_rle_rgbe"]:
        raise ValueError("Only Radiance RGBE panoramas are supported")
    line_end = data.find(b"\n", end + 2, end + 128)
    match = re.fullmatch(rb"-Y ([0-9]{1,6}) \+X ([0-9]{1,6})", data[end + 2 : line_end])
    if line_end < 0 or match is None:
        raise ValueError("Use an upright Radiance panorama with -Y/+X axes")
    height, width = map(int, match.groups())
    if width != 2 * height or width < 4 or width * height > MAX_PIXELS:
        raise ValueError("Environment images must be 2:1, at least 4x2, and at most 32 megapixels")
    pos = line_end + 1
    if width < 8 or width > 32767:
        if len(data) - pos != width * height * 4:
            raise ValueError("Truncated or unsupported flat RGBE panorama")
        if any(data[i : i + 3] == b"\x01\x01\x01" for i in range(pos, len(data), 4)):
            raise ValueError("Legacy RGBE run encoding is unsupported")
    else:
        for _ in range(height):
            if data[pos : pos + 4] != bytes((2, 2, width >> 8, width & 255)):
                raise ValueError("Radiance panorama has an invalid scanline")
            pos += 4
            for _ in range(4):
                remaining = width
                while remaining:
                    if pos >= len(data):
                        raise ValueError("Truncated Radiance panorama")
                    count = data[pos]
                    pos += 1
                    run = count - 128 if count > 128 else count
                    length = 1 if count > 128 else count
                    if run < 1 or run > remaining or pos + length > len(data):
                        raise ValueError("Invalid Radiance scanline run")
                    remaining -= run
                    pos += length
        if pos != len(data):
            raise ValueError("Unexpected bytes after Radiance panorama")
    return PanoramaInfo("HDR", width, height, True)


def _environment_image(snapshot, inbox):
    if len(snapshot.data) > MAX_FILE_BYTES:
        raise ValueError("Environment exceeds the 128 MiB byte limit")
    if snapshot.suffix == ".hdr":
        info = _hdr_info(snapshot.data)
        image = None
        try:
            with tempfile.TemporaryDirectory(prefix="environment-", dir=inbox) as directory:
                path = Path(directory) / "panorama.hdr"
                path.write_bytes(snapshot.data)
                image = bpy.data.images.load(str(path), check_existing=False)
                if (
                    tuple(image.size) != (info.width, info.height)
                    or not image.is_float
                    or image.file_format != "HDR"
                ):
                    raise ValueError("Blender could not decode the declared Radiance panorama")
                image.pack()
                if image.packed_file is None:
                    raise ValueError("Blender could not pack the Radiance panorama")
                image.filepath = ""
        except BaseException:
            _discard(images=(image,))
            raise
    else:
        info = inspect_panorama(snapshot.data)
        expected = {".png": "PNG", ".jpg": "JPEG", ".jpeg": "JPEG", ".exr": "OPEN_EXR"}
        if info.file_format != expected.get(snapshot.suffix):
            raise ValueError("Environment filename does not match its actual image format")
        image = worlds._load_image(snapshot.data, info)
    image.name = "Open Studio environment"
    image["open_studio_image_sha256"] = snapshot.sha256
    return image, info


def apply_environment(snapshot, inbox, strength):
    worlds._main_thread()
    _bounded_number(strength, "Environment strength", 0, 1000)
    scene = bpy.context.scene
    worlds._scene(scene)
    existing = _environments.get(scene.as_pointer())
    key = snapshot.sha256, strength
    if existing:
        old_key, receipt = existing
        try:
            if (
                old_key == key
                and scene.world == receipt._world
                and worlds._fingerprint(receipt._world, receipt._image) == receipt._snapshot
            ):
                return receipt, False
        except (ReferenceError, RuntimeError):
            pass
    image, info = _environment_image(snapshot, inbox)
    world, previous = None, scene.world
    try:
        world = bpy.data.worlds.new("Open Studio environment")
        world.use_nodes = True
        nodes, links = world.node_tree.nodes, world.node_tree.links
        nodes.clear()
        environment = nodes.new("ShaderNodeTexEnvironment")
        environment.image, environment.projection = image, "EQUIRECTANGULAR"
        background = nodes.new("ShaderNodeBackground")
        background.inputs["Strength"].default_value = strength
        output = nodes.new("ShaderNodeOutputWorld")
        links.new(environment.outputs["Color"], background.inputs["Color"])
        links.new(background.outputs["Background"], output.inputs["Surface"])
        receipt = worlds.WorldApplication(
            info, scene, previous, world, image, worlds._fingerprint(world, image)
        )
        scene.world = world
        _environments[scene.as_pointer()] = key, receipt
        return receipt, True
    except BaseException:
        scene.world = previous
        worlds._discard_unused(world, image)
        raise


def restore_environment():
    worlds._main_thread()
    scene = bpy.context.scene
    existing = _environments.get(scene.as_pointer())
    if existing is None:
        raise ValueError("No environment restore is available in this live file session")
    existing[1].restore()
    del _environments[scene.as_pointer()]
    return scene.world.name if scene.world else None


@persistent
def _clear(*_):
    _materials.clear()
    _environments.clear()


def register():
    for handlers in (
        bpy.app.handlers.load_pre,
        bpy.app.handlers.undo_pre,
        bpy.app.handlers.redo_pre,
    ):
        if _clear not in handlers:
            handlers.append(_clear)


def unregister():
    _clear()
    for handlers in (
        bpy.app.handlers.load_pre,
        bpy.app.handlers.undo_pre,
        bpy.app.handlers.redo_pre,
    ):
        if _clear in handlers:
            handlers.remove(_clear)
