# SDK catalog, pricing and history

Integration status of the SDK catalog, project selection, cost previews, submission and history. The [runtime map](runtime.md) lists the entry points and shared components.

## Active SDK catalog

Blockout Design/Refine now use
[BlockoutJobs](../../scenario/blender/blockout_jobs.py), owned by the selected
JobSession. UI and MCP share exact quote approval, durable submission, polling
and complete text retrieval. Guarded delivery stores a plan; explicit local
Build plan creates geometry. No unbound prototype event can apply a late plan.
The [Blockout contract](../BLENDER_JOB_CONTEXT.md#blockout-plan-commands) describes
scene ownership and recovery limits. Saved-plan recovery reads complete text
through that session, then requires a separate approval bound to a newly selected
scene and its unchanged Blockout fields. It replaces the stored plan only;
geometry still needs Build plan. This scoped integration does not establish
Film or complete release acceptance.

[SDKCatalog](../../scenario/core/api/sdk_catalog.py) binds model list/detail
reads to the explicitly selected API-key pair. The application owns this context;
opening or closing a panel does not replace it. The existing manager dispatches
reads off the main thread through one reusable SDK adapter/HTTP pool per catalog
connection. Retirement disables later requests immediately; the last active
reader closes the pool. Extension teardown does not wait for catalog network I/O
or close a pool underneath an in-flight request.

Overlapping list refreshes share one complete paginated SDK read for the same
connection and privacy scope. Public and private lists can progress independently;
each caller receives its own records. Failed or interrupted pagination preserves
the previous complete cache, releases all waiting callers and permits an explicit
retry. A later refresh still reads the service. Retiring credentials rejects the
pending result and clears both privacy caches.
List records are converted before publishing the cache. Malformed-record
conversion failures preserve the previous list and reach every overlapping caller
as a sanitized `ScenarioError`, keeping failures on the catalog event queue.

The same connection also exposes core reads for later trained-model routing
([#97](https://github.com/scenario-labs/blender-plugin/issues/97)). Explicit bulk
model summaries are read at most once per connection, share pending reads, are
cached apart from form schemas and are cleared on retirement. Trained records
from the private list and public LoRAs are classified from REST fields; a
malformed `type` is unsupported rather than an exception. The native catalog
load now reads the private trained list as well, and the picker's Trained chip
reads base-model descriptions on demand
([trained models in the native picker](../SDK_ADOPTION.md#trained-models-in-the-native-picker)).
Local MCP `list_models` with the trained source starts the same reads and lists the
same rows, `estimate_cost` and `render_form` build trained choices with the same
`trained_routes.apply` as the forms, and `model_defaults` uses the forms' defaults
owner ([trained models through local MCP](../MCP.md#historical-upstream-reference)).
Lane lists and the picker's other
chips keep their former exclusion of LoRAs, compositions and other trained
types, and still list a private custom model that reaches them.
See [trained-model catalog reads](../SDK_ADOPTION.md#trained-model-catalog-reads).

The GUI pump and main-thread MCP catalog/schema calls deliver the same queued
completions. An MCP call that needs a model description not loaded yet waits for
its read's worker on the request thread, then delivers it on the main thread
([MCP quote contract](../MCP.md#historical-upstream-reference)). Credential changes retire the context, discard its model/schema
caches and visible quotes, and reject late success/error events from the old
context. Main-thread entry points and GUI ticks mirror Blender's online-access
permission into a thread-safe event; each SDK request, including subsequent
catalog pages, checks that snapshot. Workers never read `bpy`.

The list is delivered before the curated model schemas finish warming. Each
successful detail becomes available independently; the final catalog event
rebuilds derived lane choices with the available details. One failed detail does
not hide the list or successful neighbors. Warmup events preserve existing
visible estimates, including the derived 3D/Edit 3D lanes. The provisional list
does not start a second bulk schema warmup. A selected model can still request its
detail independently; concurrent reads of the same model share one request. An
explicit selection or mode/task change invalidates its quote immediately, and
schema completion re-arms pricing if the estimate timer observed a missing schema.
That intent survives failed detail reads until a successful retry; switching
models does not re-price the new selection, and credential retirement or active
runtime reset clears retained intent and schema caches.
Restoring a dynamic enum's index to the same stable model id does not count as a
new selection. All events retain the credential-context identity check.

Catalog-driven form synchronization does not tag a scene for a dependency
update, except when an Edit 3D or Environment form's saved model left the
catalog: selecting the first model then goes through RNA. Blender tags the scene on every RNA assignment to a property with an
update callback, even with an unchanged value, and that update makes the scene's
[captured origins](../BLENDER_JOB_CONTEXT.md#origin-and-quote-lifetime) stale.
Seeding a new or replaced parameter with its schema default, correcting an enum
value the schema no longer offers and restoring a model enum index therefore
store raw values without callbacks. A seeded form re-arms its own price in its
own scene, and a seeded model duration still drives the camera path. A refreshed
base description that changes a form's LoRA strength range, or any other LoRA
input its trained choice runs through, such as the declared default strength,
re-arms that form's price the same way. A base's first description in a session,
as after reopening a file, has no earlier inputs to compare: a price saved with
the form stays unless its saved strength range differs. Removing an
obsolete parameter alone does not re-arm a price. A trained-model project default
applied automatically to a form without an explicit choice
([project selection](#optional-project-selection)) follows the same rule: it
stores the model enum index and LoRA strengths as raw values, runs the form's
model synchronization itself and re-arms that form's price. Explicit selections
and edits that assign a model, mode, task, parameter or LoRA strength still use
RNA and invalidate captured origins. A default's **Use** always re-arms its
form's price, and invalidates them only when it assigns a different base model
or a LoRA strength; a composition used on the form's current base writes only
the form's trained list. Request bodies, models and render forms keep their own
exact checks at approval.

Caches are connection-local and in memory. The active path does not reuse the
prototype's unscoped disk model cache. Restart therefore requires a catalog
refresh. API-key requests use the server's credential-bound scope without requiring
team/project selection. Missing generated discovery resources in
[SDK issue #29](https://github.com/scenario-labs/scenario-sdk-python/issues/29)
are bridged by the adapter's [named extensions](../SDK_ADOPTION.md#sdk-resource-extensions).
Discovery is optional and does not infer the key's default project from the first
listed project. Opening the catalog also opens the
[credential-bound local job store](../JOB_STORAGE.md#identity-and-ownership).
Its installation-local HMAC pseudonym binds the exact selected key/secret pair to
`JobScope.account_id`; it is not a discovered server account and is never sent to
the API. The catalog's adapter carries the same scope. Credential changes retire
both selections; switching back or restarting reopens the original records.
Missing/corrupt local scope keys and storage failures block context creation with
an explicit error rather than silently replacing history. Model/schema caches
remain in memory. Opening the store alone does not activate a `JobSession` or
job workers. Quote, submission, upload and recovery entry points lazily activate
the shared session described below. They use the same selected scope while the
catalog retains its own metadata pool. Prototype jobs are not migrated or resumed;
[local prototype records](runtime.md#local-prototype-records) remain read-only snapshots.

**Test connection** uses the same SDK context for one fresh model-list page of
size one. It runs on a worker, leaves cached models intact, and shares a pending
check across repeated clicks. The GUI/headless completion queue updates status
only for the current connection and request. In background mode, the operator
waits for its connection worker and drains that queue without a GUI timer. The
account strip shows pending, error or success independently of cached models.
Credential changes and runtime reset
discard late success and failure. The result confirms model access; it does not
derive account/project identity or activate durable jobs. Failures carry the
adapter's fixed status text: HTTP 401 and 403 ask to check the selected key and
secret, a 403 names the Project ID only when that request carried the override,
and 429 asks for a later retry. Other statuses keep the generic HTTP text. Model
record reads through `SDKAdapter.model` are the exception: their HTTP 403 or 404
reports that the model is not available to the selected credentials or project.
No message includes response bodies, URLs or identifiers.

Explicit local MCP recovery calls now lazily activate the selected
[JobSession](../BLENDER_JOB_CONTEXT.md). `list_local_jobs` reads its durable
records and `cancel_prepared_job` cancels only an unsubmitted intent, guarded by
the observed revision and a current-context token. Neither sends service requests
or imports prototype jobs. The session owns a separate SDK pool with the same
credential scope and online-permission snapshot as the catalog. Credential changes
and runtime reset stop admission; in-flight receipts keep their original scope.
File loading retires the selected owner and invalidates its context token; the
next recovery call creates a fresh session against the same scoped store.
GUI ticks and the headless MCP loop reap retired sessions after work finishes.
Native model form entry points, explicit saved-job controls and result transfers
across model lanes use this session, as described below.

## Optional project selection

Preferences expose an explicit optional Project ID for either credential source.
`runtime.project_id` trims surrounding whitespace and treats blank as no override;
the shared JobScope validates nonempty IDs before storage or SDK construction.
The selected value is passed to `open_credential_store` and therefore to both
catalog and job SDK pools. Neither discovery nor a guessed server identity is
required. This uses the existing SDK `project_id` query configuration unchanged.

Project changes follow the same retirement path as credential changes: clear
catalog/schema/history/quote projections, deactivate the old session and reject
late callback authority. In-flight receipts remain in their original scoped store.
Returning to a project reopens its records with fresh session approval identity;
normalized-equivalent edits keep the current session. Explicit trained-model lane
defaults follow the same selection: `ensure_model_defaults` opens an owner of the
selected store's scope without any service request, and this retirement path
retires it, so a default reviewed under the old selection cannot be written into
the new one ([runtime owner](../JOB_STORAGE.md#runtime-owner)). The native Image and
Render Image forms read those defaults into a per-selection cache that drawing only
reads; the GUI pump, before pricing, and local MCP render inspection, quotes or
`configure` without a model give a form with no explicit choice its lane default
once the route checks, without tagging the scene
([catalog delivery](#active-sdk-catalog)), and block its price
with the reason otherwise
([trained models section](../UI_STYLE.md#trained-models-section-and-project-defaults)).
A form that fails to take the default keeps waiting without keeping it from the
other forms, and Save or Clear still succeeds once the store has written it; the
pump reports the failure and retries that form, and local MCP logs it and returns
the wait. Read-only history and Film controls check both credentials and project
without mutating state in drawing.
Prototype jobs have no reliable credential or project ownership. Their automatic
service engine is retired for every selection, including the default project;
clearing the override cannot resume it. See [local prototype records](runtime.md#local-prototype-records).
This does not migrate prototype data or establish live project permissions,
discovery or complete release acceptance.

## Active SDK cost previews and model submission

Native model forms use [ModelJobs](../../scenario/blender/model_jobs.py), a main-thread
facade over the existing selected `JobSession`, coordinator and worker pool.
It does not own another executor or persistent registry. UI and MCP prepare the
same model inputs; each quote captures the scene before a worker retrieves fresh
SDK model metadata and the exact server estimate. UI cost delivery is keyed to
the originating scene/form. Only the currently selected scene can receive a
usable quote. Completion is routed by the ticket's originating lane, not the
currently selected tab. Repricing releases only that form's former approval.

The result action **Remove background** selects a current Image background-removal
model and prepares one local file reference with default settings. The user
explicitly uploads the reference, reviews the shared exact estimate and chooses
Generate. Failed file/model/schema
preflight preserves the current form; a successful preparation invalidates its
old quote without canceling existing jobs or admitted uploads.

Clicking **Generate** in any model form requires its unchanged ready quote. MCP `generate`
for every model lane requires its `quote_id` and the explicitly approved `cu_cost_exact`
string as `approved_cost`. The quote also binds its lane: callers cannot turn an
Image approval into a render, material or other lane submission. A displayed float
is not used to reconstruct the price. The 3D tab in Edit mode resolves to the
Edit 3D lane through one rule (`props.effective_lane`) shared by the pricing pump,
the sidebar and Settings forms, the composer and the Generate operator, so a ready
3D-tab price cannot label or enable an Edit 3D submission. The model picker maps a
chosen model to its form in the other direction. The composer writes its text only
into the form it was synchronized from, never into a form that replaced it.
The facade consumes the handle before preparation; the coordinator persists an
intent and claims `submitting` before the single SDK request. Repeated clicks,
reused quote handles, changed inputs, stale origins and failed writes cannot
repeat that submission. Timeouts retain uncertain saved state without retry; an
[identical request](../JOB_COORDINATOR.md#identical-unconfirmed-requests) in that
scope is refused until Scenario confirms it or the user marks it reviewed in Blender.
An unsuccessful UI attempt clears the ready price and requires explicit repricing;
it never silently obtains another approval. Retained MCP/UI approvals are not evicted
to admit new quotes. At capacity, new estimates are rejected until a consumed handle
can be reclaimed; repricing a UI form explicitly releases its previous approval.

GUI and headless main-thread context maintenance drains completed submissions,
polls their known remote IDs at two-second intervals and downloads successful
results through the coordinator. Saved state is projected into the existing Jobs view. This projection is
not registered with the prototype manager. Each poll also projects the latest
validated remote status and progress fraction, and submissions record a
display-only scene and lane binding; both stay in memory and MCP `job_status`
reports the same values ([projection rules](../BLENDER_JOB_CONTEXT.md#remote-progress-and-scene-lane-binding)).
A retrieval that observes a failure also projects the `metadata.error`/`metadata.hint`
reason, shown when plain text and otherwise a fixed sentence, as the failed view's
error, in memory only
([failure reasons](../BLENDER_JOB_CONTEXT.md#failure-reasons)).
The GUI pump compares the drawn job rows and online access each tick and
redraws the 3D viewport and Preferences regions only when they change, so
progress and the offline line update without hovering. The compact composer's
job strip reads the newest bound view of its scene and form from that same
in-memory projection, without creating an owner or reading storage, and
dispatches only the shared saved-job descriptors' operators through their own
invoke path ([strip rules](../UI_STYLE.md#compact-job-strip)). Closing a
panel does not stop work; credential/file changes retire the owner while
in-flight receipts stay in the original store. Local MCP status can inspect
these records after restart.
The model-job pump and explicit inspection retain scoped shared sidebar rows,
deduplicated by local job ID, with the owner's current projection replacing stale
copies. Existing rows keep their order while newly observed rows appear first;
shared rows take precedence over prototype collisions. Only prototype rows have
a fifty-row display limit. Active recovered jobs remain visible even without
creation timestamps. These display rules do not delete saved jobs or stop workers.

Local MCP uses the [shared reference upload path](../SDK_UPLOADS.md#active-reference-uploads);
generation forms expose **Upload reference** for typed local files and image
stills, with guarded lane/input attachment and saved inspection.
Render forms prepare scene snapshots and optional first-frame uploads in
explicit role-bound slots. Native saved-job controls and local MCP can also
hand one downloaded saved PNG, JPEG or WebP result to the Render Video
first-frame slot by reusing its asset ID, without an upload or a stored file
path; see the
[handoff command](../BLENDER_JOB_CONTEXT.md#saved-image-to-render-video-first-frame). Saved video/audio, GLB, glTF, FBX, OBJ and mesh PLY
models, material maps and supported mesh edits use explicit destination approval through the shared session; their
format and target limits are described below and in the application guides.
Explicit viewport/camera clips and selected-mesh GLB uploads now share the typed
reference lifecycle through both forms and MCP. They stage private snapshots;
clips preserve the preview/scene range without duration padding, and mesh export
restores selection. Later source edits do not change an uploaded reference.
Native recovery can refresh known uploads, cancel unclaimed preparation, upload
again one that cannot resume and clean terminal staging copies. Reusing an
imported reference after restart or form changes requires a fresh destination
confirmation and invalidates old prices.
Existing Scenario asset IDs are supported. Downloaded images
are receipt-verified again on the main thread, decoded from private snapshots and
packed as image datablocks after a durable application claim. Automatic import
supports bounded 8-/16-bit grayscale/RGB/RGBA PNG and scanline OpenEXR; other formats remain saved
without import. It does not assign textures or replace scene targets. A stale
origin, read/download error or uncertain application stops automatic delivery
without another generation. The [transfer policy](../RESULT_TRANSFERS.md#active-image-delivery)
names the documented CDN hosts. The Jobs panel's **Inspect saved jobs** button
and MCP `list_local_jobs` provide saved state. UI recovery buttons and
`recover_local_job` use the current context token and observed revision to refresh,
resume polling/download, cancel a known inference job, reconcile interrupted download
receipts, or save a pending import receipt. These actions never submit generation.
Resuming after restart does not approve import into the new scene. **Import saved
images** captures the selected destination for confirmation. MCP uses
`prepare_result_application` followed by explicit `apply_result_application`.
Both reverify local bytes and recheck the approved scene revision before an
atomic application claim saves the destination separately from the original job
origin. An interrupted import cannot be claimed again.
Its `job_status` and `wait_for_job` return the current saved state; active jobs
advance through the same maintenance pump. `wait_for_job` waits on the HTTP worker
while the main thread remains available for delivery. It returns at completion,
review-required state or timeout, and rejects a changed credential context.
An unresumed restarted record is returned immediately.
Files, captures and Spark preparation must finish before the final quote. Render
forms require an uploaded scene snapshot and, when enabled, an uploaded or
handed-off first frame, then use the same quote and submit path. An empty look
with automatic Spark enabled remains blocked; users can enter a look or disable
automatic Spark for the default look. Non-image model jobs use the same durable
submission, polling, download, cancellation and recovery commands. They stop at
saved `ready` results without automatically assigning materials, importing meshes
or inserting media strips. Status includes the saved
result manifest and receipt presence, without returning paths for unverified
application. Restarted display records are generic model jobs; the original lane
is not persisted, so they have no scene/lane binding. Explicit PNG/EXR import
remains available by result type.
For supported video/audio, **Add video/audio strip** and MCP
`prepare_result_application(asset_id=...)` capture one asset and the selected
scene/frame for approval. The shared command verifies local receipts and claims
application before inserting one strip. Receipt retry never repeats insertion;
see [saved media application](../BLENDER_JOB_CONTEXT.md#explicit-saved-video-and-audio-application).
The same asset-selected MCP approval and the per-unit **Import <format> model**
controls support one classified import unit at the captured cursor: a
self-contained GLB, a JSON glTF with its saved buffers and images, a binary FBX,
an OBJ with its saved MTL and maps, or a mesh PLY. The main thread rehashes every
member into a private snapshot before the claim, then imports it in a disposable
scene, preserves existing selection and packs textures only from the package. GLB
and glTF rigs, weights and node/morph animation clips remain in the new group;
timing uses scene FPS without changing the current frame or timeline range, while
FBX clips keep the file's rate. Its separate durable claim, which lists every
member for a reuse, and receipt recovery follow the same session. SPZ, `.splat`
and Gaussian splat PLY results are decoded by a worker in the local-operation
slot with reviewed point limits and axes, then built on the main thread as a new
point cloud (mesh plus Geometry Nodes and a new material) under the same claim
and receipt rules; it approximates splats and is not a splat renderer. In-place
rig/animation transfer remains open.
Saved PNG, JPEG and OpenEXR results, including `image/aces`, also offer explicit
World replacement unless a texture role or a declared header size other than 2:1
rules it out, with a separate guarded restore in the current session.
Application refuses a file whose container differs from its saved media type;
[World application](../WORLD_APPLICATION.md#accepted-local-files) lists the
accepted variants. Completed jobs can reuse saved results
through fresh UI/MCP approval and a separate durable local claim, preserving the
original generation outcome. Interrupted local claims block another application.
Saved texture sets also offer explicit one-mesh material-slot approval, using
[stored roles and packed material construction](../MATERIAL_APPLICATION.md).
MCP lane `panorama` (Environment) lists only the models of the bpy-free
[panorama contracts](../../scenario/core/scene/panorama_contract.py) and prepares
their requests through them before the shared quote: locked and lane-default
values, 2:1 sizes, and panorama references limited to saved, downloaded 360
results of the selected scope. A changed model schema fails closed. The lane's
jobs have no automatic image or World application, and the same models stay in
the Image and Render Image lanes. The native Environment tab, Studio Create and
the composer's Generate mode build the same contract body through
`generation.build_request`; native panorama references are rechecked saved
results or files that passed a 2:1 preflight before a byte-bound upload. See
[Environment lane delivery](../BLENDER_JOB_CONTEXT.md#environment-lane-delivery).
Render-lane MCP calls use the native form preparation described below, including
uploaded inputs, prompt decoration and separate Spark approval. Do not describe this slice as
complete generation or supported release acceptance.

## Active SDK history

Cloud history uses the selected catalog connection's SDK adapter and HTTP pool.
`jobs.with_raw_response.list` reads one page at a time with inputs/results visible;
`assets.with_raw_response.retrieve` resolves at most 30 prompt previews per page.
Prompt text is local to each read, with no session-wide or cross-credential cache.
Unavailable or explicitly truncated text previews remain unresolved; an existing
local job's prompt can still supply the display text. History downloads no full
text; see [full prompt text](../RESULT_TRANSFERS.md#full-prompt-text).

A row's cost is `billing.cuCost` plus its `cuCostDetails` add-ons, such as
quality-gate fees, which the SDK job billing type documents as charged on top of
the main action. Workflow runs (`jobType: workflow`) are listed beside their
model steps. A run's own charge is normally 0, so its row adds the charges of
the steps named by `metadata.flow[].jobId` or by a step's `metadata.workflowJobId`.
The history worker reads a finished run's missing steps with
`jobs.with_raw_response.retrieve`, at most 24 per page. It chooses whole runs,
those needing the fewest reads first, so one large loop cannot starve the other
runs on the page; a run that does not fit gets no reads, since a partial read
could not price it. A second pass reads the missing steps of nested runs that the
first pass found, within the same 24 reads, and never retries an identifier.
A failed or malformed step read, a nested run still running, a cycle, nesting
deeper than four runs or nesting that would need a third pass leaves the run's
cost unavailable instead of reporting 0; a running workflow shows no cost yet.
Step rows name their run so the total is not counted twice. Malformed billing on
a listed row makes only that row's cost unavailable; malformed metadata still
fails the page. Drawing performs no reads.

The existing worker queue delivers pages to both the GUI and headless MCP.
Connection identity and request keys reject superseded results and errors;
credential retirement clears visible cloud history and its cursor. A failed read
preserves the last valid page. Empty successful pages count as loaded, duplicate
rows are suppressed, and repeated pagination cursors fail without changing the
visible page. MCP `list_generations(refresh=true)` explicitly refreshes or retries;
subsequent calls without `refresh` deliver/read the shared result. Repeated UI or
MCP refresh requests reuse a pending history read instead of starting more workers.
The MCP `refresh` argument accepts only JSON booleans; other types fail before
starting or delivering history work.

Each page keeps model inference (`custom`) jobs and workflow runs; uploads and
mesh preview renders are skipped locally, so a page can list fewer rows than it
read, or none. The panel draws every loaded row, keeps **Load older** while a
cursor remains, and says so when a page listed no generation. MCP
`list_generations` reports rows beyond its `limit` as `more_loaded` and a
remaining cursor as `older_page`; `older=true` requests that page through the
same cursor and pending-read rules as **Load older**, and retries a failed older
read. It accepts only JSON booleans and cannot be combined with `refresh`. A
failed older read sets a separate older-page error and keeps the loaded rows and
cursor, as **Load older** does: later MCP calls return those rows with
`older_error` and a retry note, and only a repeated cursor points to `refresh=true`.

Cloud rows now identify matching jobs in the selected credential-bound store.
Those rows offer **Inspect saved jobs**, which exposes the existing recovery and
destination-approval controls. The native history import entry point rechecks
current storage even when the displayed page predates a remote acknowledgement.
MCP status and import lookup likewise prefer scoped saved records to an old
unscoped cache; ambiguous remote IDs require an explicit local request ID.
Neither path borrows the old cache's files or silently applies a saved result.
Explicit history reads cache saved remote IDs for drawing; live shared-job views
cover acknowledgements delivered after the cloud page. Drawing performs no job
database reads. Native import and MCP history/status/import responses still
recheck current storage, independently of the display snapshot. A failed saved
read disables cloud-row actions until an explicit read succeeds; switching
credentials clears the snapshot. Storage failures do not fall back to the older
import path.
MCP import also requires complete selected credentials before consulting either
store; missing credentials permit cold read-only inspection, never application.

The cloud page itself remains an in-memory browse result. A
[shared cloud adoption command](../JOB_COORDINATOR.md#adopting-a-completed-cloud-job)
can verify and save one completed model job without fabricating a local quote
or submitting generation. Native **Save for recovery** and MCP
`recover_cloud_job` share its bounded pending reads and paused saved-job view.
Completed MCP results remain bound to their owning facade independently of the
16-entry display cache. Job rows merge by request ID without periodic reordering,
following the sidebar row rules above.
Neither uses old cached files, downloads results or applies to the current scene.
The old MCP `import_result` rejects prototype files with recovery guidance.
History rendering ignores legacy file/action projections, including a duplicate
session row, while explicit saved-result commands retain their byte verification
and destination approval.
