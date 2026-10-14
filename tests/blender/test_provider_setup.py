"""Installed provider setup, profile switching and read-only drawing."""

import unittest
from unittest.mock import Mock, patch

import bpy
from helpers import submodule


class ProviderSetupTests(unittest.TestCase):
    def setUp(self):
        self.runtime = submodule("blender.runtime")
        self.setup = submodule("blender.provider_setup")
        self.prefs = self.runtime.prefs()
        self.enterContext(patch.object(self.runtime, "sync_catalog_context"))
        self.saved = [
            {field: getattr(row, field) for field in self.setup.PROFILE_FIELDS}
            for row in self.prefs.provider_profiles
        ]
        self.saved_mode = self.prefs.provider_mode
        self.saved_index = self.prefs.active_provider_index
        self.prefs.provider_profiles.clear()
        self.prefs.active_provider_index = 0
        self.prefs.provider_mode = "COMFYUI"

    def tearDown(self):
        self.prefs.provider_profiles.clear()
        for values in self.saved:
            row = self.prefs.provider_profiles.add()
            for field, value in values.items():
                setattr(row, field, value)
        self.prefs.provider_mode = self.saved_mode
        self.prefs.active_provider_index = self.saved_index

    def test_first_run_defaults_to_external_assistant_without_credentials(self):
        self.assertEqual(self.prefs.bl_rna.properties["provider_mode"].default, "AGENT")
        self.prefs.provider_mode = "AGENT"
        self.assertIsNone(self.setup.profile_from_preferences(self.prefs))
        self.assertTrue(self.setup.uses_external_assistant(self.prefs))
        panels = submodule("blender.panels")
        with patch.object(self.runtime, "online", return_value=False):
            self.assertEqual(panels.generate_button_text(None), "Generate through your assistant")
            self.assertEqual(panels.model_button_text(None), "Codex / external assistant")

    def test_optional_comfy_uses_loopback_and_requires_no_scenario_credentials(self):
        selected = self.setup.profile_from_preferences(self.prefs)
        self.assertEqual(selected.provider, "comfyui")
        self.assertEqual(selected.base_url, "http://127.0.0.1:8188")
        self.assertEqual(selected.api_key, "")

    def test_named_profiles_add_switch_and_remove_without_reusing_identity(self):
        self.assertEqual(bpy.ops.scenario.add_ai_profile(provider="comfyui"), {"FINISHED"})
        first_id = self.setup.profile_from_preferences(self.prefs).profile_id
        self.assertEqual(
            bpy.ops.scenario.add_ai_profile(provider="openai_compatible"), {"FINISHED"}
        )
        row = self.prefs.provider_profiles[1]
        row.name = "My API"
        row.base_url = "https://example.com/v1"
        row.api_key = "synthetic-profile-token"
        row.image_model = "image-fixture"
        selected = self.setup.profile_from_preferences(self.prefs)
        self.assertEqual(selected.image_model, "image-fixture")
        self.assertNotEqual(first_id, selected.profile_id)
        self.prefs.active_provider_index = 0
        self.assertEqual(self.setup.profile_from_preferences(self.prefs).profile_id, first_id)
        self.assertEqual(bpy.ops.scenario.remove_ai_profile(), {"FINISHED"})
        self.assertEqual(self.setup.profile_from_preferences(self.prefs).name, "My API")

    def test_saved_scenario_account_is_separate_from_other_profiles(self):
        bpy.ops.scenario.add_ai_profile(provider="scenario")
        selected = self.setup.active_provider_preferences(self.prefs)
        selected.api_key = "synthetic-scenario-key"
        selected.api_secret = "synthetic-scenario-secret"
        self.assertIsNone(self.setup.profile_from_preferences(self.prefs))
        self.assertEqual(selected.bl_rna.properties["api_key"].subtype, "PASSWORD")
        self.assertEqual(selected.bl_rna.properties["api_secret"].subtype, "PASSWORD")
        bpy.ops.scenario.add_ai_profile(provider="comfyui")
        self.assertEqual(self.setup.profile_from_preferences(self.prefs).api_key, "")

    def test_fal_profile_exposes_default_queue_and_independent_token(self):
        self.assertEqual(bpy.ops.scenario.add_ai_profile(provider="fal"), {"FINISHED"})
        row = self.prefs.provider_profiles[0]
        row.api_key = "synthetic-fal-token"
        selected = self.setup.profile_from_preferences(self.prefs)
        self.assertEqual(selected.provider, "fal")
        self.assertEqual(selected.base_url, "https://queue.fal.run")
        self.assertEqual(selected.api_key, "synthetic-fal-token")

    def test_drawing_first_run_is_read_only_and_exposes_choose_ai(self):
        panels = submodule("blender.panels")
        before = len(self.prefs.provider_profiles)
        layout = Mock()
        with patch.object(self.runtime, "online", return_value=False):
            panels.draw_account_strip(layout, bpy.context)
        layout.row.return_value.operator.assert_any_call(
            "scenario.choose_ai", text="Choose AI", icon="PREFERENCES"
        )
        self.assertEqual(len(self.prefs.provider_profiles), before)
        self.setup.draw_provider_setup(Mock(), bpy.context, self.prefs)
        self.assertEqual(len(self.prefs.provider_profiles), before)

    def test_test_connection_uses_existing_runtime_check(self):
        with patch.object(self.runtime, "request_connection_check", return_value=None) as check:
            self.assertEqual(bpy.ops.scenario.test_ai_connection(), {"FINISHED"})
            check.assert_called_once_with()

    def test_incomplete_profile_keeps_editable_fields_and_actionable_error(self):
        bpy.ops.scenario.add_ai_profile(provider="openai_compatible")
        layout = Mock()
        self.setup.draw_provider_setup(layout, bpy.context, self.prefs)
        selected = self.prefs.provider_profiles[0]
        layout.box.return_value.prop.assert_any_call(selected, "base_url")
        layout.box.return_value.prop.assert_any_call(selected, "image_model")
        self.assertTrue(
            any(
                call.kwargs.get("icon") == "ERROR"
                for call in layout.box.return_value.label.call_args_list
            )
        )

    def test_external_assistant_setup_exposes_connection_and_copy_actions(self):
        self.prefs.provider_mode = "AGENT"
        layout = Mock()
        service = submodule("blender.mcp_service")
        with patch.object(service, "status", return_value={"running": False}):
            self.setup.draw_provider_setup(layout, bpy.context, self.prefs)
        layout.box.return_value.row.return_value.operator.assert_any_call(
            "scenario.mcp_start", text="Start assistant connection", icon="PLAY"
        )
        layout.box.return_value.row.return_value.operator.assert_any_call(
            "scenario.mcp_copy", text="Copy Codex setup", icon="COPYDOWN"
        )
