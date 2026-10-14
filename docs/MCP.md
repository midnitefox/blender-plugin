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
  Base color is required; roughness or smoothness, metallic, OpenGL normal,
  bump height, ambient occlusion and retained edge maps are optional.
  PNG/JPEG/WebP and single-part non-deep scanline EXR need matching dimensions.
  Limits are 64 MiB per PNG/JPEG/WebP, 128 MiB per EXR, 8192 pixels per edge,
  256 MiB combined, 32 megapixels combined and 128 million decoded channel samples.
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
The tools preserve unrelated objects, slots and timeline strips. Single-asset
imports accept optional SHA-256 verification; film asset bindings require it.
File handoffs reject paths outside the inbox.

The plugin cannot invoke the assistant's generation tools or access their
accounts. A generated image supplies pixels; model creation, accurate PBR maps,
rigging and other outputs require suitable assistant capabilities. Existing-rig
animation retargeting is unsupported. The local
mesh-edit round trip, structured blockout and animated-reference capture are
available through dedicated tools below.

## Saved local assets

`save_agent_result` retains explicitly chosen inbox files, their roles, title and
description in a local library. Required SHA-256 digests bind the saved bytes.
`list_agent_results` discovers entries across sessions; `inspect_agent_result`
verifies all files or previews one supported image. Saving is separate from
format decoding and scene import. Descriptions are declared provenance.

`prepare_agent_result` verifies a whole saved bundle, refuses filename collisions
with different inbox content, and returns files plus an import recipe. It does
not change the scene. Choose fresh targets, placement and timing before calling
the matching import tool. A saved bundle cannot preserve a live mesh-edit handle.

## Modeling, scene design and video references

`import_agent_model_package` accepts glTF/OBJ with explicit resources,
embedded-texture FBX and mesh PLY. Resources are copied from the same bounded
inbox and rewritten into a private package snapshot. Inspect returned dropped
references and FBX timing warnings. `import_agent_splat` decodes SPZ v2/v3,
Gaussian PLY or `.splat` on a worker, then publishes an approximate colored point
cloud only if the original scene is still current. These imports create new
groups each time; inspect after an interrupted call before retrying.

`export_agent_mesh` returns a named static mesh's GLB, digest, mapping and live
target handle. `apply_agent_mesh` requires that handle, a returned inbox GLB,
an explicit REMESH/UV/RETEXTURE/PARTS/RIG policy, a verified affine mapping and
the `keep_original` choice. Source identity and content must remain unchanged.
Successful or uncertain application consumes the handle; file load, Undo/Redo
and disable clear pending exports. See each tool schema for source restrictions.
Use object-local coordinates for mirrored edit sources. `mode: "REFERENCE"`
instead snapshots evaluated geometry at the current frame, with no edit handle.
World-space references can include up to 64 explicitly named meshes; local-space
references use one. Reference-only snapshots cannot authorize `apply_agent_mesh`.

`blockout_plan` returns the saved recipe, revision, planning guidance and limits.
`build_agent_blockout` accepts a complete recipe and current revision for a
replacement. `clear_agent_blockout` requires the current revision. Both guard
owned content against edits, sharing and outside references before removal.

`capture_agent_animation` produces a silent MP4 in a unique retained outbox
directory. It requires the GUI, validates actual decoded timing and dimensions,
and restores capture settings on success or failure. The assistant must copy
the clip for long-term storage and use its own video tools for generation.
`render_still` supports optional grey clay shading and an explicit camera.
`snapshot_render_result` copies an existing Render Result without rendering.
Both retain verified PNGs and return a bounded inline preview.

## Editable film scenes

`build_agent_shot` accepts a complete local recipe and explicit inbox GLB actors.
It creates independent editable geometry, animation and camera movement.
`build_agent_timeline` assembles exact scene names and IDs with inclusive source
frame ranges into editable scene strips. `build_agent_review` arranges finished
inbox pictures, video and audio using explicit timing, trims, channels and loops.
These builders create fresh scenes and preserve the previously active scene.
They require no generation-service state. Inspect after an interrupted request
before retrying a non-idempotent build.

`inspect_agent_film` reports current editable contents and, optionally, original
build inputs. A persistent scene ID establishes identity, not a content revision.
`activate_agent_scene` selects a named local scene for subsequent scene tools or
capture. Scene identities must be unique when supplied. Film asset bindings use
required SHA-256 hashes and the same bounded inbox as other handoffs.

`export_agent_film` snapshots a director timeline or review and renders an MP4
in an isolated offline Blender process. `package_agent_project` copies selected
editable scenes and dependencies into a portable ZIP, rewrites external media
to relative paths and reopens it for verification. Both return a session
operation ID. Poll `get_agent_operation` to `DONE` before consuming its result;
`ERROR` and `CANCELLED` are terminal failures. `cancel_agent_operation` requests
cancellation without removing an already completed file.

The operation owner cancels unpublished work on load, Undo, Redo and extension
disable. Scene IDs bind identity; the snapshot captures current edits. A stopped
MCP listener does not cancel an admitted operation. Published outputs are kept
in the assistant outbox with verified path, size and SHA-256, and never overwrite
an existing file. Export and packaging have separate schema limits and refuse
unsupported media or dependencies.

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
| `import_agent_material` | Apply an assistant-created PBR map set from the local inbox to one named editable mesh with UVs. Supply base_color and explicit optional roughness OR smoothness, metallic, normal, height, ambient_occlusion and edge roles, each with filename and optional sha256. Maps accept PNG/JPEG/WebP or single-part non-deep scanline EXR, with matching dimensions and at most 8192 pixels per edge. Limits: 64 MiB per PNG/JPEG/WebP, 128 MiB per EXR, 256 MiB combined bytes, 32 megapixels combined and 128 million decoded channel samples. Creates a new packed material in the active slot while preserving other objects and slots. Base color is interpreted as sRGB including EXR; other maps are Non-Color. Smoothness is inverted into Roughness; Normal uses OpenGL tangent space; height uses bump; AO multiplies base color; edge remains a named packed node for manual wiring. Repeating an unchanged set in this session reuses it. Does not create maps or establish their physical accuracy. | `object_name`*: string<br>`maps`*: object; Explicit role to {filename, sha256?}; base_color is required<br>`normal_convention`: string (['OPENGL'])<br>`height_distance`: number | - |
| `restore_agent_material` | Restore the named mesh's material slot from the most recent assistant PBR application in this live file session. Refuses changed graphs, maps, destinations or copied geometry. A mesh copy created to isolate a shared mesh stays isolated. Restores only while the original data still exists; unavailable after load, undo, redo or extension disable. | `object_name`*: string | - |
| `import_agent_environment` | Apply an assistant-supplied equirectangular PNG/JPEG/EXR/Radiance HDR from the local inbox to the current scene. Requires actual 2:1 dimensions, at least 4x2, at most 32 megapixels and 128 MiB. Packs the image and creates a separate Environment Texture World. The previous World stays untouched and can be restored in this live file session. Repeat unchanged input and strength reuses it. Dimensions do not establish correct panorama projection, seams or high dynamic range. | `filename`*: string<br>`sha256`: string<br>`strength`: number | - |
| `restore_agent_environment` | Restore the previous World after the most recent assistant environment application in this live file session. Refuses an edited or replaced World or image. Unavailable after load, undo, redo or extension disable. | none | - |

### Saved local results

| Tool | Description | Arguments | Notes |
| --- | --- | --- | --- |
| `save_agent_result` | Save chosen inbox assets in a durable local library for discovery and reuse after Blender or the chat restarts. Supply a title, kind and files with required SHA-256 digests. Files are copied before publishing the record: at most 128 files and 512 MiB combined. For single files use role asset (default). MATERIAL requires explicit map roles with base_color and optional roughness OR smoothness, metallic, normal, height, ambient_occlusion and edge. MODEL_PACKAGE uses one asset plus resource files; OBJ textures use base_color, normal, roughness or metallic roles. Include every dependency. Saving verifies bytes, names and roles; format decoding and package completeness are checked by the import tool. Optional description is user-supplied provenance, not independently verified. No scene change, automatic scene application, upload or provider request. Identical requests reuse the same intact record. Local copies remain until explicitly removed outside this tool. | `title`*: string<br>`description`: string<br>`kind`*: string (enum: see tools/list)<br>`files`*: array | - |
| `list_agent_results` | Discover durable local saved results by title, description and kind. Returns paged metadata, stable result_ids and a damaged-record count. Listing validates metadata; inspect_agent_result or prepare_agent_result revalidates file bytes. No scene change. | `offset`: integer<br>`limit`: integer<br>`kind`: string (enum: see tools/list)<br>`search`: string | read-only annotation |
| `inspect_agent_result` | Inspect a saved local result's title, description, original names, roles and digests. With no preview_file, verify every saved file. With preview_file, verify that named PNG/JPEG/WebP/EXR and return a bounded inline image preview without changing Blender data. Other formats have file details only; this does not claim a rendered model or media preview. | `result_id`*: string<br>`preview_file`: string; Optional image basename in this result | read-only annotation |
| `prepare_agent_result` | Verify a saved bundle and copy its original names into the assistant inbox for deliberate reuse. Returns an import recipe with required destination choices. Preserves any identical inbox files and refuses collisions with different content; never overwrites a file. All saved files are verified before publication. Does not apply assets, reuse old target identities or authorize a mesh edit. Inspect the current scene and call the import tool explicitly after choosing its destination, placement, axes or media channels. | `result_id`*: string | - |

### Assistant audio and video

| Tool | Description | Arguments | Notes |
| --- | --- | --- | --- |
| `import_agent_media` | Import assistant-created WAV/MP3/OGG audio or MP4/WebM video from the agent_workspace inbox into the current scene's sequencer. Choose explicit free channels and start frame; occupied ranges are refused and existing strips, selection and scene FPS are preserved. Video playback is retimed to preserve its duration at the scene FPS. If existing strips make native retiming unsafe, use matching-FPS media or build_agent_review in a fresh scene. include_audio also requires a decodable audio stream and a different audio_channel. Identical unchanged requests reuse their strips after checking actual media and timing. The extension keeps a verified persistent media copy; strips cannot pack it, so moving the project requires copying that media and relinking its paths. This does not generate audio or video. | `filename`*: string; Media basename in the inbox<br>`kind`*: string (['AUDIO', 'VIDEO'])<br>`start_frame`: integer<br>`channel`: integer<br>`name`: string<br>`include_audio`: boolean<br>`audio_channel`: integer<br>`sha256`: string; Optional SHA-256 of exact media | - |

### Assistant models

| Tool | Description | Arguments | Notes |
| --- | --- | --- | --- |
| `import_agent_model` | Import a self-contained GLB 2.0 created by the connected assistant's modeling tools. Copy it to agent_workspace's inbox and pass its basename. Maximum 256 MiB; external file or URL references are refused. Meshes, hierarchy, materials, packed textures, rigs, skin weights, shape keys and animation clips are retained. A new group is placed with its frame-zero bottom center at the explicit location, or the current 3D cursor. Existing selection, current frame, timeline range and scene frame rate stay unchanged. Rigged imports require a Blender window context. This imports an existing generated model; it does not generate or rig one. Every call creates another group; inspect the scene after an interrupted call before retrying. Arbitrary Python execution may stay off. | `filename`*: string; GLB basename in the inbox<br>`sha256`: string; Optional SHA-256 of the GLB<br>`location`: array; World-space bottom-center location; defaults to the 3D cursor<br>`name`: string | - |

### Model packages and point clouds

| Tool | Description | Arguments | Notes |
| --- | --- | --- | --- |
| `import_agent_model_package` | Import an assistant-created glTF, OBJ, embedded-texture FBX or mesh PLY from the local inbox. List glTF buffers/images and OBJ MTL/texture maps explicitly in resources; copy all files into the flat inbox. OBJ texture images require base_color, normal, roughness or metallic role. Max 128 files, 256 MiB per file, 512 MiB total; glTF JSON max 8 MiB and MTL max 4 MiB. Local references are rewritten into a private snapshot. Textures are packed. FBX textures must be embedded with portable relative paths without traversal; expanded FBX arrays max 512 MiB. FBX/OBJ/PLY and rigged glTF require a Blender window. Existing selection/timing is preserved. Inspect returned dropped_references and frame_rate_mismatch. Each call creates a new group; inspect before retrying. | `filename`*: string<br>`sha256`: string<br>`resources`: array<br>`location`: array<br>`name`: string | - |
| `import_agent_splat` | Import assistant-created SPZ v2-v3, Gaussian PLY or .splat as a colored point cloud. Copy the file into the local inbox. Max 256 MiB, 20 million source points, 2 million retained points. Choose OPENGL or OPENCV source axes; defaults are OPENGL for SPZ and OPENCV for PLY/.splat. This is a point-cloud approximation, without Gaussian anisotropy or view-dependent color. SPZ v1 and v4 are unsupported. Each call creates a new group at location or cursor; inspect before retrying. | `filename`*: string<br>`sha256`: string<br>`max_points`: integer<br>`axes`: string (['OPENGL', 'OPENCV'])<br>`location`: array<br>`name`: string | - |

### Mesh editing

| Tool | Description | Arguments | Notes |
| --- | --- | --- | --- |
| `export_agent_mesh` | Export named meshes as a local embedded GLB with SHA-256, coordinate metadata and adjacent manifest. mode EDIT (default) grants a session edit handle for exactly one named static mesh. Its source must be local to this scene, in Object Mode, without modifiers, constraints, vertex groups, animation, shape keys, object material overrides or custom mesh properties other than this extension's verified blockout ownership marker. coordinates WORLD (default) preserves scene placement and requires an invertible orientation-preserving source transform for EDIT. Choose LOCAL to export source-local geometry and edit mirrored or singular sources without changing their transforms; the suggested result_to_source is identity. mode REFERENCE exports evaluated current-frame geometry with supported modifiers, current rig pose and shape keys baked into static meshes. It never grants edit authority or exports animation. Use object_name for one source, or object_names for 1-64 distinct REFERENCE/WORLD sources. WORLD reference geometry bakes transforms, correcting reflected face winding and corner normals. Use LOCAL for a singular reference transform. Realized surface meshes are required; temporary copies are limited to 2,000,000 geometry/attribute components and the GLB to 256 MiB. Selection, active scene, frame and source data are preserved. A new export supersedes this mesh's previous handle; pending handles expire on file load, Undo/Redo and extension disable. Up to 64 pending exports are retained. Edit the exported file with the assistant's available tools. For EDIT, return a self-contained GLB through the inbox and apply_agent_mesh. For REFERENCE, import returned assets separately. No animation retargeting or generation service is invoked. | `object_name`: string<br>`object_names`: array<br>`mode`: string (['EDIT', 'REFERENCE'])<br>`coordinates`: string (['WORLD', 'LOCAL']) | - |
| `apply_agent_mesh` | Apply a returned embedded GLB (up to 256 MiB) from the assistant inbox to the exact unchanged EDIT target captured by export_agent_mesh. REFERENCE exports cannot be applied in place. A LOCAL export can target a mirrored source because its mapping stays in source-local coordinates; source reflection is not baked into the returned edit. REMESH replaces geometry, UVs and materials; UV replaces active UVs; RETEXTURE replaces UV/material assignments; PARTS replaces the source mesh with 2 to 128 parts parented to the source; RIG attaches a compatible returned armature and weights. UV/RETEXTURE/RIG require matching indexed geometry after the explicit mapping. RIG accepts animation clips but rejects morphs; other policies require static meshes. result_to_source is an explicit orientation-preserving affine 4x4 matrix from returned Blender scene coordinates (after glTF axis conversion) into source-local coordinates. Never guess alignment. keep_original explicitly chooses whether to preserve a separate original object. Success consumes the handle, preserves source transforms/parent/selection, and reports native Undo availability. No separate restore command. On an uncertain failure inspect the scene before exporting again. Works with arbitrary Python execution disabled. | `handle`*: string<br>`filename`*: string; Returned GLB basename in the inbox<br>`sha256`: string<br>`policy`*: string (['REMESH', 'UV', 'RETEXTURE', 'PARTS', 'RIG'])<br>`result_to_source`*: array<br>`keep_original`*: boolean | destructive annotation |

### Scene blockout

| Tool | Description | Arguments | Notes |
| --- | --- | --- | --- |
| `blockout_plan` | Inspect the current scene's saved assistant blockout recipe, revision and edit safety. Also returns supported primitives, categories, colors, size limits and planning guidance. Use the connected assistant's reasoning to design or refine a complete recipe from the user's request. Reads local state without changing geometry. | none | read-only annotation |
| `build_agent_blockout` | Build a complete assistant-designed primitive blockout in its own collection. Creates category-colored boxes, cylinders, thin-box planes, wedges, cones and spheres, grouped by label, with explicit center positions, full XYZ sizes and optional XYZ rotation_degrees. Each element requires a stable unique id. Refinement replaces the entire saved recipe and requires its current expected_revision from blockout_plan. Validates and stages the whole plan before replacing pristine owned content. Refuses user edits, shared data and external references; preserves unrelated scene content. Identical plans with the current revision reuse the existing build. The recipe and revision persist in the blend file. | `plan`*: object<br>`expected_revision`: string; Required for replacing an existing blockout; read blockout_plan first | destructive annotation |
| `clear_agent_blockout` | Remove only the current scene's pristine owned blockout and its saved recipe. Requires the current expected_revision from blockout_plan. Refuses user-edited, shared or externally referenced objects, meshes, materials and collections, preserving them for manual review. Unrelated scene content stays intact. | `expected_revision`*: string | destructive annotation |

### Animation captures

| Tool | Description | Arguments | Notes |
| --- | --- | --- | --- |
| `capture_agent_animation` | Capture a silent local MP4 reference from the scene camera or 3D viewport for your assistant's own video tool. Requires Blender's GUI and an open 3D viewport. Choose a camera_name for CAMERA, or use the active/first scene camera. Supply both start_frame and end_frame, or use the current preview/scene range. Captures are limited to 20 seconds, 600 frames and 1920x1080; width and height must be even. grey_clay requests solid single-color shading. The original camera, frame, range, rendering and viewport settings are restored. Returns a unique retained outbox path, SHA-256 and clip timing. Copy the MP4 to your project for long-term storage. This does not generate video. | `source`: string (['CAMERA', 'VIEWPORT'])<br>`camera_name`: string; Camera in the current scene<br>`start_frame`: integer<br>`end_frame`: integer<br>`width`: integer<br>`height`: integer<br>`grey_clay`: boolean | read-only annotation; GUI required |

### Film inspection

| Tool | Description | Arguments | Notes |
| --- | --- | --- | --- |
| `inspect_agent_film` | List local Blender scenes and film identities, or inspect one named scene's actual timing, camera, sequencer strips and optional saved build recipe. Scene IDs establish identity, not content revisions. Manual Blender edits are authoritative; the stored recipe records original build inputs. Inspection does not open external media or verify that its files still exist. Lists are bounded and report truncation. Pass scene_id with scene_name to detect a replaced or duplicated identity. | `scene_name`: string<br>`scene_id`: string<br>`include_recipe`: boolean | read-only annotation |
| `activate_agent_scene` | Switch the current Blender window to an explicitly named local scene in Object Mode. Use the scene_id returned by a film builder or inspect_agent_film to guard scene identity. This lets scene tools and animation capture operate on a new shot while Python execution stays disabled. Preserves the geometry, timeline and selection stored in each scene. Reports whether the active scene changed. | `scene_name`*: string<br>`scene_id`: string | - |

### Film scenes and timelines

| Tool | Description | Arguments | Notes |
| --- | --- | --- | --- |
| `build_agent_shot` | Build a new independent editable shot scene from a complete assistant-authored recipe. Specify integer FPS, even resolution and 1 to 7200 frames within 120 seconds. Categorized primitives use blockout center positions/full XYZ sizes, linear RGB colors and optional grouped labels. Camera/target paths and full actor/primitive transform keys use strictly increasing integer frames starting at 1 with linear interpolation. Primitive motion scale multiplies its size; actor transforms use its GLB origin, Blender Z-up units and XYZ degrees, without automatic fitting. Every GLB actor requires an inbox filename and SHA256; geometry and rigs import independently and images are packed. Animated actors require an explicit zero-based animation_index from the source GLB, with once/loop/hold mode; phase selects a held pose. Builds a new scene, preserves current scene/selection/frame, and rolls back failed construction. Returns its persistent scene_id identity, timing and native undo availability; save the blend to retain it. | `plan`*: object | - |
| `build_agent_timeline` | Build a new editable director timeline from ordered explicit shot scene_name/scene_id references and inclusive source start_frame/end_frame ranges. Uses consecutive scene strips on channel 1 from timeline frame 1, with cut markers. All sources must be local shot scenes with matching integer FPS/resolution and a valid camera; duplicate scene identities and out-of-range trims are refused before mutation. scene_id is identity, not a content fingerprint: the timeline references each shot's current editable content. Supports up to 128 cuts within ten minutes and 72000 frames. Does not replace existing scenes or change current scene/selection/frame. Returns identity, timing and undo availability. | `plan`*: object | - |

### Film review

| Tool | Description | Arguments | Notes |
| --- | --- | --- | --- |
| `build_agent_review` | Create a NEW editable sequencer scene from verified assistant inbox media, preserving the active scene, selection and unrelated strips. Each call creates a distinct review scene. Pictures accept IMAGE (PNG/JPEG/WebP) or VIDEO (MP4/WebM); audio accepts WAV/MP3/OGG. Every item requires an inbox basename, exact sha256, start_frame, frame_count and channel. Source trim is seconds aligned to a destination frame boundary. Video preserves source seconds across FPS changes. loop repeats the source remaining after trim; false refuses insufficient source duration. IMAGE holds its frame_count and cannot trim or loop. include_audio requires a decodable video audio stream and explicit separate audio_channel. volume controls audio gain. Overlapping ranges on the same channel are refused. Limits: 600-second timeline, 128 items, 2048 resulting strips, 512 MiB total media, 64 MiB per image. Retained verified copies survive inbox cleanup; copy and relink those files when moving the blend project. Returns scene_name, scene_id, kind, timing, strips and retained_media. This tool does not generate media or export a finished film. | `name`*: string<br>`fps`: integer<br>`width`: integer<br>`height`: integer<br>`pictures`: array<br>`audio`: array | - |

### Film export and portable projects

| Tool | Description | Arguments | Notes |
| --- | --- | --- | --- |
| `export_agent_film` | Snapshot the current contents of one explicitly named director timeline or media review and render H.264 MP4 locally in an isolated Blender worker. Supports bounded SCENE strips and their selected camera or sequencer input. Optional scene_id guards identity, not revisions. Writes a new basename.mp4 in the assistant outbox; never overwrites. Use integer scene FPS 1-120, even dimensions 64-4096 and at most 15 minutes. Supply both frame bounds or neither. Audio defaults to audible timeline audio. Returns a session operation_id; poll get_agent_operation for progress and the verified output path/digest. File load, Undo, Redo or extension disable cancel unpublished operations. Does not change the working scene or its render settings. | `scene_name`*: string<br>`scene_id`: string<br>`filename`*: string<br>`start_frame`: integer<br>`end_frame`: integer<br>`width`: integer<br>`height`: integer<br>`audio`: boolean | - |
| `package_agent_project` | Snapshot explicit local scenes and their dependencies into a portable editable project ZIP in the assistant outbox. Each scene entry uses scene_name and optional scene_id. The new basename.zip contains Project.blend, copied relative Media, local recipes, a manifest and opening instructions. Verifies the rewritten blend by reopening it in an isolated Blender process and reads back the entire ZIP. Preserves packed images. Refuses linked/overridden data, movie clips, caches, volumes, tiled/sequence textures, and packed, retimed or modified sound strips. Limits: 256 scenes, 2000 external media files, 16 GiB media, 4 GiB blend. Returns operation_id for get_agent_operation; never overwrites and cancels unpublished work on load/Undo/Redo/disable. Stored recipes are build inputs; the blend contains current edits. No generation service is required to open the extracted project. | `scenes`*: array<br>`filename`*: string | - |
| `get_agent_operation` | Read a local film export or project package operation from this Blender session. Returns phase, progress, sources, result and error. DONE includes the verified local output path, SHA-256 and size. Reading may finalize a completed worker's prepared file in the outbox. Operation IDs do not survive restarting Blender. | `operation_id`*: string | - |
| `cancel_agent_operation` | Request cancellation of a pending local film export or project package. The worker stops and its private staging is removed; unpublished output cannot appear later. A completed published file remains available. Poll get_agent_operation until CANCELLED, ERROR or DONE. | `operation_id`*: string | - |

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
| `render_still` | Capture a quick OpenGL camera or viewport still as a PNG at the requested size. | `source`: string (['CAMERA', 'VIEWPORT'])<br>`camera_name`: string<br>`width`: integer<br>`height`: integer<br>`grey_clay`: boolean | read-only annotation; GUI required |
| `snapshot_render_result` | Save the existing Blender Render Result as a verified PNG without starting a render. | none | read-only annotation |
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
