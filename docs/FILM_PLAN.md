# Film recipe contracts

The adopted [Film recipe helpers](../scenario/core/scene/film_plan.py) and
[scene-plan helpers](../scenario/core/scene/film_scene_plan.py) preserve bounded
planning, editorial timing, continuity declarations and reference ordering from
the selected Studio source. They are pure Python: they do not open files, contact
Scenario, submit jobs or mutate Blender.

This is the input contract for Film; its open checks and production gaps are in
[#440](https://github.com/scenario-labs/blender-plugin/issues/440).
Native Film task controls and MCP commands now use these contracts. Film shows a
visible experimental status in its sidebar header, on every Studio Film page and
in every Film MCP tool description. This does not establish a Film generation
journey or satisfy the [release gate](maintenance/release-plan.md).

## Recipe and timing validation

`validate_film_plan` returns a new normalized recipe. It rejects non-finite or
non-JSON values, cycles, recipes exceeding two million UTF-8 JSON bytes, unknown
structural keys, invalid transforms and ambiguous timing. It supports one to
fifty shots, at most three hundred upload/model tasks, and at most fifteen minutes
of editorial duration. Tasks have explicit names; another take needs another
task name. Model parameter objects are data and still require the selected
SDK model schema before a quote.

Text-field limits apply both before and after punctuation normalization. Replacing
an em dash with a comma and space cannot produce a value above its field limit.
Placeholder names are trimmed; names that then collide are rejected so no legend
entry is silently overwritten. Distinct names retain their order and meanings.
Scene object names must also be unique after normalization so motion targets are
unambiguous. Non-string names/kinds and excessively nested input report
`ValueError`, including recursion during copying or task-reference resolution.

Shot and task IDs allow up to 96 characters. Omitted `style_task`, `previs_task`
and `video_task` use the shot ID with `-style`, `-previs` or `-video` appended.
If that derived name exceeds 96 characters, validation names the field that needs
an explicit task ID. A 96-character shot ID remains valid with explicit task
names; validation does not shorten or rewrite task identities.

The adopted scene schema validates primitives, bounded locations/scales/colors,
camera paths and optional World settings. Its deterministic local templates are
identified as local templates; they do not call a generator. This schema retains
full transforms and camera/World data for Film. It does not replace the existing
[Blockout element schema](../scenario/core/scene/blockout.py) or its active
saved-result approval path.

Editorial durations and trims must land on frame boundaries. Adjacent shots
cover consecutive frame ranges without gaps. Source duration must cover the
editorial trim/window; the adopted source-duration contract still permits only
whole seconds from four to thirty. This inherited restriction is not a claim
about every provider's current model capabilities.
An editorial window longer than thirty seconds reports the `source_trim` plus
`duration` conflict, whether `source_duration` is explicit or omitted.

Audio helpers validate dialogue, sound effects and music tracks, source trims,
gains, looping and non-overlapping duck intervals. They calculate segments; they
do not decode, play or import audio. An omitted `audio_tracks` retains the
source's default looping score description; an explicit empty list disables it.
Neither case authorizes producing a score. With the list omitted, final review
and composition need that score task, `score` unless the caller names another,
among the recipe's `tasks`. A recipe without it still loads, and previs never
uses the score; final preparation refuses it with a `FilmSourceError` that names
the task and the `"audio_tracks": []` alternative. A load cannot know which
score task a later preparation names, so `film_recipe` load and inspection warn
only when no task is named `score`, the default; the warning states that
condition and the `score_task_id` and empty-list alternatives. Preparation checks
the score task it is given. `continuity_audit` compares declared
adjacent object states and intentional changes. Its `visual_verification` result
is always false: a matching declaration does not prove visual continuity.

## Credentials and task references

`project_id` is optional. An absent/null value means use the selected credential
scope, including the server's API-key default project. The helpers never discover
an account/team or infer a project. `require_plan_scope` rejects an explicit
recipe project that differs from the selected project override; callers must
check it before preparing service work.

`resolve_references` resolves `$task` or `$task:index` from ordered `TaskAssets`
observations in the exact selected [JobScope](JOB_STORAGE.md#identity-and-ownership).
Service, credential identity, project override and team must all match, including
when project/team are absent. A recipe's project field alone cannot authorize an
output. Missing tasks, invalid/negative/ambiguous indexes and foreign-scope
observations fail. Asset identifiers are opaque and need no `asset_` prefix.

The shared Film model-task command now constructs these observations from
matching completed scoped model jobs and explicitly associated imported uploads,
as described below. The pure helper itself does not read storage
or certify completion of a caller-supplied observation. Resolved parameters still
need a fresh exact quote, explicit approval and a durable task/submission binding;
resolution is not spending authorization. No separate Film runner, client or
production-state database is introduced here.

## Durable model tasks

`JobCoordinator.quote_film_task` validates a raw recipe in the selected scope,
resolves completed model-task references and uses the existing SDK model schema
and exact estimate command. `JobWorkers` and `JobSession` expose it on the same
bounded queue and main-thread origin delivery as ordinary model quotes. It creates
no separate runner, client or production-state file. Native Film controls and MCP
use it through the selected `FilmJobs` facade described below.

The caller supplies a stable opaque `production_id` and a recipe task name.
Reopening that production must reuse its identity; a title, scene name or fresh
random ID on every load is not sufficient. The quote captures a normalized recipe
SHA256 and a task SHA256. The task digest includes the normalized task and the
digests of referenced ancestors, so editing an earlier dependency cannot silently
reuse a later task's old output. References must name earlier tasks. Appending a
new task can reuse unchanged dependencies even though the full recipe digest
changes. Unknown, forward, self and malformed references fail before service work.

`prepare_quote` saves the binding with the exact payload/quote hashes and cost
in the existing [job store](JOB_STORAGE.md#film-task-reservations), before paid
submission. The scope/production/task tuple is reserved atomically across owners.
Any existing attempt, including canceled, failed, prepared and uncertain jobs,
blocks another preparation for that task name. Inspect the saved job for recovery;
a deliberate new take needs a new task name and a fresh quote. A lost write
acknowledgement cannot create a second intent. Restart does not restore a quote or
spending permission. The existing single-use submission and uncertainty rules apply.

References use the saved result manifest's ordered asset IDs after confirmed
remote success. A missing manifest, pending/failed job, changed task or dependency,
other production, cloud-only record or foreign credential/project scope fails.
Local download or Blender application is not required to reuse remote asset IDs;
existing transfer/application failures do not erase a successful remote output.
Literal asset IDs still pass through the selected SDK/model validation and remote
permissions. Upload-task references require the saved association described below.

Recipe text and parameters are not persisted by this binding; the caller must
retain/reload the recipe separately. Quotes are immutable snapshots: approval
rejects a quote whose recipe or production changed and submits the exact
approved request. No recipe field supplies
spending approval, authorizes scene application or infers an API key's project.

## Saved upload tasks

`JobCoordinator.bind_film_upload` associates a recipe's upload task with an
explicitly selected local upload request ID and observed revision. The upload must
already be `imported` in the selected credential scope, with a valid asset ID.
The command does not stage a file, send bytes, initialize/finalize an upload,
refresh remote status or remove sources. Use the existing upload/recovery commands
first. SDK model imports cannot supply asset references.

The shared [job database](JOB_STORAGE.md#film-upload-associations) retains the
association atomically with its task reservation. A model intent and an upload
association cannot reserve the same scope/production/task name. Replacing the
source or changing the task requires a new task name. Repeating the same local
association returns the original record; a new recipe digest from appended tasks
does not overwrite its history. Restart can inspect the association without any
network call or upload replay.

A dependent model quote resolves `$upload-task` or `$upload-task:0` only after
checking the unchanged imported upload again. Pending, failed, uncertain, missing,
stale-revision and foreign-scope uploads fail before estimation. The association
retains the source SHA256, asset ID and kind and rejects a changed observation.
Completed staging cleanup does not invalidate the remote asset; no local byte
read is needed for reuse. Output indexes above zero fail for an upload's single
asset. Existing model schema and service permissions still decide whether that
asset is a valid input for a particular operation.

`JobWorkers` snapshots recipes on the existing queue. `JobSession` captures the
association caller's origin and rejects a changed scene or retired context at
delivery. Repeating an association from another scene returns the same saved
reference to the new reader without retargeting or applying it. The native saved-upload
picker and MCP `bind_film_upload` share this command.

## Native and MCP task controls

Expand **Film** in the Scenario sidebar and choose **Load recipe**. Loading
validates the raw JSON before changing scene data. Reloading preserves the
production identity, including when a title or source filename changes. Save the
blend file to retain both the recipe and identity. **New production** deliberately
starts a separate identity after confirmation; existing saved jobs remain.
Copying a scene or blend file preserves its production identity and task reservations.
Name a new task for another take within that production.

Select one upload task and choose **Use saved upload** to review an already
imported upload. Confirmation records its observed revision and checks the unchanged
scene, recipe, production and selected credentials. Association sends no bytes.
Use the existing reference upload controls first when the source is still local.

For a model task, **Estimate task** resolves its saved dependencies and requests
one exact SDK estimate. **Generate** opens a separate confirmation with the exact
decimal CU cost. Approval consumes the handle before persistence and dispatch.
Recipe edits, a different production/scene or retired credentials reject approval;
the maintenance pump discards obsolete ready estimates, including after frame or
dependency revision changes. Approval checks again before attempting persistence,
so an invalidated price requests a fresh estimate without implying a saved job.
Deleted scene wrappers are
skipped during read-only panel lookup and retired by the pump. Completed quote
and association delivery waits while another scene is current, then rechecks the
unchanged original scene before consuming its completion. Finished tasks are
drained immediately into bounded Film handles, releasing shared session slots
even while delivery waits for that scene. Blender dependency
revision changes, including those emitted during scene activation, still reject
the old quote and require a fresh estimate. **Discard estimate**
releases an unused current quote for repricing. A saved task cannot be spent again,
even with a new quote after an uncertain response or restart.

Current submitted model tasks and associated upload tasks show their saved status
instead of another estimate or association button. A repeated model estimate in
that active context is refused without replacing the saved status. Explicit MCP
upload-association retries still revalidate and return the same saved association;
they never replay an upload.
A different upload or observed revision is rejected before starting another action,
so a conflicting retry does not replace the task's saved association status.
Saved status survives eviction from the 128-action cache. Its separate read-only
projection retains at most the current recipe tasks for each live scene and owns
no worker, approval or reservation authority.

MCP uses `film_recipe` to load/inspect the same scene data and obtain its stable
`production_id`. `estimate_film_task` returns the resolved payload and exact cost;
`approve_film_task` requires that cost verbatim. `discard_film_estimate` releases
an unused approval. `bind_film_upload` requires the context, request and revision
returned by `list_reference_uploads`. Inspection reads local records without
resuming saved jobs. If another scene was current when an MCP response finished,
return to the original scene and inspect again. Inspection completes already-admitted
preparation and exposes an unchanged ready estimate as a `quoted` task, including
its existing `quote_id`, `model_id`, `parameters` and `cu_cost_exact`. Approve that
exact quote or discard it before requesting another. Pending preparation appears
as `quoting` or `binding`; saved upload associations remain inspectable as `bound`.
Inspection never requests a new price, submits, or applies a result, and it omits
quotes whose recipe, scene revision or credential context changed.
See [MCP contracts](MCP.md#historical-upstream-reference).

`FilmJobs` owns bounded presentation handles, not an executor or another store.
Approved tasks enter the existing `ModelJobs` polling/download path. Results stay
saved for explicit application through the existing saved-job controls or MCP.
Restarted/paused jobs require explicit recovery; opening Film does not resume or
resubmit them. These controls do not build scenes or assemble a finished film.

## Native shot and timeline primitives

[`film_scene.py`](../scenario/blender/film_scene.py) constructs one new shot scene
from a raw validated recipe. It preserves the current scene, selection, active
object and timeline. A failure removes only newly created geometry, materials,
images, rigs, actions, camera curves and Worlds; no partially built shot is returned.
Repeated builds create separate takes instead of editing an earlier scene.

The builder uses the recipe's frame rate, editorial duration, camera lens/path,
animated camera target, primitive transforms/colors/roles, placeholder motion and
World settings. Camera paths remain editable Bezier curves. Motion keys map
continuously onto the inclusive shot frame range; distinct subframe keys are not
rounded together. It adds local lighting and a ground plane when the plan has no
environment plane. Shot metadata retains the production identity, recipe digest,
editorial/source timing, declared continuity and placeholder legend.

Each hero needs an explicitly selected `HeroSource` containing a saved GLB result
and its download receipt/path. The existing model importer rechecks bounded,
embedded GLB bytes and packs textures. Preflight verifies every source before
mutation, and import rehashes it again. Actors have independent geometry, rigs,
morph data and animation instances. A hero can specify either `width` or `height`
for uniform, proportion-preserving sizing, or omit both to keep its imported size.
Specifying both fails recipe validation before scene creation. Size normalization,
asset orientation, actor transforms, trajectories and imported hold/loop animation
are separate.
Placement measures evaluated rig/morph deformation at the selected initial pose.
Action stops use the same continuous editorial frame mapping as motion keys.
Hold/loop uses the importer's active clip or first stored NLA clip; other clip data
is retained, but this recipe schema has no named-clip selector. Hero reference and
material-task names remain recipe metadata; the builder keeps the GLB's embedded
materials and does not apply a separate material task.

`build_timeline` composes an explicitly supplied complete set of matching live
shot scenes into a new editable scene-strip sequence, with exact editorial order,
frame boundaries and markers. It rejects foreign production/recipe identities,
removed scenes and changed shot timing before creating data. It preserves the
working scene and never replaces an existing sequence.

These are main-thread local primitives, with no SDK, network, paid submission,
file save, activation control or separate job storage. A receipt proves bytes,
not credential ownership or approval. The shared shot command below supplies the
source/destination checks and application claims. Native/MCP scene-building controls
must use that command; local timeline approval is described below.

## Shared shot application command

`JobSession.film_shots` owns bounded reviews through
[`FilmShotCommands`](../scenario/blender/film_application.py). `inspect` lists
downloaded GLB outputs for the selected scene's recipe and shot. `prepare` requires
an explicit hero-to-output selection with each observed request ID and revision;
it captures the current scene/revision, production identity and exact saved recipe.
Local verification may run in Edit Mode; approval still requires Object Mode
before claiming jobs or building a scene.
`poll` advances local receipt verification on the existing bounded worker queue.
`status` reads a cached review; neither inspection nor preparation builds a scene.

The [source resolver](../scenario/core/jobs/film_sources.py) requires matching
credential scope, production/task identity, model and transitive task digest.
Unrelated editorial changes can reuse an unchanged model task after a fresh review.
Changed task parameters/dependencies, incomplete downloads, uncertain application,
stale revisions and non-GLB selections fail. It never selects an arbitrary first
variant. API keys keep their credential-bound default project; no discovery is
required. Receipt verification is serially admitted per distinct source job, so
hero counts do not require a larger worker pool. A transient other-scene timer
context defers delivery, while finished verification is drained into its bounded
review to release shared session slots. A full queue postpones admission of the
next verification without discarding earlier results. Every later admission
rechecks the recipe, origin and source records; real changes still invalidate
the review. No scene build or paid action is retried by this queue handling.

Separate `approve(review_id)` rechecks the unchanged recipe, destination, source
records, owned verification tickets and GLB bytes. It consumes the review before
claiming every distinct job and before building. Several actors/heroes sharing
one job use one claim; an already applied job uses the existing local model-reuse
claim with the selected asset IDs. The same coordinator/store records the outcome.
A no-hero shot is a local single-use approved action without a fabricated job.

If a later claim fails, known earlier claims are marked failed with no scene build;
the uncertain claim requires inspection. Confirmed full native rollback marks all
known claims failed. Incomplete cleanup retains uncertainty and does not offer a
blind retry. After a successful build, receipt persistence can fail independently:
`retry_receipts` only acknowledges or writes the already attempted outcome and
never calls the builder. It retains the existing shot. Native Undo does not rewind
job receipts; an intentional new take needs another review and claim.

Reviews and receipt handles are owner-local, capped at sixteen, and disappear on
session shutdown. Saved tasks/results survive restart and require fresh destination
review; uncertain durable application remains explicit inspection work. `discard`
retires an unapproved review and drains pending verification without applying it.
After inspecting an uncertain attempt's scene and saved jobs,
`dismiss_uncertain(..., inspected=True)` retires only its review. It refuses
dismissal while a known receipt can still be saved through `retry_receipts`.
Dismissal neither changes saved job state nor repeats scene work: unresolved
application claims still block a fresh review. If a failed claim never reached
storage and the jobs remain eligible, the user can explicitly prepare again
without restarting the session.
There is no new SDK request, download, store or worker pool.
Native/MCP presentation uses the same commands below; full Film workflow and
release acceptance remain separate.

## Native and MCP shot controls

The **Film > Shots** list stores stable shot IDs and titles in the blend file.
Loading a revised recipe preserves selection by ID when that shot still exists.
Choose **Prepare shot**, select one saved GLB for each hero, and confirm to verify
local files. Cancelling the dialog creates no review or application claim.
The application maintenance pump delivers verification even with the panel closed.

A ready review exposes **Build shot** with a separate confirmation naming its
shot, recipe scene and number of saved heroes. It creates a new scene and keeps
the working scene selected. The native operator participates in Blender Undo;
saved job receipts do not rewind, and replaying a consumed review cannot rebuild.
See [desktop evidence](ui/film.md#film-shot-controls). A shot with no heroes
follows the same explicit approval without inventing a generation job.

**Discard review** retires unapproved work. **Save build receipt** retries only a
known persistence outcome. **Acknowledge inspection** requires its checkbox after
the user has inspected the scene and saved jobs; it cannot discard a pending
receipt or release a durable application claim. **Shot error details** provides
the full retained error, including a copyable fallback for an expired review.
Uncertain recovery remains visible for the affected scene/shot after a recipe or
production change, until its receipt or inspection is acknowledged.
Drawing reads cached status without starting sessions,
reading the store, polling workers or changing properties.

Local MCP uses `film_shot_sources` to inspect eligible hero outputs and return
the selected context token. `prepare_film_shot` requires that token, production
ID, shot ID and explicit per-hero job revision/asset selections.
`film_shot_review` polls status, discards unapproved work, retries receipts or
dismisses an inspected uncertain review with `inspected: true`.
`build_film_shot` is the separate explicit build approval. All four use the same
session-owned reviews as the native controls. An MCP build records one Undo
History step afterwards, which [marks the file modified](architecture/blender.md#marking-the-file-modified).
No tool generates, downloads or silently repeats a build.

## Editable timeline approval

Under **Film > Timeline**, **Build timeline** presents one local scene choice
for every shot and the exact editorial frame count/rate. Confirming creates a
new scene-strip sequence and preserves the working scene and existing timelines.
Strips reference the selected live scenes, so later scene edits affect the
sequence. This assembles existing Blender data; it does not generate, download,
render, export, import hero files or change any saved job/application receipt.
Save the blend file to retain the resulting scene.

`JobSession.film_timeline` owns bounded source choices and single-use reviews.
Source discovery accepts only local scenes with matching production, recipe,
shot, camera and timing metadata, including explicitly selected saved/reloaded
scenes. Those editable markers establish compatibility, not service provenance.
Opaque source handles retain live scene/camera identity and the captured scene
revision; names cannot redirect a selection after deletion. There are at most
256 choices per inspection and sixteen reviews. A new inspection replaces old
unprepared choices without invalidating prepared reviews.

Preparation captures the recipe scene and complete selected shot set, including
in Edit Mode. Building still requires a Blender window in Object Mode; the
native build control is disabled outside that context. Approval
rechecks the session, origin, recipe, source revisions, camera references and
timing before consuming the review and constructing the sequence. Confirmed
rollback retains an error; incomplete cleanup retains uncertainty and blocks
another build from that recipe scene until explicit inspection acknowledgement.
Uncertainty takes priority over newer ready reviews in the panel and blocks their
approval too, even after a recipe reload. Ordinary panel status belongs only to
the current production and recipe; prior reviews remain explicitly inspectable. A missing shot scene stops the native dialog with a named build
instruction; a missing selection cannot create a review.
Discarding a review removes its status from the panel without deleting Blender
data; explicit MCP inspection retains its discarded phase and prior error.
There is no remote job to reconcile or receipt to retry for this local
composition action.

MCP follows `film_timeline_sources`, `prepare_film_timeline`, then explicitly
approved `build_film_timeline`. `film_timeline_review` inspects or discards the
review; discarding uncertainty requires `inspected: true`. Native confirmation
calls the same prepare/approve methods. Cancelling the dialog creates no review
or timeline. The native build participates in Undo and an MCP build records one
Undo History step afterwards, while the consumed approval cannot replay. See
[desktop evidence](ui/film.md#film-timeline-controls).

## Local capture foundation

[`local_capture.snapshot`](../scenario/blender/local_capture.py) exports the
selected local scene and its dependencies into a private blend snapshot on
Blender's main thread. It preserves the working file, active scene, frame and
render settings. The caller supplies an existing private storage root and an
explicit still frame or inclusive video range. The snapshot is hashed before
handoff; external resources retain absolute references, so their bytes are not
frozen by exporting the blend file.

On Windows, Python storage retains its extended-length namespace, while paths
passed to Blender use ordinary drive or UNC syntax. Capture paths must fit the
Windows `MAX_PATH` limit, including Blender's temporary export suffix and the
terminating NUL. An overlong snapshot path fails before export with a request
for shorter paths, and removes its new staging directory. Merely stripping the
namespace does not make deep paths compatible with Blender.
The actual child profile and temporary paths are validated before creating the
exclusive start marker. If either is too long, their temporary directory is
removed and the snapshot remains unclaimed, with no frame directory created.

[`local_render.render`](../scenario/core/jobs/local_render.py) is a blocking,
bpy-free worker primitive. It verifies the snapshot and admits that capture
directory once, then runs the bundled
[`render_worker.py`](../scenario/blender/render_worker.py) in an offline,
factory-startup Blender process with script auto-execution disabled. A disposable
profile and temporary directory isolate the child. Inherited Scenario credentials
and Blender/Python path overrides are removed. The child renders Workbench RGB
PNGs with explicit dimensions and material, texture or object colors; it never
registers the extension or uses the parent's UI context. It disables inherited
border/crop, multiview, compositor and sequencer output, enables the snapshot's
first view layer and renders that one layer at the approved dimensions.

Stills require no external media tool. Video checks for installed `ffmpeg` and
`ffprobe` with the [shared lookup](STUDIO_ADOPTION.md#external-media-tools)
before export and again before rendering. It encodes the exact frame sequence as
silent H.264 MP4 and checks dimensions, decoded frame count and frame rate with
ffprobe. No tool is downloaded or bundled. Missing tools fail
before expensive work; encoding failure preserves usable PNGs and logs.
Cancellation, timeout and process failure reap the child and remove its scratch
profile. Snapshot, frames, output and diagnostics remain in the caller-owned
capture directory; the caller must manage their eventual cleanup.
Process logs are checked against an 8 MiB limit and separate probe stdout against
64 KiB, both during polling and after child exit. An oversized final write fails
capture while retaining the diagnostics; polling does not cap disk usage between
checks.

The primitive accepts one to 1,800 frames, frame rates from 1 to 120 and dimensions
from 64 to 4,096. MP4 dimensions must be even. It returns a content hash, size,
media type and timing without attaching or uploading anything. It never pads,
trims or retimes a shot to satisfy a provider. Film shot scenes use editorial
duration; recipe `source_duration` describes the separate generated clip, with
`source_trim` selecting its editorial window.

Completed-media hashing permits the larger of 1 GiB or
`frames * (width * height * 4 + 65536)` bytes: four bytes per pixel and 64 KiB
of encoding/container headroom per frame. This keeps the existing allowance for
small captures while allowing larger validated videos to exceed 1 GiB.
Blend snapshots still have a separate 1 GiB limit, and the render worker stops
after staged PNG frames exceed 2 GiB. These are local bounds, not upload approval
or a guarantee that every maximum-dimension/frame-count combination will fit.

The shared capture command below now owns this foundation for native controls
and local MCP. Local rendering does not approve upload or generation.

## Shared capture and upload approval

Under **Film > Capture**, **Render capture** uses the shot selected in **Shots**.
A missing matching shot scene stops before opening confirmation and asks the user
to build the selected shot. Its confirmation chooses one matching local shot scene,
still/video, Workbench
color mode and dimensions. Stills use the first shot frame; video uses the exact
editorial range and has no audio. The dialog distinguishes that range from the
recipe's generated source duration and trim. Cancelling creates no capture review
or scene snapshot. A prepared MCP review can also be approved here with
**Render prepared capture**.

`JobSession.film_capture` keeps up to eight session-local reviews. Opaque choices
retain live scene/camera identity, recipe binding and revisions for both the
recipe and shot scenes. Preparation validates settings and optional tools;
separate approval exports the snapshot and consumes the review once before
worker admission. Changed origins reject work before and after rendering.
Confirmed approval cannot be replayed after an admission or render failure.

Rendering uses the existing bounded `JobWorkers` queue, with at most one local
render queued/running per session. It adds no executor or durable generation
record. UI closure does not stop it. The main-thread maintenance pump drains
results independently of the panel; a transient other-scene context waits for
the unchanged recipe scene before accepting completion. Actual revision changes
invalidate it. Cancel signals the owned process; credential/file retirement and
shutdown signal it too. Cleanup waits until rendering and upload staging finish.
MCP cancellation also drops a finished result that has not yet been delivered,
without first accepting it into the current recipe scene.

**Open capture** previews the local output. **Upload capture** separately confirms
its dimensions, timing, size and full content hash for the selected connection.
It uses the existing upload runtime. Staging must match that hash before an upload
intent can authorize initialization; replaced bytes cannot be silently uploaded.
The capture review consumes upload approval once. Failed, canceled and uncertain
uploads require inspection in **Inspect uploads**, without replaying approval.
After upload work ends, their local capture review can be discarded while keeping
the durable upload record and staged copy. Once imported, select a Film upload task and choose
**Use saved upload** to bind the selected saved upload to that recipe task.
Neither capture nor upload estimates or submits generation.

**Open capture files** exposes retained diagnostics/frames. **Discard capture**
confirms deleting only that review's private snapshot/media/log directory, after
active work ends. Durable upload records and their staged copies remain.
Session shutdown cleans these local capture directories after joining workers.
An OS cleanup failure logs a sanitized warning and retains its handle without
blocking session retirement. Manual discard reports the failure and can be
retried after files are released; cleanup never restarts a render or upload.
Capture reviews are not restored after restart and never replay automatically;
an interrupted process can leave private files for manual inspection.

MCP follows `film_capture_sources`, `prepare_film_capture`, separately approved
`render_film_capture`, then `film_capture_review` for status/cancel/discard.
`upload_film_capture` requires separate output approval and returns the ordinary
reference-upload handle. Use `reference_upload_status` and, after import,
`bind_film_upload`. These share the native commands. See
[desktop evidence](ui/film.md#film-capture-controls) for still capture; desktop
video capture and live media acceptance remain pending.

## Unpaid composition drafts

[`film_finish.py`](../scenario/core/scene/film_finish.py) adapts Studio's final
and previs composition planning into a pure helper. It validates the raw recipe,
uses the contiguous editorial cut, preserves explicit selected take names, and
builds an unpaid `model_scenario-compose-video` task on a 1920 x 1080 canvas.
Final layers preserve source trim and native audio volume; previs layers use
zero trim and exclude editorial audio. No helper submits, estimates, uploads,
downloads, reserves a take or changes a Blender scene.

`compose_recipe` returns a separate JSON recipe with a new master task. Existing
tasks and the caller's recipe remain unchanged; an existing master name is
rejected. `audio_tracks: []` explicitly omits a score. An omitted track list
retains the recipe contract's looping score at volume 0.4, so its task must be
declared. `required_sources` lists the declared source roles; an undeclared
source raises `FilmSourceError` naming up to five tasks, plus both remedies for
an implied score. `recipe_warnings` reports only an undeclared default `score`
task, conditionally and without failing a load. All final audio needs an `AudioDuration` observation bound to its
selected asset and exact scope.
The caller must obtain that observation from verified saved media; supplying a
value does not prove its duration or validate its bytes. Exact rational seconds
are converted per track: floor for whole-frame loops, ceil for a finite partial
last frame. The same source can serve both kinds without using the wrong
rounding. Duck boundaries preserve loop phase and use absolute gains. The helper
emits explicit segments, rejects finite-source overrun (a `FilmSourceError`
naming the track) and more than 50 combined picture/audio layers, and does not
truncate an excessive mix.
The recipe's 50-shot limit is separate from this combined composition budget.
For example, 49 shots plus one unsegmented score fit; 50 shots plus that score
are rejected before a draft is returned. Fifty shots still fit in previs mode
or with explicit `audio_tracks: []`. Score loops and duck boundaries each consume
additional layers, so reserve room for their expanded segments.

[`film_finishing.py`](../scenario/core/jobs/film_finishing.py) reads the selected
job/upload stores to prepare an immutable draft. Each source must be the exact
current production/task dependency, contain one output of the expected media
kind, and belong to the selected scope. Imported upload observations must match
the saved association and revision. Completed model outputs may be available
without a local download; audio measurement still needs a separate verified
local source. Actual media inspection requires completed local downloads.
A succeeded, downloading or download-failed model result reports that it must
be downloaded first, without probing files or requesting a download. The draft
retains each source's request, revision, asset and task digest. Source resolution
validates the recipe scope and computes all transitive task digests once, reusing
them for every selected source. `validate_composition_draft` rejects changed inputs or a newly
reserved master. Neither helper acquires an approval or an atomic generation
claim. API-key default scope needs no project discovery. A missing, stale or
mismatched source raises `FilmSourceError` naming its task, as does a missing
master for a review that includes it. Its text holds only recipe identifiers.

The resulting model task resolves through the existing Film task command and
SDK `generate.with_raw_response.run_model` path. Its eventual estimate must fetch
current model metadata, validate the payload and preserve the server's exact
cost; this template is not evidence that a provider currently accepts it. The
[composition controls](#native-and-mcp-composition-controls) verify media and
quote these drafts through the session commands below, without installing them
in the recipe, and require explicit generation approval. Native review assembly
and video export have separate [review](#native-and-mcp-review-controls) and
[export](#native-and-mcp-video-export-controls) controls.

## Verified media preparation

`JobSession.prepare_film_composition` snapshots a recipe and runs its local media
inspection on the existing job workers. The coordinator reads the current scoped
source associations, measures each saved file, verifies that picture duration
covers the exact source trim and cut, then rechecks every source revision before
returning an immutable `VerifiedComposition`. Audio duration observations come
from those same receipt-bound bytes. This command creates no job, quote, upload,
download or scene change. API-key default scope still needs no project discovery.

Model results must already have verified downloaded receipts in `ready`,
`apply_failed` or `applied` state. Imported uploads must retain their staged source
bytes. An association whose staging was explicitly cleaned remains usable as a
remote asset but cannot supply a duration to this command; preparation stops
without refetching or reuploading it. Missing, changed, ambiguous or unsupported
media likewise stops before estimation.

[`media_probe.py`](../scenario/core/jobs/media_probe.py) requires an explicitly
installed `ffprobe`, found by the [shared lookup](STUDIO_ADOPTION.md#external-media-tools),
copies at most 512 MiB per source into private storage, and verifies the saved
size/SHA256 while copying. It probes only that copy with
a forced supported container demuxer and the local-file protocol. MOV external
track references and absolute external paths are disabled. Credential/proxy
variables are scrubbed using the existing local-process environment. The command
bounds process time, diagnostic output, stream inventory and timing metadata;
cancellation or retirement terminates and reaps the owned child. Owned probe
scratch is removed on success or failure; original results/staging stay intact.
No binary is fetched or bundled.

Supported MIME types cover MP4/MOV, WebM/Matroska, AVI, WAV, MP3, Ogg, FLAC, M4A
and AAC. M4A accepts both uploaded `audio/m4a` and container `audio/mp4` types.
Ambiguous multiple picture/sound streams are rejected; audio cover art is
ignored. Picture and sound use their own exact rational duration where present;
a container-wide fallback is allowed only for a single primary media stream.
Measurements describe container metadata, not a full decode, constant-frame-rate
proof or human motion/audio acceptance. An absent or unavailable average frame
rate remains unknown; a composition draft can still use the verified duration.
No frame rate is inferred, and missing duration, invalid reported rates or
insufficient cut coverage still fail. A later source replacement cannot alter
the already inspected private copy, but this read does not lock the saved file
or authorize later use of changed bytes.

One local render or inspection can run per worker owner. Session delivery checks
the original scope and scene revision, and retirement cancels work independently
of any panel. The native/MCP composition controls below retain the original
recipe review and require separate generation approval. The quote command below
rechecks the saved sources.

## Quoted composition generation

`JobSession.quote_film_composition` accepts only a `VerifiedComposition` issued
by the same active coordinator for the unchanged original scene. A copied,
reconstructed or modified observation is not an inspection ticket. The command
resolves the prepared master task through the existing Film references and
requests fresh model metadata and the server's exact estimate using the shared
SDK adapter. It does not install a recipe or submit generation.

The immutable source observations travel with the quote and prepared request.
The coordinator rechecks their scope, task digest, asset and saved revision after
schema retrieval, after estimation, before durable preparation and immediately
before claiming submission. The master must remain unreserved until preparation;
a prepared master validates its sources without rejecting its own reservation.
Sources that change while queued stop dispatch. These are current-record checks
at those boundaries, not a transaction locking every source for the request's
entire lifetime. Probing does not freeze the remote asset or certify provider
composition support.

An explicit caller approval still uses `prepare_quote` and ordinary model
submission, preserving the exact decimal cost, payload, captured origin and
single durable master identity. Competing prices and a lost preparation
acknowledgement cannot create a second master. A timeout after dispatch remains
uncertain and never retries automatically. The source guards and inspection
tickets are session-local; restart permits saved-job inspection/recovery, not
resubmission of an old quote. The existing job schema and SDK transport are
unchanged, and API keys need no discovered/default project identifier.

The controls below expose this path without installing or modifying the recipe.
Provider acceptance remains separate work.

## Native and MCP composition controls

Under **Film > Composition**, choose **Final** or **Previs**, then **Prepare
composition**. Preparation verifies retained source media locally; it needs
installed `ffprobe` and never fetches missing sources. **Request price** uses the
verified draft and fresh model metadata. The separate **Generate** confirmation
shows the original scene, master mode, frame count/rate, source/layer counts and
full exact CU price. It creates one saved master job through `ModelJobs`, without
editing the recipe, constructing a sequence or automatically importing results.
Final/Previs selects which review to display and prepare. It is temporary UI state
for each scene in the current Blender session, not a saved scene edit; switching
scenes restores that scene's mode without changing another window's scene choice.
Switching modes preserves each retained review. Recipe and scene changes still invalidate approvals.

Local MCP uses `film_recipe` inspection's `context_id` and `production_id` with
`prepare_film_composition`. Poll `film_composition_review` until `READY`, then
call `estimate_film_composition`. Review its resolved parameters and exact
`cu_cost_exact` before explicitly authorizing `generate_film_composition` with
that unchanged string as `approved_cost`. Native controls and MCP share the same
review handles and submission owner. API-key default scope requires no discovery.

`FilmCompositionCommands` belongs to the existing `FilmJobs` owner. It retains
at most 16 reviews, never evicts active approvals and adds no executor, store or
service client. Main-thread maintenance drains work independently of the panel.
Completed inspection/pricing waits for the original scene to be selected before
delivery; changed recipes, production identities, scene revisions or credentials
invalidate it. Drawing reads cached status without polling, storage or network I/O.

**Cancel preparation** stops a local probe or discards a pending price when the
request ends. **Discard review** releases only the handle after work ends, keeping
source media and saved jobs. Errors remain readable and copyable. A preparation
stopped by a `FilmSourceError` or media inspection shows that reason, such as an
undeclared score task; other failures show generic guidance. A failed or
uncertain submission consumes approval before durable preparation; inspect saved
jobs instead of repeating generation. A lost preparation acknowledgement can
still expose its committed master request ID. No old quote is restored on restart.

The recipe's declared `final_master_task` and `previs_master_task` identities make
saved master jobs visible in `film_recipe` inspection even though preparation
never appended a task to the recipe. **Inspect saved jobs** offers the existing
recovery commands. A new take needs an explicitly different master task identity
or production; discarding a review does not free a spent identity.

See [desktop evidence](ui/film.md#film-composition-controls). Live provider
acceptance and other OS/DPI behavior remain pending.

## Native saved-media review primitive

[`blender/film_review.py`](../scenario/blender/film_review.py) adapts local final
and previs VSE assembly from the selected Studio source. A caller supplies the
selected `JobScope`, production, complete recipe source mapping and optional
master. Each `ReviewSource` pairs a `StoredResult` receipt/path with the exact
`MediaInfo` observation. These objects describe evidence, not owner-issued
approval. The active job owner must still validate current source/task revisions,
original recipe/scene and user approval before calling this primitive.

Preflight validates the recipe, scope, exact task/media roles, receipt/measurement
agreement, picture trim coverage, frame rates and editorial audio segments.
At most 512 MiB per file and 2 GiB per review can be copied; the assembled cut is
bounded to 2,000 picture/sound strips. Each source is copied once per declared
task into a new private `film-review` directory under extension user storage.
Copying checks whole-file size/SHA256 and file identity before native decoding.
No files are downloaded, uploaded or read through a legacy asset-path fallback.

The new scene uses the cut's exact frame range/rate, a 1920x1080 canvas, Standard
view transform and fill-to-canvas picture scaling. Each picture strip receives
its source trim and editorial range; Blender's decoded dimensions, frame rate
and duration must agree with the cut. Silent videos create no sound strip. Native
audio uses the recipe volume and matching trim, ending at its measured source
boundary when it ends before the picture. Editorial loop/duck segments retain
phase, gains and channel assignment. An explicitly supplied master becomes muted
picture/sound alternates; it never replaces the editable cut automatically.
Previs uses its declared takes with no final trims or editorial score.

The direct movie-strip API indexes source frames. This primitive currently
requires the measured movie rate to equal the Film cut rate and rejects shorter
sources rather than freezing or silently stretching them. Mixed-rate normalization
and variable-frame-rate/provider acceptance remain separate work. Metadata and
successful native decoding do not certify human motion/audio quality.

All Blender work is on the main thread. The new scene's view layers are
synchronized before returning so Blender 5.2 can safely copy its scene data.
The working scene, selection, frame, view layer and existing sequences remain
unchanged. Failed builds remove only
their new scene/sounds and private copies. If scene cleanup fails, file copies
remain for inspection so partial strips cannot reference deleted data. Successful
copies persist independently of original download/upload cleanup and must remain
available while the review references them. Saving a blend file does not pack
these movies; the [portable case-study bundle](#portable-case-study-bundle)
gathers them explicitly. The
[offline video export](#offline-film-video-export-primitive) renders the
referenced media into a new MP4 instead.

The standalone primitive is synchronous. The shared preparation and application
commands below move copying and probing to workers and supply current-source
checks and application claims.

## Shared native-review preparation and application

`JobSession.prepare_film_review` queues immutable recipe JSON on the existing
worker pool, sharing its one-local-media-operation admission and cancellation
with capture/composition inspection. No additional client, executor or store is
created. The worker reads current scoped Film task bindings and retained upload
or downloaded-result receipts, hashes independent copies under extension user
storage, probes those exact bytes, and rechecks source revisions and the captured
origin before returning. Picture coverage, media kinds and matching video/cut
rates must pass. The same 512 MiB/file and 2 GiB/review limits apply.

Optional `include_master` requires the saved master to match the current recipe
compiled with these measured source durations and task references. It is copied
as a muted alternate; an unrelated, stale or absent master rejects preparation.
Preparation does not reserve a master, generate, download, upload or mutate jobs.

The coordinator retains at most 16 issued preparations. A copied object or a
different owner cannot consume one. Failed/cancelled preparation removes its
new copies; explicit discard and shutdown after joining workers remove unused
completed preparations. Consumption transfers file ownership to application.
Successful or uncertain native scenes keep those files after session shutdown.
A process crash can leave files for inspection; restart never recreates approval
or automatically sweeps possibly referenced media.

`JobSession.apply_film_review` is a separate explicit approval command. It checks
the issued completion, original scene/origin, production and unchanged recipe,
current source revisions and private copy identities before consuming the ticket.
Generated sources receive the existing durable application claims before native
decoding; already applied results use separate local reuse claims. Imported upload
records retain their upload state. The builder uses the worker's files directly
and checks their regular-file identity, size and timestamps before and after
decoding, without copying or hashing large movies on the GUI thread. This assumes
application-owned private directories; it is not a hostile same-user filesystem
isolation boundary. Native decoding itself still runs on Blender's main thread.

Complete rollback records failed applications and deletes new files. Incomplete
rollback preserves partial data/files and uncertain claims. A lost claim response
never starts the builder. Known receipt-write failures return a session-owned
`FilmReviewOutcome`; `retry_film_review_receipt` retries only those saved outcomes,
never file copying or scene mutation. Restart loses that in-memory receipt
authority and leaves the existing durable inspection state. `discard_film_review`
can retire an unused completion after its original context changes.
Prepared copies remain owned by the session during native rollback. Known rollback
outcomes are saved before file deletion; pending receipts retain those copies until
`retry_film_review_receipt` saves the outcome and then retries cleanup, without
building again. The coordinator owns these unused copies independently of the
receipt handle. Retirement removes them after workers join even if a rollback
receipt remains unsaved, without retrying that receipt or changing durable
inspection state. Dropping a receipt handle cannot abandon file ownership.
Successful or uncertain native scenes never transfer their media to this cleanup.
A deletion failure requires cleanup inspection and never leaves
an otherwise confirmed rollback applying solely because files could not be removed.
If preparation or its final admission fails and file deletion also fails, the
original error is preserved with a cleanup note. The coordinator retains those
unused directories, counts them toward its 16-review bound and retries removal
after workers join at shutdown. Failed shutdown cleanup remains owned for another
shutdown attempt. No failed-preparation directory is a generation or application approval.
The local log names each retained directory once, without copying transport errors.
An inactive session stays registered while unused Film media needs cleanup; enabled
reaping retries at most once every five seconds. Disabling the extension stops its
timer while retaining in-process ownership; registering again resumes cleanup.
Process exit or module reload can still require manual inspection of logged files.

The [native/MCP review controls](#native-and-mcp-review-controls) below present
this command layer with separate preparation and build approvals.

## Native and MCP review controls

Under **Film > Review**, choose **Final** or **Previs**, then **Prepare review**.
Its dialog names the recipe scene and mode. **Include saved master as muted
alternate** is enabled only when the recipe's declared master job has one saved
video result; the shared preparation still validates that master. Confirming
queues the shared preparation above: it copies up to 2 GiB of retained upload
files or downloaded results into private extension storage and measures those
copies with installed `ffprobe`. Video and cut frame rates must match. Nothing is
downloaded, uploaded, generated or claimed, and no scene changes. Cancelling the
dialog creates no review. A saved-job storage error while checking the master is
reported with its storage message, not as a recipe problem. Final/Previs is
temporary per-scene WindowManager state, like the composition selector, so
navigation does not tag the recipe scene.

A ready review shows its frame count/rate and shot/source counts. **Build review
scene** asks for a separate confirmation naming the recipe scene, mode, frames,
fps, shot/source/audio-segment counts, private copy size and master inclusion.
It states that the build creates a new scene, keeps the working scene selected
and marks generated sources applied in saved jobs. It also states that Undo removes
the scene but not saved receipts or private copies. Build is disabled outside
Object Mode in a Blender window; preparation remains available in Edit Mode. Like
the timeline build, the build operator declares only Blender's Undo option, not the
register option behind the Info log and redo panel, because its approval is
consumed once. Preparing and ready reviews show "Selecting, editing or Undo here
discards it".

`JobSession.film_review` owns at most 16 session-local review handles and adds no
executor, store, service client or persisted state. Preparing rejects another
active review for the same scene and mode, and any unresolved uncertain review for
that scene. A full bound retires the oldest finished handle, never an active one.
Runtime maintenance drains finished preparation while the panel is closed. A
review becomes ready only while its recipe scene is current and its captured
origin is unchanged; preparation that finishes while another scene is active is
held as waiting with its copies. The prepared completion stays issued so that the
separate build approval consumes it exactly once. A changed recipe, production,
scene revision (including the current frame) or connection invalidates preparing,
waiting and ready reviews and deletes their unused private copies; prepare again.
Selecting the recipe scene again after leaving it counts as such a change: Blender
runs `frame_change_pre` for the newly selected scene and the shared session revokes
its captured origin. A waiting review therefore fails on a real return through the
scene selector, as does every unbuilt review for that scene. Any dependency update
of the recipe scene counts too, such as selecting, adding or editing an object.
An Undo or Redo also counts: the shared session's `undo_pre`/`redo_pre` handler
resets every captured origin, so every unbuilt review fails on the next poll.
Building one review is followed by such an update, so another unbuilt review for
that scene, such as one for the other mode, fails on the next maintenance poll;
prepare and build one review at a time. Final and previs reviews can both be ready
for one scene until one of them is built. Build a ready review before leaving its
recipe scene. Drawing only reads cached status.
Preparation occupies the session's single local media slot, shared with capture
and composition inspection, and one shared worker while it copies and probes.

**Cancel preparation** stops the local probe; a late result never becomes ready
and its copies are deleted. **Discard review** deletes only an unbuilt review's
private copies; saved jobs, source media and scenes remain. A build rejected before
consumption, for example after a source change, deletes its copies without a claim.
Once consumed, outcomes follow the shared command above. A complete rollback saves
failed claims. A known receipt-write failure offers **Save review receipt**, which
never copies media or rebuilds. Uncertainty without a known receipt requires
**Acknowledge inspection** with its checkbox. Dismissal retires only the review; it
never clears a saved claim or deletes data. An uncertain review blocks new
preparation for its scene until its receipt is saved or it is dismissed. No build
is retried automatically, and
**Error details** shows the complete error for copying.

A preparation that fails shows its reason when it is first-party: a
`FilmSourceError` names the missing, stale or mismatched source task, track or
master, including the undeclared score of a recipe that omits `audio_tracks`,
and a media inspection error names the tool or measurement. Any other failure,
during preparation or the main-thread check before READY, shows "Inspect saved
Film media, frame rates and the declared master before preparing again" and logs
one warning on `scenario.film_review` with only the exception type and the file,
line and function that raised it. Exception text can hold private paths, so it is
never shown or logged. A changed recipe, scene or connection still reports the
invalidation message without a log.

Local MCP uses `film_recipe` inspection's `context_id` and `production_id` with
`prepare_film_review`. Poll `film_review_status` until `READY`, show the returned
timing, counts, size and master inclusion for explicit approval, then call
`build_film_review`. The status tool also cancels, discards, retries a known
receipt or dismisses an inspected uncertain review (`inspected: true`). Native and
MCP controls share the same handles: an MCP-prepared review can be built natively
and the reverse. See [MCP review commands](MCP.md#historical-upstream-reference).

Restart drops review handles and pending receipt authority, as described above;
saved jobs keep their durable claims for inspection. Undo after a build removes
the review scene while its private copies and applied receipts remain, so repeated
build and Undo cycles accumulate copies under `film-review` without an automatic
sweep. The strict origin invalidation above is deliberate. Composition and capture
waiting use the same shared origin check; letting scene activation keep an
unchanged origin is separate session work.

[Installed tests](../tests/blender/test_film_review_controls.py) call the Undo/Redo
handler directly rather than stepping Blender's undo history, and evaluate the
dependency graph explicitly where a window would after a click or build. The
[release evidence](maintenance/release-acceptance.md#film) records a desktop pass
and a production run through local MCP; other OS/DPI behavior and human
motion/audio review remain pending. Built review scenes export through the
[export controls](#native-and-mcp-video-export-controls).

## Offline Film video export primitive

[`local_capture.export_snapshot`](../scenario/blender/local_capture.py) writes
one live local sequencer scene to a private blend snapshot on Blender's main
thread, in Object Mode. It rejects scene, movie-clip and mask strips, requires an
integer frame rate from 1 to 120 and defaults to the scene's frame range, output
size (truncating the resolution percentage as Blender's own output does, then
rounding each side down to the even size H.264 needs) and
whether any sound strip is audible: not muted itself, by its sequencer channel or
by an enclosing meta strip or that strip's channel. It synchronizes
that scene's own view layers before `libraries.write`, which avoids the Blender
5.2 copy crash seen for new inactive scenes. A second synchronization with nothing
in between is a no-op. A window evaluates only the scenes it shows, though, so a
scene no window shows keeps depsgraph tags from changes elsewhere (removing any
datablock requests a relations rebuild of every depsgraph) until the next
synchronization, which then reports an update although its data is unchanged.
Callers therefore synchronize inside `JobSession.synchronizing`, which does not
let that evaluation revoke the scene's origins. Every distinct external movie,
sound, image and font file is stamped once by path, size, modification time,
device and inode, so the media budget counts files rather than strips; packed
sounds and fonts travel in the snapshot. The working file, selected scene and
frame are unchanged. Bounds are validated before any file is written, and a
failed snapshot removes its new `film-export-*` staging directory.

[`local_export`](../scenario/core/jobs/local_export.py) is the bpy-free export
primitive. A specification allows at most fifteen minutes of frames (900 seconds
at the scene rate, at most 108,000), even 64 to 4,096 pixel dimensions and at
most 2,000 media files totalling 16 GiB. The default render timeout is five
minutes plus two seconds per frame, capped at six hours; verification scales with
the frame count up to two hours. `render` hashes the stamped media cancellably,
admits the snapshot once (a reused snapshot is refused before hashing), then runs
the bundled [`film_export_worker.py`](../scenario/blender/film_export_worker.py)
in an offline, quiet, factory-startup Blender child with script auto-execution
disabled, a disposable profile and the same scrubbed environment as capture. The
child uses Blender's built-in FFmpeg encoder: MPEG-4 container, H.264 at
Blender's high quality constant rate factor, no autosplit, sequencer output only
and 100% resolution at an explicit 8-bit colour depth, with stereo 48 kHz AAC
at 192 kb/s or no audio track. It prints a progress line every
`max(1, frames // 100)` rendered frames and after the last one; the parent reads
only the last 4 KiB of the log. The parent stops the child when the staged file
exceeds 16 GiB or the staging volume falls below 64 MiB free, since the
free-space preflight is only an estimate. Exactly one `output/film.mp4` is
accepted, and its final hash stops when the export is cancelled.

Verification always reads the MP4 container with the bpy-free
[`mp4_inspection`](../scenario/core/jobs/mp4_inspection.py) reader, which parses
box headers and movie metadata only. It requires one H.264 track with an 8-bit
4:2:0 profile (Baseline, Main, Extended or High), the exact dimensions, sample
count and constant frame duration, and exactly one stereo 48 kHz AAC track only
when audio was requested, with no other tracks. Presented audio and picture
durations may differ by at most one frame or 50 ms, whichever is larger. The
file is then decoded:

- When the [shared lookup](STUDIO_ADOPTION.md#external-media-tools) finds an
  installed `ffprobe`, `-count_frames` must report the exact decoded frame count,
  `h264`, `yuv420p`, the frame rate and the expected AAC stream.
- Otherwise a second offline, factory-startup Blender child decodes the file with
  Blender's own FFmpeg. It checks the movie's dimensions, rate and length and the
  sound's sample rate, channels and length. It renders the first and last frames
  twice over two sentinel colours; identical results show the opaque movie frame
  was decoded. This fallback does not decode every frame or report the pixel
  format, so it is weaker than the ffprobe check. Missing ffprobe never blocks an
  export, and the receipt records which verification ran.

Media stamps are rechecked after verification; a change during rendering rejects
the export. No tool is downloaded or bundled. A Blender build without FFmpeg is
refused before snapshotting and again in the child. Blender lists every codec
whatever its linked FFmpeg libraries can encode, so a build that lacks an H.264
or AAC encoder is not detected in advance: its render child fails, surfacing as
the generic export process failure with `render.log` retained in staging. File
system errors in staging or verification reports become path-free export errors.

`validate_destination` requires an absolute, visible `.mp4` path with a portable
name: at most 255 UTF-8 bytes, no control characters, none of `<>:"|?*\/` and no
Windows device stem such as `CON` or `COM1`, checked after removing trailing
spaces as Windows does (the rules of Python 3.13's `ntpath.isreserved`). The name
must not exist, including as a dangling link, its folder must exist, and the
resolved folder must lie outside the caller's private roots, each of which must
be absolute. Containment compares Windows extended and ordinary spellings as equal
and folds case and Unicode composition on every platform, so a folder that differs
from a private root only in spelling is refused even on a volume where it is a
different folder. The result uses that resolved folder. `reserve` creates the
destination exclusively as an empty placeholder before rendering, so permission
problems surface before a long encode. On POSIX the reservation keeps the
placeholder open until it is released or replaced, so a file recreated at that
path cannot reuse its inode number; Windows keeps no handle, and NTFS file IDs
carry a reuse sequence number. `publish` first checks that the destination volume
has room for the exact staged size plus 64 MiB, then copies the verified staged
bytes to a hidden fixed-length `.scenario-<hex>.partial` file in the same folder,
so any valid destination name fits. It hashes the copied bytes, syncs the file,
compares size and SHA-256, checks that the placeholder is still the same empty
file and that the export was not cancelled meanwhile, then atomically replaces it.
Failure or cancellation removes only its own partial file and placeholder, never
another file, and keeps the staged output. `ExportPublishError.staged` allows a copy-only
publish to another approved destination. After reserving, a free-space preflight
assumes about 25 Mbit/s at 1080p, scaled by pixel count, plus 64 MiB, doubled
when staging and destination share a volume; a disk that fills later still fails
cleanly.

`JobSession.export_film` and `publish_film_export` validate the destination on
Blender's main thread before any thread, placeholder or media hashing starts, and
`export_film` refuses a specification whose child is not the running Blender
executable with the bundled export worker.
Their private roots are the snapshot's staging root, the extension's user
storage, the installed extension folder and Blender's user resource and
extensions folders, so every UI or MCP caller gets the same policy; a reused
snapshot is refused there too. `LocalExportWorker` refuses a relative path before
starting its thread, and the coordinator re-checks the portable shape before
reserving. Each approved export runs on a session-owned `LocalExportWorker`
thread. It never occupies a shared job worker or the capture/review local media
slot, so polling, downloads, capture and review preparation continue during a
long export. One export or re-publish runs per session at a time. The coordinator
checks that the owner is active and both the working and exported scene origins
are current before reserving the destination. A later scene edit does not cancel
the export, because the snapshot and media stamps bind the bytes. Owner
retirement (credential or project change, file load or extension disable) cancels
the export, reaps the child and releases the placeholder; retirement after
rendering keeps the staged output unpublished. Session shutdown waits at most five
seconds in total for the cancelled export thread, enough to reap a child. A media
or destination read blocked on an unresponsive mount cannot be interrupted, so the
daemon thread is then left running with a logged warning and the session stays
owned until the reaper finds it finished. That late finish stops at its next
cancellation check: it starts no child, registers no staged export and replaces no
placeholder unless that final rename was already under way. If Blender exits
first, the empty placeholder can remain. `drain` returns a
`local_export` completion, and `deliver_local_export` consumes it without
resolving a scene, only while the session is active.
`publish_film_export` re-publishes only a staged export issued by the same owner,
without rendering; it checks the active owner but not the origins its caller
passes, which the receipt carries as given. `ExportTask.progress()` reports the media, render, verify and
publish phases. Each successful publish writes one local log line with the
destination basename and SHA-256.

Export handles and receipts are session-local; no job store or blend schema
changes. The caller owns the staging directory and removes it with
`discard_staging`; the published video is the durable artifact. A crash can
leave staging under extension user storage and a zero-byte placeholder or hidden
partial file next to the destination. There is no startup sweep.

[Unit](../tests/unit/test_local_export.py) and
[installed](../tests/blender/test_film_export_primitive.py) tests include a real
child export with Blender's decode check; a build without an H.264 or AAC encoder
is not exercised. The [native/MCP export controls](#native-and-mcp-video-export-controls)
call these session commands; the [case-study bundle](#portable-case-study-bundle)
uses the same thread and publish step. Human playback review and Windows/Linux
desktop evidence remain separate. Export makes no Scenario request, spends
nothing and writes no job record.

## Native and MCP video export controls

**Film > Export** follows **Review** in the sidebar and on Studio's Film
**Review** page. **Export video...** lists the local review scenes built for the
current production and unchanged recipe: scenes tagged as a downloaded final or
previs review whose production ID and recipe digest match. Timeline, foreign,
changed-recipe and library scenes are excluded. Inspection synchronizes each
candidate's own view layers, as the snapshot later does, and captures its origin;
neither synchronization counts as an edit of that scene.
Blender's file browser then opens at `<Output Folder>/Film`, which this explicit
action creates when missing, with a portable suggested name
`<Title> - <Mode> Review.mp4`, numbered when that file already exists. Its side
panel lists the review scenes with frames, rate, size (the snapshot's default:
Blender's truncated output size rounded down to even), media count, audible sound
strips and any blocking reason: no strips, a rate that is not an integer from 1 to
120, more than fifteen minutes, scene, clip or mask strips, missing media or the
media budget. **Audio** matches the sound strips by default, or always includes or
omits an AAC track; **Use review scene size** can be turned off for explicit even
dimensions. A scene size outside 64 to 4096 pixels is not a blocking reason: the
browser opens with that option off and the nearest bounded size, and preparing
that scene size, natively or through MCP without both dimensions, is refused
with the size and a request for an explicit one. Confirming the file browser
validates the destination with the session policy and prepares a READY export;
nothing renders.

**Render video** is a separate confirmation. It repeats the review scene, frames,
rate, duration, size, audio track and media count, wraps the full resolved
destination and names the verification that will run (installed `ffprobe`, else
Blender's weaker decode check), from the same lookup the export uses. Confirming
approves that exact destination once.
The command requires Object Mode, the selected recipe scene, both captured
origins and an absent destination. It rereads the review scene's frames, rate,
size, strip and media counts, audible sound strips and blocking reason shown when
preparing, refusing any change, then writes the private snapshot and starts the
session-owned export thread. A different destination string, an agent
approval of a destination outside the Output Folder, Edit Mode or another running
export refuses without consuming the approval; once those checks pass, the
approval is consumed even if the export cannot start. The panel shows
the media check, rendered frames and percentage, verification and saving;
runtime maintenance advances and redraws it while the panel is closed.
**Cancel export** stops the child, the destination stays absent and the private
files remain until discarded.

An EXPORTED handle shows the file name, **Open folder** and **Copy SHA-256**; its
status adds the size, SHA-256 and verification used. When rendering and
verification succeed but saving fails, the handle keeps the verified private
video: **Choose new file...** selects another new file and **Save video copy**
confirms that exact file, copying without rendering again. **Error details** shows
the complete path-free error. **Discard export** (or **Discard private files**
after an export) deletes only the private snapshot, staged video and logs; an
exported video stays where it was saved.

A READY export binds the recipe scene's and the review scene's captured origins.
A frame change, object selection or edit, an Undo or Redo, or leaving either scene
and selecting it again, including viewing the review scene, turns it into ERROR,
as does the destination appearing meanwhile; prepare again. Blender evaluates a
review scene that no window shows only when something updates it explicitly, such
as the export's own synchronizations. Their evaluation cannot tell tags from
changes elsewhere apart from an edit, so it does not revoke the origin: an edit
made to that scene without showing it, for example from a script, is caught only
by the approval's reread of what preparation showed. Once rendering starts, later edits do not stop it, because the snapshot
and media stamps bind the bytes. Session retirement (credential or project
change, file load or extension disable) cancels a running export; shutdown
removes every handle's private staging once the export thread has stopped.
`JobSession.film_export` (`FilmExportCommands`) keeps at most eight session-local
handles, one open export per review scene and one export or copy running at a
time. A full set retires only finished handles that own no private files.
Handles add no executor, store, service client or persisted state; restart drops
them, and the exported file is the durable artifact.

Local MCP uses `film_export_sources(production_id)` for the context, sources,
`default_directory` (`<Output Folder>/Film`), encoder availability and expected
verification. `prepare_film_export` takes that context, the production, a
`source_id` and an absolute new `destination_path`, with optional even
width/height and `include_audio`. Agent destinations must resolve inside the
configured Output Folder after following links, compared part by part without
folding case or Unicode composition, although Windows resolution returns an
existing folder's on-disk spelling. Only the missing default Film folder, with a
missing Output Folder above it, may be created. Show the
returned destination and settings for approval, then call
`render_film_export(review_id, approved_destination)` with that destination
verbatim, like an exact cost. Poll `film_export_review`; it also cancels,
discards, and after a failed save uses `prepare_republish` for a new Output
Folder destination, then `republish` with the returned `pending_destination`
verbatim. Native file browser choices are limited only by the session policy
above. Native and MCP controls share the same handles, but agent render and
copy approvals recheck the Output Folder: an agent cannot approve a file chosen
natively elsewhere, which stays for native confirmation. See
[MCP export commands](MCP.md#historical-upstream-reference).

[Installed tests](../tests/blender/test_film_export_controls.py) run one real child
export; the others replace the child with a fast handoff while the real publish
runs. The [release evidence](maintenance/release-acceptance.md#film) records an
earlier desktop pass and a production run through local MCP; a desktop rerun of
export, cancel, **Open folder** and **Copy SHA-256**, other OS/DPI behavior and
human playback review remain pending. This MP4 export does not replace the
[portable case-study bundle](#portable-case-study-bundle).

## Portable case-study bundle

**Film > Case study** follows Export in the sidebar and on Studio's Film
**Review** page. It packs one built review scene, optionally the director timeline
with its shot scenes, their media, the sanitized recipe, a production ledger, a
manifest and an opening guide into one new ZIP archive. This adapts the retained
case-study export of Emmanuel de Maistre's Scenario Studio (see
[Studio adoption](STUDIO_ADOPTION.md#film-case-study-bundle-adaptation)); unlike
that source it never rewrites the working file's own paths, and it removes
service identifiers from the ledger. The MP4 export does not substitute for it.

**Export case study...** opens Blender's file browser at `<Output Folder>/Film`
with a portable suggested name `<Title> - Case Study.zip`, numbered when taken.
Its side panel lists the recipe's built review scenes (the same tags as the video
export) and its director timelines: local scenes tagged as an editable shot plan
for this production and validated recipe whose scene strips still hold the
current shot scenes in recipe order. **Include director timeline** adds the chosen
timeline and its shots. Each choice shows any blocking reason. Confirming the file
binds the choice and a destination resolved by the session policy, with a `.zip`
suffix; nothing is written. **Save case study** is a separate confirmation that
repeats the review scene, timeline and shot count, the scene and media counts and
bytes, the ledger's approved tasks and quoted total, and the full destination.
It needs Object Mode, and an approval is consumed once, as for the video export.

The chosen scenes and everything they use travel together. Scene strips are
allowed only in the timeline. Linked or overridden data, movie clips, cache files,
volumes, clip or mask strips, packed sound strip audio, sound strip modifiers or
retiming, image sequences, movie or tiled textures, multi-file packed images,
missing media, more than 2,000 media files or 16 GiB of them, and more than 256
scenes are refused with a reason before any approval. A scene in which a sculpt or
paint mode was used references Blender's linked Essentials brushes, so it is
refused as linked data until those brushes are made local.

Approval writes a private snapshot of the chosen scenes on the main thread, with
absolute media paths, and stamps every external movie, sound, image, image strip
frame and font they read. On the session's export thread the files are copied into
private staging and hashed. An offline Blender child then edits only that
snapshot: it points every external reference at `//Media/<NNN kind>.<ext>`,
rebuilds sound strips so their hidden legacy path is relative too (keeping timing,
settings, their own properties, animation and drivers), repacks packed images
under neutral `//Packed/` names, renames packed fonts and sounds the same way,
sets each scene's output path to `//render/` and removes Scenario properties that
name a job, asset or production while keeping descriptive tags. It also removes
the extension's saved settings from every bundled data block: Blender 5 stores
registered properties apart from custom ones, and composer lanes keep reference
uploads' request and asset IDs, concept jobs and local reference paths, while the
Film form keeps its production and raw recipe. Rewritten path fields are filled
with the longest value Blender stores in them (1,023 characters of a path, 767 of
a folder, 255 of a file name, and both for a movie strip) before their final
value, so an earlier path is not left in their unused bytes. Every rewritten path
uses `/`; Blender converts a file's paths to the native separator when it opens
it, so the same bundle opens on any system. It writes the scenes as an
uncompressed data library with fake users. A second offline child reopens that
library and reports its scenes, file references and remaining Scenario properties
and settings; the export requires exactly the chosen scenes, only bundle-relative
Media references that exist, neutral packed names, no libraries and no private
properties. The library and every document are scanned for the production,
credential scope, request, job, asset, upload and application identifiers of the
approved tasks, extension storage, the source media folders and every
identifier-like value of the removed properties and settings, in data blocks, their
node trees and master collections and strips, through nested groups, lists and
byte strings. Descriptive settings are not scanned for: model choices, parameter
and input names, kinds and values, titles, labels, prompts and object names, which
the recipe or the scenes carry anyway. Any match refuses the export. The library
scan reports its own progress, so a long scan shows "Checking the library for
private data" with a percentage rather than the finished verification.

The archive holds one top folder, `<Title> Case Study`, with the `.blend` file,
`Film Recipe.json`, `Production Ledger.json`, `Case Study Manifest.json`,
`Open The Case Study.md` and the `Media` folder. Media are stored as they are;
other members are deflated. Every member is reread against its SHA-256 before the
archive is hashed and saved without overwriting, exactly like the video export:
placeholder reservation, verified copy, copy-only re-publish after a failed save,
cancellation and discard of private staging. The working file, scenes, frame and
live media paths never change.

The approved tasks follow Studio's selection: the current final and previs master
takes, the score or named audio tracks, hero reference, mesh and material takes,
each shot's style, previs and video takes, dialogue tasks, and their `$task`
parameter dependencies, limited to recipe tasks and saved observations. For a
recipe that omits `audio_tracks`, the score is the task the review was built
with, which its recorded audio mix names, not an assumed `score`; a review whose
audio mix cannot be read is refused until it is built again. The
ledger lists, per approved task, its title, kind and recipe model, how it was
obtained (generated, selected cloud result, uploaded or not saved), the saved state,
the exact quoted cost and the result media types, plus the total quoted cost in
CU. It omits credentials, URLs, request, job, asset, upload, project and team
identifiers, local paths and generation parameters. The recipe keeps only
approved tasks, drops its project, credential and identity fields, and omits URLs
and Scenario-shaped service identifiers in free text; it must still validate as
a recipe, or the export is refused. Recipe identifiers and model IDs stay so the
recipe can be loaded again, including a private trained model's ID. The manifest
records the format, generator, Studio attribution, scenes, approved tasks,
continuity audit, the `.blend` and media sizes and SHA-256 receipts and the
opening steps, which the guide repeats with extraction and privacy notes.

A ready case study binds the recipe, review, timeline and shot scenes' captured
origins; the video export's invalidations apply to each of them. Approval also
rereads the review scene's frames, rate, size and strip count and recounts the
scenes, media files and bytes and the ledger's approved tasks, priced tasks and
quoted total; if any differs from what preparation showed, for example because a
media file or a saved job changed without touching a scene, the approval is
consumed and nothing is written. Inspection and the snapshot synchronize the
chosen scenes inside `JobSession.synchronizing`, as the video export does, so an
edit of a scene no window shows is caught only by these rereads. A running export
keeps its snapshot.
`JobSession.film_case_study` (`FilmCaseStudyCommands`) keeps at most eight
session-local handles and one open case study per review scene. Video exports and
case studies share the session's single export thread: while one runs, approving
the other refuses without consuming it.

Local MCP uses `film_case_study_sources(production_id)` for the context, sources,
timelines, `default_directory` and `default_name`. `prepare_film_case_study`
takes that context, the production, a `source_id`, an optional `timeline_id` and an
absolute new `.zip` `destination_path` inside the Output Folder, under the video
export's agent folder policy. Show the returned destination and contents, then
call `export_film_case_study(review_id, approved_destination)` with that
destination verbatim. Poll `film_case_study_review`; it also cancels, discards,
and after a failed save uses `prepare_republish` and `republish`. See
[MCP case-study commands](MCP.md#historical-upstream-reference).

[Unit](../tests/unit/test_local_case_study.py) and
[installed](../tests/blender/test_film_case_study.py) tests build a real bundle,
reopen the extracted archive in a fresh offline Blender and check that no
archived byte holds a private identifier or the source, storage or output
folders. Blender keeps unused bytes of earlier settings in some fixed-size fields
that this export does not rewrite, such as the sequencer's last-used folders; the
bundle can carry them, so review it before publishing it widely. The
[release evidence](maintenance/release-acceptance.md#film) records a bundle saved
through local MCP; the desktop save, other OS/DPI behavior, Windows paths and
human review of a bundle remain pending.

## Remaining integration and evidence

Mixed-rate normalization and provider-specific preparation remain separate work.
Keep useful source capabilities and tests as those paths are connected; do not
describe this helper adoption as a completed Film workflow.

[Film job](../tests/unit/test_film_jobs.py),
[upload association](../tests/unit/test_film_uploads.py),
[recipe](../tests/unit/test_film_plan.py) and
[scene-plan](../tests/unit/test_film_scene_plan.py) tests cover these contracts
offline. No Film model task has run live; physical desktop interaction and human
playback review continue in #440.
