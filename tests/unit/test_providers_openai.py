# SPDX-FileCopyrightText: 2026 Open Studio contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Real loopback contracts for explicitly configured compatible endpoints."""

import base64
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from scenario.core.api.sdk_adapter import AdapterError
from scenario.core.providers.config import ProviderProfile
from scenario.core.providers.openai_compatible import OpenAICompatibleAdapter

PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRZkAAAAASUVORK5CYII="
)


@pytest.fixture
def endpoint():
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def handle_request(self):
            data = self.rfile.read(int(self.headers.get("Content-Length", "0")))
            self.server.requests.append((self.command, self.path, dict(self.headers), data))
            status, headers, body = self.server.response(self.command, self.path, data)
            if isinstance(body, dict):
                body = json.dumps(body).encode()
                headers = {"Content-Type": "application/json", **headers}
            self.send_response(status)
            for name, value in headers.items():
                self.send_header(name, value)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        do_GET = handle_request
        do_POST = handle_request

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.requests = []
    server.response = lambda *args: (200, {}, {"choices": [{"message": {"content": "Hello"}}]})
    server.url = f"http://127.0.0.1:{server.server_port}/v1"
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


@pytest.fixture
def adapter(endpoint, tmp_path):
    clients = []

    def create(*, capabilities=("text",), options=None, **kwargs):
        settings = dict(capabilities=list(capabilities))
        settings.update(options or {})
        profile = ProviderProfile(
            profile_id="fixture",
            name="Fixture",
            provider="openai_compatible",
            base_url=endpoint.url,
            api_key="fixture-token",
            text_model="custom/text-model",
            image_model="custom-image",
            speech_model="custom-speech",
            options_json=json.dumps(settings),
        )
        client = OpenAICompatibleAdapter(profile, online=lambda: True, state_dir=tmp_path, **kwargs)
        clients.append(client)
        return client

    yield create
    for client in clients:
        client.close()


def submit(client, model_id, parameters):
    quote = client.estimate_model(client.model(model_id), parameters)
    assert quote.cost is None
    assert quote.details["provider_billed"] is True
    claimed = []
    job = client.submit_estimate(quote, before_send=lambda: claimed.append(True))
    assert claimed == [True]
    with pytest.raises(ValueError):
        client.submit_estimate(quote, before_send=lambda: claimed.append(True))
    return client.job(job["jobId"])


def test_catalog_declares_only_explicit_capabilities(adapter, endpoint):
    client = adapter()
    assert [m["id"] for m in client.models()] == ["openai-text"]
    assert client.model("openai-text")["capabilities"] == ["txt2txt"]
    with pytest.raises((ValueError, AdapterError), match="configured|enabled|available"):
        client.model("openai-image")
    assert endpoint.requests == []


def test_image_catalog_uses_native_prompt_and_reference_controls(adapter):
    from scenario.core.api.catalog import ModelRecord
    from scenario.core.schema.params import parse_schema

    schema = parse_schema(
        ModelRecord.from_api(
            adapter(capabilities=("image_edit",)).model("openai-image-edit"),
            provider="openai_compatible",
        )
    )
    assert schema.prompt_name == "prompt"
    assert schema.by_name("numImages").ptype == "number"
    assert schema.by_name("numImages").is_integer
    assert schema.by_name("image").kind == "image"


def test_upload_preflight_rejects_invalid_bytes_without_writes(adapter, endpoint, monkeypatch):
    client = adapter(capabilities=("image_edit",))
    assert client.max_reference_bytes == 64 * 1024 * 1024
    client.validate_upload(PNG, media_type="image/png", kind="image")
    for data, mime, kind in [
        (b"bad", "image/png", "image"),
        (PNG, "image/jpeg", "image"),
        (PNG, "image/png", "3d"),
    ]:
        with pytest.raises(ValueError):
            client.validate_upload(data, media_type=mime, kind=kind)
    monkeypatch.setattr(OpenAICompatibleAdapter, "max_reference_bytes", 4)
    with pytest.raises(ValueError):
        client.validate_upload(PNG, media_type="image/png", kind="image")
    assert list(client._state_dir.iterdir()) == []
    assert endpoint.requests == []


def test_connection_test_reads_service_without_claiming_generation_verified(adapter, endpoint):
    endpoint.response = lambda *args: (200, {}, {"data": [{"id": "custom/text-model"}]})
    result = adapter().test_connection()
    assert endpoint.requests[0][1] == "/v1/models"
    assert result["models"] == 1
    assert result["generation_verified"] is False


def test_modified_model_record_cannot_be_quoted(adapter):
    client = adapter()
    model = client.model("openai-text")
    model["providerModel"] = "different-billing-model"
    with pytest.raises(ValueError, match="changed|refresh"):
        client.estimate_model(model, {"prompt": "Hello"})


def test_text_generation_persists_complete_assets_and_survives_restart(adapter, endpoint):
    text = "a long plan " * 3000
    endpoint.response = lambda *args: (200, {}, {"choices": [{"message": {"content": text}}]})
    client = adapter()
    job = submit(client, "openai-text", {"prompt": "Create a greybox"})
    assert job["status"] == "success"
    fresh = adapter()
    assert fresh.job(job["jobId"]) == job
    asset = fresh.asset(job["metadata"]["assetIds"][0], original_assets=True)
    assert asset["kind"] == "text"
    assert asset["status"] == "success"
    assert asset["properties"]["hasFullPreview"] is True
    assert asset["properties"]["preview"] == text
    assert fresh.read_result(asset["url"], max_bytes=65536) == text.encode()
    assert len(endpoint.requests) == 1
    request = endpoint.requests[0]
    assert request[1] == "/v1/chat/completions"
    assert request[2]["Authorization"] == "Bearer fixture-token"
    assert json.loads(request[3])["model"] == "custom/text-model"


def test_blockout_alias_routes_to_explicit_text_model(adapter, endpoint):
    client = adapter()
    job = submit(client, "model_scenario-llm", {"prompt": "Return only a scene plan JSON array"})
    asset = client.asset(job["metadata"]["assetIds"][0])
    assert asset["kind"] == "text"
    assert json.loads(endpoint.requests[0][3])["model"] == "custom/text-model"


@pytest.mark.parametrize("operation", ["prompt", "translate"])
def test_prompt_helpers_use_configured_text_model(adapter, endpoint, operation):
    client = adapter()
    params = {"prompt": "Buenos dias"}
    if operation == "prompt":
        params["mode"] = "completion"
    quote = getattr(client, f"estimate_{operation}")(params)
    job = client.submit_estimate(quote, before_send=lambda: None)
    completed = client.job(job["jobId"])
    assert completed["status"] == "success"
    request = json.loads(endpoint.requests[0][3])
    assert request["model"] == "custom/text-model"
    assert any("Buenos dias" in m["content"] for m in request["messages"])
    if operation == "translate":
        assert "English" in request["messages"][0]["content"]


def test_image_generation_decodes_and_persists_bytes(adapter, endpoint):
    endpoint.response = lambda *args: (
        200,
        {},
        {"data": [{"b64_json": base64.b64encode(PNG).decode()}]},
    )
    client = adapter(capabilities=("image",))
    job = submit(client, "openai-image", {"prompt": "A castle", "size": "1024x1024"})
    asset = client.asset(job["metadata"]["assetIds"][0])
    assert asset["mimeType"] == "image/png"
    assert client.read_result(asset["url"], max_bytes=1024) == PNG
    assert endpoint.requests[0][1] == "/v1/images/generations"
    assert json.loads(endpoint.requests[0][3])["model"] == "custom-image"


def test_image_edit_uses_staged_reference_multipart(adapter, endpoint):
    endpoint.response = lambda *args: (
        200,
        {},
        {"data": [{"b64_json": base64.b64encode(PNG).decode()}]},
    )
    client = adapter(capabilities=("image_edit",))
    reference = client.stage_image(PNG, media_type="image/png")
    submit(client, "openai-image-edit", {"prompt": "Paint the walls", "image": reference["id"]})
    request = endpoint.requests[0]
    assert request[1] == "/v1/images/edits"
    assert request[2]["Content-Type"].startswith("multipart/form-data; boundary=")
    assert b'name="image"; filename="reference.png"' in request[3]
    assert b"custom-image" in request[3]
    assert PNG in request[3]


def test_image_edit_rejects_unstaged_paths_before_sending(adapter, endpoint):
    client = adapter(capabilities=("image_edit",))
    with pytest.raises((ValueError, AdapterError), match="reference|asset|image"):
        client.estimate_model(
            client.model("openai-image-edit"), {"prompt": "Paint", "image": "C:/private.png"}
        )
    assert endpoint.requests == []


def test_speech_has_separate_capability_and_returns_audio(adapter, endpoint):
    wav = b"RIFF\x28\x00\x00\x00WAVEfmt " + b"\x00" * 32
    endpoint.response = lambda *args: (200, {"Content-Type": "audio/wav"}, wav)
    client = adapter(capabilities=("speech",))
    job = submit(client, "openai-speech", {"prompt": "Hello world", "voice": "alloy"})
    asset = client.asset(job["metadata"]["assetIds"][0])
    assert asset["mimeType"] == "audio/wav"
    assert client.read_result(asset["url"], max_bytes=1024) == wav
    assert endpoint.requests[0][1] == "/v1/audio/speech"
    assert json.loads(endpoint.requests[0][3]) == {
        "model": "custom-speech",
        "input": "Hello world",
        "voice": "alloy",
        "response_format": "wav",
    }


def test_server_error_is_sanitized_and_never_retries(adapter, endpoint):
    endpoint.response = lambda *args: (500, {}, {"error": "secret fixture-token full URL"})
    client = adapter()
    quote = client.estimate_model(client.model("openai-text"), {"prompt": "Hello"})
    with pytest.raises(AdapterError) as failure:
        client.submit_estimate(quote, before_send=lambda: None)
    assert "fixture-token" not in str(failure.value)
    assert "secret" not in str(failure.value)
    assert len(endpoint.requests) == 1
    with pytest.raises(ValueError):
        client.submit_estimate(quote, before_send=lambda: None)


@pytest.mark.parametrize(
    "body",
    [
        {"choices": []},
        {"choices": [{"message": {"content": None}}]},
        {"choices": [{"message": {"content": "x" * 65537}}]},
    ],
)
def test_invalid_text_response_cannot_be_success(adapter, endpoint, body):
    endpoint.response = lambda *args: (200, {}, body)
    client = adapter()
    quote = client.estimate_model(client.model("openai-text"), {"prompt": "Hello"})
    job = client.submit_estimate(quote, before_send=lambda: None)
    with pytest.raises(AdapterError):
        client.job(job["jobId"])
    assert len(endpoint.requests) == 1


def test_external_result_url_requires_explicit_allowlist(adapter, endpoint):
    endpoint.response = lambda *args: (
        200,
        {},
        {"data": [{"url": "https://untrusted.example/secret?token=hidden"}]},
    )
    client = adapter(capabilities=("image",))
    quote = client.estimate_model(client.model("openai-image"), {"prompt": "Castle"})
    job = client.submit_estimate(quote, before_send=lambda: None)
    with pytest.raises(AdapterError) as failure:
        client.job(job["jobId"])
    assert "hidden" not in str(failure.value)
    assert "untrusted.example" not in str(failure.value)
    assert len(endpoint.requests) == 1


def test_malformed_base64_is_rejected_without_regenerating(adapter, endpoint):
    endpoint.response = lambda *args: (200, {}, {"data": [{"b64_json": "bad?base64"}]})
    client = adapter(capabilities=("image",))
    quote = client.estimate_model(client.model("openai-image"), {"prompt": "Castle"})
    job = client.submit_estimate(quote, before_send=lambda: None)
    for _ in range(2):
        with pytest.raises(AdapterError):
            client.job(job["jobId"])
    assert len(endpoint.requests) == 1


def test_result_download_has_no_authorization_and_retries_only_retrieval(adapter, endpoint):
    available = False

    def respond(method, path, data):
        if method == "POST":
            return 200, {}, {"data": [{"url": endpoint.url + "/result.png?signature=fixture"}]}
        if available:
            return 200, {"Content-Type": "image/png"}, PNG
        return 503, {}, b"temporary"

    endpoint.response = respond
    client = adapter(capabilities=("image",))
    quote = client.estimate_model(client.model("openai-image"), {"prompt": "Castle"})
    receipt = client.submit_estimate(quote, before_send=lambda: None)
    with pytest.raises(AdapterError, match="retrieved"):
        client.job(receipt["jobId"])
    available = True
    completed = adapter(capabilities=("image",)).job(receipt["jobId"])
    assert completed["status"] == "success"
    assert [req[0] for req in endpoint.requests] == ["POST", "GET", "GET"]
    assert all("Authorization" not in req[2] for req in endpoint.requests[1:])


def test_result_redirect_is_never_followed(adapter, endpoint):
    def respond(method, path, data):
        if method == "POST":
            return 200, {}, {"data": [{"url": endpoint.url + "/result"}]}
        return 302, {"Location": endpoint.url + "/steal-token"}, b""

    endpoint.response = respond
    client = adapter(capabilities=("image",))
    receipt = client.submit_estimate(
        client.estimate_model(client.model("openai-image"), {"prompt": "Castle"}),
        before_send=lambda: None,
    )
    with pytest.raises(AdapterError):
        client.job(receipt["jobId"])
    assert len(endpoint.requests) == 2


def test_closed_adapter_cannot_fetch_external_results(adapter, endpoint):
    client = adapter(capabilities=("image",))
    endpoint.response = lambda *args: (200, {}, {"data": [{"url": endpoint.url + "/image.png"}]})
    receipt = client.submit_estimate(
        client.estimate_model(client.model("openai-image"), {"prompt": "Castle"}),
        before_send=lambda: None,
    )
    client.close()
    with pytest.raises(AdapterError, match="Online|closed"):
        client.job(receipt["jobId"])
    assert len(endpoint.requests) == 1


def test_bounded_base64_is_checked_before_decode(adapter, endpoint, monkeypatch):
    import scenario.core.providers.openai_compatible as module

    monkeypatch.setattr(module, "MAX_RESULT_BYTES", 8)
    endpoint.response = lambda *args: (200, {}, {"data": [{"b64_json": "A" * 16}]})
    client = adapter(capabilities=("image",))
    receipt = client.submit_estimate(
        client.estimate_model(client.model("openai-image"), {"prompt": "Castle"}),
        before_send=lambda: None,
    )
    with pytest.raises(AdapterError, match="byte limit"):
        client.job(receipt["jobId"])


def test_prompt_helper_refuses_images_without_vision_capability(adapter, endpoint):
    with pytest.raises(ValueError, match="vision"):
        adapter().estimate_prompt({"mode": "contextual", "prompt": "Hello", "images": ["img-1"]})
    assert endpoint.requests == []


@pytest.mark.parametrize(
    "target_id,capability", [("openai-text", "text"), ("openai-image", "image")]
)
def test_json_array_cannot_escape_sanitized_result_boundary(
    adapter, endpoint, target_id, capability
):
    endpoint.response = lambda *args: (200, {"Content-Type": "application/json"}, b"[]")
    client = adapter(capabilities=(capability,))
    receipt = client.submit_estimate(
        client.estimate_model(client.model(target_id), {"prompt": "Hello"}),
        before_send=lambda: None,
    )
    with pytest.raises(AdapterError):
        client.job(receipt["jobId"])
