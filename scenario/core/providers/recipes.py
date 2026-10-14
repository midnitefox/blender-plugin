# SPDX-FileCopyrightText: 2026 Open Studio contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Bounded declarative Comfy API graphs; importing a recipe never installs code.

An API graph is interpreted only by the explicitly selected ComfyUI server.
Recipe bindings identify model inputs and output media semantics explicitly.
Mark exactly one string input with ``"prompt": true`` to connect the main
prompt box; its ``node`` and ``input`` select the graph's text destination.
"""

import copy
import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from ..schema.forms import prepare_run

MAX_RECIPE_BYTES = 2097152
CAPABILITIES = frozenset(
    {
        "txt2img",
        "img2img",
        "video2img",
        "txt2video",
        "img2video",
        "video2video",
        "audio2video",
        "txt23d",
        "img23d",
        "3d23d",
        "txt2audio",
        "audio2audio",
        "video2audio",
        "txt2text",
        "material",
        "panorama",
    }
)
KINDS = frozenset({"image", "video", "audio", "3d", "text", "material"})
ROLES = frozenset(
    {"base", "albedo", "normal", "roughness", "metallic", "height", "ao", "smoothness", "edge"}
)


def _label(value, *, limit=128):
    return (
        isinstance(value, str)
        and 0 < len(value) <= limit
        and not any(ord(c) < 32 or ord(c) == 127 for c in value)
    )


def _json_bytes(value):
    try:
        data = json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False).encode()
    except (TypeError, ValueError, RecursionError):
        raise ValueError("Workflow recipes must contain finite JSON values") from None
    if len(data) > MAX_RECIPE_BYTES:
        raise ValueError("Workflow recipe exceeds the 2 MiB limit")
    return data


@dataclass(frozen=True)
class Recipe:
    """Content-addressed recipe snapshot; later file edits cannot alter a quote."""

    document_json: bytes = field(repr=False)

    @classmethod
    def from_dict(cls, raw):
        if not isinstance(raw, dict) or raw.keys() - {
            "id",
            "name",
            "description",
            "capabilities",
            "graph",
            "inputs",
            "outputs",
            "edit3d_tasks",
            "panorama_mode",
        }:
            raise ValueError("Use a recipe object with graph, inputs and outputs")
        if not isinstance(raw.get("id"), str) or not re.fullmatch(
            r"[A-Za-z0-9_-]{1,64}", raw["id"]
        ):
            raise ValueError("Workflow recipe needs a portable ID")
        if not _label(raw.get("name"), limit=200):
            raise ValueError("Workflow recipe needs a name")
        caps = raw.get("capabilities")
        if (
            not isinstance(caps, list)
            or not caps
            or len(caps) > 20
            or any(not isinstance(c, str) or c not in CAPABILITIES for c in caps)
        ):
            raise ValueError("Declare supported workflow capabilities")
        graph = raw.get("graph")
        if not isinstance(graph, dict) or not 1 <= len(graph) <= 512:
            raise ValueError("Provide an API-format workflow graph with at most 512 nodes")
        links = {}
        for node_id, node in graph.items():
            if (
                not _label(node_id)
                or not isinstance(node, dict)
                or not _label(node.get("class_type"))
                or not isinstance(node.get("inputs"), dict)
            ):
                raise ValueError("Workflow graph nodes require class_type and inputs")
            links[node_id] = set()
            for value in node["inputs"].values():
                if (
                    isinstance(value, list)
                    and len(value) == 2
                    and isinstance(value[0], str)
                    and type(value[1]) is int
                ):
                    if value[0] not in graph or value[1] < 0:
                        raise ValueError("Workflow graph contains an invalid node connection")
                    links[node_id].add(value[0])
        # Remove leaf nodes iteratively so arbitrarily nested imported graphs
        # cannot exhaust Python recursion before the server receives them.
        while links:
            leaves = {node for node, parents in links.items() if not parents}
            if not leaves:
                raise ValueError("Workflow graph contains a cycle")
            links = {
                node: parents - leaves for node, parents in links.items() if node not in leaves
            }
        inputs = raw.get("inputs")
        if not isinstance(inputs, list) or len(inputs) > 128:
            raise ValueError("Declare workflow input bindings")
        names = set()
        locations = set()
        prompts = []
        for entry in inputs:
            if not isinstance(entry, dict):
                raise ValueError("Workflow inputs must be objects")
            name, node, key = entry.get("name"), entry.get("node"), entry.get("input")
            if (
                not _label(name)
                or name in names
                or not isinstance(node, str)
                or node not in graph
                or not isinstance(key, str)
                or key not in graph[node]["inputs"]
                or (node, key) in locations
            ):
                raise ValueError(
                    "Workflow inputs need unique names and existing node/input bindings"
                )
            if entry.get("type") not in {
                "string",
                "number",
                "boolean",
                "file",
                "file_array",
                "string_array",
                "number_array",
            }:
                raise ValueError("Choose a supported workflow input type")
            if type(entry.get("prompt", False)) is not bool:
                raise ValueError("Workflow prompt flags must be true or false")
            if entry.get("prompt"):
                if entry["type"] != "string":
                    raise ValueError("The workflow prompt binding must be a string input")
                prompts.append(name)
            names.add(name)
            locations.add((node, key))
        if len(prompts) > 1:
            raise ValueError("Mark only one workflow string input with prompt: true")
        if any(cap.startswith("txt2") for cap in caps) and not prompts:
            raise ValueError(
                "Text-driven workflows need one string input marked prompt: true, with node/input bindings"
            )
        outputs = raw.get("outputs")
        if not isinstance(outputs, list) or not 1 <= len(outputs) <= 128:
            raise ValueError("Declare at least one workflow output binding")
        for entry in outputs:
            if (
                not isinstance(entry, dict)
                or entry.get("node") not in graph
                or not _label(entry.get("key"))
                or entry.get("kind") not in KINDS
                or not _label(entry.get("media_type"))
                or "/" not in entry["media_type"]
            ):
                raise ValueError("Workflow outputs need node, key, kind and media_type bindings")
            if entry.get("role") is not None and entry["role"] not in ROLES:
                raise ValueError("Choose an explicit supported material map role")
            if entry.get("projection") is not None and entry["projection"] != "equirectangular":
                raise ValueError("Only equirectangular panorama projection is supported")
            if (entry.get("role") or entry.get("projection")) and not entry[
                "media_type"
            ].startswith("image/"):
                raise ValueError("Map roles and panorama projections require an image output")
        if "material" in caps and not any(o.get("role") for o in outputs):
            raise ValueError("Material recipes must declare output map roles")
        if "panorama" in caps and not any(
            o.get("projection") == "equirectangular" for o in outputs
        ):
            raise ValueError("Panorama recipes must declare their equirectangular projection")
        if "panorama" in caps:
            if raw.get("panorama_mode", "GENERATE") not in {"GENERATE", "FROM_IMAGE"}:
                raise ValueError("Choose a supported panorama generation mode")
            panoramas = [output for output in outputs if output.get("projection")]
            if any(
                output["media_type"]
                not in {
                    "image/png",
                    "image/jpeg",
                    "image/webp",
                    "image/tiff",
                    "image/gif",
                    "image/avif",
                }
                or output.get("role")
                for output in panoramas
            ):
                raise ValueError(
                    "Panorama recipes must declare LDR panorama images without material map roles"
                )
            if raw.get("panorama_mode") == "FROM_IMAGE" and not any(
                entry.get("type") in {"file", "file_array"}
                and entry.get("kind", "image") == "image"
                and entry.get("required") is True
                for entry in inputs
            ):
                raise ValueError("From-image panorama recipes require a typed image reference")
        tasks = raw.get("edit3d_tasks", [])
        if (
            not isinstance(tasks, list)
            or any(
                not isinstance(task, str)
                or task not in {"REMESH", "RETEXTURE", "UV", "RIG", "ANIMATE", "PARTS"}
                for task in tasks
            )
            or (tasks and "3d23d" not in caps)
        ):
            raise ValueError(
                "Mesh edit tasks require the 3d23d capability and supported task names"
            )
        return cls(_json_bytes(raw))

    @property
    def document(self):
        return json.loads(self.document_json)

    @property
    def model_id(self):
        return f"comfy_{self.document['id']}_{hashlib.sha256(self.document_json).hexdigest()[:20]}"

    def model(self):
        raw = self.document
        inputs = [
            {key: value for key, value in entry.items() if key not in {"node", "input"}}
            for entry in raw["inputs"]
        ]
        model = {
            "id": self.model_id,
            "name": raw["name"],
            "type": "custom",
            "status": "active",
            "privacy": "public",
            "shortDescription": raw.get("description", "Configured ComfyUI workflow"),
            "capabilities": raw["capabilities"],
            "inputs": inputs,
            "provider": "comfyui",
            "openStudio": {
                "lanes": [cap for cap in raw["capabilities"] if cap in {"material", "panorama"}],
                "edit3dTasks": raw.get("edit3d_tasks", []),
            },
        }
        if "panorama" in raw["capabilities"]:
            references = []
            for entry in inputs:
                if (
                    entry["type"] not in {"file", "file_array"}
                    or entry.get("kind", "image") != "image"
                ):
                    continue
                maximum = (
                    entry.get("maxLength", entry.get("max_length", 16))
                    if entry["type"] == "file_array"
                    else 1
                )
                if type(maximum) is not int or not 1 <= maximum <= 16:
                    raise ValueError(
                        "Panorama reference arrays need an explicit limit from 1 to 16"
                    )
                references.append(
                    {
                        "parameter": entry["name"],
                        "kind": "still",
                        "required": entry.get("required") is True,
                        "max": maximum,
                    }
                )
            model["openStudio"]["panorama"] = {
                "projection": "equirectangular",
                "mode": raw.get("panorama_mode", "GENERATE"),
                "output": "ldr",
                "references": references,
            }
        return model

    def preflight(self, node_info):
        if not isinstance(node_info, dict):
            raise ValueError("ComfyUI returned no node definitions")
        missing = sorted(
            {node["class_type"] for node in self.document["graph"].values()} - node_info.keys()
        )
        if missing:
            raise ValueError("ComfyUI is missing required nodes: " + ", ".join(missing))
        raw = self.document
        bound = {(entry["node"], entry["input"]) for entry in raw["inputs"]}
        for node_id, node in raw["graph"].items():
            info = node_info.get(node["class_type"])
            definitions = info.get("input", {}) if isinstance(info, dict) else {}
            required = definitions.get("required", {}) if isinstance(definitions, dict) else {}
            if not isinstance(required, dict):
                raise ValueError("ComfyUI returned invalid node input definitions")
            for name, definition in required.items():
                if name not in node["inputs"]:
                    raise ValueError(f"ComfyUI workflow is missing the required input: {name}")
                value = node["inputs"][name]
                if (node_id, name) in bound or isinstance(value, list):
                    continue
                # Combo widgets publish their available values (checkpoints,
                # samplers and model files) as the first definition element.
                if (
                    isinstance(definition, list)
                    and definition
                    and isinstance(definition[0], list)
                    and value not in definition[0]
                ):
                    raise ValueError(
                        f"ComfyUI lacks the configured model or choice for input: {name}"
                    )

    def bind(self, parameters):
        raw = self.document
        _, values = prepare_run(self.model_id, {"parameters": self.model()["inputs"]}, parameters)
        graph = copy.deepcopy(raw["graph"])
        for entry in raw["inputs"]:
            if entry["name"] in values:
                graph[entry["node"]]["inputs"][entry["input"]] = values[entry["name"]]
        return graph


def load_recipes(path):
    """Read exactly the selected JSON file, bounded; never scan or execute it."""
    target = Path(path)
    if target.suffix.lower() != ".json" or not target.is_file():
        raise ValueError("Choose an existing JSON workflow recipe file")
    try:
        with target.open("rb") as source:
            data = source.read(MAX_RECIPE_BYTES + 1)
        if len(data) > MAX_RECIPE_BYTES:
            raise ValueError
        document = json.loads(data)
    except (OSError, ValueError, RecursionError):
        raise ValueError("Workflow recipe file must be valid JSON within 2 MiB") from None
    if (
        not isinstance(document, dict)
        or document.get("version") != 1
        or not isinstance(document.get("recipes"), list)
        or not 1 <= len(document["recipes"]) <= 128
    ):
        raise ValueError("Use a version 1 recipe file containing a recipes list")
    recipes = [Recipe.from_dict(raw) for raw in document["recipes"]]
    if len({recipe.document["id"] for recipe in recipes}) != len(recipes):
        raise ValueError("Recipe IDs must be unique")
    return recipes


def basic_image_recipe(checkpoint):
    """Core-node preset following ComfyUI's published basic API example."""
    if not _label(checkpoint, limit=1024):
        raise ValueError("Choose an installed checkpoint")
    return Recipe.from_dict(
        {
            "id": "basic-image",
            "name": "Basic image (installed checkpoint)",
            "description": "Core ComfyUI image workflow. Checkpoint architecture must support CLIP and VAE.",
            "capabilities": ["txt2img"],
            "graph": {
                "3": {
                    "class_type": "KSampler",
                    "inputs": {
                        "cfg": 7.0,
                        "denoise": 1.0,
                        "latent_image": ["5", 0],
                        "model": ["4", 0],
                        "negative": ["7", 0],
                        "positive": ["6", 0],
                        "sampler_name": "euler",
                        "scheduler": "normal",
                        "seed": 0,
                        "steps": 20,
                    },
                },
                "4": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": checkpoint}},
                "5": {
                    "class_type": "EmptyLatentImage",
                    "inputs": {"batch_size": 1, "height": 512, "width": 512},
                },
                "6": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["4", 1], "text": ""}},
                "7": {"class_type": "CLIPTextEncode", "inputs": {"clip": ["4", 1], "text": ""}},
                "8": {"class_type": "VAEDecode", "inputs": {"samples": ["3", 0], "vae": ["4", 2]}},
                "9": {
                    "class_type": "SaveImage",
                    "inputs": {"images": ["8", 0], "filename_prefix": "OpenStudio"},
                },
            },
            "inputs": [
                {
                    "name": "prompt",
                    "type": "string",
                    "prompt": True,
                    "required": True,
                    "node": "6",
                    "input": "text",
                },
                {
                    "name": "negative_prompt",
                    "type": "string",
                    "default": "",
                    "node": "7",
                    "input": "text",
                },
                {
                    "name": "seed",
                    "type": "number",
                    "default": 0,
                    "min": 0,
                    "max": 2147483647,
                    "step": 1,
                    "node": "3",
                    "input": "seed",
                },
                {
                    "name": "steps",
                    "type": "number",
                    "default": 20,
                    "min": 1,
                    "max": 100,
                    "step": 1,
                    "node": "3",
                    "input": "steps",
                },
                {
                    "name": "width",
                    "type": "number",
                    "default": 512,
                    "min": 64,
                    "max": 2048,
                    "step": 64,
                    "node": "5",
                    "input": "width",
                },
                {
                    "name": "height",
                    "type": "number",
                    "default": 512,
                    "min": 64,
                    "max": 2048,
                    "step": 64,
                    "node": "5",
                    "input": "height",
                },
            ],
            "outputs": [{"node": "9", "key": "images", "kind": "image", "media_type": "image/png"}],
        }
    )
