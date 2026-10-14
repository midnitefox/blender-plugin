# Known limitations

This page distinguishes inspected code limitations from unverified service or
platform behavior. See the [audit](maintenance/backlog.md) for the disposition
of older reports and the [runtime map](architecture/runtime.md) for integration
boundaries. No paid or live acceptance is implied by this documentation.

An offline Render Image desktop probe on macOS arm64 / Blender 5.0.1 reached
a ready quote, accepted text edits, submitted once through a synthetic transport
and retained viewport navigation. It later crashed with the native traceback in
a background SQLite job read. Subsequent diagnostic polling, screenshot and
144-second native interaction probes on that same ZIP completed cleanly, including
shutdown and normal-profile preservation.
[#263](https://github.com/scenario-labs/blender-plugin/issues/263) was closed on
2026-10-04; its public record does not document a root cause or verified fix.
The historical failure and clean reruns are evidence limits, not a claim that
the current candidate reproduces the crash. The 0.10.0 build later ran 591 s of
Sequencer playback on macOS without errors (see the
[release evidence](maintenance/release-acceptance.md#audio)), and the desktop
checks still open are in [#440](https://github.com/scenario-labs/blender-plugin/issues/440);
synthetic probes and passing headless tests do not establish live generation or
full render-lane desktop acceptance.

## Runtime and authentication

- The Image lane now shares SDK quotes, durable submission, reference uploads
  and result/recovery commands between UI and local MCP. All MCP `generate`
  lanes now use shared quotes, durable submission and downloads; only Image
  automatically imports results. Native form generation now uses those shared
  quotes and jobs too. Files, mesh/clip captures and render scene/first-frame inputs
  use explicit uploads before the final quote. The native **Use as video first
  frame** control and local MCP can instead hand a downloaded saved PNG, JPEG or
  WebP result to the Render Video first frame by reusing its asset ID. A free
  dry run resolves that asset ID (an unknown ID returns HTTP 404) but does not
  check which inputs may be combined: on 2026-10-10 it priced a Seedance 2.0 Fast
  first frame sent with the scene clip. The service accepts that submission, then
  the job fails because an image and reference videos aren't allowed together
  (the service reimburses failed generation jobs). Seedance 2.x, Minimax H3 and
  Wan 3.0 state that limit only in input description text. Their first frame is
  now sent as image 1 of the reference images with the clip, and every shared
  quote path refuses a first frame sent with reference images or videos on these
  models (see the input-exclusivity limits below). On 2026-10-10 one paid
  Seedance 2.0 Fast job sent a handed-off frame this way from local MCP and
  succeeded, on a build that preceded the merged control. No Minimax H3 or Wan
  3.0 job has run, and how closely a clip opens on its image has not been
  reviewed. Seedance 2.0 Mini's first-frame exclusivity is a known service-side
  issue, already reported to the Scenario backend team. A physical desktop pass
  on macOS arm64 Blender 5.1.2, at UI scales 1.0 and 2.0 on pre-merge builds,
  confirmed, bound and removed a handed-off frame and undid and redid the binding
  as one step. A later pass on the merged build at UI scale 1.0 handed off a
  saved result, captured and uploaded the scene clip and reached a ready Minimax
  H3 quote whose request sends the frame as reference image 1 and the clip as a
  reference video; that quote was not submitted. Blender 5.0 and 5.2 have no
  desktop evidence. The handoff
  stores no file path, so the blend shows no first-frame thumbnail. Render forms
  accept a written look or the default look with automatic Spark disabled. An
  empty automatic look requires its own exact Spark quote, approval and guarded
  result delivery before
  the final render quote. Film task quotes, approvals and saved-upload associations
  now share these jobs through native controls and MCP. Film shot construction
  uses separate saved-model verification and build approval. Synthetic desktop
  shot interaction, including Undo/Redo, passes on macOS Blender 5.1.2; live
  provider, other OS/DPI and motion/audio acceptance remain separate. Local
  timeline assembly requires explicit selection of completed shot scenes and
  confirmation; it creates editable scene strips without generation or rendering.
  Synthetic timeline selection, cancellation, Undo/Redo and Sequencer inspection
  pass on macOS Blender 5.1.2; see [the evidence and limits](ui/film.md#film-timeline-controls).
  Local capture now shares explicit native/MCP approval, cancellation and separate
  byte-checked upload. Capture reviews/files are session-local. Desktop still
  capture, cancellation of approval dialogs, synthetic upload and cleanup pass on
  macOS Blender 5.1.2; see [the evidence and limits](ui/film.md#film-capture-controls).
  Desktop video cancellation, live uploads, other OS/DPI and sustained media
  acceptance remain pending. Composition preparation, exact pricing and separate
  generation approval now share native/MCP controls and preserve saved masters
  after restart. Synthetic composition approval, mode navigation, invalidation and
  saved-job inspection pass on macOS Blender 5.1.2; see
  [the evidence and limits](ui/film.md#film-composition-controls). Live provider
  acceptance and other OS/DPI behavior remain pending. Native/MCP review assembly
  now prepares private saved-media copies and separately approves a new review
  scene with application claims. Beyond installed synthetic tests, one
  end-to-end production run through local MCP built reviews from uploaded previz
  clips and generated takes; desktop interaction and human motion/audio review
  remain pending.
  A frame, recipe or object edit, object selection, an Undo or Redo, building
  another review for the same scene, or leaving the recipe scene and selecting it
  again, before building deletes the copies and requires a fresh preparation;
  Undo after a build keeps them. See
  [review controls](FILM_PLAN.md#native-and-mcp-review-controls). Native/MCP
  [video export controls](FILM_PLAN.md#native-and-mcp-video-export-controls) render
  a built review scene to a new MP4 after a destination choice and a separate
  render approval; agents may write only inside the Output Folder. They have
  installed synthetic coverage and that local MCP run, which exported a 20 s
  1080p MP4 with audio; desktop interaction and human playback review remain
  pending. A ready export is invalidated by the same scene changes as a
  review, and one export runs at a time. An edit made to the review scene without
  showing it, for example from a script, is noticed only at approval and only when
  it changes the frames, rate, size, strip or media counts or audible sound shown.
  A [portable case-study bundle](FILM_PLAN.md#portable-case-study-bundle) packs a
  built review, an optional timeline with its shots, their media, the sanitized
  recipe and a production ledger into a new ZIP; it shares that thread, refuses
  linked data (including the Essentials brushes a scene links once a sculpt or
  paint mode was used in it), movie clips, packed sound strip audio, image
  sequences and other unportable data, keeps model IDs in the recipe and output-only path
  settings such as strip proxy folders, and cannot clear Blender's unused bytes
  of earlier settings, so review a bundle before
  publishing it widely. It notices an edit of its unshown scenes only through the
  review's frames, rate, size and strip count, the scene and media counts and bytes
  and the ledger. It has installed synthetic coverage and the bundle of that
  local MCP run. Video export
  verification uses an installed ffprobe when present and otherwise a weaker
  Blender decode check. Film finds `ffmpeg` and `ffprobe` on Blender's PATH and,
  on macOS and Linux, in fixed standard install folders
  ([lookup](STUDIO_ADOPTION.md#external-media-tools)); no setting selects another
  tool location. Blender builds without H.264/AAC encoders cannot export;
  they are detected only when the render child fails, with its log retained.
  The desktop rerun of export and the case-study save, human playback review
  and the criteria for lifting Film's experimental status continue in
  [#440](https://github.com/scenario-labs/blender-plugin/issues/440).
- Film captures and exports only the scenes it builds: capture takes one
  matching shot scene per recipe shot and export takes a built review scene, so
  an artist-built multi-shot scene, such as one timeline whose cameras are bound
  to markers, cannot be captured or exported as it is. A Film task takes its
  media only from an upload or a Film model task, not from a saved result of the
  generation lanes: such a result has to be uploaded again as a new asset, and
  the case-study ledger then lists it as an upload without its generation cost.
  The Render Video lane takes its motion reference only from a new camera or
  viewport capture, not from an already-rendered video such as a previz render.
  The end-to-end production run met all three;
  [#440](https://github.com/scenario-labs/blender-plugin/issues/440) tracks them.
- Several Film versions in one production are awkward to manage. Each final
  review build adds a scene named after the recipe title,
  `<title> / Final Review`, to which Blender appends `.001`, `.002` and so on;
  the name does not say which version the scene holds. Export and case-study
  sources list only the review scenes of the recipe currently loaded, so
  exporting another version needs its recipe loaded again first. A case-study
  ZIP's root folder comes from the recipe title alone, so case studies of
  versions that share a title collide when extracted side by side. A second
  end-to-end run with two versions met all three;
  [#440](https://github.com/scenario-labs/blender-plugin/issues/440) tracks them.
- Local MCP `job_status` lists `files` only for image results imported
  automatically in the current session. A ready video, an image not imported
  automatically, other result kinds and every job after a restart list none;
  `results` still names each saved asset, its media type and whether it was
  downloaded, without a local path.
- Credentials use one explicitly selected pair (saved Blender preferences by
  default, environment only when selected). UI and MCP model jobs use credential-bound
  local storage. Preferences also allow an explicit optional project ID; changing
  it retires approvals and selects separate saved jobs. Blank uses the API key's
  default scope without discovering its identity. Live project permission and
  cross-project service acceptance continue in #440; browser sign-in in
  [#67](https://github.com/scenario-labs/blender-plugin/issues/67) is deferred.
- An uncertain submission is not reconciled automatically. Its saved job stays
  unconfirmed, and an identical request is refused until the user marks it
  reviewed in Blender for the session
  ([identical requests](JOB_COORDINATOR.md#identical-unconfirmed-requests)). If a
  model job completed, recover it from cloud history with **Save for recovery**
  (local MCP: `recover_cloud_job`) instead of generating again. Uncertain workflow
  and prompt-helper submissions have no in-Blender recovery; check their outcome
  in the Scenario web app, whose display of such jobs has not been checked.
- Remote cancellation is offered only for jobs Scenario reports as `inference`,
  the only type its job action reference documents as cancellable. Captured
  model generations report `custom`, so Blender does not cancel them; they
  finish or fail on Scenario and stay recoverable. Every job of a paid desktop
  pass reported `custom`, or `workflow` for a workflow. Which lanes return
  `inference` and live cancellation acceptance continue in
  [#440](https://github.com/scenario-labs/blender-plugin/issues/440).
- Existing trained LoRAs and compositions are offered through the model
  picker's Trained chip and local MCP; training is out of scope. A private
  composition, the desktop recheck of project defaults and restart persistence,
  and whether lanes beyond Image and Render Image should offer trained LoRAs
  continue in [#440](https://github.com/scenario-labs/blender-plugin/issues/440).
- Remote job progress is advisory and kept only in memory. Providers may report
  no fraction, or 0 until completion, so a job may show only `generating`; a
  fraction below 1% shows no bar, and no time estimate is given. Readings and the submitting scene/lane binding are not
  persisted: after a restart, progress appears only after an explicit refresh or
  resume, and recovered jobs remain unbound. In paid desktop passes, TripoSplat
  and Tencent UV Unwrapping reported several intermediate fractions, GPT Image 2
  only 3%, and no job reported a queued state; other providers are unobserved.
  At UI scale 1.0 the composer's strip and collapsed pill filled to the reported
  fraction over a dim track. The composer bar at UI scale 2.0 and the Jobs rows'
  bars still need desktop evidence
  ([#440](https://github.com/scenario-labs/blender-plugin/issues/440)). See [the projection rules](BLENDER_JOB_CONTEXT.md#remote-progress-and-scene-lane-binding).
- Scenario cannot cancel a running workflow step: rejecting a waiting approval
  or selection step (**Stop workflow**, or MCP `decide_workflow_step` `reject`)
  is the only way to stop a workflow, and completed steps stay charged. Steps
  inside a nested workflow node are not shown. A selection that feeds a ForEach
  loop, a nested workflow or a node type the API reference does not list can
  only be rejected until live evidence shows its charge stays within the
  approved price. So can any selection in a workflow with such a node when the
  links between its steps cannot be read from the job or the workflow
  definition; an approval then warns that its review assets may be incomplete.
  Review assets and candidates are asset IDs or text until
  **Load previews** fetches private previews: a PNG, JPEG or WebP asset larger
  than 8 MiB or 4096 pixels per side, or another kind of asset, is previewed
  only through a server thumbnail Scenario offers, and MCP returns no preview.
  The native dialog shows at most 12 wrapped lines of each text candidate.
  A paid desktop pass on macOS arm64 Blender 5.1.2 at UI scale 1.0 reviewed one
  private test workflow: it loaded the candidates' previews, chose 2 of 3,
  approved a step and stopped the workflow at a later approval, with only the
  step that had run charged. A decision sent from a dialog after its step was
  decided elsewhere was refused with *The workflow step changed; review it
  again*. Concurrent decisions, omitted project IDs, rejection inside a loop,
  the review assets of other node types and UI scale 2.0 are unverified
  ([#440](https://github.com/scenario-labs/blender-plugin/issues/440)). See
  [waiting workflow steps](ui/studio-and-library.md#waiting-workflow-steps).
- Installing 0.10.0 from the official extension repository was verified on macOS
  with Blender 5.0.1, 5.1.2 and 5.2.1. 0.10.0 is the first release the repository
  offers, so an update from one published release to the next, keeping
  preferences and stored jobs, will be verified with the next release (see the
  [user guide](USER_GUIDE.md#historical-upstream-reference)).

## Creation and scene application

- MCP Render Image/Video now share native form preparation, uploaded references,
  prompt decoration and exact approvals. Paid Render Image and Render Video jobs
  ran through local MCP for 0.10.0 (see the
  [release evidence](maintenance/release-acceptance.md#render-image-and-render-video));
  first-frame handoff and clip capture on Blender 5.0 and 5.2 continue in #440.
  Scene captures require a GUI.
- Input combinations are checked only where model descriptions state them. No
  schema field marks inputs that cannot be sent together, and a free dry run
  prices such a body. In every lane, the shared quote path refuses two file
  inputs when one's description says it can't be combined with the other. On
  2026-10-10 that matched 11 of 709 public models: first or last frames with
  reference inputs on Seedance 2.5, 2.0 Fast and 2.0 Mini, Minimax H3 and Wan
  3.0 (Video and Render Video lanes) and on the deprecated Seedance 2.0;
  reference images with an input video on Gemini 3.1 Flash and Nano Banana 2.1
  (Image lane); an image with a video on Wan 2.7 i2v, which no lane lists; and
  audio references with an image reference on BytePlus Seed Audio 1.0 and its
  multilingual version (Audio lane). Differently worded limits are not caught.
  Settings and item counts stated only in text are not checked either, so those
  limits are left to the service, for example: Kling V3 Omni's Generate Audio,
  or its 4K mode, with a reference video, and more than 4 reference images with
  a video; Luma Ray 3.2 Edit's guide frame with keyframes; Meshy's Texture
  Prompt with texture reference images; Tripo v3.0 Texturing's text prompt with
  its image prompt; Recraft V4 Styles' style ID with style reference images; and
  Meshy Smart Topology's prompt with its image. Over the curated models, the
  weekly payload audit fails only on a file input whose wording names no sibling
  input. Recognized exclusivity wording ("mutually exclusive with", "can't be
  combined with") that involves a setting is listed at `MED`; other wording,
  such as Kling V3 Omni's 4K mode and reference-image count, is not reported.
- Reversible mesh and panoramic World application exist as explicit synchronous
  primitives. Full generation/history integration of in-place mesh edits remains
  [#99](https://github.com/scenario-labs/blender-plugin/issues/99); panoramas
  reach World through the Environment lane and saved-result approval.
- The Environment lane (sidebar, Studio, the composer's Generate mode and local
  MCP) generates 360 panoramas, HDRIs and upscales only through verified model
  contracts, and its results wait for explicit World approval. Paid runs
  returned Flux 360, HDRi Skybox, skybox upscale and Skybox GPT results; HY World
  output, seam and pole quality and the measured HDR range still need a live
  run. A physical desktop pass covered the form, free quotes and World
  application before the lane was rebased onto #394; the rebased composer tab
  row, a local panorama file upload and From image beyond its quote still need
  desktop evidence (#440). A panorama input takes a
  saved 360 result or a checked local 2:1 file; local MCP accepts only the saved
  result.
- A saved material result shows **Tiling** whenever the file has a material with
  its derived name, and Tiling then scales that material's Mapping node, even if
  it is an unrelated material. Saved application builds no Mapping node
  ([#485](https://github.com/scenario-labs/blender-plugin/issues/485)).
- Opening an Edit 3D or Environment form whose saved model is no longer in the
  catalog selects the first model, which makes a ready price in that scene stale.
- Multi-object mesh export combines the selection. Safe in-place multi-object
  editing needs separate acceptance under #99.
- Automatic Image-lane import and saved image and material application
  currently accept only PNG and scanline OpenEXR files. World application also
  accepts baseline or progressive JPEG and ACES-labelled OpenEXR panoramas.
  Other image results, such as WebP, or JPEG and ACES-labelled OpenEXR outside
  World, stay saved with the job without import. The World review's format line
  for an ACES-labelled result, *ACES-labelled OpenEXR (float, AP0 as
  ACES2065-1)*, comes from the server's media type. On 2026-10-10 the EXR
  original of a paid HDRi Skybox result declared no primaries, so Blender
  decoded it as Linear Rec.709 under that line; only the file's own declarations
  select the color space (see
  [OpenEXR color primaries](WORLD_APPLICATION.md#openexr-color-primaries)).
- Saved GLB, JSON glTF, FBX, OBJ and mesh PLY results import as a new group, but
  package binding without persisted asset lineage is conservative: several OBJ or
  glTF files, several MTLs or several maps for one slot stay unbound and are
  reported, so such a job imports untextured or not at all. An FBX that references
  external texture files is rejected (Blender's importer may read that file before
  the rollback), and an animated FBX keeps the file's frame rate when the scene
  uses another. ASCII FBX, ksplat, sog, STL and USD results are not imported.
  Snapshot copying and hashing are synchronous on the main thread. A paid
  desktop pass imported the FBX result of Tencent UV Unwrapping, which also
  delivered OBJ alternates; no catalog model was found that delivers glTF JSON,
  and other provider package shapes need live evidence (#440).
- Saved Gaussian splats (SPZ v2/v3, 3DGS PLY and .splat) import as a coloured
  point cloud, not as rendered Gaussian splats: rotations, anisotropic scales,
  view-dependent colour and sorted alpha blending are not reproduced, and files
  denser than the reviewed point limit (at most 2,000,000) are thinned evenly.
  SPZ v4 (ZSTD), the pre-release v1 and compressed splat PLY files cannot be
  decoded; their files are kept. SPZ extension records, including a declared
  coordinate system, are not read; the job row and MCP only flag their presence.
  The default saved axes follow each file format's convention. A live TripoSplat
  SPZ result imported upright with them on a desktop; other providers' worlds
  still need live visual evidence. Choose the other axes in the review when a
  result is not upright. Viewport performance at 1,000,000 or more points is not
  established (#440).
- Prompt helpers require their own exact quote and approval. Unusable Spark
  output fails without an automatic paid LLM fallback; complete text can be
  retrieved again without regeneration. Historical prices are not current quotes.
- Studio workflow file inputs upload image, audio, video and 3D files or
  snapshots only. Inputs declared as other kinds (for example `image-hdr`,
  `document`, `json` or `text`) or with listed allowed asset IDs still need a
  Library selection or a typed asset ID. A snapshot upload that is still sending
  stops for good after a scene edit, frame change, render or undo, and a chosen
  file's after undo or redo; it does not resume, so the content is uploaded again
  (see the [user guide](USER_GUIDE.md#historical-upstream-reference)). That a chosen file keeps
  uploading through a real render or timeline playback was only simulated in
  tests.
  Workflow and generation-form uploads share 128 upload handles; finished
  uploads keep theirs until the credentials or project change, a blend file
  loads (Open, Revert or File > New) or the extension restarts. A paid desktop
  pass at UI scale 1.0 uploaded a local image file and viewport and camera
  captures into workflow inputs; audio, video and 3D uploads and UI scale 2.0
  continue in #440.
- Video-to-motion and speech-to-text workflows remain an explicit capability
  request in [#190](https://github.com/scenario-labs/blender-plugin/issues/190);
  model tags alone do not establish supported input and result handling. Models
  offering `video23d` or `audio2txt` show a visible experimental status in the
  picker, the Model row and `list_models`; the viewport composer's model chip
  does not show it. They can be submitted; provider behavior and result handling
  are not accepted. Generic import by file type may still bring in a returned
  model (GLB, glTF, FBX, OBJ or mesh PLY) or media file.

## Interface and capture

- The composer holds the lane tabs, the prompt and the model chip; the rest of a
  lane's form stays in the sidebar. Its prompt is one line, and multi-line
  editing is [#444](https://github.com/scenario-labs/blender-plugin/issues/444).
  UI scales 1.25 and 1.5 continue in
  [#440](https://github.com/scenario-labs/blender-plugin/issues/440).
- The model picker is rebuilt when the trained list, base descriptions or new
  thumbnails arrive, and Blender drops a click on **OK** that lands on such a
  rebuild without a message: click again. It is no longer rebuilt on every
  maintenance tick, which made **OK** need two or three clicks, but a physical
  check of the single click is pending in
  [#440](https://github.com/scenario-labs/blender-plugin/issues/440). As in any
  Blender dialog, a click outside a field being edited only ends the edit.
- Input methods (IME) cannot compose in the composer prompt. Blender composes
  only in its own text fields and gives add-ons neither the composition text nor
  a Python type for its composition events, so the composer receives raw keys
  (romaji, for example). F2 in the focused prompt opens a native text field
  whose text is added at the caret; a composition starting in the focused prompt
  (two untyped presses without text in a row) opens the same field, so it no
  longer stays pending for the next native field. A lone such press, as a dead
  key sends, opens nothing. The field does not open while another form has
  replaced the focused prompt's or when the form's model takes no prompt, the
  cases where typed keys are refused too, so a composition started then can
  still stay pending. In an input mode that commits each key, the first
  key's text arrives in the second press the composer consumes and is lost.
  Inspected Blender 5.0.1, 5.1.2 and 5.2.1 macOS source ends input-method state
  only after a text edit that received a composition. After another edit, such
  as an Outliner rename cancelled with Esc, selecting an input method can route
  keys to it while no field is editing; by that reading, letter keys then miss
  viewport shortcuts too. Installed tests simulate these events. A physical pass
  on macOS arm64 Blender 5.1.2 with a French layout opened the F2 field at the
  pointer at UI scales 1.0 and 2.0 (at 2.0 it covers the prompt line and its
  caret), typed dead keys directly and, after an Outliner rename cancelled with
  Esc, brought a whole Japanese composition into the prompt without losing
  keys. With the Japanese input source selected by the test driver rather than
  by a person, the F2 field received raw romaji, as Blender's own fields did,
  and a composition there took three Esc or Return presses to cancel or apply.
  On 2026-10-13 the maintainer composed, committed and cancelled with Esc after
  F2 with an input source they selected, on the candidate ZIP on macOS Blender
  5.1.2. Candidate-window placement was not recorded, and other platforms' input
  methods remain unverified; both continue in #440.
- The composer's [job strip](UI_STYLE.md#compact-job-strip) shows only jobs
  submitted from that scene and form in the current Blender session: jobs from
  before a restart, recovered cloud jobs, workflows and Film tasks are not bound
  to a form and stay in Jobs. A viewport too small for the tray hides it. While
  an automatic status refresh runs, its held action answers *try again in a
  moment* instead of dispatching. A paid desktop pass on macOS arm64 Blender
  5.1.2 at UI scale 1.0 followed real jobs through the strip's submitting,
  generating, finishing, downloading, importing, ready, applied and failed
  states, opened the job card and the Reuse and Apply reviews from its chips
  with Esc and viewport focus returning, and dismissed the tray. The service
  never reported a queued state, so Waiting was not seen, and Cancel was not
  offered because no job reported `inference`. After a job is applied, the
  collapsed pill keeps a grey done line until the job is dismissed. UI scale 2.0
  continues in #440.
- Viewport screenshots and OpenGL capture require a GUI. Automated GUI probes
  can take keyboard focus; follow the isolated-profile validation procedure.
- Saved shared video and audio results offer **Play video** and **Play in
  Blender** for video, and **Play audio** (with **Stop audio**), **Waveform**
  and **Open in system player** for audio in their saved-job controls. Retained
  prototype results keep Play and Add to sequencer, without a waveform or
  in-extension audio. Beyond installed-ZIP native tests, partial physical
  desktop passes on macOS arm64 Blender 5.1.2 opened a saved video in the system
  player and in Blender's animation player at UI scale 1.0, and drew a waveform
  in the sidebar at UI scales 1.0 and 2.0. The last pass's ZIP wrapped the
  waveform status by measured width, before later amendments for Studio's popup
  width and hyphenated words, and kept the duration with its unit. On 2026-10-13
  the maintainer listened to a saved audio result through the system player and
  drew its waveform on the 0.10.0 candidate ZIP. These passes predate
  in-extension audio. Play audio and Stop audio on the desktop, listening
  through them, the waveform's Cancel, Retry and close on the desktop, clicking
  Play at UI scale 2.0, Studio at UI scale 2.0 and other platforms have no
  desktop evidence yet, which remains
  [#440](https://github.com/scenario-labs/blender-plugin/issues/440) work. Local
  MCP `inspect_result_previews` reads the same envelope as bounded levels,
  without an image, and no MCP tool launches a player or plays audio. See the
  [supported limits](USER_GUIDE.md#historical-upstream-reference).
- **Play audio** starts a separate offline Blender process for each playback,
  so sound starts after that process does: 0.6 to 0.85 seconds on macOS arm64
  with Blender 5.0.1 to 5.2.1, measured with a digitally silent file; Windows
  and Linux start-up, including antivirus scanning, is unmeasured. It plays from
  the start to the end, up to ten minutes, with no pause, seeking, volume
  control or playhead on the waveform. It uses the **Audio Device** chosen in
  Preferences > System > Sound, opened with Blender's audio module defaults
  (48 kHz stereo) rather than the sample rate, channels and mixing buffer chosen
  there, and still plays when Blender itself was started with `-noaudio`. Only
  macOS CoreAudio has been exercised, silently; WASAPI, PulseAudio, PipeWire,
  JACK, OpenAL and SDL outputs, sandboxed Blender packages and human listening
  are unverified. Installed tests replace the output with a recording stand-in
  or Blender's None output, so they show the process, its decoding checks and
  its lifetime, not sound. A crash of Blender stops playback through the
  closed pipe of the child's standard input; that path is tested by closing the
  pipe, not by crashing Blender. Disabling the extension waits up to about six
  seconds for a child that does not stop; one that survives even the kill is
  logged and left to its watcher thread, which removes its directory only if it
  exits while Blender runs. That case is tested with a stand-in process.
- Play first copies the saved file, up to 512 MiB, on the preview lane, so a
  large file waits for its copy and queues behind server thumbnail polls and
  waveform decodes there; Play audio too. Up to four copies or 1 GiB are kept
  for reuse, plus any copy a running animation player or audio playback reads.
  The system player is tracked only
  until its launcher (`open`, `xdg-open` or, on Windows, an isolated Python
  helper) exits: an eviction, connection change, file load or disabling the
  extension can then remove a copy it still has open. Both launches strip
  `SCENARIO_`, `PYTHON` and proxy variables from the environment Blender passes;
  macOS still adds proxy settings from the login session to every application it
  launches. Unit tests check the filter with recorded launchers and a stand-in
  `xdg-open`, and native tests with a recorded launcher. A private check on macOS
  arm64 Blender 5.1.2 with Environment credentials opened the private read-only
  copy in the system player and started Blender's animation player without
  `SCENARIO_`, `PYTHON` or proxy names in its environment; probe applications
  launched through `open` received no `SCENARIO_` or `PYTHON` names from the
  filtered launch, while an unfiltered `open` passed its caller's whole
  environment on. Real default players on Windows and Linux remain unverified.
  Windows
  refuses to delete a copy a player still holds open; it is removed by a later
  day's sweep. Whether Blender's animation player plays a video's soundtrack,
  behavior without a default player (for example Linux without `xdg-open`) and
  sandboxed Blender packages are unverified. Without a usable executable path,
  Play in Blender fails with an explicit error.
- **Output folder**, the Film **Open capture**, **Open capture files** and
  export and case-study **Open folder** buttons, and the **Create a key in the
  portal**, **Update guide** and **Release ZIPs** links start the same launcher
  as the system player instead of Blender's `wm.path_open` and `wm.url_open`,
  with the same environment filter and the same macOS proxy exception. Links
  open only as `https` addresses. A missing file or folder and a launcher that
  cannot start are reported at once; a launcher that exits with an error is
  reported on the status line, and one still running after about 30 seconds is
  no longer watched. Unit tests check the commands and environment with recorded
  launchers and a stand-in `xdg-open`, and a source scan finds no other use of
  Blender's or Python's openers; native tests run every button's operator with
  a recorded launcher. No physical desktop check of these buttons was made.
  The private macOS check of `open` described above covers the launcher they
  share, not the buttons. Real file managers and browsers on Windows and Linux,
  Linux without `xdg-open` and sandboxed Blender packages are unverified.
- Each waveform starts a separate offline Blender process: a one-second file took
  0.65 to 0.9 seconds end to end on macOS arm64 with Blender 5.0.1 to 5.2.1.
  Windows and Linux start-up, antivirus interference and sandboxed Blender
  packages are unmeasured. Without a usable executable path, waveforms fail with
  an explicit error.
- A waveform decode occupies the single preview lane for its duration, at most
  60 seconds; Cancel terminates its decoder process. A decode that cannot start
  within 120 seconds, for example behind other lane work, fails with Retry. The
  shown waveform is drawn from its cached envelope and checked once per second
  for a changed connection; it does not monitor later external file changes, but
  every Play and decode rechecks the saved receipt.
- Automatic result previews cover the first twelve jobs of Jobs and of
  Generations and show four results per job at a time; a job with more pages
  through them with **‹** and **›**. A physical recheck of #423's fit at UI
  scales 1.0 and 2.0 found the arrows as tall as the tiles' icon cells, still
  about 9 px (scale 1.0) or 18 px (scale 2.0) beyond the visible image in the
  default-width sidebar, with paged tiles about 30% smaller than an unpaged
  row's. #432 then sized the arrows to the preview images, and a physical
  recheck on 2026-10-13 found them within 0 to 1 px of the images at UI scales
  1.0 and 2.0, and Studio's arrows about 1.2 steps wide at 2.0, stretched past
  their one step by the row. The arrows now keep one step at every UI scale;
  that width awaits a physical desktop recheck. Which result kinds receive a
  Scenario preview image, how long it takes and where a first-access redirect
  points are not established by live evidence; a preview missing after about
  five minutes of online polling needs **Retry preview (N)**. Video clips and 3D
  turntables are fetched by the runtime but not displayed, the preview of a
  video is Scenario's image rather than a chosen frame, and MP3 or other audio
  waveforms stay behind the explicit **Waveform** control. Images over 8192
  pixels per side or 8192 by 4096 pixels in all show no preview, nor do
  multi-part, deep or layered OpenEXR files. Decoding runs
  on Blender's main thread, one image per timer tick: about 0.1 s for a
  4096-pixel JPEG and 0.2 s at 8192 by 4096 pixels on macOS arm64, longer for
  real photographs and on slower machines. Background Blender decodes no image
  previews, so local MCP inspection in background Blender reports image stills
  as failed after 120 seconds; it returns preview states and metadata, never
  the image itself. Desktop evidence is partial: physical passes on macOS arm64
  Blender 5.1.2 showed tiles loading and ready in Jobs and ready in Generations
  and Studio at UI scale 1.0, and, in the sidebar at UI scales 1.0 and 2.0,
  **Retry preview (N)** after a cached still was removed during preparation,
  number-first captions and status lines wrapped by measured width. Offline
  passes with Online Access disabled showed locally decoded image tiles and the
  pager in Jobs, Generations and the Studio Jobs page at UI scales 1.0 and 2.0.
  Server previews with Online Access disabled, Studio's Results page at UI scale
  2.0 and other platforms were not exercised
  ([#440](https://github.com/scenario-labs/blender-plugin/issues/440)).
- Prompt helpers lack an explicit generic/model-contextual Spark preference
  ([#188](https://github.com/scenario-labs/blender-plugin/issues/188)).
- Capture timing follows scene settings; native dialogs use Blender's theme
  rather than the custom composer drawing.
- Studio > Library [organization](ui/studio-and-library.md#library-organization) changes one
  asset per review (local MCP accepts up to 49) and loads at most 200 collections.
  Applied changes are immediate account metadata that Blender Undo does not
  reverse. Collection rename and deletion, model collections and tag listing are
  not available, and pages loaded with Public assets cannot be organized. Reviews
  and results are session-local: a restart, file load or connection change loses
  the card, while a sent write keeps its effect. Search results may lag behind
  tag changes; verification reads the assets back instead. An add of an asset
  that is already a member is refused as a whole and writes nothing, as the
  service was observed to behave; the review then reads the asset back as
  verified without another request. This is not yet checked live from this
  repository. On 2026-10-10 a free live run from a pre-merge build applied and
  read back one asset's collection add and removal and tag changes, through local
  MCP and the native operators in background Blender, so that removal's DELETE
  body reached the service; name uniqueness, tag normalization and service limits
  are unverified live. On macOS, Japanese IME composition did not start in the
  Organize dialog's Tags field, as in Blender's own text fields, where pasting the
  text worked. A read-only physical pass on macOS arm64 Blender 5.1.2 checked the
  card placement and read-enabled controls at UI scales 1.0 and 2.0; at 2.0 only
  the review card's header shows at the bottom of the Studio popup until it is
  scrolled. Apply on a desktop still needs a physical check
  ([#440](https://github.com/scenario-labs/blender-plugin/issues/440)).

## Verification

Offline contracts establish the behavior of fixtures and local state machines.
Browser sign-in remains deferred under
[#67](https://github.com/scenario-labs/blender-plugin/issues/67); the open
provider, UI/MCP parity and platform checks are in
[#440](https://github.com/scenario-labs/blender-plugin/issues/440).
Inherited historical reports must be reproduced before being called current
bugs. Passing a knowledge check establishes structure and freshness signals,
not prose truth or runtime correctness.
