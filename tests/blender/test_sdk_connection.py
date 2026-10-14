# SPDX-FileCopyrightText: 2026 Scenario Inc.
# SPDX-License-Identifier: GPL-3.0-or-later
"""Native asynchronous connection operator with synthetic SDK responses."""

import os
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

import bpy
import httpx
from helpers import isolated_manager, online_access, submodule, temp_credentials


class SDKConnectionTests(unittest.TestCase):
    def setUp(self):
        self.runtime = submodule("blender.runtime")
        self.generation = submodule("blender.generation")
        self.handlers = submodule("blender.handlers")
        self.enterContext(patch.object(self.runtime, "state", self.runtime.RuntimeState()))
        self.prefs = self.enterContext(temp_credentials())
        self.enterContext(online_access(True))
        self.manager = self.enterContext(isolated_manager())
        self.addCleanup(self.runtime.state.reset)
        self.calls = []
        self.status = 200
        self.entered, self.release = threading.Event(), threading.Event()
        self.release.set()
        self.addCleanup(self.release.set)
        adapter = submodule("core.api.sdk_adapter")

        def respond(request):
            self.assertIsNot(threading.current_thread(), threading.main_thread())
            self.calls.append(request)
            self.entered.set()
            self.assertTrue(self.release.wait(5))
            self.assertEqual((request.method, request.url.path), ("GET", "/v1/models"))
            self.assertEqual(dict(request.url.params), {"privacy": "public", "pageSize": "1"})
            return httpx.Response(self.status, json={"models": [], "private": "do-not-expose"})

        def factory(credentials, **kwargs):
            return adapter.SDKAdapter(credentials, transport=httpx.MockTransport(respond), **kwargs)

        self.enterContext(
            patch.object(submodule("core.api.sdk_catalog"), "SDKAdapter", side_effect=factory)
        )

    def completed(self):
        self.manager.join(5)
        self.assertFalse(self.manager.has_active())
        return self.manager.drain_catalog()

    def deliver(self):
        for event in self.completed():
            self.handlers.dispatch(event)

    def test_operator_returns_while_network_is_pending_and_repeated_clicks_share_check(self):
        self.release.clear()
        self.assertIsNotNone(self.runtime.request_connection_check())
        try:
            self.assertTrue(self.entered.wait(5))
            self.assertIn("Checking", self.runtime.state.account_label)
            key = self.runtime.state.connection_request
            self.assertIsNotNone(key)
            self.assertIsNotNone(self.runtime.request_connection_check())
            self.assertIs(self.runtime.state.connection_request, key)
            self.assertEqual(len(self.calls), 1)
        finally:
            self.release.set()
        self.deliver()
        self.assertEqual(
            self.runtime.state.account_label, "Connected to Scenario (model access verified)"
        )
        self.assertIsNone(self.runtime.state.connection_request)
        self.assertFalse(self.runtime.state.catalog_loaded)

    def test_failed_credentials_are_visible_without_private_response_details(self):
        self.status = 403
        self.runtime.request_connection_check()
        self.deliver()
        self.assertIn("Connection failed", self.runtime.state.account_label)
        self.assertIn("403", self.runtime.state.account_label)
        self.assertIn("Check the selected API key and secret", self.runtime.state.account_label)
        self.assertNotIn("do-not-expose", self.runtime.state.account_label)
        self.status = 200
        self.runtime.request_connection_check()
        self.deliver()
        self.assertIn("verified", self.runtime.state.account_label)

    def test_queued_old_success_and_failure_do_not_relabel_replacement_credentials(self):
        for status in (200, 403):
            with self.subTest(status=status):
                self.status = status
                self.runtime.request_connection_check()
                queued = self.completed()
                self.prefs.api_secret = f"replacement-{status}"
                self.runtime.state.account_label = "replacement label"
                for event in queued:
                    self.handlers.dispatch(event)
                self.assertEqual(self.runtime.state.account_label, "replacement label")
                self.assertIsNone(self.runtime.state.connection_request)

    def test_environment_change_invalidates_connection_label_during_sync(self):
        saved = self.prefs.credential_source
        try:
            with patch.dict(
                os.environ,
                {"SCENARIO_API_KEY": "launch-key", "SCENARIO_API_SECRET": "launch-secret"},
            ):
                self.prefs.credential_source = "ENVIRONMENT"
                self.runtime.request_connection_check()
                self.deliver()
                self.assertIn("verified", self.runtime.state.account_label)
                os.environ["SCENARIO_API_SECRET"] = "other-secret"
                self.runtime.sync_catalog_context()
                self.assertEqual(self.runtime.state.account_label, "")
        finally:
            self.prefs.credential_source = saved

    def test_runtime_reset_rejects_a_queued_connection_result(self):
        self.runtime.request_connection_check()
        queued = self.completed()
        self.runtime.state.reset()
        for event in queued:
            self.handlers.dispatch(event)
        self.assertEqual(self.runtime.state.account_label, "")

    def test_offline_and_missing_credentials_fail_before_scheduling(self):
        with online_access(False):
            with self.assertRaisesRegex(Exception, "Online Access"):
                self.runtime.request_connection_check()
        self.prefs.api_secret = ""
        with self.assertRaisesRegex(Exception, "selected connection"):
            self.runtime.request_connection_check()
        self.assertFalse(self.calls)
        self.assertIsNone(self.runtime.state.connection_request)

    def test_thread_start_failure_does_not_leave_a_stuck_check(self):
        with patch.object(
            self.manager, "check_connection", side_effect=RuntimeError("private detail")
        ):
            with self.assertRaisesRegex(Exception, "Could not start"):
                self.runtime.request_connection_check()
        self.assertIsNone(self.runtime.state.connection_request)
        self.assertEqual(self.runtime.state.account_label, "")
        self.assertFalse(self.calls)

    def test_connection_check_preserves_existing_catalog(self):
        catalog = self.runtime.ensure_catalog()
        self.runtime.state.catalog_loaded = True
        self.runtime.state.records["fixture-model"] = object()
        self.runtime.request_connection_check()
        self.manager.join(5)
        self.generation.process_catalog_events()
        self.assertIs(self.runtime.state.catalog, catalog)
        self.assertTrue(self.runtime.state.catalog_loaded)
        self.assertIn("fixture-model", self.runtime.state.records)

    def test_background_operator_delivers_success_and_error_without_a_gui_pump(self):
        self.assertTrue(bpy.app.background)
        for status, expected in ((200, "verified"), (403, "Connection failed")):
            with self.subTest(status=status):
                self.status = status
                self.assertEqual(bpy.ops.scenario.test_connection(), {"FINISHED"})
                self.assertIn(expected, self.runtime.state.account_label)
                self.assertIsNone(self.runtime.state.connection_request)
                self.assertIsNone(self.runtime.state.connection_worker)
                self.assertFalse(self.manager.has_active())

    def test_account_icon_tracks_check_state_even_with_a_loaded_catalog(self):
        self.runtime.state.catalog_loaded = True
        panels = submodule("blender.panels")
        for status, expected in (
            ("pending", "SORTTIME"),
            ("error", "ERROR"),
            ("success", "CHECKMARK"),
        ):
            with self.subTest(status=status):
                self.runtime.state.connection_status = status
                layout = Mock()
                self.assertTrue(panels.draw_account_strip(layout, bpy.context))
                self.assertEqual(layout.row.return_value.label.call_args.kwargs["icon"], expected)

    def test_permission_guidance_wraps_instead_of_clipping(self):
        message = (
            "Access denied (HTTP 403). Check the selected API key and secret, and that the "
            "Project ID belongs to this key, or clear it to use the key's default scope."
        )
        label = f"Connection failed: {message}"
        prefs = submodule("prefs")
        panels = submodule("blender.panels")
        self.runtime.state.connection_status = "error"
        self.runtime.state.account_label = label
        self.runtime.state.catalog_error = message
        owner = SimpleNamespace(
            layout=Mock(), credential_source="ENVIRONMENT", composer_enabled=True
        )
        with patch.object(prefs.updates, "draw"):
            prefs.ScenarioPreferences.draw(owner, bpy.context)
        lane = SimpleNamespace(estimate_state="ERROR", estimate_error=message, last_error=message)
        loading, generate_row = Mock(), Mock()
        panels.draw_loading(loading)
        with (
            patch.object(panels, "generate_enabled", return_value=False),
            patch.object(panels, "generate_button_text", return_value="Generate"),
        ):
            panels.draw_generate_row(generate_row, lane, "image")
        # A failed model-description read keeps its "Could not load this model: " prefix.
        failure = f"Could not load this model: {message}"
        schema_lane = SimpleNamespace(model_id="fixture-model", last_error=failure)
        self.runtime.state.model_errors[schema_lane.model_id] = failure
        schema_failed, schema_offline = Mock(), Mock()
        panels.draw_schema_status(schema_failed, schema_lane, "image")
        with patch.object(self.runtime, "online", return_value=False):
            panels.draw_schema_status(schema_offline, schema_lane, "image")
        status = "Access denied (HTTP 403). Check the"
        for drawn, text, width, icons, first in (
            (owner.layout.box.return_value.label, label, 70, ["ERROR"], status),
            (loading.label, message, panels.SIDEBAR_CHARS, ["ERROR"], status),
            (generate_row.label, message, panels.SIDEBAR_CHARS, ["INFO", "ERROR"], status),
            (schema_failed.label, failure, panels.SIDEBAR_CHARS, ["ERROR"], "Could not load"),
        ):
            with self.subTest(width=width, icons=icons):
                blocks = wrapped_blocks(drawn, text)
                self.assertEqual([block[0][1] for block in blocks], icons)
                for block in blocks:
                    self.assertGreater(len(block), 2)
                    self.assertTrue(all(len(line) <= width for line, _ in block))
                    self.assertEqual([icon for _, icon in block[1:]], ["NONE"] * (len(block) - 1))
                    # The status (or the failed-read prefix) starts the first line.
                    self.assertIn(first, block[0][0])
        schema_failed.operator.assert_called_once_with(
            "scenario.retry_model", text="Retry loading model", icon="FILE_REFRESH"
        )
        # The offline refusal wraps too, without a retry that could not read.
        offline = wrapped_blocks(schema_offline.label, self.generation.MODEL_OFFLINE)
        self.assertEqual([block[0][1] for block in offline], ["ERROR"])
        self.assertGreater(len(offline[0]), 1)
        self.assertTrue(all(len(line) <= panels.SIDEBAR_CHARS for line, _ in offline[0]))
        schema_offline.operator.assert_not_called()
        self.runtime.state.connection_status = "pending"
        self.runtime.state.account_label = "Checking Scenario connection..."
        owner.layout = Mock()
        with patch.object(prefs.updates, "draw"):
            prefs.ScenarioPreferences.draw(owner, bpy.context)
        owner.layout.box.return_value.label.assert_any_call(
            text="Checking Scenario connection...", icon="INFO"
        )
        # Without a failure recorded this session, the saved lane error is not drawn.
        self.runtime.state.model_errors.clear()
        schema_idle = Mock()
        panels.draw_schema_status(schema_idle, schema_lane, "image")
        schema_idle.label.assert_called_once_with(
            text="The model description is not loaded", icon="INFO"
        )


def wrapped_blocks(labels, text):
    """Contiguous label runs whose lines rejoin to text, as (line, icon) pairs."""
    entries = [
        (entry.kwargs.get("text", ""), entry.kwargs.get("icon")) for entry in labels.call_args_list
    ]
    blocks, start = [], 0
    while start < len(entries):
        for end in range(start + 1, len(entries) + 1):
            if " ".join(line for line, _ in entries[start:end]) == text:
                blocks.append(entries[start:end])
                start = end
                break
        else:
            start += 1
    return blocks
