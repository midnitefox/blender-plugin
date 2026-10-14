# SPDX-FileCopyrightText: 2026 Scenario Inc.
# SPDX-License-Identifier: GPL-3.0-or-later
"""Keep the assistant user guide aligned with local tools and the Markdown renderer."""

import re
import tomllib
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
GUIDE = ROOT / "docs/USER_GUIDE.md"
TEMPLATE = ROOT / "docs/handbook-template.html"
STALE = re.compile(
    r"\b(?:\d+|fifteen|sixteen|seventeen|eighteen|nineteen|twenty)\s+tools\b"
    r"|\bPro plan\b|\btested on\b|\bVersion \d+\.\d+|\bv\d+\.\d+\.\d+\b"
    r"|scenario-mcp|Team > API Keys|build\.sh|SCENARIO_GUI_PROBE"
    r"|one collapsible|the pencil opens|August 2026|BUGS\.md",
    re.IGNORECASE,
)


def test_registered_image_handoff_modes_have_guide_instructions():
    from tools.gen_mcp_docs import parse_specs

    specs = parse_specs(ROOT / "scenario/mcp/tools_agent.py")
    image_import = next(tool for tool in specs if tool["name"] == "import_agent_image")
    text = GUIDE.read_text(encoding="utf-8")
    for mode in image_import["properties"]["mode"]["enum"]:
        assert f"| `{mode}` |" in text
    for tool in ("agent_workspace", "import_agent_image", "scene_summary", "camera_path"):
        assert f"`{tool}`" in text
    assert "## Assistant connection (MCP)" in text
    assert "## Local scene and camera tools" in text
    assert "## Python permission" in text


def test_guide_minimum_tracks_the_extension_manifest():
    manifest = tomllib.loads((ROOT / "scenario/blender_manifest.toml").read_text(encoding="utf-8"))
    minimum = manifest["blender_version_min"]
    if minimum.endswith(".0"):
        minimum = minimum.rsplit(".", 1)[0]
    assert f"Blender {minimum} or later" in GUIDE.read_text(encoding="utf-8")


@pytest.mark.parametrize("document", [GUIDE, TEMPLATE], ids=["guide", "template"])
def test_public_guide_avoids_retired_or_unverified_claims(document):
    assert not STALE.search(document.read_text(encoding="utf-8"))


@pytest.mark.parametrize(
    "text",
    ["16 tools", "nineteen tools", "Pro plan", "tested on", "v0.9.9", "Team > API Keys"],
)
def test_stale_claim_guard_rejects_regressions(text):
    assert STALE.search(text)


def test_any_guide_illustrations_reference_existing_files():
    images = re.findall(r"!\[[^\]]*\]\((images/[^)]+)\)", GUIDE.read_text(encoding="utf-8"))
    for image in images:
        assert (GUIDE.parent / image).is_file(), image


def test_handbook_has_one_generated_body_and_navigation():
    template = TEMPLATE.read_text(encoding="utf-8")
    assert template.count("{{content}}") == 1
    assert template.count("{{toc}}") == 1
    assert template.count("{{version}}") == 2
    assert not re.search(r"<h2\b|<section\b|\{\{img:", template, re.IGNORECASE)
    assert not (ROOT / "docs/user-guide.src.html").exists()
    assert not (ROOT / "docs/user-guide.html").exists()
