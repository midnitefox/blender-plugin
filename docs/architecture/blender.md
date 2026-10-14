# Blender boundaries

Read [AGENTS.md](../../AGENTS.md), [UI style](../UI_STYLE.md) and
[validation](../development/validation.md) before changing Blender-facing code.
These requirements apply to `scenario/blender/`, scene tools in `scenario/mcp/`
and native tests. Pure protocol and core modules must remain free of `bpy`.

The current assistant entry points are listed in the [runtime map](runtime.md).
References below to catalog workers, JobSession, cloud claims, paid jobs and
upstream preview panels describe retained service source. They do not apply to
assistant setup or authorize adding those systems to active registration.

## Threads, context and storage

Keep `bpy` access on Blender's main thread. The GUI timer in
[assistant_loop.py](../../scenario/blender/assistant_loop.py) processes local MCP
requests and pending file-modified marks; the
[MCP service](../../scenario/blender/mcp_service.py) supplies headless queue
processing. A GUI timer is not proof that background mode delivers work.
Never mutate properties in `draw()` or keep a worker dependent on a live panel.

Check `bpy.app.online_access` before network activity. Store state and cache
through `bpy.utils.extension_path_user`; never write into the installed package.
Use an isolated disposable profile for every development build or probe.

Open folders, files and web links with `scenario.open_path` and
`scenario.open_url` from [`desktop.py`](../../scenario/blender/desktop.py), and
players with `core.jobs.playback.system_open`; never with Blender's
`wm.path_open` or `wm.url_open` or Python's `webbrowser`. Those start the system
opener with Blender's whole environment, and the launched application inherits
the `SCENARIO_` credentials an environment-configured session holds. The
playback launchers strip `SCENARIO_`, `PYTHON` and proxy variables, accept only
absolute paths and `https` links, and a unit-test source scan rejects any other
opener in `scenario/`.

Decode and play generated media in owned offline Blender processes, never with
`aud` in the user's session: audio envelopes come from
[`waveform_worker.py`](../../scenario/blender/waveform_worker.py) and **Play
audio** runs [`audio_player_worker.py`](../../scenario/blender/audio_player_worker.py),
both started with `--offline-mode`, `--factory-startup`, `--disable-autoexec`,
`--background` and `-noaudio` from bpy-free owners in `core.jobs`. Their
directories are removed off Blender's main thread: the decoder's on the preview
lane, and each player's by its own watcher thread. See
[in-extension audio playback](../RESULT_PREVIEWS.md#in-extension-audio-playback).

Mark the file modified after every main-thread change that no Undo operator
records; see [marking the file modified](#marking-the-file-modified).

Assistant operations must bind work to the captured scene and target identity;
names alone cannot identify a deleted or replaced object. Deferred results must
refuse a replacement scene. Retained service integration additionally uses
account/project scope and [JobSession](../BLENDER_JOB_CONTEXT.md) origins. Closing
a view must not route pending results to another target.

## Marking the file modified

Blender asks to save on quit only for a file marked modified (`bpy.data.is_dirty`),
and only an undo push marks it: assigning data from Python, a timer or a local
MCP tool leaves the file looking saved. An operator that declares `UNDO` records
its own step. Every other main-thread change calls
[`file_state.mark_modified`](../../scenario/blender/file_state.py) once it is
complete: local MCP tools that change the file, and the results, World
restoration, Blockout plans, Prompt Spark prompts, workflow forms and uploads
that maintenance delivers whichever panel or tool started them. Each mark calls
`bpy.ops.ed.undo_push` with a name such as **Scenario image import**, which Undo
History lists when Global Undo is on. The operator needs a window; Blender runs
app timers with its first window, and the helper supplies that window when a
context has none. A saved model, mesh edit or first frame whose own desktop
checkpoints were recorded is not marked again. Never mark from an operator that
declares Undo, which would record a second identical step, or while drawing.

Mark only an actual change. Validate before the first write, or compare a reading
of the data a command may change before and after it with `file_state.marking`.
A refusal before any change then leaves the file unmarked, and a failure after a
partial change still marks it. An application whose outcome is uncertain is
marked, because data may remain; one whose rollback was verified is not.

No Python API sets `is_dirty` directly. Blender's `ED_undo_push` tags the file
modified before it records a step, even when Global Undo is off or no steps are
kept, so a push is the only route. A sculpt, paint or paint-curve stroke holds
an open undo step that a push would close under another name, so a mark made
during a stroke waits and the next pump tick records it once the stroke ends;
reading a file drops a waiting mark. In background mode the first push creates
Blender's undo stack.

The push tags no data-block and runs no dependency-graph, undo or redo handler,
so origins, quotes and approvals captured before it stay current. Catalog
seeding, restored model choices, project defaults, estimate state and upload
progress links stay unmarked: marking them would flag every opened file as modified (see
[catalog delivery](sdk-runtime.md#active-sdk-catalog)). Local MCP has no save tool,
and nothing saves the file for the user. Blender 5.0 reports every background
file as modified, even right after reading it; from 5.1 a file read in the
background starts unmarked, as on the desktop.

## Temporary preview decoding

[`preview_decode.py`](../../scenario/blender/preview_decode.py) decodes a local
image into a bounded preview on Blender's main thread. It loads the file inside
`bpy.data.temp_data()` and fits it within a requested edge without upscaling.
It then returns RGBA display values with straight alpha, unpremultiplying high
bit-depth and float images and converting them from linear to sRGB, or writes
an 8-bit RGBA PNG through a temporary scene with the Standard view transform.
String and path arguments both pass the local capture path check as path
objects. Every failure raises `PreviewDecodeError` with a message that names
neither the path nor Blender's own error.

Decode display-only images this way, never by loading them into `bpy.data` and
removing them again. Removing a main-data image makes the next dependency-graph
evaluation report updates for the scene, its objects and its collections. The JobSession dependency handler then
invalidates every origin captured in that scene (see
[origin lifetime](../BLENDER_JOB_CONTEXT.md#origin-and-quote-lifetime)): quotes
bound to them can no longer be prepared or spent, and completed results wait
for review instead of automatic application. The temporary block reports no
ID updates. Writing the PNG runs the handler with an empty update list, which
JobSession ignores.

A Python reference into the temporary block that outlives it reads freed
memory: it can crash Blender or reach data that later reuses the memory. The
helper therefore keeps the image, the scene and its render settings in helper
frames that end inside the block, drops its own references before the block
closes and raises only after the block is freed. Neither the error's context
nor the locals of the helper frames on its traceback refer into the block.
The helper does not inspect the file first: `limit` sees the dimensions only
after Blender has loaded the whole image, so callers check the format and
declared dimensions before decoding.
[Workflow review previews](../RESULT_PREVIEWS.md#workflow-review-assets) decode
their icons with it after verifying the cached file, its format and its declared
dimensions, and the
[automatic result preview](../RESULT_PREVIEWS.md#automatic-previews-in-blender)
controller decodes its image copies and icons with it. Local MCP reaches it only
through that controller, which also decodes the image stills that
[`inspect_result_previews`](../RESULT_PREVIEWS.md#local-mcp-inspection)
requests on the shared scheduler; no MCP tool calls it directly.

## Scene application

[Mesh application](../MESH_APPLICATION.md) applies one saved GLB to one captured
mesh under explicit remesh, UV, retexture, parts or rig policies with guarded
rollback; [`mesh_result_application.py`](../../scenario/blender/mesh_result_application.py)
connects the synchronous `mesh_application.py` primitive to verified saved results.
Preserve materials, object context and the documented ownership checks; do not
relax rollback checks merely to make an edited target pass.

[World application](../WORLD_APPLICATION.md) preflights supported image formats,
loads and packs the image, and installs a new World on an explicit scene.
Its restoration checks protect later user changes. The explicit JobSession World
command wraps one verified saved asset with original-context checks and a durable
application claim. Recovered unapplied results, and completed results through a
separate reuse claim, have shared native/MCP World approval and guarded
session-local restoration for saved PNG, JPEG and OpenEXR panoramas.

Existing [image](../../scenario/blender/apply_image.py),
[3D import](../../scenario/blender/apply_3d.py),
[material](../../scenario/blender/apply_material.py),
[video](../../scenario/blender/apply_video.py) and
[audio](../../scenario/blender/apply_audio.py) application paths retain useful
behavior and native tests during integration. One provider job can return many
variants; use [primary-mesh selection](../../scenario/core/scene/placement.py)
instead of importing every asset as a separate result.

## Compatibility and proof

The approved floor is Blender 5.0. Check API availability at the supported
versions and validate the installed ZIP with the native test runner. Syntax
compatibility, a manifest declaration and ZIP validation are separate from
successful native execution. UI changes additionally need actual focus,
keyboard, viewport and screenshot review; headless tests cannot establish that.

## Saved media strips

[`media_application.py`](../../scenario/blender/media_application.py) is the
shared saved-result primitive for MP4/WebM movie and MP3/WAV/OGG sound strips.
It snapshots and rehashes a bounded local receipt before decoding on Blender's
main thread, keeps existing strips and timing unchanged, and rolls back only
new data. Its persistent file remains necessary for the strip. The
[JobSession command](../BLENDER_JOB_CONTEXT.md#explicit-saved-video-and-audio-application)
adds exact scene/frame approval, a durable claim and receipt-only recovery.
Prototype video/audio operators remain separate; they are not the entry points
for newly generated shared jobs.

## Saved models

[`model_application.py`](../../scenario/blender/model_application.py) stages one
receipt-bound embedded GLB, or one saved GLB, glTF, FBX, OBJ or mesh PLY package
snapshot rehashed on the main thread before its claim, in a disposable scene, then
publishes a new collection/group at an approved cursor. It preserves the original
selection, packs only textures embedded in or read from the package, retains rigs
and node/morph animation clips, and keeps the destination timeline unchanged. The
[model guide](../MESH_APPLICATION.md#explicit-saved-glb-import) and its
[package section](../MESH_APPLICATION.md#saved-model-package-import) detail
supported containers, bounded synchronous work and rollback.
[`splat_application.py`](../../scenario/blender/splat_application.py) builds a
worker-decoded splat snapshot on the main thread as a new mesh, Geometry Nodes
point group and unlit material, never reusing data by name, and publishes it like
a model group; see [saved Gaussian splat import](../MESH_APPLICATION.md#saved-gaussian-splat-import).
In-place mesh edits are the separate [scene application](#scene-application) path.

## Saved materials

[`material_application.py`](../../scenario/blender/material_application.py)
loads only unambiguous receipt-bound texture maps into new packed images and a
new material. It assigns one approved mesh slot, preserving old material data,
other slots and face indices. The [material guide](../MATERIAL_APPLICATION.md)
details target restrictions, bounded decoding, rollback and receipt-only recovery.
Native and MCP actions share the scoped session; prototype material callbacks
are not used for this saved-result command.

## Film shot scenes

[`film_scene.py`](../../scenario/blender/film_scene.py) provides transactional local
shot construction and editable scene-strip timeline assembly. It validates the
whole recipe before mutation, uses receipt-checked GLB import for explicit heroes,
restores the working scene and removes newly created data on failure. See the
[Film primitive contract](../FILM_PLAN.md#native-shot-and-timeline-primitives) for
choreography and source identity. UI and MCP reach it only through the
[shared shot application command](../FILM_PLAN.md#shared-shot-application-command),
which claims every source job before building.

## Local render snapshots

[`local_capture.py`](../../scenario/blender/local_capture.py) writes the selected
local scene and dependencies to a private blend file without saving or changing
the working file. Only this snapshot step touches the parent's bpy state.
The [local render primitive](../FILM_PLAN.md#local-capture-foundation) owns its
offline child process and disposable profile; its standalone worker changes
render settings only in that child. Still capture does not require ffmpeg.
The [Film capture owner](../FILM_PLAN.md#shared-capture-and-upload-approval)
binds native/MCP approval to the recipe and selected shot, queues work through
the existing session and accepts completion only in the unchanged original
context. Separate upload approval hashes the staged bytes against the rendered
receipt. The session joins workers before cleaning retained capture artifacts;
no bpy mutation runs on a worker or during drawing.

## Film saved-media review

[`film_review.py`](../../scenario/blender/film_review.py) provides transactional
local final/previs sequence assembly from explicitly scoped, measured receipts.
It rehashes independent copies before movie/audio decoding, preserves the working
scene and removes only new data on a fully rolled-back failure. Copies persist
for successful sequences and remain available after uncertain cleanup. It
supports native trims, audio loop/duck segments and optional muted masters, and
requires matching movie/cut frame rates. The [review contract](../FILM_PLAN.md#native-saved-media-review-primitive)
describes byte/strip limits. Its shared session command supplies worker
preparation and generated-source claims before native decoding. Private copy
identities are rechecked around decoding without GUI-thread copying or hashing.
Session-owned [native/MCP review controls](../FILM_PLAN.md#native-and-mcp-review-controls)
separate preparation from the build approval. Drawing reads cached status, the
maintenance pump advances work, and the build runs on the main thread only in
Object Mode in a Blender window. The [portable case-study bundle](#film-case-study-bundle)
packs a built review.

## Film video export

[`local_capture.export_snapshot`](../../scenario/blender/local_capture.py)
synchronizes one sequencer scene's own view layers, stamps its external media
and writes a private snapshot on the main thread without changing the working
file, scene or frame. The bundled
[`film_export_worker.py`](../../scenario/blender/film_export_worker.py) changes
render settings only in an offline child, encoding H.264/AAC MP4 with Blender's
built-in FFmpeg, and in verification mode decodes the result in a fresh child.
The session validates each destination on the main thread against the staging
root, extension storage and Blender's user folders before any work starts.
Rendering, verification and no-overwrite publishing stay in bpy-free core code on
a session-owned export thread; no bpy runs there. See the
[export contract](../FILM_PLAN.md#offline-film-video-export-primitive) for bounds,
verification levels, cancellation and crash leftovers. Session-owned
[native/MCP export controls](../FILM_PLAN.md#native-and-mcp-video-export-controls)
inspect review scenes and snapshot on the main thread, separate the destination
choice from the render approval and read cached status while drawing; the
maintenance pump delivers outcomes.

## Film case-study bundle

[`film_case_study.py`](../../scenario/blender/film_case_study.py) inspects review
scenes and director timelines, follows the chosen scenes' data with
`bpy.data.user_map`, stamps the external files they read and writes one private
snapshot with absolute paths on the main thread, without changing the working
file, scenes, frame or media paths. It refuses linked data and data that cannot
travel before any approval. The bundled
[`film_case_study_worker.py`](../../scenario/blender/film_case_study_worker.py)
edits only that snapshot in an offline child: it fills each rewritten path field
with the longest value Blender stores there before setting its bundle-relative
value, rebuilds sound strips so their hidden paths are relative, repacks packed
images and removes Scenario properties that name jobs, assets or productions
together with the extension's registered settings, which Blender 5 stores apart
from custom properties. The main thread scans for identifier-like values of
everything removed, including nested groups, lists and byte strings in settings,
node trees, master collections and strips; descriptive settings such as model
choices are skipped. Rewritten paths and image strip frames use `/`: Blender
converts every path to the native separator, `\` on Windows, when it opens a
file, and `os.path.join` reads a `//` folder as a Windows network share. A second
child reopens the written library and reports its file references with `/`
separators, because its listing joins a strip's folder and file name with the
native separator. Media copies, checks, private scans
and the archive run in bpy-free core code on the session-owned export thread. See
the [bundle contract](../FILM_PLAN.md#portable-case-study-bundle). Drawing reads
cached status.
