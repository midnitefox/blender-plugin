# SPDX-FileCopyrightText: 2026 Open Studio contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Installed assistant-only surface, local scene work and lifecycle cleanup."""

import base64
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import bpy
from helpers import RecordingStruct, addon_name, reset_scene, submodule

LOCAL_TOOLS = {
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
}
ASSISTANT_TOOLS = {"agent_workspace", "import_agent_image"}


class AssistantModeTests(unittest.TestCase):
    def setUp(self):
        reset_scene()
        self.runtime = submodule("blender.runtime")
        self.service = submodule("blender.mcp_service")

    def test_active_tools_are_exactly_assistant_and_local_blender_tools(self):
        registry = self.service.build_registry()
        self.assertEqual(set(registry.names()), LOCAL_TOOLS | ASSISTANT_TOOLS)
        for name in ("generate", "estimate_cost", "upload_asset", "list_models", "list_assets"):
            self.assertIsNone(registry.get(name))
        self.assertFalse(hasattr(bpy.context.scene, "scenario"))
        self.assertTrue(hasattr(bpy.context.scene, "scenario_shot"))

    def test_service_credentials_and_generation_operators_are_absent(self):
        preferences = self.runtime.prefs()
        fields = set(preferences.bl_rna.properties.keys())
        forbidden = {
            "api_key",
            "api_secret",
            "credential_source",
            "project_id",
            "provider_mode",
            "provider_profiles",
            "active_provider_index",
            "text_model",
            "image_model",
            "speech_model",
            "base_url",
            "workflows_path",
        }
        self.assertFalse(fields & forbidden)
        for name in (
            "SCENARIO_OT_generate",
            "SCENARIO_OT_test_connection",
            "SCENARIO_OT_refresh_catalog",
            "SCENARIO_OT_upload_reference",
            "SCENARIO_OT_upload_selected_mesh",
            "SCENARIO_OT_history_refresh",
            "SCENARIO_OT_add_ai_profile",
        ):
            self.assertIsNone(bpy.types.Operator.bl_rna_get_subclass_py(name), name)
        self.assertFalse(hasattr(self.runtime, "credentials"))
        self.assertFalse(hasattr(self.runtime, "ensure_job_session"))

    def test_active_registration_has_not_imported_service_sdk_or_job_runtime(self):
        prefix = addon_name()
        for suffix in (
            "core.api.sdk_adapter",
            "core.api.sdk_catalog",
            "blender.job_session",
            "blender.generation",
            "mcp.tools_scenario",
        ):
            self.assertNotIn(prefix + "." + suffix, sys.modules)
        self.assertFalse(
            any(name == "scenario_sdk" or name.startswith("scenario_sdk.") for name in sys.modules)
        )

    def test_camera_path_builds_without_generation_scene_properties(self):
        tools = submodule("mcp.tools_blender")
        planner = submodule("blender.shot_planner")
        self.assertFalse(hasattr(bpy.context.scene, "scenario"))
        bpy.ops.mesh.primitive_cube_add(size=2, location=(0, 0, 1))
        result = tools.camera_path({"preset": "orbit", "duration": 2, "focal": 35})
        camera = bpy.data.objects[result["camera"]]
        self.assertEqual(bpy.context.scene.camera, camera)
        self.assertEqual(camera.type, "CAMERA")
        self.assertGreater(result["keyframes"], 1)
        self.assertTrue(planner.fcurves_of(camera))
        self.assertGreater(result["frame_end"], result["frame_start"])
        result = tools.camera_path(
            {
                "duration": 2,
                "waypoints": [{"position": [2, -4, 2]}, {"position": [-2, -4, 3]}],
            }
        )
        self.assertEqual(result["markers"], 2)
        self.assertFalse(hasattr(bpy.context.scene, "scenario"))

    def test_screenshot_and_render_tools_remain_exposed_without_a_provider(self):
        registry = self.service.build_registry()
        tools = submodule("mcp.tools_blender")
        capture = submodule("blender.capture")
        self.assertIs(registry.get("screenshot_viewport").handler, tools.screenshot_viewport)
        self.assertIs(registry.get("render_still").handler, tools.render_still)
        if bpy.app.background:
            with self.assertRaisesRegex(RuntimeError, "GUI"):
                tools.screenshot_viewport({})
        created = []
        data = b"\x89PNG\r\n\x1a\nfixture"

        def write_capture(context, path, **options):
            self.assertEqual(context.scene, bpy.context.scene)
            self.assertEqual(options, {"source": "VIEWPORT", "width": 320, "height": 180})
            created.append(Path(path))
            Path(path).write_bytes(data)

        with patch.object(capture, "capture_still", side_effect=write_capture):
            result = tools.render_still({"source": "VIEWPORT", "width": 320, "height": 180})
        self.assertEqual(result["mimeType"], "image/png")
        self.assertEqual(base64.b64decode(result["_image"]), data)
        self.assertEqual(len(created), 1)
        self.assertFalse(created[0].parent.exists())

    def test_setup_and_panels_draw_without_writes_or_starting_a_connection(self):
        setup = submodule("blender.provider_setup")
        ui = submodule("blender.assistant_ui")
        state = submodule("blender.file_state")
        writes = []
        preferences = RecordingStruct(self.runtime.prefs(), writes)
        with (
            patch.object(self.runtime, "prefs", return_value=preferences),
            patch.object(self.service, "status", return_value={"running": False, "error": ""}),
            patch.object(self.service, "start", side_effect=AssertionError("Drawing started MCP")),
            patch.object(
                state, "mark_modified", side_effect=AssertionError("Drawing changed file")
            ),
        ):
            setup.draw_provider_setup(Mock(), bpy.context, preferences)
            panels = [cls for cls in ui.CLASSES if issubclass(cls, bpy.types.Panel)]
            self.assertTrue(panels)
            for cls in panels:
                cls.draw(SimpleNamespace(layout=Mock()), bpy.context)
        self.assertEqual(writes, [])

    def test_python_execution_stays_opt_in(self):
        preferences = self.runtime.prefs()
        self.assertFalse(preferences.bl_rna.properties["mcp_allow_python"].default)
        old = preferences.mcp_allow_python
        try:
            preferences.mcp_allow_python = False
            with self.assertRaises(PermissionError):
                submodule("mcp.tools_blender").execute_python({"code": "print('not executed')"})
        finally:
            preferences.mcp_allow_python = old

    def test_z_disable_and_reenable_clean_up_server_timer_and_scene_properties(self):
        loop = submodule("blender.assistant_loop")
        preferences = self.runtime.prefs()
        saved = {
            name: getattr(preferences, name)
            for name in ("mcp_port", "mcp_allow_python", "log_level")
        }
        self.assertTrue(bpy.app.timers.is_registered(loop.tick))
        with (
            patch.object(self.runtime, "online", return_value=True),
            patch.object(
                self.runtime,
                "prefs",
                return_value=SimpleNamespace(mcp_port=0),
            ),
        ):
            server = self.service.start()
        self.assertIsNotNone(server)
        self.assertTrue(server.running)
        connection = self.service.connection_file_path()
        self.assertTrue(connection.is_file())
        try:
            self.assertEqual(bpy.ops.preferences.addon_disable(module=addon_name()), {"FINISHED"})
            self.assertFalse(server.running)
            self.assertFalse(connection.exists())
            self.assertFalse(bpy.app.timers.is_registered(loop.tick))
            self.assertFalse(hasattr(bpy.types.Scene, "scenario_shot"))
            self.assertIsNone(
                bpy.types.Operator.bl_rna_get_subclass_py("OPENSTUDIO_OT_assistant_start")
            )
            self.assertIsNone(self.runtime.state.mcp)
            self.assertIsNone(self.runtime.state.cli_handle)
            self.assertTrue(self.runtime.state.mcp_token == "")
        finally:
            if addon_name() not in bpy.context.preferences.addons:
                bpy.ops.preferences.addon_enable(module=addon_name())
            restored = self.runtime.prefs()
            if restored is not None:
                for name, value in saved.items():
                    setattr(restored, name, value)
        self.assertIsNotNone(self.runtime.prefs())
        self.assertTrue(bpy.app.timers.is_registered(loop.tick))
        self.assertTrue(hasattr(bpy.types.Scene, "scenario_shot"))
        self.assertFalse(hasattr(bpy.types.Scene, "scenario"))
        self.assertEqual(set(self.service.build_registry().names()), LOCAL_TOOLS | ASSISTANT_TOOLS)
