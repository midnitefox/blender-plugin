# SPDX-FileCopyrightText: 2026 Scenario Inc.
# SPDX-License-Identifier: GPL-3.0-or-later
"""Shot planner: numbered shot markers in the viewport, one-click camera moves, a keyframed shot camera.

Markers are small camera objects named "Shot 1", "Shot 2"... in the "Scenario Shots" collection: the frustum shows
where the shot looks and its lens IS the zoom level, so a marker selected in the viewport reads like a storyboard
frame. A move from the library places markers (editable like any object); Build camera path always keyframes the
"Scenario Shot Camera" through the markers, closing the loop for orbits and ellipses. bpy only on the main thread;
the maths live in core.scene.shot_plan."""

import bpy
from bpy.props import (
    BoolProperty,
    EnumProperty,
    FloatProperty,
    IntProperty,
    PointerProperty,
    StringProperty,
)
from mathutils import Euler, Vector

from ..core.scene import shot_plan

SHOTS_COLLECTION = "Scenario Shots"
CAMERA_NAME = "Scenario Shot Camera"
TARGET_NAME = "Shot Target"
AIM_CONSTRAINT = "Scenario Aim"
MARKER_PREFIX = "Shot "
PROP_INDEX, PROP_FOCAL, PROP_HOLD = "scenario_shot_index", "scenario_focal", "scenario_hold"
FALLBACK_BOX = ((-1.0, -1.0, 0.0), (1.0, 1.0, 2.0))


def _preset_items(self, context):
    """Grouped moves; None entries are separators in the dropdown (Blender ignores them in an expanded row)."""
    return shot_plan.preset_items()


class ScenarioShotProps(bpy.types.PropertyGroup):
    preset: EnumProperty(
        name="Move",
        items=_preset_items,
        description="Camera move from the library; Place markers turns it into editable Shot markers around the subject",
    )
    duration: FloatProperty(
        name="Duration (s)",
        default=shot_plan.DEFAULT_DURATION,
        min=shot_plan.MIN_DURATION,
        max=shot_plan.MAX_DURATION,
        description="Length of the shot in seconds; the frame range follows it",
    )
    focal: FloatProperty(
        name="Focal (mm)",
        default=shot_plan.DEFAULT_FOCAL,
        min=8.0,
        max=400.0,
        subtype="NONE",
        description="Focal length in mm (the zoom level) for placed markers and new markers",
    )
    start_frame: IntProperty(
        name="Start frame",
        default=1,
        min=0,
        description="First frame of the shot; the scene frame range starts here",
    )
    aim_at_subject: BoolProperty(
        name="Aim at subject",
        default=True,
        description="Keep the camera pointed at the subject along the whole path",
    )
    closed_loop: BoolProperty(
        name="Closed loop",
        default=False,
        description="End the path exactly where it starts (set automatically for orbits and ellipses)",
    )
    description: StringProperty(
        name="Shot",
        description="One camera move plus optional seconds and lens, read by keywords (not AI): orbit, dolly in, truck left, crane, top down, zoom in, 8 s, 50 mm. One move per path; the first move you name wins",
    )
    use_selection: BoolProperty(
        name="Frame selection",
        default=False,
        description="Frame the selected objects instead of every visible mesh",
    )
    markers_source: StringProperty(
        description="The move the markers were placed from, or 'markers' when placed by hand"
    )
    previous_camera: StringProperty(
        description="The scene camera before the shot camera took over, restored by Clear path"
    )


# -- scene helpers -------------------------------------------------------------


def shots_collection(scene, create=True):
    coll = bpy.data.collections.get(SHOTS_COLLECTION)
    if coll is None:
        if not create:
            return None
        coll = bpy.data.collections.new(SHOTS_COLLECTION)
    if coll.name not in scene.collection.children:
        scene.collection.children.link(coll)
    return coll


def marker_objects(scene):
    coll = bpy.data.collections.get(SHOTS_COLLECTION)
    if coll is None:
        return []
    markers = [o for o in coll.objects if o.type == "CAMERA" and PROP_INDEX in o]
    return sorted(markers, key=lambda o: (int(o[PROP_INDEX]), o.name))


def active_marker(context):
    """The selected/active Shot marker, or None. Used to edit one marker's hold in the panel."""
    obj = context.active_object
    return obj if obj is not None and obj.type == "CAMERA" and PROP_INDEX in obj else None


def shot_camera(scene):
    cam = bpy.data.objects.get(CAMERA_NAME)
    if cam is None or cam.type != "CAMERA":
        return None
    return cam


def subject_bbox(context, use_selection=False):
    """World-space (min, max) of the selected meshes, else of every visible mesh; a 2 m box when the scene is empty."""
    scene = context.scene
    shots = bpy.data.collections.get(SHOTS_COLLECTION)
    excluded = set(shots.objects.keys()) if shots else set()
    candidates = []
    if use_selection:
        candidates = [
            o for o in context.selected_objects if o.type == "MESH" and o.name not in excluded
        ]
    if not candidates:
        candidates = [
            o
            for o in scene.objects
            if o.type == "MESH" and o.name not in excluded and not o.hide_render and o.visible_get()
        ]
    points = []
    for obj in candidates:
        points.extend(obj.matrix_world @ Vector(corner) for corner in obj.bound_box)
    if not points:
        return FALLBACK_BOX
    return (
        tuple(min(p[i] for p in points) for i in range(3)),
        tuple(max(p[i] for p in points) for i in range(3)),
    )


def subject_centre(context, use_selection=False):
    lo, hi = subject_bbox(context, use_selection)
    return Vector(tuple((a + b) / 2.0 for a, b in zip(lo, hi, strict=False)))


def _look_at_euler(position, target):
    direction = Vector(target) - Vector(position)
    if direction.length < 1e-6:
        direction = Vector((0.0, 1.0, 0.0))
    return direction.to_track_quat("-Z", "Y").to_euler()


def add_marker(context, location, rotation=None, focal=None, hold=0.0, look_at=None):
    """Add the next numbered marker. A hand-placed marker (no `look_at`) marks the set as custom."""
    scene = context.scene
    props = scene.scenario_shot
    coll = shots_collection(scene)
    index = len(marker_objects(scene)) + 1
    cam_data = bpy.data.cameras.new(f"{MARKER_PREFIX}{index}")
    cam_data.lens = float(focal or props.focal)
    cam_data.display_size = 0.35
    marker = bpy.data.objects.new(f"{MARKER_PREFIX}{index}", cam_data)
    marker.location = Vector(location)
    if rotation is not None:
        marker.rotation_euler = Euler(rotation)
    else:
        marker.rotation_euler = _look_at_euler(
            location,
            look_at if look_at is not None else subject_centre(context, props.use_selection),
        )
    marker.show_name = True
    marker.hide_render = True
    marker[PROP_INDEX] = index
    marker[PROP_FOCAL] = float(cam_data.lens)
    marker[PROP_HOLD] = float(hold)
    coll.objects.link(marker)
    if look_at is None:
        props.markers_source = "markers"
    return marker


def renumber_markers(scene):
    for index, marker in enumerate(marker_objects(scene), start=1):
        marker[PROP_INDEX] = index
        marker.name = f"{MARKER_PREFIX}{index}"
        if marker.data is not None:
            marker.data.name = marker.name


def remove_marker(marker):
    data = marker.data
    bpy.data.objects.remove(marker, do_unlink=True)
    if data is not None and data.users == 0:
        bpy.data.cameras.remove(data)


def clear_markers(scene):
    for marker in marker_objects(scene):
        remove_marker(marker)
    scene.scenario_shot.markers_source = ""


def place_markers(context):
    """Replace the markers by the chosen move around the subject: one editable Shot marker per waypoint.

    Closed moves keep their loop through the `closed_loop` flag instead of a duplicate marker."""
    scene = context.scene
    props = scene.scenario_shot
    preset = shot_plan.resolve_preset(props.preset)
    clear_markers(scene)
    bbox = subject_bbox(context, props.use_selection)
    centre = tuple((a + b) / 2.0 for a, b in zip(*bbox, strict=False))
    markers = []
    for wp in shot_plan.marker_waypoints(preset, bbox[0], bbox[1], props.focal):
        markers.append(
            add_marker(
                context, wp.position, focal=wp.focal, hold=wp.hold, look_at=wp.look_at or centre
            )
        )
    props.closed_loop = shot_plan.is_closed(preset)
    props.markers_source = preset
    context.view_layer.update()  # matrix_world of the new markers is identity until the depsgraph runs
    return markers


def _view3d(context):
    space = getattr(context, "space_data", None)
    region_3d = getattr(context, "region_data", None)
    if space is not None and space.type == "VIEW_3D" and region_3d is not None:
        return space, region_3d
    wm = context.window_manager
    for window in wm.windows if wm else ():
        for area in window.screen.areas:
            if area.type == "VIEW_3D":
                return area.spaces.active, area.spaces.active.region_3d
    return None, None


def waypoints_from_markers(markers):
    out = []
    for marker in markers:
        matrix = marker.matrix_world
        focal = float(
            marker.get(PROP_FOCAL, 0.0)
            or (marker.data.lens if marker.data else shot_plan.DEFAULT_FOCAL)
        )
        out.append(
            shot_plan.Waypoint(
                tuple(matrix.translation),
                rotation_euler=tuple(matrix.to_euler()),
                focal=focal,
                hold=float(marker.get(PROP_HOLD, 0.0) or 0.0),
            )
        )
    return out


def _ensure_camera(scene):
    coll = shots_collection(scene)
    cam = shot_camera(scene)
    if cam is None:
        cam = bpy.data.objects.new(CAMERA_NAME, bpy.data.cameras.new(CAMERA_NAME))
    if cam.name not in coll.objects:
        coll.objects.link(cam)
    cam.animation_data_clear()
    cam.data.animation_data_clear()
    for constraint in list(cam.constraints):
        if constraint.name == AIM_CONSTRAINT:
            cam.constraints.remove(constraint)
    return cam


def _ensure_target(scene, centre):
    coll = shots_collection(scene)
    target = bpy.data.objects.get(TARGET_NAME)
    if target is None:
        target = bpy.data.objects.new(TARGET_NAME, None)
        target.empty_display_type = "PLAIN_AXES"
        target.empty_display_size = 0.25
    if target.name not in coll.objects:
        coll.objects.link(target)
    target.location = Vector(centre)
    target.hide_render = True
    return target


def _remove_target():
    target = bpy.data.objects.get(TARGET_NAME)
    if target is not None:
        bpy.data.objects.remove(target, do_unlink=True)


def fcurves_of(datablock):
    """The F-curves animating `datablock`. Blender 4.x keeps them on action.fcurves; layered actions (4.4+/5.x)
    keep them in layers > strips > channelbags."""
    anim = getattr(datablock, "animation_data", None)
    if anim is None or anim.action is None:
        return []
    action = anim.action
    if hasattr(action, "fcurves"):
        return list(action.fcurves)
    out = []
    for layer in getattr(action, "layers", ()):
        for strip in layer.strips:
            for bag in getattr(strip, "channelbags", ()):
                out.extend(bag.fcurves)
    return out


def _smooth(datablock):
    for fcurve in fcurves_of(datablock):
        for key in fcurve.keyframe_points:
            key.interpolation = "BEZIER"
            key.easing = "AUTO"


def frame_range(scene):
    """(start, end, fps) the shot settings imply: `duration` seconds from `start_frame` at the scene fps."""
    props = scene.scenario_shot
    fps = scene.render.fps / (scene.render.fps_base or 1.0)
    total = max(2, int(round(props.duration * fps)))
    start = int(props.start_frame)
    return start, start + total - 1, fps


def build_path(context):
    """Keyframe the shot camera through the markers (placed first from the move when there are fewer than two),
    closing the loop when `closed_loop` is on. Returns (camera, keyframe_count, last_frame)."""
    scene = context.scene
    props = scene.scenario_shot
    markers = marker_objects(scene)
    if len(markers) < 2:
        markers = place_markers(context)
    renumber_markers(scene)
    context.view_layer.update()  # markers moved by hand or just placed: read their world matrices after the depsgraph ran
    bbox = subject_bbox(context, props.use_selection)
    centre = tuple((a + b) / 2.0 for a, b in zip(*bbox, strict=False))
    waypoints = waypoints_from_markers(markers)
    if props.closed_loop:
        waypoints = shot_plan.close_loop(waypoints)
    start, end, fps = frame_range(scene)
    schedule = [
        (frame + start - 1, index)
        for frame, index in shot_plan.frame_schedule(waypoints, props.duration, fps)
    ]
    current = scene.camera
    if current is not None and current.name != CAMERA_NAME:
        props.previous_camera = current.name
    cam = _ensure_camera(scene)
    aim = bool(props.aim_at_subject)
    focals = {round(wp.focal, 3) for wp in waypoints}
    for frame, index in schedule:
        wp = waypoints[index]
        cam.location = Vector(wp.position)
        cam.keyframe_insert("location", frame=frame)
        if not aim:
            if wp.rotation_euler is not None:
                cam.rotation_euler = Euler(wp.rotation_euler)
            else:
                cam.rotation_euler = _look_at_euler(wp.position, wp.look_at or centre)
            cam.keyframe_insert("rotation_euler", frame=frame)
        cam.data.lens = float(wp.focal)
        if len(focals) > 1:
            cam.data.keyframe_insert("lens", frame=frame)
    if aim:
        target = _ensure_target(scene, centre)
        constraint = cam.constraints.new("TRACK_TO")
        constraint.name = AIM_CONSTRAINT
        constraint.target = target
        constraint.track_axis = "TRACK_NEGATIVE_Z"
        constraint.up_axis = "UP_Y"
    else:
        _remove_target()
    _smooth(cam)
    _smooth(cam.data)
    last_frame = schedule[-1][0] if schedule else end
    cam["scenario_shot_frames"] = int(last_frame - start + 1)
    cam["scenario_shot_source"] = props.markers_source or "markers"
    scene.camera = cam
    scene.frame_start = start
    scene.frame_end = int(last_frame)
    scene.use_preview_range = False
    scene.frame_set(start)
    return cam, len(schedule), int(last_frame)


def clear_path(context):
    """Remove the shot camera, its target and every marker; give the scene its previous camera back."""
    scene = context.scene
    props = scene.scenario_shot
    clear_markers(scene)
    _remove_target()
    cam = shot_camera(scene)
    if cam is not None:
        data = cam.data
        bpy.data.objects.remove(cam, do_unlink=True)
        if data is not None and data.users == 0:
            bpy.data.cameras.remove(data)
    previous = bpy.data.objects.get(props.previous_camera) if props.previous_camera else None
    if previous is not None and previous.type == "CAMERA":
        scene.camera = previous
    elif scene.camera is None:
        scene.camera = next((o for o in scene.objects if o.type == "CAMERA"), None)
    props.previous_camera = ""
    coll = bpy.data.collections.get(SHOTS_COLLECTION)
    if coll is not None and not coll.objects:
        bpy.data.collections.remove(coll)


def path_summary(scene):
    """'Scenario Shot Camera, 12 markers' or '' when nothing has been built or placed."""
    cam = shot_camera(scene)
    count = len(marker_objects(scene))
    if cam is None and count == 0:
        return ""
    parts = [cam.name] if cam is not None else []
    if count:
        parts.append(f"{count} marker{'s' if count != 1 else ''}")
    return ", ".join(parts)


# -- operators -----------------------------------------------------------------


def _object_mode(context):
    return context.mode == "OBJECT"


def _confirm(
    operator, context, event, message, title="Open Studio camera path", confirm_text="Replace"
):
    """Ask before erasing; in --background (no window) the operator simply runs."""
    if bpy.app.background or event is None or context.window is None:
        return operator.execute(context)
    wm = context.window_manager
    try:
        return wm.invoke_confirm(
            operator, event, message=message, title=title, confirm_text=confirm_text
        )
    except TypeError:  # Blender < 4.1 has no message/title arguments
        return wm.invoke_confirm(operator, event)


class SCENARIO_OT_shot_add_marker(bpy.types.Operator):
    bl_idname = "scenario.shot_add_marker"
    bl_label = "Add shot marker at cursor"
    bl_description = "Add the next numbered shot marker at the 3D cursor, looking at the subject"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return _object_mode(context)

    def execute(self, context):
        marker = add_marker(context, context.scene.cursor.location.copy())
        self.report({"INFO"}, f"{marker.name} added")
        return {"FINISHED"}


class SCENARIO_OT_shot_add_marker_from_view(bpy.types.Operator):
    bl_idname = "scenario.shot_add_marker_from_view"
    bl_label = "Add shot marker from view"
    bl_description = "Add the next numbered shot marker where the viewport looks from, with the viewport lens as zoom level"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return _object_mode(context)

    def execute(self, context):
        space, region_3d = _view3d(context)
        if region_3d is None:
            marker = add_marker(context, context.scene.cursor.location.copy())
        else:
            matrix = region_3d.view_matrix.inverted()
            focal = context.scene.scenario_shot.focal
            if region_3d.view_perspective == "CAMERA" and context.scene.camera is not None:
                matrix = context.scene.camera.matrix_world.copy()
                focal = context.scene.camera.data.lens
            elif space is not None and hasattr(space, "lens"):
                focal = space.lens
            marker = add_marker(
                context, matrix.translation.copy(), rotation=matrix.to_euler(), focal=focal
            )
        self.report({"INFO"}, f"{marker.name} added")
        return {"FINISHED"}


class SCENARIO_OT_shot_remove_last_marker(bpy.types.Operator):
    bl_idname = "scenario.shot_remove_last_marker"
    bl_label = "Remove last shot marker"
    bl_description = "Remove the marker with the highest number"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return _object_mode(context) and bool(marker_objects(context.scene))

    def execute(self, context):
        markers = marker_objects(context.scene)
        remove_marker(markers[-1])
        return {"FINISHED"}


class SCENARIO_OT_shot_clear_markers(bpy.types.Operator):
    bl_idname = "scenario.shot_clear_markers"
    bl_label = "Clear shot markers"
    bl_description = "Remove every shot marker (the shot camera stays)"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return _object_mode(context) and bool(marker_objects(context.scene))

    def invoke(self, context, event):
        return _confirm(
            self, context, event, f"Remove the {len(marker_objects(context.scene))} shot markers?"
        )

    def execute(self, context):
        clear_markers(context.scene)
        return {"FINISHED"}


class SCENARIO_OT_shot_renumber(bpy.types.Operator):
    bl_idname = "scenario.shot_renumber"
    bl_label = "Renumber shot markers"
    bl_description = "Number the markers 1, 2, 3... in their current order"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return _object_mode(context) and bool(marker_objects(context.scene))

    def execute(self, context):
        renumber_markers(context.scene)
        return {"FINISHED"}


class SCENARIO_OT_shot_place_markers(bpy.types.Operator):
    bl_idname = "scenario.shot_place_markers"
    bl_label = "Place markers"
    bl_description = "Replace the markers by the chosen move around the subject, one editable Shot marker per waypoint; move or renumber them, then build"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return _object_mode(context)

    def invoke(self, context, event):
        existing = path_summary(context.scene)
        if existing:
            return _confirm(self, context, event, f"Replace the existing markers ({existing})?")
        return self.execute(context)

    def execute(self, context):
        markers = place_markers(context)
        props = context.scene.scenario_shot
        label = shot_plan.PRESETS[shot_plan.resolve_preset(props.preset)][0]
        self.report(
            {"INFO"},
            f"{label}: {len(markers)} markers placed"
            + (" (closed loop)" if props.closed_loop else ""),
        )
        return {"FINISHED"}


class SCENARIO_OT_shot_build_path(bpy.types.Operator):
    bl_idname = "scenario.shot_build_path"
    bl_label = "Build camera path"
    bl_description = "Keyframe the shot camera through the markers (placed from the move when there are fewer than two), set the frame range and make it the scene camera"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return _object_mode(context)

    def invoke(self, context, event):
        if shot_camera(context.scene) is not None:
            return _confirm(
                self,
                context,
                event,
                f"Replace the existing camera path ({path_summary(context.scene)})?",
            )
        return self.execute(context)

    def execute(self, context):
        cam, keys, last = build_path(context)
        self.report(
            {"INFO"}, f"{cam.name}: {keys} keyframes, frames {context.scene.frame_start} to {last}"
        )
        return {"FINISHED"}


class SCENARIO_OT_shot_clear_path(bpy.types.Operator):
    bl_idname = "scenario.shot_clear_path"
    bl_label = "Clear path"
    bl_description = "Delete the shot camera, its target and every marker, and give the scene its previous camera back"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return _object_mode(context) and bool(path_summary(context.scene))

    def invoke(self, context, event):
        return _confirm(
            self,
            context,
            event,
            f"Clear the camera path ({path_summary(context.scene)})?",
            title="Clear camera path",
            confirm_text="Clear",
        )

    def execute(self, context):
        clear_path(context)
        self.report({"INFO"}, "Camera path removed")
        return {"FINISHED"}


class SCENARIO_OT_shot_preview(bpy.types.Operator):
    bl_idname = "scenario.shot_preview"
    bl_label = "Preview"
    bl_description = "Look through the shot camera and play the timeline"

    @classmethod
    def poll(cls, context):
        return shot_camera(context.scene) is not None

    def execute(self, context):
        scene = context.scene
        scene.camera = shot_camera(scene)
        _, region_3d = _view3d(context)
        if region_3d is not None:
            region_3d.view_perspective = "CAMERA"
        screen = getattr(context, "screen", None)
        if screen is not None and not screen.is_animation_playing:
            try:
                bpy.ops.screen.animation_play()
            except RuntimeError:
                pass
        return {"FINISHED"}


class SCENARIO_OT_shot_from_description(bpy.types.Operator):
    bl_idname = "scenario.shot_from_description"
    bl_label = "Plan"
    bl_description = "Read one move plus seconds and lens from the description by keywords (not AI), place the markers and build the camera path"
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        return _object_mode(context)

    def invoke(self, context, event):
        existing = path_summary(context.scene)
        if existing:
            return _confirm(self, context, event, f"Replace the existing camera path ({existing})?")
        return self.execute(context)

    def execute(self, context):
        props = context.scene.scenario_shot
        plan = shot_plan.plan_from_text(props.description)
        props.preset, props.duration, props.focal = plan["preset"], plan["duration"], plan["focal"]
        place_markers(context)
        if plan.get("hold", 0.0) > 0:
            markers = marker_objects(context.scene)
            if markers:
                markers[-1][PROP_HOLD] = float(
                    plan["hold"]
                )  # a "hold 2 s" in the text pauses the camera on arrival
        cam, keys, last = build_path(context)
        label = shot_plan.PRESETS[shot_plan.resolve_preset(plan["preset"])][0]
        held = f", hold {plan['hold']:g} s" if plan.get("hold", 0.0) > 0 else ""
        self.report(
            {"INFO"},
            f"{label}, {plan['duration']:g} s at {plan['focal']:g} mm{held}: {keys} keyframes, frames {context.scene.frame_start} to {last}",
        )
        return {"FINISHED"}


# -- panel ---------------------------------------------------------------------


def draw_shot_planner(layout, context):
    scene = context.scene
    props = scene.scenario_shot
    box = layout.box()
    box.label(text="Camera path", icon="CAMERA_DATA")
    row = box.row(align=True)
    row.prop(
        props, "description", text="", placeholder="One move + seconds + lens: orbit, 8 s, 50 mm"
    )
    row.operator(
        SCENARIO_OT_shot_from_description.bl_idname, text="Plan", icon="OUTLINER_OB_CAMERA"
    )
    row = box.row(align=True)
    row.prop(props, "preset", text="")
    row.operator(SCENARIO_OT_shot_place_markers.bl_idname, text="Place markers", icon="PINNED")
    render_lane = scene.scenario.lane_state("render_video")
    matched = bool(render_lane is not None and render_lane.match_timeline)
    col = box.column(align=True)
    duration_row = col.row(align=True)
    duration_row.enabled = (
        not matched
    )  # with Match timeline on, the video model duration drives the shot
    duration_row.prop(props, "duration")
    col.prop(props, "focal")
    col.prop(props, "start_frame")
    start, end, fps = frame_range(scene)
    box.label(text=f"Frames {start} to {end} at {fps:g} fps", icon="TIME")
    if matched:
        box.label(text=f"Camera path duration: {props.duration:g} s; build the path before capture")
    row = box.row(align=True)
    row.prop(props, "aim_at_subject")
    row.prop(props, "use_selection")
    row.prop(props, "closed_loop")
    markers = marker_objects(scene)
    row = box.row(align=True)
    row.label(text=f"Markers  {len(markers)}", icon="PINNED")
    row.operator(SCENARIO_OT_shot_add_marker.bl_idname, text="At cursor", icon="ADD")
    row.operator(
        SCENARIO_OT_shot_add_marker_from_view.bl_idname, text="From view", icon="VIEW_CAMERA"
    )
    row.operator(SCENARIO_OT_shot_remove_last_marker.bl_idname, text="", icon="REMOVE")
    row.operator(SCENARIO_OT_shot_clear_markers.bl_idname, text="", icon="X")
    # per-marker pause: select a Shot marker and set how long the camera holds still there (the move steals the rest)
    marker = active_marker(context)
    if marker is not None and PROP_HOLD in marker:
        box.prop(
            marker, f'["{PROP_HOLD}"]', text=f"Hold at Shot {int(marker.get(PROP_INDEX, 0))} (s)"
        )
    row = box.row(align=True)
    row.scale_y = 1.3
    row.operator(SCENARIO_OT_shot_build_path.bl_idname, text="Build camera path", icon="ANIM")
    row.operator(SCENARIO_OT_shot_preview.bl_idname, text="", icon="PLAY")
    cam = shot_camera(scene)
    if cam is not None or markers:
        row = box.row(align=True)
        row.operator(SCENARIO_OT_shot_clear_path.bl_idname, text="Clear path", icon="TRASH")
    if cam is not None:
        frames = int(cam.get("scenario_shot_frames", scene.frame_end - scene.frame_start + 1))
        source = str(cam.get("scenario_shot_source", "markers"))
        label = shot_plan.PRESETS[source][0] if source in shot_plan.PRESETS else "markers"
        box.label(
            text=f"{cam.name}: {frames} frames from {label}"
            + (", closed loop" if props.closed_loop else ""),
            icon="CHECKMARK",
        )


CLASSES = (
    ScenarioShotProps,
    SCENARIO_OT_shot_add_marker,
    SCENARIO_OT_shot_add_marker_from_view,
    SCENARIO_OT_shot_remove_last_marker,
    SCENARIO_OT_shot_clear_markers,
    SCENARIO_OT_shot_renumber,
    SCENARIO_OT_shot_place_markers,
    SCENARIO_OT_shot_build_path,
    SCENARIO_OT_shot_clear_path,
    SCENARIO_OT_shot_preview,
    SCENARIO_OT_shot_from_description,
)


def register():
    for cls in CLASSES:
        bpy.utils.register_class(cls)
    bpy.types.Scene.scenario_shot = PointerProperty(type=ScenarioShotProps)


def unregister():
    if hasattr(bpy.types.Scene, "scenario_shot"):
        del bpy.types.Scene.scenario_shot
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)
