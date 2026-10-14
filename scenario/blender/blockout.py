# SPDX-FileCopyrightText: 2026 Scenario Inc.
# SPDX-License-Identifier: GPL-3.0-or-later
"""Prompt to Blockout: the Scenario LLM designs a structured greybox (rich primitives, categories, groups) that the
builder places in the viewport, colour-coded and organised into sub-collections, to refine or replace with generated
assets. The design runs off the main thread (LLM); the build runs on the main thread from the stored plan."""

import json
import textwrap
from math import radians

import bmesh
import bpy
from bpy.props import StringProperty

from ..core.scene import blockout as core
from . import runtime

COLLECTION = "Blockout"
MARK = "scenario_blockout"
_MESHES = {}


# -- primitive meshes (shared unit shapes in a 1 m cube; per-object scale gives the metre size) -----------------------
def _hand_mesh(name, verts, faces):
    mesh = bpy.data.meshes.new(name)
    mesh.from_pydata(verts, [], faces)
    mesh.update()
    return mesh


def _bmesh_mesh(name, build):
    mesh = bpy.data.meshes.new(name)
    bm = bmesh.new()
    build(bm)
    bm.to_mesh(mesh)
    bm.free()
    mesh.update()
    return mesh


def _build_box():
    verts = [
        (-0.5, -0.5, -0.5),
        (0.5, -0.5, -0.5),
        (0.5, 0.5, -0.5),
        (-0.5, 0.5, -0.5),
        (-0.5, -0.5, 0.5),
        (0.5, -0.5, 0.5),
        (0.5, 0.5, 0.5),
        (-0.5, 0.5, 0.5),
    ]
    faces = [(0, 1, 2, 3), (4, 5, 6, 7), (0, 1, 5, 4), (1, 2, 6, 5), (2, 3, 7, 6), (3, 0, 4, 7)]
    return _hand_mesh("Scenario Blockout box", verts, faces)


def _build_wedge():
    # a ramp / lean-to roof rising from -x (low) to +x (high)
    verts = [
        (-0.5, -0.5, -0.5),
        (0.5, -0.5, -0.5),
        (0.5, 0.5, -0.5),
        (-0.5, 0.5, -0.5),
        (0.5, -0.5, 0.5),
        (0.5, 0.5, 0.5),
    ]
    faces = [(0, 1, 2, 3), (1, 4, 5, 2), (0, 4, 1), (3, 2, 5), (0, 3, 5, 4)]
    return _hand_mesh("Scenario Blockout wedge", verts, faces)


def _mesh_for(primitive):
    mesh = _MESHES.get(primitive)
    try:
        if mesh is not None and mesh.name in bpy.data.meshes:
            return mesh
    except ReferenceError:
        pass  # the cached mesh was removed (e.g. a fresh file); rebuild it
    if primitive == "wedge":
        mesh = _build_wedge()
    elif primitive == "cylinder":
        mesh = _bmesh_mesh(
            "Scenario Blockout cylinder",
            lambda bm: bmesh.ops.create_cone(
                bm, cap_ends=True, cap_tris=False, segments=24, radius1=0.5, radius2=0.5, depth=1.0
            ),
        )
    elif primitive == "cone":
        mesh = _bmesh_mesh(
            "Scenario Blockout cone",
            lambda bm: bmesh.ops.create_cone(
                bm, cap_ends=True, cap_tris=False, segments=24, radius1=0.5, radius2=0.0, depth=1.0
            ),
        )
    elif primitive == "sphere":
        mesh = _bmesh_mesh(
            "Scenario Blockout sphere",
            lambda bm: bmesh.ops.create_uvsphere(bm, u_segments=20, v_segments=12, radius=0.5),
        )
    else:  # box and plane share the cube (a plane is just a thin box)
        mesh = _build_box()
    _MESHES[primitive] = mesh
    return mesh


# -- collections ------------------------------------------------------------------------------------------------------
def _sub_collection(name, parent):
    coll = bpy.data.collections.new(name)
    coll[MARK] = True
    parent.children.link(coll)
    return coll


def _owned_tree(scene):
    root = scene.scenario_blockout.built_collection
    if root is None:
        return None
    collections = {root, *root.children_recursive}
    parents = tuple(bpy.data.collections) + tuple(item.collection for item in bpy.data.scenes)
    if root not in tuple(scene.collection.children):
        raise ValueError("The Blockout collection moved; inspect it before rebuilding")
    for coll in collections:
        if coll.get(MARK) is not True:
            raise ValueError(
                "The Blockout contains another collection; preserve it before rebuilding"
            )
        allowed = {scene.collection} if coll == root else collections
        if any(coll in tuple(parent.children) and parent not in allowed for parent in parents):
            raise ValueError("The Blockout collection is shared; make it local before rebuilding")
        for obj in coll.objects:
            if MARK not in obj or any(owner not in collections for owner in obj.users_collection):
                raise ValueError(
                    "The Blockout contains unrelated or shared objects; preserve them first"
                )
    return root


def _remove_tree(root):
    collections = (root, *tuple(root.children_recursive))
    objects = {obj for coll in collections for obj in coll.objects}
    for obj in objects:
        bpy.data.objects.remove(obj, do_unlink=True)
    for coll in reversed(collections):
        bpy.data.collections.remove(coll)
    return len(objects)


def clear_blockout(scene):
    """Clear only this scene's explicitly owned, unshared generated collection."""
    root = _owned_tree(scene)
    return _remove_tree(root) if root is not None else 0


def _colour_the_viewport():
    """Show the per-object greybox colours: switch every 3D viewport's solid shading to colour by object."""
    for window in bpy.context.window_manager.windows:
        for area in window.screen.areas:
            if area.type == "VIEW_3D":
                area.spaces.active.shading.color_type = "OBJECT"


def build_blockout(context, elements):
    """Stage a complete local build before replacing only this scene's owned tree."""
    scene = context.scene
    old = _owned_tree(scene)
    root = _sub_collection(COLLECTION, scene.collection)
    groups, created = {}, []
    try:
        for el in elements:
            group = el.get("group") or COLLECTION
            category = el.get("category") or core.DEFAULT_CATEGORY
            if group == COLLECTION:
                gcoll = root
            else:
                gcoll = groups.get(group)
                if gcoll is None:
                    gcoll = _sub_collection(group, root)
                    groups[group] = gcoll
            obj = bpy.data.objects.new(
                el.get("name") or "Block", _mesh_for(el.get("primitive") or "box")
            )
            created.append(obj)
            gcoll.objects.link(obj)
            obj.location = el.get("position") or (0.0, 0.0, 0.0)
            obj.scale = el.get("size") or (1.0, 1.0, 1.0)
            obj.rotation_euler = (0.0, 0.0, radians(el.get("rotation", 0.0) or 0.0))
            r, g, b = core.CATEGORIES.get(category, core.CATEGORIES[core.DEFAULT_CATEGORY])[1]
            obj.color = (r, g, b, 1.0)
            obj[MARK] = category
            obj.display_type = "SOLID"
    except BaseException:
        _remove_tree(root)
        raise
    # All decoding and creation succeeded before touching the old collection.
    # After publication starts, never destroy the staged replacement on failure.
    scene.scenario_blockout.built_collection = root
    if old is not None:
        _remove_tree(old)
    root.name = COLLECTION
    for name, coll in groups.items():
        coll.name = name
    for obj, element in zip(created, elements, strict=True):
        obj.name = element.get("name") or "Block"
    if created and not bpy.app.background:
        _colour_the_viewport()
    return created


# -- state ------------------------------------------------------------------------------------------------------------
def _store(scene, elements):
    scene.scenario_blockout.plan_json = json.dumps(elements)


def stored_plan(scene):
    try:
        data = json.loads(scene.scenario_blockout.plan_json or "[]")
    except ValueError:
        return []
    return data if isinstance(data, list) else []


def on_blockout_plan(payload):
    """Ignore retired unbound events; the selected JobSession delivers plans."""


def _design(context, prompt, previous=None):
    runtime.ensure_blockout_jobs().quote(
        context.scene, "REFINE" if previous is not None else "DESIGN"
    )


class SCENARIO_OT_blockout_approve(bpy.types.Operator):
    bl_idname = "scenario.blockout_approve"
    bl_label = "Generate Blockout plan"
    bl_description = (
        "Approve the displayed exact price once and prepare a plan without changing geometry"
    )
    quote_id: StringProperty(options={"SKIP_SAVE"})
    approved_cost: StringProperty(options={"SKIP_SAVE"})

    @classmethod
    def poll(cls, context):
        from .operators import _network_poll

        return _network_poll(cls, context)

    def execute(self, context):
        try:
            runtime.ensure_blockout_jobs().approve(
                self.quote_id, context.scene, approved_cost=self.approved_cost
            )
        except Exception:
            self.report(
                {"WARNING"},
                "Blockout approval is stale or could not be submitted; inspect saved jobs",
            )
            return {"CANCELLED"}
        return {"FINISHED"}


def draw_status(layout, scene):
    from ..core.ui.costs import format_approved_cost

    jobs = runtime.state.blockout_jobs
    item = jobs.current(scene) if jobs is not None else None
    if item is None:
        return
    if item.phase == "READY":
        from .blockout_jobs import binding

        row = layout.row()
        row.enabled = binding(scene) == item.binding
        op = row.operator(
            "scenario.blockout_approve",
            text=f"Generate plan ({format_approved_cost(item.cost)})",
            icon="PLAY",
        )
        op.quote_id, op.approved_cost = item.identifier, item.cost
    elif item.phase == "QUOTING":
        layout.label(text="Getting plan price...", icon="TIME")
    elif item.phase in {"SUBMITTING", "POLLING", "LISTING", "READING"}:
        layout.label(text="Preparing Blockout plan...", icon="TIME")
    elif item.phase == "DONE":
        layout.label(text="Plan ready. Build it to update geometry.", icon="CHECKMARK")
    elif item.phase == "ERROR":
        from .blockout_jobs import CANCELED

        if item.error == CANCELED:
            # A local cancellation sent nothing: there is no saved job to review.
            for index, line in enumerate(textwrap.wrap(CANCELED, 32)):
                layout.label(text=line, icon="INFO" if index == 0 else "NONE")
            return
        layout.label(text="Blockout needs review", icon="ERROR")
        if item.request_id:
            layout.operator(
                "scenario.inspect_saved_jobs", text="Inspect saved jobs", icon="VIEWZOOM"
            )
        else:
            layout.label(text="Check inputs and request a new price")


class SCENARIO_OT_blockout_design(bpy.types.Operator):
    bl_idname = "scenario.blockout_design"
    bl_label = "Design blockout"
    bl_description = "Request the exact price for a Blockout plan before approving generation"

    @classmethod
    def poll(cls, context):
        from .operators import _network_poll

        return _network_poll(cls, context)

    def execute(self, context):
        props = context.scene.scenario_blockout
        prompt = props.prompt.strip()
        if not prompt:
            self.report({"WARNING"}, "Describe the scene to block out first")
            return {"CANCELLED"}
        try:
            _design(context, prompt)
        except Exception:
            self.report(
                {"WARNING"}, "Could not request a Blockout price; check inputs and connection"
            )
            return {"CANCELLED"}
        return {"FINISHED"}


class SCENARIO_OT_blockout_refine(bpy.types.Operator):
    bl_idname = "scenario.blockout_refine"
    bl_label = "Refine blockout"
    bl_description = (
        "Request the exact price for a refined Blockout plan before approving generation"
    )

    @classmethod
    def poll(cls, context):
        from .operators import _network_poll

        return _network_poll(cls, context)

    def execute(self, context):
        props = context.scene.scenario_blockout
        change = props.refine.strip()
        previous = stored_plan(context.scene)
        if not change:
            self.report({"WARNING"}, "Describe the change to make")
            return {"CANCELLED"}
        if not previous:
            self.report({"WARNING"}, "Design a blockout first, then refine it")
            return {"CANCELLED"}
        try:
            _design(context, change, previous=previous)
        except Exception:
            self.report(
                {"WARNING"}, "Could not request a Blockout price; check inputs and connection"
            )
            return {"CANCELLED"}
        return {"FINISHED"}


class SCENARIO_OT_blockout_build(bpy.types.Operator):
    bl_idname = "scenario.blockout_build"
    bl_label = "Rebuild blockout"
    bl_description = "Rebuild the greybox from the current plan without calling the LLM again"
    bl_options = {"REGISTER", "UNDO"}

    def execute(self, context):
        elements = core.parse_plan(json.dumps(stored_plan(context.scene)))
        if not elements:
            self.report({"WARNING"}, "No blockout plan yet; design one first")
            return {"CANCELLED"}
        try:
            created = build_blockout(context, elements)
        except (ValueError, RuntimeError):
            self.report(
                {"WARNING"}, "Could not build safely; inspect the existing Blockout collection"
            )
            return {"CANCELLED"}
        self.report({"INFO"}, f"Rebuilt {len(created)} elements")
        return {"FINISHED"}


class SCENARIO_OT_blockout_clear(bpy.types.Operator):
    bl_idname = "scenario.blockout_clear"
    bl_label = "Clear blockout"
    bl_description = "Delete the Blockout collection and forget the plan"
    bl_options = {"REGISTER", "UNDO"}

    def invoke(self, context, event):
        return context.window_manager.invoke_confirm(self, event)

    def execute(self, context):
        try:
            removed = clear_blockout(context.scene)
        except (ValueError, RuntimeError):
            self.report(
                {"WARNING"}, "The Blockout contains shared or unrelated data; preserve it first"
            )
            return {"CANCELLED"}
        context.scene.scenario_blockout.plan_json = ""
        self.report({"INFO"}, f"Cleared {removed} object(s)")
        return {"FINISHED"}


CLASSES = (
    SCENARIO_OT_blockout_design,
    SCENARIO_OT_blockout_approve,
    SCENARIO_OT_blockout_refine,
    SCENARIO_OT_blockout_build,
    SCENARIO_OT_blockout_clear,
)


def register():
    for cls in CLASSES:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)
