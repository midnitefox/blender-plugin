# SPDX-FileCopyrightText: 2026 Open Studio contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Loopback ComfyUI contract: discovered nodes, bound graphs and durable receipts."""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import httpx
import pytest

from scenario.core.api.sdk_adapter import AdapterError
from scenario.core.providers.comfy import ComfyUIAdapter
from scenario.core.providers.config import ProviderProfile
from scenario.core.providers.recipes import Recipe, load_recipes


def recipe():
    return {
        "id": "example",
        "name": "Test image",
        "capabilities": ["txt2img"],
        "graph": {
            "1": {"class_type": "TestImage", "inputs": {"text": ""}},
            "2": {"class_type": "SaveImage", "inputs": {"images": ["1", 0]}},
        },
        "inputs": [
            {
                "name": "prompt",
                "type": "string",
                "prompt": True,
                "required": True,
                "node": "1",
                "input": "text",
            }
        ],
        "outputs": [{"node": "2", "key": "images", "kind": "image", "media_type": "image/png"}],
    }


@pytest.fixture
def server(tmp_path):
    state = {"requests": [], "history": {}, "queue_running": [], "queue_pending": []}

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            state["requests"].append(("GET", self.path, None))
            if self.path == "/object_info":
                value = {"TestImage": {}, "SaveImage": {}}
            elif self.path.startswith("/history/"):
                value = state["history"]
            elif self.path == "/queue":
                value = {key: state[key] for key in ("queue_running", "queue_pending")}
            elif self.path.startswith("/view?"):
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b"PNG fixture bytes")
                return
            else:
                value = {}
            self.send_response(200)
            self.end_headers()
            self.wfile.write(json.dumps(value).encode())

        def do_POST(self):
            raw = self.rfile.read(int(self.headers["Content-Length"]))
            if self.path == "/upload/image":
                state["requests"].append(("POST", self.path, raw))
                self.send_response(200)
                self.end_headers()
                self.wfile.write(
                    json.dumps(
                        {"name": "upload.png", "subfolder": "open-studio", "type": "input"}
                    ).encode()
                )
                return
            body = json.loads(raw)
            state["requests"].append(("POST", self.path, body))
            value = {"prompt_id": "remote-1", "number": 1} if self.path == "/prompt" else {}
            if self.path == "/queue":
                state["queue_pending"] = []
            self.send_response(200)
            self.end_headers()
            self.wfile.write(json.dumps(value).encode())

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    path = tmp_path / "recipes.json"
    path.write_text(json.dumps({"version": 1, "recipes": [recipe()]}))
    profile = ProviderProfile(
        "fixture",
        "Fixture",
        "comfyui",
        f"http://127.0.0.1:{httpd.server_port}",
        workflows_path=str(path),
    )
    yield profile, state
    httpd.shutdown()
    httpd.server_close()
    thread.join(timeout=2)


def test_loopback_submit_poll_result_and_restart(server, tmp_path):
    profile, state = server
    adapter = ComfyUIAdapter(profile, online=lambda: True, state_dir=tmp_path / "state")
    model = adapter.models()[0]
    assert model["capabilities"] == ["txt2img"]
    assert adapter.test_connection()["models"] == 1
    quote = adapter.estimate_model(model, {"prompt": "A green cube"})
    assert quote.cost is None
    calls = []
    receipt = adapter.submit_estimate(quote, before_send=lambda: calls.append(True))
    assert receipt["jobId"] == "remote-1"
    submissions = [r for r in state["requests"] if r[0] == "POST"]
    assert len(submissions) == 1
    assert submissions[0][2]["prompt"]["1"]["inputs"]["text"] == "A green cube"
    state["history"] = {
        "remote-1": {
            "status": {"completed": True, "status_str": "success"},
            "outputs": {
                "2": {"images": [{"filename": "sample.png", "subfolder": "", "type": "output"}]}
            },
        }
    }
    adapter.close()
    # Source recipe edits cannot change the already submitted output mapping.
    changed = recipe()
    changed["outputs"][0]["node"] = "1"
    Path(profile.workflows_path).write_text(json.dumps({"version": 1, "recipes": [changed]}))
    fresh = ComfyUIAdapter(profile, online=lambda: True, state_dir=tmp_path / "state")
    job = fresh.job("remote-1")
    assert job["status"] == "success"
    asset = fresh.asset(job["metadata"]["assetIds"][0])
    assert asset["mimeType"] == "image/png"
    assert fresh.read_result(asset["url"]) == b"PNG fixture bytes"
    assert calls == [True]
    assert len([r for r in state["requests"] if r[0] == "POST"]) == 1
    fresh.close()


def test_recipe_identity_is_immutable_and_missing_nodes_block(server, tmp_path):
    profile, _ = server
    first = load_recipes(profile.workflows_path)[0]
    changed = recipe()
    changed["graph"]["1"]["inputs"]["text"] = "different"
    assert Recipe.from_dict(changed).model_id != first.model_id
    changed["graph"]["1"]["class_type"] = "MissingNode"
    with pytest.raises(ValueError, match="MissingNode"):
        Recipe.from_dict(changed).preflight({"SaveImage": {}})


def test_queued_cancellation_never_interrupts_another_running_job(server, tmp_path):
    profile, state = server
    adapter = ComfyUIAdapter(profile, online=lambda: True, state_dir=tmp_path / "state")
    model = adapter.models()[0]
    receipt = adapter.submit_estimate(
        adapter.estimate_model(model, {"prompt": "test"}), before_send=lambda: None
    )
    state["queue_pending"] = [[0, receipt["jobId"], {}, {}, []]]
    state["queue_running"] = [[1, "someone-else", {}, {}, []]]
    adapter.cancel_inference(receipt["jobId"])
    assert not any(r[1] == "/interrupt" for r in state["requests"])
    assert adapter.job(receipt["jobId"])["status"] == "canceled"
    adapter.close()


def test_running_job_cancellation_is_explicitly_unsupported(server, tmp_path):
    profile, state = server
    adapter = ComfyUIAdapter(profile, online=lambda: True, state_dir=tmp_path / "state")
    model = adapter.models()[0]
    adapter.submit_estimate(
        adapter.estimate_model(model, {"prompt": "test"}), before_send=lambda: None
    )
    state["queue_running"] = [[1, "remote-1", {}, {}, []]]
    with pytest.raises(AdapterError, match="running"):
        adapter.cancel_inference("remote-1")
    assert not any(r[1] == "/interrupt" for r in state["requests"])
    adapter.close()


def test_incomplete_history_keeps_polling(server, tmp_path):
    profile, state = server
    adapter = ComfyUIAdapter(profile, online=lambda: True, state_dir=tmp_path / "state")
    model = adapter.models()[0]
    adapter.submit_estimate(
        adapter.estimate_model(model, {"prompt": "test"}), before_send=lambda: None
    )
    state["history"] = {"remote-1": {"status": {"completed": False, "status_str": "running"}}}
    assert adapter.job("remote-1")["status"] == "in-progress"
    state["history"] = {"remote-1": {"status": {"completed": True, "status_str": "error"}}}
    assert adapter.job("remote-1")["status"] == "failure"
    adapter.close()


def test_basic_preset_discovers_installed_checkpoint(tmp_path):
    from scenario.core.api.catalog import ModelRecord
    from scenario.core.providers.recipes import basic_image_recipe
    from scenario.core.schema.params import parse_schema

    baseline = basic_image_recipe("installed.safetensors")
    nodes = {node["class_type"]: {} for node in baseline.document["graph"].values()}
    nodes["CheckpointLoaderSimple"] = {
        "input": {"required": {"ckpt_name": [["installed.safetensors"]]}}
    }
    selected = ProviderProfile("p", "Local", "comfyui", "http://127.0.0.1:8188")
    adapter = ComfyUIAdapter(
        selected,
        online=True,
        state_dir=tmp_path,
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json=nodes)),
    )
    model = adapter.models()[0]
    schema = parse_schema(ModelRecord.from_api(model, provider="comfyui"))
    assert schema.prompt_name == "prompt"
    assert baseline.bind({schema.prompt_name: "cube"})["6"]["inputs"]["text"] == "cube"
    quote = adapter.estimate_model(model, {"prompt": "cube"})
    assert quote.payload["steps"] == 20
    assert quote.payload["width"] == 512
    adapter.close()


def test_imported_text_recipe_requires_explicit_prompt_binding():
    raw = recipe()
    raw["inputs"][0].pop("prompt")
    with pytest.raises(ValueError, match="prompt.*true"):
        Recipe.from_dict(raw)
    raw["inputs"][0]["prompt"] = True
    imported = Recipe.from_dict(raw)
    assert imported.bind({"prompt": "selected text"})["1"]["inputs"]["text"] == "selected text"


@pytest.mark.parametrize("invalid", ["non-string", "ambiguous", "non-boolean"])
def test_imported_recipe_rejects_invalid_prompt_mapping(invalid):
    raw = recipe()
    if invalid == "non-string":
        raw["inputs"][0]["type"] = "number"
    elif invalid == "non-boolean":
        raw["inputs"][0]["prompt"] = "true"
    else:
        raw["graph"]["1"]["inputs"]["other"] = ""
        raw["inputs"].append(
            {"name": "other", "type": "string", "prompt": True, "node": "1", "input": "other"}
        )
    with pytest.raises(ValueError, match="prompt"):
        Recipe.from_dict(raw)


def test_image_reference_upload_uses_saved_bound_filename(server, tmp_path):
    profile, state = server
    raw = recipe()
    raw["graph"]["1"]["inputs"]["image"] = ""
    raw["inputs"].append(
        {
            "name": "image",
            "type": "file",
            "kind": "image",
            "required": True,
            "node": "1",
            "input": "image",
        }
    )
    raw["capabilities"] = ["img2img"]
    Path(profile.workflows_path).write_text(json.dumps({"version": 1, "recipes": [raw]}))
    adapter = ComfyUIAdapter(profile, online=True, state_dir=tmp_path / "state")
    uploaded = adapter.stage_image(b"\x89PNG\r\n\x1a\nfixture", media_type="image/png")
    adapter.close()
    fresh = ComfyUIAdapter(profile, online=True, state_dir=tmp_path / "state")
    quote = fresh.estimate_model(fresh.models()[0], {"prompt": "test", "image": uploaded["id"]})
    fresh.submit_estimate(quote, before_send=lambda: None)
    submitted = [r[2] for r in state["requests"] if r[1] == "/prompt"][0]
    assert submitted["prompt"]["1"]["inputs"]["image"] == "open-studio/upload.png"
    fresh.close()


@pytest.mark.parametrize(
    "filename,folder",
    [
        ("../escape.png", ""),
        ("x.png", "../escape"),
        ("x.png", "C:/private"),
        ("x.png", "/absolute"),
        ("x.png", "a\\b"),
    ],
)
def test_untrusted_output_paths_are_rejected(filename, folder):
    with pytest.raises(AdapterError):
        ComfyUIAdapter._descriptor({"filename": filename, "subfolder": folder, "type": "output"})


def test_recipe_rejects_cycles_and_mismatched_media_semantics():
    raw = recipe()
    raw["graph"]["1"]["inputs"]["loop"] = ["2", 0]
    with pytest.raises(ValueError, match="cycle"):
        Recipe.from_dict(raw)
    raw = recipe()
    raw["outputs"][0]["projection"] = "equirectangular"
    raw["outputs"][0]["media_type"] = "video/mp4"
    with pytest.raises(ValueError):
        Recipe.from_dict(raw)


def test_preflight_reports_missing_checkpoint_before_submission():
    from scenario.core.providers.recipes import basic_image_recipe

    configured = basic_image_recipe("missing.safetensors")
    nodes = {node["class_type"]: {} for node in configured.document["graph"].values()}
    nodes["CheckpointLoaderSimple"] = {
        "input": {"required": {"ckpt_name": [["installed.safetensors"]]}}
    }
    with pytest.raises(ValueError, match="ckpt_name"):
        configured.preflight(nodes)


def test_material_and_panorama_declarations_are_explicit():
    raw = recipe()
    raw["capabilities"] = ["txt2img", "material"]
    with pytest.raises(ValueError, match="map roles"):
        Recipe.from_dict(raw)
    raw["outputs"][0]["role"] = "normal"
    assert Recipe.from_dict(raw).model()["openStudio"]["lanes"] == ["material"]
    raw["capabilities"] = ["txt2img", "panorama"]
    raw["outputs"][0].pop("role")
    with pytest.raises(ValueError, match="projection"):
        Recipe.from_dict(raw)
    raw["outputs"][0]["projection"] = "equirectangular"
    assert Recipe.from_dict(raw).model()["openStudio"]["lanes"] == ["panorama"]


def test_recipe_emits_usable_explicit_panorama_contract():
    from scenario.core.api.catalog import ModelRecord, models_for_lane
    from scenario.core.scene.panorama_contract import contract_for

    raw = recipe()
    raw["capabilities"] = ["txt2img", "panorama"]
    raw["outputs"][0]["projection"] = "equirectangular"
    record = ModelRecord.from_api(Recipe.from_dict(raw).model(), provider="comfyui")
    assert models_for_lane("panorama", [record]) == [record]
    contract = contract_for(record.id, record)
    assert contract.output == "ldr"
    assert contract.mode == "GENERATE"


def test_recipe_file_is_bounded_read_only_json(tmp_path):
    path = tmp_path / "recipe.json"
    payload = b" " * (2097152 + 1)
    path.write_bytes(payload)
    with pytest.raises(ValueError, match="2 MiB"):
        load_recipes(path)
    assert path.read_bytes() == payload


@pytest.mark.parametrize(
    "data,media_type,kind",
    [
        (b"not an image", "image/png", "image"),
        (b"\x89PNG\r\n\x1a\nfixture", "image/jpeg", "image"),
        (b"\x89PNG\r\n\x1a\nfixture", "image/png", "video"),
        (b"\x89PNG\r\n\x1a\n" + b"x" * 33554432, "image/png", "image"),
    ],
    ids=["invalid-signature", "wrong-mime", "wrong-kind", "too-large"],
)
def test_upload_preflight_is_pure_and_uses_provider_limit(tmp_path, data, media_type, kind):
    selected = ProviderProfile("p", "Local", "comfyui", "http://127.0.0.1:8188")
    adapter = ComfyUIAdapter(selected, online=False, state_dir=tmp_path)
    assert adapter.max_reference_bytes == 33554432
    with pytest.raises(ValueError):
        adapter.validate_upload(data, media_type=media_type, kind=kind)
    assert not list(adapter._state_dir.iterdir())
    adapter.validate_upload(b"\x89PNG\r\n\x1a\nfixture", media_type="image/png", kind="image")
    assert not list(adapter._state_dir.iterdir())
    adapter.close()
