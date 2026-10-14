# Blender job contexts

`scenario.blender.job_session.JobSession` owns one explicitly selected SDK
connection, scoped store, coordinator and worker pool. Extension registration
installs file, dependency, frame, undo and redo hooks; it creates no session,
connection or worker. This is the integration boundary for the shared runtime.
The active runtime creates one selected session lazily for local MCP recovery
inspection, prepared-intent cancellation, and every UI/MCP submission with its
quote, upload and result delivery. Pending files, captures and Prompt Spark
preparation require separate completion before a native form can obtain its
final quote.

`runtime.ensure_job_session()` binds the session to the catalog's credential-local
scope and store. The job owner gets a separate SDK HTTP pool using the same
explicit credentials and worker-safe online-permission event. Catalog retirement
disables both pools' network permission; each owner closes its own pool only after
its in-flight work finishes. A catalog preview quote is not a session-issued quote
and cannot be reused for shared paid dispatch.

Credential changes and runtime reset deactivate the session without waiting for
network I/O. The existing session registry retains in-flight work so its receipt
can finish in the original store. GUI timers and the main-thread MCP loop reap
retired sessions after completion. Closing a panel does not retire the session.
File loading retires the selected session through the existing lifecycle hooks.
The next recovery call creates a fresh owner and context token for the same store.

The local MCP tools `list_local_jobs` and `cancel_prepared_job` perform no service
requests. Inspection returns exact saved costs and recovery suggestions, not new
spending approval. Cancellation needs the current context token and observed
record revision, and only accepts an unclaimed prepared intent. Resetting, loading a file or
switching credentials invalidates the context token, even if another scope has
the same request ID. Shared saved-job controls below also expose this
prepared-intent cancellation, known model-job cancellation and download/receipt
recovery. These recovery tools do not import prototype jobs. Model submission
creates new durable intents.

Active Image jobs poll and download through this session. `apply_images` requires
an owned verification completion, resolves the original scene/revision, and claims
`applying` before loading any image. Every byte snapshot must match its saved
receipt and supported container before Blender decodes and packs it. Multiple
variants are imported together; failure removes only newly created images.
Confirmed rollback records `apply_failed`; incomplete rollback or uncertain
receipt persistence retains `applying`. `retry_image_receipt` can acknowledge a
known completed import without importing again, using its owner-local handle.
These methods never rebind a restarted job by scene name. Explicit recovered
image imports use a separately approved destination as described below. The Image facade exposes
receipt-only retry to UI/MCP while its original owner retains the outcome handle.

## Film model task command

`JobSession.quote_film_task(recipe, production_id=..., task_id=..., origin=...)`
queues the shared [Film quote command](JOB_COORDINATOR.md#film-model-task-quotes).
The caller captures its scene/target origin before admission. The existing bounded
queue copies the recipe; normal main-thread delivery and `prepare_quote` reject a
changed scene or retired credential context. The bound intent uses the same
`submit` and recovery paths, with its production/task identity committed before
network dispatch. The shared [Film task controls](FILM_PLAN.md#native-and-mcp-task-controls)
persist recipe/production identity in scene properties, bind approvals to their
exact values and discard obsolete ready quotes, including captured frame/dependency
revision changes. Approval rechecks before persistence and does not replace a
submitted task's saved status with a new estimate attempt. The session command still operates
on a snapshot; no additional worker or job engine is introduced.
Finished quote and association tasks leave the shared completion queue immediately.
Their bounded Film handles retain the outcomes until the unchanged source scene
can receive them, so an unselected scene cannot occupy shared admission slots.

`JobSession.bind_film_upload` queues the local upload-task association with an
explicit observed upload revision and captured caller origin. It shares normal
admission, recipe snapshotting and delivery guards. A saved association survives a
late rejected callback; it never applies to the changed scene. Repeating the same
association from another scene delivers the unchanged saved reference to that new
reader. Imported-upload eligibility is checked through the selected coordinator's
upload store, with no remote read, staging or upload replay.

## Film shot application

`JobSession.film_shots` shares this session's receipt workers and coordinator
application claims. Its [shot review contract](FILM_PLAN.md#shared-shot-application-command)
binds explicit GLB selections to the current recipe scene, production, scoped task
digest and observed job revision. Inspection and asynchronous verification are
separate from single-use approval. Every distinct result job is claimed before
the new shot scene is built; repeated actors do not duplicate a job claim.

Deleted scenes, real origin changes, changed recipes and retired credentials reject
delivery/application. Temporarily different timer contexts defer delivery to the
unchanged source scene. Finished verification leaves shared session
slots even while its review waits for the original scene. Temporary admission
failure preserves earlier verification and retries only the unqueued local read,
after rechecking the recipe, origin and sources. Confirmed rollback records failure;
partial claims or incomplete cleanup remain uncertain. Known success/failure
receipt writes can be retried without repeating scene work. Explicitly acknowledging inspection can
dismiss an uncertain review only after known receipts have been saved; it never
clears an uncertain durable claim or rebuilds a scene. Restart requires a fresh review, using the
same durable results and explicit application recovery policy. The command is
shared by the [native and MCP shot controls](FILM_PLAN.md#native-and-mcp-shot-controls).
The maintenance pump advances its verification independently of an open view.
Drawing only reads matching cached reviews and hides retired credential contexts.

## Film capture ownership

`JobSession.film_capture` owns the [capture reviews](FILM_PLAN.md#shared-capture-and-upload-approval)
shared by native controls and MCP. It captures the recipe origin and selected
shot/camera origin, validates the local scene snapshot on the main thread and
queues `render_local` on the existing workers. `LocalCaptureResult` includes
both origins and the selected scope; ordinary `drain` checks scope/recipe identity
and `deliver` accepts it only in the unchanged original scene. The capture owner
also checks the shot revision and camera identity. A transient context mismatch
waits; invalidation prevents delivery and upload.

The normal maintenance pump polls capture reviews independently of UI lifetime.
At most one queued/running local render occupies a session; capacity and
completion bounds still apply. A consumed approval cannot be replayed after
admission failure. Cancellation and owner retirement signal the child; shutdown
joins workers before removing the owned snapshot, media and diagnostic directory.
Captured output is session-local, with no new persistent registry or restart replay.

Separate upload approval calls the existing reference facade with the rendered
content hash. Source staging validates it before creating an upload intent.
The capture directory stays alive while upload staging/transfer runs, and discard
does not remove durable upload records or their separate staged copies.
An imported upload requires its own Film task association; no automatic generation
or scene application follows capture or upload.

## Film video export ownership

`JobSession.export_film` admits one approved [offline video export](FILM_PLAN.md#offline-film-video-export-primitive)
on the session-owned export thread after checking the active session, the
selected working scene and the exported scene's captured origin. On the main
thread it then validates the destination: absolute, visible, portable, new and
outside the snapshot's staging root, the extension's user storage, the installed
extension and Blender's user resource and extensions folders. A reused snapshot
or a specification whose child is not this Blender running the bundled export
worker is refused too, so nothing starts a thread, placeholder or media hash for
a rejected request. `publish_film_export` applies the same destination policy.
Capture and review preparation keep their separate local slot on the shared
workers. `cancel_film_export` signals the child; session retirement cancels it,
and shutdown waits at most five seconds for the thread before workers and the
coordinator close. A thread still blocked on file access keeps the session owned,
including its saved-result preview copies, until the reaper finds it finished;
its late outcome is never delivered. The reaper skips a session while its export
task is pending, so its timer ticks never wait on the blocked thread.
Completions drain as `local_export` outcomes; `deliver_local_export` consumes one
without resolving a scene, because export never mutates Blender data. Like the
asset library, workflow catalog and cloud read deliveries, it refuses once the
session is inactive, even while a failed shutdown still holds issued outcomes. A
publish failure carries its verified staged output, and `publish_film_export`
copies it to another approved destination without rendering. Handles, receipts
and progress are session-local; no job store, blend property or restart replay
is added.

`JobSession.film_export` is the session-owned `FilmExportCommands` behind the
[native and MCP export controls](FILM_PLAN.md#native-and-mcp-video-export-controls).
Inspection captures each review scene's origin after synchronizing its own view
layers; preparation captures the selected recipe scene's origin and binds both
with the resolved destination. Approval rechecks both origins in Object Mode,
rereads the review scene's shown bounds, snapshots on the main thread and calls
`export_film`. Both synchronizations run inside `JobSession.synchronizing`: a
window evaluates only the scenes it shows, so an unshown review scene can hold
tags from changes elsewhere, such as a datablock removal, and their evaluation
must not revoke its origin. Other scenes and other updates still revoke origins.
Runtime maintenance drains the export task and delivers it with
`deliver_local_export`, never through a scene callback. A frame, recipe or object
change, Undo/Redo or scene reselection invalidates a ready export, while a running
export keeps its snapshot. Shutdown closes the handles, removing their private
staging, only once the export thread has stopped.

`JobSession.export_case_study` admits one approved
[case-study bundle](FILM_PLAN.md#portable-case-study-bundle) on the same export
thread with the same checks, a `.zip` destination and a specification whose child
is this Blender running the bundled case-study worker. `publish_film_export` also
copies a verified staged archive, keeping the `.zip` suffix. `JobSession.film_case_study`
(`FilmCaseStudyCommands`) extends the export handles: inspection also captures
each director timeline's and shot scene's origin, a ready case study binds them
with the recipe and review scene origins, and approval rereads the review bounds,
then snapshots and builds the sanitized documents from this scope's saved jobs and
uploads on the main thread. Its synchronizations also run inside
`JobSession.synchronizing`.
Video exports and case studies refuse each other's approval without consuming it
while one runs. The handles close with the export handles once the thread has stopped.

## Film media preparation ownership

`JobSession.prepare_film_composition` captures the supplied current origin and
queues local saved-media inspection in its existing workers. Scratch lives under
`bpy.utils.extension_path_user(..., path="state")`; workers never use `bpy`.
`drain` checks `VerifiedComposition` scope/origin, and `deliver` separately checks
the selected scene and its revision before exposing the draft once. A completed
probe cannot deliver to a changed scene. The existing local cancellation handle
and retirement signal stop owned processes; no panel owns their lifetime.

`quote_film_composition` queues a fresh exact price for the same issued inspection
and original origin. The ordinary `drain`/`deliver` checks apply to that quote;
`prepare_quote` and submission retain the composition source guards in the
coordinator. [Composition review](#film-composition-review-ownership) presents both
to native and MCP controls; they do not install a new recipe or approve spending.
See [media prerequisites and limits](FILM_PLAN.md#verified-media-preparation).

## Film composition review ownership

[`FilmCompositionCommands`](../scenario/blender/film_composition.py) is owned by
`FilmJobs`, using its existing session and `ModelJobs` for preparation, exact
quotes and one-time master submission. It retains the original scene and recipe
binding throughout; no recipe installation or automatic application changes the
submission origin. Main-thread maintenance waits for the original scene before
delivering a completed probe/price and invalidates changed origins or recipes.
Native controls and four MCP tools share these bounded ephemeral handles.
The native Final/Previs selector is temporary WindowManager state, so navigating
between modes does not tag the recipe scene or invalidate its retained quote.
The shared scene-revision and recipe guards remain unchanged.
Cancellation/discard never deletes source media or saved master jobs. Declared
master task IDs remain locally inspectable after restart even when the original
recipe had no master task row. See [composition controls](FILM_PLAN.md#native-and-mcp-composition-controls)
for the approval, recovery, scoped desktop proof and remaining provider acceptance
boundaries.

## Local Film timeline approval

`JobSession.film_timeline` retains bounded live source choices and single-use
reviews for [editable scene-strip assembly](FILM_PLAN.md#editable-timeline-approval).
It shares session retirement and scene revisions without adding workers, SDK
calls, storage or job claims. Explicit local scene choices are checked again
before mutation; deletion, camera/timing changes, changed recipes and inactive
sessions reject approval. Session shutdown releases its source/review handles.
Saved Blender scenes remain available for new explicit inspection after restart.
Partial cleanup must be inspected before dismissing uncertainty and preparing
again. No saved job or result receipt is changed by timeline assembly.

## Cloud result adoption command

`JobSession.adopt_cloud_job(identifier, expected_model_id=..., scene=...)`
captures the selected scene on the main thread and queues the
[shared cloud read](JOB_COORDINATOR.md#adopting-a-completed-cloud-job).
It captures no mesh target, mutates no scene data and creates no second worker
pool. `deliver_cloud_read` consumes only a cloud-read completion issued by the
same active session, after checking its job/model identity and credential scope.
Switching, editing or removing the reader's scene does not discard this metadata.
Repeating a read from another scene preserves the original saved intent.
This delivery path returns a saved record only; it cannot deliver quotes or
apply results. Ordinary delivery and result application retain their scene,
revision and destination-approval guards. Retiring credentials or loading another
file still prevents late delivery through the old session.

Once saved, cloud records survive restart and use the existing inspection,
explicit download and fresh destination-approval commands. Status distinguishes
`source: cloud` from `generation`; cloud costs remain unknown (`None`), never a
synthetic zero. The native recovery view omits the missing cost. A cloud record
cannot recover original mesh-source ownership from remote metadata.

`ModelJobs.recover_cloud` connects native history and MCP to this command.
Pending reads of the same remote job/model share one task. Up to 16 retained
reads expose pending/sanitized failure state, and the normal application pump
attaches a successful saved-job view in paused recovery state. Closing the panel
does not own the worker. A retired credential context cannot deliver a late read
into its replacement. No adoption read joins automatic image application.
The 16-entry limit belongs to the display cache: a completed read's owning MCP
caller retains its result even after cache eviction or a later read of the same
job. Completion checks the original facade's ownership and active session.

Shared recovery rows and prototype updates use the same stable display merge.
A scoped row replaces a colliding legacy row without duplicates, and repeated
pump ticks preserve order. The existing 50-row prototype limit only trims
prototype rows; it never drops shared recovery rows or changes durable records.

## Blockout plan commands

`BlockoutJobs` is a presentation facade over the selected session, not another
worker or store. Design and Refine capture the original scene revision plus
prompt, refinement, type, scale and previous plan before quoting. Refine includes
the complete bounded prior plan, without text truncation, using an explicit
update instruction rather than describing the refinement note as a new scene.
An absent, malformed or incomplete prior plan is rejected with a guided error
before a quote request, including through MCP.
UI and MCP `estimate_blockout` / `approve_blockout` share exact cost approval and
persist an intent before one paid dispatch. A repeated approval cannot resubmit;
uncertain retained actions require saved-job inspection.
At the 32-action limit, requesting a new quote can reclaim a finished presentation
handle without changing saved jobs or scene plans. Pending commands, unapproved
quotes and unconfirmed or uncertain jobs retain their handles.

The application pump polls the known job, lists its single plain-text output and
reads the complete body through `JobSession.read_model_text`. Delivery validates
scope, original scene/revision and unchanged inputs before storing a normalized
array of 1–200 elements. Complete arrays may have Markdown fences or surrounding
prose; malformed/truncated arrays are rejected without recovering a partial
prefix. Delivery never creates geometry. A scene switch, edited plan, file load
or retired owner cannot redirect a late result to the current scene.

Build plan stages local geometry before replacing the scene's explicitly stored
collection pointer. It does not adopt collections by name. Unmarked additions,
externally linked objects and shared collection trees block destructive rebuild
or Clear; a staging failure removes only the new tree and preserves the old one.
Clear requires native confirmation. Build and Clear are native undo operators.
Undo can also restore the earlier plan snapshot; Redo restores its plan and
geometry without submitting again.

Saved model jobs remain available after restart or a text-read failure. MCP
`read_model_text` checks the current credential context and observed revision and
returns complete text without changing a scene or spending again.
These guarantees do not establish live provider or integrated release acceptance.

### Explicit saved-plan destination

`BlockoutRecovery` uses that same session for asynchronous saved-plan reads. It
loads a missing manifest, requires one plain-text result, reads the complete body
and parses the bounded plan before offering approval. The review captures a new
selected scene, its revision and all Blockout fields; it never rebinds an old
scene by name. Up to 16 reviews retain text only in memory. A pending read cannot
be duplicated for the same job/destination.

Native saved-job controls expose **Read saved Blockout plan**, followed by
**Use saved Blockout plan**. The dialog names the scene, element/group counts and
whether an existing plan will be replaced. Cancellation discards its handle.
MCP `prepare_blockout_plan`, `blockout_plan_status` and `apply_blockout_plan`
use the same read, review and single-use approval commands. Status can discard
a finished review without mutating Blender data. A deleted destination is skipped
by saved-job drawing and returns an unavailable-scene error from status; a new
scene with the same name never inherits its review. Preparing another review
reclaims deleted destinations once their pending reads have been drained, so
inaccessible reviews cannot permanently fill the 16-review limit. Drawing and
status queries do not perform this cleanup.

Application rechecks the selected context, saved job revision, destination
revision and unchanged fields before consuming the handle and replacing only
`plan_json`. It does not build geometry, change the job's application receipt or
persist model text in the registry. Saving the blend preserves the chosen plan.
Build remains the separate explicit local geometry operation. The native use-plan
operator declares Undo; desktop interaction acceptance is recorded separately in
[the UI guide](ui/recovery.md#saved-blockout-plan-recovery).

## Origin and quote lifetime

On Blender's main thread, capture inputs together with
`origin = capture(scene, target)`, then request the estimate.
`prepare(estimate, origin=origin)` validates that same origin and persists its
opaque file-session, scene and optional object identities in the durable intent.
It never stamps an old quote with a fresh revision after estimation.
Names, active-object selection and file paths are never used to rediscover a
missing target. `capture` also exposes this origin for callers preparing inputs.
Removed RNA scene/target references are reported as `OriginUnavailable` at capture
and delivery boundaries; callers do not need a separate stale-RNA error branch.
Dependency updates conservatively invalidate the affected scene's revisions;
frame changes invalidate unconditionally in their own pre-change hook, regardless
of whether the unevaluated depsgraph lists updates. Catalog loads fill and
restore generation forms without RNA update callbacks, and so does a trained-model
project default applied automatically to a form without an explicit choice, so
neither causes such an update; see [catalog delivery](architecture/sdk-runtime.md#active-sdk-catalog).
Explicit selections and edits that assign a property with an RNA update
callback, such as the model or a LoRA strength, still invalidate. A default's
**Use** always reprices its form but invalidates only when it assigns a
different base model or a LoRA strength that way; a composition used on the
form's current base changes only the form's trained list.
Undo/redo and file loading
invalidate captured state too. Recording an undo step does not: the
[file-modified mark](architecture/blender.md#marking-the-file-modified) after a
Scenario change tags no data and runs no history handler. The one exception is
`JobSession.synchronizing(scenes)`, which a main-thread caller opens while it
evaluates those scenes' own view layers: a scene that no window shows keeps tags
from changes elsewhere until such an update, which then reports a change although
its data is unchanged. Inside the block, main-thread dependency and frame updates
to a named scene revoke none of that scene's origins. Origins share one revision
per scene (`OriginRevisions`), so this keeps every origin captured in it,
including origins other features captured, not only the caller's; callers
recheck the content they approved instead. Other scenes, updates outside the
block, deleted scenes and targets, render-thread callbacks, Undo/Redo and file
loading still invalidate. Every main-thread scene callback and reaper tick
prunes removed captured scenes and deleted object wrappers, including when a surviving
scene has no dependency updates. Deleted targets invalidate the scenes that captured
them and their registry entries are dropped; live targets in other scenes retain
their origins. The target pass reads captured RNA references, proportional to captured
target count; it does not scan every object or rediscover targets by name. Unlinked
but still live objects remain recorded, while delivery still requires originating-scene
membership. Reused object names never rebind a deleted target. This is an event-level guard, not synchronous interception of every
scene deletion; work already durably claimed remains in flight.
Render-thread frame/dependency callbacks invalidate only the thread-safe revision
registry conservatively across sessions; they never inspect or mutate bpy data. Callers
must prepare inputs and capture their origin together on the main thread.

A payload that does not come from the scene, such as a file the user chose to
upload, binds only to the scene it is for. `capture_destination(scene)` records a
destination-only origin: dependency, frame and render-thread updates keep it, while
removing that scene, Undo/Redo, file loading and deactivation revoke it. Delivery
still requires that scene to be selected, and callers recheck their own destination,
such as the form slot or workflow input. Such an origin never vouches for scene
content: the coordinator refuses to prepare a paid request with it, so quotes and
spending keep the revision binding above. Scene snapshots (viewport, camera and
render-result stills, clips, selected meshes and Film captures) keep ordinary origins.

A thread-safe, bpy-free revision registry is checked again by the coordinator
inside the submission claim, with its lock held through the durable SUBMITTING
write. Invalidation is serialized against that boundary; it cannot slip between
the origin check and storage commit. A queued
quote whose origin was invalidated cannot spend. Work already claimed can finish
and persist to its originating scope. The optional `origin_guard` context-manager factory on
`JobCoordinator` must be thread-safe and must never access Blender. The guard covers local
SQLite work only, never HTTP; invalidation may briefly wait for that commit.

The coordinator can apply this same pure revision guard to configured upload
preparation and mutation claims; see [upload commands](SDK_UPLOADS.md#shared-worker-commands).
The optional session upload configuration below uses this boundary. Inspection
and explicit upload refresh do not rediscover or rebind targets.

File identities are deliberately session-local. Restarted records stay available
for recovery, but automatic application cannot assume an old file or target is
unchanged. Explicit recovery captures a new destination for review; matching a
scene or object name is insufficient.

## Results and lifecycle

`submit`, `refresh_remote` and `cancel_remote` return task handles. `drain()` returns completed
outcomes on the main thread without waiting for network work. Per-task errors
for invalid worker origin/scope or malformed results do not drop successful
neighbors from the same drain. Undrained outcomes count against a separate
admission limit, so a closed view cannot accumulate unbounded completed payloads.

`deliver(completion, callback)` validates the issuing session, active scope,
origin revision, selected scene and continued target membership immediately
before calling `callback(result, captured_scene, captured_target)`. Successful
outcomes can be delivered only once, even when the callback fails. This guard
never performs import itself or marks a job APPLIED: durable import transactions
and error/retry UI must be layered above it. Failed or stale results are available
for caller review; they never silently fall back to the current selection.

Account/project switching must explicitly deactivate the previous session before
creating its replacement from authoritative credentials/scope. File loading does
this automatically. A main-thread timer closes inactive owners after tracked work
finishes; an explicit headless loop can call shutdown directly. Extension disable
joins every session before unregistering Blender services. HTTP waits retain the
worker timeout limitations; shutting down is not remote cancellation.

Once every owned worker has exited, session-local outcomes and registry ownership
are released even if SDK connection cleanup raises. Direct `shutdown()` callers
still observe that exception. The background reaper and extension unregister
isolate ordinary failures per session, log a fixed message without transport
exception details, and continue servicing or cleaning up the other owners.
Unregister removes timer and lifecycle hooks even after session cleanup failures,
allowing the remaining extension registry cleanup to proceed. Control exceptions
continue to propagate; a session with live workers retains its ownership.

The selected API-key context and worker-safe online snapshot are bound by the
active runtime as described above. The account scope is a local pseudonym, not a
guessed server account ID.

## Recovery inspection and cancellation

`recovery_plan()` is a main-thread, read-only view of the selected connection's
saved jobs and suggested actions. It does not rediscover Blender targets, change
records or submit requests. Restarted records retain their original origins.

`cancel_prepared(request_id, expected_revision=...)` durably cancels an unclaimed
local intent immediately on the main thread. It bypasses the worker queue and
completion-admission limit, so a full queue cannot delay cancellation until after
a queued submission spends. The queued submission subsequently fails its stored
state check. An already claimed request or stale revision fails explicitly; local
cancellation does not promise remote cancellation.

`cancel_remote(request_id, expected_revision=...)` uses the same bounded pool and
completion queue as refresh/submission. Its original origin is loaded from this
connection's scoped store, never captured from the currently selected scene.
The [coordinator's model-job cancellation contract](JOB_COORDINATOR.md#known-model-job-cancellation)
still governs eligibility (documented `inference` jobs only), the durable
single-action claim and authoritative status polling. A canceled acknowledgement alone cannot report terminal success;
an uncertain response remains recoverable by refreshing the known ID without
replaying the action. A running workflow cannot be canceled; rejecting a
[waiting workflow step](#waiting-workflow-steps) is the only way to stop one.
`ModelJobs.control` answers `cancel` on a running (`remote`) workflow job with
`WorkflowCancelUnavailable`, which says so, before its revision and action
checks and without queuing a command. A prepared workflow job that was never
sent keeps its local `cancel_prepared` action.

Cancellation, refresh and recovery inspection deliberately do not require the
old scene/target to remain available. This allows explicit cancellation and
reconciliation after deletion or restart. Their completions still carry the
original stored origin; `deliver` continues to reject unavailable, stale or
unrecognized origins before Blender application. The Image facade exposes these
methods through the saved-job controls below; account scope remains the explicit
credential-bound local identity rather than a guessed discovery result.

## Stored result retrieval and verification

The optional `result_downloader` and `result_root` constructor arguments forward
an explicit [storage policy and private root](RESULT_TRANSFERS.md) to the shared
coordinator. Supply both together; incomplete configuration fails before workers
start. The application owner must choose trusted storage hosts, provide a
thread-safe online-access snapshot and retain the private directory through
application. No default production host policy is selected by the session.
Without storage configuration, download and verification report a configuration
error without changing the stored job, including a reopened READY record.

On the main thread, `load_results`, `download_results`, `recover_downloads` and
`verify_results` queue
the corresponding [coordinator commands](JOB_COORDINATOR.md#result-retrieval-and-download-commands)
with the original stored origin and expected record revision. Metadata and signed
transfer work run on the existing pool. Like refresh and cancellation, these
commands remain available after a scene switch, target deletion or restart;
retrieval does not grant permission to apply into a new Blender context.

`drain` validates both the record inside `VerifiedResults` and ordinary result
records against the issuing origin/scope. Verification returns a frozen result
containing a tuple of verified local paths. `deliver` still requires the original
current scene/target and permits the completion to be used once. Receipt
verification does not lock file bytes, run an importer or mark a job APPLIED;
callers must preserve private storage ownership. The explicit application
commands below consume these completions. Download failures remain
`DOWNLOAD_FAILED` for explicit retry, while local verification failures do not
trigger another download or generation.
Explicit interrupted-download recovery only reconciles verified local receipts
and durable state under the cross-process transfer lock. A restarted session can
recover those downloads, but its completion cannot rebind an old scene or target.

## Saved-result preview ownership

The optional `preview_root` constructor argument gives the session a
[result preview scheduler](RESULT_PREVIEWS.md) over its own coordinator and the
workers' dedicated preview lane. The runtime supplies `cache/result-previews`
under extension user data; if it cannot create that directory, the session still
runs jobs without previews. On the main thread, `result_previews` returns the
scheduler only while the session is active and configured; otherwise it raises
`OriginUnavailable`. Previews are keyed by the saved job, asset and receipt, not
by a captured origin: they work after a scene switch or restart and never grant
scene or application authority. The review assets of a waiting workflow step are
keyed by the saved workflow job instead and requested with `request_review`;
`runtime.load_review_previews` binds that request to the context, saved
revision and step `review_id`, and the runtime-owned
[review previews](RESULT_PREVIEWS.md#workflow-review-assets) object, cleared
when jobs retire, turns ready stills into icons in the GUI pump. It decodes them
in [temporary Blender data](architecture/blender.md#temporary-preview-decoding),
never by loading and removing a `bpy.data` image, so the dependency handler sees
no update and origins captured in the scene stay current.

The optional `preview_waveform` argument is the
[offline audio decoder](RESULT_PREVIEWS.md#offline-audio-envelopes)
specification. The runtime resolves it on the main thread from
`bpy.app.binary_path` and the installed `waveform_worker.py`, or passes `None`
when Blender has no usable executable; audio envelopes then fail with that
reason. The decoder runs in a separate process started from the preview lane, so
it neither touches this Blender's data nor holds its Python lock.

The existing session maintenance timer, and `reap_retired` in headless loops,
call `service_previews` to collect results and queue due polls or cache maintenance. Preview
tasks are not session completions, so `drain` never returns them. `deactivate`
closes the scheduler and cancels its lane work, but an SDK metadata read already
in flight cannot be interrupted. The timer therefore shuts a retired session
down only once its preview lane is idle as well as its tracked tasks are done:
the session stays registered meanwhile, and the main thread never joins
preview I/O. After `shutdown` joins the workers, it removes private copies still
owned by outstanding decode requests. Extension disable or exit calls `shutdown`
directly and still waits for a running preview command; a running audio decode
is signaled first, so its process is terminated, or killed three seconds later.
Worker threads cannot call these methods.

`prepare_playback` queues a [private player copy](RESULT_PREVIEWS.md#private-player-copies)
of one saved result on the same preview lane and returns its task; it is not a
session completion either, and `cancel_playback` signals it. The main-thread
[saved media controller](../scenario/blender/result_media.py) owns the copies:
it opens the latest one in the system player, or starts Blender's animation
player (`-a`) in a separate process. The system player starts through
`core.jobs.playback.system_open` (`open`, `xdg-open`, or an isolated Python
helper calling `os.startfile` on Windows), never `wm.path_open`; both launches
strip `SCENARIO_`, `PYTHON` and proxy variables from the environment Blender
passes. It reuses
an unchanged copy of the same receipt, keeps at most four copies or 1 GiB plus
any copy a running animation player or system player launcher uses or the
pending Play reuses, reports a launcher that exits with an error, and allows two
animation players. For **Play audio** it starts one owned offline Blender
process that plays the copy through Blender's audio engine
([in-extension audio playback](RESULT_PREVIEWS.md#in-extension-audio-playback)),
pins that copy until the process exits, and reads its phase from memory in its
timer. The process's own watcher thread, not the main thread, reads its reports,
kills it when it overstays and removes its directory. Another Play audio,
**Stop audio**, retirement, a file load and unregistering stop it, and
unregistering waits for it; a process that survives even the kill is kept with a
warning while its watcher keeps reaping it. A copy that arrives for a replaced
Play audio or a retired context never starts one. For **Waveform** it asks `result_previews` for the envelope
rendition only, so no server still is polled, and polls its status from a timer;
Cancel, and selecting another result's waveform while one loads, call the
scheduler's `cancel`, which terminates a running decode. A late copy or envelope is discarded unless the
context token, session and receipt are unchanged. Retirement, a file load and
unregistering clear the selection and remove copies no running player reads,
including copies evicted for a Play whose lane command, which removes them
first, has not run: deactivation cancels a queued command.

`world_sizes` queues the coordinator's header read of saved World candidates on
the same preview lane and returns its task, which is not a session completion
either. The model job owner queues at most one such read at a time from its
maintenance poll, for at most one preview batch of downloaded candidates whose
current receipt has no read size; the poll that records it queues the rest. It
keeps the declared sizes in memory only for the jobs in its views, forgetting a
job's sizes once it leaves them, and stops offering an image whose size is not
2:1; see [World candidates](WORLD_APPLICATION.md#saved-result-ui-and-mcp-approval).

The main-thread [preview controller](../scenario/blender/result_previews.py)
is the scheduler's Blender-side owner for
[automatic previews](RESULT_PREVIEWS.md#automatic-previews-in-blender). Its own
persistent timer requests the still rendition of a few downloaded results for
the first saved jobs that Jobs and Generations draw, reading each saved record
once per revision, or again to repeat a request the scheduler refused after a
delay growing from 5 s to a minute; it never requests envelopes or clips. A
failed redraw is logged and does not stop the timer. It answers
`decode_requests` one per tick, decoding the private copy inside
`bpy.data.temp_data()` with the shared
[temporary preview decode](architecture/blender.md#temporary-preview-decoding),
which leaves captured origins current, and passing the PNG to `finish_decode`,
or calling `discard_decode` with a reason. It builds icons only from rehashed
cache files, in its own preview collection. It binds its tiles to the context
token and session it requested them in: a different token or session,
`retire_jobs`, a file load and unregistering release its icons and tiles, so a
late status is never drawn. Background Blender registers no timer, so headless
loops decode no images and their decode requests expire.

Local MCP [`inspect_result_previews`](MCP.md#historical-upstream-reference) uses the
same scheduler from Blender's main thread. It checks the context token, saved
revision and previewable state, requests the stills of every result the preview
controller's `tile_targets` ranks, which the tiles page through four at a time,
or of one named result, and an envelope
only when asked, and calls the scheduler's `retry` or `cancel` for one result;
a retry also calls the controller's `forget_failures`. A bounded wait takes a
scheduler `watch`, which the maintenance timer or headless loop sets while
pumping and `deactivate` releases. The HTTP thread waits on it and closes it
when the wait ends, so the next pump forgets a watch whose delivery never runs;
the final statuses are read on the main thread only if the context token,
session and saved revision are unchanged.

## Explicit saved-result World application

`apply_world(completion, asset_id=...)` consumes an owned `verify_results`
completion on the main thread. The caller explicitly selects one saved asset as
an equirectangular panorama; neither its name nor its aspect ratio establishes
that projection. The command requires the original current scene/revision and
continued membership of any captured target. It never falls back to selection,
names or another file session. An invalid selection does not consume the outcome.

Immediately before mutation, it consumes the completion and acquires the
[durable application claim](JOB_COORDINATOR.md#durable-application-claims).
The [World primitive](WORLD_APPLICATION.md) checks the selected saved receipt
against the exact decoded bytes and its saved media type against the actual
PNG, JPEG or OpenEXR container. Other results from a multi-asset job remain
available on disk; this command applies only the selected asset, then marks the
job APPLIED. It performs no service call, download, generation or blend-file save.
`AppliedWorldResult` contains the final stored record and the primitive's guarded
restoration handle as `application`. APPLIED records the completed local scene
assignment, not a saved file, undo entry or complete generation workflow.

A known parsing/application failure becomes APPLY_FAILED only when the original
World binding and both World/image allocation sets are unchanged after the
primitive's cleanup. A fresh verification completion can then support an explicit
local retry. The consumed completion can never be reused. Stale revisions,
unavailable origins and interrupted claims do not trigger retries or rebinding.

Unexpected exceptions, failed rollback/allocation cleanup and persistence errors
raise `WorldResultUncertain`; control exceptions propagate. A durable APPLYING
claim is never automatically reset. If persistence fails after successful scene
assignment, the exception retains its `application` restoration handle. The saved
record may already be APPLIED if its commit succeeded before the error. Inspect
both scene and record; never repeat or undo the mutation merely because a write
failed. Guarded restoration is an explicit caller action and does not rewrite
the durable record. As with all primitive handles, discard it after file load,
undo or extension shutdown. SQLite and Blender do not share an atomic transaction.

When scene assignment completed and only its receipt failed,
`retry_world_receipt(outcome)` accepts the exact `WorldResultUncertain` raised by
this session. It retries or acknowledges only the original successful receipt,
using the coordinator's saved outcome evidence. It never reads or mutates a scene,
reapplies a World, restores anything, verifies files, downloads or generates.
It returns `AppliedWorldResult` with the saved record and original restoration
handle, then consumes the pending outcome. A failed retry raises the same sanitized
outcome so callers can retain it for another explicit attempt. Fabricated, foreign,
consumed and shutdown-session outcomes are rejected, as are errors from uncertain
scene mutations or failed cleanup. The pending handle is caller-owned and is not
reconstructed after restart. Origin invalidation alone does not block bookkeeping;
session shutdown discards pending receipts. A later explicit restoration is left
intact: APPLIED acknowledges the prior assignment, not the current World binding.

`apply_world` itself has no UI or MCP entry point; both reach the same primitive
through the [recovered panorama destination](#recovered-panorama-destination-and-restoration).

## Shared asynchronous estimates

After capturing inputs and origin on the main thread, `quote_model` or
`quote_workflow` queues metadata retrieval and exact SDK estimation on the same
session worker pool. Its completion carries an `OriginQuote`, not a stored job;
no intent is persisted merely to display a price. `drain` checks its scope and
origin, and `deliver` rechecks the selected captured scene/target as usual.
`prepare_quote` accepts that unchanged quote after selection and persists its
original origin once. Scene changes during metadata/estimation reject the quote;
the caller must recapture inputs and request a new estimate. The quote cannot be
rebound through the older direct-estimate preparation method. These APIs do not
authorize paid dispatch by themselves.

## Asset organization reviews

Collection and tag changes alter Scenario metadata, not Blender data, so they
are bound to the session's connection rather than a scene. `JobSession`
admits `organization_snapshot`, `organize` and `collection_page` to its existing
workers on the main thread and records no scene origin for them. `drain`
accepts only a snapshot or result of the session's own scope, or a collection
page, for each issued task. `deliver_asset_organization` consumes that completion
once from the active session without resolving a scene; the generic `deliver`
and the asset library delivery refuse it.

[AssetOrganization](../scenario/blender/asset_organization.py), attached as
`session.asset_organization`, is the single entry point for the native Library
and local MCP. `prepare` validates the request immediately and queues the fresh
read, returning a review ID. `poll` moves finished worker outcomes into the
[pure reviews](JOB_COORDINATOR.md#asset-organization-commands); the runtime pump
calls it, and MCP can wait on `task(review_id)` off the main thread before
polling. `status` and `discard` return a JSON-safe projection without URLs,
owner IDs or service text, including the per-asset change, the number of writes
apply sends when each succeeds and the no-credit, no-undo notice. `apply` consumes a READY
review once; if the command cannot even be queued, the review returns to READY
because nothing was sent. `collections` and `take_collections` read one page
of collections without thumbnails or owner IDs; like `poll`, a failed read
reports only text written for these surfaces, never the worker's own error text.
A finished page nobody takes within the 10-minute review lifetime, for example
after a local MCP request stopped waiting, is discarded unread by `poll`, so it
cannot hold a session admission slot; taking it later asks for a new read.

A review moves from PREPARING to READY, UNCHANGED (nothing to change) or
REJECTED, then from READY to APPLYING and FINISHED or NOT_SENT; it can also be
DISCARDED or EXPIRED. Any review except an APPLYING one can be discarded; a
discarded FINISHED review keeps its result, which still describes what Scenario
changed. A READY review expires after 10 minutes. At most 32 reviews
are kept, evicting only finished or discarded ones, and only one review applies
at a time per session. A worker failure after apply is reported as an unknown,
unconfirmed outcome, never as unsent. Undo and scene switches keep reviews.
Loading a file, a credential or project change, or a reset retires the session
as before: its reviews are discarded, queued work is cancelled unsent, a write
in flight finishes but the guard refuses later ones, and the late outcome cannot
be delivered to the replacement session. Inspect the assets in the Library of
the right connection; nothing is resent automatically. The local
[MCP organization tools](MCP.md#historical-upstream-reference) use this owner and accept a
review only with the `job_context_id` issued alongside it, so a replaced session
rejects an old context. The native Library's
[Organize dialog and review card](ui/studio-and-library.md#library-organization) use the same
owner; `review(review_id)` returns the raw review state, including read-back
records, so the view can update its loaded rows without another request.

## Upload references

The session optionally accepts `upload_store`, `upload_sources` and
`part_uploader`, forwarding the complete configuration to its existing
coordinator and bounded worker pool. Their scope and explicit storage-host policy
must satisfy the [upload contracts](SDK_UPLOADS.md#shared-worker-commands).
The session primitive has no default production host allowlist;
partial configuration raises `TypeError` before starting another worker owner.
With all three dependencies supplied, a mismatched upload/job scope still raises
`ValueError`; the existing source and transfer-policy validation also applies.

Capture the scene/target origin with the chosen source before calling
`prepare_upload(source, origin=..., kind=..., content_type=...)`; a file the user
chose takes a [destination-only origin](#origin-and-quote-lifetime). Main-thread
admission checks that origin and completion capacity before queuing staging.
`initialize_upload`, `transfer_upload_part` and `finalize_upload` load the
persisted record through public `inspect_upload`, resolve its original target at
admission, and queue the existing command with the supplied expected revision. The core
origin guard checks again before intent persistence and mutation claims, so a
queued command cannot use a stale captured revision. No source read, storage PUT
or service call runs on Blender's main thread.

`inspect_upload` and `upload_recovery_plan` are synchronous main-thread metadata
reads. They remain available when completion capacity is full and do not require
the old scene/target to exist. `refresh_upload` likewise queues an explicit status
read using the saved origin, allowing recovery after target deletion or restart.
It never transfers another part, recreates initialization or repeats completion.
Unknown requests in another account/project are absent, not rebound to the
current connection or selection.

`cancel_prepared_upload(request_id, expected_revision=...)` is synchronous on the
main thread and bypasses worker/completion queue capacity. It cancels only local
PREPARED intent in the active selected scope. Target deletion, origin invalidation
or restarting the session does not prevent this local bookkeeping. The returned
record is immediate; no completion is queued and no scene mutation, source read,
file deletion or service request occurs. Initialization races are decided by the
durable revision claim: already claimed or uncertain uploads cannot be canceled
through this command. Inactive sessions reject it. It does not provide remote
abort.

`restart_upload(request_id, expected_revision=..., origin=...)` queues the
explicit [upload restart](SDK_UPLOADS.md#part-plan-source-and-restart-limit) on
the existing workers. Admission requires a current captured origin for the
replacement but not the old scene or target, so it works after restart. The
completion carries the new PREPARED record under that origin. The reference
facade reuses the original origin while it still resolves to the selected scene
and target, and otherwise captures the selected scene.

`discard_upload_source(request_id, expected_revision=...)` queues explicit
[finished-upload source cleanup](SDK_UPLOADS.md#explicit-finished-upload-source-cleanup)
on the existing workers. Hashing and deletion stay off Blender's main thread.
Admission still checks the active scope and completion capacity, but needs no
current old scene/target, including after restart. The command returns the
unchanged terminal upload record and never changes the scene or deletes the
user's original file. Cleanup completions retain their original origin; they do
not authorize delivery into a replacement target.

The active runtime configures all three dependencies with a credential-scoped
store, private staging and a separate S3 upload policy. The
[reference facade](SDK_UPLOADS.md#active-reference-uploads) advances native and
local MCP uploads on this same pool, and the reference form's saved-upload
controls and MCP `recover_reference_upload` reach the refresh, local
cancellation, restart and cleanup commands above. GUI ticks and headless MCP
maintenance drain progress independently of an open panel. Private capture ownership is retained
by the session until staging ends or retirement joins the workers; cleanup never
removes a capture underneath a live staging task. Local MCP inspection and
explicit recovery do not approve reference attachment into a new scene.

Upload task outcomes use the normal `drain`/`deliver` path: successful records
must match the stored scope/origin, and delivery rechecks the current captured
scene/target before a once-only main-thread callback. A late claimed receipt may
persist after a file switch, while delivery remains blocked. Restarted origins
remain unrecognized for automatic application even when status refresh succeeds.
Callbacks do not by themselves attach a reference or commit a Blender application
transaction. The [typed form facade](SDK_UPLOADS.md#typed-form-attachment)
adds guarded attachment for image, audio, video and 3D inputs across generation
lanes after upload, without another worker pool. Explicit
native reattachment captures a fresh destination, requires separate confirmation
and rechecks the entire reference form before adding/replacing a slot. Recovery
tasks are owned and drained by the facade independently of dialog or MCP response
lifetime. The active
runtime supplies credential-bound scope without requiring project discovery.

## Active model submission

[ModelJobs](../scenario/blender/model_jobs.py) retains bounded ephemeral quote
handles and in-memory display projections for the selected session. UI and MCP
model generation share its quote and submission commands. Handles bind the
captured scene, originating lane, exact input snapshot and session-issued SDK estimate. Submission
validates the current inputs and exact approved cost before consuming the handle
and persisting intent. The coordinator still checks origin, ownership and expiry
at dispatch. No handle is reconstructed from a saved fingerprint after restart.

`drain(task=...)` collects only the requested task, leaving other consumers'
completions intact. Context maintenance routes each quote back to its originating
form even after a tab switch and drives receipt delivery in both the GUI and
actual CLI loop. Model status after restart is read from the
credential-scoped store, without automatically polling or resubmitting remotely.
Active jobs poll and download through this owner. Only Image automatically
imports supported images; other lanes stop at saved results for explicit application.

## Environment lane delivery

The native Environment form (lane `panorama`) and local MCP prepare requests with
the same [`panorama_contract.prepare`](../scenario/core/scene/panorama_contract.py).
`generation.build_request` applies it after building the form body: the scene's
mode must match the selected contract model, the GPT size comes from the scene's
2:1 preset, and locked, default and factor values replace the form's values.
Files and captures still waiting for upload stand in as placeholders so the
contract judges the rest of the form; a capture is never a panorama.

A panorama-kind reference is accepted in two forms only. A saved result chosen
with **Use saved panorama** or **Make HDRI** carries the selected scope, asset
and projection markers; every build rechecks it against the selected store with
the same `saved_panorama_refs` as MCP (downloaded, server-declared
equirectangular, the asset's own non-OpenEXR file). A local 2:1 PNG or JPEG
passes `inspect_panorama` before upload; the upload is bound to the checked
bytes' SHA-256 and the reference keeps the measured size, which sets the exact
upscale cap. `scope_error` still rejects references of another connection, and
attaching an arbitrary saved upload to a panorama input is refused.

Submission records lane `panorama` with the `image` kind. The lane is never
admitted to automatic image import, so results stop at saved results and no
World changes without the explicit approval below. Each maintenance poll
projects the saved results' server-declared projections into the view
(`meta["panoramas"]`, LDR or HDR by source and media type, and
`meta["panorama_inputs"]`, the downloaded LDR results). The job store persists
the projection, so the label and offers survive a restart. **Make HDRI** is a form
prefill, not a saved-job command: it checks the context token and the saved
result, switches the scene to the Environment HDRI form, attaches the scoped
reference and retires the form's price. It never quotes or submits.

## Active saved-job controls

The Jobs panel can inspect saved jobs without network activity. Except
`cancel_prepared` (below), recovery buttons and MCP `recover_local_job` share
`ModelJobs.control`, guarded by the selected context token and exact saved
revision. Queued commands retain their original scope.

- `refresh` observes a known remote ID once, without continuing delivery.
- `resume` polls and downloads the existing job, including after restart or a
  failed download, without authorizing automatic import.
- `cancel` uses the coordinator's once-claimed model cancellation command and
  continues observing the known ID. The acknowledgement alone is not cancellation.
  It is offered only for a `remote` model record whose latest refresh or cancel
  snapshot in this owner repeated its known ID with `jobType=inference`, the only
  type the API documents as cancellable. `ModelJobs` keeps that observation in
  memory only, so a restarted or newly inspected job needs an explicit Refresh
  first. Other jobs, including the captured `custom` model jobs, show no Cancel
  button; a native or MCP cancel for them reports that Scenario documents
  cancellation only for inference jobs, sends nothing and leaves the job running.
  If a fresh coordinator read no longer reports `inference`, the owner withdraws
  the offer and pauses delivery with the same message, without a claim.
- `recover_download` verifies interrupted saved receipts under the existing lock,
  without network access or import.
- `retry_receipt` saves an already completed local application's outcome without
  any scene mutation. The exact pending owner-local handle must still exist.
- `mark_reviewed` is offered for a `submitting` or `uncertain` record with no
  command in flight, whose [identical requests](JOB_COORDINATOR.md#identical-unconfirmed-requests)
  are refused. After native confirmation (**Mark reviewed**) it lets them through
  for this session only, sending and saving nothing. MCP `job_status` omits it
  and `recover_local_job` refuses it.
- `cancel_prepared` is offered for any unsent prepared intent: model (including
  Blockout), workflow, Film task, prompt or translate, including one queued behind
  other work. Prompt, translate, Blockout and restarted intents get a Jobs panel
  view after inspection. After
  native confirmation (**Discard unsent job**) it calls the same
  `runtime.cancel_prepared_job` command as MCP `cancel_prepared_job`, not
  `ModelJobs.control`, with the context token and observed revision. It sends no
  service request. The queued submission then fails its stored-state check and the
  job shows as canceled; the prompt field and the Blockout panel report the local
  cancellation instead of a stopped action. A claimed request or stale revision is rejected. The dialog
  warns that a canceled Film task stays reserved, so another take needs a new name.
  MCP `job_status` lists the same `cancel_prepared` action name, and its
  description maps that name to `cancel_prepared_job`.

Native surfaces draw these controls from one descriptor list.
[`saved_job_actions.describe`](../scenario/core/ui/saved_job_actions.py) is
free of `bpy`: it turns one projected view into ordered descriptors with the
label, icon, operator and operator properties (the context token, then either
the request ID and observed revision or a ready Blockout review ID, and any
asset, purpose, action, or workflow node and step review ID). Each job-action descriptor
keeps its `ModelJobs.actions` name, which MCP `job_status` reports except
`mark_reviewed`. `import_model`
draws one control per importable unit that `ModelJobs` projected into the view,
in classified order, instead of one per GLB media type. The sidebar
Jobs and Generations panels, and the Studio Jobs and Results pages that reuse
them, draw through `job_recovery.draw_controls`; the compact composer's
[job strip](UI_STYLE.md#compact-job-strip) dispatches the same descriptors'
operators with `INVOKE_DEFAULT`, and its job card draws those panels' rows.
Presence means enabled:
a descriptor appears only while its action, or for playback its downloaded
result, is offered, and rows without an operator are status text. Building
descriptors reads only in-memory views, the current scene's Blockout review and
a workflow job's [step projection](#native-review-of-waiting-steps), without
store reads, session creation or scene changes. A descriptor grants nothing:
each operator still checks its
context token and saved revision or review, and opens its own confirmation or
destination review where one applies.
A new saved-job action adds one descriptor here, not a surface-specific branch.
`use_first_frame` draws one control per still that `ModelJobs` projects as
`first_frame_assets`, because eligibility needs texture roles and receipts that
the view's media types do not carry; its numbering counts every saved asset.
Results whose saved metadata declares a 360 projection add a status row
(**360 panorama (LDR)**, **360 HDR environment (OpenEXR)** or both) and, once a
settled job has a downloaded LDR panorama, one **Make HDRI (N)** descriptor in
the `prefill` group. A prefill has no `ModelJobs.actions` name because it
changes no saved job; MCP agents reach the same step with `estimate_cost` on lane
`panorama` and the saved asset ID.

Downloaded video and audio results also get read-only playback descriptors: a
video row with **Play video** and **Play in Blender**, and for audio a row with
**Play audio** (**Stop audio** while it plays) and **Waveform**, then a row with
**Open in system player**. They are not `ModelJobs.actions` names: they follow the
view's saved state (`ready`, `applying`, `apply_failed` or `applied`) and the
media types `ResultTypes.playback` lists, never change the job, and MCP
`job_status` does not report them. Their operators recheck the context token,
saved revision and downloaded receipt (**Stop audio** names only the playing
asset), then use the
[saved media controller](../scenario/blender/result_media.py) described under
saved-result preview ownership.

No action reconstructs a quote, replays an uncertain submission, guesses a remote
ID or rebinds the original scene. Missing/stale revisions and retired contexts fail
before command dispatch. The UI confirms remote cancellation through its native
invoke path. MCP recovery waits off the main thread and rechecks its owner before
returning status. Shared `wait_for_job` reads saved state on the HTTP worker while
the main thread advances delivery; pause, failure, completion or timeout returns
the current result without canceling or regenerating. MCP status reports
`delivery_active` from the same predicate the wait uses: the job is owned by this
session, not paused and not in a terminal or failed state, `prepared` counts only
while its submission is queued, `ready` counts only while an Image lane
automatic import is pending, and `succeeded` counts only when `ModelJobs`
downloads the results. `downloads_results` decides that from the saved intent:
model and workflow generations, including Film tasks and cloud jobs saved for
recovery, download; `prompt` and `translate` operations and Blockout plans (the
text model) do not, because their tools read the text into memory, so
`succeeded` is their last saved state. Their views keep the terminal display
status `succeeded` and stay in Generations after **Inspect saved jobs**, and
`ModelJobs.poll` never starts a download for them, including after an explicit
resume of a restarted Blockout job. Only a `succeeded` view whose results
`ModelJobs` downloads gets the non-terminal `awaiting-download` display status
that keeps it in Jobs. A job the predicate counts whose next step needs Scenario
(`remote`, `cancel_requested` or such a `succeeded` job) does not count while
Online Access is disabled; MCP reports exactly that hold as `delivery_offline`,
so a paused or restarted job, or a finished Prompt Spark, Translate or Blockout
job, never reports it, and delivery resumes by itself once access is allowed.
The wait runs on the HTTP worker, so the predicate reads only in-memory state,
including the Online Access value the last main-thread maintenance poll
observed, never `bpy`. A returned result with `delivery_active` set is therefore
an expired wait. Stopping the MCP server interrupts its wait without canceling
the generation.

## Remote progress and scene lane binding

Automatic and explicit `refresh_remote`/`cancel_remote` completions, and workflow
decisions, carry the coordinator's `RemoteSnapshot` from SDK `jobs.retrieve`.
`ModelJobs._observe` is their single display consumer, so Prompt, Translate and
Blockout jobs show no remote progress; the inference cancellation
offer above reads the same refresh and cancel completions on the delivery path,
without draining them again.
[`progress.observe`](../scenario/core/jobs/progress.py)
turns an active snapshot into one `RemoteProgress` reading (status, fraction,
UTC and monotonic time) bound to the record's remote job ID and saved revision.
The fraction must be a finite number from 0 to 1; booleans, strings, missing,
non-finite and out-of-range values are unknown, never 0. Providers may still
report 0 until completion, so a percentage is shown only for `in-progress` or
`finalizing` from 1%; the whole percentage snaps to the reported decimal
before flooring, so 0.29 shows 29% and 0.005 shows no bar. The reading's word
comes from one table shared with the composer's job strip: `pending` and
`queued` read as queued, `warming-up` as starting, `in-progress` as generating
and `finalizing` as finalizing. A terminal snapshot clears the reading. The
projection runs after the next poll is scheduled and outside the delivery error
mapping: if it fails, the reading is dropped and only the error type is logged,
never the response, and delivery is never paused. Later interpretations of the
same snapshots extend this hook rather than draining the completions again.

The reading lives only in the view's `meta["remote"]`, the projection the Jobs
views and MCP `job_status` both read. Each maintenance poll drops it when the
record leaves `remote`/`cancel_requested` or its remote ID or revision changes,
and marks it stale when automatic polling is not keeping it current: Online
Access is off, delivery is paused (including after an explicit one-time
refresh) or the scheduled refresh is overdue by `STALE_GRACE`. Staleness follows
each job's scheduled poll time, so a longer scheduled interval is not stale
merely for being longer. Polling cadence, online gating and saved revisions are
unchanged: an active refresh writes nothing and nothing about progress is
persisted. After a restart, readings appear only after an explicit **Refresh
status** or **Resume download**.

`ModelJobs.submit` records a display-only `JobBinding`: the submitting scene's
`session_uid` and the submitted generation lane, for UI forms and MCP `generate`
alike, before dispatch. `bound_views(scene, lane)` returns that scene lane's
views newest first from in-memory dictionaries only, so the compact composer's
job strip finds the current job of the form it shows without guessing. A binding
grants no destination, origin or application authority; results still apply
only through their own approvals and origin checks. Workflow and Film
submissions are unbound. Bindings and readings retire with their owner on a
credential, project or file change. Lanes are not persisted: restarted and
recovered jobs are unbound, and MCP reports `lane: null` for them.

### Failure reasons

When a retrieval reports `status: failure`, the coordinator commits the saved
`failed` state from it, and the same `ModelJobs._observe` hook reads the reason
Scenario gave. SDK 2.2.0 and the [job retrieval reference](https://docs.scenario.com/api/python/resources/jobs/methods/retrieve)
document two optional strings in `Job.metadata`: `error` ("Eventual error for
the job") and `hint` ("Actionable hint for the user explaining what went wrong
and how to resolve it"). [`failures.observe`](../scenario/core/jobs/failures.py)
joins them into one `RemoteFailure` bound to the record's remote job ID and
saved revision. The text is untrusted, so nothing is rewritten or redacted: an
allowlist gate, made only of plain string operations, shows each part as it is
when it is plain text and otherwise replaces it with a fixed sentence. After at
most 4,096 characters are read and control, format and space characters become
single spaces, a part is shown only when:

- every character is a letter, a digit, a space or one of
  `. , ; : ' " ( ) - ! ? / % _`, and the part contains no `//`;
- every space-separated token, without surrounding punctuation, is an exact
  support id, a Scenario resource ID, a word of letters (with hyphens or
  apostrophes inside) shorter than 32 characters, or another token shorter than
  12 characters that switches between letters and digits at most twice, so
  `4096x4096`, `720p` and `H3` pass and short random strings do not;
- a `/` only joins two tokens shorter than 12 characters that each pass these
  same rules, as in `width/height` or `in/out`;
- no token contains a credential word (`key`, `secret`, `token`, `password`,
  `passwd`, `pwd`, `auth`, `credential`, `signature`, `session`, `cookie`, or
  `sig` alone), also with its separators removed (`tok/en`, `tok_en`,
  `pass/word`) or digits standing for letters (`tok3n`, `s3ss10n`), except
  harmless words such as `keyframe`, `key-frame`, `keyword` or `author`.

A Scenario resource ID is `model_`, `job_`, `asset_`, `team_`, `proj_`, `col_`,
`workflow_`, `upload_` or `apiu_` followed by 20 to 24 letters and digits, as
recorded fixtures show, or a public model's lowercase hyphenated slug of 2 to 9
words. A support error id is lowercase `error_` followed by 6 to 64 letters and
digits with a capital or digit.

A part that is not plain text reads "Scenario reported more details that are
not shown here.", and nothing is taken from it, not even a support id. When
neither part is plain text, the reason is "Scenario reported a failure without
a readable reason." When both are shown and one contains the other, ignoring
case, spacing and final punctuation, only the longer is kept. The joined text
is cut to 400 characters at a word boundary with an ellipsis, never inside a
support id; when the cut drops the first support id of the shown text, it is
appended. Missing, blank and non-text values give no part, and other statuses
give no reason; a failed reading drops it and logs only its error type.

Each maintenance poll sets a `failed` view's error to that reason while the
record keeps the reading's remote ID and revision, else to none: nothing else
remains to deliver for a failed job. Generations, the composer's job strip and
job card, **Generation details** and MCP `job_status` all read this view error.
Like progress, the reason stays in memory: the job database stores no message,
so a job that failed before a restart shows `failed` without a reason, and
`list_local_jobs` has no error field. Prompt and Blockout jobs and cloud history
rows show no reason.

## Waiting workflow steps

`ModelJobs._observe` also projects the waiting approval and selection steps of a
`remote` workflow record from each refresh and decision snapshot, through the
[coordinator's step parser](JOB_COORDINATOR.md#waiting-workflow-step-decisions),
with the [step links](JOB_COORDINATOR.md#step-links) the coordinator attached
from the workflow definition.
The projection stays in memory, bound to the record's remote job ID and saved
revision; each maintenance poll drops it once the record changes or leaves
`remote`. A failed projection drops the steps and logs only the error type, once
until a projection succeeds again. While a step waits, the next automatic
refresh is scheduled after `STEP_POLL_INTERVAL` (15 seconds), because each
retrieval re-signs candidate URLs and only a decision moves the job on; the
stale marking follows that schedule. `steps()` reads the projection from memory,
so drawing code may call it. `actions()` lists `review_step` before the refresh
controls, and `control` rejects it. The saved-job descriptors draw it as the row
**Workflow waits for your decision**, followed by one native review control per
projected step (see [native review](#native-review-of-waiting-steps)).

`ModelJobs.decide(request_id, expected_revision, node_id, review_id, action, selected_indices=None)`
is the shared native and MCP entry; `runtime.decide_workflow_step` adds the
context token check. It requires the exact saved revision, a current projected
step with the same `review_id`, a decision that step allows within its bounds,
an active session, no command in flight for the job and Online Access. It then
queues `JobSession.decide_workflow_node`, which, like cancellation, uses the
stored origin and needs no current scene or target. Deciding clears the last
decision error and resumes automatic polling, also for a restarted job refreshed
explicitly. Its completion is projected like a refresh. A refusal, uncertainty,
changed step or local validation failure never pauses delivery: its text stays
as the decision error until another decision, a newly waiting step or the record
leaving `remote`, and without a fresh retrieval the job is refreshed at the next
poll.

A waiting step does not advance by itself. Until a decision is queued, the job
is not counted by the `delivery_active` predicate, so `wait_for_job` returns at
once and `delivery_offline` stays false; MCP `job_status` reports `awaiting_decision`,
sanitized `pending_steps` and `decision_error`. After a restart, steps appear
only after an explicit read: **Refresh status**, **Resume download** or
`read_steps`.

`ModelJobs.read_steps(request_id)` is the shared entry that reads a saved
workflow job's steps again; `runtime.read_workflow_steps` adds the context token
check. A job no longer `remote` returns None without a request. Otherwise it
requires an active session, no submission in flight and Online Access, then
queues one `refresh_remote` retrieval whose completion `poll` projects like any
refresh. A command already in flight for the job, an automatic refresh or a
decision, is returned instead of queuing another. Unlike **Refresh status**, it
leaves the delivery mode unchanged: an automatically polled job is not paused,
and a paused or restarted job is not resumed. `ModelJobs.decision_outcome(task)`
classifies a finished decision task as `applied`, `refused`, `uncertain` or
`not_sent` without draining its completion, which `poll` still owns.

MCP `workflow_job_steps` and `decide_workflow_step` call these entries and
`runtime.decide_workflow_step`; see [workflow step decisions](MCP.md#historical-upstream-reference).

### Native review of waiting steps

`ModelJobs.step_view(request_id)` returns, from memory only, the projection's
saved revision and steps, whether a decision is queued or being sent, and the
last decision error, so drawing code may call it. `job_recovery.result_actions`
passes it to the saved-job descriptors, which add one review control per step
only while its revision equals the view's saved revision, a **Sending your
decision...** row, wrapped decision error rows and, for a running workflow job
without a waiting step, a note that Scenario cannot cancel a running step.
`ModelJobs.waiting_step(request_id, expected_revision, node_id)` returns the
saved record and one current waiting step; a changed revision, a job that is not
a `remote` workflow, a command in flight or a missing step is refused, and
nothing is queued. [workflow_steps.py](../scenario/blender/workflow_steps.py)
opens the review dialog with that step and its `review_id`, and sends the chosen
decision once through `runtime.decide_workflow_step`, the entry MCP
`decide_workflow_step` uses; `decide` checks the binding again.
`ModelJobs.review_assets(request_id, expected_revision, node_id, review_id)`
returns the asset IDs one current step lists for review, an approval's result
assets or an asset selection's candidates, with the same binding but without
refusing a command in flight, since previews never decide; **Load previews**
passes them to the [review previews](#saved-result-preview-ownership). The
dialog, its wording, the previews and the Workflows page section are described
in [UI style](ui/studio-and-library.md#waiting-workflow-steps).

## Explicit recovered Image application

For downloaded PNG/EXR results in `ready` or confirmed `apply_failed` state,
**Import saved images** captures the current file-session/scene revision and
shows a native confirmation dialog. MCP `prepare_result_application` returns
the scene, image names and a single-use `application_id`; after user approval,
`apply_result_application` consumes it. Neither inspection nor download authorizes
an import. Canceling the dialog discards its approval. Approvals are owner-local,
bounded to 128 and never persisted or reconstructed after restart.

Admission rechecks the selected context, exact stored record and destination
before queuing verification on the existing workers. When verification finishes,
`apply_recovered_images(completion, destination=...)` resolves that same captured
destination again. It uses the coordinator's explicit recovered claim to save
`applying` and `application_origin` atomically before decoding or packing images.
The original `intent.origin`, request, scope, quote and result receipts stay
unchanged. Scene/file changes require new review; the importer never recaptures
the current selection as replacement approval.

The importer adds packed image datablocks to the current file; it assigns no
object, material or World and does not save the blend file. The Image Editor can
select the imported images. Import, rollback and receipt-only recovery use the
same primitive as automatic delivery. `applied` and interrupted `applying`
records cannot be imported again through this command. No service call, download
or paid submission occurs during application.

## Prompt Spark command facade

`JobSession.quote_prompt(parameters, origin=...)` requires a main-thread captured
origin and available completion capacity. It dispatches the SDK dry run through
the existing worker pool and returns a task; drain its completion before using
`prepare_quote` and `submit(operation="prompt", target_id="prompt", ...)`.
Caller parameter edits after queueing cannot change the quoted request. Scene,
file, target or account changes prevent preparing/submitting a stale quote.
The `PromptJobs` facade owns ephemeral New/Rewrite/Translate UI handles,
sharing this session and worker pool. `quote_translate` has the same captured-origin
contract. No extra thread pool or job store is created. Render look preparation
uses this same facade, with uploaded-reference identity bound to its quote.

`read_prompt_results` uses the same record-command admission and completion
queue. `PromptResults` carries its original stored record through scope/origin
checks; `deliver` resolves the captured scene/target immediately before a caller's
callback. Reading text does not itself mutate a prompt field, mark a job applied,
or grant permission to redirect a recovered result to the current scene.

Native New/Rewrite/Translate and MCP `estimate_prompt` first request a price.
`approve_prompt` and the native approval button consume that exact displayed
quote once. The facade also binds original field text and model, blocks concurrent
preparation, invalidates the image-generation quote on approval, and verifies
those bindings again before assigning the result. Retiring credentials discards
the facade; stale callbacks never update every scene or substitute an LLM call.
The shared pump advances saved remote jobs independently of an open panel.

MCP `read_prompt_result` checks the current credential context and stored revision,
but returns text without changing a scene. This permits explicit recovery when
the original field has changed. Saved prompt/translation jobs offer status refresh,
not image/mesh download or import actions.

### MCP render form commands

`render_commands` provides main-thread inspection/configuration, explicit scene
and first-frame upload preparation, and guarded reference removal through the
existing native form. MCP `render_form` uses these commands; it owns no worker
pool or paid job engine. Parameter edits are validated before applying them and
invalidate the visible price. Multi-select arrays accept each current schema
choice at most once, bounded by the available choices like the native checkboxes;
duplicates and unknown choices fail before changing any form field.
Removing a reference requires a key bound to its
scene, model, slot and current source; saved upload work remains independent.

Render `estimate_cost` and `generate` rebuild `generation.build_request`, including
render prompt decoration, ordered uploaded references and Spark readiness. They
reject raw `parameters`, a different selected model, pending uploads and an
unapproved empty automatic look. The estimate finisher rechecks the body before
returning its handle; submission uses the existing `ModelJobs` payload, exact
cost, scene, lane and credential checks. UI and MCP use the same model commands
and retained result application policy. No new service endpoint is introduced.

### Render look preparation

An empty render look with Spark enabled requests only a free prompt quote after
its references are uploaded. It never authorizes a paid call. The existing prompt
approval button consumes that quote once; result delivery fills the look and
invalidates the final render price. Rendering still needs its own exact quote and
approval. Repeated price refreshes reuse the pending look action; errors and
uncertainty do not automatically start another attempt.

`render_prompt_jobs.parameters` uses the uploaded scene still followed by style
images for Render Image. Render Video requires an enabled first frame, uploaded or
[handed from a saved image](#saved-image-to-render-video-first-frame);
only that frame and style images enter Spark's `images`, never the video asset.
The selected model, look, ordered references and their roles, Spark toggle and
first-frame identity are checked again at approval and delivery. Pending files,
wrong-scope references and more than 15 images fail before the prompt dry run.
The old unbound render-result event cannot overwrite any scene's look.

Native and MCP New/Rewrite on a render lane use this same preparation. A read-only
quote can become stale when the user edits references or saves/changes the scene;
request a fresh price explicitly rather than replaying it. This does not add an
automatic scene-still capture for Render Video when no first frame is available.

## Explicit saved video and audio application

`prepare_media_application` captures one result asset, the current scene revision
and frame. The native **Add video/audio strip** confirmation and MCP
`prepare_result_application(asset_id=...)` share this single-use approval;
`apply_result_application` consumes it. Both admission and delivery recheck the
selected credential context, stored record, scene and exact frame. A changed
frame requires new approval even when the scene otherwise remains valid.

The existing worker verifies saved receipts. `JobSession.apply_recovered_media`
then claims recovered application durably before calling
[`media_application.apply_media`](../scenario/blender/media_application.py) on
the main thread. It copies and rehashes the selected receipt into a private
extension-user-storage directory, checks its container, and inserts one MP4/WebM
movie or MP3/WAV/OGG sound strip on a wholly unused, unlocked, unmuted channel.
The local copy is bounded to 512 MiB and remains synchronous; large files can
pause Blender during copying and decoding. No credentials, download or paid
submission are involved in this application command.

Existing strips, selection, active strip, scene range, frame rate and resolution
are preserved. Video inserts picture frames only, fitted inside the scene;
embedded audio is omitted and source frames play at the scene frame rate.
A confirmed rollback becomes `apply_failed`; incomplete rollback remains
uncertain. Failure to persist success retains a receipt-only retry that never
inserts again. Restarted `applying` and completed `applied` records cannot be
claimed again. One selected asset consumes this job's application claim; other
variants remain saved but cannot be inserted by another claim for that job.

Unlike packed images, strips depend on the independent local media copy. Keep
that file available, including when moving the blend file; the command does not
save the blend, pack media or clean snapshots after strip deletion. It does not
change which scene an existing Sequencer editor displays. Select the approved
scene in that editor's header to inspect the strip. Mesh, material, World and
Film integration are separate from this video/audio path.

Shutdown releases pending media receipt handles after its workers stop, matching
image and World ownership. A retained exception cannot persist success after
shutdown; the saved uncertain record and any existing strip remain unchanged.

## Saved image to Render Video first frame

`use_first_frame` is offered for a `ready`, `apply_failed` or `applied` job
with at least one downloaded PNG, JPEG or WebP colour result: no texture role,
or the base role. EXR and normal, height or other maps are excluded. The same
gates as other reuse apply: no pending command or receipt, no unfinished local
claim and at most 128 local applications. Prompt and translate jobs never offer it.

`ModelJobs.prepare_first_frame_application`, reached through MCP
`prepare_result_application` with `purpose="video_first_frame"` and the
`asset_id`, or through the native **Use as video first frame (N)** control,
reads no file, sends no request and changes no form. It requires the
selected scene, a loaded Render Video model whose first-frame target is an
image input, no existing first-frame slot (including a pending or uncertain
upload marker) and room in that input. The target is
[`render_lanes.first_frame_target`](../scenario/blender/render_lanes.py), shared
with uploaded first frames: a single image input named like a first frame, unless
the model says it can't be combined with the reference video that carries the
scene clip, else the first image array that can go with the clip. The review
reports `sent_as` (`first_frame`, or `reference_image` for image 1 of that
array), its `reason` and a `note`. A model whose schema is not loaded is refused without
starting a read; select or configure it in the form first. The single-use
approval binds the job revision, the asset and its receipt digest, the scene
revision and the reviewed form: model, input, existing references, any chosen
first-frame file and whether the first frame is enabled.

`apply_result_application`, or confirming the native dialog, rechecks the
record, the scene and that form, then queues the existing receipt verification. On delivery,
[`first_frame_handoff.bind`](../scenario/blender/first_frame_handoff.py) consumes
the verification through `JobSession.verified_result`, which takes no
application claim. It rehashes the file against its receipt (at most 128 MiB,
no symbolic link), checks its PNG, JPEG or WebP container and rechecks the
scene and form. It then adds one role-tagged first-frame slot with
`source="ASSET"`, the saved asset ID, the existing scope, asset and kind
markers, and provenance: request ID, asset ID and receipt digest. It clears any
chosen first-frame file, enables the first frame and invalidates the Render
Video and Prompt Spark prices. A failure removes the new slot and restores the
file and enabled state. It then records one Blender undo step when global undo,
the undo step count and a window context allow it, and status reports whether
it did (`undo_recorded`). Background sessions have no desktop history; without a
step the binding still marks the file modified. Undo of a recorded step
removes the slot and restores the enabled state; Redo restores the slot. Neither
changes the saved job or sends a request.

The handoff is a form binding like a saved-upload or Library attachment, not a
scene application. The job keeps its state and gains no `local_applications`
entry, and nothing is uploaded, generated or spent. MCP status reports this
session's latest outcome as `first_frame` (`bound` or `failed`); it is not
persisted. A failed or stale handoff leaves the form unchanged and pauses the
job with an error until the next explicit action. A changed scene, frame or
form, or a retired context, requires a fresh review.

No local path is written to the form or the saved blend. The verified result
stays in private extension storage, and the slot's provenance lets a reader find
that saved job again in the same credential scope: MCP `render_form` reports
`source_result` with `saved`. After restart, `reference_form.scope_error`
accepts the slot only under the same credential and project scope. Choosing a
local first-frame file afterwards requires removing the slot, as for any changed
first frame, and a saved upload attached over the slot drops its provenance.
Quotes send the asset ID like any other existing asset; a deleted or
inaccessible asset fails the quote and nothing retries.

### Native first-frame control

The sidebar Generations and Jobs panels and the Studio Results and Jobs pages
draw **Use as video first frame (N)** from the shared descriptor. The
[`scenario.use_saved_first_frame`](../scenario/blender/job_recovery.py)
operator prepares the same approval as MCP and opens a confirmation naming the
scene, the Render Video form, model, input and saved image. It states that the
saved asset is reused without an upload, generation or stored file path, which
chosen first-frame file it replaces and whether it turns the first frame on,
that the Render Video and Prompt Spark prices become invalid and that removing
the slot undoes it. Refusals report their reason, such as an existing slot, a
model without an image input or a model that is not loaded; a retired
connection asks to inspect saved jobs again. Cancel discards the approval.
Confirming runs the operator's execute step, which consumes the approval once
through the shared apply command; it accepts an approval prepared through MCP
the same way, and a second use fails. Drawing reads only the projected view
and the slot's provenance; it opens no store, file or session. The Render Video
form shows a read-only **From a saved result** line under a first-frame slot
whose provenance is intact. The slot has no thumbnail because no file path is
kept.

## Explicit saved model application

`prepare_model_application` captures one importable unit of the saved job (GLB,
JSON glTF, FBX, OBJ with its MTL and maps, PLY, SPZ or `.splat`), keyed by its primary
file's asset ID, with the current scene revision and cursor location. `ModelJobs`
classifies the saved manifest once per saved revision with
[`classify_packages`](MESH_APPLICATION.md#saved-3d-package-classification) and
projects the importable units into the view, so drawing reads no store or file.
Native **Import <format> model** and **Import alternate: <format>** controls and
MCP's generic asset preparation share `apply_saved_result`; media remains
frame-bound and models are cursor-bound. The existing worker verifies all saved
receipts. `apply_recovered_model` rechecks the destination, reclassifies the
verified record and builds the unit's private snapshot on the main thread before
it claims application; a rejected snapshot raises `ModelPreflightError`, which
leaves the saved job unchanged and pauses delivery with **Nothing was imported**.
A completed job's reuse claim lists every member the import read.
`retry_model_receipt` persists only the known outcome and shutdown clears pending
handles. Model status includes names of its retained imported objects, the
`import_units` and `import_findings` classification and the session-local
`model_import` summary; names do not authorize replay or locate replacement
targets after restart.

SPZ, `.splat` and PLY units also carry a reviewed point limit and saved axes in
the approval; `revise_model_application` exchanges a native review for a new
handle with other options and the same captured destination, cursor and unit.
For these units `apply_saved_result` queues `JobSession.prepare_model_import`
instead of verification, in the workers' single local-operation slot; a busy slot
raises before anything is queued for SPZ and `.splat`, while a PLY then queues
ordinary verification that `apply_recovered_model` accepts only for the mesh
snapshot path, which refuses splat data before any claim. Its
`PreparedModelImport` completion carries the registered verification of every
receipt and, for splats, the decoded snapshot.
`drain` binds it to the job origin like a verification. `apply_recovered_model`
accepts it only for the approved unit and exactly the approved options, claims
it, and builds the point cloud with `splat_application.import_splat`; a PLY
without splat data continues through the mesh snapshot path. A worker rejection
or cancellation pauses delivery with **Nothing was imported** before any claim,
and receipt-only retry never rebuilds points. The session summary adds the splat
point counts, axes and SPZ extension flag, which the native row shows as status
lines.

See [model import](MESH_APPLICATION.md#explicit-saved-glb-import),
[package import](MESH_APPLICATION.md#saved-model-package-import) and
[splat import](MESH_APPLICATION.md#saved-gaussian-splat-import) for formats,
allocation, placement, rollback and remaining edit-policy limits. In-place mesh
edits and Film shots still use only `model/gltf-binary` results. Neither this
approval nor the importer calls Scenario or submits generation.

## Recovered panorama destination and restoration

`apply_recovered_world(completion, destination=..., asset_id=...)` wraps the
existing World primitive with the coordinator's recovered claim. The shared
facade captures the scene revision and current World; both admission and delivery
recheck them. UI and MCP use one approval owner and existing verification workers.
Known World failures retain the safe retry state only after complete rollback;
uncertainty retains a receipt-only retry, never another assignment.

After a completed assignment, a separate prepared restore can call the retained
World handle on the main thread. The approval checks the same selected context,
record and current World, and the handle rejects edits to its owned data.
Restoration does not alter the durable completed job or reopen its original claim.
See [saved World approval](WORLD_APPLICATION.md#saved-result-ui-and-mcp-approval)
for original/reuse admission and session-local restoration limits.

## Explicit saved material application

`apply_recovered_material` consumes owned result verification, resolves the
approved scene/mesh origin, checks the frozen slot/UV/face-index target and the
unambiguous saved map roles, then claims recovered application before decoding
or assignment. The [material primitive](MATERIAL_APPLICATION.md) packs new images
and builds a new material without editing old materials or other slots. Only a
verified complete cleanup permits a local retry; incomplete cleanup retains an
uncertain claim. A successful assignment with a failed receipt write retains a
session-owned handle for `retry_material_receipt`, which never repeats scene work.
Shutdown clears those handles after workers stop.

Native **Apply saved material** and MCP `prepare_result_application` with
`purpose: material` use that same destination approval and saved-job command.
Like other saved-result commands, admission and delivery require the approved
scene to remain selected through the shared `_resolve` guard. A scene switch
stops before the local application claim; it leaves the completed generation,
saved results and material slots unchanged for fresh approval after returning.
Changing selection cannot retarget it. Multi-object/shared meshes, ambiguous
texture sets and global undo remain separate work.

## Explicit local result reuse

The explicit recovered image, media, model, mesh, material and World delivery
methods, and Film review application, claim through `_claim_saved_application`.
Original `ready`/`apply_failed` records keep their recovered claim; `applied`
records receive a separate coordinator local claim. Automatic image import and
`apply_world` never take this reuse path. The destination,
purpose and exact selected assets remain bound to the prepared approval and
stored revision. Verification precedes the claim; each native primitive rechecks
actual bytes before decoding, with its existing rollback and receipt rules.

`ModelJobs.actions` permits fresh review only when no command, pending receipt or
unfinished local claim exists and the 128-entry limit is not reached. Status
exposes `local_applications` independently of the terminal generation state.
The session's `objects`, `materials` and `images` status lists retain live outputs
from the original application and every reuse. Receipt-only recovery deduplicates
those references; deleted datablocks are omitted. These names are session-local
inspection data, not persisted handles or authority to change the scene. Restart
retains durable application records without reconstructing live output references.
The same job may remain `applied` while local scene work is uncertain; inspect
its local outcomes, actions and error. Owner-local receipt retry never repeats
Blender work. Restart loses receipt authority and never clears uncertainty.
World restoration retains only the most recent assignment handle per job and
destination scene in the current session; this is not a persistent history of
reversible scene edits.

## Verified saved-mesh replacement

`apply_recovered_mesh(completion, destination=..., asset_id=..., target=...,
policy=..., result_to_source=..., keep_original=...)` applies one saved GLB with an
explicit REMESH, UV, RETEXTURE, PARTS or RIG policy to an explicitly captured
source mesh. The target snapshot must match the resolved
scene/object origin and its geometry/context must still match before claiming
application. A caller captures this source before asynchronous work and binds its
policy, mapping and Keep original choice to the approved operation; the command
does not derive provider semantics or infer targets from selection.

The [verified mesh command](MESH_APPLICATION.md#captured-source-and-verified-saved-mesh-command)
rehashes the actual bytes, imports in isolation and uses the existing mesh
primitive, or the skin primitive for RIG. Confirmed rollback permits a failed
local outcome; incomplete scene cleanup retains uncertainty. A successful mesh change followed by a failed store
write retains an owner-issued `ModelResultUncertain` handle for the existing
`retry_model_receipt`. That retry never imports or replaces again. Shutdown drops
receipt authority; restart does not authorize replay of an unfinished claim.
Completed jobs can use a separate `mesh_edit` local claim without changing their
original generation outcome. Its target identifies the explicitly selected source.

Native **Apply mesh edit** and MCP `prepare_result_application` with
`purpose: mesh_edit` share this command and its captured target. The
[review](MESH_APPLICATION.md#saved-mesh-edit-approval) binds policy, coordinate
placement and Keep original before verification. Editing review options never
recaptures the source. Admission calls the shared destination guard before
queuing verification; a retired session rejects the consumed approval without
queuing work, claiming application or changing the scene. The single-use handle
cannot be replayed. Delivery rechecks the destination after verification.
The captured-source mode below links export metadata to that same guard;
end-to-end edit-lane acceptance remains incomplete. Desktop Undo follows
[native history](MESH_APPLICATION.md#native-undo-for-saved-mesh-edits); there is
no persistent restoration handle, new generation request or automatic
provider-coordinate mapping.

## Prepared Film review application

`prepare_film_review` queues scoped saved-media copies/probes off the main thread
and returns an issued completion in its original scene. Separate
`apply_film_review` verifies the production, recipe and current origin, consumes
the worker preparation once, and claims every generated source before building
a new independent sequence. Imported-upload sources retain their upload records.
The active scene, selection and frame stay unchanged. No second execution owner,
service request or generation is introduced.

Successful builds complete the existing generated-source claims; complete rollback
fails them. Lost claim responses and partial cleanup remain uncertain without
another build. `retry_film_review_receipt` accepts only this session's known
outcome and retries persistence, never decoding or mutation. Unused completion
discard and joined shutdown clean preparation copies; successful/uncertain scenes
retain their files. See [the detailed contract](FILM_PLAN.md#shared-native-review-preparation-and-application)
for copy identity assumptions, limits and acceptance boundaries.
Failed copy cleanup cannot suppress a known rollback receipt or its retry handle.
The result retains inspection-required status after receipt recovery until the
remaining files have been inspected; it never repeats native application.

[`FilmReviewCommands`](../scenario/blender/film_review_commands.py) is owned by the
session as `JobSession.film_review`, like the timeline and capture handles. It
keeps at most 16 ephemeral reviews bound to the original scene, production, recipe
and captured origin, and calls only the session's prepare, apply, receipt-retry and
discard commands. Runtime maintenance drains preparation; a review becomes ready
only while its recipe scene is current with an unchanged origin. A window selecting
the recipe scene again runs `frame_change_pre`, which revokes that origin, so
waiting and ready reviews then fail and delete their copies. Object selection or
edits revoke it through the dependency handler, and Undo/redo resets every origin,
with the same result. The issued completion is consumed only by the separate build
approval.
Invalidation, cancellation and discard delete unused copies through the
coordinator; handle cleanup never changes built scenes or saved claims. Shutdown
drops the handles after workers join, and the coordinator removes unconsumed
copies. Native controls and three MCP tools share these handles; see
[review controls](FILM_PLAN.md#native-and-mcp-review-controls).

## Source identity in mesh uploads

Mesh capture snapshots the selected source objects before export and rechecks
their geometry/context after the exporter restores selection. The post-export
origin supplies the upload revision while retaining the pre-export file, scene
and target identities.
`capture_many` validates a whole target batch against one fresh scene-membership
set before recording any origins, retaining the main-thread and active-session
guards. Mesh export uses one batch before and one after export; membership is
never cached across passes. The ordinary `capture` method delegates a single
target through the same checks, without enumerating objects for scene-only origins.
Each pass finishes its fingerprints and source snapshots before registering
origins. A rejected first-pass snapshot leaves the session registries unchanged;
deleting its source cannot invalidate other work through target pruning.
For a single source the `JobOrigin.target_id` identifies that mesh. Multi-source
exports persist all IDs in local `mesh_source` metadata without assigning a
primary target. The exact export hash accompanies the upload through the existing
worker and storage path; ordinary file/capture uploads have no mesh provenance.

See [the upload contract](SDK_UPLOADS.md#captured-mesh-export-provenance) for limits,
schema upgrade and native roundtrip coverage. Shared UI/MCP quotes preserve
these snapshots for matching 3D input assets in
[generation intents](JOB_STORAGE.md#captured-mesh-inputs-in-generation-intents).
They do not restore target authority after restart or prove the current object
is unchanged from the uploaded snapshot.

## Live captured-source application

`mesh_provenance.export_with_source` retains an eligible static source guard in
the selected `JobSession`. `mesh_source_target` resolves the exact stored input
binding against that live export and validates the frozen object before the
shared UI/MCP mesh approval. It ignores active selection and rejects absent,
changed or retired authority. File/scene/object identities must still match;
undo/load/credential retirement clears guards rather than reconstructing them.

The approval reuses the same post-verification destination checks, durable claim,
Keep original and persistence-only recovery as generic saved-mesh application.
See [captured-source application](MESH_APPLICATION.md#applying-to-the-captured-mesh-source)
for capacity, ambiguity and unsupported-source limits. No stored metadata alone
permits scene mutation or paid resubmission.
