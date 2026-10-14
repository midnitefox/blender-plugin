# Local MCP reference

## Overview

`scenario-blender` connects an agent to the open Blender scene and generation
into that scene. The hosted server at `mcp.scenario.com` provides platform-wide
operations such as collection management, training, workflow authoring and usage.
Local workflow discovery and approved execution use the same saved-job session
as model generation. Local [asset organization](#asset-organization) reviews
collection membership and tag changes for library assets in Blender's selected
connection before sending them. Connect either
or both according to the task; they have separate authentication and capabilities.

The local server accepts MCP JSON-RPC over HTTP at
`http://127.0.0.1:9876/mcp` by default. It starts with the extension in a GUI
session when Blender's **Allow Online Access** is on. The **MCP Port** preference
sets the first port; if busy, the server tries the next three. Copy the actual
URL from **Scenario > Agents (MCP)**. An authenticated GET on `/mcp` returns 405:
use POST for tool calls; the server does not provide an SSE stream.

The extension is experimental. The [runtime map](architecture/runtime.md)
separates implemented helpers from active UI/MCP integration. A tool description
is guidance for the connected agent, not a server-enforced spending approval.
Every model lane, through `generate` or a native form, requires a session-owned
quote and the exact approved cost; pending files, captures and Prompt Spark
preparation must finish before a usable final quote.

`list_local_jobs` inspects the separate credential-scoped durable store and returns
saved costs, revisions and suggested recovery actions without contacting Scenario.
It includes MCP and native form model submissions, not prototype records, and
never refreshes remote jobs.
Rows identify `source: generation` or `cloud`. A record created through the
[cloud adoption foundation](JOB_COORDINATOR.md#adopting-a-completed-cloud-job)
has `cu_cost_exact: null`, because no local quote exists. `job_status` preserves
the same distinction. Use `recover_cloud_job` to save a completed cloud model
job before explicit download and result approval.
`cancel_prepared_job` cancels only an unsubmitted durable intent, using the context
token and revision from inspection. A reset, file load or credential switch invalidates that
token. Claimed or uncertain submissions require reconciliation, never blind retry.
An identical request is refused while such a submission is unconfirmed; only the
user can mark it reviewed in Blender, so `job_status` omits `mark_reviewed` and
`recover_local_job` refuses it.

`recover_local_job` uses that token and the observed revision for explicit refresh,
resume/download, known model-job cancellation, interrupted-download reconciliation
or pending import-receipt retry. `cancel` appears in `actions` only after a refresh
in the current context reported a running `inference` job, the only type Scenario
documents as cancellable; for any other job it is refused without a request and
the job keeps running, matching the native Jobs panel. A running workflow job
cannot be canceled: `cancel` returns that explanation and sends nothing, while an
unsent prepared one keeps `cancel_prepared_job`. Resuming a restarted job does not import it into
the current scene. Shared `wait_for_job` waits without blocking Blender's main
thread and returns when delivery finishes, needs review, is held by disabled
Online Access, or reaches its timeout.
See [job contexts](BLENDER_JOB_CONTEXT.md) for lifetime and remaining integration.

Shared `job_status` and `wait_for_job` results add the native Jobs projection as
flat fields. `lane` is the model lane that submitted the job in this Blender
session, or `null` for workflow, Film, restarted and recovered jobs; it never
selects a destination. While a known remote job is active, `remote_status` is its
latest validated Scenario status, `progress` its reported fraction from 0 to 1
(`null` when missing or invalid), `remote_observed_at` the reading's UTC time and
`remote_stale` whether automatic polling has stopped keeping it current. They are
`null` otherwise and are never persisted. Only `in-progress` and `finalizing`
fractions measure generation; the native Jobs views show a percentage only for
those statuses from 1%, and name the status in the words of the composer's job
strip: queued, starting, generating or finalizing. `remote_status` and `progress`
stay the raw validated values. Providers may keep `progress` at 0 until
completion, so it is not a time estimate. A restarted job reports progress only after explicit
`recover_local_job` `refresh` or `resume`; see
[the projection rules](BLENDER_JOB_CONTEXT.md#remote-progress-and-scene-lane-binding).
For a `failed` job, `error` is the reason Scenario gave, read from the same
retrieval that observed the failure: its `metadata.error`, then its
`metadata.hint`, each shown as one line when it is plain text and otherwise
replaced by "Scenario reported more details that are not shown here."; when neither is plain text, `error` is "Scenario reported a failure without a readable reason."
It is at most 400 characters, with its support error id (`error_...`) kept from
plain text. It is also never persisted, so a job that failed
before a restart reports `null`. `list_local_jobs` reads only saved records and
has no error field; see [failure reasons](BLENDER_JOB_CONTEXT.md#failure-reasons).

`delivery_active` is `true` while this Blender session still advances the job by
itself: submission, polling, download or an Image lane's automatic import. It is
`false` once delivery finishes, pauses for review (`delivery_paused`), is held by
disabled Online Access, or the job is only inspected after a restart or was
prepared but never queued. It is also `false` for a finished Prompt Spark,
Translate or Blockout job in `succeeded`: nothing downloads its text, which
`read_prompt_result`, `read_model_text` or `prepare_blockout_plan` read on
request. A `wait_for_job` result with `delivery_active` `true` means the wait
expired, including while an automatic import is pending in `ready` or
`applying`; call it again. Prepare no result application while it is `true`.

`delivery_offline` is `true` only while Blender's Online Access is disabled and
holds a step this session would otherwise take by itself: status polling in
`remote` or `cancel_requested`, or the result download of a finished model or
workflow job in `succeeded`. Blender sends nothing meanwhile, so
`delivery_active` is `false` and `wait_for_job` returns without waiting for its
timeout. Ask the user to allow Online Access instead of calling it again; the
job then resumes by itself, with no recovery action. It is `false` for settled
jobs, for paused or restarted jobs, which wait for an explicit recovery action
whatever Online Access allows, and for finished Prompt Spark, Translate and
Blockout jobs, which have nothing to download. A native Jobs row in `remote`,
`cancel_requested` or a pending download adds `Status paused while online access
is disabled` or `Download paused while online access is disabled`, also when it
is paused or restarted, because its explicit recovery needs Online Access too.

Workflow jobs add `awaiting_decision`, `pending_steps` and `decision_error`.
`awaiting_decision` is `true` while the last retrieval in this session showed a
waiting `user-approval` or `user-selection` step and no decision is being sent.
The job then does not advance by itself, so `delivery_active` is `false` and `wait_for_job` returns at once;
status refreshes every 15 seconds meanwhile. Each `pending_steps` entry has its
node ID and type, loop iteration, dependencies, up to 50 review asset IDs with
their total, a selection's frozen candidates (`items`: asset IDs or texts, per
`item_kind`) with `min` and `max`, the decisions that may be sent (`actions`),
`blocked`, `links_complete` and `review_id`. Signed URLs are never included.
`blocked` is `loop` when the selection feeds a ForEach loop, a nested workflow
or a node type the API reference does not list, which the approved price may
not cover; `unverified` when the links between the workflow's steps could not be
read and the workflow contains such a node, so the selection may feed it; and
`unsupported` for unreadable candidates or limits. These three leave only
`reject`, while `unidentified` leaves no decision. Step links come from the job
and, while a step waits, from the workflow's definition, read once per waiting
period; `links_complete` is `false` while some are unknown, so `depends_on` and
the review assets may be incomplete. Rejecting stops the
workflow and is the only workflow cancellation. `decision_error` explains the
last refused, unsent or uncertain decision; never repeat an uncertain one until
the step is shown again. The `review_step` action marks a waiting step; it is
not a `recover_local_job` action. Read the steps with `workflow_job_steps` and send
only the user's explicit choice with `decide_workflow_step`; see
[workflow step decisions](#workflow-step-decisions) and
[waiting workflow steps](BLENDER_JOB_CONTEXT.md#waiting-workflow-steps).

To import recovered PNG/EXR results, call `prepare_result_application` with the
current context, request and revision, then show its destination and image list
to the user. After approval, `apply_result_application` consumes the returned
handle once. Scene/file changes invalidate it. Verification and the durable
application claim precede image import; an interrupted import is never retried
automatically. These commands spend no credits and make no service requests.

For one saved video/audio asset, pass its `asset_id` to the same preparation
command. Show the returned scene, frame, kind and local-file requirement before
approval. Confirmation inserts one MP4/WebM movie or MP3/WAV/OGG sound strip on
an unused channel; it preserves scene timing and omits embedded video audio.
Changed frames also invalidate this approval. One selected asset consumes the
job's application claim; receipt-only recovery never inserts another strip.
The independent media file must remain available to the blend file. See
[saved media application](BLENDER_JOB_CONTEXT.md#explicit-saved-video-and-audio-application)
for size, rollback and persistence limits.

For a saved model, pass the `key` of one importable entry in `job_status`
`import_units` as `asset_id`. The approval returns `kind: model`, the `format`
(glb, gltf, fbx, obj, ply, spz or splat), whether it is the `primary` result or an alternate,
its `members` with their roles and media types, glTF candidate `resources` and the
approved `cursor` instead of a media frame. Show the scene, cursor, format and
files before approval. Every member is rehashed into a private copy before the
durable claim; a changed or unsupported package leaves the saved job unchanged
(ready, or applied for a reuse) with an error that starts with
`Nothing was imported`. The importer adds one new model group, packs textures
from the package and leaves current selection intact. The cursor
is rechecked before the claim, and a reuse claim lists every member. GLB and glTF
rigs, weights and node/morph clips stay in the new group with the current scene
FPS. FBX clips keep the file's frame rate and `model_import.frame_rate_mismatch`
reports a different scene FPS; `model_import.dropped_references` counts OBJ and MTL
references left out. External-file and pointer-animation results remain
unsupported. See [model import](MESH_APPLICATION.md#explicit-saved-glb-import) and
[package import](MESH_APPLICATION.md#saved-model-package-import) for bounds and
recovery. This never replaces an existing mesh.

SPZ, `.splat` and PLY units also return `splat_options` with the approved
`max_points` and `axes` (null for mesh formats). Pass `splat_max_points`
(250000, 500000, 1000000 or 2000000) and `splat_axes` (`OPENGL` or `OPENCV`) to
choose them; the defaults are 1,000,000 points and the file format's convention,
`OPENGL` for SPZ and `OPENCV` for PLY and `.splat`, which providers may not
follow. To change an option, prepare again and show the new approval. Application
first reads the file on a worker in the single local-operation slot: a decoder
rejection, such as an SPZ v4 file, or a changed file stops with
`Nothing was imported` before any claim. While a local render or Film media
inspection holds that slot, SPZ and `.splat` imports are refused before anything
is queued, and a PLY is only verified: a mesh imports and splat data stops the
same way.
Splats become a new coloured point cloud, not a Gaussian splat render, and a PLY
without splat data continues as a mesh. `model_import.splat` then reports
`points_total`, `points_kept`, `step`, `axes` and `spz_extensions`; when
`spz_extensions` is true the file may store other axes, so check the result.
See [saved Gaussian splat import](MESH_APPLICATION.md#saved-gaussian-splat-import).

For one unapplied saved panorama, pass `purpose: world` and its `asset_id` to
`prepare_result_application`. A result whose saved metadata declares a texture
role is a material map and is refused. So is an image whose downloaded file header
declares a size that is not 2:1 within the World pixel limit, such as a square or
16:9 image, with `This saved image is not a 2:1 panorama within the World pixel
limit`. `job_status` omits `apply_world` when no supported image remains, the same
rule that decides the native **Set panorama as World (N)** buttons. Blender reads
each header in the background after a download or a saved-job inspection; until
it has, or when the header declares no size, the image stays offered and the
apply command's own check decides. Show the scene, current World and returned `format`
before approval. `format` states the saved media type's declared format, such as
`JPEG (LDR)` or an OpenEXR whose ACES AP0 primaries use ACES2065-1; restoration
returns `null`. The same apply command verifies and packs a supported 2:1 PNG,
JPEG or scanline OpenEXR as an explicitly chosen equirectangular environment.
The actual container must match the saved media type. It preserves the previous
World. This is not a seamless-content or actual HDR-range guarantee.

After a completed World assignment, `purpose: restore_world` prepares a separate
session-local restore without an asset ID. Show that operation before consuming
its handle. Edited owned World/image data or a changed destination prevents
restoration. The completed job stays `applied` and cannot be replayed. Existing
automatically imported Image jobs also stay ineligible for another claim. See
[World approval and limits](WORLD_APPLICATION.md#saved-result-ui-and-mcp-approval).

For one unapplied saved texture set, `purpose: material` captures the active mesh,
its active material slot and unambiguous stored map roles. Show that destination
and map list before consuming the shared application approval. It packs new
images, creates a material and changes only that slot. It does not reuse a new
selection after confirmation. See [material application](MATERIAL_APPLICATION.md)
for single-user/UV restrictions, supported maps, rollback and no-global-undo limits.

## Typed local references

`upload_reference` accepts a chosen local file and an optional `kind`: `image`
(the default), `audio`, `video` or `3d`. The kind must match the filename extension;
an omitted kind never infers a different media type. For example, a WAV input
uses `{"path": "/chosen/reference.wav", "kind": "audio"}`. See the
[format policy and limits](SDK_UPLOADS.md#active-reference-uploads). The file must be
one the extension saved in its own folders or one the user chose in Blender this
session; any other path is refused before it is read
([agent file uploads](#agent-file-uploads)).

Poll `reference_upload_status` until `imported` before using the returned asset ID
in a model or workflow estimate. Status, `list_reference_uploads` and recovery return saved
`kind` and `content_type` metadata; initial staging can report null metadata.
Only the selected file is sent: no conversion, external-buffer or texture-sidecar
discovery. Upload approval does not approve generation, and a lost response must
be reconciled using saved progress instead of starting another upload. The same uploads and saved-reference recovery are available in generation
forms and loaded Studio workflow forms for image, audio, video and 3D inputs. `capture_reference` also supports explicit `VIEWPORT_CLIP`, `CAMERA_CLIP` and
`MESH` snapshots. Clips use the preview/scene range at 1280x720 without audio,
trimming or padding; mesh export produces one GLB from the selected meshes.
Only mesh export works in background mode. `render_form` uses the native render
form and the same upload session for explicit scene/first-frame slots. Its
`prepare` action requires a GUI for scene captures; first-frame file uploads also
work headlessly. Result-specific application remains separate.

## Token lifecycle

GUI startup generates a bearer token the first time the server starts in a
Blender session. The extension holds it in memory and does not save it to disk.
The panel masks it; Copy buttons include the full token in setup snippets.
Restarting Blender changes a generated token, so copy the setup again. A token
explicitly supplied to the headless command remains the caller's responsibility.

The local token is different from your Scenario API key and secret. Client
configuration, shell history, clipboard managers and process listings may retain
copied tokens; keep them out of source control, screenshots and shared logs.

## Client setup

Use the panel's Copy buttons for the actual port and platform paths. Examples
below use placeholders, never real credentials. These clients must run where
`127.0.0.1` reaches the same machine as Blender; a hosted agent cannot reach your
computer's loopback address directly.

### Claude Code

```sh
claude mcp add --transport http scenario-blender http://127.0.0.1:9876/mcp \
  --header "Authorization: Bearer <token>"
```

### Cursor

Add the copied entry to your client MCP configuration:

```json
{
  "mcpServers": {
    "scenario-blender": {
      "url": "http://127.0.0.1:9876/mcp",
      "headers": {"Authorization": "Bearer <token>"}
    }
  }
}
```

### Claude Desktop

The stdio bridge forwards requests to the running Blender server. Copy the
installed shim path from the panel and verify that the command points to an
available Python 3 interpreter. The current panel can fall back to `python3`
on Windows instead of discovering Blender's bundled `python.exe`; select an
absolute working interpreter path there. Replace both path placeholders:

```json
{
  "mcpServers": {
    "scenario-blender": {
      "command": "<absolute path to Blender Python>",
      "args": [
        "<installed extension>/mcp/stdio_shim.py",
        "--url", "http://127.0.0.1:9876/mcp",
        "--token", "<token>"
      ]
    }
  }
}
```

The bridge token is a process argument. It does not start Blender or grant
Scenario API access by itself.

### Codex

The installed CLI supports `--url` and `--bearer-token-env-var`. Export the token
in the environment that launches Codex, then add the server:

```sh
export SCENARIO_BLENDER_TOKEN='<token>'
codex mcp add scenario-blender --url http://127.0.0.1:9876/mcp \
  --bearer-token-env-var SCENARIO_BLENDER_TOKEN
```

The equivalent `config.toml` entry is:

```toml
[mcp_servers.scenario-blender]
url = "http://127.0.0.1:9876/mcp"
bearer_token_env_var = "SCENARIO_BLENDER_TOKEN"
```

Restart a client launched before the environment variable was set. See the
[official Codex MCP configuration](https://learn.chatgpt.com/docs/extend/mcp?surface=cli)
for configuration and token-environment behavior.

### Connection check

This request lists tool metadata; it does not call a generation tool:

```sh
curl -sS -X POST http://127.0.0.1:9876/mcp \
  -H "Authorization: Bearer <token>" \
  -H "Content-Type: application/json" \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/list"}'
```

### Headless Blender

With the extension enabled, use the registered underscore command:

```sh
export SCENARIO_BLENDER_TOKEN='<token>'
blender --background scene.blend --command scenario_blender --port 9876
```

Blender must allow online access; `--online-mode` explicitly enables it for the
invocation. `--token '<token>'` overrides the environment but exposes the token
in process arguments. Supplied tokens are hidden in the startup banner. If
neither source is present, the command generates a token and displays it once
in that banner. Stop with Ctrl+C. Scene captures need the GUI; other scene tools
use the headless main-thread request loop. Development builds and tests use the
[isolated native runner](../CONTRIBUTING.md#native-blender-test-loop).

## Tools

The table is generated from the same definitions served by `tools/list`. That
response includes full argument guidance, examples and returned keys. The job
tools require `job_id` or the compatibility alias `id`; either accepts a
`local_id` from generation or a known Scenario job ID. A nonempty `job_id` wins
when both are supplied.

<!-- tools:start -->
Generated by `tools/gen_mcp_docs.py` from `scenario/mcp/tools_*.py`.
Do not edit this block by hand; run `make mcp-docs`. An asterisk marks a required argument.

### Assistant image tools (local, no generation provider required)

| Tool | Description | Arguments | Notes |
| --- | --- | --- | --- |
| `agent_workspace` | Get the local image inbox for Codex ImageGen or another assistant's generated assets. No Scenario account, API key, ComfyUI or provider connection is required. Copy a generated PNG/JPEG/WebP into the returned inbox, then use import_agent_image. Use the assistant's own image-generation tool; Blender cannot invoke it directly. | none | read-only annotation |
| `import_agent_image` | Import an assistant-generated image from the agent_workspace inbox. IMAGE loads packed pixels; REFERENCE creates a viewport reference at the 3D cursor; TEXTURE applies a new base-color material to the named mesh's active slot, requiring UVs. This does not generate a full PBR material. Existing other-object materials are preserved. Packed pixels survive inbox cleanup. Repeating the same image and destination reuses it. No external requests, provider credentials or arbitrary Python are needed. | `filename`*: string; Image basename inside the assistant inbox<br>`mode`: string (['IMAGE', 'REFERENCE', 'TEXTURE'])<br>`object_name`: string; Required for TEXTURE<br>`sha256`: string; Optional digest to verify the exact generated image<br>`display_size`: number | - |

### Scene tools (run on Blender's main thread)

| Tool | Description | Arguments | Notes |
| --- | --- | --- | --- |
| `scene_summary` | Inspect the open Blender scene before choosing a scene operation. | none | read-only annotation |
| `object_detail` | Inspect one named Blender object's transform, geometry summary and custom properties. | `name`*: string | read-only annotation |
| `execute_python` | Run arbitrary Python with bpy on Blender's main thread, only when explicitly enabled in preferences. | `code`*: string; Python source. bpy and result = {} are preloaded. | destructive annotation; Python preference gate; off by default |
| `select_objects` | Replace the selection in the current view layer with the named Blender objects. | `names`*: array | - |
| `set_frame` | Move Blender's timeline to a frame and evaluate the scene there. | `frame`*: integer | - |
| `screenshot_viewport` | Capture the visible 3D viewport area as a PNG image, including its UI overlays. | none | read-only annotation; GUI required |
| `camera_path` | Build an animated scene camera from a preset, description or explicit waypoints for Render Video. | `preset`: string<br>`duration`: number; seconds<br>`focal`: number; mm<br>`aim_at_subject`: boolean<br>`description`: string; e.g. 'slow orbit, 8 s, 35mm'<br>`waypoints`: array | - |
| `render_still` | Capture a quick OpenGL camera or viewport still as a PNG at the requested size. | `source`: string (['CAMERA', 'VIEWPORT'])<br>`width`: integer<br>`height`: integer | read-only annotation; GUI required |
| `blender_api_help` | Inspect Blender's running Python API before writing a script. | `path`*: string; e.g. bpy.ops.mesh.primitive_cube_add, bpy.types.Object, bpy.data.objects | read-only annotation |
| `datablocks_summary` | Summarize the datablocks in the open Blender file. | none | read-only annotation |

### Scenario tools

| Tool | Description | Arguments | Notes |
| --- | --- | --- | --- |
| `list_assets` | Read one asset-library page using the selected credentials and optional project scope. | `public`: boolean<br>`page_size`: integer<br>`pagination_token`: string<br>`collection_id`: string | read-only annotation |
| `search_assets` | Search asset-library metadata through the shared SDK session. | `query`*: string<br>`public`: boolean<br>`limit`: integer<br>`offset`: integer | read-only annotation |
| `list_collections` | Read one page of Scenario collections in Blender's selected credential and optional project scope. | `page_size`: integer<br>`pagination_token`: string | read-only annotation |
| `prepare_asset_organization` | Read the current tags and collection memberships of library assets and prepare one reviewed collection or tag change, without sending it. | `operation`*: string (['add_to_collection', 'remove_from_collection', 'update_tags', 'create_collection'])<br>`asset_ids`: array<br>`collection_id`: string<br>`collection_name`: string<br>`add_tags`: array<br>`remove_tags`: array | read-only annotation |
| `apply_asset_organization` | Apply one READY organization review once, after the user explicitly approved its shown change. | `context_id`*: string<br>`review_id`*: string | destructive annotation |
| `asset_organization_status` | Inspect an organization review, including an apply whose call timed out, or discard a review that is not applying. | `context_id`*: string<br>`review_id`*: string<br>`action`: string (['status', 'discard']) | read-only annotation |
| `list_workflows` | List workflows in the selected credential/project scope without spending. | `privacy`: string (['private', 'public'])<br>`query`: string<br>`offset`: integer<br>`limit`: integer | read-only annotation |
| `workflow_schema` | Read a workflow's declared input definitions without spending. | `workflow_id`*: string | read-only annotation |
| `estimate_workflow` | Request a free exact workflow price bound to the selected scene and connection. | `workflow_id`*: string<br>`parameters`: object | - |
| `run_workflow` | Approve one unchanged workflow estimate and persist its identity before paid dispatch. | `workflow_id`*: string<br>`parameters`: object<br>`quote_id`*: string<br>`approved_cost`*: string | - |
| `discard_workflow_estimate` | Discard one ready unsubmitted workflow approval without changing saved jobs. | `quote_id`*: string | - |
| `workflow_job_steps` | Read a saved workflow job's waiting approval and selection steps again so the user can review them; decides nothing. | `context_id`*: string<br>`request_id`*: string | read-only annotation |
| `decide_workflow_step` | Send one decision the user explicitly chose for a waiting workflow step: approve, select items or reject. | `context_id`*: string<br>`request_id`*: string<br>`expected_revision`*: integer<br>`node_id`*: string<br>`review_id`*: string<br>`action`*: string (['approve', 'select', 'reject'])<br>`selected_indices`: array | destructive annotation |
| `prepare_film_composition` | Inspect saved Film media and prepare a final or previs composition without spending. | `context_id`*: string<br>`production_id`*: string<br>`mode`: string (['final', 'previs'])<br>`score_task_id`: string | - |
| `film_composition_review` | Inspect, cancel or discard a session-local Film composition review. | `review_id`*: string<br>`action`: string (['status', 'cancel', 'discard']) | - |
| `estimate_film_composition` | Request the exact server price for one READY verified Film composition. | `review_id`*: string | read-only annotation |
| `generate_film_composition` | Approve one exact Film composition price and save its master identity before a single submission. | `review_id`*: string<br>`approved_cost`*: string | - |
| `prepare_film_review` | Copy and measure saved Film media for a final or previs review scene without building or spending. | `context_id`*: string<br>`production_id`*: string<br>`mode`: string (['final', 'previs'])<br>`score_task_id`: string<br>`include_master`: boolean | - |
| `film_review_status` | Inspect, cancel or discard a session-local Film review, or retry only its known saved receipt. | `review_id`*: string<br>`action`: string (['status', 'cancel', 'discard', 'retry_receipt', 'dismiss_uncertain'])<br>`inspected`: boolean | - |
| `build_film_review` | Approve one READY Film review and build a new review scene from its private copies. | `review_id`*: string | - |
| `film_export_sources` | Inspect the built Film review scenes of the current recipe that can be exported as video. | `production_id`*: string | read-only annotation |
| `prepare_film_export` | Bind one built Film review scene and a new MP4 destination for a separate render approval. | `context_id`*: string<br>`production_id`*: string<br>`source_id`*: string<br>`destination_path`*: string<br>`width`: integer<br>`height`: integer<br>`include_audio`: boolean | - |
| `render_film_export` | Approve one READY Film export and render it to its destination in an offline Blender process. | `review_id`*: string<br>`approved_destination`*: string | - |
| `film_export_review` | Inspect, cancel or discard a session-local Film export, or save its verified video to another new file after saving failed. | `review_id`*: string<br>`action`: string (['status', 'cancel', 'discard', 'prepare_republish', 'republish'])<br>`destination_path`: string<br>`approved_destination`: string | - |
| `film_case_study_sources` | Inspect the built Film review scenes and director timelines of the current recipe that can form a portable case-study bundle. | `production_id`*: string | read-only annotation |
| `prepare_film_case_study` | Bind one built Film review scene, an optional director timeline and a new ZIP destination for a separate build approval. | `context_id`*: string<br>`production_id`*: string<br>`source_id`*: string<br>`timeline_id`: string<br>`destination_path`*: string | - |
| `export_film_case_study` | Approve one READY Film case study and build its portable ZIP bundle in offline Blender processes. | `review_id`*: string<br>`approved_destination`*: string | - |
| `film_case_study_review` | Inspect, cancel or discard a session-local Film case study, or save its verified archive to another new file after saving failed. | `review_id`*: string<br>`action`: string (['status', 'cancel', 'discard', 'prepare_republish', 'republish'])<br>`destination_path`: string<br>`approved_destination`: string | - |
| `film_capture_sources` | Inspect matching local scenes for one Film shot before capture. | `production_id`*: string<br>`shot_id`*: string | read-only annotation |
| `prepare_film_capture` | Prepare a local shot capture for separate render approval. | `context_id`*: string<br>`production_id`*: string<br>`shot_id`*: string<br>`source_id`*: string<br>`kind`: string (['STILL', 'VIDEO'])<br>`width`: integer<br>`height`: integer<br>`color_type`: string (['MATERIAL', 'TEXTURE', 'OBJECT']) | - |
| `render_film_capture` | Approve a READY Film capture and start one local render on the shared workers. | `review_id`*: string | - |
| `film_capture_review` | Inspect, cancel or discard an owner-local Film capture. | `review_id`*: string<br>`action`: string (['status', 'cancel', 'discard']) | - |
| `upload_film_capture` | Upload the reviewed bytes of one completed Film capture through the shared upload runtime. | `review_id`*: string | - |
| `film_timeline_sources` | Inspect matching local scenes for every shot in the current Film recipe. | `production_id`*: string | read-only annotation |
| `prepare_film_timeline` | Prepare explicit completed shot choices for separate timeline build approval. | `context_id`*: string<br>`production_id`*: string<br>`selections`*: object | - |
| `film_timeline_review` | Inspect or discard an owner-local Film timeline review. | `review_id`*: string<br>`action`: string (['status', 'discard'])<br>`inspected`: boolean | - |
| `build_film_timeline` | Approve one READY Film timeline review and create a new editable scene-strip sequence. | `review_id`*: string | - |
| `film_shot_sources` | Inspect eligible downloaded hero models for one shot in the current Film recipe. | `production_id`*: string<br>`shot_id`*: string | read-only annotation |
| `prepare_film_shot` | Verify explicit saved hero selections and prepare a shot for separate build approval. | `context_id`*: string<br>`production_id`*: string<br>`shot_id`*: string<br>`selections`*: object | - |
| `film_shot_review` | Inspect or discard a Film shot review, or retry only its known persistence receipts. | `review_id`*: string<br>`action`: string (['status', 'discard', 'retry_receipts', 'dismiss_uncertain'])<br>`inspected`: boolean | - |
| `build_film_shot` | Approve one READY Film review and build a new shot scene from its verified saved models. | `review_id`*: string | - |
| `film_recipe` | Inspect or load the current scene's Film recipe, or explicitly start a new production. | `action`: string (['inspect', 'load', 'new_production'])<br>`recipe`: object | - |
| `estimate_film_task` | Request the exact server price for one model task in the loaded Film recipe. | `production_id`*: string<br>`task_id`*: string | read-only annotation |
| `approve_film_task` | Approve one unchanged Film estimate and save its identity before one paid submission. | `quote_id`*: string<br>`approved_cost`*: string | - |
| `discard_film_estimate` | Release one unsubmitted Film estimate so its task can be repriced. | `quote_id`*: string | - |
| `bind_film_upload` | Associate one imported saved upload with a Film upload task without sending bytes. | `production_id`*: string<br>`task_id`*: string<br>`context_id`*: string<br>`request_id`*: string<br>`expected_revision`*: integer | - |
| `upload_reference` | Upload an explicitly chosen image, audio, video or 3D file through the shared durable upload session. | `path`*: string<br>`kind`: string (['image', 'audio', 'video', '3d']) | - |
| `reference_upload_status` | Read a reference upload's progress while the shared session advances its already authorized work. | `context_id`*: string<br>`reference_id`*: string | read-only annotation |
| `list_reference_uploads` | Inspect saved uploads under the selected credential scope, including after restart. | none | read-only annotation |
| `recover_reference_upload` | Explicitly inspect a known remote upload, cancel unclaimed preparation, restart an unfinished upload, or clean its finished private source copy. | `context_id`*: string<br>`request_id`*: string<br>`expected_revision`*: integer<br>`action`*: string (['refresh', 'cancel_prepared', 'restart', 'cleanup']) | destructive annotation |
| `prepare_result_application` | Prepare explicit saved image/media/model import, material assignment, panorama World replacement, session-local World restoration, or a Render Video first frame from a saved image. | `context_id`*: string<br>`request_id`*: string<br>`expected_revision`*: integer<br>`asset_id`: string<br>`purpose`: string (['import', 'material', 'world', 'restore_world', 'mesh_edit', 'mesh_source', 'video_first_frame'])<br>`mesh_policy`: string (['REMESH', 'UV', 'RETEXTURE', 'PARTS', 'RIG'])<br>`mesh_placement`: string (['WORLD', 'LOCAL'])<br>`keep_original`: boolean<br>`splat_max_points`: integer ([250000, 500000, 1000000, 2000000])<br>`splat_axes`: string (['OPENGL', 'OPENCV']) | read-only annotation |
| `apply_result_application` | Apply or restore saved results after the user approves the prepared destination and operation. | `context_id`*: string<br>`application_id`*: string | destructive annotation |
| `recover_local_job` | Explicitly recover a saved job without repeating generation or importing into another scene. | `context_id`*: string<br>`request_id`*: string<br>`expected_revision`*: integer<br>`action`*: string (['refresh', 'resume', 'cancel', 'recover_download', 'retry_receipt']) | destructive annotation |
| `list_local_jobs` | Inspect durable local jobs for the selected API-key pair without network requests. | none | read-only annotation |
| `cancel_prepared_job` | Cancel an unsubmitted durable local intent without contacting Scenario. | `context_id`*: string<br>`request_id`*: string<br>`expected_revision`*: integer | destructive annotation |
| `estimate_blockout` | Get the exact free estimate for the current scene's Blockout description or refinement. This reads the native Blockout fields and current plan; it does not generate or build geometry. Approve the exact returned cu_cost_exact separately. Args: action DESIGN or REFINE. Returns: quote_id, action, cu_cost_exact. | `action`: string (['DESIGN', 'REFINE']) | read-only annotation |
| `approve_blockout` | Spend the explicitly approved exact cost once for estimate_blockout. Args: quote_id and approved_cost. Inputs, current plan and origin must still match. Returns: request_id and state. The unchanged source scene receives a complete plan; no geometry is built automatically. Inspect list_local_jobs after failure or restart; never repeat an uncertain submission. | `quote_id`*: string<br>`approved_cost`*: string | - |
| `prepare_blockout_plan` | Read and validate one saved Scenario LLM plan for the current scene without generating or building geometry. Args: context_id, request_id, expected_revision from list_local_jobs. Returns: review_id, state, scene, elements, groups, replaces_plan, error. Poll blockout_plan_status until ready, then review the destination and replacement before apply_blockout_plan. | `context_id`*: string<br>`request_id`*: string<br>`expected_revision`*: integer | read-only annotation |
| `blockout_plan_status` | Inspect a prepared saved-plan review or discard its finished approval handle. Args: context_id, review_id, optional discard. Returns: state, scene, elements, groups, replaces_plan, error; discarded state when requested. Never generates or changes a scene. | `context_id`*: string<br>`review_id`*: string<br>`discard`: boolean | read-only annotation |
| `apply_blockout_plan` | Use a ready saved-plan review once, after explicit destination and replacement approval. Args: context_id, review_id from prepare_blockout_plan. Returns: scene, elements, geometry_changed=false. Replaces only the unchanged destination scene stored Blockout plan; use native Build plan separately. Never spends or builds geometry. | `context_id`*: string<br>`review_id`*: string | destructive annotation |
| `read_model_text` | Read one explicitly selected complete text asset from a successful saved model job, including after restart. Obtain context_id and revision from list_local_jobs and asset_id from job_status results. Returns: request_id, asset_id and bounded full text. Never spends, parses a plan, applies to the scene or substitutes a truncated preview. Args: context_id, request_id, expected_revision, asset_id. | `context_id`*: string<br>`request_id`*: string<br>`expected_revision`*: integer<br>`asset_id`*: string | read-only annotation |
| `inspect_result_previews` | Inspect the previews of a downloaded saved job's results, as the native preview tiles and Waveform control show them, without generating, spending, applying or changing the scene or saved job. | `context_id`*: string<br>`request_id`*: string<br>`expected_revision`*: integer<br>`asset_id`: string<br>`envelope`: boolean<br>`action`: string (['inspect', 'retry', 'cancel'])<br>`bins`: integer<br>`wait`: number | read-only annotation |
| `estimate_prompt` | Get the exact server price for New, Rewrite or Translate on the current scene's native prompt field. This does not generate or change text. Args: lane and action (GENERATE, REWRITE, TRANSLATE). Returns: quote_id and cu_cost_exact. Obtain explicit approval of that exact cost before approve_prompt. The current field text/model must remain unchanged. | `lane`: string (enum: see tools/list)<br>`action`*: string (['GENERATE', 'REWRITE', 'TRANSLATE']) | read-only annotation |
| `approve_prompt` | Spend the explicitly approved exact price once for a quote from estimate_prompt. Args: quote_id and approved_cost (the unchanged decimal string). Returns: request_id and queued state. Queues one durable submission; never retry an uncertain outcome. The shared runtime updates only the unchanged original prompt field. Inspect list_local_jobs for recovery. | `quote_id`*: string<br>`approved_cost`*: string | destructive annotation |
| `read_prompt_result` | Read full text from a saved successful prompt or translation job without generating, spending or applying it. Args: context_id, request_id and expected_revision from list_local_jobs; refresh a known remote job's status first if necessary. Returns: prompts; old scene origins are readable but are never silently applied to the current scene. | `context_id`*: string<br>`request_id`*: string<br>`expected_revision`*: integer | read-only annotation |
| `list_models` | List the loaded lane catalog, with curated models first and at most 40 matches, or the trained LoRAs and compositions the native picker offers. | `lane`: string (enum: see tools/list)<br>`query`: string<br>`source`: string (['catalog', 'trained', 'all']) | read-only annotation |
| `model_schema` | Read the model's current form parameters for this Blender extension. | `model_id`*: string | read-only annotation |
| `model_defaults` | Read, save or clear the selected credentials and project's trained-model default of the Image or Render Image form, without spending. | `action`: string (['get', 'set', 'clear'])<br>`lane`: string (['image', 'render_image'])<br>`context_id`: string<br>`expected_revision`: integer<br>`base_model_id`: string<br>`picks`: array | - |
| `render_form` | Inspect or prepare the native Render Image/Video form without submitting generation. | `lane`*: string (['render_image', 'render_video'])<br>`action`: string (['inspect', 'configure', 'prepare', 'remove'])<br>`settings`: object<br>`role`: string (['scene', 'first_frame'])<br>`reference_key`: string | - |
| `estimate_cost` | Prepare a generation quote without submitting. Scenario returns its exact CU estimate; other providers may have no quote API. | `model_id`*: string<br>`parameters`: object<br>`lane`: string (enum: see tools/list) | read-only annotation |
| `generate` | Submit a generation using the selected provider's billing or compute. Every model lane uses durable shared jobs. | `lane`*: string (enum: see tools/list)<br>`quote_id`*: string<br>`approved_cost`*: string<br>`model_id`*: string<br>`parameters`: object; Model parameters; file parameters take Scenario asset ids | - |
| `job_status` | Read one local generation's status and cost without spending credits. Active model jobs advance through shared remote polling and verified downloads; restarted jobs remain inspection-only. | `job_id`: string; Scenario job id (job_...) or the local_id returned by generate<br>`id`: string; Same as job_id, kept for compatibility | read-only annotation |
| `wait_for_job` | Wait for a generation while Blender remains responsive. Shared jobs return when delivery finishes, pauses for review, a workflow waits for a decision, is held by disabled Online Access, or the wait expires. Restarted shared jobs remain inspection-only until explicitly resumed. Prototype records return a local snapshot immediately with scoped recovery guidance. | `job_id`: string; Scenario job id (job_...) or the local_id returned by generate<br>`id`: string; Same as job_id, kept for compatibility<br>`timeout`: number | read-only annotation |
| `recover_cloud_job` | Read one completed cloud model job into the selected credential-scoped saved jobs. | `job_id`*: string<br>`model_id`*: string | - |
| `import_result` | Reject direct cached-file import and explain the required saved-result approval flow. | `job_id`: string; Scenario job id (job_...) or the local_id returned by generate<br>`id`: string; Same as job_id, kept for compatibility | - |
| `capture_reference` | Capture a viewport/camera still or clip, or export selected meshes, and upload the snapshot as a Scenario reference asset. | `source`: string (['VIEWPORT', 'CAMERA', 'VIEWPORT_CLIP', 'CAMERA_CLIP', 'MESH']) | - |
| `list_generations` | List recent cloud generations using this Blender runtime's loaded history. | `limit`: integer<br>`older`: boolean<br>`refresh`: boolean | read-only annotation |
<!-- tools:end -->

### Unsaved scene changes

Blender asks to save before quitting only when the open file is marked modified,
and only an Undo History step marks it. A tool that changes the file records one
step once its change is complete: `select_objects`, `set_frame`, `camera_path`,
`execute_python`, `film_recipe` (`load` and `new_production`), `render_form`
(`configure`, `remove` and `prepare`), `apply_blockout_plan`, the three Film
`build_*` tools and World restoration through `apply_result_application`. Results,
plans, prompts and uploads that maintenance delivers later, such as automatically
imported Image results, are marked when they land.

Only an actual change is marked. A call refused before changing anything, such as
invalid arguments, a refused recipe or a refused build, leaves the file unmarked,
and so do an unchanged selection or frame, a reload of the same recipe and a build
that built nothing. A call that fails after changing part of the scene still marks
it. Reading tools, quotes, catalog loads, project defaults (including those
`model_defaults` saves or clears) and saved-job inspection leave the file
unmarked, and the step changes no data, so estimates and approvals stay current.
No tool saves the file: ask the user to save it. See
[marking the file modified](architecture/blender.md#marking-the-file-modified).

## Security model

The Blender integration binds only to `127.0.0.1`. MCP operations require the
bearer token, checked with a constant-time comparison. `/health` is unauthenticated
and reports availability, Blender version and server name/version. Native clients
may omit Origin; a supplied Origin must be an HTTP(S) loopback origin. Other
origins and browser CORS preflights are refused. Every request first passes the
[Host check](#request-boundaries). This is local access control,
not a promise that a token-holding program is safe.

Early POST and Origin rejections send the complete HTTP error with
`Connection: close`, then close the socket's write side before discarding a
bounded pending body. Only an unambiguous declared length within the existing
10 MiB body limit can be discarded, in chunks under a one-second total deadline.
Rejected bytes are never parsed or dispatched. This staged teardown reduces the
risk of a TCP reset hiding the error from clients still sending their body;
malformed, chunked or oversized requests close without reading their declared body.

**Allow connected agents to run Python** is off by default and fails closed if
preferences are unavailable. With it off, an authorized agent can still read
and change the scene, capture/upload a reference and request paid generation.
Require an estimate and explicit spending approval. If a generation call times
out, inspect job status; do not submit it again blindly. Client timeouts do not
prove that queued or remote work was canceled.

The server's own main-thread queue deadline has an explicit execution boundary.
A request that expires before its handler starts is removed from execution and
returns `the tool was not executed`; resuming Blender cannot run that old request.
Stopping the server also rejects new admission and releases queued callers
without executing their tools, including after a restart. A handler already
running is not interrupted: its timeout reports an unknown outcome and asks the
client to inspect the operation before retrying. This does not cancel remote
jobs or establish whether an HTTP client's independent timeout happened before
or after dispatch.

A call that omits an argument its input schema lists as required, or whose
arguments are not an object, is refused before the tool runs with
`ValueError: Missing required argument: <name>` (or the plural form); argument
values are still checked by each tool.
A failed tool call returns an `isError` result with one line, `Type: message`,
and no traceback. The message is returned only when the extension wrote it: a
validation error or refusal raised by the tool or by extension code it calls,
or one of the extension's own error classes. A failure raised by the operating
system, Blender or a library can name local paths or service details, so the
client receives `Type: <tool> failed unexpectedly; see the Blender console`
instead. The full traceback goes to the `scenario.mcp` console log, at error
level for unexpected failures and at debug level for the others.
`execute_python` errors keep a traceback of the agent's code but omit the
frames of the extension's own sandbox module, which would show its installed
path. Frames of code that the agent's code called, such as Blender's modules,
remain.

With Python enabled, a connected agent can run arbitrary Python with your user's
permissions. The blocklist in `scenario/mcp/sandbox.py` is a guard rail, not a
security boundary. The token can be copied into client configuration files and,
for the stdio bridge, command arguments. Do not expose the port through a public
proxy or share setup snippets.

Film video and case-study exports let an agent write new MP4 or ZIP files only
inside the configured Output Folder. The destination's folder is resolved, following links, and must lie
in the resolved Output Folder. Each part's spelling is compared exactly, although
Windows resolution returns an existing folder's on-disk spelling. Only the missing
default `Film` subfolder, with a missing Output Folder above it, can be created,
an existing file is never overwritten, and Blender, extension and staging storage
stay refused even when the Output Folder points there. Render and copy approvals
must repeat the exact resolved destination and recheck the Output Folder, so an
agent cannot approve a file chosen elsewhere in Blender's file browser. This is a
policy check before a long render, not a sandbox: with Python
execution enabled, a token holder can still write elsewhere. Report vulnerabilities through
[SECURITY.md](../SECURITY.md); see [privacy and local data](PRIVACY.md) for uploads,
credential storage and data handling.

### Request boundaries

Every request, `/health` included, must carry exactly one `Host` header naming
`127.0.0.1`, `localhost` or `[::1]`, with or without a port, in any letter case.
The server checks it before routing, authentication and the Origin check. A missing
or repeated `Host` returns 400; any other name returns 403 `forbidden host`, with the
same early-rejection teardown as an Origin refusal. A DNS-rebinding page reaches the
port under its own host name, which its browser sends as `Host`, so it can neither
read `/health` nor call `/mcp`, even without an `Origin` header. MCP clients, the
stdio bridge and `curl` send the host of the URL they were given; use the copied
`127.0.0.1` URL, because a URL naming this computer another way is refused.

Each connection has a 60-second idle timeout: a socket read or write that makes no
progress for 60 seconds closes the connection, so an idle keep-alive connection or
a stalled request cannot hold a server thread. A request waiting for Blender's
main thread, or in `wait_for_job`, reads nothing from its socket meanwhile, so a
tool that takes longer still answers within the tool's own limits (120 seconds,
170 for `wait_for_job`). The timeout bounds each read, not a whole request: a client
that sends a byte at least every 60 seconds keeps its thread, and only programs on
this computer can connect. The server refuses any bind address other than
`127.0.0.1`; the GUI start and the headless command both use it.

### Agent file uploads

`upload_reference` uploads a local file only when one of these holds:

- the extension produced it in its own user folders, the Scenario folder under
  Blender's extension user directory that holds saved results, previews, captures
  and staging; or
- the user chose it in Blender during the current Blender session: a reference file
  picked with a generation form's **Upload** button, or a file set in the Render
  Video first-frame field or a workflow input's **File** field, through the file
  browser or typed into the field.

Every other path, including the Output Folder, which can point anywhere, is refused
with `UploadRefused` before the file is opened, copied or sent. The path is resolved
first: `~` is expanded, a blend-relative `//` path is made absolute and every link is
followed. The result must be an existing regular file inside one of those folders,
or a file the user chose, compared by resolved path; the resolved file is what is
uploaded. A link inside the extension's folders that points outside is refused, and
a missing file receives the same refusal as a file outside the allowlist, so a
refusal does not reveal which files exist. `render_form` `prepare` with the
`first_frame` role applies the same rule, so a `first_frame_path` an agent set through
`configure` is refused until the user chooses that file.

Choices are recorded on Blender's main thread when one of those fields changes or
the **Upload** file browser confirms a file, outside a local MCP tool call: a value an agent
writes while its tool runs is never a choice. They are kept in memory by resolved
path, up to the 256 most recent, and forgotten when Blender quits or the extension
is disabled; a path saved in a blend file in an earlier session must be chosen again.
This policy does not apply to the user's own uploads from forms and does not bind
Python execution: with **Allow connected agents to run Python** on, an agent can
read and send files with your permissions.

### Default start and upload decisions

Agent uploads use the allowlist above rather than a native confirmation that
names each file: the extension uploads its own captures directly, so
`upload_reference` mostly serves files the user chose. Local MCP starts
automatically in GUI sessions when **Allow Online Access** is on, protected by its
token and the Origin, Host, idle-timeout and loopback-bind checks above. **Stop MCP
server** in **Scenario > Agents (MCP)** closes it for the rest of the session. The
extension's network permission text and [PRIVACY.md](PRIVACY.md#the-local-mcp-server)
disclose the loopback listener. The headless `scenario_blender` command stays an
explicit start.

## Local server and mcp.scenario.com

Open Studio adds `agent_workspace` and `import_agent_image` for local handoff
from Codex ImageGen or another assistant. These require neither a generation
provider nor arbitrary Python execution. The assistant copies an image into the
returned inbox, then imports it as packed image data, a reference, or a base-color
texture on a named mesh with UVs. They have no hosted Scenario equivalent.

Local names retain their verb-first form. The platform uses names such as
`models_list` and `job_get`; similar names do not make their parameters or scene
behavior interchangeable. Remote names follow the
[hosted tool reference](https://mcp.scenario.com/docs/tools).

| Concept | scenario-blender | Hosted platform |
| --- | --- | --- |
| Choose models | `list_models(lane, query)` | `models_list`, `search`, `recommend` |
| Model parameters | `model_schema(model_id)` | `model_schema_get` |
| Trained LoRAs, compositions and project defaults | `list_models(lane, source=trained)`, base LoRA inputs in `estimate_cost`/`generate` or `render_form` `trained`, `model_defaults(action, lane)` | `models_list` lists models; no hosted equivalent of the local per-project defaults is asserted here |
| Price without generating | `estimate_cost(model_id, parameters)` | `model_run` with `dry_run=true` |
| Generate | `generate(lane, quote_id, approved_cost, model_id, parameters)`; only Image results import automatically | `model_run` |
| Job status | `job_status(job_id or id)` | `job_get` |
| Durable local recovery | `list_local_jobs`, `cancel_prepared_job` | Local only; no remote polling or cancellation |
| Saved job actions | `recover_local_job(context_id, request_id, expected_revision, action)` | Scoped job refresh/cancellation, asset retrieval, or local receipt recovery; never a new generation |
| Review saved Image import | `prepare_result_application(context_id, request_id, expected_revision)` | Local destination capture; show the returned scene and images for approval |
| Review one saved video/audio strip | `prepare_result_application(context_id, request_id, expected_revision, asset_id)` | Local scene/frame capture; show returned destination for approval |
| Review one saved model | `prepare_result_application(context_id, request_id, expected_revision, asset_id)` | Pass an `import_units` key; local scene/cursor capture; show the returned format, files and destination for approval |
| Review panorama World or restore | `prepare_result_application(context_id, request_id, expected_revision, purpose, asset_id)` | Use `world` with one PNG/JPEG/OpenEXR asset and show its `format`, or `restore_world` for the retained session handle |
| Apply approved saved results | `apply_result_application(context_id, application_id)` | Local verified import or strip insertion into the captured destination; no platform call |
| Wait | `wait_for_job(job_id or id, timeout)`, one job without blocking Blender | `jobs_wait` |
| Reference upload | `upload_reference(path, kind)` or `capture_reference(source)`, then `reference_upload_status(context_id, reference_id)` until imported | `upload_asset`, `upload_asset_complete` for an existing file |
| Saved upload recovery | `list_reference_uploads`, then `recover_reference_upload(context_id, request_id, expected_revision, action)` | Known-upload status retrieval; local cancellation/cleanup have no platform equivalent |
| History | `list_generations(limit)`, then `list_generations(older)` for older pages | `jobs_list` |
| Recover a cloud result | `recover_cloud_job`, then explicit saved-result approval | No Blender scene access |
| Prompt assistance | `estimate_prompt(lane, action)` then `approve_prompt(quote_id, approved_cost)`; same native prompt field and exact-price approval | `prompt_spark` for generation; SDK `generate.translate` for English translation |
| Saved prompt text | `read_prompt_result(context_id, request_id, expected_revision)` | `job_get` and `asset_get`; read-only recovery without Blender mutation |
| Saved result previews | `inspect_result_previews(context_id, request_id, expected_revision, asset_id)`, read-only states, still metadata and waveform envelopes | `asset_get` and `asset_display` for Scenario's preview images; local stills and envelopes have no platform equivalent |
| Inspect scene | `scene_summary`, `object_detail`, `datablocks_summary`, `blender_api_help` | Local only |
| Change scene | `select_objects`, `set_frame`, `camera_path`, `execute_python` | Local only |
| Capture scene | `screenshot_viewport`, `render_still` | Local only |
| Workflow discovery | `list_workflows`, `workflow_schema` | `workflows_list`, `workflow_get` |
| Asset library metadata | `list_assets`, `search_assets` | SDK `assets.list` and `search.asset_search`; no hosted tool-name equivalence asserted here |
| Collections | `list_collections(page_size, pagination_token)` | `collections_list` |
| Organize library assets | `prepare_asset_organization(operation, asset_ids, collection_id or collection_name, add_tags, remove_tags)`, then `apply_asset_organization(context_id, review_id)` after explicit approval | `collection_add_assets`, `collection_remove_assets`, `collection_create`, `asset_add_tags`, `asset_remove_tags`; the local review reads current state first and verifies by reading back. Both skip assets already in the collection; the hosted `collection_add_assets` also batches beyond 49 (undocumented), while a local review takes at most 49, the API's per-request cap |
| Organization outcome or discard | `asset_organization_status(context_id, review_id, action)` | Local session review; `collection_get` and `asset_get` read service state |
| Workflow price and execution | `estimate_workflow`, `run_workflow` | `workflow_run` |
| Discard workflow approval | `discard_workflow_estimate` | Local only; no remote cancellation |
| Waiting workflow steps | `workflow_job_steps(context_id, request_id)`, then `decide_workflow_step(context_id, request_id, expected_revision, node_id, review_id, action, selected_indices)` with the user's explicit choice | `job_get`; `workflow_approve` and `workflow_reject` for approval steps; no hosted selection tool is listed |
| Collection rename/delete, model collections, tag listing, training, workflow authoring and usage | Use the hosted server | Discover operations in the hosted tool reference |

Connect both when needed: use the local setup above for Blender and the
[hosted server setup](https://mcp.scenario.com/docs) for Scenario-wide work.
Some platform operations are discovered through its tool catalog.

`capture_reference` returns `context_id` and `reference_id`, not an asset ID or a
local capture path. Both upload tools return while the shared session stages and
transfers the prepared reference. Poll `reference_upload_status`
until `state` is `imported`, then use `asset_id` in a fresh `estimate_cost` call.
The generation still requires its own exact-cost approval. An `upload_reference`
file keeps uploading while the scene is edited, played or rendered; a
`capture_reference` snapshot stops if the scene changes before it finishes. Undo,
redo, removing the scene or loading a file stops either. Do not repeat an
uncertain upload: inspect saved progress and explicitly refresh its known ID.
After restart, use `list_reference_uploads`; session handles do not survive.
Recovery can refresh, cancel unclaimed preparation, restart an unfinished upload
as a new request, or clean a finished private source copy; it never resends a
part to the same upload or deletes the original file.
See [upload limits and destination policy](SDK_UPLOADS.md#active-reference-uploads).

`wait_for_job` accepts a finite timeout from 0 to 170 seconds (default 170).
A zero timeout reads status immediately. Shared waits observe their scoped session
on the HTTP thread while main-thread maintenance performs delivery. Server stop
interrupts waiting; a changed session rejects the old completion. Waiting does
not approve generation, cancellation or scene application.

Prototype registry records have no automatic service engine. Their waits return
the saved local snapshot immediately, including nonterminal records, with
`recover_cloud_job` guidance. Their saved status is not a fresh remote status.
No credentials, service requests or registry writes are needed for cold reads.

## Troubleshooting

- **401:** the token is missing or differs from the server's current token. Copy
  the setup again; check the actual port and the client's launch environment.
- **403 `forbidden host` or 400:** the client named the server other than
  `127.0.0.1`, `localhost` or `[::1]`, or sent no `Host`; use the copied URL. See
  [request boundaries](#request-boundaries).
- **`upload_reference` refused:** choose the file in Blender (a form's **Upload**
  button, the first-frame field or a workflow input's **File** field) and call it
  again, or capture the scene with `capture_reference`; see
  [agent file uploads](#agent-file-uploads).
- **No free port from 9876:** all four candidate ports are busy. Choose a different
  MCP Port and copy the newly displayed URL.
- **Allow Online Access is off:** enable it in Blender's System preferences before
  starting the server; this permission also allows Scenario network operations.
- **Capture fails in background mode:** open a GUI session with a 3D viewport.
- **Catalog is still loading:** retry after loading completes and inspect the
  extension's status for credential or service errors. This is separate from the
  local MCP bearer token.
- **Model description is still loading:** the read took longer than the
  [30-second wait](#model-quote-and-submission-contract); nothing was quoted,
  submitted or changed. Call again; the call joins the same read.
- **A tool reports `failed unexpectedly`:** open Blender's system console; the
  `scenario.mcp` error log has the traceback. Set Log Level to Debug in Scenario
  Preferences to also log tracebacks of ordinary validation failures.
- **Call times out:** a modal dialog or long-running main-thread tool can delay
  other requests. Check status before repeating any action that may spend credits
  or modify the scene. `wait_for_job` leaves the main thread available while
  waiting, but its preparation and final status delivery still require that thread.

Maintainers: run `make mcp-docs` after changing tool definitions and
`uv run --locked --no-env-file python tools/gen_mcp_docs.py --check` to detect drift.

## Model quote and submission contract

For every model lane, `estimate_cost` returns `quote_id`, `cu_cost_exact` (the
server cost as a decimal string), the numeric `cu_cost` and `details`. Model
preparation and credential checks run on Blender's main thread, and the dry run
on the shared session workers while the MCP request thread waits without
blocking Blender. After explicit user approval, pass those values as `quote_id`
and `approved_cost` to `generate` with the same lane, model and parameters. The estimate's lane
is `image` when omitted. A changed lane, scene, credential context, input or
consumed quote requires a fresh estimate. An estimate alone does not authorize
spending. Failed or timed-out submissions must be inspected by their returned
local ID, never blindly repeated.

`model_schema`, `estimate_cost` and `generate` on every lane, including the
render lanes, `render_form` (`configure` and `prepare`) and the render-lane
`estimate_prompt` need the model's description. When this session has not
loaded it yet, the call starts or joins its background read and waits up to 30
seconds (a chosen limit, not a measured read time) for it on the MCP request thread. Blender's main thread stays free, then
delivers the read and answers the call. These tools read the description before
they quote, submit or change anything. If the read is still in flight after the
wait, the call returns `The model description is still loading; call again in a
few seconds. Nothing was quoted, submitted or changed`. A failed read returns its
reason, and the next call reads the description again. `render_form` `inspect`
and `remove` wait the same way, but still answer, with the form's errors, when
the read fails or is still in flight. Reviewing a `video_first_frame`
application never starts a read; it requires the Render Video model loaded, for
example by `render_form`.

All MCP model submissions persist intent before SDK dispatch. `job_status`,
`wait_for_job` and `list_local_jobs` expose saved state across restart. Active
jobs poll and download through the shared session; restarted jobs need explicit
recovery. `wait_for_job` lets the main thread advance work and returns at a
terminal/review state, while disabled Online Access holds delivery, or at its
timeout. Cancellation and interrupted download recovery use the same scoped
revision guards as Image.

Only the Image lane automatically imports verified PNG/EXR images into its
unchanged original scene. Other lanes stop at `ready` after saving downloads;
there is no implicit texture assignment, mesh import or sequencer insertion.
`job_status.results` reports asset identity, media type, expected size, `source`,
`projection` and whether a download receipt was recorded. `source: original` means
a declared OpenEXR original was saved instead of its preview, or a WAV original
instead of Scenario's MP3 copy, with unknown size;
`projection: equirectangular` appears only when Scenario declares a 360 image.
Neither is an application approval. This inspection does not reverify local files
or authorize scene application. Recovered display records use generic
`kind: model`; original lane metadata is not persisted. PNG/EXR results can use
the explicit image import approval above, without inferring a material or World
assignment from their media type. Supported video/audio uses the explicit
asset and scene/frame approval above. Saved model `import_units` use the
scene/cursor approval above; in-place mesh editing is separate.
`import_result` rejects
direct cached-file import, including prototype records, with guidance for scoped
recovery and explicit destination approval. It requires complete selected
credentials to inspect the saved store. Read-only inspection of any prototype record
uses its local snapshot without credentials, polling or manager creation. No
prototype result event automatically applies cached files into the current scene.

Speech-to-text (`audio2txt`) and video-to-motion (`video23d`) models are
experimental. `estimate_cost` and `generate` describe this status and accept
them like other models, and `list_models` returns the picker's status text in
each entry's `capability_status` (empty otherwise), named apart from the
server's model status. Their results stay in saved jobs.
`prepare_result_application` imports a returned model or media file by file type
only; motion and transcription handling is not accepted ([#190](https://github.com/scenario-labs/blender-plugin/issues/190)).

### Trained models (LoRAs and compositions)

`list_models(lane=image|render_image, source=trained)` lists the LoRAs and
compositions the native picker's **Trained** chip offers for the selected
credentials and project: private trained models first, then public Scenario LoRAs
and compositions, available rows first. Like the chip, a call reads the trained
list again after a failed read, and reads the lane's base-model descriptions that
are still unknown, once per connection. While those reads run, `bases.pending` is
true, rows wait with a reason and `note` asks to call again. `source=all` adds the
same rows to the catalog listing; other lanes refuse both sources. Each row has
`kind`, the REST `type`, `private`, and `available` or a `reason`. Its `routes`
name each base model that runs it and the inputs that carry it: `model_input`,
plus `scale_input`, `scale_range`, `scale_step`, `start_scale`, `default_scale`,
`min_items` and `max_items` for a LoRA stack. `start_scale` is where the native
picker starts that LoRA's strength on that base: the strength the LoRA declares
for itself, otherwise 0.75 (the Scenario web app's default), within
`scale_range`. `default_scale` is the base's declared default, which fills only a
strength a request leaves out. A composition also lists its `concepts`. Rows and
routes that the lane's saved default uses are marked `default`. A call returns at
most 40 rows and always keeps those the saved default uses. Private plain
custom models are not listed, as in the picker. `model_schema` reports the same
inputs as `lora_component`; its parameters carry `model_types`,
`min_items`/`max_items` and a `role` (`lora_models`, `lora_strengths` or
`composition`). An array count the schema declares is reported only when it is a
finite whole number of zero or more; any other value is left out.

A trained model is never the generation target; it runs through a base model from
its routes. A LoRA or composition has no inputs of its own, so `model_schema`,
`estimate_cost` and `generate` refuse its ID as `model_id` with a final answer that
points to those routes: a listed one before any request, an unlisted one after its
single description read. Any other description without inputs gets the same final
refusal; neither is read again. In the Image lane, pass the base as `model_id` and
the trained models in its own inputs, for example
`{"prompt": "...", "loras": ["model_a", "model_b"], "lorasScale": [0.8, 1.25]}`,
or `{"prompt": "...", "modelId": "model_c"}` for a composition. `estimate_cost`
and `generate` read these inputs strictly and rebuild them with
`trained_routes.apply`, the function the native form uses. A choice the form would
refuse is refused before any request:

- a model that is not listed, not trained, or not a LoRA or composition;
- a type the base does not accept, or more LoRAs than it allows;
- a missing strength (no captured base declares a default), a strength outside
  the declared range or with more than four decimals, or strengths that do not
  line up one per LoRA;
- a LoRA in the composition input, a composition in the LoRA input (with or
  without a strength), or a composition combined with LoRAs. A model in the other
  input is refused, never moved: the service refuses a composition in the LoRA
  input.

Other lanes refuse these inputs. The quote then reads every chosen model again,
as it does for the form. For Render Image, `render_form` `configure` takes
`trained: [{model_id, scale}]`, checked the same way before the form changes,
except that a LoRA without `scale` starts at its `start_scale`, as a picker choice
does. On a base without a strength range it keeps none and the base's declared
default is sent, as in the form. `[]` removes the choice.

The same choice through the native form and through MCP builds the same target
and payload, so its saved intent has the same `payload_sha256`. For equal bytes,
a form sends each decimal field as the shortest decimal its 32-bit storage holds
(`0.8`, not `0.800000011920929`). `estimate_cost` and `generate` start
each number parameter that is not given at its declared default, in the field's
own type (`0.0` in a decimal field), as a form does. `null` still leaves an
optional parameter to the service, like a field switched off in the form. The
[Environment lane](#environment-lane-360-panoramas) has no form; its requests
apply their panorama contract's lane defaults and sizes instead.
Approval is unchanged: `generate` needs the same parameters and the exact
`cu_cost_exact`.

`model_defaults` reads, saves and clears the per-project defaults that the native
**Trained models** box shows for the Image and Render Image forms. `get` returns
`context_id` and, for each lane, `revision`, `default`, `label` and whether it
runs now; when it runs, `parameters` holds the base's LoRA inputs to add to an
Image `estimate_cost` call. `set` takes `context_id`, `expected_revision`,
`base_model_id` and `picks`; a LoRA without `scale` is saved at its `start_scale`,
as choosing it in the picker and then **Save** would. Like the native **Save**, it refuses a choice that
cannot run in that form now, a stale revision, or a context from other credentials
or another project; `clear` takes the same context and revision. Neither requests
a price or changes a payload. A form without an explicit choice takes the saved
default, as in the native UI, while Image `estimate_cost` uses only the parameters
it is given. A default is a local choice kept in the job store, not spending
authority. Live use of a private composition is tracked in
[#440](https://github.com/scenario-labs/blender-plugin/issues/440).

### Preparing Render Image and Render Video

1. Discover a model with `list_models(lane=render_image|render_video)` and inspect
   its parameters with `model_schema`.
2. Call `render_form` with `action=configure` and `settings`: select `model_id`,
   supply `look` or choose `spark_enabled=false` for the photoreal default, edit
   scalar `parameters`, and optionally provide `style_assets`. These edits change
   the current native form; they do not capture, upload or submit generation.
3. Call `render_form(action=prepare, role=scene)` to capture and upload the current
   camera/viewport. For Render Video, set `first_frame_path` and prepare
   `role=first_frame` when using it. `render_form` reports `first_frame_route`:
   `sent_as=first_frame` uses the model's own first-frame input, while
   `sent_as=reference_image` sends the image as image 1 of the named input and
   keeps the scene clip. Its `reason` is `exclusive` when the model's input
   descriptions say a first frame can't be combined with reference videos
   (Seedance 2.x, Minimax H3, Wan 3.0), or `no_first_frame_input`; show the
   `note` to the user. Inspect until uploads finish. Captures use
   the current scene/camera and existing clip range; later scene edits do not
   change these uploaded snapshots. To use a downloaded saved image instead,
   find `use_first_frame` in its `job_status` actions, then call
   `prepare_result_application` with `purpose=video_first_frame` and its
   `asset_id`. Show the scene, model, input, `sent_as` with its `note`, file
   and whether a chosen first-frame file is replaced; after approval, `apply_result_application`
   binds the saved asset ID without an upload or a stored file path, and status
   `first_frame` reports `bound` or `failed`. `render_form` then reports an empty
   `first_frame_path` and the slot's `source_result`.
4. An empty look with automatic Spark enabled requires a separate
   `estimate_prompt(lane=..., action=GENERATE)` and explicit `approve_prompt`
   with its exact approved cost. The shared pump delivers the look only to the
   unchanged form. Render Video Spark requires the uploaded or handed-off first
   frame.
5. Call `estimate_cost` with the same lane/model and **omit `parameters`**. Show
   `cu_cost_exact`, obtain approval, then pass that exact string and `quote_id`
   to `generate`, again omitting `parameters`. Both tools rebuild the native
   decorated prompt and ordered uploaded inputs. Changed inputs, scene or
   credentials cannot spend an old approval.

Use `render_form(action=inspect)` to read preparation errors and reference
handles. `action=remove` requires the exact current `reference_key`; it detaches
that slot without canceling or deleting its saved upload. Repeated preparation
refuses an occupied slot, including uncertain uploads. Inspect saved progress
before explicitly replacing a snapshot. A model change requires removing its
old references first. `style_assets` replaces only unmarked style references;
marked uploads require explicit removal. When the style input also takes the
scene capture or an enabled first frame sent as a reference image, it keeps
one slot free for each that is not prepared yet. Optional scalar parameters accept null
to disable them; invalid edits fail before changing the form. Final quotes still
validate conditional and one-of schema requirements, and refuse two file inputs
whose descriptions say they can't be combined. That guard reads description
wording and checks file inputs only: a setting described as exclusive, such as
Kling V3 Omni's `generateAudio` with a reference video, is still sent (see
[known limitations](KNOWN_LIMITATIONS.md#creation-and-scene-application)).

Inspection returns only the enabled parameters used by the render lane. Its
`parameters` object can be passed back to `configure`, including numeric choices
represented by Blender's string enum identifiers. Only declared numeric choices
accept these strings; other numeric settings still require JSON numbers.
Inspection also lists `trained_models`: the LoRAs or composition chosen for the
Render Image form in the native picker's Trained chip, each with its kind and
the strength it sends: its own, rounded, or for a LoRA without one the base
model's declared default; `null` for a composition, which applies its own
strengths, and for a LoRA with no strength to send. The render quote and
generation include them, and a choice
that cannot run blocks preparation with its reason. A Render Image form whose
model was never chosen takes the selected credentials and project's saved
default when it is inspected, configured or quoted, as in the native form, or
reports why it cannot. A `configure` without `model_id`, `trained` included,
edits the form once it holds that default; while the default is still applying
or unavailable, it changes nothing and returns that reason. Naming `model_id` in
`configure` is an explicit choice that the default no longer replaces.
`configure` with `trained` replaces the choice, checked like a picker choice
before the form changes, and is an explicit choice too; see
[trained models](#trained-models-loras-and-compositions) for the rules and
`model_defaults`.

Raw render-lane parameter calls fail with preparation guidance. Use the
ordinary Image/Video lanes for direct model parameters. Scene capture, file
upload, Spark approval and render submission remain separate actions.
Multi-object material application is
[#446](https://github.com/scenario-labs/blender-plugin/issues/446), and the open
render and Film checks are in
[#440](https://github.com/scenario-labs/blender-plugin/issues/440).


### Reusing completed shared results

Use the same `prepare_result_application` and `apply_result_application` sequence
with a fresh observed revision and destination. Preparation returns `reuse: true`
for another local application of a completed result; show this explicitly with
the selected files and destination before applying. It does not submit generation.
Restoration returns `reuse: false` and targets the most recent World assignment
for that job in the selected scene and current file session. Applying the same
result in another scene preserves both scenes' restoration handles.

`job_status.local_applications` reports each local claim's identifier, source
revision, purpose, selected asset IDs, destination identity and state. The
original generation's `status: applied` does not prove a new local application
finished. Observe `actions`, `delivery_paused`, errors and the local outcome.
The `objects`, `materials` and `images` lists include every still-live output of
that job retained by the current session, including earlier reuse. Deleted data
is omitted, and receipt retry does not duplicate entries. These display names are
not durable object identifiers and are not reconstructed after restart.
An unfinished local claim blocks further reuse, including after restart. A
session-owned `retry_receipt` action saves only the known outcome. No tool clears
uncertainty or automatically repeats scene work. Each job retains at most 128
local claims without evicting earlier records.

### Environment lane (360 panoramas)

Lane `panorama` (Environment) generates 2:1 equirectangular panoramas and HDR
environments through dedicated Scenario models. The native
[Environment tab](USER_GUIDE.md#environment) builds the same contract body for
the same inputs. `list_models(lane="panorama")` returns every model with a
verified [panorama contract](../scenario/core/scene/panorama_contract.py), whatever
mode the native form shows, each with a `contract` object:

| Mode | Model | Output | References |
| --- | --- | --- | --- |
| `GENERATE` | `model_scenario-skybox-flux` | LDR | optional `image`: a saved 360 panorama to restyle |
| `GENERATE` | `model_scenario-skybox-gpt` | LDR | up to ten `referenceImages` of any image asset |
| `FROM_IMAGE` | `model_hunyuan-world-image-to-skybox` | LDR | one `image` of a place, such as an uploaded capture |
| `HDRI` | `model_hdri-skybox` | HDR OpenEXR behind an LDR preview | one saved 360 panorama as `image` |
| `UPSCALE` | `model_sc-upscale-flux-skybox` | LDR | one saved 360 panorama as `image`, up to four style `imagePrompt` images |

`estimate_cost` and `generate` refuse other models in this lane and apply the
same contract to the parameters before the exact quote:

- Locked values are set on every request, and a different value is refused. The
  HDRI model saves only its OpenEXR output: `outputExr` true, `outputHdr`,
  `conversionCubemapCross` and `conversionCubemapFaces` false.
- Lane defaults apply when a parameter is unset: the Flux model makes one
  panorama (`numOutputs` 1) instead of the schema's two. The Image lane keeps
  the schema defaults of the same models.
- The GPT model takes only 2048x1024 (default), 3072x1536 or 3840x1920.
- A panorama-kind reference must be the `asset_id` of a saved, downloaded result
  of this account and project whose `job_status` result shows
  `projection: equirectangular` and `source: asset`. Other asset IDs, OpenEXR
  originals and results without a declared projection are refused, never assumed
  to be panoramas.
- The upscale factor is capped for the 32-megapixel World limit. The saved
  reference's pixel size is not stored, so the factor is at most 2, which fits a
  2:1 panorama up to 4096 pixels wide, and an upscaled result cannot be upscaled
  again here. Flux and HY World output sizes are unverified; a larger reference
  could still exceed the limit, and World application then refuses the result.
- A model schema that no longer has an input the contract depends on, or no
  longer accepts a locked value, fails with "This panorama model's contract
  changed; update the extension".

Quotes, single-use approval, durable submission and recovery are the same as in
the other model lanes. Panorama jobs never import an image or change a World by
themselves: apply a saved result with `prepare_result_application(purpose="world")`
and [explicit approval](WORLD_APPLICATION.md#saved-result-ui-and-mcp-approval).
Pass the prompt in `parameters`. `estimate_prompt` with `lane: panorama` prices
New, Rewrite or Translate for the native Environment form's own prompt field,
like the other native lanes. A native panorama input also accepts a local 2:1
file that passes a preflight before upload; `upload_reference` has no such check,
so an uploaded file is refused as a panorama reference here. HY World output and
the HDR original's host are open in
[#440](https://github.com/scenario-labs/blender-plugin/issues/440). The Image and
Render Image lanes also list these models.

## Saved result previews

`inspect_result_previews` reports the previews that the native Jobs and
Generations tiles and the saved-job **Waveform** control show, from the same
session [preview scheduler](RESULT_PREVIEWS.md#local-mcp-inspection). Pass
`context_id`, `request_id` and `expected_revision` from `list_local_jobs` for a
job whose results are downloaded (`ready`, `applying`, `apply_failed` or
`applied`). Without `asset_id` it inspects every downloaded result with a
preview kind, ranked as the tiles are; the tiles page through them four at a
time, and the tool needs no pager. Pass one `asset_id` from `job_status` for a
single result. Each
result keeps the tile's 1-based `number` and `caption`, such as `Audio (2)`,
which names it as status lines and saved-job controls do; the tile itself draws
the number first, `(2) Audio`.

A still is Scenario's preview image for video, 3D and audio results
(`source: scenario`) or a small PNG decoded from a verified saved image
(`source: local`). A result without a preview kind, such as text, reports an
`unsupported` still without a `source`: nothing is requested or decoded for it.
Requesting a Scenario still reads asset metadata and
downloads the image while Online Access allows, polling for about five minutes
when it is missing; with Online Access disabled it reports `offline` and sends
nothing. Only an interactive Blender decodes image stills: in background
Blender their `decode` state fails after 120 seconds. `envelope: true` with one
audio `asset_id` decodes its waveform envelope in an offline Blender process
from the verified saved file, with no service request; a ready envelope returns
its duration, sample rate, channels, a `silent` flag and up to `bins` RMS and
peak levels from 0 to 1, merged from its 256 cached levels (fewer for audio
shorter than 2.56 seconds). Envelopes and ready stills are cached by receipt, so
inspecting again, including after a restart, needs no new decode.

`action: retry` prepares one result's previews again and clears a `missing`
marker, like **Retry preview (N)**; `action: cancel` stops its local decoding,
like the waveform's **Cancel**, and fails unfinished local renditions until a
retry. A `wait` of up to 60 seconds returns once the requested renditions, the
envelope with `envelope: true` and otherwise the stills, are no longer
`queued`, `pending` or `decode`, so an envelope wait does not also wait for a
Scenario still that keeps polling. A retry reads `queued` at once, even while a
batch for that result is on the preview lane, so a wait after it returns only
once the retried renditions settle. `in_progress` is `true` while any returned
rendition is still in progress, including when the wait expired first. If the
saved job changes during a wait, the tool refuses with a request to list local
jobs again instead of reporting its earlier revision.
`offline` renditions end the wait, because they change only when the user
allows Online Access. The tool returns state, sizes, dimensions and levels
only: never image bytes, cache or result paths, signed URLs or the saved file.
It never generates, spends, applies, writes the saved job or changes the scene,
and no local MCP tool opens a system or Blender player.

## Captured mesh input history

`estimate_cost` also returns `mesh_sources` for typed 3D inputs that identify
completed mesh snapshot uploads in the selected credential/project scope.
`job_status` retains these bindings after submission and restart. Each includes
the exact parameter/array position, asset and upload identities, captured origin,
export hash, source fingerprints and transforms. Ordinary assets and file uploads
have no inferred source metadata. Ambiguous matches fail before spending.

The shared coordinator persists and revalidates these records before its paid
claim. They describe uploaded snapshots and stay out of Scenario request bodies.
They do not prove the current source is unchanged, establish provider alignment,
or authorize finding/replacing an object after restart. See
[the storage contract](JOB_STORAGE.md#captured-mesh-inputs-in-generation-intents).


## Applying to an original mesh source

Use `prepare_result_application` with `purpose: mesh_source`, the observed job
revision and one saved GLB `asset_id`. It resolves the job's single captured mesh
input against the same session's unchanged live export, ignoring active selection.
Show the returned target, asset, policy, coordinate placement and Keep original
choice before calling `apply_result_application`. The generic `mesh_edit` purpose
continues to review the active mesh as an explicit destination.

Multiple captured inputs/sources, changed sources and missing live guards fail
before mutation. Undo/load, credential retirement and restart cannot restore a
guard from saved provenance. This shares the native
[captured-source action](MESH_APPLICATION.md#applying-to-the-captured-mesh-source),
including verification, durable claims and no generation or automatic retry.


## Saved mesh undo status

`mesh_edit.undo_available` reports whether a desktop Blender history checkpoint
was recorded for a completed saved mesh edit. This applies to every
`mesh_policy` through `mesh_edit` and `mesh_source` review. Respect Blender's Global
Undo and history limits; a false value does not mean application failed or may
be repeated. Headless application does not create desktop history; it still
marks the file modified.

Undo/Redo restores scene data without replaying service calls or changing durable
job/application/spending records. The transient `mesh_edit` status becomes null
after a history change, including Redo; it is not rebound by object name.
History changes discard live target authority,
so another local application requires fresh destination review. See the
[full history contract](MESH_APPLICATION.md#native-undo-for-saved-mesh-edits).


### Saved parts application

Use `mesh_policy: PARTS` with `purpose: mesh_edit` or `mesh_source` and one
explicit saved GLB asset. Review that every mesh in this artifact is intended as
a part. The source retains its object identity and becomes an empty mesh parent
with 2 to 128 named static children; Keep original defaults to true. The same
explicit coordinate mapping, target checks, durable claims and undo limits apply.
`mesh_edit.parts` reports the child names while the live application is valid;
history invalidation clears the transient mesh status without resetting the job.
Do not reconstruct authority from those names. See the
[parts contract](MESH_APPLICATION.md#apply-static-parts) for limits and rollback.


## Attaching a returned rig

Use `mesh_policy: RIG` with `purpose: mesh_source` for the unchanged original
exported source, or `mesh_edit` for a newly reviewed destination. Show the named
source, coordinate mapping and Keep original choice. The source must be unrigged;
one returned mesh and armature must have exactly matching indexed geometry and
normalized bone weights. This preserves source geometry, UVs and materials while
adding the returned skin/rig clips. Morphs, mesh animation and incompatible
modifiers/constraints are rejected. Move the source and its new rig group together
afterward. `mesh_edit.rig` names the current session's attached armature; it is not
a durable lookup authority after undo/load. The shared native history and
receipt-only recovery rules apply. See [the RIG contract](MESH_APPLICATION.md#attach-a-returned-rig)
for bounds and provider/retargeting limitations.


## Blockout plan commands

`estimate_blockout` reads the current scene's native Blockout description, type,
scale and previous plan for DESIGN or REFINE. It returns an exact free quote;
`approve_blockout` requires that quote and unchanged decimal cost, then persists
one intent before submission. Changed fields, scenes or credentials reject the
approval. UI and MCP use the same facade and job session. Complete returned JSON
updates only the unchanged original scene's stored plan; geometry is built by a
separate explicit local **Build plan** action.

After failure or restart, use `list_local_jobs` for context/revision and
`job_status` for saved asset IDs. `read_model_text` retrieves one selected full
text output without spending or applying it to a scene. Incomplete previews must
be downloaded successfully; truncated JSON prefixes are not plans. For native recovery, choose **Read saved Blockout plan** in saved jobs, then
**Use saved Blockout plan** to review the destination and whether its existing
plan will be replaced. This does not change geometry or spend credits.

MCP uses `prepare_blockout_plan` with the current context and saved revision,
then `blockout_plan_status` until ready. Show the scene, element/group counts and
replacement flag before explicit `apply_blockout_plan` approval. The handle is
single-use and bound to the unchanged destination and saved record. Pass
`discard: true` to the status command to discard a finished review. The complete
plan stays in memory until approval; no result text is stored in job metadata.
Use native **Build plan** separately for geometry. Native plan application has
Undo. `apply_blockout_plan` keeps the same scope and destination guards and
records one Undo History step after the plan changes, which marks the file
modified.

## Cloud history and scoped saved results

`list_generations` returns at most `limit` loaded rows, newest first. It never
truncates silently: `more_loaded` counts loaded rows beyond `limit`, and
`older_page: true` means an older cloud page exists. Call with `older: true`,
then again without it, to append that page. If that read fails, the next call
still returns the loaded rows and `older_page`, with `older_error` and a note:
retry with `older: true`, which reuses the cursor as **Load older** does. Only a
repeated cursor asks for `refresh: true`, which restarts paging. A page skips
uploads and mesh preview renders, so it can list fewer rows than the service
returned.

`list_generations` returns `local_request_ids` for cloud rows matched to the
selected credential-bound store. All cloud rows expose empty `local_files`; use
`list_local_jobs` and the returned request identity for current status/revision
and explicit result preparation/approval. Matching is refreshed even when the
cloud page was loaded before a local remote-job acknowledgement.

Each row's `kind` is `image`, `video`, `3d`, `material`, `audio`, `workflow`
or `unknown`. A workflow run is always `workflow`. A model row matched to saved
results uses their recorded media types: a mesh, video or audio file outranks
accompanying images, and images with PBR map roles beyond a base color form a
material. A prototype cache record keeps its recorded kind. Otherwise the
model's lane in the loaded catalog names the row, including when the catalog
loads after the history page. `unknown` means no saved result or catalog record
describes the row yet, or its model has no generation lane; it never stands for
an image. The cloud job list returns result asset IDs without their media types.

Each row's `cu_cost` is the job's `billing.cuCost` plus its `cuCostDetails`
add-ons, such as quality-gate fees, which an approved quote may exclude.
Workflow runs appear as `kind=workflow`
rows whose own charge is normally 0; their `cu_cost` adds the charges of the
step jobs named by the run's flow or by each step's `workflowJobId`. Steps
missing from the page are read on the history worker, at most 24 per page,
whole runs with the fewest missing steps first, then once more for the steps of
nested runs within the same limit. If any step cannot be read or priced, the
finished run reports `cu_cost: null` and `cost_unavailable: true` instead of 0;
a running workflow reports no cost yet. A row whose own billing is malformed
reports the same, while the other rows keep their costs.
Step rows keep their own cost and name their run in `workflow_job_id`. A run's
non-null `cu_cost` already includes them, so do not add them again; while the
run is running or its cost is unavailable, no total includes them yet. Workflow
rows cannot use `recover_cloud_job`.

`job_status`, `wait_for_job` and the old `import_result` lookup prefer a matching
scoped record to an old unscoped cache. `import_result` rejects direct application
of both saved jobs and prototype cache entries. Ambiguous remote IDs require a
local request ID, and a failed store read never falls back to cached import.
Cold prototype-only status reads retain their existing behavior; no prototype
registry migration is performed.

For an unsaved successful model row, call `recover_cloud_job(job_id, model_id)`.
The shared SDK read verifies the current remote job/model and saves a scoped
cloud result. Pending UI/MCP reads for that job/model share one worker. Repeated
reads preserve the existing intent, result receipts and application history,
including after restart. The response returns context, request ID and revision.
Read failures may be explicitly retried; this never retries generation.
Use `recover_local_job(action: resume)` to download, then prepare and approve the
result destination. The recovery read does not download files or mutate a scene,
and it rejects delivery into a changed credential context.

### Film task commands

Film is experimental. Every Film tool description says so, matching the native
panel header and Studio's Film pages; the commands remain available.

`film_recipe` loads or inspects the native scene's validated recipe and stable
production identity. Loading preserves identity; `new_production` deliberately
starts another production without submitting anything. Save the blend file.
Every response has a `warnings` list. It is empty unless the recipe omits
`audio_tracks` and no task is named `score`. Final review and composition with
the default `score_task_id` then refuse it: declare that task, pass a
`score_task_id` naming another declared audio task when preparing, or add
`"audio_tracks": []` for no score. A recipe with this warning still loads, and
previs is unaffected. The warning covers only the default task; preparation
checks the `score_task_id` it receives and names any undeclared one.
`estimate_film_task` uses that identity and task name to return resolved inputs
and an exact shared SDK quote. `approve_film_task` consumes the quote with its
verbatim CU cost before durable submission. `discard_film_estimate` releases an
unused quote. `bind_film_upload` saves an immutable reference to an unchanged
imported upload observed through `list_reference_uploads`.

If a scene switch interrupted the estimate response, return to the original scene
and call `film_recipe` with `action: inspect`. A `quoted` task returns the existing
`quote_id`, `model_id`, `parameters` and `cu_cost_exact` for approval or discard.
Inspection completes already-admitted preparation but never requests a new price;
stale quotes are omitted. Pending preparation appears as `quoting` or `binding`.
Saved upload associations remain visible as `bound` after an interrupted response.
Identical binding retries are revalidated; a different upload or revision is
rejected before replacing an already-bound task's displayed status.

Recipe management, inspection and upload association are local operations with
no platform equivalent. Estimate/approval use the same model operations as
`estimate_cost`/`generate`, through the shared session. Use `job_status`,
`wait_for_job`, explicit recovery and application for saved results; Film does
not auto-import or replay uncertain tasks. These commands do not construct scenes,
capture shots, assemble media or export a finished film.

### Film shot commands

`film_shot_sources` lists eligible saved GLBs for each hero and returns the
current context token. `prepare_film_shot` binds explicit per-hero asset/job
revision selections to that context, production, recipe and destination.
`film_shot_review` polls verification status without building; its other actions
discard unapproved work, save known receipts or dismiss an uncertain review after
explicit inspection (`inspected: true`). Dismissal never clears a durable claim
and refuses pending receipt handles. `build_film_shot` separately approves one
ready review, claims all sources and builds a new scene while retaining the
working scene. It must not be repeated after uncertainty.

These four local Blender commands have no platform equivalent and make no
generation/download request. They share native UI review handles; a build records
one Undo History step afterwards, like the native operator, which marks the file
modified. Timeline approval uses the commands below;
capture and export remain separate.

### Film capture commands

Inspect `film_capture_sources(production_id, shot_id)` and choose an opaque
source handle. `prepare_film_capture` requires that handle and context identity,
with still/video, dimensions and color mode. Show the exact returned settings
for approval before `render_film_capture(review_id)`. Rendering consumes that
review once and returns immediately; `film_capture_review` polls, cancels local
work or explicitly discards its private files after work stops.

Once CAPTURED, review the local output and ask for separate upload approval
before `upload_film_capture`. Staging checks its content hash before remote
initialization. Poll the ordinary `reference_upload_status` handle, inspect
uncertainty without retry, then explicitly `bind_film_upload` after import.
Neither local rendering nor uploading grants generation approval.
These tools use the [same native capture owner](FILM_PLAN.md#shared-capture-and-upload-approval).
Reviews expire with the session, capture files are cleaned on shutdown and
interrupted captures never resume automatically.

### Editable Film timeline commands

`film_timeline_sources` returns current-context opaque scene choices for every
recipe shot. `prepare_film_timeline` requires that context, production and a
complete shot-to-source-ID mapping. `build_film_timeline` separately approves
the resulting review, rechecks unchanged local scenes and creates a new editable
scene-strip sequence. The working scene stays selected. Later edits to the
referenced shots affect the timeline.

`film_timeline_review` reads status or discards a review; uncertain partial
cleanup requires `inspected: true` and must never be replayed automatically.
These four local commands have no platform equivalent, make no service call and
never change saved jobs or import result files. Local scene markers establish
recipe compatibility, not service provenance. They share the native command
owner; a build records one Undo History step afterwards, like the native
operator, which marks the file modified. Capture, review assembly
and video export use the separate commands in this section.

### Film composition commands

Inspect `film_recipe` for the selected context and production, then call
`prepare_film_composition` with final/previs mode. Poll `film_composition_review`
until local saved-media verification is READY. `estimate_film_composition` returns
the resolved model payload and full `cu_cost_exact`; obtain explicit approval of
both before `generate_film_composition(review_id, approved_cost)`.

Preparation needs retained upload files or downloaded results and installed
`ffprobe`; it never uploads or downloads missing sources. A missing, stale or
mismatched source, including the undeclared score above, ends the review in
`ERROR` with an error naming its task. The reviewed recipe
and original scene stay unchanged. Pricing/submission use the existing shared SDK
model path. Cancel/discard release preparation or approval, preserving saved jobs
and media. On an error, lost response or restart, inspect the recipe's declared
master job and use ordinary saved-job recovery; never repeat uncertain generation.
These tools share [native composition review](FILM_PLAN.md#native-and-mcp-composition-controls).
Review assembly uses the commands below.

### Film review commands

Inspect `film_recipe` for the selected context and production, then call
`prepare_film_review` with final/previs mode and an explicit optional
`include_master`. Poll `film_review_status` until READY. Show its timing,
shot/source/audio-segment counts, private copy size and master inclusion, and
obtain explicit build approval before `build_film_review(review_id)`. Building
needs a Blender window in Object Mode; an Edit Mode rejection keeps the review.

Preparation copies retained upload files or downloaded results into private
extension storage and needs installed `ffprobe` and matching frame rates; it never
uploads, downloads, generates or claims. When preparation fails, `error` names the
missing, stale or mismatched source task, track or master, such as the undeclared
score of a final recipe that omits `audio_tracks`, or the media inspection problem.
Other failures return generic guidance and log only their exception type and code
location once. Recipe, frame, scene or connection changes
invalidate the review and delete its copies; scene changes include selecting, adding
or editing objects, and any Undo or Redo resets every captured origin. Building one
review also invalidates any other unbuilt review for the same scene, so prepare and
build one at a time. Keep the recipe scene
selected until building: selecting another scene and then the recipe scene again
also invalidates it. `WAITING` means preparation finished while another scene was
active; a return through the scene selector turns it into `ERROR`, so prepare again.
Building consumes the review, saves application claims for generated sources and
creates a new scene while the working scene stays selected. `film_review_status` can
cancel preparation, discard an unbuilt review's copies, `retry_receipt` for a known
saved outcome or dismiss an inspected uncertain review with `inspected: true`; none
rebuilds or clears a saved claim. An uncertain review blocks new preparation for its
scene until its receipt is saved or it is dismissed.

These three local commands have no platform equivalent, make no service call and
share the [native review controls](FILM_PLAN.md#native-and-mcp-review-controls).
A build records one Undo History step afterwards, like the native operator, which
marks the file modified. Handles expire with the session.
Built review scenes export through the commands below.

### Film export commands

`film_export_sources(production_id)` lists the review scenes built for the
current production and unchanged recipe as opaque `source_id` handles, with frames,
rate, size, media count, audible sound strips and any blocking reason. It also
returns the context, `default_directory` (the Output Folder's Film subfolder), a
suggested `default_name` per source and the verification that will run. Call
`prepare_film_export` with that context, production and source and an absolute new
`destination_path` inside the Output Folder; optional even `width`/`height` and
`include_audio` override the scene's size and audible sound strips. A listed size
outside 64 to 4096 pixels is not a blocking reason, but preparing it then needs
both `width` and `height`; without them the refusal names that size. Nothing
renders. Show the returned `destination`, timing, size, audio and media count and
obtain explicit approval before `render_film_export(review_id,
approved_destination)`, sending that destination verbatim like an exact cost.

Rendering needs Object Mode with the recipe scene selected and both scenes
unchanged since preparation: a frame change, object selection or edits, Undo or
Redo, or leaving a scene and selecting it again invalidate a ready export, as does
the destination appearing. An edit of the review scene while no window shows it,
such as from Python, is refused only when it changes the frames, rate, size, strip
or media counts or audible sound that `film_export_sources` reported. It snapshots
the review scene privately, encodes an H.264 MP4 with an AAC track only when
requested in an offline Blender child, verifies it with installed `ffprobe` or,
more weakly, with Blender's decoder, and saves it without overwriting. Later scene
edits do not stop a running export; one export runs at a time. Poll
`film_export_review` for the stage, percentage, `sha256`, size and verification.
Its actions cancel (the destination stays absent), discard the private snapshot,
staged video and logs (an exported file stays), and after a failed save
`prepare_republish` with a new `destination_path`, then `republish` with the
returned `pending_destination` as `approved_destination`. A re-publish copies the
verified video and never renders again. Both approvals recheck that the
destination resolves inside the Output Folder; a native choice elsewhere is
refused without consuming it and stays for native confirmation.

These four local commands have no platform equivalent, make no service call and
spend nothing. They share the [native export controls](FILM_PLAN.md#native-and-mcp-video-export-controls)
and their handles; handles and private files end with the session.

### Film case-study commands

`film_case_study_sources(production_id)` lists the recipe's built review scenes as
`source_id` handles and its director timelines as `timeline_id` handles, each with
any blocking reason, plus the context, `default_directory` and a suggested
`default_name`. A timeline must still hold the current shot scenes in recipe
order. Call `prepare_film_case_study` with that context, production and source,
an optional `timeline_id` and an absolute new `.zip` `destination_path` inside the
Output Folder. Nothing is written. Show the returned destination, scenes, media
count and bytes and the ledger's approved tasks and `quoted_cost_total`, and obtain
explicit approval before `export_film_case_study(review_id, approved_destination)`,
sending that destination verbatim.

Building needs Object Mode with the recipe scene selected and the chosen scenes
unchanged since preparation; if the review scene's frames, rate, size or strip
count, the scene, media or ledger counts, the media bytes or the quoted total
shown by preparation changed, the approval is consumed and nothing is written. An
edit of a chosen scene while no window shows it, such as from Python, is caught
only through these values. Offline Blender children rewrite a private snapshot
into a portable library with bundle-relative media and without Scenario
identifiers or the extension's saved settings, then verify it; the archive adds
the sanitized recipe, production ledger, manifest and opening guide and is saved
without overwriting. The ledger keeps quoted costs but no credentials, URLs,
service identifiers or local paths. Poll `film_case_study_review` for the stage,
percentage, `sha256` and size. Its actions cancel (the destination stays absent),
discard private staging (a saved archive stays), and after a failed save
`prepare_republish` then `republish` with the returned `pending_destination`; a
re-publish copies the verified archive and never builds again. Case studies and
video exports share one export thread.

These four local commands have no platform equivalent, make no service call and
spend nothing. They share the
[native case-study controls](FILM_PLAN.md#portable-case-study-bundle) and their
handles, under the same agent folder policy as video export.


## Workflow execution

Use `list_workflows` for a private or public catalog and `workflow_schema` for
its declared inputs. Listing reads the full bounded catalog, deduplicates IDs
and returns at most 40 filtered rows with `next_offset`. A new call refreshes
metadata, so pages are not a stable snapshot. Inputs retain conditional and file
definitions; supported form validation happens again with fresh metadata at
estimation. File parameters use Scenario asset IDs: Library assets, or the
imported `asset_id` from `upload_reference` or `capture_reference`. This is the
MCP equivalent of native workflow input uploads from a file, viewport, camera,
clip or selected mesh; the native **Render result** snapshot has no
`capture_reference` source. MCP passes the ID in `parameters` rather than binding
it into the saved Studio form, and the quote binds captured selected-mesh sources
the same way.

`estimate_workflow` requests a free exact quote through SDK 2.2.0
[`workflows.run`](https://docs.scenario.com/api/python/resources/workflows/methods/run)
with `dry_run="true"` in the query. It returns the original `parameters`, the
normalized `payload` including defaults, and `cu_cost_exact`. Review that payload
and price. `run_workflow` requires its `quote_id`, the same workflow and original
parameters, and that exact decimal string as `approved_cost`.

Both tools also return `loop_steps`, `quote_may_understate` and `cost_warning`.
`loop_steps` counts loop nodes in the retrieved workflow definition: nodes of
type `for-each` and any node that carries a ForEach field (`loopBodyNodeIds`,
`count`, `loopNodeId` or `iterationIndex`). It is 0 only when every node has a
known non-loop type, or when the flow is empty and the record says
`hasFlow: false`. It is `null`, with the warning, when the flow is missing or
malformed; when it is an empty flow, unless the record says `hasFlow: false`;
when it contains a nested `workflow` step or any node carrying a `workflowId`,
whose definition the quote does not read; or when a node's type is missing, not
a string or not a known type. The SDK documents `hasFlow` as present even when
`flow` is not, so an empty list alone does not prove the workflow is empty.
Known non-loop types are those of the SDK 2.2.0 `WorkflowFlow.type` other than
`for-each` and `workflow`, plus the `user-selection` type seen in live workflow
jobs. The server's dry run prices one pass through a loop, so a run that
iterates more often can be charged more than `cu_cost_exact`. Show the warning
with the price. It never blocks approval.

`job_status` keeps reporting the approved quote as `cu_cost_exact`, not the
final charge. For a saved workflow job it also returns the same three fields.
The saved record does not keep the loop count, so after a restart `loop_steps`
is `null` and `quote_may_understate` is true.

Approval is bound to the current scene revision, file, credential and project.
It is consumed before local persistence and the single paid dispatch. A timeout
or failed write never permits reusing it. Inspect `job_status` and
`list_local_jobs`; closing a view does not stop admitted work. Known jobs use the
same polling, download, explicit application and restart recovery as model jobs.
Workflow results are never automatically imported. `discard_workflow_estimate`
releases only a ready local approval, leaving saved jobs unchanged.

A running workflow step cannot be canceled: `recover_local_job` `cancel` on a
running workflow job returns that explanation and sends nothing. Rejecting a waiting
approval or selection step is the only way to stop a workflow. The native
review dialog in Jobs and Studio sends decisions through the same entry as
`decide_workflow_step`.

### Workflow step decisions

When `job_status` or `wait_for_job` reports `awaiting_decision`, call
`workflow_job_steps` with the context and request ID from `list_local_jobs`. For
a `remote` workflow job it retrieves the job once more through the shared
session, or waits for the read or decision already in flight. It returns the
saved revision and state, the job's approved `cu_cost_exact` and the waiting
steps in the `pending_steps` form, with `blocked_reason` text for a blocked step.
A job no longer `remote` returns without a request and with no steps. The read
keeps how the job is delivered: an automatically polled job stays polled, and a
paused or restarted job stays paused, so its steps appear without resuming
downloads. It needs Online Access for a `remote` job. Each call is one more
status read, plus one read of the workflow's definition for its step links the
first time a step is seen waiting, so follow the job with `job_status` or
`wait_for_job` and call it only to review steps before a decision.

Show each step to the user with its review asset IDs or candidate items, its
bounds and the approved price. `decide_workflow_step` then sends only the user's
explicit choice: `approve` or `reject` for an approval, `select` with zero-based
`selected_indices` or `reject` for a selection. It needs the step's `review_id`
and the job's `expected_revision`. A changed step, revision or context, an action
the step does not list, picks outside its bounds, a command already in flight
or disabled Online Access is refused before any request. The decision takes the
shared `ModelJobs.decide` path: the job is retrieved again, the step must be
unchanged, and one request follows that is never retried. Picks are sent in the
given order. Review assets and candidates are asset IDs only; no preview or
signed URL is returned.

`outcome` is `applied` when Scenario accepted the decision and the job was read
again, `refused` for an HTTP 4xx reply, `uncertain` when the decision may have
been applied, and `not_sent` when a check failed before sending or the job had
already finished. The result also carries the steps read after the decision and
`decision_error`. Never repeat an uncertain decision: read the steps again and
decide only if the step is shown waiting again and the user chooses again.

Rejecting stops the workflow, and completed steps stay charged. The API reference
says a rejected ForEach iteration cascades to the surrounding loop instead, so
the refreshed state is authoritative. Approving or selecting lets the remaining
steps run and spend credits; nothing checks that charge against the approved
price. A selection that feeds a ForEach loop, a nested workflow or an unlisted
node type stays blocked, with only `reject`, because the approved price may not
cover what it runs. So does a selection
in a workflow with such a node when its step links cannot be read (`unverified`).
When `links_complete` is `false`, an approval's dependencies and review assets
may be incomplete; say so before the user approves.


## Asset library metadata

Use `list_assets` to read one page of assets accessible in the configured
credential/project scope. `public: true` instead requests the public library.
An optional `collection_id` narrows listing. Continue with the returned
`next_pagination_token` and the same filters; no cursor is followed automatically.

Use `search_assets` with nonempty `query` text for server-side asset search.
Continue with the returned `next_offset`, keeping query, public selection and
limit unchanged. The estimated total is not a stable snapshot, and changes to
the library can change later results. Each call is bounded to 100 rows.

Both commands return reference metadata: asset ID, name, description, MIME and
generation types, tags and collection IDs. Download URL fields, account records
and indexed text bodies are omitted. Search text can be incomplete, so it is
never exposed as a complete text asset. These calls do not download, upload,
generate, apply results or change collection/tag organization. Returned asset IDs
can be used in supported model/workflow parameters before a fresh exact quote.
Scene/session changes reject stale delivery; an explicit retry repeats only a
read. Studio's [Library page](ui/studio-and-library.md#native-library-controls)
browses the same metadata and attaches selections to inputs.

## Asset organization

`list_collections` reads one page of collections in the selected credential and
project scope. Rows contain `collection_id`, name, asset and model counts and
`updated_at`; thumbnail URLs and owner IDs are omitted. Continue with the
returned `next_pagination_token`; no cursor is followed automatically.

To change organization, call `prepare_asset_organization` with one operation:

| Operation | Arguments | Change applied after approval |
| --- | --- | --- |
| `add_to_collection` | `asset_ids`, `collection_id` | One request adds the assets that are not yet members; if another client added some after the review, the rest are sent again, in at most three requests |
| `remove_from_collection` | `asset_ids`, `collection_id` | One request removes the assets that are members |
| `update_tags` | `asset_ids`, `add_tags` and/or `remove_tags` | One non-strict tag request per asset that still differs |
| `create_collection` | `collection_name`, optional `asset_ids` | One create, then one request adding the assets |

Use 1 to 49 unique asset IDs (optional only for a create). Tags and names are
exact single-line labels; a tag list holds at most 30 unique tags without commas,
and a tag cannot be added and removed together. See the
[command contract](JOB_COORDINATOR.md#asset-organization-commands) for every
validation rule. Preparation reads each asset's current tags and memberships with
one bulk read, plus the target collection or, for a create, an exact-name lookup.
It sends no write. The review lists each asset's change and `request_count`, the
writes apply sends when each one succeeds. A missing asset, an existing exact collection name
(returned in `existing_collection_ids`) or a name whose earlier create in this
session had an unknown outcome makes the review `REJECTED`. Apply checks the name
again before creating: a name taken since preparation sends nothing and returns
`NOT_SENT` with those IDs in `existing_collection_ids`. A request with nothing
to change is `UNCHANGED`. `project_id` is the Project ID override, or `null` for
the key's own scope.

Show the review to the user before applying. Organization changes are immediate
Scenario account metadata: they use no credits and Blender Undo does not reverse
them. A `READY` review expires after 10 minutes and applies once through
`apply_asset_organization(context_id, review_id)`. Writes are not retried, with
one bounded exception: the service refuses a whole add, writing nothing, when
any asset is already a member. If another client added some assets after the
review, apply reads them back and sends only the rest again, within three add
requests; the result message counts the assets that were already members. The
first uncertain tag write stops the remaining assets, which stay `NOT_SENT`.
The assets are then read back: each outcome is `VERIFIED`,
`REJECTED` (with the HTTP status), `UNCONFIRMED`, `NOT_SENT` or `UNVERIFIED`, and
the result is `VERIFIED`, `PARTIAL`, `UNCONFIRMED` or `REJECTED`. Never repeat
unconfirmed work. Inspect the assets with `list_assets`, then prepare a new review
only if the change is still wanted; it reads the current state first and sends
only what still differs.

After a timeout or error from `apply_asset_organization`, even one saying the
tool was not executed, call `asset_organization_status` first. The server can
report a delivery timeout after the apply was already queued; the status shows
whether the review is still `READY` (nothing was queued), applying or finished.
A change continues in Blender after a client timeout, and a review never applies
twice. `action: discard` releases a review that is not applying; nothing is sent.
A discarded review that was already applied keeps its `result`. Discarding a
review whose apply ended with an unknown outcome returns a note that the change
may already be in Scenario: inspect the assets' collections and tags with
`list_assets` before assuming nothing changed, and never repeat it automatically.

Reviews belong to the selected connection, not to a scene: undo and scene
switches keep them. Loading a file, changing credentials or the Project ID, or a
reset discards them and replaces `context_id`, so the old context is rejected. A
write already in flight keeps its effect, later writes are refused, and the old
apply reports an unknown outcome instead of a result. With online access off,
nothing is sent. One review applies at a time per session, and at most 32 are
kept. Public library assets are not blocked locally; the service decides.

These tools use the shared [session reviews](BLENDER_JOB_CONTEXT.md#asset-organization-reviews)
and the SDK methods listed in [asset organization writes](SDK_ADOPTION.md#asset-organization-writes),
with no raw API fallback. Collection rename and deletion, model collections and
tag listing are not provided locally. A tag the service normalizes reads back as
`UNCONFIRMED`.
Studio's [Library organization](ui/studio-and-library.md#library-organization) controls
prepare and apply the same reviews, one asset at a time, and disable Organize
on pages loaded with Public assets. Apply from the desktop dialog continues in
[#440](https://github.com/scenario-labs/blender-plugin/issues/440).
