# SPDX-FileCopyrightText: 2026 Scenario Inc.
# SPDX-License-Identifier: GPL-3.0-or-later
"""Export the selected mesh objects as one GLB for the Edit 3D lane. Main thread only."""

import logging
import time

import bpy

from . import runtime

log = logging.getLogger("scenario.mesh_export")


def export_dir():
    path = runtime.paths().cache_dir / "exports"
    path.mkdir(parents=True, exist_ok=True)
    return path


def source_objects(context):
    """The meshes to send: the selected mesh objects, else the active one if it is a mesh."""
    meshes = [o for o in context.selected_objects if o.type == "MESH"]
    if not meshes and context.active_object is not None and context.active_object.type == "MESH":
        meshes = [context.active_object]
    return meshes


def export_glb(context, objects, path=None, *, only_scene=False, current_frame=False):
    """Write `objects` to a GLB (materials and textures embedded, modifiers applied) and return the path.

    The glTF exporter is a bundled add-on; it is missing only in stripped builds, which we report instead of crashing."""
    if not objects:
        raise RuntimeError("Select the mesh to edit first")
    if not hasattr(bpy.ops.export_scene, "gltf"):
        raise RuntimeError("Blender's glTF exporter is not available")
    path = path or str(export_dir() / f"edit3d_{int(time.time() * 1000)}.glb")
    view_layer = context.view_layer
    previous = [o for o in context.selected_objects]
    active = view_layer.objects.active
    try:
        for obj in previous:
            obj.select_set(False)
        for obj in objects:
            obj.select_set(True)
        view_layer.objects.active = objects[0]
        bpy.ops.export_scene.gltf(
            filepath=path,
            export_format="GLB",
            use_selection=True,
            use_active_scene=only_scene,
            export_current_frame=current_frame,
            export_apply=True,
            export_yup=True,
            export_animations=False,
            export_texcoords=True,
            export_normals=True,
            export_materials="EXPORT",
        )
    finally:
        for obj in objects:
            obj.select_set(False)
        for obj in previous:
            try:
                obj.select_set(True)
            except ReferenceError:
                pass
        if active is not None:
            try:
                view_layer.objects.active = active
            except ReferenceError:
                pass
    log.info("exported %d object(s) to %s", len(objects), path)
    return path
