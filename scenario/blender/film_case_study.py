# SPDX-FileCopyrightText: 2026 Scenario Inc.
# SPDX-License-Identifier: GPL-3.0-or-later
"""Session-owned portable Film case-study bundles over the owned export thread.

Adapted from Emmanuel de Maistre's Scenario Studio case-study export. A bundle
holds one built review scene, optionally the director timeline with its shot
scenes, their media, the sanitized recipe, a production ledger, a manifest and an
opening guide, in one new ZIP archive. Choosing the archive (prepare) and building
it (approve) are separate approvals; building repeats the exact destination. The
working file and its scenes never change: an offline child rewrites a private
snapshot. The ledger keeps quoted costs, never service identifiers, URLs or paths.
Handles add no executor, store or persisted state; restart drops them.
"""

import json
import os
import shutil
import tempfile
import uuid
from dataclasses import dataclass, field
from pathlib import Path

import bpy

from .. import __version__
from ..core.jobs import film_export_policy, local_case_study
from ..core.jobs.local_export import LocalExportError, media_stamp
from ..core.jobs.local_render import blender_path, digest
from ..core.scene import film_case_study
from . import film_jobs, film_scene
from .film_application import _main_thread
from .film_case_study_worker import embedded, removed, strip_file
from .film_export import _LIMIT, _OPEN, _SOURCES, FilmExportCommands, _Export, _readable

STALE = (
    "The Film recipe, review, timeline or shot scenes, frame or connection changed, or a"
    " scene was selected again; prepare the case study again"
)
CHANGED = (
    "The case-study scenes, media or ledger changed after it was prepared; prepare the"
    " case study again"
)
TIMELINE = "editable_shot_plan"
_BLOCKED_STRIPS = frozenset({"MOVIECLIP", "MASK"})
# Movies first, so a movie also read as sound keeps its movie name.
_PRIORITY = {"movie": 0, "image": 1, "sound": 2, "font": 3}
_SCENE_STRIPS = "Scene strips belong only in the director timeline; remove them from the review"


@dataclass(frozen=True, eq=False)
class _Source:
    identifier: str
    scene: object = field(repr=False)
    name: str
    kind: str
    origin: object
    binding: tuple = field(repr=False)
    digest: str
    default_name: str
    frames: int
    fps: int
    width: int
    height: int
    strips: int
    media: int
    blocked: str


@dataclass(frozen=True, eq=False)
class _Timeline:
    identifier: str
    scene: object = field(repr=False)
    name: str
    origin: object
    binding: tuple = field(repr=False)
    shots: tuple = field(repr=False)
    blocked: str


@dataclass(eq=False)
class _CaseStudy(_Export):
    timeline: object = field(default=None, repr=False)
    summary: dict = field(default_factory=dict, repr=False)


@dataclass(frozen=True)
class _Collected:
    scenes: tuple
    media: tuple
    markers: frozenset


def _closure(roots, users):
    """The data blocks a library write of ``roots`` includes: everything they use."""
    reachable, changed = set(roots), True
    while changed:
        changed = False
        for datablock, owners in users.items():
            if datablock not in reachable and not owners.isdisjoint(reachable):
                reachable.add(datablock)
                changed = True
    return reachable


# Extension settings that describe the scene rather than name an account, job, asset,
# upload or local file: model choices (recipes keep model IDs too), parameter and
# input names, kinds and values, titles, labels, prompts and object names, which
# the bundle carries anyway. Every other setting value is scanned for, so a new
# setting counts as private until it is listed here.
_DESCRIPTIVE_SETTINGS = frozenset(
    {
        "description",
        "kind",
        "label",
        "lane",
        "model_id",
        "model_key",
        "multi_value",
        "name",
        "options",
        "param_name",
        "prompt",
        "ptype",
        "refine",
        "source_object",
        "spark_look",
        "str_value",
        "title",
    }
)


def _values(value, descriptive=frozenset()):
    """Every string an ID property holds: nested groups, group and string lists, bytes."""
    if hasattr(value, "to_dict"):
        value = value.to_dict()
    elif hasattr(value, "to_list"):
        value = value.to_list()
    if isinstance(value, dict):
        for key, item in value.items():
            if key not in descriptive:
                yield from _values(item, descriptive)
    elif isinstance(value, (list, tuple, set, frozenset)):
        for item in value:
            yield from _values(item, descriptive)
    elif isinstance(value, (bytes, bytearray)):
        try:
            yield bytes(value).decode("utf-8")
        except UnicodeDecodeError:
            pass  # Not text, so not an identifier a reader could recognize.
    elif isinstance(value, str):
        yield value


def _markers(owner):
    """Identifier-like values of the Scenario properties and settings the bundle removes."""
    custom, settings = removed(owner)
    found = set()
    try:
        values = [value for item in custom for value in _values(item)]
        values += [value for item in settings for value in _values(item, _DESCRIPTIVE_SETTINGS)]
    except RecursionError:
        raise ValueError("Scenario properties are nested too deeply for a case study") from None
    for value in values:
        if 12 <= len(value) <= 256 and not any(c.isspace() or not c.isprintable() for c in value):
            found.add(value)
    return found


def _collect(roots, timeline, users=None):
    """Stamp every external file the chosen scenes read; refuse what cannot travel."""
    reachable = _closure(roots, bpy.data.user_map() if users is None else users)
    kinds, markers, scenes = {}, set(), []

    def add(raw, kind):
        path = Path(os.path.normpath(bpy.path.abspath(raw)))
        if not path.is_absolute():
            raise LocalExportError(
                "Case-study media must use absolute or saved-file-relative paths"
            )
        kinds.setdefault(path, set()).add(kind)

    for datablock in reachable:
        if datablock.library is not None or datablock.override_library is not None:
            raise ValueError("Make linked or overridden data local before exporting a case study")
        if isinstance(datablock, (bpy.types.MovieClip, bpy.types.CacheFile, bpy.types.Volume)):
            raise ValueError("Remove movie clips, cache files and volumes from the case study")
        for owner in embedded(bpy, datablock):
            markers |= _markers(owner)
        if isinstance(datablock, bpy.types.Image):
            if datablock.type not in {"IMAGE", "MULTILAYER"} or datablock.source in {
                "GENERATED",
                "VIEWER",
            }:
                continue
            if datablock.packed_file is not None:
                if len(datablock.packed_files) != 1:
                    raise ValueError("Use single-file packed images in the case study")
            elif datablock.source == "FILE":
                add(datablock.filepath_raw, "image")
            else:
                raise ValueError("Use single-file or packed image textures in the case study")
        elif isinstance(datablock, bpy.types.Sound):
            if datablock.packed_file is None:
                add(datablock.filepath, "sound")
        elif isinstance(datablock, bpy.types.VectorFont):
            if datablock.filepath != "<builtin>" and datablock.packed_file is None:
                add(datablock.filepath, "font")
        elif isinstance(datablock, bpy.types.Scene):
            scenes.append(datablock)
            editor = datablock.sequence_editor
            for strip in tuple(editor.strips_all) if editor is not None else ():
                markers |= _markers(strip)
                if strip.type in _BLOCKED_STRIPS:
                    raise ValueError("Remove clip and mask strips before exporting a case study")
                if strip.type == "SCENE" and datablock != timeline:
                    raise ValueError(_SCENE_STRIPS)
                if strip.type == "MOVIE":
                    add(strip.filepath, "movie")
                elif strip.type == "IMAGE":
                    for element in strip.elements:
                        add(strip_file(strip.directory, element.filename), "image")
                elif strip.type == "SOUND":
                    if strip.sound is None or strip.sound.packed_file is not None:
                        raise ValueError("Unpack sound strip audio before exporting a case study")
                    if len(strip.modifiers) or len(getattr(strip, "retiming_keys", ())):
                        raise ValueError(
                            "Remove sound strip modifiers and retiming before exporting a case study"
                        )
    if len(scenes) > local_case_study.MAX_SCENES:
        raise ValueError("Bundle at most 256 scenes")
    if len(kinds) > local_case_study.MAX_MEDIA:
        raise LocalExportError("Bundle at most 2,000 media files")
    ordered = sorted(
        ((min(found, key=_PRIORITY.__getitem__), path) for path, found in kinds.items()),
        key=lambda item: (_PRIORITY[item[0]], str(item[1])),
    )
    media = tuple(
        local_case_study.BundleMedia(
            media_stamp(path), local_case_study.media_name(index, kind, path), kind
        )
        for index, (kind, path) in enumerate(ordered, 1)
    )
    if sum(item.stamp.size for item in media) > local_case_study.MAX_MEDIA_BYTES:
        raise LocalExportError("Bundle at most 16 GiB of distinct media files")
    names = sorted({scene.name for scene in scenes})
    return _Collected(tuple(names), media, frozenset(markers))


def _timeline_shots(candidate, plan, production, digest):
    """The timeline's scene strips must name the current shots in recipe order."""
    editor = candidate.sequence_editor
    strips = sorted(
        (
            strip
            for strip in (editor.strips_all if editor is not None else ())
            if strip.type == "SCENE"
        ),
        key=lambda strip: strip.frame_final_start,
    )
    shots = tuple(strip.scene for strip in strips)
    current = len(shots) == len(plan["shots"]) and all(
        shot_scene is not None
        and film_scene.matching_shot(
            shot_scene, production_id=production, digest=digest, shot=shot, fps=plan["fps"]
        )
        for shot_scene, shot in zip(shots, plan["shots"], strict=False)
    )
    return shots, "" if current else "Rebuild the director timeline from the current shots"


class FilmCaseStudyCommands(FilmExportCommands):
    """Bounded case-study handles; the session owns the export thread and children."""

    _SUFFIX = ".zip"
    _STALE = STALE

    def __init__(self, session, store):
        super().__init__(session)
        self.store = store
        self._timelines = {}

    @staticmethod
    def _timeline_digest(binding):
        return film_scene.timeline_recipe(json.loads(binding[1]), binding[0])

    def _is_timeline(self, candidate, binding, digest):
        try:
            return (
                not candidate.library
                and not candidate.override_library
                and candidate.get("scenario_film_sequence_kind") == TIMELINE
                and candidate.get("scenario_film_production_id") == binding[0]
                and candidate.get("scenario_film_recipe_sha256") == digest
            )
        except ReferenceError:
            return False

    @staticmethod
    def _bounds(candidate):
        """A review scene's listed frames, rate, output size and strip count."""
        render, editor = candidate.render, candidate.sequence_editor
        percentage = render.resolution_percentage
        return (
            candidate.frame_end - candidate.frame_start + 1,
            render.fps,
            render.resolution_x * percentage // 100,
            render.resolution_y * percentage // 100,
            len(editor.strips_all) if editor is not None else 0,
        )

    def _review_source(self, candidate, kind, binding, digest, default_name, users):
        frames, fps, width, height, strips = self._bounds(candidate)
        media, blocked = 0, ""
        if not strips:
            blocked = "Add sequencer strips to this review scene"
        else:
            try:
                media = len(_collect((candidate,), None, users).media)
            except (ValueError, LocalExportError) as error:
                blocked = str(error)
        return _Source(
            uuid.uuid4().hex,
            candidate,
            candidate.name,
            kind,
            self.session.capture(candidate),
            binding,
            digest,
            default_name,
            frames,
            fps,
            width,
            height,
            strips,
            media,
            blocked,
        )

    def inspect(self, scene):
        """List built review scenes and director timelines as opaque choices; writes nothing."""
        _main_thread()
        binding, plan, digest = self._recipe(scene)
        timeline_plan, timeline_digest = self._timeline_digest(binding)
        default_name = local_case_study.bundle_names(plan["title"])[2]
        users = bpy.data.user_map()
        sources, timelines = {}, {}
        for candidate in tuple(bpy.data.scenes):
            kind = self._kind(candidate, binding, digest)
            timeline = kind is None and self._is_timeline(candidate, binding, timeline_digest)
            if kind is None and not timeline:
                continue
            if len(sources) + len(timelines) >= _SOURCES:
                raise ValueError("Keep at most 256 Film review scenes and timelines")
            shots, blocked = (
                _timeline_shots(candidate, timeline_plan, binding[0], timeline_digest)
                if timeline
                else ((), "")
            )
            # Synchronize each scene's own view layers as the snapshot will (the Blender
            # 5.2 copy guard). This evaluation is not an edit, so it keeps the origins of
            # a ready case study of the same scenes; approval rereads what it showed.
            synchronized = (candidate, *(shot for shot in shots if shot is not None))
            with self.session.synchronizing(synchronized):
                for item in synchronized:
                    for layer in item.view_layers:
                        layer.update()
            if not timeline:
                source = self._review_source(candidate, kind, binding, digest, default_name, users)
                sources[source.identifier] = source
                continue
            if not blocked:
                try:
                    _collect((candidate,), candidate, users)
                except (ValueError, LocalExportError) as error:
                    blocked = str(error)
            item = _Timeline(
                uuid.uuid4().hex,
                candidate,
                candidate.name,
                self.session.capture(candidate),
                binding,
                tuple(
                    (shot, self.session.capture(shot), shot.name)
                    for shot in shots
                    if shot is not None and not blocked
                ),
                blocked,
            )
            timelines[item.identifier] = item
        self._sources, self._timelines = sources, timelines
        return {
            "production_id": binding[0],
            "scene": scene.name,
            "default_name": default_name,
            "sources": [self._choice(source) for source in sources.values()],
            "timelines": [
                {
                    "timeline_id": item.identifier,
                    "scene": item.name,
                    "shots": len(item.shots),
                    "blocked_reason": item.blocked,
                }
                for item in timelines.values()
            ],
        }

    @staticmethod
    def _choice(source):
        return {
            "source_id": source.identifier,
            "scene": source.name,
            "kind": source.kind,
            "frames": source.frames,
            "fps": source.fps,
            "width": source.width,
            "height": source.height,
            "strips": source.strips,
            "media": source.media,
            "blocked_reason": source.blocked,
            "default_name": source.default_name,
        }

    def _check(self, review, *, selected=False):
        source, timeline = review.source, review.timeline
        try:
            current = (
                self.session.active
                and review.scene in tuple(bpy.data.scenes)
                and not review.scene.library
                and not review.scene.override_library
                and film_jobs.snapshot(review.scene) == review.binding
                and self.session._origins.current(review.origin)
                and source.scene in tuple(bpy.data.scenes)
                and self._kind(source.scene, review.binding, source.digest) == source.kind
                and self.session._origins.current(source.origin)
            )
            if current and timeline is not None:
                _, digest = self._timeline_digest(review.binding)
                current = (
                    timeline.scene in tuple(bpy.data.scenes)
                    and self._is_timeline(timeline.scene, review.binding, digest)
                    and self.session._origins.current(timeline.origin)
                    and all(
                        shot in tuple(bpy.data.scenes) and self.session._origins.current(origin)
                        for shot, origin, _ in timeline.shots
                    )
                )
        except ReferenceError:
            current = False
        if not current:
            raise ValueError(STALE)
        if selected:
            self.session.validate_destination(review.origin)

    def _documents(self, review, collected):
        """The sanitized recipe, ledger, manifest base and guide, plus scan markers."""
        raw, plan = film_jobs.recipe(review.scene)
        production = review.binding[0]
        # The score the review was built with, which need not be the default task.
        score = film_case_study.review_score_task(
            plan, review.source.scene.get("scenario_audio_mix")
        )
        candidates = film_case_study.approved_task_ids(plan, None, score_task_id=score)
        records, uploads = {}, {}
        for task_id in candidates:
            record = self.store.film_job(production, task_id)
            upload = self.store.film_upload(production, task_id) if record is None else None
            if record is not None:
                records[task_id] = record
            if upload is not None:
                uploads[task_id] = upload
        known = {task["id"] for task in plan["tasks"]} | set(records) | set(uploads)
        task_ids = film_case_study.approved_task_ids(plan, known, score_task_id=score)
        scope = self.session.scope
        private = {production, scope.account_id, scope.project_id, scope.team_id}
        private |= collected.markers
        for record in records.values():
            private |= {record.intent.request_id, record.remote_job_id}
            private |= {item.asset.asset_id for item in record.results}
            private |= {item.application_id for item in record.local_applications}
        for upload in uploads.values():
            private |= {upload.upload_request_id, upload.asset_id}
        private.discard(None)
        source, timeline = review.source, review.timeline
        shots = [name for _, _, name in timeline.shots] if timeline is not None else []
        top, blend, _ = local_case_study.bundle_names(plan["title"])
        texts = film_case_study.documents(
            raw,
            plan,
            task_ids=task_ids,
            records=records,
            uploads=uploads,
            private=private,
            blend=blend,
            scenes={
                "review": source.name,
                "review_kind": source.kind,
                "timeline": timeline.name if timeline is not None else None,
                "shots": shots,
                "all": list(collected.scenes),
            },
            frame=source.scene.frame_start,
            generator=f"Open Studio {__version__}",
        )
        markers = tuple(
            sorted(value for value in private if isinstance(value, str) and 12 <= len(value) <= 256)
        )
        return (
            (top, blend),
            tuple((name, texts[name]) for name in local_case_study.DOCUMENTS),
            markers,
        )

    def _summary(self, review, collected, documents):
        ledger = json.loads(dict(documents)[film_case_study.LEDGER_FILE])
        return {
            "scenes": len(collected.scenes),
            "media": len(collected.media),
            "media_bytes": sum(item.stamp.size for item in collected.media),
            "approved_tasks": len(ledger["tasks"]),
            "priced_tasks": ledger["priced_tasks"],
            "quoted_cost_total": ledger["quoted_cost_total"],
        }

    def _roots(self, review):
        timeline = review.timeline
        return (review.source.scene, *((timeline.scene,) if timeline is not None else ()))

    def prepare(self, scene, *, source_id, destination, timeline_id=None, within=None):
        """Bind one review scene, an optional timeline and a new ZIP; nothing is written yet.

        ``within`` restricts the destination to that folder (the agent policy);
        native file browser choices pass None and get the session policy only.
        """
        _main_thread()
        binding, _, digest = self._recipe(scene)
        source = self._sources.get(source_id) if isinstance(source_id, str) else None
        if source is None or source.binding != binding or source.digest != digest:
            raise ValueError("Use a source from the current Film case-study inspection")
        if source.blocked:
            raise ValueError(source.blocked)
        timeline = None
        if timeline_id is not None:
            timeline = self._timelines.get(timeline_id) if isinstance(timeline_id, str) else None
            if timeline is None or timeline.binding != binding:
                raise ValueError("Use a timeline from the current Film case-study inspection")
            if timeline.blocked:
                raise ValueError(timeline.blocked)
        self.poll()
        for existing in self._reviews.values():
            try:
                same = existing.source.scene == source.scene
            except ReferenceError:
                same = False
            if same and existing.phase in _OPEN:
                raise ValueError("Finish or discard this review scene's current case study first")
        retired = None
        if len(self._reviews) >= _LIMIT:
            retired = next(
                (
                    key
                    for key, item in self._reviews.items()
                    if item.phase not in _OPEN and item.directory is None and item.task is None
                ),
                None,
            )
            if retired is None:
                raise ValueError("Discard a previous Film case study before preparing another")
        resolved = self._destination(destination, self._root(), within)
        # Flush pending depsgraph updates so they cannot invalidate the new origin.
        bpy.context.view_layer.update()
        review = _CaseStudy(
            uuid.uuid4().hex,
            scene,
            scene.name,
            binding,
            self.session.capture(scene),
            source,
            resolved,
            source.width,
            source.height,
            False,
            Path(within) if within is not None else None,
            timeline=timeline,
        )
        self._check(review, selected=True)
        collected = _collect(self._roots(review), timeline.scene if timeline else None)
        _, texts, _ = self._documents(review, collected)
        review.summary = self._summary(review, collected, texts)
        if retired is not None:
            del self._reviews[retired]
        self._reviews[review.identifier] = review
        return self.status(review.identifier)

    def _snapshot(self, review):
        """Write the private snapshot and specification on the main thread.

        The approval covers the review bounds, counts, size and ledger shown when
        preparing; any change refuses the build, including a media file or saved job
        that changed without touching a scene.
        """
        source, timeline = review.source, review.timeline
        shown = (source.frames, source.fps, source.width, source.height, source.strips)
        if self._bounds(source.scene) != shown:
            raise ValueError(CHANGED)
        collected = _collect(self._roots(review), timeline.scene if timeline else None)
        names, texts, markers = self._documents(review, collected)
        if self._summary(review, collected, texts) != review.summary:
            raise ValueError(CHANGED)
        directory = Path(tempfile.mkdtemp(prefix=local_case_study.STAGING_PREFIX, dir=self._root()))
        try:
            root = directory / local_case_study.BUNDLE
            root.mkdir(mode=0o700)
            snapshot = root / local_case_study.SNAPSHOT
            # Scenes no window shows keep tags from changes elsewhere until now. This
            # evaluation is not an edit (JobSession.synchronizing); the rereads above
            # cover what the approval showed.
            scenes = tuple(bpy.data.scenes[name] for name in collected.scenes)
            with self.session.synchronizing(scenes):
                for scene in scenes:
                    for layer in scene.view_layers:
                        layer.update()
            previous = bpy.data.filepath
            bpy.data.libraries.write(
                blender_path(snapshot),
                set(self._roots(review)),
                path_remap="ABSOLUTE",
                compress=True,
            )
            os.chmod(snapshot, 0o600)
            if bpy.data.filepath != previous:
                raise RuntimeError("Snapshot unexpectedly changed the working file")
            # Extension storage (staging, private review copies) and the source media
            # folders must not survive anywhere in the rewritten library either.
            package = __package__.rsplit(".", 1)[0]
            folders = {str(Path(bpy.utils.extension_path_user(package)).resolve())}
            folders |= {str(item.stamp.path.parent) for item in collected.media}
            markers += tuple(sorted(value for value in folders if 12 <= len(value) <= 256))
            return local_case_study.CaseStudySpec(
                directory,
                Path(bpy.app.binary_path).resolve(),
                Path(__file__).with_name("film_case_study_worker.py").resolve(),
                digest(snapshot),
                names[0],
                names[1],
                collected.scenes,
                collected.media,
                texts,
                markers,
            )
        except BaseException:
            shutil.rmtree(directory, ignore_errors=True)
            raise

    def approve(self, identifier, *, approved_destination, within=None):
        """Separately approve one ready case study; consumed even if it cannot start.

        ``within`` is the approver's folder policy: an agent approval passes the
        Output Folder, so it cannot approve a native choice elsewhere.
        """
        _main_thread()
        self.poll()
        review = self._get(identifier)
        if review.phase != "READY":
            raise ValueError("Prepare and approve a fresh ready Film case study")
        if type(approved_destination) is not str or approved_destination != review.destination:
            raise ValueError("Approve the exact destination shown for this Film case study")
        if within is not None:
            # Refused without consuming: the handle stays for an approver allowed to use it.
            film_export_policy.require_within(review.destination, within)
        if bpy.context.mode != "OBJECT":
            raise ValueError("Switch to Object Mode to export a case study")
        if self._running():
            raise ValueError("Wait for the current Film export to finish")
        self._check(review, selected=True)
        review.phase = "EXPORTING"
        try:
            if review.within is not None:
                film_export_policy.require_within(review.destination, review.within)
            spec = self._snapshot(review)
            review.directory = spec.directory
            review.task = self.session.export_case_study(
                spec, review.destination, origin=review.origin, source_origin=review.source.origin
            )
        except Exception as error:
            review.phase = "ERROR"
            review.error = _readable(error, "The case study could not start; prepare it again")
            # Nothing ran, so the unused snapshot is removed rather than kept for diagnosis.
            self._remove(review)
        else:
            review.progress = review.task.progress()
        return self.status(identifier)

    def status(self, identifier):
        _main_thread()
        review = self._get(identifier)
        source, timeline, published = review.source, review.timeline, review.published
        stage, done, total = review.progress
        exported = review.phase == "EXPORTED" and published is not None
        summary = review.summary
        return {
            "review_id": identifier,
            "production_id": review.binding[0],
            "phase": review.phase,
            "scene": review.scene_name,
            "source_id": source.identifier,
            "source_scene": source.name,
            "kind": source.kind,
            "timeline_scene": timeline.name if timeline is not None else "",
            "shots": len(timeline.shots) if timeline is not None else 0,
            "scenes": summary.get("scenes", 0),
            "media": summary.get("media", 0),
            "media_bytes": summary.get("media_bytes", 0),
            "approved_tasks": summary.get("approved_tasks", 0),
            "priced_tasks": summary.get("priced_tasks", 0),
            "quoted_cost_total": summary.get("quoted_cost_total"),
            "destination": review.destination,
            "output_folder_only": review.within is not None,
            "pending_destination": review.pending,
            "progress": {"stage": stage, "done": done, "total": total},
            "percent": min(100, done * 100 // total) if total else 0,
            "published": exported,
            "sha256": published.sha256 if exported else "",
            "size": published.size if exported else 0,
            "republish_available": review.phase == "PUBLISH_FAILED" and review.staged is not None,
            "directory": str(review.directory) if review.directory is not None else "",
            "error": review.error,
        }

    def close(self):
        super().close()
        self._timelines.clear()
