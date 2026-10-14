# SPDX-FileCopyrightText: 2026 Scenario Inc.
# SPDX-License-Identifier: GPL-3.0-or-later
"""Explicit upload commands on the shared SDK, store and worker owner."""

import math
import threading
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from .store import JobOrigin, StoreConflict, StoreError
from .upload_sources import UploadSources
from .upload_store import StoredUpload, UploadState, UploadStore
from .upload_transfers import PartUploader


class UploadError(RuntimeError):
    """Sanitized upload failure; inspect durable progress before further actions."""


class UploadMutationUncertain(UploadError):
    """A claimed initialization, part or completion may have reached the service."""


class UploadPlanUnavailable(UploadError):
    """No usable part destinations exist in this owner; nothing was claimed or sent.

    Scenario returns signed part URLs only in the create response and offers no
    way to reissue them, so an interrupted transfer cannot resume. Restart the
    upload: abandon the known upload and upload the saved snapshot again.
    """


class UploadRecoveryAction(StrEnum):
    REVIEW_SOURCE = "review_source"
    RECONCILE_UNKNOWN = "reconcile_unknown"
    REVIEW_TRANSFER = "review_transfer"
    RESTART_UPLOAD = "restart_upload"
    POLL_REMOTE = "poll_remote"
    FINISHED = "finished"


@dataclass(frozen=True)
class UploadRecoveryItem:
    """An inspection snapshot, not permission to initialize, send or retry."""

    record: StoredUpload
    action: UploadRecoveryAction


_RECOVERY = {
    UploadState.PREPARED: UploadRecoveryAction.REVIEW_SOURCE,
    UploadState.INITIALIZING: UploadRecoveryAction.RECONCILE_UNKNOWN,
    UploadState.INITIALIZATION_UNCERTAIN: UploadRecoveryAction.RECONCILE_UNKNOWN,
    UploadState.UPLOADING: UploadRecoveryAction.REVIEW_TRANSFER,
    UploadState.PART_UNCERTAIN: UploadRecoveryAction.RESTART_UPLOAD,
    UploadState.FINALIZING: UploadRecoveryAction.POLL_REMOTE,
    UploadState.FINALIZATION_UNCERTAIN: UploadRecoveryAction.POLL_REMOTE,
    UploadState.PROCESSING: UploadRecoveryAction.POLL_REMOTE,
    UploadState.IMPORTED: UploadRecoveryAction.FINISHED,
    UploadState.FAILED: UploadRecoveryAction.FINISHED,
    UploadState.CANCELED: UploadRecoveryAction.FINISHED,
    UploadState.ABANDONED: UploadRecoveryAction.FINISHED,
}
_PLAN_UNAVAILABLE = (
    "Part destinations exist only in the session that created this upload; restart the upload"
)


def _integer(value, expected):
    return not isinstance(value, bool) and isinstance(value, (int, float)) and value == expected


class _PartPlan:
    """Signed part destinations held in memory by the owner that received them.

    Only the SDK create response carries them. They are never persisted,
    logged, returned or serialized, and the representation omits them.
    """

    __slots__ = ("_destinations", "_identity")

    def __init__(self, current, destinations):
        intent = current.intent
        self._identity = (
            intent.request_id,
            current.upload_id,
            intent.file_sha256,
            intent.file_size,
            intent.part_sha256,
        )
        self._destinations = destinations

    def matches(self, current):
        intent = current.intent
        return self._identity == (
            intent.request_id,
            current.upload_id,
            intent.file_sha256,
            intent.file_size,
            intent.part_sha256,
        )

    def destination(self, number):
        return self._destinations[number - 1]

    def __repr__(self):
        return "<upload part plan>"

    def __reduce__(self):
        raise TypeError("Upload part plans cannot be serialized")


class UploadCommands:
    """Shares its coordinator's adapter, active guard and bounded worker queue."""

    def __init__(self, adapter, store, sources, uploader, guard, *, clock=time.time):
        if (
            not isinstance(store, UploadStore)
            or not isinstance(sources, UploadSources)
            or not isinstance(uploader, PartUploader)
        ):
            raise TypeError("Configure the scoped upload store, sources and part uploader")
        if sources.max_part_bytes > uploader.policy.max_bytes:
            raise ValueError("Upload source parts exceed the storage transfer limit")
        self._adapter, self._store, self._sources = adapter, store, sources
        self._uploader, self._guard, self._clock = uploader, guard, clock
        # In-memory only: create-response part plans and claimed in-flight PUTs.
        self._plans, self._sending, self._retired = {}, set(), False
        self._plans_lock = threading.Lock()

    def retire(self):
        """Forget every cached part plan; a retired owner never caches another."""
        with self._plans_lock:
            self._retired = True
            self._plans.clear()

    def _keep_plan(self, request_id, plan):
        with self._plans_lock:
            if not self._retired:
                self._plans[request_id] = plan

    def _drop_plan(self, request_id):
        with self._plans_lock:
            self._plans.pop(request_id, None)

    def _origin_current(self, origin):
        try:
            with self._guard(origin):
                return True
        except UploadError:
            return False

    def inspect(self, request_id):
        """Read one immutable scoped record, or None; never fetch remote status."""
        with self._guard():
            return self._store.get(request_id)

    def measure_media(self, request_id, *, expected_revision, root, cancel):
        """Inspect retained imported-source bytes without any upload/service action."""
        current = self._current(request_id, expected_revision, {UploadState.IMPORTED})
        result = self._sources.measure_media(current.intent, root=root, cancel=cancel)
        with self._guard():
            if self._store.get(request_id) != current:
                raise StoreConflict("Upload changed during media inspection")
        return result

    def mesh_sources(self, asset_id):
        """Read selected captured exports in this scope; never infer a primary source."""
        with self._guard():
            return self._store.mesh_sources(asset_id)

    def recovery_plan(self):
        """Inspect saved progress without writes, file verification or dispatch.

        A missing receipt does not prove an active worker is dead or bytes were
        rejected. Known uploads can be polled; an unknown ID cannot be guessed.
        An upload this owner cannot continue (no cached part plan, a stale
        origin or a claim it is not sending) can only be restarted.
        """
        with self._guard():
            with self._plans_lock:
                planned, sending = set(self._plans), set(self._sending)
            return tuple(
                UploadRecoveryItem(record, self._suggest(record, planned, sending))
                for record in self._store.records()
            )

    def _suggest(self, record, planned, sending):
        if record.state != UploadState.UPLOADING:
            return _RECOVERY[record.state]
        request_id = record.intent.request_id
        if record.active_part is not None:
            if request_id in sending:
                return UploadRecoveryAction.POLL_REMOTE
            return UploadRecoveryAction.RESTART_UPLOAD
        complete = len(record.receipts) == len(record.intent.part_sha256)
        if (complete or request_id in planned) and self._origin_current(record.intent.origin):
            return UploadRecoveryAction.REVIEW_TRANSFER
        return UploadRecoveryAction.RESTART_UPLOAD

    def discard_source(self, request_id, *, expected_revision):
        """Explicitly remove a finished upload's snapshot, retaining durable history."""
        current = self._current(
            request_id,
            expected_revision,
            {
                UploadState.CANCELED,
                UploadState.FAILED,
                UploadState.IMPORTED,
                UploadState.ABANDONED,
            },
        )

        @contextmanager
        def guard():
            with self._guard():
                if self._store.get(request_id) != current:
                    raise StoreConflict("Upload changed before source cleanup; reload it")
                yield

        try:
            self._sources.discard(current.intent, guard=guard)
        except (StoreError, UploadError):
            raise
        except Exception:
            raise UploadError(
                "Could not discard the staged upload source; inspect local storage"
            ) from None
        return current

    def _current(self, request_id, revision, states):
        with self._guard():
            current = self._store.get(request_id)
            if (
                type(revision) is not int
                or current is None
                or current.revision != revision
                or current.state not in states
            ):
                raise StoreConflict("Upload changed or cannot perform this action; reload it")
            return current

    def _transition(self, current, state, **kwargs):
        return self._store.transition(
            current.intent.request_id, expected_revision=current.revision, state=state, **kwargs
        )

    def prepare(
        self, source, *, origin, kind, content_type, mesh_source=None, expected_sha256=None
    ):
        # SDK model uploads produce model identities, not asset references.
        if kind == "model":
            raise UploadError("Model import needs its own result lifecycle")
        if not isinstance(origin, JobOrigin):
            raise UploadError("Capture the upload origin before staging its source")
        with self._guard(origin):
            pass
        try:
            intent = self._sources.stage(
                source,
                request_id=uuid.uuid4().hex,
                scope=self._store.scope,
                origin=origin,
                kind=kind,
                content_type=content_type,
                mesh_source=mesh_source,
                expected_sha256=expected_sha256,
            )
        except Exception:
            raise UploadError("Could not prepare a stable upload source") from None
        try:
            with self._guard(origin):
                return self._store.create(intent)
        except BaseException:
            self._discard_unrecorded(intent)
            raise

    def initialize(self, request_id, *, expected_revision):
        current = self._current(request_id, expected_revision, {UploadState.PREPARED})
        with self._guard(current.intent.origin):
            pass
        try:
            self._sources.verify(current.intent)
        except Exception:
            raise UploadError("Staged upload is unavailable or changed") from None
        with self._guard(current.intent.origin):
            current = self._transition(current, UploadState.INITIALIZING)
        try:
            intent = current.intent
            response = self._adapter.create_upload(
                kind=intent.kind,
                file_name=intent.file_name,
                content_type=intent.content_type,
                file_size=intent.file_size,
                parts=len(intent.part_sha256),
            )
            # Persist the returned remote identity before inspecting any transfer instructions.
            current = self._transition(current, UploadState.UPLOADING, upload_id=response.get("id"))
        except StoreError:
            raise
        except Exception:
            self._transition(current, UploadState.INITIALIZATION_UNCERTAIN)
            raise UploadMutationUncertain(
                "Upload initialization is uncertain; do not recreate it"
            ) from None
        current = self._observe(current, response)
        if current.state == UploadState.UPLOADING:
            # The create response is the only source of signed part URLs. Keep
            # a validated plan in memory; the record never stores the URLs.
            try:
                self._keep_plan(request_id, self._plan(current, response))
            except UploadError:
                raise UploadPlanUnavailable(
                    "Scenario returned no usable part destinations; restart the upload"
                ) from None
        return current

    def cancel_prepared(self, request_id, *, expected_revision):
        """Cancel only unclaimed local intent, retaining sources without remote work."""
        current = self._current(request_id, expected_revision, {UploadState.PREPARED})
        # Origin loss does not prevent discarding local intent. The active scope
        # and revision still guard the write against initialization and switches.
        with self._guard():
            return self._transition(current, UploadState.CANCELED)

    def _plan(self, current, response):
        """Validate a complete multipart plan from a create (or future) response."""
        intent = current.intent
        count = len(intent.part_sha256)
        if (
            response.get("id") != current.upload_id
            or response.get("status") != "pending"
            or response.get("source") != "multipart"
            or response.get("kind") != intent.kind
            or response.get("originalFileName") != intent.file_name
            or response.get("contentType") != intent.content_type
            or not _integer(response.get("fileSize"), intent.file_size)
            or not _integer(response.get("partsCount"), count)
        ):
            raise UploadError("Remote upload does not match the saved source and transfer state")
        parts = response.get("parts")
        if not isinstance(parts, list) or len(parts) != count:
            raise UploadError("Remote upload has an incomplete part plan")
        destinations = []
        for expected, part in enumerate(parts, 1):
            if not isinstance(part, dict) or not _integer(part.get("number"), expected):
                raise UploadError("Remote upload has an invalid part sequence")
            try:
                expires = part.get("expires")
                if not isinstance(expires, str):
                    raise ValueError
                expiry = datetime.fromisoformat(expires.replace("Z", "+00:00"))
                if expiry.tzinfo is None:
                    raise ValueError
                destination = (part.get("url"), expiry.timestamp())
                self._fresh(destination)
            except Exception:
                raise UploadError(
                    "Upload part destination is invalid or expires too soon"
                ) from None
            destinations.append(destination)
        return _PartPlan(current, tuple(destinations))

    def _fresh(self, destination):
        """Return a policy-approved URL valid for more than 30 more seconds.

        This local margin is not a transfer-duration guarantee.
        """
        url, expiry = destination
        now = self._clock()
        if not math.isfinite(now) or not math.isfinite(expiry) or expiry <= now + 30:
            raise ValueError("Upload part destination expires too soon")
        self._uploader.policy.destination(url)
        return url

    def _pending(self, current, response):
        """Liveness check for a retrieval, which omits the create-only plan fields."""
        intent = current.intent
        if (
            response.get("id") != current.upload_id
            or response.get("status") != "pending"
            or response.get("source") != "multipart"
            or response.get("kind") != intent.kind
        ):
            raise UploadError("Remote upload is no longer pending for this source")
        # Optional metadata is absent on live retrievals; when present it must match.
        for key, expected in (
            ("originalFileName", intent.file_name),
            ("contentType", intent.content_type),
        ):
            if response.get(key) is not None and response.get(key) != expected:
                raise UploadError("Remote upload does not match the saved source")
        for key, expected in (
            ("fileSize", intent.file_size),
            ("partsCount", len(intent.part_sha256)),
        ):
            if response.get(key) is not None and not _integer(response.get(key), expected):
                raise UploadError("Remote upload does not match the saved source")

    def _part_url(self, current, response, number, plan):
        self._pending(current, response)
        if response.get("parts") is not None:
            # Forward-compatible: a retrieval that does carry a full plan must
            # pass every create-plan check; it is used once and never saved.
            plan = self._plan(current, response)
        if plan is None:
            raise UploadPlanUnavailable(_PLAN_UNAVAILABLE)
        try:
            return self._fresh(plan.destination(number))
        except Exception:
            raise UploadPlanUnavailable(
                "Upload part destinations expired or are unusable; restart the upload"
            ) from None

    def transfer_part(self, request_id, *, expected_revision):
        current = self._current(request_id, expected_revision, {UploadState.UPLOADING})
        if current.active_part is not None or len(current.receipts) == len(
            current.intent.part_sha256
        ):
            raise StoreConflict("No unclaimed upload part is available")
        number = len(current.receipts) + 1
        with self._plans_lock:
            plan = self._plans.get(request_id)
        plan = plan if plan is not None and plan.matches(current) else None
        # A plan serves one uninterrupted transfer in this owner. Every failure
        # except a lost claim race, and the final receipt, ends it: the owner
        # then reports a restart instead of a resumable transfer.
        keep = sending = False
        try:
            with self._guard(current.intent.origin):
                pass
            try:
                response = self._adapter.upload(current.upload_id)
                data = self._sources.part(current.intent, number)
                url = self._part_url(current, response, number, plan)
            except UploadPlanUnavailable:
                raise
            except Exception:
                raise UploadError("Could not verify the next upload part; no bytes sent") from None
            with self._guard(current.intent.origin):
                try:
                    current = self._store.claim_part(request_id, expected_revision=current.revision)
                except StoreConflict:
                    keep = True  # Another command owns this transfer step.
                    raise
                with self._plans_lock:
                    self._sending.add(request_id)
                    sending = True
            try:
                receipt = self._uploader.upload(
                    url,
                    data,
                    number=number,
                    content_type=current.intent.content_type,
                    expected_sha256=current.intent.part_sha256[number - 1],
                )
            except Exception:
                self._transition(current, UploadState.PART_UNCERTAIN)
                raise UploadMutationUncertain(
                    "Upload part is uncertain; do not send it again"
                ) from None
            # A persistence failure leaves the in-flight claim intact, even after an acknowledged PUT.
            recorded = self._store.record_part(
                request_id, receipt, expected_revision=current.revision
            )
            # Completion needs only the upload ID, never a part destination.
            keep = len(recorded.receipts) < len(recorded.intent.part_sha256)
            return recorded
        finally:
            with self._plans_lock:
                if sending:
                    self._sending.discard(request_id)
                if not keep:
                    self._plans.pop(request_id, None)

    def restart(self, request_id, *, expected_revision, origin):
        """Abandon an uncompleted upload and prepare its saved snapshot again.

        Only records whose suggested recovery is RESTART_UPLOAD qualify. Part
        URLs cannot be reissued, so an upload this owner cannot continue is
        never resumed or completed. The abandoned record keeps its remote ID
        and receipts; a new PREPARED request (returned) uploads the same verified
        bytes. Nothing is sent here. Captured mesh provenance is kept only when
        the replacement uses the original, still current origin.
        """
        current = self._current(
            request_id, expected_revision, {UploadState.UPLOADING, UploadState.PART_UNCERTAIN}
        )
        if not isinstance(origin, JobOrigin):
            raise UploadError("Capture the upload origin before restarting it")
        with self._guard():
            with self._plans_lock:
                planned, sending = set(self._plans), set(self._sending)
            if request_id in sending:
                raise StoreConflict("An upload part is still being sent; wait for it to finish")
            # Never abandon an upload this owner can still continue or complete.
            if self._suggest(current, planned, sending) != UploadRecoveryAction.RESTART_UPLOAD:
                raise StoreConflict("This upload can still continue; restart is not needed")
        with self._guard(origin):
            pass
        mesh_source = current.intent.mesh_source if origin == current.intent.origin else None
        try:
            intent = self._sources.restage(
                current.intent,
                request_id=uuid.uuid4().hex,
                origin=origin,
                mesh_source=mesh_source,
            )
        except Exception:
            raise UploadError(
                "The saved upload copy is unavailable or changed; upload the original file again"
            ) from None
        try:
            with self._guard(origin):
                with self._plans_lock:
                    if request_id in self._sending:
                        raise StoreConflict(
                            "An upload part is still being sent; wait for it to finish"
                        )
                _, replacement = self._store.abandon(
                    request_id, expected_revision=current.revision, replacement=intent
                )
        except BaseException:
            self._discard_unrecorded(intent)
            raise
        self._drop_plan(request_id)
        return replacement

    def _discard_unrecorded(self, intent):
        """Remove a new snapshot that no saved request owns after prepare or restart fails.

        Only the failing command knows the fresh request ID, so no other command
        can record it. A found record or an unreadable store keeps the copy:
        leftover private bytes are safer than removing a referenced snapshot.
        """
        try:
            if self._store.get(intent.request_id) is None:
                self._sources.discard(intent)
        except Exception:
            pass  # The original failure is what the caller must see.

    def finalize(self, request_id, *, expected_revision):
        current = self._current(request_id, expected_revision, {UploadState.UPLOADING})
        with self._guard(current.intent.origin):
            current = self._transition(current, UploadState.FINALIZING)
        self._drop_plan(request_id)
        try:
            response = self._adapter.complete_upload(current.upload_id)
            updated = self._observe(current, response)
            if updated == current:
                raise UploadError("Completion was not confirmed")
            return updated
        except StoreError:
            raise
        except Exception:
            self._transition(current, UploadState.FINALIZATION_UNCERTAIN)
            raise UploadMutationUncertain(
                "Upload completion is uncertain; retrieve its status"
            ) from None

    def refresh(self, request_id, *, expected_revision):
        current = self._current(
            request_id,
            expected_revision,
            {
                UploadState.UPLOADING,
                UploadState.PART_UNCERTAIN,
                UploadState.FINALIZING,
                UploadState.FINALIZATION_UNCERTAIN,
                UploadState.PROCESSING,
            },
        )
        try:
            response = self._adapter.upload(current.upload_id)
        except Exception:
            raise UploadError("Could not retrieve the known upload") from None
        return self._observe(current, response)

    def _observe(self, current, response):
        if (
            response.get("id") != current.upload_id
            or response.get("kind") != current.intent.kind
            or response.get("source") != "multipart"
        ):
            raise UploadError("Scenario returned another upload identity or kind")
        status = response.get("status")
        target = {
            "complete": UploadState.PROCESSING,
            "validating": UploadState.PROCESSING,
            "validated": UploadState.PROCESSING,
            "imported": UploadState.IMPORTED,
            "failed": UploadState.FAILED,
        }.get(status)
        if status != "pending" and target is None:
            raise UploadError("Scenario returned an unrecognized upload state")
        if target is None or target == current.state:
            if self._store.get(current.intent.request_id) != current:
                raise StoreConflict("Upload changed during retrieval; reload it")
            return current
        self._drop_plan(current.intent.request_id)
        try:
            return self._transition(
                current,
                target,
                asset_id=response.get("entityId") if target == UploadState.IMPORTED else None,
            )
        except ValueError:
            raise UploadError("Scenario returned an invalid imported asset identity") from None
