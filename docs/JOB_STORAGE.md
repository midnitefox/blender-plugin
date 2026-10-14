# Scoped job intent storage

[`JobStore`](../scenario/core/jobs/store.py) is the local persistence for shared
jobs. The store itself does not start workers, call Scenario or modify Blender.
The shared coordinator records every UI and local MCP job in it before paid
dispatch, and the prototype registry is kept only for read-only local records.

## Identity and ownership

The caller supplies one immutable `JobScope`: exact API base URL, stable local
account scope, optional team and optional project. These are non-secret identifiers,
not credentials. Every query and key includes that scope. Switching accounts or
projects means selecting another store instance; a request ID from another scope
cannot be read or changed through the current instance.

For explicit API-key credentials,
[`open_credential_store`](../scenario/core/jobs/credential_storage.py) binds that
scope to the exact service and selected key/secret pair. A random installation-local
key signs this input with HMAC-SHA256; only the resulting `local-key-…` pseudonym
enters `JobScope.account_id`. It is not a server account identifier and is never
sent to Scenario. No discovery request or team/project selection is required.
Optional explicit team/project values still partition the store. Changing either
credential selects different records; switching back to the original pair restores
access to its records. Credential rotation does not automatically transfer jobs.

The active Blender catalog opens this store in the extension user-data directory
at `state/shared-jobs/`. `scope.key` must be backed up together with `jobs.sqlite3`.
The key is published complete without replacing a competing process's key; an
invalid key or a missing key beside an existing database fails explicitly and
preserves the files. First-time creators serialize on the permanent `.scope.lock`
file, then atomically replace their private temporary file into the still-absent
key path. This requires no hard-link support. Lock contention waits at most two
seconds before an explicit retry-later error; OS file locks are released when
the descriptor or process closes. Do not unlink the lock while Blender is using
the directory, since competing creators must lock the same file. New lock files
remain empty: Windows supports [locking beyond EOF](https://learn.microsoft.com/en-us/cpp/c-runtime-library/reference/locking?view=msvc-170),
so no sentinel write can race with another owner's mandatory byte-range lock.
No raw API credential is written by this binding. The local
key is not encryption, and losing it prevents recreating the old scope even with
the original credentials. Different installation keys produce different scopes.

The runtime retires this store selection together with its catalog when the
credentials or project change. `ensure_job_store()` reopens the selected local
history after a runtime reset without starting workers or replaying submissions.
Storage failure blocks creation of the catalog context too. The first job command
lazily activates the selected [JobSession](BLENDER_JOB_CONTEXT.md); prototype
records are not imported.

`JobIntent` freezes the local request ID, model/workflow target (or the fixed `prompt`/`translate` target for prompt helpers), payload SHA-256,
server quote SHA-256, exact quoted cost as a decimal string, and originating
file/scene/target/revision IDs. IDs describe the captured origin, never current
selection. Generate a fresh request ID for an intentional new operation and hash
the exact immutable payload/quote bytes held by the SDK estimate. The caller must
still verify the estimate belongs to the current adapter and matches the current
scope, payload and origin before authorizing dispatch.

Only hashes and exact cost are persisted for payload/quote identity. No prompt,
credential, raw service response, absolute file path or signed storage URL is stored.
Result manifests retain portable basenames, asset IDs, media types, optional
expected size/digest, allowlisted image texture roles, the delivered file's source,
server-declared projection and verified download receipts.
An old stored quote is not reusable spending authorization after restart. The
store does not itself verify a supplied fingerprint against a live estimate or
prove ownership of a supplied remote ID; those checks belong to the coordinator.

## Atomicity and failures

Pass a database path beneath Blender's `bpy.utils.extension_path_user(...)` from
the Blender integration. The core module accepts a path so unit tests and workers
remain bpy-free; it must not be pointed inside the installed extension. Use a
private local directory. Newly created directories/database files request 0700/
0600 permissions where the OS supports them; existing parent permissions and
Windows ACLs are not changed. Scope separation is application isolation, not
on-disk encryption or protection from another process with filesystem access.
Every connection rechecks that the database is a regular nonsymlink file,
including after a competing creation. The parent must remain trusted: this check
and SQLite's path open are separate operations, not an atomic no-follow open.

The database has an application ID and schema version **11**. SQLite transactions
with `synchronous=FULL` commit the whole change or report `StoreError`; no cached
in-memory result is reported as saved before commit succeeds. `BEGIN IMMEDIATE`
serializes writers across threads/processes. Each operation owns a connection,
with a two-second lock timeout. Storage failure must stop the caller before the
next external side effect. Filesystem/hardware durability still depends on the
platform honoring SQLite's synchronization operations; use local storage.

Creation rejects an existing request identity. Updates require the last observed
revision and increment it atomically, so only one racing caller can claim a
prepared request. `StoreConflict` means reload; it is never permission to create
another request or resend. Intent fields and a known remote job ID cannot change.

Foreign databases, unsupported versions, malformed records and mismatched stored
identities/revisions raise errors. They are preserved for explicit recovery,
never silently replaced with empty history. An already-open store also fails if
its database disappears. Previous shared schemas 2 through 10 upgrade in one
transaction that validates every scope, record, identity and revision. A corrupt
row or failed commit preserves all previous rows and the old version. This is
not a prototype import; version 1 and foreign databases remain rejected. Schema 2/3
results receive an unknown (`None`) texture role; schema 4 roles are preserved. Migration never contacts
Scenario or infers semantics from filenames. Schema 3 application destinations
are preserved, while schema 2 retains its original-origin application semantics.
Schemas before 5 receive an empty local-application history; schema 5 preserves
its existing claims, including unfinished applications. Schemas before 6
receive empty mesh input bindings; schema 6 preserves its captured mesh sources.
Schema 7 cloud results remain cloud records without synthetic spend or Film
bindings. Schemas before 10 receive the asset's own file and no projection (see
[schema 10](#schema-10-declared-originals-and-lane-defaults)). Schema 10 rows keep
their exact bytes ([schema 11](#schema-11-wav-originals)). Older extension builds
reject a newer schema; stop older Blender processes before upgrading and do not
expect an older build to open the upgraded store.

## Explicit cloud result records

Schema 7 adds `CloudJobIntent` for a completed model job retrieved using the
selected SDK connection. It retains a local recovery ID, exact scope, locally
captured scene origin, model ID and `source: cloud`. It has no payload or quote
hashes, no quoted cost and no captured mesh source. Selecting a cloud result
cannot invent proof of a local generation or authorize another submission.

The [coordinator](JOB_COORDINATOR.md#adopting-a-completed-cloud-job) verifies
the remote job before calling `adopt_cloud_job`. The store inserts it directly
as `succeeded`, with the confirmed remote ID and no result manifest. Only the
existing download and application states are valid for this intent type;
`create` and submission reject it. Result retrieval and destination approval
remain separate commands.

Adoption checks the selected scope for the remote ID within the same write
transaction. One existing record with the same model is returned unchanged,
preserving its original quote, origin, state and receipts. Ambiguous remote IDs,
conflicting models and local-ID collisions fail explicitly. Repeated or competing
reads cannot overwrite a saved application or create a second recovery record.
This is an explicit cloud read, not bulk import of the prototype registry.

## State boundaries

| Stage | Allowed progression |
| --- | --- |
| Prepared intent | `prepared` → `submitting` or local `canceled` |
| Submission attempt | `submitting` → `remote` with a remote ID, or `uncertain` |
| Lost response | `uncertain` → `remote` only after authoritative correlation |
| Remote job | `remote` → observed `succeeded`, `failed` or `canceled`, or durable `cancel_requested` |
| Cancellation claim | `cancel_requested` → observed `succeeded`, `failed` or `canceled`; never reset or replay |
| Download | `succeeded` → `downloading` → `ready` or `download_failed`; explicit download retry is allowed |
| Blender application | `ready` → `applying` → `applied` or `apply_failed`; explicit application retry requires origin/target checks |

There is no transition from `submitting` or `uncertain` back to `prepared` or
`submitting`. Neither a timeout nor an empty job listing permits replay. Remote
cancellation may race with success; the coordinator must reconcile the server's
actual state rather than declaring a cancellation on request acknowledgement.
A cancellation command claims `remote → cancel_requested` with the same atomic
revision check before sending. A second coordinator/process cannot claim it
again. Restart recovery polls this state even after a crash before sending;
there is no lease expiry or explicit reset/reattempt command. Schema 3 retains the
immutable result manifest and receipts introduced in schema 2 and adds a separate
`application_origin`. Claiming `applying` atomically saves that destination before
any Blender mutation. Automatic application uses the original intent origin;
explicit recovery may supply a newly approved destination. Neither changes the
intent. Application receipts preserve the claimed destination. A new destination
requires a new claim after confirmed rollback, never an interrupted `applying`
record. Upgrading schema 2 records in application states records their original
origin as the destination. Older readers reject schema 3 instead of dropping it.

Opening storage never executes or automatically advances work. At startup, once
old workers are stopped, the coordinator must treat saved `submitting` as
uncertain, poll known remote IDs, and surface interrupted downloads/application
for explicit recovery. In particular, an interrupted Blender application may
already have changed the scene; do not blindly apply it again. View closure does
not imply cancellation, and a reopened file/account must not retarget a result.


## Result transfer ownership

`result_transfer_lock(request_id)` holds a nonblocking OS file lock keyed by the
private database filename, full scope hash and request hash. Result download and
recovery commands use this same lock. POSIX uses `flock`; Windows locks byte zero
with `msvcrt.locking`. Other platforms fail closed. Locks release when their
file descriptor closes or the process exits. They have no timeout lease and do
not hold a SQLite transaction during network work.

Sidecar lock files remain beside the database in a private directory. Never
unlink them while any owner may be active: a replacement inode could admit two
owners. Symlinked/nonregular/multiply-linked lock files are rejected. The parent
directory is resolved once when opening the store, so aliases such as macOS
`/tmp` share the database and lock location; retargeting an alias cannot redirect
an already opened store. The database filename itself is never resolved through
a symlink and symlinked database files remain rejected. The resolved parent
must remain privately owned and all owners must use the same canonical database
path on a local filesystem that supports these locks. This is a cooperating
writer protocol, not protection from arbitrary disk access or direct store calls.
Stop all older extension processes that do not use this protocol before running
recovery; a schema upgrade does not establish lock compatibility.

The coordinator's explicit `recover_downloads` command rechecks receipts under
this lock before moving an interrupted `downloading` record to `ready` or
`download_failed`. It never resets a submission or uncertain application, adopts
unreceipted bytes, deletes files or performs network work. See the
[recovery command](JOB_COORDINATOR.md#explicit-interrupted-download-recovery).

## Durable result metadata

After observing remote success, `set_results` binds a nonempty tuple of at most
128 `ResultAsset` entries once. Each entry has an opaque asset ID, portable
basename, normalized media type, optional expected size/SHA256, optional
allowlisted `texture_role`, the delivered file's `source` and an optional
server-declared `projection` ([schema 10](#schema-10-declared-originals-and-lane-defaults),
[schema 11](#schema-11-wav-originals)).
The role is independent of MIME: it describes image
semantics, never a file decoder. Unknown roles remain `None`; arbitrary metadata,
prompts and URLs are not retained. Current schema records require the role key
even when null, so a truncated record cannot silently gain defaults. Duplicate asset
IDs and filenames (case-insensitive for portable filesystems) are rejected. The
caller must obtain metadata from the original scoped SDK job/asset responses;
the store does not prove provider ownership or follow an incoming URL. Use a
private result directory unique to the scope/request, so filenames cannot collide
with another job's output.

The manifest is committed before `downloading` can be claimed. Each successful
transfer calls `record_download` with its `DownloadedResult`; it must match the
manifest's name and any expected size/digest. Receipts cannot be replaced.
Revision checks and SQLite transactions serialize these writes with other job
updates. A write failure preserves the previous durable record and propagates.
Partial receipts survive `download_failed` and an explicit retry. `ready` requires
a receipt for every asset. Neither a manifest nor a receipt is permission to
spend again or apply to a different origin.

Before using an existing receipt, call `verify_download` from the
[transfer module](RESULT_TRANSFERS.md). It rehashes the local regular file and
checks its size, bounded reads and stable file identity. Missing, modified,
symlinked or non-regular files fail without deletion or network work. Only the
caller can decide explicit repair; an unreceipted published file is not implicitly
trusted after a crash. Do not promote an interrupted `applying` record to applied
or retry it automatically: the scene may already have changed.

## Prompt Spark intents

Prompt Spark and translation intents use operations `prompt` and `translate`
respectively, and the same prepared, submitting, uncertain and known-remote
lifecycle. These operations add no dedicated database fields. Readers that do not
know them reject them instead of interpreting them as model/workflow intent.
Restart can inspect and refresh a known job ID; it cannot recover spending
authorization or replay the request from the stored hashes. Prompt text/results
are not persisted here.


## Durable local result reuse

A generation job's original `applied` state remains terminal. Local reuse adds
`local_applications` to that same scoped record rather than reopening generation
or overwriting its first application destination. Each entry retains a unique
application ID, the reviewed job revision, captured file/scene/target revision,
purpose and selected asset IDs. It stores no extra paths, prompts, credentials,
URLs or Blender names. Supported purpose labels are images, media, model,
mesh_edit, World and material; a label does not establish decoder compatibility
or scene acceptance. New mesh replacements use `mesh_edit`; new static-model
imports retain `model`. Existing claims keep their recorded labels, including
older mesh replacements stored as `model`. This adds a validated label without
changing schema-5 fields. Older readers reject an unknown label; do not downgrade
a store containing newer application purposes.

`claim_local_application` requires the exact current revision of an applied job,
valid selected result IDs and no unfinished reuse. It appends an `applying` entry
and increments the job revision in one SQLite write transaction. Competing owners
cannot both claim the same review. The original intent, exact quote, remote ID,
results, download receipts, first application origin and generation state remain
unchanged. Claims do not verify files or establish user permission: the shared
coordinator and the UI/MCP caller must provide those checks.

`finish_local_application` records only known success (`applied`) or confirmed
no-change/full rollback (`failed`) against that same unfinished identity and
revision. It increments the job revision without replacing history. A failed
write can have committed; inspect the exact successor and retry only a known
receipt, never scene work. An interrupted `applying` record survives restart and
blocks another reuse. Elapsed time, file loading or another process cannot clear
it. There is no automatic uncertainty resolver or manual reset in this component.

Histories are limited to 128 entries per job and preserve every admitted entry.
At capacity, new reuse is rejected without evicting prior outcomes. Decoding
requires every version-5 field, unique identities, ordered source revisions,
matching selected asset IDs and a consistent final outcome/revision. Unsupported
formats and malformed histories fail closed. Schema upgrades validate all scopes
and roll back completely on corrupt rows or commit failures; stop old Blender
processes before upgrading and do not downgrade the database.

Native UI and MCP use these records through the existing runtime and fresh
[saved-result approval](BLENDER_JOB_CONTEXT.md#explicit-local-result-reuse), including
byte verification before decoding. Status inspection exposes the separate local
outcomes. Resolution of uncertain scene outcomes still requires inspection; no
automatic reset or replay is offered.


## Captured mesh inputs in generation intents

Schema 6 stores `JobIntent.mesh_sources`, an immutable tuple of typed input
bindings. Each binding records the parameter and optional array index, imported
asset ID, upload request/revision, captured upload origin and exact mesh export
provenance. It accompanies the exact normalized payload and quote hashes before
the submission claim. Metadata stays local; the SDK request still contains only
the model/workflow parameters.

The shared quote path uses the SDK adapter's schema selection: model `inputs`
with a missing/null fallback to `parameters`, and workflow `inputs_definition`
with a missing/null fallback to `inputs`. Bindings use the same selected fields
as payload preparation; unused alternate schema keys cannot add provenance.
Absent or null schemas without a usable fallback stop before estimation.
The shared quote path matches only declared 3D file inputs to completed captured
uploads in the selected credential/project scope. Prompt strings, unknown assets
and ordinary file uploads acquire no inferred source. Repeated array values keep
their positions for both `type: file_array` and `type: file` with `array: true`,
matching payload validation. Ambiguous captured records for the same asset reject the quote.
The coordinator rechecks every binding before preparation and again before the
paid claim. A missing or changed upload stops dispatch while preserving the job.

Bindings describe the immutable uploaded snapshot, not the current geometry of
an object. Their origin may differ from the generation's current scene context.
They neither retarget the job nor authorize automatic application, provider
alignment or original-object recovery after restart. Reopening keeps the source
history available for explicit review; no paid request is replayed.


## Film task reservations

Schema 8 adds optional `JobIntent.film_task`: the opaque production ID, bounded
task name, normalized recipe SHA256 and task/dependency SHA256. Only model intents
may carry it. Existing schemas 2 through 7 receive `None` after full validation;
cloud result records remain unchanged. Current records require the field, even
when null. Invalid/truncated bindings fail closed. No raw recipe, prompt, path,
credential or signed URL is added to storage.

`create` checks the selected scope for the same production/task inside its existing
`BEGIN IMMEDIATE` transaction, then inserts the intent and binding together. A
competing owner cannot reserve the same task even with a different recipe digest,
request ID, origin or model. Every existing state keeps its reservation, including
an unsubmitted cancellation or failed generation. A new take requires a new task
name and separately reviewed fresh quote. Failed commits roll back both identity
and reservation; a committed write with a lost acknowledgement is discoverable
through `film_job` and cannot be repeated.

`film_job(production_id, task_id)` returns only the selected credential scope's
record, rejecting ambiguous matches. It never reconstructs a quote or changes
state. A derived index selects the exact scope, production and task without
decoding unrelated history. It checks at most two matches, preserving ambiguity
rejection and the existing transaction's atomic reservation. Index creation
preserves records and skips malformed JSON; direct reads still reject damaged
records. The [Film command](FILM_PLAN.md#durable-model-tasks) verifies completed
results and matching task/dependency digests before reference reuse. Low-level
store calls trust the coordinator's binding; a caller-supplied digest is not
proof of recipe validation or permission. Existing submission, transfer and
application states remain unchanged.


## Film upload associations

Schema 9 adds a `film_uploads` table in the same job database. It associates a
Film production/task binding with an existing imported upload's local request ID,
observed revision, remote asset ID, upload kind and source SHA256. The selected
scope is explicit. This is local reference metadata, with no upload state machine,
worker, remote request, path, prompt, signed URL or source bytes. The existing
upload database and its mutation/recovery rules remain unchanged.

`bind_film_upload` saves this immutable association inside the same immediate
transaction used by model-task reservation. Upload and model tasks share the
scope/production/task namespace: competing writers cannot assign both meanings
to one name. A conflicting source, revision or task digest requires a new task
name. Repeating the same association returns the original record unchanged,
including its recipe digest; appending unrelated recipe tasks can therefore
retain the original source. A lost write acknowledgement permits inspecting or
repeating this local association, never replaying an upload.

`film_upload` inspects one selected-scope association. Malformed fields, mismatched
SQL/record identities and conflicting model/upload records fail closed. The store
trusts the coordinator to supply verified evidence. Before binding and before
quoting a dependent task, the [Film command](FILM_PLAN.md#saved-upload-tasks) reads
the original upload in the selected scope and requires the exact imported state,
revision, source identity, kind and asset. Missing or uncertain uploads are not
adopted. Source cleanup may remove verified staging bytes without changing the
imported record or its remote asset association.

Shared schemas 2–8 upgrade in one transaction: all jobs are validated and preserved,
including schema-8 Film model reservations, and the new table starts empty. A
corrupt row or failed commit preserves the old version and data. No associations
are guessed from asset names, request similarity or old prototype files. Older
builds reject schema 9; stop old extension processes before upgrading.

### Legacy mesh size correction

`record_download(..., allow_mesh_size_correction=True)` is an internal recovery
exception for `model/obj` and `model/mtl` only. The result command verifies a
complete bounded storage response before enabling it. In the same revision-checked
transaction as the first receipt, the store replaces the stale expected size
with the receipt's actual size; identity, name, MIME, digest, role, intent and
previous receipts stay fixed. Other media, a wrong digest/name, an existing
receipt, a stale revision or a missing download claim are rejected. Ordinary
recording remains strict. This uses the existing schema and never alters remote
metadata; see [legacy mesh transfers](RESULT_TRANSFERS.md#legacy-obj-and-mtl-byte-counts).


## Schema 10: declared originals and lane defaults

Schema 10 batches into one upgrade the persisted fields that the accepted 0.10.0
designs need: result file source, result projection and per-scope lane defaults
for trained or private models. Live remote progress, a failed job's reason, the
composer's scene/lane binding, workflow step decisions, Film review/export state, asset organization
reviews and preview caches stay in memory or in disposable caches, so they add
nothing here. No intent-level lane or trained-model display name is stored:
restarted display records remain generic model jobs and show the quoted target
model. Saved `.blend` properties added by later interface work are separate from
this database, and the upload database keeps its own version.

Three open maintainer decisions would add persisted fields if adopted, each in a
new schema version with its own upgrade rather than a new reading of existing rows:

- An intent-level panorama purpose, needed only if live skybox assets lack the
  `metadata.type` that projection depends on
  ([#440](https://github.com/scenario-labs/blender-plugin/issues/440)).
- A `first_frame` local-application purpose, if handing a saved image to Render
  Video should become a durable application claim instead of a form binding
  ([#440](https://github.com/scenario-labs/blender-plugin/issues/440)).
- Each result's parent asset and file name, for exact binding of multi-file 3D
  packages ([#440](https://github.com/scenario-labs/blender-plugin/issues/440)).

Live skybox results must also confirm `metadata.type`, `originalMimeType`, an
`originalFileUrl` host inside the storage policy and a `Content-Length` on the
original's storage response; see
[declared HDR originals](RESULT_TRANSFERS.md#declared-hdr-originals-and-360-projection).

### Result source and projection

Every `ResultAsset` records `source` and `projection`; current records require
both keys, even when they hold the defaults. `source: asset` is the asset's own
file (`url`). `source: original` is a server-declared original (`originalFileUrl`)
saved instead of an image preview. In schema 10 only the OpenEXR labels
`image/x-exr` and `image/aces` qualify; [schema 11](#schema-11-wav-originals) adds
WAV originals of audio files. The saved media type is the original's. Originals carry
no size metadata, so `expected_size` stays unknown; the download instead requires
the storage response's `Content-Length`, and the receipt digest records the
bytes. Radiance HDR, mesh, splat, video and other audio originals keep the asset's
own file. `projection: equirectangular` is set only when Scenario's
`metadata.type` declares a 360 image (`skybox-base-360`, `upscale-skybox` or
`skybox-hdri`); `skybox-3d` and every other value stay `None`. The store rejects
other labels, an original of any other type and a projection on a non-image file. Neither
field authorizes World application, implies a measured dynamic range or replaces
Blender's decoder checks. The [result command](RESULT_TRANSFERS.md#declared-hdr-originals-and-360-projection)
chooses the file and refreshes its destination.

### Lane defaults for trained models

A separate `trained_defaults` table holds at most one explicit default per selected
scope and generation lane. The key is the same SHA256 of `JobScope` used by jobs,
so the credential pseudonym, API base, team and project override all partition it;
no server identity or discovered default project is inferred. A `TrainedModelDefault`
names the lane, a route and model identities only:

| Route | Identities |
| --- | --- |
| `stack` | A base model plus 1 to 16 distinct trained models, each with an optional finite strength |
| `composition` | A base model plus exactly one composition, without a strength |
| `custom` | One private runnable model, without picks |
| `direct` | One trained model run as its own target, without picks |

The row also repeats the full scope and is decoded strictly. No prompt, schema,
quote, thumbnail, URL or raw model record is stored. `trained_default(lane)` returns
the lane's state, with revision 0 when nothing was ever saved; `trained_defaults()`
lists the saved lanes ordered by lane name. `set_trained_default` and
`clear_trained_default` require the last observed revision and increment it in one
immediate transaction. Clearing keeps the row and its revision, so a writer holding
a pre-clear revision still conflicts instead of restoring a stale choice. Clearing
an already empty lane at its current revision changes nothing.

A default is a remembered choice, not spending authority. Callers must recheck the
routes against fresh model schemas and obtain a new exact quote before any paid
submission; a missing or incompatible model must block the lane rather than fall
back to the base model. Lane names are lowercase ASCII identifiers of 1 to 32
letters, digits and underscores that do not start with an underscore, such as `3d`
or `render_image`. The store does not compare them with the current catalog, so
renaming or retiring a lane never makes a saved row unreadable; callers decide
whether a saved lane still exists before offering it. Corrupt rows, a scope or
lane mismatch, a revision below 1 or unknown fields raise `StoreError` and are
preserved for recovery.

#### Runtime owner

Callers reach these rows through one
[`ModelDefaults`](../scenario/core/jobs/model_defaults.py) owner per selection,
not through the store directly. `runtime.ensure_model_defaults` binds it to the
selected job store, so its scope is exactly the credential pseudonym and optional
Project ID override that select local jobs. A blank override is the key scope;
no project, team or account is taken from discovery or a model list. It needs
no network, job session or connection check. Reading a lane never writes and
never falls back to another lane, project or credential scope. `save` takes an
explicit `TrainedModelDefault` with the revision the caller last saw, and `clear`
takes the lane with that revision; nothing else writes a default.

A credential or project change in `sync_catalog_context`, or a runtime reset,
retires the owner. Retirement waits for a running call, then every read and write
raises `DefaultsRetired`, a `StoreConflict`, while the rows stay in their own
scope. The owner's `context_id` is an in-memory token: the runtime's
`save_model_default` and `clear_model_default` refuse a token from an earlier
selection, so a choice reviewed in one project cannot land in another even when
both lanes are at the same revision. `parse_default` and `describe` convert
between this type and plain JSON values for UI and MCP callers. They refuse
unknown fields, booleans, numeric strings and non-finite strengths rather than
coercing them, and the description carries no scope or credential identity.

The Image and Render Image forms use the owner through
[`trained_defaults`](../scenario/blender/trained_defaults.py) on the main thread.
It reads each lane's state once per owner `context_id` into a cache that drawing
only reads, so a default another Blender session saves later is read after the
selection changes or a write is refused. **Save** and **Clear** pass the
`context_id` and revision drawn on their buttons, so a stale or foreign review is
refused and the cache is read again. Only `stack` and `composition` defaults are written. A form with no
explicit choice in the scope takes its lane default once the routes check against
fresh descriptions and the form lists its base; otherwise its price stays blocked
with the reason, never falling back to the base model. An unreadable row is
reported for its lane and left unchanged. `render_form` inspection and render
quotes apply a Render Image form's default the way the GUI pump does.

Local MCP `model_defaults` reads, saves and clears the same rows through the same
module ([trained models](MCP.md#historical-upstream-reference)). `get`
returns the owner's `context_id` with each lane's revision and default; `set` and
`clear` pass that context and revision back, like **Save** and **Clear**, and go
through the same write path (`trained_defaults.store` and `clear`). A default set
through MCP names a base and its picks explicitly. A LoRA named without a strength
is stored at the strength the picker starts it at on that base, as choosing it and
**Save** would (`trained_models.started`); it is checked with the same
`resolve` that applies a default to a form, so a choice that cannot run in that
form now is refused, and only `stack` and `composition` defaults are written. An
unreadable row is reported in `error` and left unchanged.

### Upgrade from schema 9

Opening a schema 2 to 9 store upgrades it in one immediate transaction, like
earlier migrations. Every job row is decoded and rewritten with
`source: asset` and `projection: None`; identities, revisions, receipts, quotes,
local application claims, mesh bindings and Film bindings are otherwise unchanged.
Schema 9 Film upload associations are rechecked, including their scope key, task
identity and the shared Film task namespace, and preserved byte for byte. The empty
`trained_defaults` table is created last. Nothing is inferred from filenames,
media types or remote calls. One invalid row, a conflicting association or a failed
commit rolls back the whole upgrade and leaves the schema 9 file unchanged. A
concurrent opener waits on the same write lock and then sees the current schema,
so the upgrade runs once. A schema 9 reader refuses the upgraded store as an
unsupported format without changing it.

The unit and installed-ZIP tests upgrade a store rebuilt from
[the SQL dump of one written by the schema 9 code](../tests/fixtures/README.md#schema-9-job-store),
containing every job state except the transient `downloading` and `applying`,
every operation, local claim, mesh and Film binding, a cloud record and a second
credential scope. The
[package update check](development/validation.md#scenario-package-state-across-updates)
exercises the same upgrade through Blender's native extension update, then opens
the upgraded store with the predecessor package's own storage code, which must
refuse it without changing any byte.

## Schema 11: WAV originals

Scenario re-encodes every non-MP3 audio output to a 128 kb/s MP3 asset file and
keeps the model's file as the asset's original. Saving that MP3 lost quality and
added encoder padding: a 2.5 s WAV cut came back about 44 ms longer. Schema 11
lets an audio result record `source: original` with media type `audio/wav` or
`audio/x-wav`, so the extension saves the WAV instead. See
[declared originals](RESULT_TRANSFERS.md#declared-hdr-originals-and-360-projection)
for how the file is chosen and downloaded.

No field changes; only the values a result may hold widen. A schema 10 reader would
reject such a row as damaged data, so the version changes and a 0.10.x build
refuses the whole store as an unsupported format instead. Results saved before the
upgrade keep the MP3 they recorded.

Opening a schema 10 store upgrades it in the same immediate transaction as earlier
schemas. Every job row is decoded and rewritten; schema 10 rows already have the
current shape, so their bytes do not change. Film upload associations and lane
defaults are rechecked, including their scope keys, and preserved byte for byte.
One invalid row or a failed commit leaves the schema 10 file unchanged. The unit
tests build a schema 10 store from the upgraded schema 9 fixture plus a saved lane
default, and check the upgrade, its rollback and refusal by a schema 10 reader. The
[package update check](development/validation.md#scenario-package-state-across-updates)
opens the upgraded store with the predecessor package's own storage code.
