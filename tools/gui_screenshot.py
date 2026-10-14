# SPDX-FileCopyrightText: 2026 Scenario Inc.
# SPDX-License-Identifier: GPL-3.0-or-later
"""Open Blender with the Scenario panel visible, screenshot it, quit.

Usage: blender [file.blend] --python tools/gui_screenshot.py -- /abs/out.png [lane] [delay_seconds] [prompt]
Opening a .blend file (any, tools/blank.blend is provided) avoids the splash screen.
"""

import os
import sys

import bpy

os.environ["SCENARIO_GUI_PROBE"] = (
    "1"  # the window steals keyboard focus: never let a stray key spend credits
)

argv = sys.argv[sys.argv.index("--") + 1 :] if "--" in sys.argv else []
OUT = argv[0] if argv else "/tmp/scenario-panel.png"
LANE = argv[1] if len(argv) > 1 else "image"
DELAY = float(argv[2]) if len(argv) > 2 else 6.0
PROMPT = argv[3] if len(argv) > 3 else ""
ACTION = argv[4] if len(argv) > 4 else ""


def _view3d():
    window = bpy.context.window_manager.windows[0]
    area = next(a for a in window.screen.areas if a.type == "VIEW_3D")
    return window, area


def _prepare():
    try:
        window, area = _view3d()
        space = area.spaces.active
        space.show_region_ui = True
        bpy.context.scene.scenario.lane = LANE
        if PROMPT and bpy.context.scene.scenario.lane_state(LANE) is not None:
            bpy.context.scene.scenario.lane_state(LANE).prompt = PROMPT
        if ACTION == "shot":
            # a preset camera move around the scene, so the Render Video lane shows a built path
            bpy.context.scene.scenario_shot.description = "slow orbit, 8 s, 35mm"
            bpy.ops.scenario.shot_from_description()
            with bpy.context.temp_override(
                window=window, area=area, region=next(r for r in area.regions if r.type == "WINDOW")
            ):
                area.spaces.active.region_3d.view_perspective = "CAMERA"
        if ACTION == "edit3d":
            cube = next((o for o in bpy.context.scene.objects if o.type == "MESH"), None)
            if cube is not None:
                for o in bpy.context.selected_objects:
                    o.select_set(False)
                cube.select_set(True)
                bpy.context.view_layer.objects.active = cube
            bpy.context.scene.scenario.lane = "3d"
            bpy.context.scene.scenario.three_d_mode = (
                "EDIT"  # edit3d lives under the 3D tab in Edit mode
            )
            bpy.context.scene.scenario.edit3d_task = "RETEXTURE"
            probe_model = os.environ.get("SCENARIO_PROBE_MODEL")
            if probe_model:
                # force a specific edit3d model (e.g. Rodin Bang) and trigger its async schema fetch
                import importlib

                name = next(
                    n for n in bpy.context.preferences.addons.keys() if n.endswith(".open_studio")
                )
                generation = importlib.import_module(name + ".blender.generation")
                lane_state = bpy.context.scene.scenario.lane_state("edit3d")
                lane_state.model_id = probe_model
                generation.on_model_changed(bpy.context, lane_state)
        if ACTION == "gen3d":
            # 3D tab, Generate/Text mode, with a chosen model (e.g. a text-to-motion model that takes a character mesh)
            cube = next((o for o in bpy.context.scene.objects if o.type == "MESH"), None)
            if cube is not None:
                for o in bpy.context.selected_objects:
                    o.select_set(False)
                cube.select_set(True)
                bpy.context.view_layer.objects.active = cube
            bpy.context.scene.scenario.lane = "3d"
            bpy.context.scene.scenario.three_d_mode = "TEXT"
            probe_model = os.environ.get("SCENARIO_PROBE_MODEL")
            if probe_model:
                import importlib

                name = next(
                    n for n in bpy.context.preferences.addons.keys() if n.endswith(".open_studio")
                )
                generation = importlib.import_module(name + ".blender.generation")
                lane_state = bpy.context.scene.scenario.lane_state("3d")
                lane_state.model_id = probe_model
                generation.on_model_changed(bpy.context, lane_state)
        if ACTION == "failjobs":
            # inject a couple of terminal generations (one failed with a long error) to show the error display + collapse all
            import importlib

            name = next(
                n for n in bpy.context.preferences.addons.keys() if n.endswith(".open_studio")
            )
            runtime = importlib.import_module(name + ".blender.runtime")
            records = importlib.import_module(name + ".core.jobs.records")
            runtime.state.jobs_view.clear()
            ok = records.JobRecord.new(
                lane="3d",
                kind="3d",
                model_id="model_meshy-7-txt23d",
                body={},
                meta={"prompt": "She's running", "model_name": "Meshy 7 - Text to 3D"},
            )
            ok.status, ok.cu_cost, ok.asset_ids = "success", 208, ["asset_ok1"]
            fail = records.JobRecord.new(
                lane="edit3d",
                kind="3d",
                model_id="model_meshy-7-retexture",
                body={},
                meta={"prompt": "red dress", "model_name": "Meshy 7 - Retexture"},
            )
            fail.status, fail.cu_cost = "failed", 60
            fail.error = "The model file is too large for processing. Try reducing the resolution or simplifying the geometry of your input, then retry the generation. [Error ID: error_Uq14k1SGmKWhPfv4HZ4hyZRN]"
            runtime.state.jobs_view.extend([fail, ok])
        if ACTION == "blockout":
            # build a sample greybox (rich primitives + category colours) and frame it, on the Blockout tab
            import importlib

            name = next(
                n for n in bpy.context.preferences.addons.keys() if n.endswith(".open_studio")
            )
            blockout = importlib.import_module(name + ".blender.blockout")
            for o in list(bpy.context.scene.objects):
                if o.type == "MESH":
                    bpy.data.objects.remove(o, do_unlink=True)

            def E(nm, cat, prim, pos, sz, rot=0, grp="Blockout"):
                return {
                    "name": nm,
                    "category": cat,
                    "primitive": prim,
                    "position": pos,
                    "size": sz,
                    "rotation": rot,
                    "group": grp,
                }

            els = [
                E("Ground", "floor", "plane", [0, 0, -0.05], [16, 16, 0.1], grp="Ground"),
                E("Well", "water", "cylinder", [0, 0, 0.6], [1.8, 1.8, 1.2], grp="Center"),
                E("Gate", "structure", "box", [0, -6.5, 2.0], [4.5, 0.5, 4], grp="Walls"),
                E("Wall L", "wall", "box", [-6, 0, 1.5], [0.4, 12, 3], grp="Walls"),
                E("Wall R", "wall", "box", [6, 0, 1.5], [0.4, 12, 3], grp="Walls"),
                E(
                    "Stall A",
                    "furniture",
                    "box",
                    [-3.5, 3, 1.0],
                    [2.4, 2, 1.6],
                    rot=15,
                    grp="Market",
                ),
                E(
                    "Stall B",
                    "furniture",
                    "box",
                    [3.5, 3, 1.0],
                    [2.4, 2, 1.6],
                    rot=-15,
                    grp="Market",
                ),
                E(
                    "Awning A",
                    "prop",
                    "wedge",
                    [-3.5, 3, 2.2],
                    [2.6, 2.2, 0.8],
                    rot=15,
                    grp="Market",
                ),
                E("Tower", "structure", "cylinder", [5, -5, 3.5], [2.2, 2.2, 7], grp="Tower"),
                E("Spire", "structure", "cone", [5, -5, 8.2], [2.4, 2.4, 2.4], grp="Tower"),
                E("Tree", "vegetation", "sphere", [-5, -4, 2.2], [2.4, 2.4, 2.4], grp="Nature"),
                E("Trunk", "vegetation", "cylinder", [-5, -4, 0.8], [0.4, 0.4, 1.6], grp="Nature"),
            ]
            blockout.build_blockout(bpy.context, els)
            import json as _json

            bpy.context.scene.scenario_blockout.plan_json = _json.dumps(
                els
            )  # so the tab shows the plan summary
            bpy.context.scene.scenario_blockout.prompt = (
                "a medieval market square: a central well, a stone gate, stalls, a watchtower"
            )
            bpy.context.scene.scenario.lane = "blockout"
            for a in bpy.context.window_manager.windows[0].screen.areas:
                if a.type == "VIEW_3D":
                    for region in a.regions:
                        if region.type == "WINDOW":
                            with bpy.context.temp_override(area=a, region=region):
                                bpy.ops.object.select_all(action="SELECT")
                                bpy.ops.view3d.view_selected()
        if ACTION == "blockout_live":
            # Prepare the form only. Screenshot probes must never submit a paid design.
            for o in list(bpy.context.scene.objects):
                if o.type == "MESH":
                    bpy.data.objects.remove(o, do_unlink=True)
            bo = bpy.context.scene.scenario_blockout
            bo.prompt = (
                PROMPT
                or "a small artist's studio: a desk, an easel, shelves, a couch, a rug, a window"
            )
            bo.scene_type, bo.scale = "interior", "room"
            bpy.context.scene.scenario.lane = "blockout"
            print("GUI probe: skipped paid blockout design")
        if ACTION == "generations":
            bpy.ops.scenario.history_refresh()
        if ACTION == "edit_mode":
            cube = next((o for o in bpy.context.scene.objects if o.type == "MESH"), None)
            if cube is not None:
                for o in bpy.context.selected_objects:
                    o.select_set(False)
                cube.select_set(True)
                bpy.context.view_layer.objects.active = cube
            bpy.context.scene.scenario.three_d_mode = "EDIT"
        if ACTION == "settings":
            region = next(r for r in area.regions if r.type == "WINDOW")
            with bpy.context.temp_override(window=window, area=area, region=region):
                bpy.ops.scenario.quick_settings("INVOKE_DEFAULT", lane=LANE)
        if ACTION == "picker":
            # open the model picker dialog over the viewport
            region = next(r for r in area.regions if r.type == "WINDOW")
            with bpy.context.temp_override(window=window, area=area, region=region):
                bpy.ops.scenario.pick_model("INVOKE_DEFAULT", lane=LANE)
        if ACTION == "composer":
            import importlib

            name = next(
                n for n in bpy.context.preferences.addons.keys() if n.endswith(".open_studio")
            )
            runtime = importlib.import_module(name + ".blender.runtime")
            state = runtime.state.composer
            state.expanded = True
            state.sync_from_lane(bpy.context.scene)
            state.focused = True
            state.field.end()
            for a in bpy.context.window_manager.windows[0].screen.areas:
                a.tag_redraw()
        if ACTION == "mcpproof":
            import importlib
            import json
            import subprocess

            name = next(
                n for n in bpy.context.preferences.addons.keys() if n.endswith(".open_studio")
            )
            service = importlib.import_module(name + ".blender.mcp_service")
            st = service.status()
            print("mcp status:", st["running"], st["url"])

            def _curl(payload):
                cmd = [
                    "curl",
                    "-s",
                    "-X",
                    "POST",
                    st["url"],
                    "-H",
                    f"Authorization: Bearer {st['token']}",
                    "-H",
                    "Content-Type: application/json",
                    "-d",
                    json.dumps(payload),
                ]
                return subprocess.run(cmd, capture_output=True, text=True, timeout=60).stdout

            def _proof():
                listed = json.loads(_curl({"jsonrpc": "2.0", "id": 1, "method": "tools/list"}))
                print("mcpproof tools:", [t["name"] for t in listed["result"]["tools"]])
                summary = json.loads(
                    _curl(
                        {
                            "jsonrpc": "2.0",
                            "id": 2,
                            "method": "tools/call",
                            "params": {"name": "scene_summary", "arguments": {}},
                        }
                    )
                )
                text = summary["result"]["content"][0]["text"]
                print(
                    "mcpproof scene_summary objects:",
                    [o["name"] for o in json.loads(text)["objects"]],
                )
                return None

            import threading

            threading.Thread(target=_proof, daemon=True).start()
        if ACTION == "capture":
            import importlib
            import pathlib

            name = next(
                n for n in bpy.context.preferences.addons.keys() if n.endswith(".open_studio")
            )
            capture = importlib.import_module(name + ".blender.capture")
            out_dir = pathlib.Path(OUT).parent
            clip = capture.capture_playblast(
                bpy.context,
                str(out_dir / "gui_playblast.mp4"),
                source="CAMERA",
                frame_start=1,
                frame_end=72,
            )
            still = capture.capture_still(
                bpy.context, str(out_dir / "gui_still.png"), source="VIEWPORT"
            )
            for p in (pathlib.Path(clip["path"]), pathlib.Path(still)):
                print("capture file:", p, p.exists(), p.stat().st_size if p.exists() else 0)
            print(
                "capture dir listing:",
                sorted(x.name for x in out_dir.iterdir() if x.name.startswith("gui_")),
            )
        ui_region = next(r for r in area.regions if r.type == "UI")
        try:
            ui_region.active_panel_category = "Open Studio"
        except (AttributeError, TypeError) as err:
            print("active_panel_category not settable:", err)
        area.tag_redraw()
        print("prepared: sidebar", space.show_region_ui, "ui region width", ui_region.width)
    except Exception as err:  # keep going, the screenshot tells the rest
        print("prepare failed:", err)
    return None


def _select_tab():
    try:
        window, area = _view3d()
        ui_region = next(r for r in area.regions if r.type == "UI")
        ui_region.active_panel_category = "Open Studio"
        area.tag_redraw()
        print("tab selected:", ui_region.active_panel_category)
    except Exception as err:
        print("tab select failed:", err)
    return None


def _shot():
    window, area = _view3d()
    ui_region = next(r for r in area.regions if r.type == "UI")
    print(
        "ui region width at shot:",
        ui_region.width,
        "tab:",
        getattr(ui_region, "active_panel_category", "?"),
    )
    with bpy.context.temp_override(
        window=window, screen=window.screen, area=area, region=ui_region
    ):
        try:
            ui_region.active_panel_category = "Open Studio"
            bpy.ops.wm.redraw_timer(type="DRAW_WIN_SWAP", iterations=2)
        except Exception as err:
            print("forced redraw failed:", err)
        bpy.ops.screen.screenshot(filepath=OUT)
    print("screenshot saved", OUT)
    bpy.ops.wm.quit_blender()
    return None


bpy.app.timers.register(_prepare, first_interval=1.5)
bpy.app.timers.register(_select_tab, first_interval=max(2.5, DELAY - 2.0))
bpy.app.timers.register(_shot, first_interval=DELAY)
