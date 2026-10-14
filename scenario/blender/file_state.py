# SPDX-FileCopyrightText: 2026 Scenario Inc.
# SPDX-License-Identifier: GPL-3.0-or-later
"""Mark the open file modified after a Scenario change that no Undo operator records.

Blender asks to save on quit only for a file marked modified, and only an undo push
marks it: assigning data from Python, a timer or a local MCP tool leaves the file
looking saved. An operator that declares Undo records its own step. Scenario's other
main-thread changes (local MCP tools and the results, Film builds and plans that the
maintenance pump delivers) call `mark_modified` once the change is complete.

The push records one Edit > Undo History step. It tags no data-block and runs no
dependency-graph, undo or redo handler, so origins, quotes and approvals captured
before it stay current.
"""

import logging
import threading
from contextlib import contextmanager

import bpy
from bpy.app.handlers import persistent

_log = logging.getLogger("scenario.blender")

# Blender opens an undo step when a sculpt, paint or paint-curve stroke starts and
# closes it when the stroke ends. A push in between would close that step under
# another name and split the stroke's history, so the mark waits for the stroke.
_STROKE_FAMILIES = frozenset({"SCULPT", "PAINT", "PAINTCURVE", "SCULPT_CURVES", "GREASE_PENCIL"})

_pending = None  # the message of a mark waiting for a stroke to end


def _family(idname):
    """`SCULPT_OT_brush_stroke` or `sculpt.brush_stroke` -> `SCULPT`."""
    family = idname.split("_OT_", 1)[0] if "_OT_" in idname else idname.split(".", 1)[0]
    return family.upper()


def _stroke_running(windows):
    for window in windows:
        for operator in getattr(window, "modal_operators", ()):
            if _family(operator.bl_idname or "") in _STROKE_FAMILIES:
                return True
    return False


def _window(context):
    """The context's window, or the first one: Blender's timers run with that one."""
    if context.window is not None and context.screen is not None:
        return context.window
    manager = context.window_manager
    return next((w for w in manager.windows if w.screen is not None), None) if manager else None


def _push(message):
    context = bpy.context
    window = _window(context)
    if window is None:
        _log.warning("Open Studio changed the scene, but Blender has no window to mark it modified")
        return False
    try:
        if window == context.window and context.screen is not None:
            result = bpy.ops.ed.undo_push(message=message)
        else:
            with context.temp_override(window=window, screen=window.screen):
                result = bpy.ops.ed.undo_push(message=message)
    except Exception:
        # The change is complete. A missing mark is not a failed change and must
        # never authorize repeating it.
        _log.warning("Open Studio changed the scene, but Blender could not mark it modified")
        return False
    return result == {"FINISHED"}


def mark_modified(message):
    """Record one completed change as an undo step, which marks the file modified.

    Call it on the main thread after the change, never while drawing and never
    from an operator that declares Undo: that operator records its own step.
    Returns whether the step was recorded now; during a sculpt or paint stroke
    it is recorded by the first `flush` after the stroke. It never raises.
    """
    global _pending
    if threading.current_thread() is not threading.main_thread():
        raise RuntimeError("Mark the file modified on Blender's main thread")
    manager = bpy.context.window_manager
    if manager is not None and _stroke_running(manager.windows):
        _pending = message
        return False
    _pending = None
    return _push(message)


@contextmanager
def marking(message, read):
    """Mark the file modified when ``read()`` differs after the block, even if it raised.

    ``read`` returns a comparable reading of the data the block may change. A
    refusal before any change leaves it equal and the file unmarked; a partial
    change before a failure still marks it. Data that can no longer be read
    counts as changed.
    """
    before = read()
    try:
        yield
    finally:
        try:
            changed = read() != before
        except Exception:
            changed = True
        if changed:
            mark_modified(message)


def flush():
    """Record a mark that waited for a stroke, once no stroke is running (main thread)."""
    global _pending
    if _pending is None:
        return False
    manager = bpy.context.window_manager
    if manager is not None and _stroke_running(manager.windows):
        return False
    message, _pending = _pending, None
    return _push(message)


@persistent
def _load_pre(*_):
    # A waiting mark belongs to the file being closed, never to the next one.
    global _pending
    _pending = None


def register():
    if _load_pre not in bpy.app.handlers.load_pre:
        bpy.app.handlers.load_pre.append(_load_pre)


def unregister():
    global _pending
    _pending = None
    if _load_pre in bpy.app.handlers.load_pre:
        bpy.app.handlers.load_pre.remove(_load_pre)
