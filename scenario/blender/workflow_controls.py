# SPDX-FileCopyrightText: 2026 Scenario Inc.
# SPDX-License-Identifier: GPL-3.0-or-later
"""Native workflow forms over the shared session, quotes and durable jobs."""

import functools
import json
import re
import textwrap
from dataclasses import dataclass
from types import SimpleNamespace

import bpy
from bpy.props import (
    BoolProperty,
    CollectionProperty,
    EnumProperty,
    PointerProperty,
    StringProperty,
)

from ..core.api.errors import ScenarioError
from ..core.schema.forms import display_label, schema_defaults, validate_parameters
from ..core.schema.params import parse_schema
from ..core.ui.costs import workflow_loop_warning
from . import file_state, label_wrap, reference_form, runtime, workflow_steps

# Persisted ID properties on workflow file inputs. A marker blocks pricing until
# its upload attaches or is explicitly resolved; the request records provenance.
UPLOAD_MARKER = "_scenario_workflow_upload"
UPLOAD_REQUEST = "_scenario_workflow_upload_request"
# Interface units requested for the Generate workflow confirmation.
CONFIRMATION_WIDTH = 540
# Interface units a warning line drawn straight in a popup's layout, as in the
# confirmation or on Studio's Workflows page, cannot use: the first line's icon
# and the label's text and clipping margins, plus slack.
WARNING_INSET = 64


def _json(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, allow_nan=False)


def _choices(self, context):
    # Keep enum strings alive after reopening a saved form. Only this plain
    # Python cache changes during drawing, never RNA or request state.
    key = ("workflow_choices", self.options)
    if key not in runtime.state.enum_cache:
        values = json.loads(self.options or "[]")
        runtime.state.enum_cache[key] = [
            (str(i), str(value), "") for i, value in enumerate(values)
        ] or [("NONE", "No choices", "")]
    return runtime.state.enum_cache[key]


def _on_upload_path(self, context):
    from .reference_uploads import note_chosen_file

    # The user's choice here lets local MCP upload this file (an agent's change does not).
    note_chosen_file(self.upload_path)


class ScenarioWorkflowInput(bpy.types.PropertyGroup):
    name: StringProperty()
    label: StringProperty()
    kind: StringProperty()
    description: StringProperty()
    enabled: BoolProperty(name="Include input", default=True)
    text: StringProperty(name="Value")
    boolean: BoolProperty(name="Value")
    asset_scope: StringProperty()
    asset_value: StringProperty()
    options: StringProperty()
    choice: EnumProperty(name="Value", items=_choices)
    upload_path: StringProperty(
        name="File",
        description="Local file to upload explicitly for this workflow input",
        subtype="FILE_PATH",
        # Uploads resolve blend-relative paths with bpy.path.abspath.
        options={"PATH_SUPPORTS_BLEND_RELATIVE"},
        update=_on_upload_path,
    )


class ScenarioWorkflowForm(bpy.types.PropertyGroup):
    workflow_id: StringProperty(name="Workflow ID")
    loaded_id: StringProperty()
    title: StringProperty()
    schema_json: StringProperty()
    inputs: CollectionProperty(type=ScenarioWorkflowInput)


def fields_for(record):
    fields = record.get("inputs_definition")
    if fields is None:
        fields = record.get("inputs")
    if isinstance(fields, dict):
        fields = [dict(value, name=name) for name, value in fields.items()]
    schema = {"parameters": fields}
    # Validate names, types and conditional relationships before changing the form.
    schema_defaults(schema)
    if len(fields) > 128:
        raise ValueError("This workflow has too many inputs for the native form")
    return schema


def load_form(form, record):
    schema = fields_for(record)
    defaults = schema_defaults(schema)
    serialized = _json(schema)
    required = {
        spec.name: spec.required_always
        for spec in parse_schema(
            SimpleNamespace(parameters=schema["parameters"], ui_config={})
        ).specs
    }
    rows = []
    for field in schema["parameters"]:
        name, kind = field["name"], field.get("type", "string")
        if kind == "file" and field.get("array") is True:
            kind = "file_array"
        value = defaults.get(name)
        is_text = kind in {"string", "file", "model"}
        rows.append(
            (
                field,
                kind,
                name in defaults,
                "" if value is None else str(value) if is_text else _json(value),
            )
        )
    form.inputs.clear()
    for field, kind, enabled, value in rows:
        item = form.inputs.add()
        item.name = field["name"]
        item.label = str(field.get("label") or display_label(item.name))
        item.kind = kind
        item.description = str(field.get("description") or "")
        item.enabled = enabled or required[item.name]
        item.text = value
        item.boolean = defaults.get(item.name) is True
        choices = field.get("allowedValues", field.get("allowed_values", field.get("enum")))
        if isinstance(choices, list) and choices and not kind.endswith("_array") and kind != "file":
            item.options = _json(choices)
            default = _json(defaults.get(item.name))
            item.choice = str(next((i for i, v in enumerate(choices) if _json(v) == default), 0))
    form.schema_json = serialized
    form.loaded_id = form.workflow_id = record["id"]
    form.title = str(record.get("name") or record["id"])


def input_value(item):
    # Previously saved forms keep file-enum selections in choice, not text.
    # Preserve that selection until the input is explicitly cleared or reloaded.
    if item.options:
        choices = json.loads(item.options)
        index = int(item.choice)
        if not 0 <= index < len(choices):
            raise ValueError(f"Choose a value for {item.label}")
        return choices[index]
    if item.kind == "boolean":
        return item.boolean
    if item.kind in {"string", "file", "model"}:
        return item.text
    try:
        return json.loads(item.text)
    except (ValueError, TypeError) as error:
        raise ValueError(f"Check the value for {item.label}") from error


def parameters(form):
    if not form.loaded_id or form.loaded_id != form.workflow_id or not form.schema_json:
        raise ValueError("Load the selected workflow's inputs first")
    values = {}
    for item in form.inputs:
        if item.get(UPLOAD_MARKER):
            raise ValueError(
                f"{item.label}: finish the reference upload or inspect its saved progress "
                "before requesting a price"
            )
        if not item.enabled:
            continue
        value = input_value(item)
        if item.asset_scope:
            store = runtime.state.job_store
            if (
                store is None
                or not runtime.catalog_selection_matches()
                or reference_form.scope_key(store.scope) != item.asset_scope
                or _json(value) != item.asset_value
            ):
                raise ValueError(
                    f"{item.label}: reference connection or value changed; clear and choose it again"
                )
        values[item.name] = value
    errors = validate_parameters(json.loads(form.schema_json), values)
    if errors:
        raise ValueError("\n".join(errors))
    return values


def input_labels(form):
    """The label the form draws beside each input's value, by parameter name."""
    return {item.name: item.label for item in form.inputs if item.label}


def signature(form):
    value = [
        form.workflow_id,
        form.loaded_id,
        form.schema_json,
        [
            (
                x.name,
                x.kind,
                x.enabled,
                x.text,
                x.boolean,
                x.options,
                x.choice if x.options else "",
                x.asset_scope,
                x.asset_value,
            )
            for x in form.inputs
        ],
    ]
    uploads = [(x.name, str(x[UPLOAD_MARKER])) for x in form.inputs if x.get(UPLOAD_MARKER)]
    if uploads:
        # Starting or resolving an upload invalidates prior approvals. Unmarked
        # forms keep the earlier signature so saved-state comparisons still hold.
        value.append(uploads)
    return _json(value)


def _loaded(form):
    """Everything load_form writes, for the file-modified mark.

    signature() is the approval identity: its stored schema carries the inputs'
    labels and descriptions, but not the displayed title, which a reload of a
    renamed workflow changes alone.
    """
    return signature(form), form.title


@dataclass
class WorkflowView:
    scene: object
    task: object = None
    action: str = ""
    signature: str = ""
    ticket: object = None
    cost: str = ""
    # Nonblocking: a loop quote covers one pass, so the charge can be higher.
    cost_warning: str = ""
    error: str = ""


class WorkflowControls:
    """Own only UI projections; workers and durable jobs belong to ModelJobs."""

    def __init__(self, jobs):
        self.jobs = jobs
        self.views = {}
        self.catalog = []
        self.catalog_privacy = ""

    def view(self, scene, *, create=False):
        key = scene.as_pointer()
        value = self.views.get(key)
        if value is not None and value.scene != scene:
            value = None
        if value is None and create:
            if len(self.views) >= 32:
                for old_key, old in tuple(self.views.items()):
                    if old.task is not None:
                        continue
                    if old.ticket is not None and not old.ticket.used:
                        self.jobs.discard_workflow_quote(old.ticket.identifier)
                    del self.views[old_key]
                    break
                else:
                    raise ValueError("Wait for a running workflow request to finish")
            value = self.views[key] = WorkflowView(scene)
        return value

    def start(self, scene, action, *, privacy="private"):
        self.poll()
        view = self.view(scene, create=True)
        if view.task is not None:
            raise ValueError("Wait for the current workflow request")
        form = scene.scenario_workflow
        view.error = ""
        if action == "price":
            values = parameters(form)
            if view.ticket is not None and not view.ticket.used:
                self.jobs.discard_workflow_quote(view.ticket.identifier)
            view.ticket = self.jobs.quote_workflow(scene, form.workflow_id, values)
            view.task = view.ticket.task
            view.cost = view.cost_warning = ""
        elif action in {"load", "list"}:
            if action == "load" and not form.workflow_id.strip():
                raise ValueError("Choose a workflow first")
            view.task = self.jobs.session.workflow_metadata(
                scene, identifier=form.workflow_id if action == "load" else None, privacy=privacy
            )
        else:
            raise ValueError("Unknown workflow action")
        view.signature = signature(form)
        view.action = action + (":" + privacy if action == "list" else "")
        return view

    def poll(self):
        if not self.jobs.session.active:
            return
        for view in self.views.values():
            if view.task is None or not view.task.done():
                continue
            task, view.task = view.task, None
            try:
                if view.action == "price":
                    estimate = self.jobs.finish_quote(view.ticket)
                    if signature(view.scene.scenario_workflow) != view.signature:
                        self.jobs.discard_workflow_quote(view.ticket.identifier)
                        raise ValueError("Inputs changed")
                    view.cost = str(estimate.cost)
                    view.cost_warning = workflow_loop_warning(estimate.loop_steps) or ""
                else:
                    outcomes = self.jobs.session.drain(task=task)
                    if not outcomes:
                        raise ValueError("Missing workflow completion")
                    if view.action == "load":
                        result = self.jobs.session.deliver(outcomes[0], lambda value, *_: value)
                        if signature(view.scene.scenario_workflow) != view.signature:
                            raise ValueError("Inputs changed")
                        if view.ticket is not None and not view.ticket.used:
                            self.jobs.discard_workflow_quote(view.ticket.identifier)
                        form = view.scene.scenario_workflow
                        # Delivered by maintenance, after the load operator has returned. A
                        # refused schema changes nothing; a failure part-way still marks.
                        read = functools.partial(_loaded, form)
                        with file_state.marking("Scenario workflow form", read):
                            load_form(form, result)
                        view.cost = view.cost_warning = ""
                    else:
                        result = self.jobs.session.deliver_workflow_catalog(outcomes[0])
                        self.catalog = result
                        self.catalog_privacy = view.action.split(":", 1)[1]
            except Exception:
                view.error = "Could not finish this workflow request. Check the connection and unchanged inputs, then try again."
                view.cost = view.cost_warning = ""

    def ready(self, scene):
        view = self.view(scene)
        if (
            view is None
            or view.task is not None
            or not view.cost
            or view.ticket is None
            or view.ticket.used
            or signature(scene.scenario_workflow) != view.signature
        ):
            raise ValueError("Request a fresh workflow price")
        self.jobs.require_quote(view.ticket.identifier)
        return view

    def approve(self, scene, quote_id, cost):
        view = self.ready(scene)
        if view.ticket.identifier != quote_id or view.cost != cost:
            raise ValueError("The workflow approval changed")
        form = scene.scenario_workflow
        return self.jobs.submit_workflow(
            quote_id, scene, form.workflow_id, parameters(form), approved_cost=cost
        )


def controls(*, create=True):
    if create:
        jobs = runtime.ensure_model_jobs()
        if runtime.state.workflow_controls is None:
            runtime.state.workflow_controls = WorkflowControls(jobs)
    owner = runtime.state.workflow_controls
    if (
        owner is None
        or owner.jobs is not runtime.state.model_jobs
        or not owner.jobs.session.active
        or not runtime.catalog_selection_matches()
    ):
        return None
    return owner


def _error(operator, message):
    operator.report({"WARNING"}, message)
    return {"CANCELLED"}


class SCENARIO_OT_workflow_catalog(bpy.types.Operator):
    bl_idname = "scenario.workflow_catalog"
    bl_label = "Refresh workflows"
    privacy: StringProperty(default="private", options={"HIDDEN", "SKIP_SAVE"})

    def execute(self, context):
        try:
            if self.privacy not in {"private", "public"}:
                raise ValueError("Invalid privacy")
            controls().start(context.scene, "list", privacy=self.privacy)
        except Exception:
            return _error(self, "Enable online access and wait for any workflow request to finish")
        return {"FINISHED"}


class SCENARIO_OT_load_workflow(bpy.types.Operator):
    bl_idname = "scenario.load_workflow"
    bl_label = "Load workflow inputs"
    bl_description = "Read this workflow and replace its input form with the saved defaults"
    workflow_id: StringProperty(options={"HIDDEN", "SKIP_SAVE"})

    def invoke(self, context, event):
        return context.window_manager.invoke_confirm(self, event)

    def execute(self, context):
        try:
            owner = controls()
            view = owner.view(context.scene)
            if view is not None and view.task is not None:
                raise ValueError("Pending workflow request")
            if self.workflow_id:
                context.scene.scenario_workflow.workflow_id = self.workflow_id
            # Flush this explicit selection before capturing the read's scene revision.
            context.view_layer.update()
            owner.start(context.scene, "load")
        except Exception:
            return _error(self, "Choose a workflow and wait for any pending request")
        return {"FINISHED"}


class SCENARIO_OT_estimate_workflow(bpy.types.Operator):
    bl_idname = "scenario.estimate_workflow"
    bl_label = "Request workflow price"

    def execute(self, context):
        try:
            controls().start(context.scene, "price")
        except ValueError as error:
            return _error(self, str(error))
        except Exception:
            return _error(self, "Check online access, the workflow and its inputs")
        return {"FINISHED"}


class SCENARIO_OT_generate_workflow(bpy.types.Operator):
    bl_idname = "scenario.generate_workflow"
    bl_label = "Generate workflow"
    bl_description = "Approve the exact displayed price and save one workflow job"

    def invoke(self, context, event):
        try:
            self._owner = controls()
            view = self._owner.ready(context.scene)
            self._scene = context.scene
            self._scene_name = context.scene.name
            self._quote_id, self._cost = view.ticket.identifier, view.cost
            self._warning = view.cost_warning
            form = context.scene.scenario_workflow
            self._title = form.title
            self._payload = view.ticket.quote.estimate.payload
            # The quote is current for this form, so its inputs are the ones priced.
            self._labels = input_labels(form)
        except Exception:
            return _error(self, "Request a current workflow price first")
        return context.window_manager.invoke_props_dialog(self, width=CONFIRMATION_WIDTH)

    def draw(self, context):
        self.layout.label(text=self._title)
        self.layout.label(text=f"Scene: {self._scene_name}")
        for name, value in self._payload.items():
            # Name each value as the form does; a value it has no input for keeps its readable name.
            label = self._labels.get(name) or display_label(name)
            for line in textwrap.wrap(f"{label}: {_json(value)}", 70):
                self.layout.label(text=line)
        self.layout.label(text=f"Exact price: {self._cost} CU")
        # Blender lays a popup out before sizing it; wrapped lines measure the requested width.
        with label_wrap.popup_layout(CONFIRMATION_WIDTH):
            _warn(self.layout, context, self._warning)
        self.layout.label(text="Submit once. Inspect saved jobs after an uncertain response.")
        self.layout.label(text="Results require separate application.")
        self.layout.label(text="Approval and selection steps wait for your review in Jobs.")
        self.layout.label(
            text="A running step cannot be canceled; Stop workflow at a waiting step."
        )

    def execute(self, context):
        try:
            if controls() is not self._owner or context.scene != self._scene:
                raise ValueError("Context changed")
            self._owner.approve(self._scene, self._quote_id, self._cost)
        except Exception:
            return _error(self, "Review this workflow and inspect saved jobs before continuing")
        return {"FINISHED"}


def _warn(layout, context, text):
    """Draw a warning wrapped by the width the interface font measures for it.

    A warning that needs more than one line starts each sentence on a line of
    its own, so no line ends with the first words of the next sentence.
    """
    if not text:
        return
    lines = label_wrap.label_lines(context, text, WARNING_INSET)
    if len(lines) > 1:
        lines = [
            line
            for sentence in re.split(r"(?<=\.) +", text)
            for line in label_wrap.label_lines(context, sentence, WARNING_INSET)
        ]
    for index, line in enumerate(lines):
        layout.label(text=line, icon="ERROR" if index == 0 else "BLANK1")


def draw(layout, context):
    form = context.scene.scenario_workflow
    owner = controls(create=False)
    workflow_steps.draw_pending(layout, context)
    box = layout.box()
    box.label(text="Workflow", icon="NODETREE")
    row = box.row(align=True)
    for privacy in ("private", "public"):
        row.operator("scenario.workflow_catalog", text=privacy.title()).privacy = privacy
    if owner is not None:
        query = context.window_manager.scenario_workflow_query
        box.prop(context.window_manager, "scenario_workflow_query", text="Search")
        rows = [
            r for r in owner.catalog if query.casefold() in str(r.get("name") or r["id"]).casefold()
        ]
        for record in rows[:40]:
            box.operator(
                "scenario.load_workflow", text=str(record.get("name") or record["id"])
            ).workflow_id = record["id"]
        if len(rows) > 40:
            box.label(text="Refine the search to show more workflows")
    box.prop(form, "workflow_id")
    box.operator("scenario.load_workflow")
    if form.loaded_id:
        box = layout.box()
        box.label(text=form.title or "Inputs", icon="PREFERENCES")
        box.label(text="Unchecked inputs use the workflow defaults when available")
        schema = json.loads(form.schema_json)
        for item, field in zip(form.inputs, schema["parameters"], strict=False):
            row = box.row()
            row.prop(item, "enabled", text="")
            value = row.row()
            value.enabled = item.enabled
            prop = "choice" if item.options else "boolean" if item.kind == "boolean" else "text"
            value.prop(item, prop, text=item.label)
            if item.kind in {"file", "file_array"}:
                from . import workflow_uploads

                workflow_uploads.draw(box, context.scene, item, field)
                box.operator(
                    "scenario.clear_workflow_reference",
                    text="Clear references" if item.kind == "file_array" else "Clear reference",
                ).input_name = item.name
                if item.asset_scope:
                    box.label(text="Reference is bound to its selected connection", icon="LINKED")
            if item.kind not in {"string", "file", "model", "boolean", "number", "integer"}:
                box.label(text="Structured value (JSON)")
            allowed = field.get("allowedValues", field.get("allowed_values", field.get("enum")))
            if allowed:
                box.label(text="Choices: " + ", ".join(str(v) for v in allowed))
    view = owner.view(context.scene) if owner is not None else None
    if view is not None and view.task is not None:
        layout.label(text="Loading workflow request...", icon="TIME")
    elif view is not None and view.error:
        layout.label(text="Workflow request needs review", icon="ERROR")
    row = layout.row()
    row.enabled = runtime.online() and (view is None or view.task is None)
    row.operator("scenario.estimate_workflow")
    ready = False
    if owner is not None:
        try:
            view = owner.ready(context.scene)
            ready = True
        except (ValueError, ReferenceError, ScenarioError):
            # Missing, stale or invalid quotes leave Generate disabled. Drawing
            # must not refresh the quote or mutate the saved form to recover.
            pass
    row = layout.row()
    row.scale_y = 1.5
    row.enabled = ready and runtime.online()
    text = "Generate (request price)"
    if ready:
        # A loop quote is a lower bound: show it as "from" the exact quoted price.
        text = f"Generate ({'from ' if view.cost_warning else ''}{view.cost} CU)"
    row.operator("scenario.generate_workflow", text=text, icon="PLAY")
    if ready and view.cost_warning:
        _warn(layout, context, view.cost_warning)


CLASSES = (
    ScenarioWorkflowInput,
    ScenarioWorkflowForm,
    SCENARIO_OT_workflow_catalog,
    SCENARIO_OT_load_workflow,
    SCENARIO_OT_estimate_workflow,
    SCENARIO_OT_generate_workflow,
)


def register():
    for cls in CLASSES:
        bpy.utils.register_class(cls)
    bpy.types.Scene.scenario_workflow = PointerProperty(type=ScenarioWorkflowForm)
    bpy.types.WindowManager.scenario_workflow_query = StringProperty(options={"SKIP_SAVE"})


def unregister():
    del bpy.types.WindowManager.scenario_workflow_query
    del bpy.types.Scene.scenario_workflow
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)
