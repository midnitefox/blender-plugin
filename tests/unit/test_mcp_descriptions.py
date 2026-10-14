# SPDX-FileCopyrightText: 2026 Scenario Inc.
# SPDX-License-Identifier: GPL-3.0-or-later
"""Check the actual MCP tool contract without importing Blender."""

import ast
import re
from pathlib import Path

import pytest

from scenario.core.ui.capability_status import UNACCEPTED_CAPABILITIES

ROOT = Path(__file__).resolve().parents[2]
EXPECTED = {
    "tools_scenario": {
        "list_assets",
        "search_assets",
        "list_collections",
        "prepare_asset_organization",
        "apply_asset_organization",
        "asset_organization_status",
        "list_workflows",
        "workflow_schema",
        "estimate_workflow",
        "run_workflow",
        "discard_workflow_estimate",
        "workflow_job_steps",
        "decide_workflow_step",
        "prepare_film_composition",
        "film_composition_review",
        "estimate_film_composition",
        "generate_film_composition",
        "prepare_film_review",
        "film_review_status",
        "build_film_review",
        "film_export_sources",
        "prepare_film_export",
        "render_film_export",
        "film_export_review",
        "film_case_study_sources",
        "prepare_film_case_study",
        "export_film_case_study",
        "film_case_study_review",
        "film_capture_sources",
        "prepare_film_capture",
        "render_film_capture",
        "film_capture_review",
        "upload_film_capture",
        "film_shot_sources",
        "film_timeline_sources",
        "prepare_film_timeline",
        "film_timeline_review",
        "build_film_timeline",
        "prepare_film_shot",
        "film_shot_review",
        "build_film_shot",
        "film_recipe",
        "estimate_film_task",
        "approve_film_task",
        "discard_film_estimate",
        "bind_film_upload",
        "list_local_jobs",
        "cancel_prepared_job",
        "recover_local_job",
        "recover_cloud_job",
        "prepare_result_application",
        "apply_result_application",
        "list_models",
        "model_schema",
        "model_defaults",
        "estimate_cost",
        "render_form",
        "estimate_prompt",
        "estimate_blockout",
        "approve_blockout",
        "read_model_text",
        "inspect_result_previews",
        "prepare_blockout_plan",
        "blockout_plan_status",
        "apply_blockout_plan",
        "approve_prompt",
        "read_prompt_result",
        "generate",
        "job_status",
        "wait_for_job",
        "import_result",
        "capture_reference",
        "upload_reference",
        "reference_upload_status",
        "list_reference_uploads",
        "recover_reference_upload",
        "list_generations",
    },
    "tools_blender": {
        "scene_summary",
        "object_detail",
        "execute_python",
        "select_objects",
        "set_frame",
        "screenshot_viewport",
        "camera_path",
        "render_still",
        "blender_api_help",
        "datablocks_summary",
    },
}


def specs(module):
    tree = ast.parse((ROOT / f"scenario/mcp/{module}.py").read_text())
    assignments = {
        node.targets[0].id: node.value
        for node in tree.body
        if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name)
    }
    return assignments, assignments["SPECS"].elts


def property_names(node, assignments):
    assert isinstance(node, ast.Dict)
    names = set()
    for key, value in zip(node.keys, node.values, strict=True):
        if key is None:
            assert isinstance(value, ast.Name)
            names.update(property_names(assignments[value.id], assignments))
        else:
            names.add(ast.literal_eval(key))
    return names


@pytest.mark.parametrize("module", EXPECTED)
def test_real_tool_descriptions_are_complete_static_contracts(module):
    _, calls = specs(module)
    names = []
    for call in calls:
        assert isinstance(call, ast.Call) and call.func.id == "ToolSpec"
        name, description = call.args[:2]
        assert isinstance(name, ast.Constant) and isinstance(description, ast.Constant)
        name, description = name.value, description.value
        names.append(name)
        assert re.fullmatch(r"[a-z][a-z0-9_]*", name)
        assert len(description) >= 120, name
        assert all(label in description for label in ("Args:", "Returns:", "Example:")), name
        assert description.count("Returns:") == 1, name  # One complete return contract.
        if module == "tools_scenario":
            assert re.search(
                r"\n(?:Platform equivalent: [^\n]+|No platform equivalent)\.$", description
            )
        else:
            assert "platform equivalent" not in description.lower(), name
            assert "generation estimate" not in description, name
    assert len(names) == len(set(names))
    assert set(names) == EXPECTED[module]


def test_experimental_paths_are_explicit_without_removing_tools():
    _, calls = specs("tools_scenario")
    descriptions = {call.args[0].value: call.args[1].value for call in calls}
    film = {name for name in descriptions if "film" in name}
    assert film == {name for name in EXPECTED["tools_scenario"] if "film" in name}
    for name in film:
        assert "\nFilm is experimental. " in descriptions[name], name
    for name in ("estimate_cost", "generate"):
        description = descriptions[name]
        assert "are experimental" in description, name
        assert "results stay in saved jobs" in description, name
        assert "motion and transcription handling is not accepted" in description, name
        assert "imports a returned model or media file by file type only" in description, name
        assert "without Blender application" not in description, name
        for capability in UNACCEPTED_CAPABILITIES:
            assert f"({capability})" in description, (name, capability)


def test_film_review_descriptions_name_every_pre_build_invalidation():
    _, calls = specs("tools_scenario")
    descriptions = {call.args[0].value: call.args[1].value for call in calls}
    for name in ("prepare_film_review", "film_review_status"):
        description = descriptions[name]
        for change in (
            "object selection or edits",
            "Undo or Redo",
            "building another review for the scene",
            "selecting it again",
        ):
            assert change in description, (name, change)


def test_film_export_descriptions_state_the_destination_contract():
    _, calls = specs("tools_scenario")
    descriptions = {call.args[0].value: call.args[1].value for call in calls}
    assert "only inside the configured Output Folder" in descriptions["prepare_film_export"]
    assert "never overwritten" in descriptions["prepare_film_export"]
    assert (
        "obtain explicit approval before render_film_export" in descriptions["prepare_film_export"]
    )
    assert (
        "approve the destination returned by prepare_film_export verbatim"
        in descriptions["render_film_export"]
    )
    assert "own destination approval" in descriptions["film_export_review"]
    # Agent approvals recheck the folder, including for a destination chosen natively.
    assert "a destination still inside the Output Folder" in descriptions["render_film_export"]
    assert "pending_destination, inside the Output Folder" in descriptions["film_export_review"]
    assert "Nothing renders yet" in descriptions["prepare_film_export"]
    assert "No upload, generation or spending" in descriptions["render_film_export"]
    assert "never renders again" in descriptions["film_export_review"]


def test_film_case_study_descriptions_state_the_archive_and_privacy_contract():
    _, calls = specs("tools_scenario")
    descriptions = {call.args[0].value: call.args[1].value for call in calls}
    prepare = descriptions["prepare_film_case_study"]
    assert "only inside the configured Output Folder" in prepare
    assert "absolute new .zip path" in prepare and "never overwritten" in prepare
    assert "obtain explicit approval before export_film_case_study" in prepare
    assert "Nothing is written yet" in prepare
    approve = descriptions["export_film_case_study"]
    assert "approve the destination returned by prepare_film_case_study verbatim" in approve
    assert "a destination still inside the Output Folder" in approve
    assert "no credentials, URLs, service identifiers or local paths" in approve
    assert "No upload, generation or spending" in approve
    assert "quoted total shown by prepare_film_case_study changed" in approve
    assert "the extension's saved settings" in approve
    review = descriptions["film_case_study_review"]
    assert "own destination approval" in review and "never builds again" in review
    assert "pending_destination, inside the Output Folder" in review
    assert "readOnlyHint" in ast.unparse(
        next(call for call in calls if call.args[0].value == "film_case_study_sources")
    )


def test_list_models_reports_the_picker_status():
    _, calls = specs("tools_scenario")
    descriptions = {call.args[0].value: call.args[1].value for call in calls}
    assert (
        "capabilities and capability_status (empty, or the experimental note"
        in descriptions["list_models"]
    )
    tree = ast.parse((ROOT / "scenario/mcp/tools_scenario.py").read_text())
    function = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "list_models"
    )
    entries = [node for node in ast.walk(function) if isinstance(node, ast.Dict)]
    pairs = [
        (key.value, value)
        for entry in entries
        for key, value in zip(entry.keys, entry.values, strict=True)
        if isinstance(key, ast.Constant)
    ]
    # Named apart from the server's model status, such as a training state.
    assert "status" not in {key for key, _ in pairs}
    status = [value for key, value in pairs if key == "capability_status"]
    assert len(status) == 1
    assert ast.unparse(status[0]) == "capability_status.model_status(rec.capabilities)"


def test_list_generations_names_every_history_kind_including_unknown():
    _, calls = specs("tools_scenario")
    description = {call.args[0].value: call.args[1].value for call in calls}["list_generations"]
    assert "kind (image, video, 3d, material, audio, workflow or unknown)" in description


def test_job_tools_advertise_both_reference_spellings_without_requiring_legacy_id():
    assignments, calls = specs("tools_scenario")
    for call in calls:
        if call.args[0].value not in {"job_status", "wait_for_job", "import_result"}:
            continue
        schema = call.args[2]
        assert schema.func.id == "_schema"
        assert {"job_id", "id"} <= property_names(schema.args[0], assignments)
        assert len(schema.args) == 1 or ast.literal_eval(schema.args[1]) == []


def test_organization_tools_review_before_one_unrepeated_write():
    from scenario.core.jobs.organization import MAX_COLLECTION_ASSETS, MAX_TAG_CHANGES, Operation

    _, calls = specs("tools_scenario")
    found = {call.args[0].value: call for call in calls}
    annotations = {
        name: ast.literal_eval(found[name].args[4])
        for name in (
            "list_collections",
            "prepare_asset_organization",
            "apply_asset_organization",
            "asset_organization_status",
        )
    }
    assert annotations == {
        "list_collections": {"readOnlyHint": True},
        "prepare_asset_organization": {"readOnlyHint": True},
        "apply_asset_organization": {"destructiveHint": True},
        "asset_organization_status": {"readOnlyHint": True},
    }
    prepare = found["prepare_asset_organization"]
    for text in ("explicit approval", "uses no credits", "Blender Undo", "without sending it"):
        assert text in prepare.args[1].value, text
    schema = ast.literal_eval(prepare.args[2].args[0])
    assert schema["operation"]["enum"] == [operation.value for operation in Operation]
    assert schema["asset_ids"]["maxItems"] == MAX_COLLECTION_ASSETS
    assert schema["add_tags"]["maxItems"] == schema["remove_tags"]["maxItems"] == MAX_TAG_CHANGES
    assert ast.literal_eval(prepare.args[2].args[1]) == ["operation"]
    apply = found["apply_asset_organization"].args[1].value
    for text in (
        "Never repeat UNCONFIRMED",
        "asset_organization_status",
        "Assets already in the collection are skipped",
        "sent again within a fixed bound",
        # A NOT_SENT apply and an unknown FINISHED outcome both return result null.
        "NOT_SENT with result null",
        "result null when the outcome is unknown",
    ):
        assert text in apply, text
    assert "sent once" not in apply
    assert "FINISHED or NOT_SENT and result {" not in apply
    for name in ("apply_asset_organization", "asset_organization_status"):
        assert ast.literal_eval(found[name].args[2].args[1]) == ["context_id", "review_id"]
    status = found["asset_organization_status"].args[1].value
    assert "Never sends an organization write" in status


def _module_constants(path):
    tree = ast.parse((ROOT / path).read_text())
    return {
        node.targets[0].id: node.value
        for node in tree.body
        if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name)
    }


def test_splat_options_match_the_reviewed_choices_everywhere():
    from scenario.core.scene.splats import AXES, FORMAT_AXES

    builder = _module_constants("scenario/blender/splat_application.py")
    choices = ast.literal_eval(builder["POINT_CHOICES"])
    axes = ast.literal_eval(builder["AXES"])
    assert ast.literal_eval(builder["DEFAULT_POINTS"]) in choices
    assert set(axes) == AXES
    _, calls = specs("tools_scenario")
    call = next(call for call in calls if call.args[0].value == "prepare_result_application")
    schema = {
        ast.literal_eval(key): ast.literal_eval(value)
        for key, value in zip(call.args[2].args[0].keys, call.args[2].args[0].values, strict=True)
    }
    assert schema["splat_max_points"] == {"type": "integer", "enum": list(choices)}
    assert schema["splat_axes"] == {"type": "string", "enum": list(axes)}
    description = call.args[1].value
    assert "1000000 (default)" in description
    # The documented defaults are the decoder's format conventions.
    assert FORMAT_AXES == {"spz": "OPENGL", "ply": "OPENCV", "splat": "OPENCV"}
    assert "OPENGL for SPZ, OPENCV for PLY and .splat" in description
    dialog = _module_constants("scenario/blender/job_recovery.py")
    points = [int(item[0]) for item in ast.literal_eval(dialog["SPLAT_POINT_ITEMS"])]
    assert points == list(choices)
    assert [item[0] for item in ast.literal_eval(dialog["SPLAT_AXIS_ITEMS"])] == list(axes)
    status = next(call for call in calls if call.args[0].value == "job_status").args[1].value
    assert "importable (true for every listed format in this release)" in status
    assert "spz_extensions" in status


def test_panorama_lane_contract_is_described_where_agents_choose_and_spend():
    _, calls = specs("tools_scenario")
    descriptions = {call.args[0].value: call.args[1].value for call in calls}
    listing = descriptions["list_models"]
    assert "edit3d or panorama" in listing
    assert "contract with mode (GENERATE, FROM_IMAGE, HDRI or UPSCALE)" in listing
    estimate = descriptions["estimate_cost"]
    assert "Lane panorama accepts only list_models(lane=panorama) models" in estimate
    assert "saved, downloaded 360 result of this account and project" in estimate
    assert "A changed model schema fails closed" in estimate
    # The unknown-size cap fits only references up to 4096 pixels wide.
    assert "at most 2 for a reference of unknown size, which fits a 2:1 panorama" in estimate
    assert "4096 pixels wide, and an upscaled result is not upscaled again" in estimate
    generate = descriptions["generate"]
    assert "edit3d or panorama" in generate
    assert "Panorama results never change a World automatically" in generate
    assert "prepare_result_application(purpose=world)" in generate


def test_prompt_estimates_offer_only_native_lanes_with_a_prompt_field():
    _, calls = specs("tools_scenario")
    lanes = {}
    for call in calls:
        properties = call.args[2].args[0]
        for key, value in zip(properties.keys, properties.values, strict=True):
            if isinstance(key, ast.Constant) and key.value == "lane":
                enum = dict(zip(value.keys, value.values, strict=True))
                lanes[call.args[0].value] = ast.unparse(
                    next(v for k, v in enum.items() if k.value == "enum")
                )
    assert lanes["estimate_prompt"] == "list(NATIVE_LANES)"
    for name in ("list_models", "estimate_cost", "generate"):
        assert lanes[name] == "list(LANES)", name
    tree = ast.parse((ROOT / "scenario/mcp/tools_scenario.py").read_text())
    imports = {
        (node.module, alias.name, alias.asname)
        for node in tree.body
        if isinstance(node, ast.ImportFrom)
        for alias in node.names
    }
    assert ("blender.props", "GENERATION_LANES", "NATIVE_LANES") in imports
    assert ("core.api.catalog", "GENERATION_LANES", "LANES") in imports


def test_workflow_step_tools_require_the_users_explicit_choice():
    assignments, calls = specs("tools_scenario")
    tools = {call.args[0].value: call for call in calls}
    descriptions = {name: call.args[1].value for name, call in tools.items()}
    decide = descriptions["decide_workflow_step"]
    for text in (
        "Send one decision the user explicitly chose",
        "receiving their explicit choice; never decide automatically, by default or on the "
        "user's behalf",
        "Rejecting stops the workflow and is the only way to stop one",
        "completed steps stay charged",
        "the charge after a decision is not live-verified against the approved price",
        "one request follows and is never retried",
        "Never repeat an uncertain decision until the step is shown waiting again",
        "refused before any request",
        "outcome (applied, refused, uncertain or not_sent)",
    ):
        assert text in decide, text
    steps = descriptions["workflow_job_steps"]
    for text in ("decides nothing", "never signed URLs", "stays paused", "blocked_reason"):
        assert text in steps, text
    # Unknown step links fail closed, and the definition read is disclosed.
    for name in ("workflow_job_steps", "job_status"):
        for text in ("links_complete", "unverified", "workflow's definition"):
            assert text in descriptions[name], (name, text)
    # Every other surface that mentions a waiting step points to the same tools.
    for name in ("run_workflow", "job_status"):
        assert "decide_workflow_step" in descriptions[name], name
        assert "explicit choice" in descriptions[name], name
    assert "no local MCP tool sends" not in descriptions["job_status"]
    assert (
        "interactive approval/selection nodes are not supported" not in descriptions["run_workflow"]
    )
    assert (
        "A running workflow job cannot be canceled: cancel returns an explanation and sends nothing"
        in descriptions["recover_local_job"]
    )
    schema = ast.literal_eval(tools["decide_workflow_step"].args[2].args[0])
    required = ast.literal_eval(tools["decide_workflow_step"].args[2].args[1])
    assert required == [
        "context_id",
        "request_id",
        "expected_revision",
        "node_id",
        "review_id",
        "action",
    ]
    assert schema["action"]["enum"] == ["approve", "select", "reject"]
    assert schema["selected_indices"]["items"] == {"type": "integer", "minimum": 0}
    assert schema["expected_revision"] == {"type": "integer", "minimum": 0}
    assert ast.literal_eval(tools["decide_workflow_step"].args[4]) == {"destructiveHint": True}
    assert ast.literal_eval(tools["workflow_job_steps"].args[4]) == {"readOnlyHint": True}
    assert ast.literal_eval(tools["workflow_job_steps"].args[2].args[1]) == [
        "context_id",
        "request_id",
    ]


def test_server_instructions_limit_default_work_to_local_blender_and_assistant_images():
    from scenario.mcp.protocol import INSTRUCTIONS

    assert "agent_workspace" in INSTRUCTIONS and "import_agent_image" in INSTRUCTIONS
    assert "scene_summary" in INSTRUCTIONS
    assert "workflow_job_steps" not in INSTRUCTIONS
    assert "mcp.scenario.com" not in INSTRUCTIONS
