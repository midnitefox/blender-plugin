# SPDX-FileCopyrightText: 2026 Scenario Inc.
# SPDX-License-Identifier: GPL-3.0-or-later
"""Registers every Blender class of the extension in dependency order."""

import logging

import bpy

from .. import prefs

log = logging.getLogger("scenario")


_log_handler = None


def _modules():
    from . import (
        apply_video,
        blockout,
        composer,
        desktop,
        file_state,
        film,
        job_recovery,
        library_view,
        model_picker,
        operators,
        panels,
        panorama_lane,
        popover,
        prompt_tools,
        props,
        provider_setup,
        reference_form,
        result_media,
        result_previews,
        shot_planner,
        studio,
        trained_models,
        trained_section,
        workflow_controls,
        workflow_references,
        workflow_steps,
        workflow_uploads,
    )

    modules = [
        props,
        file_state,
        desktop,
        apply_video,
        result_media,
        result_previews,
        shot_planner,
        operators,
        provider_setup,
        job_recovery,
        workflow_steps,
        reference_form,
        panorama_lane,
        prompt_tools,
        blockout,
        film,
        model_picker,
        trained_models,
        trained_section,
        panels,
        workflow_controls,
        workflow_references,
        workflow_uploads,
        library_view,
        studio,
        popover,
        composer,
    ]
    try:
        from . import (
            icons,  # Scenario's modality icons; optional so a build without the PNGs still loads
        )

        modules.insert(0, icons)
    except ImportError:
        pass
    return modules


def _configure_logging():
    global _log_handler
    if _log_handler is not None:
        return
    _log_handler = logging.StreamHandler()
    _log_handler.setFormatter(logging.Formatter("%(levelname)s:%(name)s:%(message)s"))
    log.addHandler(_log_handler)
    log.propagate = False
    prefs = None
    try:
        prefs = __import__(__package__ + ".runtime", fromlist=["prefs"]).prefs()
    except Exception:
        pass
    log.setLevel(logging.DEBUG if (prefs and prefs.log_level == "DEBUG") else logging.INFO)


def apply_log_level(level):
    log.setLevel(logging.DEBUG if level == "DEBUG" else logging.INFO)


def register():
    _configure_logging()
    for cls in prefs.CLASSES:
        bpy.utils.register_class(cls)
    for module in _modules():
        module.register()
    from . import mcp_service, pump

    pump.start()
    if not bpy.app.background:
        mcp_service.start()
    try:
        runtime_module = __import__(__package__ + ".runtime", fromlist=["state"])
        runtime_module.state.cli_handle = bpy.utils.register_cli_command(
            mcp_service.CLI_COMMAND, mcp_service.cli
        )
    except (AttributeError, ValueError, RuntimeError) as err:
        log.warning("CLI command %r not registered: %s", mcp_service.CLI_COMMAND, err)
    log.info("Open Studio registered")


def unregister():
    from . import mcp_service, pump, runtime

    handle = getattr(runtime.state, "cli_handle", None)
    if handle is not None:
        try:
            bpy.utils.unregister_cli_command(handle)
        except (AttributeError, ValueError, RuntimeError, TypeError):
            pass
        runtime.state.cli_handle = None
    mcp_service.stop()
    pump.stop()
    for module in reversed(_modules()):
        module.unregister()
    for cls in reversed(prefs.CLASSES):
        bpy.utils.unregister_class(cls)
    runtime.shutdown()
    log.info("Open Studio unregistered")
    global _log_handler
    if _log_handler is not None:
        log.removeHandler(_log_handler)
        _log_handler = None
