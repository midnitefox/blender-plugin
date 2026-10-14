# SPDX-FileCopyrightText: 2026 Scenario Inc.
# SPDX-License-Identifier: GPL-3.0-or-later
import json
import math
import struct

from conftest import FIXTURES

from scenario.core.api.catalog import ModelRecord
from scenario.core.schema.params import (
    build_body,
    float32_decimal,
    missing_required_files,
    parse_schema,
    validate,
)


def record(name):
    return ModelRecord.from_api(
        json.loads((FIXTURES / "models" / f"{name}.json").read_text())["model"]
    )


def spec_by_name(schema, name):
    return next(s for s in schema.specs if s.name == name)


def test_parse_patina_schema():
    schema = parse_schema(record("model_patina-material"))
    assert schema.prompt_name == "prompt"
    prompt = spec_by_name(schema, "prompt")
    assert prompt.required_always and prompt.is_prompt and prompt.max_length == 2048
    width = spec_by_name(schema, "width")
    assert (
        width.ptype == "number"
        and width.is_integer
        and (width.min, width.max, width.step) == (512, 2048, 16)
    )
    assert width.cost_impact and width.group == "Settings"
    maps = spec_by_name(schema, "maps")
    assert maps.ptype == "string_array" and maps.is_array
    assert maps.allowed_values == ("basecolor", "normal", "roughness", "metalness", "height")
    assert maps.allowed_labels["basecolor"] == "Base Color"
    upscale = spec_by_name(schema, "upscaleFactor")
    assert upscale.allowed_values == (0, 2, 4) and upscale.allowed_labels[0] == "None"
    mask = spec_by_name(schema, "mask")
    assert mask.is_file and mask.kind == "image" and not mask.is_array
    image = spec_by_name(schema, "image")
    assert image.required_if_defined == ("mask",)
    assert [p["label"] for p in schema.resolution_presets][:2] == [
        "1:1 (512x512)",
        "1:1 (1024x1024)",
    ]


def test_parse_gemini_file_array_and_enum():
    schema = parse_schema(record("model_google-gemini-3-1-flash"))
    refs = spec_by_name(schema, "referenceImages")
    assert refs.is_file and refs.is_array and refs.kind == "image"
    res = spec_by_name(schema, "resolution")
    assert res.allowed_values == ("512", "1K", "2K", "4K") and res.default == "1K"


def test_build_body_matches_recorded_job_input():
    schema = parse_schema(record("model_patina-material"))
    job = json.loads((FIXTURES / "patina-copper-512" / "job.json").read_text())["job"]
    recorded = dict(job["metadata"]["input"])
    recorded.pop("modelId")
    recorded.pop("seed")
    values = {
        "prompt": "weathered copper patina with verdigris streaks",
        "width": 512.0,
        "height": 512.0,
        "maps": ["basecolor", "normal", "roughness", "metalness", "height"],
        "numOutputs": 1.0,
        "upscaleFactor": None,
        "tilingMode": "",
        "seed": None,
    }
    body = build_body(schema.specs, values, files={})
    assert body == recorded


def test_build_body_files_scalar_vs_array_and_disabled_optionals():
    schema = parse_schema(record("model_google-gemini-3-1-flash"))
    body = build_body(
        schema.specs,
        {"prompt": "x", "resolution": "2K", "numOutputs": 2},
        files={"referenceImages": ["asset_a", "asset_b"]},
        enabled={"resolution": False},
    )
    assert body["referenceImages"] == ["asset_a", "asset_b"]
    assert "resolution" not in body and body["numOutputs"] == 2
    patina = parse_schema(record("model_patina-material"))
    body = build_body(patina.specs, {"prompt": "x"}, files={"image": ["asset_1"]})
    assert body["image"] == "asset_1"


def test_validate_rules():
    schema = parse_schema(record("model_patina-material"))
    assert validate(schema.specs, {}) == ["Prompt is required"]
    errors = validate(
        schema.specs,
        {"prompt": "x" * 3000, "width": 100, "tilingMode": "diagonal", "mask": "asset_m"},
    )
    assert "Prompt is longer than 2048 characters" in errors
    assert "Width must be between 512 and 2048" in errors
    assert "Tiling Mode must be one of both, horizontal, vertical" in errors
    assert "Image is required when Mask is set" in errors
    assert validate(schema.specs, {"prompt": "ok"}) == []


def test_missing_required_files():
    schema = parse_schema(record("model_patina"))
    assert missing_required_files(schema.specs, {}) == ["image"]
    assert missing_required_files(schema.specs, {"image": "asset_x"}) == []


def test_conditional_required_file_becomes_either_or():
    # Rodin Bang marks its reference image `required: true` but its description says "if no prompt is provided":
    # the plugin must relax it and require at least one of {image, prompt}, so the prompt-only path works.
    schema = parse_schema(record("model_rodin-hyper3d-bang"))
    image = spec_by_name(schema, "image")
    model = spec_by_name(schema, "model")
    assert image.is_file and not image.required_always  # relaxed: no longer a hard requirement
    assert model.required_always  # the mesh input stays required
    assert schema.one_of == [("image", "prompt")]
    # with the mesh present, nothing is a missing required file (the reference image is optional now)
    assert missing_required_files(schema.specs, {"model": "asset_mesh"}) == []
    assert missing_required_files(schema.specs, {}) == ["model"]


def test_either_or_validation():
    schema = parse_schema(record("model_rodin-hyper3d-bang"))
    mesh = {"model": "asset_mesh"}
    # prompt only (the reported bug: this used to fail with "Reference Image is required")
    assert validate(schema.specs, {**mesh, "prompt": "weathered bronze"}, schema.one_of) == []
    # reference image only
    assert validate(schema.specs, {**mesh, "image": "asset_ref"}, schema.one_of) == []
    # neither: one friendly either/or error, naming both options
    errors = validate(schema.specs, mesh, schema.one_of)
    assert errors == ["Provide one of: Reference Image or Texture Prompt"]


def test_if_not_defined_becomes_either_or():
    # Cartwheel Text to Motion: characterModel (3D mesh) and characterFile (reference image) each declare
    # required:{ifNotDefined:{the other}}. Neither is required on its own; at least one must be provided.
    schema = parse_schema(record("model_cartwheel-text-to-motion"))
    model = spec_by_name(schema, "characterModel")
    image = spec_by_name(schema, "characterFile")
    assert model.kind == "3d" and not model.required_always
    assert image.kind == "image" and not image.required_always
    assert schema.one_of == [("characterFile", "characterModel")]
    base = {"prompt": "walk"}
    assert validate(schema.specs, {**base, "characterModel": "asset_mesh"}, schema.one_of) == []
    assert validate(schema.specs, {**base, "characterFile": "asset_img"}, schema.one_of) == []
    assert validate(schema.specs, base, schema.one_of) == [
        "Provide one of: Reference Character Image or Character 3D Model"
    ]


def test_optional_character_mesh_input_is_recognised():
    # Uthana Text to Motion is txt23d but accepts an optional character mesh (characterFile, kind 3d): the plugin
    # must recognise it as a mesh input so the 3D lane can offer the selected scene mesh.
    from scenario.core.api.catalog import mesh_param

    rec = record("model_uthana-text-to-motion-3.0")
    schema = parse_schema(rec)
    char = spec_by_name(schema, "characterFile")
    assert char.kind == "3d" and char.is_file and not char.required_always
    assert mesh_param(rec) == "characterFile"


def _stored(value):
    """What Blender's 32-bit FloatProperty reads back for `value`."""
    return struct.unpack("<f", struct.pack("<f", value))[0]


def test_float32_readings_are_sent_as_the_decimal_the_form_shows():
    assert _stored(0.8) == 0.800000011920929
    for shown in (0.8, 0.7, 0.05, 3.5, -2.3, 1e-07, 123456.7, 0.01):
        sent = float32_decimal(_stored(shown))
        assert sent == shown, shown
        assert _stored(sent) == _stored(shown)  # still the very value Blender holds
        assert json.dumps(sent) == json.dumps(shown)
    assert float32_decimal(_stored(1 / 3)) == 0.33333334


def test_float32_decimal_leaves_whole_exact_and_other_values_alone():
    assert float32_decimal(1104.0) == 1104.0
    assert float32_decimal(_stored(2147483647.0)) == 2147483648.0  # whole numbers stay exact
    assert float32_decimal(0.1 + 0.2) == 0.1 + 0.2  # not a 32-bit reading
    assert float32_decimal(1e39) == 1e39  # beyond the 32-bit range
    assert math.isinf(float32_decimal(math.inf))
    assert math.isnan(float32_decimal(math.nan))
    assert float32_decimal(3) == 3.0 and type(float32_decimal(3)) is float
