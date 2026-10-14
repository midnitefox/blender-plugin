# Scoped upload metadata commands

The shared [SDK adapter](../scenario/core/api/sdk_adapter.py) exposes the
multipart upload metadata lifecycle through the packaged **scenario-sdk 2.2.0**.
These commands do not read files, transfer bytes, persist signed URLs or start a
background worker by themselves. Native forms and local MCP use them through the
shared workers for [reference uploads](#active-reference-uploads).

| Command | Public SDK method | Behavior |
| --- | --- | --- |
| `create_upload(...)` | `uploads.with_raw_response.create` | POST multipart metadata once; returns the unwrapped `upload` record |
| `upload(identifier)` | `uploads.with_raw_response.retrieve` | GET a known upload in the selected project |
| `complete_upload(identifier)` | `uploads.with_raw_response.trigger_action(action="complete")` | Explicitly request completion once; return the actual reported processing state |

The adapter's selected credentials, HTTPS API endpoint and immutable project
apply to every command. Callers cannot inject a different project or arbitrary
SDK extension parameters through upload options. Online permission is checked
before each request; closed clients reject commands. The SDK has retries disabled
and redirects disabled, including for reads and completion acknowledgements.
A selected local account/team identity is not itself proof of server permissions.

## Inputs and responses

Initialization accepts the SDK's multipart kinds (`3d`, `asset`, `audio`,
`avatar`, `image`, `model`, `text`, `video`), a file basename, a bare MIME type,
an integer byte count and a positive integer part count. Zero bytes are allowed
as metadata; this does not claim the service accepts empty media. These local
checks do not invent service size/part limits or a chunking policy. The caller
must bind this metadata to its actual file before future byte transfer.

Optional asset settings use the SDK's `collection_ids`, `parent_id` and `hide`
names; the SDK serializes their documented camel-case wire aliases. They are
copied and validated before dispatch. Model uploads reject asset options,
matching the selected SDK's contract. URL ingestion and model-provider import
shortcuts are outside this multipart-only interface.

Upload and asset-option identifiers follow a local maximum of 256 characters
and reject path separators, URL metacharacters, whitespace and control characters.
Every response must contain an `upload` object with a valid ID and a nonempty
string status. Retrieval/completion require the returned ID to match the requested
one. The adapter retains unknown fields and statuses instead of manufacturing a
terminal result. Transfer instructions are preserved as metadata; they are **not
a validated or authorized storage transfer plan**. Do not log raw records: parts
may contain signed URLs and other sensitive response details.

Only the create response carries the multipart plan. Live service responses
(SDK 2.2.0) return `parts` (number, signed `url`, `expires`), `partsCount`,
`fileSize`, `contentType` and `originalFileName` from `create`. Retrieval of the
same pending upload, before or after its part PUTs, and the completion action
omit all five fields; the SDK's shared `Upload` model types them as optional.
No operation reissues part URLs or aborts a pending upload. The response
`fileName` is a server storage key, not the chosen name.

## Completion and uncertain responses

The SDK 2.2.0 generated action parameter is `Literal["complete"]`; its docstring
says `"upload-complete"`. The adapter uses the generated literal already covered
by the dependency contracts. The live service accepted `"complete"` and
acknowledged it as `validating`; retrieval then reported `validated` and
`imported` with an `entityId` within seconds. Do not silently substitute another
action or invent an abort endpoint. The action takes no part list or ETags.

Only an explicit caller action can initialize or finalize an upload. The caller
must first establish that all its parts transferred successfully before calling
`complete_upload`. This low-level adapter cannot establish that from metadata.
A `validating` acknowledgement stays `validating`; even `complete` must not be
silently interpreted as an imported asset. Preserve the returned `entityId`,
`jobId`, actual status and future fields for the higher-level lifecycle.

A timeout, HTTP failure or invalid receipt after a mutation may leave an upload
created or completion accepted remotely. The adapter raises a redacted error and
makes no second request. It does not automatically create another upload, repeat
completion or poll. A higher-level owner must persist request/file/scope identity,
retain known upload IDs, distinguish uncertain mutation from transfer failure,
and reconcile through explicit reads. An unknown initialization ID remains
uncertain; do not attach a guessed upload or automatically recreate it.

## Remaining integration and verification

Signed PUT transport, private source staging, durable claims, shared worker
commands, a host/size policy, saved-upload recovery and verified source cleanup
are described below; automatic orphan retention is not implemented. Storage
requests check destination/online policy and never forward Scenario
Authorization. No upload-abort method was established in this SDK.

Offline contracts exercise the actual SDK through MockTransport, including
scoping, field aliases, one-attempt failures, response identity, future processing
states, invalid inputs and ambient credential isolation. Installed-ZIP tests use
the bundled SDK with synthetic responses shaped like the live contract above:
only creation returns the part plan. These checks do not establish live upload
acceptance, OAuth transport or successful file import.


## Signed part byte transfer

[`PartUploader`](../scenario/core/jobs/upload_transfers.py) performs one storage
PUT attempt for a supplied immutable `bytes` snapshot. SDK upload initialization,
retrieval and completion remain in the shared adapter. The pinned SDK exposes
numbered part URLs and expiration metadata; it does not transfer these bytes.
The primitive introduces no raw Scenario API endpoint or SDK fallback.

The caller must persist the upload identity, original scope, source/part digest
and transfer claim before calling it. It must bind the chosen URL and part number
to that SDK upload plan, check expiration, and choose an explicit part-size policy.
There is no implicit trusted-host list: the caller supplies an HTTPS storage
policy. `StoragePolicy` uses exact configured hosts; active reference uploads use
the separate S3 REST policy described below. Policies must come from reviewed
configuration, not by copying hosts from incoming URLs. No method initializes or
finalizes an upload here.

Inputs require a positive part number, a nonempty immutable byte snapshot within
the policy's byte limit, a bare MIME type, and a SHA256 matching the saved part
identity. Invalid or changed bytes fail before connecting. Large files must be
staged and split by the application; this primitive never reads the user's source
path or assembles a whole file in memory. Empty media needs a separately verified
service contract rather than an invented zero-part upload.

The transport uses verified TLS with bundled certificate authorities and sends
only PUT, Content-Type, Content-Length and Connection: close (plus HTTP's Host).
It has no Scenario credentials, cookies, ambient proxy/certificate settings,
redirect handling, URL logging or automatic retry. Body writes are at most 64 KiB;
permission and elapsed-time budget are checked between them and before reading
the response. Blocking DNS/TLS/socket calls retain the existing transfer timeout
limitations. Revocation cannot retract bytes already sent.

HTTP 200, 201 or 204 yields a URL-free `UploadedPart(number, size, sha256)` receipt.
This acknowledges this PUT only; it does not establish remote digest validation,
all-parts completion or successful asset import. Response bodies and ETags are
neither read nor persisted. The selected SDK completion method does not take ETags.

Failures before the first possible HTTP write raise sanitized `TransferError`.
After that boundary, transport errors and non-success HTTP statuses raise
`UploadUncertain`: storage may have accepted bytes. There is no retry or automatic
completion. The caller must preserve the durable claim and reconcile using the
known upload identity. Control exceptions propagate after cleanup and likewise
must leave the caller's persisted claim available for recovery. Ordinary cleanup
errors do not replace an acknowledged receipt with a failure that could invite
replay. These are offline transport contracts, not live upload acceptance.


## Durable upload claims

[`UploadStore`](../scenario/core/jobs/upload_store.py) records immutable source
metadata, whole-file and per-part SHA256 identities, original account/project
scope and Blender origin in a separate versioned SQLite database. It does not
read source files, run workers or call the network. The application must stage
and hash its actual source before creating the intent, then verify each part
against that identity before sending it. Signed URLs and credentials have no
fields in this record. Local chunking limits are not service acceptance claims.

Initialization, each part, and finalization require a committed claim before the
caller performs the corresponding mutation. Revisions and immediate SQLite
transactions reject stale or competing claims across connections. A part receipt
must match the claimed number, exact size and saved digest; receipts form an
ordered prefix. Finalization requires every receipt. Only an authoritative
imported observation can attach an asset ID.

Reopening preserves in-flight states verbatim. An interrupted initialization
without a known remote ID remains uncertain; it cannot be reset or recreated.
A part claimed without a receipt cannot be claimed again, including after a
restart. Uncertain finalization cannot be retried. Explicit SDK reads of a known
upload may reconcile processing, imported or failed observations without sending
bytes again. `abandon` is the only other exit from UPLOADING or PART_UNCERTAIN:
one transaction retires the record as terminal `abandoned` and inserts a PREPARED
replacement with the same name, kind, MIME, size and digest. The selected SDK has
no per-part receipt or abort API; pending remote status alone cannot prove an
interrupted part was rejected. No automatic retry or cleanup endpoint is invented
here.

The store validates source identity, state invariants, scope and revision when
reading. Missing/corrupt records, foreign databases, future versions and failed
writes raise errors and preserve evidence rather than resetting storage. Failed
receipt persistence leaves the earlier claim in place. New database files and
directories use private permissions where supported. The application owns the
storage directory under Blender's extension user path and must prevent its
replacement while in use. Each SQLite connection rechecks the database with
`lstat`, including after a competing creation. This rejects a symlink observed
at that boundary; it is not an atomic defense against an attacker who controls
the directory and can swap the path after the check. This database is separate
from the job database.


## Shared worker commands

The coordinator optionally owns one `UploadStore`, `UploadSources` and
`PartUploader` with the same scope as its job store. Its existing `JobWorkers`
queue exposes fixed commands; there is no second SDK client or thread pool.
It also exposes synchronous local cancellation, which does not wait behind queued
initialization or consume another queue slot.

| Command | Behavior |
| --- | --- |
| `prepare_upload` | Copy the chosen source into private storage and persist its immutable identity; no network |
| `initialize_upload` | Verify the staged whole file, commit initialization intent, call SDK create once and preserve the returned ID |
| `transfer_upload_part` | Retrieve the known upload to confirm it is still pending, take the next destination from this owner's in-memory create plan, verify immutable bytes, claim and send exactly one part |
| `finalize_upload` | Require all saved receipts, claim completion, then call the SDK completion action once |
| `restart_upload` | Abandon an uploading or part-uncertain upload that cannot continue and stage its verified saved copy as a new PREPARED request; no service request |
| `refresh_upload` | Retrieve a known upload and commit recognized processing/imported/failed observations without replay |
| `cancel_prepared_upload` | Immediately cancel only PREPARED local intent at its expected revision; no source access, deletion or remote request |
| `discard_upload_source` | Explicitly remove a verified staged copy for a CANCELED, FAILED, IMPORTED or ABANDONED upload; retain its durable record and the user's original file |

Local cancellation requires the active selected scope but does not require the
old Blender origin or source file to remain available. It works offline and after
reopening the scoped store. The same SQLite revision check arbitrates cancellation
against initialization, including independent owners. When cancellation wins, a
queued or preflighting initializer cannot commit its claim and never dispatches.
When initialization wins, cancellation refuses; INITIALIZING, uncertain, remote
and terminal states cannot be reset or reported as remotely aborted. The selected
SDK still provides no verified upload-abort operation.

The canceled record retains source identity and original scope/origin. Both the
user's file and staged snapshot remain untouched; explicit source cleanup uses
the separate command below. Repeated cancellation and stale revisions require
reloading saved state.
A storage error may occur before or after commit: inspect the record rather than
assuming cancellation succeeded, resetting it or starting another upload. This
immediate command returns its immutable saved record directly, not a queued task.

Staging defaults to a local 256 MiB file limit and 8 MiB parts, configurable by
the application. Files are copied with bounded reads into a new private directory;
symlinks, nonregular sources, changed size/timestamps and incomplete copies fail.
File and directory flushes precede intent persistence (directory fsync on POSIX).
The original path is not stored. Subsequent edits to the original file do not
alter the snapshot. Each part read rechecks size and its saved digest; initialization
also verifies the full digest. On Windows, comparing a path with an open file uses
creation time where available because those APIs can assign different meanings
to `ctime`. Checks before and after reading the same open file still compare its
`ctime`, along with identity, size and modification time, to reject changes.
The application must retain ownership of the
staging directory and ancestors. When deactivation, an origin change or failed
intent persistence stops preparation after staging, preparation removes its new
private copy unless a saved record references it or the store cannot be read.
A copy kept that way, or one left by a process crash before persistence, remains
a private orphan for explicit retention/cleanup policy. No user source is deleted.
These are local resource limits, not service guarantees.

### Part plan source and restart limit

Initialization validates the create response's pending multipart plan: kind,
chosen filename (`originalFileName`), MIME, size, part count, ordered part numbers
1 to N, every URL against the storage host policy and every timezone-aware expiry
more than 30 seconds away. The owner that created the upload keeps that plan in
memory only, bound to the request, remote upload ID and source digests. Its object
representation omits the URLs and it refuses serialization. Signed URLs never
reach the upload record, SQLite, smoke manifests, logs, task results, MCP output
or exception text. An unusable plan caches nothing: initialization reports
`UploadPlanUnavailable` after saving the known upload ID, and no part is claimed.

Each part command still retrieves the known upload first. That liveness check
requires the same ID, kind, `multipart` source and `pending` status. Optional
metadata must match only when present, because live retrievals omit it. If a
future retrieval carries a full `parts` list, it must pass every create-plan check
and is used once without being saved. The chosen destination is rechecked for
host policy and the 30-second freshness margin before the claim. This local margin
is not a transfer-duration guarantee; observed part URLs expired after about 48
hours. Model import is rejected because its entity is not an asset reference.

A plan serves one uninterrupted transfer in its owner. The final receipt,
completion, an observed remote state, deactivation or context switch, an expired
destination and any failed part attempt forget it. Losing a claim race to another
command does not. Without a plan, `transfer_upload_part` raises
`UploadPlanUnavailable` before any claim or PUT. A restarted process, a new session
or a remaining part after an earlier failure therefore cannot resume the transfer.
Scenario's upload guide says to re-initialize when URLs are needed again.

`restart_upload(request_id, expected_revision=..., origin=...)` is that explicit
re-initialization. It accepts exactly the records whose suggested action is
`RESTART_UPLOAD` (see the recovery table below): part-uncertain uploads, and
uploading ones this owner cannot continue. It refuses a part being sent and an
upload that can still continue or complete. It verifies and copies the saved
private snapshot into a new request with the same name, kind, MIME and whole-file
digest. One SQLite transaction then moves the old record to terminal ABANDONED,
keeping its remote ID and receipts, and saves the replacement as PREPARED.
Completion was never requested for the abandoned upload, so it cannot become an
asset; this client never transfers to, completes or polls it again. The remote
pending upload stays unaborted, because no abort operation exists. A missing or
changed snapshot abandons nothing. A restart that fails after copying, for example
because a concurrent command changed the record's revision or the origin changed,
abandons nothing and, like a failed preparation, removes its new copy unless a
saved record references it or the store cannot be read. The replacement keeps
captured-mesh provenance only when it reuses the original, still current origin;
otherwise it is an ordinary upload of the same verified bytes. Only an explicit
user or caller action restarts; nothing retries automatically. An uncertain part
is never sent to the same upload again. Older readers reject the new `abandoned`
state; do not downgrade a store that contains one.

Deactivation before a mutation claim prevents dispatch. Once claimed, responses
can persist only to the old scope; a later command is rejected by that inactive
owner. Ordinary errors after a claim conservatively become uncertainty, including
local permission revocation or definitive rejections; the commands do not infer
that retry is safe. Failed persistence and thread-control exceptions leave the
in-flight claim intact. A known upload's `complete`, `validating` or `validated`
status means processing, not imported. Only `imported` with a valid `entityId`
binds an asset; pending status does not release uncertain claims.

Preparation requires a captured `JobOrigin` before reading the source. When the
coordinator has an `origin_guard`, preparation checks that revision before staging
and again while persisting the intent. Initialization and part transfer check it
before preflight; initialization, each part and finalization hold the same pure
revision guard through their durable mutation claim. Invalidation cannot slip
between that check and the committed claim. The guard must be thread-safe and
must not access `bpy`; the shared `OriginRevisions` registry supplies that boundary.
File staging/verification and HTTP remain outside the guard. An unrelated scene's
revision does not invalidate the captured origin.

If the origin changes during staging, no upload intent is saved or dispatched.
Preparation removes the completed private snapshot, as it does after failed
persistence or deactivation, unless a saved record references it or the store
cannot be read. The finished-upload cleanup command requires a saved terminal
record; it cannot remove unrecorded orphans or arbitrary staging directories.
A request already durably claimed may finish and save its receipt under the
original scope/origin. Inspection and explicit remote refresh intentionally do
not require a current origin, so a missing scene or a new file session does not
erase recovery evidence. They never rebind it, release a claim or repeat a
mutation. Callers without an `origin_guard` remain responsible for establishing
their own current-origin policy.

Offline tests exercise the actual SDK with synthetic HTTP responses and mocked
storage connections, including the installed extension and shared worker queue.
No live source is uploaded by these tests. Authoritative account discovery
remains with [#67](https://github.com/scenario-labs/blender-plugin/issues/67),
and the open upload checks are in
[#440](https://github.com/scenario-labs/blender-plugin/issues/440).

## Explicit finished-upload source cleanup

`discard_upload_source(request_id, expected_revision=...)` runs through the existing
worker queue and accepts only CANCELED, FAILED, IMPORTED or ABANDONED records in
the active selected scope. An abandoned upload's replacement has its own copy. It needs no current Blender origin or online access. PREPARED,
in-flight and uncertain uploads retain their sources. Cleanup never cancels an
upload, calls a service, deletes a remote asset or changes its saved history.
It returns the same immutable record, including all source hashes and receipts.

The command derives one private staging directory from the saved scope and request
identity. It accepts only `source.bin` there, verifies its saved whole-file and
part hashes within the configured byte limits, then rechecks file/directory
identity and contents immediately before removal. Symlinks, hard-linked or
nonregular files, changed bytes/metadata and unfamiliar files are preserved with
an error. It never recursively deletes a directory or reads the user's original
path. The original source is not stored and remains untouched.

Hash verification happens outside the coordinator lock. The final removal guard
rechecks active ownership and the exact saved record, so credential retirement
during verification prevents deletion. File removal and empty-directory removal
occur under that guard. As with staging and downloads, the application must own
the private directory and its ancestors: path checks do not establish atomic
protection against an attacker replacing files between filesystem calls.

An absent snapshot or an empty known staging directory is safe to clean again.
If interruption or a filesystem error occurs after unlinking the file, the
remaining empty directory can be removed by a later explicit retry, including
after restart. The durable terminal record stays unchanged. Concurrent cleanup
attempts may report an identity/removal conflict and require retry; no command
recreates a source or hides an unsuccessful filesystem operation. Directory
metadata durability across sudden power loss is not guaranteed. Corrupt copies,
unrecorded staging orphans, automatic retention and recovery UI remain separate.

## Scoped inspection and recovery visibility

`JobCoordinator.inspect_upload(request_id)` returns the immutable `StoredUpload`
for that coordinator's service/account/team/project scope, or `None` when no
matching local request exists. `upload_recovery_plan()` lists that scope's saved
records in local request-ID order, each wrapped in a frozen `UploadRecoveryItem`.
These public methods use the configured upload owner; callers do not need access
to its private store. The existing upload store, source staging and transfer
policy configuration is still required. Inactive owners reject inspection.

The snapshot retains the original scope, Blender origin, revision, source
identity, remote upload/asset IDs and durable part claims/receipts. It contains
no signed transfer URLs, credentials or original source paths. It is saved
metadata, not proof that the staged file still exists or that a Blender target
can receive a reference. No file verification, network request or recovery write
occurs during inspection. Invalid persisted records raise `StoreError` without
resetting the evidence or returning a partial recovery list.

| Saved progress | Suggested `UploadRecoveryAction` | Meaning |
| --- | --- | --- |
| Prepared | `REVIEW_SOURCE` | Review the staged source before any explicit initialization |
| Initializing or initialization uncertain, without an upload ID | `RECONCILE_UNKNOWN` | Preserve uncertainty; do not guess an ID or recreate the upload |
| Uploading without a part claim, with every receipt saved or this owner's part plan, and a current origin | `REVIEW_TRANSFER` | The owning session can send the remaining parts or request completion |
| Uploading without a part plan or current origin, with a claim this owner is not sending, or part uncertain | `RESTART_UPLOAD` | Part destinations cannot be recovered; explicit `restart_upload` abandons it and uploads the saved copy again |
| Uploading with a part this owner is sending, finalizing, finalization uncertain, or processing | `POLL_REMOTE` | A known upload can be retrieved explicitly; pending status does not release a claim |
| Imported, failed, canceled or abandoned | `FINISHED` | No further upload recovery is suggested; import alone does not apply a Blender reference |

Suggestions never authorize initialization, transfer, completion, retry or a
claim reset. An in-flight worker may still finish after inspection or context
deactivation; its receipt stays in the original scope. Earlier snapshots remain
unchanged, and subsequent commands must still pass their revision/state guards.
After restart, a newly configured owner reads the same claims without assuming
that a previous worker is dead. The optional
[JobSession upload facade](BLENDER_JOB_CONTEXT.md#upload-references) forwards these
commands through its existing workers and guarded main-thread delivery. Form UI
and local MCP recovery controls use these commands as described below.

## Active reference uploads

[`ReferenceUploads`](../scenario/blender/reference_uploads.py) is a main-thread
facade over the selected `JobSession`, scoped upload store and existing workers.
`upload_reference` starts one explicitly authorized local-file upload, with an
explicit `kind` of `image` (the default), `audio`, `video` or `3d`, for the
[files an agent may upload](MCP.md#historical-upstream-reference) only; `capture_reference`
prepares a viewport/camera still or clip, or a selected-mesh GLB first. Both
return a session-owned handle immediately. Poll `reference_upload_status` until
the saved state is `imported`, then pass its `asset_id` to the intended model or
workflow file input in a fresh estimate. Uploading does not submit generation or
approve its cost. Generation forms use the same
owner through **Upload reference** for image, audio, video and 3D files; loaded
workflow forms use it through [workflow input attachment](#workflow-input-attachment). Image
inputs also support explicit still captures; video inputs support explicit clips.
**Upload selected mesh** creates one GLB reference for an empty 3D input. Preparation
runs on the main thread in private temporary storage retained until asynchronous
staging finishes or the session retires. Clips use the preview range when enabled,
otherwise the scene range, at 1280x720 with no audio or implicit duration padding.
Capture settings/current frame and mesh selection are restored. Restoration writes
the preview-range toggle only when it changed, and a clip keeps an enabled preview
range that already equals its span. In the GUI, Blender answers any write to that
toggle, even an unchanged one, with a same-frame update after the capture returns.
Its frame hook would invalidate the upload origin captured right after the snapshot.
Real frame changes still invalidate it; see
[origin and quote lifetime](BLENDER_JOB_CONTEXT.md#origin-and-quote-lifetime).
Stills/clips need an interactive viewport; mesh export also works headlessly.
Uploaded snapshots do not track later source edits. It never uploads while drawing or pricing.
Render forms use the same lifecycle for explicit scene captures and first frames.
Their role is part of asynchronous attachment and saved-confirmation guards.
A disabled first-frame slot is omitted from generation while its upload retains
its independent lifetime; changing the chosen path requires a new preparation.
Requests require one uploaded scene slot in the current model input and place it
before style references. A Render Video first frame goes to the model's
first-frame input, or first in its reference-image array when the model has no
such input or says its first frame can't be combined with the scene clip. A Render Video first frame
[handed from a saved image result](BLENDER_JOB_CONTEXT.md#saved-image-to-render-video-first-frame)
needs no upload: it reuses that result's existing asset ID under the same scope,
asset and kind markers, with no upload marker or local file path.

The upload kind and final file extension must match the local format policy.
An omitted kind remains `image`; a video path does not silently change the kind.
Unknown kinds and mismatched/unsupported extensions fail before staging or SDK
requests. MIME metadata follows the documented Scenario upload guide below.

| Reference kind | Accepted filename extensions |
| --- | --- |
| `image` | PNG, JPG/JPEG, WebP, GIF, AVIF, TIF/TIFF, HEIC/HEIF, SVG |
| `audio` | MP3, WAV, OGG, M4A |
| `video` | MP4, WebM |
| `3d` | GLB, GLTF, OBJ, FBX, STL, PLY, VOX |

This is filename-based metadata selection, not media decoding, conversion or
provider validation. Only the chosen file is staged. GLTF/OBJ sidecars, external
textures and buffers are not discovered or uploaded; prepare a self-contained
input when the model needs one. Model-specific format constraints still apply.
Status and saved/recovery inspection expose the persisted `kind` and
`content_type`; these are null in status until staging has persisted the intent.
All kinds share the same byte limits, origin guards and uncertain-write policy.

Multipart request MIME values follow the public upload contract, including
`audio/m4a` for M4A and `application/vnd.autodesk.fbx` for FBX. These need not
match aliases on generated or inspected assets. Before PUT, the saved MIME
must match the upload record exactly; do not normalize it from a later asset
response. The saved chosen filename is compared with `originalFileName`;
response `fileName` identifies the server storage object, not the chosen name.

Preparation snapshots up to 256 MiB into private storage, using 8 MiB parts
(the final part may be smaller). Metadata replaces basename characters outside
ASCII letters, digits, dots, underscores and hyphens with underscores; the original
file is never renamed. Later edits to that file cannot change the staged bytes.
These are application limits, not a claim that every format/size is accepted by
every model. A snapshot of the scene stops further mutation claims after any scene
revision change. A chosen file's bytes never depended on the scene, so
`upload_reference` and the forms' file uploads take a
[destination-only origin](BLENDER_JOB_CONTEXT.md#origin-and-quote-lifetime): they
continue through scene edits, frame changes, playback and renders, and stop only
when their scene is removed or replaced (Undo/Redo, file loading) or the
connection changes. Attachment still requires the same scene, form slot or input
and connection scope. A restart keeps whichever binding the upload had. Initialization,
each PUT and completion still require durable claims and use one attempt only.
Known processing uploads are polled at two-second intervals. Uncertain responses
stop automatic mutation; no later status read releases a part claim or replays
initialization/completion. The facade retains at most 128 handles per session.
An admission rejection from a full worker/outcome queue has not started a task;
the facade waits briefly and retries admission only. It never treats a failed
task as this safe case. Explicit refresh that confirms `processing` re-enables
status reads, while uncertain initialization/part/finalization cannot be replayed.
The facade owns recovery tasks and drains their outcomes even after the dialog
closes or the MCP caller disconnects. Recovery suspends automatic admission for
its record until the saved outcome has been observed; a successful local cancellation cannot schedule
initialization from a stale in-memory projection. Completed task projections are
also refreshed from durable storage before selecting their next command.

The [Scenario upload guide](https://docs.scenario.com/get-started/content/uploading-assets)
documents multipart uploads and signed S3 destinations. The active `S3UploadPolicy`
accepts HTTPS S3 global/regional REST endpoints under `amazonaws.com`, including
virtual-hosted and dual-stack forms described by the
[AWS S3 endpoint guide](https://docs.aws.amazon.com/AmazonS3/latest/developerguide/RESTAPI.html).
It also accepts virtual-hosted
[Transfer Acceleration](https://docs.aws.amazon.com/AmazonS3/latest/userguide/transfer-acceleration-getting-started.html)
endpoints, `BUCKET.s3-accelerate.amazonaws.com` and its `.dualstack` form, which
the live create response signs today. Acceleration bucket names contain no dots,
so exactly one bucket label is allowed. It rejects the bare acceleration endpoint,
extra labels, S3 website endpoints, other AWS services, custom CNAMEs, IP literals,
credentials, fragments and nonstandard ports. It is intentionally broader than
an exact bucket allowlist: the selected SDK's validated create response is
trusted to choose the bucket/path. No local caller supplies a transfer URL.
The coordinator verifies upload/source identity, numbered parts and expiration
before that URL reaches the transport. Result downloads retain their separate
exact CDN-host policy. Neither transport sends Scenario credentials, follows
redirects, uses ambient proxies nor retries a PUT. The signed URLs authorize only
the `host` header, so the transport's Content-Type header does not affect the
signature; storage acknowledged such a PUT with HTTP 200.

Capture files live in a private temporary directory. The session retains that
directory until staging completes, including while retired workers finish; drain
or shutdown removes it. Filesystem cleanup errors are sanitized and retained for
a later shutdown attempt. Process crashes can leave temporary or staging orphans;
automatic orphan retention remains unimplemented. Render-result capture forces
PNG and restores the scene's output settings. Viewport/camera capture needs a GUI;
image inputs in generation forms also offer Render Result capture.

After restart or context change, `list_reference_uploads` reads only the current
credential scope's saved metadata. `recover_reference_upload` requires its context
token and observed revision. It can refresh a known remote ID, cancel an unclaimed
local preparation, restart an upload whose suggested action is `restart_upload`,
or delete the verified private source of a finished or abandoned upload. Restart
requires online access and handle capacity. It reuses the original origin while it
still identifies the selected scene and target, and otherwise captures the selected
scene. It returns the abandoned record plus a new `reference_id` that advances
like a fresh upload; the agent polls it with `reference_upload_status`. A ticket
stopped by `UploadPlanUnavailable` names this action. Recovery cannot recreate an
unknown upload, resend a part to the same upload, finalize again or abort remotely.
Source cleanup never deletes the original file. Inspection/imported metadata does
not authorize attaching a reference into a different scene.

Offline native tests exercise actual SDK wrappers, SQLite and workers through
synthetic API/PUT responses, including changed origins, lost responses, restart
inspection and cleanup. Transport unit tests separately exercise signed PUT and
host rejection. This is not live S3/import acceptance or GUI interaction proof.

### Typed form attachment

[`reference_form.py`](../scenario/blender/reference_form.py) binds each explicit
upload to the original scene object, lane, model ID, input kind, reference slot
and source fields. Supported kinds are image, audio, video and 3D; still captures
are offered only for image inputs.
A saved marker blocks a second upload click, including after reopening a blend.
Local validation or rejected queue admission removes the marker only when no
task was accepted; the user can correct that input and retry. Asynchronous errors
retain it. A transient timer context without the originating scene pauses further
admission and attachment; returning to that scene still requires the unchanged
origin, model and slot.
The main-thread maintenance pump converts the unchanged slot to a Scenario asset
only after an imported observation and a fresh origin check. It marks the label
as an uploaded snapshot and invalidates the form's prior estimate. Generation
then quotes/submits the immutable asset ID, not the later contents of the original
file. An active marked upload that has not attached blocks request construction
while it is pending or uncertain, and a local file or capture not yet uploaded
blocks the final price: generation never uploads implicitly. A disabled render
first frame is excluded from the request and does not block it. Explicit clips
and mesh snapshots use these same guards.
MCP `render_form(action=prepare)` uses these same role-tagged slots and guarded
uploads. Configure and inspect are local form actions; quote and submission do
not initiate an upload. Explicit reference removal requires its current slot key
and leaves saved upload work intact. See the
[MCP render sequence](MCP.md#historical-upstream-reference).
An unloaded or list-only model schema is a temporary state, not an edited input.
In-flight bindings wait for model inputs before attachment and recheck the kind
when they load. Saved assets retain their scope checks; request construction
reports the unloaded model until its schema is available. Saved-reference
confirmation also requires loaded inputs and a fresh review after loading.

Uploaded references retain their credential-scope fingerprint. Reopening under
another connection, changing the input kind or manually changing that asset ID
blocks pricing rather than silently using it in another account/project. These local markers are not server
permission checks and contain no credentials. A scene/model/slot change prevents
late attachment; it does not abort already authorized remote work. No upload
operation grants generation approval.

**Inspect uploads** snapshots this connection's durable metadata without sending
bytes. Its paginated view shows errors, state and request IDs. Explicit actions
refresh a known upload, cancel an unclaimed preparation, upload again an
unfinished upload whose suggested action is `restart_upload`, or clean a terminal
upload's verified private staging copy. Cancellation, **Upload again** and cleanup
ask for confirmation. These actions share MCP recovery ownership, context/revision
guards and no-replay rules; cleanup preserves the original file. The replacement
upload does not attach itself: its form slot keeps the saved-upload marker until
**Use this reference** attaches the imported replacement.

Automatic attachment handles do not survive restart. **Use saved upload** on a
reference slot or **Saved uploads** on a typed input offers attachment only for
matching imported media from the selected scope. **Use this reference** opens a
separate confirmation naming the scene, lane, model, input, reference and file.
The inspection view captures the destination before opening; a scene, model or
reference-list change requires reopening it. Confirmation consumes a single-use
approval and rechecks the stored record, current scope, scene revision and entire reference
form before adding or replacing a slot. Changed selections require a fresh review.
Attaching invalidates the previous price and never submits generation. Native
tests save/reopen an actual blend before explicitly approving the recovered
reference; they do not establish physical GUI interaction or live-service acceptance.
Never remove and re-add a reference as a substitute for reconciling an uncertain upload.

### Workflow input attachment

[`workflow_uploads.py`](../scenario/blender/workflow_uploads.py) uploads one
explicitly confirmed local file or snapshot into a loaded workflow file input
through the same `ReferenceUploads` owner, session, store, workers and signed
transfers. It adds no SDK method, transport or worker pool: initialization,
parts, completion and status reads keep the `uploads.create`,
`uploads.trigger_action` and `uploads.retrieve` mapping and their one-attempt,
no-replay rules described above. Its in-flight bindings share the facade's
128-binding capacity with generation forms. Each upload also takes one of the
facade's 128 handles per session, which finished uploads keep until the facade
is replaced: after a credential or project change, after a blend file loads
(Open, Revert or File > New deactivates the job session) or when the extension
restarts.

Uploadable inputs are file fields whose saved schema kind is image (the default),
audio, video or 3D, using the same sources as generation forms. Other kinds and
fields that list allowed asset IDs are refused before staging. Preflight also
refuses occupied single inputs, full arrays, a missing or unreadable file and an
existing marker; starting rechecks the reviewed connection and scope.
A persisted `_scenario_workflow_upload` ID property marks the input before origin
capture; it remains after uncertain or failed remote work, blocks pricing and
prevents a second upload. Local validation or rejected queue admission removes it.
JobSession refuses the next initialization, part or completion admission once the
captured origin is revoked, and the ticket then stops for good: for a snapshot, by
a scene edit, frame change, render or undo; for a chosen file, only by undo, redo,
removing its scene or loading a file. `ReferenceUpload.sent_nothing` identifies a stopped
ticket that never reached Scenario: staging failed before a saved record was
observed, an admission was refused while the record was still `prepared` (it
reaches `initializing` before the first service request), or the record was
canceled. Delivery then removes only its own marker, keeps existing input values
and leaves a note on the input. Each input keeps only its latest note, stored by
the scene's session UID and input name, and shown only while the loaded workflow,
its schema and the input's values match those it was recorded with. It survives
undo and redo, notes on other inputs never evict it, and another upload admitted
into the input retires it, as do a Library or saved-upload attachment to it and
**Clear reference**; a retry refused before admission keeps it. Notes live
only on the facade, so replacing it, as for its handles above, also drops them.
The upload operator returns `FINISHED`
whenever a new marker remains after a start failure, so its undo step is recorded.

The maintenance pump attaches only after an imported observation and a fresh
origin check, and only while the workflow ID, schema digest, input kind, marker
token and input value are unchanged. Once it observes the saved record for an
unchanged input, it records the request in `_scenario_workflow_upload_request`,
even if the upload later stops; an upload released as having sent nothing records
none. Attachment uses the same checked append and scope digest/canonical value
binding as Library workflow references and removes the marker. Explicit **Cancel preparation** of a `prepared` record frees every input
still marked for it, matched through the facade's marker-to-ticket ledger, which
survives undo, or through that recorded request after reopening or a connection
change. A blend file saved before the first observation has no request link.
A failed, canceled or abandoned record ends delivery with an error and keeps the
marker; the new upload that **Upload again** sends is not bound to the input.
**Stop waiting** releases any marked input without a running delivery: it removes
the marker and request link, keeps values and leaves the saved record untouched.
Saved uploads use the shared inspection view with a workflow destination:
attaching needs a separate single-use confirmation that rechecks the stored record,
scene origin, scope and complete form signature, and supersedes an in-flight
upload into that input. Undo/redo, blend reopening and connection retirement
discard in-memory bindings without resending bytes. The upload operator records
its own undo step, so among history changes only undoing that step removes the
marker. Selected-mesh uploads keep their captured source, so workflow quotes bind
them as described below. Local MCP exposes the equivalent sequence for
file, viewport, camera, clip and selected-mesh sources: `upload_reference` or
`capture_reference`, then the imported asset ID in `estimate_workflow` parameters.
The native **Render result** snapshot has no `capture_reference` source.

## Captured mesh export provenance

UI and MCP mesh snapshot uploads persist local export provenance in the
scoped upload intent. Before exporting, the main thread captures each selected
mesh's session identity, datablock identity, name, base geometry/attribute and
material-slot fingerprint, world matrix, parent and collection membership. It
rechecks those values after export. A changed source rejects admission and cleans
the private export without starting an upload.

Each capture pass completes its fingerprints and source snapshots before
`JobSession.capture_many` registers origins using one current scene-membership set
for every selected source. Membership is rebuilt after export rather than retained
across it. Main-thread and active-session guards precede snapshot reads; live-target
validation precedes registration. A first-pass budget, attribute or snapshot-read
failure records no new origins, so deleting that rejected source cannot invalidate
other work through the captured-target registry. Membership rejection also records
no partial origins. Scene-only origin capture does not enumerate scene objects.

Known capture rejections preserve their corrective messages through the shared
UI/MCP upload error path, including source/selection changes and export bounds.
Unexpected exporter or filesystem exceptions retain a generic message so private
details are not exposed. Both paths clean the private export before admission.

The durable `mesh_source` records the exact GLB SHA256, each source target ID,
base-mesh fingerprint and world matrix. Its exporter convention is
`blender-world-gltf-y-up`, with evaluated modifiers and animations disabled,
matching the existing GLB exporter settings. One mesh becomes the upload origin's
explicit target; multiple meshes retain all IDs and leave the primary target
unset. The metadata is immutable and never added to a Scenario API request.
Worker staging verifies that its copied bytes match the recorded export hash.
It cannot substitute a newer export or another source file under that provenance.

Capture permits at most 64 distinct meshes. Export fingerprints have their own
policy, independent of the in-place mesh application's component/type limits.
Numeric geometry and attributes use Blender's bulk reads, including quaternion,
matrix and short-vector attributes. String attributes use bounded byte reads.
Before reading any buffers, each snapshot pass budgets at most 256 MiB across
all distinct selected mesh datablocks, including metadata and worst-case string
storage. Shared datablocks are read once per pass; an oversized selection rejects
before export with a request to select fewer or simpler meshes. The second pass
has the same aggregate bound and detects source changes after export.

The GLB has a separate 256 MiB file limit. Export and fingerprinting remain
synchronous and can pause Blender; staging and network transfer remain on the
shared worker. This bounds additional snapshot work, not exporter memory or
wall-clock time. The export fingerprint is not interchangeable with the stricter
mesh-application fingerprint. It describes base mesh data and material
slots, not evaluated modifier output, material node graphs, shape-key animation
or a complete rig contract; the file hash identifies the actual exported bytes.
A parented/scaled synthetic mesh is exported and imported natively to verify
world-coordinate roundtripping. This does not establish any provider's output
alignment or topology preservation.

Upload schema 2 requires an explicit nullable `mesh_source`. Opening a schema-1
store atomically validates all scopes and records, adds null provenance and
preserves identities, revisions, receipts and uncertain claims. Any malformed
legacy row rolls the whole upgrade back. Older uploads and ordinary file/capture
uploads remain valid with no mesh provenance; no source is inferred from names.

The shared model/workflow quote path binds imported captured sources to
their typed 3D input parameters and persists them before spending; see
[generation source bindings](JOB_STORAGE.md#captured-mesh-inputs-in-generation-intents).
Quotes look up only selected asset IDs within the credential scope, using a
partial SQLite index of imported captured meshes. Each unique input needs one
query and at most two decoded records; two matches still reject an ambiguous
source. Repeated array entries keep their original positions without repeating
the lookup, and absent optional inputs do not read upload storage. External
assets and ordinary uploads do not gain an inferred source. Each quote allows at
most 128 distinct 3D input asset IDs across all parameters, including unmatched
external assets. A 129th identity fails before another lookup; repeated IDs share
the same lookup budget.

Opening existing schema-2 storage builds the derived index once, without changing
saved records, revisions, schema version or uncertain claims. SQLite maintains it
as uploads change; subsequent quotes do not scan retained upload history. Index
entries require valid JSON, so a malformed record cannot prevent other records
or credential scopes from opening or querying storage. Reading that damaged
record directly still fails and preserves it for recovery. Index
creation can take time for a large existing store. A missing index during lookup
fails instead of silently falling back to a history scan. Preparation and
submission still recheck the selected upload's exact identity and revision.

[Applying a result to the captured source](MESH_APPLICATION.md#applying-to-the-captured-mesh-source)
uses a retained live guard, never a target found by name after restart.


## Captured media byte approval

The [Film capture controls](FILM_PLAN.md#shared-capture-and-upload-approval)
separately approve uploading a completed still/clip through `ReferenceUploads`.
They pass its rendered `expected_sha256` through the existing session, worker
and upload command to `UploadSources.stage`. Staging compares the copied whole
file against that exact hash before publishing an upload intent. A mismatch
removes only the new staging copy and cannot initialize a remote upload.
Ordinary file uploads omit this optional additional binding.

No SDK endpoint, authentication, retry or multipart-transfer contract changes.
After staging, the existing shared initialization, transfer, finalization and
inspection commands own remote work and uncertainty. The Film capture review
retains one upload handle and never silently repeats it. It keeps the original
capture directory alive until work stops; explicit capture discard preserves
the separate durable upload staging/history. Associating an imported upload with
a recipe task remains the explicit local action below.

## Film task association

The [Film shared command](FILM_PLAN.md#saved-upload-tasks) can associate an existing
`imported` upload with an explicit production/task in the job database. It requires
the selected upload scope and exact observed revision, then retains the asset,
kind and source digest. A dependent Film quote rechecks this evidence locally.
Pending or uncertain upload states cannot become references, and SDK model
imports are not asset uploads.

Association and idempotent reassociation send no SDK request, transfer bytes or
read/delete source files. They do not relax initialization, part or completion
claims. A finished staging cleanup retains the imported record, so the associated
remote asset remains reusable. The native **Use saved upload** control and local
MCP `bind_film_upload` command perform this explicit association through the
selected Film session. Capture upload does not choose a task automatically.

## Retained upload media measurement

`UploadCommands.measure_media` accepts the exact current revision of an imported
upload and inspects a private copy of its retained staging bytes. The saved intent's
whole-file size and SHA256 must match; the imported record is rechecked after
inspection. A missing or explicitly discarded staging source cannot be measured,
even though its remote asset association remains valid. Inspection never refetches,
reuploads, repairs or removes the original source. No SDK operation changes.
See [Film media preparation](FILM_PLAN.md#verified-media-preparation).
