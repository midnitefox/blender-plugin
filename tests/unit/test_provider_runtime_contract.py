# SPDX-FileCopyrightText: 2026 Open Studio contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Provider selection preserves storage isolation and honest unknown prices."""

from dataclasses import replace

import pytest

from scenario.core.api.sdk_adapter import Estimate
from scenario.core.jobs import credential_storage
from scenario.core.jobs.store import JobIntent, JobOrigin, JobScope


@pytest.mark.parametrize("endpoint", ["http://127.0.0.1:8188", "http://[::1]:8188"])
def test_explicit_local_service_scope(endpoint):
    assert JobScope(endpoint, "local-provider").service == endpoint


@pytest.mark.parametrize("endpoint", ["http://example.com", "http://192.168.1.2:8188"])
def test_remote_service_still_requires_tls(endpoint):
    with pytest.raises(ValueError):
        JobScope(endpoint, "local-provider")


def test_unknown_price_roundtrips_without_becoming_free(tmp_path):
    from scenario.core.jobs.store import JobStore

    scope = JobScope("https://example.invalid/v1", "local-provider")
    store = JobStore(tmp_path / "jobs.sqlite3", scope)
    intent = JobIntent(
        "request",
        scope,
        JobOrigin("file", "scene", "revision"),
        "model",
        "configured-image",
        "a" * 64,
        "b" * 64,
        None,
    )
    store.create(intent)
    assert JobStore(tmp_path / "jobs.sqlite3", scope).get("request").intent.quote_cost is None
    estimate = Estimate("model", "configured-image", None, None, b"{}", b"{}", object(), 0)
    assert estimate.approval_amount == "provider-billed"


def test_provider_scope_reopens_and_isolates_key_profile_endpoint(tmp_path):
    from scenario.core.providers.config import ProviderProfile

    profile = ProviderProfile("profile-a", "Local", "comfyui", "http://127.0.0.1:8188")
    opened = credential_storage.open_provider_store(tmp_path, profile)
    assert credential_storage.open_provider_store(tmp_path, profile).scope == opened.scope
    for other in (
        replace(profile, profile_id="profile-b"),
        replace(profile, api_key="synthetic-secret-key"),
        replace(profile, base_url="http://127.0.0.1:8189"),
    ):
        assert credential_storage.open_provider_store(tmp_path, other).scope != opened.scope
    raw = b"".join(path.read_bytes() for path in tmp_path.iterdir() if path.is_file())
    assert b"synthetic-secret-key" not in raw


def test_provider_submission_persists_claim_and_unknown_cost_once(tmp_path):
    from scenario.core.jobs.coordinator import JobCoordinator
    from scenario.core.jobs.store import JobState
    from scenario.core.providers.base import ProviderAdapter
    from scenario.core.providers.config import ProviderProfile

    profile = ProviderProfile("test", "Test", "comfyui", "http://127.0.0.1:8188")
    store = credential_storage.open_provider_store(tmp_path / "jobs", profile)
    calls = []

    class Adapter(ProviderAdapter):
        def models(self, **kwargs):
            return [
                {
                    "id": "test-image",
                    "type": "custom",
                    "inputs": [
                        {"name": "prompt", "type": "string", "required": True},
                    ],
                }
            ]

        def _submit(self, operation, target_id, payload):
            saved = store.records()
            assert len(saved) == 1 and saved[0].state == JobState.SUBMITTING
            assert saved[0].intent.quote_cost is None
            calls.append(payload)
            return {"jobId": "test-remote"}

    adapter = Adapter(profile, online=True, account_id=store.scope.account_id, state_dir=tmp_path)
    coordinator = JobCoordinator(adapter, store)
    model = adapter.models()[0]
    quote = adapter.estimate_model(model, {"prompt": "concept"})
    origin = JobOrigin("file", "scene", "revision")
    prepared = coordinator.prepare(quote, origin)
    options = dict(origin=origin, operation="model", target_id=model["id"], payload=quote.payload)
    result = coordinator.submit(prepared, **options)
    assert result.remote_job_id == "test-remote"
    with pytest.raises(ValueError):
        coordinator.submit(prepared, **options)
    assert len(calls) == 1
    adapter.close()


def test_provider_reference_uses_verified_snapshot_and_durable_claim(tmp_path):
    from contextlib import nullcontext

    from scenario.core.jobs.upload_sources import UploadSources
    from scenario.core.jobs.upload_store import UploadState, UploadStore
    from scenario.core.jobs.upload_transfers import PartUploader, S3UploadPolicy
    from scenario.core.jobs.uploads import UploadCommands
    from scenario.core.providers.base import ProviderAdapter
    from scenario.core.providers.config import ProviderProfile

    profile = ProviderProfile("test", "Test", "comfyui", "http://127.0.0.1:8188")
    scope = JobScope(profile.base_url, "local-provider")
    root = tmp_path / "sources"
    root.mkdir()
    store = UploadStore(tmp_path / "uploads.sqlite3", scope)
    calls = []

    class Adapter(ProviderAdapter):
        upload_kinds = frozenset({"image"})

        def validate_upload(self, data, *, media_type, kind):
            assert store.records()[0].state == UploadState.PREPARED
            if data != b"generated image fixture":
                raise ValueError("The selected image is invalid")

        def stage_image(self, data, *, media_type):
            assert store.records()[0].state == UploadState.INITIALIZING
            calls.append((data, media_type))
            return {"id": "provider-image", "kind": "image"}

    adapter = Adapter(profile, online=True, account_id=scope.account_id, state_dir=tmp_path)
    commands = UploadCommands(
        adapter,
        store,
        UploadSources(root),
        PartUploader(S3UploadPolicy(), online_access=lambda: True),
        lambda *args: nullcontext(True),
    )
    source = tmp_path / "image.png"
    source.write_bytes(b"generated image fixture")
    prepared = commands.prepare(
        source,
        origin=JobOrigin("file", "scene", "revision"),
        kind="image",
        content_type="image/png",
    )
    source.write_bytes(b"changed original")
    result = commands.initialize(prepared.intent.request_id, expected_revision=prepared.revision)
    assert result.state == UploadState.IMPORTED and result.asset_id == "provider-image"
    assert calls == [(b"generated image fixture", "image/png")]
    adapter.close()


@pytest.mark.parametrize("oversized", [False, True])
def test_provider_invalid_reference_never_claims_or_sends(tmp_path, oversized):
    from contextlib import nullcontext

    from scenario.core.jobs.upload_sources import UploadSources
    from scenario.core.jobs.upload_store import UploadState, UploadStore
    from scenario.core.jobs.upload_transfers import PartUploader, S3UploadPolicy
    from scenario.core.jobs.uploads import UploadCommands, UploadError
    from scenario.core.providers.base import ProviderAdapter
    from scenario.core.providers.config import ProviderProfile

    class Adapter(ProviderAdapter):
        upload_kinds = frozenset({"image"})
        max_reference_bytes = 8 if oversized else 100

        def validate_upload(self, data, *, media_type, kind):
            raise ValueError("The selected image is invalid")

        def stage_image(self, data, *, media_type):
            pytest.fail("Invalid local reference must not be sent")

    profile = ProviderProfile("test", "Test", "comfyui", "http://127.0.0.1:8188")
    store = UploadStore(tmp_path / "uploads.sqlite3", JobScope(profile.base_url, "test"))
    source_root = tmp_path / "sources"
    source_root.mkdir()
    adapter = Adapter(profile, online=True, state_dir=tmp_path)
    commands = UploadCommands(
        adapter,
        store,
        UploadSources(source_root),
        PartUploader(S3UploadPolicy(), online_access=lambda: True),
        lambda *args: nullcontext(True),
    )
    source = tmp_path / "image.png"
    source.write_bytes(b"not an image")
    prepared = commands.prepare(
        source,
        origin=JobOrigin("file", "scene", "revision"),
        kind="image",
        content_type="image/png",
    )
    with pytest.raises(UploadError):
        commands.initialize(prepared.intent.request_id, expected_revision=prepared.revision)
    assert store.get(prepared.intent.request_id) == prepared
    assert prepared.state == UploadState.PREPARED
    adapter.close()


def test_provider_known_upload_recovers_by_polling_without_resending(tmp_path):
    from contextlib import nullcontext

    from scenario.core.jobs.upload_sources import UploadSources
    from scenario.core.jobs.upload_store import UploadState, UploadStore
    from scenario.core.jobs.upload_transfers import PartUploader, S3UploadPolicy
    from scenario.core.jobs.uploads import UploadCommands, UploadRecoveryAction
    from scenario.core.providers.base import ProviderAdapter
    from scenario.core.providers.config import ProviderProfile

    class Adapter(ProviderAdapter):
        def asset(self, asset_id):
            assert asset_id == "known-provider-image"
            return {"id": asset_id, "kind": "image"}

        def stage_image(self, data, *, media_type):
            pytest.fail("Known uploads must never be resent during recovery")

    profile = ProviderProfile("test", "Test", "comfyui", "http://127.0.0.1:8188")
    store = UploadStore(tmp_path / "uploads.sqlite3", JobScope(profile.base_url, "test"))
    source_root = tmp_path / "sources"
    source_root.mkdir()
    adapter = Adapter(profile, online=True, state_dir=tmp_path)
    commands = UploadCommands(
        adapter,
        store,
        UploadSources(source_root),
        PartUploader(S3UploadPolicy(), online_access=lambda: True),
        lambda *args: nullcontext(True),
    )
    source = tmp_path / "image.png"
    source.write_bytes(b"original snapshot")
    prepared = commands.prepare(
        source,
        origin=JobOrigin("file", "scene", "revision"),
        kind="image",
        content_type="image/png",
    )
    claimed = store.transition(
        prepared.intent.request_id,
        expected_revision=prepared.revision,
        state=UploadState.INITIALIZING,
    )
    known = store.transition(
        prepared.intent.request_id,
        expected_revision=claimed.revision,
        state=UploadState.UPLOADING,
        upload_id="known-provider-image",
    )
    assert commands.recovery_plan()[0].action == UploadRecoveryAction.POLL_REMOTE
    recovered = commands.refresh(prepared.intent.request_id, expected_revision=known.revision)
    assert recovered.state == UploadState.IMPORTED
    assert recovered.asset_id == "known-provider-image"
    adapter.close()


def test_provider_connection_check_contacts_adapter_instead_of_configuration(tmp_path):
    from scenario.core.api.sdk_catalog import SDKCatalog
    from scenario.core.providers.base import ProviderAdapter
    from scenario.core.providers.config import ProviderProfile

    profile = ProviderProfile("test", "Test", "comfyui", "http://127.0.0.1:8188")
    seen = []

    class Adapter(ProviderAdapter):
        def models(self, **kwargs):
            return []

        def test_connection(self):
            seen.append("contacted")

    catalog = SDKCatalog(profile, online=True, adapter_factory=Adapter)
    catalog.check_connection()
    assert seen == ["contacted"]
    catalog.close()


def test_compatible_image_runs_through_shared_saved_result_pipeline(tmp_path):
    import base64

    import httpx

    from scenario.core.jobs.coordinator import JobCoordinator
    from scenario.core.jobs.store import JobState
    from scenario.core.providers.config import ProviderProfile
    from scenario.core.providers.openai_compatible import OpenAICompatibleAdapter
    from scenario.core.providers.transfers import ProviderResultDownloader

    pixels = b"\x89PNG\r\n\x1a\nfixture pixels"
    profile = ProviderProfile(
        "images",
        "Image service",
        "openai_compatible",
        "https://provider.example.invalid/v1",
        image_model="configured-image",
        options_json='{"capabilities":["image"]}',
    )
    store = credential_storage.open_provider_store(tmp_path / "jobs", profile)
    calls = []

    def handle(request):
        calls.append(request)
        assert request.url.path == "/v1/images/generations"
        assert store.records()[0].state == JobState.SUBMITTING
        return httpx.Response(200, json={"data": [{"b64_json": base64.b64encode(pixels).decode()}]})

    adapter = OpenAICompatibleAdapter(
        profile,
        online=True,
        account_id=store.scope.account_id,
        state_dir=tmp_path,
        transport=httpx.MockTransport(handle),
    )
    root = tmp_path / "results"
    root.mkdir()
    coordinator = JobCoordinator(
        adapter,
        store,
        result_root=root,
        result_downloader=ProviderResultDownloader(adapter, online_access=lambda: True),
    )
    origin = JobOrigin("file", "scene", "revision")
    quote = adapter.estimate_model(adapter.model("openai-image"), {"prompt": "concept"})
    prepared = coordinator.prepare(quote, origin)
    current = coordinator.submit(
        prepared, origin=origin, operation="model", target_id="openai-image", payload=quote.payload
    )
    request_id = current.intent.request_id
    coordinator.refresh_remote(request_id, expected_revision=current.revision)
    current = store.get(request_id)
    assert current.state == JobState.SUCCEEDED
    coordinator.load_results(request_id, expected_revision=current.revision)
    current = store.get(request_id)
    coordinator.download_results(request_id, expected_revision=current.revision)
    current = store.get(request_id)
    assert current.state == JobState.READY
    verified = coordinator.verify_results(request_id, expected_revision=current.revision)
    assert verified.paths[0].read_bytes() == pixels
    assert len(calls) == 1
    assets = adapter.bulk_assets([current.results[0].asset.asset_id])
    assert current.results[0].asset.asset_id in assets
    coordinator.close()


def test_provider_catalog_bulk_summaries_keep_explicit_provenance(tmp_path):
    from functools import partial

    from scenario.core.api.sdk_catalog import SDKCatalog
    from scenario.core.providers.config import ProviderProfile
    from scenario.core.providers.openai_compatible import OpenAICompatibleAdapter

    profile = ProviderProfile(
        "images",
        "Images",
        "openai_compatible",
        "https://provider.example.invalid/v1",
        image_model="configured-image",
        options_json='{"capabilities":["image"]}',
    )
    catalog = SDKCatalog(
        profile, online=True, adapter_factory=partial(OpenAICompatibleAdapter, state_dir=tmp_path)
    )
    records = catalog.get_many(["openai-image"])
    assert records["openai-image"].provider == "openai_compatible"
    assert "image" in records["openai-image"].lanes
    catalog.close()


def test_changing_model_keeps_saved_jobs_and_result_bytes(tmp_path):
    from scenario.core.providers.base import ProviderAdapter
    from scenario.core.providers.config import ProviderProfile

    profile = ProviderProfile(
        "profile",
        "Images",
        "openai_compatible",
        "https://example.invalid/v1",
        image_model="model-a",
    )
    store = credential_storage.open_provider_store(tmp_path / "jobs", profile)
    origin = JobOrigin("file", "scene", "revision")
    intent = JobIntent("previous", store.scope, origin, "model", "image", "a" * 64, "b" * 64, None)
    store.create(intent)
    adapter = ProviderAdapter(
        profile, online=False, account_id=store.scope.account_id, state_dir=tmp_path
    )
    saved = adapter.save_result("asset", b"previous generated bytes", "image/png")
    adapter.close()
    changed = replace(profile, image_model="model-b", workflows_path="different-recipes")
    reopened = credential_storage.open_provider_store(tmp_path / "jobs", changed)
    assert reopened.scope == store.scope
    assert reopened.get("previous").intent == intent
    adapter = ProviderAdapter(
        changed, online=False, account_id=reopened.scope.account_id, state_dir=tmp_path
    )
    assert adapter.read_result(saved["url"]) == b"previous generated bytes"
    assert changed.identity_hash != profile.identity_hash
    adapter.close()
