# SPDX-FileCopyrightText: 2026 Scenario Inc.
# SPDX-License-Identifier: GPL-3.0-or-later
"""Atomic scoped intent storage for the shared runtime being adopted under #65.

No dispatch, network, Blender access or prototype migration. Callers provide an
extension-user-data path and stable, non-secret scope/origin identities.
"""

import hashlib
import json
import math
import os
import re
import sqlite3
import stat
from contextlib import contextmanager
from dataclasses import asdict, dataclass, replace
from decimal import Decimal, InvalidOperation
from enum import StrEnum
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import urlsplit

from .result_metadata import ORIGINAL_MEDIA_TYPES, PROJECTIONS, REWRITTEN_MESH_TYPES, TEXTURE_ROLES
from .transfers import DownloadedResult, TransferError, _root, validate_result_name

if TYPE_CHECKING:
    from .mesh_source import MeshSource

_VERSION = 11
_APPLICATION_ID = 0x53434A42
_FILM_TASK_FILTER = "json_valid(record) AND json_extract(record, '$.intent.film_task') IS NOT NULL"
# A bounded identifier, not the current catalog: "3d" is a lane, and a renamed or
# retired lane must never make an existing stored row unreadable.
_LANE = re.compile(r"[a-z0-9][a-z0-9_]{0,31}")
TRAINED_ROUTES = frozenset({"stack", "composition", "custom", "direct"})
MAX_TRAINED_PICKS = 16


class StoreError(RuntimeError):
    """Persistence failed; callers must stop before dispatching or applying."""


def _regular_database(path):
    """Recheck each connection; the caller must still own the parent directory."""
    try:
        regular = stat.S_ISREG(path.lstat().st_mode)
    except OSError:
        raise StoreError("Storage path is unavailable; preserve it for recovery") from None
    if not regular:
        raise StoreError("Storage must be a regular local file")


class StoreConflict(StoreError):
    """A request already exists, or another caller changed its revision."""


def _identity(value):
    if (
        not isinstance(value, str)
        or not value
        or len(value) > 256
        or any(char.isspace() or ord(char) < 32 for char in value)
        or any(char in value for char in "/\\?#")
    ):
        raise ValueError("Use a nonempty opaque identity, not a path, URL or credential")
    return value


@dataclass(frozen=True)
class JobScope:
    service: str
    account_id: str
    project_id: str | None = None
    team_id: str | None = None

    def __post_init__(self):
        if (
            not isinstance(self.service, str)
            or len(self.service) > 2048
            or any(char.isspace() or ord(char) < 32 for char in self.service)
        ):
            raise ValueError("Use a valid API base URL")
        url = urlsplit(self.service)
        if (
            url.scheme != "https"
            or not url.hostname
            or url.username
            or url.password
            or url.query
            or url.fragment
            or self.service != self.service.rstrip("/")
        ):
            raise ValueError("Use an HTTPS API base URL without secrets or a trailing slash")
        _identity(self.account_id)
        for value in (self.project_id, self.team_id):
            if value is not None:
                _identity(value)


@dataclass(frozen=True)
class JobOrigin:
    file_id: str
    scene_id: str
    revision: str
    target_id: str | None = None

    def __post_init__(self):
        for value in (self.file_id, self.scene_id, self.revision):
            _identity(value)
        if self.target_id is not None:
            _identity(self.target_id)


@dataclass(frozen=True)
class JobMeshSource:
    parameter: str
    index: int | None
    asset_id: str
    upload_id: str
    upload_revision: int
    origin: JobOrigin
    mesh_source: "MeshSource"

    def __post_init__(self):
        # Local import avoids a module cycle with shared opaque-ID validation.
        from .mesh_source import MeshSource

        if (
            not isinstance(self.parameter, str)
            or not self.parameter.strip()
            or len(self.parameter) > 256
            or any(ord(char) < 32 for char in self.parameter)
            or (
                self.index is not None
                and (type(self.index) is not int or not 0 <= self.index < 128)
            )
            or type(self.upload_revision) is not int
            or self.upload_revision < 0
            or not isinstance(self.origin, JobOrigin)
            or not isinstance(self.mesh_source, MeshSource)
        ):
            raise ValueError("Use an exact mesh input and saved upload provenance")
        _identity(self.asset_id)
        _identity(self.upload_id)
        expected = (
            self.mesh_source.objects[0].target_id if len(self.mesh_source.objects) == 1 else None
        )
        if self.origin.target_id != expected:
            raise ValueError("Mesh provenance and captured source target disagree")


def _decode_mesh_binding(value):
    from .mesh_source import decode_mesh_source

    if not isinstance(value, dict) or set(value) != set(JobMeshSource.__dataclass_fields__):
        raise ValueError("Invalid mesh input binding")
    value = dict(value)
    origin = value.pop("origin")
    if not isinstance(origin, dict) or set(origin) != set(JobOrigin.__dataclass_fields__):
        raise ValueError("Invalid mesh input origin")
    source = decode_mesh_source(value.pop("mesh_source"))
    return JobMeshSource(**value, origin=JobOrigin(**origin), mesh_source=source)


@dataclass(frozen=True)
class FilmTaskBinding:
    """Local production/task identity, never a remote identifier or spend approval."""

    production_id: str
    task_id: str
    recipe_sha256: str
    task_sha256: str

    def __post_init__(self):
        _identity(self.production_id)
        if not isinstance(self.task_id, str) or not re.fullmatch(
            r"[a-zA-Z0-9][a-zA-Z0-9_-]{0,95}", self.task_id
        ):
            raise ValueError("Use a bounded Film task name")
        for value in (self.recipe_sha256, self.task_sha256):
            if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value):
                raise ValueError("Film recipe and task require SHA-256 identities")


@dataclass(frozen=True)
class FilmUploadReference:
    """Immutable association to an already imported scoped upload, without URLs."""

    scope: JobScope
    film_task: FilmTaskBinding
    upload_request_id: str
    upload_revision: int
    asset_id: str
    file_sha256: str
    kind: str

    def __post_init__(self):
        if (
            not isinstance(self.scope, JobScope)
            or not isinstance(self.film_task, FilmTaskBinding)
            or type(self.upload_revision) is not int
            or self.upload_revision < 0
            or not isinstance(self.kind, str)
            or self.kind not in {"3d", "asset", "audio", "avatar", "image", "text", "video"}
            or not isinstance(self.file_sha256, str)
            or not re.fullmatch(r"[0-9a-f]{64}", self.file_sha256)
        ):
            raise ValueError("Use a scoped imported asset upload and its exact source identity")
        _identity(self.upload_request_id)
        _identity(self.asset_id)


def _decode_film_upload(raw, scope):
    try:
        value = json.loads(raw)
        if not isinstance(value, dict) or set(value) != set(
            FilmUploadReference.__dataclass_fields__
        ):
            raise ValueError
        for fields, cls in ((value["scope"], JobScope), (value["film_task"], FilmTaskBinding)):
            if not isinstance(fields, dict) or set(fields) != set(cls.__dataclass_fields__):
                raise ValueError
        reference = FilmUploadReference(
            **{
                **value,
                "scope": JobScope(**value["scope"]),
                "film_task": FilmTaskBinding(**value["film_task"]),
            }
        )
        if reference.scope != scope:
            raise ValueError
        return reference
    except (ValueError, TypeError, KeyError, AttributeError):
        raise StoreError(
            "Stored Film upload is invalid; preserve its database for recovery"
        ) from None


def _create_film_uploads(connection):
    connection.execute(
        "CREATE TABLE film_uploads (scope TEXT NOT NULL, production_id TEXT NOT NULL, "
        "task_id TEXT NOT NULL, record TEXT NOT NULL, PRIMARY KEY (scope, production_id, task_id))"
    )


def _lane(value):
    if not isinstance(value, str) or not _LANE.fullmatch(value):
        raise ValueError("Use a lowercase generation lane identifier")
    return value


@dataclass(frozen=True)
class TrainedModelPick:
    """One explicitly chosen trained model and optional strength, never a quote."""

    model_id: str
    scale: float | None = None

    def __post_init__(self):
        _identity(self.model_id)
        if self.scale is not None and (
            type(self.scale) is not float or not math.isfinite(self.scale)
        ):
            raise ValueError("Use a finite decimal strength or no strength")


@dataclass(frozen=True)
class TrainedModelDefault:
    """An explicit lane default for one scope; reusing it still needs a fresh exact quote.

    `stack` adds one or more trained models to a base model, `composition` runs one
    composition through its base, and `custom`/`direct` name one runnable model.
    The store keeps identities only; callers recheck schema routes before quoting.
    """

    lane: str
    route: str
    base_model_id: str
    picks: tuple[TrainedModelPick, ...] = ()

    def __post_init__(self):
        _lane(self.lane)
        if not isinstance(self.route, str) or self.route not in TRAINED_ROUTES:
            raise ValueError("Choose a stack, composition, custom or direct route")
        _identity(self.base_model_id)
        if (
            not isinstance(self.picks, tuple)
            or len(self.picks) > MAX_TRAINED_PICKS
            or any(not isinstance(pick, TrainedModelPick) for pick in self.picks)
        ):
            raise ValueError("Use a bounded immutable selection of trained models")
        identities = [pick.model_id for pick in self.picks]
        if len(set(identities)) != len(identities) or self.base_model_id in identities:
            raise ValueError("Choose each trained model once, separately from its base")
        valid = {
            "stack": bool(self.picks),
            "composition": len(self.picks) == 1 and self.picks[0].scale is None,
            "custom": not self.picks,
            "direct": not self.picks,
        }[self.route]
        if not valid:
            raise ValueError("Trained model picks do not match the selected route")


@dataclass(frozen=True)
class TrainedDefaultState:
    """One lane's saved default in the selected scope; revision 0 means never saved."""

    lane: str
    revision: int
    default: TrainedModelDefault | None = None


def _create_trained_defaults(connection):
    connection.execute(
        "CREATE TABLE trained_defaults (scope TEXT NOT NULL, lane TEXT NOT NULL, "
        "revision INTEGER NOT NULL, record TEXT NOT NULL, PRIMARY KEY (scope, lane))"
    )


def _decode_trained_default(raw, scope, lane, revision):
    try:
        _lane(lane)
        value = json.loads(raw)
        if not isinstance(value, dict) or set(value) != {"scope", "default"}:
            raise ValueError
        fields = value["scope"]
        if not isinstance(fields, dict) or set(fields) != set(JobScope.__dataclass_fields__):
            raise ValueError
        if JobScope(**fields) != scope or type(revision) is not int or revision < 1:
            raise ValueError
        default = value["default"]
        if default is not None:
            if not isinstance(default, dict) or set(default) != set(
                TrainedModelDefault.__dataclass_fields__
            ):
                raise ValueError
            picks = default["picks"]
            if not isinstance(picks, list) or len(picks) > MAX_TRAINED_PICKS:
                raise ValueError
            for pick in picks:
                if not isinstance(pick, dict) or set(pick) != set(
                    TrainedModelPick.__dataclass_fields__
                ):
                    raise ValueError
            default = TrainedModelDefault(
                **{**default, "picks": tuple(TrainedModelPick(**pick) for pick in picks)}
            )
            if default.lane != lane:
                raise ValueError
        return TrainedDefaultState(lane, revision, default)
    except (ValueError, TypeError, KeyError, AttributeError):
        raise StoreError(
            "Stored trained-model default is invalid; preserve its database for recovery"
        ) from None


@dataclass(frozen=True)
class JobIntent:
    request_id: str
    scope: JobScope
    origin: JobOrigin
    operation: str
    target_id: str
    payload_sha256: str
    quote_sha256: str
    quote_cost: str
    mesh_sources: tuple[JobMeshSource, ...] = ()
    film_task: FilmTaskBinding | None = None

    def __post_init__(self):
        _identity(self.request_id)
        _identity(self.target_id)
        if not isinstance(self.scope, JobScope) or not isinstance(self.origin, JobOrigin):
            raise ValueError("A scope and origin are required")
        if not isinstance(self.operation, str) or self.operation not in {
            "model",
            "workflow",
            "prompt",
            "translate",
        }:
            raise ValueError("Choose a model, workflow, prompt or translate operation")
        for value in (self.payload_sha256, self.quote_sha256):
            if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value):
                raise ValueError("Request and quote require SHA-256 identities")
        if (
            not isinstance(self.mesh_sources, tuple)
            or len(self.mesh_sources) > 128
            or any(not isinstance(source, JobMeshSource) for source in self.mesh_sources)
            or len({(source.parameter, source.index) for source in self.mesh_sources})
            != len(self.mesh_sources)
            or (self.mesh_sources and self.operation not in {"model", "workflow"})
        ):
            raise ValueError("Use unique bounded mesh input bindings for a model or workflow")
        if self.film_task is not None and (
            not isinstance(self.film_task, FilmTaskBinding) or self.operation != "model"
        ):
            raise ValueError("Film task bindings require a model generation intent")
        try:
            if not isinstance(self.quote_cost, str) or len(self.quote_cost) > 128:
                raise ValueError
            cost = Decimal(self.quote_cost)
            if not cost.is_finite() or cost < 0:
                raise ValueError
        except (ValueError, InvalidOperation):
            raise ValueError(
                "Quote cost must be an exact finite nonnegative decimal string"
            ) from None


@dataclass(frozen=True)
class CloudJobIntent:
    """A verified cloud result selected locally, never a quoted spend intent."""

    request_id: str
    scope: JobScope
    origin: JobOrigin
    target_id: str
    source: str = "cloud"

    def __post_init__(self):
        _identity(self.request_id)
        _identity(self.target_id)
        if (
            self.source != "cloud"
            or not isinstance(self.scope, JobScope)
            or not isinstance(self.origin, JobOrigin)
            or self.origin.target_id is not None
        ):
            raise ValueError(
                "Use a scoped cloud result and a local scene without source provenance"
            )

    @property
    def operation(self):
        return "model"

    @property
    def quote_cost(self):
        return None

    @property
    def mesh_sources(self):
        return ()

    @property
    def film_task(self):
        return None


class JobState(StrEnum):
    PREPARED = "prepared"
    SUBMITTING = "submitting"
    UNCERTAIN = "uncertain"
    REMOTE = "remote"
    CANCEL_REQUESTED = "cancel_requested"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELED = "canceled"
    DOWNLOADING = "downloading"
    DOWNLOAD_FAILED = "download_failed"
    READY = "ready"
    APPLYING = "applying"
    APPLY_FAILED = "apply_failed"
    APPLIED = "applied"


_TRANSITIONS = {
    JobState.PREPARED: {JobState.SUBMITTING, JobState.CANCELED},
    JobState.SUBMITTING: {JobState.UNCERTAIN, JobState.REMOTE},
    JobState.UNCERTAIN: {JobState.REMOTE},
    JobState.REMOTE: {
        JobState.CANCEL_REQUESTED,
        JobState.SUCCEEDED,
        JobState.FAILED,
        JobState.CANCELED,
    },
    JobState.CANCEL_REQUESTED: {JobState.SUCCEEDED, JobState.FAILED, JobState.CANCELED},
    JobState.SUCCEEDED: {JobState.DOWNLOADING},
    JobState.DOWNLOADING: {JobState.READY, JobState.DOWNLOAD_FAILED},
    JobState.DOWNLOAD_FAILED: {JobState.DOWNLOADING},
    JobState.READY: {JobState.APPLYING},
    JobState.APPLYING: {JobState.APPLIED, JobState.APPLY_FAILED},
    JobState.APPLY_FAILED: {JobState.APPLYING},
}


@dataclass(frozen=True)
class ResultAsset:
    """Immutable URL-free metadata from the scoped remote job/asset response.

    `source` names the delivered file: the asset's own file, or its server-declared
    original (an EXR environment behind a JPEG preview, or a model's WAV behind
    Scenario's MP3 copy). `projection` is semantic
    metadata from the server, never a decoder guarantee or a filename inference.
    """

    asset_id: str
    name: str
    media_type: str
    expected_size: int | None = None
    expected_sha256: str | None = None
    texture_role: str | None = None
    source: str = "asset"
    projection: str | None = None

    def __post_init__(self):
        _identity(self.asset_id)
        try:
            validate_result_name(self.name)
        except TransferError:
            raise ValueError("Use a portable result filename") from None
        if not isinstance(self.media_type, str) or not re.fullmatch(
            r"[a-z0-9][a-z0-9.+-]{0,63}/[a-z0-9][a-z0-9.+-]{0,63}", self.media_type
        ):
            raise ValueError("Use a normalized media type")
        if self.texture_role is not None and (
            not isinstance(self.texture_role, str)
            or self.texture_role not in TEXTURE_ROLES
            or not self.media_type.startswith("image/")
        ):
            raise ValueError("Use a supported image texture role")
        if not isinstance(self.source, str) or self.source not in {"asset", "original"}:
            raise ValueError("Choose the asset file or its declared original")
        if self.source == "original" and self.media_type not in ORIGINAL_MEDIA_TYPES:
            raise ValueError("Only a declared EXR or WAV original may replace the asset file")
        if self.projection is not None and (
            not isinstance(self.projection, str)
            or self.projection not in PROJECTIONS
            or not self.media_type.startswith("image/")
        ):
            raise ValueError("Use a supported image projection")
        if self.expected_size is not None and (
            type(self.expected_size) is not int or not 0 <= self.expected_size <= 2**63 - 1
        ):
            raise ValueError("Invalid expected result size")
        if self.expected_sha256 is not None and (
            not isinstance(self.expected_sha256, str)
            or not re.fullmatch(r"[a-f0-9]{64}", self.expected_sha256)
        ):
            raise ValueError("Invalid expected result digest")


@dataclass(frozen=True)
class StoredResult:
    asset: ResultAsset
    receipt: DownloadedResult | None = None

    def __post_init__(self):
        if not isinstance(self.asset, ResultAsset):
            raise ValueError("Result asset metadata is required")
        if self.receipt is not None:
            if not isinstance(self.receipt, DownloadedResult):
                raise ValueError("A verified download receipt is required")
            if (
                self.receipt.name != self.asset.name
                or self.asset.expected_size not in (None, self.receipt.size)
                or self.asset.expected_sha256 not in (None, self.receipt.sha256)
            ):
                raise ValueError("Download receipt does not match the result manifest")


def _validate_results(results):
    if (
        not isinstance(results, tuple)
        or len(results) > 128
        or not all(isinstance(item, StoredResult) for item in results)
    ):
        raise ValueError("Use a bounded immutable result manifest")
    if len({item.asset.asset_id for item in results}) != len(results) or len(
        {item.asset.name.casefold() for item in results}
    ) != len(results):
        raise ValueError("Result asset identities and filenames must be unique")


class LocalApplicationState(StrEnum):
    APPLYING = "applying"
    APPLIED = "applied"
    FAILED = "failed"


@dataclass(frozen=True)
class LocalApplication:
    """One approved local reuse; never an authorization to generate or download."""

    application_id: str
    source_revision: int
    destination: JobOrigin
    purpose: str
    asset_ids: tuple[str, ...]
    state: LocalApplicationState = LocalApplicationState.APPLYING

    def __post_init__(self):
        _identity(self.application_id)
        if type(self.source_revision) is not int or self.source_revision < 0:
            raise ValueError("Capture the source job revision before local application")
        if not isinstance(self.destination, JobOrigin):
            raise ValueError("Capture the local application destination")
        if not isinstance(self.purpose, str) or self.purpose not in {
            "images",
            "media",
            "model",
            "mesh_edit",
            "world",
            "material",
        }:
            raise ValueError("Choose a supported local application purpose")
        if not isinstance(self.asset_ids, tuple) or not 1 <= len(self.asset_ids) <= 128:
            raise ValueError("Choose a bounded immutable selection of saved assets")
        for asset_id in self.asset_ids:
            _identity(asset_id)
        if len(set(self.asset_ids)) != len(self.asset_ids):
            raise ValueError("Select each saved asset only once")
        if not isinstance(self.state, LocalApplicationState):
            raise ValueError("Use a supported local application state")


@dataclass(frozen=True)
class StoredJob:
    intent: JobIntent | CloudJobIntent
    state: JobState = JobState.PREPARED
    revision: int = 0
    remote_job_id: str | None = None
    results: tuple[StoredResult, ...] = ()
    application_origin: JobOrigin | None = None
    local_applications: tuple[LocalApplication, ...] = ()


def _validate_local_applications(record):
    items = record.local_applications
    if not isinstance(items, tuple) or len(items) > 128:
        raise ValueError("Use a bounded immutable local application history")
    if items and record.state != JobState.APPLIED:
        raise ValueError("Only completed jobs can have local reuse records")
    identifiers = set()
    assets = {item.asset.asset_id for item in record.results}
    revision = -1
    for index, item in enumerate(items):
        if not isinstance(item, LocalApplication):
            raise ValueError("Invalid local application record")
        if (
            item.application_id in identifiers
            or not revision < item.source_revision < record.revision
            or (index > 0 and item.source_revision != revision + 2)
            or not set(item.asset_ids) <= assets
            or (item.state == LocalApplicationState.APPLYING and index != len(items) - 1)
        ):
            raise ValueError("Local application identity, revision or assets are inconsistent")
        identifiers.add(item.application_id)
        revision = item.source_revision
    if items and record.revision != revision + (
        1 if items[-1].state == LocalApplicationState.APPLYING else 2
    ):
        raise ValueError("Local application outcome and saved revision are inconsistent")


def _json(value):
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False
    )


def _decode(raw, scope, *, version=_VERSION):
    try:
        value = json.loads(raw)
        # Never let dataclass defaults turn a truncated in-flight record into
        # a fresh prepared request. Versioned records require every stored key.
        expected = {
            "intent",
            "state",
            "revision",
            "remote_job_id",
            "results",
        }
        if version >= 3:
            expected.add("application_origin")
        if version >= 5:
            expected.add("local_applications")
        if not isinstance(value, dict) or set(value) != expected:
            raise ValueError
        intent = value.pop("intent")
        intent_type = (
            CloudJobIntent
            if version >= 7 and isinstance(intent, dict) and intent.get("source") == "cloud"
            else JobIntent
        )
        if version < 8 and intent_type is JobIntent:
            if not isinstance(intent, dict) or "film_task" in intent:
                raise ValueError
            intent["film_task"] = None
        if version < 6:
            if not isinstance(intent, dict) or "mesh_sources" in intent:
                raise ValueError
            intent["mesh_sources"] = []
        for fields, cls in (
            (intent, intent_type),
            (intent["scope"], JobScope),
            (intent["origin"], JobOrigin),
        ):
            if not isinstance(fields, dict) or set(fields) != set(cls.__dataclass_fields__):
                raise ValueError
        intent["scope"] = JobScope(**intent["scope"])
        intent["origin"] = JobOrigin(**intent["origin"])
        if intent_type is JobIntent:
            film = intent["film_task"]
            if film is not None:
                if not isinstance(film, dict) or set(film) != set(
                    FilmTaskBinding.__dataclass_fields__
                ):
                    raise ValueError
                intent["film_task"] = FilmTaskBinding(**film)
            bindings = intent["mesh_sources"]
            if not isinstance(bindings, list) or len(bindings) > 128:
                raise ValueError
            intent["mesh_sources"] = tuple(_decode_mesh_binding(binding) for binding in bindings)
        application = value.pop("application_origin", None)
        if version == 2 and value["state"] in {"applying", "apply_failed", "applied"}:
            application = asdict(intent["origin"])
        if application is not None:
            if not isinstance(application, dict) or set(application) != set(
                JobOrigin.__dataclass_fields__
            ):
                raise ValueError
            application = JobOrigin(**application)
        raw_results = value.pop("results")
        if not isinstance(raw_results, list) or len(raw_results) > 128:
            raise ValueError
        results = []
        # Earlier schemas receive unknown semantics and the asset's own file;
        # migration never infers a role, original or projection from names.
        absent = {}
        if version < 10:
            absent.update(source="asset", projection=None)
        if version in {2, 3}:
            absent["texture_role"] = None
        for item in raw_results:
            if not isinstance(item, dict) or set(item) != {"asset", "receipt"}:
                raise ValueError
            asset_fields = set(ResultAsset.__dataclass_fields__) - set(absent)
            if not isinstance(item["asset"], dict) or set(item["asset"]) != asset_fields:
                raise ValueError
            item["asset"].update(absent)
            receipt = item["receipt"]
            if receipt is not None:
                if set(receipt) != set(DownloadedResult.__dataclass_fields__):
                    raise ValueError
                receipt = DownloadedResult(**receipt)
            results.append(StoredResult(ResultAsset(**item["asset"]), receipt))
        results = tuple(results)
        _validate_results(results)
        raw_applications = value.pop("local_applications", [])
        if not isinstance(raw_applications, list) or len(raw_applications) > 128:
            raise ValueError
        applications = []
        for item in raw_applications:
            if not isinstance(item, dict) or set(item) != set(
                LocalApplication.__dataclass_fields__
            ):
                raise ValueError
            if not isinstance(item["destination"], dict) or set(item["destination"]) != set(
                JobOrigin.__dataclass_fields__
            ):
                raise ValueError
            if not isinstance(item["asset_ids"], list):
                raise ValueError
            applications.append(
                LocalApplication(
                    **{
                        **item,
                        "destination": JobOrigin(**item["destination"]),
                        "asset_ids": tuple(item["asset_ids"]),
                        "state": LocalApplicationState(item["state"]),
                    }
                )
            )
        record = StoredJob(
            intent=intent_type(**intent),
            results=results,
            application_origin=application,
            local_applications=tuple(applications),
            **value,
        )
        _validate_local_applications(record)
        state = JobState(record.state)
        if (application is not None) != (
            state in {JobState.APPLYING, JobState.APPLY_FAILED, JobState.APPLIED}
        ):
            raise ValueError
        if record.intent.scope != scope or type(record.revision) is not int or record.revision < 0:
            raise ValueError
        if record.remote_job_id is not None:
            _identity(record.remote_job_id)
        needs_remote = state not in {
            JobState.PREPARED,
            JobState.SUBMITTING,
            JobState.UNCERTAIN,
            JobState.CANCELED,
        }
        if needs_remote and record.remote_job_id is None:
            raise ValueError
        if (
            state in {JobState.PREPARED, JobState.SUBMITTING, JobState.UNCERTAIN}
            and record.remote_job_id is not None
        ):
            raise ValueError
        has_results = state in {
            JobState.SUCCEEDED,
            JobState.DOWNLOADING,
            JobState.DOWNLOAD_FAILED,
            JobState.READY,
            JobState.APPLYING,
            JobState.APPLY_FAILED,
            JobState.APPLIED,
        }
        if intent_type is CloudJobIntent and not has_results:
            raise ValueError
        needs_results = has_results and state != JobState.SUCCEEDED
        needs_receipts = state in {
            JobState.READY,
            JobState.APPLYING,
            JobState.APPLY_FAILED,
            JobState.APPLIED,
        }
        if (
            (record.results and not has_results)
            or (needs_results and not record.results)
            or (needs_receipts and any(item.receipt is None for item in record.results))
        ):
            raise ValueError
        if state == JobState.SUCCEEDED and any(item.receipt for item in record.results):
            raise ValueError
        return replace(record, state=state)
    except (ValueError, TypeError, KeyError, AttributeError, TransferError):
        raise StoreError("Stored job data is invalid; preserve the database for recovery") from None


class JobStore:
    """SQLite transactions serialize writers across threads and Blender processes.

    Each operation opens its own connection. The immutable scope participates in
    every key and query. Revision checks reject stale callbacks; intents and known
    remote IDs cannot be replaced. This is storage, not a second job executor.
    """

    def __init__(self, path, scope: JobScope):
        if not isinstance(scope, JobScope):
            raise ValueError("A job scope is required")
        self._path = Path(path).absolute()
        self._scope = scope
        self._key = hashlib.sha256(_json(asdict(scope)).encode()).hexdigest()
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            # Pin parent aliases once so SQL and transfer locks keep one identity.
            # Do not resolve the leaf: a symlinked database must still be rejected.
            self._path = self._path.parent.resolve(strict=True) / self._path.name
            if self._path.is_symlink():
                raise StoreError("The job database must be a regular local file")
            try:
                descriptor = os.open(self._path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            except FileExistsError:
                pass
            else:
                os.close(descriptor)
            with self._connection(write=True, initialize=True) as connection:
                version = connection.execute("PRAGMA user_version").fetchone()[0]
                application = connection.execute("PRAGMA application_id").fetchone()[0]
                tables = connection.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
                if version == 0 and application == 0 and not tables:
                    connection.execute(
                        "CREATE TABLE jobs (scope TEXT NOT NULL, request_id TEXT NOT NULL, "
                        "revision INTEGER NOT NULL, record TEXT NOT NULL, "
                        "PRIMARY KEY (scope, request_id))"
                    )
                    _create_film_uploads(connection)
                    _create_trained_defaults(connection)
                    connection.execute(f"PRAGMA application_id = {_APPLICATION_ID}")
                    connection.execute(f"PRAGMA user_version = {_VERSION}")
                elif version in {2, 3, 4, 5, 6, 7, 8, 9, 10} and application == _APPLICATION_ID:
                    self._upgrade_previous(connection, version)
                else:
                    self._check_version(connection)
                # Derived metadata only; keep saved records and spend claims intact.
                connection.execute(
                    "CREATE INDEX IF NOT EXISTS job_film_task ON jobs "
                    "(scope, json_extract(record, '$.intent.film_task.production_id'), "
                    "json_extract(record, '$.intent.film_task.task_id')) "
                    f"WHERE {_FILM_TASK_FILTER}"
                )
        except OSError:
            raise StoreError("Could not initialize job storage") from None

    @property
    def scope(self):
        return self._scope

    @staticmethod
    def _upgrade_previous(connection, version):
        """Upgrade supported shared stores in the caller's single write transaction.

        Every row is decoded and rewritten in the current shape; one invalid row
        rolls back the whole upgrade and keeps the old version. A concurrent opener
        waits on the same immediate transaction and then sees the current version.
        Missing roles, originals and projections are never guessed.
        """
        film_tasks = set()
        for key, request_id, revision, raw in connection.execute(
            "SELECT scope, request_id, revision, record FROM jobs"
        ).fetchall():
            try:
                scope = JobScope(**json.loads(raw)["intent"]["scope"])
            except (ValueError, TypeError, KeyError):
                raise StoreError(
                    "Stored job data is invalid; preserve the database for recovery"
                ) from None
            record = _decode(raw, scope, version=version)
            expected_key = hashlib.sha256(_json(asdict(scope)).encode()).hexdigest()
            if (
                key != expected_key
                or request_id != record.intent.request_id
                or revision != record.revision
            ):
                raise StoreError("Stored job identity or revision is inconsistent")
            if record.intent.film_task is not None:
                binding = record.intent.film_task
                identity = (key, binding.production_id, binding.task_id)
                if identity in film_tasks:
                    raise StoreError("Film task has multiple saved jobs; preserve storage")
                film_tasks.add(identity)
            connection.execute(
                "UPDATE jobs SET record=? WHERE scope=? AND request_id=?",
                (_json(asdict(record)), key, request_id),
            )
        if version < 9:
            _create_film_uploads(connection)
        else:
            # Schema 9 associations keep their format; recheck them before upgrading.
            for key, production_id, task_id, raw in connection.execute(
                "SELECT scope, production_id, task_id, record FROM film_uploads"
            ).fetchall():
                try:
                    scope = JobScope(**json.loads(raw)["scope"])
                except (ValueError, TypeError, KeyError):
                    raise StoreError(
                        "Stored Film upload is invalid; preserve its database for recovery"
                    ) from None
                reference = _decode_film_upload(raw, scope)
                identity = (key, production_id, task_id)
                if (
                    key != hashlib.sha256(_json(asdict(scope)).encode()).hexdigest()
                    or (reference.film_task.production_id, reference.film_task.task_id)
                    != (production_id, task_id)
                    or identity in film_tasks
                ):
                    raise StoreError("Stored Film upload identity is inconsistent")
        if version < 10:
            _create_trained_defaults(connection)
        else:
            # Schema 10 lane defaults keep their format; recheck them before upgrading.
            for key, lane, revision, raw in connection.execute(
                "SELECT scope, lane, revision, record FROM trained_defaults"
            ).fetchall():
                try:
                    scope = JobScope(**json.loads(raw)["scope"])
                except (ValueError, TypeError, KeyError):
                    raise StoreError(
                        "Stored lane default is invalid; preserve its database for recovery"
                    ) from None
                if key != hashlib.sha256(_json(asdict(scope)).encode()).hexdigest():
                    raise StoreError("Stored lane default identity is inconsistent")
                _decode_trained_default(raw, scope, lane, revision)
        connection.execute(f"PRAGMA user_version = {_VERSION}")

    @contextmanager
    def result_transfer_lock(self, request_id):
        """Exclude live download/recovery commands, including other Blender processes.

        Keep lock files: unlinking one can give competing owners different inodes.
        The OS releases ownership on close/process exit; elapsed time never does.
        All writers must use this protocol and the same privately owned database.
        """
        _identity(request_id)
        descriptor = None
        try:
            parent = _root(self._path.parent)
            directory = parent / ".scenario-result-locks"
            directory.mkdir(mode=0o700, exist_ok=True)
            directory = _root(directory)
            # One digest retains all identities without long concatenated paths.
            # Case aliases identify the same database on Windows.
            key = _json([os.path.normcase(self._path.name), self._key, request_id])
            path = directory / (hashlib.sha256(key.encode()).hexdigest() + ".lock")
            if path.is_symlink():
                raise StoreError("Result lock must be a regular private file")
            descriptor = os.open(path, os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0), 0o600)
            info = os.fstat(descriptor)
            if (
                not stat.S_ISREG(info.st_mode)
                or info.st_nlink != 1
                or info.st_size > 1
                or (info.st_dev, info.st_ino) != (path.lstat().st_dev, path.lstat().st_ino)
            ):
                raise StoreError("Result lock must be a regular private file")
            if info.st_size == 0:
                os.write(descriptor, b"\0")
            os.lseek(descriptor, 0, os.SEEK_SET)
            try:
                if os.name == "nt":
                    import msvcrt

                    msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
                elif os.name == "posix":
                    import fcntl

                    fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                else:
                    raise StoreError("Result transfer locking is unsupported on this platform")
            except OSError:
                raise StoreConflict(
                    "Result transfer is busy or cannot be locked; retry later"
                ) from None
        except (OSError, ValueError, TransferError):
            if descriptor is not None:
                os.close(descriptor)
            raise StoreError("Could not lock private result storage") from None
        except BaseException:
            if descriptor is not None:
                os.close(descriptor)
            raise
        try:
            yield
        finally:
            os.close(descriptor)

    @staticmethod
    def _check_version(connection):
        version = connection.execute("PRAGMA user_version").fetchone()[0]
        application = connection.execute("PRAGMA application_id").fetchone()[0]
        if version != _VERSION or application != _APPLICATION_ID:
            raise StoreError("Unsupported job database format; preserve it for recovery")

    @contextmanager
    def _connection(self, *, write=False, initialize=False):
        connection = None
        try:
            _regular_database(self._path)
            # mode=rw prevents a removed database from silently becoming empty.
            connection = sqlite3.connect(
                self._path.as_uri() + "?mode=rw", uri=True, timeout=2.0, isolation_level=None
            )
            connection.execute("PRAGMA synchronous = FULL")
            connection.execute("BEGIN IMMEDIATE" if write else "BEGIN")
            if not initialize:
                self._check_version(connection)
            yield connection
            connection.commit()
        except sqlite3.Error:
            raise StoreError("Job storage failed; do not dispatch or apply the operation") from None
        finally:
            if connection is not None:
                connection.close()  # Rolls back any uncommitted transaction.

    def _read(self, connection, request_id):
        row = connection.execute(
            "SELECT revision, record FROM jobs WHERE scope=? AND request_id=?",
            (self._key, request_id),
        ).fetchone()
        if row is None:
            return None
        record = _decode(row[1], self.scope)
        if record.intent.request_id != request_id or record.revision != row[0]:
            raise StoreError("Stored job identity or revision is inconsistent")
        return record

    def get(self, request_id):
        _identity(request_id)
        with self._connection() as connection:
            return self._read(connection, request_id)

    def records(self):
        with self._connection() as connection:
            return self._records(connection)

    def unconfirmed(self, operation, target_id, payload_sha256):
        """Submitting or uncertain records with this request identity, in this scope.

        SQLite selects them, so unrelated history is never decoded; a malformed
        row is skipped here and still fails when it is read on its own.
        """
        with self._connection() as connection:
            identifiers = connection.execute(
                "SELECT request_id FROM jobs WHERE scope=? AND json_valid(record) "
                "AND json_extract(record, '$.state') IN (?, ?) "
                "AND json_extract(record, '$.intent.operation')=? "
                "AND json_extract(record, '$.intent.target_id')=? "
                "AND json_extract(record, '$.intent.payload_sha256')=? ORDER BY request_id",
                (
                    self._key,
                    JobState.SUBMITTING.value,
                    JobState.UNCERTAIN.value,
                    operation,
                    target_id,
                    payload_sha256,
                ),
            ).fetchall()
            return tuple(self._read(connection, row[0]) for row in identifiers)

    def _records(self, connection):
        identifiers = connection.execute(
            "SELECT request_id FROM jobs WHERE scope=? ORDER BY request_id", (self._key,)
        ).fetchall()
        return tuple(self._read(connection, row[0]) for row in identifiers)

    def film_job(self, production_id, task_id):
        """Inspect one reserved take in this scope; never reconstruct authorization."""
        _identity(production_id)
        _identity(task_id)
        with self._connection() as connection:
            record = self._film_job(connection, production_id, task_id)
            if (
                record is not None
                and self._film_upload(connection, production_id, task_id) is not None
            ):
                raise StoreError("Film task has conflicting saved identities; inspect storage")
            return record

    def _film_job(self, connection, production_id, task_id):
        rows = connection.execute(
            "SELECT request_id FROM jobs INDEXED BY job_film_task WHERE scope=? "
            "AND json_extract(record, '$.intent.film_task.production_id')=? "
            "AND json_extract(record, '$.intent.film_task.task_id')=? "
            f"AND {_FILM_TASK_FILTER} LIMIT 2",
            (self._key, production_id, task_id),
        ).fetchall()
        if len(rows) > 1:
            raise StoreError("Film task matches multiple saved jobs; preserve them for recovery")
        return self._read(connection, rows[0][0]) if rows else None

    def _film_upload(self, connection, production_id, task_id):
        row = connection.execute(
            "SELECT record FROM film_uploads WHERE scope=? AND production_id=? AND task_id=?",
            (self._key, production_id, task_id),
        ).fetchone()
        if row is None:
            return None
        reference = _decode_film_upload(row[0], self.scope)
        if (reference.film_task.production_id, reference.film_task.task_id) != (
            production_id,
            task_id,
        ):
            raise StoreError("Film upload identity is inconsistent; preserve storage")
        return reference

    def film_upload(self, production_id, task_id):
        _identity(production_id)
        _identity(task_id)
        with self._connection() as connection:
            reference = self._film_upload(connection, production_id, task_id)
            if (
                reference is not None
                and self._film_job(connection, production_id, task_id) is not None
            ):
                raise StoreError("Film task has conflicting saved identities; inspect storage")
            return reference

    def bind_film_upload(self, reference: FilmUploadReference):
        """Save verified imported-upload evidence supplied by the coordinator."""
        if not isinstance(reference, FilmUploadReference) or reference.scope != self.scope:
            raise ValueError("Film upload belongs to another scope")
        binding = reference.film_task
        with self._connection(write=True) as connection:
            if self._film_job(connection, binding.production_id, binding.task_id) is not None:
                raise StoreConflict("Film task already has a saved job; name a new upload task")
            previous = self._film_upload(connection, binding.production_id, binding.task_id)
            if previous is not None:
                # Appending a new take changes the recipe, not this chosen source.
                if replace(previous, film_task=binding) != reference or (
                    previous.film_task.task_sha256 != binding.task_sha256
                ):
                    raise StoreConflict("Film upload task is already bound; name a new task")
                return previous
            connection.execute(
                "INSERT INTO film_uploads VALUES (?, ?, ?, ?)",
                (self._key, binding.production_id, binding.task_id, _json(asdict(reference))),
            )
        return reference

    def _trained_default(self, connection, lane):
        row = connection.execute(
            "SELECT revision, record FROM trained_defaults WHERE scope=? AND lane=?",
            (self._key, lane),
        ).fetchone()
        if row is None:
            return TrainedDefaultState(lane, 0)
        return _decode_trained_default(row[1], self.scope, lane, row[0])

    def trained_default(self, lane):
        """Inspect one lane's explicit default in this scope; never apply or quote it."""
        _lane(lane)
        with self._connection() as connection:
            return self._trained_default(connection, lane)

    def trained_defaults(self):
        """Return every saved lane default in this scope, ordered by lane."""
        with self._connection() as connection:
            lanes = connection.execute(
                "SELECT lane FROM trained_defaults WHERE scope=? ORDER BY lane", (self._key,)
            ).fetchall()
            states = tuple(self._trained_default(connection, row[0]) for row in lanes)
        return tuple(state for state in states if state.default is not None)

    def set_trained_default(self, default: TrainedModelDefault, *, expected_revision):
        """Save an explicit choice; revision 0 creates the lane's first default."""
        if not isinstance(default, TrainedModelDefault):
            raise ValueError("A trained-model default is required")
        return self._write_trained_default(default.lane, default, expected_revision)

    def clear_trained_default(self, lane, *, expected_revision):
        """Clear a lane default and keep its revision, so a stale writer still conflicts."""
        return self._write_trained_default(_lane(lane), None, expected_revision)

    def _write_trained_default(self, lane, default, expected_revision):
        if type(expected_revision) is not int or expected_revision < 0:
            raise ValueError("A nonnegative expected revision is required")
        with self._connection(write=True) as connection:
            current = self._trained_default(connection, lane)
            if current.revision != expected_revision:
                raise StoreConflict("Saved default changed; reload before acting")
            if default is None and current.default is None:
                return current
            updated = TrainedDefaultState(lane, current.revision + 1, default)
            record = _json(
                {
                    "scope": asdict(self.scope),
                    "default": asdict(default) if default is not None else None,
                }
            )
            if current.revision == 0:
                connection.execute(
                    "INSERT INTO trained_defaults VALUES (?, ?, ?, ?)",
                    (self._key, lane, updated.revision, record),
                )
            else:
                connection.execute(
                    "UPDATE trained_defaults SET revision=?, record=? "
                    "WHERE scope=? AND lane=? AND revision=?",
                    (updated.revision, record, self._key, lane, expected_revision),
                )
        return updated

    def adopt_cloud_job(self, intent: CloudJobIntent, remote_job_id):
        """Save authoritative successful-job evidence supplied by the coordinator."""
        if not isinstance(intent, CloudJobIntent) or intent.scope != self.scope:
            raise ValueError("Use a cloud result intent belonging to this scope")
        _identity(remote_job_id)
        with self._connection(write=True) as connection:
            existing = [
                record
                for record in self._records(connection)
                if record.remote_job_id == remote_job_id
            ]
            if existing:
                if (
                    len(existing) != 1
                    or existing[0].intent.operation != "model"
                    or existing[0].intent.target_id != intent.target_id
                ):
                    raise StoreConflict(
                        "Remote job matches conflicting local records; inspect them"
                    )
                return existing[0]
            if self._read(connection, intent.request_id) is not None:
                raise StoreConflict("Local recovery identity already exists")
            record = StoredJob(intent, state=JobState.SUCCEEDED, remote_job_id=remote_job_id)
            connection.execute(
                "INSERT INTO jobs VALUES (?, ?, ?, ?)",
                (self._key, intent.request_id, 0, _json(asdict(record))),
            )
        return record

    def create(self, intent: JobIntent):
        if not isinstance(intent, JobIntent) or intent.scope != self.scope:
            raise ValueError("Intent belongs to another scope")
        record = StoredJob(intent)
        with self._connection(write=True) as connection:
            if self._read(connection, intent.request_id) is not None:
                raise StoreConflict("Request identity already exists; do not resubmit")
            if intent.film_task is not None and (
                self._film_job(connection, intent.film_task.production_id, intent.film_task.task_id)
                is not None
                or self._film_upload(
                    connection, intent.film_task.production_id, intent.film_task.task_id
                )
                is not None
            ):
                raise StoreConflict(
                    "Film task already has a saved job; inspect it or name a new take"
                )
            connection.execute(
                "INSERT INTO jobs VALUES (?, ?, ?, ?)",
                (self._key, intent.request_id, 0, _json(asdict(record))),
            )
        return record

    def transition(
        self,
        request_id,
        *,
        expected_revision,
        state: JobState,
        remote_job_id=None,
        application_origin=None,
    ):
        _identity(request_id)
        if type(expected_revision) is not int or expected_revision < 0:
            raise ValueError("A nonnegative expected revision is required")
        if not isinstance(state, JobState):
            raise ValueError("A supported job state is required")
        if remote_job_id is not None:
            _identity(remote_job_id)
        if application_origin is not None and (
            state != JobState.APPLYING or not isinstance(application_origin, JobOrigin)
        ):
            raise ValueError("Bind an application origin only when claiming application")
        with self._connection(write=True) as connection:
            previous = self._read(connection, request_id)
            if previous is None or previous.revision != expected_revision:
                raise StoreConflict("Job is missing or changed; reload before acting")
            if state not in _TRANSITIONS.get(previous.state, set()):
                raise ValueError("This job transition is not allowed")
            if remote_job_id is not None and previous.remote_job_id not in {None, remote_job_id}:
                raise ValueError("A known remote job identity cannot change")
            remote = previous.remote_job_id or remote_job_id
            if state == JobState.REMOTE and remote is None:
                raise ValueError("Reconciliation requires an authoritative remote job identity")
            if previous.remote_job_id is None and state != JobState.REMOTE and remote is not None:
                raise ValueError("Only remote acknowledgement can bind a remote job identity")
            if state == JobState.DOWNLOADING and not previous.results:
                raise ValueError("Persist the result manifest before downloading")
            if state == JobState.READY and any(item.receipt is None for item in previous.results):
                raise ValueError("Every result needs a verified download receipt")
            updated = replace(
                previous,
                state=state,
                revision=previous.revision + 1,
                remote_job_id=remote,
                application_origin=(application_origin or previous.intent.origin)
                if state == JobState.APPLYING
                else previous.application_origin,
            )
            connection.execute(
                "UPDATE jobs SET revision=?, record=? WHERE scope=? AND request_id=? AND revision=?",
                (
                    updated.revision,
                    _json(asdict(updated)),
                    self._key,
                    request_id,
                    expected_revision,
                ),
            )
        return updated

    def _update_results(self, request_id, expected_revision, update):
        _identity(request_id)
        if type(expected_revision) is not int or expected_revision < 0:
            raise ValueError("A nonnegative expected revision is required")
        with self._connection(write=True) as connection:
            previous = self._read(connection, request_id)
            if previous is None or previous.revision != expected_revision:
                raise StoreConflict("Job is missing or changed; reload before acting")
            results = update(previous)
            _validate_results(results)
            updated = replace(previous, results=results, revision=previous.revision + 1)
            connection.execute(
                "UPDATE jobs SET revision=?, record=? WHERE scope=? AND request_id=? AND revision=?",
                (
                    updated.revision,
                    _json(asdict(updated)),
                    self._key,
                    request_id,
                    expected_revision,
                ),
            )
        return updated

    def _update_local_applications(self, request_id, expected_revision, update):
        _identity(request_id)
        if type(expected_revision) is not int or expected_revision < 0:
            raise ValueError("A nonnegative expected revision is required")
        with self._connection(write=True) as connection:
            previous = self._read(connection, request_id)
            if previous is None or previous.revision != expected_revision:
                raise StoreConflict("Saved result changed; inspect it before local reuse")
            if previous.state != JobState.APPLIED:
                raise ValueError("Local reuse requires a completed original application")
            updated = replace(
                previous, revision=previous.revision + 1, local_applications=update(previous)
            )
            _validate_local_applications(updated)
            connection.execute(
                "UPDATE jobs SET revision=?, record=? WHERE scope=? AND request_id=? AND revision=?",
                (
                    updated.revision,
                    _json(asdict(updated)),
                    self._key,
                    request_id,
                    expected_revision,
                ),
            )
        return updated

    def claim_local_application(
        self, request_id, *, expected_revision, application_id, destination, purpose, asset_ids
    ):
        """Persist one explicit reuse before scene mutation; leave generation completed.

        The coordinator must verify receipts and the approved destination first.
        This storage primitive cannot establish byte integrity or user approval.
        An unfinished record survives restart and forbids another reuse claim.
        """
        application = LocalApplication(
            application_id, expected_revision, destination, purpose, asset_ids
        )

        def update(previous):
            if any(
                item.state == LocalApplicationState.APPLYING for item in previous.local_applications
            ):
                raise StoreConflict(
                    "An unfinished local application requires review; do not repeat it"
                )
            if any(item.application_id == application_id for item in previous.local_applications):
                raise StoreConflict("Local application identity was already used")
            return (*previous.local_applications, application)

        return self._update_local_applications(request_id, expected_revision, update)

    def finish_local_application(self, request_id, *, expected_revision, application_id, state):
        """Record known success or confirmed no-change/full rollback, never uncertainty.

        A failed write may have committed. Inspect its exact successor or retry
        only the known outcome; this method never authorizes scene work again.
        """
        _identity(application_id)
        if not isinstance(state, LocalApplicationState) or state == LocalApplicationState.APPLYING:
            raise ValueError("Record only a known local application outcome")

        def update(previous):
            items = previous.local_applications
            if (
                not items
                or items[-1].application_id != application_id
                or items[-1].state != LocalApplicationState.APPLYING
            ):
                raise StoreConflict("Use the unfinished local application identity")
            return (*items[:-1], replace(items[-1], state=state))

        return self._update_local_applications(request_id, expected_revision, update)

    def set_results(self, request_id, assets, *, expected_revision):
        """Bind a trusted scoped result manifest once, before any file transfer."""
        if not isinstance(assets, tuple) or not 1 <= len(assets) <= 128:
            raise ValueError("A nonempty immutable asset manifest is required")
        results = tuple(StoredResult(asset) for asset in assets)
        _validate_results(results)

        def update(previous):
            if previous.state != JobState.SUCCEEDED or previous.results:
                raise ValueError("Only a successful job without a manifest accepts result metadata")
            return results

        return self._update_results(request_id, expected_revision, update)

    def record_download(
        self,
        request_id,
        asset_id,
        receipt,
        *,
        expected_revision,
        allow_mesh_size_correction=False,
    ):
        """Save one immutable verified receipt; a failure never authorizes generation."""
        _identity(asset_id)
        if not isinstance(receipt, DownloadedResult):
            raise ValueError("A verified download receipt is required")
        if type(allow_mesh_size_correction) is not bool:
            raise ValueError("Choose an explicit mesh size policy")

        def update(previous):
            if previous.state != JobState.DOWNLOADING:
                raise ValueError("Claim downloading before recording a result")
            found = next(
                (item for item in previous.results if item.asset.asset_id == asset_id), None
            )
            if found is None or found.receipt is not None:
                raise ValueError("Result is absent or already has a receipt")
            asset = found.asset
            if allow_mesh_size_correction:
                if asset.media_type not in REWRITTEN_MESH_TYPES:
                    raise ValueError("Only rewritten OBJ/MTL results accept a size correction")
                asset = replace(asset, expected_size=receipt.size)
            recorded = StoredResult(asset, receipt)
            return tuple(recorded if item is found else item for item in previous.results)

        return self._update_results(request_id, expected_revision, update)
