# Environment, panorama and World

Interaction rules and desktop evidence for the Environment lane and World application. The shared rules are in the [UI style guide](../UI_STYLE.md).

## Environment lane

**Environment** is appended to the lane tabs, in the Audio / Materials row; a
saved blend stores a tab as its item index, so new tabs are only appended. Its
form starts with a segmented **Generate / From image / HDRI / Upscale** control
and a wrapped line describing the mode, then follows the usual order: Model,
Prompt (when the model has one), References, Settings, then Generate. The model
picker and dropdown list only the mode's verified panorama contract models; a
model left from another mode is not priced. GPT's size is a **Panorama size**
dropdown of its 2:1 presets instead of free width and height. Contract-forced
settings are drawn greyed, with **Greyed settings are fixed by the Environment
lane** below them. A result line never calls an LDR result HDR: **Result: LDR
2:1 panorama, saved until you set it as World** or **Result: HDR OpenEXR
environment, saved until you set it as World**.

An input that takes a 360 panorama offers **Saved panorama** in its header
instead of **Saved uploads**, and only **Upload** as a source, with **A saved
360 result or a 2:1 PNG/JPEG** below. Its references never offer **Use saved
upload**; a capture left on such an input shows a remedy instead of an upload
button. **Use saved panorama** lists the selected connection's downloaded LDR
360 results in a dialog; confirming attaches one and invalidates the price.
Still inputs (GPT references, HY World's image, upscale style images) keep the
ordinary image sources.

Saved-job controls label a server-declared 360 result **360 panorama (LDR)**,
**360 HDR environment (OpenEXR)** or **360 panorama and HDR environment**, keep
**Set panorama as World (N)**, and offer **Make HDRI (N)** on settled jobs with a
downloaded LDR panorama. Make HDRI only opens the HDRI form with that reference;
it never prices, generates or changes a World. A material map, such as a 3D
package's PBR maps or a texture set, is never a World candidate, a Make HDRI
source or a **Use saved panorama** choice. The floating composer shows an
**Environment** tab (**Env** when its full label does not fit) that drives the
Generate mode; choosing it selects that mode, and while the sidebar shows
another Environment mode the composer drives Image. As on every tab, its
**Settings** chip commits focused text to the original form before the dialog
opens, so a mode change there cannot retarget that text; a form that changed
under the text refuses to open and keeps both texts. Make HDRI and Reload
relist the target mode's models even when the scene already shows that mode,
because the list is shared by scenes. Reload attaches a panorama input's
recorded asset as a saved panorama only while the selected connection still
holds it as a downloaded LDR 360 result; any other asset ID is restored bare and
refused until a panorama is chosen again. A saved job's local files do not name
their input, so Reload never puts one on a panorama input; they fill only still
inputs such as Upscale's style images. Mode, size and model changes happen in
property callbacks or operators; drawing never writes. Contract values and the
recorded model choice that a catalog load fills in are stored without RNA
updates, like seeded schema defaults, so they keep captured origins current.

Installed tests cover the appended tab values through a saved and reopened
blend, mode model lists and price invalidation, contract values, read-only
drawing of every mode, picker restriction, Quick settings, Reload, the composer
tab, the panorama upload preflight, material maps kept out of every Environment
and World control, and a fake service quote, submission, saved result, restart,
Make HDRI and World approval in a newly selected scene. A physical desktop pass
before the lane merged, with free quotes only, covered the tab, modes, settings
and result lines, World application and restore of saved PNG, OpenEXR and JPEG
panoramas, and Esc and viewport keys after each dialog. The recheck of the
merged lane, including its composer tab row, is tracked in #440 (see the
[release evidence](../maintenance/release-acceptance.md#panorama-and-world)).
No screenshot is committed.

## Saved panorama approval and restoration

**Set panorama as World (N)** prepares one PNG, JPEG or OpenEXR result and
identifies the selected scene and current World. A **Selected** line states the
saved media type's declared format in the same wording as MCP `format`.
Invoke computes that text; drawing only displays it. Explain the
2:1/equirectangular requirement, packed image, preserved original World, LDR/HDR
distinction and session-local restore limit. The
[World guide](../WORLD_APPLICATION.md#saved-result-ui-and-mcp-approval) defines
which results offer the action and how N counts them. Drawing reads only the
saved-job projection's candidate list, never a file, a size or the job store;
the application check of the actual bytes remains the authority. Cancel
discards the prepared handle.

After completed application, **Restore previous World** uses a separate prepared
approval. It names the current scene/World and explains that edited owned data
prevents restoration. Do not silently clear edits, reapply the job or reset its
completed state after restoration. Reference [the user guide](../USER_GUIDE.md#historical-upstream-reference)
for screenshots and [the World guide](../WORLD_APPLICATION.md#saved-result-ui-and-mcp-approval)
for reuse and file-load restoration boundaries.

Offline desktop acceptance on macOS arm64 Blender 5.1.2 used exact ZIP
`c570617fdcc2e4ff5e5b424e08768843186a38a95eb6e58252cec6c471b262f1`.
Mouse approval applied a synthetic saved 2:1 PNG as one packed World; a separate
confirmation restored the original World and removed the restore action while
the job stayed applied. Mock submissions and request counts remained unchanged.
Viewport selection, front-view keyboard input and zoom worked afterward. Blender
exited cleanly and the normal-profile fingerprint was unchanged. The test-only
app copy used a distinct bundle identifier and local ad-hoc signature for window
targeting; vendor executable code and the extension ZIP were unchanged. This does
not establish actual HDR/seam quality, other OS desktop support or release
acceptance. That capture predates the **Selected** format line and the JPEG and
`image/aces` offers; headless native tests cover their invoke, draw and cancel
handling, and the Environment lane's desktop pass above applied JPEG and OpenEXR
panoramas through these dialogs.

## World restoration across scenes

Reusing a panorama in another scene preserves the earlier scene's
**Restore previous World** action. Each review still identifies the selected
scene and its current World; restoration changes only that scene. The
[World contract](../WORLD_APPLICATION.md#saved-result-ui-and-mcp-approval) describes
identity, ownership checks and session limits.

An isolated macOS arm64 Blender 5.1.2 desktop check used exact ZIP
`8676574595eb6fcc21cee719467184bc87c6d3f73a9986741edc643749f2e9dc`,
which passed 692 installed native tests on each of Blender 5.0.1, 5.1.2 and 5.2.1.
The synthetic fixture applied one saved panorama to two scenes. Native input
opened the first restoration review and cancelled without mutation; a fresh
confirmation restored its original World while preserving the second panorama.
The native scene selector switched to the second destination; its separate
review restored that scene's original World. Viewport focus and front-view
keyboard input worked afterward. The saved generation and local claim stayed
applied, and mock submission/request/download counts did not change. The
process exited cleanly, with no network attempts and an unchanged normal profile.
This is synthetic local restoration proof, not live panorama quality, other-OS
desktop or integrated release acceptance. Catalog loading text is fixture state.

A subsequent lifecycle correction was checked with exact ZIP
`24c7731dc6f339d55f826dfd27e68f0b90052b2c987fe9572431d5dbe6c353ca`:
694 installed native tests passed on each of the same three Blender versions.
The scene identity survives the render-thread origin revision reset; Undo/Redo
still retires live ownership, and deleted or retired destinations no longer
offer restoration. Regression tests invoke the actual render-thread and history
callbacks, verify rejection of stale approvals and prune expired handles.
They do not establish physical desktop Undo/Redo behavior.
The isolated 5.1.2 desktop flow above was repeated after the fixture invoked
the render-thread callback: cancel, independent confirmations, native scene
selection and viewport keyboard input all worked. Both original Worlds were
restored with unchanged applied records and mock counts, no network attempts,
clean exit and an unchanged normal profile. The screenshots below retain the
earlier artifact's provenance.

![Separate World restoration review for the second scene](../images/world-scene-restore-approval.png)

![Restoration finished after each scene recovered its original World](../images/world-scenes-restored.png)
