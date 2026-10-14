# SPDX-FileCopyrightText: 2026 Open Studio contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Complete local shot/timeline recipes and explicit embedded GLB clip selection."""

import json
import math
import re
import struct

from . import assistant_blockout as blockout
from .glb import inspect_glb

MAX_ACTORS = 16
MAX_KEYS = 128


def _fields(value, required, optional=()):
    if (
        not isinstance(value, dict)
        or not set(required) <= value.keys()
        or (value.keys() - set(required) - set(optional))
    ):
        raise ValueError("Use the complete documented recipe fields without unknown fields")


def _integer(value, label, low, high):
    if type(value) is not int or not low <= value <= high:
        raise ValueError(f"{label} must be an integer from {low} to {high}")
    return value


def _number(value, label, low, high):
    if type(value) not in {int, float} or not math.isfinite(value) or not low <= value <= high:
        raise ValueError(f"{label} must be a finite number from {low} to {high}")
    return value


def _identifier(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,60}", value):
        raise ValueError("Use a 1 to 60 character letter/digit/_/- element id")
    return value


def _timing(raw):
    fps = _integer(raw["fps"], "FPS", 1, 120)
    width = _integer(raw["width"], "Width", 64, 4096)
    height = _integer(raw["height"], "Height", 64, 2160)
    if width % 2 or height % 2:
        raise ValueError("Use even film dimensions")
    return dict(
        name=blockout._label(raw["name"], "Scene name"), fps=fps, width=width, height=height
    )


def _transform(raw):
    return {
        "position": blockout._vector(raw["position"], "Position", -2000, 2000),
        "rotation_degrees": blockout._vector(
            raw.get("rotation_degrees", [0, 0, 0]), "Rotation", -360, 360
        ),
        "scale": blockout._vector(raw.get("scale", [1, 1, 1]), "Scale", 0.001, 500),
    }


def _motion(keys, frames, *, path=False, required=False):
    if not isinstance(keys, list) or not (1 if required else 0) <= len(keys) <= MAX_KEYS:
        raise ValueError("Use at most 128 ordered keys; a camera path needs at least one")
    result, previous = [], 0
    for key in keys:
        _fields(
            key,
            {"frame", "position"} if path else {"frame", "position", "rotation_degrees", "scale"},
        )
        frame = _integer(key["frame"], "Key frame", 1, frames)
        if frame <= previous or (previous == 0 and frame != 1):
            raise ValueError("Motion starts at frame 1 and key frames must strictly increase")
        values = (
            {"position": blockout._vector(key["position"], "Position", -2000, 2000)}
            if path
            else _transform(key)
        )
        result.append(dict(frame=frame, **values))
        previous = frame
    return result


def _point(keys, frame):
    for left, right in zip(keys, keys[1:], strict=False):
        if frame <= right["frame"]:
            factor = (frame - left["frame"]) / (right["frame"] - left["frame"])
            return [
                a + (b - a) * factor
                for a, b in zip(left["position"], right["position"], strict=True)
            ]
    return keys[-1]["position"]


def _camera(raw, frames):
    _fields(raw, {"lens_mm", "path", "target_path"})
    result = {
        "lens_mm": _number(raw["lens_mm"], "Lens", 8, 400),
        "path": _motion(raw["path"], frames, path=True, required=True),
        "target_path": _motion(raw["target_path"], frames, path=True, required=True),
    }
    times = sorted(
        {key["frame"] for field in ("path", "target_path") for key in result[field]} | {frames}
    )
    relative = [
        [
            a - b
            for a, b in zip(
                _point(result["path"], t), _point(result["target_path"], t), strict=True
            )
        ]
        for t in times
    ]
    # Piecewise linear relative trajectories must never pass through zero.
    for a, b in zip(relative, relative[1:] or relative, strict=False):
        delta = [y - x for x, y in zip(a, b, strict=True)]
        norm = sum(v * v for v in delta)
        factor = (
            max(0, min(1, -sum(x * v for x, v in zip(a, delta, strict=True)) / norm)) if norm else 0
        )
        if sum((x + factor * v) ** 2 for x, v in zip(a, delta, strict=True)) < 1e-8:
            raise ValueError("Camera and target paths must stay apart throughout the shot")
    return result


def validate_shot(raw):
    _fields(raw, {"name", "fps", "width", "height", "frames", "primitives", "camera", "actors"})
    result = _timing(raw)
    frames = _integer(raw["frames"], "Shot frame count", 1, min(7200, result["fps"] * 120))
    result["frames"] = frames
    primitives = raw["primitives"]
    if not isinstance(primitives, list) or len(primitives) > blockout.MAX_ELEMENTS:
        raise ValueError("Use zero to 200 categorized primitives")
    clean = []
    for primitive in primitives:
        if not isinstance(primitive, dict):
            raise ValueError("Use complete primitive objects")
        clean.append({key: value for key, value in primitive.items() if key != "motion"})
    normalized = (
        blockout.validate_plan({"name": result["name"], "elements": clean})["elements"]
        if clean
        else []
    )
    result["primitives"] = [
        dict(item, motion=_motion(raw_item.get("motion", []), frames))
        for item, raw_item in zip(normalized, primitives, strict=True)
    ]
    result["camera"] = _camera(raw["camera"], frames)
    actors = raw["actors"]
    if not isinstance(actors, list) or len(actors) > MAX_ACTORS:
        raise ValueError("Use zero to 16 explicitly bound GLB actors")
    result["actors"], ids = [], {item["id"] for item in normalized}
    for actor in actors:
        _fields(
            actor,
            {"id", "name", "filename", "sha256", "position"},
            {"rotation_degrees", "scale", "motion", "clip"},
        )
        identifier = _identifier(actor["id"])
        if identifier in ids:
            raise ValueError("Primitive and actor ids must be unique within the shot")
        ids.add(identifier)
        filename, digest = actor["filename"], actor["sha256"]
        if not isinstance(filename, str) or not re.fullmatch(
            r"[A-Za-z0-9][A-Za-z0-9_. -]{0,179}\.glb", filename, re.IGNORECASE
        ):
            raise ValueError("Use an explicit inbox GLB filename without directories")
        if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise ValueError("Every actor needs its exact lowercase SHA-256 digest")
        clip = actor.get("clip")
        if clip is not None:
            _fields(clip, {"animation_index", "mode"}, {"speed", "phase"})
            if clip["mode"] not in ("once", "loop", "hold"):
                raise ValueError("Choose once, loop or hold animation playback")
            clip = {
                "animation_index": _integer(clip["animation_index"], "Animation index", 0, 127),
                "mode": clip["mode"],
                "speed": _number(clip.get("speed", 1), "Animation speed", 0.1, 10),
                "phase": _number(clip.get("phase", 0), "Held animation phase", 0, 1),
            }
            if clip["mode"] != "hold" and clip["phase"]:
                raise ValueError("phase is only used for a held pose")
        result["actors"].append(
            dict(
                id=identifier,
                name=blockout._label(actor["name"], "Actor name"),
                filename=filename,
                sha256=digest,
                **_transform(actor),
                motion=_motion(actor.get("motion", []), frames),
                clip=clip,
            )
        )
    if not normalized and not result["actors"]:
        raise ValueError("A shot needs at least one primitive or actor")
    return result


def validate_timeline(raw):
    _fields(raw, {"name", "fps", "width", "height", "shots"})
    result = _timing(raw)
    if not isinstance(raw["shots"], list) or not 1 <= len(raw["shots"]) <= 128:
        raise ValueError("A timeline needs 1 to 128 ordered shot references")
    result["shots"], total = [], 0
    for shot in raw["shots"]:
        _fields(shot, {"scene_name", "scene_id", "start_frame", "end_frame"})
        identifier = shot["scene_id"]
        if not isinstance(identifier, str) or not re.fullmatch(r"[0-9a-f]{32}", identifier):
            raise ValueError("Use the exact shot scene UUID returned by its build or inspection")
        start = _integer(shot["start_frame"], "Source start frame", 1, 1048574)
        end = _integer(shot["end_frame"], "Source end frame", start, 1048574)
        total += end - start + 1
        name = shot["scene_name"]
        if (
            not isinstance(name, str)
            or not name
            or len(name) > 255
            or any(ord(c) < 32 for c in name)
        ):
            raise ValueError("Use the exact visible shot scene name")
        result["shots"].append(
            dict(
                scene_name=name,
                scene_id=identifier,
                start_frame=start,
                end_frame=end,
            )
        )
    if total > min(72000, result["fps"] * 600):
        raise ValueError("Keep a director timeline within ten minutes and 72000 frames")
    return result


def select_glb_clip(data, clip):
    """Keep exactly the requested embedded animation before Blender sees the file."""
    document = inspect_glb(data, static_only=False)
    animations = document.get("animations", [])
    if not animations and clip is None:
        return data
    if clip is None or clip["animation_index"] >= len(animations):
        raise ValueError("Animated GLB actors require an explicit valid animation_index")
    document["animations"] = [animations[clip["animation_index"]]]
    length = struct.unpack_from("<I", data, 12)[0]
    binary_chunk = data[20 + length :]
    body = json.dumps(document, separators=(",", ":"), allow_nan=False).encode()
    body += b" " * (-len(body) % 4)
    selected = struct.pack("<4sII", b"glTF", 2, 20 + len(body) + len(binary_chunk))
    selected += struct.pack("<I4s", len(body), b"JSON") + body + binary_chunk
    inspect_glb(selected, static_only=False)
    return selected


serialize = blockout.serialize
