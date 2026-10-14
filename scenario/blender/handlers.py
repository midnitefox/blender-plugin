# SPDX-FileCopyrightText: 2026 Scenario Inc.
# SPDX-License-Identifier: GPL-3.0-or-later
"""Applies job-manager events on the main thread."""

import logging

import bpy

from . import generation, props, runtime, trained_models

log = logging.getLogger("scenario.handlers")


def dispatch(event):
    name, payload = event
    if (
        name
        in {
            "catalog",
            "catalog_failed",
            "models",
            "connection",
            "trained_catalog",
            "trained_catalog_failed",
            "trained_bases",
        }
        and isinstance(payload, dict)
        and "catalog" in payload
    ):
        runtime.sync_catalog_context()
        if payload["catalog"] is not runtime.state.catalog:
            return
    if name == "connection":
        if payload.get("key") is not runtime.state.connection_request:
            return
        runtime.state.connection_request = None
        runtime.state.connection_worker = None
        error = payload.get("error")
        runtime.state.connection_status = "error" if error else "success"
        runtime.state.account_label = (
            f"Connection failed: {error}"
            if error
            else "Connected to Scenario (model access verified)"
        )
        runtime.set_message(runtime.state.account_label)
    elif name == "catalog":
        generation.set_catalog(
            payload["records"], payload["detailed"], warmup=payload.get("warmup", False)
        )
    elif name == "catalog_failed":
        payload = payload["error"] if isinstance(payload, dict) else payload
        runtime.state.catalog_loading = False
        runtime.state.catalog_error = str(payload)
        runtime.set_message(f"Could not load models: {payload}")
    elif name == "models":
        generation.set_models(
            payload["detailed"], payload["failed"], mark_dirty=payload.get("mark_dirty", True)
        )
    elif name == "trained_catalog":
        trained_models.on_catalog(payload["pairs"], payload.get("private", ()))
    elif name == "trained_catalog_failed":
        trained_models.on_catalog_failed(payload["error"])
    elif name == "trained_bases":
        trained_models.on_bases(payload)
    elif name == "estimate":
        _on_estimate(payload)
    elif name in ("job", "job_done", "job_failed"):
        _on_job(name, payload)
    elif name == "history":
        from . import history

        history.on_history_event(payload)
    elif name == "prompt":
        try:
            from . import prompt_tools

            prompt_tools.on_prompt_event(payload)
        except ImportError:
            runtime.set_message("Prompt tools are not installed")
    elif name == "blockout_plan":
        from . import blockout

        blockout.on_blockout_plan(payload)
    elif name == "error":
        runtime.set_message(str(payload))


def _on_estimate(result):
    runtime.sync_catalog_context()
    if result.catalog is None or result.catalog is not runtime.state.catalog:
        return
    origin = runtime.state.estimate_origins.pop(result.key, None)
    if origin is None:
        return
    log.info("estimate result %s cu=%s error=%s", result.key, result.cu_cost, result.error)
    scene, lane = origin
    try:
        lane_state = scene.scenario.lane_state(lane)
        if lane_state.estimate_key != result.key:
            return
        if result.error:
            runtime.state.estimates.pop(result.key, None)
            lane_state.estimate_state = "ERROR"
            lane_state.estimate_error = result.error
        else:
            lane_state.estimate_state = "READY"
            lane_state.estimate_cu = float(result.cu_cost)
            lane_state.estimate_error = ""
            runtime.state.estimates[result.key] = result.quote
    except ReferenceError:
        return  # The originating scene was deleted or replaced by a file load.
    current_keys = {
        scene.scenario.lane_state(lane).estimate_key
        for scene in bpy.data.scenes
        for lane in props.GENERATION_LANES
    }
    for key in list(runtime.state.estimates):
        if key not in current_keys:
            del runtime.state.estimates[key]


def _on_job(name, rec):
    runtime.show_job_views((rec,))
    if name == "job_done":
        runtime.set_message(
            "Prototype result was not applied. Recover the cloud job in the selected "
            "account, then approve its destination."
        )
    elif name == "job_failed":
        runtime.set_message(f"Generation failed: {rec.error or rec.status}")
