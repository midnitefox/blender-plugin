# SPDX-FileCopyrightText: 2026 Scenario Inc.
# SPDX-License-Identifier: GPL-3.0-or-later
"""MCP tools that read or change the open Blender scene. Every handler runs on the main thread."""

import base64
import contextlib
import os
import tempfile

import bpy

from ..blender import file_state
from . import sandbox
from .protocol import ToolSpec


def _vec(v):
    return [round(float(x), 4) for x in v]


def scene_summary(args):
    scene = bpy.context.scene
    objects = []
    for obj in scene.objects:
        objects.append(
            {
                "name": obj.name,
                "type": obj.type,
                "location": _vec(obj.location),
                "dimensions": _vec(obj.dimensions),
                "parent": obj.parent.name if obj.parent else None,
                "collections": [c.name for c in obj.users_collection],
                "materials": [
                    s.material.name for s in getattr(obj, "material_slots", []) if s.material
                ],
                "hidden": obj.hide_get(),
            }
        )
    active = bpy.context.view_layer.objects.active
    return {
        "file": bpy.data.filepath or "(unsaved)",
        "objects": objects,
        "active": active.name if active else None,
        "selected": [o.name for o in bpy.context.selected_objects],
        "cameras": [o.name for o in scene.objects if o.type == "CAMERA"],
        "scene_camera": scene.camera.name if scene.camera else None,
        "frame_range": [scene.frame_start, scene.frame_end],
        "frame_current": scene.frame_current,
        "fps": scene.render.fps / (scene.render.fps_base or 1.0),
        "resolution": [scene.render.resolution_x, scene.render.resolution_y],
        "unit_system": scene.unit_settings.system,
        "cursor": _vec(scene.cursor.location),
        "blender": bpy.app.version_string,
    }


def object_detail(args):
    obj = bpy.data.objects.get(args.get("name", ""))
    if obj is None:
        raise ValueError(f"No object named {args.get('name')!r}")
    detail = {
        "name": obj.name,
        "type": obj.type,
        "location": _vec(obj.location),
        "rotation_euler": _vec(obj.rotation_euler),
        "scale": _vec(obj.scale),
        "dimensions": _vec(obj.dimensions),
        "parent": obj.parent.name if obj.parent else None,
        "modifiers": [m.type for m in getattr(obj, "modifiers", [])],
        "custom_properties": {k: repr(obj[k]) for k in obj.keys() if not k.startswith("_")},
    }
    if obj.type == "MESH":
        detail.update(
            {
                "vertices": len(obj.data.vertices),
                "faces": len(obj.data.polygons),
                "uv_layers": [uv.name for uv in obj.data.uv_layers],
                "materials": [s.material.name if s.material else None for s in obj.material_slots],
            }
        )
    if obj.type == "CAMERA":
        detail.update(
            {
                "lens_mm": obj.data.lens,
                "sensor_width": obj.data.sensor_width,
                "clip": [obj.data.clip_start, obj.data.clip_end],
            }
        )
    return detail


def execute_python(args):
    from .. import prefs as prefs_module

    prefs = prefs_module.get_prefs()
    if prefs is None:
        raise PermissionError(
            "Open Studio preferences are not available; Python execution stays disabled"
        )
    if not prefs.mcp_allow_python:
        raise PermissionError(
            "Python execution is disabled in Open Studio preferences (Local assistant connection > Allow connected assistants to run Python)"
        )
    code = args.get("code") or ""
    if not code.strip():
        raise ValueError("code is required")
    if sandbox.blocked_token(code):
        return sandbox.run_python(code)  # refused before running: nothing changed
    try:
        return sandbox.run_python(code)
    finally:
        # Like Blender's Run Script, which records Undo: the code may change anything.
        file_state.mark_modified("Open Studio MCP Python")


def _selection():
    layer = bpy.context.view_layer
    return frozenset(obj for obj in layer.objects if obj.select_get()), layer.objects.active


def select_objects(args):
    names = set(args.get("names") or [])
    with file_state.marking("Open Studio MCP selection", _selection):
        for obj in bpy.context.view_layer.objects:
            obj.select_set(obj.name in names)
        first = next((bpy.data.objects[n] for n in names if n in bpy.data.objects), None)
        if first is not None:
            bpy.context.view_layer.objects.active = first
    return {
        "selected": sorted(names & set(bpy.data.objects.keys())),
        "missing": sorted(names - set(bpy.data.objects.keys())),
    }


def set_frame(args):
    scene = bpy.context.scene
    frame = int(args["frame"])
    # frame_set also resets the subframe.
    with file_state.marking(
        "Open Studio MCP frame", lambda: (scene.frame_current, scene.frame_subframe)
    ):
        scene.frame_set(frame)
    return {"frame_current": scene.frame_current}


def _png_content(path):
    with open(path, "rb") as handle:
        return {"_image": base64.b64encode(handle.read()).decode("ascii"), "mimeType": "image/png"}


@contextlib.contextmanager
def _private_png(name):
    """Blender writes a path inside a private directory, removed on every exit."""
    with tempfile.TemporaryDirectory(prefix="scenario-mcp-") as directory:
        yield os.path.join(directory, name)


def screenshot_viewport(args):
    if bpy.app.background:
        raise RuntimeError("Screenshots need the Blender GUI")
    wm = bpy.context.window_manager
    window = wm.windows[0]
    area = next((a for a in window.screen.areas if a.type == "VIEW_3D"), None)
    if area is None:
        raise RuntimeError("No 3D viewport is open")
    region = next(r for r in area.regions if r.type == "WINDOW")
    with _private_png("shot.png") as path:
        with bpy.context.temp_override(
            window=window, screen=window.screen, area=area, region=region
        ):
            bpy.ops.screen.screenshot_area(filepath=path)
        return _png_content(path)


def render_still(args):
    from ..blender import capture

    with _private_png("render.png") as path:
        capture.capture_still(
            bpy.context,
            path,
            source=args.get("source", "CAMERA"),
            width=int(args.get("width", 1280)),
            height=int(args.get("height", 720)),
        )
        return _png_content(path)


def camera_path(args):
    """Build an animated scene camera: from explicit waypoints, or from a preset around the subject."""
    from ..blender import shot_planner
    from ..core.scene import shot_plan

    context = bpy.context
    scene = context.scene
    props = scene.scenario_shot
    preset = None
    if args.get("preset"):
        resolve = getattr(shot_plan, "resolve_preset", None)
        preset = resolve(args["preset"]) if resolve else args["preset"]
        if preset not in shot_plan.PRESETS or (
            resolve
            and args["preset"] not in shot_plan.PRESETS
            and args["preset"] not in getattr(shot_plan, "PRESET_ALIASES", {})
        ):
            raise ValueError(f"preset must be one of {sorted(shot_plan.PRESETS)}")
    # Read every argument before the first change, so a refused call leaves the file clean.
    duration = float(args["duration"]) if args.get("duration") is not None else None
    focal = float(args["focal"]) if args.get("focal") is not None else None
    plan = shot_plan.plan_from_text(args["description"]) if args.get("description") else None
    markers = []
    for wp in args.get("waypoints") or []:
        position = wp.get("position")
        if not position or len(position) != 3:
            raise ValueError("each waypoint needs position: [x, y, z]")
        rotation = wp.get("rotation_euler")
        markers.append(
            (
                tuple(float(v) for v in position),
                tuple(float(v) for v in rotation) if rotation else None,
                float(wp["focal"]) if wp.get("focal") else None,
                float(wp.get("hold") or 0.0),
            )
        )
    try:
        if preset is not None:
            props.preset = preset
        if duration is not None:
            props.duration = duration
        if focal is not None:
            props.focal = focal
        if args.get("aim_at_subject") is not None:
            props.aim_at_subject = bool(args["aim_at_subject"])
        if plan is not None:
            props.preset, props.duration, props.focal = (
                plan["preset"],
                plan["duration"],
                plan["focal"],
            )
        if markers:
            shot_planner.clear_markers(scene)
            if hasattr(props, "closed_loop"):
                props.closed_loop = bool(
                    args.get("closed_loop", False)
                )  # explicit waypoints are an open path unless asked
            for position, rotation, marker_focal, hold in markers:
                shot_planner.add_marker(
                    context, position, rotation=rotation, focal=marker_focal, hold=hold
                )
        camera, keyframes, last_frame = shot_planner.build_path(context)
    finally:
        # Every step above changes the scene: a failure part-way still marks it.
        file_state.mark_modified("Open Studio camera path")
    return {
        "camera": camera.name,
        "keyframes": keyframes,
        "frame_start": scene.frame_start,
        "frame_end": last_frame,
        "preset": props.preset,
        "duration": props.duration,
        "focal": props.focal,
        "markers": len(shot_planner.marker_objects(scene)),
        "note": "The scene camera now follows this path. Review the animation on the timeline.",
    }


def blender_api_help(args):
    """Look up a bpy path (an operator, a type, a collection) and return its docstring, signature and properties, so
    an agent checks the real API before writing execute_python. Pure introspection, no docs bundled."""
    import inspect

    path = (args.get("path") or "").strip()
    if not path:
        return {
            "error": "Provide a path, e.g. bpy.ops.mesh.primitive_cube_add, bpy.types.Object, or bpy.data.objects"
        }
    target = bpy
    parts = [p for p in path.split(".") if p]
    if parts and parts[0] == "bpy":
        parts = parts[1:]
    for part in parts:
        try:
            target = getattr(target, part)
        except (AttributeError, KeyError) as err:
            return {"path": path, "error": f"not found at '{part}': {err}"}
    info = {"path": path, "type": type(target).__name__}
    doc = inspect.getdoc(target)
    if doc:
        info["doc"] = doc[:2000]
    try:  # operators and types expose their properties through the RNA
        rna = target.get_rna_type()
        info["properties"] = [
            {"name": p.identifier, "type": p.type, "description": (p.description or "")[:160]}
            for p in rna.properties
            if p.identifier != "rna_type"
        ][:60]
    except (AttributeError, TypeError):
        pass
    if "properties" not in info:
        try:
            members = [m for m in dir(target) if not m.startswith("_")]
            if members:
                info["members"] = members[:80]
        except TypeError:
            pass
    return info


def datablocks_summary(args):
    """Counts of the data-blocks in the open .blend (objects, meshes, materials, images, collections...) and the file
    path, so an agent understands the scene's contents before acting."""
    names = (
        "objects",
        "meshes",
        "materials",
        "images",
        "collections",
        "cameras",
        "lights",
        "armatures",
        "curves",
        "node_groups",
        "textures",
        "actions",
        "worlds",
        "scenes",
    )
    counts = {name: len(getattr(bpy.data, name)) for name in names if hasattr(bpy.data, name)}
    return {
        "filepath": bpy.data.filepath or "(unsaved)",
        "counts": counts,
        "collections": [c.name for c in bpy.data.collections][:40],
    }


def _schema(props, required=()):
    return {"type": "object", "properties": props, "required": list(required)}


SPECS = (
    ToolSpec(
        "scene_summary",
        (
            "Inspect the open Blender scene before choosing a scene operation.\n"
            "Args: none.\n"
            "Returns: file, objects[] (name, type, location, dimensions, parent, collections, materials, hidden), active, selected, cameras, scene_camera, frame_range, frame_current, fps, resolution, unit_system, cursor and blender version.\n"
            "Example: {}.\n"
            "Prefer object_detail for one object's geometry or modifiers; this does not return mesh vertex data.\n"
        ),
        _schema({}),
        scene_summary,
        {"readOnlyHint": True},
    ),
    ToolSpec(
        "object_detail",
        (
            "Inspect one named Blender object's transform, geometry summary and custom properties.\n"
            "Args:\n"
            "  - name: required string, exact object name.\n"
            "Returns: name, type, location, rotation_euler, scale, dimensions, parent, modifiers and custom_properties. Meshes also return vertices, faces, uv_layers and materials; cameras return lens_mm, sensor_width and clip. An unknown name raises ValueError.\n"
            'Example: {"name": "Cube"}.\n'
            "Prefer scene_summary to discover names first; this does not change the object.\n"
        ),
        _schema({"name": {"type": "string"}}, ["name"]),
        object_detail,
        {"readOnlyHint": True},
    ),
    ToolSpec(
        "execute_python",
        (
            "Run arbitrary Python with bpy on Blender's main thread, only when explicitly enabled in preferences.\n"
            "Args:\n"
            "  - code: required string, Python source; bpy and a result dict are preloaded.\n"
            "Returns: result, stdout and stderr; failures also include error. Disabled by default unless the user enables Allow connected assistants to run Python. Common quit, factory-reset, preference-reload and file-deletion call spellings are blocked; this is not a security sandbox.\n"
            'Example: {"code": "result[\'objects\'] = len(bpy.data.objects)"}.\n'
            "Check blender_api_help first and prefer specific tools instead of arbitrary code.\n"
        ),
        _schema(
            {
                "code": {
                    "type": "string",
                    "description": "Python source. bpy and result = {} are preloaded.",
                }
            },
            ["code"],
        ),
        execute_python,
        {"destructiveHint": True},
    ),
    ToolSpec(
        "select_objects",
        (
            "Replace the selection in the current view layer with the named Blender objects.\n"
            "Args:\n"
            "  - names: required array of strings, object names to select; an empty array clears selection.\n"
            "Returns: selected and missing name lists. A matching object becomes active; do not rely on array order to choose it.\n"
            'Example: {"names": ["Cube"]}.\n'
            "Prefer scene_summary to verify names and the existing selection before changing it.\n"
        ),
        _schema({"names": {"type": "array", "items": {"type": "string"}}}, ["names"]),
        select_objects,
    ),
    ToolSpec(
        "set_frame",
        (
            "Move Blender's timeline to a frame and evaluate the scene there.\n"
            "Args:\n"
            "  - frame: required integer, the desired frame number.\n"
            "Returns: frame_current, the scene's resulting frame.\n"
            'Example: {"frame": 42}.\n'
            "Prefer scene_summary to check the frame range first; this changes the current scene state.\n"
        ),
        _schema({"frame": {"type": "integer"}}, ["frame"]),
        set_frame,
    ),
    ToolSpec(
        "screenshot_viewport",
        (
            "Capture the visible 3D viewport area as a PNG image, including its UI overlays.\n"
            "Args: none.\n"
            "Returns: PNG image content for the MCP client, not a permanent file path.\n"
            "Example: {}.\n"
            "Do not use in background mode or without a 3D viewport: it raises RuntimeError. Prefer render_still for a camera or viewport still at a chosen size. Temporary capture files are cleaned up.\n"
        ),
        _schema({}),
        screenshot_viewport,
        {"readOnlyHint": True},
    ),
    ToolSpec(
        "camera_path",
        (
            "Build an animated scene camera from a preset, description or explicit waypoints.\n"
            "Args:\n"
            "  - preset: optional string from shot_plan.PRESETS or PRESET_ALIASES, such as orbit or push_in.\n"
            "  - description: optional free text, such as slow orbit, 8 s, 35mm; parsed values override preset, duration and focal.\n"
            "  - duration: optional number of seconds.\n"
            "  - focal: optional number of millimetres.\n"
            "  - aim_at_subject: optional boolean.\n"
            "  - waypoints: optional array of objects, each requiring position [x, y, z]; rotation_euler [x, y, z], focal and hold are optional. Explicit waypoints replace existing markers.\n"
            "Returns: camera, keyframes, frame_start, frame_end, preset, duration, focal, markers and note.\n"
            'Example: {"preset": "orbit", "duration": 8, "focal": 35}.\n'
            "Prefer scene_summary first; this changes the scene camera and animation. Review the resulting timeline.\n"
        ),
        _schema(
            {
                "preset": {"type": "string"},
                "duration": {"type": "number", "description": "seconds"},
                "focal": {"type": "number", "description": "mm"},
                "aim_at_subject": {"type": "boolean"},
                "description": {"type": "string", "description": "e.g. 'slow orbit, 8 s, 35mm'"},
                "waypoints": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "position": {"type": "array", "items": {"type": "number"}},
                            "rotation_euler": {"type": "array", "items": {"type": "number"}},
                            "focal": {"type": "number"},
                            "hold": {"type": "number"},
                        },
                    },
                },
            }
        ),
        camera_path,
    ),
    ToolSpec(
        "render_still",
        (
            "Capture a quick OpenGL camera or viewport still as a PNG at the requested size.\n"
            "Args:\n"
            "  - source: optional string, CAMERA (default) or VIEWPORT.\n"
            "  - width: optional integer, default 1280 pixels.\n"
            "  - height: optional integer, default 720 pixels.\n"
            "Returns: PNG image content, with temporary capture files cleaned up.\n"
            'Example: {"source": "CAMERA", "width": 1280, "height": 720}.\n'
            "Do not use in background mode: capture needs the GUI and a 3D viewport or raises RuntimeError. Prefer screenshot_viewport to inspect the visible viewport UI.\n"
        ),
        _schema(
            {
                "source": {"type": "string", "enum": ["CAMERA", "VIEWPORT"]},
                "width": {"type": "integer"},
                "height": {"type": "integer"},
            }
        ),
        render_still,
        {"readOnlyHint": True},
    ),
    ToolSpec(
        "blender_api_help",
        (
            "Inspect Blender's running Python API before writing a script.\n"
            "Args:\n"
            "  - path: required string, a bpy operator, type or collection path.\n"
            "Returns: path, type, doc when available, properties[] or members[] when exposed; invalid or missing paths return error.\n"
            'Example: {"path": "bpy.ops.mesh.primitive_cube_add"}.\n'
            "Prefer this to guessing API signatures, and use specific scene tools instead of execute_python where possible. This is introspection, not execution of the named operator.\n"
        ),
        _schema(
            {
                "path": {
                    "type": "string",
                    "description": "e.g. bpy.ops.mesh.primitive_cube_add, bpy.types.Object, bpy.data.objects",
                }
            },
            ["path"],
        ),
        blender_api_help,
        {"readOnlyHint": True},
    ),
    ToolSpec(
        "datablocks_summary",
        (
            "Summarize the datablocks in the open Blender file.\n"
            "Args: none.\n"
            "Returns: filepath, counts (objects, meshes, materials, images, collections, cameras, lights, armatures, curves, node_groups, textures, actions, worlds, scenes when available) and up to 40 collection names.\n"
            "Example: {}.\n"
            "Prefer scene_summary for selection and object transforms, or object_detail for a named object; counts alone do not establish what is visible.\n"
        ),
        _schema({}),
        datablocks_summary,
        {"readOnlyHint": True},
    ),
)
