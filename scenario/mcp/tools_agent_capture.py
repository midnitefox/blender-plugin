# SPDX-FileCopyrightText: 2026 Open Studio contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Local animated camera or viewport references for the connected assistant."""

from ..blender import assistant_capture
from .protocol import ToolSpec
from .tools_agent import _schema


def capture_agent_animation(args):
    return assistant_capture.capture_animation(args)


SPECS = [
    ToolSpec(
        "capture_agent_animation",
        "Capture a silent local MP4 reference from the scene camera or 3D viewport for your "
        "assistant's own video tool. Requires Blender's GUI and an open 3D viewport. "
        "Choose a camera_name for CAMERA, or use the active/first scene camera. Supply both "
        "start_frame and end_frame, or use the current preview/scene range. Captures are limited "
        "to 20 seconds, 600 frames and 1920x1080; width and height must be even. grey_clay requests "
        "solid single-color shading. The original camera, frame, range, rendering and viewport "
        "settings are restored. Returns a unique retained outbox path, SHA-256 and clip timing. "
        "Copy the MP4 to your project for long-term storage. This does not generate video.",
        _schema(
            {
                "source": {"type": "string", "enum": ["CAMERA", "VIEWPORT"], "default": "CAMERA"},
                "camera_name": {"type": "string", "description": "Camera in the current scene"},
                "start_frame": {"type": "integer", "minimum": -1048574, "maximum": 1048574},
                "end_frame": {"type": "integer", "minimum": -1048574, "maximum": 1048574},
                "width": {"type": "integer", "minimum": 64, "maximum": 1920, "default": 1280},
                "height": {"type": "integer", "minimum": 64, "maximum": 1080, "default": 720},
                "grey_clay": {"type": "boolean", "default": False},
            }
        ),
        capture_agent_animation,
        {"readOnlyHint": True, "openWorldHint": False},
    ),
]
