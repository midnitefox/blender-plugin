# SPDX-FileCopyrightText: 2026 Open Studio contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Explicit local result retention, discovery, image previews and deliberate reuse."""

import base64
import json
import tempfile
from pathlib import Path

from ..blender import preview_decode, runtime
from ..core import assistant_results
from ..core.jobs.result_previews import bounded_local_image, local_image_layout
from .protocol import DeferredTool, ToolSpec
from .tools_agent import _inbox, _schema


def _library():
    return assistant_results.private_root(runtime.paths().state_dir / "assistant-results")


def _pass(value):
    return value


def save_agent_result(args):
    root, inbox = _library(), _inbox()
    # Freeze JSON input before handing bounded local file work to the request thread.
    request = json.loads(json.dumps(args))
    return DeferredTool(lambda: assistant_results.save(root, inbox, request), _pass)


def list_agent_results(args):
    root = _library()
    options = {key: args[key] for key in ("offset", "limit", "kind", "search") if key in args}
    return DeferredTool(lambda: assistant_results.listing(root, **options), _pass)


def _preview_finished(value):
    record, snapshot = value
    layout = local_image_layout(snapshot.data)
    if layout is None:
        raise ValueError("Saved image has no supported bounded preview layout")
    width, height, channels = layout
    if not bounded_local_image(width, height, channels):
        raise ValueError("Saved image exceeds the safe preview decode limit")
    with tempfile.TemporaryDirectory(prefix="result-preview-", dir=_library()) as temporary:
        directory = Path(temporary)
        source, output = directory / ("source" + snapshot.suffix), directory / "preview.png"
        source.write_bytes(snapshot.data)
        size, _ = preview_decode.decode(
            source, 768, lambda w, h: (w, h) == (width, height), output=output
        )
        pixels = output.read_bytes()
    return [
        {
            "type": "text",
            "text": json.dumps(
                {
                    **record,
                    "preview": {"filename": snapshot.name, "width": size[0], "height": size[1]},
                    "preview_file_verified": True,
                    "note": "Image preview only. Other bundle files have not been reverified by this preview.",
                }
            ),
        },
        {"type": "image", "mimeType": "image/png", "data": base64.b64encode(pixels).decode()},
    ]


def inspect_agent_result(args):
    root, result_id = _library(), args.get("result_id")
    filename = args.get("preview_file")
    if filename is not None:
        return DeferredTool(
            lambda: assistant_results.read_preview(root, result_id, filename), _preview_finished
        )
    return DeferredTool(lambda: assistant_results.inspect(root, result_id, verify=True), _pass)


def prepare_agent_result(args):
    root, inbox, result_id = _library(), _inbox(), args.get("result_id")
    return DeferredTool(lambda: assistant_results.prepare(root, inbox, result_id), _pass)


SPECS = [
    ToolSpec(
        "save_agent_result",
        "Save chosen inbox assets in a durable local library for discovery and reuse after Blender "
        "or the chat restarts. Supply a title, kind and files with required SHA-256 digests. "
        "Files are copied before publishing the record: at most 128 files and 512 MiB combined. "
        "For single files use role asset (default). MATERIAL requires explicit map roles with "
        "base_color and optional roughness OR smoothness, metallic, normal, height, ambient_occlusion "
        "and edge. MODEL_PACKAGE uses one asset plus resource files; OBJ textures use base_color, "
        "normal, roughness or metallic roles. Include every dependency. Saving verifies bytes, "
        "names and roles; format decoding and package completeness are checked by the import tool. "
        "Optional description is user-supplied provenance, not independently verified. No scene "
        "change, automatic scene application, upload or provider request. Identical requests reuse "
        "the same intact record. Local copies remain until explicitly removed outside this tool.",
        _schema(
            {
                "title": {"type": "string", "minLength": 1, "maxLength": 160},
                "description": {"type": "string", "maxLength": 4000},
                "kind": {"type": "string", "enum": list(assistant_results.KINDS)},
                "files": {
                    "type": "array",
                    "minItems": 1,
                    "maxItems": 128,
                    "items": {
                        "type": "object",
                        "properties": {
                            "filename": {"type": "string"},
                            "sha256": {"type": "string"},
                            "role": {"type": "string", "default": "asset"},
                        },
                        "required": ["filename", "sha256"],
                        "additionalProperties": False,
                    },
                },
            },
            ["title", "kind", "files"],
        ),
        save_agent_result,
        {"destructiveHint": False, "idempotentHint": True, "openWorldHint": False},
    ),
    ToolSpec(
        "list_agent_results",
        "Discover durable local saved results by title, description and kind. Returns paged "
        "metadata, stable result_ids and a damaged-record count. Listing validates metadata; "
        "inspect_agent_result or prepare_agent_result revalidates file bytes. No scene change.",
        _schema(
            {
                "offset": {"type": "integer", "minimum": 0, "maximum": 4096, "default": 0},
                "limit": {"type": "integer", "minimum": 1, "maximum": 100, "default": 25},
                "kind": {"type": "string", "enum": list(assistant_results.KINDS)},
                "search": {"type": "string", "maxLength": 160, "default": ""},
            }
        ),
        list_agent_results,
        {"readOnlyHint": True, "openWorldHint": False},
    ),
    ToolSpec(
        "inspect_agent_result",
        "Inspect a saved local result's title, description, original names, roles and digests. "
        "With no preview_file, verify every saved file. With preview_file, verify that named "
        "PNG/JPEG/WebP/EXR and return a bounded inline image preview without changing Blender data. "
        "Other formats have file details only; this does not claim a rendered model or media preview.",
        _schema(
            {
                "result_id": {"type": "string"},
                "preview_file": {
                    "type": "string",
                    "description": "Optional image basename in this result",
                },
            },
            ["result_id"],
        ),
        inspect_agent_result,
        {"readOnlyHint": True, "openWorldHint": False},
    ),
    ToolSpec(
        "prepare_agent_result",
        "Verify a saved bundle and copy its original names into the assistant inbox for deliberate "
        "reuse. Returns an import recipe with required destination choices. Preserves any identical "
        "inbox files and refuses collisions with different content; never overwrites a file. "
        "All saved files are verified before publication. Does not apply assets, reuse old target "
        "identities or authorize a mesh edit. Inspect the current scene and call the import tool "
        "explicitly after choosing its destination, placement, axes or media channels.",
        _schema({"result_id": {"type": "string"}}, ["result_id"]),
        prepare_agent_result,
        {"destructiveHint": False, "idempotentHint": True, "openWorldHint": False},
    ),
]
