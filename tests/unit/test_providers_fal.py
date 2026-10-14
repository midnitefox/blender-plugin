# SPDX-FileCopyrightText: 2026 Open Studio contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Queue and result contracts against real credential-free loopback fixtures."""

import base64
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from scenario.core.api.sdk_adapter import AdapterError
from scenario.core.jobs.result_metadata import texture_role
from scenario.core.providers.config import ProviderProfile
from scenario.core.providers.fal import FalAdapter, presets

PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jRZkAAAAASUVORK5CYII="
)
GLB = b"glTF\x02\x00\x00\x00\x14\x00\x00\x00\x00\x00\x00\x00JSON"


@pytest.fixture
def server():
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def handle_request(self):
            data = self.rfile.read(int(self.headers.get("Content-Length", "0")))
            self.server.requests.append((self.command, self.path, dict(self.headers), data))
            status, headers, result = self.server.respond(self.command, self.path, data)
            if isinstance(result, (dict, list)):
                result = json.dumps(result).encode()
            self.send_response(status)
            for name, value in headers.items():
                self.send_header(name, value)
            self.send_header("Content-Length", str(len(result)))
            self.end_headers()
            self.wfile.write(result)

        do_GET = handle_request
        do_POST = handle_request
        do_PUT = handle_request

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    server.url = f"http://127.0.0.1:{server.server_port}"
    server.requests = []
    server.status = "IN_QUEUE"
    server.result = {
        "model_glb": {"url": server.url + "/result.glb", "content_type": "model/gltf-binary"}
    }
    server.receipt = {
        "request_id": "fixture-request",
        "status_url": server.url + "/fal-ai/test/requests/fixture-request/status",
        "response_url": server.url + "/fal-ai/test/requests/fixture-request",
        "cancel_url": server.url + "/fal-ai/test/requests/fixture-request/cancel",
    }

    def respond(method, path, data):
        if method == "POST":
            return 200, {}, server.receipt
        if method == "PUT":
            return 202, {}, {"status": "CANCELLATION_REQUESTED"}
        if path.endswith("/status"):
            return 200, {}, {"request_id": "fixture-request", "status": server.status}
        if path.endswith("/fixture-request"):
            return 200, {}, server.result
        return 200, {}, PNG if path.endswith(".png") else GLB

    server.respond = respond
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


@pytest.fixture
def adapter(server, tmp_path):
    clients = []

    def create(options=None):
        profile = ProviderProfile(
            "fal-fixture",
            "Fixture",
            "fal",
            server.url,
            api_key="fixture-key",
            options_json=json.dumps(options or {}),
        )
        client = FalAdapter(profile, online=lambda: True, state_dir=tmp_path)
        clients.append(client)
        return client

    yield create
    for client in clients:
        client.close()


def submit(client, model_id="fal-hunyuan-text-to-3d", parameters=None):
    quote = client.estimate_model(client.model(model_id), parameters or {"prompt": "a castle"})
    assert quote.cost is None
    receipt = client.submit_estimate(quote, before_send=lambda: None)
    with pytest.raises(ValueError):
        client.submit_estimate(quote, before_send=lambda: None)
    return receipt


def test_catalog_presets_have_declared_lanes_and_real_schema(adapter, server):
    client = adapter()
    models = {model["id"]: model for model in client.models()}
    assert models["fal-hunyuan-text-to-3d"]["endpoint"] == "fal-ai/hunyuan3d-v3/text-to-3d"
    assert models["fal-patina-material"]["openStudio"]["lanes"] == ["material"]
    assert models["fal-meshy-rigging"]["openStudio"]["edit3dTasks"] == ["RIG"]
    assert "panorama" not in {
        lane for model in models.values() for lane in model["openStudio"]["lanes"]
    }
    assert server.requests == []


def test_queue_submit_once_poll_and_restart_download_without_auth(adapter, server):
    client = adapter()
    receipt = submit(client)
    assert receipt["jobId"] == "fixture-request"
    assert server.requests[0][1] == "/fal-ai/hunyuan3d-v3/text-to-3d"
    assert server.requests[0][2]["Authorization"] == "Key fixture-key"
    assert server.requests[0][2]["X-Fal-No-Retry"] == "1"
    assert json.loads(server.requests[0][3])["prompt"] == "a castle"
    assert client.job("fixture-request")["status"] == "queued"
    server.status = "COMPLETED"
    fresh = adapter()
    result = fresh.job("fixture-request")
    assert result["status"] == "success"
    asset = fresh.asset(result["metadata"]["assetIds"][0])
    assert asset["kind"] == "3d"
    assert fresh.read_result(asset["url"], max_bytes=1024) == GLB
    assert "Authorization" not in server.requests[-1][2]
    assert len([r for r in server.requests if r[0] == "POST"]) == 1
    before = len(server.requests)
    assert adapter().job("fixture-request") == result
    assert len(server.requests) == before


def test_foreign_queue_receipt_cannot_receive_credentials(adapter, server):
    client = adapter()
    server.receipt["status_url"] = "https://attacker.example/status"
    with pytest.raises(AdapterError, match="receipt|destination"):
        submit(client)
    assert len(server.requests) == 1
    receipts = list(client._state_dir.glob("fal-receipts-*.json"))
    assert len(receipts) == 1
    assert json.loads(receipts[0].read_text())["request_id"] == "fixture-request"


def test_queue_receipt_must_bind_request_id(adapter, server):
    server.receipt["status_url"] = server.url + "/fal-ai/test/requests/other/status"
    with pytest.raises(AdapterError):
        submit(adapter())
    assert len(server.requests) == 1


def test_staged_image_is_sent_as_data_uri_only_at_submission(adapter, server):
    client = adapter()
    asset = client.stage_image(PNG, media_type="image/png")
    quote = client.estimate_model(client.model("fal-meshy-image-to-3d"), {"image_url": asset["id"]})
    assert quote.payload["image_url"] == asset["id"]
    assert server.requests == []
    client.submit_estimate(quote, before_send=lambda: None)
    assert (
        json.loads(server.requests[0][3])["image_url"]
        == "data:image/png;base64," + base64.b64encode(PNG).decode()
    )


def test_upload_preflight_checks_signature_kind_and_size_without_writes(
    adapter, server, monkeypatch
):
    client = adapter()
    assert client.max_reference_bytes == 64 * 1024 * 1024
    client.validate_upload(GLB, media_type="model/gltf-binary", kind="3d")
    for data, mime, kind in [
        (b"bad", "image/png", "image"),
        (PNG, "image/png", "3d"),
        (GLB, "model/gltf-binary", "text"),
    ]:
        with pytest.raises(ValueError):
            client.validate_upload(data, media_type=mime, kind=kind)
    monkeypatch.setattr(FalAdapter, "max_reference_bytes", 4)
    with pytest.raises(ValueError):
        client.validate_upload(GLB, media_type="model/gltf-binary", kind="3d")
    assert list(client._state_dir.iterdir()) == []
    assert server.requests == []


def test_staged_mesh_retexture_preserves_selected_model_and_prompt(adapter, server):
    client = adapter()
    asset = client.stage_file(GLB, media_type="model/gltf-binary", kind="3d")
    submit(
        client,
        "fal-meshy-retexture",
        {"model_url": asset["id"], "text_style_prompt": "weathered bronze"},
    )
    payload = json.loads(server.requests[0][3])
    assert payload["model_url"].startswith("data:model/gltf-binary;base64,")
    assert payload["text_style_prompt"] == "weathered bronze"


def test_arbitrary_file_path_and_public_url_are_not_staged_references(adapter, server):
    client = adapter()
    for value in (
        "C:/private.glb",
        "https://external.example/a.glb",
        "data:model/gltf-binary;base64,Zg==",
    ):
        with pytest.raises((AdapterError, ValueError)):
            client.estimate_model(client.model("fal-meshy-rigging"), {"model_url": value})
    assert server.requests == []


def test_material_maps_use_declared_map_types(adapter, server):
    client = adapter()
    server.status = "COMPLETED"
    server.result = {
        "images": [
            {
                "url": server.url + "/albedo.png",
                "map_type": "basecolor",
                "content_type": "image/png",
            },
            {"url": server.url + "/normal.png", "map_type": "normal", "content_type": "image/png"},
            {
                "url": server.url + "/roughness.png",
                "map_type": "roughness",
                "content_type": "image/png",
            },
        ]
    }
    receipt = submit(client, "fal-patina-material", {"prompt": "stone"})
    result = client.job(receipt["jobId"])
    assert [texture_role(client.asset(a)) for a in result["metadata"]["assetIds"]] == [
        "albedo",
        "normal",
        "roughness",
    ]


@pytest.mark.parametrize(
    ("model_id", "endpoint", "extra"),
    [
        ("fal-patina-image-maps", "fal-ai/patina", {}),
        ("fal-patina-extract", "fal-ai/patina/material/extract", {"prompt": "the stone wall"}),
    ],
)
def test_photo_material_presets_require_staged_image(adapter, server, model_id, endpoint, extra):
    client = adapter()
    model = client.model(model_id)
    assert model["openStudio"]["lanes"] == ["material"]
    with pytest.raises(ValueError):
        client.estimate_model(model, extra)
    image = client.stage_image(PNG, media_type="image/png")
    submit(client, model_id, {"image_url": image["id"], **extra})
    assert server.requests[0][1] == "/" + endpoint
    assert json.loads(server.requests[0][3])["image_url"].startswith("data:image/png;base64,")


def test_result_retry_only_retrieves_existing_request(adapter, server):
    client = adapter()
    submit(client)
    server.status = "COMPLETED"
    original = server.respond
    server.respond = lambda method, path, data: (
        (503, {}, b"temporary") if path.endswith(".glb") else original(method, path, data)
    )
    with pytest.raises(AdapterError, match="retriev"):
        client.job("fixture-request")
    server.respond = original
    assert adapter().job("fixture-request")["status"] == "success"
    assert len([row for row in server.requests if row[0] == "POST"]) == 1
    assert len([row for row in server.requests if row[1].endswith("/fixture-request")]) == 1


def test_pending_request_retains_its_output_recipe_after_configuration_changes(adapter, server):
    client = adapter()
    submit(client)
    changed = adapter({"models": [presets()[1]]})
    with pytest.raises(AdapterError):
        changed.model("fal-hunyuan-text-to-3d")
    server.status = "COMPLETED"
    result = changed.job("fixture-request")
    assert result["status"] == "success"
    assert changed.asset(result["metadata"]["assetIds"][0])["kind"] == "3d"
    assert len([row for row in server.requests if row[0] == "POST"]) == 1


@pytest.mark.parametrize(
    "inputs", [[None], [{"name": "x", "type": "file_array"}], [{"name": "x"}, {"name": "x"}]]
)
def test_unsupported_custom_recipe_fails_before_service_contact(adapter, server, inputs):
    recipe = presets()[0]
    recipe["inputs"] = inputs
    with pytest.raises(ValueError, match="input"):
        adapter({"models": [recipe]})
    assert server.requests == []


def test_fal_catalog_to_saved_results_through_shared_coordinator(server, tmp_path):
    from functools import partial

    from scenario.core.api.catalog import models_for_lane
    from scenario.core.api.sdk_catalog import SDKCatalog
    from scenario.core.jobs.coordinator import JobCoordinator
    from scenario.core.jobs.credential_storage import open_provider_store
    from scenario.core.jobs.store import JobOrigin, JobState
    from scenario.core.providers.transfers import ProviderResultDownloader
    from scenario.core.schema.params import parse_schema

    profile = ProviderProfile("fal-pipeline", "Pipeline", "fal", server.url, api_key="fixture-key")
    store = open_provider_store(tmp_path / "jobs", profile)
    catalog = SDKCatalog(
        profile,
        online=True,
        scope=store.scope,
        adapter_factory=partial(FalAdapter, state_dir=tmp_path),
    )
    client = catalog.create_job_adapter()
    root = tmp_path / "results"
    root.mkdir()
    coordinator = JobCoordinator(
        client,
        store,
        result_root=root,
        result_downloader=ProviderResultDownloader(client, online_access=lambda: True),
    )
    try:
        records = catalog.fetch_list()
        selected = next(
            record
            for record in models_for_lane("3d", records)
            if record.id == "fal-hunyuan-text-to-3d"
        )
        assert parse_schema(selected).prompt_name == "prompt"
        assert parse_schema(selected).by_name("face_count").is_integer
        quote = client.estimate_model(client.model(selected.id), {"prompt": "a castle"})
        origin = JobOrigin("file", "scene", "revision")
        prepared = coordinator.prepare(quote, origin)
        original = server.respond

        def respond(method, path, data):
            if method == "POST":
                assert store.records()[0].state == JobState.SUBMITTING
                assert store.records()[0].intent.quote_cost is None
            return original(method, path, data)

        server.respond = respond
        current = coordinator.submit(
            prepared, origin=origin, operation="model", target_id=selected.id, payload=quote.payload
        )
        request_id = current.intent.request_id
        server.status = "COMPLETED"
        coordinator.refresh_remote(request_id, expected_revision=current.revision)
        current = store.get(request_id)
        assert current.state == JobState.SUCCEEDED
        coordinator.load_results(request_id, expected_revision=current.revision)
        current = store.get(request_id)
        coordinator.download_results(request_id, expected_revision=current.revision)
        current = store.get(request_id)
        assert current.state == JobState.READY
        assert (
            coordinator.verify_results(request_id, expected_revision=current.revision)
            .paths[0]
            .read_bytes()
            == GLB
        )
        assert len([row for row in server.requests if row[0] == "POST"]) == 1
    finally:
        coordinator.close()
        catalog.close()


def test_cancellation_requests_do_not_claim_completion(adapter, server):
    client = adapter()
    submit(client)
    assert client.cancel_inference("fixture-request")["status"] == "CANCELLATION_REQUESTED"
    assert client.job("fixture-request")["status"] == "queued"
    assert [r[0] for r in server.requests].count("PUT") == 1


def test_failure_metadata_does_not_echo_service_errors(adapter, server):
    client = adapter()
    submit(client)
    server.respond = lambda *args: (
        200,
        {},
        {"request_id": "fixture-request", "status": "COMPLETED", "error": "secret fixture-key"},
    )
    result = client.job("fixture-request")
    assert result["status"] == "failure"
    assert "fixture-key" not in json.dumps(result)


def test_untrusted_asset_url_is_not_downloaded(adapter, server):
    client = adapter()
    submit(client)
    server.status = "COMPLETED"
    server.result = {"model_glb": {"url": "https://attacker.example/private?token=secret"}}
    with pytest.raises(AdapterError) as error:
        client.job("fixture-request")
    assert "secret" not in str(error.value)
    assert len(server.requests) == 3


def test_result_media_mismatch_does_not_publish_mesh(adapter, server):
    client = adapter()
    submit(client)
    server.status = "COMPLETED"
    server.result = {"model_glb": {"url": server.url + "/oops.png", "content_type": "image/png"}}
    with pytest.raises(AdapterError, match="media|format"):
        client.job("fixture-request")


def test_submit_error_does_not_retry(adapter, server):
    server.respond = lambda *args: (500, {}, {"error": "fixture-key"})
    with pytest.raises(AdapterError):
        submit(adapter())
    assert len(server.requests) == 1
