# SPDX-FileCopyrightText: 2026 Scenario Inc.
# SPDX-License-Identifier: GPL-3.0-or-later
"""Disclosures for the registered local assistant workflow and its handbook."""

import ast
import re
from pathlib import Path
from urllib.parse import urlsplit

from tools.build_docs_html import build_handbook

ROOT = Path(__file__).resolve().parents[2]
# Dormant upstream service modules do not describe the registered local workflow.
ACTIVE_SOURCES = (
    "scenario/prefs.py",
    "scenario/blender/registry.py",
    "scenario/blender/runtime.py",
    "scenario/blender/assistant_loop.py",
    "scenario/blender/assistant_ui.py",
    "scenario/blender/assistant_materials.py",
    "scenario/blender/assistant_media.py",
    "scenario/blender/assistant_mesh.py",
    "scenario/blender/assistant_mesh_snapshot.py",
    "scenario/blender/assistant_blockout.py",
    "scenario/blender/assistant_capture.py",
    "scenario/blender/assistant_film.py",
    "scenario/blender/assistant_film_scene.py",
    "scenario/blender/assistant_film_review.py",
    "scenario/blender/assistant_film_export.py",
    "scenario/blender/assistant_film_export_worker.py",
    "scenario/blender/assistant_project_worker.py",
    "scenario/core/jobs/assistant_project_bundle.py",
    "scenario/core/assistant_results.py",
    "scenario/blender/provider_setup.py",
    "scenario/blender/mcp_service.py",
    "scenario/blender/updates.py",
    "scenario/mcp/tools_agent.py",
    "scenario/mcp/tools_agent_materials.py",
    "scenario/mcp/tools_agent_results.py",
    "scenario/mcp/tools_agent_media.py",
    "scenario/mcp/tools_agent_models.py",
    "scenario/mcp/tools_agent_packages.py",
    "scenario/mcp/tools_agent_mesh.py",
    "scenario/mcp/tools_agent_blockout.py",
    "scenario/mcp/tools_agent_capture.py",
    "scenario/mcp/tools_agent_film.py",
    "scenario/mcp/tools_agent_film_scene.py",
    "scenario/mcp/tools_agent_film_review.py",
    "scenario/mcp/tools_agent_film_export.py",
    "scenario/mcp/tools_blender.py",
    "scenario/mcp/server.py",
    "scenario/mcp/stdio_shim.py",
)


def test_active_literal_network_destinations_require_a_disclosure_review():
    hosts = set()
    for relative in ACTIVE_SOURCES:
        source = (ROOT / relative).read_text(encoding="utf-8")
        for node in ast.walk(ast.parse(source)):
            if isinstance(node, ast.Constant) and isinstance(node.value, str):
                for url in re.findall(r"https?://[A-Za-z0-9.:-]+", node.value):
                    hosts.add(urlsplit(url).hostname)
    assert hosts <= {"127.0.0.1", "github.com"}, hosts
    note = (ROOT / "docs/PRIVACY.md").read_text(encoding="utf-8")
    assert all(host in note for host in hosts)
    # Assistant data processing and arbitrary Python cannot be inferred from URLs.
    assert "own service" in note
    assert "Code can access files or the network" in note


def test_privacy_entry_points_and_critical_local_storage_disclosures():
    note = (ROOT / "docs/PRIVACY.md").read_text(encoding="utf-8")
    for text in (
        "scene summaries",
        "current file path",
        "Viewport and",
        "make no external",
        "agent-connection.json",
        "bearer token",
        "127.0.0.1",
        "inbox persists",
        "saved `.blend` file",
        "assistant-media",
        "not packed into `.blend` files",
        "Allow connected assistants to run Python",
        "off by default",
        "not a security sandbox",
    ):
        assert text in note, text
    for obsolete in (
        "SCENARIO_API_KEY",
        "SCENARIO_API_SECRET",
        "userpref.blend",
        "before you press",
    ):
        assert obsolete not in note
    for path, link in (("README.md", "docs/PRIVACY.md"), ("docs/USER_GUIDE.md", "PRIVACY.md")):
        text = (ROOT / path).read_text(encoding="utf-8")
        assert text.count(link) == 1
        assert "removed when the extension is uninstalled" not in text
    prefs = ast.parse((ROOT / "scenario/prefs.py").read_text(encoding="utf-8"))
    properties = {
        node.target.id
        for node in ast.walk(prefs)
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name)
    }
    assert "mcp_allow_python" in properties
    assert not properties & {"api_key", "api_secret", "provider_mode", "provider_profiles"}


def test_standalone_guide_preserves_assistant_disclosures(tmp_path):
    output = build_handbook(
        ROOT / "docs/USER_GUIDE.md",
        ROOT / "docs/handbook-template.html",
        ROOT / "scenario/blender_manifest.toml",
        tmp_path / "handbook.html",
        inline_images=True,
    )
    text = output.read_text(encoding="utf-8")
    assert "<title>Open Studio for Blender: user guide</title>" in text
    assert 'href="https://github.com/scenario-labs/blender-plugin/' not in text
    assert (
        'href="https://github.com/midnitefox/blender-plugin/blob/feat/provider-choice/docs/PRIVACY.md"'
        in text
    )
    assert "Privacy" in text
    assert "PRIVACY.md" in text
    assert "requested scene data and viewport captures" in text
    assert "Local asset import makes no" in text
    assert "private connection file" in text
    assert "removed with the extension" not in text
