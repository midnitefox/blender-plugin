# SPDX-FileCopyrightText: 2026 Scenario Inc.
# SPDX-FileCopyrightText: 2026 Open Studio contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Local assistant connection and filesystem state, with no generation service."""

import logging
import os
import pathlib
import time
from dataclasses import dataclass

import bpy

from .. import prefs as prefs_module

log = logging.getLogger("open_studio")
PACKAGE = __package__.rsplit(".", 1)[0]


class RuntimeState:
    def __init__(self):
        self.mcp = None
        self.mcp_token = ""
        self.mcp_error = ""
        self.cli_handle = None
        self.last_message = ""
        self.message_at = 0.0

    def reset(self):
        self.__init__()


state = RuntimeState()


@dataclass(frozen=True)
class Paths:
    state_dir: pathlib.Path
    cache_dir: pathlib.Path
    output_dir: pathlib.Path


def prefs():
    return prefs_module.get_prefs()


def online():
    return bool(bpy.app.online_access)


def paths():
    state_dir = pathlib.Path(bpy.utils.extension_path_user(PACKAGE, path="state", create=True))
    cache_dir = pathlib.Path(bpy.utils.extension_path_user(PACKAGE, path="cache", create=True))
    output_dir = pathlib.Path(os.path.expanduser("~/Downloads/Open Studio"))
    return Paths(state_dir, cache_dir, output_dir)


def set_message(text):
    state.last_message, state.message_at = text, time.monotonic()
    log.info(text)


def message_visible(ttl=8.0):
    return state.last_message if time.monotonic() - state.message_at <= ttl else ""


def shutdown():
    if state.mcp is not None:
        state.mcp.stop()
    state.reset()
