# SPDX-FileCopyrightText: 2026 Open Studio contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Shared catalog/job boundary and durable provider-local result storage."""

import copy
import hashlib
import json
import os
import re
import tempfile
import threading
import time
from pathlib import Path
from weakref import WeakValueDictionary

from ..api.sdk_adapter import AdapterError, Estimate
from ..schema.forms import prepare_run, record_schema
from .config import ProviderProfile, canonical_endpoint
from .http import ProviderHTTP

MAX_RECORD_BYTES = 134217728
MAX_RESULT_BYTES = 268435456


def identifier(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", value):
        raise ValueError("Use a valid provider record identifier")
    return value


class ProviderAdapter:
    """Adapters normalize service contracts without inventing provider capabilities."""

    upload_kinds = frozenset()
    max_reference_bytes = 64 * 1024 * 1024

    @classmethod
    def validate_upload(cls, data, *, media_type, kind):
        """Validate reviewed bytes without networking or changing durable state."""
        if not isinstance(kind, str) or kind not in cls.upload_kinds:
            raise ValueError("This provider does not support the selected reference kind")
        if not isinstance(data, bytes) or not 0 < len(data) <= cls.max_reference_bytes:
            raise ValueError("Reference data is empty or exceeds this provider's size limit")
        if not isinstance(media_type, str) or not re.fullmatch(
            r"[a-z0-9.+-]+/[a-z0-9.+-]+", media_type
        ):
            raise ValueError("Choose a supported reference media type")

    def __init__(
        self,
        profile,
        *,
        online,
        base_url=None,
        account_id=None,
        project_id=None,
        team_id=None,
        state_dir=None,
        transport=None,
    ):
        if not isinstance(profile, ProviderProfile):
            raise ValueError("Select an explicit provider profile")
        if base_url is not None and canonical_endpoint(base_url) != profile.base_url:
            raise ValueError("The endpoint does not match the selected provider profile")
        self.profile = profile
        self._online = online if callable(online) else lambda: bool(online)
        self.base_url = profile.base_url
        self.account_id = account_id or profile.storage_identity_hash
        self.project_id, self.team_id = project_id, team_id
        self._closed = False
        self._scope = object()
        self._estimate_lock = threading.RLock()
        self._estimates = WeakValueDictionary()
        self._http = ProviderHTTP(profile, online=self.network_allowed, transport=transport)
        self._state_dir = None
        if state_dir is not None:
            root = Path(state_dir).absolute()
            root.mkdir(parents=True, exist_ok=True)
            if root.resolve() != root:
                raise ValueError("Use a private provider storage directory without symbolic links")
            scope = hashlib.sha256(
                json.dumps(
                    [profile.storage_identity_hash, self.account_id, project_id, team_id]
                ).encode()
            ).hexdigest()
            self._state_dir = root / "providers" / scope
            self._state_dir.mkdir(parents=True, exist_ok=True)
            if self._state_dir.resolve() != self._state_dir:
                raise ValueError("Provider storage cannot follow symbolic links")

    def network_allowed(self):
        return not self._closed and self._online()

    def close(self):
        with self._estimate_lock:
            self._closed = True
            self._estimates.clear()
        self._http.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    def _path(self, kind, value, suffix=".json"):
        identifier(kind)
        identifier(value)
        if self._state_dir is None:
            raise AdapterError("Configure private job storage before using this provider")
        path = self._state_dir / f"{kind}-{value}{suffix}"
        if path.resolve() != path:
            raise AdapterError("Provider storage cannot follow symbolic links")
        return path

    def _write_bytes(self, path, data):
        fd, temporary = tempfile.mkstemp(prefix=".pending-", dir=path.parent)
        try:
            with os.fdopen(fd, "wb") as output:
                output.write(data)
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    def save_record(self, kind, value, record):
        data = json.dumps(record, ensure_ascii=False, allow_nan=False).encode()
        if len(data) > MAX_RECORD_BYTES:
            raise AdapterError("Provider metadata exceeds the saved-record limit")
        self._write_bytes(self._path(kind, value), data)

    def load_record(self, kind, value):
        path = self._path(kind, value)
        try:
            with path.open("rb") as source:
                data = source.read(MAX_RECORD_BYTES + 1)
            if len(data) > MAX_RECORD_BYTES:
                raise ValueError
            return json.loads(data)
        except (OSError, ValueError, RecursionError):
            raise AdapterError(
                "No valid saved record exists for this provider connection"
            ) from None

    def save_result(self, value, data, media_type):
        if not isinstance(data, bytes) or len(data) > MAX_RESULT_BYTES:
            raise AdapterError("Provider result exceeds the byte limit")
        path = self._path("result", value, ".bin")
        self._write_bytes(path, data)
        kind = media_type.split("/", 1)[0]
        kind = "3d" if kind == "model" else kind
        asset = {
            "id": value,
            "type": kind,
            "kind": kind,
            "status": "success",
            "url": f"provider-result:{value}",
            "mimeType": media_type,
            "size": len(data),
            "sha256": hashlib.sha256(data).hexdigest(),
            "metadata": {"size": len(data)},
            "properties": {"size": len(data)},
        }
        self.save_record("assets", value, asset)
        return asset

    def read_result(self, reference, *, max_bytes=MAX_RESULT_BYTES, cancel=None):
        if not isinstance(reference, str) or not reference.startswith("provider-result:"):
            raise AdapterError("Use a result owned by this provider connection")
        value = identifier(reference.removeprefix("provider-result:"))
        if type(max_bytes) is not int or not 1 <= max_bytes <= MAX_RESULT_BYTES:
            raise ValueError("Use a bounded positive result size")
        if cancel is not None and cancel.is_set():
            raise AdapterError("Provider transfer canceled")
        record = self.asset(value)
        try:
            with self._path("result", value, ".bin").open("rb") as source:
                data = source.read(max_bytes + 1)
        except OSError:
            raise AdapterError("The saved provider result is unavailable") from None
        if (
            len(data) > max_bytes
            or len(data) != record.get("size")
            or hashlib.sha256(data).hexdigest() != record.get("sha256")
        ):
            raise AdapterError("The saved provider result changed or exceeds the byte limit")
        return data

    def asset(self, value, *, original_assets=False):
        return self.load_record("assets", value)

    def bulk_assets(self, values):
        return {value: self.asset(value) for value in values}

    def asset_records(self, values):
        return self.bulk_assets(values)

    def job(self, value):
        return self.load_record("jobs", value)

    def model(self, value):
        for model in self.models():
            if model.get("id") == value:
                return copy.deepcopy(model)
        raise AdapterError("This model is unavailable in the selected provider connection")

    def models_bulk(self, values, *, chunk=50):
        return {value: self.model(value) for value in values}

    def model_page(self, *, privacy="public", page_size=100, pagination_token=None):
        if pagination_token:
            return {"models": []}
        return {"models": self.models(privacy=privacy)[:page_size]}

    def prepare_model(self, model, parameters):
        current = self.model(model.get("id"))
        if current != model:
            raise ValueError("Provider model configuration changed; refresh the model")
        return prepare_run(current["id"], record_schema("model", current), parameters)

    def estimate_model(self, model, parameters):
        return self._estimate("model", *self.prepare_model(model, parameters))

    def _estimate(self, operation, target_id, payload):
        details = {
            "provider_billed": True,
            "provider": self.profile.provider,
            "cost_label": "Provider billed; review your provider pricing",
        }
        if self.profile.provider == "comfyui":
            details["cost_label"] = "Local or hosted compute; no service quote"
        quote = Estimate(
            operation,
            target_id,
            self.project_id,
            None,
            json.dumps(payload, allow_nan=False).encode(),
            json.dumps(details).encode(),
            self._scope,
            time.monotonic(),
        )
        with self._estimate_lock:
            if self._closed:
                raise AdapterError("The selected provider connection was closed")
            self._estimates[id(quote)] = quote
        return quote

    def owns_estimate(self, estimate):
        with self._estimate_lock:
            return not self._closed and self._estimates.get(id(estimate)) is estimate

    def submit_estimate(self, estimate, *, before_send):
        with self._estimate_lock:
            if not self.owns_estimate(estimate):
                raise ValueError("Use an unchanged unused quote from this connection")
            if not callable(before_send):
                raise TypeError("A durable submission claim callback is required")
            if not self.network_allowed():
                raise AdapterError("Online access is disabled")
            if self._state_dir is None:
                raise AdapterError("Configure private job storage before submitting")
            before_send()
            del self._estimates[id(estimate)]
        return self._submit(estimate.operation, estimate.target_id, estimate.payload)

    def test_connection(self):
        models = self.models()
        return {
            "provider": self.profile.provider,
            "models": len(models),
            "capabilities": sorted(
                {cap for model in models for cap in model.get("capabilities", [])}
            ),
            "message": f"Connected; {len(models)} configured models available",
        }

    def _unsupported(self, *args, **kwargs):
        raise AdapterError("This operation is not supported by the selected provider")

    models = _unsupported
    workflows = _unsupported
    workflow = _unsupported
    prepare_workflow = _unsupported
    estimate_workflow = _unsupported
    estimate_prompt = _unsupported
    estimate_translate = _unsupported
    cancel_inference = _unsupported
    workflow_decision = _unsupported
    asset_page = _unsupported
    search_assets = _unsupported
    collection_page = _unsupported
    collection = _unsupported
    create_collection = _unsupported
    add_collection_assets = _unsupported
    remove_collection_assets = _unsupported
    update_asset_tags = _unsupported
    create_upload = _unsupported
    upload = _unsupported
    complete_upload = _unsupported
    job_page = _unsupported
    jobs = _unsupported
    teams = _unsupported
    projects = _unsupported
    _submit = _unsupported
