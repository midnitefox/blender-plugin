# SPDX-FileCopyrightText: 2026 Scenario Inc.
# SPDX-FileCopyrightText: 2026 Open Studio contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Owned local blockouts, complete-plan staging and persistent edit guards.

Primitive geometry is adapted from the upstream local blockout builder. No
legacy panels, service jobs or Scene.scenario_blockout properties are imported.
"""

import hashlib
import json
import threading
import uuid
from math import radians

import bmesh
import bpy

from ..core.scene import assistant_blockout as core
from . import file_state

STATE = "open_studio_blockout"
OWNER = "open_studio_blockout_owner"
ELEMENT = "open_studio_blockout_element"
PLAN = "open_studio_blockout_plan"
REVISION = "open_studio_blockout_revision"
FINGERPRINT = "open_studio_blockout_fingerprint"


def _scene():
    if threading.current_thread() is not threading.main_thread():
        raise ValueError("Build blockouts on Blender's main thread")
    scene = bpy.context.scene
    if (
        not scene.is_editable
        or scene.library
        or scene.override_library
        or bpy.context.mode != "OBJECT"
    ):
        raise ValueError("Choose a local editable scene in Object Mode")
    return scene


def _root(scene):
    owner = scene.get(STATE)
    if owner is None:
        return None
    roots = [
        collection
        for collection in bpy.data.collections
        if collection.get(OWNER) == owner and PLAN in collection
    ]
    if len(roots) != 1:
        raise ValueError(
            "The owned blockout collection is missing or ambiguous; preserve it manually"
        )
    return roots[0]


def _value(value):
    if value is None or type(value) in {str, float, int, bool}:
        return value
    if isinstance(value, bpy.types.ID):
        return (value.bl_rna.identifier, value.name_full)
    if hasattr(value, "to_dict"):
        return _value(value.to_dict())
    if isinstance(value, dict):
        return {key: _value(item) for key, item in sorted(value.items())}
    if isinstance(value, set):
        return sorted(_value(item) for item in value)
    return [_value(item) for item in value]


def _settings(value, exclude=()):
    result = {}
    for prop in value.bl_rna.properties:
        if (
            not prop.is_readonly
            and prop.identifier not in exclude
            and prop.type in {"BOOLEAN", "INT", "FLOAT", "STRING", "ENUM"}
        ):
            result[prop.identifier] = _value(getattr(value, prop.identifier))
    if isinstance(value, bpy.types.ID):
        result["custom"] = {
            key: _value(value[key]) for key in sorted(value.keys()) if key not in exclude
        }
    return result


def _mesh_state(mesh):
    if mesh.shape_keys is not None:
        raise ValueError("A blockout mesh has shape keys; preserve those edits before replacing it")
    return {
        "settings": _settings(mesh),
        "vertices": [tuple(vertex.co) for vertex in mesh.vertices],
        "edges": [tuple(edge.vertices) for edge in mesh.edges],
        "polygons": [
            (tuple(face.vertices), face.material_index, face.use_smooth) for face in mesh.polygons
        ],
        "attributes": [
            (
                attribute.name,
                attribute.domain,
                attribute.data_type,
                [_settings(item) for item in attribute.data],
            )
            for attribute in mesh.attributes
        ],
        "materials": [material.name if material else None for material in mesh.materials],
    }


def _material_state(material):
    tree = material.node_tree
    if material.animation_data or (tree and tree.animation_data):
        raise ValueError(
            "A blockout material is animated; preserve those edits before replacing it"
        )
    return {
        "settings": _settings(material),
        "nodes": [
            (_settings(node), [_settings(socket) for socket in node.inputs]) for node in tree.nodes
        ]
        if tree
        else [],
        "links": [
            (
                link.from_node.name,
                link.from_socket.identifier,
                link.to_node.name,
                link.to_socket.identifier,
            )
            for link in tree.links
        ]
        if tree
        else [],
    }


def _owned_data(root):
    collections = (root, *root.children_recursive)
    objects = {obj for collection in collections for obj in collection.objects}
    meshes = {obj.data for obj in objects if obj.type == "MESH"}
    materials = {material for mesh in meshes for material in mesh.materials if material}
    return collections, objects, meshes, materials


def _fingerprint(root):
    collections, objects, meshes, materials = _owned_data(root)
    result = {
        "collections": [
            (
                _settings(collection, {FINGERPRINT}),
                sorted(obj.name for obj in collection.objects),
                sorted(child.name for child in collection.children),
            )
            for collection in sorted(collections, key=lambda value: value.name)
        ],
        "objects": [],
        "meshes": [
            (mesh.name, _mesh_state(mesh)) for mesh in sorted(meshes, key=lambda value: value.name)
        ],
        "materials": [
            (material.name, _material_state(material))
            for material in sorted(materials, key=lambda value: value.name)
        ],
    }
    for obj in sorted(objects, key=lambda value: value.name):
        if (
            obj.type != "MESH"
            or obj.parent
            or obj.modifiers
            or obj.constraints
            or obj.animation_data
            or obj.vertex_groups
            or obj.instance_collection
            or obj.rigid_body
            or obj.rigid_body_constraint
        ):
            raise ValueError("The blockout has edited objects; preserve them before replacing it")
        result["objects"].append(
            (
                # These derived matrices/dimensions lag behind RNA transforms
                # until depsgraph evaluation. Location/rotation/scale and mesh
                # coordinates carry the actual edit authority.
                _settings(obj, {"matrix_local", "matrix_world", "dimensions"}),
                _settings(obj.display),
                obj.data.name,
                [
                    (layer.name, obj.name in layer.objects, obj.hide_get(view_layer=layer))
                    for scene in obj.users_scene
                    for layer in scene.view_layers
                    if obj.name not in layer.objects or obj.hide_get(view_layer=layer)
                ],
                [
                    (slot.link, slot.material.name if slot.material else None)
                    for slot in obj.material_slots
                ],
            )
        )
    return hashlib.sha256(core.serialize(result).encode()).hexdigest()


def _guard(scene, root, revision):
    if not isinstance(revision, str) or revision != root.get(REVISION):
        raise ValueError("Blockout revision is stale; inspect blockout_plan before replacing it")
    collections, objects, meshes, materials = _owned_data(root)
    owned = set(collections) | objects | meshes | materials
    owner = root.get(OWNER)
    if root not in tuple(scene.collection.children) or any(
        value.library or value.override_library or value.get(OWNER) != owner for value in owned
    ):
        raise ValueError("The blockout contains unrelated, moved or linked data; preserve it first")
    if any(
        any(collection not in collections for collection in obj.users_collection) for obj in objects
    ):
        raise ValueError(
            "Blockout objects are linked outside their owned collection; preserve them first"
        )
    parents = tuple(bpy.data.collections) + tuple(item.collection for item in bpy.data.scenes)
    for collection in collections:
        permitted = {scene.collection} if collection == root else set(collections)
        if any(
            collection in tuple(parent.children) and parent not in permitted for parent in parents
        ):
            raise ValueError(
                "A blockout collection is linked outside its owned tree; preserve it first"
            )
    allowed = (
        owned
        | {scene, scene.collection}
        | {material.node_tree for material in materials if material.node_tree}
    )
    for users in bpy.data.user_map(subset=owned).values():
        if any(user not in allowed for user in users):
            raise ValueError(
                "The blockout data is shared or referenced outside its owned tree; preserve it first"
            )
    if _fingerprint(root) != root.get(FINGERPRINT):
        raise ValueError("The blockout was edited; preserve those edits before replacing it")


def _mesh(primitive):
    mesh = bpy.data.meshes.new("Open Studio " + primitive)
    try:
        if primitive == "wedge":
            vertices = [
                (-0.5, -0.5, -0.5),
                (0.5, -0.5, -0.5),
                (0.5, 0.5, -0.5),
                (-0.5, 0.5, -0.5),
                (0.5, -0.5, 0.5),
                (0.5, 0.5, 0.5),
            ]
            faces = [(0, 3, 2, 1), (1, 2, 5, 4), (0, 1, 4), (3, 5, 2), (0, 4, 5, 3)]
            mesh.from_pydata(vertices, [], faces)
        else:
            bm = bmesh.new()
            try:
                if primitive in {"box", "plane"}:
                    bmesh.ops.create_cube(bm, size=1)
                elif primitive in {"cylinder", "cone"}:
                    bmesh.ops.create_cone(
                        bm,
                        cap_ends=True,
                        cap_tris=False,
                        segments=24,
                        radius1=0.5,
                        radius2=0.5 if primitive == "cylinder" else 0,
                        depth=1,
                    )
                else:
                    bmesh.ops.create_uvsphere(bm, u_segments=20, v_segments=12, radius=0.5)
                bm.to_mesh(mesh)
            finally:
                bm.free()
        mesh.update()
        return mesh
    except BaseException:
        bpy.data.meshes.remove(mesh)
        raise


def _discard(root):
    collections, objects, meshes, materials = _owned_data(root)
    for obj in objects:
        bpy.data.objects.remove(obj, do_unlink=True)
    for collection in reversed(collections):
        bpy.data.collections.remove(collection)
    for mesh in meshes:
        if mesh.users == 0:
            bpy.data.meshes.remove(mesh)
    for material in materials:
        if material.users == 0:
            bpy.data.materials.remove(material)
    return len(objects)


def _stage(plan, owner):
    root = bpy.data.collections.new("Open Studio " + plan["name"])
    root[OWNER] = owner
    root[PLAN] = core.serialize(plan)
    root[REVISION] = uuid.uuid4().hex
    groups = {}
    # Track new datablocks even if a builder failed before linking them.
    before_meshes, before_materials, before_objects = (
        set(bpy.data.meshes),
        set(bpy.data.materials),
        set(bpy.data.objects),
    )
    try:
        for element in plan["elements"]:
            group = groups.get(element["group"])
            if group is None:
                group = bpy.data.collections.new(element["group"])
                group[OWNER] = owner
                root.children.link(group)
                groups[element["group"]] = group
            mesh = _mesh(element["primitive"])
            mesh[OWNER] = owner
            obj = bpy.data.objects.new(element["name"], mesh)
            group.objects.link(obj)
            obj[OWNER], obj[ELEMENT] = owner, element["id"]
            obj.location, obj.scale = element["position"], element["size"]
            obj.rotation_euler = tuple(map(radians, element["rotation_degrees"]))
            obj.color = (*element["color"], 1)
            material = bpy.data.materials.new(element["name"] + " blockout color")
            material[OWNER] = owner
            material.diffuse_color = obj.color
            material.use_nodes = True
            shader = next(
                node for node in material.node_tree.nodes if node.type == "BSDF_PRINCIPLED"
            )
            shader.inputs["Base Color"].default_value = obj.color
            mesh.materials.append(material)
        return root
    except BaseException:
        _discard(root)
        for collection, before in (
            (bpy.data.objects, before_objects),
            (bpy.data.meshes, before_meshes),
            (bpy.data.materials, before_materials),
        ):
            for value in set(collection) - before:
                collection.remove(value)
        raise


def _history_before():
    prefs = bpy.context.preferences.edit
    if not bpy.app.background and prefs.use_global_undo and prefs.undo_steps > 1:
        if not file_state._push("Before Open Studio blockout"):
            raise ValueError("Blender could not prepare blockout undo history")


def inspect_plan():
    scene = _scene()
    root = _root(scene)
    if root is None:
        return {"plan": None, "revision": None, "editable": True}
    result = {
        "plan": json.loads(root[PLAN]),
        "revision": root.get(REVISION),
        "collection": root.name,
        "editable": True,
    }
    try:
        _guard(scene, root, result["revision"])
    except ValueError as error:
        result.update(editable=False, warning=str(error))
    return result


def build(plan, expected_revision=None):
    normalized = core.validate_plan(plan)
    scene = _scene()
    old = _root(scene)
    if old is not None:
        _guard(scene, old, expected_revision)
        if old[PLAN] == core.serialize(normalized):
            return dict(inspect_plan(), reused=True)
    elif expected_revision is not None:
        raise ValueError("No blockout has that revision in this scene")
    owner = scene.get(STATE) or uuid.uuid4().hex
    _history_before()
    staged = _stage(normalized, owner)
    try:
        staged[FINGERPRINT] = _fingerprint(staged)
        if old is not None:
            _guard(scene, old, expected_revision)
        scene.collection.children.link(staged)
    except BaseException:
        _discard(staged)
        raise
    # Publish the new owner before cleanup. A rare cleanup error leaves both
    # trees for explicit inspection; never retry deletion based on guessed state.
    if old is not None:
        try:
            _discard(old)
        except Exception:
            scene[STATE] = owner
            file_state.mark_modified("Open Studio blockout cleanup needs inspection")
            raise RuntimeError("Blockout replacement cleanup needs inspection") from None
    scene[STATE] = owner
    for layer in scene.view_layers:
        layer.update()
    staged[FINGERPRINT] = _fingerprint(staged)
    file_state.mark_modified("Open Studio assistant blockout")
    return dict(inspect_plan(), reused=False)


def clear(expected_revision):
    scene = _scene()
    root = _root(scene)
    if root is None:
        raise ValueError("There is no owned blockout to clear in this scene")
    _guard(scene, root, expected_revision)
    _history_before()
    try:
        count = _discard(root)
        del scene[STATE]
    except Exception:
        file_state.mark_modified("Open Studio blockout cleanup needs inspection")
        raise RuntimeError(
            "Blockout cleanup needs inspection; preserve the remaining data"
        ) from None
    file_state.mark_modified("Open Studio clear blockout")
    return {"removed_objects": count, "plan": None, "revision": None}
