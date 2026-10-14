# SPDX-FileCopyrightText: 2026 Scenario Inc.
# SPDX-License-Identifier: GPL-3.0-or-later
"""Credential-bound catalog reads through the shared SDK adapter, without bpy."""

import copy
import threading
from concurrent.futures import Future
from contextlib import contextmanager

from .. import history
from ..jobs.store import JobScope
from ..schema.params import parse_schema
from .catalog import ModelRecord
from .catalog import trained_models as trained_pairs
from .errors import ScenarioError
from .sdk_adapter import (
    MODEL_BULK_LIMIT,
    AdapterError,
    AdapterUnavailable,
    SDKAdapter,
    model_identifiers,
)

# Step jobs read per history page to price workflow runs whose steps are not
# listed on that page. Unread steps leave the run's cost unavailable, never 0.
WORKFLOW_STEP_READS = 24


class SDKCatalog:
    """One application catalog context, independent of the view that requested it.

    Reads share this connection's HTTP pool until retirement. Retirement disables
    subsequent requests and closes the pool only after the final reader exits.
    Cache entries live only in this connection. Bulk summaries are cached apart
    from model details, so they never stand in for a form schema. An optional
    local job scope binds the adapter to credential-isolated storage; it is not
    a server account claim.
    """

    def __init__(self, credentials, *, online, adapter_factory=None, scope=None):
        credentials.authorization()
        if scope is not None and not isinstance(scope, JobScope):
            raise ValueError("Use an explicit job scope")
        self._scope = scope
        self._credentials = credentials
        self._adapter_factory = adapter_factory or SDKAdapter
        self._permission = threading.Event()
        self._condition = threading.Condition()
        self._active = True
        self._readers = 0
        self._adapter = None
        self._lists = {}
        self._list_reads = {}
        self._records = {}
        self._model_reads = {}
        self._summaries = {}
        self._summary_reads = {}
        self.update_online(online)

    @property
    def scope(self):
        return self._scope

    def update_online(self, enabled):
        """Mirror Blender permission on its main thread; workers read only Event."""
        with self._condition:
            if enabled and self._active:
                self._permission.set()
            else:
                self._permission.clear()

    @property
    def closed(self):
        with self._condition:
            return not self._active and not self._readers

    def close(self, *, wait=False):
        with self._condition:
            self._active = False
            self._permission.clear()
            self._lists.clear()
            self._records.clear()
            self._summaries.clear()
            self._close_idle_adapter()
            if wait:
                self._condition.wait_for(lambda: not self._readers)

    def _close_idle_adapter(self):
        """Called under the condition; no network request can own this pool now."""
        if not self._active and not self._readers and self._adapter is not None:
            adapter, self._adapter = self._adapter, None
            adapter.close()

    def _check_active(self):
        if not self._active:
            raise ScenarioError(0, "The selected catalog connection changed")

    def network_allowed(self):
        """Worker-safe permission shared by SDK requests and signed transfers."""
        return self._permission.is_set()

    def create_job_adapter(self):
        """Give the job owner its own pool with this scope and permission snapshot.

        The caller must close it after its workers finish. Catalog retirement
        disables its network permission too, but cannot close an in-flight job.
        """
        with self._condition:
            self._check_active()
            if self._scope is None:
                raise ScenarioError(0, "Local job storage is not configured")
            return self._adapter_factory(
                self._credentials,
                online=self._permission.is_set,
                base_url=self._scope.service,
                account_id=self._scope.account_id,
                project_id=self._scope.project_id,
                team_id=self._scope.team_id,
            )

    @contextmanager
    def _read(self):
        try:
            with self._condition:
                self._check_active()
                if self._adapter is None:
                    context = (
                        dict(
                            base_url=self._scope.service,
                            account_id=self._scope.account_id,
                            project_id=self._scope.project_id,
                            team_id=self._scope.team_id,
                        )
                        if self._scope is not None
                        else {}
                    )
                    self._adapter = self._adapter_factory(
                        self._credentials, online=self._permission.is_set, **context
                    )
                adapter = self._adapter
                self._readers += 1
            try:
                yield adapter
            finally:
                with self._condition:
                    self._readers -= 1
                    try:
                        self._close_idle_adapter()
                    finally:
                        self._condition.notify_all()
        except AdapterUnavailable as error:
            raise ScenarioError(error.status, str(error)) from None
        except AdapterError as error:
            raise ScenarioError(0, str(error)) from None
        except ValueError:
            raise ScenarioError(0, "The catalog request is invalid") from None

    def fetch_list(self, privacy="public"):
        """Refresh once for overlapping readers of this connection/privacy scope."""
        with self._condition:
            self._check_active()
            if privacy not in ("public", "private"):
                raise ScenarioError(0, "The catalog request is invalid")
            pending = self._list_reads.get(privacy)
            owner = pending is None
            if owner:
                pending = self._list_reads[privacy] = Future()
        if not owner:
            rows = pending.result()
            with self._condition:
                self._check_active()
            return [ModelRecord.from_api(copy.deepcopy(row)) for row in rows]
        try:
            rows, records = self._fetch_list(privacy)
            pending.set_result(copy.deepcopy(rows))
            return records
        except BaseException as error:
            pending.set_exception(error)
            raise
        finally:
            with self._condition:
                del self._list_reads[privacy]

    def _fetch_list(self, privacy):
        with self._read() as adapter:
            rows = adapter.models(privacy=privacy)
            try:
                records = [ModelRecord.from_api(row) for row in rows]
            except (AttributeError, TypeError, ValueError):
                raise ScenarioError(0, "Scenario returned an invalid model catalog") from None
            with self._condition:
                self._check_active()
                self._lists[privacy] = copy.deepcopy(rows)
        return rows, records

    def history_page(self, token=None):
        """Read one cloud page, bounded prompt previews and workflow steps.

        Finished workflow runs name their step jobs; steps missing from this
        page are read with `jobs.retrieve` and returned as `related_jobs`,
        which price runs but are not history rows. A second pass reads the
        missing steps of nested runs the first pass found. Both passes share
        WORKFLOW_STEP_READS and never retry an identifier.
        """
        with self._read() as adapter:
            page = adapter.job_page(pagination_token=token)
            rows = page["jobs"]
            related, attempted = [], []
            for _ in range(2):
                for identifier in history.missing_workflow_steps(
                    [*rows, *related],
                    limit=WORKFLOW_STEP_READS - len(attempted),
                    attempted=attempted,
                ):
                    attempted.append(identifier)
                    try:
                        related.append(adapter.job(identifier))
                    except (AdapterError, ValueError):
                        continue  # An unread step leaves its run's cost unavailable.
            page["related_jobs"] = related
            texts = {}
            for identifier in history.prompt_asset_ids(rows)[:30]:
                try:
                    asset = adapter.asset(identifier)
                except (AdapterError, ValueError):
                    continue  # A missing prompt must not hide the cloud job.
                metadata = asset.get("metadata") or {}
                preview = metadata.get("preview") if isinstance(metadata, dict) else None
                if isinstance(preview, str) and metadata.get("hasFullPreview") is not False:
                    texts[identifier] = preview
            history.resolve_prompts(rows, texts)
            with self._condition:
                self._check_active()
            return page

    def check_connection(self):
        """Verify model access with one SDK read, without inferring identity."""
        with self._read() as adapter:
            adapter.model_page(page_size=1)
            with self._condition:
                self._check_active()

    def load_list_cached(self, privacy="public"):
        with self._condition:
            self._check_active()
            rows = copy.deepcopy(self._lists.get(privacy))
        return None if rows is None else [ModelRecord.from_api(row) for row in rows]

    def load_cached(self, model_id):
        with self._condition:
            self._check_active()
            row = copy.deepcopy(self._records.get(model_id))
        return None if row is None else ModelRecord.from_api(row)

    def get_many(self, model_ids, refresh=False):
        """Bulk model summaries by ID, read at most once per connection.

        Returns {id: ModelRecord} in request order for the models Scenario
        returned. An omitted ID is remembered as absent for this connection, so
        repeated loads do not repeat its read. `refresh` skips cached summaries
        but joins a read of the same ID already in flight. Overlapping callers
        share pending reads. These summaries come from `models.get_bulk`, which
        the API reference does not document as carrying `inputs`; they never
        replace the details `get` caches for forms and quotes.
        """
        # Apply the adapter's identifier rules before owning a shared read: an ID it rejects
        # must fail only this request, never a caller waiting on a valid ID from it.
        try:
            requested = model_identifiers(model_ids)
        except ValueError:
            raise ScenarioError(0, "The catalog request is invalid") from None
        if len(requested) > MODEL_BULK_LIMIT:
            raise ScenarioError(0, "The catalog request is invalid")
        rows, waiting, owned = {}, {}, {}
        with self._condition:
            self._check_active()
            for model_id in requested:
                if not refresh and model_id in self._summaries:
                    rows[model_id] = self._summaries[model_id]
                elif model_id in self._summary_reads:
                    waiting[model_id] = self._summary_reads[model_id]
                else:
                    owned[model_id] = self._summary_reads[model_id] = Future()
        if owned:
            try:
                found = self._fetch_summaries(list(owned))
            except BaseException as error:
                for pending in owned.values():
                    pending.set_exception(error)
                raise
            else:
                for model_id, pending in owned.items():
                    rows[model_id] = found.get(model_id)
                    pending.set_result(copy.deepcopy(rows[model_id]))
            finally:
                with self._condition:
                    for model_id in owned:
                        del self._summary_reads[model_id]
        for model_id, pending in waiting.items():
            rows[model_id] = pending.result()
        with self._condition:
            self._check_active()
            return {
                model_id: ModelRecord.from_api(copy.deepcopy(rows[model_id]))
                for model_id in requested
                if rows[model_id] is not None
            }

    def _fetch_summaries(self, model_ids):
        with self._read() as adapter:
            found = adapter.models_bulk(model_ids)
            try:
                for row in found.values():
                    ModelRecord.from_api(row)
            except (AttributeError, TypeError, ValueError):
                raise ScenarioError(0, "Scenario returned an invalid model summary") from None
            with self._condition:
                self._check_active()
                for model_id in model_ids:
                    self._summaries[model_id] = copy.deepcopy(found.get(model_id))
        return found

    def trained_models(self, refresh=False):
        """(kind, ModelRecord) pairs: this scope's private trained list, then public LoRAs.

        Reuses each privacy list cached on this connection and reads only a
        missing list, or both with `refresh`. Classification uses only REST
        fields (catalog.trained_kind); a plain record from the private list is
        custom_private. Base lanes and the picker still leave out LoRAs,
        compositions and unsupported records, and no route runs them yet.
        """
        lists = {}
        for privacy in ("private", "public"):
            lists[privacy] = None if refresh else self.load_list_cached(privacy)
            if lists[privacy] is None:
                lists[privacy] = self.fetch_list(privacy)
        return trained_pairs(lists["private"], lists["public"])

    def estimate(self, model_id, parameters):
        """Price validated inputs on this connection; never submit or upload files."""
        parameters = copy.deepcopy(parameters)
        model = self.get(model_id).raw
        with self._read() as adapter:
            quote = adapter.estimate_model(model, parameters)
            with self._condition:
                self._check_active()
            return quote

    def get(self, model_id, refresh=False):
        with self._condition:
            self._check_active()
            if not refresh and model_id in self._records:
                return ModelRecord.from_api(copy.deepcopy(self._records[model_id]))
            pending = self._model_reads.get(model_id)
            owner = pending is None
            if owner:
                pending = self._model_reads[model_id] = Future()
        if not owner:
            row = pending.result()
            with self._condition:
                self._check_active()
            return ModelRecord.from_api(copy.deepcopy(row))
        try:
            row = self._fetch_model(model_id)
            record = ModelRecord.from_api(row)
            pending.set_result(copy.deepcopy(row))
            return record
        except BaseException as error:
            pending.set_exception(error)
            raise
        finally:
            with self._condition:
                del self._model_reads[model_id]

    def _fetch_model(self, model_id):
        with self._read() as adapter:
            row = adapter.model(model_id)
            if row.get("id") != model_id:
                raise ScenarioError(0, "Scenario returned a different model identity")
            try:
                # Forms parse this schema on the main thread; reject it before caching.
                parse_schema(ModelRecord.from_api(row))
            except (AttributeError, KeyError, TypeError, ValueError):
                raise ScenarioError(0, "Scenario returned an invalid model description") from None
            with self._condition:
                self._check_active()
                self._records[model_id] = copy.deepcopy(row)
        return row
