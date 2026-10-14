# SPDX-FileCopyrightText: 2026 Scenario Inc.
# SPDX-License-Identifier: GPL-3.0-or-later
"""The Scenario tab: four panels. "Scenario" (what to generate), "Jobs" (what is running), "Generations" (what came
back, here and in the cloud), "Agents" (the MCP server). Lane tabs only cover generation."""

import os
import textwrap

import bpy

from ..core.ui.costs import format_cu, workflow_loop_warning
from . import (
    generation,
    history,
    job_recovery,
    label_wrap,
    params_ui,
    props,
    result_previews,
    runtime,
)

KIND_ICON = {
    "image": "IMAGE_DATA",
    "video": "FILE_MOVIE",
    "3d": "MESH_DATA",
    "material": "MATERIAL",
    "audio": "SPEAKER",
    "workflow": "NODETREE",
}
GENERATE_LANES = ("image", "video", "3d", "material", "audio")
SOURCE_ICON = {
    "FILE": "FILE_IMAGE",
    "VIEWPORT": "RESTRICT_VIEW_OFF",
    "CAMERA": "CAMERA_DATA",
    "VIEWPORT_CLIP": "RENDER_ANIMATION",
    "CAMERA_CLIP": "RENDER_ANIMATION",
    "RENDER": "RENDER_RESULT",
    "ASSET": "URL",
    "MESH": "MESH_DATA",
}
# short labels for the reference-add buttons (the enum labels read as list entries; these read as actions)
ADD_SOURCE_LABEL = {
    "FILE": "Upload",
    "MESH": "Selected mesh",
    "RENDER": "Render",
    "VIEWPORT": "Viewport",
    "CAMERA": "Camera",
    "VIEWPORT_CLIP": "Viewport clip",
    "CAMERA_CLIP": "Camera clip",
}
IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".webp")
MESH_EXTS = (".glb", ".gltf", ".fbx", ".obj", ".spz", ".ply")
AUDIO_EXTS = (".mp3", ".wav", ".ogg", ".m4a", ".flac")
SIDEBAR_CHARS = 36  # wrapped status text fits the default sidebar width


def draw_account_strip(layout, context):
    creds = runtime.credentials()
    row = layout.row(align=True)
    if not creds.valid:
        source = getattr(runtime.prefs(), "credential_source", "PREFERENCES")
        message = (
            "Environment key/secret missing"
            if source == "ENVIRONMENT"
            else "Add key and secret in Preferences"
        )
        row.label(text=message, icon="ERROR")
        row.operator("preferences.addon_show", text="", icon="PREFERENCES").module = runtime.PACKAGE
        return False
    if not runtime.online():
        row.label(text="Online access is disabled", icon="ERROR")
        return False
    label = runtime.state.account_label or (
        "Connected" if runtime.state.catalog_loaded else "Connecting..."
    )
    icon = {"pending": "SORTTIME", "error": "ERROR", "success": "CHECKMARK"}.get(
        runtime.state.connection_status,
        "CHECKMARK" if runtime.state.catalog_loaded else "SORTTIME",
    )
    row.label(text=label, icon=icon)
    row.operator("scenario.refresh_catalog", text="", icon="FILE_REFRESH")
    row.operator("preferences.addon_show", text="", icon="PREFERENCES").module = runtime.PACKAGE
    return True


def thumbnail(path):
    """Preview icon id for an image file on disk (0 when there is none). Loads lazily into the shared preview collection."""
    if not path or not os.path.exists(path) or not path.lower().endswith(IMAGE_EXTS):
        return 0
    previews = runtime.previews()
    if path not in previews:
        previews.load(path, path, "IMAGE")
    return previews[path].icon_id


_thumbnail = thumbnail


def draw_model_row(layout, lane_state, lane):
    """The model chooser: the search dialog when the picker module is present, the plain dropdown otherwise."""
    try:
        from . import model_picker

        model_picker.draw_model_row(layout, lane_state, lane)
    except ImportError:
        layout.prop(lane_state, "model_id", text="Model")


def equal_segments(parent, struct, prop, values):
    """One continuous segmented control filling the row: a `prop_enum` per value in an align=True row (Blender merges
    the borders), with a chain of even splits so the cells share the full width. Rows with fewer values get wider
    cells, so cell sizes differ from row to row. Text and icons are Blender's own for the enum item."""
    row = parent.row(align=True)
    container, n = row, len(values)
    for i, value in enumerate(values):
        remaining = n - i
        seg = container.split(factor=1.0 / remaining, align=True) if remaining > 1 else container
        seg.prop_enum(struct, prop, value)
        container = seg


def draw_enum_tabs(layout, struct, prop, rows):
    """Tab rows: each row a continuous full-width segmented control (cells wider when the row holds fewer values)."""
    col = layout.column(align=True)
    for values in rows:
        equal_segments(col, struct, prop, values)


def draw_prompt_row(layout, lane_state, lane, text="", placeholder=None):
    """Prompt with the Spark / Rewrite / Translate buttons (plain prompt when prompt_tools is missing)."""
    try:
        from . import prompt_tools

        prompt_tools.draw_prompt_row(layout, lane_state, lane, text=text, placeholder=placeholder)
    except ImportError:
        row = layout.row(align=True)
        if text:
            row.label(text=text)
        row.prop(lane_state, "prompt", text="")


def draw_reference_row(box, lane_state, index, ref):
    from . import reference_form

    row = box.row(align=True)
    path = bpy.path.abspath(ref.filepath) if ref.filepath else ""
    icon_id = thumbnail(path) if ref.source == "FILE" else 0
    if icon_id:
        row.template_icon(icon_value=icon_id, scale=2.2)
        row.label(text=(ref.label or os.path.basename(path))[-34:])
    else:
        row.label(
            text=ref.label or ref.filepath or ref.source, icon=SOURCE_ICON.get(ref.source, "DOT")
        )
    remove = row.operator("scenario.remove_reference", text="", icon="X")
    remove.lane, remove.index = props.lane_of(lane_state), index
    reference_form.draw(box, lane_state, index, ref)


def draw_references(
    layout, lane_state, schema, title_for=None, fixed_first=None, hide=(), boxed=True, skip_refs=()
):
    """One file input per group. `fixed_first` names an implicit first entry (the capture, the selected mesh) that
    the lane adds itself; `title_for` overrides the title; `hide` skips inputs entirely. `boxed=False` draws flat,
    without a box, for callers that are already inside one (avoids nesting coloured boxes).

    An Environment input that takes a 360 panorama offers only a checked 2:1 file
    and Use saved panorama; captures and arbitrary saved uploads are not panoramas."""
    from . import mesh_export, panorama_lane

    title_for, fixed_first = title_for or {}, dict(fixed_first or {})
    for ref in lane_state.references:
        spec = schema.by_name(ref.param_name)
        if spec is not None and spec.kind == "3d":
            fixed_first.pop(ref.param_name, None)
    refs = params_ui.collect_file_refs(lane_state, schema)
    lane = props.lane_of(lane_state)
    # A selected mesh can be explicitly uploaded to any empty 3D input.
    # Only when the user has attached an explicit file/asset for that input is the selection not shown.
    selected = mesh_export.source_objects(bpy.context)
    if selected and lane != "edit3d":  # the Edit lane passes its own pinned label
        names = ", ".join(o.name for o in selected[:2]) + (
            f" +{len(selected) - 2}" if len(selected) > 2 else ""
        )
        for spec in schema.specs:
            if (
                spec.is_file
                and (spec.kind or "").lower() == "3d"
                and spec.name not in fixed_first
                and not any(r.param_name == spec.name for r in lane_state.references)
            ):
                fixed_first.setdefault(spec.name, f"Selected mesh: {names}")
    for spec in schema.specs:
        if not spec.is_file or spec.name in hide:
            continue
        box = layout.box() if boxed else layout.column(align=True)
        header = box.row()
        count = len(refs.get(spec.name, [])) + (1 if spec.name in fixed_first else 0)
        label = title_for.get(spec.name) or (
            spec.label + (" (required)" if spec.required_always else "")
        )
        kind = spec.kind or "image"
        icon = {
            "image": "IMAGE_DATA",
            "video": "FILE_MOVIE",
            "3d": "MESH_DATA",
            "audio": "SPEAKER",
        }.get(kind, "FILE")
        header.label(
            text=f"{label}  {count}" + (f"/{spec.max_length}" if spec.max_length else ""), icon=icon
        )
        panorama_input = panorama_lane.is_panorama_input(lane_state, spec.name)
        if panorama_input:
            op = header.operator("scenario.use_saved_panorama", text="Saved panorama", icon="WORLD")
            op.param_name = spec.name
        elif kind in {"image", "audio", "video", "3d"}:
            op = header.operator("scenario.inspect_uploads", text="Saved uploads", icon="VIEWZOOM")
            op.param_name, op.lane = spec.name, lane
        # the Edit lane's required mesh hides its add options; everywhere else offer the kind-appropriate sources
        # (a 3D input: Upload a model to override the selection; image/video: stills or clips) even when auto-pinned.
        if not (spec.ptype == "file" and spec.name in fixed_first and lane == "edit3d"):
            add_row = box.row(align=True)
            sources = props.addable_sources_for(spec.kind)
            if panorama_input:
                sources = [item for item in sources if item[0] == "FILE"]
                box.label(text="A saved 360 result or a 2:1 PNG/JPEG", icon="INFO")
            for source_id, source_label, _desc in sources:
                op = add_row.operator(
                    "scenario.add_reference",
                    text=ADD_SOURCE_LABEL.get(source_id, source_label),
                    icon=SOURCE_ICON.get(source_id, "ADD"),
                )
                op.lane, op.param_name, op.source = lane, spec.name, source_id
        if (
            kind == "3d"
            and selected
            and not any(ref.param_name == spec.name for ref in lane_state.references)
        ):
            op = box.operator("scenario.upload_selected_mesh", icon="EXPORT")
            op.lane, op.param_name = lane, spec.name
        if spec.name in fixed_first:
            row = box.row(align=True)
            row.enabled = False
            row.label(text=fixed_first[spec.name], icon="PINNED")
        elif (spec.kind or "").lower() == "3d" and not refs.get(spec.name):
            box.label(
                text="Select a mesh in the viewport to animate it, or Upload one", icon="INFO"
            )
        for index, ref in enumerate(lane_state.references):
            if ref.as_pointer() in skip_refs:
                continue
            if ref.param_name != spec.name:
                continue
            draw_reference_row(box, lane_state, index, ref)


def draw_clip_options(layout, context, lane_state, schema):
    from ..core.scene import capture_plan

    has_video_input = any(s.is_file and s.kind == "video" for s in schema.specs)
    uses_capture = any(r.source in props.CAPTURE_SOURCES for r in lane_state.references)
    if not has_video_input and not uses_capture:
        return
    box = layout.box()
    box.label(text="Scene clip", icon="RENDER_ANIMATION")
    scene = context.scene
    fps = scene.render.fps / (scene.render.fps_base or 1.0)
    start, end, seconds = capture_plan.frame_span(
        scene.frame_start,
        scene.frame_end,
        fps,
        use_preview=scene.use_preview_range,
        preview_start=scene.frame_preview_start,
        preview_end=scene.frame_preview_end,
    )
    box.label(text=f"Frames {start} to {end}: {seconds:.1f} s at {fps:g} fps, 1280x720")
    if schema.by_name("duration") is not None:
        box.prop(lane_state, "match_timeline")
        if lane_state.match_timeline:
            _, value, _ = generation.timeline_sync_info(context.scene, lane_state, schema)
            if value is not None:
                box.label(text=f"Model output duration: {value:g} s", icon="INFO")
    box.label(text="Upload captures this range without padding; saved clips stay unchanged")
    box.prop(lane_state, "force_solid")


def generate_button_text(lane_state):
    if not runtime.online():
        return "Offline"
    if not runtime.credentials().valid:
        return "Set up credentials"
    if lane_state.estimate_state == "READY" or generation.repeat_label(lane_state):
        # An unchanged form keeps its last price while it waits to offer Generate again.
        prefix = "from " if lane_state.estimate_partial else ""
        return f"Generate  ({prefix}{format_cu(lane_state.estimate_cu)} CU)"
    if lane_state.estimate_state == "PENDING":
        return "Generate  (estimating...)"
    return "Generate"


def generate_enabled(lane_state, lane):
    """Presentation guard; submission still validates the exact quote and origin."""
    if not runtime.online() or not runtime.credentials().valid or not lane_state.model_id:
        return False
    ticket = runtime.state.estimates.get(lane_state.estimate_key)
    return (
        lane_state.estimate_state == "READY"
        and ticket is not None
        and ticket.lane == lane
        and not generation.repeat_holding(lane_state)
    )


def form_takes_prompt(lane_state, lane):
    """Whether the form shows a prompt, as the sidebar draws it.

    Render lanes always edit their look. Other forms hide the prompt when the loaded model takes
    none, as several Edit 3D tasks (retopology, rigging) do; an unloaded model keeps it.
    """
    if lane in ("render_image", "render_video"):
        return True
    schema = generation.schema_for(lane_state.model_id)
    return schema is None or bool(schema.prompt_name)


def model_chip_parts(lane_state):
    """(base, first trained name, how many more) for the composer's model chip; read-only."""
    record = runtime.state.records.get(lane_state.model_id)
    if record is None or not len(lane_state.trained):
        return model_button_text(lane_state), "", 0
    from . import trained_models

    names = [trained_models.slot_name(slot) for slot in lane_state.trained]
    return record.name, names[0], len(names) - 1


def model_chip_category(lane_state, lane):
    """The picker chip the composer's model chip opens: Trained when the form has a trained choice."""
    from . import trained_models

    if lane in trained_models.LANES and len(lane_state.trained):
        from ..core.api.model_filter import TRAINED_CATEGORY

        return TRAINED_CATEGORY
    return "all"


def model_button_text(lane_state):
    """The model chip label: the base, followed by the trained choice as "Base · LoRA (+n)"."""
    record = runtime.state.records.get(lane_state.model_id)
    if record is not None:
        if len(lane_state.trained):
            from . import trained_models

            return trained_models.selection_label(lane_state, record.name)
        return record.name
    if not runtime.online():
        return "Online access disabled"
    if not runtime.credentials().valid:
        return "Set up credentials"
    if runtime.state.catalog_error:
        return "Could not load models"
    return "Loading models..." if runtime.state.catalog_loading else "Pick a model"


def draw_generate_row(layout, lane_state, lane):
    layout.separator(factor=0.5)  # breathing room above the primary action
    row = layout.row(align=True)
    row.scale_y = 1.5
    row.enabled = generate_enabled(lane_state, lane)
    row.operator(
        "scenario.generate", text=generate_button_text(lane_state), icon="PLAY"
    ).lane = lane
    if lane_state.estimate_state in ("ERROR", "UNAVAILABLE") and lane_state.estimate_error:
        draw_wrapped(layout, lane_state.estimate_error, "INFO")
    error = generation.lane_error(lane_state)  # a stale saved model-load failure is not drawn
    if error:
        draw_wrapped(layout, error, "ERROR")


def draw_wrapped(layout, message, icon):
    """Sidebar labels clip instead of wrapping; keep status guidance readable."""
    lines = textwrap.wrap(message, SIDEBAR_CHARS, max_lines=6, placeholder="...")
    for index, line in enumerate(lines):
        layout.label(text=line, icon=icon if index == 0 else "NONE")


def draw_loading(layout):
    if runtime.state.catalog_error:
        draw_wrapped(layout, runtime.state.catalog_error, "ERROR")
        layout.operator(
            "scenario.refresh_catalog", text="Retry loading models", icon="FILE_REFRESH"
        )
    else:
        layout.label(text="Loading models...", icon="TIME")


def draw_schema_status(layout, lane_state, lane):
    """Explain a form without its description; drawing reads state and never retries.

    Only a pending read is described as loading. While online, the form offers an
    explicit retry, with the failure recorded this session if there is one. A lane
    error saved in a reopened file is not drawn as a current failure.
    """
    model_id = lane_state.model_id
    if not model_id or model_id == "NONE":
        layout.label(text="Pick a model to show its settings", icon="INFO")
        return
    if generation.is_loading(model_id):
        layout.label(text="Loading the model description...", icon="TIME")
        return
    if not runtime.online():
        draw_wrapped(layout, generation.MODEL_OFFLINE, "ERROR")
        return
    failure = runtime.state.model_errors.get(model_id)
    if failure:
        draw_wrapped(layout, failure, "ERROR")
    else:
        layout.label(text="The model description is not loaded", icon="INFO")
    layout.operator(
        "scenario.retry_model", text="Retry loading model", icon="FILE_REFRESH"
    ).lane = lane


def draw_generate_lane(layout, context, lane):
    scene = context.scene
    if lane == "3d":
        layout.row(align=True).prop(scene.scenario, "three_d_mode", expand=True)
    # The same lane rule as pricing, the composer and the Generate operator.
    if props.effective_lane(scene, lane) == "edit3d":
        draw_edit3d_lane(layout, context)
        return
    lane_state = scene.scenario.lane_state(lane)
    draw_model_row(layout, lane_state, lane)
    schema = generation.schema_for(lane_state.model_id)
    if schema is None:
        draw_schema_status(layout, lane_state, lane)
        return
    if schema.prompt_name:
        draw_prompt_row(layout, lane_state, lane)
    draw_references(layout, lane_state, schema)
    params_ui.draw_params(
        layout,
        lane_state,
        schema,
        locked={"duration"} if (lane == "video" and lane_state.match_timeline) else (),
    )
    if lane == "video":
        draw_clip_options(layout, context, lane_state, schema)
    if lane == "material":
        # Two short lines: one long label is middle-clipped at the default sidebar width.
        hint = layout.column(align=True)
        hint.label(text="Saved only, not applied to meshes", icon="INFO")
        hint.label(text="Apply from Jobs to a mesh slot", icon="BLANK1")
    if lane == "audio":
        # Two short lines: one long label is middle-clipped at the default sidebar width.
        hint = layout.column(align=True)
        hint.label(text="Saved jobs offer Play, Waveform", icon="INFO")
        hint.label(text="and Add audio strip", icon="BLANK1")
    draw_generate_row(layout, lane_state, lane)


PANORAMA_PROMPTS = {
    "GENERATE": "Describe the 360 environment",
    "FROM_IMAGE": "Optional: describe the place",
    "UPSCALE": "Optional: describe the details",
}


def draw_panorama_lane(layout, context):
    """The Environment tab: a mode, then a verified contract model's form.

    Drawing reads the scene and contract only; contract values are written by
    property callbacks, and pricing happens through the shared exact quote.
    """
    from ..core.scene import panorama_contract

    scenario = context.scene.scenario
    lane_state = scenario.lane_state("panorama")
    mode = scenario.panorama_mode
    equal_segments(layout, scenario, "panorama_mode", [item[0] for item in props.PANORAMA_MODES])
    draw_wrapped(layout, panorama_contract.MODE_DESCRIPTIONS[mode], "INFO")
    if not runtime.state.lane_models.get("panorama"):
        draw_wrapped(layout, "No verified panorama model for this mode in the catalog", "ERROR")
        return
    draw_model_row(layout, lane_state, "panorama")
    schema = generation.schema_for(lane_state.model_id)
    if schema is None:
        draw_schema_status(layout, lane_state, "panorama")
        return
    contract = panorama_contract.contract_for(lane_state.model_id)
    if contract is None or contract.mode != mode:
        draw_wrapped(layout, "Pick a verified model for this Environment mode", "ERROR")
        return
    if schema.prompt_name:
        draw_prompt_row(layout, lane_state, "panorama", placeholder=PANORAMA_PROMPTS.get(mode))
    draw_references(layout, lane_state, schema)
    if contract.sizes:
        box = layout.box()
        box.label(text="Panorama size", icon="IMAGE_DATA")
        box.prop(scenario, "panorama_size", text="")
    params_ui.draw_params(
        layout,
        lane_state,
        schema,
        exclude=set(contract.size_params),
        locked={name for name, _value in contract.locked},
    )
    if contract.locked:
        draw_wrapped(layout, "Greyed settings are fixed by the Environment lane", "LOCKED")
    draw_wrapped(layout, panorama_contract.result_line(contract), "WORLD")
    draw_generate_row(layout, lane_state, "panorama")


def draw_edit3d_lane(layout, context):
    """The 3D tab in Edit mode: Scenario's 3D tools on the selected mesh."""
    from ..core.api.catalog import edit3d_task, mesh_param
    from . import mesh_export

    scene = context.scene
    lane_state = scene.scenario.lane_state("edit3d")
    box = layout.box()
    objects = mesh_export.source_objects(context)
    if objects:
        names = ", ".join(o.name for o in objects[:3]) + (
            f" +{len(objects) - 3}" if len(objects) > 3 else ""
        )
        box.label(text=f"Mesh: {names}", icon="MESH_DATA")
        box.label(text="Upload a GLB snapshot before requesting a generation price", icon="INFO")
    else:
        box.label(text="Select the mesh to edit in the viewport", icon="ERROR")
    tasks = [t[0] for t in props.EDIT3D_TASK_ITEMS]
    # continuous segmented rows, like the lane tabs
    draw_enum_tabs(layout, scene.scenario, "edit3d_task", (tasks[:4], tasks[4:]))
    task = edit3d_task(scene.scenario.edit3d_task)
    layout.label(text=task[2], icon="INFO")
    if not runtime.state.lane_models.get("edit3d"):
        layout.label(text="No model for this task in the catalog", icon="ERROR")
        return
    draw_model_row(layout, lane_state, "edit3d")
    schema = generation.schema_for(lane_state.model_id)
    if schema is None:
        draw_schema_status(layout, lane_state, "edit3d")
        return
    if schema.prompt_name:
        draw_prompt_row(layout, lane_state, "edit3d")
    record = runtime.state.records.get(lane_state.model_id)
    mesh_name = mesh_param(record) if record is not None else None
    draw_references(
        layout,
        lane_state,
        schema,
        fixed_first={mesh_name: "Selected mesh (upload before pricing)"} if mesh_name else None,
    )
    params_ui.draw_params(layout, lane_state, schema)
    # Enablement and submission use the Edit form's own lane-bound quote.
    draw_generate_row(layout, lane_state, "edit3d")


def draw_mcp_lane(layout, context):
    from . import mcp_service

    st = mcp_service.status()
    box = layout.box()
    box.label(text="Let agents build in this Blender", icon="PLUGIN")
    if st["running"]:
        box.label(text=f"Running on {st['url']}", icon="CHECKMARK")
        box.label(text=f"Token {mcp_service.masked_token()}  ({st['calls']} tool calls served)")
        box.operator("scenario.mcp_stop", icon="PAUSE")
    else:
        box.label(text=st["error"] or "Stopped", icon="ERROR" if st["error"] else "INFO")
        box.operator("scenario.mcp_start", icon="PLAY")
    prefs = runtime.prefs()
    if prefs is not None:
        box.prop(prefs, "mcp_allow_python")
    box = layout.box()
    box.label(text="Connect a client (copies the setup)", icon="COPYDOWN")
    col = box.column(align=True)
    for kind, label in (
        ("claude_code", "Claude Code"),
        ("cursor", "Cursor"),
        ("claude_desktop", "Claude Desktop (stdio)"),
        ("codex", "Codex"),
        ("curl", "curl test"),
    ):
        col.operator("scenario.mcp_copy", text=label, icon="CONSOLE").kind = kind
    box.label(
        text="Tools: scene, objects, Python, API help, screenshots, camera path, models, cost, generate, import",
        icon="INFO",
    )


# -- results ------------------------------------------------------------------


def _short_prompt(rec, limit=40):
    text = (
        rec.meta.get("prompt") or rec.meta.get("spark_look") or rec.meta.get("look") or ""
    ).strip()
    if not text:
        text = rec.meta.get("model_name") or rec.model_id
    return text if len(text) <= limit else text[: limit - 1] + "…"


def _primary_asset(rec):
    for asset_id in rec.asset_ids:
        kind = rec.asset_types.get(asset_id, "")
        if rec.kind == "3d" and "texture" in kind:
            continue
        return asset_id
    return rec.asset_ids[0] if rec.asset_ids else ""


def draw_failure(layout, text, inset=result_previews.TEXT_INSET):
    """A failure's whole text in red, wrapped to the measured width so no support id is clipped."""
    column = layout.column(align=True)
    column.alert = True
    for index, line in enumerate(label_wrap.label_lines(bpy.context, text, inset)):
        column.label(text=line, icon="ERROR" if index == 0 else "BLANK1")


def draw_result(layout, rec, *, collapsible=True):
    """One generation: a header (type icon, prompt start, cost, collapse arrow), then model, asset id and actions.
    Only the output is shown; the inputs and settings live behind Details. `collapsible=False` (the composer's
    job card) always shows it expanded, without the arrow, and leaves the sidebar's collapsed flag alone."""
    collapsed = collapsible and bool(rec.meta.get("collapsed"))
    box = layout.box()
    header = box.row(align=True)
    if collapsible:
        toggle = header.operator(
            "scenario.toggle_result",
            text="",
            icon="TRIA_RIGHT" if collapsed else "TRIA_DOWN",
            emboss=False,
        )
        toggle.local_id = rec.local_id
    header.label(text=_short_prompt(rec), icon=KIND_ICON.get(rec.kind, "FILE"))
    if not rec.is_success and rec.error:
        # a red error button whose tooltip is the full message, and which opens it in full on click (like the web app)
        header.operator(
            "scenario.error_details", text="", icon="ERROR", emboss=False
        ).local_id = rec.local_id
    elif not rec.is_success:
        header.label(text="", icon="ERROR")
    header.label(text=result_cost_label(rec))
    if collapsed:
        return
    row = box.row(align=True)
    row.label(text=rec.meta.get("model_name") or rec.model_id, icon="NODE_MATERIAL")
    if generation.reload_lane(rec) is not None:
        reload = row.operator("scenario.reload_generation", text="", icon="FILE_REFRESH")
        reload.local_id = rec.local_id
    details = row.operator("scenario.result_details", text="", icon="INFO")
    details.local_id = rec.local_id
    asset_id = _primary_asset(rec)
    if asset_id:
        row = box.row(align=True)
        op = row.operator("scenario.copy_text", text=asset_id, icon="COPYDOWN")
        op.text, op.what = asset_id, "asset id"
        if len(rec.asset_ids) > 1:
            row.label(text=f"+{len(rec.asset_ids) - 1}")
    if rec.error and not rec.is_success:
        draw_failure(box, f"Failed: {rec.error}")
    elif rec.error:
        row = box.row(align=True)
        row.label(text=rec.error[:60], icon="ERROR")
        row.operator(
            "scenario.error_details", text="", icon="INFO"
        ).local_id = rec.local_id  # tooltip = full message; click = full text + copy
    result_previews.draw(box, rec)
    job_recovery.draw_controls(box, rec)
    if rec.meta.get("download_errors"):
        box.label(
            text=f"{len(rec.meta['download_errors'])} file(s) could not be downloaded", icon="ERROR"
        )
    if rec.kind == "image":
        shared = bool(rec.meta.get("shared_job"))
        for path in rec.files[:6]:
            # Shared rows show their receipt-bound preview above; thumbnail()
            # would read the file while drawing.
            icon_id = 0 if shared else thumbnail(path)
            row = box.row(align=True)
            if icon_id:
                row.template_icon(icon_value=icon_id, scale=3.0)
            col = row.column(align=True)
            col.operator("scenario.show_image", text="View image", icon="ZOOM_ALL").filepath = path
            col.operator_menu_enum(
                "scenario.use_as_reference",
                "target",
                text="Use as reference",
                icon="IMAGE_REFERENCE",
            ).filepath = path
            col.operator(
                "scenario.remove_background", text="Remove background", icon="MOD_MASK"
            ).filepath = path
            sub = col.row(align=True)
            sub.operator("scenario.apply_texture", text="Apply as texture").filepath = path
            sub.operator("scenario.add_plane", text="Add as plane").filepath = path
    elif rec.kind == "3d":
        primary = rec.meta.get("primary_mesh") or next(
            (p for p in rec.files if p.lower().endswith(MESH_EXTS)), ""
        )
        if primary:
            row = box.row(align=True)
            add = row.operator("scenario.import_mesh_file", text="Add to scene", icon="IMPORT")
            add.filepath, add.local_id = primary, rec.local_id
            names = "|".join(rec.meta.get("objects") or [])
            sel = row.operator(
                "scenario.select_result_objects", text="Select", icon="RESTRICT_SELECT_OFF"
            )
            sel.names, sel.local_id = names, rec.local_id
            rm = row.operator("scenario.delete_result_objects", text="", icon="TRASH")
            rm.names, rm.local_id = names, rec.local_id
        for alt in rec.meta.get("mesh_alternates") or []:
            row = box.row(align=True)
            row.label(text=os.path.basename(alt)[-40:], icon="FILE_3D")
            add = row.operator("scenario.import_mesh_file", text="Add")
            add.filepath, add.local_id = alt, rec.local_id
    elif rec.kind == "video" and not rec.meta.get("shared_job"):
        # Retained prototype rows; shared jobs play saved results from their descriptors.
        for index, path in enumerate(rec.files[:3]):
            row = box.row(align=True)
            row.operator("scenario.play_video", text="Play", icon="PLAY").filepath = path
            row.operator(
                "scenario.play_video_blender", text="Play in Blender", icon="BLENDER"
            ).filepath = path
            add = box.operator(
                "scenario.video_to_sequencer", text="Add video strip", icon="SEQUENCE"
            )
            add.local_id, add.file_index = rec.local_id, index
    elif rec.kind == "audio" and not rec.meta.get("shared_job"):
        for path in [p for p in rec.files if p.lower().endswith(AUDIO_EXTS)][:4] or rec.files[:2]:
            row = box.row(align=True)
            row.operator("scenario.play_video", text="Play", icon="PLAY").filepath = path
            row.operator(
                "scenario.add_sound_strip", text="Add to sequencer", icon="SEQUENCE"
            ).filepath = path
    elif rec.kind == "material":
        mat_name = (
            rec.meta.get("material_name")
            or f"Scenario {(rec.meta.get('prompt') or rec.model_id).strip()[:40]}"
        )
        row = box.row(align=True)
        row.label(text="PBR material", icon="MATERIAL")
        if bpy.data.materials.get(mat_name):
            row.operator(
                "scenario.retile_material", text="Tiling", icon="UV"
            ).material_name = mat_name
    elif rec.files:
        box.label(text=os.path.basename(rec.files[0]), icon="FILE")


def result_cost_label(rec):
    """A session job's approved price; a workflow quote not proven loop-free is a lower bound."""
    if rec.cu_cost is None:
        return ""
    lower_bound = rec.kind == "workflow" and workflow_loop_warning(
        rec.meta.get("workflow_loop_steps")
    )
    return f"{'from ' if lower_bound else ''}{format_cu(rec.cu_cost)} CU"


def history_cost_label(entry):
    """A row's documented charge; never 0 for a run whose step charges are unknown."""
    if entry.cu_cost is not None:
        return f"{format_cu(entry.cu_cost)} CU"
    return "Cost unavailable" if entry.cost_unavailable else entry.status


def draw_history(layout, context, shown_ids=()):
    if not runtime.catalog_selection_matches():
        layout.label(text="Refresh cloud history for the selected connection", icon="INFO")
        return
    if not runtime.state.history and not runtime.state.history_token:
        layout.label(text="Press Refresh cloud to list this project's generations", icon="INFO")
        return
    # Explicit history reads cache this projection. Live shared views also cover
    # acknowledgements delivered after that page, without storage I/O in draw.
    cached_ids = runtime.state.history_saved_ids
    if cached_ids is None:
        layout.label(text="Could not inspect saved jobs; refresh history", icon="ERROR")
        return
    if not runtime.state.history:
        # Uploads or mesh preview renders can fill a page; older pages may list generations.
        layout.label(text="No generations on this page; load older", icon="INFO")
    saved_ids = cached_ids | {
        view.job_id
        for view in runtime.state.jobs_view
        if view.job_id and view.meta.get("shared_job")
    }
    owner = runtime.state.model_jobs
    # Every loaded row: Load older appends a page, so a fixed slice would hide it.
    for entry in runtime.state.history:
        if entry.job_id in shown_ids:
            continue  # already listed among this session's results
        saved = bool(entry.local_request_ids) or entry.job_id in saved_ids
        kind = history.entry_kind(entry)
        box = layout.box()
        header = box.row()
        header.label(
            text=(entry.prompt or entry.model_id or entry.workflow_id)[:40],
            icon=KIND_ICON.get(kind, "FILE"),
        )
        header.label(text=history_cost_label(entry))
        if entry.workflow_job_id:
            box.label(text="Workflow step; included in its run's total once known", icon="LINKED")
        if entry.asset_ids:
            op = box.row(align=True).operator(
                "scenario.copy_text", text=entry.asset_ids[0], icon="COPYDOWN"
            )
            op.text, op.what = entry.asset_ids[0], "asset id"
        if saved:
            box.operator(
                "scenario.inspect_saved_jobs", text="Inspect saved jobs", icon="FILE_REFRESH"
            )
        elif entry.is_success and entry.kind != "workflow":
            read = owner.cloud_reads.get(entry.job_id) if owner else None
            pending = read is not None and read.pending
            if read is not None and read.error:
                box.label(text="Cloud read failed; inspect saved jobs or retry", icon="ERROR")
            row = box.row()
            row.enabled = not pending and bool(entry.model_id)
            op = row.operator(
                "scenario.import_result",
                text="Reading cloud job..." if pending else "Save for recovery",
                icon="FILE_REFRESH",
            )
            op.job_id, op.kind, op.model_id, op.prompt = (
                entry.job_id,
                kind,
                entry.model_id,
                entry.prompt,
            )
        else:
            # Cloud workflow runs have no model-job recovery; their steps do.
            icon = "CHECKMARK" if entry.is_success else "TIME"
            if entry.status in ("failure", "failed", "canceled"):
                icon = "ERROR"
            box.label(text=entry.status, icon=icon)
    if runtime.state.history_token:
        layout.operator("scenario.history_older", icon="TRIA_DOWN")


# -- panels -------------------------------------------------------------------


def draw_blockout_tab(layout, context):
    """Prompt to Blockout: describe a scene, the Scenario LLM designs a greybox, then refine or rebuild it."""
    from ..core.scene import blockout as core
    from . import blockout as bo

    props = context.scene.scenario_blockout
    box = layout.box()
    box.label(text="Prompt to Blockout", icon="MESH_CUBE")
    box.prop(props, "prompt", text="")
    row = box.row(align=True)
    row.prop(props, "scene_type", text="")
    row.prop(props, "scale", text="")
    box.operator("scenario.blockout_design", text="Get design price", icon="MOD_BUILD")

    plan = bo.stored_plan(context.scene)
    if plan:
        summary = core.plan_summary(plan)
        info = layout.box()
        info.label(
            text=f"{summary['total']} elements  ·  {len(summary['by_group'])} group(s)",
            icon="OUTLINER_OB_GROUP_INSTANCE",
        )
        grid = info.grid_flow(columns=2, align=True, even_columns=True)
        for cat, count in sorted(summary["by_category"].items(), key=lambda kv: (-kv[1], kv[0])):
            label = core.CATEGORIES.get(cat, core.CATEGORIES[core.DEFAULT_CATEGORY])[0]
            grid.label(text=f"{label}: {count}", icon="LAYER_ACTIVE")
        actions = info.row(align=True)
        actions.operator("scenario.blockout_build", text="Build plan", icon="FILE_REFRESH")
        actions.operator("scenario.blockout_clear", text="Clear", icon="TRASH")
        refine = layout.box()
        refine.label(text="Refine", icon="GREASEPENCIL")
        rrow = refine.row(align=True)
        rrow.prop(
            props, "refine", text="", placeholder="add a second floor, taller tower, more stalls..."
        )
        rrow.operator("scenario.blockout_refine", text="Get refinement price", icon="MOD_BUILD")
    bo.draw_status(layout, context.scene)
    layout.label(
        text="Generate a plan, then build its grouped primitives locally.",
        icon="INFO",
    )


class SCENARIO_PT_main(bpy.types.Panel):
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Scenario"
    bl_label = "Scenario"
    bl_order = 0

    def draw(self, context):
        layout = self.layout
        if not draw_account_strip(layout, context):
            return
        scenario = context.scene.scenario
        draw_enum_tabs(
            layout,
            scenario,
            "lane",
            (
                ("image", "video", "3d"),
                ("audio", "material", "panorama"),
                ("render_image", "render_video"),
                ("blockout",),
            ),
        )
        layout.separator(factor=0.5)  # breathing room between the tabs and the lane form
        lane = scenario.lane
        if lane == "blockout":
            draw_blockout_tab(layout, context)
        elif not runtime.state.catalog_loaded:
            draw_loading(layout)
        elif lane in GENERATE_LANES:
            draw_generate_lane(layout, context, lane)
        elif lane == "panorama":
            draw_panorama_lane(layout, context)
        elif lane == "render_image":
            from . import render_lanes

            render_lanes.draw_render_image_lane(layout, context)
        elif lane == "render_video":
            from . import render_lanes

            render_lanes.draw_render_video_lane(layout, context)
        message = runtime.message_visible()
        if message:
            layout.label(text=message[:80])


STATUS_TEXT = {
    "preparing": "Prompt Spark is writing the look",
    "submitting": "uploading and submitting",
    "queued": "queued",
    "in-progress": "rendering",
}
# Unfinished shared saved states. While a known remote job is polled, its latest
# validated Scenario status replaces "submitted".
SHARED_STATUS_TEXT = {
    "prepared": "waiting to submit",
    "submitting": "submitting",
    "uncertain": "submission unconfirmed",
    "remote": "submitted",
    "cancel_requested": "canceling",
    "succeeded": "finished on Scenario",
    "downloading": "downloading results",
    "download_failed": "download failed",
    "ready": "results saved",
    "applying": "applying",
    "apply_failed": "application needs review",
}
# Saved states whose next step needs Scenario, and what pauses without online
# access, whether or not a remote reading exists yet.
SHARED_OFFLINE_TEXT = {
    "remote": "Status paused while online access is disabled",
    "cancel_requested": "Status paused while online access is disabled",
    "succeeded": "Download paused while online access is disabled",
}


def draw_shared_status(layout, rec, model):
    """Saved state and ModelJobs' projected advisory remote reading; no I/O or writes."""
    state = rec.meta.get("saved_state", rec.status)
    reading = rec.meta.get("remote")
    text = SHARED_STATUS_TEXT.get(state, state)
    if reading is not None and state == "remote":
        text = reading.label
    layout.label(text=f"{model}: {text}", icon="TIME")
    if reading is not None and reading.percent is not None:
        layout.progress(factor=reading.fraction, type="BAR", text=f"{reading.percent}%")
    offline = None if runtime.online() else SHARED_OFFLINE_TEXT.get(state)
    if offline is not None:
        layout.label(text=offline, icon="INFO")
    elif reading is not None and reading.stale:
        layout.label(text="Status is not updating", icon="INFO")


def draw_active_job(layout, rec):
    """One unfinished job: its status, any review error and its saved-job controls."""
    box = layout.box()
    row = box.row(align=True)
    row.label(text=_short_prompt(rec, 44), icon=KIND_ICON.get(rec.kind, "TIME"))
    model = rec.meta.get("model_name", rec.model_id)
    if rec.meta.get("shared_job"):
        draw_shared_status(box, rec, model)
    else:
        status = STATUS_TEXT.get(rec.status, rec.status)
        progress = f" {int(rec.progress * 100)}%" if rec.status == "in-progress" else ""
        box.label(text=f"{model}: {status}{progress}", icon="TIME")
    if rec.error:
        box.operator(
            "scenario.error_details", text="Job needs review", icon="ERROR"
        ).local_id = rec.local_id
    result_previews.draw(box, rec)
    job_recovery.draw_controls(box, rec)


class SCENARIO_PT_jobs(bpy.types.Panel):
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Scenario"
    bl_label = ""
    bl_order = 1

    def draw_header(self, context):
        active = sum(1 for r in runtime.state.jobs_view if not r.is_terminal)
        self.layout.label(
            text="Jobs" if not active else ("1 Job" if active == 1 else f"{active} Jobs")
        )

    def draw(self, context):
        layout = self.layout
        layout.operator(
            "scenario.inspect_saved_jobs", text="Inspect saved jobs", icon="FILE_REFRESH"
        )
        active = [r for r in runtime.state.jobs_view if not r.is_terminal]
        if not active:
            layout.label(text="No job running", icon="CHECKMARK")
            return
        for rec in active:
            draw_active_job(layout, rec)


class SCENARIO_PT_generations(bpy.types.Panel):
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Scenario"
    bl_label = "Generations"
    bl_order = 2

    def draw(self, context):
        layout = self.layout
        row = layout.row(align=True)
        row.operator("scenario.open_output_folder", text="Output folder", icon="FILE_FOLDER")
        row.operator("scenario.history_refresh", text="Refresh cloud", icon="FILE_REFRESH")
        terminal = [r for r in runtime.state.jobs_view if r.is_terminal]
        if terminal:
            any_open = any(not r.meta.get("collapsed") for r in terminal)
            row.operator(
                "scenario.collapse_results",
                text="Collapse all" if any_open else "Expand all",
                icon="FULLSCREEN_EXIT" if any_open else "FULLSCREEN_ENTER",
            )
        shown, shown_ids = 0, set()
        for rec in runtime.state.jobs_view:
            if not rec.is_terminal:
                continue
            draw_result(layout, rec)
            if rec.meta.get("shared_job"):
                shown_ids.add(rec.job_id)
            shown += 1
            if shown >= 12:
                break
        if shown == 0:
            layout.label(text="Nothing generated yet in this session")
        layout.separator()
        layout.prop(
            context.scene.scenario,
            "show_cloud_history",
            text="Project history (cloud)",
            icon="WORLD",
        )
        if context.scene.scenario.show_cloud_history:
            draw_history(layout, context, shown_ids)


class SCENARIO_PT_agents(bpy.types.Panel):
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Scenario"
    bl_label = "Agents (MCP)"
    bl_order = 3
    bl_options = {"DEFAULT_CLOSED"}

    def draw(self, context):
        draw_mcp_lane(self.layout, context)


CLASSES = (SCENARIO_PT_main, SCENARIO_PT_jobs, SCENARIO_PT_generations, SCENARIO_PT_agents)


def register():
    for cls in CLASSES:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)
