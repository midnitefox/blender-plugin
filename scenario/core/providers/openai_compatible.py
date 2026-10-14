# SPDX-FileCopyrightText: 2026 Open Studio contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Explicit text, image and speech contracts for compatible HTTP services.

Compatibility with chat completions says nothing about image or speech APIs.
Each endpoint needs its own enabled capability and selected model. Synchronous
responses are durably retained before interpretation so a failed result fetch
can be retried without another generation request. This module never retries a
submission and never sends service credentials to result URLs.
"""

import base64
import binascii
import copy
import json
import re
import threading
import time
import uuid
from urllib.parse import urlsplit

import httpx

from ..api.sdk_adapter import AdapterError
from ..jobs.transfers import StoragePolicy, TransferError
from ..schema.forms import prepare_run, record_schema
from .base import ProviderAdapter

MAX_RESULT_BYTES = 64 * 1024 * 1024
MAX_RESPONSE_BYTES = 96 * 1024 * 1024
MAX_TEXT_BYTES = 65536
MAX_IMAGES = 4
_TEXT_MODELS = {"openai-text", "model_scenario-llm"}
_MODEL_CAPABILITIES = {
    "openai-text": ("text", "text_model", "Text and scene planning", ["txt2txt"]),
    "openai-image": ("image", "image_model", "Image generation", ["txt2img"]),
    "openai-image-edit": ("image_edit", "image_model", "Image editing", ["img2img"]),
    "openai-speech": ("speech", "speech_model", "Speech", ["txt2audio"]),
}
_PROMPT_MODES = {
    "completion": "Complete and improve the creative prompt. Return only the resulting prompt.",
    "contextual": "Improve the creative prompt while retaining its subject and intent. Return only the prompt.",
    "contextual-v2": "Improve the creative prompt while retaining its subject and intent. Return only the prompt.",
    "image-editing": "Write clear image editing instructions from the user's request. Return only the prompt.",
    "inventive": "Write an imaginative and specific creative prompt from the user's idea. Return only the prompt.",
    "structured": "Refine the creative prompt into clear, structured instructions. Return only the prompt.",
}


def _text(value, *, limit=MAX_TEXT_BYTES, label="prompt"):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"Provide nonempty {label} text")
    try:
        valid = len(value.encode("utf-8")) <= limit
    except UnicodeError:
        valid = False
    if not valid:
        raise ValueError(f"The {label} exceeds the supported text limit")
    return value


def _image_type(data):
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if data.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        return "image/webp"
    raise AdapterError("The service returned an unsupported image; use PNG, JPEG or WebP")


class OpenAICompatibleAdapter(ProviderAdapter):
    """Configured endpoint/model routes on the shared durable job boundary."""

    upload_kinds = frozenset({"image"})
    max_reference_bytes = MAX_RESULT_BYTES

    def __init__(self, profile, **kwargs):
        super().__init__(profile, **kwargs)
        options = profile.options
        capabilities = options.get("capabilities", [])
        if (
            not isinstance(capabilities, list)
            or any(type(item) is not str for item in capabilities)
            or set(capabilities) - {"text", "image", "image_edit", "speech"}
        ):
            raise ValueError("Choose explicit supported compatible-service capabilities")
        self._enabled = frozenset(capabilities)
        self._selected_profile = profile
        self._options = options
        self._result_lock = threading.RLock()
        for capability, field, _, _ in _MODEL_CAPABILITIES.values():
            if capability in self._enabled:
                _text(getattr(profile, field), limit=512, label="model")
        hosts = options.get("result_hosts", [])
        if not isinstance(hosts, list) or any(type(host) is not str for host in hosts):
            raise ValueError("Configure explicit trusted result hostnames")
        self._result_policy = StoragePolicy(frozenset(hosts)) if hosts else None

    def models(self, *, privacy="public", max_pages=100):
        if privacy not in {"public", "private"}:
            raise ValueError("Choose public or private catalog visibility")
        if privacy == "private":
            return []
        return [
            self.model(identifier)
            for identifier, (capability, *_rest) in _MODEL_CAPABILITIES.items()
            if capability in self._enabled
        ]

    def model_page(self, *, privacy="public", page_size=100, pagination_token=None):
        # A real, read-only connection check. A /models response does not enable
        # capabilities: users explicitly select the available operation types.
        result = self._http.request_json("GET", "/models")
        if not isinstance(result, dict) or not isinstance(result.get("data"), list):
            raise AdapterError("The service returned an invalid model discovery response")
        return {"models": self.models(privacy=privacy)[:page_size]}

    def test_connection(self):
        self.model_page(page_size=1)
        report = super().test_connection()
        report.update(
            generation_verified=False,
            message="Connected to model discovery; generation capabilities are configured and require a first generation to verify",
        )
        return report

    def model(self, identifier):
        # The existing native blockout command uses this stable model alias.
        # It keeps its validated local plan/application path and selected text
        # provider; it never implies access to Scenario's service.
        spec = _MODEL_CAPABILITIES.get("openai-text" if identifier in _TEXT_MODELS else identifier)
        if spec is None or spec[0] not in self._enabled:
            raise ValueError("This model capability is not enabled for the selected connection")
        capability, field, label, advertised = spec
        fields = [{"name": "prompt", "type": "string", "required": True, "prompt": True}]
        if capability in {"image", "image_edit"}:
            fields.extend(
                [
                    {"name": "size", "type": "string"},
                    {"name": "quality", "type": "string"},
                    {
                        "name": "numImages",
                        "type": "integer",
                        "min": 1,
                        "max": MAX_IMAGES,
                        "default": 1,
                    },
                ]
            )
        if capability == "image_edit":
            fields.append({"name": "image", "type": "file", "kind": "image", "required": True})
        if capability == "speech":
            fields.append(
                {
                    "name": "voice",
                    "type": "string",
                    "required": True,
                    "default": self._options.get("speech_voice", "alloy"),
                }
            )
        return {
            "id": identifier,
            "name": label,
            "type": "custom",
            "status": "",
            "privacy": "public",
            "capabilities": list(advertised),
            "inputs": fields,
            "shortDescription": f"Uses the configured {getattr(self._selected_profile, field)} model",
            "provider": "openai_compatible",
            "providerModel": getattr(self._selected_profile, field),
        }

    def prepare_model(self, model, parameters):
        if not isinstance(model, dict):
            raise ValueError("Choose a configured model")
        record = self.model(model.get("id"))
        if record != model:
            raise ValueError("Provider model configuration changed; refresh the model")
        identifier, payload = prepare_run(record["id"], record_schema("model", record), parameters)
        _text(payload.get("prompt"))
        for name in ("size", "quality", "voice"):
            if name in payload:
                _text(payload[name], limit=256, label=name)
        if identifier == "openai-image-edit":
            self._reference(payload.get("image"))
        return identifier, payload

    def estimate_prompt(self, parameters):
        self.model("openai-text")
        if not isinstance(parameters, dict) or parameters.keys() - {
            "mode",
            "prompt",
            "modelId",
            "images",
            "numResults",
        }:
            raise ValueError("Unsupported prompt helper parameters")
        mode = parameters.get("mode")
        if mode not in _PROMPT_MODES:
            raise ValueError("Choose a supported prompt helper mode")
        if parameters.get("images"):
            raise ValueError("This text connection has no enabled vision capability")
        count = parameters.get("numResults", 1)
        if type(count) is not int or count != 1:
            raise ValueError("This text connection returns one prompt per request")
        _text(parameters.get("prompt", "Create an imaginative visual prompt"))
        return self._estimate("prompt", "prompt", copy.deepcopy(parameters))

    def estimate_translate(self, parameters):
        self.model("openai-text")
        if not isinstance(parameters, dict) or set(parameters) != {"prompt"}:
            raise ValueError("Provide only the nonempty prompt to translate")
        _text(parameters["prompt"])
        return self._estimate("translate", "translate", dict(parameters))

    @classmethod
    def validate_upload(cls, data, *, media_type, kind):
        """Pure reference preflight before the upload service claims an intent."""
        super().validate_upload(data, media_type=media_type, kind=kind)
        try:
            valid = _image_type(data) == media_type
        except AdapterError:
            valid = False
        if not valid:
            raise ValueError("The image reference does not match its declared media type")

    def stage_image(self, data, *, media_type):
        """Register caller-owned verified reference bytes, without reading paths."""
        self.validate_upload(data, media_type=media_type, kind="image")
        return self._save_asset(f"reference-{uuid.uuid4().hex}", data, media_type)

    def _reference(self, identifier):
        if not isinstance(identifier, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,128}", identifier):
            raise ValueError("Choose a staged image reference from this connection")
        try:
            asset = self.asset(identifier)
            data = self.read_result(asset["url"], max_bytes=MAX_RESULT_BYTES)
        except (AdapterError, KeyError, ValueError):
            raise ValueError("Choose an available image reference from this connection") from None
        if _image_type(data) != asset.get("mimeType"):
            raise ValueError("The image reference has invalid media metadata")
        return asset, data

    def _multipart(self, parameters):
        asset, data = self._reference(parameters["image"])
        boundary = "openstudio" + uuid.uuid4().hex
        body = bytearray()
        fields = {
            "model": self._selected_profile.image_model,
            "prompt": parameters["prompt"],
            "n": parameters.get("numImages", 1),
        }
        fields.update(
            (name, parameters[name]) for name in ("size", "quality") if name in parameters
        )
        for name, value in fields.items():
            body.extend(
                f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'.encode()
            )
        suffix = {"image/png": "png", "image/jpeg": "jpg", "image/webp": "webp"}[asset["mimeType"]]
        body.extend(
            (
                f'--{boundary}\r\nContent-Disposition: form-data; name="image"; filename="reference.{suffix}"\r\nContent-Type: {asset["mimeType"]}\r\n\r\n'
            ).encode()
        )
        body.extend(data)
        body.extend(f"\r\n--{boundary}--\r\n".encode())
        return bytes(body), f"multipart/form-data; boundary={boundary}"

    def _submit(self, operation, target_id, payload):
        if operation not in {"model", "prompt", "translate"}:
            raise ValueError("This connection does not support that generation operation")
        job_id = "openai-" + uuid.uuid4().hex
        receipt = {"jobId": job_id, "status": "pending", "metadata": {"assetIds": []}}
        self.save_record("jobs", job_id, receipt)
        # A durable unknown-outcome marker precedes the request. It must never be
        # interpreted as permission to replay the paid operation after restart.
        self.save_record(
            "openai-requests",
            job_id,
            {"operation": operation, "target_id": target_id, "state": "sending"},
        )
        try:
            response = self._send(operation, target_id, payload, job_id)
        except AdapterError as error:
            error.job_id = job_id
            raise
        self.save_record(
            "openai-responses",
            job_id,
            {"operation": operation, "target_id": target_id, "response": response},
        )
        self.save_record(
            "openai-requests",
            job_id,
            {"operation": operation, "target_id": target_id, "state": "received"},
        )
        return receipt

    def _send(self, operation, target_id, payload, job_id):
        if operation in {"prompt", "translate"} or target_id in _TEXT_MODELS:
            self.model("openai-text")
            messages = []
            if operation == "translate":
                messages.append(
                    {
                        "role": "system",
                        "content": "Translate the user's text into English. Return only the translation.",
                    }
                )
            elif operation == "prompt":
                messages.append({"role": "system", "content": _PROMPT_MODES[payload["mode"]]})
            messages.append(
                {
                    "role": "user",
                    "content": payload.get("prompt", "Create an imaginative visual prompt"),
                }
            )
            return self._http.request_json(
                "POST",
                "/chat/completions",
                {"model": self._selected_profile.text_model, "messages": messages},
                max_bytes=MAX_RESPONSE_BYTES,
            )
        self.model(target_id)
        if target_id == "openai-image":
            data = {
                "model": self._selected_profile.image_model,
                "prompt": payload["prompt"],
                "n": payload.get("numImages", 1),
            }
            data.update((name, payload[name]) for name in ("size", "quality") if name in payload)
            return self._http.request_json(
                "POST", "/images/generations", data, max_bytes=MAX_RESPONSE_BYTES
            )
        if target_id == "openai-image-edit":
            body, content_type = self._multipart(payload)
            raw = self._http.request_bytes(
                "POST",
                "/images/edits",
                body,
                content_type=content_type,
                max_bytes=MAX_RESPONSE_BYTES,
            )
            try:
                response = json.loads(raw)
            except (ValueError, UnicodeError, RecursionError):
                raise AdapterError(
                    "The service returned an invalid image editing response"
                ) from None
            if not isinstance(response, dict):
                raise AdapterError("The service returned an invalid image editing response")
            return response
        if target_id == "openai-speech":
            data = self._http.request_bytes(
                "POST",
                "/audio/speech",
                {
                    "model": self._selected_profile.speech_model,
                    "input": payload["prompt"],
                    "voice": payload["voice"],
                    "response_format": "wav",
                },
                max_bytes=MAX_RESULT_BYTES,
            )
            asset = self._save_asset(job_id + "-audio", data, "audio/wav")
            return {"audio_asset": asset["id"]}
        raise ValueError("Choose an enabled compatible-service model")

    def job(self, identifier):
        with self._result_lock:
            record = self.load_record("jobs", identifier)
            if record.get("status") == "success":
                return record
            try:
                response = self.load_record("openai-responses", identifier)
            except AdapterError:
                raise AdapterError(
                    "This synchronous request has an unknown outcome; inspect the provider before generating again"
                ) from None
            assets = self._materialize(identifier, response)
            result = {**record, "status": "success", "metadata": {"assetIds": assets}}
            self.save_record("jobs", identifier, result)
            return result

    def _materialize(self, identifier, saved):
        operation, target_id, response = saved["operation"], saved["target_id"], saved["response"]
        if not isinstance(response, dict):
            raise AdapterError("The service returned invalid result metadata")
        if operation in {"prompt", "translate"} or target_id in _TEXT_MODELS:
            try:
                choices = response["choices"]
                if not isinstance(choices, list) or len(choices) != 1:
                    raise ValueError
                text = _text(choices[0]["message"]["content"], label="response")
            except (KeyError, TypeError, ValueError):
                raise AdapterError(
                    "The service returned no complete, bounded text result"
                ) from None
            return [self._save_asset(identifier + "-text", text.encode(), "text/plain")["id"]]
        if target_id == "openai-speech":
            asset = self.asset(response["audio_asset"])
            data = self.read_result(asset["url"], max_bytes=MAX_RESULT_BYTES)
            if len(data) < 12 or data[:4] != b"RIFF" or data[8:12] != b"WAVE":
                raise AdapterError("The speech service did not return the requested WAV audio")
            return [asset["id"]]
        images = response.get("data")
        if not isinstance(images, list) or not 1 <= len(images) <= MAX_IMAGES:
            raise AdapterError("The service returned no supported image result list")
        assets = []
        total = 0
        for index, item in enumerate(images):
            if not isinstance(item, dict):
                raise AdapterError("The service returned invalid image result metadata")
            encoded = item.get("b64_json")
            if encoded is not None:
                if not isinstance(encoded, str) or len(encoded) > 4 * ((MAX_RESULT_BYTES + 2) // 3):
                    raise AdapterError("The image result exceeds the supported byte limit")
                try:
                    data = base64.b64decode(encoded, validate=True)
                except (binascii.Error, ValueError):
                    raise AdapterError("The service returned invalid encoded image data") from None
            else:
                data = self._fetch_result(item.get("url"))
            total += len(data)
            if not data or total > MAX_RESULT_BYTES:
                raise AdapterError("The image result exceeds the supported byte limit")
            assets.append(
                self._save_asset(f"{identifier}-image-{index}", data, _image_type(data))["id"]
            )
        return assets

    def _save_asset(self, identifier, data, media_type):
        asset = self.save_result(identifier, data, media_type)
        kind = media_type.split("/")[0]
        properties = {"size": len(data)}
        if media_type == "text/plain":
            properties.update(preview=data.decode("utf-8"), hasFullPreview=True)
        asset.update(kind=kind, type=kind, status="success", properties=properties)
        asset["metadata"] = dict(properties)
        self.save_record("assets", identifier, asset)
        return asset

    def _fetch_result(self, url):
        try:
            if (
                not isinstance(url, str)
                or not url.isascii()
                or any(ord(c) <= 32 or ord(c) == 127 for c in url)
                or "\\" in url
            ):
                raise ValueError
            target, endpoint = urlsplit(url), urlsplit(self._selected_profile.base_url)
            if target.username is not None or target.password is not None or target.fragment:
                raise ValueError
            same_origin = (
                target.scheme,
                target.hostname,
                target.port or (443 if target.scheme == "https" else 80),
            ) == (
                endpoint.scheme,
                endpoint.hostname,
                endpoint.port or (443 if endpoint.scheme == "https" else 80),
            )
            if not same_origin:
                if self._result_policy is None:
                    raise ValueError
                self._result_policy.destination(url)
            if not self.network_allowed():
                raise AdapterError("Online access is disabled or the connection was closed")
            # New credential-free connection: no selected API key, environment
            # proxy, netrc, cookies, redirects or automatic retry follows a URL.
            started = time.monotonic()
            with httpx.Client(trust_env=False, follow_redirects=False, timeout=60) as client:
                with client.stream("GET", url, headers={"Accept": "image/*"}) as response:
                    if response.status_code != 200:
                        raise AdapterError(
                            "The image result could not be retrieved; retry retrieval without generating again"
                        )
                    length = response.headers.get("content-length")
                    if length is not None and (
                        not length.isdigit() or int(length) > MAX_RESULT_BYTES
                    ):
                        raise ValueError
                    data = bytearray()
                    for chunk in response.iter_bytes(65536):
                        if not self.network_allowed():
                            raise AdapterError("Online access is disabled")
                        if time.monotonic() - started > 180:
                            raise AdapterError("The image result transfer exceeded its time limit")
                        if len(data) + len(chunk) > MAX_RESULT_BYTES:
                            raise ValueError
                        data.extend(chunk)
                    return bytes(data)
        except (ValueError, TypeError, TransferError, httpx.HTTPError):
            raise AdapterError(
                "The image result destination or data was rejected; configure trusted storage hosts and retry retrieval"
            ) from None
