# SPDX-FileCopyrightText: 2026 Scenario Inc.
# SPDX-License-Identifier: GPL-3.0-or-later
"""Main-thread model generation commands shared by UI and local MCP."""

import json
import logging
import os
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field, replace

import bpy
from mathutils import Matrix

from ..core.api.catalog import GENERATION_LANES, LANE_KIND
from ..core.api.errors import ScenarioError
from ..core.api.sdk_adapter import ESTIMATE_STARTED_JOB, EstimateStartedJob
from ..core.jobs import failures, progress, workflow_nodes
from ..core.jobs.coordinator import (
    UNCONFIRMED,
    CancellationUnsupported,
    DecisionRefused,
    DecisionUncertain,
    RemoteSnapshot,
    cancellable_job,
)
from ..core.jobs.preview_scheduler import BATCH_LIMIT
from ..core.jobs.records import JobRecord
from ..core.jobs.results import ResultError
from ..core.jobs.store import (
    JobOrigin,
    JobState,
    LocalApplicationState,
    StoreConflict,
    StoredJob,
    _identity,
)
from ..core.jobs.workers import WorkerError
from ..core.scene import panorama_contract
from ..core.scene.model_formats import MATERIAL_MEDIA_TYPES, ModelFormat, model_format
from ..core.scene.panorama import is_world_size, world_candidate
from ..core.scene.splats import SplatCancelled, SplatOptions
from ..core.ui.costs import workflow_quote_notice
from ..core.ui.saved_job_actions import NATIVE_ONLY, WorkflowSteps
from . import file_state, first_frame_handoff
from .blockout_jobs import MODEL as BLOCKOUT_MODEL
from .job_session import (
    ImageResultUncertain,
    MaterialResultUncertain,
    MediaResultUncertain,
    ModelResultUncertain,
    WorldResultUncertain,
)
from .material_application import MaterialApplicationError, capture_target, selected_maps
from .material_application import validate_target as validate_material_target
from .media_application import MEDIA_TYPES
from .mesh_application import MeshApplicationError
from .mesh_application import capture_target as capture_mesh_target
from .mesh_application import validate_target as validate_mesh_target
from .mesh_result_application import MeshEditApplication, MeshResultApplicationError
from .mesh_result_application import validate_request as validate_mesh_request
from .model_application import (
    MODEL_MEDIA_TYPE,
    ModelApplication,
    ModelPreflightError,
    import_plan,
    importable,
    worker_prepared,
)
from .model_application import validate_destination as validate_model_destination
from .splat_application import AXES as SPLAT_AXES
from .splat_application import DEFAULT_POINTS, POINT_CHOICES, default_axes
from .world_application import PanoramaError, WorldApplicationError

# Shared by the native operator and MCP recover_local_job through control().
CANCEL_UNSUPPORTED = (
    "Scenario documents cancellation only for inference jobs and has not confirmed "
    "this job as one; nothing was sent and the job keeps running"
)
# Shared by the native World operator and MCP prepare_result_application.
NOT_WORLD_SIZE = "This saved image is not a 2:1 panorama within the World pixel limit"
# Native review notes for a submission Scenario never confirmed; MCP keeps the error.
UNCONFIRMED_REVIEW = (
    "Scenario may have started this request. Check your Scenario history; "
    "if it isn't there, choose Mark reviewed, then you can send it again."
)
UNCONFIRMED_REVIEWED = (
    "Marked reviewed for this session: an identical request can be sent again. "
    "Check your Scenario history first, or you may be charged twice."
)

_log = logging.getLogger("scenario.jobs")

# Session-local first-frame outcomes reported by status; never persisted.
_FIRST_FRAME_HISTORY = 128

# Seconds between automatic refreshes of a known remote job. A reading becomes
# stale once its scheduled refresh is overdue by the grace, so a longer
# scheduled interval never reads as stale merely because it is longer.
POLL_INTERVAL = 2.0
STALE_GRACE = 2 * POLL_INTERVAL
# While a workflow waits for a decision, poll less often: each retrieval
# re-signs up to 50 candidate URLs, and only a decision moves the job on.
STEP_POLL_INTERVAL = 15.0
DECIDE = "decide_workflow_node"
# What happened to one finished decision command; see ModelJobs.decision_outcome.
DECISION_OUTCOMES = ("applied", "refused", "uncertain", "not_sent")


class AutomaticEstimatesStopped(ScenarioError):
    """A price check returned a job; nothing was resubmitted and pricing stopped."""

    def __init__(self, error):
        super().__init__(
            0,
            f"{error}. Automatic prices are off until Blender restarts; once that job finishes, "
            "save it from history.",
        )


def automatic_estimates_stopped():
    """A price check returned a job in this Blender session, through any connection or path."""
    return ESTIMATE_STARTED_JOB.is_set()


# Saved states this session never advances by itself; READY is settled too
# unless an automatic image import is still pending.
_SETTLED = frozenset(
    {
        JobState.UNCERTAIN,
        JobState.FAILED,
        JobState.CANCELED,
        JobState.DOWNLOAD_FAILED,
        JobState.APPLY_FAILED,
        JobState.APPLIED,
    }
)
# Saved states whose next automatic step contacts Scenario: status polling or,
# for a job whose results this session downloads, the download. Disabled Online
# Access holds them until it is allowed again; Jobs rows draw an offline line
# and MCP reports `delivery_offline` for a job this session advances by itself.
NEEDS_SCENARIO = frozenset({JobState.REMOTE, JobState.CANCEL_REQUESTED, JobState.SUCCEEDED})
# Saved states whose results the World offer reads sizes for: those it can offer.
_WORLD_STATES = frozenset({JobState.READY, JobState.APPLY_FAILED, JobState.APPLIED})
# Prototype display statuses of shared views, which the Jobs and Generations
# panels split with JobRecord.is_terminal. A SUCCEEDED job whose results this
# session downloads has none saved yet, so it must not read as the terminal
# "succeeded": it stays a Jobs row with its saved state and offline line until
# delivery settles. Other SUCCEEDED jobs keep "succeeded"; see downloads_results.
_DISPLAY_STATUS = {
    JobState.REMOTE: "in-progress",
    JobState.SUCCEEDED: "awaiting-download",
    JobState.APPLIED: "success",
}


class WorkflowCancelUnavailable(ScenarioError):
    """A running workflow cannot be cancelled; nothing is sent.

    Scenario documents job cancellation for inference jobs only and has no
    workflow cancellation endpoint. Rejecting a waiting approval or selection
    step through ModelJobs.decide is the only way to stop a workflow.
    """

    def __init__(self):
        super().__init__(
            0,
            "Scenario cannot cancel a running workflow: it finishes or pauses at its next "
            "approval or selection step, and rejecting a waiting step is the only way to stop it",
        )


def downloads_results(record):
    """Whether ModelJobs saves this job's results by downloading them.

    Decided from the saved intent alone: model and workflow generations do,
    including Film tasks and cloud jobs saved for recovery. Prompt Spark and
    Translate text (`prompt` and `translate` operations) and Blockout plans
    (the text model) are read into memory by their own tools instead, so
    `succeeded` is their last saved state. ModelJobs never downloads them, and
    their views stay terminal, listed in Generations, without an offline hold.
    """
    intent = record.intent
    return intent.operation == "workflow" or (
        intent.operation == "model" and intent.target_id != BLOCKOUT_MODEL
    )


def _panoramas(record):
    """Asset ID -> "ldr" or "hdr" for results whose saved metadata declares a 360 projection.

    An OpenEXR original or file is an HDR environment; any other image is LDR.
    """
    return {
        item.asset.asset_id: "hdr"
        if item.asset.source == "original"
        or item.asset.media_type in panorama_contract.HDR_MEDIA_TYPES
        else "ldr"
        for item in record.results
        if item.asset.projection == "equirectangular"
    }


def _display_status(record):
    if record.state == JobState.SUCCEEDED and not downloads_results(record):
        return record.state.value
    return _DISPLAY_STATUS.get(record.state, record.state.value)


def _snapshot(body):
    return json.dumps(body, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _world_assets(record):
    """Saved World candidates by metadata, in result order; material maps are excluded.

    ModelJobs offers them less those whose header, read for the current
    receipt, declares a size that cannot be a 2:1 World panorama.
    """
    return tuple(
        item.asset.asset_id
        for item in record.results
        if world_candidate(item.asset.media_type, item.asset.texture_role)
    )


def _remember(cache, request_id, values, collection):
    """Retain live session outputs across reuse and deduplicate receipt retries."""
    live = tuple(collection)
    cache[request_id] = tuple(
        dict.fromkeys(value for value in (*cache.get(request_id, ()), *values) if value in live)
    )


@dataclass
class ModelQuote:
    identifier: str
    model_id: str
    scene: object = field(repr=False)
    inputs: str = field(repr=False)
    task: object = field(repr=False)
    lane: str = "image"
    quote: object = field(default=None, repr=False)
    used: bool = False
    operation: str = "model"


@dataclass(frozen=True)
class JobBinding:
    """Display-only origin of a model-lane submission in this session.

    `scene_uid` is Blender's session identifier of the submitting scene, stable
    across renames and undo, and `lane` is the submitted generation lane. A
    binding grants no destination, origin or application authority. It is never
    persisted, so restarted and recovered jobs remain unbound.
    """

    scene_uid: int
    lane: str


@dataclass(frozen=True)
class StepProjection:
    """Waiting workflow steps from one retrieval, bound to the record that produced it."""

    remote_job_id: str
    revision: int
    steps: tuple


@dataclass
class CloudRecovery:
    job_id: str
    model_id: str
    task: object = field(repr=False)
    owner: object = field(repr=False)
    record: StoredJob | None = None
    error: str = ""
    pending: bool = True


@dataclass(frozen=True)
class ImageApplicationApproval:
    identifier: str
    record: StoredJob
    destination: JobOrigin
    scene_name: str


@dataclass(frozen=True)
class MediaApplicationApproval:
    identifier: str
    record: StoredJob
    destination: JobOrigin
    scene_name: str
    asset_id: str
    frame: int
    kind: str


@dataclass(frozen=True)
class ModelApplicationApproval:
    """One reviewed import unit; `asset_id` is its key, the primary member's ID.

    SPZ, .splat and PLY units also carry the reviewed point limit and saved axes;
    mesh units leave both None.
    """

    identifier: str
    record: StoredJob
    destination: JobOrigin
    scene_name: str
    asset_id: str
    cursor: tuple
    kind: str = "model"
    unit: object = field(default=None, repr=False)
    max_points: int | None = None
    axes: str | None = None

    @property
    def splat_options(self):
        return None if self.max_points is None else SplatOptions(self.max_points, self.axes)


@dataclass(frozen=True)
class WorldApplicationApproval:
    identifier: str
    record: StoredJob
    destination: JobOrigin
    scene_name: str
    scene: object = field(repr=False)
    previous: object = field(repr=False)
    asset_id: str
    restore: bool = False
    kind: str = "world"
    media_type: str = ""


@dataclass(frozen=True)
class MaterialApplicationApproval:
    identifier: str
    record: StoredJob
    destination: JobOrigin
    scene_name: str
    target_name: str
    target: object = field(repr=False)
    roles: tuple
    kind: str = "material"


@dataclass(frozen=True)
class MeshApplicationApproval:
    identifier: str
    record: StoredJob
    destination: JobOrigin
    scene_name: str
    target_name: str
    asset_id: str
    target: object = field(repr=False)
    policy: str
    placement: str
    mapping: tuple
    keep_original: bool
    original_source: bool = False
    kind: str = "mesh_edit"


class ModelJobs:
    """Own ephemeral quotes and display projections, never another job engine."""

    def __init__(self, session, store, *, online=lambda: True):
        self.session = session
        self.store = store
        self.quotes = {}
        self.submissions = {}
        self.views = {}
        self._bindings = {}
        self._online = online
        # Online Access as the main thread last saw it. `wait` runs on the MCP
        # HTTP worker, which must not read bpy, so it uses this copy.
        self._online_seen = online()
        self._commands = {}
        self._next_poll = {}
        # In-memory workflow step projections and the last decision's error
        # text with the review IDs then waiting; never persisted.
        self._steps = {}
        self._step_errors = {}
        self._unreadable_steps = set()
        self._paused = set()
        self._images = {}
        self._receipts = {}
        self._automatic_application = set()
        self._application_approvals = {}
        self._application_destinations = {}
        self._media_destinations = {}
        self._model_destinations = {}
        self._objects = {}
        self._world_destinations = {}
        self._worlds = {}
        self._material_destinations = {}
        self._materials = {}
        self._mesh_destinations = {}
        self._mesh_edits = {}
        # request_id -> remote ID whose latest owned retrieval reported a type
        # the API documents as cancellable. Memory only: a restarted or newly
        # inspected job offers no cancellation until an explicit refresh.
        self._cancellable = {}
        # request_id -> failures.RemoteFailure: the reason Scenario gave in the
        # retrieval that observed the failure. Memory only, like progress: a job
        # that failed before a restart shows no reason.
        self._failures = {}
        self._first_frame_destinations = {}
        self._first_frames = {}
        # (request_id, asset_id) -> (receipt SHA-256, declared (width, height) or
        # None): sizes of World candidates read from their file headers on the
        # preview lane, for the World offer only. Memory only; application still
        # checks the actual bytes. Each poll forgets the sizes of jobs no longer in
        # `views`, so memory follows what is shown and a shown job is never read
        # again for the same receipt. `_world_read` is the (task, targets) in flight.
        self._world_sizes = {}
        self._world_read = None
        self._plans = {}
        self._model_imports = {}
        self.cloud_reads = {}
        self._cloud_read_owner = object()

    def recover_cloud(self, job_id, model_id, scene):
        """Save one selected cloud job for explicit recovery, never automatic import."""
        _identity(job_id)
        _identity(model_id)
        self._poll_cloud_reads()
        existing = self.cloud_reads.get(job_id)
        if existing is not None and existing.pending:
            if existing.model_id != model_id:
                raise ScenarioError(0, "This cloud job already has a different model read pending")
            return existing
        if len(self.cloud_reads) >= 16 and job_id not in self.cloud_reads:
            for key, item in tuple(self.cloud_reads.items()):
                if not item.pending:
                    del self.cloud_reads[key]
                    break
            else:
                raise ScenarioError(0, "Wait for a cloud recovery read to finish")
        task = self.session.adopt_cloud_job(job_id, expected_model_id=model_id, scene=scene)
        item = CloudRecovery(job_id, model_id, task, self._cloud_read_owner)
        self.cloud_reads[job_id] = item
        return item

    def _poll_cloud_reads(self):
        if not self.session.active:
            return
        for item in self.cloud_reads.values():
            if not item.pending or not item.task.done():
                continue
            item.pending = False
            try:
                completions = self.session.drain(task=item.task)
                if not completions:
                    raise RuntimeError("Missing cloud read completion")
                record = self.session.deliver_cloud_read(completions[0])
                item.record = record
                self._view(record)
            except Exception:
                item.error = "Could not read this cloud job; inspect saved jobs or retry the read"

    def finish_cloud(self, item):
        # Completed results can leave the bounded UI cache while a deferred MCP
        # caller still owns its handle. Cache membership is not request ownership.
        if (
            not self.session.active
            or not isinstance(item, CloudRecovery)
            or item.owner is not self._cloud_read_owner
        ):
            raise ScenarioError(0, "The cloud recovery context changed; inspect saved jobs")
        self._poll_cloud_reads()
        if item.pending or item.error:
            raise ScenarioError(0, item.error or "The cloud job read is still running")
        return item.record

    def quote(self, scene, model_id, body, *, lane="image"):
        if lane not in GENERATION_LANES:
            raise ScenarioError(0, "Choose a supported generation lane")
        return self._quote(scene, model_id, body, lane=lane, operation="model")

    def quote_workflow(self, scene, workflow_id, body):
        return self._quote(scene, workflow_id, body, lane="workflow", operation="workflow")

    def _quote(self, scene, identifier, body, *, lane, operation):
        snapshot = _snapshot(body)
        if scene == bpy.context.scene:
            # Operators flush pending dependency updates before execute(). Do
            # that before capturing the quote's revision, not at paid dispatch.
            bpy.context.view_layer.update()
        if len(self.quotes) >= 128:
            for key, old in tuple(self.quotes.items()):
                if old.used and old.task.done():
                    self.session.drain(task=old.task)
                    del self.quotes[key]
                    break
            else:
                raise ScenarioError(
                    0, "Too many retained estimates; use an existing quote before requesting more"
                )
        origin = self.session.capture(scene)
        task = getattr(self.session, f"quote_{operation}")(
            identifier, json.loads(snapshot), origin=origin
        )
        quote = ModelQuote(
            uuid.uuid4().hex, identifier, scene, snapshot, task, lane=lane, operation=operation
        )
        self.quotes[quote.identifier] = quote
        return quote

    def finish_quote(self, ticket):
        if self.quotes.get(ticket.identifier) is not ticket or not self.session.active:
            raise ScenarioError(0, "The estimate context changed; estimate again")
        if ticket.quote is None:
            completions = self.session.drain(task=ticket.task)
            if not completions:
                raise ScenarioError(0, "The estimate is still running")
            try:
                ticket.quote = self.session.deliver(completions[0], lambda value, *_: value)
            except EstimateStartedJob as error:
                ticket.used = True
                raise AutomaticEstimatesStopped(error) from None
            except Exception:
                if ticket.operation == "workflow":
                    ticket.used = True
                raise
        return ticket.quote.estimate

    def require_quote(self, quote_id):
        ticket = self.quotes.get(quote_id) if isinstance(quote_id, str) else None
        if ticket is None or ticket.used:
            raise ScenarioError(0, "Use a fresh, unsubmitted estimate; inspect existing jobs first")
        return ticket

    def submit(self, quote_id, scene, model_id, body, *, approved_cost, meta=None, lane="image"):
        ticket = self.require_quote(quote_id)
        estimate = self.finish_quote(ticket)
        if (
            ticket.operation != "model"
            or ticket.scene != scene
            or ticket.lane != lane
            or ticket.model_id != model_id
            or ticket.inputs != _snapshot(body)
            or approved_cost != str(estimate.cost)
        ):
            raise ScenarioError(0, "The request or approved cost changed; estimate again")
        # Consumption precedes persistence: an error may occur after a committed
        # write. A second click must never prepare another intent from this quote.
        ticket.used = True
        return self._submit_quote(
            ticket.quote,
            lane=lane,
            kind=LANE_KIND[lane],
            meta=meta,
            binding=JobBinding(scene.session_uid, lane),
        )

    def submit_workflow(self, quote_id, scene, workflow_id, body, *, approved_cost):
        if os.environ.get("SCENARIO_GUI_PROBE") == "1":
            raise PermissionError("Generation is disabled while an automated GUI probe runs")
        ticket = self.require_quote(quote_id)
        estimate = self.finish_quote(ticket)
        if (
            ticket.operation != "workflow"
            or ticket.scene != scene
            or ticket.model_id != workflow_id
            or ticket.inputs != _snapshot(body)
            or approved_cost != str(estimate.cost)
            or not self._online()
        ):
            raise ScenarioError(0, "Approve the unchanged workflow and exact price while online")
        ticket.used = True
        return self._submit_quote(
            ticket.quote,
            lane="workflow",
            kind="workflow",
            meta={"workflow_loop_steps": estimate.loop_steps},
        )

    def discard_workflow_quote(self, quote_id):
        ticket = self.require_quote(quote_id)
        if ticket.operation != "workflow":
            raise ScenarioError(0, "Use a workflow estimate")
        self.finish_quote(ticket)
        ticket.used = True
        ticket.quote = None

    def submit_film(self, quote, *, approved_cost):
        """Dispatch an owned Film approval through the ordinary saved-job lifecycle."""
        if quote.film_task is None or approved_cost != str(quote.estimate.cost):
            raise ScenarioError(0, "Approve a Film task's unchanged exact price")
        return self._submit_quote(quote, lane="film", kind="film")

    def _submit_quote(self, quote, *, lane, kind, meta=None, binding=None):
        estimate = quote.estimate
        model_id = estimate.target_id
        prepared = self.session.prepare_quote(quote)
        view = JobRecord(
            local_id=prepared.intent.request_id,
            lane=lane,
            kind=kind,
            model_id=model_id,
            body=estimate.payload,
            status="prepared",
            cu_cost=float(estimate.cost),
            created_at=time.time(),
            meta=dict(meta or {}),
        )
        self.views[view.local_id] = view
        # Bind before dispatch so a job that could not be queued stays findable.
        if binding is not None:
            self._bindings[view.local_id] = binding
        view.meta["shared_job"] = True
        if lane == "image":
            self._automatic_application.add(view.local_id)
        try:
            task = self.session.submit(
                prepared, operation=estimate.operation, target_id=model_id, payload=estimate.payload
            )
        except Exception:
            view.error = "Submission could not be queued; inspect the saved job before continuing"
            raise
        self.submissions[view.local_id] = task
        return view

    def poll(self):
        """Advance owned jobs through existing commands; never replay paid work."""
        self._prune_worlds()
        if not self.session.active:
            return
        self._poll_cloud_reads()
        self._finish_world_sizes()
        for request_id, task in tuple(self.submissions.items()):
            if task.done():
                outcomes = self.session.drain(task=task)
                try:
                    record = self.store.get(request_id)
                except Exception:
                    # An unreadable record keeps the review path below; raising here
                    # would keep the entry and stop every later poll, not only this one.
                    record = None
                # Local cancellation before dispatch makes the queued submission
                # fail its stored-state check; that rejection is the intended outcome.
                canceled = record is not None and record.state == JobState.CANCELED
                if outcomes and outcomes[0].error is not None and not canceled:
                    self.views[
                        request_id
                    ].error = "Submission did not complete; inspect the saved job before continuing"
                    self._paused.add(request_id)
                del self.submissions[request_id]
        for request_id, (command, task) in tuple(self._commands.items()):
            if not task.done():
                continue
            del self._commands[request_id]
            if command == DECIDE:
                self._finish_decision(request_id, task)
                continue
            destination = self._application_destinations.pop(request_id, None)
            media = self._media_destinations.pop(request_id, None)
            model = self._model_destinations.pop(request_id, None)
            world = self._world_destinations.pop(request_id, None)
            material = self._material_destinations.pop(request_id, None)
            mesh = self._mesh_destinations.pop(request_id, None)
            first_frame = self._first_frame_destinations.pop(request_id, None)
            observed = None
            try:
                completions = self.session.drain(task=task)
                if not completions:
                    raise RuntimeError("Missing owned result completion")
                completion = completions[0]
                if command == "prepare_model" and isinstance(
                    completion.error, (ResultError, SplatCancelled)
                ):
                    # The worker read or decode stopped before any claim; its
                    # sanitized reason reaches the row like a snapshot preflight.
                    raise ModelPreflightError(str(completion.error)) from None
                if completion.error is not None:
                    raise completion.error
                if command in ("refresh_remote", "cancel_remote"):
                    self._observe_cancellation(request_id, completion.result)
                if command == "verify_first_frame":
                    bound = first_frame_handoff.bind(self.session, completion, first_frame)
                    self._remember_first_frame(request_id, first_frame, bound=bound)
                    self._paused.discard(request_id)
                elif command == "verify_mesh_edit":
                    applied = self.session.apply_recovered_mesh(
                        completion,
                        destination=mesh.destination,
                        asset_id=mesh.asset_id,
                        target=mesh.target,
                        policy=mesh.policy,
                        result_to_source=mesh.mapping,
                        keep_original=mesh.keep_original,
                    )
                    self._remember_model_application(request_id, applied.application)
                    self._paused.discard(request_id)
                elif command == "verify_material":
                    applied = self.session.apply_recovered_material(
                        completion, destination=material.destination, target=material.target
                    )
                    _remember(
                        self._materials,
                        request_id,
                        (applied.application.material,),
                        bpy.data.materials,
                    )
                    self._paused.discard(request_id)
                elif command == "verify_world":
                    self._validate_world(world)
                    applied = self.session.apply_recovered_world(
                        completion, destination=world.destination, asset_id=world.asset_id
                    )
                    self._remember_world(request_id, world.destination, applied.application)
                    self._paused.discard(request_id)
                elif command in ("verify_model", "prepare_model"):
                    applied = self.session.apply_recovered_model(
                        completion,
                        destination=model.destination,
                        asset_id=model.asset_id,
                        cursor=model.cursor,
                        options=model.splat_options,
                    )
                    self._remember_model_application(request_id, applied.application)
                    self._paused.discard(request_id)
                elif command == "verify_media":
                    self.session.apply_recovered_media(
                        completion,
                        destination=media.destination,
                        asset_id=media.asset_id,
                        frame=media.frame,
                    )
                    self._paused.discard(request_id)
                elif command == "verify_results":
                    self.views[request_id].files = [str(p) for p in completion.result.paths]
                    result = (
                        self.session.apply_images(completion)
                        if destination is None
                        else self.session.apply_recovered_images(
                            completion, destination=destination
                        )
                    )
                    _remember(self._images, request_id, result.images, bpy.data.images)
                    self._paused.discard(request_id)
                elif command in ("refresh_remote", "cancel_remote"):
                    observed = completion.result
                self._next_poll[request_id] = time.monotonic() + POLL_INTERVAL
            except first_frame_handoff.FirstFrameHandoffError as error:
                self._remember_first_frame(request_id, first_frame, error=error.reason)
                self._pause(request_id, first_frame_handoff.STOPPED)
            except MaterialResultUncertain as error:
                if error.application is not None:
                    self._receipts[request_id] = error
                    _remember(
                        self._materials,
                        request_id,
                        (error.application.material,),
                        bpy.data.materials,
                    )
                self._pause(request_id, "Material assignment needs recovery; do not apply again")
            except MaterialApplicationError:
                self._pause(
                    request_id,
                    "Material application stopped; inspect saved maps and mesh destination",
                )
            except WorldResultUncertain as error:
                if error.application is not None:
                    self._receipts[request_id] = error
                    self._remember_world(request_id, world.destination, error.application)
                self._pause(
                    request_id, "World assignment needs receipt recovery; do not apply again"
                )
            except (PanoramaError, WorldApplicationError):
                self._pause(
                    request_id,
                    "World import needs a supported, unchanged 2:1 PNG/JPEG/EXR panorama; inspect the saved result",
                )
            except ModelResultUncertain as error:
                if error.application is not None:
                    self._receipts[request_id] = error
                    self._remember_model_application(request_id, error.application)
                self._pause(
                    request_id, "Model application needs receipt recovery; do not apply again"
                )
            except (MeshApplicationError, MeshResultApplicationError):
                self._pause(
                    request_id,
                    "Mesh application stopped; inspect the saved GLB, source and edit policy",
                )
            except ModelPreflightError as error:
                # Rejected before the claim: the saved job and the scene are unchanged.
                self._pause(request_id, f"Nothing was imported: {error}")
            except MediaResultUncertain as error:
                if error.application is not None:
                    self._receipts[request_id] = error
                self._pause(
                    request_id, "Media insertion needs receipt recovery; do not insert again"
                )
            except ImageResultUncertain as error:
                if error.images:
                    self._receipts[request_id] = error
                    _remember(self._images, request_id, error.images, bpy.data.images)
                self._pause(request_id, "Image import needs receipt recovery; do not import again")
            except CancellationUnsupported:
                # The coordinator refused before any claim or action; the job keeps running.
                self._cancellable.pop(request_id, None)
                self._pause(request_id, CANCEL_UNSUPPORTED)
            except Exception:
                if command == "verify_first_frame":
                    # Verification failed before binding: the form is unchanged.
                    self._remember_first_frame(request_id, first_frame, error=None)
                    self._pause(request_id, first_frame_handoff.STOPPED)
                else:
                    self._pause(
                        request_id,
                        "Result delivery stopped; review the saved job before retrying",
                    )
            if observed is not None:
                # Display only: outside the delivery error mapping, so a failed
                # projection can never pause delivery or skip the next poll.
                self._observe(request_id, observed)
        online = self._online_seen = self._online()
        unread_sizes = []
        for request_id, view in self.views.items():
            record = self.store.get(request_id)
            if record is None:
                raise ScenarioError(
                    0, "The saved job is unavailable; preserve storage for recovery"
                )
            if record.state != JobState.REMOTE:
                self._cancellable.pop(request_id, None)
            view.job_id = record.remote_job_id
            view.status = _display_status(record)
            view.asset_ids = [item.asset.asset_id for item in record.results]
            view.asset_types = {
                item.asset.asset_id: item.asset.media_type for item in record.results
            }
            # Drawing reads the World offer here; it has no saved texture roles or sizes.
            view.meta["world_assets"] = self._world_offer(record)
            unread_sizes.extend(self._unread_world_sizes(record))
            # Server-declared 360 results, for their label and the Make HDRI prefill.
            view.meta["panoramas"] = _panoramas(record)
            view.meta["panorama_inputs"] = tuple(
                item.asset.asset_id
                for item in record.results
                if panorama_contract.is_ldr_panorama(item)
            )
            view.meta["saved_revision"] = record.revision
            view.meta["saved_state"] = record.state.value
            self._project_steps(request_id, record)
            view.meta["recovery_actions"] = self.actions(record)
            # Native first-frame controls name only the stills a review accepts.
            view.meta["first_frame_assets"] = tuple(
                item.asset.asset_id for item in first_frame_handoff.eligible_assets(record)
            )
            view.meta["import_units"] = self._unit_rows(record)
            view.meta["import_notices"] = self._import_notices(request_id)
            self._project_remote(request_id, record, view, online=online)
            view.meta["error_title"] = view.meta["review_note"] = None
            if record.state in (JobState.SUBMITTING, JobState.UNCERTAIN):
                # The error is shared with MCP job_status; only Blender can mark it reviewed.
                view.meta["error_title"] = "Submission not confirmed"
                view.meta["review_note"] = self._unconfirmed_note(request_id)
                view.error = "Submission outcome is not confirmed; do not submit it again"
            elif any(
                item.state == LocalApplicationState.APPLYING for item in record.local_applications
            ):
                view.meta["error_title"] = "Application not confirmed"
                view.error = "Local application outcome is unconfirmed; inspect the scene and do not repeat it"
            elif record.state == JobState.FAILED:
                # Nothing remains to deliver: a failed job's only error is Scenario's reason.
                view.error = self._failure_reason(request_id, record)
            elif request_id not in self._paused:
                view.error = None
            if (
                request_id in self._paused
                or request_id in self.submissions
                or request_id in self._commands
            ):
                continue
            command = None
            if record.state in (JobState.REMOTE, JobState.CANCEL_REQUESTED):
                if online and time.monotonic() >= self._next_poll.get(request_id, 0):
                    command = "refresh_remote"
            elif record.state == JobState.SUCCEEDED and online and downloads_results(record):
                command = "download_results"
            elif record.state == JobState.READY and request_id in self._automatic_application:
                command = "verify_results"
            if command:
                try:
                    task = getattr(self.session, command)(
                        request_id, expected_revision=record.revision
                    )
                    self._commands[request_id] = (command, task)
                except Exception:
                    self._pause(request_id, "Result delivery could not start; inspect saved jobs")
        self._prune_world_sizes()
        self._read_world_sizes(unread_sizes)

    def _unread_world_sizes(self, record):
        """World candidates of an offerable job whose current receipt has no read size."""
        if record.state not in _WORLD_STATES:
            return ()
        request_id = record.intent.request_id
        return tuple(
            (request_id, item.asset.asset_id, item.receipt.sha256)
            for item in record.results
            if item.receipt is not None
            and world_candidate(item.asset.media_type, item.asset.texture_role)
            and self._world_sizes.get((request_id, item.asset.asset_id), (None,))[0]
            != item.receipt.sha256
        )

    def _read_world_sizes(self, targets):
        """Queue one preview-lane header read of unread World candidates; nothing is read here."""
        if self._world_read is not None or not targets:
            return
        # One preview batch at a time: the rest stay unread, and the poll that
        # records this read queues the next batch.
        targets = tuple(targets[:BATCH_LIMIT])
        try:
            task = self.session.world_sizes(targets)
        except Exception:
            # A full or retired preview lane: the offer keeps them; a later poll reads them.
            return
        self._world_read = (task, targets)

    def _finish_world_sizes(self):
        """Record a finished header read; a failed read leaves those sizes unknown."""
        if self._world_read is None or not self._world_read[0].done():
            return
        task, targets = self._world_read
        self._world_read = None
        try:
            read = {tuple(item[:3]): item[3] for item in task.result()}
        except Exception:
            read = {}
        for request_id, asset_id, sha256 in targets:
            self._world_sizes[(request_id, asset_id)] = (
                sha256,
                read.get((request_id, asset_id, sha256)),
            )

    def _prune_world_sizes(self):
        """Forget the sizes of jobs no longer in `views`; shown ones keep theirs."""
        for key in tuple(self._world_sizes):
            if key[0] not in self.views:
                del self._world_sizes[key]

    def _not_world(self, record):
        """World candidates whose header, read for the current receipt, cannot be 2:1."""
        request_id = record.intent.request_id
        receipts = {
            item.asset.asset_id: item.receipt.sha256
            for item in record.results
            if item.receipt is not None
        }
        refused = set()
        for asset_id in _world_assets(record):
            sha256, size = self._world_sizes.get((request_id, asset_id), (None, None))
            if size is not None and sha256 == receipts.get(asset_id) and not is_world_size(*size):
                refused.add(asset_id)
        return refused

    def _world_offer(self, record):
        """World candidates this session offers, in result order.

        A candidate whose size is not read yet, or whose header declares none,
        stays offered: the confirmation-time check of the actual bytes decides.
        """
        refused = self._not_world(record)
        return tuple(asset_id for asset_id in _world_assets(record) if asset_id not in refused)

    def _pause(self, request_id, message):
        self._paused.add(request_id)
        self.views[request_id].error = message

    def _observe_cancellation(self, request_id, snapshot):
        """Remember whether the latest owned retrieval confirmed a cancellable job.

        Only a remote record whose snapshot repeats its known ID and reports a
        documented cancellable type qualifies; anything else withdraws the offer.
        """
        self._cancellable.pop(request_id, None)
        if not isinstance(snapshot, RemoteSnapshot):
            return
        record = snapshot.record
        if (
            record.intent.request_id != request_id
            or record.state != JobState.REMOTE
            or record.remote_job_id is None
        ):
            return
        try:
            response = snapshot.response
        except ValueError:
            return
        if isinstance(response, dict) and response.get("jobId") == record.remote_job_id:
            if cancellable_job(response):
                self._cancellable[request_id] = record.remote_job_id

    def _confirmed_cancellable(self, record):
        return (
            record.state == JobState.REMOTE
            and record.intent.operation == "model"
            and record.remote_job_id is not None
            and self._cancellable.get(record.intent.request_id) == record.remote_job_id
        )

    def _observe(self, request_id, snapshot):
        """Project one validated remote reading; terminal or other snapshots clear it.

        This is the only display consumer of refresh, cancel and decision
        snapshots; `_observe_cancellation` reads the same completion for the
        cancel offer. Later remote interpretations extend this hook instead of
        draining them separately; waiting workflow steps are one of them.
        """
        meta = self.views[request_id].meta
        try:
            reading = progress.observe(snapshot)
        except Exception as error:
            # Never log the response; it may carry signed result URLs.
            _log.warning("Remote progress reading dropped (%s)", type(error).__name__)
            reading = None
        if reading is None:
            meta.pop("remote", None)
        else:
            meta["remote"] = reading
        self._observe_failure(request_id, snapshot)
        self._observe_steps(request_id, snapshot)

    def _observe_failure(self, request_id, snapshot):
        """Keep the cleaned reason a retrieval gives for the failure it observed.

        Any other snapshot drops it. A reading that cannot be interpreted is
        dropped too, logging only its error type: the job still reads failed.
        """
        try:
            failure = failures.observe(snapshot)
        except Exception as error:
            # Never log the response; it may carry signed result URLs.
            _log.warning("Failure reason dropped (%s)", type(error).__name__)
            failure = None
        if failure is None:
            self._failures.pop(request_id, None)
        else:
            self._failures[request_id] = failure

    def _failure_reason(self, request_id, record):
        """Scenario's reason for this unchanged failed record, or None; reads memory only."""
        failure = self._failures.get(request_id)
        if (
            failure is None
            or record.state != JobState.FAILED
            or (failure.remote_job_id, failure.revision) != (record.remote_job_id, record.revision)
        ):
            return None
        return failure.reason

    def _observe_steps(self, request_id, snapshot):
        """Project a workflow's waiting approval and selection steps from one snapshot.

        A failed projection drops the steps and logs only the error type, once
        until a projection succeeds again. While a step waits, the next
        automatic refresh moves to STEP_POLL_INTERVAL.
        """
        record = snapshot.record
        if (
            record.intent.operation != "workflow"
            or record.state != JobState.REMOTE
            or record.remote_job_id is None
        ):
            self._steps.pop(request_id, None)
            return
        try:
            # The coordinator attaches the workflow definition's step links.
            steps = workflow_nodes.pending_steps(
                snapshot.response,
                remote_job_id=record.remote_job_id,
                workflow_id=record.intent.target_id,
                links=getattr(snapshot, "links", None),
            )
        except Exception as error:
            # Warn once per unreadable stretch; polling repeats every few seconds.
            if request_id not in self._unreadable_steps:
                self._unreadable_steps.add(request_id)
                _log.warning("Workflow steps dropped (%s)", type(error).__name__)
            self._steps.pop(request_id, None)
            return
        self._unreadable_steps.discard(request_id)
        self._steps[request_id] = StepProjection(record.remote_job_id, record.revision, steps)
        failed = self._step_errors.get(request_id)
        if failed is not None and {step.review_id for step in steps} - failed[1]:
            # A step that was not waiting at the failed decision supersedes it.
            del self._step_errors[request_id]
        if steps:
            self._next_poll[request_id] = max(
                self._next_poll.get(request_id, 0.0), time.monotonic() + STEP_POLL_INTERVAL
            )

    def _current_steps(self, request_id, record):
        """Steps still bound to this saved record; reads memory only (any thread)."""
        projection = self._steps.get(request_id)
        if (
            projection is None
            or record.state != JobState.REMOTE
            or record.remote_job_id != projection.remote_job_id
            or record.revision != projection.revision
        ):
            return ()
        return projection.steps

    def _deciding(self, request_id):
        """Whether a decision for this job is queued or being sent; memory only."""
        command = self._commands.get(request_id)
        return command is not None and command[0] == DECIDE

    def _project_steps(self, request_id, record):
        """Drop steps and decision errors once their record changed or left `remote`."""
        projection = self._steps.get(request_id)
        if projection is not None and (
            record.state != JobState.REMOTE
            or (record.remote_job_id, record.revision)
            != (projection.remote_job_id, projection.revision)
        ):
            del self._steps[request_id]
        if record.state != JobState.REMOTE:
            self._step_errors.pop(request_id, None)
            self._unreadable_steps.discard(request_id)

    def steps(self, request_id=None):
        """Read-only waiting workflow steps from memory, so drawing code may call it.

        With a request ID, return its steps (possibly empty); otherwise map each
        job with waiting steps to them. Maintenance polls drop projections whose
        saved record changed or left `remote`.
        """
        if request_id is not None:
            projection = self._steps.get(request_id)
            return projection.steps if projection is not None else ()
        return {key: value.steps for key, value in self._steps.items() if value.steps}

    def step_view(self, request_id):
        """One job's step projection, decision in flight and last decision error.

        Reads memory only, so drawing code may call it. The saved-job
        descriptors use the steps only while their revision equals the view's
        saved revision.
        """
        projection = self._steps.get(request_id)
        failed = self._step_errors.get(request_id)
        return WorkflowSteps(
            revision=projection.revision if projection is not None else None,
            steps=projection.steps if projection is not None else (),
            deciding=self._deciding(request_id),
            error=failed[0] if failed is not None else "",
        )

    def waiting_step(self, request_id, expected_revision, node_id):
        """Return the saved record and one of its current waiting steps for a review.

        The native review dialog captures them when it opens; `decide` checks
        the same binding again before queuing a decision. Reads the store and
        memory only and queues nothing.
        """
        record = self.store.get(request_id)
        if (
            type(expected_revision) is not int
            or record is None
            or record.revision != expected_revision
            or record.state != JobState.REMOTE
            or record.intent.operation != "workflow"
        ):
            raise ScenarioError(0, "The saved workflow job changed; inspect it again")
        if request_id in self._commands or request_id in self.submissions:
            raise ScenarioError(0, "Wait for the current job command to finish before reviewing")
        step = next(
            (item for item in self._current_steps(request_id, record) if item.node_id == node_id),
            None,
        )
        if step is None:
            raise ScenarioError(0, "The workflow step changed; review it again")
        return record, step

    def review_assets(self, request_id, expected_revision, node_id, review_id):
        """Distinct asset IDs one current waiting step lists for review, for its previews.

        Bound like a decision to the saved revision and the step's `review_id`,
        but a command in flight, such as the automatic refresh, does not refuse
        it: previews never send a decision. A selection may list one asset as
        several candidates; it is previewed once. Reads the store and memory only.
        """
        record = self.store.get(request_id)
        if (
            type(expected_revision) is not int
            or record is None
            or record.revision != expected_revision
            or record.state != JobState.REMOTE
            or record.intent.operation != "workflow"
        ):
            raise ScenarioError(0, "The saved workflow job changed; inspect it again")
        step = next(
            (item for item in self._current_steps(request_id, record) if item.node_id == node_id),
            None,
        )
        if step is None or not isinstance(review_id, str) or step.review_id != review_id:
            raise ScenarioError(0, "The workflow step changed; review it again")
        return tuple(dict.fromkeys(workflow_nodes.preview_assets(step)))

    def read_steps(self, request_id):
        """Queue one retrieval of a saved workflow job's waiting steps; UI and MCP share it.

        Unlike the Refresh status control, this keeps how the job is delivered:
        an automatically polled job stays polled, and a paused or restarted
        job stays paused until a decision or an explicit resume. A command
        already in flight for the job, an automatic refresh or a decision, is
        returned instead of queuing another retrieval. A saved workflow job
        that is no longer `remote` has no waiting step, so nothing is read and
        None is returned. Completion is projected by `poll` like any refresh.
        """
        record = self.store.get(request_id)
        if record is None or record.intent.operation != "workflow":
            raise ScenarioError(0, "Only saved workflow jobs have approval or selection steps")
        if record.state != JobState.REMOTE or record.remote_job_id is None:
            return None
        if not self.session.active:
            raise ScenarioError(0, "The job context changed; inspect saved jobs again")
        pending = self._commands.get(request_id)
        if pending is not None:
            return pending[1]
        if request_id in self.submissions:
            raise ScenarioError(0, "Wait for the current job command to finish before reading")
        if not self._online():
            raise ScenarioError(0, "Allow Online Access before contacting Scenario")
        view = self._view(record)
        task = self.session.refresh_remote(request_id, expected_revision=record.revision)
        self._commands[request_id] = ("refresh_remote", task)
        view.meta["recovery_actions"] = ()
        return task

    @staticmethod
    def decision_outcome(task):
        """Classify one finished decision command from its task; changes nothing.

        "applied": Scenario accepted the decision and the job was read again.
        "refused": Scenario answered HTTP 4xx, so the decision was not accepted.
        "uncertain": it may have been applied; never repeat it until the step
        is shown waiting again. "not_sent": a check before sending failed, the
        command was abandoned, or the job had already finished, so nothing was
        sent. `poll` still owns the completion and its decision error text.
        """
        if not task.done():
            raise ScenarioError(0, "The decision is still being sent; inspect the job again")
        try:
            snapshot = task.result()
        except DecisionRefused:
            return "refused"
        except DecisionUncertain:
            return "uncertain"
        except Exception:
            return "not_sent"
        return "applied" if getattr(snapshot, "sent", False) else "not_sent"

    def decide(
        self, request_id, expected_revision, node_id, review_id, action, selected_indices=None
    ):
        """Queue one reviewed decision for a waiting workflow step; UI and MCP share it.

        The decision is bound to the saved revision and to the projected step's
        `review_id`, and validated against its actions and selection bounds
        before anything is queued. The coordinator retrieves the job and checks
        that binding again immediately before its single SDK call. Rejection
        stops the workflow. A selection blocked because it feeds a loop is
        refused here and by the coordinator.
        """
        record = self.store.get(request_id)
        if (
            type(expected_revision) is not int
            or record is None
            or record.revision != expected_revision
            or record.state != JobState.REMOTE
            or record.intent.operation != "workflow"
        ):
            raise ScenarioError(0, "The saved workflow job changed; inspect it again")
        step = next(
            (item for item in self._current_steps(request_id, record) if item.node_id == node_id),
            None,
        )
        if step is None or not isinstance(review_id, str) or step.review_id != review_id:
            raise ScenarioError(0, "The workflow step changed; review it again")
        try:
            picks = workflow_nodes.validate_decision(step, action, selected_indices)
        except workflow_nodes.StepError as error:
            raise ScenarioError(0, str(error)) from None
        if not self.session.active:
            raise ScenarioError(0, "The job context changed; inspect saved jobs again")
        if request_id in self._commands or request_id in self.submissions:
            raise ScenarioError(0, "Wait for the current job command to finish before deciding")
        if not self._online():
            raise ScenarioError(0, "Allow Online Access before contacting Scenario")
        view = self._view(record)
        task = self.session.decide_workflow_node(
            request_id,
            expected_revision=expected_revision,
            node_id=step.node_id,
            review_id=review_id,
            action=action,
            selected_indices=picks,
        )
        self._commands[request_id] = (DECIDE, task)
        self._step_errors.pop(request_id, None)
        # Deciding is explicit recovery too: a restarted or refreshed job
        # resumes automatic polling after it.
        self._paused.discard(request_id)
        self._automatic_application.discard(request_id)
        view.meta["recovery_actions"] = ()
        view.error = None
        return task

    def _finish_decision(self, request_id, task):
        """Project a decision's retrieval; refusal or uncertainty refreshes, never pauses."""
        snapshot = None
        message = None
        try:
            completions = self.session.drain(task=task)
            if not completions:
                raise RuntimeError("Missing owned decision completion")
            completion = completions[0]
            if completion.error is not None:
                raise completion.error
            snapshot = completion.result
        except (DecisionRefused, DecisionUncertain) as error:
            message, snapshot = str(error), error.snapshot
        except StoreConflict:
            message = "The workflow step changed; review it again before deciding"
        except ValueError as error:
            # Local validation before sending, by the coordinator or the adapter.
            message = f"The decision was not sent: {error}"
        except Exception:
            message = "The decision was not sent; refresh the job and review the step again"
        if message is None:
            self._step_errors.pop(request_id, None)
        else:
            waiting = frozenset(step.review_id for step in self.steps(request_id))
            self._step_errors[request_id] = (message, waiting)
        # Without a fresh retrieval, refresh at the next poll instead of pausing.
        self._next_poll[request_id] = (
            time.monotonic() + POLL_INTERVAL if snapshot is not None else 0.0
        )
        if snapshot is not None:
            self._observe(request_id, snapshot)

    def _project_remote(self, request_id, record, view, *, online):
        """Keep a reading only for its unchanged known job, and mark it stale.

        Stale means automatic polling is not keeping the reading current:
        delivery is paused, Online Access is off or the scheduled refresh is
        overdue by the grace. It is display state; nothing is persisted.
        """
        reading = view.meta.get("remote")
        if reading is None:
            return
        if (
            record.state not in (JobState.REMOTE, JobState.CANCEL_REQUESTED)
            or record.remote_job_id != reading.remote_job_id
            or record.revision != reading.revision
        ):
            del view.meta["remote"]
            return
        due = max(self._next_poll.get(request_id, 0.0), reading.observed_monotonic)
        stale = not online or request_id in self._paused or time.monotonic() > due + STALE_GRACE
        if stale != reading.stale:
            view.meta["remote"] = replace(reading, stale=stale)

    def bound_views(self, scene, lane):
        """Return this session's views submitted from one scene lane, newest first.

        Reads only in-memory bindings and views, so drawing code may call it.
        A binding identifies where a job came from; it never authorizes applying
        a result there.
        """
        try:
            key = JobBinding(scene.session_uid, lane)
        except ReferenceError:
            return ()
        return tuple(
            self.views[request_id]
            for request_id, binding in reversed(self._bindings.items())
            if binding == key and request_id in self.views
        )

    def busy(self, request_id):
        """Whether a submission or a job command for this request is still running.

        `actions` offers nothing for such a job until it finishes, including during
        each automatic status refresh, except a prepared job's local cancellation.
        Reads memory only, so drawing code may call it.
        """
        return request_id in self.submissions or request_id in self._commands

    def imports_automatically(self, request_id):
        """Whether this session imports the job's ready Image results without another review.

        True from an Image lane submission until the job is canceled or handed to an
        explicit action. It stays true after the automatic import applies and while
        that import is paused, so callers also read the saved state and the view's
        error. Reads memory only.
        """
        return request_id in self._automatic_application

    def inspect(self):
        """Attach saved display projections without resuming or applying old work."""
        for record in self.store.records():
            self._view(record)
        self.poll()
        return tuple(self.views.values())

    def _view(self, record):
        request_id = record.intent.request_id
        if request_id not in self.views:
            self.views[request_id] = JobRecord(
                local_id=request_id,
                lane="workflow" if record.intent.operation == "workflow" else "model",
                kind="workflow" if record.intent.operation == "workflow" else "model",
                model_id=record.intent.target_id,
                body={},
                cu_cost=float(record.intent.quote_cost)
                if record.intent.quote_cost is not None
                else None,
                meta={"shared_job": True, "prompt": "Recovered saved job"},
            )
            self._paused.add(request_id)
        return self.views[request_id]

    def _plan(self, record):
        """Classify saved results once per saved revision; reads no files."""
        request_id = record.intent.request_id
        cached = self._plans.get(request_id)
        if cached is not None and cached[0] == record.revision:
            return cached[1]
        try:
            plan = import_plan(record)
        except ValueError:
            plan = None
        self._plans[request_id] = (record.revision, plan)
        return plan

    def _units(self, record):
        plan = self._plan(record)
        return plan.units if plan is not None else ()

    def _unit_rows(self, record):
        """Importable units for drawing: (key, format, primary) in classified order."""
        return tuple(
            (unit.key, unit.format.value, unit.primary)
            for unit in self._units(record)
            if importable(unit)
        )

    def _unconfirmed_note(self, request_id):
        """The review note of a submission whose outcome Scenario never confirmed."""
        if self.session.unconfirmed_reviewed(request_id):
            return UNCONFIRMED_REVIEWED
        return UNCONFIRMED_REVIEW

    def actions(self, record):
        """Return available explicit controls, without changing saved state."""
        request_id = record.intent.request_id
        state = record.state
        if state == JobState.PREPARED:
            # Any unsent intent, including a queued model, workflow, prompt or
            # translate submission, can be canceled locally like MCP allows.
            # The shared command rejects it once dispatch claims the revision.
            return ("cancel_prepared",)
        if request_id in self._commands or request_id in self.submissions:
            return ()
        if request_id in self._receipts:
            return ("retry_receipt",)
        if state in UNCONFIRMED:
            # An identical request stays refused until the user reviews this one.
            return () if self.session.unconfirmed_reviewed(request_id) else ("mark_reviewed",)
        if any(item.state == LocalApplicationState.APPLYING for item in record.local_applications):
            return ()
        reusable = state in (JobState.READY, JobState.APPLY_FAILED) or (
            state == JobState.APPLIED and len(record.local_applications) < 128
        )
        if record.intent.operation in {"prompt", "translate"}:
            return ("refresh",) if state == JobState.REMOTE else ()
        from .blockout_recovery import available as blockout_available

        if blockout_available(record):
            return ("recover_blockout",)
        actions = []
        if state == JobState.REMOTE and self._current_steps(request_id, record):
            # Status only here: decisions go through ModelJobs.decide, never control.
            actions.append("review_step")
        if state in (JobState.REMOTE, JobState.CANCEL_REQUESTED):
            actions += ["refresh", "resume"]
        elif state in (JobState.SUCCEEDED, JobState.DOWNLOAD_FAILED):
            actions.append("resume")
        elif state == JobState.DOWNLOADING:
            actions.append("recover_download")
        elif reusable and all(
            item.asset.media_type in {"image/png", "image/exr", "image/x-exr"}
            for item in record.results
        ):
            actions.append("import_images")
        if reusable and any(item.asset.media_type in MEDIA_TYPES for item in record.results):
            actions.append("import_media")
        if self._confirmed_cancellable(record):
            actions.append("cancel")
        if reusable and any(importable(unit) for unit in self._units(record)):
            actions.append("import_model")
        # In-place mesh edits stay on the GLB media type they were accepted for.
        if reusable and any(item.asset.media_type == MODEL_MEDIA_TYPE for item in record.results):
            actions.append("apply_mesh")
            if (
                len(record.intent.mesh_sources) == 1
                and len(record.intent.mesh_sources[0].mesh_source.objects) == 1
            ):
                actions.append("apply_mesh_source")
        if reusable and self._world_offer(record):
            actions.append("apply_world")
        if reusable and first_frame_handoff.eligible_assets(record):
            actions.append("use_first_frame")
        if reusable:
            try:
                selected_maps(record)
            except MaterialApplicationError:
                pass
            else:
                actions.append("apply_material")
        if state == JobState.APPLIED and any(
            self.session.has_scene(scene_id) for scene_id in self._worlds.get(request_id, {})
        ):
            actions.append("restore_world")
        if request_id in self._receipts:
            actions.append("retry_receipt")
        return tuple(actions)

    def control(self, request_id, expected_revision, action):
        """Explicit recovery never reconstructs a quote or approves another import."""
        record = self.store.get(request_id)
        if (
            action == "cancel"
            and record is not None
            and record.intent.operation == "workflow"
            and record.state == JobState.REMOTE
        ):
            # Explain why rather than refuse generically: no request can stop it.
            # A prepared, unsent workflow job keeps its cancel_prepared action.
            raise WorkflowCancelUnavailable()
        if (
            action == "cancel"
            and record is not None
            and type(expected_revision) is int
            and record.revision == expected_revision
            and record.state == JobState.REMOTE
            and record.intent.operation == "model"
            and request_id not in self._commands
            and request_id not in self.submissions
            and not self._confirmed_cancellable(record)
        ):
            raise ScenarioError(0, CANCEL_UNSUPPORTED)
        if (
            type(expected_revision) is not int
            or record is None
            or record.revision != expected_revision
            or action not in self.actions(record)
            or action
            in {
                "cancel_prepared",
                "import_images",
                "import_media",
                "import_model",
                "apply_world",
                "restore_world",
                "apply_material",
                "apply_mesh",
                "apply_mesh_source",
                "use_first_frame",
                "recover_blockout",
                "review_step",
            }
        ):
            raise ScenarioError(0, "The saved job or available action changed; inspect it again")
        view = self._view(record)
        if action == "mark_reviewed":
            # Local and session-only: nothing is sent, saved or resubmitted.
            self.session.mark_unconfirmed_reviewed(request_id, expected_revision=expected_revision)
            view.meta["recovery_actions"] = self.actions(record)
            view.meta["review_note"] = self._unconfirmed_note(request_id)
            return None
        if action == "retry_receipt":
            pending = self._receipts[request_id]
            if isinstance(pending, MaterialResultUncertain):
                outcome = self.session.retry_material_receipt(pending)
                _remember(
                    self._materials, request_id, (outcome.application.material,), bpy.data.materials
                )
            elif isinstance(pending, WorldResultUncertain):
                # The original scene handle was retained before persistence failed.
                self.session.retry_world_receipt(pending)
            elif isinstance(pending, ModelResultUncertain):
                outcome = self.session.retry_model_receipt(pending)
                self._remember_model_application(request_id, outcome.application)
            elif isinstance(pending, MediaResultUncertain):
                self.session.retry_media_receipt(pending)
            else:
                outcome = self.session.retry_image_receipt(pending)
                _remember(self._images, request_id, outcome.images, bpy.data.images)
            del self._receipts[request_id]
            self._paused.discard(request_id)
            view.error = None
            return None
        command = {
            "refresh": "refresh_remote",
            "cancel": "cancel_remote",
            "recover_download": "recover_downloads",
            "resume": "refresh_remote"
            if record.state in (JobState.REMOTE, JobState.CANCEL_REQUESTED)
            else "download_results",
        }[action]
        if command != "recover_downloads" and not self._online():
            raise ScenarioError(0, "Allow Online Access before contacting Scenario")
        task = getattr(self.session, command)(request_id, expected_revision=expected_revision)
        self._commands[request_id] = (command, task)
        view.meta["recovery_actions"] = ()
        self._automatic_application.discard(request_id)
        view.error = None
        if action in ("resume", "cancel"):
            self._paused.discard(request_id)
        else:
            self._paused.add(request_id)
        return task

    def show_canceled(self, record):
        """Project a shared local cancellation; nothing remains to deliver or resume."""
        request_id = record.intent.request_id
        view = self.views.get(request_id)
        if view is None or record.state != JobState.CANCELED:
            return None
        self._paused.discard(request_id)
        self._automatic_application.discard(request_id)
        view.status, view.error = record.state.value, None
        view.meta["saved_revision"], view.meta["saved_state"] = record.revision, record.state.value
        view.meta["recovery_actions"] = self.actions(record)
        return view

    def prepare_image_application(self, request_id, expected_revision, scene):
        """Capture a reviewable destination without verification, import or network."""
        record = self.store.get(request_id)
        if (
            type(expected_revision) is not int
            or record is None
            or record.revision != expected_revision
            or "import_images" not in self.actions(record)
        ):
            raise ScenarioError(0, "The saved images changed; inspect the job again")
        if len(self._application_approvals) >= 128:
            raise ScenarioError(
                0, "Too many pending import approvals; complete or cancel one first"
            )
        if scene != bpy.context.scene:
            raise ScenarioError(0, "Select the destination scene before reviewing the import")
        bpy.context.view_layer.update()
        ticket = ImageApplicationApproval(
            uuid.uuid4().hex, record, self.session.capture(scene), scene.name
        )
        self._application_approvals[ticket.identifier] = ticket
        return ticket

    def discard_image_application(self, identifier):
        self._application_approvals.pop(identifier, None)

    def apply_saved_images(self, identifier):
        """Consume explicit approval once; verification finishes on the existing pool."""
        ticket = (
            self._application_approvals.pop(identifier, None)
            if isinstance(identifier, str)
            else None
        )
        if ticket is None or not self.session.active:
            raise ScenarioError(0, "Review and approve the saved image destination again")
        record = self.store.get(ticket.record.intent.request_id)
        if record != ticket.record or "import_images" not in self.actions(record):
            raise ScenarioError(0, "The saved images changed; inspect the job again")
        # Validate the approved destination before queuing and again immediately
        # before claiming application. Never recapture a changed scene here.
        self.session.validate_destination(ticket.destination)
        request_id = record.intent.request_id
        task = self.session.verify_results(request_id, expected_revision=record.revision)
        self._application_destinations[request_id] = ticket.destination
        self._commands[request_id] = ("verify_results", task)
        self._automatic_application.discard(request_id)
        self._paused.add(request_id)
        view = self._view(record)
        view.meta["recovery_actions"] = ()
        view.error = None
        return request_id, task

    def prepare_media_application(self, request_id, expected_revision, scene, asset_id):
        record = self.store.get(request_id)
        if (
            type(expected_revision) is not int
            or record is None
            or record.revision != expected_revision
            or "import_media" not in self.actions(record)
        ):
            raise ScenarioError(0, "The saved media changed; inspect the job again")
        selected = [
            item
            for item in record.results
            if item.asset.asset_id == asset_id and item.asset.media_type in MEDIA_TYPES
        ]
        if len(selected) != 1:
            raise ScenarioError(0, "Choose one supported saved video or sound asset")
        if len(self._application_approvals) >= 128:
            raise ScenarioError(0, "Complete or cancel an existing application review first")
        if scene != bpy.context.scene:
            raise ScenarioError(0, "Select the destination scene before reviewing insertion")
        bpy.context.view_layer.update()
        ticket = MediaApplicationApproval(
            uuid.uuid4().hex,
            record,
            self.session.capture(scene),
            scene.name,
            asset_id,
            scene.frame_current,
            MEDIA_TYPES[selected[0].asset.media_type][0],
        )
        self._application_approvals[ticket.identifier] = ticket
        return ticket

    def prepare_model_application(
        self, request_id, expected_revision, scene, asset_id, *, max_points=None, axes=None
    ):
        """Capture one import unit's destination; `asset_id` is the unit key.

        SPZ, .splat and PLY units take a point limit (default 1,000,000) and saved
        axes (default: the file format's convention); mesh units take neither.
        """
        record = self.store.get(request_id)
        if (
            type(expected_revision) is not int
            or record is None
            or record.revision != expected_revision
            or "import_model" not in self.actions(record)
        ):
            raise ScenarioError(0, "The saved model changed; inspect the job again")
        selected = [
            unit for unit in self._units(record) if unit.key == asset_id and importable(unit)
        ]
        if len(selected) != 1:
            raise ScenarioError(
                0,
                "Choose one GLB, glTF, FBX, OBJ, PLY, SPZ or .splat result "
                "from this job's import units",
            )
        unit = selected[0]
        max_points, axes = self._splat_options(unit, max_points, axes)
        if len(self._application_approvals) >= 128:
            raise ScenarioError(0, "Complete or cancel an existing application review first")
        if scene != bpy.context.scene:
            raise ScenarioError(0, "Select the destination scene before reviewing insertion")
        bpy.context.view_layer.update()
        validate_model_destination(scene, tuple(scene.cursor.location))
        ticket = ModelApplicationApproval(
            uuid.uuid4().hex,
            record,
            self.session.capture(scene),
            scene.name,
            asset_id,
            tuple(scene.cursor.location),
            unit=unit,
            max_points=max_points,
            axes=axes,
        )
        self._application_approvals[ticket.identifier] = ticket
        return ticket

    @staticmethod
    def _splat_options(unit, max_points, axes):
        """Validate reviewed splat options; return (None, None) for a mesh unit."""
        if not worker_prepared(unit):
            if max_points is not None or axes is not None:
                raise ScenarioError(0, "Point limits and axes apply only to SPZ, PLY or .splat")
            return None, None
        if max_points is None:
            max_points = DEFAULT_POINTS
        if axes is None:
            axes = default_axes(unit.format.value)
        if type(max_points) is not int or max_points not in POINT_CHOICES:
            raise ScenarioError(0, "Keep 250,000, 500,000, 1,000,000 or 2,000,000 splat points")
        if axes not in SPLAT_AXES:
            raise ScenarioError(0, "Choose OPENGL or OPENCV saved splat axes")
        return max_points, axes

    def revise_model_application(self, identifier, *, max_points, axes):
        """Change reviewed splat options under a new handle; the old one stops working.

        The captured scene, cursor and unit stay the same; nothing is recaptured.
        """
        ticket = self._application_approvals.get(identifier)
        if not isinstance(ticket, ModelApplicationApproval) or not worker_prepared(ticket.unit):
            raise ScenarioError(0, "Use an unconsumed SPZ, PLY or .splat import review")
        max_points, axes = self._splat_options(ticket.unit, max_points, axes)
        self.session.validate_destination(ticket.destination, cursor=ticket.cursor)
        if self.store.get(ticket.record.intent.request_id) != ticket.record:
            raise ScenarioError(0, "The saved model changed; start a fresh review")
        if (max_points, axes) == (ticket.max_points, ticket.axes):
            return ticket
        revised = replace(ticket, identifier=uuid.uuid4().hex, max_points=max_points, axes=axes)
        del self._application_approvals[identifier]
        self._application_approvals[revised.identifier] = revised
        return revised

    def _remember_model_application(self, request_id, application):
        if isinstance(application, MeshEditApplication):
            previous = self._mesh_edits.get(request_id)
            if previous is None or previous[1] is not application:
                self._mesh_edits[request_id] = (self.session.history_revision, application)
            # Receipt-only retry must keep the original history revision.
        else:
            _remember(self._objects, request_id, application.objects, bpy.data.objects)
            if isinstance(application, ModelApplication):
                summary = {
                    "unit": application.members[0] if application.members else None,
                    "format": application.format,
                    "members": list(application.members),
                    "dropped_references": application.dropped_references,
                    "frame_rate_mismatch": application.frame_rate_mismatch,
                    "splat": dict(application.splat) if application.splat else None,
                }
                self._model_imports[request_id] = (self.session.history_revision, summary)

    @staticmethod
    def _mesh_options(target, policy, placement, keep_original):
        if placement not in {"WORLD", "LOCAL"}:
            raise ScenarioError(0, "Choose scene or object-local result coordinates")
        validate_mesh_target(target)
        try:
            mapping = (
                target.obj.matrix_world.inverted() if placement == "WORLD" else Matrix.Identity(4)
            )
        except ValueError:
            raise ScenarioError(0, "The target transform cannot map scene coordinates") from None
        mapping = validate_mesh_request(
            target, policy=policy, result_to_source=mapping, keep_original=keep_original
        )
        return tuple(tuple(row) for row in mapping)

    def prepare_mesh_application(
        self,
        request_id,
        expected_revision,
        scene,
        obj,
        asset_id,
        *,
        policy="REMESH",
        placement="WORLD",
        keep_original=True,
        original_source=False,
        review_placement=False,
    ):
        record = self.store.get(request_id)
        if (
            type(expected_revision) is not int
            or record is None
            or record.revision != expected_revision
            or "apply_mesh" not in self.actions(record)
            or len(self._application_approvals) >= 128
        ):
            raise ScenarioError(0, "Inspect the saved mesh and finish existing reviews first")
        if not any(
            item.asset.asset_id == asset_id and item.asset.media_type == MODEL_MEDIA_TYPE
            for item in record.results
        ):
            raise ScenarioError(0, "Choose one saved static GLB result")
        if type(original_source) is not bool:
            raise ScenarioError(0, "Choose whether to use the captured source")
        if original_source:
            if len(record.intent.mesh_sources) != 1:
                raise ScenarioError(0, "Choose a job with exactly one captured mesh input")
            target = self.session.mesh_source_target(record.intent.mesh_sources[0])
            scene, obj = target.scene, target.obj
        else:
            target = capture_mesh_target(scene, obj)
        # Native review chooses its displayed default from the resolved target,
        # which can differ from the selected object. Explicit MCP options stay strict.
        if review_placement and placement == "WORLD" and target.obj.matrix_world.determinant() <= 0:
            placement = "LOCAL"
        mapping = self._mesh_options(target, policy, placement, keep_original)
        ticket = MeshApplicationApproval(
            uuid.uuid4().hex,
            record,
            self.session.capture(scene, obj),
            scene.name,
            obj.name,
            asset_id,
            target,
            policy,
            placement,
            mapping,
            keep_original,
            original_source,
        )
        self._application_approvals[ticket.identifier] = ticket
        return ticket

    def revise_mesh_application(self, identifier, *, policy, placement, keep_original):
        """Change reviewed options without recapturing or retargeting the source."""
        ticket = self._application_approvals.get(identifier)
        if not isinstance(ticket, MeshApplicationApproval):
            raise ScenarioError(0, "Use an unconsumed mesh destination review")
        mapping = self._mesh_options(ticket.target, policy, placement, keep_original)
        self.session.validate_destination(ticket.destination)
        if self.store.get(ticket.record.intent.request_id) != ticket.record:
            raise ScenarioError(0, "The saved mesh changed; start a fresh review")
        if (policy, placement, keep_original) == (
            ticket.policy,
            ticket.placement,
            ticket.keep_original,
        ):
            return ticket
        revised = replace(
            ticket,
            identifier=uuid.uuid4().hex,
            policy=policy,
            placement=placement,
            mapping=mapping,
            keep_original=keep_original,
        )
        del self._application_approvals[identifier]
        self._application_approvals[revised.identifier] = revised
        return revised

    def _apply_saved_mesh(self, ticket):
        self._application_approvals.pop(ticket.identifier)
        record = self.store.get(ticket.record.intent.request_id)
        if record != ticket.record or "apply_mesh" not in self.actions(record):
            raise ScenarioError(0, "The saved mesh changed; inspect it again")
        validate_mesh_request(
            ticket.target,
            policy=ticket.policy,
            result_to_source=ticket.mapping,
            keep_original=ticket.keep_original,
        )
        self.session.validate_destination(ticket.destination)
        request_id = record.intent.request_id
        task = self.session.verify_results(request_id, expected_revision=record.revision)
        self._mesh_destinations[request_id] = ticket
        self._commands[request_id] = ("verify_mesh_edit", task)
        self._automatic_application.discard(request_id)
        self._paused.add(request_id)
        view = self._view(record)
        view.meta["recovery_actions"], view.error = (), None
        return request_id, task

    def prepare_material_application(self, request_id, expected_revision, scene, obj):
        record = self.store.get(request_id)
        if (
            type(expected_revision) is not int
            or record is None
            or record.revision != expected_revision
            or "apply_material" not in self.actions(record)
            or len(self._application_approvals) >= 128
        ):
            raise ScenarioError(0, "Inspect the saved material and finish existing reviews first")
        bpy.context.view_layer.update()
        target = capture_target(scene, obj)
        roles = selected_maps(record)
        ticket = MaterialApplicationApproval(
            uuid.uuid4().hex,
            record,
            self.session.capture(scene, obj),
            scene.name,
            obj.name,
            target,
            tuple(roles),
        )
        self._application_approvals[ticket.identifier] = ticket
        return ticket

    def _apply_saved_material(self, ticket):
        self._application_approvals.pop(ticket.identifier)
        record = self.store.get(ticket.record.intent.request_id)
        if record != ticket.record or "apply_material" not in self.actions(record):
            raise ScenarioError(0, "The saved material changed; inspect it again")
        self.session.validate_destination(ticket.destination)
        validate_material_target(ticket.target)
        request_id = record.intent.request_id
        task = self.session.verify_results(request_id, expected_revision=record.revision)
        self._material_destinations[request_id] = ticket
        self._commands[request_id] = ("verify_material", task)
        self._automatic_application.discard(request_id)
        self._paused.add(request_id)
        view = self._view(record)
        view.meta["recovery_actions"], view.error = (), None
        return request_id, task

    def _remember_world(self, request_id, destination, application):
        self._worlds.setdefault(request_id, {})[destination.scene_id] = application

    def _prune_worlds(self):
        for request_id, worlds in tuple(self._worlds.items()):
            for scene_id in tuple(worlds):
                if not self.session.has_scene(scene_id):
                    del worlds[scene_id]
            if not worlds:
                del self._worlds[request_id]

    def _world_application(self, request_id, destination):
        application = self._worlds.get(request_id, {}).get(destination.scene_id)
        if application is None or not self.session.has_scene(destination.scene_id):
            raise ScenarioError(0, "Select the scene whose previous World should be restored")
        return application

    def prepare_world_application(
        self, request_id, expected_revision, scene, asset_id, *, restore=False
    ):
        record = self.store.get(request_id)
        action = "restore_world" if restore else "apply_world"
        if (
            type(expected_revision) is not int
            or record is None
            or record.revision != expected_revision
        ):
            raise ScenarioError(0, "Inspect the current saved World result again")
        if not restore and asset_id in self._not_world(record):
            raise ScenarioError(0, NOT_WORLD_SIZE)
        if action not in self.actions(record):
            raise ScenarioError(0, "Inspect the current saved World result again")
        if scene != bpy.context.scene or len(self._application_approvals) >= 128:
            raise ScenarioError(0, "Choose the destination and finish existing reviews first")
        bpy.context.view_layer.update()
        destination = self.session.capture(scene)
        media_type = ""
        if restore:
            self._world_application(request_id, destination)
        else:
            candidates = self._world_offer(record)
            selected = [
                item.asset.media_type
                for item in record.results
                if item.asset.asset_id == asset_id and asset_id in candidates
            ]
            if not selected:
                raise ScenarioError(
                    0, "Choose one saved PNG, JPEG or OpenEXR panorama, not a material map"
                )
            media_type = selected[0]
        ticket = WorldApplicationApproval(
            uuid.uuid4().hex,
            record,
            destination,
            scene.name,
            scene,
            scene.world,
            asset_id or "",
            restore,
            media_type=media_type,
        )
        self._application_approvals[ticket.identifier] = ticket
        return ticket

    def _validate_world(self, ticket):
        self.session.validate_destination(ticket.destination)
        if ticket.scene.world != ticket.previous:
            raise ScenarioError(0, "The scene World changed; approve its replacement again")

    def prepare_first_frame_application(self, request_id, expected_revision, scene, asset_id):
        """Review one saved image for the Render Video first frame; no read, network or edit.

        The approval binds the job revision, the asset and its receipt digest,
        the captured scene and the reviewed form (model, input, existing slots,
        chosen file and enabled state). It is single-use.
        """
        record = self.store.get(request_id)
        if (
            type(expected_revision) is not int
            or record is None
            or record.revision != expected_revision
            or "use_first_frame" not in self.actions(record)
        ):
            raise ScenarioError(0, "The saved image changed; inspect the job again")
        if record.intent.scope != self.session.scope:
            raise ScenarioError(0, "The saved image belongs to another connection")
        selected = [
            item
            for item in first_frame_handoff.eligible_assets(record)
            if item.asset.asset_id == asset_id
        ]
        if len(selected) != 1:
            raise ScenarioError(0, "Choose one downloaded PNG, JPEG or WebP saved image")
        if len(self._application_approvals) >= 128:
            raise ScenarioError(0, "Complete or cancel an existing application review first")
        if scene != bpy.context.scene:
            raise ScenarioError(0, "Select the destination scene before reviewing the first frame")
        target = first_frame_handoff.target(scene)
        bpy.context.view_layer.update()
        item = selected[0]
        ticket = first_frame_handoff.FirstFrameApproval(
            uuid.uuid4().hex,
            record,
            self.session.capture(scene),
            scene.name,
            scene,
            item.asset,
            item.receipt.sha256,
            target,
        )
        self._application_approvals[ticket.identifier] = ticket
        return ticket

    def _apply_first_frame(self, ticket):
        self._application_approvals.pop(ticket.identifier)
        if not self.session.active:
            raise ScenarioError(0, "Review the current connection and first frame again")
        record = self.store.get(ticket.record.intent.request_id)
        if record != ticket.record or "use_first_frame" not in self.actions(record):
            raise ScenarioError(0, "The saved image changed; inspect the job again")
        self.session.validate_destination(ticket.destination)
        if first_frame_handoff.target(ticket.scene).key != ticket.target.key:
            raise ScenarioError(0, "The Render Video form changed; review the first frame again")
        request_id = record.intent.request_id
        task = self.session.verify_results(request_id, expected_revision=record.revision)
        self._first_frame_destinations[request_id] = ticket
        self._commands[request_id] = ("verify_first_frame", task)
        self._automatic_application.discard(request_id)
        self._paused.add(request_id)
        view = self._view(record)
        view.meta["recovery_actions"], view.error = (), None
        return request_id, task

    def _remember_first_frame(self, request_id, ticket, *, bound=None, error=None):
        if ticket is None:
            return
        self._first_frames.pop(request_id, None)
        while len(self._first_frames) >= _FIRST_FRAME_HISTORY:
            del self._first_frames[next(iter(self._first_frames))]
        self._first_frames[request_id] = {
            "state": "bound" if bound is not None else "failed",
            "scene": ticket.scene_name,
            "lane": first_frame_handoff.LANE,
            "input": ticket.target.param_name,
            "asset_id": ticket.asset.asset_id,
            "undo_recorded": bound.undo_recorded if bound is not None else False,
            "error": None if bound is not None else (error or first_frame_handoff.STOPPED),
        }

    def prepare_asset_application(
        self, request_id, expected_revision, scene, asset_id, *, max_points=None, axes=None
    ):
        record = self.store.get(request_id)
        # Any model-family file routes to model review, which accepts only unit keys.
        if record is not None and any(
            item.asset.asset_id == asset_id
            and (
                model_format(item.asset.media_type) is not None
                or item.asset.media_type in MATERIAL_MEDIA_TYPES
                or item.asset.media_type.startswith("model/")
            )
            for item in record.results
        ):
            return self.prepare_model_application(
                request_id, expected_revision, scene, asset_id, max_points=max_points, axes=axes
            )
        if max_points is not None or axes is not None:
            raise ScenarioError(0, "Point limits and axes apply only to SPZ, PLY or .splat")
        return self.prepare_media_application(request_id, expected_revision, scene, asset_id)

    def apply_saved_result(self, identifier):
        ticket = (
            self._application_approvals.get(identifier) if isinstance(identifier, str) else None
        )
        if isinstance(ticket, first_frame_handoff.FirstFrameApproval):
            return self._apply_first_frame(ticket)
        if isinstance(ticket, MeshApplicationApproval):
            return self._apply_saved_mesh(ticket)
        if isinstance(ticket, MaterialApplicationApproval):
            return self._apply_saved_material(ticket)
        if not isinstance(
            ticket, (MediaApplicationApproval, ModelApplicationApproval, WorldApplicationApproval)
        ):
            return self.apply_saved_images(identifier)
        self._application_approvals.pop(identifier)
        if not self.session.active:
            raise ScenarioError(0, "Review the current connection and media destination again")
        record = self.store.get(ticket.record.intent.request_id)
        is_model = isinstance(ticket, ModelApplicationApproval)
        is_world = isinstance(ticket, WorldApplicationApproval)
        action = (
            ("restore_world" if ticket.restore else "apply_world")
            if is_world
            else ("import_model" if is_model else "import_media")
        )
        if record != ticket.record or action not in self.actions(record):
            raise ScenarioError(0, "The saved result changed; inspect the job again")
        if is_world:
            self._validate_world(ticket)
        elif is_model:
            self.session.validate_destination(ticket.destination, cursor=ticket.cursor)
        else:
            self.session.validate_destination(ticket.destination, frame=ticket.frame)
        request_id = record.intent.request_id
        if is_world and ticket.restore:
            application = self._world_application(request_id, ticket.destination)
            # Neither the native nor the agent restoration runs inside an Undo
            # operator. A refusal restores nothing and leaves the file clean.
            with file_state.marking("Scenario World restore", lambda: application.restored):
                application.restore()
            worlds = self._worlds[request_id]
            del worlds[ticket.destination.scene_id]
            if not worlds:
                del self._worlds[request_id]
            self._view(record).error = None
            return request_id, None
        prepare = is_model and worker_prepared(ticket.unit)
        if prepare:
            # Decoding uses the single local-operation slot shared with local
            # renders and Film media inspection.
            try:
                task = self.session.prepare_model_import(
                    request_id,
                    expected_revision=record.revision,
                    asset_id=ticket.asset_id,
                    options=ticket.splat_options,
                )
            except WorkerError as error:
                if ticket.unit.format is not ModelFormat.PLY:
                    # The sanitized reason, such as a busy local slot; nothing was queued.
                    raise ScenarioError(0, f"{error}; review the import again") from None
                # Only splats need the slot: this PLY verifies on the ordinary queue
                # and imports if its verified header shows a mesh.
                prepare = False
        if not prepare:
            task = self.session.verify_results(request_id, expected_revision=record.revision)
        if is_world:
            self._world_destinations[request_id] = ticket
        elif is_model:
            self._model_destinations[request_id] = ticket
        else:
            self._media_destinations[request_id] = ticket
        self._commands[request_id] = (
            "verify_world"
            if is_world
            else "prepare_model"
            if prepare
            else "verify_model"
            if is_model
            else "verify_media",
            task,
        )
        self._automatic_application.discard(request_id)
        self._paused.add(request_id)
        view = self._view(record)
        view.meta["recovery_actions"], view.error = (), None
        return request_id, task

    def wait(self, request_id, timeout, *, stopped=lambda: False):
        """Wait on an HTTP worker using only saved state; the main thread advances jobs."""
        deadline = time.monotonic() + timeout
        sleeper = threading.Event()
        while self.session.active:
            if stopped():
                raise ScenarioError(0, "The job wait stopped; generation was not cancelled")
            record = self.store.get(request_id)
            if record is None:
                raise ScenarioError(0, "Saved job is unavailable")
            if not self._delivering(request_id, record):
                return
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return
            sleeper.wait(min(0.1, remaining))
        raise ScenarioError(0, "The job context changed while waiting; inspect saved jobs again")

    def _advancing(self, request_id, record):
        """Whether this session advances the job by itself while Online Access allows it.

        False once it is settled, including a finished job whose results
        ModelJobs never downloads, paused for review, prepared but never
        queued, or not owned by this session. `wait` calls this on the HTTP
        worker, so it reads only in-memory state, never bpy.
        """
        return (
            request_id in self.views
            and request_id not in self._paused
            and record.state not in _SETTLED
            and (record.state != JobState.SUCCEEDED or downloads_results(record))
            and (record.state != JobState.PREPARED or request_id in self.submissions)
            and (record.state != JobState.READY or request_id in self._automatic_application)
            # A waiting workflow step moves on only after a decision.
            and (not self._current_steps(request_id, record) or self._deciding(request_id))
        )

    def _held_offline(self, record):
        return not self._online_seen and record.state in NEEDS_SCENARIO

    def _delivering(self, request_id, record):
        """Whether the job advances now; `wait` returns once it does not.

        MCP reports it as `delivery_active`: the job is advancing and its next
        step is not held by disabled Online Access.
        """
        return self._advancing(request_id, record) and not self._held_offline(record)

    def _offline(self, request_id, record):
        """Whether disabled Online Access alone holds a step this session takes by itself.

        That step is status polling or the result download. A paused, restarted
        or settled job, and a finished job that ModelJobs never downloads, is
        not held; MCP reports this as `delivery_offline`.
        """
        return self._held_offline(record) and self._advancing(request_id, record)

    def _mesh_status(self, request_id):
        saved = self._mesh_edits.get(request_id)
        if saved is None:
            return None
        revision, application = saved
        if revision != self.session.history_revision:
            # History can replace RNA wrappers, even when object names survive.
            # Retire this transient status; never rebind it by name after redo.
            return None
        objects = tuple(bpy.data.objects)
        return {
            "target": application.source.name if application.source in objects else None,
            "original": application.original.name if application.original in objects else None,
            "policy": application.policy,
            "undo_available": application.undo_available,
            "parts": tuple(part.name for part in application.parts if part in objects),
            "rig": application.rig.name if application.rig in objects else None,
        }

    def _model_import_status(self, request_id):
        saved = self._model_imports.get(request_id)
        if saved is None or saved[0] != self.session.history_revision:
            # Like mesh edits, retire the summary once history may have replaced it.
            return None
        return dict(saved[1])

    def _import_notices(self, request_id):
        """Status rows for this session's last splat import while its summary is current."""
        status = self._model_import_status(request_id)
        splat = status.get("splat") if status else None
        if not splat:
            return ()
        notices = [f"Shown as points: {splat['points_kept']:,} of {splat['points_total']:,} splats"]
        if splat["spz_extensions"]:
            notices.append("This SPZ declares extensions; check its axes")
        return tuple(notices)

    def _import_units_status(self, record):
        plan = self._plan(record)
        if plan is None:
            return [], []
        media = {item.asset.asset_id: item.asset.media_type for item in record.results}
        units = []
        for unit in plan.units:
            roles = {unit.key: "model"}
            if unit.material is not None:
                roles[unit.material] = "material"
            roles.update({asset_id: slot for slot, asset_id in unit.textures})
            units.append(
                {
                    "key": unit.key,
                    "format": unit.format.value,
                    "kind": unit.kind.value,
                    "primary": unit.primary,
                    "importable": importable(unit),
                    "members": [
                        {
                            "asset_id": asset_id,
                            "role": roles.get(asset_id, "model"),
                            "media_type": media.get(asset_id),
                        }
                        for asset_id in unit.members
                    ],
                    "resources": list(unit.resources),
                }
            )
        findings = [
            {"asset_id": finding.asset_id, "reason": finding.reason} for finding in plan.findings
        ]
        return units, findings

    def status(self, reference):
        self.poll()
        matches = [
            record
            for record in self.store.records()
            if reference in (record.intent.request_id, record.remote_job_id)
        ]
        if len(matches) != 1:
            return None
        record = matches[0]
        view = self.views.get(record.intent.request_id)
        notice = {}
        if record.intent.operation == "workflow":
            # The saved intent keeps the approved quote, not the loop count; a
            # count this session did not record is unknown coverage.
            notice = workflow_quote_notice(view.meta.get("workflow_loop_steps") if view else None)
        reading = view.meta.get("remote") if view is not None else None
        steps = self._current_steps(record.intent.request_id, record)
        failed = self._step_errors.get(record.intent.request_id)
        binding = self._bindings.get(record.intent.request_id)
        units, findings = self._import_units_status(record)
        return {
            **notice,
            "local_id": record.intent.request_id,
            "job_id": record.remote_job_id,
            "status": record.state.value,
            "revision": record.revision,
            # In-memory projection shared with the Jobs views; never persisted.
            "lane": binding.lane if binding is not None else None,
            "remote_status": reading.status if reading is not None else None,
            "progress": reading.fraction if reading is not None else None,
            "remote_observed_at": reading.observed_utc if reading is not None else None,
            "remote_stale": reading.stale if reading is not None else None,
            "mesh_sources": [asdict(source) for source in record.intent.mesh_sources],
            "cu_cost": float(record.intent.quote_cost)
            if record.intent.quote_cost is not None
            else None,
            "cu_cost_exact": record.intent.quote_cost,
            "source": getattr(record.intent, "source", "generation"),
            "kind": self.views[record.intent.request_id].kind
            if record.intent.request_id in self.views
            else "model",
            "files": list(self.views[record.intent.request_id].files)
            if record.intent.request_id in self.views
            else [],
            "results": [
                {
                    "asset_id": item.asset.asset_id,
                    "name": item.asset.name,
                    "media_type": item.asset.media_type,
                    "size": item.asset.expected_size,
                    "source": item.asset.source,
                    "projection": item.asset.projection,
                    "downloaded": item.receipt is not None,
                }
                for item in record.results
            ],
            "local_applications": [
                {
                    "application_id": item.application_id,
                    "source_revision": item.source_revision,
                    "purpose": item.purpose,
                    "asset_ids": list(item.asset_ids),
                    "state": item.state.value,
                    "destination": {
                        "file_id": item.destination.file_id,
                        "scene_id": item.destination.scene_id,
                        "target_id": item.destination.target_id,
                        "revision": item.destination.revision,
                    },
                }
                for item in record.local_applications
            ],
            # Waiting workflow steps, never with URLs; see workflow_nodes.
            "awaiting_decision": bool(steps) and not self._deciding(record.intent.request_id),
            "pending_steps": [step.summary() for step in steps],
            "decision_error": failed[0] if failed is not None else None,
            "delivery_paused": record.intent.request_id in self._paused,
            "delivery_active": self._delivering(record.intent.request_id, record),
            "delivery_offline": self._offline(record.intent.request_id, record),
            # Only the user marks an unconfirmed request reviewed, in Blender's Jobs list.
            "actions": tuple(item for item in self.actions(record) if item not in NATIVE_ONLY),
            "error": self.views[record.intent.request_id].error
            if record.intent.request_id in self.views
            else None,
            "mesh_edit": self._mesh_status(record.intent.request_id),
            "first_frame": dict(self._first_frames.get(record.intent.request_id) or {}) or None,
            # Classified from the saved manifest; nothing here authorizes an import.
            "import_units": units,
            "import_findings": findings,
            "model_import": self._model_import_status(record.intent.request_id),
            "objects": [
                obj.name
                for obj in self._objects.get(record.intent.request_id, ())
                if obj in tuple(bpy.data.objects)
            ],
            "materials": [
                material.name
                for material in self._materials.get(record.intent.request_id, ())
                if material in tuple(bpy.data.materials)
            ],
            "images": [
                image.name
                for image in self._images.get(record.intent.request_id, ())
                if image in tuple(bpy.data.images)
            ],
            "note": "Saved job state; active jobs advance without repeating generation. "
            "Restarted or paused jobs require explicit recovery.",
        }
