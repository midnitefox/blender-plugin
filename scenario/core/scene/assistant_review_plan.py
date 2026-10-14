# SPDX-FileCopyrightText: 2026 Open Studio contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Bounded editorial recipes, without Blender or generation-service state."""

import json
import math
import re

from ..jobs.transfers import TransferError, validate_result_name

MAX_SECONDS = 600
MAX_ITEMS = 128
MAX_STRIPS = 2048
MAX_TOTAL_BYTES = 512 * 1024 * 1024


def integer(value, label, low, high):
    if type(value) is not int or not low <= value <= high:
        raise ValueError(f"{label} must be an integer from {low} to {high}")
    return value


def number(value, label, low, high):
    if type(value) not in (int, float) or not math.isfinite(value) or not low <= value <= high:
        raise ValueError(f"{label} must be a finite number from {low} to {high}")
    return float(value)


def _boolean(value, label):
    if type(value) is not bool:
        raise ValueError(f"{label} must be a boolean")
    return value


def _item(raw, picture, fps):
    allowed = {
        "filename",
        "sha256",
        "start_frame",
        "frame_count",
        "channel",
        "source_trim_seconds",
        "loop",
        "volume",
    }
    if picture:
        allowed |= {"kind", "include_audio", "audio_channel"}
    if not isinstance(raw, dict) or set(raw) - allowed:
        raise ValueError("Use only the documented review item fields")
    name = raw.get("filename")
    try:
        validate_result_name(name)
    except (TransferError, TypeError):
        raise ValueError("Use an inbox file basename") from None
    digest = raw.get("sha256")
    if not isinstance(digest, str) or re.fullmatch("[a-f0-9]{64}", digest) is None:
        raise ValueError("Every source needs its exact lowercase SHA-256")
    item = dict(filename=name, sha256=digest)
    item["start_frame"] = integer(raw.get("start_frame"), "Start frame", 1, MAX_SECONDS * fps)
    item["frame_count"] = integer(raw.get("frame_count"), "Frame count", 1, MAX_SECONDS * fps)
    if item["start_frame"] + item["frame_count"] - 1 > MAX_SECONDS * fps:
        raise ValueError("Keep the review timeline within 600 seconds")
    item["channel"] = integer(raw.get("channel"), "Channel", 1, 128)
    trim = number(raw.get("source_trim_seconds", 0), "Source trim seconds", 0, 86400)
    if abs(trim * fps - round(trim * fps)) > 0.000001:
        raise ValueError("Source trim must align to a destination frame boundary")
    item.update(
        source_trim_seconds=trim,
        loop=_boolean(raw.get("loop", False), "loop"),
        volume=number(raw.get("volume", 1), "Volume", 0, 4),
    )
    if picture:
        kind = raw.get("kind")
        if kind not in {"IMAGE", "VIDEO"}:
            raise ValueError("Picture kind must be IMAGE or VIDEO")
        item.update(
            kind=kind, include_audio=_boolean(raw.get("include_audio", False), "include_audio")
        )
        if kind == "IMAGE" and (trim or item["loop"] or item["include_audio"]):
            raise ValueError("An IMAGE holds its duration and cannot trim, loop or include audio")
        if item["include_audio"]:
            item["audio_channel"] = integer(raw.get("audio_channel"), "Audio channel", 1, 128)
            if item["audio_channel"] == item["channel"]:
                raise ValueError("Embedded audio requires a different channel")
        elif "audio_channel" in raw:
            raise ValueError("audio_channel requires include_audio")
    return item


def validate_plan(raw):
    if not isinstance(raw, dict) or set(raw) - {
        "name",
        "fps",
        "width",
        "height",
        "pictures",
        "audio",
    }:
        raise ValueError("Use only the documented review fields")
    name = raw.get("name")
    if not isinstance(name, str) or not name.strip() or len(name) > 60 or "\0" in name:
        raise ValueError("Use a nonempty review name of at most 60 characters")
    fps = integer(raw.get("fps", 24), "FPS", 1, 120)
    width = integer(raw.get("width", 1920), "Width", 64, 3840)
    height = integer(raw.get("height", 1080), "Height", 64, 2160)
    if width % 2 or height % 2:
        raise ValueError("Use even review dimensions")
    pictures, audio = raw.get("pictures", []), raw.get("audio", [])
    if (
        not isinstance(pictures, list)
        or not isinstance(audio, list)
        or not 1 <= len(pictures) + len(audio) <= MAX_ITEMS
    ):
        raise ValueError("Use from 1 to 128 picture and audio items")
    result = dict(
        name=name.strip(),
        fps=fps,
        width=width,
        height=height,
        pictures=[_item(item, True, fps) for item in pictures],
        audio=[_item(item, False, fps) for item in audio],
    )
    occupied = []
    for item in result["pictures"] + result["audio"]:
        channels = [item["channel"]]
        if item.get("include_audio"):
            channels.append(item["audio_channel"])
        for channel in channels:
            start, end = item["start_frame"], item["start_frame"] + item["frame_count"]
            if any(c == channel and a < end and start < b for c, a, b in occupied):
                raise ValueError("Review items overlap on the same channel; choose a free range")
            occupied.append((channel, start, end))
    return result


def segments(item, source_frames, fps):
    """Repeat the trimmed source, with a final short segment, or refuse overrun."""
    trim = round(item["source_trim_seconds"] * fps)
    available = source_frames - trim
    if available <= 0 or (not item["loop"] and item["frame_count"] > available):
        raise ValueError("Source does not cover its trim and requested frame count")
    count = math.ceil(item["frame_count"] / available)
    if count > MAX_STRIPS:
        raise ValueError("Keep the review within 2048 strips")
    start, remaining, result = item["start_frame"], item["frame_count"], []
    while remaining:
        frames = min(available, remaining)
        result.append(dict(start_frame=start, frame_count=frames, trim_frames=trim))
        start, remaining = start + frames, remaining - frames
    return result


def canonical(plan):
    return json.dumps(
        plan, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False
    )
