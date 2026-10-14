# SPDX-FileCopyrightText: 2026 Open Studio contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Provider provenance grants configured lanes without changing Scenario filtering."""

from copy import deepcopy

import pytest

from scenario.core.api.catalog import ModelRecord, edit3d_models, models_for_lane
from scenario.core.scene import panorama_contract
from scenario.core.schema.params import parse_schema


def model(**changes):
    return {
        "id": "provider-model",
        "name": "Provider model",
        "type": "custom",
        "status": "active",
        "capabilities": ["txt2img"],
        "inputs": [{"name": "prompt", "type": "string", "required": True}],
        "openStudio": {"lanes": [], "edit3dTasks": []},
    } | changes


def panorama(**changes):
    declaration = {
        "projection": "equirectangular",
        "mode": "GENERATE",
        "output": "ldr",
        "references": [],
    } | changes
    return model(openStudio={"lanes": ["panorama"], "panorama": declaration})


def test_active_requires_explicit_provider_provenance():
    raw = model(provider="comfyui")
    legacy = ModelRecord.from_api(raw)
    selected = ModelRecord.from_api(raw, provider="comfyui")
    assert models_for_lane("image", [legacy]) == []
    assert models_for_lane("image", [selected]) == [selected]
    with pytest.raises(ValueError):
        ModelRecord.from_api(raw, provider="unknown")


def test_material_and_edit_tasks_are_provider_declared():
    raw = model(openStudio={"lanes": ["material"]})
    material = ModelRecord.from_api(raw, provider="comfyui")
    assert models_for_lane("material", [material]) == [material]
    assert models_for_lane("render_image", [material]) == []
    assert models_for_lane("material", [ModelRecord.from_api(raw)]) == []
    mesh = ModelRecord.from_api(
        model(capabilities=["3d23d"], openStudio={"edit3dTasks": ["RIG"]}), provider="fal"
    )
    assert edit3d_models("RIG", [mesh]) == [mesh]
    assert edit3d_models("REMESH", [mesh]) == []
    assert edit3d_models("ALL", [mesh]) == [mesh]


def test_only_explicit_valid_provider_panorama_contract_is_listed():
    selected = ModelRecord.from_api(panorama(), provider="comfyui")
    ordinary = ModelRecord.from_api(model(name="360 HDR panorama 2048x1024"), provider="comfyui")
    assert models_for_lane("panorama", [selected, ordinary]) == [selected]
    assert panorama_contract.contract_for(selected.id, selected).mode == "GENERATE"
    assert panorama_contract.contract_for(ordinary.id, ordinary) is None
    assert panorama_contract.contract_for(selected.id, ModelRecord.from_api(panorama())) is None
    assert panorama_contract.contract_for(selected.id, panorama()) is None
    assert panorama_contract.models_for_mode("GENERATE", [selected]) == [selected]
    assert panorama_contract.models_for_mode("HDRI", [selected]) == []


@pytest.mark.parametrize(
    "changes",
    [
        {"projection": "cubemap"},
        {"output": "hdr"},
        {"mode": "UPSCALE"},
        {"references": [{"parameter": "missing", "kind": "still", "required": True, "max": 1}]},
    ],
)
def test_invalid_provider_panorama_contract_fails_closed(changes):
    selected = ModelRecord.from_api(panorama(**changes), provider="comfyui")
    assert panorama_contract.contract_for(selected.id, selected) is None
    assert models_for_lane("panorama", [selected]) == []


def test_provider_reference_contract_rechecks_typed_schema():
    raw = panorama(
        mode="FROM_IMAGE",
        references=[{"parameter": "image", "kind": "still", "required": True, "max": 1}],
    )
    raw["inputs"].append({"name": "image", "type": "file", "kind": "image", "required": True})
    selected = ModelRecord.from_api(raw, provider="comfyui")
    contract = panorama_contract.contract_for(selected.id, selected)
    assert contract is not None
    body, errors = panorama_contract.prepare(
        contract, parse_schema(selected).specs, {"prompt": "forest", "image": "uploaded-reference"}
    )
    assert errors == []
    assert body["image"] == "uploaded-reference"
    changed = deepcopy(raw)
    changed["inputs"][-1]["kind"] = "3d"
    assert (
        panorama_contract.contract_for(
            selected.id, ModelRecord.from_api(changed, provider="comfyui")
        )
        is None
    )


def test_inactive_provider_is_not_made_available_by_lane_metadata():
    record = ModelRecord.from_api(
        model(status="disabled", openStudio={"lanes": ["image"]}), provider="comfyui"
    )
    assert models_for_lane("image", [record]) == []
