# Quote-bound job commands

[`JobCoordinator`](../scenario/core/jobs/coordinator.py) implements synchronous
commands for the shared runtime. It connects the scoped
[SDK adapter](SDK_ADOPTION.md) to the [intent store](JOB_STORAGE.md). It has no
worker pool, timer, UI ownership or bpy calls. UI/MCP model quote and submission
use these commands through [ModelJobs](../scenario/blender/model_jobs.py).
Native forms require completed reference/preparation inputs before quoting;
files, mesh/clip snapshots and render scene/first-frame references use explicit
upload commands. Render look preparation composes these commands with a separate
approval before the render quote.

## Prepare, claim, send, acknowledge

1. Build the adapter with the selected service, non-secret account/team identity
   and optional project. `account_id` is required for the coordinator even though
   read/estimate-only adapters may omit it. Missing identity fails before any
   request; a store with different scope is also rejected.
   For explicit API keys, the active runtime's
   [credential-bound local store](JOB_STORAGE.md#identity-and-ownership) supplies
   the same local account pseudonym to the catalog adapter and store. No server
   identity discovery is needed. These identities are not proof that credentials
   have been accepted by the remote service. The selected `JobSession` owns the
   coordinator.
2. Obtain a real estimate through the adapter. Only the actual unchanged object
   issued by that active adapter is accepted; copying its public fields does not
   create another issued quote.
3. After the caller chooses the quote, `prepare(estimate, origin)` persists a
   fresh immutable intent and returns an ephemeral `PreparedJob`. This does not
   send a paid request. The local quote lifetime defaults to 120 seconds from
   **estimate issuance**, not preparation. It is a local freshness policy, not a
   claim about server pricing guarantees. Injected clocks must use the same
   monotonic time domain as the adapter.
4. An explicit `submit` command supplies the current normalized payload,
   operation/target and captured origin including its revision. These must match
   the prepared request. UI callers must invalidate/rebuild the current request
   when fields, account/project, scene or target revision change.
5. Inside the adapter's required `before_send` callback, the coordinator commits
   `prepared → submitting` using the current stored revision. A failed write,
   stale record, inactive coordinator or expired/mismatched quote stops dispatch.
6. The adapter consumes the issued quote once and calls the operation's public
   `with_raw_response` method (`generate.run_model`, `workflows.run`,
   `generate.prompt` or `generate.translate`), with `max_retries=0`.
   Submission omits `dry_run`; estimates use `dry_run="true"` in the query.
   Optional project scope stays in the query, and the quoted payload is unchanged.
7. A valid remote job ID is committed before the command returns a remote record.
   No result is applied to Blender by this command.

`submit` is a spending command for an explicitly authorized user/MCP action.
Neither a quote, `PreparedJob`, callback nor persisted record grants spending
permission by itself. Integration must preserve its own approval boundary.

## Failure and context boundaries

Transport/HTTP failures and missing/malformed receipts after a durable claim
produce `SubmissionUncertain` and store `uncertain`. This includes remote IDs
that cannot satisfy the store's identity rules (such as overlong IDs or internal
Unicode whitespace), even if SDK-level ID validation accepted them. No failure
triggers a second request, and a consumed quote cannot be reused through another
coordinator.
Even an apparent HTTP rejection is treated conservatively until reconciliation.
If saving uncertainty or the successful receipt fails, `StoreError` propagates;
the durable `submitting` record remains recoverable and cannot be resubmitted.

Closing/deactivating an account context invalidates queued preparations. Network
work is outside the coordinator lock, so `deactivate()` does not wait for an
in-flight response. Already claimed work can finish; its receipt stays bound to
the original account/project and origin. It must never be applied to a newly
selected scene or object. A worker owner must wait for active calls before closing
their HTTP clients. View closure alone must not deactivate application jobs.

Restart recovery must not recreate a `PreparedJob` from stored fingerprints.
Stored `submitting`/`uncertain` requests require reconciliation; similar inputs in
job listings are not authoritative correlation.

The low-level adapter hook orders persistence but cannot enforce correct
behavior by arbitrary callers; product code must use the shared coordinator.

## Identical unconfirmed requests

A request is refused while another one in the same credential scope, with the
same operation, target and `payload_sha256`, is still `submitting` or
`uncertain`. Such a request may have been sent without a confirmed job, so a
second one could be charged twice. `DuplicateRequestError`, a `QuoteError`,
names the earlier request: one still `submitting` asks the user to wait for
Scenario's answer, while an `uncertain` one asks for a review. The check runs after the free dry run but before a
quote is returned, again before preparation, and once more inside the
submission claim under the coordinator lock, so concurrent identical
submissions send at most one. A confirmed `remote` job never blocks: repeating a
generation is a deliberate new request. The store's `unconfirmed()` lookup
filters these rows in SQLite, so quoting never decodes unrelated history.

There is deliberately no command that resolves an uncertain record. The user
checks Scenario history (and can save a completed job with **Save for
recovery**), then marks the record reviewed through
`mark_unconfirmed_reviewed(request_id, expected_revision=...)`. The mark is kept
in memory by the coordinator only: nothing is sent or saved, and a new
coordinator, after a restart or context change, refuses again. `ModelJobs`
offers it as the native `mark_reviewed` action behind a confirmation; MCP
`job_status` omits it and `recover_local_job` refuses it, so an agent cannot
let a possible duplicate through.

A dry-run response that carries a job raises `EstimateStartedJob` in the adapter
and issues no `Estimate`. The adapter also records the event for the process, so
automatic form pricing stays off until Blender restarts even when that quote was
superseded or discarded unread. `ModelJobs.finish_quote` turns a delivered error
into `AutomaticEstimatesStopped`, naming the job. Nothing is resubmitted. Cloud adoption accepts only
completed jobs, so the user saves that job from history once it finishes.

## Local capture queue

`render_local(spec, origin=..., source_origin=..., cancel=...)` renders a private
snapshot with no Scenario request. It validates the active owner and both
captured origins before and after local rendering, outside long-held locks.
Its `LocalCaptureResult` binds the media receipt to the selected scope, recipe
origin and shot origin for main-thread delivery.

`JobWorkers.render_local` uses the existing bounded queue, admitting at most
one local renderer per owner. Its cancellation event is separate from network
commands: explicit local cancellation or owner deactivation signals the renderer,
while already claimed remote work still preserves original-scope receipts.
Queued local work is canceled on retirement. Shutdown joins owned workers before
the Blender session releases capture directories. Local render approval does not
create a paid generation intent or authorize uploading the output.

## Local Film export thread

`export_film(spec, destination, origin=..., source_origin=..., cancel=..., progress=...)`
renders, verifies and publishes a local MP4 with no Scenario request. It
re-checks the destination's portable shape and refuses a reused snapshot, checks
the active owner and both captured origins, reserves the destination and only
then runs the free-space preflight, so a missing folder is reported as such.
Before publishing it checks only the active owner; a later scene edit does not
cancel the approved bytes. Private-root policy belongs to the Blender session.
`publish_film_export` copies a staged export issued by this coordinator to
another destination without rendering; it checks the active owner, not the
origins. `LocalExportResult` carries the published receipt with the selected
scope and the origins its command received: an initial export checked both
before reserving, while a re-publish carries the caller's values as given. It
grants no scene, upload or spend authority. Deactivation forgets issued staged
exports, and a render that finishes afterwards registers none.

`export_case_study(spec, destination, ...)` does the same for a
[portable case-study bundle](FILM_PLAN.md#portable-case-study-bundle): it requires a
`.zip` destination, reserves it after the same checks, runs the free-space
preflight for the media copies and archive, builds and verifies the archive and
publishes it. Like a render, a build that finishes after deactivation registers
no staged file and publishes nothing. `publish_film_export` re-publishes either
kind of staged file with its own suffix, and the local log names a video or case
study with its basename and SHA-256.

`LocalExportWorker` runs these commands on its own named thread, one at a time
per owner, instead of the bounded `JobWorkers` queue. A multi-hour export
therefore never holds a polling/download worker or the capture/review local media
slot. It refuses a relative destination before starting the thread. Deactivation
cancels the running export and reaps its child. Shutdown waits at most
`EXPORT_STOP_TIMEOUT` (five seconds) in total for the daemon thread before the
coordinator closes. A read blocked on an unresponsive mount leaves it running
with a logged warning and `stopped` false; at its next cancellation check it stops
before starting a child or replacing the placeholder. See the
[export contract](FILM_PLAN.md#offline-film-video-export-primitive).

## Film model task quotes

`quote_film_task(recipe, production_id=..., task_id=..., origin=...)` uses the
[Film task contract](FILM_PLAN.md#durable-model-tasks) to resolve model-task
references from the selected store and obtain an ordinary SDK model estimate.
It captures the production/task and recipe/dependency digests in the owned
`OriginQuote`. `prepare_quote` carries this binding into the same atomic intent
creation, whose unique take reservation is the final concurrency check before
submission. Two estimates do not authorize two attempts of the same take.
The generic `submit` command retains its exact payload, cost, ownership, expiry,
origin and no-retry boundaries. Film metadata never enters the service payload.

The command reuses `SDKAdapter.model`, `estimate_model` and `submit_estimate`
(public `models.with_raw_response.retrieve` and `generate.with_raw_response.run_model`).
It adds no endpoint or raw fallback. API-key default project scope remains valid.
`JobWorkers` snapshots recipe data before queueing. `JobSession` captures/delivers
the existing origin; a changed scene or retired owner rejects the quote before
preparation.

`bind_film_upload(recipe, production_id=..., task_id=..., request_id=...,
expected_revision=..., origin=...)` validates an upload task and reads the selected
scope's existing imported upload locally. It saves an immutable
[association](JOB_STORAGE.md#film-upload-associations) under the origin guard and
returns a `FilmUploadResult` for that caller's captured origin. Repeated equivalent
associations preserve the first binding while allowing delivery to the current
reader. A retired owner or stale scene cannot write a new association.

Dependent model quotes recheck the saved upload's imported state, exact revision,
source digest, kind and asset before resolving its single output. The command
uses `UploadCommands.inspect`, adding no SDK call, transfer or raw fallback.
Upload initialization/completion, cleanup and uncertainty retain their existing
commands. Binding is not permission to send another upload or apply a result.

## Adopting a completed cloud job

`adopt_cloud_job(identifier, expected_model_id=..., origin=...)` retrieves one
explicitly selected model job through the existing `SDKAdapter.job` method.
It checks the exact job ID, successful status, `custom`/`inference` job type,
`metadata.input.modelId` and a bounded unique list of output asset IDs.
The selected coordinator must remain active before the read and at the final
durable write. Scene changes before or during the read do not discard metadata;
the saved origin remains provenance, never destination approval. HTTP work does
not hold the coordinator lock. Quote, submission and application commands retain
their separate origin guards.

The command creates a [cloud result record](JOB_STORAGE.md#explicit-cloud-result-records)
with no fabricated quote, request payload or source-object provenance. A matching
existing record is preserved. No estimate, paid submission, download or Blender
mutation occurs. Failed verification writes nothing. Storage errors do not
report successful adoption; a later explicit read preserves any committed
record and can safely retry without spending.

`JobWorkers` and `JobSession` queue this command through their existing bounded
pool. A saved record then uses ordinary manifest/download recovery and explicit
destination approval. Native history **Save for recovery** and MCP
`recover_cloud_job` call this command through the same `ModelJobs` facade.

## Restart inspection and known-job refresh

`recovery_plan()` returns immutable records and their suggested next action for
this scope only. It performs no network call or write. Prepared requests need a
new quote; submitting/uncertain requests need authoritative correlation; known
remote jobs can be polled. Interrupted downloads and application require explicit
review, while completed/failed/canceled records are finished. Inspection does not
claim another process's worker is dead or silently change its state.

`refresh_remote(request_id, expected_revision=...)` only accepts the current
`remote` or `cancel_requested` record with a known remote ID. It uses `SDKAdapter.job`, which calls the
public SDK `jobs.retrieve` with the selected project and online-access guard.
A matching ID and recognized terminal state are committed before returning an
immutable `RemoteSnapshot`. Unknown states, mismatched IDs, read failures and
failed persistence preserve local state. A stale polling result cannot overwrite
a newer decision; simultaneous identical terminal results are idempotent.

The snapshot retains the response in memory behind copy-on-read JSON access.
Raw responses, result text and signed URLs are not persisted. An active refresh
commits nothing and keeps the saved revision; Blender's owner derives a validated
in-memory [progress reading](BLENDER_JOB_CONTEXT.md#remote-progress-and-scene-lane-binding)
from the snapshot's `status` and `progress` with the bpy-free
[`progress.observe`](../scenario/core/jobs/progress.py). The saved remote ID
allows result metadata to be fetched again. There is deliberately no command to
attach a guessed remote ID or regenerate an unknown submission.

`cancel_prepared(request_id, expected_revision=...)` durably cancels only an
unclaimed local intent and sends no request. A queued submit will subsequently
fail its stored-state check. Remote cancellation is separate: neither canceling
a Python future nor receiving an action acknowledgement proves server cancellation.

## Application-owned workers

`JobWorkers` owns a coordinator and its SDK client for an application context.
It starts a fixed number of non-daemon threads, bounds the pending queue, and
rejects excess commands before dispatch. `submit` copies the current JSON
payload at admission; the coordinator checks the original quote, origin and
expiry again when the worker reaches the spend boundary. `refresh_remote` uses
the same pool. There is no generic callable execution or Blender access.

Each command returns a `JobTask` with `done()` and `result(timeout=...)`. A GUI
owner polls from its main-thread pump; a headless owner can block explicitly.
No result callback runs application code on a worker. Results retain their
stored scope/origin; callers must verify the captured file/scene/revision/target
before any Blender mutation. Exceptions remain observable through `result`.
The owner retains task handles; workers drop completed task/payload references.
Ordinary command exceptions settle the task and leave worker capacity available.
Thread-control exceptions (such as `SystemExit` or `KeyboardInterrupt`) settle
that task, deactivate the owner and cancel queued execution, then propagate out
of the worker. The application owner still calls `shutdown()` to join other
in-flight work and close the client; no pending task is abandoned unresolved.

One more non-daemon thread, `ScenarioPreview`, runs only the
[preview commands](#saved-result-preview-commands) from its own bounded queue
(eight tasks by default) with per-task cancellation. It never runs job commands,
and job workers never run preview commands, so a slow preview poll or download
cannot delay remote refresh, result downloads or submission. Preview admission
is also independent of the single local media slot. `deactivate()` cancels
queued previews and signals the running one; a running SDK read can still take
until its client timeout. `previews_idle` reports when no preview command is
queued or running, so a main-thread owner can wait for it before `shutdown()`
joins this thread with the job workers. A thread-control exception in either
kind of thread retires the whole owner.

`cancel_prepared` persists local cancellation immediately. If a queued command
later runs, the coordinator rejects it before network dispatch. If dispatch has
already claimed the record, cancellation reports a conflict: it cannot claim
that remote work was canceled. A failed cancellation write propagates.

On a context switch, `deactivate()` stops admission, invalidates quotes and
cancels queued task execution without waiting for network calls. Their PREPARED
records remain available for recovery review; cancellation of execution alone
is not a durable user cancellation. Already claimed requests can finish and
persist only to the original store. Closing/collapsing a view must not call this
method. A file-load owner must suppress old-target application independently.

On extension disable/exit, `shutdown()` deactivates, joins in-flight workers,
then closes the SDK connection. It is idempotent and safe for concurrent owners;
it rejects calls from its own worker threads. It does not kill network I/O or
promise instant shutdown; HTTP timeout policy still applies. Direct synchronous
coordinator/adapter users must also be stopped before transferring ownership or
shutdown. One owner per coordinator is required: in Blender, the selected
`JobSession` (see the [runtime map](architecture/runtime.md)).


## Known model-job cancellation

`cancel_remote(request_id, expected_revision=...)` accepts only a current scoped
`remote` record with a known ID and a model operation. A fresh `jobs.retrieve`
observation must report `jobType=inference`. The public
[job action reference](https://docs.scenario.com/api/resources/jobs/methods/trigger_action)
and the pinned SDK 2.2.0 `jobs.trigger_action` both state "Today only cancel on
inference jobs is supported". The SDK's retrieve enum lists `custom` and
`workflow` as separate types, and the sanitized
[captured model-job fixture](../tests/fixtures/patina-copper-512/job.json) uses
`custom`. Any other type raises `CancellationUnsupported` before the claim: the
record keeps its state and revision, no action is sent and polling continues.
`cancellable_job()` is the same predicate for owners deciding whether to offer
cancellation. This is not a general workflow-cancellation contract; a workflow
stops only by [rejecting a waiting step](#waiting-workflow-step-decisions). A job
already observed terminal is committed without sending a cancellation. An SDK
contract test fails if the pinned documentation of this limit changes.

After eligibility checks, an atomic revision-checked store transition commits
`remote → cancel_requested` before the SDK action. This claim serializes competing
coordinators and Blender processes sharing the database; only one can dispatch.
A failed claim write stops the action. There is no lease expiry or reset back to
`remote`, so process death or a delayed first caller cannot allow another caller
to repeat the same cancellation. Stale revisions and inactive contexts fail too.

The winning caller makes one SDK `jobs.trigger_action(action="cancel")` call,
with the selected project, online-access guard and `max_retries=0`. The action
acknowledgement never establishes terminal status, even if it reports `canceled`.
A subsequent `jobs.retrieve` supplies the authoritative snapshot: an active job
remains `cancel_requested`, a completed job becomes `succeeded`, and only observed
`canceled` becomes `canceled`. Completion races retain their actual outcome.

Action/observation transport or malformed-wrapper failures report
`CancellationUncertain`; invalid status/identity evidence reports `RecoveryError`.
Both preserve the durable cancellation claim and known ID for explicit refresh.
Persistence and revision conflicts also propagate; failed writes never report
terminal success. After restart, `recovery_plan()` suggests polling that saved ID,
including if the process died after the claim but before sending. Recovery never
replays the action. A fresh explicit cancel command also rejects a claimed job;
a safe reattempt/reset contract is not implemented. This conservative boundary
can leave a job running after a claim-before-send crash or definitive action
rejection, while polling still recovers its eventual terminal result.

Readers that do not know `cancel_requested` fail closed on that record instead
of silently treating it as dispatchable.

`JobWorkers.cancel_remote` uses the bounded application pool and the same task
polling interface. Deactivation before the action is claimed stops dispatch;
once claimed, late observations stay bound to the original store. Shutdown
joins this work before closing the SDK. Scenario documents no endpoint to cancel
a running workflow step. Live cancellation of an `inference` job and which
generation lanes return `inference` jobs continue in #440.
`ModelJobs` exposes these commands through the shared UI/MCP saved-job controls.


## Waiting workflow step decisions

A workflow job stays `in-progress` while a `user-approval` or `user-selection`
node of its `metadata.flow` is `processing`: Scenario waits for one decision.
[`workflow_nodes.pending_steps`](../scenario/core/jobs/workflow_nodes.py) projects
those nodes from one `jobs.retrieve` response into immutable `PendingStep`
values, in flow order: node ID and type, iteration index and loop node ID of a
loop copy, dependencies (at most 64 listed), review asset IDs from every
dependency's outputs (at most 50, with their total) and whether every node's
links were read
(see [step links](#step-links)). A selection also carries its frozen candidates, at
most 50 asset IDs or texts of at most 4,096 characters each, and its declared
pick bounds (defaults 1 and 100). Signed asset URLs are never copied.
`review_id` hashes the projected fields with the remote job ID, so a re-signed
URL keeps it while any reviewed change replaces it. The response must be this
record's workflow job; a flow of more than 2,048 nodes, or nodes without string
`id`, `type` and `status`, raises `StepError`.

Each step lists the decisions this extension may send. A selection with
unreadable candidates or bounds is `unsupported`. A selection whose output
reaches a ForEach loop, a nested workflow or a node of a type the API reference
does not list, through input, conditional or nested references or `dependsOn`,
is blocked as `loop`. Both offer only `reject`. The exact estimate approved
before the run is not shown to cover the runs such a selection starts, so
choosing items stays blocked until live evidence shows the charge stays within
that approval. A node ID the adapter could not send, or one shared by several
waiting steps, is `unidentified` and offers no decision.

`decide_workflow_node(request_id, expected_revision=..., node_id=..., review_id=..., action=..., selected_indices=None)`
accepts only the current scoped `remote` record of a workflow operation, and one
decision per request at a time; a concurrent second call fails locally. It first
retrieves the job. A finished job is committed and returned as a
`DecisionSnapshot` with `sent` false, without a decision. The step must still
wait with the reviewed `review_id`, otherwise `StoreConflict`, and the action and
picks must fit it, otherwise `StepError`. After rechecking that the context is
active, the coordinator makes one `SDKAdapter.workflow_decision` call, never
retried. An HTTP 4xx reply raises `DecisionRefused` with its status; any other
failure, including a lost response, raises `DecisionUncertain`. Both try one
more retrieval, commit any terminal status it shows and carry it as `snapshot`.
On success, a fresh retrieval returns a `DecisionSnapshot` with `sent` true; if
that retrieval fails, the outcome is reported as uncertain.

No decision record is stored and the store schema is unchanged. After a decision
the step no longer waits, so the next retrieval shows the outcome, also after a
restart, and another decision must start from a step shown waiting again.
Rejection is the only workflow cancellation: the API reference documents that it
stops the workflow, and the retrieval's `canceled` status is committed like any
other terminal status. Completed steps stay charged. `cancel_remote` stays
model-only because the job action supports cancelling inference jobs only, and
a running workflow step cannot be stopped. `JobWorkers.decide_workflow_node`
queues the command on the shared pool with an immutable copy of the picks.
The service's handling of repeated or concurrent decisions, rejection inside a
loop, omitted `projectId` and the charge after an approval or selection are not
live-verified.

### Step links

A retrieved job's flow may omit the links between nodes: live jobs listed
`inputs` only on `user-selection` nodes and no `dependsOn` on `custom-model`,
`for-each` or `user-approval` nodes. In the job flow, a node with neither field
therefore has unknown links, not none. While a retrieval shows a waiting step,
`_observe_remote` reads the workflow definition once with
`SDKAdapter.workflow` (SDK `workflows.retrieve`), and
[`workflow_nodes.definition_links`](../scenario/core/jobs/workflow_nodes.py)
keeps only node IDs, types, input references, `dependsOn` and ForEach body IDs
as a `FlowLinks` value attached to the `RemoteSnapshot`. The coordinator keeps it
in memory for that remote job until no step waits; a failed read or an
unreadable definition attaches nothing and is read again at the next retrieval.
Such a failure logs only its error type, once per waiting period until a read
succeeds.
Read-only checks of live public definitions confirmed `inputs` references and
`loopBodyNodeIds`; definitions with approval or selection steps, and their
`dependsOn`, follow the SDK model and are covered offline only.

`pending_steps` uses the definition only when it describes the job: the same
workflow, an `updatedAt` no later than the job's `createdAt`, the job's nodes
with the same types, agreement with every link the job flow shows itself, and
every other job node a `<template>__iter__<n>` copy of a ForEach body node. Its
`dependsOn` then lists an approval's dependencies and review assets; inside a
ForEach copy, a dependency's copy from the same iteration replaces it. A copy
that omits `iterationIndex` or `loopNodeId` takes its iteration index from its
`__iter__<n>` suffix and its loop node ID from the one ForEach node whose
`loopBodyNodeIds` lists its template; a template two nodes list names no loop
node. Without that proof, a name is never read as a copy.
Otherwise the job flow's own links apply. If some node's links stay unknown in
a flow with a loop, a nested workflow or an unlisted node type, a selection not
proven to feed one is blocked as `unverified`, which offers only `reject`, so
a parsing gap never unblocks choosing items. `links_complete` is false while
any node's links are unknown; an approval is still offered then, with possibly
incomplete review assets. The links are part of the reviewed projection, so a
decision whose retrieval no longer reads them conflicts with its `review_id`.


## Result retrieval and download commands

`load_results(request_id, expected_revision=...)` rechecks the saved successful
job through public SDK `jobs.retrieve`, then reads every asset through
`assets.retrieve`, always under the original immutable project scope. Only a
matching successful job with a nonempty unique `metadata.assetIds` list (up to
128) is supported. Each retrieved asset must match its ID and declare success,
a valid MIME type and an integral nonnegative `properties.size`. An image asset
that declares an OpenEXR original, or an audio asset that declares a WAV original,
with a download destination is instead saved as that original, with its MIME type
and an unknown size; see
[declared HDR originals](RESULT_TRANSFERS.md#declared-hdr-originals-and-360-projection). Unsupported
non-asset outputs and malformed/changed metadata fail explicitly. No result list
is inferred from a similar job or current selection.

The entire URL-free manifest is committed once after metadata validation. Local
basenames derive from index, asset-ID digest and MIME extension; provider paths
and filenames never choose local directories. Signed URLs remain in memory.
For delivered image MIME types, the manifest retains an allowlisted texture role
from the SDK asset's `metadata.type`; ZIPs or models never become images because
of a generation label. See [result semantics](RESULT_TRANSFERS.md#texture-result-semantics).
Missing or unknown optional metadata remains unclassified.
These contracts come from the pinned SDK's job/asset response models plus the
captured model-job asset list; they still need live provider acceptance.

Configure the coordinator with `result_downloader=ResultDownloader(policy, ...)`
and an existing absolute private `result_root` from Blender's extension user
data. There is no implicit storage-host allowlist. The root is split by hashes
of the full account/team/project/service scope and local request identity, so
identical asset names in separate requests cannot overwrite each other.

`download_results` loads a missing manifest, durably claims `downloading`, and
retrieves each unfinished asset again for a fresh signed URL. Identity, MIME and
size must still match the manifest, apart from
[legacy OBJ/MTL byte counts](RESULT_TRANSFERS.md#legacy-obj-and-mtl-byte-counts).
A saved known texture role must match too.
An unclassified/legacy result remains unclassified even if a fresh response
supplies a role; that does not rewrite its manifest or grant material semantics.
The saved source and projection follow the same rule: the manifest's file choice
is refreshed, never switched to a newly declared original or relabelled as 360. The bounded downloader publishes without
overwriting files; its verification uses the same configured byte cap. Each
receipt commits before advancing to another asset. Existing receipts are
rehash-verified instead of redownloaded. All receipts must exist before `ready`.

A transfer, metadata or verification failure saves `download_failed` and reports
a sanitized error. A persistence failure propagates and can leave `downloading`
for explicit recovery inspection; it never claims an uncommitted state was saved.
An explicit retry accepts only `succeeded` or `download_failed`, refreshes missing
URLs and preserves completed receipts. It never deletes/replaces a suspect file,
resumes an interrupted worker automatically or calls generation. An unreceipted
published file after a crash requires separate explicit reconciliation.

`verify_results` returns the unchanged scoped/origin-bound record and verified
local paths for `ready`, `apply_failed` or `applied`. It performs no service call
or scene mutation and rejects a changed job revision. These paths are not an
application authorization: the Blender owner must recheck origin/target and
atomically claim application before changing the scene. Interrupted `applying`
is deliberately rejected; a prior scene mutation may already have occurred.

All three methods are available through `JobWorkers` using the same bounded pool,
SDK lifetime and main-thread polling interface. Deactivation stops subsequent
asset work; a transfer already in flight can save its receipt to the old scope.
Closing a view must not deactivate these application-owned workers. The runtime
configures the CDN storage policy, and ModelJobs drives downloads and recovery for
every model lane; recovered results apply only after fresh destination approval
(see the [runtime map](architecture/runtime.md)).


### Explicit interrupted-download recovery

`recover_downloads(request_id, expected_revision=...)` is an explicit offline
command for a saved `downloading` record. It rehashes every committed receipt.
If every asset has a verified receipt, it saves `ready`; otherwise it saves
`download_failed`, retaining all prior receipts. A later explicit
`download_results` command retries only missing assets using fresh SDK URLs.
Recovery itself calls neither the SDK nor generation, starts no transfer,
resolves no Blender target and does not apply results.

A missing or changed receipted file, an unreceipted destination (including a
symlink), unavailable storage or failed verification leaves the record and files
unchanged for review. In particular, size alone cannot adopt an orphan file
published before a receipt commit. Staging cleanup and orphan-file reconciliation
remain separate. A failed final state write propagates without claiming recovery
succeeded. Expected revisions, scope and active-owner checks still apply.

Downloads and recovery hold the store's nonblocking per-request OS lock for the
whole command, including metadata, byte publication and receipt writes. This
prevents another cooperating owner/process from treating a live download as
interrupted. Process exit releases the lock; elapsed time does not. The
[storage lock contract](JOB_STORAGE.md#result-transfer-ownership) requires one
private database path and cooperating versions; stop older writers before using
this recovery command. Recovery never resets submitting, uncertain or applying
records. `JobWorkers` and `JobSession` queue it; native saved-job controls and
MCP `recover_local_job` offer it as `recover_download`.

### Worker splat preparation

`prepare_model_import(request_id, expected_revision=..., asset_id=..., options=...,
cancel=...)` decodes one saved SPZ, Gaussian PLY or .splat result into an
immutable `PreparedModelImport` off Blender's main thread. It verifies every
receipt again and binds the decoded bytes to the selected receipt, as described
in [worker-side splat preparation](RESULT_TRANSFERS.md#worker-side-splat-preparation).
The coordinator then registers `prepared.verified` exactly like a `verify_results`
ticket: one claim consumes it and deactivation clears it. Cancellation, a changed
record or a changed file leaves nothing registered. A mesh PLY returns `ply_mesh`
and no snapshot; other PLY layouts that are not splats fail. There is no SDK
request, download, claim or scene mutation. `JobWorkers.prepare_model_import`
uses the single local-operation slot shared with local capture and Film media
inspection, so `cancel_local` and owner retirement stop preparation before the
next receipt is hashed or between decoding chunks; one receipt's hash, up to the
storage policy's byte limit (256 MiB by default), is not interrupted.
`JobSession.prepare_model_import` queues it for reviewed saved splat imports; see
[saved Gaussian splat import](MESH_APPLICATION.md#saved-gaussian-splat-import).


## Saved-result preview commands

`result_preview_targets(request_id, asset_ids=None)` reads the scoped store only.
It accepts a `ready`, `applying`, `apply_failed` or `applied` job and returns one
immutable target per selected asset with a download receipt, keyed by scope,
request, asset, MIME type and receipt. Unknown or malformed asset selections fail
before any work. `review_preview_targets(request_id, asset_ids)` also reads the
store only: it accepts 1 to 50 distinct asset IDs for a `remote` workflow job
with a known remote ID and returns review targets keyed by scope, request,
remote job and asset, without a receipt. The caller, which owns the waiting
step projection, supplies that step's asset IDs.

The [preview lane](#application-owned-workers) runs six commands with the cache
root supplied by the application, and a seventh, `world_sizes`, without it. `prepare_result_previews(work, root=...)`
rechecks that each target still holds the same receipt (a review target, the
same `remote` workflow job), reads verified cache
entries, issues private decode copies for images and audio, and reads server
preview metadata once per batch through the adapter's `assets.get_bulk`, whatever
the batch size. Available stills and clips are downloaded with the configured
result downloader and its storage hosts, passing the task's cancellation event.
A transfer that does not complete is reported `pending` until the work item's
final poll, then `failed`; bytes that fail their receipt or content checks fail
at once. Without online access those renditions report `offline`; no other
request is made. Receipts are checked again before the batch returns, and every
issued copy is removed if the batch fails, is canceled or reaches an inactive
coordinator. `finish_result_preview` validates and caches Blender's decoded
output for an owned request; `discard_result_preview` removes an unused copy.
`maintain_result_previews(root=...)` only sweeps abandoned work directories and
evicts cache entries beyond the budget, without reading a job or the network.
`decode_result_preview(request, root=..., waveform=...)` decodes an owned audio
request's copy in an
[offline Blender process](RESULT_PREVIEWS.md#offline-audio-envelopes), checks
and caches the envelope and always removes the copy; the task's cancellation
event terminates the process.
`prepare_playback(request_id, expected_revision=..., asset_id=..., root=...)`
makes a [private player copy](RESULT_PREVIEWS.md#private-player-copies) of one
downloaded video or audio receipt at the exact saved revision. It first removes
copies the owner listed in `discard` and sweeps stale ones not in `keep`, returns
an unchanged `reuse` copy of the same receipt instead of copying again, and
rechecks the record at receipt level afterwards. A cancellation or inactive
coordinator after copying removes the new copy.
`world_sizes(targets)` takes at most one preview batch, the scheduler's
`BATCH_LIMIT` of 32, of (request ID, asset ID, receipt SHA-256) triples naming
downloaded World candidates, so it holds the lane no longer than a preview
batch, and reads at most the first 2 MiB of each file. It returns each triple
with the declared width and height, or none when the record no longer holds
that receipt as a candidate, the file cannot be read or those bytes declare no
size. It neither hashes nor copies; the
[World offer](WORLD_APPLICATION.md#saved-result-ui-and-mcp-approval) uses it only
as a display filter. Only `prepare_result_previews` reads the network. None of these commands transitions a job, changes its
revision, claims application or submits work. See
[result previews](RESULT_PREVIEWS.md) for renditions, limits and the polling
window.

## Durable application claims

The coordinator registers each successful `verify_results` response as an
ephemeral verification ticket. `claim_application(verified)` accepts only that
exact object from this active owner, an unchanged saved `ready` or `apply_failed`
record, and a configured thread-safe `origin_guard` that still recognizes the
original origin. Copying public fields, selecting a new scene or reopening the
store cannot recreate this authority. `applied` results can still be verified
for inspection but cannot be claimed again.

`claim_recovered_application(verified, destination)` is a separate explicit
command. The caller must capture and obtain approval for a new destination,
then resolve it before mutation. The same owner, ticket, state and stored-revision
checks apply, with the origin guard checking the approved destination. The
atomic claim stores it as `application_origin` while preserving `intent.origin`.
Downloading or inspecting a job does not authorize this command.

The origin guard and coordinator lock cover the atomic `applying` write. The
verification ticket is consumed before that write is attempted, because a write
failure can be uncertain. A successful claim returns an immutable
`ApplicationClaim(record, paths)` belonging to that owner. The stored revision
rejects another owner racing for the same result. The claim reads no result bytes
and calls neither Blender nor the network. These synchronous methods are
not worker commands; the Blender integration must claim immediately before its
main-thread mutation, after resolving the captured target.

Verified paths do not freeze file bytes. The application must check the exact
snapshot it decodes against the saved download receipt and must preserve private
storage. A claim alone does not validate image/mesh semantics, apply anything to
Blender, or authorize a different asset/target.

After confirmed success, `complete_application(claim)` saves `applied` once.
`fail_application(claim)` saves `apply_failed` only when the caller has confirmed
that no mutation occurred or every change was rolled back. Unexpected exceptions,
interruption or uncertain rollback must leave `applying` unchanged. Neither
method catches a Blender callback or interprets an exception as successful rollback.
An explicit retry after confirmed failure requires a new verification ticket.
The ordinary claim still requires the original current origin; the recovered
claim requires a freshly approved current destination.

Both receipt methods require the exact unfinished claim and its unchanged saved
revision. They remain available after origin invalidation or owner deactivation,
because application itself can change the scene revision; the receipt still goes
only to the original scope. Persistence failure propagates without claiming an
unsaved outcome. The caller may retry an uncommitted local receipt write using
the same claim, but must never repeat its Blender mutation as part of that retry.
A write that committed before losing acknowledgement causes a subsequent revision
conflict through these ordinary receipt methods.

`retry_application_receipt(claim)` explicitly retries the outcome already reported
by that exact claim to its original owner. The owner remembers the first attempted
outcome before reading/writing storage; a failed receipt cannot switch from
successful application to rollback or vice versa. If storage still matches the
claimed record, retry writes that same outcome. If it matches the complete expected
successor (including revision, intent and results), retry acknowledges the saved
commit without another write. Any other state fails closed. Repeated acknowledgement
is read-only. The command performs no Blender, file-verification or network work
and remains available after origin invalidation or owner deactivation.

Receipt evidence uses weak references to caller-held claims. Copies, foreign
owners, claims with no attempted receipt and claims reconstructed after restart
are rejected. Dropping a claim loses this local retry authority; it does not change
the saved job. A retry records the previously confirmed outcome, not the current
state of a scene after later edits or explicit restoration.

`recovery_plan()` continues to inspect without writes. An abandoned or restarted
`applying` record remains `REVIEW_APPLICATION`: no expiry, reset, automatic
completion or retry is provided. The SQLite record is durable; verification
tickets and application claims are owner-local and are never reconstructed from
names, paths or stored metadata. `JobSession` uses these claims for automatic
Image delivery and every explicitly approved saved-result application.


## Reference upload commands

Optional upload storage, source staging and signed PUT configuration attach to
this same coordinator and worker queue. Their scope must match the job store.
See [upload commands](SDK_UPLOADS.md#shared-worker-commands) for preparation,
initialization, one-part transfer, finalization, explicit refresh and restart.
They do not submit generation, create another client/pool, or apply references
to Blender. Signed part destinations from the create response stay in the upload
owner's memory and are forgotten on `deactivate()`; a new coordinator cannot
resume a transfer and offers `restart_upload` instead (see the
[restart limit](SDK_UPLOADS.md#part-plan-source-and-restart-limit)).
`cancel_prepared_upload` synchronously cancels unclaimed local upload intent,
including queued initialization, under the original scope and expected revision.
It does not require a current scene origin, access sources or abort remote work.

`inspect_upload(request_id)` and `upload_recovery_plan()` provide read-only,
scope-bound persisted state without exposing the private store. Their immutable
snapshots preserve original scope/origin and any uncertain claims; conservative
suggestions do not grant permission to dispatch or retry. See
[upload inspection](SDK_UPLOADS.md#scoped-inspection-and-recovery-visibility) for
the state mapping and lifecycle boundaries.

`discard_upload_source(request_id, expected_revision=...)` queues explicit local
cleanup for a saved CANCELED, FAILED, IMPORTED or ABANDONED upload. It verifies the staged
copy and rechecks the active scope/record before removal, preserving the original
file and durable history. Source hashing runs outside the coordinator lock;
guarded deletion does not require a current scene origin. See
[finished-upload cleanup](SDK_UPLOADS.md#explicit-finished-upload-source-cleanup)
for filesystem ownership, interruption and orphan limits.

The optional thread-safe `origin_guard` also protects upload preparation and each
durable initialization/part/finalization claim. Source reads and network calls
remain outside the revision lock; inspection and explicit refresh preserve access
to old-origin records. A chosen file's upload carries a
[destination-only origin](BLENDER_JOB_CONTEXT.md#origin-and-quote-lifetime), which
scene edits keep; preparing a paid request with such an origin is refused, so it
can never stand in for inputs captured from the scene. The [upload guide](SDK_UPLOADS.md#shared-worker-commands)
describes rejected staging and already-claimed response behavior.


## Asset organization commands

`organization_snapshot`, `organize` and `collection_page` add unpaid collection
and tag changes to the same coordinator and worker queue through the adapter's
[organization methods](SDK_ADOPTION.md#asset-organization-writes). The pure
contract is [organization.py](../scenario/core/jobs/organization.py).
`build_request` binds the selected `JobScope` and validates the operation (add to
or remove from a collection, change tags, or create a collection), 1 to 49
unique asset IDs (optional for a create), the collection ID or name, and tags.
Tags and names are stripped and must pass the adapter's label rules. A tag list
takes at most 200 entries, counting duplicates, and at most 30 unique tags after
deduplication, without commas; each refusal names the limit it enforces, and a
tag cannot be added and removed together. The coordinator refuses a request for another scope before
any request is made.

A snapshot reads the assets' tags and memberships with `get_bulk`, the target
collection, or for a create an exact-name lookup of at most 20 pages of 100.
Missing assets, an existing name (returned with its IDs) or a name whose create
outcome is unknown in this session make it unappliable; a request with nothing
to change sends nothing. Each issued snapshot is held weakly, and `organize`
accepts only an unused snapshot of its own scope, once.

`OrganizationCommands.execute` calls the active-context guard immediately before
every write and never after the last one, so deactivation stops later writes
and still reports those already sent. Membership changes send one request for
the assets that need it. As the [adapter guide](SDK_ADOPTION.md#asset-organization-writes)
records, the service was observed (not yet checked live here) to refuse a whole
add, writing nothing, when any asset is already a member, which another client
can cause after the snapshot; the adapter raises `AlreadyMembers` for that
refusal only.
One `get_bulk` read then drops the assets now in the collection and, after the
guard, sends the others again, within `ADD_ATTEMPTS` (three) add requests per
review. A failed read, a refusal the read cannot explain, any other outcome or
the bound stops it, and the result message counts the assets that were already
members. Tag changes send one non-strict request per asset in
order: a definite rejection continues with the next independent asset, while
the first uncertain outcome, disabled online access or a failed guard stops
further writes and leaves the rest NOT_SENT. A create repeats the exact-name
lookup and posts once. After an uncertain create, one read reconciles it, by the
acknowledged ID when the service renamed it, otherwise by exact name; only a
confirmed collection with the requested name continues to the add step. The
name otherwise stays in this session's uncertain set and is never created again
here.

When anything was sent, one `get_bulk` read classifies every asset: VERIFIED when
it shows the requested state, REJECTED with the status for a refusal the read
confirms, UNCONFIRMED for an uncertain write or an acknowledgement the read does
not show (for example a dropped DELETE body), NOT_SENT for writes never sent and
UNVERIFIED when the read failed or omitted the asset. A created collection is
read back by ID. The result is VERIFIED, PARTIAL, UNCONFIRMED or REJECTED.
`execute` raises `OrganizationNotSent` only when nothing was sent, and nothing
is replayed after an uncertain outcome: the user prepares a new review after
inspecting.

`deactivate()` clears issued snapshots and the uncertain create names; queued
organization commands are cancelled unsent with other queued work. Nothing is
persisted and the job store schema is unchanged. The exact-name dedup, no-replay
and read-back rules and their tests adapt Studio's `organization.py` at the
[recorded source revision](STUDIO_ADOPTION.md#source-intake-and-provenance).
[JobSession reviews](BLENDER_JOB_CONTEXT.md#asset-organization-reviews) own
review state and delivery. The [local MCP tools](MCP.md#historical-upstream-reference) and
the native [Library organization](ui/studio-and-library.md#library-organization) controls use
them; desktop and live acceptance remain.


## Shared catalog and origin-bound quotes

The coordinator and its existing worker queue expose `models`, `model`,
`workflows` and `workflow` through the same selected SDK adapter. Late metadata
is rejected after context deactivation. Detail retrieval must return the exact
requested identity. These commands do not create another catalog client/cache,
start another pool, or quote a trained model as the generation target.

`quote_model` and `quote_workflow` take the origin captured with input values
before estimation. Workers copy finite JSON parameters at admission. Each quote
retrieves the current detailed SDK schema, applies the shared strict validation
and defaults, then preserves the exact server estimate. Active scope and origin
are checked before work, after metadata retrieval and after estimation. A scene
change during those calls cannot turn a late estimate into a current quote.
The returned `OriginQuote` holds the original scope/origin and immutable SDK
estimate; it is ephemeral and does not yet create a durable job intent.

Between metadata retrieval and estimation, the quote prepares the exact payload
without a request. It then reads every model the caller chose in a `model` or
`model_array` input (a value other than empty or the schema default) again
through the same adapter, including the LoRAs of a chosen composition. The
reads are bounded at 16 per quote and recheck the active context and origin
before each request. A deleted or inaccessible (HTTP 403 or 404), untrained or
incompatible model, or a LoRA selection without a strength for each LoRA,
raises `RouteQuoteError` before any dry run: nothing is quoted, dispatched or
persisted. Its text names input labels and positions only. The estimate must
then carry the checked target and payload. Routes and their policy are
described in [trained-model routes](SDK_ADOPTION.md#trained-model-routes).

After the user chooses it, `prepare_quote` accepts only that context's unchanged,
unconsumed quote, checks its origin again and persists one PREPARED intent.
Copies, changed origins/scopes, expired quotes and previously prepared quotes
are rejected. The older direct-SDK `prepare` method cannot accept an estimate
issued through the bound-quote path, even after its wrapper is discarded.
Submission still requires the current exact payload and the original origin,
and the existing durable claim before paid SDK dispatch. These methods do not
supply spending approval or automatically submit anything.

`JobSession.quote_model` / `quote_workflow` admit this work on Blender's main
thread, and its normal bounded completion queue delivers the result with its
captured origin. `prepare_quote` resolves that same scene/target before persisting.
Frame/file/dependency invalidation, view-independent lifetime and shutdown retain
the existing session rules. UI/MCP model forms use these commands through
ModelJobs.

Estimate ownership is checked before acquiring the coordinator/origin locks.
Preparation keeps quote selection and persistence under the coordinator lock,
but never acquires the adapter estimate lock there. Submission rechecks and
consumes the estimate under the adapter lock while committing its durable claim;
preparation does not reserve spending authorization. This ordering lets a new
preparation overlap an existing submission without deadlocking either thread.


## Prompt Spark commands

`quote_prompt(parameters, origin=...)` skips model/workflow discovery and binds
the SDK's exact quote to the captured origin before and after network work.
`prepare_quote` and `submit` use operation and target `prompt`, sharing the
existing immutable payload/scope checks, single-use issuance and durable claim.
A missing or lost receipt leaves uncertainty, never permission to retry.
`JobWorkers.quote_prompt` snapshots parameters before queue admission and uses
the existing bounded pool. It creates no independent thread or job owner.

Known prompt job IDs can be read after restart through `refresh_remote` without
resubmission. Native and MCP prompt controls use the same explicit approval
facade, including render look preparation from uploaded images. Preparing a look
never submits the render itself.


### Prompt result recovery

`read_prompt_results(request_id, expected_revision=...)` is a read-only command
on the existing coordinator, worker pool and JobSession. It requires scoped
`prompt` or `translate` intent in `succeeded`, retrieves that exact successful job
through `SDKAdapter.job`, and checks the matching job type. It reads
`metadata.output.prompts` for Spark or `metadata.output.translation` for translation. The result holds the
original stored record and one to five verbatim, nonempty strings, each limited
to 64 KiB of UTF-8. It is not an application claim or new spending approval.

Asset references are resolved through `SDKAdapter.asset`. Only successful
assets with top-level `kind="text"` and `mimeType="text/plain"` qualify.
`properties.preview` is accepted only when
`hasFullPreview` is exactly true. Otherwise the full asset is downloaded with
a 64 KiB cap through the configured credential-free storage policy, verified,
decoded as UTF-8 and removed from private temporary staging. Invalid/missing
output fails explicitly; no truncated preview, LLM fallback or generation retry
is substituted. URLs and text are not added to the job database or result repr.

Reads recheck owner activity and the stored revision before returning. Restart
can repeat retrieval using the saved remote identity. Main-thread JobSession
delivery still rejects a retired account or changed/missing origin; it never
writes a prompt on its own. The native prompt facade explicitly delivers to the
unchanged originating field. MCP `read_prompt_result` can return scoped saved text
after a scene change, without applying it. Redirecting a recovered result to a new
native destination remains separate integration.

### Model text recovery

`read_model_text(request_id, expected_revision=..., asset_id=...)` reads one
explicitly selected text output through the coordinator and its existing worker
pool. It requires a saved `model` intent with a successful remote outcome: local
`succeeded`, `download_failed`, `ready`, `apply_failed` or `applied`. Pending,
uncertain and actively changing download/application states are rejected.

The command retrieves the saved remote job through `SDKAdapter.job`, checks its
identity and successful status, then validates a bounded, unique
`metadata.assetIds` list containing the selected asset. A stored result manifest,
when present, must also contain that asset. `SDKAdapter.asset` retrieves it using
the same selected credentials and optional project scope. The full-text reader
above enforces asset identity, successful status, top-level text kind, plain-text
MIME, explicit preview completeness and the 64 KiB limit. An integral JSON number
is accepted as the full-body byte count; booleans and fractional sizes are not.

The result contains the unchanged stored record, selected asset ID and text;
text is excluded from its repr. Retrieval rechecks scope activity and the stored
revision before returning. It never writes the text or URLs to the job database,
changes generation/application state, parses a scene plan or submits generation.
Restart and transfer failure can repeat this read without another paid request.
This is fresh remote retrieval, not certification of a previously stored download
receipt. Native Blockout recovery and MCP `read_model_text` reach it through
`JobSession`.

`quote_translate` uses the fixed `translate` operation and target, bypassing
model discovery. Its worker snapshots the prompt and preserves the same bounded
queue, exact quote identity, origin checks and durable single-use submission.


## Explicit local reuse claims

`claim_local_application(verified, destination, purpose=..., asset_ids=...)` is a
separate local command for an already-applied generation. It requires verified
results issued by this active owner, an unchanged scoped record and a current
explicit destination under the existing origin guard. The caller must bind the
purpose and exact selected IDs to its user review, apply only those assets, and
recheck actual bytes before decoding. The command consumes verification before
attempting its durable write; a failed or lost response never returns authority
to repeat scene work. Ordinary `claim_application` still rejects applied jobs.

The returned owner-local `ApplicationClaim` identifies its separate durable
[local application record](JOB_STORAGE.md#durable-local-result-reuse).
`complete_application` and `fail_application` preserve the generation's completed
state and record only the local outcome. `retry_application_receipt` freezes the
first reported outcome, recognizes an exact committed successor after a lost
response, and retries persistence only. Retirement may prevent new application
while an already-known receipt still finishes in its original scope. Copied
claims, other owners and a restarted process cannot invent receipt evidence.

`recovery_plan` reports `review_application` for unfinished local reuse even
though the original generation stays applied. Restart never authorizes replay or
assumes the scene was unchanged. Native UI/MCP supply fresh destination and
selection approval through the [saved-result boundary](BLENDER_JOB_CONTEXT.md#explicit-local-result-reuse).
These coordinator commands perform no Scenario service operation, download, paid
submission or Blender mutation; the main-thread caller owns application.

## Unpaid Film composition inspection

[`film_finishing.py`](../scenario/core/jobs/film_finishing.py) reads matching
completed model outputs and imported upload associations from the selected
stores. It produces a [composition draft](FILM_PLAN.md#unpaid-composition-drafts)
without reserving a master or sending an estimate. Revalidation compares each
request/revision/asset/kind/task digest and rejects an already reserved master.
A draft is immutable review data, not a quote or spend authorization; its helper
performs no atomic dispatch claim. The source guards for composition quote
delivery and dispatch are described below. The existing Film model-task command
resolves the draft's references before the ordinary fresh schema/estimate and
single-dispatch path. No new worker pool or SDK endpoint is introduced.

`prepare_film_composition` wraps that reader with receipt-bound saved-media
measurement through the existing result/upload owners. It snapshots recipe JSON,
checks the captured origin before and after each probe, and compares all source
observations again before returning `VerifiedComposition`. Process/file work runs
outside the coordinator and origin locks, so scene invalidation and cancellation
can proceed. The existing worker pool admits one local capture or inspection at
a time and frees this slot before publishing completion. Its cancellation signals
also stop the probe on retirement. This is an unpaid read; no SDK endpoint or
new store is introduced. See [the measurement limits](FILM_PLAN.md#verified-media-preparation).

## Verified composition quote ownership

`prepare_film_composition` registers its immutable result as an owner-issued,
weakly held inspection ticket. `quote_film_composition` rejects fabricated/copy
objects, foreign scopes and changed origins before service reads. It resolves
only the draft's declared master, then uses the ordinary model schema/estimate
path. `OriginQuote.composition` and `PreparedJob.composition` carry its immutable
source observations. Checks run after schema retrieval and estimation, before
saving preparation and under the existing origin/submission claim guard.

Before preparation, source validation also requires an unused master. At dispatch,
`validate_composition_sources` checks the same source records while the existing
prepared intent check authorizes this already reserved master. No second store,
worker pool, submission command or SDK fallback is added. Guard data is in memory;
only the existing exact payload/quote digests and Film task intent are persisted.
Restart cannot resurrect a prior owner's quote. See [the composition boundary](FILM_PLAN.md#quoted-composition-generation).

## Native Film review media ownership

`prepare_film_review` uses the same bounded local worker queue to copy and measure
saved scoped media. It returns an owner-issued `PreparedFilmReview` with immutable
source observations, measured copies and verified generated-job results. The
optional saved master must match the compiled current recipe. No SDK operation,
new store or spending is involved. See [the native review contract](FILM_PLAN.md#shared-native-review-preparation-and-application).

The coordinator retains at most 16 preparations for cleanup. `take_film_review`
checks identity, scope, origin, current sources and file stamps before consuming
one; this transfers copy ownership to the main-thread caller, not spending or
permission to skip its native destination checks. `discard_film_review` deletes
only an unused issued preparation. Shutdown joins workers before cleanup; failed
preparation removes its own files. Consumed files remain the native application's
responsibility. A crash does not reconstruct tickets or authorize replay.
