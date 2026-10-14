# SPDX-FileCopyrightText: 2026 Scenario Inc.
# SPDX-License-Identifier: GPL-3.0-or-later
"""Generated reference stays aligned with tool source without importing bpy."""

import importlib.util
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location("gen_mcp_docs", ROOT / "tools/gen_mcp_docs.py")
generator = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(generator)


def test_committed_reference_matches_all_registered_source_contracts():
    doc = (ROOT / "docs/MCP.md").read_text(encoding="utf-8")
    assert generator.replace_block(doc, generator.generated()) == doc
    names = []
    for _, relative in generator.GROUPS:
        for tool in generator.parse_specs(ROOT / relative):
            names.append(tool["name"])
            assert re.search(r"`" + re.escape(tool["name"]) + r"(?:\(|`)", doc)
    assert len(names) == len(set(names))
    assert {Path(path).name for _, path in generator.GROUPS} == {
        "tools_agent.py",
        "tools_agent_materials.py",
        "tools_agent_media.py",
        "tools_agent_models.py",
        "tools_agent_packages.py",
        "tools_agent_mesh.py",
        "tools_agent_blockout.py",
        "tools_agent_capture.py",
        "tools_agent_film.py",
        "tools_agent_film_scene.py",
        "tools_agent_film_review.py",
        "tools_agent_film_export.py",
        "tools_blender.py",
    }
    assert "generate" not in names and "estimate_cost" not in names
    for relative in ("README.md", "docs/USER_GUIDE.md", "docs/MCP.md"):
        assert not re.search(r"\b\d+ tools\b", (ROOT / relative).read_text(encoding="utf-8"))


def test_renamed_tool_is_detected_as_documentation_drift(tmp_path):
    source = ROOT / "scenario/mcp/tools_blender.py"
    changed = tmp_path / "tools_blender.py"
    changed.write_text(
        source.read_text(encoding="utf-8").replace('"scene_summary",', '"renamed_summary",', 1)
    )
    groups = [
        (generator.GROUPS[0][0], generator.parse_specs(changed)),
        (generator.GROUPS[1][0], generator.parse_specs(ROOT / generator.GROUPS[1][1])),
    ]
    doc = (ROOT / "docs/MCP.md").read_text(encoding="utf-8")
    assert generator.replace_block(doc, generator.render(groups)) != doc


def test_parser_handles_shared_job_properties_and_dynamic_enum_without_execution(tmp_path):
    path = tmp_path / "tool.py"
    path.write_text("""raise RuntimeError("source must never execute")
_JOB_REF = {"job_id": {"type": "string"}, "id": {"type": "string"}}
SPECS = (ToolSpec("example", "First line | summary.\\nArgs: rest", _schema(
    {**_JOB_REF, "lane": {"type": "string", "enum": list(LANES)}}, ["lane"]),
    handler, {"readOnlyHint": True}),)
""")
    tools = generator.parse_specs(path)
    assert set(tools[0]["properties"]) == {"id", "job_id", "lane"}
    assert tools[0]["properties"]["lane"]["enum"] == "list(LANES)"
    rendered = generator.render([("Example", tools)])
    assert "`lane`*: string" in rendered
    assert "read-only annotation" in rendered
    assert "First line \\| summary." in rendered
    assert "Args: rest" not in rendered


@pytest.mark.parametrize(
    "document",
    [
        "no markers",
        "<!-- tools:end --><!-- tools:start -->",
        "<!-- tools:start --><!-- tools:start --><!-- tools:end -->",
    ],
)
def test_malformed_markers_fail_instead_of_rewriting_unrelated_text(document):
    with pytest.raises(ValueError):
        generator.replace_block(document, "replacement\n")


def test_named_schema_is_resolved_without_importing_the_tool_module(tmp_path):
    path = tmp_path / "tool.py"
    path.write_text("""raise RuntimeError("Never import this module")
_OPERATION = _schema({"operation_id": {"type": "string"}}, ["operation_id"])
SPECS = [ToolSpec("status", "Read status.", _OPERATION, handler)]
""")
    tool = generator.parse_specs(path)[0]
    assert tool["properties"] == {"operation_id": {"type": "string"}}
    assert tool["required"] == ["operation_id"]


def test_initialize_guidance_uses_assistant_image_handoff():
    from scenario.mcp.protocol import INSTRUCTIONS

    assert "scene_summary" in INSTRUCTIONS
    assert INSTRUCTIONS.index("agent_workspace") < INSTRUCTIONS.index("import_agent_image")
    assert "mcp.scenario.com" not in INSTRUCTIONS
    assert "estimate_cost" not in INSTRUCTIONS
    assert "CU cost" not in INSTRUCTIONS


def test_current_entrypoints_do_not_route_users_to_upstream_service_setup():
    from scenario.mcp.protocol import INSTRUCTIONS

    assert "execute_python" in INSTRUCTIONS
    assert "explicitly enabled" in INSTRUCTIONS
    for relative in (
        "README.md",
        "docs/USER_GUIDE.md",
        "docs/MCP.md",
        "docs/PRIVACY.md",
        "SUPPORT.md",
    ):
        text = (ROOT / relative).read_text(encoding="utf-8")
        assert "blender.scenario.com/repo" not in text
        assert "SCENARIO_API_KEY" not in text
        assert "mcp.scenario.com" not in text
