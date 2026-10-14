# SPDX-FileCopyrightText: 2026 Open Studio contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Offline assistant timeline renderer; never registers the extension."""

import importlib.util
from pathlib import Path


def _mute_file_outputs(scene):
    """Compositing may affect the picture but must not write auxiliary files."""
    tree = getattr(scene, "compositing_node_group", None) or getattr(scene, "node_tree", None)
    pending = [tree] if tree else []
    visited = set()
    while pending:
        tree = pending.pop()
        if tree in visited:
            continue
        visited.add(tree)
        if len(visited) > 1024:
            raise ValueError("Compositor node nesting exceeds the local export limit")
        for node in tree.nodes:
            if node.bl_idname == "CompositorNodeOutputFile":
                node.mute = True
            nested = getattr(node, "node_tree", None)
            if nested:
                pending.append(nested)


def main():
    import bpy

    module_spec = importlib.util.spec_from_file_location(
        "open_studio_export_worker", Path(__file__).with_name("film_export_worker.py")
    )
    worker = importlib.util.module_from_spec(module_spec)
    module_spec.loader.exec_module(worker)
    path, spec = worker._parameters()
    if spec["mode"] == "verify":
        worker._verify(bpy, path, spec)
        return
    root = bpy.data.scenes.get(spec["scene_name"])
    visited = set()

    def inspect(scene, ancestors):
        if scene is None or scene.library or scene in ancestors or len(ancestors) >= 16:
            raise ValueError("Use local scene strips without recursive scene references")
        if scene in visited:
            return
        visited.add(scene)
        if len(visited) > 256:
            raise ValueError("Render at most 256 scenes")
        editor = scene.sequence_editor
        for strip in tuple(editor.strips_all) if editor else ():
            if strip.type in {"MOVIECLIP", "MASK"}:
                raise ValueError("Movie clips and mask strips require their own export policy")
            if strip.type == "SCENE":
                child = strip.scene
                if child is None or (
                    strip.scene_input == "CAMERA" and (strip.scene_camera or child.camera) is None
                ):
                    raise ValueError("Scene strips need their selected source and camera")
                inspect(child, {*ancestors, scene})
        # These are private snapshot scenes. Rendering at the chosen output size
        # must not accidentally render a source shot at an unrelated 8K setting.
        scene.render.resolution_x, scene.render.resolution_y = spec["width"], spec["height"]
        scene.render.resolution_percentage = 100
        _mute_file_outputs(scene)

    inspect(root, set())
    # The shared worker remains review-only for every other caller. In this
    # isolated process the assistant has validated the full scene dependencies.
    worker.BLOCKED_STRIPS = frozenset({"MOVIECLIP", "MASK"})
    worker._render(bpy, path, spec)


if __name__ == "__main__":
    main()
