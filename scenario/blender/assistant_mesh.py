# SPDX-FileCopyrightText: 2026 Open Studio contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Session-bound local mesh exports and explicit returned-GLB edit policies."""

import json
import logging
import tempfile
import uuid
from dataclasses import dataclass
from pathlib import Path

import bpy
from bpy.app.handlers import persistent
from mathutils import Matrix

from ..core.agent_artifacts import read_inbox_file
from ..core.jobs.local_render import blender_path
from ..core.jobs.store import ResultAsset, StoredResult
from ..core.jobs.transfers import DownloadedResult, _root
from ..core.scene.glb import MAX_GLB_BYTES, inspect_glb
from . import assistant_blockout as blockout
from . import (
    assistant_mesh_snapshot,
    file_state,
    mesh_application,
    mesh_export,
    mesh_result_application,
    runtime,
)

_log = logging.getLogger("open_studio.mesh")
_exports = {}
MAX_EXPORTS = 64


@dataclass
class MeshExport:
    target: object
    directory: object
    path: Path
    sha256: str
    sources: tuple
    mode: str
    coordinates: str

    def close(self):
        try:
            self.directory.cleanup()
        except OSError:
            _log.warning("An assistant mesh export temporary file could not be removed")


def _drop(handle):
    entry = _exports.pop(handle, None)
    if entry is not None:
        entry.close()


def _owned_mesh_properties(scene, obj):
    """Opt in only the marker on a member of this scene's registered blockout."""
    if obj is None or obj.type != "MESH":
        return ()
    owner = scene.get(blockout.STATE)
    if (
        not isinstance(owner, str)
        or not owner
        or obj.get(blockout.OWNER) != owner
        or obj.data.get(blockout.OWNER) != owner
        or not isinstance(obj.get(blockout.ELEMENT), str)
    ):
        return ()
    root = blockout._root(scene)
    if root is None or obj.name not in root.all_objects:
        return ()
    return ((blockout.OWNER, owner),)


def export_mesh(object_name=None, *, object_names=None, mode="EDIT", coordinates="WORLD"):
    """Separate evaluated references from guarded edits and explicit coordinate frames."""
    mesh_application._main_thread()
    if mode not in {"EDIT", "REFERENCE"} or coordinates not in {"WORLD", "LOCAL"}:
        raise ValueError("Choose EDIT or REFERENCE mode and WORLD or LOCAL coordinates")
    if object_names is not None:
        if object_name is not None or mode != "REFERENCE" or coordinates != "WORLD":
            raise ValueError("object_names is only for REFERENCE mode with WORLD coordinates")
        if (
            not isinstance(object_names, list)
            or not 1 <= len(object_names) <= 64
            or any(
                not isinstance(name, str) or not name or len(name) > 255 for name in object_names
            )
            or len(set(object_names)) != len(object_names)
        ):
            raise ValueError("Name between one and 64 distinct reference meshes")
        names = object_names
    else:
        if not isinstance(object_name, str) or not object_name or len(object_name) > 255:
            raise ValueError("Name the exact mesh in the current scene")
        names = [object_name]
    scene = bpy.context.scene
    objects = tuple(scene.objects.get(name) for name in names)
    provenance = assistant_mesh_snapshot.sources(bpy.context, objects)
    obj = objects[0]
    target = (
        mesh_application.capture_target(
            scene, obj, allowed_mesh_properties=_owned_mesh_properties(scene, obj)
        )
        if mode == "EDIT"
        else None
    )
    matrix = assistant_mesh_snapshot.affine(obj.matrix_world)
    try:
        inverse = matrix.inverted()
    except ValueError:
        inverse = None
    if mode == "EDIT" and coordinates == "WORLD":
        try:
            mesh_application._matrix(matrix)
        except mesh_application.MeshApplicationError:
            raise ValueError(
                "Use LOCAL coordinates for a mirrored or singular source; WORLD edit mappings cannot reflect"
            ) from None
        if inverse is None:
            raise ValueError("Use LOCAL coordinates for a source transform that cannot be inverted")
    mapping = (
        (Matrix.Identity(4) if coordinates == "LOCAL" else inverse) if mode == "EDIT" else None
    )
    root = runtime.paths().state_dir / "assistant-mesh-exports"
    if root.is_symlink():
        raise ValueError("Mesh exports require a private directory")
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    root = _root(root)
    directory = tempfile.TemporaryDirectory(prefix="mesh-", dir=root)
    try:
        path = Path(directory.name) / "source.glb"
        if mode == "EDIT" and coordinates == "WORLD":
            mesh_export.export_glb(
                bpy.context, [obj], blender_path(path), only_scene=True, current_frame=True
            )
            stats = [
                {
                    "object": obj.name,
                    "vertices": len(obj.data.vertices),
                    "polygons": len(obj.data.polygons),
                    "source_reflected": False,
                }
            ]
        else:
            stats = assistant_mesh_snapshot.export(
                bpy.context,
                objects,
                blender_path(path),
                evaluated=mode == "REFERENCE",
                coordinates=coordinates,
            )
        if assistant_mesh_snapshot.sources(bpy.context, objects) != provenance:
            raise ValueError("Source mesh changed during export; capture it again")
        if target is not None:
            mesh_application.validate_target(target)
        snapshot = read_inbox_file(
            Path(directory.name), path.name, suffixes={".glb"}, max_bytes=MAX_GLB_BYTES
        )
        inspect_glb(snapshot.data, static_only=True)
        manifest = {
            "format": "open-studio-mesh-export-v1",
            "mode": mode,
            "coordinates": coordinates,
            "application_available": mode == "EDIT",
            "evaluated": mode == "REFERENCE",
            "frame": scene.frame_current,
            "subframe": scene.frame_subframe,
            "file": path.name,
            "sha256": snapshot.sha256,
            "sources": [
                {
                    "object": source.name,
                    "mesh": source.data.name,
                    "matrix_world": [list(row) for row in source.matrix_world],
                }
                for source in objects
            ],
            "geometry": stats,
            "result_to_source": [list(row) for row in mapping] if mapping is not None else None,
            "reference_policy": "Static current-frame geometry; no skin, clips or animation retargeting",
        }
        manifest_path = path.with_suffix(".json")
        manifest_path.write_text(json.dumps(manifest, indent=2, allow_nan=False), encoding="utf-8")
        # One pending export per target; re-exporting explicitly supersedes its
        # previous handle. Bound the other retained temporary files as well.
        for handle, existing in tuple(_exports.items()):
            if existing.sources == objects:
                _drop(handle)
        if len(_exports) >= MAX_EXPORTS:
            _drop(next(iter(_exports)))
        handle = uuid.uuid4().hex
        _exports[handle] = MeshExport(
            target, directory, path, snapshot.sha256, objects, mode, coordinates
        )
        return {
            "export_id": handle,
            "handle": handle if mode == "EDIT" else None,
            "mode": mode,
            "coordinates": coordinates,
            "application_available": mode == "EDIT",
            "evaluated": mode == "REFERENCE",
            "object": obj.name if len(objects) == 1 else None,
            "objects": [source.name for source in objects],
            "path": blender_path(path),
            "sha256": snapshot.sha256,
            "bytes": len(snapshot.data),
            "format": "GLB",
            "manifest_path": blender_path(manifest_path),
            "frame": scene.frame_current,
            "subframe": scene.frame_subframe,
            "geometry": stats,
            "source_world_to_local": [list(row) for row in inverse]
            if inverse is not None and len(objects) == 1
            else None,
            "result_to_source": [list(row) for row in mapping] if mapping is not None else None,
            "notes": (
                "REFERENCE is a static evaluated snapshot, with modifiers, current rig pose and shape keys baked. "
                "It grants no edit handle. Use it as an assistant reference and import returned assets separately. "
                "LOCAL exports source-local geometry; source reflections remain on the unchanged source object. "
                "The export mode and coordinate frame are also saved in the adjacent manifest. "
                "Temporary references expire on re-export, file load, Undo/Redo, extension disable or eviction."
            )
            if mode == "REFERENCE"
            else (
                "Use your assistant's mesh tools to edit this snapshot, then copy the returned "
                "self-contained GLB into agent_workspace's inbox. Keep the target geometry, UVs, "
                "material bindings, name, transforms, parenting and collection membership unchanged. "
                "result_to_source is suitable only if the result preserves this export's explicit coordinate frame "
                "after glTF-to-Blender axis conversion; inspect the result before applying. "
                "The handle expires after application, re-export of this mesh, file load, Undo/Redo "
                "or extension disable. Up to 64 pending exports are retained; the oldest then expires."
            ),
        }
    except BaseException:
        directory.cleanup()
        raise


def apply_mesh(args, inbox):
    """Consume a current source handle only after a known success or uncertainty."""
    mesh_application._main_thread()
    handle = args.get("handle")
    entry = _exports.get(handle) if isinstance(handle, str) else None
    if entry is None:
        raise ValueError("Export the named mesh again to obtain a current mesh-edit handle")
    if entry.mode != "EDIT" or entry.target is None:
        raise ValueError(
            "REFERENCE exports grant no edit authority; import the returned asset separately"
        )
    policy = args.get("policy")
    mapping = args.get("result_to_source")
    keep_original = args.get("keep_original")
    mesh_result_application.validate_request(
        entry.target, policy=policy, result_to_source=mapping, keep_original=keep_original
    )
    snapshot = read_inbox_file(
        inbox,
        args.get("filename", ""),
        args.get("sha256"),
        suffixes={".glb"},
        max_bytes=MAX_GLB_BYTES,
    )
    inspect_glb(snapshot.data, static_only=policy != "RIG")
    directory = tempfile.TemporaryDirectory(prefix="mesh-result-", dir=inbox)
    try:
        path = Path(directory.name) / "result.glb"
        path.write_bytes(snapshot.data)
        item = StoredResult(
            ResultAsset(
                "assistant-" + snapshot.sha256,
                path.name,
                "model/gltf-binary",
                len(snapshot.data),
                snapshot.sha256,
            ),
            DownloadedResult(path.name, len(snapshot.data), snapshot.sha256),
        )
        try:
            result = mesh_result_application.apply_saved_mesh(
                entry.target,
                item,
                path,
                policy=policy,
                result_to_source=mapping,
                keep_original=keep_original,
            )
        except (
            mesh_result_application.MeshResultApplicationError,
            mesh_application.MeshApplicationError,
        ):
            # These exceptions establish either preflight refusal or verified
            # rollback. The same unchanged source may accept a corrected file.
            raise
        except Exception:
            _drop(handle)
            file_state.mark_modified("Open Studio mesh edit needs inspection")
            raise
        _drop(handle)
        if not result.undo_available:
            file_state.mark_modified("Open Studio assistant mesh edit")
        return {
            "object": result.source.name,
            "policy": result.policy,
            "export_mode": entry.mode,
            "export_coordinates": entry.coordinates,
            "sha256": snapshot.sha256,
            "original": result.original.name if result.original is not None else None,
            "parts": [obj.name for obj in result.parts],
            "rig": result.rig.name if result.rig is not None else None,
            "undo_available": result.undo_available,
            "handle_consumed": True,
            "restore_available": False,
            "notes": (
                "The edit is applied; do not repeat the call. Save the blend file to retain it. "
                "Keep original retains a separate unchanged mesh object when requested. "
                "Desktop Undo/Redo is available only when reported; it changes scene data and "
                "expires all pending export handles. No separate restore command is provided. "
                "Shader graphs of existing materials are never reverted by this operation."
            ),
        }
    finally:
        try:
            directory.cleanup()
        except OSError:
            _log.warning("An assistant mesh result temporary file could not be removed")


@persistent
def _clear(*_):
    for handle in tuple(_exports):
        _drop(handle)


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
