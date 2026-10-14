# SPDX-FileCopyrightText: 2026 Open Studio contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Bounded inspection and explicit scene activation for local film work."""

import itertools
import json

import bpy

from . import file_state

KIND = "open_studio_film_kind"
IDENTITY = "open_studio_film_id"
RECIPE = "open_studio_film_recipe"
MAX_SCENES = 256
MAX_STRIPS = 2048
MAX_RECIPE_BYTES = 2 * 1024 * 1024


def _identity(scene):
    value = scene.get(IDENTITY)
    return value if isinstance(value, str) and len(value) <= 64 else None


def target(scene_name, scene_id=None):
    if not isinstance(scene_name, str) or not scene_name or len(scene_name) > 255:
        raise ValueError("scene_name must identify an existing Blender scene")
    scene = bpy.data.scenes.get(scene_name)
    if scene is None:
        raise ValueError("The requested scene no longer exists; inspect_agent_film again")
    if scene_id is not None:
        if not isinstance(scene_id, str) or not scene_id or len(scene_id) > 64:
            raise ValueError("scene_id must be the identity returned for this scene")
        matches = [item for item in bpy.data.scenes if _identity(item) == scene_id]
        if _identity(scene) != scene_id or len(matches) != 1:
            raise ValueError(
                "The scene identity changed or is duplicated; inspect_agent_film again"
            )
    return scene


def _summary(scene):
    kind = scene.get(KIND)
    identity = _identity(scene)
    return {
        "scene_name": scene.name,
        "scene_id": identity,
        "identity_unique": bool(identity)
        and sum(_identity(item) == identity for item in bpy.data.scenes) == 1,
        "kind": kind
        if isinstance(kind, str) and kind in {"shot", "timeline", "review"}
        else "scene",
        "active": scene == bpy.context.scene,
        "local": scene.library is None and scene.override_library is None,
        "camera": scene.camera.name if scene.camera else None,
        "frame_start": scene.frame_start,
        "frame_end": scene.frame_end,
        "frame_current": scene.frame_current,
        "fps": scene.render.fps / (scene.render.fps_base or 1),
        "width": scene.render.resolution_x,
        "height": scene.render.resolution_y,
        "resolution_percentage": scene.render.resolution_percentage,
        "objects": len(scene.objects),
        "render_engine": scene.render.engine,
        "use_sequencer": scene.render.use_sequencer,
    }


def _strip(strip):
    result = {
        "name": strip.name,
        "type": strip.type,
        "channel": strip.channel,
        "start_frame": strip.frame_final_start,
        "end_frame_exclusive": strip.frame_final_end,
        "frame_count": strip.frame_final_duration,
        "muted": strip.mute,
    }
    if strip.type == "SCENE" and strip.scene:
        result.update(
            scene_name=strip.scene.name,
            scene_id=_identity(strip.scene),
            scene_input=strip.scene_input,
            source_frame_offset=strip.frame_offset_start,
        )
    elif strip.type == "SOUND" and strip.sound:
        result.update(
            filepath=strip.sound.filepath,
            packed=bool(strip.sound.packed_file),
            volume=strip.volume,
        )
    elif strip.type == "MOVIE":
        result.update(filepath=strip.filepath, source_fps=strip.fps)
    elif strip.type == "IMAGE":
        result.update(
            directory=strip.directory,
            images=[item.filename for item in itertools.islice(strip.elements, 16)],
            image_count=len(strip.elements),
        )
    return result


def inspect(scene_name=None, scene_id=None, include_recipe=False):
    if not isinstance(include_recipe, bool):
        raise ValueError("include_recipe must be true or false")
    if scene_name is None:
        if scene_id is not None:
            raise ValueError("scene_id requires scene_name")
        return {
            "active_scene": bpy.context.scene.name,
            "scenes": [_summary(scene) for scene in itertools.islice(bpy.data.scenes, MAX_SCENES)],
            "scene_count": len(bpy.data.scenes),
            "truncated": len(bpy.data.scenes) > MAX_SCENES,
        }
    scene = target(scene_name, scene_id)
    result = _summary(scene)
    strips = scene.sequence_editor.strips_all if scene.sequence_editor else ()
    result.update(
        strips=[_strip(strip) for strip in itertools.islice(strips, MAX_STRIPS)],
        strip_count=len(strips),
        strips_truncated=len(strips) > MAX_STRIPS,
        recipe_role="Original build inputs. Current editable Blender contents are authoritative.",
    )
    if include_recipe:
        raw = scene.get(RECIPE)
        result["recipe"] = None
        if isinstance(raw, str) and len(raw.encode("utf-8")) <= MAX_RECIPE_BYTES:
            try:
                result["recipe"] = json.loads(raw)
            except (ValueError, RecursionError):
                result["recipe_error"] = "The saved build recipe is malformed"
        elif raw is not None:
            result["recipe_error"] = "The saved build recipe has an unsupported type or size"
    return result


def activate(scene_name, scene_id=None):
    scene = target(scene_name, scene_id)
    window = bpy.context.window
    if window is None:
        raise ValueError("An active Blender window is required to switch scenes")
    if scene.library is not None or scene.override_library is not None:
        raise ValueError("Switch to a local scene for assistant film work")
    if bpy.context.mode != "OBJECT":
        raise ValueError("Return to Object Mode before switching scenes")
    previous = window.scene.name
    changed = window.scene != scene
    if changed:
        window.scene = scene
        file_state.mark_modified("Open Studio activate scene")
    return dict(_summary(scene), previous_scene=previous, changed=changed)
