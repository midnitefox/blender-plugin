# SPDX-FileCopyrightText: 2026 Scenario Inc.
# SPDX-License-Identifier: GPL-3.0-or-later
"""Apply an explicitly chosen GLB mesh, skin or parts policy to a captured source."""

import logging
from dataclasses import dataclass, field

import bpy
from mathutils import Matrix

from . import mesh_application, model_application, rig_application
from .model_application import _undo_enabled, _undo_push

_log = logging.getLogger("scenario.jobs")


class MeshResultApplicationError(RuntimeError):
    """Rejected before mutation, or all scene changes were verifiably rolled back."""


@dataclass(frozen=True)
class MeshEditApplication:
    source: object = field(repr=False)
    original: object = field(repr=False)
    policy: str
    undo_available: bool = False
    parts: tuple = field(default=(), repr=False)
    rig: object = field(default=None, repr=False)


def validate_request(target, *, policy, result_to_source, keep_original):
    """Validate the captured source and explicit policy without decoding files."""
    mesh_application.validate_target(target)
    if (
        policy not in {"REMESH", "UV", "RETEXTURE", "PARTS", "RIG"}
        or type(keep_original) is not bool
    ):
        raise MeshResultApplicationError(
            "Choose REMESH, UV, RETEXTURE, PARTS or RIG and an explicit Keep original value"
        )
    if policy == "PARTS" and (not target.mesh.vertices or not target.mesh.polygons):
        raise MeshResultApplicationError(
            "Choose a source with surface geometry, not a parts anchor"
        )
    return mesh_application._matrix(result_to_source)


def _release_import(imported, objects, staging):
    # Remove only imported objects, never the source or its Keep original copy.
    for obj in objects:
        bpy.data.objects.remove(obj, do_unlink=True)
    bpy.data.scenes.remove(staging)
    # The replacement mesh retains any material/image dependencies it needs.
    for name in model_application._DATA:
        if name in {"objects", "scenes"}:
            continue
        values = getattr(bpy.data, name)
        for value in imported[name]:
            if not value.users and not value.use_fake_user:
                values.remove(value)


def _stage_parts(target, meshes, mapping):
    """Copy static part geometry into source-local coordinates before replacement."""
    if not 2 <= len(meshes) <= 128:
        raise MeshResultApplicationError("Choose a static GLB containing 2 to 128 mesh parts")
    components = 0
    for obj in meshes:
        mesh_application._validate_object(obj.users_scene[0], obj)
        data = obj.data
        components += len(data.vertices) + len(data.edges) + len(data.loops) + len(data.polygons)
        components += sum(len(attribute.data) for attribute in data.attributes)
        if components > mesh_application.MAX_COMPONENTS:
            raise MeshResultApplicationError("Parts exceed the synchronous component limit")
        if not data.vertices or not data.polygons:
            raise MeshResultApplicationError("Each part must contain surface geometry")
        mesh_application._fingerprint(data)
        mesh_application._matrix(mapping @ obj.matrix_world)
    parts = []
    for index, obj in enumerate(meshes, 1):
        mesh = obj.data.copy()
        mesh.use_fake_user = False
        mesh.transform(mapping @ obj.matrix_world)
        mesh.update()
        mesh_application._fingerprint(mesh)
        name = f"{target.name} Part {index:03d} {obj.name}"
        part = bpy.data.objects.new(name, mesh)
        parts.append(part)
    # Reuse the guarded mesh-swap primitive with an empty result. The original
    # object becomes the group anchor; existing children/context are unchanged.
    blank = bpy.data.objects.new("Scenario parts staging", bpy.data.meshes.new("Scenario parts"))
    target.scene.collection.objects.link(blank)
    return tuple(parts), blank


def _publish_parts(target, parts):
    for part in parts:
        part.parent = target.obj
        part.matrix_parent_inverse = Matrix.Identity(4)
        part.matrix_basis = Matrix.Identity(4)
        for collection in target.obj.users_collection:
            collection.objects.link(part)
        part.select_set(False)
    bpy.context.view_layer.update()


def apply_saved_mesh(target, item, path, *, policy, result_to_source, keep_original):
    """Use an explicit GLB-scene-to-source-local mapping; never fit/guess alignment."""
    mapping = validate_request(
        target, policy=policy, result_to_source=result_to_source, keep_original=keep_original
    )
    undo = _undo_enabled()
    if undo:
        # Capture the latest user state before importing temporary datablocks.
        # A missing pre-state must stop replacement while the source is intact.
        try:
            _undo_push("Before Scenario mesh edit")
        except Exception:
            raise MeshResultApplicationError(
                "Could not prepare Blender undo; source is unchanged"
            ) from None
    before = model_application._snapshot()
    receipt = None
    try:
        with model_application.staged_model(item, path, before, static_only=policy != "RIG") as (
            staging,
            objects,
        ):
            meshes = [obj for obj in objects if obj.type == "MESH"]
            if policy != "RIG" and any(obj.type not in {"MESH", "EMPTY"} for obj in objects):
                raise MeshResultApplicationError("Choose static meshes with optional Empty parents")
            parts = ()
            rig = None
            if policy == "PARTS":
                parts, primary = _stage_parts(target, meshes, mapping)
                objects = (*objects, primary)
                local_mapping = Matrix.Identity(4)
            else:
                if len(meshes) != 1:
                    raise MeshResultApplicationError("Choose one primary mesh without variants")
                primary = meshes[0]
                # Blender has already converted GLB axes. Preserve imported node
                # transforms; never infer alignment or move the source object.
                local_mapping = mapping @ primary.matrix_world
                target.scene.collection.objects.link(primary)
            imported = {
                name: set(getattr(bpy.data, name)) - previous for name, previous in before.items()
            }
            mesh_application.validate_target(target)
            if policy == "RIG":
                receipt = rig_application.apply_skin(
                    target, primary, objects, mapping, keep_original=keep_original
                )
                rig = receipt.rig
                objects = tuple(obj for obj in objects if obj not in receipt.objects)
            else:
                receipt = mesh_application.apply_mesh(
                    target.scene,
                    target.obj,
                    primary,
                    policy="REMESH" if policy == "PARTS" else policy,
                    result_to_source=local_mapping,
                    keep_original=keep_original,
                    allowed_mesh_properties=target.allowed_mesh_properties,
                )
            if parts:
                _publish_parts(target, parts)
            if receipt.original is not None:
                receipt.original.select_set(False)
            _release_import(imported, objects, staging)
            receipt.accept()
            if undo:
                try:
                    # The history state must contain no staging scene/private holder.
                    _undo_push("Scenario mesh edit")
                except Exception:
                    # The edit is complete. A missing history entry is not a failed
                    # application and must never authorize another replacement.
                    undo = False
                    _log.warning("Mesh applied, but Blender undo could not be recorded")
            return MeshEditApplication(target.obj, receipt.original, policy, undo, parts, rig)
    except Exception:
        if receipt is not None:
            try:
                receipt.rollback()
            except Exception:
                raise RuntimeError("Mesh replacement is uncertain; inspect the source") from None
        # Never remove new data while the source may still point at it.
        try:
            mesh_application.validate_target(target)
        except Exception:
            raise RuntimeError("Mesh source changed during application; inspect it") from None
        model_application._remove_new_data(before)
        if model_application._snapshot() != before:
            raise RuntimeError("Mesh cleanup is incomplete; do not apply again") from None
        raise MeshResultApplicationError(
            "Could not apply the verified mesh; source and saved files remain"
        ) from None
