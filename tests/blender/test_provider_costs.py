# SPDX-FileCopyrightText: 2026 Open Studio contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Installed native/MCP quote boundaries preserve unknown provider billing."""

import json
import unittest
from concurrent.futures import Future
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import Mock, patch

import bpy
from helpers import reset_scene, submodule


class ProviderCostTests(unittest.TestCase):
    def setUp(self):
        reset_scene()
        self.runtime = submodule("blender.runtime")
        self.tools = submodule("mcp.tools_scenario")
        self.models = submodule("blender.model_jobs")
        self.error = submodule("core.api.errors").ScenarioError
        self.enterContext(patch.object(self.runtime, "state", self.runtime.RuntimeState()))
        self.future = Future()
        self.future.set_result(None)
        self.quote = submodule("core.api.sdk_adapter").Estimate(
            "model",
            "openai-image",
            None,
            None,
            b'{"prompt":"A castle"}',
            b'{"provider_billed":true,"cost_label":"Provider billed; review pricing"}',
            object(),
            0.0,
        )
        self.origin_quote = SimpleNamespace(estimate=self.quote, mesh_sources=(), film_task=None)
        self.ticket = SimpleNamespace(
            identifier="test-provider-quote",
            quote=self.origin_quote,
            used=False,
            scene=bpy.context.scene,
            operation="model",
            lane="image",
            model_id="openai-image",
            inputs=self.models._snapshot({"prompt": "A castle"}),
            task=self.future,
        )
        session = SimpleNamespace(active=True)
        self.owner = self.models.ModelJobs(session, None, online=lambda: True)
        self.owner.quotes[self.ticket.identifier] = self.ticket
        self.enterContext(patch.object(self.runtime, "ensure_model_jobs", return_value=self.owner))

    def test_mcp_quote_exposes_explicit_provider_approval_and_no_creative_units(self):
        record = SimpleNamespace(id="openai-image")
        with (
            patch.object(
                self.tools, "_generation_input", return_value=(record, self.quote.payload, None)
            ),
            patch.object(self.owner, "quote", return_value=self.ticket),
        ):
            deferred = self.tools.estimate_cost({"lane": "image"})
            result = deferred.finish(deferred.run())
        self.assertIsNone(result["cu_cost"])
        self.assertIsNone(result["cu_cost_exact"])
        self.assertTrue(result["provider_billed"])
        self.assertEqual(result["approved_cost"], "provider-billed")
        self.assertEqual(result["cost_label"], "Provider billed; review pricing")

    def test_zero_or_absent_approval_cannot_submit_unknown_provider_cost(self):
        with patch.object(self.owner, "_submit_quote") as submit:
            for approval in (None, "0", "None"):
                with self.subTest(approval=approval), self.assertRaises(self.error):
                    self.owner.submit(
                        self.ticket.identifier,
                        bpy.context.scene,
                        "openai-image",
                        self.quote.payload,
                        approved_cost=approval,
                    )
            submit.assert_not_called()
            self.owner.submit(
                self.ticket.identifier,
                bpy.context.scene,
                "openai-image",
                self.quote.payload,
                approved_cost="provider-billed",
            )
            submit.assert_called_once()
        self.assertTrue(self.ticket.used)

    def test_native_view_keeps_unknown_cost_and_submission_payload(self):
        prepared = SimpleNamespace(intent=SimpleNamespace(request_id="provider-job"))
        self.owner.session.prepare_quote = Mock(return_value=prepared)
        self.owner.session.submit = Mock(return_value=self.future)
        view = self.owner._submit_quote(self.origin_quote, lane="image", kind="image")
        self.assertIsNone(view.cu_cost)
        self.assertEqual(view.body, {"prompt": "A castle"})
        self.owner.session.submit.assert_called_once()
        self.assertIn("provider-job", self.owner.views)

    def test_native_quote_delivery_sets_ready_without_a_fabricated_price(self):
        generation = submodule("blender.generation")
        lane = bpy.context.scene.scenario.lane_state("image")
        lane.estimate_key = "provider-test"
        lane.estimate_state = "PENDING"
        self.runtime.state.model_jobs = self.owner
        self.runtime.state.model_previews["provider-test"] = self.ticket
        with (
            patch.object(generation, "_prune_ui_estimates"),
            patch.object(self.owner, "poll"),
            patch.object(self.runtime, "show_job_views"),
        ):
            generation.process_model_jobs()
        self.assertEqual(lane.estimate_state, "READY")
        self.assertEqual(lane.estimate_cu, -1.0)
        self.assertIs(self.runtime.state.estimates["provider-test"], self.ticket)

    def test_known_price_retains_exact_decimal_approval(self):
        quote = submodule("core.api.sdk_adapter").Estimate(
            "model",
            "model-known",
            None,
            Decimal("1.234567890123456789"),
            b"{}",
            b"{}",
            object(),
            0.0,
        )
        fields = submodule("core.ui.costs").quote_cost_fields(quote)
        self.assertEqual(fields["cu_cost_exact"], "1.234567890123456789")
        self.assertEqual(fields["approved_cost"], fields["cu_cost_exact"])
        self.assertFalse(fields["provider_billed"])
        self.assertIsInstance(json.dumps(fields), str)
