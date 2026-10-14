# SPDX-FileCopyrightText: 2026 Scenario Inc.
# SPDX-License-Identifier: GPL-3.0-or-later
"""Blender-side offline fixture for capture_gui.py; never submits service operations."""

import base64
import datetime
import importlib
import json
import os
import platform
import subprocess
import sys
import tempfile
import tomllib
import traceback
from pathlib import Path

import bpy
import gpu

OUTPUT, INSTALLED, VIEW, LANE, DELAY, FIXTURE, CAPTURE_BACKEND = sys.argv[
    sys.argv.index("--") + 1 :
]
OUTPUT = Path(OUTPUT)
EXTENSION_ID = Path(INSTALLED).name
DISPLAY_NAME = "Open Studio"
FAILED = False


def view3d():
    window = bpy.context.window_manager.windows[0]
    area = next(a for a in window.screen.areas if a.type == "VIEW_3D")
    return window, area, next(r for r in area.regions if r.type == "UI")


def guarded(callback):
    def run():
        global FAILED
        if FAILED:
            return None
        try:
            return callback()
        except Exception:
            FAILED = True
            (OUTPUT / "gui.json").write_text(
                json.dumps({"status": "failed", "error": traceback.format_exc()})
            )
            traceback.print_exc()
            bpy.ops.wm.quit_blender()
        return None

    return run


def prepare():
    global DISPLAY_NAME

    DISPLAY_NAME = tomllib.loads((Path(INSTALLED) / "blender_manifest.toml").read_text())["name"]
    print("Capture GPU:", gpu.platform.backend_type_get(), gpu.platform.renderer_get(), flush=True)
    if bpy.app.online_access or os.environ.get("SCENARIO_GUI_PROBE") != "1":
        raise RuntimeError("Capture requires offline mode and the GUI probe guard")
    if VIEW == "composer" and LANE == "audio":
        raise RuntimeError("The composer has no audio lane")
    name = next(n for n in bpy.context.preferences.addons.keys() if n.endswith("." + EXTENSION_ID))
    module = importlib.import_module(name)
    if Path(module.__file__).resolve().parent != Path(INSTALLED).resolve():
        raise RuntimeError("Extension loaded outside verified installation")
    window, area, _ = view3d()
    area.spaces.active.show_region_ui = VIEW == "sidebar"
    runtime = importlib.import_module(name + ".blender.runtime")
    # All writable plugin paths remain inside this capture, including any manager
    # created by the ordinary GUI pump. These credentials are deliberately fake.
    runtime.prefs().output_dir = str(OUTPUT / "profile" / "outputs")
    if FIXTURE == "form":
        runtime.prefs().provider_mode = "SCENARIO"
        runtime.prefs().api_key = runtime.prefs().api_secret = "offline-screenshot-fixture"
        catalog = importlib.import_module(name + ".core.api.catalog")
        handlers = importlib.import_module(name + ".blender.handlers")
        record = catalog.ModelRecord.from_api(
            {
                "id": "model_offline-screenshot",
                "name": "Offline fixture",
                "type": "custom",
                "capabilities": [
                    {
                        "image": "txt2img",
                        "video": "txt2video",
                        "3d": "txt23d",
                        "audio": "txt2audio",
                    }[LANE]
                ],
                "inputs": [
                    {
                        "name": "prompt",
                        "type": "string",
                        "prompt": True,
                        "required": {"always": True},
                    },
                    {"name": "seed", "type": "integer", "default": 42},
                ],
            }
        )
        handlers.dispatch(
            ("catalog", {"privacy": "public", "records": [record], "detailed": [record]})
        )
        lane_state = bpy.context.scene.scenario.lane_state(LANE)
        lane_state.model_id = record.id
        lane_state.model_key = record.id
        runtime.state.account_label = "Offline screenshot fixture"
    bpy.context.scene.scenario.lane = LANE
    bpy.context.scene.scenario.lane_state(
        LANE
    ).prompt = "A small ceramic robot tending a rooftop garden"
    if VIEW == "composer":
        state = runtime.state.composer
        state.expanded = True
        state.sync_from_lane(bpy.context.scene)
        state.focused = False
    area.tag_redraw()


def select():
    window, area, region = view3d()
    if VIEW == "sidebar":
        with bpy.context.temp_override(window=window, area=area, region=region):
            # Software-rendered CI can reach this timer before the first sidebar
            # draw. Blender keeps the dynamic category property read-only until
            # that draw has populated its enum items.
            bpy.ops.wm.redraw_timer(type="DRAW_WIN_SWAP", iterations=2)
            region.active_panel_category = DISPLAY_NAME
            region.tag_redraw()
            bpy.ops.wm.redraw_timer(type="DRAW_WIN_SWAP", iterations=2)
    area.tag_redraw()


def capture_x11(destination):
    """Read only this disposable Blender process's visible X11 window."""
    if sys.platform != "linux" or not os.environ.get("DISPLAY"):
        raise RuntimeError("X11 capture requires a Linux X display")
    found = subprocess.run(
        ["xdotool", "search", "--onlyvisible", "--pid", str(os.getpid())],
        check=True,
        capture_output=True,
        text=True,
        timeout=10,
    ).stdout.split()
    if len(found) != 1 or not found[0].isdigit():
        raise RuntimeError("Expected exactly one visible window owned by this Blender process")
    subprocess.run(
        ["import", "-window", found[0], str(destination)],
        check=True,
        capture_output=True,
        timeout=10,
    )


def verify_mcp_captures():
    """Exercise installed capture handlers in the real GUI without service calls."""
    name = next(n for n in bpy.context.preferences.addons.keys() if n.endswith("." + EXTENSION_ID))
    tools = importlib.import_module(name + ".mcp.tools_blender")
    before = set(Path(tempfile.gettempdir()).glob("scenario-mcp-*"))
    results = {}
    for label, handler, arguments in (
        ("screenshot", tools.screenshot_viewport, {}),
        ("render", tools.render_still, {"source": "VIEWPORT", "width": 320, "height": 180}),
    ):
        content = handler(arguments)
        raw = base64.b64decode(content["_image"], validate=True)
        if content["mimeType"] != "image/png" or not raw.startswith(b"\x89PNG\r\n\x1a\n"):
            raise RuntimeError(f"MCP {label} did not return a PNG")
        if set(Path(tempfile.gettempdir()).glob("scenario-mcp-*")) != before:
            raise RuntimeError(f"MCP {label} left a temporary capture directory")
        (OUTPUT / f"mcp-{label}.png").write_bytes(raw)
        results[label] = {"bytes": len(raw), "temporary_directory_removed": True}
    return results


def capture_credential_preferences(window, area, complete):
    """Wait for each preference layout to draw before capturing its native window."""
    name = next(n for n in bpy.context.preferences.addons.keys() if n.endswith("." + EXTENSION_ID))
    prefs = bpy.context.preferences.addons[name].preferences
    runtime = importlib.import_module(name + ".blender.runtime")
    sources = iter(
        (
            ("AGENT", "PREFERENCES", False, "assistant"),
            ("SCENARIO", "PREFERENCES", True, "preferences"),
            ("SCENARIO", "ENVIRONMENT", False, "environment"),
        )
    )
    original_mode = prefs.provider_mode
    area.type = "PREFERENCES"
    with bpy.context.temp_override(window=window, area=area):
        bpy.ops.preferences.addon_show(module=name)
    prefs.api_key = prefs.api_secret = "offline-preferences-fixture"

    def select_next():
        selection = next(sources, None)
        if selection is None:
            prefs.credential_source = "PREFERENCES"
            prefs.provider_mode = original_mode
            complete()
            return
        mode, source, valid, capture_name = selection
        prefs.provider_mode = mode
        prefs.credential_source = source
        if runtime.credentials().valid != valid:
            raise RuntimeError("Credential source did not select the expected offline pair")
        area.tag_redraw()

        def capture_selected():
            destination = OUTPUT / f"preferences-{capture_name}.png"
            with bpy.context.temp_override(window=window, area=area):
                if CAPTURE_BACKEND == "blender":
                    if bpy.ops.screen.screenshot(filepath=str(destination)) != {"FINISHED"}:
                        raise RuntimeError("Preferences screenshot did not finish")
                else:
                    capture_x11(destination)
            if not destination.is_file():
                raise RuntimeError("Preferences screenshot file is missing")
            select_next()

        # Return to Blender's event loop so the window presents the new layout.
        # Synchronous redraw_timer calls can still capture the previous frame.
        bpy.app.timers.register(guarded(capture_selected), first_interval=1.0)

    select_next()


def inspect_screenshot(path):
    """Reject blank evidence and always release the temporary Blender image."""
    shot = bpy.data.images.load(str(path), check_existing=False)
    try:
        dimensions = list(shot.size)
        # Xvfb without a window manager can return a valid but entirely black
        # front buffer. Sample RGB pixels (exclude alpha) before claiming capture.
        pixels = shot.pixels
        stride = max(4, (len(pixels) // 512 // 4) * 4)
        samples = {
            tuple(round(v, 3) for v in pixels[i : i + 3]) for i in range(0, len(pixels), stride)
        }
        if len(samples) < 2:
            raise RuntimeError("Screenshot is blank; check the display/window manager")
    finally:
        bpy.data.images.remove(shot)
    return dimensions, len(samples)


def capture_extension_permissions(window, area, report):
    """Show the installed manifest's actual Get Extensions permission row."""
    repository = Path(INSTALLED).resolve().parent
    index = next(
        index
        for index, repo in enumerate(bpy.context.preferences.extensions.repos)
        if Path(repo.directory).resolve() == repository
    )
    area.type = "PREFERENCES"
    with bpy.context.temp_override(window=window, area=area):
        bpy.context.preferences.active_section = "EXTENSIONS"
        wm = bpy.context.window_manager
        wm.extension_search = DISPLAY_NAME
        wm.extension_type = "ALL"
        wm.extension_show_panel_installed = True
        wm.extension_show_panel_available = False
        bpy.ops.extensions.package_show_set(pkg_id=EXTENSION_ID, repo_index=index)
        area.tag_redraw()
        bpy.ops.wm.redraw_timer(type="DRAW_WIN_SWAP", iterations=2)
    # Metal needs a real event-loop turn after changing editor type; an immediate
    # screenshot can still contain the previous viewport front buffer.
    bpy.app.timers.register(guarded(lambda: capture_permissions(report)), first_interval=1.0)


def capture_permissions(report):
    window = bpy.context.window_manager.windows[0]
    area = next(area for area in window.screen.areas if area.type == "PREFERENCES")
    with bpy.context.temp_override(window=window, area=area):
        destination = OUTPUT / "permissions.png"
        if CAPTURE_BACKEND == "blender":
            if bpy.ops.screen.screenshot(filepath=str(destination)) != {"FINISHED"}:
                raise RuntimeError("Permissions screenshot did not finish")
        else:
            capture_x11(destination)
    inspect_screenshot(destination)
    report["status"] = "captured"
    report["captured_at"] = datetime.datetime.now(datetime.UTC).isoformat()
    (OUTPUT / "gui.json").write_text(json.dumps(report, indent=2) + "\n")
    bpy.ops.wm.quit_blender()


def capture():
    window, area, region = view3d()
    if bpy.app.online_access:
        raise RuntimeError("Online access changed during capture")
    lane_state = bpy.context.scene.scenario.lane_state(LANE)
    if FIXTURE == "form" and (
        lane_state.model_id != "model_offline-screenshot"
        or lane_state.model_key != "model_offline-screenshot"
    ):
        raise RuntimeError("Synthetic fixture model is not selected")
    if VIEW == "sidebar" and (region.width <= 1 or region.active_panel_category != DISPLAY_NAME):
        raise RuntimeError("Scenario sidebar is not active")
    with bpy.context.temp_override(window=window, area=area):
        bpy.ops.wm.redraw_timer(type="DRAW_WIN_SWAP", iterations=2)
        if CAPTURE_BACKEND == "blender":
            result = bpy.ops.screen.screenshot(filepath=str(OUTPUT / "plugin.png"))
            if result != {"FINISHED"}:
                raise RuntimeError("Blender screenshot operator did not finish")
        else:
            capture_x11(OUTPUT / "plugin.png")
    dimensions, sample_count = inspect_screenshot(OUTPUT / "plugin.png")
    mcp_captures = verify_mcp_captures()
    active_sidebar = region.active_panel_category

    report = {
        "credential_captures": [
            "preferences-assistant.png",
            "preferences-preferences.png",
            "preferences-environment.png",
        ],
        "mcp_captures": mcp_captures,
        "blender_version": list(bpy.app.version),
        "blender": bpy.app.version_string,
        "python": sys.version,
        "os": platform.platform(),
        "online_access": bpy.app.online_access,
        "view": VIEW,
        "lane": LANE,
        "fixture": FIXTURE,
        "selected_model_id": lane_state.model_id,
        "active_sidebar": active_sidebar,
        "image_size": dimensions,
        "capture_backend": CAPTURE_BACKEND,
        "gpu_backend": gpu.platform.backend_type_get(),
        "gpu_renderer": gpu.platform.renderer_get(),
        "distinct_rgb_samples": sample_count,
    }
    capture_credential_preferences(
        window, area, lambda: capture_extension_permissions(window, area, report)
    )


bpy.app.timers.register(guarded(prepare), first_interval=1.5)
bpy.app.timers.register(guarded(select), first_interval=3)
bpy.app.timers.register(guarded(capture), first_interval=float(DELAY))
