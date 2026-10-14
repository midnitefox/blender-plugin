# SPDX-FileCopyrightText: 2026 Scenario Inc.
# SPDX-License-Identifier: GPL-3.0-or-later
import pathlib
import unittest

import bpy
from helpers import addon, addon_name, submodule, temp_credentials


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
        self.assertEqual(prefs.bl_rna.properties["output_dir"].default, "~/Downloads/Open Studio")
        self.assertEqual(prefs.mcp_port, 9876)
        self.assertTrue(prefs.composer_enabled)

    def test_runtime_paths_live_outside_the_extension_dir(self):
        runtime = submodule("blender.runtime")
        paths = runtime.paths()
        self.assertTrue(str(paths.state_dir).endswith("state"))
        self.assertNotIn(
            "extensions/user_default/open_studio/", str(paths.state_dir).replace("\\", "/") + "/"
        )
        self.assertTrue(paths.state_dir.exists())

    def test_credentials_resolve_from_prefs(self):
        runtime = submodule("blender.runtime")
        with temp_credentials():
            self.assertTrue(runtime.credentials().valid)
