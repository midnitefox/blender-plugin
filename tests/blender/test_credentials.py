# SPDX-FileCopyrightText: 2026 Scenario Inc.
# SPDX-License-Identifier: GPL-3.0-or-later
"""Credential source selection through the installed Blender preferences."""

import os
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import bpy
from helpers import submodule


class CredentialTests(unittest.TestCase):
    def test_selected_source_is_atomic_and_missing_preferences_fail_closed(self):
        runtime = submodule("blender.runtime")
        prefs = runtime.prefs()
        saved = prefs.credential_source, prefs.api_key, prefs.api_secret
        label = runtime.state.account_label
        try:
            prefs.api_key, prefs.api_secret = "saved-key", "saved-secret"
            env = {"SCENARIO_API_KEY": "launch-key", "SCENARIO_API_SECRET": "launch-secret"}
            with patch.dict(os.environ, env, clear=True):
                prefs.credential_source = "PREFERENCES"
                self.assertEqual(runtime.credentials().key, "saved-key")
                runtime.state.account_label = "Previously verified account"
                prefs.credential_source = "ENVIRONMENT"
                self.assertEqual(runtime.state.account_label, "")
                self.assertEqual(runtime.credentials().key, "launch-key")
                self.assertEqual(runtime.credentials().secret, "launch-secret")
                del os.environ["SCENARIO_API_SECRET"]
                self.assertFalse(runtime.credentials().valid)
                with self.assertRaisesRegex(Exception, "selected connection"):
                    runtime.ensure_catalog()
                with patch.object(runtime, "prefs", return_value=None):
                    self.assertFalse(runtime.credentials().valid)
                prefs.credential_source = "PREFERENCES"
                self.assertTrue(runtime.credentials().valid)
        finally:
            prefs.credential_source, prefs.api_key, prefs.api_secret = saved
            runtime.state.account_label = label

    def test_missing_credentials_banner_matches_selected_source(self):
        runtime = submodule("blender.runtime")
        panels = submodule("blender.panels")
        prefs = runtime.prefs()
        saved = prefs.credential_source, prefs.api_key, prefs.api_secret
        label = runtime.state.account_label
        try:
            with patch.dict(os.environ, {}, clear=True):
                prefs.api_key, prefs.api_secret = "saved-key", "saved-secret"
                prefs.credential_source = "ENVIRONMENT"
                layout = Mock()
                self.assertFalse(panels.draw_account_strip(layout, None))
                layout.row.return_value.label.assert_called_once_with(
                    text="Environment key/secret missing", icon="ERROR"
                )
                prefs.api_key = prefs.api_secret = ""
                prefs.credential_source = "PREFERENCES"
                layout = Mock()
                self.assertFalse(panels.draw_account_strip(layout, None))
                layout.row.return_value.label.assert_called_once_with(
                    text="Add key and secret in Preferences", icon="ERROR"
                )
        finally:
            prefs.credential_source, prefs.api_key, prefs.api_secret = saved
            runtime.state.account_label = label

    def test_defaults_and_password_fields_are_registered(self):
        prefs = submodule("blender.runtime").prefs()
        properties = prefs.bl_rna.properties
        self.assertEqual(properties["credential_source"].default, "PREFERENCES")
        self.assertEqual(properties["project_id"].default, "")
        for field in ("api_key", "api_secret"):
            self.assertEqual(properties[field].subtype, "PASSWORD")

    def test_saved_password_values_are_plain_text_in_isolated_preferences(self):
        prefs = submodule("blender.runtime").prefs()
        saved = prefs.credential_source, prefs.api_key, prefs.api_secret
        key, secret = "privacy-fixture-key-unique", "privacy-fixture-secret-unique"
        try:
            prefs.credential_source = "PREFERENCES"
            prefs.api_key, prefs.api_secret = key, secret
            bpy.ops.wm.save_userpref()
            raw = (Path(bpy.utils.user_resource("CONFIG")) / "userpref.blend").read_bytes()
            self.assertIn(key.encode(), raw)
            self.assertIn(secret.encode(), raw)
        finally:
            prefs.credential_source, prefs.api_key, prefs.api_secret = saved
            bpy.ops.wm.save_userpref()

    def test_environment_pair_is_not_copied_into_saved_preferences(self):
        runtime = submodule("blender.runtime")
        prefs = runtime.prefs()
        saved = prefs.credential_source, prefs.api_key, prefs.api_secret
        key, secret = "privacy-env-key-unique", "privacy-env-secret-unique"
        try:
            prefs.credential_source = "ENVIRONMENT"
            prefs.api_key = prefs.api_secret = ""
            with patch.dict(os.environ, {"SCENARIO_API_KEY": key, "SCENARIO_API_SECRET": secret}):
                self.assertEqual(runtime.credentials().key, key)
                bpy.ops.wm.save_userpref()
            raw = (Path(bpy.utils.user_resource("CONFIG")) / "userpref.blend").read_bytes()
            self.assertNotIn(key.encode(), raw)
            self.assertNotIn(secret.encode(), raw)
        finally:
            prefs.credential_source, prefs.api_key, prefs.api_secret = saved
            bpy.ops.wm.save_userpref()
