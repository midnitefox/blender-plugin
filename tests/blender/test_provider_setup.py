# SPDX-FileCopyrightText: 2026 Open Studio contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""The installed extension offers one local assistant setup workflow."""

import unittest
from unittest.mock import Mock, patch

import bpy
from helpers import submodule


class ProviderSetupTests(unittest.TestCase):
    def setUp(self):
        self.runtime = submodule("blender.runtime")
        self.setup = submodule("blender.provider_setup")
        self.service = submodule("blender.mcp_service")
        self.prefs = self.runtime.prefs()

    def test_preferences_have_no_direct_provider_or_credential_fields(self):
        for name in (
            "provider_mode",
            "provider_profiles",
            "active_provider_index",
            "credential_source",
            "api_key",
            "api_secret",
            "project_id",
            "composer_enabled",
            "output_dir",
        ):
            self.assertNotIn(name, self.prefs.bl_rna.properties)
        for name in ("mcp_port", "mcp_allow_python", "log_level"):
            self.assertIn(name, self.prefs.bl_rna.properties)
        self.assertFalse(self.prefs.mcp_allow_python)

    def test_setup_has_no_direct_provider_operators(self):
        self.assertEqual([cls.bl_idname for cls in self.setup.CLASSES], ["scenario.choose_ai"])
        self.assertEqual(self.setup.SCENARIO_OT_choose_ai.bl_label, "Assistant setup")
        self.assertFalse(hasattr(bpy.types, "SCENARIO_PG_ai_profile"))

    def test_drawing_is_read_only_and_exposes_connection_and_copy(self):
        layout = Mock()
        original = self.prefs.mcp_port
        with (
            patch.object(self.service, "status", return_value={"running": False}),
            patch.object(self.runtime, "online", return_value=False),
        ):
            self.setup.draw_provider_setup(layout, bpy.context, self.prefs)
        row = layout.box.return_value.row.return_value
        row.operator.assert_any_call(
            "scenario.mcp_start", text="Start assistant connection", icon="PLAY"
        )
        row.operator.assert_any_call("scenario.mcp_copy", text="Copy Codex setup", icon="COPYDOWN")
        for label in ("Claude Desktop", "Claude Code", "Cursor"):
            row.operator.assert_any_call("scenario.mcp_copy", text=label, icon="COPYDOWN")
        text = " ".join(
            call.kwargs.get("text", "") for call in layout.box.return_value.label.call_args_list
        )
        self.assertIn("ImageGen", text)
        self.assertIn("Copy Codex setup", text)
        self.assertIn("Allow Online Access", text)
        for stale in ("API", "Scenario", "ComfyUI", "provider"):
            self.assertNotIn(stale, text)
        self.assertEqual(self.prefs.mcp_port, original)

    def test_running_connection_has_actionable_status(self):
        layout = Mock()
        with patch.object(self.service, "status", return_value={"running": True}):
            self.setup.draw_provider_setup(layout, bpy.context, self.prefs)
        layout.box.return_value.row.return_value.label.assert_any_call(
            text="Assistant connection running", icon="CHECKMARK"
        )
