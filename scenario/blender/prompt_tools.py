# SPDX-FileCopyrightText: 2026 Scenario Inc.
# SPDX-License-Identifier: GPL-3.0-or-later
"""Native prompt tools request an exact price before explicit paid approval."""

import textwrap

import bpy
from bpy.props import EnumProperty, StringProperty

from ..core.ui.costs import format_cu
from . import runtime
from .prompt_jobs import LABELS

TOOLTIP_GENERATE = "Request the exact price for a new prompt before approving generation"
TOOLTIP_REWRITE = "Request the exact price to rewrite this prompt before approving generation"
TOOLTIP_TRANSLATE = "Request the exact price to translate this prompt to English"
MODE_ITEMS = [
    ("GENERATE", "Generate a new prompt", TOOLTIP_GENERATE),
    ("REWRITE", "Rewrite your prompt", TOOLTIP_REWRITE),
]


def _poll(context):
    from .operators import _network_poll

    return _network_poll(None, context)


def on_prompt_event(payload):
    """Ignore retired unbound events; only JobSession may deliver prompt results."""


def _quote(operator, context, action):
    try:
        runtime.ensure_prompt_jobs().quote(context.scene, operator.lane, action)
    except Exception:
        operator.report(
            {"WARNING"}, "Could not request a prompt price; check the field and connection"
        )
        return {"CANCELLED"}
    return {"FINISHED"}


class SCENARIO_OT_prompt_spark(bpy.types.Operator):
    bl_idname = "scenario.prompt_spark"
    bl_label = "Prompt Spark"
    bl_description = TOOLTIP_GENERATE
    lane: StringProperty(default="image")
    mode: EnumProperty(items=MODE_ITEMS, default="GENERATE")

    @classmethod
    def poll(cls, context):
        return _poll(context)

    @classmethod
    def description(cls, context, properties):
        return TOOLTIP_REWRITE if properties.mode == "REWRITE" else TOOLTIP_GENERATE

    def execute(self, context):
        return _quote(self, context, self.mode)


class SCENARIO_OT_prompt_translate(bpy.types.Operator):
    bl_idname = "scenario.prompt_translate"
    bl_label = "Translate to English"
    bl_description = TOOLTIP_TRANSLATE
    lane: StringProperty(default="image")

    @classmethod
    def poll(cls, context):
        return _poll(context)

    def execute(self, context):
        return _quote(self, context, "TRANSLATE")


class SCENARIO_OT_prompt_approve(bpy.types.Operator):
    bl_idname = "scenario.prompt_approve"
    bl_label = "Approve prompt generation"
    bl_description = "Approve this quote once and update the unchanged original prompt"
    quote_id: StringProperty(options={"SKIP_SAVE"})
    approved_cost: StringProperty(options={"SKIP_SAVE"})

    @classmethod
    def poll(cls, context):
        return _poll(context)

    def execute(self, context):
        try:
            runtime.ensure_prompt_jobs().approve(
                self.quote_id, context.scene, approved_cost=self.approved_cost
            )
        except Exception:
            self.report(
                {"WARNING"},
                "Prompt approval is stale or could not be submitted; inspect saved jobs",
            )
            return {"CANCELLED"}
        return {"FINISHED"}


class SCENARIO_OT_prompt_clear(bpy.types.Operator):
    bl_idname = "scenario.prompt_clear"
    bl_label = "Clear prompt"
    bl_description = "Delete the prompt text"
    bl_options = {"REGISTER", "UNDO"}
    lane: StringProperty(default="image")

    def invoke(self, context, event):
        return context.window_manager.invoke_confirm(self, event)

    def execute(self, context):
        lane = context.scene.scenario.lane_state(self.lane)
        if lane is None:
            return {"CANCELLED"}
        lane.prompt = ""
        return {"FINISHED"}


def draw_prompt_status(layout, lane_state, lane):
    jobs = runtime.state.prompt_jobs
    item = jobs.current(bpy.context.scene, lane) if jobs is not None else None
    if item is None:
        return
    if item.phase == "READY":
        layout.label(text=f"Cost: {format_cu(item.cost)} CU")
        row = layout.row()
        row.enabled = (
            lane_state.prompt == item.original_text and lane_state.model_id == item.model_id
        )
        op = row.operator(
            SCENARIO_OT_prompt_approve.bl_idname,
            text=f"Approve {LABELS[item.action]}",
            icon="CHECKMARK",
        )
        # Formatting is presentation only; approval stays bound to the exact quote.
        op.quote_id, op.approved_cost = item.identifier, item.cost
    elif item.phase == "QUOTING":
        layout.label(text="Getting prompt price...", icon="TIME")
    elif item.phase in {"SUBMITTING", "POLLING", "READING"}:
        layout.label(text="Preparing prompt...", icon="TIME")
    elif item.phase == "ERROR":
        for index, line in enumerate(textwrap.wrap(item.error, 32)):
            layout.label(text=line, icon="ERROR" if index == 0 else "NONE")
        if item.request_id:
            layout.operator(
                "scenario.inspect_saved_jobs", text="Inspect saved jobs", icon="VIEWZOOM"
            )


def _icon_kwargs(name):
    try:
        from . import icons

        return icons.kwargs(name)
    except ImportError:
        return {
            "icon": {
                "dice": "LIGHT_SUN",
                "sparkles": "FILE_REFRESH",
                "translate": "WORLD_DATA",
            }.get(name, "QUESTION")
        }


def draw_prompt_row(
    layout, lane_state, lane, text="", placeholder=None, wrap_width=44, max_lines=3
):
    """The prompt as a box of its own, like Scenario's: a header, the full-width field, the long prompt wrapped
    underneath so it stays readable, and the three prompt tools (dice, sparkles, translate) as a row of equal
    buttons. Rewrite and Translate need a prompt to work on."""
    box = layout.box()
    header = box.row(align=True)
    header.label(text=text or "Prompt", icon="TEXT")
    rows = int(getattr(lane_state, "prompt_rows", 1) or 1)
    grip = header.row(align=True)
    grip.alignment = "RIGHT"
    clear = grip.row(align=True)
    clear.enabled = bool(lane_state.prompt)  # greyed out when there is nothing to delete
    clear.operator(SCENARIO_OT_prompt_clear.bl_idname, text="", icon="TRASH").lane = lane
    grip.prop(
        lane_state, "prompt_rows", text="", icon="FIXED_SIZE"
    )  # drag to grow the box (height)
    field = box.column(align=True)
    field.scale_y = max(
        1.0, float(rows)
    )  # the prompt lives here; drag the height to make it taller
    if placeholder:
        field.prop(lane_state, "prompt", text="", placeholder=placeholder)
    else:
        field.prop(lane_state, "prompt", text="")
    # three equal buttons spanning the box; Blender renders icon-only buttons at a fixed size and never stretches
    # them, so each carries a short label, which makes the row fill the width evenly (like the web app's three tools)
    tools = box.row(align=True)
    op = tools.operator(SCENARIO_OT_prompt_spark.bl_idname, text="New", **_icon_kwargs("dice"))
    op.lane, op.mode = lane, "GENERATE"
    op = tools.operator(
        SCENARIO_OT_prompt_spark.bl_idname, text="Rewrite", **_icon_kwargs("sparkles")
    )
    op.lane, op.mode = lane, "REWRITE"
    tools.operator(
        SCENARIO_OT_prompt_translate.bl_idname, text="Translate", **_icon_kwargs("translate")
    ).lane = lane
    draw_prompt_status(box, lane_state, lane)
    return box


CLASSES = (
    SCENARIO_OT_prompt_spark,
    SCENARIO_OT_prompt_translate,
    SCENARIO_OT_prompt_approve,
    SCENARIO_OT_prompt_clear,
)


def register():
    for cls in CLASSES:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)
