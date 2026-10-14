# SPDX-FileCopyrightText: 2026 Scenario Inc.
# SPDX-License-Identifier: GPL-3.0-or-later
import pathlib
import unittest

import bpy
from helpers import addon, addon_name, submodule


class RegisterTests(unittest.TestCase):
    def test_extension_enabled_and_prefs_have_defaults(self):
        mod = addon()
        import re

        manifest = re.search(
            r'^version = "([^"]+)"',
            (pathlib.Path(mod.__file__).parent / "blender_manifest.toml").read_text(),
            re.M,
        ).group(1)
        self.assertEqual(mod.__version__, manifest)
        prefs = bpy.context.preferences.addons[addon_name()].preferences
        self.assertNotIn("output_dir", prefs.bl_rna.properties)
        self.assertEqual(prefs.mcp_port, 9876)
        self.assertFalse(prefs.mcp_allow_python)

    def test_runtime_paths_live_outside_the_extension_dir(self):
        runtime = submodule("blender.runtime")
        paths = runtime.paths()
        self.assertTrue(str(paths.state_dir).endswith("state"))
        self.assertNotIn(
            "extensions/user_default/open_studio/", str(paths.state_dir).replace("\\", "/") + "/"
        )
        self.assertTrue(paths.state_dir.exists())

    def test_preferences_do_not_register_service_credentials(self):
        prefs = bpy.context.preferences.addons[addon_name()].preferences
        for name in ("api_key", "api_secret", "project_id", "provider_mode", "provider_profiles"):
            self.assertNotIn(name, prefs.bl_rna.properties)
