# SPDX-FileCopyrightText: 2026 Scenario Inc.
# SPDX-License-Identifier: GPL-3.0-or-later
"""JSON-RPC 2.0 + MCP method handling. No bpy, no sockets: pure functions over dicts."""

import dis
import json
import logging
from dataclasses import dataclass, field

log = logging.getLogger("scenario.mcp")
# "scenario" in tests, "bl_ext.<repository>.scenario" when installed.
_EXTENSION_PACKAGE = __name__.rsplit(".", 2)[0]
_RAISE_VARARGS = dis.opmap["RAISE_VARARGS"]

PROTOCOL_VERSIONS = ("2025-06-18", "2025-03-26", "2024-11-05")
PARSE_ERROR, INVALID_REQUEST, METHOD_NOT_FOUND, INVALID_PARAMS, INTERNAL_ERROR, TOOL_TIMEOUT = (
    -32700,
    -32600,
    -32601,
    -32602,
    -32603,
    -32000,
)
INSTRUCTIONS = (
    "Blender is open on the user's machine. Start with scene_summary and inspect the exact target "
    "with object_detail before changing it. Prefer the specific local tools over execute_python. "
    "For image creation, use your assistant's own image-generation tool when available. "
    "Call agent_workspace, save or copy the generated image into its inbox, then import_agent_image "
    "with its basename as a REFERENCE, TEXTURE or IMAGE. TEXTURE requires a named editable mesh "
    "with UV coordinates and applies a new base-color material to its active slot. Save the blend "
    "file to retain packed assets. Use import_agent_material for an explicit PBR map set, "
    "import_agent_environment for an equirectangular World, import_agent_model for a model, "
    "and import_agent_media for audio/video strips. Read each schema before preparing files. "
    "Audio/video retains external media files that must travel with the project. "
    "Generate assets with the connected assistant's available tools, then import the results. "
    "Image pixels alone do not establish mesh geometry, correct PBR maps or a genuine HDR panorama. "
    "For multi-file glTF/OBJ, embedded FBX or mesh PLY use import_agent_model_package; "
    "Gaussian files use import_agent_splat, which produces an approximate colored point cloud. "
    "For mesh editing, export_agent_mesh captures the exact source and session handle. "
    "Return an aligned GLB to apply_agent_mesh with an explicit policy and mapping; never guess alignment. "
    "For scene layout, inspect blockout_plan, design a complete primitive recipe and build_agent_blockout. "
    "Use its current revision for rebuild or clear; preserve user edits when a guard refuses replacement. "
    "capture_agent_animation creates a silent local MP4 reference for your own video tools. "
    "For films, build_agent_shot creates independent editable shot scenes from explicit recipes "
    "and inbox assets; build_agent_timeline assembles named shots into an editable director timeline. "
    "inspect_agent_film reports current contents and persistent scene identities. "
    "activate_agent_scene selects a shot before capture or local scene tools. "
    "build_agent_review assembles finished inbox pictures, video and audio with explicit timing. "
    "Stored film recipes describe original build inputs; manual Blender edits remain authoritative. "
    "export_agent_film renders a local MP4; package_agent_project copies an editable portable project. "
    "Both return an operation_id: poll get_agent_operation to DONE before using the result, "
    "or request cancel_agent_operation. File load, Undo/Redo and disabling the extension cancel "
    "pending work. Published outputs are retained locally and are never overwritten. "
    "Viewport and OpenGL captures require Blender's GUI. Use blender_api_help before scripting. "
    "execute_python is disabled unless the user has explicitly enabled the Python preference; "
    "it runs with Blender's user permissions and is not a security sandbox. "
    "This extension cannot invoke the assistant's own generation tools itself."
)


class ToolTimeout(Exception):
    pass


@dataclass
class DeferredTool:
    """Main-thread preparation, HTTP-thread work, then main-thread delivery.

    `finish` may return another DeferredTool to continue on the HTTP thread.
    """

    run: object
    finish: object


@dataclass
class ToolSpec:
    name: str
    description: str
    input_schema: dict
    handler: object
    annotations: dict = field(default_factory=dict)
    offthread: bool = False  # True: safe to run on the HTTP thread (network only, no bpy)


class Registry:
    def __init__(self):
        self._tools = {}

    def add(self, spec):
        self._tools[spec.name] = spec
        return spec

    def get(self, name):
        return self._tools.get(name)

    def names(self):
        return list(self._tools)

    def list_payload(self):
        out = []
        for spec in self._tools.values():
            item = {
                "name": spec.name,
                "description": spec.description,
                "inputSchema": spec.input_schema,
            }
            if spec.annotations:
                item["annotations"] = spec.annotations
            out.append(item)
        return out


def _error(msg_id, code, message, data=None):
    err = {"code": code, "message": message}
    if data is not None:
        err["data"] = data
    return {"jsonrpc": "2.0", "id": msg_id, "error": err}


def _result(msg_id, result):
    return {"jsonrpc": "2.0", "id": msg_id, "result": result}


def _in_extension(module):
    return module == _EXTENSION_PACKAGE or module.startswith(_EXTENSION_PACKAGE + ".")


def _written_by_extension(error):
    """True when this extension wrote the failure's message, so the client may read it.

    Tool handlers and the code they call report user-facing failures with their own text: an
    error class this extension defines (ScenarioError, StoreError and the other sanitized UI and
    worker errors), or a raise statement in its own code (a validation ValueError, a refusing
    PermissionError, a "still loading" RuntimeError). A failure raised by the OS, bpy or a
    library can carry local paths, profile layout or service details. That includes one raised
    by a C function called directly from extension code: its innermost instruction is the call,
    not a raise. An OSError naming a file is never returned.
    """
    if isinstance(error, OSError) and (error.filename is not None or error.filename2 is not None):
        return False
    if _in_extension(getattr(type(error), "__module__", None) or ""):
        return True
    tb = error.__traceback__
    if tb is None:
        return False
    while tb.tb_next is not None:
        tb = tb.tb_next
    if not _in_extension(tb.tb_frame.f_globals.get("__name__") or ""):
        return False
    code = tb.tb_frame.f_code.co_code
    return 0 <= tb.tb_lasti < len(code) and code[tb.tb_lasti] == _RAISE_VARARGS


def tool_failure_text(name, error):
    """Client text for a failed tool call; the traceback stays in the local log."""
    kind = type(error).__name__
    if _written_by_extension(error):
        log.debug("MCP tool %s failed", name, exc_info=error)
        return f"{kind}: {error}"
    log.error("MCP tool %s failed unexpectedly", name, exc_info=error)
    return f"{kind}: {name} failed unexpectedly; see the Blender console"


def to_content(value):
    if isinstance(value, dict) and "_image" in value:
        return [
            {
                "type": "image",
                "data": value["_image"],
                "mimeType": value.get("mimeType", "image/png"),
            }
        ]
    if (
        isinstance(value, list)
        and value
        and all(isinstance(v, dict) and "type" in v for v in value)
    ):
        return value
    if isinstance(value, str):
        return [{"type": "text", "text": value}]
    return [{"type": "text", "text": json.dumps(value, indent=1, default=str)}]


def _check_arguments(spec, arguments):
    """Refuse a call that omits an argument its input schema requires, before any work.

    Handlers would otherwise read a missing context as a changed one, or fail
    on a missing key. Only presence is checked; handlers still validate values.
    """
    if not isinstance(arguments, dict):
        raise ValueError("Tool arguments must be a JSON object")
    missing = [name for name in spec.input_schema.get("required", ()) if name not in arguments]
    if missing:
        plural = "s" if len(missing) > 1 else ""
        raise ValueError(f"Missing required argument{plural}: {', '.join(missing)}")


def handle_message(message, registry, server_info, executor=None):
    if not isinstance(message, dict) or "method" not in message:
        return _error(
            message.get("id") if isinstance(message, dict) else None,
            INVALID_REQUEST,
            "Invalid JSON-RPC request",
        )
    method, msg_id, params = message["method"], message.get("id"), message.get("params") or {}
    if msg_id is None:  # notification
        return None
    if method == "initialize":
        wanted = params.get("protocolVersion")
        version = wanted if wanted in PROTOCOL_VERSIONS else PROTOCOL_VERSIONS[0]
        return _result(
            msg_id,
            {
                "protocolVersion": version,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": dict(server_info),
                "instructions": INSTRUCTIONS,
            },
        )
    if method == "ping":
        return _result(msg_id, {})
    if method == "tools/list":
        return _result(msg_id, {"tools": registry.list_payload()})
    if method == "tools/call":
        name = params.get("name")
        spec = registry.get(name) if name else None
        if spec is None:
            return _error(msg_id, INVALID_PARAMS, f"Unknown tool: {name}")
        arguments = params.get("arguments") or {}
        try:
            _check_arguments(spec, arguments)
            if executor is not None and not spec.offthread:
                value = executor(spec.handler, arguments)
            else:
                value = spec.handler(arguments)
            # A finish may hand more work back, such as a quote after the model
            # description it waited for; each step returns to the main thread.
            while isinstance(value, DeferredTool):
                deferred = value
                completed = deferred.run()
                value = (
                    executor(deferred.finish, completed)
                    if executor is not None
                    else deferred.finish(completed)
                )
        except ToolTimeout as err:
            return _error(msg_id, TOOL_TIMEOUT, f"Tool timeout: {err}")
        except Exception as err:  # tool failures are results, not protocol errors
            text = tool_failure_text(spec.name, err)
            return _result(msg_id, {"content": [{"type": "text", "text": text}], "isError": True})
        return _result(msg_id, {"content": to_content(value)})
    return _error(msg_id, METHOD_NOT_FOUND, f"Method not found: {method}")


def parse_body(raw):
    try:
        return json.loads(raw.decode("utf-8") if isinstance(raw, (bytes, bytearray)) else raw), None
    except (ValueError, UnicodeDecodeError) as err:
        return None, _error(None, PARSE_ERROR, f"Parse error: {err}")
