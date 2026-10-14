# Open Studio for Blender: user guide

Open Studio connects an assistant to your local Blender project. The current
build supports image, material, environment, model and audio/video handoff,
alongside local scene, camera and film tools. It is a development preview; full
creative workflow parity with the upstream extension is still in progress.

## Install

Use Blender 5.0 or later and this fork's extension ZIP. Builds, when published,
are available in [Open Studio releases](https://github.com/midnitefox/blender-plugin/releases).
A ZIP built from this checkout can also be installed.

1. Disable an older copy of the extension if one is installed.
2. Open **Edit > Preferences > Get Extensions** and choose **Install from Disk**
   from the menu in the upper right.
3. Select the ZIP and enable **Open Studio**.
4. Open the 3D viewport sidebar with **N** and select **Open Studio**.

No generation account or credentials are configured in Blender. Your assistant
handles its own generation tools. Update by installing a newer ZIP from this
fork, with Blender closed while replacing files if an installation tool requires
it. Keep a backup of important `.blend` files before testing development builds.

## Assistant connection (MCP)

MCP lets your assistant call tools in the open Blender project. Open
**Open Studio** in the viewport sidebar, allow Blender's online access and
start the local server. Copy the setup shown for your assistant. Keep Blender open while using it.

All copied client setups launch the included stdio bridge with `--connection-file`.
The bridge reads Blender's current local address and token from that private
file, so it can follow a server restart. Paste the copied setup into the
assistant's MCP configuration workflow, then reconnect the assistant's MCP client.
Ask the assistant to run `scene_summary` to check the connection.

Copy a fresh configuration after moving or reinstalling the extension if its
installation path changes. Keep the connection file and token private. The
[MCP reference](MCP.md) explains the other clients and local permissions.

## Bring an assistant-generated image into Blender

For example, ask: "Generate a weathered wood base-color image and apply it to
my UV-mapped crate in Blender." The assistant needs its own working image tool
and the ability to save or copy the resulting file on this computer.

The handoff is:

1. Inspect the scene and identify the destination.
2. Generate or edit the image using the assistant's image tool.
3. Call `agent_workspace` and copy the file into its returned inbox.
4. Call `import_agent_image` with the image's basename and the desired mode.
5. Check the result in Blender and save the `.blend` file.

Blender receives an existing image through this handoff. The extension does not
invoke the assistant's image tool or manage its account. A local image you
already have can follow the same import process.

| Mode | Use | Requirements |
| --- | --- | --- |
| `REFERENCE` | Concept art or a modeling reference at the 3D cursor. | An image file; this is the default mode. |
| `TEXTURE` | Apply a base-color texture to a mesh's active material slot. | Exact editable mesh name and existing UV coordinates. |
| `IMAGE` | Load packed pixels for later use. | An image file. |

The inbox accepts PNG, JPEG and WebP files up to 64 MiB. Import validates the
file and decodes it in Blender before applying it. It packs the image into the
project, so the saved `.blend` file can retain it after inbox cleanup. Repeating
the same image and destination reuses the imported content.

Texture mode creates a new base-color material for the chosen object. Materials
used by other objects are preserved. It supplies one texture input; roughness,
normal and other PBR maps use the dedicated material handoff below.

## Apply a material map set

Ask the assistant to create or prepare matching maps, copy them into the inbox,
and call `import_agent_material` for the exact mesh. The mesh needs UVs and must
be editable in Object Mode. Supply `base_color` and whichever of `roughness`,
`metallic`, `normal`, `height` and `ambient_occlusion` are available:

```json
{"object_name":"Crate","maps":{"base_color":{"filename":"wood.png"},"roughness":{"filename":"wood-roughness.png"},"normal":{"filename":"wood-normal.png"}}}
```

Maps must be PNG, JPEG or WebP with matching dimensions, up to 64 MiB each,
256 MiB combined and 32 megapixels combined. Base color uses sRGB; data maps
use Non-Color. Normal maps must use OpenGL tangent space. Height adds bump;
it does not change geometry. AO multiplies base color. The tool creates a new
material in the chosen mesh's active slot and preserves other slots and objects.
It packs each map into the saved project. Creating accurate, aligned PBR maps
depends on the assistant's tools and requires visual review.

`restore_agent_material` restores the previous assignment for the named mesh
while the imported result remains unchanged. Restoration is available only in
the current session, until file load, Undo/Redo or extension disable. It refuses
to discard changes made after application.

## Apply a panoramic environment

Have the assistant prepare a 2:1 equirectangular PNG, JPEG, EXR or Radiance HDR,
copy it into the inbox, then call:

```json
{"filename":"studio.exr","strength":1.0}
```

Use that object as the arguments to `import_agent_environment`. The limit is
128 MiB and 32 megapixels. The image is packed and assigned to a new World.
A 2:1 image must actually depict an equirectangular panorama; its dimensions
alone do not establish projection, a seamless horizon or high dynamic range.
`restore_agent_environment` returns to the previous World while the applied
result is unchanged, with the same session limits as material restoration.

## Import a model

The assistant can copy a self-contained GLB 2.0 into the inbox and call
`import_agent_model` with its filename. Files may be up to 256 MiB; external
file and URL references are refused. The model retains hierarchy, materials,
packed textures, rigs, skin weights, shape keys and animation clips supported
by Blender's importer. Rigged import requires a Blender window context.

Use `name` to label its new collection and `location` for a world-space
bottom-center position. The default position is the 3D cursor. Existing
selection, current frame, timeline range and frame rate are preserved. Save the
project to retain the model. Each call creates a new group; after an interrupted
call, inspect the scene before retrying. Creating, remeshing or rigging a model
requires suitable assistant tools; this handoff imports their finished result.

For glTF or OBJ packages, ask the assistant to use `import_agent_model_package`
and list every buffer, material file and texture in `resources`. Copy all files
into the flat inbox. OBJ maps need explicit `base_color`, `normal`, `roughness`
or `metallic` roles. The same tool imports embedded-texture FBX and mesh PLY.
Limits are 128 files, 256 MiB per file and 512 MiB total; glTF JSON is limited to
8 MiB and MTL to 4 MiB. FBX/OBJ/PLY need a Blender window. Review reported dropped
OBJ references and any FBX frame-rate mismatch before using imported animation.

`import_agent_splat` accepts SPZ v2/v3, Gaussian PLY and `.splat`, up to 256 MiB
and 20 million source points. It keeps up to two million points and lets the
assistant choose source axes. The result is an approximate colored point cloud;
Gaussian shape, rotations and view-dependent color are not reproduced. SPZ v1
and v4 are unsupported. The points remain in the saved `.blend` file.

## Edit a mesh through the assistant

Ask the assistant to inspect and export the exact mesh using `export_agent_mesh`.
It receives a local GLB, its fingerprint and a session handle tied to that object.
The assistant edits the file with its available tools, returns the GLB through
the inbox, and calls `apply_agent_mesh` with an explicit operation and coordinate
mapping. It must verify alignment before applying the result.

Supported operations are remesh, UV replacement, retexture, splitting into parts
and compatible rig attachment. UV, retexture and rig attachment require matching
indexed geometry. The source must be a local static mesh in Object Mode without
modifiers, constraints, vertex groups, animation, shape keys, object material
overrides or custom mesh properties. Existing rigs are not retargeted.

Keep the exported source unchanged while the assistant works. File load,
Undo/Redo, disabling the extension or exporting that mesh again invalidates its
previous handle. Up to 64 pending exports are kept. The assistant explicitly
chooses whether to retain a separate original. Successful application consumes
the handle and reports native Undo availability. Inspect the scene after an
uncertain failure before exporting again.

## Design and refine a blockout

For example, ask: "Lay out a small courtyard with a fountain, covered walkway
and two seating areas, then leave room for a camera to orbit it."

The assistant calls `blockout_plan`, designs a complete recipe using boxes,
cylinders, planes, wedges, cones and spheres, and submits it to
`build_agent_blockout`. Elements have explicit categories, colors, names and
transforms. A recipe can contain up to 200 elements. The extension saves the
recipe and revision with the project so it can be inspected after reopening.

For refinement, the assistant reads that saved recipe and submits the full
updated version with its current revision. Rebuild and `clear_agent_blockout`
affect only the owned collection. They refuse stale revisions, manual edits,
shared data or external references. Preserve such edits before replacing the
blockout; the tools do not force-delete them.

## Add audio or video to the timeline

The assistant copies WAV, MP3, OGG, MP4 or WebM into the inbox and calls
`import_agent_media`. Choose `AUDIO` or `VIDEO`, a start frame and a free channel:

```json
{"filename":"shot.mp4","kind":"VIDEO","start_frame":1,"channel":1,"include_audio":true,"audio_channel":2}
```

The maximum is 512 MiB per file. The tool validates the media before adding
strips, refuses occupied channel ranges and preserves existing strips and scene
frame rate. Video is retimed to retain its duration to the nearest scene frame.
Embedded audio is included only when requested and requires a decodable stream
and a separate free channel. Repeating an unchanged request reuses its strips.

If existing strips make Blender's native retiming unsafe, rate-mismatched video
is refused before insertion. The assistant can prepare video at the scene's
frame rate or assemble it in a fresh `build_agent_review` scene, which safely
orders the timing operations while preserving the requested layout.

Audio and video remain external files. The tool returns the retained `asset_path`
inside the extension's user storage. Keep that file for playback. To move the
project, copy the retained media alongside the `.blend` and relink its sequencer
paths. Deleting the original inbox file does not remove the retained copy.

## Local scene and camera tools

Ask the assistant to inspect your scene, describe a named object, select objects,
move to a frame, or plan a camera move. `camera_path` can create an animated
camera from a preset, a description or explicit waypoints. Review the resulting
camera and animation in Blender before saving.

`screenshot_viewport` captures the visible 3D viewport with overlays.
`render_still` returns a quick OpenGL camera or viewport still at a chosen size.
Both require the graphical interface and return image content to the assistant.

`capture_agent_animation` records a silent camera or viewport MP4 for the
assistant's video tool. It defaults to 1280×720 and accepts up to 1920×1080,
20 seconds and 600 frames. Supply both start/end frames or use the current
preview/scene range. Grey clay is optional. The tool restores the original
camera, current frame, timeline range, render and viewport settings, then
validates the produced clip's actual dimensions and timing. It returns a
retained outbox path; copy the clip to the project for long-term storage.
The assistant can process this reference using its available video tools and
bring the finished clip back through `import_agent_media`.

The mesh-edit workflow also accepts primitives made by `build_agent_blockout`.
After a mesh edit, blockout refinement and clearing refuse to replace that
changed content. Preserve the edited objects before starting a new blockout.

## Build an editable film

Ask the assistant to plan the shots, create the scene assets with its available
tools, and build the film in Blender. For example: "Make two courtyard shots:
a wide approach followed by a close view of the fountain. Keep the scene and
camera movement editable, and assemble the shots for review."

`build_agent_shot` creates a fresh scene from an explicit recipe: timing,
resolution, colored primitives, optional self-contained GLB actors, camera
positions and camera targets. Actors use files from the inbox with their exact
SHA-256 hashes. Each shot owns its imported geometry and animation. Actor
animation requires an explicit clip selection; the assistant must inspect the
asset and choose a compatible clip. This does not retarget an unrelated rig.

`build_agent_timeline` combines ordered shot scenes using their returned names
and identities, with explicit inclusive source frame ranges. The result uses
editable scene strips. These builders preserve the scene you were working in
and create a new scene on every successful call. After an interrupted request,
the assistant should inspect existing scenes before retrying.

`inspect_agent_film` lists scenes or inspects one scene's actual camera, timing
and strips. It can also return the original build recipe. Manual Blender edits
remain authoritative; the saved recipe does not automatically track those edits.
`activate_agent_scene` selects a shot for scene tools and camera capture without
requiring Python execution.

The assistant can capture shot references, process them with its available video
tools, then use `build_agent_review` to arrange finished inbox images, video and
audio in a new sequencer scene. The recipe specifies destination frame counts,
free channels, source trims in seconds, volume and optional looping. Images hold
for their declared duration. Video retains its source timing at the review's
frame rate; audio must be explicitly requested. Each source requires its exact
hash. Files are copied to retained local storage so inbox cleanup does not break
the review. Save the project and keep its external media with it.

`export_agent_film` renders a director timeline or media review to H.264 MP4
using an isolated Blender process. It accepts an explicit frame range, even
dimensions from 64 to 4096, integer frame rates from 1 to 120, and up to 15
minutes. Audio can be explicitly enabled or disabled. The working scene and
its render settings stay intact.

`package_agent_project` creates a ZIP containing the selected editable scenes,
their dependencies, copied media, recipes and an opening guide. It rewrites
media paths to be relative and reopens the copied project to verify them.
Extract the whole ZIP and open `Project.blend`, keeping `Media` beside it.
Ordinary Blender can open this project without the extension. The portable
format supports local scenes and standard images, fonts and audio/video; it
refuses linked libraries, caches, volumes, tiled/sequence textures and modified
or retimed sound strips. The tool reports unsupported content before delivery.

Exports and packages return an operation ID. The assistant checks
`get_agent_operation` until the state is `DONE`, then receives the verified
local file path and hash. It can request `cancel_agent_operation` while work is
pending. File load, Undo/Redo and disabling the extension cancel unpublished
work. Stopping the connection alone lets an admitted local operation continue.
Completed files remain in the local outbox; copy them to your project folder.
Output names are never overwritten, so a revised export needs a new filename.

## Python permission

The dedicated tools above work with arbitrary Python execution disabled. For
more general Blender scripting, **Allow connected assistants to run Python** is a
separate preference, off by default. Enable it only for an assistant you trust
with your local files and Blender project. Python runs with Blender's user
permissions; the limited blocked operations do not make it a sandbox.

The assistant can call `blender_api_help` to inspect the running Blender Python
API before writing a script. This read-only helper does not need Python
execution permission.

## Headless use

For a separately configured isolated background session:

```sh
blender --background --online-mode --command open_studio --port 9876
```

The extension must be enabled in that Blender profile. Authentication details
are written to the private connection file. Use the stdio bridge with that
file; screenshots and OpenGL captures need the GUI. See the [MCP reference](MCP.md)
for the optional explicit token configuration.

## Troubleshooting

- **Assistant cannot connect:** keep Blender open, allow its online access,
  start the local server, copy the current setup and reconnect the MCP client.
  The stdio bridge needs the current extension and connection-file paths.
- **Assistant cannot generate an image:** check the assistant's own image tool.
  Blender's local import works with a generated or existing supported file.
- **Image import fails:** put the actual image file in the returned inbox and
  pass only its basename. Use PNG, JPEG or WebP within the size limit.
- **Texture destination fails:** use an editable mesh in the current scene with
  UV coordinates. Inspect the exact object name with `scene_summary`.
- **Screenshot fails:** open a 3D viewport in Blender's graphical interface.
- **Python is disabled:** use the dedicated local tools, or explicitly enable
  the separate Python preference when the task requires trusted scripting.

For fork bugs, follow [Support](../SUPPORT.md). Include Blender and extension
versions, the action and a sanitized error. Keep the token and connection file
out of reports.

## Privacy

A connected assistant can receive requested scene data and viewport captures.
It may process those results using its own service. Local asset import makes no
external request; the assistant's generation step follows its own account and
service settings. Keep the local token and private connection file out of shared
reports. Imported pixels remain in a saved `.blend` file after inbox cleanup.
See [Privacy](PRIVACY.md) for permissions and local storage details.

## Historical upstream reference

Retained architecture documents and old screenshots describe the upstream
service-backed extension. Their references to model catalogs, saved cloud
results, materials, panoramas or generation panels are historical source context.
Those workflows are not current setup or usage instructions for this build.
This guide and the [current MCP reference](MCP.md) define the exposed workflow.
