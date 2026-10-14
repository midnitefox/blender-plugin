# SPDX-FileCopyrightText: 2026 Open Studio contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Provider configuration, credential boundaries and durable submit-once ownership."""

import json
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace

import httpx
import pytest

from scenario.core.api.sdk_adapter import AdapterError
from scenario.core.providers.base import ProviderAdapter
from scenario.core.providers.config import ProviderProfile, canonical_endpoint
from scenario.core.providers.http import ProviderHTTP


def profile(**kwargs):
    values = dict(
        profile_id="local", name="Local AI", provider="comfyui", base_url="http://127.0.0.1:8188"
    )
    return ProviderProfile(**(values | kwargs))


@pytest.mark.parametrize(
    "endpoint",
    [
        "http://example.com",
        "https://a.test/?token=x",
        "https://key@a.test",
        "https://a.test/#x",
        "file:///tmp",
        "http://127.0.0.1.evil.test",
        "https://a.test/../v1",
        "https://a.test\\evil",
        "https://a.test/%2e%2e/x",
    ],
)
def test_unsafe_endpoint_is_rejected(endpoint):
    with pytest.raises(ValueError):
        canonical_endpoint(endpoint)


def test_profiles_are_immutable_canonical_and_secret_redacted():
    selected = profile(
        base_url="HTTP://LOCALHOST:8188/",
        api_key="private-test-key",
        options_json='{"capabilities":["image"]}',
    )
    assert selected.base_url == "http://localhost:8188"
    assert selected.valid
    assert "private-test-key" not in repr(selected)
    assert selected.identity_hash != replace(selected, api_key="another-key").identity_hash
    assert selected.identity_hash != replace(selected, profile_id="another").identity_hash
    assert selected.identity_hash == replace(selected, name="Renamed").identity_hash
    options = selected.options
    options["capabilities"].append("video")
    assert selected.options == {"capabilities": ["image"]}


class RecordingAdapter(ProviderAdapter):
    def _submit(self, operation, target_id, payload):
        self.calls.append(payload)
        return {"jobId": "remote"}


def test_unknown_price_is_explicit_and_quote_cannot_be_replayed(tmp_path):
    adapter = RecordingAdapter(profile(), online=lambda: True, state_dir=tmp_path)
    adapter.calls = []
    estimate = adapter._estimate("model", "model", {"prompt": "test"})
    assert estimate.cost is None
    assert estimate.details["provider_billed"] is True
    assert estimate.details["cost_label"]
    claims = []
    assert adapter.submit_estimate(estimate, before_send=lambda: claims.append(True)) == {
        "jobId": "remote"
    }
    with pytest.raises(ValueError):
        adapter.submit_estimate(estimate, before_send=lambda: claims.append(True))
    assert claims == [True]
    assert adapter.calls == [{"prompt": "test"}]


def test_failed_submission_consumes_quote_but_failed_claim_does_not(tmp_path):
    adapter = RecordingAdapter(profile(), online=lambda: True, state_dir=tmp_path)
    quote = adapter._estimate("model", "model", {})

    def fail():
        raise RuntimeError("claim failed")

    with pytest.raises(RuntimeError):
        adapter.submit_estimate(quote, before_send=fail)
    assert adapter.owns_estimate(quote)

    def submit(*args):
        raise AdapterError("uncertain")

    adapter._submit = submit
    with pytest.raises(AdapterError):
        adapter.submit_estimate(quote, before_send=lambda: None)
    assert not adapter.owns_estimate(quote)


def test_results_survive_restart_and_profiles_cannot_read_each_other(tmp_path):
    adapter = RecordingAdapter(profile(), online=lambda: True, state_dir=tmp_path)
    asset = adapter.save_result("asset-1", b"result", "image/png")
    assert asset["url"] == "provider-result:asset-1"
    fresh = RecordingAdapter(profile(), online=lambda: False, state_dir=tmp_path)
    assert fresh.asset("asset-1")["mimeType"] == "image/png"
    assert fresh.read_result(asset["url"], max_bytes=7) == b"result"
    with pytest.raises(AdapterError):
        fresh.read_result(asset["url"], max_bytes=2)
    other = RecordingAdapter(profile(profile_id="other"), online=lambda: True, state_dir=tmp_path)
    with pytest.raises(AdapterError):
        other.read_result(asset["url"])
    with pytest.raises(ValueError):
        fresh.load_record("assets", "../elsewhere")


def test_http_refuses_redirects_and_never_leaks_secrets_or_retries():
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(302, headers={"Location": "https://evil.invalid/?private=1"})

    client = ProviderHTTP(
        profile(api_key="selected-test-key"),
        online=lambda: True,
        transport=httpx.MockTransport(handler),
    )
    with pytest.raises(AdapterError) as error:
        client.request_json("POST", "/prompt", {"prompt": "private"})
    assert len(requests) == 1
    assert requests[0].headers["authorization"] == "Bearer selected-test-key"
    assert "private" not in str(error.value)
    assert "evil" not in str(error.value)
    with pytest.raises(ValueError):
        client.request_json("GET", "https://evil.invalid")
    client.close()


def test_http_online_permission_response_bound_and_no_ambient_auth():
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(200, content=json.dumps({"ok": True}).encode())

    enabled = False
    client = ProviderHTTP(profile(), online=lambda: enabled, transport=httpx.MockTransport(handler))
    with pytest.raises(AdapterError):
        client.request_json("GET", "/object_info")
    assert not requests
    enabled = True
    assert client.request_json("GET", "/object_info") == {"ok": True}
    assert "authorization" not in requests[0].headers
    with pytest.raises(AdapterError):
        client.request_bytes("GET", "/object_info", max_bytes=1)
    client.close()


def test_timeout_is_never_retried_and_error_is_sanitized():
    requests = []

    def handler(request):
        requests.append(request)
        raise httpx.ReadTimeout("private-signed-url-or-key", request=request)

    client = ProviderHTTP(profile(), online=True, transport=httpx.MockTransport(handler))
    with pytest.raises(AdapterError) as error:
        client.request_json("POST", "/prompt", {"prompt": "private"})
    assert len(requests) == 1
    assert "private" not in str(error.value)
    client.close()


def test_two_threads_cannot_submit_one_quote_twice(tmp_path):
    adapter = RecordingAdapter(profile(), online=True, state_dir=tmp_path)
    adapter.calls = []
    quote = adapter._estimate("model", "model", {"prompt": "same"})

    def send():
        try:
            return adapter.submit_estimate(quote, before_send=lambda: None)
        except ValueError:
            return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        replies = list(pool.map(lambda _: send(), range(2)))
    assert sum(reply is not None for reply in replies) == 1
    assert len(adapter.calls) == 1
    adapter.close()


def test_provider_headers_are_explicit_and_endpoint_cannot_change(tmp_path):
    requests = []
    chosen = profile(provider="fal", base_url="https://queue.fal.run", api_key="example-key")
    client = ProviderHTTP(
        chosen,
        online=True,
        transport=httpx.MockTransport(
            lambda request: requests.append(request) or httpx.Response(200, json={})
        ),
    )
    client.request_json("GET", "/")
    assert requests[0].headers["Authorization"] == "Key example-key"
    with pytest.raises(ValueError):
        ProviderAdapter(chosen, online=True, base_url="https://other.invalid", state_dir=tmp_path)
    client.close()


def test_base_upload_preflight_rejects_unsupported_references_without_storage(tmp_path):
    adapter = ProviderAdapter(profile(), online=False, state_dir=tmp_path)
    with pytest.raises(ValueError, match="support"):
        adapter.validate_upload(b"image", media_type="image/png", kind="image")
    assert not list(adapter._state_dir.iterdir())
    adapter.close()
