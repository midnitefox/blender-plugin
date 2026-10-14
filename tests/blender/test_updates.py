# SPDX-FileCopyrightText: 2026 Scenario Inc.
# SPDX-License-Identifier: GPL-3.0-or-later
"""Independent fork updates open its own release page without repository changes."""

import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import bpy
from helpers import submodule


class UpdateTests(unittest.TestCase):
    def test_offline_controls_reject_opening_releases(self):
        self.assertFalse(bpy.app.online_access)
        self.assertFalse(bpy.ops.scenario.setup_updates.poll())
        self.assertFalse(bpy.ops.scenario.check_updates.poll())

    def test_update_actions_open_only_fork_releases(self):
        updates = submodule("blender.updates")
        opener = Mock(return_value={"FINISHED"})
        fake_bpy = SimpleNamespace(
            app=SimpleNamespace(online_access=True),
            ops=SimpleNamespace(scenario=SimpleNamespace(open_url=opener)),
        )
        operator = SimpleNamespace(report=Mock())
        with patch.object(updates, "bpy", fake_bpy):
            for cls in (updates.SCENARIO_OT_setup_updates, updates.SCENARIO_OT_check_updates):
                self.assertEqual(cls.execute(operator, None), {"FINISHED"})
            fake_bpy.app.online_access = False
            self.assertEqual(
                updates.SCENARIO_OT_check_updates.execute(operator, None), {"CANCELLED"}
            )
        self.assertEqual(opener.call_count, 2)
        opener.assert_called_with(url="https://github.com/midnitefox/blender-plugin/releases")

    def test_preferences_draw_fork_links_without_configuring_a_repository(self):
        updates = submodule("blender.updates")
        layout = Mock()
        updates.draw(layout, bpy.context)
        operators = layout.box.return_value.operator.call_args_list
        self.assertTrue(operators)
        self.assertTrue(all(call.args[0] == "scenario.open_url" for call in operators))
        self.assertEqual(
            updates.GUIDE_URL,
            "https://github.com/midnitefox/blender-plugin/blob/feat/provider-choice/docs/USER_GUIDE.md",
        )
