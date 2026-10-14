# SPDX-FileCopyrightText: 2026 Open Studio contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Optional fal queue adapter with documented presets and typed result roles.

Contracts: https://fal.ai/docs/documentation/model-apis/inference/queue and the
endpoint-specific https://fal.ai/models/{endpoint}/api schemas. Preset metadata
is deliberately narrower than the whole provider catalog. Preset availability
and generated output quality require the user's own first generation to verify.
"""

import base64
import copy
import hashlib
import json
import re
import threading
import time
import uuid
from urllib.parse import unquote, urlsplit

import httpx

from ..api.sdk_adapter import AdapterError
from ..jobs.result_metadata import image_signature_matches
from ..jobs.transfers import StoragePolicy, TransferError
from .base import ProviderAdapter, identifier

MAX_INPUT_BYTES = 64 * 1024 * 1024
MAX_RESULT_BYTES = 256 * 1024 * 1024
MAX_JOB_BYTES = 512 * 1024 * 1024
MAX_OUTPUTS = 32
_TRUSTED_HOSTS = frozenset({"fal.media", "v3.fal.media", "v3b.fal.media"})
_MAP_ROLES = {
    "basecolor": "texture-albedo",
    "normal": "texture-normal",
    "roughness": "3d-texture-roughness",
    "metalness": "texture-metallic",
    "height": "texture-height",
}
_MEDIA = {
    "image": {"image/png", "image/jpeg", "image/webp"},
    "3d": {"model/gltf-binary"},
    "video": {"video/mp4"},
    "audio": {"audio/wav", "audio/x-wav", "audio/mpeg", "audio/mp3"},
}


def _field(name, field_type="string", **options):
    return {"name": name, "type": field_type, **options}


def _file(name, kind, *, required=True):
    return _field(name, "file", kind=kind, required=required)


def _preset(key, endpoint, name, capabilities, fields, outputs, *, lanes=(), edit_tasks=()):
    return {
        "id": key,
        "endpoint": endpoint,
        "name": name,
        "capabilities": list(capabilities),
        "inputs": fields,
        "outputs": outputs,
        "openStudio": {"lanes": list(lanes), "edit3dTasks": list(edit_tasks)},
    }


def _output(path, kind, mime, **options):
    return {"path": path, "kind": kind, "mime_type": mime, **options}


def presets():
    """Curated input/output contracts verified against official endpoint schemas."""
    return [
        _preset(
            "fal-hunyuan-text-to-3d",
            "fal-ai/hunyuan3d-v3/text-to-3d",
            "Hunyuan 3D: text to mesh",
            ["txt23d"],
            [
                _field("prompt", required=True, prompt=True, maxLength=1024),
                _field("enable_pbr", "boolean", default=False),
                _field("face_count", "integer", min=40000, max=1500000, default=500000),
                _field("generate_type", enum=["Normal", "LowPoly", "Geometry"], default="Normal"),
            ],
            [_output("model_glb", "3d", "model/gltf-binary")],
        ),
        _preset(
            "fal-meshy-image-to-3d",
            "fal-ai/meshy/v7.1/image-to-3d",
            "Meshy 7.1: image to mesh",
            ["img23d"],
            [
                _file("image_url", "image"),
                _field(
                    "model_type", enum=["standard", "lowpoly", "smart-topology"], default="standard"
                ),
                _field("target_polycount", "integer", min=100, max=300000, default=30000),
                _field("should_texture", "boolean", default=True),
                _field("enable_pbr", "boolean", default=False),
            ],
            [_output("model_glb", "3d", "model/gltf-binary")],
        ),
        _preset(
            "fal-meshy-retexture",
            "fal-ai/meshy/v6-lite/retexture",
            "Meshy: retexture a mesh",
            ["3d23d"],
            [
                _file("model_url", "3d"),
                _field("text_style_prompt", prompt=True, maxLength=600),
                _file("image_style_url", "image", required=False),
                _field("enable_pbr", "boolean", default=False),
                _field("enable_original_uv", "boolean", default=True),
                _field("texture_resolution", enum=["2k", "4k", "8k"], default="2k"),
            ],
            [_output("model_glb", "3d", "model/gltf-binary")],
            edit_tasks=["RETEXTURE"],
        ),
        _preset(
            "fal-meshy-rigging",
            "fal-ai/meshy/rigging",
            "Meshy: humanoid rigging",
            ["3d23d"],
            [
                _file("model_url", "3d"),
                _field("height_meters", "number", default=1.7),
                _field("enable_animation", "boolean", default=False),
                _field("animation_action_id", "integer", min=0, max=696, default=92),
            ],
            [_output(["animation_glb", "rigged_character_glb"], "3d", "model/gltf-binary")],
            edit_tasks=["RIG"],
        ),
        _preset(
            "fal-patina-material",
            "fal-ai/patina/material",
            "Patina: seamless PBR material",
            ["txt2img", "img2img"],
            [
                _field("prompt", required=True, prompt=True),
                _file("image_url", "image", required=False),
                _field("image_size", enum=["square_hd", "square"], default="square_hd"),
                _field("num_images", "integer", min=1, max=1, default=1),
                _field("strength", "number", min=0.01, max=1, default=0.6),
                _field("enable_prompt_expansion", "boolean", default=False),
                _field("output_format", enum=["png"], default="png"),
            ],
            [_output("images", "image", "image/png", map_field="map_type", default_role="texture")],
            lanes=["material"],
        ),
        _preset(
            "fal-patina-image-maps",
            "fal-ai/patina",
            "Patina: PBR maps from a photo",
            ["img2img"],
            [
                _file("image_url", "image"),
                _field("output_format", enum=["png"], default="png"),
            ],
            [_output("images", "image", "image/png", map_field="map_type")],
            lanes=["material"],
        ),
        _preset(
            "fal-patina-extract",
            "fal-ai/patina/material/extract",
            "Patina: extract a seamless material",
            ["img2img"],
            [
                _field("prompt", required=True, prompt=True),
                _file("image_url", "image"),
                _field("image_size", enum=["square_hd", "square"], default="square_hd"),
                _field("num_images", "integer", min=1, max=1, default=1),
                _field("strength", "number", min=0.01, max=1, default=0.6),
                _field("enable_prompt_expansion", "boolean", default=False),
                _field("output_format", enum=["png"], default="png"),
            ],
            [_output("images", "image", "image/png", map_field="map_type", default_role="texture")],
            lanes=["material"],
        ),
        _preset(
            "fal-hunyuan-video-edit",
            "fal-ai/hunyuan-video/video-to-video",
            "Hunyuan: restyle a video",
            ["video2video"],
            [
                _field("prompt", required=True, prompt=True),
                _file("video_url", "video"),
                _field("resolution", enum=["480p", "580p", "720p"], default="720p"),
                _field("num_frames", "integer", enum=[85, 129], default=129),
                _field("strength", "number", min=0.01, max=1, default=0.85),
                _field("enable_safety_checker", "boolean", default=True),
            ],
            [_output("video", "video", "video/mp4")],
            lanes=["render_video"],
        ),
        _preset(
            "fal-stable-audio",
            "fal-ai/stable-audio-25/text-to-audio",
            "Stable Audio 2.5: music and sound",
            ["txt2audio"],
            [
                _field("prompt", required=True, prompt=True),
                _field("seconds_total", "integer", min=1, max=190, default=30),
                _field("num_inference_steps", "integer", min=4, max=8, default=8),
            ],
            [_output("audio", "audio", "audio/wav")],
        ),
        _preset(
            "fal-elevenlabs-sfx",
            "fal-ai/elevenlabs/sound-effects/v2",
            "ElevenLabs: sound effects",
            ["txt2audio"],
            [
                _field("text", required=True, prompt=True),
                _field("duration_seconds", "number", min=0.5, max=22),
                _field("prompt_influence", "number", min=0, max=1, default=0.3),
                _field("loop", "boolean", default=False),
                _field("output_format", enum=["mp3_44100_128"], default="mp3_44100_128"),
            ],
            [_output("audio", "audio", "audio/mpeg")],
        ),
    ]


def _signature(data, media_type):
    if media_type.startswith("image/"):
        return image_signature_matches(data[:16], media_type)
    if media_type == "model/gltf-binary":
        return len(data) >= 12 and data[:4] == b"glTF"
    if media_type == "video/mp4":
        return len(data) >= 12 and data[4:8] == b"ftyp"
    if media_type in {"audio/wav", "audio/x-wav"}:
        return len(data) >= 12 and data[:4] == b"RIFF" and data[8:12] == b"WAVE"
    if media_type in {"audio/mpeg", "audio/mp3"}:
        return data.startswith(b"ID3") or (
            len(data) >= 2 and data[0] == 255 and data[1] & 224 == 224
        )
    return False


class FalAdapter(ProviderAdapter):
    """One request per paid dispatch, durable queue IDs, credential-free results."""

    upload_kinds = frozenset({"image", "3d", "video", "audio"})
    max_reference_bytes = MAX_INPUT_BYTES

    def __init__(self, profile, **kwargs):
        super().__init__(profile, **kwargs)
        self._lock = threading.RLock()
        raw = profile.options.get("models", presets())
        if not isinstance(raw, list) or not 1 <= len(raw) <= 64:
            raise ValueError("Configure one to 64 explicit fal model recipes")
        self._models = {}
        for item in raw:
            model = self._validate_model(item)
            if model["id"] in self._models:
                raise ValueError("Use unique fal model recipe identifiers")
            self._models[model["id"]] = model
        hosts = profile.options.get("result_hosts", [])
        if not isinstance(hosts, list) or any(not isinstance(host, str) for host in hosts):
            raise ValueError("Configure explicit trusted result hostnames")
        self._storage = StoragePolicy(_TRUSTED_HOSTS | frozenset(hosts), max_bytes=MAX_RESULT_BYTES)

    @staticmethod
    def _validate_model(value):
        if not isinstance(value, dict):
            raise ValueError("Use a declarative fal model recipe")
        model = copy.deepcopy(value)
        identifier(model.get("id"))
        endpoint = model.get("endpoint")
        if (
            not isinstance(endpoint, str)
            or not re.fullmatch(r"[A-Za-z0-9_-]+(?:/[A-Za-z0-9_.-]+){1,6}", endpoint)
            or any(part in {".", ".."} for part in endpoint.split("/"))
        ):
            raise ValueError("Use an explicit fal endpoint identifier")
        if (
            not isinstance(model.get("name"), str)
            or not model["name"].strip()
            or len(model["name"]) > 200
        ):
            raise ValueError("Name each fal model recipe")
        if not isinstance(model.get("inputs"), list) or not isinstance(
            model.get("capabilities"), list
        ):
            raise ValueError("Declare model inputs and capabilities")
        names = set()
        for field in model["inputs"]:
            if (
                not isinstance(field, dict)
                or not isinstance(field.get("name"), str)
                or not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,127}", field["name"])
                or field["name"] in names
                or field.get("type", "string")
                not in {"string", "number", "integer", "boolean", "file"}
                or (field.get("type") == "file" and field.get("kind", "image") not in _MEDIA)
            ):
                raise ValueError(
                    "Declare unique supported scalar inputs and staged file references"
                )
            names.add(field["name"])
        outputs = model.get("outputs")
        if not isinstance(outputs, list) or not 1 <= len(outputs) <= MAX_OUTPUTS:
            raise ValueError("Declare a bounded fal output list")
        for output in outputs:
            if (
                not isinstance(output, dict)
                or output.get("kind") not in _MEDIA
                or output.get("mime_type") not in _MEDIA[output["kind"]]
            ):
                raise ValueError("Declare each output's supported kind and media type")
            paths = output.get("path")
            if isinstance(paths, str):
                paths = [paths]
            if (
                not isinstance(paths, list)
                or not 1 <= len(paths) <= 8
                or any(
                    not isinstance(path, str)
                    or not re.fullmatch(
                        r"[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*){0,6}", path
                    )
                    for path in paths
                )
            ):
                raise ValueError("Use explicit object-field paths for fal outputs")
            if output.get("map_field") not in {None, "map_type"}:
                raise ValueError("Only documented map_type semantics are supported")
            if output.get("default_role") not in {None, "texture", *_MAP_ROLES.values()}:
                raise ValueError("Use an explicit supported material map role")
        semantics = model.setdefault("openStudio", {"lanes": [], "edit3dTasks": []})
        if not isinstance(semantics, dict):
            raise ValueError("Declare model lane semantics")
        model.update(
            type="custom",
            status="",
            privacy="public",
            provider="fal",
            shortDescription="Optional fal endpoint; usage is billed by fal",
        )
        return model

    def models(self, *, privacy="public", max_pages=100):
        if privacy not in {"public", "private"}:
            raise ValueError("Choose public or private catalog visibility")
        return copy.deepcopy(list(self._models.values())) if privacy == "public" else []

    def test_connection(self):
        # The documented queue API has no harmless generic authentication ping.
        # Do not create a chargeable job or report credentials as verified.
        report = super().test_connection()
        report.update(
            connection_verified=False,
            generation_verified=False,
            message="Model recipes loaded; fal credentials are checked when you explicitly approve the first generation",
        )
        return report

    def stage_image(self, data, *, media_type):
        return self.stage_file(data, media_type=media_type, kind="image")

    @classmethod
    def validate_upload(cls, data, *, media_type, kind):
        """Pure file preflight; staged state and network are unchanged."""
        super().validate_upload(data, media_type=media_type, kind=kind)
        if media_type not in _MEDIA[kind] or not _signature(data, media_type):
            raise ValueError("Provide bounded reference bytes in a supported media format")

    def stage_file(self, data, *, media_type, kind):
        self.validate_upload(data, media_type=media_type, kind=kind)
        return self.save_result("fal-reference-" + uuid.uuid4().hex, data, media_type)

    def _reference(self, asset_id, kind):
        try:
            asset = self.asset(identifier(asset_id))
            if asset.get("kind") != kind or asset.get("mimeType") not in _MEDIA[kind]:
                raise ValueError
            data = self.read_result(asset["url"], max_bytes=MAX_INPUT_BYTES)
            if not _signature(data, asset["mimeType"]):
                raise ValueError
        except (AdapterError, KeyError, TypeError, ValueError):
            raise ValueError("Choose a staged reference from this provider connection") from None
        return asset, data

    def prepare_model(self, model, parameters):
        target, payload = super().prepare_model(model, parameters)
        for field in model["inputs"]:
            value = payload.get(field["name"])
            if field.get("type") == "file" and value is not None:
                self._reference(value, field.get("kind", "image"))
            elif isinstance(value, str) and len(value.encode()) > 65536:
                raise ValueError("Model text input exceeds the supported limit")
        if target == "fal-meshy-retexture" and not (
            payload.get("text_style_prompt") or payload.get("image_style_url")
        ):
            raise ValueError("Provide a texture description or a staged style image")
        return target, payload

    def _queue_path(self, url, request_id, role):
        try:
            if (
                not isinstance(url, str)
                or not url.isascii()
                or any(ord(c) <= 32 or ord(c) == 127 for c in url)
                or "\\" in url
            ):
                raise ValueError
            target, origin = urlsplit(url), urlsplit(self.base_url)
            if (
                (target.scheme, target.hostname, target.port)
                != (origin.scheme, origin.hostname, origin.port)
                or target.username is not None
                or target.password is not None
                or target.query
                or target.fragment
            ):
                raise ValueError
            path = unquote(target.path)
            tail = f"/requests/{request_id}"
            if role in {"status", "cancel"}:
                tail += "/" + role
            if role == "response" and path.endswith("/response"):
                path = path.removesuffix("/response")
            if (
                not path.endswith(tail)
                or any(part in {".", ".."} for part in path.split("/"))
                or "\\" in path
                or "%" in path
            ):
                raise ValueError
            if not target.path.startswith(origin.path.rstrip("/") + "/"):
                raise ValueError
            return target.path[len(origin.path.rstrip("/")) :]
        except (ValueError, UnicodeError):
            raise AdapterError("The fal queue receipt destination is invalid") from None

    def _submit(self, operation, target_id, payload):
        if operation != "model":
            raise ValueError("This fal connection supports configured model recipes")
        model = self.model(target_id)
        body = copy.deepcopy(payload)
        for field in model["inputs"]:
            if field.get("type") == "file" and body.get(field["name"]) is not None:
                asset, data = self._reference(body[field["name"]], field.get("kind", "image"))
                body[field["name"]] = (
                    f"data:{asset['mimeType']};base64," + base64.b64encode(data).decode()
                )
        if len(json.dumps(body).encode()) > 96 * 1024 * 1024:
            raise ValueError("Combined reference inputs exceed the submission limit")
        local_id = "fal-intent-" + uuid.uuid4().hex
        self.save_record("fal-intents", local_id, {"model_id": target_id, "state": "sending"})
        response = self._http.request_json("POST", "/" + model["endpoint"], body)
        # Even a malformed receipt can contain a real paid request ID. Keep it
        # before validation so a rejected response never erases that evidence.
        self.save_record("fal-receipts", local_id, response)
        try:
            request_id = identifier(response["request_id"])
            paths = {
                role: self._queue_path(response[role + "_url"], request_id, role)
                for role in ("status", "response", "cancel")
            }
        except (KeyError, TypeError, ValueError):
            raise AdapterError("The fal service returned an invalid queue receipt") from None
        self.save_record(
            "fal-requests", request_id, {"model_id": target_id, "model": model, "paths": paths}
        )
        record = {
            "jobId": request_id,
            "status": "pending",
            "jobType": "inference",
            "metadata": {"assetIds": [], "input": {"modelId": target_id}},
        }
        self.save_record("jobs", request_id, record)
        self.save_record(
            "fal-intents",
            local_id,
            {"model_id": target_id, "state": "received", "request_id": request_id},
        )
        return record

    def job(self, value):
        with self._lock:
            record = self.load_record("jobs", value)
            if record.get("status") in {"success", "failure", "canceled"}:
                return record
            saved = self.load_record("fal-requests", value)
            status = self._http.request_json("GET", saved["paths"]["status"])
            if not isinstance(status, dict) or status.get("request_id") != value:
                raise AdapterError("The fal status response does not match this request")
            remote = status.get("status")
            if remote not in {"IN_QUEUE", "IN_PROGRESS", "COMPLETED"}:
                raise AdapterError("The fal service returned an unsupported queue status")
            if remote != "COMPLETED":
                record["status"] = "queued" if remote == "IN_QUEUE" else "in-progress"
                self.save_record("jobs", value, record)
                return record
            if status.get("error") or status.get("error_type"):
                record["status"] = "failure"
                record["metadata"]["error"] = "The fal provider reported generation failure"
                self.save_record("jobs", value, record)
                return record
            try:
                result = self.load_record("fal-responses", value)
            except AdapterError:
                result = self._http.request_json("GET", saved["paths"]["response"])
                self.save_record("fal-responses", value, result)
            model = saved.get("model") or self.model(saved["model_id"])
            assets = self._materialize(value, model, result)
            record["status"] = "success"
            record["metadata"]["assetIds"] = assets
            self.save_record("jobs", value, record)
            return record

    def cancel_inference(self, value):
        saved = self.load_record("fal-requests", value)
        result = self._http.request_json("PUT", saved["paths"]["cancel"])
        if not isinstance(result, dict) or result.get("status") != "CANCELLATION_REQUESTED":
            raise AdapterError("The fal provider did not confirm a cancellation request")
        return {"status": "CANCELLATION_REQUESTED"}

    @staticmethod
    def _select(result, path):
        paths = [path] if isinstance(path, str) else path
        for candidate in paths:
            current = result
            for name in candidate.split("."):
                current = current.get(name) if isinstance(current, dict) else None
            if current is not None:
                return current
        return None

    def _materialize(self, job_id, model, result):
        if not isinstance(result, dict):
            raise AdapterError("The fal provider returned invalid result metadata")
        assets, total = [], 0
        for output in model["outputs"]:
            selected = self._select(result, output["path"])
            if selected is None:
                raise AdapterError("The fal result is missing a declared output")
            rows = selected if isinstance(selected, list) else [selected]
            if not rows or len(rows) + len(assets) > MAX_OUTPUTS:
                raise AdapterError("The fal result exceeds the supported output count")
            for row in rows:
                if isinstance(row, str):
                    row = {"url": row}
                if not isinstance(row, dict):
                    raise AdapterError("The fal provider returned invalid file metadata")
                mime = row.get("content_type") or output["mime_type"]
                if mime != output["mime_type"]:
                    raise AdapterError(
                        "The fal result media type does not match its configured format"
                    )
                data = self._download(row.get("url"))
                total += len(data)
                if total > MAX_JOB_BYTES or not _signature(data, mime):
                    raise AdapterError(
                        "The fal result exceeds its limit or has an invalid media format"
                    )
                size = row.get("file_size")
                if size is not None and (type(size) is not int or size != len(data)):
                    raise AdapterError("The fal result size does not match its metadata")
                result_id = "fal-" + hashlib.sha256(f"{job_id}:{len(assets)}".encode()).hexdigest()
                asset = self.save_result(result_id, data, mime)
                if output.get("map_field"):
                    value = row.get(output["map_field"])
                    if value is not None and value not in _MAP_ROLES:
                        raise AdapterError("The fal result has an unsupported material map role")
                    role = _MAP_ROLES.get(value, output.get("default_role"))
                    if role:
                        asset["metadata"]["type"] = role
                elif output.get("default_role"):
                    asset["metadata"]["type"] = output["default_role"]
                for dimension in ("width", "height"):
                    number = row.get(dimension)
                    if type(number) is int and 0 < number <= 32768:
                        asset["properties"][dimension] = number
                self.save_record("assets", asset["id"], asset)
                assets.append(asset["id"])
        return assets

    def _download(self, url):
        try:
            if (
                not isinstance(url, str)
                or not url.isascii()
                or any(ord(c) <= 32 or ord(c) == 127 for c in url)
                or "\\" in url
            ):
                raise ValueError
            target, origin = urlsplit(url), urlsplit(self.base_url)
            if target.username is not None or target.password is not None or target.fragment:
                raise ValueError
            if (target.scheme, target.hostname, target.port) != (
                origin.scheme,
                origin.hostname,
                origin.port,
            ):
                self._storage.destination(url)
            if not self.network_allowed():
                raise AdapterError("Online access is disabled")
            started = time.monotonic()
            with httpx.Client(trust_env=False, follow_redirects=False, timeout=60) as client:
                with client.stream("GET", url) as response:
                    if response.status_code != 200:
                        raise AdapterError(
                            "The fal result could not be retrieved; retry retrieval without generating again"
                        )
                    size = response.headers.get("content-length")
                    if size is not None and (not size.isdigit() or int(size) > MAX_RESULT_BYTES):
                        raise ValueError
                    data = bytearray()
                    for chunk in response.iter_bytes(65536):
                        if not self.network_allowed() or time.monotonic() - started > 300:
                            raise AdapterError("The fal result transfer was interrupted")
                        if len(data) + len(chunk) > MAX_RESULT_BYTES:
                            raise ValueError
                        data.extend(chunk)
                    return bytes(data)
        except (ValueError, TypeError, TransferError, httpx.HTTPError):
            raise AdapterError("The fal result destination or bytes were rejected") from None
