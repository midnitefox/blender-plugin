# Local panorama World application

`scenario.blender.world_application.apply_world(scene, filepath, *, expected_receipt=None, media_type=None)` is a synchronous,
main-thread primitive for an explicitly selected scene. It creates a packed image
and a separate World with an equirectangular Environment Texture → Background →
World Output graph. The original World, including its nodes and other scene users,
is untouched. The returned session-local handle can restore that exact original
World. A scene with no original World restores to `None`.

## Accepted local files

The filename is not trusted as a format declaration. The bounded core preflight
checks actual signatures and dimensions before Blender decodes a private snapshot
of the same bytes. Blender's decoded format and dimensions must agree. The image
is packed before scene assignment; deleting the source file afterward is safe.
Temporary copies live under the extension's user directory, not its installation.

For a downloaded result, supply its saved `DownloadedResult` as
`expected_receipt`. The source basename, byte count and SHA256 must match before
container parsing, image decoding or World allocation. Larger files report the
panorama byte limit before receipt comparison. Verification applies to the exact
bounded byte snapshot passed to Blender, so replacing the source path
after that read cannot change the decoded image. Missing, changed or mismatched
data fails locally and preserves the original World; it never triggers another
download or generation. Ordinary explicit local-file callers can omit the receipt.

A saved result also supplies its server-declared `media_type`. It must be one of
`image/png`, `image/jpeg`, `image/exr`, `image/x-exr` or `image/aces`, and must
name the container the preflight actually finds; `image/aces` must be OpenEXR.
Any other type is rejected before the file is opened, and a mismatch is rejected
before decoding. This keeps the confirmation's declared format truthful.

A receipt describes bytes, not authoritative job ownership or a current Blender
target. The caller must obtain it from the selected scoped job, preserve its
private storage, and validate the original file/scene/revision before application.
The optional byte check does not claim a durable application transaction.
The explicit [JobSession World command](BLENDER_JOB_CONTEXT.md#explicit-saved-result-world-application)
adds owned verification, original-context checks and a durable claim around this
primitive for one selected saved result.

- RGB/RGBA PNG with 8- or 16-bit samples. CRCs and chunk boundaries are checked;
  animated PNG and HDR metadata (`cICP`, `mDCV`, `cLLI`) are rejected. A maximum
  of 4,096 total chunks, including IHDR, IDAT and IEND, bounds per-chunk preflight
  work independently of file size and pixel dimensions. Files above this
  application limit are rejected before decoding; it is not a PNG format limit.
- Baseline, extended-sequential or progressive Huffman JPEG with 8-bit samples
  and three color components. Lossless, hierarchical, arithmetic-coded, 12-bit,
  grayscale and CMYK files are rejected. A maximum of 4,096 marker segments,
  counted before, between and after scans, bounds marker work, and at most 64
  scans are accepted. A scan header followed directly by another marker is
  rejected, and the end-of-image marker that ends the scans must be the last
  bytes of the file. This catches simple truncation, which libjpeg would
  conceal with gray pixels, but not a damaged scan or appended data that itself
  ends with that marker. An EXIF orientation other than 1 is rejected. Blender
  must decode the result as a JPEG image; Blender 5.0, 5.1 and 5.2 use `sRGB`
  (see below on ICC profiles).
- Single-part, non-deep OpenEXR version 2 scanline files (compression types 0–9), with matching data
  and display windows. Explicit cubemap metadata is rejected. Blender must decode
  the result as a floating-point image.
- All formats require a 2:1 aspect ratio, minimum 4×2, at most 32 megapixels and 128 MiB
  on disk. EXR header scanning is limited to 1 MiB; the expected scanline table, block
  coordinates and complete nonoverlapping byte extents are checked before decode. Non-regular files are rejected;
  POSIX FIFOs are opened without waiting for a writer.

The parser follows the [OpenEXR file layout](https://openexr.com/en/latest/OpenEXRFileLayout.html)
(version/flags and null-terminated attribute headers),
[PNG specification](https://www.w3.org/TR/png-3/) (signature, IHDR, CRC and chunks)
and [ITU-T T.81](https://www.w3.org/Graphics/JPEG/itu-t81.pdf) Annex B
(marker segments and the frame header).
The parser checks structural completeness, not compressed-payload integrity; the
optional receipt check above binds the source bytes separately. This preflight is
not another image decoder: Blender's decoder remains authoritative.
The byte, pixel, chunk, segment and scan limits bound input size, decoded
dimensions and the two JPEG amplification cases described below, which were
measured during review. They do not bound decoder CPU time in general, and other
slow inputs within these limits may exist.
Radiance HDR, WebP, tiled/layered/multipart/deep EXR and other formats remain unsupported.

The JPEG check reads only what must be known before Blender allocates pixels
or spends decoding time. A few kilobytes of JPEG can declare billions of pixels,
and Blender exposes no header-only dimension query, so the marker walk reads the
frame header before the first scan. It then follows the file to its end without
decoding it. Entropy-coded data runs to the next marker; stuffed `0xFF` data
bytes (`FF00`), fill bytes and restart markers inside it are skipped by a linear
byte search. Every other marker segment, before, between or after scans, counts
toward the segment limit, and its payload is skipped by its declared length.
Each scan marker also counts toward the scan limit. After the first scan, a
second frame header, an unsupported frame type, a start-of-image or reserved
marker, a scan header followed directly by another marker or data after the
end-of-image marker is rejected.

Both limits stop a measured main-thread stall. Each progressive scan makes
libjpeg revisit every block of the frame: a 257 KB file with 4,010 tiny scans
took about 8 s to load on Blender 5.1.2. Blender keeps JPEG comment markers,
and libjpeg walks its whole saved-marker list to append each one, so the cost
grows with the square of their number. Appending 160,000 empty comments before
the end-of-image marker of the 541-byte progressive fixture (a 640 KB file) made
Blender 5.1.2 take more than 50 s to load it; the 4,072 that fit within the
segment limit took 0.04 s. Typical encoders write about a dozen scans and a few
dozen marker segments.

The walk also reads one EXIF value: the IFD0 Orientation tag of an APP1 EXIF
segment. Blender 5.0, 5.1 and 5.2 ignore EXIF orientation, so a rotated or
mirrored file would become an upside-down or mirrored World. Such files, an
unreadable orientation and several EXIF segments are rejected; save the
panorama upright instead. Quantization and Huffman tables, entropy-coded data,
color transforms, ICC profiles and other metadata are left to Blender. Those
Blender versions ignore embedded ICC profiles and decode every JPEG as `sRGB`,
so a wide-gamut JPEG, for example Display P3 or Rec.2020, is applied with sRGB
primaries and appears mis-tinted. Convert it to sRGB first.

### OpenEXR color primaries

The same bounded header scan reads the three attributes Blender 5.0, 5.1 and 5.2
use to choose an OpenEXR color space. Each declares Rec.709, ACES AP0 or nothing:

- `chromaticities` matching Rec.709, or ACES AP0 with the ACES white point,
  within 0.001;
- `acesImageContainerFlag` (SMPTE ST 2065-4): 1 declares an ACES container,
  whose primaries are AP0; other integer values declare nothing;
- `colorInteropID`: `lin_rec709_scene` or `lin_ap0_scene`.

Other chromaticities, such as ACEScg AP1 or Rec.2020, malformed attributes and
conflicting declarations are rejected before decoding. Those Blender versions
decode AP1 or Rec.2020 chromaticities as linear Rec.709, and select `ACES2065-1`
for the container flag even beside Rec.709 chromaticities; either would tint the
lighting. AP0 chromaticities or the container flag take precedence over any
`colorInteropID` in those versions.

Other `colorInteropID` values are not classified. Blender interprets some of
them itself: `ACEScg` for `lin_ap1_scene`, `Linear Rec.2020` for
`lin_rec2020_scene`, `sRGB` for the `srgb_rec709_display` that its own
`Image.save` writes into OpenEXR files, and, in 5.0 only, `Non-Color` for `data`.
Other tested values, such as `srgb_rec709_scene` or unknown strings, are ignored.

After decoding, a classified declaration must agree with Blender's choice:
`ACES2065-1` for AP0, and `Linear Rec.709` or `sRGB` (Rec.709 primaries with
the linear or sRGB transfer) for Rec.709. The primitive never overrides Blender.
A disagreement fails closed and preserves the original World; it can come from
a `colorInteropID` such as `lin_ap1_scene` beside Rec.709 chromaticities, other
metadata, another decoder or a custom OCIO configuration with other color space
names. Without a classified declaration, Blender's own choice applies: its
default `Linear Rec.709` or the space it derives from such a `colorInteropID`.
The `image/aces` media type is a server label; only the file's own declarations
select the color space. PNG and JPEG keep Blender's own choice. Image import and
material maps keep their previous handling of EXR color metadata.

`PanoramaInfo.hdr_capable` describes accepted floating-point OpenEXR capability.
It does not assert measured dynamic range. Accepted PNG and JPEG are treated as LDR.
`PanoramaInfo.chromaticities` reports the declared OpenEXR primaries for panoramas:
`None`, `rec709` or `aces_ap0`, whichever attribute declared them.
A 2:1 image alone proves neither equirectangular content nor seamless edges/poles;
the caller explicitly selects that projection. Cloud generation must separately
establish a supported model/output contract and validate live results.

## Restore and failure ownership

`handle.restore()` returns `True` once and `False` on an already restored handle.
It requires the original live scene, the applied World still assigned there, the
original World still available, and unchanged owned World/image settings. A changed
node graph, socket value, mapping/image-user setting, World setting, image color
space, dirty pixel buffer or changed packed bytes refuses restoration. This conservative fingerprint
excludes the large image pixel array (dirty state and the bounded packed-byte
digest guard content edits). It also includes writable UI properties such as node positions/names; preserve edits
or restore manually when it refuses. It never searches by scene or World name.

Restore preserves the applied World/image if another scene or material has acquired
users. Unused owned data is removed best effort. Preparation/decoding failures
clean up owned data and preserve the previous scene assignment; cleanup failures
may leave orphan data but cannot unlink another user's data. The original World
is not given a fake user: manually purging it makes later restoration unavailable.

Handles contain live RNA references and are not serializable history records.
Discard them after file load, undo or extension shutdown. Invalidated/removed
references fail closed. This primitive creates no undo entry. Operators offering Blender global undo
must own its transaction and invalidate handles when undo changes ownership.
The saved-result path below records one undo step after it applies or restores
a World, which [marks the file modified](architecture/blender.md#marking-the-file-modified),
and offers explicit guarded restoration without a global undo transaction. Async jobs must validate captured file, scene, target and revision
before calling this function; selecting an active scene is not such validation.

## Validation and integration

Unit tests exercise bounded malformed-container rejection, JPEG coding,
segment, scan and EXIF orientation limits, and OpenEXR color declaration
classification. Installed-ZIP native fixtures generate small PNG, JPEG and
floating EXR files locally and test explicit scene application, graph/packing,
shared users, restoration, edited/deleted data, thread rejection,
corrupt/truncated files, saved-receipt and media-type mismatches, source
replacement after snapshotting and rollback after decode. A committed
first-party progressive JPEG and an extended-sequential copy of a Blender-written
baseline JPEG decode natively; a scan flood and comment floods after its first
scan are rejected before decoding, while comments up to the segment limit
decode to the same pixels. Directly written EXR fixtures declare Rec.709, ACES AP0
or ACEScg AP1 primaries, an ACES container flag or a `colorInteropID`. The AP0
cases check the `ACES2065-1` color space and its pixel conversion, and one checks
the restore guard on a color space edit. Other native cases show Blender
ignoring EXIF orientation and choosing `ACES2065-1` or `ACEScg` for refused
declarations. A Blender-written EXR and an EXR declaring only `lin_ap1_scene`
keep Blender's choice, while `lin_ap1_scene` beside Rec.709 chromaticities and
simulated unexpected color spaces fail closed after decoding. They make no
Scenario service calls.

JobSession binds verified downloads to guarded durable World application. The
[Environment lane](MCP.md#historical-upstream-reference), in the native sidebar,
Studio, composer and local MCP, quotes and submits verified panorama model
contracts, and its results stay saved for the approval below.

## Saved-result UI and MCP approval

For a downloaded PNG, JPEG or OpenEXR result (media type `image/png`,
`image/jpeg`, `image/exr`, `image/x-exr` or `image/aces`),
**Set panorama as World (N)** captures the selected scene revision and current
World for explicit confirmation. The confirmation and MCP `format` field state
the selected asset's declared format: PNG or JPEG as LDR, or OpenEXR as a float
container whose ACES AP0 primaries use `ACES2065-1`.
MCP uses `prepare_result_application` with `purpose: world` and a selected
`asset_id`, followed by the same `apply_result_application` command after approval.
The confirmation dialog reads no media and contacts no Scenario service.
The existing worker verifies receipts, then `apply_recovered_world` rechecks the
approved destination and saves the recovered application claim before mutation.
The primitive still verifies the actual panorama bytes, packs the image and
preserves the previous World. An image that fails these checks fails locally
without replacing anything. No automatic download or generation retry follows
failure.

The action is offered by supported image media type and declared size, not a
claim that every image is panoramic. A result whose saved metadata declares a
texture role, such as the albedo, normal, roughness or metallic map of a 3D
package or a texture set, is a material map: neither the button nor MCP World
preparation offers it. Neither offers an image whose file header, read for its
current download receipt, declares a size that is not 2:1 within the
32-megapixel World limit, such as a square or 16:9 image. MCP preparation
refuses such an asset with `This saved image is not a 2:1 panorama within the
World pixel limit`, and `job_status` omits `apply_world` when no supported image
remains.

The saved job records no image dimensions, so Blender reads them. After a
download, a saved-job inspection or a restart, the job owner reads at most the
first 2 MiB of each World candidate on the preview lane, never while drawing and
never on the main thread. Each read covers at most one preview batch of 32
candidates, so previews and playback copies still get the lane, and the next poll
reads the rest. The owner keeps each declared size in memory, bound to the
receipt digest, for as long as the job is shown. Until a candidate is read, or
when those bytes declare no size (for example a JPEG whose metadata pushes its
frame header further), the image stays offered: a late or inconclusive display
read must not hide a usable panorama, and the application check decides. A
square image can therefore show the button for a moment after its download until
the read finishes.

N is the result's position among all of the job's results, the number that
model imports such as **Import GLB model (N)** and the strip actions use. The
confirmation explicitly chooses equirectangular use; actual 2:1 dimensions and
supported container checks still occur during application on the actual bytes,
which remain the authority.
PNG and JPEG remain LDR, and an accepted EXR does not prove measured HDR range or
seamless content. Known application failure, including a media type that does not
match the actual container, is reported with panorama requirements and keeps the
local retry state. The confirmation does not state the projection; the native
saved-job rows [label](ui/environment.md#environment-lane) a result whose saved
metadata declares a 360 projection, and MCP `job_status` reports `projection`.

A successful World assignment exposes **Restore previous World** in the same
session. MCP prepares `purpose: restore_world` without an asset ID, shows the
scene and current World, then consumes the approval through
`apply_result_application`. Restoration checks the captured destination plus
the primitive's exact ownership/fingerprint guards. A replaced/edited World or
image refuses restoration and preserves user changes. The original World is not
mutated. Successful restoration leaves the job `applied`; it does not rewind the
claim or permit generation/application replay. A pending persistence receipt must
be saved before restoration is offered, and retry never repeats World assignment.

This path accepts `ready` or confirmed `apply_failed` jobs under the original
claim. Already `applied` images, including automatic Image imports, use a separate
durable local claim after fresh approval. Interrupted local claims block further
reuse. Restoration covers the most recent World assignment for this job in each
destination scene in the current file session. Reusing a panorama in another
scene preserves the earlier scene's restore handle; restore each scene separately
after selecting and reviewing it. Handles use scene identities owned by this
session, independently of ordinary edit/render origin revisions. Renaming a
scene preserves restoration while deleting it and creating another with the
same name does not transfer authority. Retrying a persistence receipt keeps
its original destination even if another scene is
active. Previous owned Worlds remain subject to normal Blender save/purge rules;
these handles are not a persistent undo history.
Undo/Redo invalidates live scene ownership and retires restoration handles,
as do session retirement/restart and file load. Deleted or retired destinations
do not offer restoration, and maintenance removes their handles without touching
stale Blender data. Manually select a retained World in Blender when needed.
No global undo transaction or seam/pole acceptance is claimed; the open
panorama checks are in [#440](https://github.com/scenario-labs/blender-plugin/issues/440).
