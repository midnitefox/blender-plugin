# SPDX-FileCopyrightText: 2026 Open Studio contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Static evaluated/reference GLBs and object-local edit copies in disposable scenes."""

import math

import bpy
from mathutils import Matrix

from . import file_state, mesh_application, mesh_export, mesh_export_fingerprint, model_application


def affine(value):
    """Validate a source transform without granting a reflected edit mapping."""
    matrix = Matrix(value)
    if any(not math.isfinite(c) for row in matrix for c in row) or tuple(matrix[3]) != (0, 0, 0, 1):
        raise ValueError("The source transform must be finite and affine")
    return matrix


def sources(context, objects):
    """Reference provenance, deliberately separate from the stricter live edit guard."""
    if context.mode != "OBJECT" or context.scene.library or context.scene.override_library:
        raise ValueError("Export meshes in Object Mode from the current local scene")
    if any(
        obj is None or obj.type != "MESH" or obj.name not in context.scene.objects
        for obj in objects
    ):
        raise ValueError("Name existing mesh objects in the current scene")
    context.view_layer.update()
    fingerprints = mesh_export_fingerprint.fingerprints(obj.data for obj in objects)
    return (
        context.scene,
        context.scene.frame_current,
        context.scene.frame_subframe,
        tuple(
            (
                obj,
                obj.data,
                obj.name,
                obj.data.name,
                digest,
                tuple(tuple(row) for row in affine(obj.matrix_world)),
                obj.parent,
                frozenset(obj.users_collection),
                tuple((slot.link, slot.material) for slot in obj.material_slots),
            )
            for obj, digest in zip(objects, fingerprints, strict=True)
        ),
    )


def _world_geometry(mesh, matrix):
    """Bake an evaluated reference, preserving corner UVs and outward shading normals."""
    if matrix.determinant() == 0:
        raise ValueError("Use LOCAL coordinates for a singular source transform")
    normal_matrix = matrix.inverted().transposed().to_3x3()
    # Polygon/vertex ownership lets a winding reversal retain the normal at the
    # same surface corner, rather than applying normals in the old loop order.
    normals = []
    for polygon in mesh.polygons:
        corners = {}
        for index in polygon.loop_indices:
            vertex = mesh.loops[index].vertex_index
            if vertex in corners:
                raise ValueError("Reference faces must not repeat a vertex")
            vector = normal_matrix @ mesh.corner_normals[index].vector
            if not all(math.isfinite(c) for c in vector):
                raise ValueError("Reference normals must be finite")
            corners[vertex] = vector.normalized()
        normals.append(corners)
    mesh.transform(matrix)
    if matrix.determinant() < 0:
        mesh.flip_normals()
    mesh.update()
    values = [None] * len(mesh.loops)
    for polygon, corners in zip(mesh.polygons, normals, strict=True):
        for index in polygon.loop_indices:
            values[index] = corners[mesh.loops[index].vertex_index]
    mesh.normals_split_custom_set(values)


def export(context, objects, path, *, evaluated, coordinates):
    """Export owned static copies; restore the exact scene and remove all staging data."""
    before = model_application._snapshot()
    graph = context.evaluated_depsgraph_get() if evaluated else None
    copies, stats, components = [], [], 0
    try:
        for obj in objects:
            matrix = affine(obj.matrix_world)
            visible = obj.evaluated_get(graph) if evaluated else obj
            mesh = (
                bpy.data.meshes.new_from_object(
                    visible, preserve_all_data_layers=True, depsgraph=graph
                )
                if evaluated
                else obj.data.copy()
            )
            if not mesh.vertices or not mesh.polygons:
                raise ValueError("The evaluated source must contain realized surface mesh geometry")
            components += (
                len(mesh.vertices) + len(mesh.edges) + len(mesh.polygons) + len(mesh.loops)
            )
            components += sum(len(attribute.data) for attribute in mesh.attributes)
            if components > mesh_application.MAX_COMPONENTS:
                raise ValueError("Reference meshes exceed the synchronous component limit")
            if any(not math.isfinite(c) for vertex in mesh.vertices for c in vertex.co):
                raise ValueError("Reference mesh positions must be finite")
            # new_from_object may preserve deform groups as mesh attributes, but
            # this object owns no skeleton, modifiers, groups or shape keys.
            mesh.use_fake_user = False
            # Static objects have no OBJECT-linked overrides. Resolve the
            # evaluated object's visible slots into the private mesh instead.
            mesh.materials.clear()
            for slot in visible.material_slots:
                mesh.materials.append(slot.material)
            if coordinates == "WORLD":
                _world_geometry(mesh, matrix)
            copy = bpy.data.objects.new(obj.name + " snapshot", mesh)
            copies.append(copy)
            stats.append(
                {
                    "object": obj.name,
                    "vertices": len(mesh.vertices),
                    "polygons": len(mesh.polygons),
                    "source_reflected": matrix.determinant() < 0,
                }
            )

        def run():
            for copy in copies:
                bpy.context.scene.collection.objects.link(copy)
            bpy.context.view_layer.update()
            mesh_export.export_glb(bpy.context, copies, path, only_scene=True, current_frame=True)

        model_application._stage(before, run)
        return stats
    finally:
        model_application._remove_new_data(before)
        if model_application._snapshot() != before:
            file_state.mark_modified("Open Studio mesh export cleanup needs inspection")
            raise RuntimeError("Mesh export cleanup is incomplete; inspect the scene")
