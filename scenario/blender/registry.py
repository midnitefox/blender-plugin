# SPDX-FileCopyrightText: 2026 Scenario Inc.
# SPDX-FileCopyrightText: 2026 Open Studio contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Register only local assistant, scene and camera controls."""

import logging

import bpy

from .. import prefs

log = logging.getLogger("open_studio")
_log_handler = None


def _modules():
    from . import (
        assistant_materials,
        assistant_ui,
        desktop,
        file_state,
        provider_setup,
        shot_planner,
    )

    return (file_state, desktop, shot_planner, provider_setup, assistant_materials, assistant_ui)


def apply_log_level(level):
    log.setLevel(logging.DEBUG if level == "DEBUG" else logging.INFO)


def register():
    global _log_handler
    if _log_handler is None:
        _log_handler = logging.StreamHandler()
        log.addHandler(_log_handler)
    for cls in prefs.CLASSES:
        bpy.utils.register_class(cls)
    for module in _modules():
        module.register()
    from . import assistant_loop, mcp_service, runtime

    apply_log_level(runtime.prefs().log_level)
    assistant_loop.start()
    if not bpy.app.background:
        mcp_service.start()
    try:
        runtime.state.cli_handle = bpy.utils.register_cli_command(
            mcp_service.CLI_COMMAND, mcp_service.cli
        )
    except (AttributeError, ValueError, RuntimeError) as error:
        log.warning("Could not register the local assistant command: %s", error)
    log.info("Open Studio registered")


def unregister():
    global _log_handler
    from . import assistant_loop, mcp_service, runtime

    if runtime.state.cli_handle is not None:
        try:
            bpy.utils.unregister_cli_command(runtime.state.cli_handle)
        except (AttributeError, ValueError, RuntimeError, TypeError):
            pass
        runtime.state.cli_handle = None
    assistant_loop.stop()
    mcp_service.stop()
    for module in reversed(_modules()):
        module.unregister()
    for cls in reversed(prefs.CLASSES):
        bpy.utils.unregister_class(cls)
    runtime.shutdown()
    if _log_handler is not None:
        log.removeHandler(_log_handler)
        _log_handler = None
