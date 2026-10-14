# SPDX-FileCopyrightText: 2026 Scenario Inc.
# SPDX-License-Identifier: GPL-3.0-or-later
"""Verified request contracts for the Environment lane's 360 panorama models.

Each contract names the model inputs it depends on, forced and lane-default
values, 2:1 size presets and the kind of image each reference input takes. They
were checked against recorded `GET /models/{id}` records and zero-spend dry-run
quotes. `prepare` rechecks the current schema before applying a contract and
fails closed when it changed. The module is pure: it never reads files, calls
Scenario or touches Blender, so the native form and local MCP share it.
"""

from dataclasses import dataclass

from ..jobs.result_metadata import EXR_ORIGINAL_TYPES
from .panorama import MAX_PIXELS

GENERATE = "GENERATE"
FROM_IMAGE = "FROM_IMAGE"
HDRI = "HDRI"
UPSCALE = "UPSCALE"
PANORAMA_MODES = (GENERATE, FROM_IMAGE, HDRI, UPSCALE)
MODE_LABELS = {
    GENERATE: "Generate",
    FROM_IMAGE: "From image",
    HDRI: "HDRI",
    UPSCALE: "Upscale",
}
MODE_DESCRIPTIONS = {
    GENERATE: "Describe a 360 environment, or restyle a saved 360 panorama",
    FROM_IMAGE: "Turn one image of a place into a 360 panorama",
    HDRI: "Convert a saved 360 panorama into an HDR OpenEXR environment",
    UPSCALE: "Upscale a saved 360 panorama within the World pixel limit",
}
# Saved OpenEXR files are HDR environments, never LDR panorama inputs.
HDR_MEDIA_TYPES = EXR_ORIGINAL_TYPES | {"image/exr"}

STILL = "still"
PANORAMA = "panorama"
CONTRACT_CHANGED = "This panorama model's contract changed; update the extension"
# A saved reference's pixel size is not stored. Without it, a 2x factor keeps a
# 2:1 panorama up to 4096 pixels wide within the World pixel limit.
UNKNOWN_SIZE_FACTOR = 2


@dataclass(frozen=True)
class ReferenceRule:
    """One file input: `still` takes any image asset, `panorama` a saved 360 result."""

    param: str
    kind: str
    required: bool = False
    max_count: int = 1


@dataclass(frozen=True)
class PanoramaRef:
    """A saved LDR 360 result that the caller found in the selected scope.

    `producer` is the model that generated it. Width and height are known only
    for references whose bytes the caller inspected.
    """

    asset_id: str
    producer: str = ""
    width: int | None = None
    height: int | None = None


@dataclass(frozen=True)
class PanoramaContract:
    model_id: str
    mode: str
    output: str  # "ldr" or "hdr"
    references: tuple = ()
    locked: tuple = ()  # (input, value) pairs forced on every request
    defaults: tuple = ()  # (input, value) pairs used when the caller sets none
    sizes: tuple = ()  # allowed (width, height) presets; the first is the default
    size_params: tuple = ()  # (width input, height input)
    factor_param: str = ""
    extra_fields: tuple = ()  # other inputs the contract assumes

    @property
    def required_fields(self):
        names = {rule.param for rule in self.references}
        names.update(name for name, _ in self.locked)
        names.update(name for name, _ in self.defaults)
        names.update(self.size_params)
        names.update(self.extra_fields)
        if self.factor_param:
            names.add(self.factor_param)
        return frozenset(names)


CONTRACTS = {
    contract.model_id: contract
    for contract in (
        PanoramaContract(
            "model_scenario-skybox-flux",
            GENERATE,
            "ldr",
            references=(ReferenceRule("image", PANORAMA),),
            # The schema default makes two panoramas and doubles the quote.
            defaults=(("numOutputs", 1),),
            extra_fields=("prompt",),
        ),
        PanoramaContract(
            "model_scenario-skybox-gpt",
            GENERATE,
            "ldr",
            references=(ReferenceRule("referenceImages", STILL, max_count=10),),
            # Free width and height accept any ratio, and the schema's presets
            # include cubemap layouts; only these 2:1 sizes are offered.
            sizes=((2048, 1024), (3072, 1536), (3840, 1920)),
            size_params=("width", "height"),
            extra_fields=("prompt",),
        ),
        PanoramaContract(
            "model_hunyuan-world-image-to-skybox",
            FROM_IMAGE,
            "ldr",
            references=(ReferenceRule("image", STILL, required=True),),
        ),
        PanoramaContract(
            "model_hdri-skybox",
            HDRI,
            "hdr",
            references=(ReferenceRule("image", PANORAMA, required=True),),
            # The extension saves the OpenEXR original; Radiance and cubemap
            # outputs would only add files it cannot apply.
            locked=(
                ("outputExr", True),
                ("outputHdr", False),
                ("conversionCubemapCross", False),
                ("conversionCubemapFaces", False),
            ),
        ),
        PanoramaContract(
            "model_sc-upscale-flux-skybox",
            UPSCALE,
            "ldr",
            references=(
                ReferenceRule("image", PANORAMA, required=True),
                ReferenceRule("imagePrompt", STILL, max_count=4),
            ),
            factor_param="upscaleFactor",
        ),
    )
}
PANORAMA_MODELS = tuple(CONTRACTS)
# Every 2:1 size preset a contract offers, in order; the first is the default.
SIZE_PRESETS = tuple(dict.fromkeys(size for c in CONTRACTS.values() for size in c.sizes))


def contract_for(model_id):
    return CONTRACTS.get(model_id)


def reference_rule(contract, param):
    """The contract's rule for one file input, or None when it names no such input."""
    return next((rule for rule in contract.references if rule.param == param), None)


def is_ldr_panorama(item):
    """Whether one saved result item can be a panorama reference.

    It must be downloaded, declare an equirectangular projection in Scenario's
    saved metadata and be the asset's own file; an OpenEXR original or file is
    an HDR environment, not an input image.
    """
    asset = item.asset
    return (
        item.receipt is not None
        and asset.projection == "equirectangular"
        and asset.source == "asset"
        and asset.media_type not in HDR_MEDIA_TYPES
    )


def saved_panorama_refs(records, wanted):
    """Saved, downloaded LDR 360 results among `wanted` asset IDs, by asset ID.

    `records` are the selected scope's saved jobs. Projection comes only from
    their saved result metadata, so other asset IDs are never assumed to be
    panoramas. The native form and local MCP share this check.
    """
    refs = {}
    for record in records:
        for item in record.results:
            if item.asset.asset_id in wanted and is_ldr_panorama(item):
                refs[item.asset.asset_id] = PanoramaRef(
                    item.asset.asset_id, producer=record.intent.target_id
                )
    return refs


def models_for_mode(mode, records):
    """Records of one mode's contract models, in the given (curated) order."""
    return [
        record
        for record in records
        if (contract := CONTRACTS.get(record.id)) is not None and contract.mode == mode
    ]


def describe(contract):
    """The result text shared by the native form and MCP."""
    if contract.output == "hdr":
        result = "HDR OpenEXR environment behind an LDR preview"
    else:
        result = "LDR 360 panorama image (2:1 equirectangular)"
    return f"{result}; it stays a saved result until you approve it as the scene World"


def result_line(contract):
    """The short result line of the native form; LDR output is never called HDR."""
    if contract.output == "hdr":
        return "Result: HDR OpenEXR environment, saved until you set it as World"
    return "Result: LDR 2:1 panorama, saved until you set it as World"


def summary(contract):
    """JSON-safe contract details for MCP list_models."""
    return {
        "mode": contract.mode,
        "output": contract.output,
        "description": describe(contract),
        "references": [
            {
                "parameter": rule.param,
                "kind": rule.kind,
                "required": rule.required,
                "max": rule.max_count,
            }
            for rule in contract.references
        ],
        "sizes": [f"{width}x{height}" for width, height in contract.sizes],
        "locked": dict(contract.locked),
        "defaults": dict(contract.defaults),
        "factor_parameter": contract.factor_param or None,
    }


def _number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _allowed(spec, value):
    """Whether the current schema still accepts a forced or default value of this type."""
    if isinstance(value, bool):
        expected = "boolean"
    elif _number(value):
        expected = "number"
    else:
        expected = "string"
    if spec.ptype != expected:
        return False
    if spec.allowed_values:
        return value in spec.allowed_values
    if expected == "number":
        return (spec.min is None or value >= spec.min) and (spec.max is None or value <= spec.max)
    return True


def _drifted(contract, by_name):
    if contract.required_fields - by_name.keys():
        return True
    for rule in contract.references:
        spec = by_name[rule.param]
        expected = "file_array" if rule.max_count > 1 else "file"
        if spec.ptype != expected or (spec.kind or "image") != "image":
            return True
    for name, value in (*contract.locked, *contract.defaults):
        if not _allowed(by_name[name], value):
            return True
    for name in (*contract.size_params, contract.factor_param):
        if name and by_name[name].ptype != "number":
            return True
    for size in contract.sizes:
        for name, value in zip(contract.size_params, size, strict=True):
            spec = by_name[name]
            if not _allowed(spec, value) or (spec.step and value % spec.step):
                return True
    return False


def _identifiers(value):
    if value is None:
        return []
    values = list(value) if isinstance(value, (list, tuple)) else [value]
    return values if all(isinstance(item, str) and item.strip() for item in values) else None


def _factor_cap(ref, spec):
    """Largest factor that keeps the result within the World pixel limit, or an error."""
    highest = int(spec.max) if spec.max is not None else UNKNOWN_SIZE_FACTOR
    lowest = int(spec.min) if spec.min is not None else 1
    if ref.width and ref.height:
        factor = 0
        while factor < highest and (factor + 1) ** 2 * ref.width * ref.height <= MAX_PIXELS:
            factor += 1
        if factor < lowest:
            return None, "This panorama is too large to upscale within the 32-megapixel World limit"
        return factor, None
    producer = CONTRACTS.get(ref.producer)
    if producer is not None and producer.mode == UPSCALE:
        return None, (
            "An upscaled panorama's size is unknown here; upscale its source panorama "
            "with a larger factor instead"
        )
    return min(highest, UNKNOWN_SIZE_FACTOR), None


def prepare(contract, specs, body, *, panorama_refs=None):
    """Apply a contract to a built request body. Returns (body, errors).

    `specs` are the ParamSpecs of the model's current GET /models schema. A
    missing input, changed input type or forced value the schema no longer
    allows returns CONTRACT_CHANGED and nothing else. Panorama references must
    be keys of `panorama_refs` (asset id -> PanoramaRef); callers include only
    saved 360 results of the selected scope, so other IDs are refused rather
    than guessed. The schema's own validation still runs afterwards.
    """
    by_name = {spec.name: spec for spec in specs}
    body = dict(body)
    if _drifted(contract, by_name):
        return body, [CONTRACT_CHANGED]
    refs = panorama_refs or {}
    errors = []
    for name, value in contract.locked:
        current = body.get(name)
        if current is not None and (type(current) is not type(value) or current != value):
            errors.append(f"{by_name[name].label} is fixed to {value} in the Environment lane")
        body[name] = value
    for name, value in contract.defaults:
        if body.get(name) is None:
            body[name] = value
    if contract.sizes:
        width_name, height_name = contract.size_params
        chosen = (body.get(width_name), body.get(height_name))
        if chosen == (None, None):
            chosen = contract.sizes[0]
        preset = next((size for size in contract.sizes if size == chosen), None)
        if preset is None:
            options = ", ".join(f"{w}x{h}" for w, h in contract.sizes)
            errors.append(f"Choose a 2:1 panorama size: {options}")
        else:
            body[width_name], body[height_name] = preset
    panorama_input = None
    for rule in contract.references:
        spec = by_name[rule.param]
        ids = _identifiers(body.get(rule.param))
        if ids is None:
            errors.append(f"{spec.label}: use Scenario asset IDs")
            continue
        limit = min(rule.max_count, spec.max_length or rule.max_count)
        if rule.required and not ids:
            noun = "a saved 360 panorama" if rule.kind == PANORAMA else "an image"
            errors.append(f"{spec.label}: add {noun}")
        if len(ids) > limit:
            errors.append(f"{spec.label}: use at most {limit}")
        if rule.kind == PANORAMA:
            unknown = [item for item in ids if item not in refs]
            if unknown:
                errors.append(
                    f"{spec.label}: use a saved, downloaded 360 panorama result from this "
                    "account and project"
                )
            elif ids:
                panorama_input = refs[ids[0]]
    if contract.factor_param:
        spec = by_name[contract.factor_param]
        factor = body.get(contract.factor_param)
        if factor is None:
            factor = spec.default
        if panorama_input is not None and _number(factor):
            cap, problem = _factor_cap(panorama_input, spec)
            if problem:
                errors.append(problem)
            elif factor > cap:
                errors.append(
                    f"{spec.label} must be at most {cap} so the result stays within the "
                    "32-megapixel World limit"
                )
            body[contract.factor_param] = factor
    return body, errors
