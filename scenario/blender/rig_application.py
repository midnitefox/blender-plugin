# SPDX-FileCopyrightText: 2026 Scenario Inc.
# SPDX-License-Identifier: GPL-3.0-or-later
"""Synchronous skin attachment to a captured static source; staged-import ownership."""

from dataclasses import dataclass

import bpy
from mathutils import Matrix

from . import mesh_application as mesh


def _weights(obj):
    if sum(len(vertex.groups) for vertex in obj.data.vertices) > mesh.MAX_COMPONENTS:
        raise mesh.MeshApplicationError("Skin weights exceed the synchronous component limit")
    return (
        tuple(group.name for group in obj.vertex_groups),
        tuple(tuple((group.group, group.weight) for group in v.groups) for v in obj.data.vertices),
    )


def validate_skin(target, primary, objects, mapping):
    """Require one compatible skin; never infer vertex correspondence or retarget."""
    mesh.validate_target(target)
    rigs = [obj for obj in objects if obj.type == "ARMATURE"]
    if len(rigs) != 1 or any(obj.type not in {"MESH", "ARMATURE", "EMPTY"} for obj in objects):
        raise mesh.MeshApplicationError("Rig application requires one armature and one mesh")
    rig = rigs[0]
    if (
        primary.animation_data
        or primary.data.animation_data
        or primary.data.shape_keys
        or primary.constraints
        or primary.children
        or len(primary.modifiers) != 1
    ):
        raise mesh.MeshApplicationError(
            "Mesh animation, morphs or extra modifiers require another policy"
        )
    rig_ancestors = set()
    ancestor = rig
    while ancestor is not None:
        rig_ancestors.add(ancestor)
        ancestor = ancestor.parent
    ancestor = primary.parent
    while ancestor is not None:
        if ancestor.animation_data and ancestor not in rig_ancestors:
            raise mesh.MeshApplicationError(
                "Independent mesh-parent animation requires another policy"
            )
        ancestor = ancestor.parent
    modifier = primary.modifiers[0]
    if (
        modifier.type != "ARMATURE"
        or modifier.object != rig
        or not modifier.use_vertex_groups
        or modifier.use_bone_envelopes
        or modifier.vertex_group
    ):
        raise mesh.MeshApplicationError("Use a vertex-weight skin bound to the single returned rig")
    if rig.constraints or rig.data.animation_data or any(b.constraints for b in rig.pose.bones):
        raise mesh.MeshApplicationError(
            "Rig constraints and armature-data animation are unsupported"
        )
    if any(obj.animation_data and obj.animation_data.drivers for obj in objects):
        raise mesh.MeshApplicationError("Rig application cannot adopt animation drivers")
    mesh._fingerprint(primary.data)
    if not mesh._same_topology(target.mesh, primary.data, mapping @ primary.matrix_world):
        raise mesh.MeshApplicationError(
            "Rig application requires identical indexed topology and positions"
        )
    names, weights = _weights(primary)
    if not names or len(rig.data.bones) > 1024 or any(name not in rig.data.bones for name in names):
        raise mesh.MeshApplicationError(
            "Use at most 1024 bones and only matching bone weight groups"
        )
    for vertex in weights:
        if not vertex or any(
            index >= len(names) or not 0 <= weight <= 1 for index, weight in vertex
        ):
            raise mesh.MeshApplicationError("Every source vertex needs valid bone weights")
        if abs(sum(weight for _, weight in vertex) - 1) > 0.001:
            raise mesh.MeshApplicationError("Bone weights must be normalized for every vertex")
    return rig, modifier, names, weights


def _stage_weights(target, names, weights):
    staged = target.mesh.copy()
    owner = bpy.data.objects.new("Scenario skin staging", staged)
    try:
        for name in names:
            owner.vertex_groups.new(name=name)
        for index, vertex in enumerate(weights):
            for group, weight in vertex:
                owner.vertex_groups[group].add([index], weight, "REPLACE")
        return staged
    except BaseException:
        bpy.data.objects.remove(owner)
        bpy.data.meshes.remove(staged)
        raise
    finally:
        if mesh._present(bpy.data.objects, owner):
            bpy.data.objects.remove(owner)


@dataclass
class RigApplication:
    """Internal synchronous rollback handle; finalized by the saved-result command."""

    receipt: mesh.MeshApplication
    modifier: object
    signature: tuple
    modifier_state: tuple
    objects: tuple
    rig: object

    @property
    def original(self):
        return self.receipt.original

    def accept(self):
        self.receipt.accept()

    def rollback(self):
        source = self.receipt.source
        if (
            source.data is not self.receipt._applied
            or tuple(source.modifiers) != (self.modifier,)
            or _weights(source) != self.signature
            or mesh._holder_signature(self.modifier) != self.modifier_state
        ):
            raise mesh.MeshApplicationError(
                "Applied skin changed; inspect the source before recovery"
            )
        source.modifiers.remove(self.modifier)
        source.vertex_groups.clear()
        self.receipt.rollback()


def apply_skin(target, primary, objects, mapping, *, keep_original):
    """Retain imported rig hierarchy; keep source geometry, materials and object context."""
    rig, imported_modifier, names, weights = validate_skin(target, primary, objects, mapping)
    staged = _stage_weights(target, names, weights)
    receipt = mesh._replace_mesh(
        target.scene,
        target.obj,
        staged,
        keep_original=keep_original,
        allowed_mesh_properties=target.allowed_mesh_properties,
    )
    modifier = None
    try:
        # Blender owns vertex-group names on the mesh; the staged copy already
        # carries the matching names and deform weights when assigned to source.
        if _weights(target.obj) != (names, weights):
            raise mesh.MeshApplicationError("Blender did not retain the staged skin weights")
        modifier = target.obj.modifiers.new("Scenario Rig", "ARMATURE")
        modifier.object = rig
        modifier.use_vertex_groups = True
        modifier.use_bone_envelopes = False
        modifier.use_deform_preserve_volume = imported_modifier.use_deform_preserve_volume
        retained = tuple(obj for obj in objects if obj != primary)
        root = bpy.data.objects.new(f"{target.name} Rig", None)
        root.parent = target.obj.parent
        root.matrix_parent_inverse = Matrix.Identity(4)
        for collection in target.obj.users_collection:
            collection.objects.link(root)
        root.matrix_world = target.obj.matrix_world @ mapping
        for obj in retained:
            if obj.parent is None:
                obj.parent = root
                obj.matrix_parent_inverse = Matrix.Identity(4)
            for collection in tuple(obj.users_collection):
                collection.objects.unlink(obj)
            for collection in target.obj.users_collection:
                collection.objects.link(obj)
            obj.select_set(False)
        bpy.context.view_layer.update()
        return RigApplication(
            receipt,
            modifier,
            _weights(target.obj),
            mesh._holder_signature(modifier),
            (root, *retained),
            rig,
        )
    except BaseException:
        if modifier is not None:
            target.obj.modifiers.remove(modifier)
        target.obj.vertex_groups.clear()
        receipt.rollback()
        raise
