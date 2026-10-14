# Open Studio local MCP reference

## Overview

The `open-studio` server gives a connected assistant access to the open Blender
scene and a local inbox for asset handoff. Generation takes place through the
assistant's own tools. The registered tools cover images, PBR maps, panoramic
Worlds, models, audio/video and local Blender inspection, selection, timeline,
camera and capture operations.

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

For other assets, use the same inbox with these explicit handoffs:

- `import_agent_material`: named UV mesh and a map of explicit roles to files.
  Base color is required; roughness, metallic, OpenGL normal, bump height and
  ambient occlusion are optional. PNG/JPEG/WebP maps need matching dimensions.
  Limits are 64 MiB each, 256 MiB combined and 32 megapixels combined.
- `import_agent_environment`: 2:1 equirectangular PNG/JPEG/EXR/Radiance HDR,
  up to 128 MiB and 32 megapixels. Packs the image into a new World. Projection,
  seamlessness and actual HDR content still require review.
- `import_agent_model`: self-contained GLB 2.0 up to 256 MiB, with embedded
  materials/textures and supported rig, morph and animation data. Creates a new
  group at the requested location or cursor. Each call imports another group;
  inspect the scene after an interrupted call before retrying.
- `import_agent_media`: WAV/MP3/OGG or MP4/WebM up to 512 MiB, with an explicit
  start frame and free channels. Video keeps its source duration at the scene
  FPS, rounded to a whole scene frame. Optional embedded audio requires a
  separate channel and decodable stream.
  The result includes a retained external media path that must travel with the
  project; these strips cannot pack their source into the `.blend` file.

`restore_agent_material` and `restore_agent_environment` restore previous
assignments only while the applied result remains unchanged in the current
session. File load, Undo/Redo and extension disable clear the restore handles.
The tools preserve unrelated objects, slots and timeline strips. All file
handoffs accept optional SHA-256 verification and reject paths outside the inbox.

The plugin cannot invoke the assistant's generation tools or access their
accounts. A generated image supplies pixels; model creation, accurate PBR maps,
rigging and other outputs require suitable assistant capabilities. Mesh editing,
animation retargeting, blockout and full film orchestration remain unfinished.

## Security model

- The server uses a local bearer token and binds to loopback. Keep the token,
  connection file and configurations containing it private.
- A connected client can request scene information and captures, and change the
  scene using the registered tools. Only connect assistants you authorize to
  work on the current project. Stop the server to end access.
- Blender's online-access permission controls starting the local listener.
  The local asset imports themselves make no external requests.
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
They are unavailable in a background process. Rigged GLB imports also require
a Blender window context. Other tools operate on the scene in the Blender
process receiving the request.

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
| `agent_workspace` | Get the local inbox for assets made with the assistant's own tools, such as Codex ImageGen. Copy files into this inbox, then use the image, material, environment, model or media import tool. Each import tool declares its accepted formats and limits. Blender cannot invoke the assistant's generation tools directly. | none | read-only annotation |
| `import_agent_image` | Import an assistant-generated image from the agent_workspace inbox. IMAGE loads packed pixels; REFERENCE creates a viewport reference at the 3D cursor; TEXTURE applies a new base-color material to the named mesh's active slot, requiring UVs. This does not generate a full PBR material. Existing other-object materials are preserved. Packed pixels survive inbox cleanup. Repeating the same image and destination reuses it. This local import works with arbitrary Python execution disabled. | `filename`*: string; Image basename inside the assistant inbox<br>`mode`: string (['IMAGE', 'REFERENCE', 'TEXTURE'])<br>`object_name`: string; Required for TEXTURE<br>`sha256`: string; Optional digest to verify the exact generated image<br>`display_size`: number | - |

### Assistant materials and environments

| Tool | Description | Arguments | Notes |
| --- | --- | --- | --- |
| `import_agent_material` | Apply an assistant-created PBR map set from the local inbox to one named editable mesh with UVs. Supply base_color and explicit optional roughness, metallic, normal, height and ambient_occlusion roles, each with filename and optional sha256. Maps must be PNG/JPEG/WebP with matching dimensions. Creates a new packed material in the active slot while preserving other objects and slots. Base color is sRGB; other maps are Non-Color. Normal uses OpenGL tangent space; height uses bump; AO multiplies base color. Repeating an unchanged set in this session reuses it. Does not create maps or establish their physical accuracy. | `object_name`*: string<br>`maps`*: object; Explicit role to {filename, sha256?}; base_color is required<br>`normal_convention`: string (['OPENGL'])<br>`height_distance`: number | - |
| `restore_agent_material` | Restore the named mesh's material slot from the most recent assistant PBR application in this live file session. Refuses changed graphs, maps, destinations or copied geometry. A mesh copy created to isolate a shared mesh stays isolated. Restores only while the original data still exists; unavailable after load, undo, redo or extension disable. | `object_name`*: string | - |
| `import_agent_environment` | Apply an assistant-supplied equirectangular PNG/JPEG/EXR/Radiance HDR from the local inbox to the current scene. Requires actual 2:1 dimensions, at least 4x2, at most 32 megapixels and 128 MiB. Packs the image and creates a separate Environment Texture World. The previous World stays untouched and can be restored in this live file session. Repeat unchanged input and strength reuses it. Dimensions do not establish correct panorama projection, seams or high dynamic range. | `filename`*: string<br>`sha256`: string<br>`strength`: number | - |
| `restore_agent_environment` | Restore the previous World after the most recent assistant environment application in this live file session. Refuses an edited or replaced World or image. Unavailable after load, undo, redo or extension disable. | none | - |

### Assistant audio and video

| Tool | Description | Arguments | Notes |
| --- | --- | --- | --- |
| `import_agent_media` | Import assistant-created WAV/MP3/OGG audio or MP4/WebM video from the agent_workspace inbox into the current scene's sequencer. Choose explicit free channels and start frame; occupied ranges are refused and existing strips, selection and scene FPS are preserved. Video playback is retimed to preserve its duration at the scene FPS. include_audio also requires a decodable audio stream and a different audio_channel. Identical unchanged requests reuse their strips after checking actual media and timing. The extension keeps a verified persistent media copy; strips cannot pack it, so moving the project requires copying that media and relinking its paths. This does not generate audio or video. | `filename`*: string; Media basename in the inbox<br>`kind`*: string (['AUDIO', 'VIDEO'])<br>`start_frame`: integer<br>`channel`: integer<br>`name`: string<br>`include_audio`: boolean<br>`audio_channel`: integer<br>`sha256`: string; Optional SHA-256 of exact media | - |

### Assistant models

| Tool | Description | Arguments | Notes |
| --- | --- | --- | --- |
| `import_agent_model` | Import a self-contained GLB 2.0 created by the connected assistant's modeling tools. Copy it to agent_workspace's inbox and pass its basename. Maximum 256 MiB; external file or URL references are refused. Meshes, hierarchy, materials, packed textures, rigs, skin weights, shape keys and animation clips are retained. A new group is placed with its frame-zero bottom center at the explicit location, or the current 3D cursor. Existing selection, current frame, timeline range and scene frame rate stay unchanged. Rigged imports require a Blender window context. This imports an existing generated model; it does not generate or rig one. Every call creates another group; inspect the scene after an interrupted call before retrying. Arbitrary Python execution may stay off. | `filename`*: string; GLB basename in the inbox<br>`sha256`: string; Optional SHA-256 of the GLB<br>`location`: array; World-space bottom-center location; defaults to the 3D cursor<br>`name`: string | - |

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
