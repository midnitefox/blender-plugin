# SPDX-FileCopyrightText: 2026 Scenario Inc.
# SPDX-License-Identifier: GPL-3.0-or-later
"""Folder and link buttons with a sanitized child-process environment.

Blender's ``wm.path_open`` and ``wm.url_open`` start ``open``, ``xdg-open``,
``osascript`` or ``os.startfile`` with Blender's whole environment, and the
file browser or web browser they start inherits it, including the
``SCENARIO_API_KEY`` and ``SCENARIO_API_SECRET`` that Credentials > Environment
reads. ``scenario.open_path`` and ``scenario.open_url`` replace them for every
folder, file and link button: they start ``core.jobs.playback``'s launchers,
which pass ``player_environment()`` instead. The operator has returned by the
time a launcher exits, so a timer reports a launcher that exits with an error
on the status line.
"""

import os

import bpy
from bpy.props import StringProperty

from ..core.jobs import playback
from . import runtime

POLL_INTERVAL = 0.25
# Polls before a launcher that has not exited is left to the subprocess module,
# which reaps it on a later launch; xdg-open can wait for the application.
POLL_LIMIT = 120
PATH_FAILURE = "The system could not open this file or folder"
URL_FAILURE = "The system could not open this link"
_launchers = []  # [process, failure message, polls left] for launchers still running


def _watch(process, failure):
    _launchers.append([process, failure, POLL_LIMIT])
    if not bpy.app.timers.is_registered(_reap):
        bpy.app.timers.register(_reap, first_interval=POLL_INTERVAL, persistent=True)


def _reap():
    """Report launchers that exited with an error; forget every exited one."""
    waiting = []
    for process, failure, polls in _launchers:
        try:
            code = process.poll()
        except Exception:  # an exception here would silently kill the timer
            code = 1
        if code is None and polls > 1:
            waiting.append([process, failure, polls - 1])
        elif code:
            runtime.set_message(failure)
    _launchers[:] = waiting
    return POLL_INTERVAL if waiting else None


def open_path(path):
    """Open one existing file or folder; ``ValueError`` names a refusal for the user."""
    if not path:
        raise ValueError("File path was not set")
    path = os.path.normpath(bpy.path.abspath(os.fspath(path)))
    if not os.path.exists(path):
        raise ValueError("File or folder not found")
    _watch(playback.system_open(path), PATH_FAILURE)


def open_url(url):
    """Open one https link in the default web browser."""
    _watch(playback.system_open_url(url), URL_FAILURE)


def report_open(operator, opener, target, failure):
    """Run ``opener`` on ``target`` for an operator, reporting a refusal or failed start."""
    try:
        opener(target)
    except (ValueError, playback.PlaybackError) as error:  # Refused before any launch.
        operator.report({"ERROR"}, str(error))
        return {"CANCELLED"}
    except (OSError, RuntimeError):  # No launcher could start.
        operator.report({"ERROR"}, failure)
        return {"CANCELLED"}
    return {"FINISHED"}


class SCENARIO_OT_open_path(bpy.types.Operator):
    bl_idname = "scenario.open_path"
    bl_label = "Open"
    bl_description = "Open with the system's default application"
    bl_options = {"INTERNAL"}
    filepath: StringProperty(subtype="FILE_PATH", options={"SKIP_SAVE"})

    def execute(self, context):
        return report_open(self, open_path, self.filepath, PATH_FAILURE)


class SCENARIO_OT_open_url(bpy.types.Operator):
    bl_idname = "scenario.open_url"
    bl_label = "Open link"
    bl_description = "Open this page in the default web browser"
    bl_options = {"INTERNAL"}
    url: StringProperty(name="URL", options={"SKIP_SAVE"})

    def execute(self, context):
        return report_open(self, open_url, self.url, URL_FAILURE)


CLASSES = (SCENARIO_OT_open_path, SCENARIO_OT_open_url)


def register():
    for cls in CLASSES:
        bpy.utils.register_class(cls)


def unregister():
    if bpy.app.timers.is_registered(_reap):
        bpy.app.timers.unregister(_reap)
    _launchers.clear()  # Running launchers are left to the subprocess module.
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)
