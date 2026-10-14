# SPDX-FileCopyrightText: 2026 Scenario Inc.
# SPDX-License-Identifier: GPL-3.0-or-later
"""Synchronous, explicit-target mesh application; no import, jobs or selection lookup."""

import hashlib
import math
import threading
from dataclasses import dataclass, field

import bpy
from mathutils import Matrix

# Fingerprints run synchronously. Bound both geometry and generic attribute work.
MAX_COMPONENTS = 2_000_000
_ATTRIBUTE_FIELDS = {
    "FLOAT": "value",
    "INT": "value",
    "BOOLEAN": "value",
    "INT8": "value",
    "INT16_2D": "value",
    "INT32_2D": "value",
    "FLOAT_VECTOR": "vector",
    "FLOAT2": "vector",
    "FLOAT_COLOR": "color",
    "BYTE_COLOR": "color",
}


class MeshApplicationError(RuntimeError):
    """An unsupported or changed target requires explicit caller review."""


def _main_thread():
    if threading.current_thread() is not threading.main_thread():
        raise MeshApplicationError("Mesh application must run on Blender's main thread")


def _present(collection, value):
    return any(item is value for item in collection)


def _validate_object(scene, obj, *, allowed_mesh_properties=()):
    if not _present(bpy.data.scenes, scene) or not _present(scene.objects, obj):
        raise MeshApplicationError("Use a live object in the explicit originating scene")
    if obj.type != "MESH" or obj.mode != "OBJECT":
        raise MeshApplicationError("Only meshes in Object Mode are supported")
    if obj.library or obj.override_library or obj.data.library or obj.data.override_library:
        raise MeshApplicationError("Linked and overridden objects or meshes are unsupported")
    if obj.modifiers or obj.constraints or obj.vertex_groups:
        raise MeshApplicationError(
            "Modifiers, constraints and vertex groups require another policy"
        )
    if obj.animation_data or obj.data.animation_data or obj.data.shape_keys:
        raise MeshApplicationError("Animation and shape keys require another policy")
    if obj.parent and (obj.parent.type == "ARMATURE" or obj.parent_type != "OBJECT"):
        raise MeshApplicationError("Bone and vertex parenting require another policy")
    if any(child.parent_type != "OBJECT" for child in obj.children):
        raise MeshApplicationError("Vertex-parented children require another policy")
    if any(slot.link != "DATA" for slot in obj.material_slots):
        raise MeshApplicationError("Object material overrides require another policy")
    if any((key, obj.data[key]) not in allowed_mesh_properties for key in obj.data.keys()):
        raise MeshApplicationError("Custom mesh properties require another policy")


def _plain(value):
    if isinstance(value, float) and not math.isfinite(value):
        raise MeshApplicationError("Nonfinite mesh values are unsupported")
    if isinstance(value, (bool, int, float, str)) or value is None:
        return value
    return tuple(_plain(component) for component in value)


def _fingerprint(mesh):
    """Guard geometry, generic attributes, UV roles and material slot bindings."""
    size = len(mesh.vertices) + len(mesh.edges) + len(mesh.loops) + len(mesh.polygons)
    size += sum(len(attribute.data) for attribute in mesh.attributes)
    if size > MAX_COMPONENTS:
        raise MeshApplicationError("Mesh exceeds the synchronous application component limit")
    digest = hashlib.sha256()

    def add(value):
        digest.update(repr(_plain(value)).encode("utf-8"))
        digest.update(b"\0")

    add((len(mesh.vertices), len(mesh.edges), len(mesh.loops), len(mesh.polygons)))
    for vertex in mesh.vertices:
        add((vertex.co, vertex.select, vertex.hide))
    for edge in mesh.edges:
        add((edge.vertices, edge.use_seam, edge.use_edge_sharp, edge.select, edge.hide))
    for polygon in mesh.polygons:
        add(
            (
                polygon.vertices,
                polygon.material_index,
                polygon.use_smooth,
                polygon.select,
                polygon.hide,
            )
        )
    for loop in mesh.loops:
        add((loop.vertex_index, loop.edge_index))
    for attribute in mesh.attributes:
        name = _ATTRIBUTE_FIELDS.get(attribute.data_type)
        if name is None:
            raise MeshApplicationError("Unsupported mesh attribute type")
        add((attribute.name, attribute.data_type, attribute.domain))
        for item in attribute.data:
            add(getattr(item, name))
    add(
        tuple(
            (material.as_pointer(), material.name) if material else None
            for material in mesh.materials
        )
    )
    add(tuple((layer.name, layer.active_render, layer.active_clone) for layer in mesh.uv_layers))
    add(mesh.uv_layers.active_index)
    add((mesh.use_auto_texspace, mesh.texspace_location, mesh.texspace_size, mesh.use_fake_user))
    # Opted-in source markers are part of identity, including their exact value.
    # Default callers still reject all custom properties before fingerprinting.
    add(tuple((key, mesh[key]) for key in sorted(mesh.keys())))
    return digest.digest()


def _matrix(value):
    try:
        matrix = Matrix(value)
        if len(matrix) != 4 or any(len(row) != 4 for row in matrix):
            raise ValueError
        if any(not math.isfinite(component) for row in matrix for component in row):
            raise ValueError
        if tuple(matrix[3]) != (0.0, 0.0, 0.0, 1.0) or matrix.determinant() <= 0:
            raise ValueError
    except (TypeError, ValueError):
        raise MeshApplicationError(
            "Use an explicit finite orientation-preserving affine 4x4 mapping"
        ) from None
    return matrix


@dataclass(frozen=True)
class MeshTarget:
    scene: object = field(repr=False)
    obj: object = field(repr=False)
    mesh: object = field(repr=False)
    name: str
    mesh_name: str
    geometry: bytes = field(repr=False)
    context: tuple = field(repr=False)
    allowed_mesh_properties: tuple = field(default=(), repr=False)


def capture_target(scene, obj, *, allowed_mesh_properties=()):
    """Freeze an explicit source before asynchronous work; never infer selection."""
    _main_thread()
    try:
        _validate_object(scene, obj, allowed_mesh_properties=allowed_mesh_properties)
        if (
            scene != bpy.context.scene
            or not scene.is_editable
            or scene.library
            or scene.override_library
            or len(obj.users_scene) != 1
        ):
            raise MeshApplicationError("Choose a source in only the current local scene")
        # Parent edits can leave matrix_world stale until dependency evaluation.
        bpy.context.view_layer.update()
        matrices = tuple(
            tuple(tuple(row) for row in matrix)
            for matrix in (
                obj.matrix_basis,
                obj.matrix_local,
                obj.matrix_world,
                obj.matrix_parent_inverse,
            )
        )
        context = (
            matrices,
            obj.parent,
            obj.parent_type,
            frozenset((collection, collection.name) for collection in obj.users_collection),
            obj.active_material_index,
        )
        return MeshTarget(
            scene,
            obj,
            obj.data,
            obj.name,
            obj.data.name,
            _fingerprint(obj.data),
            context,
            tuple(allowed_mesh_properties),
        )
    except (ReferenceError, AttributeError):
        raise MeshApplicationError("The source mesh was removed or replaced") from None


def validate_target(target):
    """Reject changed identity, geometry, transforms, parenting or memberships."""
    if (
        not isinstance(target, MeshTarget)
        or capture_target(
            target.scene, target.obj, allowed_mesh_properties=target.allowed_mesh_properties
        )
        != target
    ):
        raise MeshApplicationError("The source mesh changed; review it again")


def _same_topology(source, result, mapping):
    if (len(source.vertices), len(source.edges), len(source.polygons), len(source.loops)) != (
        len(result.vertices),
        len(result.edges),
        len(result.polygons),
        len(result.loops),
    ):
        return False
    return (
        all(
            tuple(a.co) == tuple(mapping @ b.co)
            for a, b in zip(source.vertices, result.vertices, strict=True)
        )
        and all(
            tuple(a.vertices) == tuple(b.vertices)
            for a, b in zip(source.edges, result.edges, strict=True)
        )
        and all(
            tuple(a.vertices) == tuple(b.vertices)
            for a, b in zip(source.polygons, result.polygons, strict=True)
        )
        and all(
            (a.vertex_index, a.edge_index) == (b.vertex_index, b.edge_index)
            for a, b in zip(source.loops, result.loops, strict=True)
        )
    )


def _retexture(staged, result):
    """Adopt result UV/material bindings without mutating shared shader graphs."""
    if not result.materials or any(material is None for material in result.materials):
        raise MeshApplicationError("Retexture requires nonempty result material slots")
    if any(face.material_index >= len(result.materials) for face in result.polygons):
        raise MeshApplicationError("Retexture requires valid result face material assignments")
    if not result.uv_layers:
        raise MeshApplicationError("Retexture requires result UV layers")
    source_uv_names = {layer.name for layer in staged.uv_layers}
    other_names = {attribute.name for attribute in staged.attributes} - source_uv_names
    if any(layer.name in other_names for layer in result.uv_layers):
        raise MeshApplicationError("Result UV names conflict with preserved source attributes")
    for layer in tuple(staged.uv_layers):
        staged.uv_layers.remove(layer)
    for layer in result.uv_layers:
        destination = staged.uv_layers.new(name=layer.name, do_init=False)
        if destination.name != layer.name:
            raise MeshApplicationError("Cannot preserve result UV names")
        for old, new in zip(destination.data, layer.data, strict=True):
            old.uv = new.uv
        destination.active_render = layer.active_render
        destination.active_clone = layer.active_clone
    staged.uv_layers.active_index = result.uv_layers.active_index
    staged.materials.clear()
    for material in result.materials:
        staged.materials.append(material)
    for old, new in zip(staged.polygons, result.polygons, strict=True):
        old.material_index = new.material_index


def _stage(source, result, policy, mapping):
    if policy == "REMESH":
        staged = result.copy()
        try:
            staged.transform(mapping)
            staged.update()
            return staged
        except BaseException:
            bpy.data.meshes.remove(staged)
            raise
    if not _same_topology(source, result, mapping):
        raise MeshApplicationError(
            f"{policy} application requires identical indexed topology and positions"
        )
    if policy == "UV" and len(result.uv_layers) != 1:
        raise MeshApplicationError("UV application requires exactly one result UV layer")
    staged = source.copy()
    try:
        if policy == "RETEXTURE":
            _retexture(staged, result)
        else:
            destination = staged.uv_layers.active or staged.uv_layers.new(
                name=result.uv_layers[0].name
            )
            for old, new in zip(destination.data, result.uv_layers[0].data, strict=True):
                old.uv = new.uv
        staged.update()
        return staged
    except BaseException:
        bpy.data.meshes.remove(staged)
        raise


def _holder_signature(holder):
    """Snapshot writable object settings; deletion is reserved for our pristine holder."""
    settings = []
    for prop in holder.bl_rna.properties:
        if prop.is_readonly or prop.type == "COLLECTION":
            continue
        value = getattr(holder, prop.identifier)
        if prop.type == "POINTER":
            value = value.as_pointer() if value is not None else None
        elif isinstance(value, set):
            value = tuple(sorted(value))
        settings.append((prop.identifier, _plain(value)))
    return tuple(settings)


def _publish(source, staged):
    source.data = staged


@dataclass(eq=False)
class MeshApplication:
    """Caller-owned rollback boundary. Call accept() or rollback() on the main thread.

    A visible Keep original object is user-owned once returned and survives both
    methods. Without Keep original, an unlinked holder retains the original mesh
    until this receipt is finalized. No destructor touches Blender data.
    """

    source: object
    original: object | None
    _scene: object = field(repr=False)
    _before: object = field(repr=False)
    _applied: object = field(repr=False)
    _holder: object = field(repr=False)
    _holder_state: tuple = field(repr=False)
    _before_active: int = field(repr=False)
    _applied_active: int = field(repr=False)
    _before_name: str = field(repr=False)
    _before_hash: bytes = field(repr=False)
    _applied_hash: bytes = field(repr=False)
    _allowed_mesh_properties: tuple = field(default=(), repr=False)
    _closed: bool = field(default=False, repr=False)

    def _release_holder(self):
        if self.original is not None or not _present(bpy.data.objects, self._holder):
            return False
        holder = self._holder
        # Any new ownership or settings retain the object for user review.
        if (
            not holder.users
            and not holder.users_collection
            and not holder.keys()
            and not holder.modifiers
            and not holder.constraints
            and not holder.vertex_groups
            and not holder.animation_data
            and not holder.children
            and not holder.rigid_body
            and not holder.rigid_body_constraint
            and all(slot.link == "DATA" for slot in holder.material_slots)
            and _holder_signature(holder) == self._holder_state
        ):
            bpy.data.objects.remove(holder)
            return True
        return False

    def _discard_unused_original(self):
        """Discard only the unchanged original after releasing our private holder."""
        mesh = self._before
        if not _present(bpy.data.meshes, mesh) or mesh.users:
            return
        if (
            mesh.name != self._before_name
            or mesh.keys()
            or mesh.animation_data
            or mesh.shape_keys
            or mesh.asset_data
        ):
            return
        try:
            unchanged = _fingerprint(mesh) == self._before_hash
        except MeshApplicationError:
            # New unsupported data or an exceeded work cap needs user review.
            return
        if unchanged:
            bpy.data.meshes.remove(mesh)

    def accept(self):
        """Release private rollback data; retain originals with user edits or owners."""
        _main_thread()
        if self._closed:
            raise MeshApplicationError("This application receipt is already finalized")
        if self._release_holder():
            self._discard_unused_original()
        self._closed = True

    def rollback(self):
        """Restore only if both mesh snapshots and the explicit target remain valid."""
        _main_thread()
        if self._closed:
            raise MeshApplicationError("This application receipt is already finalized")
        try:
            try:
                _validate_object(
                    self._scene,
                    self.source,
                    allowed_mesh_properties=self._allowed_mesh_properties,
                )
            except MeshApplicationError as exc:
                raise MeshApplicationError(
                    f"Target structure changed; rollback requires explicit review: {exc}"
                ) from exc
            if self.source.active_material_index != self._applied_active:
                raise MeshApplicationError("Active material selection changed after application")
            if self.source.data is not self._applied:
                raise MeshApplicationError("The target mesh was replaced after application")
            if (
                _fingerprint(self._applied) != self._applied_hash
                or _fingerprint(self._before) != self._before_hash
            ):
                raise MeshApplicationError("Mesh data changed; rollback requires explicit review")
        except ReferenceError:
            raise MeshApplicationError(
                "Application data was removed; rollback is unavailable"
            ) from None
        self.source.data = self._before
        self.source.active_material_index = self._before_active
        self._release_holder()
        # This is our staged copy only; never remove the imported result or a
        # mesh that another object/user has adopted since application.
        if not self._applied.users:
            bpy.data.meshes.remove(self._applied)
        self._closed = True


def apply_mesh(
    scene,
    source,
    result,
    *,
    policy,
    result_to_source,
    keep_original=False,
    allowed_mesh_properties=(),
):
    """Apply one explicit imported mesh in source-local coordinates.

    REMESH adopts result geometry, materials and UVs. UV preserves source mesh
    attributes/materials and replaces only its active UV coordinates, requiring
    exact indexed topology/positions and one result UV layer. RETEXTURE preserves
    geometry/non-UV attributes and adopts result UV layers/material assignments,
    also requiring exact topology/positions. Selection is unused.
    The caller must validate captured job origin immediately before invoking this
    synchronous primitive; it does not prove cloud provenance or import safety.
    """
    _main_thread()
    if policy not in {"REMESH", "UV", "RETEXTURE"} or type(keep_original) is not bool:
        raise MeshApplicationError(
            "Use an explicit REMESH/UV/RETEXTURE policy and boolean Keep original"
        )
    _validate_object(scene, source, allowed_mesh_properties=allowed_mesh_properties)
    _validate_object(scene, result)
    if source is result or source.data is result.data:
        raise MeshApplicationError("Use a separate imported result mesh")
    mapping = _matrix(result_to_source)
    _fingerprint(source.data)
    _fingerprint(result.data)
    staged = _stage(source.data, result.data, policy, mapping)
    return _replace_mesh(
        scene,
        source,
        staged,
        keep_original=keep_original,
        allowed_mesh_properties=allowed_mesh_properties,
    )


def _replace_mesh(scene, source, staged, *, keep_original, allowed_mesh_properties=()):
    """Publish an already validated private mesh; caller owns non-mesh changes."""
    before = source.data
    before_active = source.active_material_index
    before_hash = _fingerprint(before)
    holder = None
    try:
        staged.use_fake_user = False
        applied_hash = _fingerprint(staged)
        holder = (
            source.copy() if keep_original else bpy.data.objects.new("Scenario rollback", before)
        )
        holder.use_fake_user = False
        if keep_original:
            holder.name = f"{source.name} Original"
            for collection in source.users_collection:
                collection.objects.link(holder)
        holder_state = _holder_signature(holder) if not keep_original else ()
        _publish(source, staged)
        return MeshApplication(
            source,
            holder if keep_original else None,
            scene,
            before,
            staged,
            holder,
            holder_state,
            before_active,
            source.active_material_index,
            before.name,
            before_hash,
            applied_hash,
            tuple(allowed_mesh_properties),
        )
    except BaseException:
        if staged is not None and source.data is staged:
            source.data = before
            source.active_material_index = before_active
        if holder is not None:
            bpy.data.objects.remove(holder, do_unlink=True)
        if staged is not None and not staged.users:
            bpy.data.meshes.remove(staged)
        raise
