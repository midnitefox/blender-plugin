# SPDX-FileCopyrightText: 2026 Scenario Inc.
# SPDX-License-Identifier: GPL-3.0-or-later
"""Offline manifest metadata rules and static permission-use signals, not a sandbox."""

import ast
import pathlib
import re
import tomllib

ROOT = pathlib.Path(__file__).resolve().parents[2]
PACKAGE = ROOT / "scenario"
MANIFEST = PACKAGE / "blender_manifest.toml"
KNOWN = {"files", "network", "clipboard", "camera", "microphone"}
# https://docs.blender.org/manual/en/latest/advanced/extensions/tags.html
# Add-on tags checked against the manual on 2026-09-24; Blender validates the ZIP too.
ADDON_TAGS = {
    "3D View",
    "Add Curve",
    "Add Mesh",
    "Animation",
    "Bake",
    "Camera",
    "Compositing",
    "Development",
    "Game Engine",
    "Geometry Nodes",
    "Grease Pencil",
    "Import-Export",
    "Lighting",
    "Material",
    "Modeling",
    "Mesh",
    "Node",
    "Object",
    "Paint",
    "Pipeline",
    "Physics",
    "Render",
    "Rigging",
    "Scene",
    "Sculpt",
    "Sequencer",
    "System",
    "Text Editor",
    "Tracking",
    "User Interface",
    "UV",
}
NETWORK_MODULES = {
    "urllib.request",
    "http.client",
    "http.server",
    "socket",
    "socketserver",
    "httpx",
    "scenario_sdk",
}


def manifest():
    return tomllib.loads(MANIFEST.read_text(encoding="utf-8"))


def permissions():
    return manifest()["permissions"]


def terse_description(text):
    # Blender permits closing brackets after a format or hostname. Dotted hosts are
    # not sentence breaks. Native validation remains authoritative for full syntax.
    return (
        isinstance(text, str)
        and 1 <= len(text) <= 64
        and text == text.strip()
        and not any(ord(char) < 32 or ord(char) == 127 for char in text)
        and not re.search(r"[.!?]\s", text)
        and (text[-1].isalnum() or text[-1] in ")]}")
    )


def test_manifest_schema_and_type():
    data = manifest()
    assert data["schema_version"] == "1.0.0"
    assert data["type"] == "add-on"


def test_manifest_extension_id_is_stable():
    value = manifest()["id"]
    assert value == "open_studio", (
        "The independent fork must keep separate update and storage identity"
    )
    assert re.fullmatch(r"[a-z][a-z0-9_]*", value)


def test_manifest_release_version_is_three_numeric_components():
    # Release automation owns this project's stable X.Y.Z convention. Equality
    # with package/changelog versions belongs to test_version.py.
    assert re.fullmatch(
        r"(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)", manifest()["version"]
    )


def test_manifest_name_and_maintainer_are_nonempty():
    data = manifest()
    for key in ("name", "maintainer"):
        assert isinstance(data[key], str) and data[key].strip(), key


def test_manifest_tagline_is_a_short_single_line():
    assert terse_description(manifest()["tagline"])


def test_manifest_tags_are_known_and_unique():
    tags = manifest()["tags"]
    assert isinstance(tags, list) and tags
    assert all(isinstance(tag, str) for tag in tags)
    assert len(tags) == len(set(tags))
    assert set(tags) <= ADDON_TAGS


def test_manifest_license_preserves_gpl():
    licenses = manifest()["license"]
    assert isinstance(licenses, list) and "SPDX:GPL-3.0-or-later" in licenses
    assert all(isinstance(value, str) and re.fullmatch(r"SPDX:\S+", value) for value in licenses)


def test_manifest_copyright_has_year_and_owner():
    owners = manifest()["copyright"]
    assert isinstance(owners, list) and owners
    assert all(
        isinstance(owner, str) and re.fullmatch(r"[0-9]{4}(?:-[0-9]{4})? \S[^\r\n]*", owner)
        for owner in owners
    )


def test_manifest_minimum_respects_supported_blender_floor():
    value = manifest()["blender_version_min"]
    assert isinstance(value, str) and re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", value)
    assert tuple(int(part) for part in value.split(".")) >= (5, 0, 0)


def test_manifest_has_no_empty_values():
    def check(value, path):
        if isinstance(value, str):
            assert value.strip(), path
        elif isinstance(value, dict):
            assert value, path
            for key, child in value.items():
                check(child, f"{path}.{key}")
        elif isinstance(value, list):
            assert value, path
            for index, child in enumerate(value):
                check(child, f"{path}[{index}]")

    check(manifest(), "manifest")


def source_uses():
    clipboard, network = [], []
    for path in PACKAGE.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Attribute)
                and node.attr == "clipboard"
                and isinstance(node.value, ast.Attribute)
                and node.value.attr == "window_manager"
            ):
                clipboard.append(str(path.relative_to(ROOT)))
            modules = []
            if isinstance(node, ast.Import):
                modules = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and not node.level:
                modules = [node.module or ""]
            if any(
                name == library or name.startswith(library + ".")
                for name in modules
                for library in NETWORK_MODULES
            ):
                network.append(str(path.relative_to(ROOT)))
    return {"clipboard": sorted(set(clipboard)), "network": sorted(set(network))}


def test_clipboard_permission_matches_code():
    uses = source_uses()["clipboard"]
    declared = "clipboard" in permissions()
    assert declared == bool(uses), f"Clipboard use: {uses}; declared={declared}"


def test_network_permission_matches_code():
    uses = source_uses()["network"]
    declared = "network" in permissions()
    assert declared == bool(uses), f"Network imports: {uses}; declared={declared}"


def test_network_permission_discloses_the_local_assistant_listener():
    # Local MCP starts in GUI sessions with Online Access on; generation is external.
    assert permissions()["network"] == "Connect local assistants on 127.0.0.1"


def test_permission_reasons_follow_manifest_rules():
    declared = permissions()
    assert declared
    for name, reason in declared.items():
        assert name in KNOWN
        assert terse_description(reason), name


def test_manifest_website_points_at_the_published_handbook():
    website = manifest()["website"]
    assert website == "https://github.com/midnitefox/blender-plugin", website
