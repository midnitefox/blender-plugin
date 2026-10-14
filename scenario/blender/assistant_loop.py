# SPDX-FileCopyrightText: 2026 Open Studio contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Deliver local assistant requests on Blender's main thread."""

import logging

import bpy

from . import file_state, mcp_service, runtime

log = logging.getLogger("open_studio")


def tick():
    try:
        if not runtime.online():
            mcp_service.stop()
        else:
            mcp_service.process_pending()
        file_state.flush()
    except Exception:
        log.exception("Local assistant request processing failed")
    return 0.1


def start():
    if not bpy.app.timers.is_registered(tick):
        bpy.app.timers.register(tick, first_interval=0.1, persistent=True)


def stop():
    if bpy.app.timers.is_registered(tick):
        bpy.app.timers.unregister(tick)
