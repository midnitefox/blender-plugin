# SPDX-FileCopyrightText: 2026 Open Studio contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Build local editorial reviews from explicit assistant media handoffs."""

import threading

from ..blender import assistant_film_review
from .protocol import ToolSpec
from .tools_agent import _inbox, _schema


def build_agent_review(args):
    if threading.current_thread() is not threading.main_thread():
        raise RuntimeError("Build the review on Blender's main thread")
    return assistant_film_review.build(args, _inbox())


SPECS = [
    ToolSpec(
        "build_agent_review",
        "Create a NEW editable sequencer scene from verified assistant inbox media, preserving the "
        "active scene, selection and unrelated strips. Each call creates a distinct review scene. "
        "Pictures accept IMAGE (PNG/JPEG/WebP) or VIDEO (MP4/WebM); audio accepts WAV/MP3/OGG. "
        "Every item requires an inbox basename, exact sha256, start_frame, frame_count and channel. "
        "Source trim is seconds aligned to a destination frame boundary. Video preserves source "
        "seconds across FPS changes. loop repeats the source remaining after trim; false refuses "
        "insufficient source duration. IMAGE holds its frame_count and cannot trim or loop. "
        "include_audio requires a decodable video audio stream and explicit separate audio_channel. "
        "volume controls audio gain. Overlapping ranges on the same channel are refused. "
        "Limits: 600-second timeline, 128 items, 2048 resulting strips, 512 MiB total media, "
        "64 MiB per image. Retained verified copies survive inbox cleanup; copy and relink those "
        "files when moving the blend project. Returns scene_name, scene_id, kind, timing, strips "
        "and retained_media. This tool does not generate media or export a finished film.",
        _schema(
            {
                "name": {"type": "string", "minLength": 1, "maxLength": 60},
                "fps": {"type": "integer", "minimum": 1, "maximum": 120, "default": 24},
                "width": {"type": "integer", "minimum": 64, "maximum": 3840, "default": 1920},
                "height": {"type": "integer", "minimum": 64, "maximum": 2160, "default": 1080},
                "pictures": {
                    "type": "array",
                    "maxItems": 128,
                    "items": {
                        "type": "object",
                        "properties": {
                            "kind": {"type": "string", "enum": ["IMAGE", "VIDEO"]},
                            "filename": {"type": "string"},
                            "sha256": {"type": "string", "pattern": "^[a-f0-9]{64}$"},
                            "start_frame": {"type": "integer", "minimum": 1, "maximum": 72000},
                            "frame_count": {"type": "integer", "minimum": 1, "maximum": 72000},
                            "channel": {"type": "integer", "minimum": 1, "maximum": 128},
                            "source_trim_seconds": {
                                "type": "number",
                                "minimum": 0,
                                "maximum": 86400,
                                "default": 0,
                            },
                            "loop": {"type": "boolean", "default": False},
                            "include_audio": {"type": "boolean", "default": False},
                            "audio_channel": {"type": "integer", "minimum": 1, "maximum": 128},
                            "volume": {"type": "number", "minimum": 0, "maximum": 4, "default": 1},
                        },
                        "required": [
                            "kind",
                            "filename",
                            "sha256",
                            "start_frame",
                            "frame_count",
                            "channel",
                        ],
                        "additionalProperties": False,
                    },
                },
                "audio": {
                    "type": "array",
                    "maxItems": 128,
                    "items": {
                        "type": "object",
                        "properties": {
                            "filename": {"type": "string"},
                            "sha256": {"type": "string", "pattern": "^[a-f0-9]{64}$"},
                            "start_frame": {"type": "integer", "minimum": 1, "maximum": 72000},
                            "frame_count": {"type": "integer", "minimum": 1, "maximum": 72000},
                            "channel": {"type": "integer", "minimum": 1, "maximum": 128},
                            "source_trim_seconds": {
                                "type": "number",
                                "minimum": 0,
                                "maximum": 86400,
                                "default": 0,
                            },
                            "loop": {"type": "boolean", "default": False},
                            "volume": {"type": "number", "minimum": 0, "maximum": 4, "default": 1},
                        },
                        "required": ["filename", "sha256", "start_frame", "frame_count", "channel"],
                        "additionalProperties": False,
                    },
                },
            },
            ["name"],
        ),
        build_agent_review,
        {"destructiveHint": False, "idempotentHint": False, "openWorldHint": False},
    ),
]
