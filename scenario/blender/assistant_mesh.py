# SPDX-FileCopyrightText: 2026 Open Studio contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Session-bound local mesh exports and explicit returned-GLB edit policies."""

import logging
import tempfile
import uuid
from dataclasses import dataclass
from pathlib import Path

import bpy
from bpy.app.handlers import persistent

from ..core.agent_artifacts import read_inbox_file
from ..core.jobs.local_render import blender_path
from ..core.jobs.store import ResultAsset, StoredResult
from ..core.jobs.transfers import DownloadedResult, _root
from ..core.scene.glb import MAX_GLB_BYTES, inspect_glb
from . import assistant_blockout as blockout
from . import file_state, mesh_application, mesh_export, mesh_result_application, runtime

_log = logging.getLogger("open_studio.mesh")
_exports = {}
MAX_EXPORTS = 64


@dataclass
class MeshExport:
    target: object
    directory: object
    path: Path
    sha256: str

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


def export_mesh(object_name):
    """Export exactly this named supported mesh and retain its live target identity."""
    mesh_application._main_thread()
    if not isinstance(object_name, str) or not object_name:
        raise ValueError("Name the exact mesh in the current scene")
    scene = bpy.context.scene
    obj = scene.objects.get(object_name)
    target = mesh_application.capture_target(
        scene, obj, allowed_mesh_properties=_owned_mesh_properties(scene, obj)
    )
    # Reject singular/reflected transforms rather than returning an invalid
    # scene-to-source mapping for an otherwise successful export.
    inverse = mesh_application._matrix(obj.matrix_world).inverted()
    root = runtime.paths().state_dir / "assistant-mesh-exports"
    if root.is_symlink():
        raise ValueError("Mesh exports require a private directory")
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    root = _root(root)
    directory = tempfile.TemporaryDirectory(prefix="mesh-", dir=root)
    try:
        path = Path(directory.name) / "source.glb"
        mesh_export.export_glb(bpy.context, [obj], blender_path(path))
        mesh_application.validate_target(target)
        snapshot = read_inbox_file(
            Path(directory.name), path.name, suffixes={".glb"}, max_bytes=MAX_GLB_BYTES
        )
        inspect_glb(snapshot.data, static_only=True)
        # One pending export per target; re-exporting explicitly supersedes its
        # previous handle. Bound the other retained temporary files as well.
        for handle, existing in tuple(_exports.items()):
            if existing.target.obj == obj:
                _drop(handle)
        if len(_exports) >= MAX_EXPORTS:
            _drop(next(iter(_exports)))
        handle = uuid.uuid4().hex
        _exports[handle] = MeshExport(target, directory, path, snapshot.sha256)
        return {
            "handle": handle,
            "object": obj.name,
            "path": blender_path(path),
            "sha256": snapshot.sha256,
            "bytes": len(snapshot.data),
            "format": "GLB",
            "source_world_to_local": [list(row) for row in inverse],
            "notes": (
                "Use your assistant's mesh tools to edit this snapshot, then copy the returned "
                "self-contained GLB into agent_workspace's inbox. Keep the target geometry, UVs, "
                "material bindings, name, transforms, parenting and collection membership unchanged. "
                "The matrix is suitable only if the result preserves the exported scene coordinates "
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
