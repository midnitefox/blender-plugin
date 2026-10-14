# Saved-result previews

[`result_previews`](../scenario/core/jobs/result_previews.py) and
[`preview_scheduler`](../scenario/core/jobs/preview_scheduler.py) prepare small
previews of downloaded saved results for the shared runtime. They are bpy-free.
Previews are read-only: they never submit, spend, apply, resolve a scene origin
or write the job store. Their user interfaces are the private previews of
[workflow review assets](#workflow-review-assets), the saved-job **Waveform**
control, which draws audio envelopes decoded in an owned offline Blender process
described below, **Play video**, **Play in Blender** and **Open in system
player**, which open [private player copies](#private-player-copies) made on the
same lane, **Play audio**, which plays such a copy through Blender's audio engine
in [another owned offline process](#in-extension-audio-playback), and
[automatic previews](#automatic-previews-in-blender), which show stills in Jobs,
Generations and the Studio pages that reuse them.
[Local MCP](#local-mcp-inspection) reports the same states, still metadata and
envelopes. Clips are fetched but not displayed; showing them is
[#445](https://github.com/scenario-labs/blender-plugin/issues/445).

## Sources by result type

Only results with a saved download receipt are previewed, in the states `ready`,
`applying`, `apply_failed` and `applied`. An interrupted application is allowed
because previews never touch the scene. The saved manifest's MIME type selects
the source:

| Saved result | Still | Other renditions |
| --- | --- | --- |
| PNG, JPEG, WebP and OpenEXR (`image/exr`, `image/x-exr`) images | Local decode request for the verified result | None |
| Video | Server `thumbnail` | Server `preview` clip, only when requested |
| 3D models, except MTL material libraries | Server `thumbnail` | Server `preview` turntable clip, only when requested |
| WAV, MP3, OGG, FLAC, M4A and AAC audio | Server `thumbnail` when offered | Local RMS envelope from decoded samples |
| Anything else | Unsupported | None |

Image previews use the verified local bytes, not CDN transformation parameters,
which are undocumented CDN behavior. A saved `image/aces` original is OpenEXR
too, but like image import, previews do not accept that label yet: it is
unsupported.

## Server stills and clips

Metadata uses the selected SDK 2.2.0 through the shared
[adapter](../scenario/core/api/sdk_adapter.py):
`assets.with_raw_response.get_bulk` for every batch, a single asset included, in
chunks of 100 identities (the SDK documents a 200-identity limit). It carries the
selected project, uses the client's `max_retries=0` and checks online access
before each request. One batch reads its metadata once. The published wheel's
`types/asset_get_bulk_response.py`, like `types/asset_retrieve_response.py`,
documents the optional `thumbnail` and `preview` objects, each with an `assetId`
and a signed `url`; see the
[asset retrieval contract](https://docs.scenario.com/api/python/resources/assets/methods/retrieve).
Signed URLs stay in memory and are never written, logged or returned in errors.

Using one method for every batch size gives an absent asset one meaning. An
asset the server omits, for example one deleted after the job, is treated like
a preview that has not appeared yet: it is polled within the window, then marked
`missing`. A failed or malformed response, including an unrequested record,
remains a read failure, described under the polling window.

Bytes use the existing [signed result transfer](RESULT_TRANSFERS.md) with the same
configured storage hosts, redirect rules and online-access predicate, and smaller
caps: 8 MiB for a still and 64 MiB for a clip. The content, not the URL or a
declared type, must be PNG, JPEG or WebP for a still and MP4 or WebM for a clip.
Blender will decode stills on its main thread, so the byte cap alone does not
bound decode memory. A still must also declare at most 4096 pixels per side in
its PNG `IHDR` chunk, JPEG start-of-frame segment or WebP `VP8`, `VP8L` or
`VP8X` header; the checked dimensions are stored in its sidecar.

A failed transfer is not a final answer: storage may not serve a new preview on
its first request, for example by redirecting to another host. Any failure of
the transfer itself leaves the rendition `pending` for the
[polling window](#polling-window): a redirect to another host or a host outside
the policy, a connection error or timeout, a status other than 200 such as a
5xx, a response without a valid `Content-Length` (a preview has no expected
size), and a chunked, incomplete or oversized body. The downloader still makes
one attempt per call and follows only the same bounded same-host redirects, and
each attempt stays within its byte cap. Bytes that arrive but no longer match
their download receipt, are not a supported format, or lack bounded dimensions,
and a metadata mismatch, fail the rendition at once. Either way the reason is
sanitized and nothing is cached. Each poll reads the metadata again, so it uses
a freshly signed URL.

Server previews are bound to the saved asset identity and receipt digest. Their
bytes are produced by the service, not derived from the saved file, so they are
not a content attestation of the result.

## Polling window

Server previews can appear minutes after a result is saved. The scheduler polls
missing ones on the preview lane with backoff delays of 5, 10, 20, 40 and then
60 seconds. The window measures 300 seconds of online polling: it opens at the
first online poll and the last poll lands at its end. If no preview appeared,
the cache records a `missing` marker for that receipt and later requests show it
without polling again. An explicit `retry` clears the marker and opens a new
window. Requesting a rendition the result did not have yet, such as a clip after
its still, also opens a new window and restarts the backoff for that result, so
the clip is not judged by a window the still already used. Renditions still
polling share the new window, including one whose final poll is already on the
lane: if that poll leaves it `missing` or `failed`, it polls again in the new
window when the batch returns. That forced poll clears the `missing` marker the
final poll wrote, and any older marker of the requested rendition. A failed
download in that final poll is polled again too, because its outcome does not
show whether the window's end caused it.

A retry queues every rendition of the result at once, except unsupported ones
and previews the lane is already decoding or publishing, such as an audio
envelope whose offline decode is running. It withdraws their outstanding decode
requests, including an envelope still waiting for room on the lane, so the
snapshot it returns and later `status` reads report the retry rather than the
old outcome. When that result's batch is already on the lane, the forced fetch
waits for it. The pump that collects the batch applies its late outcome and
queues the renditions again in the same call, so that outcome is never reported
as settled and cannot undo the retry. A `cancel` received before that pump wins
for local renditions: the pump queues only server renditions again, so a
canceled envelope stays canceled and no decode starts until another `retry`.

Without online access, renditions report `offline` and are checked every five
seconds. An offline poll pauses the window, keeping the time already used since
it opened, and the next online poll resumes it instead of starting a new one. A
transfer stopped because online access was turned off also reports `offline`.
Metadata read failures and transfers that did not complete stay `pending` with
the same backoff and become `failed` at the window's end, as do repeated lane
task failures. A transfer that keeps failing therefore makes at most nine
attempts in one window. Unlike `missing`, `failed` writes no marker. No result
state is persisted in `jobs.sqlite3`: the window starts in memory, and the
`missing` marker is what prevents a new window after a restart.

The backoff applies only to `pending` and `offline` renditions. A rendition
that was not sent with a batch, because the decode limit below held it back or
because it was requested while the batch ran, stays `queued` and goes with the
next pump.

## Local images and audio

For an image still or an audio envelope, the lane copies the saved file into a
new private `work/preview-*` directory of the cache, rehashing it against its
receipt while copying, and checks the copy's container signature. The copy has
a canonical name, never the provider's file name. Sources are capped at 128 MiB
for images and 256 MiB for audio. The scheduler keeps at most four such decode
requests outstanding, including running audio decodes, and fails a request that
waits 120 seconds for a decoder.

Blender decodes an image request later on its main thread: it writes a PNG of
at most 256 pixels per side to the request's `output`. Before issuing one, the
lane reads the dimensions the copy declares in its PNG header, JPEG frame
header, WebP header or OpenEXR data and display windows, within its first
2 MiB. An image over 8192 pixels per side or 8192 by 4096 pixels in all, or one
whose dimensions cannot be read there, fails with a reason instead, so a small
file cannot request a long decode on the main thread. An OpenEXR copy must also
be a single image: format version 2, one part, no deep data, and a channel list
without layered `layer.pass` names whose channel count, at least four, times
its pixels stays within 4 by 8192 by 4096 samples. Blender loads every part of
a multi-part file into buffers sized by the first part: in offline probes, a
generated file whose second part had a larger data window crashed Blender
5.0.1, 5.1.2 and 5.2.1, and a 276 KB layered file raised peak memory by about
610 MiB while loading as a multilayer image with no single picture to preview.
Publication checks the PNG signature, header checksum, dimensions and a 4 MiB
size limit before caching it. Every finished or discarded request removes its
private copy.

## Offline audio envelopes

Blender's audio module holds Python's global interpreter lock for an entire
decode. Measured on macOS arm64 with Blender 5.0.1, 5.1.2 and 5.2.1, decoding ten
minutes of MP3, Ogg Vorbis or AAC stereo on a worker thread paused the main
thread for 0.4 to 0.55 seconds. An audio request is therefore decoded by
[`audio_decode`](../scenario/core/jobs/audio_decode.py) in a separate Blender
process, never on a thread of the user's Blender. The scheduler queues one lane
command per request as soon as the batch returns it; audio requests are never
listed by `decode_requests`, which is for Blender's main thread.

The lane starts the session's Blender executable with `--offline-mode`,
`--factory-startup`, `--disable-autoexec`, `--background` and `-noaudio`, a new
disposable profile and temporary directory, and the environment that local
capture uses, without `SCENARIO_`, `BLENDER_`, `PYTHON` or proxy variables.
The standalone [`waveform_worker.py`](../scenario/blender/waveform_worker.py)
does not load the extension. Blender's audio module and its ffmpeg readers decode
the private copy from its start, without seeking: WAV, MP3, Ogg, FLAC, M4A and
AAC are the accepted saved types. Numpy then writes one sum of squares and one
peak of samples clamped to full scale per 10 ms block, and a header with the
sample rate, channel count and frame count. Failures use a fixed vocabulary:
unreadable, unsupported rate or channels, no samples, too long or non-finite
samples. The process's log is never read and is removed with its directory.

[`EnvelopeBuilder`](../scenario/core/audio_waveform.py) checks each block against
its frame count before folding the blocks into 256 RMS and peak bins: finite
values, peaks within full scale and a root mean square no louder than its peak.
Envelopes accept up to 600 seconds, eight channels and 192 kHz. The child holds
at most 64 Mi decoded samples, which covers ten minutes of 48 kHz stereo; longer,
faster or wider audio fails instead of being truncated. A decode times out after
60 seconds. Cancellation, retirement and the timeout terminate the child, which
is killed if it has not exited three seconds later. Only a child still running at
the deadline reports a timeout; one that exits with a failure keeps the reason it
reported, however long it took. The envelope is cached as a sidecar keyed by
the saved receipt, so a later session reads it without decoding again.

The session builds the decoder specification on Blender's main thread from
`bpy.app.binary_path` and the installed worker. Without a usable executable,
for example when Blender runs as a Python module, envelopes fail with an explicit
reason instead of waiting. A failed decode caches nothing; Retry decodes again.

## Private player copies

A desktop player opens a file by its name and extension, so **Play video**,
**Open in system player**, **Play in Blender** and **Play audio** never hand it
the saved result. The lane command
`prepare_playback` streams one downloaded receipt of a saved job in a previewable
state into a new private `play-*` directory of `cache/media-playback`, rehashing
it against the receipt while copying. It accepts the media types Film inspection
allows: MP4, MOV, WebM, MKV and AVI video and WAV, MP3, Ogg, FLAC, M4A and AAC
audio, up to 512 MiB. The copy's container signature must match its saved type,
and it is named `media` plus that type's suffix, so a downloaded name such as
`clip.command` never reaches the operating system. The file is read-only and its
directory private. After copying, the command rechecks the saved record at
receipt level: an unrelated revision, such as a strip application, keeps the
copy, while a changed receipt or state discards it. A canceled, failed or
retired command removes its partial copy.

An unchanged copy of the same receipt, compared by its file metadata, is reused
instead of copying again. Before copying, the command removes the copies its
owner evicted and sweeps `play-*` directories older than a day that the owner no
longer uses, including copies a Windows player kept open. The owner tracks
evicted copies until the command ends and removes any left, as when the
connection retires and deactivation cancels the queued command. Copies hold the
saved bytes; no URL, credential or prompt is written. They are disposable and
safe to delete while no player reads them.

## In-extension audio playback

**Play audio** plays a saved audio result's private copy through Blender's
audio engine without opening another application. Two designs were weighed:

- Blender's `aud` module in the user's session would start sooner, but it
  would decode generated media inside the session that holds unsaved work, and
  its decoding holds Python's global interpreter lock, as measured for
  [envelopes](#offline-audio-envelopes). A decoder failure there would reach
  that session too. Its device would also be shared with the session's own
  sequencer and scrubbing.
- An owned offline Blender process, like the envelope decoder, keeps decoding
  and the output device out of the user's session, and Stop is a process
  ending rather than a request to a sound handle. Its cost is the child's
  start-up: on macOS arm64, a digitally silent WAV started playing 0.6 to 0.85
  seconds after the child started, with Blender 5.0.1, 5.1.2 and 5.2.1.

The extension uses the owned process.
[`audio_player`](../scenario/core/jobs/audio_player.py), without bpy, starts the
session's Blender executable with `--offline-mode`, `--factory-startup`,
`--disable-autoexec`, `--background` and `-noaudio`, the environment the
envelope decoder uses, and a new private `listen-*` directory under
`cache/audio-player` holding its request, disposable profile, temporary files
and reports. Its standard output and errors are discarded. The standalone
[`audio_player_worker.py`](../scenario/blender/audio_player_worker.py) does not
load the extension. `-noaudio` keeps Blender from opening an output of its own;
the worker opens only the one the request names, the **Audio Device** chosen in
Blender's Preferences > System > Sound, read on the main thread. It opens that
output with Blender's audio module defaults, 48 kHz stereo, not the sample rate,
channels or mixing buffer chosen there. With **None** chosen, Play audio refuses
before copying anything.

Before opening the output, the worker opens the copy with Blender's audio
module and its ffmpeg readers and checks it as envelopes do: at most 192 kHz
and eight channels, a declared duration of at most 600 seconds, and a first
quarter second that decodes to samples. A container that declares no duration
still stops at 600 seconds. Failures use a fixed vocabulary: unreadable,
unsupported rate or channels, no samples, too long, which names the system
player instead, and an output that cannot play, such as Blender's None output,
which accepts a sound but never plays it. The worker then plays the copy from
its start, writes `playing.json` with the declared duration and exits when the
sound ends.

The child's standard input is its lifeline: the parent holds a pipe it never
writes to, and the worker stops when it reaches its end, so a parent that exits
or crashes stops playback too. Each child has its own watcher thread, and only
that thread touches the child's files: every 50 ms it reads the child's exit
status and its two small reports and applies the deadlines. **Stop audio**
closes the pipe and terminates the child at once; the watcher kills one still
running three seconds later. A child that reports neither playback nor a
failure within 60 seconds of starting fails and is stopped, and a playing child
is stopped ten seconds after the duration it reported, or after 600 seconds. A
failure's reason names no path and never includes the child's output. When it
starts, the watcher removes `listen-*` directories left more than a day
earlier, for example by a crash, and once its child has exited it removes that
child's directory.

The [saved media controller](../scenario/blender/result_media.py) runs on
Blender's main thread. It prepares the copy on the preview lane like the other
players, then starts the child, so the copy stays pinned, never evicted or
discarded, until the child exits. At most one playback is active: another
**Play audio** stops the playing one before the new copy is even prepared, and
a copy that arrives for a request that a later Play replaced, or after the
connection, project or file changed, never starts a child. Stop, a connection
or project change, a file load and unregistering stop it too. After a file load
or a connection change, cleanup completes without blocking: the watcher removes
the directory once the child exits, and the controller then releases the child
and its copy. Unregistering, which Blender also does on exit, is synchronous:
it waits up to three seconds for the child and its directory, kills the child
and waits three seconds more. A child that survives even the kill is kept, with
a warning that names no path, rather than forgotten, and its watcher keeps
reaping it. The controller's persistent timer reads each child's phase from
memory every quarter second while audio plays and every tenth of a second while
a child starts or stops; it never touches the file system for a child. Drawing
reads only the controller's memory: it never starts, polls or stops a child. Playback is local: it works with
Online Access disabled and makes no service request. No local MCP tool plays
audio.

## Automatic previews in Blender

[`result_previews`](../scenario/blender/result_previews.py) in the Blender layer
is the scheduler's main-thread owner. Its own persistent timer runs every half
second, or every tenth of a second while it has work, and never inside drawing;
a failed redraw is logged and does not stop it:

- **Requests.** It takes the first twelve unfinished rows of Jobs and the first
  twelve rows Generations lists, whether or not those panels are open, and for
  each shared saved job whose results are downloaded (`ready`, `applying`,
  `apply_failed` or `applied`) reads the saved record once per revision. A
  request the scheduler refuses, for example when it already tracks its limit
  of pending previews, shows the reason under the tiles it left without a
  preview, keeps the job's other tiles and drawn page, and is made again after
  5 s, then 10, 20, 40 and every 60 s while it is refused, rereading the
  record each time. It ranks the downloaded results, video, audio, embedded
  models and base color or untagged images first, then other texture maps and
  OBJ models, each in saved order, and requests the stills of the four on the
  job's drawn page; a pager shows and requests the others four at a time.
  It asks for stills only, so it never starts an audio envelope decode or
  fetches a clip.
- **Image decoding.** It decodes the oldest issued image copy, one per tick,
  with the shared [temporary preview decode](architecture/blender.md#temporary-preview-decoding)
  inside `bpy.data.temp_data()`, a separate data block freed right after. The
  open file, its undo history, dependency graph and data counts never see the
  image. The decoded size must stay within the same 8192-pixel bounds; the
  copy is fitted to 256 pixels per side, never upscaled, and written as an
  8-bit RGBA PNG through the Standard view transform. A file Blender cannot
  decode fails with a reason; the lane validates and caches the PNG.
- **Icons.** For a ready still, it rereads the cache file, opened without
  following a symbolic link or waiting on a named pipe, as a regular file
  inside the cache root, checked in the root's canonical spelling (the
  extended `\\?\` namespace on Windows), and compares its size and SHA-256
  with the cache entry, then decodes that file the same way to at most 256
  pixels per side. It builds at most one icon per tick, none in a tick that
  decoded an image copy, in its own `bpy.utils.previews` collection from the
  decoded pixels, and releases the least recently used icons beyond 64 that no
  tile draws. High bit-depth files are unpremultiplied and converted from linear
  to sRGB values, so semi-transparent pixels keep straight colors.
  It never calls `ImagePreviewCollection.load`: measured with a temporary home
  on Blender 5.1.2, that call wrote the loaded image's thumbnail to
  `~/.thumbnails/large` outside the extension's user data.
- **Tiles.** Drawing reads only the tiles it precomputed: the icon or the
  result kind's placeholder, a caption, a status line while preparing,
  waiting, offline or unavailable, and a **Retry preview (N)** button after a
  missing, failed or unreadable preview. The caption names a texture map by its
  stored role (`ROLE_LABEL` in `core.jobs.result_previews` has a label for
  every role in `TEXTURE_ROLES`) and any other result by its kind, after the
  number of its saved-job controls, as `(3) Albedo`: Blender keeps the start of
  a caption that does not fit, so a narrow column clips the role before the
  number. Status lines name results as the controls do, as `Albedo (3)`.
  Retry clears the missing marker and opens a new polling window. With more
  than four previewable results, the row has **‹** and **›** arrows around the
  tiles and a range line such as `3-6 of 6`; the page offset is transient
  controller state that resets when the job's results change. Such a paged
  row sets each tile's column to its share of the row beside the arrows, so
  that Blender does not narrow it, and its icons take a scale from 1.75 up to
  five. Each arrow is as tall as the preview image, which Blender draws 0.15
  icon scale steps inside every edge of its icon, and one step wide in a
  sublayout Blender keeps at that width, so a wider row such as Studio's does
  not stretch it.
- **Lifetime.** A connection, project or file change, and unregistering the
  extension, release the icons and tiles. A status read after that belongs to
  another connection and is never shown.

Background Blender registers no timer, so headless sessions decode no images.
Measured with generated files on macOS arm64 Blender 5.1.2, a decode took about
25 ms for a 2048-pixel PNG, 0.1 s for a 4096-pixel JPEG and 0.2 s at 8192 by
4096 pixels; real photographs can take longer.

## Local MCP inspection

The local MCP tool
[`inspect_result_previews`](MCP.md#historical-upstream-reference) uses the session's
scheduler on Blender's main thread, like the native controls. It checks the
context token and the saved job's revision and state, then requests the same
renditions they do: the stills of every result the tiles page through, in
their order, or of one named result, and the audio envelope only when asked, as
**Waveform** does. `retry`
and `cancel` call the scheduler's `retry` and `cancel`; a retry also clears the
preview controller's icon failure for that result, so its tile builds the icon
again. Responses copy states, reasons, dimensions, sizes and envelope levels
from the immutable status snapshots, folding an envelope's levels with
`audio_waveform.fold`; they never include a cache path, decode request, signed
URL or image bytes.

A bounded wait uses the scheduler's `watch`. It returns a signal the HTTP thread
waits on while Blender's main thread keeps pumping; `pump` sets it once none of
the watched results has a watched rendition `queued`, `pending` or `decode`,
counting `offline` ones as settled, because they change only when Online Access
returns. The tool watches the envelope when it asked for one and otherwise the
stills, so an envelope wait does not also wait up to five minutes for a
Scenario still. A retry deferred behind a batch already on the lane reads
`queued`, so the wait never reports it finished before it runs. A result without
a preview kind reports an `unsupported` still without a `source`. Statuses are
then read again on the main thread, after checking that the context token,
session and saved job's revision are unchanged; a saved job that changed during
the wait is refused rather than reported at its earlier revision. At most eight
waits are open per scheduler, and closing it releases them. A wait that ends
also closes its watch from the HTTP thread, so the next pump forgets a watch
whose main-thread delivery never runs, for example after the local MCP server
stopped. Nothing here starts a player: local MCP has no tool that opens a saved
result in a system or Blender player.

## Cache

The runtime passes `cache/result-previews` under Blender's extension user
directory. If that directory cannot be created, the session still runs jobs and
`result_previews` reports that previews are not configured. Entries live in
`v1/<scope digest>/<key digest>/`; the scope digest matches the saved result
partition and the key also binds the request, asset, MIME type and receipt
digest and size. Another credential, project, request or byte content therefore
never shares an entry. Stills and clips have a data file and a JSON sidecar
holding digests, sizes, dimensions and the server preview asset ID, never a URL,
credential or prompt. Envelopes and `missing` markers are sidecars only.

Like saved results, the paths the cache returns, including decode requests,
use the canonical spelling of its root: on Windows, the extended-length `\\?\`
namespace. Compare them with that canonical root, not with the configured one.
Work directories are built on that root and take only their name from
`mkdtemp`, which normalizes the path it returns on Python 3.12 and later.

Reads accept only a regular, nonsymlink file whose size, digest and signature
match its sidecar; anything else is a cache miss. Publication never replaces a
valid entry. At most every ten minutes the lane removes abandoned work
directories older than a day and evicts the least recently used entries beyond
512 MiB, keeping entries used in the last minute. That pass normally starts a
preview batch. Once every rendition has settled no batch comes, so when previews
were written since the last pass, the scheduler queues a maintenance-only lane
command on the same ten-minute cadence. It reads no job record and makes no
network request, and it evicts only beyond the budget. A cache that writes took
past 512 MiB is therefore brought back toward it within about ten minutes of the
previous pass, whether or not more previews are requested. A ready status can
outlive its evicted file; an explicit retry reads or fetches it again. Only
removed bytes count toward the budget: an entry the system refuses to delete,
such as a file another process holds open on Windows, leaves the next oldest
entry to go instead and is tried again on the next pass. The cache is safe to delete, even
while Blender runs: each lane command recreates the root as a private directory
when its parent still exists.

## Workflow review assets

A workflow job waiting at an approval or selection step lists assets for a
person to review: an approval's upstream result assets, or an asset selection's
candidates, at most 50 per step ([waiting workflow steps](ui/studio-and-library.md#waiting-workflow-steps)).
They are not downloaded results, so they have no receipt. **Load previews** asks
`ModelJobs.review_assets` for the current step's distinct asset IDs, bound like a
decision to the context, saved revision and the step's `review_id`, then calls
`ResultPreviewScheduler.request_review`. A selection that lists one asset as
several candidates previews it once. The coordinator's
`review_preview_targets` accepts 1 to 50 distinct IDs only for a saved
`remote` workflow job with a known remote ID. Their entries are keyed apart
from saved results, so the same asset ID never shares state, and the lane
refuses a target once its job is no longer that `remote` workflow job.

Each review asset gets one still, through the same lane, batches, metadata read,
polling window, downloader and cache as server stills:

| Review asset | Still |
| --- | --- |
| PNG, JPEG or WebP declared within 8 MiB and 4096 pixels per side (or undeclared) | The asset's own signed `url`, the field result downloads use |
| Such an image declared larger | Its server `thumbnail` when offered; otherwise it fails at once, without a transfer |
| Any other asset | Its server `thumbnail` when offered; otherwise polled, then `missing` |

The bytes must be a PNG, JPEG or WebP within the still bounds above, checked from
their content. The cache key, a `ReviewKey`, binds the scope, request, remote job
and asset; the sidecar records the digest of the bytes the transfer returned.
Transient transfer and metadata failures stay `pending` within the window, as
for server stills, and failures and `missing` markers need an explicit retry.

The [Blender owner](../scenario/blender/review_previews.py) runs on the main
thread. The GUI pump copies scheduler statuses into memory and, for each newly
ready still, verifies the cached file again with `check_still` (size, digest and
signature against its entry). It then decodes the file with the
[temporary preview decoder](architecture/blender.md#temporary-preview-decoding),
inside `bpy.data.temp_data()`: Blender must read the dimensions the lane
verified, and the still is fitted to 256 pixels per side as RGBA display values.
The open file never gains or loses an image, so its dependency graph reports no
update and job origins captured in its scenes stay current, with the quotes and
automatic application bound to them
([origin lifetime](BLENDER_JOB_CONTEXT.md#origin-and-quote-lifetime)). At most
two stills are decoded per pump tick, and none while a render job runs. The
pixels fill an icon of a private `bpy.utils.previews` collection, so Blender's
file-thumbnail cache is never written; an icon Blender refuses to fill is
removed at once. At most 128 review assets are tracked;
icons of a step that no longer waits are released, and retiring the job context
releases them all. Drawing reads only that memory.

The cache is disposable, so eviction or a person can delete a ready still's file
after the lane verified it. When `check_still` then finds the file lost or
changed, the pump calls `retry(..., review=True)` once by itself, as part of the
request already made; the lane's own cache check rejects the entry and fetches
the still again. If that happens again, the asset stays unavailable until
**Retry previews**. Retry sends through the lane again each still that failed or
is missing, and each ready still whose cached file was lost or changed or that
Blender could not decode. The lane reuses an entry that still verifies and
fetches the others again; the pump then decodes them again.

## Lane and ownership

`JobWorkers` owns one dedicated `ScenarioPreview` thread with its own bounded
queue of eight tasks and per-task cancellation. Preview polling, copies and
downloads therefore never occupy the job workers used by remote refresh, result
downloads and submissions, and never use the single local media slot.
Deactivation cancels queued previews and signals the running one. A storage
transfer stops at its next permission check once signaled, before publishing
anything; an audio decode terminates its child within about 0.1 seconds, or kills
it after three more; an SDK metadata read cannot be interrupted and finishes or
times out under the client policy. An audio decode occupies the lane for its
duration, so later server polls wait behind it: 0.65 to 0.9 seconds for a
one-second file and about 1.7 seconds for ten minutes on macOS arm64, at most
the 60-second timeout. `JobWorkers.previews_idle` reports when no preview
command is queued or running. Shutdown joins the lane with the other workers.

`ResultPreviewScheduler` runs on its owner thread, normally Blender's main
thread, and performs no network I/O or preview cache work. `request` reads saved
receipts from the local job store, `request_review` reads the saved workflow
job, and `release` removes leftover private copies after the lane stops.
`request`, `request_review`, `retry`, `cancel`, `status`, `quiet`, `watch`,
`unwatch`, `decode_requests`, `finish_decode` and `discard_decode` are its
interface; `retry` and `status` take `review=True` for a review asset. Status
snapshots are immutable and safe to read while drawing.
`request(..., renditions=...)` limits a request to some renditions of a result:
the Waveform control asks for the local envelope only, so it never polls
Scenario for a still; `quiet` and `watch` accept the same filter. `cancel` stops
one result's local decoding: an issued request is discarded, a running decode is
signaled, which terminates its child, and a copy an in-flight batch is preparing
is discarded when that batch returns. Its unfinished local renditions fail as
canceled until `retry`, including one waiting to poll again after a failed lane
batch; server stills keep polling. A retry after `cancel` reports the envelope
queued at once, even while its canceled child is still stopping or a still poll
for the same result is on the lane, so the Waveform control keeps loading
instead of reading the canceled outcome. It sends at most one batch of up to 32
assets at a time.

`JobSession(preview_root=...)` exposes it as `result_previews` while the session
is active. The session's existing maintenance timer, and `reap_retired` in
headless loops, call `service_previews`. Deactivation closes the scheduler. The
timer shuts a retired session down only once its preview lane is idle, so an
in-flight preview read never blocks Blender's main thread; the session stays
registered until then. Shutdown removes remaining private copies after the
workers stop. Extension disable or exit still joins any running preview command.
See the [job context contract](BLENDER_JOB_CONTEXT.md#saved-result-preview-ownership).

## Boundaries

These are offline contracts with synthetic SDK and storage fixtures. Live
evidence does not establish which asset kinds receive server thumbnails or
previews, their sizes and dimensions, signed URL lifetime and hosts, where a
first request may redirect, their timing after a job succeeds, or how `get_bulk`
reports a deleted asset; nor which review assets real workflow flows list, or
whether their `url` and declared sizes are as documented for results.

Installed native tests cover the offline envelope decoder, player copies, the
Play, Play audio and Waveform controls with patched players or a recording
output in place of a sound device, automatic preview tiles drawn through
recording layouts, and local MCP inspection, all with synthetic media. They do
not cover provider audio files, child start-up time, antivirus behavior, real
players and sound output on Windows and Linux desktops, macOS x64 or sandboxed
Blender packages, and the timing figures above are macOS arm64 measurements.
Human listening through Play audio and the open desktop preview checks,
including of the review previews, are in
[#440](https://github.com/scenario-labs/blender-plugin/issues/440).
