# SPDX-FileCopyrightText: 2026 Open Studio contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Assistant-created audio and video handoff through the bounded local inbox."""

from ..blender import assistant_media
from ..core.agent_artifacts import read_inbox_file
from .protocol import ToolSpec
from .tools_agent import _inbox, _schema


def import_agent_media(args):
    kind = args.get("kind")
    if kind not in {"AUDIO", "VIDEO"}:
        raise ValueError("Choose AUDIO or VIDEO")
    suffixes = assistant_media.AUDIO_SUFFIXES if kind == "AUDIO" else assistant_media.VIDEO_SUFFIXES
    snapshot = read_inbox_file(
        _inbox(),
        args.get("filename", ""),
        args.get("sha256"),
        suffixes=suffixes,
        max_bytes=assistant_media.MAX_MEDIA_BYTES,
    )
    return assistant_media.apply_media(snapshot, args)


SPECS = [
    ToolSpec(
        "import_agent_media",
        "Import assistant-created WAV/MP3/OGG audio or MP4/WebM video from the agent_workspace "
        "inbox into the current scene's sequencer. Choose explicit free channels and start frame; "
        "occupied ranges are refused and existing strips, selection and scene FPS are preserved. "
        "Video playback is retimed to preserve its duration at the scene FPS. include_audio also "
        "requires a decodable audio stream and a different audio_channel. Identical unchanged "
        "requests reuse their strips after checking actual media and timing. The extension keeps "
        "a verified persistent media copy; strips cannot pack it, so moving the project requires "
        "copying that media and relinking its paths. This does not generate audio or video.",
        _schema(
            {
                "filename": {"type": "string", "description": "Media basename in the inbox"},
                "kind": {"type": "string", "enum": ["AUDIO", "VIDEO"]},
                "start_frame": {
                    "type": "integer",
                    "minimum": -1048574,
                    "maximum": 1048574,
                    "default": 1,
                },
                "channel": {"type": "integer", "minimum": 1, "maximum": 128, "default": 1},
                "name": {"type": "string", "minLength": 1, "maxLength": 128},
                "include_audio": {"type": "boolean", "default": False},
                "audio_channel": {"type": "integer", "minimum": 1, "maximum": 128, "default": 2},
                "sha256": {"type": "string", "description": "Optional SHA-256 of exact media"},
            },
            ["filename", "kind"],
        ),
        import_agent_media,
        {"destructiveHint": False, "idempotentHint": True, "openWorldHint": False},
    ),
]
