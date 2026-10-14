# Scene inputs and application boundaries

Integration status of Film inputs, render and mesh capture, composition preparation and saved 3D edits. The [runtime map](runtime.md) lists the entry points and shared components.
Physical desktop interaction and live provider acceptance of these components
continue in [#440](https://github.com/scenario-labs/blender-plugin/issues/440).

## Film input contracts

[Film recipe helpers](../FILM_PLAN.md) preserve data-only recipe/scene validation,
editorial timing, continuity declarations and ordered task references. Project
selection is optional; referenced outputs must match the full selected job scope.
These helpers do not submit work, create scenes or own storage.
The optional Film panel and local MCP share task preparation and approval through
`FilmJobs`.

The shared `quote_film_task` command binds model tasks to the existing scoped
job store and quote/submission queue. Recipe and transitive task digests protect
reference reuse; atomic production/task reservation prevents another attempt
through a fresh quote after restart or uncertainty. The native `JobSession`
exposes the command with existing scene guards. `FilmJobs` binds its approval
to the saved scene recipe and production identity, then uses `ModelJobs` for
submission, polling and downloads without automatic application. Explicit
upload-task association selects an already imported scoped upload and saves its
immutable source/asset identity in the same job database. Dependent quotes
recheck that upload locally; association sends no bytes and never replays
uncertain uploads. See [durable Film tasks](../FILM_PLAN.md#durable-model-tasks).

The [native Film scene primitives](../FILM_PLAN.md#native-shot-and-timeline-primitives)
construct complete new shot scenes and editable timelines from validated
recipes and explicit saved GLB receipts. They restore the current scene and roll
back only new data on failure. The [shared shot application command](../FILM_PLAN.md#shared-shot-application-command)
verifies selected credential-bound task outputs, captures the unchanged recipe
scene, claims every source job before building and supports receipt-only recovery.
The [native/MCP shot controls](../FILM_PLAN.md#native-and-mcp-shot-controls) expose
source inspection, local verification, separate build approval and recovery over
that command. The maintenance pump owns verification delivery; draw reads cached
status. `JobSession.film_timeline` supplies bounded explicit local shot
selection and single-use timeline approval through native controls and MCP.
It rechecks live scenes and captured revisions and creates a new editable
scene-strip sequence without importing results or changing saved jobs.
The [local capture foundation](../FILM_PLAN.md#local-capture-foundation) exports
an immutable blend snapshot on the main thread and renders stills or exact-range
MP4s in an owned offline Blender child. Its bpy-free worker primitive checks
optional installed media tools and cleans child profiles on success or failure.
The [shared capture command](../FILM_PLAN.md#shared-capture-and-upload-approval)
binds native/MCP approval to both recipe and shot revisions, queues one local
render through the existing workers and drains it from the maintenance pump.
Retirement/cancellation stops the child, while cleanup waits for workers to join.
Separate upload approval verifies the captured content hash during staging and
then uses the existing reference lifecycle, with no separate worker pool or job store.

## Shared MCP render preparation

Local MCP `render_form` edits and inspects the native Render Image/Video form,
explicitly prepares its scene/first-frame uploads, and removes only a freshly
identified reference. The existing upload session owns work after the tool
returns. Model changes require explicit removal of previous references; an
occupied or uncertain slot cannot start another upload implicitly.

Render `estimate_cost` and `generate` use the same native request builder,
including prompt decoration and uploaded input ordering. Callers omit raw model
parameters and configure the form first. Empty automatic looks require the
existing separate Spark quote/approval/delivery before the final render price.
No capture, upload or paid Spark call occurs during render generation. Result
application remains explicit. See the [MCP sequence](../MCP.md#historical-upstream-reference)
and [context contract](../BLENDER_JOB_CONTEXT.md#mcp-render-form-commands).

## Saved 3D edit application boundary

The [verified saved-mesh command](../MESH_APPLICATION.md#captured-source-and-verified-saved-mesh-command)
connects receipt-checked GLB import to explicit remesh/UV/retexture/parts and
[compatible rig attachment](../MESH_APPLICATION.md#attach-a-returned-rig). `JobSession.apply_recovered_mesh` binds one selected asset to a captured
source object and a durable original or local-reuse claim. It rejects changed
geometry/context, ambiguous multi-mesh results and uncertain rollback, while
known receipt retry only saves persistence. The caller supplies an explicit
Blender-imported-scene-to-source-local matrix, edit policy and Keep original choice.
Native **Apply mesh edit** and MCP `purpose: mesh_edit` prepare the same
captured target, explicit scene/local placement, policy and Keep original review.
It makes no service request and uses no second executor. The captured-source flow
below connects export metadata to this review. Provider mapping contracts remain
under #99.

## Mesh upload source identity

Mesh snapshot uploads preserve [export provenance](../SDK_UPLOADS.md#captured-mesh-export-provenance)
with their durable upload intent: exact exported bytes, source session IDs,
base-mesh fingerprints, world matrices and exporter settings. Export rechecks its
captured source before worker admission; staging rejects a mismatched file hash.
Multiple sources remain explicit without guessing a primary mesh. Upload schema 2
upgrades existing records transactionally without resetting uncertain claims.

The shared quote coordinator binds captured uploads to typed 3D inputs and
persists their source snapshots in
[generation intents](../JOB_STORAGE.md#captured-mesh-inputs-in-generation-intents).
This does not change the generation origin or authorize automatic mesh replacement.

## Captured mesh source application

Native **Apply to captured source** and MCP `purpose: mesh_source` connect
persisted quote/input provenance to an unchanged live source retained during
export. They ignore active selection and feed the existing explicit mesh review
and durable application path. Ordinary destination review stays available after
restart; saved IDs alone cannot recreate original-source ownership. See
[the mesh contract](../MESH_APPLICATION.md#applying-to-the-captured-mesh-source).
Multi-source ambiguity, rigged or modified sources and provider coordinate
contracts still limit #99 acceptance.

## Composition preparation component

The [unpaid Film composition helpers](../FILM_PLAN.md#unpaid-composition-drafts)
can prepare and revalidate final/previs task drafts from scoped saved outputs.
They preserve cut and audio timing without mutating the recipe, reserving a job
or making service requests. The existing `JobSession.prepare_film_composition` and worker pool provide
[receipt-bound media measurement](../FILM_PLAN.md#verified-media-preparation),
current-source rechecks and guarded delivery. The session also exposes
[composition quoting](../FILM_PLAN.md#quoted-composition-generation) with saved-source
checks through the existing exact-price preparation and single-dispatch claim.
[Native/MCP composition controls](../FILM_PLAN.md#native-and-mcp-composition-controls)
retain the draft and original recipe for separate price and generation
approval. The existing `FilmJobs` owner maintains reviews and uses `ModelJobs` to
save the master without changing the recipe. Declared master IDs remain visible
through local inspection after restart.
The [native review primitive](../FILM_PLAN.md#native-saved-media-review-primitive)
assembles independent receipt-bound picture/audio sequences. Its
[shared command layer](../FILM_PLAN.md#shared-native-review-preparation-and-application)
prepares copies on existing workers, then separately checks original recipe/scene
approval and durable generated-source application claims.
[Native/MCP review controls](../FILM_PLAN.md#native-and-mcp-review-controls)
present it through session-owned `JobSession.film_review` handles with separate
preparation and build approvals; runtime maintenance polls them independently of
the panel. The
[offline video export primitive](../FILM_PLAN.md#offline-film-video-export-primitive)
renders an assembled sequencer scene to a verified MP4 on a session-owned thread,
outside the shared workers, and publishes it without overwriting.
[Native/MCP export controls](../FILM_PLAN.md#native-and-mcp-video-export-controls)
present it through session-owned `JobSession.film_export` handles with a
destination choice and a separate render approval; runtime maintenance polls
them, and agent destinations stay inside the Output Folder. The
[portable case-study bundle](../FILM_PLAN.md#portable-case-study-bundle) uses the
same session-owned thread and publish step through `JobSession.film_case_study`
handles: the main thread snapshots the chosen scenes and builds the sanitized
documents, offline children rewrite and verify the library, and the bpy-free core
copies media, scans for private identifiers and archives one ZIP.
