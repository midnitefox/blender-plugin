"""Independent package identity and fail-fast protection for legacy RNA names."""

from pathlib import Path

import pytest

import scenario


def test_original_and_fork_cannot_register_together():
    with pytest.raises(RuntimeError, match="Disable Scenario"):
        scenario.check_registration_conflicts(
            ["bl_ext.user_default.scenario"], "bl_ext.user_default.open_studio"
        )


def test_fork_does_not_conflict_with_itself_or_unrelated_extensions():
    scenario.check_registration_conflicts(
        ["bl_ext.user_default.open_studio", "bl_ext.user_default.some_addon"],
        "bl_ext.user_default.open_studio",
    )


def test_second_repository_copy_cannot_reuse_registered_names():
    with pytest.raises(RuntimeError, match="Open Studio"):
        scenario.check_registration_conflicts(
            ["bl_ext.other.open_studio"], "bl_ext.user_default.open_studio"
        )


def test_fork_updates_do_not_register_or_sync_the_upstream_repository():
    source = (Path(__file__).resolve().parents[2] / "scenario/blender/updates.py").read_text()
    assert "https://github.com/midnitefox/blender-plugin/releases" in source
    assert "extension_repo_add" not in source
    assert "repo_sync" not in source
    assert "blender.scenario.com" not in source
