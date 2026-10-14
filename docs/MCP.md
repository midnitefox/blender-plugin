# Open Studio local MCP reference

## Overview

The `open-studio` server gives a connected assistant access to the open Blender
scene and a local inbox for image handoff. Generation takes place through the
assistant's own tools. The current registered tools cover that image handoff and
local Blender inspection, selection, timeline, camera and capture operations.

Start with `scene_summary`. Before applying a texture, inspect the exact target
with `object_detail` and verify its UV coordinates. Use dedicated scene tools
for supported actions; arbitrary Python has a separate permission gate.

## Connect an assistant

In Blender, open **Open Studio** in the viewport sidebar, allow online access
and start the server. Copy the setup for the client you use. The default port is 9876; the
server may use a nearby free port. The copied setup follows the actual address.

The copied setup for Codex, Claude Code, Cursor and Claude Desktop uses the
included stdio bridge. It launches with Blender's Python interpreter, the installed `stdio_shim.py` path and `--connection-file`
pointing to the private `agent-connection.json`. The bridge reads the current
address and token for each request. Keep Blender open, and reconnect the MCP
client after initial setup. Recopy the setup when installation paths change.

The Codex copy action produces `codex mcp add open-studio --` followed by the
actual local paths and `--connection-file`. Use that generated command rather
than substituting a guessed interpreter, extension path or token.

Use **Other assistants** in the panel for Claude Code, Cursor or Claude Desktop.
The copied configurations point to the private connection file and contain no
bearer token. All bridge requests reach the authenticated local endpoint at
`127.0.0.1`, with JSON-RPC at `/mcp`. No additional hosted MCP service is needed
for these tools.

## Assistant image handoff

1. Use the assistant's image-generation or editing tool when available.
2. Call `agent_workspace` and save or copy the image into its returned `inbox`.
3. Call `import_agent_image` using the file's basename, for example:

```json
{"filename": "wood.png", "mode": "TEXTURE", "object_name": "Crate"}
```

Use `REFERENCE` for an image reference at the 3D cursor or `IMAGE` for a packed
image datablock. `TEXTURE` requires an editable mesh with UV coordinates and
creates a new base-color material in its active slot. The import preserves
other objects' materials. Save the `.blend` file to retain the packed image.

The handoff accepts PNG, JPEG and WebP up to 64 MiB. It reads only the bounded
file in the local inbox, validates it and decodes it in Blender. Paths outside
the inbox are refused. An optional `sha256` binds the import to exact content.
Identical imported content can be reused without creating duplicate assets.

A generated image supplies pixels. Automated mesh generation, complete PBR map
sets, rigging, motion, audio, video and panoramic World application are not
exposed by this build. The plugin cannot invoke the assistant's generation tool
or access its account on the assistant's behalf.

## Security model

- The server uses a local bearer token and binds to loopback. Keep the token,
  connection file and configurations containing it private.
- A connected client can request scene information and captures, and change the
  scene using the registered tools. Only connect assistants you authorize to
  work on the current project. Stop the server to end access.
- Blender's online-access permission controls starting the local listener.
  The image import itself makes no external request.
- `execute_python` is disabled by default and requires **Allow connected assistants
  to run Python** to be explicitly enabled. Its code runs on Blender's main
  thread with the user's permissions. Blocking some dangerous call spellings
  is not a security sandbox.
- `blender_api_help` inspects the actual running Blender Python API without
  executing the named operation. Use it before scripting.
- Captures use private temporary files and return image content to the client.
  The assistant may process received content using its own service.

See [Privacy](PRIVACY.md) for local persistence and assistant data handling.

## GUI and background operation

`screenshot_viewport` and `render_still` need a visible Blender 3D viewport.
They are unavailable in a background process. Other tools operate on the scene
in the Blender process receiving the request.

An isolated profile with the extension enabled can run:

```sh
blender --background --online-mode --command open_studio --port 9876
```

The server writes a private connection file and reports that authentication is
enabled. Use its stdio bridge to read those details. Advanced callers may supply
an explicit local token through `OPEN_STUDIO_TOKEN` or `--token`; avoid placing
a token in shell history. This token authenticates local MCP access.

## Tool reference

The tables below are generated from the source used by the current local server.
Read `tools/list` for the full descriptions, examples and argument schemas.

<!-- tools:start -->
Generated by `tools/gen_mcp_docs.py` from `scenario/mcp/tools_*.py`.
Do not edit this block by hand; run `make mcp-docs`. An asterisk marks a required argument.

### Assistant image tools

| Tool | Description | Arguments | Notes |
| --- | --- | --- | --- |
| `agent_workspace` | Get the local image inbox for Codex ImageGen or another assistant's generated assets. Copy a generated PNG/JPEG/WebP into the returned inbox, then use import_agent_image. Use the assistant's own image-generation tool; Blender cannot invoke it directly. | none | read-only annotation |
| `import_agent_image` | Import an assistant-generated image from the agent_workspace inbox. IMAGE loads packed pixels; REFERENCE creates a viewport reference at the 3D cursor; TEXTURE applies a new base-color material to the named mesh's active slot, requiring UVs. This does not generate a full PBR material. Existing other-object materials are preserved. Packed pixels survive inbox cleanup. Repeating the same image and destination reuses it. This local import works with arbitrary Python execution disabled. | `filename`*: string; Image basename inside the assistant inbox<br>`mode`: string (['IMAGE', 'REFERENCE', 'TEXTURE'])<br>`object_name`: string; Required for TEXTURE<br>`sha256`: string; Optional digest to verify the exact generated image<br>`display_size`: number | - |

### Scene tools (run on Blender's main thread)

| Tool | Description | Arguments | Notes |
| --- | --- | --- | --- |
| `scene_summary` | Inspect the open Blender scene before choosing a scene operation. | none | read-only annotation |
| `object_detail` | Inspect one named Blender object's transform, geometry summary and custom properties. | `name`*: string | read-only annotation |
| `execute_python` | Run arbitrary Python with bpy on Blender's main thread, only when explicitly enabled in preferences. | `code`*: string; Python source. bpy and result = {} are preloaded. | destructive annotation; Python preference gate; off by default |
| `select_objects` | Replace the selection in the current view layer with the named Blender objects. | `names`*: array | - |
| `set_frame` | Move Blender's timeline to a frame and evaluate the scene there. | `frame`*: integer | - |
| `screenshot_viewport` | Capture the visible 3D viewport area as a PNG image, including its UI overlays. | none | read-only annotation; GUI required |
| `camera_path` | Build an animated scene camera from a preset, description or explicit waypoints. | `preset`: string<br>`duration`: number; seconds<br>`focal`: number; mm<br>`aim_at_subject`: boolean<br>`description`: string; e.g. 'slow orbit, 8 s, 35mm'<br>`waypoints`: array | - |
| `render_still` | Capture a quick OpenGL camera or viewport still as a PNG at the requested size. | `source`: string (['CAMERA', 'VIEWPORT'])<br>`width`: integer<br>`height`: integer | read-only annotation; GUI required |
| `blender_api_help` | Inspect Blender's running Python API before writing a script. | `path`*: string; e.g. bpy.ops.mesh.primitive_cube_add, bpy.types.Object, bpy.data.objects | read-only annotation |
| `datablocks_summary` | Summarize the datablocks in the open Blender file. | none | read-only annotation |
<!-- tools:end -->

## Historical upstream reference

Architecture documents retained from the upstream extension may link here for
cloud model quotes, asset libraries, previews, uploads, film planning or workflow
approval. Those tools are not registered in this assistant-only build. The
current supported tools are the generated tables above. Old source modules and
screenshots are historical implementation context, not instructions to configure
or connect a generation service.
