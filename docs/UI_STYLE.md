# Scenario for Blender: UI style guide

The N-panel is drawn with Blender's `UILayout`, so it wears the user's Blender theme (colours, corner radius, fonts). We cannot change those; what we control is structure, spacing, wording and icons. This guide keeps the whole plugin coherent. The floating composer is custom gpu/blf drawing and mirrors the same language.

## Project scope in Preferences

Place **Project ID (optional)** in the Account box after the credential fields,
with **Blank uses the API key's default scope** below it. The same explicit choice
applies to saved and environment credentials. Do not populate a project from the
first discovery result. A changed selection clears prices and connection status;
new generation requires a fresh quote. Drawing reads the selection only.

Installed tests cover scoped SDK quotes, stale approvals and callback rejection,
local saved-job isolation, normalized-equivalent edits and invalid IDs. The exact
candidate ZIP `3bd0a4b03ec9afe792e9bc36ed8805f1c5df06c38dccc46d5e98bdea2d831764`
passes 1,065 installed tests on each macOS arm64 Blender 5.0.1, 5.1.2 and 5.2.1
(two Windows-only skips per run).

Native mouse/keyboard input and screenshot inspection pass on macOS 27.0.1 arm64,
Blender 5.1.2, using that unchanged ZIP from source `29de2245b22e64bde6f1713797fd40d3eeb844c9`
in an isolated offline profile. The fixture contains synthetic credentials and
one saved job per default/project scope. Native edits exercise blank → project A
→ project B → project A → blank; a store observation confirms only the
selected scope's saved job is returned. Enter and Tab commit edits, Tab moves
focus to the next field, Escape preserves the previous value, and surrounding
whitespace selects the same normalized scope. A URL entered as the project ID
fails local validation with no catalog and no silent default fallback.

Changing the selection clears the fixture's prior connection status. Switching
to Environment keeps the explicit project field and selection; clearing it
returns to the default saved job. After closing Preferences, native viewport
selection, wheel zoom and the front-view key work. Captures show the complete
field label and helper text without overlap, at the tested default scale and
window size. The installed package and archive remain byte-identical, no Python
socket connection/bind attempts occur, and the normal Blender profile is unchanged.

These synthetic checks do not establish live project permissions, other OS/DPI
interaction, paid generation or release acceptance; live project permission and
other platforms continue in #440.

![Blank Project ID restores the API key default scope](images/project-scope-default.png)
![Saved Blender credentials with an explicit project A selection](images/project-scope-project-a.png)
![Environment credentials retain the explicit optional project B selection](images/project-scope-environment.png)
![Viewport selection and front view remain usable after editing Preferences](images/project-scope-viewport.png)

## Tabs and segmented choices

A row of mutually exclusive choices (lane tabs, picker modality tabs and category chips, Edit 3D tasks, Environment modes) is a **continuous segmented control**: cells touch (no gaps), each takes an equal share of the full width, and the icon sits next to its label with the pair centred in the cell.

- Use `panels.equal_segments(row, struct, prop, values)` (or `panels.draw_enum_tabs(layout, struct, prop, rows)` for multiple rows). It draws each value with `prop_enum` inside an `align=True` row (Blender merges the borders) and forces equal widths with a chain of even `split()`s.
- Never use `prop(..., expand=True)` for these (it pins the icon to the left edge) or `grid_flow` (it leaves gaps).

## Sections

Each group of controls is a `layout.box()` with a one-line header: `box.label(text="Title", icon=...)`. Order inside a lane: Model, Prompt, References, Settings, then the Generate button. Put `layout.separator(factor=0.5)` between the tabs and the form, and above the Generate button.

Every section header carries an icon so the panel reads as a stack of peers: `Model` (`NODE_MATERIAL`), `Clip to render` (`RENDER_ANIMATION`), `Camera path` (`CAMERA_DATA`), `Rendering Style` (`BRUSH_DATA`), `Settings` (`PREFERENCES`). Sections do not collapse (no chevron): a header is a static label, not a toggle, so the form is always visible.

## Buttons

- The **Generate** button is the one primary action: `row.scale_y = 1.5`, icon `PLAY`, and it always shows the live price (`Generate (15 CU)` / `Generate (from N CU)` / `Generate (estimating...)`).
- After Generate, an unchanged form keeps showing its price, and Generate stays disabled until Scenario has confirmed the job and `generation.REPEAT_DELAY` (2 s) has passed. Meanwhile it fetches a fresh free quote, because each send consumes its own; a different price replaces the old one before Generate re-enables. Any edit prices the form normally instead.
- Icon-only buttons keep Blender's fixed size; a button that must fill a row carries a short label (Blender never stretches an icon-only button). The three prompt tools are `New`, `Rewrite`, `Translate`.
- Destructive actions (`Delete`, `Clear path`) ask for confirmation (`invoke_confirm`).

When Blender online access is disabled, the composer shows **Offline** on its
disabled generation button and **Online access disabled** instead of a loading
message when no model is available. Pending edits retain their estimate request
until online access and credentials are available. Only the selected scene is
priced: another scene's pending form shows no price error and is priced once
that scene is selected. A price that arrives while another scene is selected is
requested again in the same way. The generation controls
remain disabled until a ready quote handle exists; the submission path still
rechecks its exact inputs, origin and approval. A model list is only described as
loading while a catalog request is active.

The composer's prompt, model chip, price, status line and **Generate** belong to
the form that Generate submits. With the 3D tab in Edit mode, that form is Edit 3D:
the 3D tab stays highlighted and **Settings** opens the 3D dialog with its mode
switch, while the prompt, model, price and enablement come from the Edit 3D quote.
A ready Text, Image or Multi-view price never enables or labels an Edit 3D
submission. The pricing pump, the sidebar and Settings forms, the composer and the
Generate operator resolve the form through one lane rule (`props.effective_lane`).
The model picker maps a chosen model to its form in the other direction, then
shows that form's tab and mode.

The composer's text belongs to the form it was synchronized from. **Settings** and
the model chip commit and blur a focused prompt before their dialogs open, because
either can switch the form. If the form is still replaced while the prompt is
focused (another window or a tool), the composer never writes into the new form.
The card keeps describing the original form, disables **Generate** and notes
*The form changed: press Esc to show it*. Typing is refused with a warning, and a
click or double-click in the field keeps the original text. Esc, Enter, Generate,
a lane tab, collapse and dragging leave focus without writing either form; Enter
or Generate then submit nothing, and the tab shows its current form. Otherwise a
lane tab commits the text and keeps the prompt focused on the new tab's form.
When the form's model takes no prompt, as for several Edit 3D tasks, the field
reads *This model takes no prompt* and takes no focus, as the sidebar hides its
prompt row: a click or double-click does not focus it, and a lane tab that shows
such a form leaves focus. If another window or a tool loads such a model while
the prompt is focused, the next text key leaves focus with that warning and
writes nothing.

Installed tests on the packaged ZIP run the real composer draw function, with
recording drawing primitives, and the real modal handler. Run against the previous
composer, they fail where a 1.5 CU Text price labeled the Generate that the
operator routes to the Edit form's 7.25 CU quote, and where keys typed after a
mode switch still edited the Text prompt. They also cover the replaced-form and
no-prompt rules above. The lane tab, double-click and model-change rules came
after the GUI check below; their installed tests fail on the composer code that
check ran. An offline GUI check ran the exact ZIP
`89c07a5b0642ca02b53d5c1fcb57968f03fde32b3ae942ac7f048690f9272011`
on macOS 27.0.1 arm64 with Blender 5.0.1, 5.1.2 and 5.2.1. It drove the composer through
Blender's own event queue (`--enable-event-simulate`, a window opened without
focus, no OS input injected). The steps were: typing into the Text form, Settings
with a switch to Edit and Done, typing into the Edit form, a replacement while
focused, Enter, Generate, the model chip dialog, then a click outside, wheel zoom
and the front-view shortcut. Each prompt kept its own text, the key after Settings
reached the viewport's Scale transform, and screenshots show the note and
warnings. Offline, no price was ready and the dialog showed its loading state, so
the mode switch was a property change while it was open. Blender treats the blur
click as handled, so it does not also select in the viewport; the next click does.
This path is unchanged; the [Studio view](ui/studio-and-library.md#explicit-studio-view) records its
first-click header evidence. Focus after switching to another application and
back, and the IME candidate window, continue in #440.

## The model chooser

A `Model` section (a box with a `NODE_MATERIAL` header, like the others) holds a wide button (icon + model name) that opens the picker, with the native dropdown as a small fallback on the right. A form with a trained choice names it on that button as the composer's chip does (**FLUX.1 Dev · Studio LoRA (+1)**, see [trained models section](#trained-models-section-and-project-defaults)). Its one-line description belongs in the picker, not the panel.

A form is described as loading its model only while that description read is
pending. A failed read shows its sanitized reason in the form and composer note,
with **Retry loading model** repeating the same background read. A later read,
success, another model or changed credentials clear it; drawing never retries.
Online, a form with neither a pending read nor a failure recorded this session,
such as one in a reopened file, offers the same retry instead of a saved error.
A model-load error saved with the file is not drawn in the form, below Generate
or in the composer note unless this session recorded it. With online access disabled, the form says so and offers no retry. An MCP call
needing the description, such as `model_schema`, starts the next read, or joins the
pending one, and waits for it off the main thread
([MCP quote contract](MCP.md#historical-upstream-reference)); a read that
fails again returns its new reason.

### Trained models in the picker

The Image tab's last chip, **Trained**, lists LoRAs and compositions instead of
base models: the selected connection's private trained models first, then
public Scenario LoRAs and compositions. Other chips and the base lanes never
list them. Each row
says how the model runs in the form the choice lands in (Image, or Render Image
when the picker opened from it), for example **Private LoRA via FLUX.1 Dev**. A
model no checked base runs stays readable but greyed, with its reason: the base
models are still being checked, no base in this form accepts its type, it is not
trained yet, or Blender has no route for its type (**Not available in Blender**).
Private plain custom models are not listed here.

Base-model descriptions declare the LoRA inputs, so they are read only when the
Trained chip opens: in lane order, once per connection, with a progress line and
the reason when reading stops. A catalog refresh never starts these reads, and
neither does a trained list that arrives after the dialog closed on this chip.
Descriptions a stopped scan never reached are read the next time the chip opens;
only a refresh lets a later Trained chip retry the ones that failed. Its list
shows ten rows, fewer when the window is too short for the whole dialog at the
current UI scale, so OK and Cancel stay on screen.

The open dialog is rebuilt only when what it shows changes: when the trained list
or base descriptions arrive, and once newly downloaded thumbnails are cached.
Unlike Studio's popups, a pump tick never refreshes it. Blender rebuilds a
refreshed dialog and matches its old buttons to the new ones, but OK and Cancel
refer to the block they were built in, so they never match: a rebuild while OK
is highlighted or held drops that state and cancels the click without a message.
Refreshed on every tick, OK sometimes needed two or three clicks. A click that
lands on one of the remaining rebuilds can still be lost. As in any Blender
dialog, a click outside a field being edited, such as a typed **Strength**, only
ends that edit; the next click reaches OK.

The highlighted model's box shows its kind, **Runs with** for a single base or a
**Run with** choice among several (the form's current base when it runs the model,
otherwise **Choose a base model**; OK refuses until one is chosen), and for a LoRA
a **Strength** field bounded by the base's declared range. As in the Scenario web
app, it starts at the LoRA's own strength (the scale of the first entry of its
concepts, when its record carries one), otherwise at 0.75, clamped to that range;
Reset to Default Value returns the field to that start. A base's declared default
does not move the start. The chosen base and strength stay while base
descriptions keep arriving and the rows are rebuilt; another row or a new dialog
starts again from the form, and a new dialog highlights the form's first trained
model. A composition shows how many LoRAs it combines with their own strengths. One line
states the effect on the form: adding to the LoRAs already chosen, changing this
LoRA's strength, or replacing the form's trained models. Choosing through another
base, choosing a composition, or choosing a LoRA over a composition replaces the
form's choice; that line says so before OK. The whole new choice is checked against
the base's current schema before the form changes, and then the price is
invalidated. The box ends with **Experimental: LoRAs and compositions in testing** (see
[experimental status](#experimental-status)). Drawing reads this state only.

The base's LoRA inputs are never drawn as text fields. Every request writes the
form's choice into the base's declared inputs with the same route function local
MCP and the quote check use; a base change, a model no longer listed, a choice
saved under another connection or project, or a route the current schema refuses
keeps the choice and blocks the price with its reason. An ID that an earlier
version saved in the base's model text field (such as **LoRA or Composition
Model**) becomes the form's choice when it has none, so it is neither dropped
silently nor sent unchecked; it gets the same checks and remove button.
Dropping any value an earlier version saved in an input the form no longer
draws, such as the LoRA lists it drew as text, prices the form again, so a price
error saved with the file gives way to the current reason.
**Reload parameters** restores the job's LoRAs or composition that
are still listed, never adds a strength the job did not send (the base's declared
default applies, otherwise the price asks for one) and warns about the trained
models it could not restore. Before the trained list has loaded (still loading, or
its read failed), it keeps every model the base's LoRA inputs describe, unchecked,
says they are checked once the list loads, and the price waits with the list's
reason. Until then the Trained models box shows each by its ID: the job's
composition, from the base's model ID input, as a composition without a strength,
and its LoRAs, which keep the strengths they ran with. When the list arrives, the
models it names get their listed kind and name (a composition sends no strength)
and the form is priced again; one it does not name stays and blocks the price with
its reason until it is removed.

### Trained models section and project defaults

The Image and Render Image forms show a **Trained models** box (`SHADERFX`) right
after the **Model** box, in the sidebar, in Studio and in Render Image alike; any
other form shows it only when it holds a choice. Each chosen LoRA has a row with
its name and a remove button, then a **Strength (min to max)** field bounded by
the range the current base declares. Editing a strength clamps it to that range
and reprices the form. Resetting the field to its default (Backspace over it)
returns it to the start the picker gives a newly chosen LoRA, rather than 0: the
LoRA's own strength, otherwise 0.75, within the base's range; a base's declared
default does not move it. Each LoRA keeps its own strength in form order, so
removing one never shifts another's strength. When the base's description
changes its range, the fields take the new range and the form is priced again,
even after a background catalog refresh, but a strength outside the range is
kept and the price is blocked with the range until the user edits it; nothing
changes silently. On a base that declares no range, including after the form switches to
one, each LoRA loses its field, as a LoRA chosen there gets none, and the form is
priced again with what that base sends, described below. Both apply as well to a
LoRA that **Reload parameters** restored before the trained list named it. A
refreshed description that changes anything else in the base's LoRA inputs, such
as its declared default or the LoRA types it accepts, also prices the form again.

A LoRA without a strength, such as an ID adopted from an earlier text field, gets
a **Set strength** button when the base declares a range: the strength then starts
at that same start and the price is requested again. A base that declares a
default shows **Strength: the base model's default (N)** and sends that default
until a strength is set, with **Set strength** below it when the base also
declares a range. A request never adds a strength the user did not see. A
composition row reads **Name (composition)** with its LoRAs and their own strengths listed
read-only below it. The reason a choice cannot run is drawn in the box with
`ERROR` (or `TIME` while trained models load), and **Add LoRA or composition**
opens the picker on the Trained chip. The box ends with the short experimental line
**Experimental: in testing**: its title already names trained models, and the
picker's full line was clipped at the default sidebar width.

The project-default row stores one explicit choice per form for the selected
credentials and optional Project ID, through the
[runtime owner](JOB_STORAGE.md#runtime-owner). It reads **Project default:
FLUX.1 Dev · Studio LoRA (+1)** or **No project default for this form**, then
**Use**, **Save** and **Clear**. **Save** stores the form's base model, LoRAs and
strengths (or composition) only when that choice runs now, and only over the
revision and selection drawn on the button; replacing a saved default and
**Clear** ask for confirmation. Saving or clearing prices nothing and changes no
payload. **Use** replaces the form's choice with the default after the same route
check and reprices it.

A form with no explicit choice in this scope (no model chosen and no trained
model, as in a new scene or file) takes the saved default: the main-thread pump
applies it once the base description and the trained list are loaded. Until
then, the price reads **Applying the project default ...**; if the default cannot
run (a model no longer listed, a base the form no longer offers, another type, a
refused route, a base described without inputs, no connection to write it
under), the price reads **Project
default ... is unavailable: choose a model or clear the default.** with the
reason, and an unreadable stored row reads **Could not read the project
default**. Generate stays disabled, and this reason comes before the form's other
request checks. It never falls back to the base model alone: a write into the
form that fails part way restores the form's empty model key and leaves no
trained choice, so the form stays blocked. A default lands
only on a base the form's own model list offers. A default's missing base
description is read at most once per base and selection; a read that could not
start is tried again by the pump. Choosing a model or a trained model makes the
choice explicit, and the default no longer changes that form. Applying a default
in another scene reprices only that scene's form. Local MCP render inspection and
quotes apply the same step first, and so does `render_form` `configure` without a
`model_id`, which changes nothing and returns the reason while the default waits or
cannot run; a failure writing the default is logged and the form keeps waiting.
Naming `model_id` in `configure` is an explicit choice, even for the model already
shown, and the default is not applied first.
Drawing reads cached defaults only; the owner is opened by the pump, requests and
operators.

The compact composer's model chip names the choice as **Base · LoRA (+n)**. A
long LoRA name is shortened before the count, so the number of further LoRAs
stays readable; with a trained choice the chip opens the picker on the Trained
chip. The **Model** box's button in the sidebar, Studio and Render Image shows the
same label from the same helper; Blender clips it when the sidebar is narrow, and
it keeps opening the picker on All, since **Add LoRA or composition** sits in
the Trained models box below. Drawing it reads the form's slots only. Strengths are
edited in the sidebar or Studio, and the composer note says so (**Edit LoRA
strength in the sidebar or Studio**) when nothing more urgent is shown. Job views
name the base with its trained choice the same way while the session that
submitted them lasts; a recovered saved job names its base model only.

## Duration and Match timeline

The video model's `Duration (cost)` drives the camera-path duration for **Render
Video** when **Match timeline** is on. The planner tells users to build the path
before capture. Capture uses the displayed preview/scene range without implicit
padding or trimming. Uploaded clips remain unchanged when timeline/model settings
change. The base **Video** lane keeps the reverse setting direction: the model's
duration follows the scene frame range through `generation.apply_match_timeline`.

## Reference inputs

A file input's add options match its kind (`props.addable_sources_for`): a **3D** input offers a model file and **Upload selected mesh** when a mesh is selected; an **image** input offers Upload plus the viewport/camera stills and the render result; a **video** input offers Upload plus the clip captures. Never offer a source that produces the wrong asset type (no image capture on a 3D character input).

A selected mesh appears as a candidate for each empty **3D** input. **Upload
selected mesh** explicitly exports the selection as one GLB and uploads that
snapshot. A pending or completed explicit reference replaces the pinned candidate;
do not offer a second mesh upload into an occupied slot. Edit 3D still requires
an input mesh. Later scene edits do not change an already uploaded snapshot.

Across generation forms, attaching a local file does not send it. Image inputs
also support explicit viewport, camera and Render Result snapshots. Video inputs
support viewport/camera clips over the preview range, or scene range when no
preview is enabled, without implicit padding or audio. Grey clay capture is
applied when selected. Do not offer an image Render Result for a video input.
The separate **Upload reference** button sends an immutable snapshot before a
final price can be requested. Show upload progress and **Inspect uploads** when
review is needed. Do not offer automatic retry for uncertain uploads or mutate
the reference during drawing. The maintenance pump attaches a completed asset
only to its original scene, unchanged for a scene snapshot, and the unchanged
lane, model, input type and reference slot, then invalidates the old price.
A removed/edited slot must never receive a late upload result.
Transient timer context without the originating scene pauses attachment. Local
validation rejected before task admission permits correcting and retrying the input;
uncertain admitted work retains its duplicate-upload guard.

**Inspect uploads** offers explicit known-status refresh, unclaimed preparation
cancellation, **Upload again** for an unfinished upload whose suggested recovery is
a restart, and finished staging cleanup; cancellation, restart and cleanup require
confirmation. When a transfer stops or a recovery finishes while the view is shown,
the maintenance pump refreshes those suggestions. Drawing reads them and records
only an in-memory time of its last draw. Popups report no close, so a view not
drawn for two seconds stops that store read until it draws again.
A restarted upload never attaches itself to the slot.
**Use saved upload** and an input's **Saved uploads** open the same paginated
view. Offer attachment only for imported uploads matching the input kind
(image, audio, video or 3D). Attaching one needs a separate confirmation of its
scene, lane, model, input and reference destination. A changed destination must
be reviewed again. Pending or uncertain marked uploads block generation so the
prototype path cannot upload the same file a second time. Recheck that destination
on confirmation, keep drawing read-only and invalidate the prior generation price after attachment.

Render forms show **Capture and upload scene** in their scene/clip section, with
that slot's progress, inspection and removal controls. Render Video's optional
first frame always exposes a file field and native folder picker, followed by a
separate **Upload first frame** action after choosing an image. Do not draw these slots
again as style references. The scene snapshot precedes styles; a first frame
precedes other images in the same array. Disabled first frames are omitted without
canceling or deleting their upload. Changed first-frame paths require explicit
replacement. Result reuse must not silently replace a role-tagged single-file
scene or first-frame slot; require explicit removal first. A saved image reaches
the first frame only through its own reviewed
[first-frame handoff](ui/saved-media.md#saved-image-first-frame-handoff), never through
**Use as reference**. Changing the path or automatic Spark option invalidates the price.
An empty look with automatic Spark enabled blocks the render quote until its
[separate Prompt Spark price](BLENDER_JOB_CONTEXT.md#render-look-preparation)
is approved, a look is entered or Spark is turned off. Drawing remains read-only.

## Generations

Saved-job controls in Jobs, Generations and every view that reuses them come
from the shared [action descriptors](BLENDER_JOB_CONTEXT.md#active-saved-job-controls):
the same label, icon and operator properties for the same job. Add a saved-job
action as one descriptor, never as a surface-specific button. Hide an action
that is not offered rather than drawing it disabled, and keep any approval in
the operator's own dialog.

**Reload parameters** is available only when a result records a supported
generation lane, or a generic job records a supported kind. A catalog lane
without a native form never falls back to its kind's form, which would drop
that lane's rules. Film tasks and generic jobs without that information keep
their saved-result controls; they must not overwrite the Image form.

**Remove background** prepares the Image form and never submits directly. Its
message tells users to upload the reference, review the price and choose
**Generate**. Validate the file and available model/schema before replacing the
form; reset the old prompt/settings/references and invalidate the previous quote.
Existing jobs and admitted uploads keep their own lifetime and origin guards.

Each result collapses on its own; the panel header has a **Collapse all / Expand all** toggle. A failed result shows a red error control in its header whose tooltip is the whole message (with the Error ID) and which opens the full text with a Copy button on click (a `description()`-driven operator, since Blender labels have no per-instance tooltip). Never truncate an error to a single clipped line as the only way to read it. Below the model row, a failed result also draws `Failed: ` and its whole error in red, the first line with the `ERROR` icon and the next ones aligned after it, wrapped by the width Blender's interface font measures (`label_wrap`, with the preview status lines' inset). For a shared job that error is the [reason Scenario gave](BLENDER_JOB_CONTEXT.md#failure-reasons) when it is plain text, otherwise a fixed sentence, at most 400 characters with its support error id kept. **Generation details** draws the whole error the same way in a box with **Copy error**; the composer's job card declares its requested popup width, so its first draw wraps like Studio's. Drawing reads only the view's error; the maintenance poll sets it. Installed tests on Blender 5.1.2 macOS arm64 draw a failed shared job's reason through the real Generations panel, **Generation details**, the composer strip and the job card with store reads refused; a desktop screenshot of the wrapped reason belongs to the live failed-job reason check in #440. The prompt box carries a trash button that deletes its text, greyed out when empty.

## Jobs status and remote progress

A shared Jobs row names its saved state in words (`waiting to submit`,
`submitted`, `canceling`, `finished on Scenario`, `downloading results`,
`download failed`), never a raw state identifier. A model or workflow job
that finished on Scenario stays a Jobs row as `finished on Scenario` while its
results still need a download; Generations never lists it as a success before
then. Prompt Spark, Translate and Blockout results are text that their tools
read into memory without saving a result download, so such a job is finished
once Scenario reports success: Generations lists it, including after **Inspect
saved jobs**, and it never draws a Jobs row or a download line. The projection
decides from the saved request (its operation and model), never from drawn
text. While a known remote job is polled, the latest validated Scenario status
replaces `submitted`: `queued` (Scenario's `pending` and `queued`), `starting`
(`warming-up`), `generating` (`in-progress`) or `finalizing`. The composer's
job strip uses the same words, capitalized. Draw a native progress bar with its
whole percentage only while generating or finalizing and only from 1%; never
draw a percentage for zero, a fraction below 1%, unknown or queued states, and
never show an estimated time. With online
access disabled, a `submitted` or `canceling` row
adds `Status paused while online access is disabled` and a `finished on
Scenario` row adds `Download paused while online access is disabled`, whether or
not a reading exists yet. Otherwise, when the reading is not being kept current,
add one information line: `Status is not updating`. Drawing reads only the
projected view; it performs no I/O, store read, session creation or property
write. The pump redraws when the drawn rows or online access change, so the bar
moves without hovering, but it does not redraw for the brief in-flight refresh
that hides saved-job controls. Studio Jobs reuses the same draw.

Installed tests on macOS arm64 Blender 5.0.1, 5.1.2 and 5.2.1 check the drawn
label, bar factor and text, the offline and stale lines, a finished generation
drawn through the real Jobs panel with its offline download line and absent from
Generations, finished Prompt Spark, Translate and Blockout jobs inspected with
online access off and listed only in Generations without an offline line, the
absence of storage reads and writes during draw, and one redraw per projected or
online access change. No desktop interaction or screenshot of the bar is claimed
yet; that check is in #440.

## Wording

- Verbs on buttons say exactly what happens: `Generate`, `Add to scene`, `Use as reference`, `Refresh cloud`, `Save for recovery`.
- Singular/plural is correct: `1 Job` / `3 Jobs`, `1 marker` / `3 markers`.
- Prompt helpers request a free exact server price first. Show the formatted CU amount below the tools with a separate action-labelled approval button. Do not use a fixed price or a tooltip as spending authorization. Keep pending/error text readable; an uncertain job offers inspection, never automatic resubmission.
- CU labels follow the web generation indicator: up to three decimal places,
  no trailing zeros, and thousands separators below 10,000; at or above 10,000,
  use K/M/B/T with three significant digits (`0.123 CU`, `1,234.568 CU`,
  `12.3K CU`). This is display formatting only. Keep the original server decimal
  in the quote, approval action and saved job; never reconstruct it from the label.
- No internal names in user text (a person sees `Reference Images`, not `referenceImages`).

## Tooltips

Every operator sets `bl_description`: one sentence, action first, the cost when it spends credits. Blender adds the trailing period.

## Icons

Modality icons (image, video, audio, 3d) are Scenario's own PNGs (`scenario/icons/`, loaded by `blender/icons.py`, `icons.kwargs(name)` with a built-in fallback for headless). Section and action icons are Blender built-ins chosen to read at a glance.

## Status messages

`runtime.set_message(...)` lines are transient: `runtime.message_visible()` hides them after 8 s so a stale line never reads as current.

## Experimental status

Film and capabilities whose result handling is not accepted keep an explicit
experimental status. It describes acceptance only: it never hides or blocks a
model, task, estimate or approval. Use the built-in `EXPERIMENTAL` icon and
short wording. A capability line reads **Experimental: _capability_ in testing**
(`capability_status.label`): it says the capability runs and is still being
tested, never that it is refused. Wording such as *not accepted* read as if
Scenario refused a model that runs. A section whose title already names the
capability uses the short **Experimental: in testing** (`capability_status.label()`
without a subject). Drawing reads the status and changes nothing.

- The **Film** sidebar panel shows **Experimental** on the right of its header,
  visible while collapsed and above its nested panels. Studio reuses panel bodies
  only, so it draws the same line on every Film page.
- The picker's highlighted LoRA or composition box ends with
  **Experimental: LoRAs and compositions in testing**
  ([trained models](#trained-models-in-the-picker)); the **Trained models** box,
  whose title names them, ends with **Experimental: in testing**.
- For models offering `audio2txt` or `video23d`, the model picker's
  highlighted-model box and the persistent **Model** row show a read-only
  **Experimental: speech-to-text in testing** or
  **Experimental: video-to-motion in testing** label. The Model row is shared
  by the sidebar and Studio lanes, Render Image/Video and Edit 3D; the viewport
  composer's model chip does not show it. These models can be submitted and
  results stay in saved jobs; provider behavior and result handling are not
  accepted. Generic Import is offered by file type, so a returned model (GLB,
  glTF, FBX, OBJ or mesh PLY) or media file may import, but motion and
  transcription handling is not accepted ([#190](https://github.com/scenario-labs/blender-plugin/issues/190)).

Installed native tests assert the registered header preset draw call, the Studio
line, the picker status and the Model row status, and a unit test keeps every
capability line free of refusal wording. Physical placement in a desktop session
has not been captured.

## Composer placement

With region overlap, Blender draws the 3D View toolbar and sidebar over the
viewport's main region. The floating composer therefore places itself in the
span those regions leave uncovered, not in the whole region. Each draw and each
pointer event measures every visible toolbar or sidebar region whose x-range
overlaps the main region, from its actual position, so a flipped sidebar counts
on the left. Without region overlap they sit beside the viewport and change
nothing.

- The pill and card start at the bottom centre of that span.
- A saved or resized card width shrinks to the span minus margins. The card
  needs its minimum width of 420 px plus a 24 px margin on each side, all
  times the UI scale. The UI scale is Blender's custom-interface multiplier
  (`preferences.system.ui_scale`: the display DPI times the Preferences
  resolution scale), not the pixel size, which is a line width that already
  includes both. A narrower span shows the pill in its place. At a UI scale of
  4 (Retina with a Preferences resolution scale of 2), an open sidebar easily
  leaves less than the 1,872 px the card needs. The viewport also needs the
  card's 132 px height plus a 24 px margin above and below, times the UI scale
  (720 px at scale 4): a shorter viewport shows the pill too, instead of
  drawing the card's rows over each other. Drawing, hit testing and drags then
  use the pill geometry. The stored expanded choice and card width stay
  unchanged, so the card returns by itself once the sidebar closes or the
  viewport grows.
- While the card has no room, a click on the pill, expanded or collapsed, opens
  the current lane's form in the Settings dialog (the dialog of the card's
  **Settings** chip) instead of expanding. A short message in the status bar
  (and in the sidebar panel when it is open) suggests closing the sidebar or
  enlarging the viewport, and a drag still moves the pill. A prompt that had
  focus when the card gave way is committed and left, as a click outside would.
  If its lane's prompt changed elsewhere meanwhile, focus is left without
  overwriting that text and a warning is reported.
- The pill narrows to its minimum of 200 px times the UI scale while keeping its
  margins. A narrower span then eats into the margins. In a span narrower than
  the minimum itself, the pill keeps that minimum width and ends at the right
  edge of the span, the sidebar edge when one is open, with its Generate label;
  it runs under the toolbar or past the viewport's left edge instead.
- The model chip opens the model picker and shows the sidebar, which holds the
  rest of the lane's form. When showing the sidebar would leave the card no
  room, the chip opens the picker alone, so the card does not give way to the
  pill while the picker is open. A hidden sidebar reports no width, so the
  composer assumes it reopens at Blender's default width of 220 px times the UI
  scale; a sidebar widened before it was hidden can still leave less room than
  that, and the pill then stands in as usual.
- The seven lane tabs share their row in proportion to their full labels' length
  plus a fixed allowance, so Render Video gets more room than 3D. Equal cells
  would clip R-Img on the minimum card and shorten Render Video on the default
  one; with these widths the default card shows every full label and the minimum
  card every short label whole.
- A lane tab shows its full label when it fits the tab, otherwise a short one:
  **Img**, **Vid**, **3D**, **Mat**, **R-Img**, **R-Vid**, **Env**. Each tab
  decides from the measured text width, so Image and Video can keep their full
  labels beside R-Img. Only a short label that still does not fit is clipped with
  an ellipsis, as a last resort and never below its first character, so a tab is
  never blank.
- A saved or dragged offset keeps the whole pill or card inside the viewport,
  between any toolbar and sidebar: it stops at a bare viewport edge as it does
  at a side-region edge, so Generate and the minus button stay in view. The
  pill and the card share one offset, so a pill moved against an edge expands
  into a card against that same edge, and a card parked at the sidebar stays
  whole when the sidebar closes and the card widens. Only the drawn placement
  is clamped; the saved offset is kept until the next move.
- A move or resize starts from the placement as drawn. A composer parked past an
  edge (a sidebar that opened, or a viewport that shrank) therefore follows the
  pointer at once instead of waiting for it to cover the hidden excess.
  Releasing a move stores the drawn offset, and releasing a resize also stores
  the drawn width; Esc during the drag restores the saved placement.
- Drawing and hit testing share one layout, so every control is drawn exactly
  where a click reaches it.

The card and the pill avoid only the side regions so far. Horizontal regions
drawn over the viewport are not avoided by them: the asset shelf along the
bottom (in Pose Mode, for example), the tool header and a transparent header
along the top can still cover part of the composer or its default spot. The
[job strip](#compact-job-strip) does avoid them.

Installed tests cover these rules with synthetic overlapping regions, including
the modal's move, resize and release handlers with the sidebar opened and
closed, a scale-4 Retina layout whose sidebar leaves no room for the card, a
viewport one pixel too short for the card at scales 1, 2 and 4, the model
chip's choice to show the sidebar or not, and the background viewport's real
regions. A physical macOS Blender 5.2.1 check at
that scale found the card running under the sidebar and blank or ambiguous tab
labels, which motivated the pill fallback and the short labels. A physical
Blender 5.1.2 check found the minus button and Generate past the bare right
edge after the pill had been moved right and expanded, and after a card parked
at the sidebar widened when the sidebar closed in a 1018 x 671 pt window, which
motivated the whole-composer clamp. Installed tests drive both cases through
the modal. A later desktop pass on #398's head kept the card in view at window
edges and after resizing (see the
[release evidence](maintenance/release-acceptance.md#composer-and-ime)).

### Double-click

A double-click on the card background, its resize corner or the
[job strip](#compact-job-strip)'s background puts the composer back at its
default place and width, and a double-click in the prompt selects the word (or
the run of spaces) under the pointer, so typing replaces it.

Blender never hands a modal operator a double-click: before calling it, the
window manager turns the second press into a plain press (only key maps see the
double-click value), in Blender 5.0, 5.1 and 5.2 alike. The composer therefore
pairs presses itself, by Blender's rule. A press completes a double-click when
it lands on the same composer target (a control, the card or strip background,
the prompt or a strip chip) as the previous press, less than Blender's
double-click time preference after it (350 ms by default) and within the
composer's 4 px drag threshold times the UI scale. Another key or button
pressed in between (Escape, which cancels a held press, included), a drag or
resize past that threshold, or a press outside it starts over, and a completed
pair is spent, so a third quick press is a plain press again. The resize grip
follows the pointer from its first move, but jitter within the threshold while
the button is held keeps the press. Time is taken when the composer handles
each press, since Python events carry no timestamp. A click that changes the
layout under the pointer (the pill expanding into the card) never pairs with
the next press. On any other control the second press of a double-click does
nothing more, so a double-click on Generate submits once and one on a strip
chip runs its operator, or opens its dialog, once.

Installed tests send the composer's modal what Blender really delivers, two
press and release pairs at pinned times, and check the word selection, the
reset, the timing and distance limits, jitter on the resize corner, the presses
that start over (Escape included) and the single submission. Job strip tests
send the same pairs to the strip's background, which resets the placement
within the time and distance limits only, and to a strip chip, which dispatches
once. Unit tests cover the pairing rule itself. Physical double-clicks on
Blender 5.1.2 macOS selected nothing and reset nothing before pairing; real
double-clicks later reset placement and width on a desktop (see the
[release evidence](maintenance/release-acceptance.md#composer-and-ime)), and a
physical double-click on the strip's background continues in #440.

### Errors and file loads

The composer's modal operator runs one session at a time. A pointer move over
the composer starts one, and so does a pointer move anywhere in a 3D viewport
while the prompt is focused. The session lasts until a click outside the
composer, or until the pointer leaves it while the prompt is unfocused. A
refused Generate, such as a failed poll or an error report from the submission,
is reported as a warning and the session goes on, and so is an error from the
operator a [job strip](#compact-job-strip) chip runs. If handling any other event
raises, whether a click, a key, a pointer move or a drag, the composer ends its
session and Blender reports the error. So does an error that F2 or a
composition's second press raises while opening the
[native text field](#composer-text-input), unless it is a `RuntimeError` or an
`AttributeError`, which that call reports as a warning. The composer leaves a
focused prompt and marks no session as running, so the next pointer move over
it starts a new session. Blender can also end a session without an event,
because its window closed or a file loaded. The operator's cancel callback then
ends it the same way: it leaves a focused prompt, stops a selection drag, puts a
dragged composer back where the press found it and marks no session as running,
so no 3D view keeps drawing a caret that types nothing. Either way, as at every
session end, the composer forgets a pending click and an untyped press: the next
session's first click completes no [double-click](#double-click) and its first
untyped press starts a new composition run. It also gives the window its own
cursor back when a drag, or the pointer resting on the resize corner, had set
one, so no resize cursor stays over the viewport. Every file load (Open, Revert,
File > New) also collapses the card to the pill, leaves the prompt and restores
the saved placement through the composer's load handler, which persists across
loads.

A physical Blender 5.1.2 macOS check found the composer frozen for the rest of
the session after one Generate refused by the GUI spend guard: clicks on it fell
through to the viewport, and a file load did not help because Blender had
dropped the non-persistent load handler at the first load. Generate refusals
became warnings in a later change. Installed tests on the packaged ZIP now send
the modal a Generate refused by the spend guard's poll and an error raised while
a key is typed in the focused prompt. They call the cancel callback during a
placement drag and during a selection drag in the focused prompt, and run three
file loads in a row. The error, cancel and file-load tests fail on the previous
composer. Another test raises from the text field call on a composition's
second press and on F2. A session-end test ends a session by an error and by
the cancel callback, after a click and after an untyped press. Without the error
handling, the text field test fails and so does the session-end test's error
case; when a session end keeps the pending click and the untyped press, both of
its cases fail. A cursor test ends a session the same two ways while the pointer
rests on the resize corner; when a session end restores the cursor only after a
drag, both of its cases fail with the resize cursor still set. The tests call
the operator's handlers directly in background Blender, which cannot start the
modal itself: there, an `INVOKE_DEFAULT` call of a modal operator is refused
before `invoke` runs, even with a window, area and region override. No event-simulated GUI check (`--enable-event-simulate`) has run for
this change, because it was prepared without opening a Blender window. Blender's
window manager has therefore not yet been seen dropping the handler after an
error, calling cancel on a file load or a closed window, or keeping the load
handler across File > Revert in an interface session. The
[session-ending checklist](https://github.com/scenario-labs/blender-plugin/issues/66#issuecomment-6120385854)
lists those event-simulated checks and the physical rechecks, with their
expected results; #440 tracks them.

## Composer text input

The composer prompt is custom drawing fed by key events. Blender composes
input-method (IME) text only in its own text fields: an add-on can neither turn
composition on nor read it. Its composition events reach a modal operator with
no Python event type (reading one returns an empty string and logs a warning)
and no text. The composer therefore hands such text to a native field instead of
drawing a pre-edit it cannot receive.

- **F2** in the focused prompt opens a popover with one native text field, as F2
  rename does: the label **Type into the Image prompt** (the current tab's name),
  the field editing on display (`activate_init`), and the dimmed hint
  **Enter adds it to the prompt, Esc cancels**.
- The field starts empty. Blender selects all of a native field's text when it
  starts editing, so a field holding the whole prompt would replace it with the
  first composed character. Enter types the field's text at the prompt's caret,
  replacing a selection, as typed keys would; the composer keeps its focus.
- A composition that starts in the focused prompt opens the same popover, so it
  continues in a field that shows it. Left pending, it can land in the next
  native text field that opens, such as an Outliner rename: Blender 5.1.2 and
  5.2.1 insert a pending composition into a field on a click outside it.
- A composition starts with two presses in a row that have no Python type and
  no text: its start, then its first update. A dead key sends one such press,
  then its release or the key carrying the accented character, so the popover
  opens on the second press only. A release, a placement drag, an untyped event
  without focus, any named or text-carrying press in between (a click included)
  or the end of the composer's modal session starts the count again. Each
  untyped press is also a key between two clicks, so it starts
  [double-click](#double-click) pairing over, as any other key does. A key
  repeat (a held key) does not count: Blender never marks input-method events
  as repeats. In an input mode that commits each key, the first key's text
  arrives in that second press, which the composer consumes, so that character
  is lost.
- An untyped key that carries text (French ù, for example) is typed like a named
  key: without Ctrl, Cmd or Alt, at the caret. The modal reads each event's type
  once and handles an untyped event without reading it again, so each untyped
  event logs one warning rather than one per comparison.
- Drawing the popover reads the composer state and changes nothing. The field's
  update callback types the text and empties its window-manager property.
- Text from these paths follows the composer's form rules for typed keys (see
  [Buttons](#buttons)). When another form replaced the one the focused prompt
  belongs to, an untyped key with text, F2 and a composition's second press are
  refused with the same warning, open no popover, and the prompt keeps its focus
  and original text until Esc. When the form's model takes no prompt, they leave
  focus with *This model takes no prompt*. A refused composition is not handed
  to a field, so it can still stay pending. A popover confirmed after its form
  changed in either way adds nothing to either form, and the add-on's status
  message says why.

Installed tests drive the modal with synthetic untyped, dead-key and F2 events
(the popover call stubbed), set the field property and draw the panel into a
recording layout. Another installed test sends a dead key between two clicks in
the prompt, a click between two untyped presses and the end of a modal session
between presses, and checks that neither a double-click nor the popover follows.
Installed Edit form tests send an untyped key with text, F2, a composition's two
presses and a popover confirmation to a replaced form and to a form whose model
takes no prompt, and check that neither form's prompt changes.
A physical pass opened the F2 field at UI scales 1.0 and 2.0, typed dead keys
and brought a whole composition into the prompt, and the maintainer's check on
the candidate passed composition, commit and Esc after F2 (see the
[input method limits](KNOWN_LIMITATIONS.md#interface-and-capture)).
Candidate-window placement continues in #440; focus after the popover was not
recorded.

## Compact job strip

The expanded composer shows the current job of the form it describes in a
separate tray as wide as the card: the newest job this Blender session
submitted from that scene and form, through the composer, the sidebar or MCP
`generate`. Its scene and form come from the display-only
[scene lane binding](BLENDER_JOB_CONTEXT.md#remote-progress-and-scene-lane-binding);
workflow, Film, restarted and recovered jobs have none, so they never appear
here and stay in Jobs. Another scene, another lane tab or another connection
shows its own job or no tray.

- The tray sits above the card, else below it, else it is hidden; the card's
  controls never move. It stays in the span the toolbar and sidebar leave
  uncovered and wholly between the headers, tool header and asset shelf drawn
  over the viewport. The collapsed pill draws only a thin line inside its
  bottom edge in the job's tone; it keeps its own click.
- One line reads title · status · price · +N more, cut with an ellipsis. The
  status uses the [strip wording](../scenario/core/ui/job_strip.py), whose remote
  words are the Jobs rows' words capitalized (`Queued`, `Starting`,
  `Generating`, `Finalizing`), with a tone dot that is never the only signal.
  While a job is active, a thin bar under the line fills to the reported
  percentage from 1% to 99%, else it stays a dim full track. Unlike the Jobs
  bar, it shows no percentage while finalizing. Without online access, states
  that wait on Scenario add `(offline)`;
  a remote reading that polling is not keeping current adds `(last known)`.
  A paused delivery's own message replaces the status in the review tone, and
  **Inspect** shows it in full. A failed job keeps the error tone and reads
  `Generation failed: ` followed by the reason Scenario gave, cut with the line;
  **Inspect** shows the whole reason. **+N more** counts the form's other jobs
  that are not applied, canceled or failed.
- Chips come only from the shared
  [saved-job action descriptors](BLENDER_JOB_CONTEXT.md#active-saved-job-controls):
  **Inspect**, which is never dropped, and at most one primary action before it:
  a receipt retry, a cancellation, download recovery, the single result action,
  or **Apply... (N)** / **Reuse... (N)**, which opens the job card to choose.
  As in Jobs, **Cancel generation** appears only once a poll confirmed an
  inference job, the only type Scenario documents as cancellable.
  A submission in flight or unconfirmed, and an Image result being imported
  automatically, offer only Inspect. Refresh is never a chip; the pump polls.
  On a narrow tray the primary chip is dropped before the line gets shorter
  than 96 px at 1x; the tray hides when even Inspect does not fit.
- A chip runs its existing operator with `INVOKE_DEFAULT`, never `EXEC`, so the
  operator's own confirmation or destination dialog is the approval. Before
  dispatch, the click is compared with what the region last drew (request,
  saved revision and chips); a job that changed since consumes the click with
  *The job changed; check its updated actions*. A focused prompt is committed to
  its own form first, with the same guard as a click outside: a conflicting
  edit keeps focus and both texts and dispatches nothing.
- The job offers no action while an automatic status refresh or another command
  runs, except a prepared job's local cancellation. The tray keeps showing the
  actions it last offered for the same saved revision instead of blinking every
  poll, including those the poll that starts the command still lists until the
  next poll, but then only the chips that open the job card (**Inspect**,
  **Apply... (N)** and **Reuse... (N)**) dispatch; another chip answers *The job
  is updating; try again in a moment*.
- The cross hides that job's tray for its scene and form. The job continues in
  Jobs; a newer job there shows the tray again, and loading a file forgets
  dismissals. Only the 64 most recent dismissed scene forms are remembered, so
  an older one's tray can show again.
- **Inspect** opens a native job card: an unfinished job as its Jobs row, a
  finished one as its Generations result, always expanded, plus **Show all jobs
  in the sidebar**. The pump refreshes the open card like Studio. A job this
  connection no longer shows offers **Inspect saved jobs**. The card reuses the
  Generations result drawing, including its existing file-based image actions,
  which do not take durable application claims; the tray's own chips use only
  claim-backed descriptors.

Drawing reads ModelJobs' in-memory views and bindings only: it creates no job
owner or session, reads no saved job and writes no scene, WindowManager or job
state. Its only writes are the composer's Python display caches (the drawn
signature per region and the held actions); dismissals, which the modal
records, are Python only too, and a file load clears all three. Each keeps only
its newest entries: 32 regions, 64 jobs and 64 scene forms. Drawing and the
modal share one geometry, so a chip is hit where it is drawn; a press on the
tray's background drags the composer, and a [double-click](#double-click) there
resets its placement, since the modal pairs presses on that same geometry.

Installed tests on the packaged ZIP drive the real draw handler with recording
primitives and the real modal handler with synthetic events over jobs submitted
through the synthetic SDK transport. They check the line, chips, bar and caches
with draw-time ensure calls and store reads patched to fail; each chip's
`INVOKE_DEFAULT` call and exact descriptor properties; the stale-click, busy and
focused-prompt guards, the busy guard also on the poll that starts a refresh;
dismissal, its bound and file load; scene, lane and credential
changes; an unconfirmed submission offering only Inspect; a job Scenario has
not confirmed as an inference job offering no cancellation; a double-click on
the background and on a chip; and **Resume download** through the real operator
downloading once without a paid request.

Blender's text drawing turns GPU blending off, as offscreen draws on Blender
5.0.1, 5.1.2 and 5.2.1 show, and the tray draws its line before its bar, so
every filled shape enables alpha blending itself. Without that, a physical
Blender 5.1.2 macOS check saw the dim track paint as bright as the fill: the bar
looked full at every percentage, like the states that report none. Installed tests
draw the tray and the pill's line through the composer's own shape and text
primitives over a recording GPU that turns blending off after text, and check
the dim track, the fill's width at the reported fraction and the blending of
every translucent shape.
The paid desktop pass opened the job card and the Reuse and Apply reviews from
the strip's chips with Esc and viewport focus returning, and the #424 recheck at
UI scale 1.0 drew the bar's fill to the reported fraction over a dim track (see
the [release evidence](maintenance/release-acceptance.md#composer-and-ime)).
UI scale 2.0 continues in #440.

## What Blender cannot do (so we do not fake it)

- Text is left, centre or right aligned, never justified.
- A panel text field is single line; a taller prompt box grows the field, it does not wrap for editing.
- Dialogs and the sidebar cannot take the composer's custom colours; their layout follows this guide, their palette is the Blender theme's.
- Input-method composition happens only in Blender's own text fields; the composer opens one rather than faking a pre-edit (see [Composer text input](#composer-text-input)).

## Feature interaction guides

Each area keeps its interaction rules and desktop evidence in its own guide, so
parallel changes edit different files. The rules above apply to all of them.

- [Saved media, previews and image handoff](ui/saved-media.md)
- [Saved 3D results](ui/saved-3d.md)
- [Environment, panorama and World](ui/environment.md)
- [Blockout plans and cloud recovery](ui/recovery.md)
- [Film controls](ui/film.md)
- [Studio, workflows and Library](ui/studio-and-library.md)
