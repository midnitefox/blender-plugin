# SPDX-FileCopyrightText: 2026 Scenario Inc.
# SPDX-FileCopyrightText: 2026 Open Studio contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Independent assistant shot scenes and transactional editable scene-strip cuts.

Local scene/NLA construction adapts retained film_scene primitives without its
service recipe, receipts, task identifiers or command wrappers.
"""

import math
import tempfile
import threading
import uuid
from contextlib import contextmanager
from pathlib import Path

import bpy
from mathutils import Vector

from ..core.agent_artifacts import read_inbox_file
from ..core.scene import assistant_film_plan as core
from ..core.scene.glb import MAX_GLB_BYTES
from . import assistant_blockout, file_state, model_application
from .shot_planner import fcurves_of

KIND = "open_studio_film_kind"
IDENTITY = "open_studio_film_id"
RECIPE = "open_studio_film_recipe"
ELEMENT = "open_studio_film_element_id"
_DATA = (*model_application._TRACKED_DATA, "worlds", "curves")


def _snapshot():
    return {name: set(getattr(bpy.data, name)) for name in _DATA}


def _main_thread():
    if threading.current_thread() is not threading.main_thread():
        raise RuntimeError("Build film scenes on Blender's main thread")
    if bpy.context.window is None or bpy.context.mode != "OBJECT":
        raise ValueError("Build film scenes in a Blender window in Object Mode")


@contextmanager
def _transaction():
    previous = _snapshot()
    window = bpy.context.window
    original, layer = window.scene, window.view_layer
    try:
        yield
    except BaseException:
        window.scene, window.view_layer = original, layer
        added = {value for name in _DATA for value in set(getattr(bpy.data, name)) - previous[name]}
        if added:
            bpy.data.batch_remove(ids=added)
        if _snapshot() != previous:
            file_state.mark_modified("Open Studio film build needs inspection")
            raise RuntimeError("Film cleanup is incomplete; inspect the new data") from None
        raise
    finally:
        window.scene, window.view_layer = original, layer


def _history_before():
    enabled = model_application._undo_enabled()
    if enabled and not file_state._push("Before Open Studio film scene"):
        raise ValueError("Blender could not prepare film undo history")
    return enabled


def _scene(plan, kind):
    scene = bpy.data.scenes.new(plan["name"])
    scene.use_fake_user = True
    scene[KIND], scene[IDENTITY], scene[RECIPE] = kind, uuid.uuid4().hex, core.serialize(plan)
    scene.render.fps, scene.render.fps_base = plan["fps"], 1
    scene.render.resolution_x, scene.render.resolution_y = plan["width"], plan["height"]
    scene.render.resolution_percentage = 100
    scene.render.pixel_aspect_x = scene.render.pixel_aspect_y = 1
    scene.render.engine = "BLENDER_EEVEE"
    scene.eevee.taa_render_samples = 32
    scene.eevee.taa_samples = 32
    scene.frame_start, scene.frame_end = 1, plan.get("frames", 1)
    scene.render.use_sequencer = kind == "timeline"
    return scene


def _collection(scene, name):
    collection = bpy.data.collections.new(name)
    scene.collection.children.link(collection)
    return collection


def _object(collection, name, data=None):
    obj = bpy.data.objects.new(name, data)
    collection.objects.link(obj)
    return obj


def _linear(obj):
    for curve in fcurves_of(obj):
        for key in curve.keyframe_points:
            key.interpolation = "LINEAR"


def _transform(obj, spec, *, size=None):
    obj.location = spec["position"]
    obj.rotation_euler = tuple(map(math.radians, spec["rotation_degrees"]))
    scale = spec["scale"]
    obj.scale = tuple(a * b for a, b in zip(scale, size, strict=True)) if size else scale


def _motion(obj, keys, *, size=None):
    for key in keys:
        _transform(obj, key, size=size)
        for field in ("location", "rotation_euler", "scale"):
            obj.keyframe_insert(data_path=field, frame=key["frame"])
    _linear(obj)


def _primitive(collection, spec):
    obj = _object(collection, spec["name"], assistant_blockout._mesh(spec["primitive"]))
    _transform(obj, dict(spec, scale=[1, 1, 1]), size=spec["size"])
    obj[ELEMENT], obj["open_studio_film_category"] = spec["id"], spec["category"]
    obj.color = (*spec["color"], 1)
    material = bpy.data.materials.new(spec["name"] + " color")
    material.use_nodes = True
    material.diffuse_color = obj.color
    material.node_tree.nodes.get("Principled BSDF").inputs["Base Color"].default_value = obj.color
    obj.data.materials.append(material)
    _motion(obj, spec["motion"], size=spec["size"])
    return obj


def _path(obj, keys):
    for key in keys:
        obj.location = key["position"]
        obj.keyframe_insert(data_path="location", frame=key["frame"])
    _linear(obj)


def _camera(scene, collection, spec):
    camera = _object(collection, "Shot Camera", bpy.data.cameras.new("Shot Camera"))
    camera.data.lens = spec["lens_mm"]
    target = _object(collection, "Shot Target")
    target.empty_display_type, target.empty_display_size = "SPHERE", 0.15
    _path(camera, spec["path"])
    _path(target, spec["target_path"])
    track = camera.constraints.new("TRACK_TO")
    track.target, track.track_axis, track.up_axis = target, "TRACK_NEGATIVE_Z", "UP_Y"
    scene.camera = camera
    world = bpy.data.worlds.new("Shot World")
    world.use_nodes = True
    world.node_tree.nodes.get("Background").inputs["Color"].default_value = (0.08, 0.09, 0.12, 1)
    world.node_tree.nodes.get("Background").inputs["Strength"].default_value = 0.5
    scene.world = world
    aim = Vector(spec["target_path"][0]["position"])
    for name, offset, power in (("Key", (-4, -4, 6), 1500), ("Fill", (4, 2, 4), 1000)):
        light = bpy.data.lights.new("Shot " + name, "AREA")
        light.energy, light.size = power, 5
        obj = _object(collection, light.name, light)
        obj.location = aim + Vector(offset)
        obj.rotation_euler = (aim - obj.location).to_track_quat("-Z", "Y").to_euler()


def _clip(datablock, spec, frames):
    animation = datablock.animation_data
    if animation is None:
        return
    action, slot = animation.action, getattr(animation, "action_slot", None)
    if action is None:
        strips = [s for t in animation.nla_tracks for s in t.strips if s.action]
        if not strips:
            return
        action, slot = strips[0].action, getattr(strips[0], "action_slot", None)
    # The snapshot contains only one explicitly chosen GLB animation; do not
    # leave the importer's stash tracks active alongside its selected clip.
    animation.action = None
    for track in tuple(animation.nla_tracks):
        animation.nla_tracks.remove(track)
    track = animation.nla_tracks.new()
    track.name = "Shot " + spec["mode"]
    strip = track.strips.new(action.name, 1, action)
    if slot is not None:
        strip.action_slot = slot
    strip.blend_type, strip.extrapolation = "REPLACE", "HOLD"
    start, end = action.frame_range
    if spec["mode"] == "hold" or end <= start:
        strip.use_animated_time = True
        strip.strip_time = start + spec["phase"] * (end - start)
        strip.frame_end = frames + 1
    else:
        strip.scale = 1 / spec["speed"]
        if spec["mode"] == "loop":
            repeat = math.ceil(frames / ((end - start) * strip.scale)) + 1
            if repeat > 1000:
                raise ValueError(
                    "The selected clip needs more than 1000 repeats; use a longer clip"
                )
            strip.repeat = repeat
            strip.frame_end = frames + 1


def _actor(scene, spec, data, directory, frames):
    path = directory / (spec["id"] + ".glb")
    path.write_bytes(data)
    previous = model_application._snapshot()
    staging, objects = model_application._stage(previous, lambda: model_application._import(path))
    if not any(obj.type == "MESH" and obj.data.vertices for obj in objects) or any(
        obj.type not in {"MESH", "EMPTY", "ARMATURE"} for obj in objects
    ):
        raise ValueError("An actor needs mesh geometry with optional Empty parents and armatures")
    model_application._bottom_center(objects)  # Validate finite imported bounds; retain its origin.
    collection = _collection(scene, spec["name"] + " actor")
    root = _object(collection, spec["name"])
    root[ELEMENT] = spec["id"]
    for obj in objects:
        if (
            obj.type in {"MESH", "ARMATURE"}
            and obj.data in previous["meshes" if obj.type == "MESH" else "armatures"]
        ):
            raise ValueError("Actor geometry and rigs must be independently imported")
        for owner in tuple(obj.users_collection):
            owner.objects.unlink(obj)
        collection.objects.link(obj)
        if obj.parent not in objects:
            obj.parent = root
        obj["open_studio_film_actor_id"] = spec["id"]
        if spec["clip"]:
            _clip(obj, spec["clip"], frames)
            if obj.type == "MESH" and obj.data.shape_keys:
                _clip(obj.data.shape_keys, spec["clip"], frames)
    for image in set(bpy.data.images) - previous["images"]:
        if image.packed_file is None:
            image.pack()
        if image.packed_file is None:
            raise ValueError("An actor image could not be packed")
        image.filepath = ""
    bpy.data.scenes.remove(staging)
    for unused in set(bpy.data.collections) - previous["collections"] - {collection}:
        if not unused.users:
            bpy.data.collections.remove(unused)
    _transform(root, spec)
    _motion(root, spec["motion"])
    return root


def _result(scene, undo):
    marked = file_state.mark_modified("Open Studio " + scene[KIND] + " scene")
    return {
        "scene_name": scene.name,
        "scene_id": scene[IDENTITY],
        "kind": scene[KIND],
        "fps": scene.render.fps,
        "width": scene.render.resolution_x,
        "height": scene.render.resolution_y,
        "frame_start": scene.frame_start,
        "frame_end": scene.frame_end,
        "frames": scene.frame_end - scene.frame_start + 1,
        "undo_available": bool(undo and marked),
        "engine": scene.render.engine,
    }


def build_shot(raw, inbox):
    _main_thread()
    plan = core.validate_shot(raw)
    snapshots, total = {}, 0
    for actor in plan["actors"]:
        source = read_inbox_file(
            inbox, actor["filename"], actor["sha256"], suffixes={".glb"}, max_bytes=MAX_GLB_BYTES
        )
        total += len(source.data)
        if total > 512 * 1024 * 1024:
            raise ValueError("A shot's actor snapshots must total at most 512 MiB")
        snapshots[actor["id"]] = core.select_glb_clip(source.data, actor["clip"])
    undo = _history_before()
    with _transaction(), tempfile.TemporaryDirectory(prefix="shot-", dir=inbox) as directory:
        scene = _scene(plan, "shot")
        bpy.context.window.scene = scene
        groups = {}
        for primitive in plan["primitives"]:
            group = groups.get(primitive["group"])
            if group is None:
                group = _collection(scene, primitive["group"])
                groups[primitive["group"]] = group
            _primitive(group, primitive)
        _camera(scene, _collection(scene, "Camera and lights"), plan["camera"])
        for actor in plan["actors"]:
            _actor(scene, actor, snapshots[actor["id"]], Path(directory), plan["frames"])
        scene.frame_set(1)
        bpy.context.view_layer.update()
    return _result(scene, undo)


def _source(shot, plan):
    scene = bpy.data.scenes.get(shot["scene_name"])
    if (
        scene is None
        or scene.library
        or scene.override_library
        or (scene.get(KIND) != "shot" or scene.get(IDENTITY) != shot["scene_id"])
    ):
        raise ValueError("Choose the explicit local shot scene and its current identity")
    if sum(other.get(IDENTITY) == shot["scene_id"] for other in bpy.data.scenes) != 1:
        raise ValueError(
            "The shot identity is duplicated; inspect the copied scene before assembly"
        )
    if (
        scene.camera is None
        or scene.camera.type != "CAMERA"
        or scene.camera.name not in scene.objects
        or scene.render.fps != plan["fps"]
        or scene.render.fps_base != 1
        or scene.render.resolution_x != plan["width"]
        or scene.render.resolution_y != plan["height"]
        or scene.render.resolution_percentage != 100
        or scene.render.pixel_aspect_x != 1
        or scene.render.pixel_aspect_y != 1
        or shot["start_frame"] < scene.frame_start
        or shot["end_frame"] > scene.frame_end
    ):
        raise ValueError(
            "Shot camera, timing, dimensions and requested range must match the timeline"
        )
    return scene


def _timeline_strip(editor, shot, source, cursor, index):
    duration = shot["end_frame"] - shot["start_frame"] + 1
    strip = editor.strips.new_scene(f"{index:03d} {source.name}", source, 1, cursor)
    strip.scene_input = "CAMERA"
    strip.animation_offset_start = shot["start_frame"] - source.frame_start
    strip.animation_offset_end = source.frame_end - shot["end_frame"]
    # Trimming scene content also shifts Blender's strip start; place the
    # already-trimmed strip at its explicit editorial cut afterward.
    strip.frame_start = cursor
    if strip.frame_final_start != cursor or strip.frame_final_duration != duration:
        raise ValueError("Blender could not retain the requested shot trim")
    return strip, duration


def build_timeline(raw):
    _main_thread()
    plan = core.validate_timeline(raw)
    sources = [_source(shot, plan) for shot in plan["shots"]]
    undo = _history_before()
    with _transaction():
        scene = _scene(plan, "timeline")
        editor, cursor = scene.sequence_editor_create(), 1
        for index, (shot, source) in enumerate(zip(plan["shots"], sources, strict=True), 1):
            strip, duration = _timeline_strip(editor, shot, source, cursor, index)
            scene.timeline_markers.new(strip.name, frame=cursor)
            cursor += duration
        scene.frame_end = cursor - 1
        scene.frame_set(1)
    return _result(scene, undo)
