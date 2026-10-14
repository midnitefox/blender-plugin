# Workflows, Studio and Library

Integration status of workflow commands and controls, the explicit Studio view and the asset Library. The [runtime map](runtime.md) lists the entry points and shared components.

## Active workflow commands

Local MCP `list_workflows` and `workflow_schema` queue SDK metadata reads on the
selected JobSession. Exact-task ownership distinguishes metadata from saved job
completions, and delivery still checks the originating scene and connection.
`estimate_workflow` and `run_workflow` reuse ModelJobs' bounded quote ownership,
durable submission and polling; the issued estimate determines the SDK operation.
An explicit operation tag prevents a model approval from authorizing a workflow
or vice versa. Failed workflow quotes release their retained approval capacity.

The original parameters, normalized payload/defaults and exact decimal price are
separate fields. Both commands also return `loop_steps`, `quote_may_understate`
and `cost_warning`; `job_status` repeats them for saved workflow jobs, with
unknown coverage after a restart because the record keeps no loop count. The
[adapter](../../scenario/core/api/sdk_adapter.py) counts loop nodes in the same
retrieved definition the quote validated and reports 0 only when every node has
a known non-loop type. A missing or unconfirmed empty flow, a nested workflow or
an unknown node type makes coverage unknown, which also warns. A unit test fails
when an SDK upgrade changes the known type list. The server's dry run prices one
loop pass, so a run that iterates more often can cost more. The warning never blocks
approval of the exact quoted string. Approval consumes the handle before
persistence and requires unchanged input, scene and connection. Results stay
saved for explicit application; there is no workflow-specific worker pool, store
or automatic import. Native workflow controls below share those commands.
The same polling projects waiting approval and selection steps, and
[`ModelJobs.decide`](../BLENDER_JOB_CONTEXT.md#waiting-workflow-steps) sends one
reviewed decision through the shared session; rejecting a waiting step is the
only workflow cancellation. Local MCP `workflow_job_steps` and
`decide_workflow_step` read the steps again and send one reviewed decision
through the same entry, as does the native review dialog in Jobs and the Studio
Workflows page. Cancelling a running step has no documented endpoint; the
remaining workflow desktop and live checks are in #440.


## Explicit expanded native view

[studio.py](../../scenario/blender/studio.py) registers an explicitly invoked
native popup and unsaved WindowManager navigation. The header entry point is
[popover.py](../../scenario/blender/popover.py); no lifecycle hook invokes it.
It reuses existing panel drawing and operators for Create, Film, Jobs, Results
and Connection, retaining scene-owned forms and application-owned sessions.
Closing the popup owns no worker teardown or job cancellation.

Composer synchronization retains its source scene. Opening Studio flushes a
focused prompt only into that unchanged scene/lane/form; navigation itself does
not change scene data or invalidate a quote. The composer commits on blur and
passes the same outside click to native controls. UI scales 1.25 and 1.5, the
IME candidate window and the remaining workflow and Library desktop checks are
in #440.

## Native workflow controls

[workflow_controls.py](../../scenario/blender/workflow_controls.py) owns only
scene form data and session-bound UI projections. Explicit catalog/detail actions
use `JobSession.workflow_metadata`; the application maintenance pump drains them
on the main thread. Changed scene/form snapshots reject late input-load and price
delivery. Catalog lists can instead use `deliver_workflow_catalog`, which consumes
only an issued listing from the active session and grants no scene/form authority.
Unrelated form edits therefore do not reject native catalog refreshes. The form
persists its raw schema and reconstructs dynamic choices after reopening without
network access or mutation during drawing.

Pricing and confirmation use the existing `ModelJobs.quote_workflow` and
`submit_workflow` commands. Native and MCP entry points share the same quote
registry, exact-price check, operation tag, durable submission and results.
The confirmation shows normalized payload values, including service defaults.
A quote with loop nodes, or with unknown loop coverage, labels Generate as
**Generate (from N CU)** and shows the one-pass warning below it and in the
confirmation. The session result row then shows **from N CU** as well. The exact
quoted string remains the approval value.
Credential/project retirement discards the UI controller with the shared owner;
file-load retirement removes approval handles while saved scene inputs survive.
Closing Studio owns no cancellation or teardown. No new transport, store or
worker pool is introduced. File inputs can receive [explicit uploads](#workflow-input-uploads).
[workflow_steps.py](../../scenario/blender/workflow_steps.py) draws waiting
approval and selection steps from the shared saved-job descriptors and the
in-memory step projection, and its review dialog sends one decision through
`runtime.decide_workflow_step`. Its **Load previews** control fetches private
stills of a step's review assets through `runtime.load_review_previews` and the
shared preview lane; [review_previews.py](../../scenario/blender/review_previews.py)
decodes them into icons in the GUI pump with the
[temporary preview decoder](blender.md#temporary-preview-decoding), outside the
open file. Running-step cancellation and physical/live acceptance remain
separate.

The 32-entry UI projection cache reclaims idle entries under pressure, discarding
any unused price but preserving saved scene inputs and independently owned jobs.
Pending reads/quotes are never evicted. Required-field inclusion uses the same
parsed `required_always` rules as model forms.

## Shared asset library reads

`JobSession.asset_library` admits one list/search request to the existing worker
pool and records exact-task metadata ownership separately from saved-job results.
The coordinator uses the selected SDK adapter and checks admission around the
read. Main-thread delivery consumes the issued completion and checks its scene
and session; retirement cannot route metadata into a replacement connection.

Local MCP `list_assets` and `search_assets` expose bounded pages with explicit
continuation. Their projection omits signed download URLs, indexed previews and
account identifiers. Reads do not initialize ModelJobs, persist generation jobs
or grant download/application authority. Native Library controls use these same
reads. Collection and tag changes use the separate organization reviews below.

## Shared asset organization reviews

[organization.py](../../scenario/core/jobs/organization.py) holds the pure,
bpy-free contract: validated requests, fresh snapshots, guarded writes that are
never resent after an uncertain outcome, read-back classification and the
review state machine. The coordinator and
existing workers run it through the selected SDK adapter, and
[asset_organization.py](../../scenario/blender/asset_organization.py) gives the
native Library and local MCP one session-owned entry point. The runtime
maintenance pump polls it, so progress does not depend on an open view.
Reviews are connection-scoped and session-local: undo and scene switches keep
them, while file load and credential or project changes retire them with the
session. No job, upload or store schema change is involved, and no credits are
spent. See [the command contract](../JOB_COORDINATOR.md#asset-organization-commands)
and [session ownership](../BLENDER_JOB_CONTEXT.md#asset-organization-reviews).
Local MCP tools prepare, apply and inspect these reviews; see
[asset organization](../MCP.md#historical-upstream-reference). The native Library uses the
same owner through the projection described below. Live evidence is in the
[release record](../maintenance/release-acceptance.md#library-organization);
Apply from the desktop dialog is in
[#440](https://github.com/scenario-labs/blender-plugin/issues/440).

## Native Library projection and attachment

[library_view.py](../../scenario/blender/library_view.py) retains one bounded page
and at most 128 continuation positions, with explicit refresh/search/navigation.
Its filters are unsaved WindowManager properties. The runtime maintenance pump
uses `deliver_asset_library` to consume only owned library metadata from the active
session without granting scene application authority. This permits unrelated
scene edits during a read; MCP retains its existing scene-bound delivery contract.
Both surfaces use the same URL-free metadata projection in `core/api/library.py`.

Reference confirmations capture the original scene, model, lane, input and complete
reference destination. Weakly held approval identity and fresh destination checks
prevent forged/repeated/stale application. Display names are captured at review
time; drawing does not dereference a removed scene. Attachment adds only a matching,
unoccupied model reference, preserves existing slots, records the selected scope,
and invalidates the old price. It does not start a transfer or paid submission.
The existing persisted reference-scope guard applies after reopening as well.
No new SDK transport, worker pool or job store is added.

### Native Library organization

The same view holds explicitly loaded collection pages (50 per read, at most 200,
repeated cursors rejected) and one review card. Collection reads and reviews go
through `session.asset_organization`, the owner local MCP uses; the view adds no
worker, transport or persisted property. Its operators prepare, apply and discard
reviews; drawing only renders the cached review projection and row summaries built
by [library_organization.py](../../scenario/core/ui/library_organization.py), a
pure core module. The maintenance pump polls the shared owner before the Library
view, so a finished review reaches the card on the same tick. A finished result
updates loaded rows once from the read-back records: tags and memberships come
from Scenario, and a row that left the browsed collection is marked for refresh
rather than removed. A verified new collection joins the loaded list, even when
200 are already loaded. A page or collection read still running when the
read-back arrives may list the state before the write, so its result takes the
read-back once more. The owner applies one review at a time; a ready card says
when an agent's change holds it, and Apply then refuses with that reason.
Retirement on a credential, Project ID or file change drops the view with the
session and its reviews. See
[the UI contract](../ui/studio-and-library.md#library-organization); physical desktop and live
acceptance remain pending.

## Workflow Library reference bindings

[workflow_references.py](../../scenario/blender/workflow_references.py) prepares
choices for the loaded workflow form. The Library owner holds their single-use
identities; confirmation checks the captured scene origin, full form signature,
selected scope and proposed value before mutation. Model and workflow targets
share catalog reads without introducing another service client or worker owner.

`core.schema.forms.append_file_reference` validates one proposed file edit,
including kind, duplicate IDs, capacity, types and allowed values. It defers array
minimums and unrelated required inputs while the artist is filling the form;
existing complete validation remains authoritative before a quote/submission.
A Library-managed input persists its scope digest and canonical value. The native
parameter builder rejects changed or cross-connection marked inputs; disabling
an input omits it while preserving its binding. The form signature includes that
binding, so attachment/clearing invalidates a prior approval even without a native
RNA edit event. Explicit clearing verifies the original form and retains the
existing unchecked-input/default semantics. Library choices skip an input with a
pending upload marker. General cancellation and physical/live acceptance remain
separate.

## Workflow input uploads

[workflow_uploads.py](../../scenario/blender/workflow_uploads.py) connects loaded
workflow file inputs to the existing [`ReferenceUploads`](../SDK_UPLOADS.md#workflow-input-attachment)
owner. An explicit confirmation precedes a single local-file or capture upload on
the selected JobSession; no new SDK operation, transport, store or worker pool is
added. Review refuses a missing or unreadable file, and starting requires the
reviewed connection and scope. The input carries a persisted marker before origin
capture, so pricing and duplicate uploads are refused while it is present. The
operator records its own undo step, so undoing later edits keeps the marker. The
form signature includes the marker only when present, which invalidates prior
approvals without changing the saved signature of unmarked forms.

The application maintenance pump delivers on the main thread. It attaches only
the imported asset into the unchanged scene, workflow, schema, kind, marker and
input value, after a fresh origin check, through `workflow_references.bind_asset`.
That shared binding writes the same scope digest and canonical value as Library
attachment, so existing pricing checks reject edited or cross-connection values.
Stale, failed or retired bindings, including one whose upload **Upload again**
abandoned, keep the marker and an inspectable error; saved uploads, including that
restart's new upload, attach only through a separate single-use confirmation in
the shared inspection view. For a snapshot, any scene edit, frame change, render or undo
makes JobSession refuse the upload's next initialization, part or completion admission,
and the ticket stops. A chosen file's upload binds only to its scene, so it continues
through edits, frame changes and renders and stops only after undo, redo, removal of
that scene or a file load.
The exception to keeping the marker is a stopped ticket that `ReferenceUploads`
reports as never having reached Scenario (failed staging, a refused admission
while still prepared, or cancellation): the marker is removed and existing values
stay. Explicit cancellation of a prepared record frees inputs still marked for it
through a marker-to-ticket ledger that survives undo, or through the request
recorded at first observation. Without a running delivery, **Stop waiting**
releases the marker without changing values. Undo/redo, file load and connection
retirement discard in-memory bindings; undoing the upload step itself also
removes the marker.
Selected-mesh uploads gain their captured source binding at quote time. Local MCP
keeps its existing `upload_reference`, `capture_reference` and `estimate_workflow`
sequence; no MCP protocol change is needed. The remaining upload checks are in
#440.
