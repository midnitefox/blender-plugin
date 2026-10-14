# SPDX-FileCopyrightText: 2026 Open Studio contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""ComfyUI API workflows, durable prompt receipts and declared output semantics.

Protocol: https://docs.comfy.org/development/comfyui-server/comms_routes
Only a queued, owned prompt can be canceled; global /interrupt is never used.
"""

import hashlib
import uuid
from urllib.parse import urlencode

from ..api.sdk_adapter import AdapterError
from ..jobs.result_metadata import image_signature_matches
from .base import MAX_RESULT_BYTES, ProviderAdapter, identifier
from .recipes import Recipe, basic_image_recipe, load_recipes

_ROLE_TYPES = {
    "base": "texture",
    "albedo": "texture-albedo",
    "normal": "texture-normal",
    "roughness": "3d-texture-roughness",
    "metallic": "texture-metallic",
    "height": "texture-height",
    "ao": "texture-ao",
    "smoothness": "texture-smoothness",
    "edge": "texture-edge",
}


class ComfyUIAdapter(ProviderAdapter):
    upload_kinds = frozenset({"image"})
    max_reference_bytes = 32 * 1024 * 1024

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.profile.provider != "comfyui":
            raise ValueError("Select a ComfyUI profile")
        self._recipes = None

    def _discover(self):
        if self._recipes is not None:
            return self._recipes
        node_info = self._http.request_json("GET", "/object_info")
        if self.profile.workflows_path:
            recipes = load_recipes(self.profile.workflows_path)
        else:
            try:
                checkpoints = node_info["CheckpointLoaderSimple"]["input"]["required"]["ckpt_name"][
                    0
                ]
                if not isinstance(checkpoints, list) or not checkpoints:
                    raise ValueError
                checkpoint = self.profile.image_model or checkpoints[0]
                if checkpoint not in checkpoints:
                    raise ValueError
            except (KeyError, IndexError, TypeError, ValueError):
                raise AdapterError(
                    "ComfyUI has no selected image checkpoint; install one or choose a workflow recipe"
                ) from None
            recipes = [basic_image_recipe(checkpoint)]
        try:
            for recipe in recipes:
                recipe.preflight(node_info)
        except ValueError as error:
            raise AdapterError(str(error)) from None
        self._recipes = {recipe.model_id: recipe for recipe in recipes}
        return self._recipes

    def models(self, *, privacy="public", max_pages=100):
        if privacy == "private":
            return []
        if privacy != "public":
            raise ValueError("Choose public or private model visibility")
        return [recipe.model() for recipe in self._discover().values()]

    def prepare_model(self, model, parameters):
        target, payload = super().prepare_model(model, parameters)
        recipe = self._discover()[target]
        recipe.bind(self._bound_inputs(recipe, payload))
        return target, payload

    def _submit(self, operation, target_id, payload):
        if operation != "model":
            raise AdapterError("ComfyUI requires a configured workflow model")
        recipe = self._discover().get(target_id)
        if recipe is None:
            raise AdapterError("This workflow changed; refresh its configuration before generating")
        graph = recipe.bind(self._bound_inputs(recipe, payload))
        local_id = uuid.uuid4().hex
        request = {
            "modelId": target_id,
            "parameters": payload,
            "recipe": recipe.document,
            "status": "submitting",
            "requestId": local_id,
        }
        self.save_record("requests", local_id, request)
        reply = self._http.request_json("POST", "/prompt", {"prompt": graph, "client_id": local_id})
        try:
            remote = identifier(reply.get("prompt_id"))
            if reply.get("error") or reply.get("node_errors"):
                raise ValueError
        except (ValueError, AttributeError):
            raise AdapterError(
                "ComfyUI did not confirm an accepted prompt; nothing was submitted again"
            ) from None
        request.update(status="queued", jobId=remote)
        self.save_record("requests", local_id, request)
        self.save_record("comfy-prompts", remote, request)
        job = self._job(remote, request, "queued", [])
        self.save_record("jobs", remote, job)
        return job

    def _job(self, remote, request, status, assets):
        return {
            "jobId": remote,
            "jobType": "custom",
            "status": status,
            "metadata": {
                "input": {"modelId": request["modelId"], **request["parameters"]},
                "assetIds": assets,
            },
        }

    def job(self, remote):
        identifier(remote)
        request = self.load_record("comfy-prompts", remote)
        current = self.load_record("jobs", remote)
        if current.get("status") in {"success", "failure", "canceled"}:
            return current
        try:
            history = self.load_record("comfy-history", remote)
        except AdapterError:
            response = self._http.request_json("GET", f"/history/{remote}")
            history = response.get(remote) if isinstance(response, dict) else None
            status = history.get("status", {}) if isinstance(history, dict) else {}
            if isinstance(status, dict) and (
                status.get("completed") is True or status.get("status_str") in {"error", "failed"}
            ):
                self.save_record("comfy-history", remote, history)
        if not isinstance(history, dict):
            queue = self._http.request_json("GET", "/queue")
            active = self._queue_ids(queue, "queue_running")
            pending = self._queue_ids(queue, "queue_pending")
            if remote in active:
                return self._job(remote, request, "in-progress", [])
            if remote in pending:
                return self._job(remote, request, "queued", [])
            raise AdapterError(
                "ComfyUI no longer lists this prompt; inspect server history before retrying"
            )
        status = history.get("status", {})
        if not isinstance(status, dict):
            raise AdapterError("ComfyUI returned invalid prompt status")
        if status.get("status_str") in {"error", "failed"}:
            current = self._job(remote, request, "failure", [])
        elif status.get("completed") is True and status.get("status_str") == "success":
            assets = self._results(remote, request, history)
            current = self._job(remote, request, "success", assets)
        else:
            return self._job(remote, request, "in-progress", [])
        self.save_record("jobs", remote, current)
        return current

    def _results(self, remote, request, history):
        recipe = Recipe.from_dict(request["recipe"])
        outputs = history.get("outputs")
        if not isinstance(outputs, dict):
            raise AdapterError("ComfyUI returned no declared output data")
        results = []
        total = 0
        for binding_index, binding in enumerate(recipe.document["outputs"]):
            node = outputs.get(binding["node"], {})
            entries = node.get(binding["key"]) if isinstance(node, dict) else None
            if not isinstance(entries, list) or not entries or len(entries) > 128:
                raise AdapterError("ComfyUI output does not match its configured output binding")
            for index, entry in enumerate(entries):
                if len(results) >= 128:
                    raise AdapterError("ComfyUI produced too many output files")
                asset_id = (
                    "comfy-"
                    + hashlib.sha256(f"{remote}:{binding_index}:{index}".encode()).hexdigest()[:40]
                )
                try:
                    asset = self.asset(asset_id)
                    data = self.read_result(asset["url"])
                except AdapterError:
                    descriptor = self._descriptor(entry)
                    data = self._http.request_bytes(
                        "GET", "/view?" + urlencode(descriptor), max_bytes=MAX_RESULT_BYTES
                    )
                    asset = self.save_result(asset_id, data, binding["media_type"])
                    asset["kind"] = binding["kind"]
                    asset["metadata"]["provider"] = "comfyui"
                    asset["metadata"]["recipeId"] = recipe.model_id
                    role, projection = binding.get("role"), binding.get("projection")
                    if role:
                        asset["metadata"]["type"] = _ROLE_TYPES[role]
                    if projection:
                        asset["metadata"]["type"] = "skybox-base-360"
                    self.save_record("assets", asset_id, asset)
                total += len(data)
                if total > MAX_RESULT_BYTES:
                    raise AdapterError("ComfyUI combined outputs exceed the result limit")
                results.append(asset_id)
        return results

    @classmethod
    def validate_upload(cls, data, *, media_type, kind):
        super().validate_upload(data, media_type=media_type, kind=kind)
        if not image_signature_matches(data[:12], media_type):
            raise ValueError("Choose a PNG, JPEG or WebP reference image within 32 MiB")

    def stage_image(self, data, *, media_type="image/png"):
        """Upload exactly the reviewed reference bytes; never read caller paths."""
        self.validate_upload(data, media_type=media_type, kind="image")
        local_id = "comfy-upload-" + uuid.uuid4().hex
        asset = self.save_result(local_id, data, media_type)
        suffix = {"image/png": "png", "image/jpeg": "jpg", "image/webp": "webp"}[media_type]
        filename = f"{local_id}.{suffix}"
        boundary = "OpenStudio" + uuid.uuid4().hex
        body = (
            f'--{boundary}\r\nContent-Disposition: form-data; name="image"; filename="{filename}"\r\nContent-Type: {media_type}\r\n\r\n'.encode()
            + data
            + f'\r\n--{boundary}\r\nContent-Disposition: form-data; name="type"\r\n\r\ninput\r\n--{boundary}--\r\n'.encode()
        )
        raw = self._http.request_bytes(
            "POST",
            "/upload/image",
            body,
            content_type=f"multipart/form-data; boundary={boundary}",
            max_bytes=65536,
        )
        import json

        try:
            response = json.loads(raw)
            if not isinstance(response, dict) or response.get("type") != "input":
                raise ValueError
            descriptor = self._descriptor(
                {
                    "filename": response.get("name"),
                    "subfolder": response.get("subfolder", ""),
                    "type": "output",
                }
            )
        except (ValueError, AdapterError):
            raise AdapterError("ComfyUI did not confirm a valid uploaded reference") from None
        asset["metadata"]["comfy_input"] = "/".join(
            p for p in (descriptor["subfolder"], descriptor["filename"]) if p
        )
        self.save_record("assets", local_id, asset)
        return asset

    def _bound_inputs(self, recipe, payload):
        values = dict(payload)
        for entry in recipe.document["inputs"]:
            if entry["type"] not in {"file", "file_array"} or entry["name"] not in values:
                continue
            current = values[entry["name"]]
            references = current if entry["type"] == "file_array" else [current]
            bound = []
            for reference in references:
                if not isinstance(reference, str) or not reference.startswith("comfy-upload-"):
                    raise AdapterError(
                        "Upload this reference through the selected ComfyUI connection"
                    )
                asset = self.asset(reference)
                server_path = asset.get("metadata", {}).get("comfy_input")
                if not isinstance(server_path, str):
                    raise AdapterError("The ComfyUI reference upload has not completed")
                bound.append(server_path)
            values[entry["name"]] = bound if entry["type"] == "file_array" else bound[0]
        return values

    @staticmethod
    def _descriptor(entry):
        if not isinstance(entry, dict):
            raise AdapterError("ComfyUI returned an invalid output file descriptor")
        name, folder, kind = (
            entry.get("filename"),
            entry.get("subfolder", ""),
            entry.get("type", "output"),
        )
        if (
            not isinstance(name, str)
            or not name
            or len(name) > 255
            or any(c in name for c in "/\\:\x00")
            or name in {".", ".."}
            or not isinstance(folder, str)
            or len(folder) > 1024
            or "\\" in folder
            or ":" in folder
            or folder.startswith("/")
            or any(p in {".", ".."} for p in folder.split("/"))
            or any(ord(c) < 32 for c in name + folder)
            or kind not in {"output", "temp"}
        ):
            raise AdapterError("ComfyUI returned an unsafe output file descriptor")
        return {"filename": name, "subfolder": folder, "type": kind}

    @staticmethod
    def _queue_ids(queue, key):
        values = queue.get(key) if isinstance(queue, dict) else None
        if not isinstance(values, list):
            raise AdapterError("ComfyUI returned an invalid queue")
        identifiers = set()
        for row in values:
            if not isinstance(row, list) or len(row) < 2 or not isinstance(row[1], str):
                raise AdapterError("ComfyUI returned an invalid queue entry")
            identifiers.add(row[1])
        return identifiers

    def cancel_inference(self, remote):
        request = self.load_record("comfy-prompts", remote)
        queue = self._http.request_json("GET", "/queue")
        if remote in self._queue_ids(queue, "queue_running"):
            raise AdapterError(
                "Cancel this running prompt in ComfyUI; global interruption can stop another user's work"
            )
        if remote not in self._queue_ids(queue, "queue_pending"):
            raise AdapterError("This prompt is no longer queued; refresh its status")
        self._http.request_json("POST", "/queue", {"delete": [remote]})
        queue = self._http.request_json("GET", "/queue")
        if remote in self._queue_ids(queue, "queue_running") | self._queue_ids(
            queue, "queue_pending"
        ):
            raise AdapterError("ComfyUI did not confirm removal from its queue")
        # A queued item can race into execution/completion before deletion.
        history = self._http.request_json("GET", f"/history/{remote}")
        if remote in history:
            raise AdapterError(
                "This prompt completed while cancellation was requested; refresh its result"
            )
        self.save_record("jobs", remote, self._job(remote, request, "canceled", []))
        return {"jobId": remote}
