# Studio, workflows and Library

Interaction rules and desktop evidence for the explicit Studio view, workflows and the native Library. The shared rules are in the [UI style guide](../UI_STYLE.md).

## Explicit Studio view

The viewport header has a separate **Studio** button. It opens a native popup
with Create, Film, Workflows, Library, Jobs, Results and Connection pages.
Opening, closing or switching pages does not start a job or select another
account/project. No startup or file-load handler opens it. The existing **Scenario** button still
opens the sidebar; the compact composer remains the default creation surface.

Studio reuses the actual native panel controls and their operators. Create keeps
the same scene form, model, prompt, references and parameters. Film separates
Tasks, Shots, Capture, Timeline, Composition and Review, whose page also draws
the video Export and Case study panels; each keeps its existing confirmation
boundary. Results
reuse the saved-result and explicit application controls. Connection names the
selected credential source/project and links to Preferences and local agent setup
without displaying credentials.

Navigation is an unsaved WindowManager property group. Native navigation edits
must not tag a scene or invalidate its exact quote. Before opening, focused
composer text is committed only if its original scene, lane and previously
synchronized prompt still match. A conflicting form rejects opening and retains
both texts. Opening Studio never commits pending text into another scene with
an identical old prompt.

The requested popup width is bounded by the invoking area, window and UI scale;
narrow requests use rows of at most three equal navigation segments. Clicking
outside a focused composer commits its text and passes that same click to Blender,
so the Studio header button opens on the first click. Installed tests cover this handoff,
registration, shared drawing, quote preservation, pending-text ownership and
continued admitted work.

An isolated offline desktop check on macOS 27.0.1 arm64, Blender 5.1.2, used exact
ZIP SHA-256 `32d81f8cbbc59023dcac10d71c1e87fc70a04265c8791e373639cef53c97c253`.
Native paste entered `Café 雪 Studio test` in the compact composer. One header
click opened Studio and preserved that text; Film navigation and Escape dismissal
worked. Camera selection, wheel zoom and the front-view shortcut worked afterward.
The default-size captures show readable two-row navigation without overlap.
The test app used a distinct bundle identity and development signature; the
installed extension and ZIP stayed unchanged. The process exited cleanly with
no Python network violations and the normal profile unchanged.

![Focused Unicode prompt in the compact composer before opening Studio](../images/studio-composer-focused.png)
![Studio opens on the first click and retains the composer prompt](../images/studio-prompt-handoff.png)
![Viewport selection and front view after dismissing Studio](../images/studio-viewport-return.png)

A second offline fixture on that same ZIP populated the actual shared model form
with a prompt, reference and enough settings to require scrolling. The SDK used
an in-memory transport; a running synthetic job was seeded in the local saved
store and resumed without invoking submission. Native navigation through Create,
Film, Jobs, Results and Connection retained the quote identity and exact cost
`0.1234567890123456789`, model, prompt and reference. The same job owner continued
polling while the popup opened, changed pages and closed. No submission occurred.

At Blender window dimensions 1512 × 917 and 1018 × 671, with UI scale 2.0,
the populated popup remained readable and scrollable without horizontal clipping.
Native Home/Shift-End selection, Unicode paste, Enter and Escape changed the
Studio prompt to `Café 雪 Studio edited`; the compact composer showed the same
text afterward, and the shared runtime produced a fresh ready quote. The repeat
exited cleanly with no Python network violations and the normal profile unchanged.
An earlier selection-shortcut attempt exited Blender and hit a test-fixture
credential cleanup error during shutdown; that run is not clean-exit evidence.

![Studio Jobs shows a synthetic running job during native navigation](../images/studio-active-job.png)
![Populated Studio form remains readable in a narrower Blender window](../images/studio-narrow-form.png)

These checks supersede the window-attachment and populated-form evidence gaps.
Alternate-DPI interaction and IME composition remain unverified: Preferences
opened, but the attempted scale edit did not change the observed UI scale, and
Unicode paste is not IME composition. UI scales 1.25 and 1.5 and the IME
candidate window continue in #440 as acceptance follow-ups. The paid desktop pass of 2026-10-13 later ran a
capture, quote, generation, inspection and application journey (see the
[release evidence](../maintenance/release-acceptance.md#3d-generation-and-imports));
other-platform desktop interaction continues in #440.

Blur and Studio opening share the same focused-prompt ownership guard.
A conflicting scene, lane or sidebar prompt keeps both texts and focus intact;
the outside click is consumed with a warning before any native handoff. With no
focused prompt, blur writes nothing. Valid edits still commit and pass the same
click to Blender. Installed modal-path regressions reproduce all four overwrite
cases on the preceding ZIP and pass on fixed ZIP SHA-256
`15f730160c62c2e7a700b25ac6c8a7a1d134122c30ef3e5c85842912c463777b`.
That ZIP passes 1,088 installed tests on each macOS arm64 Blender 5.0.1, 5.1.2 and
5.2.1 (two Windows-only skips per run). A new isolated desktop attempt displayed
the correct fixture, but control-tool clicks did not visibly activate the composer
or Studio. It supplies no new physical-input acceptance; the screenshots and
interaction results above remain evidence for their explicitly identified ZIP.

The main-thread pump refreshes the live temporary popup region with
`tag_refresh_ui()`: normal area redraw alone can leave a completed asynchronous
quote showing as loading. Popup ownership is weak and removed regions are
discarded. The [desktop regression checks](#studio-and-library-desktop-regression-checks)
record the earlier scoped input evidence for this behavior.

This view exposes existing creation/Film/result controls and the workflow form
below, plus Library browsing, model-reference reuse, reviewed
[collection and tag organization](#library-organization) and the review of
[waiting workflow steps](#waiting-workflow-steps). Complete retained Studio
acceptance remains separate.

### Workflow controls

Studio's Workflows page has explicit private/public refresh actions, a local
name search and a Workflow ID fallback. Loading inputs asks for confirmation
before replacing the form. A saved per-scene schema drives labeled text, numeric,
boolean, fixed-choice and structured fields. Structured values remain editable
as JSON; file fields accept asset IDs, Library selections and the
[explicit uploads](#workflow-input-uploads) below. A field's
inclusion checkbox omits it from the request, allowing declared defaults.

**Request workflow price** starts a shared session quote. **Generate** opens a
separate confirmation with normalized inputs, scene and the full exact CU price.
Each input value is named by the label the form draws for it, so an input
without a schema label keeps its readable parameter name, as in the form.
Cancel submits nothing; confirmation rechecks the form, scene, selected session
and original quote. When the workflow definition has loop nodes, or its loop
coverage cannot be checked (for example, a node type the extension does not
know), the button reads **Generate (from N CU)** and a warning states
that the price covers one loop pass and the final charge can be higher. The
confirmation repeats it, and the submitted job's session result row shows
**from N CU**. The warning wraps by the width Blender's interface font measures
(`label_wrap`); when it needs more than one line, each sentence starts its own
line. The confirmation declares its requested popup width, so its first draw
wraps like Studio's. Installed tests draw the confirmation as that first draw,
at UI scale 1.0 and 2.0, and Studio's page 960 and 240 units wide; a physical
recheck on 2026-10-13 found the desktop look correct. A recovered workflow job shows
**from N CU** too, because the saved record keeps no loop count. The warning
does not disable approval. Drawing is read-only and never starts catalog or job workers.
Page search/navigation use unsaved WindowManager state; scene form edits require
a new price. The maintenance pump completes metadata and quote requests even
after the popup closes. Saved jobs use the existing Jobs and Results controls.

A workflow job waiting at an approval or selection step is reviewed and decided
as described in [waiting workflow steps](#waiting-workflow-steps). The Generate
confirmation states that these steps wait for review in Jobs and that a running
step cannot be canceled, only stopped at a waiting step.

Always-required fields use the shared parsed requirement rules, including
`required: {always: true}`, and start included. Catalog lists have session-only
delivery authority: editing a form while listing does not discard the catalog
or permit it to overwrite the form. Input loads and prices keep their original
scene/form checks. At the 32-view cache limit, opening another scene reclaims an
idle projection and its unused price; saved inputs and admitted jobs remain.
Pending requests are never evicted. Returning to an evicted price requires a
fresh estimate.

Installed tests verify synthetic native registration, shared UI/MCP quote use,
changed-input rejection, view changes, continued work and saved-form reload.
The desktop regression checks below record prompt editing, exact price refresh,
cancel/confirm and saved-job visibility with a synthetic service on their named
candidate. Open DPI, IME and platform checks are in #440.
Live outputs remain outside this implementation.

### Waiting workflow steps

A workflow job waiting at an approval or selection step shows the status row
**Workflow waits for your decision** in its Jobs row, followed by one review
button per waiting step: **Review approval**, **Choose 1 to 2 of 3** (or
**Choose 1 of 3** when the bounds are equal) for a selection, or **Review blocked
selection** when only stopping is offered. A step inside a loop adds its loop
index. A step whose node ID cannot be sent shows **This step cannot be decided
here** without a button. Studio's Workflows page lists the same buttons above
its form under **Waiting for your decision**, with the workflow name and job,
but without the recovery controls. Every surface draws the shared saved-job
descriptors from the in-memory step projection; drawing performs no I/O, starts
no request and changes no property. Offline, the review buttons are disabled.

**Review** opens a dialog that captures the step when it opens. It shows the
workflow name (from the loaded catalog or form, else its ID), the Scenario job ID,
the job's exact approved price, the step and its ID, any last decision error,
and either the result asset IDs an approval reviews or a selection's candidates
in listed order. Asset candidates are checkboxes named by asset ID; text
candidates are numbered checkboxes followed by the text, wrapped, up to 12 lines
each. A line counts the selected items against the bounds. The decision is one
[segmented choice](../UI_STYLE.md#tabs-and-segmented-choices) of two equal cells, **Approve**
(or **Select**) and **Stop workflow**. Stop is never the default, so rejecting
always needs that explicit choice; a blocked selection disables Select and
explains why, including a selection whose step links could not be read in a
workflow with a loop. When an approval's step links could not all be read, the
dialog warns above its result list that results to review may be missing and
should be checked in Scenario. The dialog states the consequence of
the chosen decision: continuing runs the remaining steps, which spend credits,
and their charge is not verified against the approved price; stopping rejects
the step and cancels the workflow, completed steps stay charged, and inside a
loop Scenario may stop only that loop, so the refreshed status is authoritative.

**Send decision** is single use. It sends the choice through the same
`runtime.decide_workflow_step` entry as local MCP `decide_workflow_step`, which
checks the context, saved revision, step review, decision and selection bounds
again and queues one request that is never retried. Selected items are sent in
listed order. A changed step, revision or connection, a command in flight or
disabled Online Access sends nothing and reports why. Opening the dialog sends
nothing either.

While the decision is sent, the row shows **Sending your decision...**. A
refused, uncertain or unsent decision then stays as wrapped error rows until
another decision, a newly waiting step or the job leaving `remote`; the dialog
shows it too. A running workflow job with no waiting step shows, without a
button, that Scenario cannot cancel a running workflow step and that it finishes
or pauses at its next approval or selection. The pump redraws Jobs rows when a
step starts or stops waiting, a decision is queued or finishes, or its error
changes.

Below its review button, each waiting step that lists assets (an approval's
result assets, or an asset selection's candidates) shows **Load previews (N)**
in its Jobs row and in Studio's Workflows section. It is the only control that
fetches them: an explicit, read-only request through `runtime.load_review_previews`,
bound to the context, saved revision and the step's `review_id`, and disabled
offline. It never decides, spends or applies. The
[preview lane and cache](../RESULT_PREVIEWS.md#workflow-review-assets) fetch each
asset's still. Once requested, the step shows up to eight loaded icons, four to a
row, and **Previews: R of N ready** with any loading, waiting for Online Access
or unavailable count; **Retry previews** appears while some are unavailable and
sends only those through the preview lane again, which fetches a still again when
it failed, is missing or lost its cached file. The review dialog shows an approval's loaded previews
as a grid of icons named by asset ID, and a candidate's loaded preview beside
its checkbox; an asset without one keeps its ID and a short status line, and a
step with nothing loaded says to load previews from the Jobs row or Studio
Workflows. While its previews load, the dialog is refreshed by the pump like
Studio. Icons are decoded by the pump from the verified cached still, at most
two per tick and at most 256 pixels per side, never while drawing or while a
render job runs; drawing reads memory only. Decoding uses temporary Blender
data, so the open file gains no image and origins captured in it stay current.
A cached still lost before decoding is fetched again once by itself. A step that is decided or changes
releases its icons, and so does a connection change.

Installed tests on macOS arm64 Blender 5.1.2 cover the descriptors drawn in the
sidebar, Studio Jobs and Studio Workflows with storage reads refused, the dialog
contents, single use, bounds, listed order, the explicit stop, a blocked
selection (also one blocked because its links could not be read), the approval
warning for unread links and the results a workflow definition links, changed
step, revision and connection, offline refusal, a refused
decision, steps after an explicit refresh following a restart, redraws and
registration, with synthetic SDK responses. Installed tests of the previews
cover the Load previews controls and their binding, metadata and transfers on
the preview lane, decoding only in the pump (a fitted icon with the stored
colors, no image added to the open file, no dependency update and a captured
origin still current), icons drawn in rows, Studio and the dialog with storage
reads and requests refused, unavailable candidates and their explicit retry, a
candidate asset listed twice fetched once, a lost cached still fetched again
once by itself and then by Retry previews, a still Blender could not decode
checked and decoded again by Retry previews, an icon Blender could not fill
released, stale, foreign and offline requests, released icons after a decision or
connection change, and cached stills reused after a restart.
Background Blender assigns no icon IDs, so drawn icons were recorded with a
stand-in ID. Physical desktop evidence of the rows, previews and dialog (input,
focus, DPI, IME and screenshots) is pending.

## Native Library controls

Studio's explicit **Library** page has Search, Public assets and Collection ID
filters, Refresh, and separate Previous/Next actions. Filter edits do not start a read or tag
the scene; changed filters require Refresh before page continuation. Reads use
the shared session and application pump, so unrelated scene edits and closing the
view do not discard the requested page. Connection retirement clears the view.

Each asset shows its name and MIME type. **Use as reference** opens a separate
confirmation for the selected scene, generation form, model and compatible input.
No existing reference is silently replaced. Unknown MIME types cannot attach;
known file inputs respect their kind and capacity, and a 360 panorama input is
not offered. Confirmation checks the
unchanged destination, adds a scoped asset reference and invalidates its price.
The dialog displays a captured scene name, so deleting the scene while it is
open cannot break redraw; confirmation still rejects the unavailable destination.
Drawing never starts requests or mutates RNA. Collection and tag changes use the
separate reviewed [Organize](#library-organization) dialog. Dynamic input choices are retained by the live dialog and resolved through
its RNA properties; Blender passes `OperatorProperties` to the enum callback,
not the Python operator carrying the prepared approvals. Installed tests exercise
that registered callback as well as state and command boundaries. The desktop
checks below record native browsing and reference confirmation for their named
candidate. Open Library desktop checks are in #440.

### Library organization

**Load collections** reads one page of 50 collections in the selected connection,
and **More** appends the next page, up to 200 loaded collections. Each loaded
collection shows its name, asset count and **Browse**, which sets Collection ID,
clears Search and Public assets and reads the first page. Each asset row adds a
tags line and a membership line. Labels that do not fit end with a "+N" count;
memberships name loaded collections and count the others. These are explicit
operator reads held by the Library view; drawing never starts one.

A read never disables the control that started it: Refresh, Previous, Next,
**Load collections**, **More** and **Browse** stay enabled while their read runs,
a loading line shows it and a second click is refused with a message. Previous,
Next and More still follow page availability. Blender 5.0 and 5.1 keep a hovered
button's disabled look across popup refreshes until the pointer leaves it (5.2
refreshes it), so a control disabled by its own read still looked unavailable
after the read finished, although the pump had refreshed the popup.

**Organize** on an asset row opens a dialog (width 480) showing the captured
asset name and the selected project label, as on the Connection page. Choose
Add to collection, Remove from collection, Add tags, Remove tags or New collection
and add. The collection dropdown lists the loaded collections, plus the browsed
Collection ID, and preselects the browsed one. Before collections are loaded,
Add and Remove ask to load them first. Once a load finds none, the dropdown reads
**No collections yet** and the dialog says the connection has no collections:
Add suggests New collection and add, Remove says there is nothing to remove. Its
choices are retained by the live dialog and resolved through its RNA properties,
like the reference input menu. Tags are one comma-separated field; surrounding
spaces are removed and text is otherwise kept exactly, so tags cannot contain
commas. The dialog states that changes are immediate account metadata, use no
credits and are not reversed by Blender Undo, and explains invalid text before OK. OK only prepares a review:
the shared session owner reads the asset's current tags and memberships, and
nothing is sent. Cancel prepares nothing.

The Library page then shows one review card under the organized asset's row.
The rows above it keep their place, so the card opens next to the Organize
button that was chosen, even far down a scrolled page; Blender's popup scrolling
cannot be moved from Python. A line under Previous and Next names the asset and
points down to the card, for example after Studio is reopened at the top. On a
page without that asset, such as another page or a browsed collection, the card
is drawn in that line's place instead. The status bar confirmation is often hidden behind
Studio, so it only repeats where the card is. While preparing it says nothing has
been sent. A ready card lists the asset's current and resulting state, the number
of requests Apply sends and the 10-minute expiry, with **Apply** and **Discard**.
Apply is accepted only for that ready card, once; it sends each write once with
no retry, then the card shows the read-back result: Verified, Refused with the
HTTP status, Unconfirmed, Not sent or Not verified. When another client added the
asset first, the service refuses the add as a whole; the read-back shows the
asset as a member, so nothing more is sent and the card shows Verified with a
note that it was already in the collection. An unconfirmed card says to
refresh and inspect; nothing is resent automatically. A finished, refused,
unchanged, unsent or expired card offers **Dismiss**. Rows take the tags and memberships Scenario
read back. A row that left the browsed collection stays marked "No longer in this
collection; refresh" until the next Refresh rather than disappearing. A verified
new collection joins the loaded collections, even when 200 are already loaded,
so **Browse** and the dropdown offer it. A page or collection read that was
already running when the read-back arrived may have listed the state before the
write, so its rows keep the read-back and the new collection stays listed; the
next read shows what Scenario lists.

Organize is disabled for pages loaded with Public assets, while a Library page is
loading and while a change applies. Preparing another review discards a card that
is not applying. The card belongs to the application's job session, not to the
popup: the maintenance pump advances it, so closing and reopening Studio during
Apply keeps it. A credential, Project ID or file change retires the session,
clears the view and discards its reviews; a write already sent keeps its effect.
Native and [local MCP](../MCP.md#historical-upstream-reference) reviews share the same owner,
so only one change applies at a time and they share its 32-review bound. While
an agent's change applies, a ready card says so; Apply stays offered but refuses
with that reason and sends nothing. When unapplied agent reviews fill the bound,
Organize says so and prepares nothing; unapplied reviews expire after 10 minutes.
A full bound evicts only settled reviews, which never change again, so a settled
card keeps its outcome until dismissed even after an agent's new review evicts
the review behind it.

Installed tests cover operator registration, the dynamic collection enum with
`OperatorProperties`, read-only drawing, public and busy pages, dialog cancel,
Apply gating and single use, row updates, an asset added elsewhere before Apply
(one request, Verified), filtered-row refresh marks, a new
collection joining a list at the load bound, the card across Studio redraws
during Apply, connection and file retirement, sanitized refusals, online access
off, a review bound filled by agent reviews, a finished card kept after an agent's
review evicts it, Apply while an agent's change applies, a page or collection read
still running when a change is verified, and narrow-width wrapping. They also
cover the card under the organized row and on top of a page without it, the
controls that start a read staying enabled while it runs with repeated clicks
refused, and the dialog text after a load finds no collections. These tests check
what is drawn; Blender's hover state needs a desktop.

A physical macOS 27 arm64, Blender 5.1.2 pass on an earlier candidate, at UI
scales 1 and 2 and in a 1018 by 671 point window, made service reads only. Dialog
focus, Unicode tags, Escape, Cancel and outside clicks, Discard and Dismiss without
requests, and viewport shortcuts afterwards worked. Japanese IME composition does
not start in the dialog's native text field, as in Blender's own fields. It found
the card out of view after OK on a lower row, Refresh and Load collections drawn
disabled after their reads until the pointer moved, and the outdated collection
hint; the placement, enabled states and text above replace that behavior and need
a new desktop check. A later read-only pass checked the card placement and
read-enabled controls, and a free live run applied and read back collection and
tag changes through local MCP and the native operators in background Blender
(see the [release evidence](../maintenance/release-acceptance.md#library-organization)).
The desktop passes made no live collection or tag write. Apply on a desktop,
Blender 5.0 and 5.2 and other platforms continue in
[#440](https://github.com/scenario-labs/blender-plugin/issues/440).

## Workflow reference selection

Library's **Reference destination** dropdown explicitly chooses Model or Workflow.
Choosing Workflow needs a loaded workflow form, not a selected generation model.
The reference dialog names the original scene, workflow and matching input before
attachment. Preserve existing file values; only empty single inputs and arrays
with capacity can receive a selection. Adding an array item may leave the form
incomplete, but Request workflow price must still enforce its full requirements.

Workflow file inputs use asset text/JSON plus Library selection; allowed-value
constraints remain active even when a file field declares enumerated IDs. Both
`file_array` and `file` with `array: true` use JSON arrays. Previously saved
file-enum forms retain their selected choice until explicitly cleared or reloaded;
changing that choice invalidates the price. Confirmed attachment to an empty
selection switches it to the Library-managed asset value. Each
Library-managed input stores its selected connection and canonical value. Edited
or cross-connection marked values require explicit clearing and selection again;
unchecked inputs retain the binding for re-enabling. **Clear reference(s)** asks
for confirmation of the original unchanged form, empties and unchecks the input,
and states that unchecked inputs use workflow defaults. A saved file retains the
binding, while price/confirmation handles retain their existing session lifetime.
No attachment, clearing or drawing action starts a generation or transfer.
Native reference selection has the scoped desktop evidence below. Installed
fixtures and that single environment do not establish full DPI/platform acceptance.


### Studio and Library desktop regression checks

The exact candidate ZIP SHA-256
`024b7654346463d89cb80e149c278254d9ac9de022447b90b1101f6d9c915351`
passed 1,132 installed tests with two Windows-only skips on each macOS arm64
Blender 5.0.1, 5.1.2 and 5.2.1. On macOS 27.0.1 / Blender 5.1.2, native desktop
clicks and keyboard input in an isolated profile reproduced and then verified
fixes for the Library enum error and Studio's stale loading display.

The same open popup now displays a completed workflow quote's full decimal
price. Editing its prompt disables the old approval; Cancel submits nothing,
and confirming a fresh quote creates one synthetic saved job. Native Library
search, Next/Previous, destination selection, reference Cancel/Confirm and
attachment to both workflow and model forms worked. Unicode prompt text survived
closing and reopening Studio. Escape restored viewport selection and keyboard
frame navigation. The popup also retained its shared form in a resized
2,214 by 1,456 pixel window, after scrolling the native viewport header to Studio.
Screenshots were inspected during these interactions.

These desktop checks use synthetic service responses with external sockets
blocked. They establish native input and popup refresh behavior, not live
workflow quality, full compact capture-to-apply acceptance, IME composition,
multiple DPI settings, other OS interaction, sustained GPU/audio behavior or
human media review. The open ones are in #440.

## Workflow input uploads

Each loaded image, audio, video or 3D workflow file input offers a **File** path
field with **Upload file**, plus kind-matched snapshots: **Viewport**, **Camera
view** and **Render result** for images, **Viewport clip** and **Camera clip** for
video, and **Selected mesh** for 3D. A separate confirmation names the file or
snapshot, input and workflow before any content is sent; uploading never
generates or approves spending. Inputs of other kinds, and file inputs that list
allowed asset IDs, keep Library selection and typed asset IDs.

Before marking the input, preflight refuses an occupied single input, a full
array, an unsupported source, a missing or unreadable file, an existing upload
marker and the shared 128-binding upload capacity. Confirmation rechecks the
reviewed form, file path, connection and scope. The saved marker invalidates the
current price and blocks **Request workflow price** until the upload attaches or
is resolved. The maintenance pump attaches only after an imported observation and
a fresh origin check, and only into the unchanged scene, workflow, schema, input
kind and input value. It writes the same scope digest and canonical value as
Library attachment, and removes the marker. Once the pump observes the saved
record for an unchanged input, the input records the upload request, even if the
upload later stops; an upload released as having sent nothing records none. A
changed destination keeps the marker and an inspectable error.

The shared session refuses a snapshot's next initialization, part or completion
request after any scene edit, frame change, render or undo in the origin scene, and
a chosen file's after undo, redo or removal of that scene, so a stopped upload
never resumes. When an upload whose delivery is still held stopped before anything
was sent (local staging failed, or the staged record was still prepared) the pump
removes only its own marker, keeps existing values and draws a read-only note on
the input that nothing was uploaded, so the content can be uploaded again. The
note stays through undo and redo while the workflow and the input's values are
unchanged. The next upload admitted into the input, a Library or saved-upload
attachment to it or **Clear reference** retires it. It is held in
memory, so a credential or project change, a blend file load or an extension
restart also clears it. An upload that already sent something keeps
the marker. **Cancel preparation** of a prepared record also frees any input still
marked for it, matched through this connection's ticket for that marker, which
survives undo, or through the recorded request after reopening or a connection
change. A blend file saved before the first observation has no request link.

**Inspect uploads** and **Use saved upload** open the shared saved-upload view
for that input. Attaching an imported upload needs a separate single-use
confirmation naming the scene, workflow, input and file; it supersedes an
in-flight upload into the same input. The upload records its own undo step with
the marker, also when starting fails after a task was admitted. Undo or redo to a
state that keeps the marker, reopening the blend file and a connection change
discard in-memory delivery, so the marker then requires this explicit review
instead of a second upload. A marked input with no running delivery offers
**Stop waiting** beside **Inspect uploads**: after a confirmation it removes the
marker and request link, keeps the current values and leaves the upload in saved
uploads. A running upload is not released this way. Undoing the upload step itself
removes the marker, so **Upload file** would send the content again. **Clear
reference** warns that an admitted upload continues in saved uploads, then removes
the marker and empties the input. Drawing stays read-only.

Installed synthetic tests cover these guards, including missing and unreadable
files, failed staging, a revoked origin after staging on an array that already holds
a value, a chosen file that still attaches through frame changes, unrelated object
moves and render-thread updates, cancellation of a waiting, undo-restored or
retired-connection marker, **Stop waiting** after an upload stopped mid-transfer
or that **Upload again** abandoned, undo around an upload and a background **Render result** upload, plus shared MCP
pricing of an uploaded asset ID and selected-mesh provenance at quote time. The
paid desktop pass at UI scale 1.0 uploaded a local image file and viewport and
camera captures into workflow inputs, each after its own confirmation (see the
[release evidence](../maintenance/release-acceptance.md#workflows)). A **Render
result** upload after F12, audio, video and 3D files and UI scale 2.0 continue
in #440; IME in these controls is not recorded.
