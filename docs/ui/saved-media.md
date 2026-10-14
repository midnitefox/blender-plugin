# Saved media, previews and image handoff

Interaction rules and desktop evidence for approving and reusing saved media. The shared rules are in the [UI style guide](../UI_STYLE.md).

## Prompt approval interaction evidence

These earlier offline Blender 5.1.2 macOS arm64 captures show the price before
approval and the delivered text afterward. The model, price and transport are
synthetic; they are not current service pricing or live generation evidence.
They predate the shorter [CU labels](../UI_STYLE.md#wording). The price request made no
submission; the separate approval produced exactly one.
Native field editing, focus transfer and viewport selection/zoom were exercised.
Computer-control focus guards interrupted some typing attempts; this does not
establish exhaustive keyboard or clipboard acceptance. The isolated run exited
cleanly and left the normal profile unchanged. A disposable Blender copy used a
distinct application identifier and local development signature to distinguish
its window; the extension ZIP was unchanged. Supported-version packaged tests
remain separate from this single-version desktop check.

![Full exact prompt price with a separate approval button](../images/prompt-price-approval.png)

![Generated text delivered to the original prompt field](../images/prompt-result-delivery.png)

Render forms label the empty-look option **Prepare look with Prompt Spark**.
Its price and approval remain inside the Look section, separate from Generate.
The following offline Blender 5.1.2 macOS arm64 fixture shows that boundary before
and after one synthetic approval. Native focus, a keyboard edit and viewport
selection/zoom were checked; the run exited cleanly with the normal profile
unchanged. As above, the test-only Blender app identifier/signature differed,
while the candidate extension ZIP was unchanged. This is not live render evidence.

![Separate exact Spark price before preparing the render look](../images/render-spark-price.png)

![Prepared look in the original render form before generation](../images/render-spark-result.png)

## Saved video and audio confirmation

Shared saved jobs show a separate **Add video/audio strip (N)** action for each
supported result. Its confirmation states the destination scene, exact frame,
one-strip operation, unused channel, unchanged timing and local-file dependency;
video additionally states that embedded audio is omitted. Cancel discards the
approval. Drawing reads the prepared fields without accessing files or changing
jobs. A changed scene, frame, record or credential context requires new review.

![Native saved audio confirmation names the scene and frame before adding one strip](../images/saved-media-approval.png)

Offline desktop acceptance used the exact packaged ZIP
`3d831dc70efe10e0e5c2cf377e2044015e49f32814ed2da6b8649822ff76a2d2`
in an isolated Blender 5.1.2 profile on macOS arm64. The fixture began with one
mock generation and a saved WAV; mouse approval inserted one sound strip at
frame 27 without another mock submission or request. Viewport selection/zoom,
editor switching, scene selection and the Home key in the Sequencer worked.
Blender exited cleanly and the normal profile fingerprint was unchanged. The
test-only app copy used a distinct bundle identifier and local ad-hoc signature
for reliable window targeting; the vendor executable code and extension ZIP
were unchanged. This is desktop interaction evidence, not human listening,
paid provider acceptance or other OS desktop coverage.
Native exact-ZIP tests separately decode synthetic MP4, WebM, MP3, WAV and OGG.

## Saved media playback and waveform

Each downloaded saved video result gets one row of saved-job descriptors:
**Play video (N)**, which opens the system player, with **Play in Blender
(N)**. Each downloaded saved audio result gets two: **Play audio (N)** with
**Waveform (N)**, then **Open in system player (N)** on its own row. Play audio
plays inside Blender, so its label names no other app; the system player's
label says that one opens, and its own row keeps it whole in a narrow sidebar,
where a third aligned button would clip it. While a result's audio prepares,
starts or plays, its **Play audio (N)** button reads **Stop audio (N)** in the
same place; the
[in-extension playback](../RESULT_PREVIEWS.md#in-extension-audio-playback) ends,
or another result's Play audio starts, and it reads Play audio again. Only one
result shows Stop. Numbering counts every saved asset, like **Add video/audio
strip (N)**. Jobs, Generations and the Studio pages that reuse them draw the
same rows from the same descriptors. These controls are read-only: they
generate, download, apply and spend nothing and leave the scene unchanged, so
they open no confirmation, and their tooltips say so. They are not job actions:
MCP `job_status` does not list them, and no MCP tool launches a player or plays
audio. Play reports through the status line once the private copy opens or
fails, and Play audio when its sound starts, ends, stops or fails, with the
reason; both are unavailable in background Blender. Drawing reads which result
plays from the controller's memory; it never starts, polls or stops a child.

The waveform box follows the job's controls: a `SOUND` header naming the result,
with a close button, then status text wrapped to the box, **Cancel** while
loading, the waveform icon when ready and **Retry** after an error or
cancellation. Blender clips a label wider than its row, and its interface font
is proportional, so status text wraps by the width Blender's interface font
measures at the widget text size, not by a character count. The available width
is the drawn region's less the panel, box and icon space of the sidebar layout.
In Studio it is the width Studio requests for its popup: Blender lays a popup
out before it sizes the popup's region, which is 0 px wide on that first draw,
and a popup's context region is the header that opened it. Lines break only at
ordinary spaces, never after a hyphen in a word that fits: a value keeps its
unit ("1.00 s", "44.1 kHz") and a wrapped line ends with its `·` separator. A
word wider than the whole line splits after its last hyphen that fits, else
after its last character that fits. Text and icon size use
`preferences.system.ui_scale` (1 when background Blender reports 0), never the
pixel size times the resolution scale, which would count that scale twice.
Drawing reads only the controller's cached state: no file, store or session
access, decode or property write. The icon is built in the controller's timer,
outside drawing.

Installed exact-ZIP tests on Blender 5.1.2 macOS arm64 cover the descriptor
rows and their operators, recording-layout draws with file and store reads
refused, the UI-scale sizing, measured wrapping in a 561 px sidebar at an
interface scale of 2 (UI scale 1.0 on a Retina display), in a sized popup and
in Studio's first draw before its popup is sized, the Jobs panel drawing the
box, and an offline waveform decoded by the real child Blender. Installed tests
on Blender 5.0.1, 5.1.2 and 5.2.1 macOS arm64 check the audio rows, Stop audio
replacing Play audio while a child prepares and plays and only for that result,
and recording-layout and Jobs panel draws that refuse to start, poll or stop a
child, with real children and a recording output instead of a sound device.

Two private physical desktop passes on Blender 5.1.2 macOS arm64 at UI scale
1.0, with system mouse and key events and a ZIP built before the measured
wrapping, showed the rows in the sidebar Jobs panel and on the Studio Jobs page.
**Play video** opened the system player; **Play in Blender** opened Blender's
animation player, which Escape closed with Blender's window frontmost again; and
**Waveform** went from its loading lines to the summary and envelope icon. A
screenshot from the second pass sets the waveform text inset. A third pass, on
a ZIP with the measured wrapping but before its later Studio popup width and
hyphen amendments, drew the sidebar rows at UI scales 1.0 and 2.0, with a
1122 px sidebar at 2.0. **Waveform** reached its summary and envelope icon at
both scales; the loading lines wrapped inside the box, and the summary kept
"1.00 s" whole and ended its first line with a `·` separator. The wrapped lines
started at the box's left edge rather than under the first line's text. All
three passes predate the credential-free system player launch: **Play video**
then went through Blender's `wm.path_open`, which runs the same macOS `open`
command with Blender's whole environment. A later private check with
Environment credentials and a ZIP with that launch confirmed that **Play video**
opened the system player on the private read-only copy, whose SHA-256 matched
the saved result, and that **Play in Blender** started a player whose
environment had no `SCENARIO_`, `PYTHON` or proxy names. On 2026-10-13 the
maintainer listened through **Play audio** on the 0.10.0 candidate ZIP, when it
opened the system player (see the
[human checks](../maintenance/release-acceptance.md#human-checks)). These passes
predate the in-extension **Play audio**, **Stop audio** and **Open in system
player** rows. Clicking them, listening through them, the waveform's Cancel,
Retry and close on the desktop, clicking **Play video** or **Play in Blender**
at UI scale 2.0, Studio at UI scale 2.0, viewport interaction and other
platforms are not yet recorded; they continue in #440.

## Saved result previews

Shared saved jobs whose results are downloaded show preview tiles above their
saved-job controls, in Jobs, Generations and the Studio pages that reuse them:
four at a time, primary results first (video, audio, embedded model, base
color or untagged image), then other maps. Each tile is the preview icon or,
until it is ready, the result kind's built-in icon at the same size, with a
caption that starts with the number of its saved-job controls: **(1) Video**,
**(3) Albedo**. Blender keeps the start of a caption that does not fit, so a
narrow sidebar clips the role before the number. Below the tiles, one
information line per distinct status names the waiting tiles as the controls
do, such as **Video (1)**: preparing, waiting for Scenario, or `Preview paused
while online access is disabled`. When the scheduler refuses a request, for
example at its limit of pending previews, the tiles it left without a preview
show its reason on that line until the request is made again, while ready
tiles and the page stay. A missing, failed or unreadable preview gets
an `ERROR` line with its reason and an explicit **Retry preview (N)** button;
nothing retries after the polling window without that click. Previews are
read-only and open no confirmation; Retry's tooltip says nothing is generated,
applied, spent or changed in the scene. Shared image rows no longer load a
draw-time thumbnail.

Blender panels cannot scroll sideways, so a job with more than four previewable
results gets a pager instead of a fifth tile: a **‹** button before the tiles,
a **›** button after them, each one icon scale step wide and as tall as the
tiles' images, and a range line such as `3-6 of 6` under the row. The range uses
a hyphen-minus. A page steps by four and the last page ends at the last result,
so it is always full: six results show 1-4, then 3-6. An arrow at its end stays
in place, drawn disabled, so the tiles never shift. The arrows run an operator
that changes the job's page offset, transient state of the preview controller
that is never saved with the file and never written while drawing; a changed
set of previewable results, a connection, project or file change, or the job
leaving the first rows each panel draws starts it on its first page again. Only
the drawn page's previews are requested; paging requests the next page at once
and shows its icons as the timer builds them. Jobs, Generations and the Studio
Jobs and Results pages draw the same pager, because they draw the same tiles.

Icon size follows the drawn region's width, or in Studio the width it requests
for its popup, divided by `preferences.system.ui_scale` (1 when background
Blender reports 0). An unpaged row's icons scale from 2.5 to 5 with that
width. An icon scale step is 20 interface units. Blender estimates a caption
10 steps wide and narrows every item of a row wider than its region, which
narrows the images but neither the icons' cells nor the arrows. A paged row
therefore sets each tile's column to its share of the row beside its arrows,
with Blender's 8 units between a row's items, and its icons take that share,
from 1.75 up to 5. Each arrow is one step wide. Blender stretches a row's items
with the row, and Studio's row, its requested width less the job box's 10
units, is wider than a row sized with 60 units of margins: it stretched the
arrows to about 1.2 steps at UI scale 2.0. Each arrow's button therefore sits in
a sublayout aligned `CENTER`, which Blender treats as fixed size, so such a row
gives its room to spare to the tiles and keeps the arrows one step wide at every
UI scale. Blender 5.0 to 5.2 draw a preview image 0.15 steps inside
every edge of its icon (`PREVIEW_PAD`), so each arrow starts 0.15 steps below
the row's top, after a separator, and is 0.3 steps shorter than the icon: as
tall as the image. At 1.75 a caption's **(99)…** still fits under its icon. A
region narrower than a row of those smallest icons still narrows it, and there
the images are shorter than the arrows.
Status lines wrap like the waveform box's, by the width Blender's interface
font measures less the sidebar's panel, job box and icon space, and a caption
such as **Model (2)** stays whole on one line. Drawing
reads only the controller's precomputed tiles and pages: no file, store or
session access, decode, request or property write. Requests, image decoding and icons happen in
the controller's timer, outside drawing.

Installed exact-ZIP tests on Blender 5.0.1, 5.1.2 and 5.2.1 macOS arm64 draw
tiles through recording layouts with file and store reads refused, and through
the real Jobs panel. They check that each caption starts with its number and
that the number with an ellipsis fits the tile's icon width in Blender's UI
font. On Blender 5.1.2 they also measure the status lines in a 561 px sidebar
at an interface scale of 2, in a sized popup and in Studio's first draw before
its popup is sized, where the icons also size from the requested width. Pager
tests on Blender 5.1.2 page six maps forward and back, clamp at both ends,
refuse a stale connection or job, start again on the first page after the
results change or the connection retires, find the same arrows and range line
in Jobs, Generations and Studio, and draw a paged row with file and store reads
refused and no change to tiles, pages, offsets or requests.

A private physical desktop check of the pager on Blender 5.1.2 macOS arm64, at
UI scales 1.0 and 2.0, found paging, the disabled arrow at each end, the range
line, no pager for four results and viewport input after an arrow click
working in Jobs, Generations and Studio. In the sidebar at its default width,
280.5 interface units at both scales, the arrows were about 1.65 times as tall
as the icons, because the paged row's icon scale was raised to 2.5 and the row
overflowed; in Studio and in a widened sidebar they matched. Its recheck, with
the arrows as tall as the icons, found them as tall as the icons' cells, 78
and 156 px, but about 1.3 times as tall as the images, 60 and 119 px: Blender
had inset each image and narrowed the tile columns, sized for their captions,
below their icons. Blender 5.1.2's row layout and preview inset give exactly
those images, the unpaged row's 85 and 170 px, and Studio's 188 and 177 px.
Paged rows now size their columns and arrows as described above. Native tests
check, in the sidebar at that width and in Studio, at interface scales 2 and 4,
that a paged row's columns fill the row and are at least as wide as its icons,
that each arrow starts 0.15 steps down and is as tall as the image, that a
narrower sidebar takes the 1.75 floor, and that unpaged rows keep their icon
scale. A physical recheck on 2026-10-13 found the arrows within 0 to 1 px of
the images at UI scales 1.0 and 2.0, and Studio's arrows about 1.2 steps wide at
UI scale 2.0, wider than the intended step.

The arrows now keep that step, as described above. Native tests on Blender
5.0.1, 5.1.2 and 5.2.1 check, at interface scales 1, 1.5, 2, 3 and 4 (UI scales
1.0, 1.5 and 2.0 on standard and Retina displays), in Studio at 329, 640 and
960 units and in sidebars of 300 and 430 units, that the arrows are 20
interface units wide in a sublayout that is not expanded, and that the same
width in interface units draws the same row at every scale. A model of
Blender's row layout, `LayoutRow::resolve_impl` stated in the tests, keeps
those 20 units in rows up to Studio's full request and gives the room to spare
to the tiles, while it stretches arrows in an expanded layout to about 1.2
steps at 329 units. A narrow Studio, at 240 and 160 units, keeps one-step
arrows beside tiles of 1.75, and the arrows still span the images in pixels at
UI scales 1.0, 1.5 and 2.0. Run against the earlier `result_previews.py` on
Blender 5.1.2, these width checks fail. An offline GUI probe of the exact ZIP
on Blender 5.1.2 macOS arm64, in an isolated profile and with no input, drew
the earlier and the new paged rows in a Properties editor layout as wide as a
Studio request, around the job box Studio draws, not in Studio's popup itself.
Measured in its screenshots, the earlier arrows were 1.21 and 1.23 steps wide
at UI scale 2.0 and 329 units, and 1.05 and 1.08 at UI scale 1.0 and 960 units;
the new ones were exactly one step in both, as tall as before. A row sized for
the default sidebar drew about one step before and after. The physical recheck
of the arrows' width in Studio and the sidebar at UI scales 1.0, 1.5 and 2.0 is
pending.

Two private physical desktop passes on Blender 5.1.2 macOS arm64 at UI scale
1.0, with system mouse and wheel events and ZIPs built before the measured
status wrapping, showed tiles going from their placeholder and status line to
ready in Jobs, and ready tiles in Generations and on the Studio Jobs and Results
pages. Wheel scrolling the sidebar and the Studio Jobs page kept the tiles in
place, and the next clicks hit the intended controls. The second pass read
number-first captions in the sidebar at its default width, where the 3D job's
two clipped model captions kept their numbers, and a screenshot from it sets the
status line inset. A third pass, on a ZIP with the measured status wrapping
but before its later Studio popup width and hyphen amendments, ran in the
sidebar at UI scales 1.0 and 2.0, with a 1122 px sidebar at 2.0. When a cached
still was removed while its preview was being prepared, the tile kept its kind
icon with an `ERROR` line, wrapped and aligned after the icon, and **Retry
preview (N)**, which prepared the preview again: for a video's still and a
decoded image at 1.0, and for the video at 2.0. At 2.0, four tiles fit one row
with their numbers visible, and status, error and grouped loading lines wrapped
inside the box with captions whole. Stills removed while idle were prepared
again after a file reload, without **Retry preview (N)**. Online Access
disabled, focus and viewport interaction while tiles update, the Studio pages
at UI scale 2.0 and other platforms are not yet recorded; they continue in
#440.

## Saved material approval

**Apply saved material** names the scene, mesh, slot and saved texture roles.
Explain packing, replacement of that slot only, height-as-bump, retained AO/edge
nodes and the absence of a global undo entry. Preparation requires a local UV
mesh with one object user in one scene; drawing reads the prepared snapshot and
never opens files or changes assignments. Cancel discards its approval. A changed
mesh, slot or scene revision requires a new review rather than retargeting.

Offline desktop acceptance on macOS arm64 Blender 5.1.2 used exact ZIP
`b30a62ff2864507ad62dfd8eb743d3b17a17e7487bf1c94185072efcff405f67`,
which passes 607 full native tests on each of Blender 5.0.1, 5.1.2 and 5.2.1.
Mouse confirmation applied one synthetic saved albedo map as a packed material
to the approved mesh slot. The job became applied with mock submission/request
counts unchanged. Material preview, viewport and Outliner selection, front-view
and orbit keyboard input, zoom and material-property inspection worked afterward.
Blender exited cleanly and the normal-profile fingerprint was unchanged. The
test-only app copy used a distinct bundle identifier and local ad-hoc signature;
vendor executable code and extension ZIP were unchanged. The earlier attempt
could not access the window; it was not counted as interaction evidence.
This check does not establish live provider quality, other OS desktop support
or full Materials acceptance. See the
[user guide](../USER_GUIDE.md#historical-upstream-reference) for the approval and result screenshots.


## Completed-result reuse approval

Completed shared jobs show **Reuse saved results** above the supported saved
application actions. Each native approval adds **Use saved results again; no new
generation.** while retaining its destination, selected assets and limitations.
Drawing uses the prepared snapshot only. Unfinished local claims suppress new
application actions; a known receipt failure offers persistence-only recovery.

Offline desktop acceptance on macOS arm64 Blender 5.1.2 used exact ZIP
`28d7dced697249cf9389ddbc0c7bb300d99faa66623c0ffc0caaf2303ba1e6eb`,
which passes 619 full native tests on each of Blender 5.0.1, 5.1.2 and 5.2.1.
An already imported synthetic albedo result was applied as a packed material to
the approved mesh slot through the visible reuse confirmation. The original job
stayed applied, its separate material application completed, and mocked request
and submission counts did not increase. Material preview, viewport and Outliner
selection, front-view keyboard input, zoom and material-property inspection
worked. Blender exited cleanly with the normal-profile fingerprint unchanged.
The test-only app copy had a distinct bundle identifier and local ad-hoc signature;
vendor executable code and extension ZIP were unchanged. See the
[user guide screenshots](../USER_GUIDE.md#historical-upstream-reference). This does not establish
live provider quality, other OS desktop support or release acceptance. An
earlier window-targeting attempt was not counted as interaction proof.

## Saved image first-frame handoff

A saved job shows **Use as video first frame (N)** for each downloaded PNG,
JPEG or WebP colour result that the shared owner accepts; numbering counts every
saved asset, as for other per-asset controls. Normal, height and other texture
maps, EXR files and results that are not downloaded get no button, and the
button is hidden, never drawn disabled, while the job has other work pending.
The 520 px confirmation titled **Use as video first frame** names the scene,
the Render Video form, model, input and saved image. When the model can't use an
exact first frame with the scene clip, or has no first-frame input, two lines
under the input say so and that the image is sent as image 1 of that input, as
the form does under **First frame** once a frame is chosen. It states that the saved
Scenario asset is reused with nothing uploaded, no new generation and no file
path stored in the blend, and lists which chosen first-frame file it replaces
and whether it turns the first frame on only when they apply. It says the Render
Video and Prompt Spark prices become invalid and that removing the slot undoes
the change. **Use as first frame** confirms; Cancel discards the approval.
Refusals report their reason as a Blender error instead of opening the
dialog. Invoke computes every line; drawing only displays them.

The binding is asynchronous: confirming queues verification and shows
**Verifying the saved image for the Render Video first frame**. The form then
shows the slot, labelled with the image name and `(saved result)`, with its
Remove button and a read-only **From a saved result** line. There is no
thumbnail because no file path is kept. Do not switch the active lane from the
pump; the message names Render Video. Drawing that line reads only the slot's
provenance.

Installed tests on macOS arm64 Blender 5.1.2 cover the per-asset buttons drawn
through the shared descriptors without store reads, the exact dialog lines with
fixed lines within the 59 characters verified for a 520 px dialog, cancellation,
refusal reasons, single use of an MCP-prepared approval through the operator,
the provenance line and memfile Undo/Redo of the binding in the runner's window.
Desktop passes on pre-merge #377 and #379 heads on Blender 5.1.2, at UI scales
1.0 and 2.0, checked the button, its confirmations, Esc, focus and one undo step
with redo (see the
[release evidence](../maintenance/release-acceptance.md#render-image-and-render-video)).
Small-window evidence is not recorded, and Blender 5.0 and 5.2 continue in #440.
