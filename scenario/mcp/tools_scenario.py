# SPDX-FileCopyrightText: 2026 Scenario Inc.
# SPDX-License-Identifier: GPL-3.0-or-later
"""MCP tools that talk to Scenario through the add-on: catalog, cost, generate, results into the scene."""

import functools
import json
import math
import time
from dataclasses import asdict
from pathlib import Path

import bpy

from ..blender import file_state, generation, runtime, trained_defaults, trained_models
from ..blender import result_previews as preview_tiles
from ..blender.props import GENERATION_LANES as NATIVE_LANES
from ..core import audio_waveform
from ..core.api import catalog, trained_offers
from ..core.api.catalog import GENERATION_LANES as LANES
from ..core.api.catalog import RENDER_LANES, models_for_lane
from ..core.api.errors import ScenarioError
from ..core.api.library import asset_summary as _asset_summary
from ..core.api.trained_routes import LoraSlot
from ..core.jobs.result_previews import (
    ENVELOPE,
    STILL,
    PreviewError,
    PreviewState,
    is_local,
    preview_kind,
    supports,
)
from ..core.jobs.results import PREVIEW_STATES, ResultError
from ..core.jobs.workflow_nodes import BLOCKED
from ..core.scene import panorama_contract
from ..core.scene.panorama import describe_world_media
from ..core.schema.params import build_body, validate
from ..core.ui import capability_status
from ..core.ui.costs import workflow_quote_notice
from .protocol import DeferredTool, ToolSpec


def _catalog_ready():
    generation.process_catalog_events()
    if not runtime.state.catalog_loaded:
        generation.request_catalog()
        raise RuntimeError("The model catalog is still loading; call again in a few seconds")


SOURCES = ("catalog", "trained", "all")
TRAINED_LIMIT = 40


def _within_limit(rows):
    """At most TRAINED_LIMIT rows in listing order, always keeping those the default uses.

    A default names at most MAX_PICKS models, so its rows always fit; a cut
    never hides which listed models the lane's saved default runs.
    """
    room = TRAINED_LIMIT - sum(1 for row in rows if row.get("default"))
    kept = []
    for row in rows:
        if row.get("default"):
            kept.append(row)
        elif room > 0:
            kept.append(row)
            room -= 1
    return kept


def _trained_listing(lane, query):
    """The picker's Trained chip rows for `lane`, with routes, availability and default marks.

    Like the chip, this reads a failed trained list again and the lane's
    unknown base descriptions once per connection; rows wait for those reads.
    """
    state = runtime.state
    trained_models.request_bases(lane)
    if not state.trained_loaded:
        if state.trained_loading:
            raise RuntimeError("Trained models are still loading; call again in a few seconds")
        raise ScenarioError(
            0, f"Could not load trained models: {state.trained_error or 'online access is off'}"
        )
    default = None
    try:
        default = trained_defaults.saved(trained_defaults.load(), lane)
    except ScenarioError:
        pass  # no selected scope, so no default; the rows themselves need no store
    rows = []
    for offer in trained_models.offers(lane):
        record = offer.record
        if query and query not in (record.name + " " + record.id).lower():
            continue
        rows.append(
            trained_offers.describe_offer(
                offer, private=trained_models.is_private(record), default=default
            )
        )
    scan = state.trained_scans.get(lane) or {}
    bases = {
        "checked": scan.get("checked", 0),
        "total": scan.get("total", 0),
        "pending": bool(scan.get("pending")),
        "error": scan.get("error", ""),
    }
    result = {"trained": _within_limit(rows), "bases": bases}
    if bases["pending"]:
        result["note"] = "base model descriptions are still being checked; call again for routes"
    return result


DESCRIPTION_WAIT = 30.0  # seconds a call waits for the model description read it needs
DESCRIPTION_LOADING = (
    "The model description is still loading; call again in a few seconds. "
    "Nothing was quoted, submitted or changed"
)


def _after_description(handler):
    """Serve a tool whose model description may not be loaded yet.

    The first call for an uncached model starts or joins its background read
    (ModelLoading). The MCP request thread waits for that read's worker, never
    Blender's main thread; the main thread then delivers the read and runs the
    tool once more. Each tool reads the description before it quotes, submits
    or changes anything, so the second run starts from the same state. A tool
    that only inspects (an optional description) then answers with its own
    errors instead of failing.
    """

    @functools.wraps(handler)
    def tool(args):
        try:
            return handler(args)
        except generation.ModelLoading as loading:
            model_id, required = loading.model_id, loading.required
        reader = generation.model_reader(model_id)
        if reader is None:
            if required:
                raise ScenarioError(0, DESCRIPTION_LOADING)
            with generation.waited_for(model_id):
                return handler(args)
        server = runtime.state.mcp
        deadline = time.monotonic() + DESCRIPTION_WAIT

        def run():
            while reader.is_alive():
                remaining = deadline - time.monotonic()
                if remaining <= 0 or (server is not None and not server.running):
                    return
                reader.join(min(0.1, remaining))

        def finish(_):
            generation.process_catalog_events()
            failure = runtime.state.model_errors.get(model_id)
            if required and failure and not generation.is_loading(model_id):
                # Running the tool again would start another read; the next call does.
                raise ScenarioError(0, f"{failure}; call again to retry")
            try:
                with generation.waited_for(model_id):
                    return handler(args)
            except generation.ModelLoading:
                raise ScenarioError(0, DESCRIPTION_LOADING) from None

        return DeferredTool(run, finish)

    return tool


def list_models(args):
    _catalog_ready()
    lane = args.get("lane") or "image"
    if lane not in LANES:
        raise ValueError(f"lane must be one of {LANES}")
    source = args.get("source") or "catalog"
    if source not in SOURCES:
        raise ValueError(f"source must be one of {SOURCES}")
    query = (args.get("query") or "").lower()
    if source != "catalog" and lane not in trained_models.LANES:
        raise ValueError("Trained models run in the image and render_image lanes")
    if source == "trained":
        return {"lane": lane, "source": source, **_trained_listing(lane, query)}
    records = runtime.state.lane_models.get(lane, [])
    if lane == "3d":
        everything = list(runtime.state.records.values())
        records = generation.three_d_models("TEXT", everything) + generation.three_d_models(
            "IMAGE", everything
        )
    elif lane == "panorama":
        # The native Environment list follows the scene's mode; list every
        # contract model here, as the 3D lane lists every input mode.
        records = models_for_lane(lane, list(runtime.state.records.values()))
    out, seen = [], set()
    for rec in records:
        if rec.id in seen:
            continue
        seen.add(rec.id)
        if query and query not in (rec.name + " " + rec.short_description + " " + rec.id).lower():
            continue
        entry = {
            "id": rec.id,
            "name": rec.name,
            "description": rec.short_description,
            "capabilities": list(rec.capabilities),
            "capability_status": capability_status.model_status(rec.capabilities),
        }
        contract = panorama_contract.contract_for(rec.id) if lane == "panorama" else None
        if contract is not None:
            entry["contract"] = panorama_contract.summary(contract)
        out.append(entry)
    if source == "all":
        return {"lane": lane, "source": source, "models": out[:40], **_trained_listing(lane, query)}
    return {"lane": lane, "models": out[:40]}


LORA_ROLES = (
    ("model_input", "lora_models"),
    ("scale_input", "lora_strengths"),
    ("model_id_input", "composition"),
)


def _item_count(raw, *names):
    """A declared array count as an int, or None when it is not a finite whole number >= 0."""
    value = next((raw.get(name) for name in names if raw.get(name) is not None), None)
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    if not math.isfinite(value) or value < 0:
        return None
    return int(value) if value == int(value) else None


_TRAINED_ROUTE = (
    "Call list_models(source=trained) for its routes, then pass the route's base as model_id "
    "with this ID in the route's model_input"
)
TRAINED_TARGET_HELP = {
    catalog.TRAINED_LORA: f"{_TRAINED_ROUTE} and a strength in its scale_input.",
    catalog.TRAINED_COMPOSITION: f"{_TRAINED_ROUTE}, without a strength.",
}


def _described(model_id):
    """generation.ensure_record; a LoRA or composition is refused with its route, finally.

    It has no inputs of its own, so polling for its description would never end.
    """
    try:
        return generation.ensure_record(model_id)
    except generation.WithoutInputs as error:
        help_text = TRAINED_TARGET_HELP.get(error.kind)
        if help_text is None:
            raise
        raise ScenarioError(0, f"{error.reason}. {help_text}") from None


def model_schema(args):
    record = _described(args["model_id"])
    schema = generation.schema_for(record.id)
    inputs = {
        raw.get("name"): raw
        for raw in record.parameters
        if isinstance(raw, dict) and isinstance(raw.get("name"), str)
    }
    try:
        slot, slot_error = LoraSlot.from_record(record.raw), ""
    except ValueError as error:
        slot, slot_error = None, str(error)
    roles = {}
    if slot is not None:
        roles = {
            getattr(slot.component, field): role
            for field, role in LORA_ROLES
            if getattr(slot.component, field)
        }
    params = []
    for spec in schema.specs:
        item = {
            "name": spec.name,
            "type": spec.ptype,
            "label": spec.label,
            "required": spec.required_always,
            "cost_impact": spec.cost_impact,
        }
        if spec.default is not None:
            item["default"] = spec.default
        if spec.allowed_values:
            item["allowed_values"] = list(spec.allowed_values)
        if spec.min is not None or spec.max is not None:
            item["range"] = [spec.min, spec.max]
        if spec.is_file:
            item["file_kind"] = spec.kind or "image"
            item["note"] = (
                "pass a Scenario asset id (capture_reference creates one from the viewport)"
            )
        if spec.description:
            item["description"] = spec.description
        raw = inputs.get(spec.name, {})
        types = raw.get("modelTypes")
        if spec.ptype in ("model", "model_array") and isinstance(types, list):
            item["model_types"] = [value for value in types if isinstance(value, str)]
        if spec.ptype.endswith("_array"):
            for key, names in (
                ("min_items", ("minLength", "min_length", "minItems")),
                ("max_items", ("maxLength", "max_length", "maxItems")),
            ):
                count = _item_count(raw, *names)
                if count is not None:
                    item[key] = count
        if spec.name in roles:
            item["role"] = roles[spec.name]
        params.append(item)
    result = {
        "model_id": record.id,
        "name": record.name,
        "prompt_parameter": schema.prompt_name,
        "parameters": params,
        "lora_component": trained_offers.describe_slot(slot) if slot is not None else None,
    }
    if slot_error:
        result["lora_component_error"] = slot_error
    return result


def _body_for(model_id, parameters, *, lane="image"):
    contract = None
    if lane == "panorama":
        contract = panorama_contract.contract_for(model_id)
        if contract is None:
            raise ValueError("Use a verified panorama model from list_models(lane=panorama)")
    record = _described(model_id)
    schema = generation.schema_for(record.id)
    parameters = dict(parameters or {})
    # A base's LoRA inputs are read strictly and rebuilt by trained_routes.apply,
    # as a form's trained choice is, so equal choices give equal payloads.
    parameters, requested = trained_models.split_parameters(record, parameters)
    if requested is not None and requested.picks and lane not in trained_models.LANES:
        raise ValueError("Trained models run in the image and render_image lanes")
    for spec in schema.specs:
        # A form starts each number field at its declared default and sends it in the
        # field's own type (0.0 in a decimal field, 1 in a whole one); do the same so an
        # unchanged form and this request serialize alike. null still leaves it out.
        # A panorama contract applies its own lane defaults and 2:1 sizes instead;
        # the Environment lane has no native form to match.
        default = spec.default
        if (
            contract is None
            and spec.ptype == "number"
            and not spec.allowed_values
            and spec.name not in parameters
            and isinstance(default, int | float)
            and not isinstance(default, bool)
        ):
            parameters[spec.name] = default
    files = {}
    for spec in schema.specs:
        if spec.is_file and spec.name in parameters:
            value = parameters.pop(spec.name)
            files[spec.name] = list(value) if isinstance(value, list) else [value]
    body = build_body(schema.specs, parameters, files)
    if requested is not None:
        body = trained_models.apply_picks(record, requested, body)
    errors = []
    if contract is not None:
        body, errors = panorama_contract.prepare(
            contract, schema.specs, body, panorama_refs=_saved_panoramas(contract, body)
        )
    errors += [
        error
        for error in validate(schema.specs, body, schema.one_of, schema.exclusive)
        if error not in errors
    ]
    if errors:
        raise ValueError("; ".join(errors))
    return record, body


def _saved_panoramas(contract, body):
    """Saved, downloaded LDR 360 results of the selected scope named by this request.

    The native Environment form applies the same shared check to its saved
    panorama references.
    """
    from ..blender import history

    wanted = set()
    for rule in contract.references:
        if rule.kind == panorama_contract.PANORAMA:
            value = body.get(rule.param)
            values = value if isinstance(value, list) else [value]
            wanted.update(item for item in values if isinstance(item, str))
    if not wanted:
        return {}
    return panorama_contract.saved_panorama_refs(history.saved_records(), wanted)


def _generation_input(args, lane):
    if lane in {"render_image", "render_video"}:
        from ..blender import render_commands

        runtime.sync_catalog_context()
        request = render_commands.request(
            bpy.context.scene, lane, args["model_id"], args.get("parameters")
        )
        return generation.ensure_record(request.model_id), request.body, request
    record, body = _body_for(args["model_id"], args.get("parameters"), lane=lane)
    return record, body, None


def render_form(args):
    from ..blender import render_commands

    return render_commands.execute(bpy.context, args)


DEFAULT_ACTIONS = ("get", "set", "clear")


def model_defaults(args):
    """Read, save or clear per-scope trained-model defaults with the native forms' rules."""
    action = args.get("action") or "get"
    if action not in DEFAULT_ACTIONS:
        raise ValueError(f"action must be one of {DEFAULT_ACTIONS}")
    lane = args.get("lane")
    if lane is not None and lane not in trained_models.LANES:
        raise ValueError("lane must be image or render_image")
    generation.process_catalog_events()  # also selects the current credentials and project
    if not runtime.state.catalog_loaded:
        generation.request_catalog()  # defaults are checked against loaded descriptions
    if action == "get":
        cache = trained_defaults.load()
        names = (lane,) if lane else trained_models.LANES
        return {
            "context_id": cache.context,
            "lanes": [trained_defaults.lane_summary(cache, name, read=True) for name in names],
        }
    if lane is None:
        raise ValueError("lane is required to set or clear a default")
    context_id, revision = args.get("context_id"), args.get("expected_revision")
    if not isinstance(context_id, str) or not context_id:
        raise ValueError("Use the context_id returned by model_defaults(action=get)")
    if type(revision) is not int or revision < 0:
        raise ValueError("expected_revision must be the nonnegative revision get returned")
    if action == "set":
        base_model_id = args.get("base_model_id")
        if not isinstance(base_model_id, str) or not base_model_id:
            raise ValueError("base_model_id is required to set a default")
        picks = trained_offers.picks_from_list(args.get("picks"))
        default = trained_defaults.explicit_default(lane, base_model_id, picks)
        trained_defaults.store(default, context_id, revision)
    else:
        trained_defaults.clear(lane, context_id, revision)
    cache = trained_defaults.load()
    return {"context_id": cache.context, "lanes": [trained_defaults.lane_summary(cache, lane)]}


def estimate_cost(args):
    lane = args.get("lane") or "image"
    if lane not in LANES:
        raise ValueError(f"lane must be one of {LANES}")
    record, body, render_request = _generation_input(args, lane)
    jobs = runtime.ensure_model_jobs()
    ticket = jobs.quote(bpy.context.scene, record.id, body, lane=lane)

    def finish_model(_):
        if runtime.ensure_model_jobs() is not jobs:
            raise ScenarioError(0, "The estimate context changed; estimate again")
        quote = jobs.finish_quote(ticket)
        if render_request is not None:
            try:
                _, current, _ = _generation_input(args, lane)
                if current != body:
                    raise ScenarioError(0, "The render form changed; estimate again")
            except Exception:
                jobs.quotes.pop(ticket.identifier, None)
                raise
        return {
            "model_id": record.id,
            "lane": lane,
            "quote_id": ticket.identifier,
            "cu_cost": float(quote.cost),
            "cu_cost_exact": str(quote.cost),
            "mesh_sources": [asdict(source) for source in ticket.quote.mesh_sources],
            "details": json.loads(quote.response_json).get("costDetails") or {},
        }

    return DeferredTool(ticket.task.result, finish_model)


def _wait_workflow(task):
    try:
        task.result()
    except Exception:
        # Deliver the owned completion on the main thread, including errors.
        pass


def _asset_library(args, *, search=False):
    options = {"public": args.get("public", False)}
    if search:
        options.update(
            query=args["query"], limit=args.get("limit", 40), offset=args.get("offset", 0)
        )
    else:
        options.update(
            page_size=args.get("page_size", 40),
            pagination_token=args.get("pagination_token"),
            collection_id=args.get("collection_id"),
        )
    session = runtime.ensure_job_session()
    task = session.asset_library(bpy.context.scene, **options)

    def finish(_):
        if runtime.ensure_job_session() is not session:
            raise ScenarioError(0, "The asset library context changed; read it again")
        outcomes = session.drain(task=task)
        if not outcomes:
            raise ScenarioError(0, "The asset library is still loading")
        page = session.deliver(outcomes[0], lambda value, *_: value)
        return {**page, "assets": [_asset_summary(row) for row in page["assets"]]}

    return DeferredTool(lambda: _wait_workflow(task), finish)


def list_assets(args):
    return _asset_library(args)


def search_assets(args):
    return _asset_library(args, search=True)


_ORGANIZATION_ARGUMENTS = frozenset(
    {"operation", "asset_ids", "collection_id", "collection_name", "add_tags", "remove_tags"}
)
_ORGANIZATION_CHANGED = (
    "The Scenario connection changed; reviews from another connection or an earlier "
    "session are discarded. Inspect assets with list_assets, then prepare the change again"
)
_DISCARDED_UNKNOWN = (
    "Discarded locally, but its apply outcome is unknown: the change may already be in "
    "Scenario. Tell the user, then inspect the assets' collections and tags with list_assets "
    "before assuming nothing changed; never repeat the change automatically. This review "
    "sends nothing more."
)


def _wait_task(task):
    if task is not None:
        _wait_workflow(task)


def list_collections(args):
    page_size = args.get("page_size", 50)
    if type(page_size) is not int or not 1 <= page_size <= 100:
        raise ValueError("page_size must be an integer from 1 to 100")
    token = args.get("pagination_token")
    if token is not None and (not isinstance(token, str) or not token):
        raise ValueError("pagination_token must be a nonempty string from list_collections")
    session = runtime.ensure_job_session()
    owner = session.asset_organization
    task = owner.collections(page_size=page_size, pagination_token=token)

    def finish(_):
        runtime.sync_catalog_context()
        if runtime.state.job_session is not session or not session.active:
            raise ScenarioError(0, "The Scenario connection changed; list collections again")
        return owner.take_collections(task)

    return DeferredTool(lambda: _wait_task(task), finish)


def _organization_note(status):
    """Guidance for the connected agent; the review state remains authoritative."""
    phase, result = status["phase"], status["result"]
    if phase == "PREPARING":
        return (
            "Reading the current state; nothing is sent while preparing. Call "
            "asset_organization_status with this context_id and review_id until the phase changes."
        )
    if phase == "READY":
        return (
            "Show the user the operation, collection, each asset's change, request_count and "
            "notice. Call apply_asset_organization once, only after explicit approval, before "
            "expires_in seconds pass."
        )
    if phase == "UNCHANGED":
        return "Nothing to change; nothing will be sent for this review."
    if phase in {"REJECTED", "NOT_SENT"}:
        # Prepare and apply both refuse an exact collection name already in use.
        step = "Correct the request" if phase == "REJECTED" else "Resolve the message"
        note = f"Nothing was sent. {step}, then prepare a new review."
        if status["existing_collection_ids"]:
            note += " To use an existing collection, prepare add_to_collection with its ID."
        return note
    if phase == "APPLYING":
        return (
            "Applying. Call asset_organization_status until it finishes; never apply this "
            "review again or repeat the change."
        )
    if phase == "DISCARDED":
        if result is not None:
            return (
                "Discarded locally after it was applied; report its result, which still "
                "describes what Scenario changed. This review sends nothing more."
            )
        return "Discarded locally; this review sends nothing more."
    if phase == "EXPIRED":
        return "Expired without sending; prepare a new review."
    if result is not None and result["state"] == "VERIFIED":
        return "Scenario confirmed every change by reading the assets back."
    if result is not None and result["state"] in {"PARTIAL", "REJECTED"}:
        return (
            "Report each asset outcome. To retry refused assets, prepare a new review; it "
            "reads the current state first and sends only what still differs."
        )
    return (
        "Never repeat unconfirmed work automatically. Inspect the assets with list_assets; "
        "a new review reads the current state first, so prepare one only if the user still "
        "wants the change."
    )


def _organization_payload(context_id, status):
    return {"context_id": context_id, **status, "note": _organization_note(status)}


def _organization_context(args):
    """Return the active session's owner for a context_id issued by an earlier call."""
    context_id, review_id = args.get("context_id"), args.get("review_id")
    if not isinstance(context_id, str) or not context_id:
        raise ValueError("Use the context_id returned by prepare_asset_organization")
    if not isinstance(review_id, str) or not review_id:
        raise ValueError("Use the review_id returned by prepare_asset_organization")
    session = runtime.ensure_job_session()
    if context_id != runtime.state.job_context_id:
        raise ScenarioError(0, _ORGANIZATION_CHANGED)
    return session, session.asset_organization, context_id, review_id


def _organization_finish(session, context_id, review_id, *, applying):
    def finish(_):
        runtime.sync_catalog_context()
        if (
            runtime.state.job_session is not session
            or not session.active
            or runtime.state.job_context_id != context_id
        ):
            if applying:
                raise ScenarioError(
                    0,
                    "The Scenario connection changed while this change was applying; its "
                    "outcome is unknown in this context. Inspect the assets with list_assets "
                    "in the selected connection; nothing is resent automatically",
                )
            raise ScenarioError(0, _ORGANIZATION_CHANGED)
        owner = session.asset_organization
        owner.poll()
        return _organization_payload(context_id, owner.status(review_id))

    return finish


def prepare_asset_organization(args):
    unknown = sorted(set(args) - _ORGANIZATION_ARGUMENTS)
    if unknown:
        raise ValueError(f"Unknown organization argument: {', '.join(unknown)}")
    if not isinstance(args.get("operation"), str):
        raise ValueError("operation is required")
    # JSON null means an omitted list; build_request validates everything else.
    lists = {
        key: () if args.get(key) is None else args[key]
        for key in ("asset_ids", "add_tags", "remove_tags")
    }
    session = runtime.ensure_job_session()
    owner = session.asset_organization
    review_id = owner.prepare(
        args["operation"],
        collection_id=args.get("collection_id"),
        collection_name=args.get("collection_name"),
        **lists,
    )
    task = owner.task(review_id)
    context_id = runtime.state.job_context_id
    return DeferredTool(
        lambda: _wait_task(task),
        _organization_finish(session, context_id, review_id, applying=False),
    )


def apply_asset_organization(args):
    session, owner, context_id, review_id = _organization_context(args)
    owner.apply(review_id)
    task = owner.task(review_id)
    return DeferredTool(
        lambda: _wait_task(task),
        _organization_finish(session, context_id, review_id, applying=True),
    )


def asset_organization_status(args):
    action = args.get("action", "status")
    if action not in {"status", "discard"}:
        raise ValueError("action must be status or discard")
    _, owner, context_id, review_id = _organization_context(args)
    owner.poll()
    if action == "status":
        return _organization_payload(context_id, owner.status(review_id))
    # FINISHED without a result is an unknown apply outcome; discard replaces its
    # message, so warn here, where poll may have just delivered that outcome.
    before = owner.status(review_id)
    payload = _organization_payload(context_id, owner.discard(review_id))
    if before["phase"] == "FINISHED" and before["result"] is None:
        payload["note"] = _DISCARDED_UNKNOWN
    return payload


def _workflow_metadata(args, *, detail=False):
    privacy = args.get("privacy", "private")
    if privacy not in {"private", "public"}:
        raise ValueError("Choose private or public workflows")
    offset, limit = args.get("offset", 0), args.get("limit", 40)
    query = args.get("query", "")
    if (
        type(offset) is not int
        or offset < 0
        or type(limit) is not int
        or not 1 <= limit <= 40
        or not isinstance(query, str)
    ):
        raise ValueError("Use a nonnegative offset, limit from 1 to 40 and text query")
    session = runtime.ensure_job_session()
    task = session.workflow_metadata(
        bpy.context.scene, identifier=args["workflow_id"] if detail else None, privacy=privacy
    )

    def finish(_):
        if runtime.ensure_job_session() is not session:
            raise ScenarioError(0, "The workflow context changed; inspect again")
        outcomes = session.drain(task=task)
        if not outcomes:
            raise ScenarioError(0, "Workflow metadata is still loading")
        result = session.deliver(outcomes[0], lambda value, *_: value)
        if detail:
            fields = result.get("inputs_definition")
            if fields is None:
                fields = result.get("inputs")
            return {
                "workflow_id": result["id"],
                "name": result.get("name", ""),
                "description": result.get("description", ""),
                "inputs": fields,
            }
        rows = [
            {
                "id": row["id"],
                "name": row.get("name", ""),
                "description": row.get("description", ""),
            }
            for row in result
        ]
        rows = [
            row
            for row in rows
            if query.lower() in " ".join(str(value) for value in row.values()).lower()
        ]
        end = offset + limit
        return {
            "privacy": privacy,
            "workflows": rows[offset:end],
            "total": len(rows),
            "next_offset": end if end < len(rows) else None,
        }

    return DeferredTool(lambda: _wait_workflow(task), finish)


def list_workflows(args):
    return _workflow_metadata(args)


def workflow_schema(args):
    return _workflow_metadata(args, detail=True)


def estimate_workflow(args):
    jobs = runtime.ensure_model_jobs()
    ticket = jobs.quote_workflow(bpy.context.scene, args["workflow_id"], args.get("parameters", {}))

    def finish(_):
        if runtime.ensure_model_jobs() is not jobs:
            raise ScenarioError(0, "The workflow context changed; estimate again")
        estimate = jobs.finish_quote(ticket)
        return {
            "workflow_id": ticket.model_id,
            "quote_id": ticket.identifier,
            "parameters": json.loads(ticket.inputs),
            "payload": estimate.payload,
            "cu_cost_exact": str(estimate.cost),
            "mesh_sources": [asdict(source) for source in ticket.quote.mesh_sources],
            **workflow_quote_notice(estimate.loop_steps),
        }

    return DeferredTool(lambda: _wait_workflow(ticket.task), finish)


def run_workflow(args):
    jobs = runtime.ensure_model_jobs()
    view = jobs.submit_workflow(
        args["quote_id"],
        bpy.context.scene,
        args["workflow_id"],
        args.get("parameters", {}),
        approved_cost=args["approved_cost"],
    )
    return {
        "local_id": view.local_id,
        "state": view.status,
        # A missing count is unknown coverage, never proof that there is no loop.
        **workflow_quote_notice(view.meta.get("workflow_loop_steps")),
        "note": "One workflow submission saved. Inspect job_status; never repeat uncertain work. "
        "Results require explicit application. A running workflow cannot be canceled. When "
        "job_status reports awaiting_decision, show workflow_job_steps to the user and send only "
        "their explicit choice with decide_workflow_step; reject stops the workflow. job_status "
        "cu_cost_exact stays the approved quote, not the final charge.",
    }


def discard_workflow_estimate(args):
    runtime.ensure_model_jobs().discard_workflow_quote(args["quote_id"])
    return {"discarded": True}


# What a caller should do after each decision outcome; ModelJobs.decision_outcome.
_DECISION_NOTES = {
    "applied": "Scenario accepted the decision. The job continues, or stops after reject; "
    "follow it with job_status or wait_for_job.",
    "refused": "Scenario refused the decision, so it was not applied. Read decision_error and "
    "the steps below; any new decision needs the user's explicit choice again.",
    "uncertain": "The decision may have been applied. Never repeat it: call workflow_job_steps "
    "and decide again only if the step is shown waiting again and the user chooses again.",
    "not_sent": "No decision was sent to Scenario: a check failed first or the job had already "
    "finished. Read state, decision_error and the steps below; any new decision needs the "
    "user's explicit choice again.",
}


def _step_reference(args):
    context_id, request_id = args.get("context_id"), args.get("request_id")
    if (
        not isinstance(context_id, str)
        or not context_id
        or not isinstance(request_id, str)
        or not request_id
    ):
        raise ValueError("Use the context_id and request_id returned by list_local_jobs")
    return context_id, request_id


def _workflow_steps(jobs, request_id):
    """One saved workflow job's waiting steps from the shared projection, never with URLs."""
    status = jobs.status(request_id)
    record = jobs.store.get(request_id)
    if status is None or record is None or status["local_id"] != request_id:
        raise ScenarioError(0, "The saved workflow job changed; inspect list_local_jobs again")
    return {
        "context_id": runtime.state.job_context_id,
        "request_id": request_id,
        "workflow_id": record.intent.target_id,
        "revision": status["revision"],
        "state": status["status"],
        "remote_status": status["remote_status"],
        "cu_cost_exact": status["cu_cost_exact"],
        "awaiting_decision": status["awaiting_decision"],
        "steps": [
            {**step, "blocked_reason": BLOCKED.get(step["blocked"])}
            for step in status["pending_steps"]
        ],
        "decision_error": status["decision_error"],
        "delivery_paused": status["delivery_paused"],
        "error": status["error"],
    }


def _check_step_context(jobs, message):
    runtime.sync_catalog_context()
    if runtime.state.model_jobs is not jobs or not jobs.session.active:
        raise ScenarioError(0, message)


def workflow_job_steps(args):
    context_id, request_id = _step_reference(args)
    jobs, task = runtime.read_workflow_steps(context_id, request_id)
    if task is None:
        return _workflow_steps(jobs, request_id)

    def finish(_):
        _check_step_context(jobs, "The job context changed while reading steps; inspect it again")
        return _workflow_steps(jobs, request_id)

    return DeferredTool(lambda: _wait_workflow(task), finish)


def decide_workflow_step(args):
    context_id, request_id = _step_reference(args)
    revision = args.get("expected_revision")
    if type(revision) is not int or revision < 0:
        raise ValueError("expected_revision must be a nonnegative integer")
    node_id, review_id = args.get("node_id"), args.get("review_id")
    if not all(isinstance(value, str) and value for value in (node_id, review_id)):
        raise ValueError("Use the node_id and review_id returned by workflow_job_steps")
    action = args.get("action")
    if action not in ("approve", "select", "reject"):
        raise ValueError("action must be approve, select or reject")
    jobs, task = runtime.decide_workflow_step(
        context_id,
        request_id,
        revision,
        node_id,
        review_id,
        action,
        args.get("selected_indices"),
    )

    def finish(_):
        outcome = jobs.decision_outcome(task)
        _check_step_context(
            jobs,
            f"The job context changed during the decision (outcome: {outcome}); never repeat "
            "it, inspect the job again in its connection",
        )
        return {
            "outcome": outcome,
            **_workflow_steps(jobs, request_id),
            "note": _DECISION_NOTES[outcome],
        }

    return DeferredTool(lambda: _wait_workflow(task), finish)


def film_recipe(args):
    from ..blender import film_jobs
    from ..core.scene.film_finish import recipe_warnings

    action = args.get("action", "inspect")
    scene = bpy.context.scene
    if action == "inspect":
        owner = runtime.ensure_film_jobs()
        inspection = owner.inspect(scene)
        raw, _ = film_jobs.recipe(scene)
        return {
            "context_id": runtime.state.job_context_id,
            **inspection,
            "warnings": recipe_warnings(raw),
        }
    # Validation refuses before any change; a reload of the same recipe changes nothing.
    with file_state.marking("Scenario Film recipe", lambda: _film_fields(scene)):
        if action == "load":
            raw = args["recipe"]
            plan = film_jobs.load_recipe(scene, raw)
        elif action == "new_production":
            import uuid

            raw, plan = film_jobs.recipe(scene)
            scene.scenario_film.production_id = uuid.uuid4().hex
        else:
            raise ValueError("Choose inspect, load or new_production")
    return {
        "production_id": scene.scenario_film.production_id,
        "title": plan["title"],
        "tasks": plan["tasks"],
        "shots": [{"shot_id": shot["id"], "title": shot["title"]} for shot in plan["shots"]],
        "warnings": recipe_warnings(raw),
    }


def _film_fields(scene):
    """The recipe fields film_recipe can change, for its file-modified mark."""
    film = scene.scenario_film
    return (
        film.recipe_json,
        film.production_id,
        film.title,
        film.shot_index,
        film.task_index,
        tuple((row.name, row.title) for row in film.shots),
        tuple((row.name, row.title, row.kind, row.model_id) for row in film.tasks),
    )


def _built(build, message):
    """Run one Film build and mark the file modified when it added or left data.

    The native build operators declare Undo; agents' builds are marked here. A
    refused or rolled-back build, or an uncertain one that built nothing, leaves
    the file unmarked.
    """
    from ..blender import film_scene

    with file_state.marking(message, film_scene._snapshot):
        return build()


def _film_owner(args):
    owner = runtime.ensure_film_jobs()
    if args["production_id"] != bpy.context.scene.scenario_film.production_id:
        raise ScenarioError(0, "The Film production changed; inspect the recipe again")
    return owner


def estimate_film_task(args):
    owner = _film_owner(args)
    scene = bpy.context.scene
    item = owner.quote(scene, args["task_id"])

    def finish(_):
        if runtime.ensure_film_jobs() is not owner or bpy.context.scene != scene:
            raise ScenarioError(
                0, "The Film context changed; return to its scene and inspect the recipe"
            )
        owner.finish(item, scene)
        return owner.quote_details(item, scene)

    return DeferredTool(item.task.result, finish)


def approve_film_task(args):
    owner = runtime.ensure_film_jobs()
    view = owner.approve(args["quote_id"], bpy.context.scene, approved_cost=args["approved_cost"])
    return {
        "request_id": view.local_id,
        "local_id": view.local_id,
        "state": view.status,
        "note": "One submission saved. Use job_status and explicit recovery/application; never repeat uncertain work.",
    }


def discard_film_estimate(args):
    runtime.ensure_film_jobs().discard(args["quote_id"], bpy.context.scene)
    return {"discarded": True}


def bind_film_upload(args):
    owner = _film_owner(args)
    if args["context_id"] != runtime.state.job_context_id:
        raise ScenarioError(0, "The upload context changed; inspect uploads again")
    scene = bpy.context.scene
    item = owner.bind_upload(
        scene,
        args["task_id"],
        request_id=args["request_id"],
        expected_revision=args["expected_revision"],
    )

    def finish(_):
        if runtime.ensure_film_jobs() is not owner or bpy.context.scene != scene:
            raise ScenarioError(0, "The Film context changed; inspect saved associations")
        owner.finish(item, scene)
        return {
            "production_id": item.binding[0],
            "task_id": item.task_id,
            "upload_request_id": item.request_id,
            "state": "bound",
        }

    return DeferredTool(item.task.result, finish)


def film_shot_sources(args):
    owner = _film_owner(args)
    return {
        "context_id": runtime.state.job_context_id,
        "production_id": args["production_id"],
        "shot_id": args["shot_id"],
        "heroes": owner.session.film_shots.inspect(bpy.context.scene, shot_id=args["shot_id"]),
    }


def prepare_film_shot(args):
    owner = _film_owner(args)
    if args["context_id"] != runtime.state.job_context_id:
        raise ScenarioError(0, "The Film connection changed; inspect the shot's sources again")
    return owner.session.film_shots.prepare(
        bpy.context.scene, shot_id=args["shot_id"], selections=args["selections"]
    )


def film_shot_review(args):
    commands = runtime.ensure_film_jobs().session.film_shots
    action = args.get("action", "status")
    if action == "status":
        commands.poll()
        return commands.status(args["review_id"])
    if action == "discard":
        return commands.discard(args["review_id"])
    if action == "retry_receipts":
        return commands.retry_receipts(args["review_id"])
    if action == "dismiss_uncertain":
        return commands.dismiss_uncertain(args["review_id"], inspected=args.get("inspected"))
    raise ValueError("Choose status, discard, retry_receipts or dismiss_uncertain")


def build_film_shot(args):
    commands = runtime.ensure_film_jobs().session.film_shots
    return _built(lambda: commands.approve(args["review_id"]), "Scenario Film shot")


def film_timeline_sources(args):
    owner = _film_owner(args)
    return {
        "context_id": runtime.state.job_context_id,
        **owner.session.film_timeline.inspect(bpy.context.scene),
    }


def prepare_film_timeline(args):
    owner = _film_owner(args)
    if args["context_id"] != runtime.state.job_context_id:
        raise ScenarioError(0, "The Film connection changed; inspect timeline sources again")
    return owner.session.film_timeline.prepare(bpy.context.scene, selections=args["selections"])


def film_timeline_review(args):
    commands = runtime.ensure_film_jobs().session.film_timeline
    action = args.get("action", "status")
    if action == "status":
        return commands.status(args["review_id"])
    if action == "discard":
        return commands.discard(args["review_id"], inspected=args.get("inspected", False))
    raise ValueError("Choose status or discard")


def build_film_timeline(args):
    commands = runtime.ensure_film_jobs().session.film_timeline
    return _built(lambda: commands.approve(args["review_id"]), "Scenario Film timeline")


def prepare_film_composition(args):
    owner = _film_owner(args).compositions
    if args["context_id"] != runtime.state.job_context_id:
        raise ValueError("The composition connection changed; inspect the recipe again")
    return owner.prepare(
        bpy.context.scene,
        mode=args.get("mode", "final"),
        score_task_id=args.get("score_task_id", "score"),
    )


def film_composition_review(args):
    owner = runtime.ensure_film_jobs().compositions
    action = args.get("action", "status")
    if action == "cancel":
        return owner.cancel(args["review_id"])
    if action == "status":
        owner.poll()
        return owner.status(args["review_id"])
    if action == "discard":
        owner.poll()
        return owner.discard(args["review_id"])
    raise ValueError("Choose status, cancel or discard")


def estimate_film_composition(args):
    owner = runtime.ensure_film_jobs().compositions
    scene = bpy.context.scene
    identifier = args["review_id"]
    owner.estimate(identifier)
    task = owner._get(identifier).task

    def finish(_):
        if runtime.ensure_film_jobs().compositions is not owner or bpy.context.scene != scene:
            raise ScenarioError(0, "The Film context changed; inspect the composition review")
        owner.poll()
        status = owner.status(identifier)
        if status["phase"] != "QUOTED":
            raise ScenarioError(0, status["error"] or "The composition price is unavailable")
        return status

    return DeferredTool(task.result, finish)


def generate_film_composition(args):
    return runtime.ensure_film_jobs().compositions.approve(
        args["review_id"], approved_cost=args["approved_cost"]
    )


def prepare_film_review(args):
    owner = _film_owner(args).session.film_review
    if args["context_id"] != runtime.state.job_context_id:
        raise ValueError("The review connection changed; inspect the recipe again")
    return owner.prepare(
        bpy.context.scene,
        mode=args.get("mode", "final"),
        score_task_id=args.get("score_task_id", "score"),
        include_master=args.get("include_master", False),
    )


def film_review_status(args):
    owner = runtime.ensure_film_jobs().session.film_review
    action = args.get("action", "status")
    if action == "cancel":
        # Like the native button, cancel before maintenance can deliver a finished preparation.
        return owner.cancel(args["review_id"])
    owner.poll()
    if action == "status":
        return owner.status(args["review_id"])
    if action == "discard":
        return owner.discard(args["review_id"])
    if action == "retry_receipt":
        return owner.retry_receipt(args["review_id"])
    if action == "dismiss_uncertain":
        return owner.dismiss_uncertain(args["review_id"], inspected=args.get("inspected"))
    raise ValueError("Choose status, cancel, discard, retry_receipt or dismiss_uncertain")


def build_film_review(args):
    commands = runtime.ensure_film_jobs().session.film_review
    return _built(lambda: commands.approve(args["review_id"]), "Scenario Film review")


def _output_folder():
    """Agents may export only inside the configured Output Folder; see film_export."""
    return Path(runtime.paths().output_dir)


def film_export_sources(args):
    owner = _film_owner(args).session.film_export
    return {
        "context_id": runtime.state.job_context_id,
        "default_directory": str(_output_folder() / "Film"),
        **owner.inspect(bpy.context.scene),
    }


def prepare_film_export(args):
    owner = _film_owner(args).session.film_export
    if args["context_id"] != runtime.state.job_context_id:
        raise ValueError("The export connection changed; inspect export sources again")
    return owner.prepare(
        bpy.context.scene,
        source_id=args["source_id"],
        destination=args["destination_path"],
        width=args.get("width"),
        height=args.get("height"),
        audio=args.get("include_audio"),
        within=_output_folder(),
    )


def render_film_export(args):
    # Agent approvals recheck the Output Folder, even for a native file choice.
    return runtime.ensure_film_jobs().session.film_export.approve(
        args["review_id"],
        approved_destination=args["approved_destination"],
        within=_output_folder(),
    )


def film_export_review(args):
    owner = runtime.ensure_film_jobs().session.film_export
    action = args.get("action", "status")
    if action == "cancel":
        return owner.cancel(args["review_id"])
    owner.poll()
    if action == "status":
        return owner.status(args["review_id"])
    if action == "discard":
        return owner.discard(args["review_id"])
    if action == "prepare_republish":
        return owner.prepare_republish(
            args["review_id"], args.get("destination_path"), within=_output_folder()
        )
    if action == "republish":
        return owner.republish(
            args["review_id"],
            approved_destination=args.get("approved_destination"),
            within=_output_folder(),
        )
    raise ValueError("Choose status, cancel, discard, prepare_republish or republish")


def film_case_study_sources(args):
    owner = _film_owner(args).session.film_case_study
    return {
        "context_id": runtime.state.job_context_id,
        "default_directory": str(_output_folder() / "Film"),
        **owner.inspect(bpy.context.scene),
    }


def prepare_film_case_study(args):
    owner = _film_owner(args).session.film_case_study
    if args["context_id"] != runtime.state.job_context_id:
        raise ValueError("The case-study connection changed; inspect case-study sources again")
    return owner.prepare(
        bpy.context.scene,
        source_id=args["source_id"],
        timeline_id=args.get("timeline_id"),
        destination=args["destination_path"],
        within=_output_folder(),
    )


def export_film_case_study(args):
    # Agent approvals recheck the Output Folder, even for a native file choice.
    return runtime.ensure_film_jobs().session.film_case_study.approve(
        args["review_id"],
        approved_destination=args["approved_destination"],
        within=_output_folder(),
    )


def film_case_study_review(args):
    owner = runtime.ensure_film_jobs().session.film_case_study
    action = args.get("action", "status")
    if action == "cancel":
        return owner.cancel(args["review_id"])
    owner.poll()
    if action == "status":
        return owner.status(args["review_id"])
    if action == "discard":
        return owner.discard(args["review_id"])
    if action == "prepare_republish":
        return owner.prepare_republish(
            args["review_id"], args.get("destination_path"), within=_output_folder()
        )
    if action == "republish":
        return owner.republish(
            args["review_id"],
            approved_destination=args.get("approved_destination"),
            within=_output_folder(),
        )
    raise ValueError("Choose status, cancel, discard, prepare_republish or republish")


def film_capture_sources(args):
    owner = _film_owner(args).session.film_capture
    return {
        "context_id": runtime.state.job_context_id,
        **owner.inspect(bpy.context.scene, shot_id=args["shot_id"]),
    }


def prepare_film_capture(args):
    owner = _film_owner(args).session.film_capture
    if args["context_id"] != runtime.state.job_context_id:
        raise ValueError("The capture connection changed; inspect sources again")
    return owner.prepare(
        bpy.context.scene,
        shot_id=args["shot_id"],
        source_id=args["source_id"],
        kind=args.get("kind", "VIDEO"),
        width=args.get("width", 1280),
        height=args.get("height", 720),
        color_type=args.get("color_type", "MATERIAL"),
    )


def render_film_capture(args):
    return runtime.ensure_film_jobs().session.film_capture.approve(args["review_id"])


def film_capture_review(args):
    owner = runtime.ensure_film_jobs().session.film_capture
    action = args.get("action", "status")
    if action == "status":
        owner.poll()
        return owner.status(args["review_id"])
    if action == "cancel":
        return owner.cancel(args["review_id"])
    if action == "discard":
        return owner.discard(args["review_id"])
    raise ValueError("Choose status, cancel or discard")


def upload_film_capture(args):
    owner = runtime.ensure_film_jobs().session.film_capture
    return {
        "context_id": runtime.state.job_context_id,
        **owner.upload(args["review_id"], runtime.ensure_reference_uploads()),
    }


def estimate_prompt(args):
    """Quote the current native prompt field through the shared prompt facade."""
    lane = args.get("lane", "image")
    if lane in RENDER_LANES:
        # A render look is prepared from the form model's inputs; load them first.
        generation.await_description(bpy.context.scene.scenario.lane_state(lane).model_id)
    jobs = runtime.ensure_prompt_jobs()
    item = jobs.quote(bpy.context.scene, args.get("lane", "image"), args["action"])

    def finish(_):
        if runtime.ensure_prompt_jobs() is not jobs:
            raise ScenarioError(0, "The prompt context changed")
        jobs.poll()
        if item.phase != "READY":
            raise ScenarioError(0, item.error or "The prompt price is unavailable")
        return {
            "quote_id": item.identifier,
            "action": item.action,
            "lane": item.lane,
            "cu_cost_exact": item.cost,
        }

    return DeferredTool(item.task.result, finish)


def approve_prompt(args):
    jobs = runtime.ensure_prompt_jobs()
    item = jobs.approve(args["quote_id"], bpy.context.scene, approved_cost=args["approved_cost"])
    return {
        "request_id": item.request_id,
        "state": item.phase.lower(),
        "note": "One prompt submission queued; do not repeat it. The unchanged original field receives the result.",
    }


def read_prompt_result(args):
    session = runtime.ensure_job_session()
    if args.get("context_id") != runtime.state.job_context_id:
        raise ScenarioError(0, "The saved-job context changed; list local jobs again")
    task = session.read_prompt_results(
        args["request_id"], expected_revision=args["expected_revision"]
    )

    def finish(_):
        if runtime.ensure_job_session() is not session:
            raise ScenarioError(0, "The saved-job context changed")
        outcomes = session.drain(task=task)
        if not outcomes:
            raise ScenarioError(0, "The prompt result completion is unavailable")
        if outcomes[0].error is not None:
            raise outcomes[0].error
        result = outcomes[0].result
        # Read-only recovery can inspect an old origin. It never applies to the
        # current field or turns a restart into new spending authorization.
        return {"request_id": result.record.intent.request_id, "prompts": list(result.prompts)}

    return DeferredTool(task.result, finish)


def estimate_blockout(args):
    jobs = runtime.ensure_blockout_jobs()
    item = jobs.quote(bpy.context.scene, args.get("action", "DESIGN"))

    def finish(_):
        if runtime.ensure_blockout_jobs() is not jobs:
            raise ScenarioError(0, "The Blockout context changed")
        jobs.poll()
        if item.phase != "READY":
            raise ScenarioError(0, item.error or "The Blockout price is unavailable")
        return {"quote_id": item.identifier, "action": item.action, "cu_cost_exact": item.cost}

    return DeferredTool(item.task.result, finish)


def approve_blockout(args):
    item = runtime.ensure_blockout_jobs().approve(
        args["quote_id"], bpy.context.scene, approved_cost=args["approved_cost"]
    )
    return {"request_id": item.request_id, "state": item.phase.lower()}


def prepare_blockout_plan(args):
    recovery = runtime.blockout_recovery(args["context_id"])
    review = recovery.prepare(args["request_id"], args["expected_revision"], bpy.context.scene)
    return recovery.status(review.identifier)


def blockout_plan_status(args):
    recovery = runtime.blockout_recovery(args["context_id"])
    recovery.poll()
    if args.get("discard", False):
        review = recovery.reviews.get(args["review_id"])
        if review is not None and review.task is not None:
            raise ScenarioError(0, "Wait for the saved-plan read before discarding its review")
        recovery.discard(args["review_id"])
        return {"state": "discarded"}
    return recovery.status(args["review_id"])


def apply_blockout_plan(args):
    result = runtime.blockout_recovery(args["context_id"]).apply(args["review_id"])
    # The native Use saved plan operator declares Undo; an agent's approval is marked here.
    file_state.mark_modified("Scenario Blockout plan")
    return result


def read_model_text(args):
    session = runtime.ensure_job_session()
    if args.get("context_id") != runtime.state.job_context_id:
        raise ScenarioError(0, "The saved-job context changed; list local jobs again")
    task = session.read_model_text(
        args["request_id"], expected_revision=args["expected_revision"], asset_id=args["asset_id"]
    )

    def finish(_):
        if runtime.ensure_job_session() is not session:
            raise ScenarioError(0, "The text result context changed")
        outcomes = session.drain(task=task)
        if not outcomes:
            raise ScenarioError(0, "The text result completion is unavailable")
        if outcomes[0].error is not None:
            raise outcomes[0].error
        result = outcomes[0].result
        return {
            "request_id": result.record.intent.request_id,
            "asset_id": result.asset_id,
            "text": result.text,
        }

    return DeferredTool(task.result, finish)


PREVIEW_ACTIONS = ("inspect", "retry", "cancel")
PREVIEW_WAIT_SECONDS = 60
PREVIEW_BINS = (8, 256, 64)  # Minimum, maximum and default envelope levels returned.


def _preview_rendition(kind, rendition, status, bins):
    """One rendition's state and cached metadata; never a path, URL or image bytes."""
    value = {"state": status.state.value}
    if status.reason:
        value["reason"] = status.reason
    preview = status.preview if status.state == PreviewState.READY else None
    if rendition == ENVELOPE:
        envelope = None if preview is None else preview.envelope
        if envelope is not None:
            rms, peaks = audio_waveform.fold(envelope, bins)
            value.update(
                seconds=envelope.seconds,
                sample_rate=envelope.sample_rate,
                channels=envelope.channels,
                silent=envelope.silent,
                overall_rms=envelope.overall_rms,
                overall_peak=envelope.overall_peak,
                bins=len(rms),
                rms=list(rms),
                peaks=list(peaks),
            )
        return value
    if supports(kind, rendition):
        # A result without a preview kind is never decoded or requested from Scenario.
        value["source"] = "local" if is_local(kind, rendition) else "scenario"
    if preview is not None:
        value.update(
            media_type=preview.media_type,
            width=preview.width,
            height=preview.height,
            size=preview.size,
        )
    return value


def _preview_args(args):
    context_id, request_id = args.get("context_id"), args.get("request_id")
    revision, asset_id = args.get("expected_revision"), args.get("asset_id")
    action, envelope = args.get("action", "inspect"), args.get("envelope", False)
    bins, wait = args.get("bins", PREVIEW_BINS[2]), args.get("wait", 0)
    if not isinstance(context_id, str) or not context_id or not isinstance(request_id, str):
        raise ValueError("Use the context_id and request_id returned by list_local_jobs")
    if type(revision) is not int or revision < 0:
        raise ValueError("expected_revision must be a nonnegative integer")
    if asset_id is not None and (not isinstance(asset_id, str) or not asset_id):
        raise ValueError("asset_id must be a saved result's asset ID from job_status")
    if action not in PREVIEW_ACTIONS:
        raise ValueError(f"action must be one of {PREVIEW_ACTIONS}")
    if type(envelope) is not bool:
        raise ValueError("envelope must be a boolean")
    if type(bins) is not int or not PREVIEW_BINS[0] <= bins <= PREVIEW_BINS[1]:
        raise ValueError("bins must be an integer from 8 to 256")
    if (
        type(wait) not in (int, float)
        or not math.isfinite(wait)
        or not 0 <= wait <= PREVIEW_WAIT_SECONDS
    ):
        raise ValueError("wait must be a finite number of seconds from 0 to 60")
    if asset_id is None and (action != "inspect" or envelope):
        raise ValueError("Choose one asset_id for an envelope, retry or cancel")
    return context_id, request_id, revision, asset_id, action, envelope, bins, wait


def inspect_result_previews(args):
    from ..blender.job_session import OriginUnavailable

    context_id, request_id, revision, asset_id, action, envelope, bins, wait = _preview_args(args)
    session = runtime.ensure_job_session()
    if context_id != runtime.state.job_context_id:
        raise ScenarioError(0, "The saved-job context changed; list local jobs again")
    record = runtime.state.job_store.get(request_id)
    if record is None or record.revision != revision:
        raise ScenarioError(0, "The saved job changed; list local jobs again")
    if record.state not in PREVIEW_STATES:
        raise ScenarioError(0, "Download the saved result before previewing it")
    positions = {item.asset.asset_id: number for number, item in enumerate(record.results, 1)}
    if asset_id is None:
        # Every result the native preview tiles page through, in their order; an
        # agent needs no pager.
        chosen = [target.number for target in preview_tiles.tile_targets(record)]
    else:
        if asset_id not in positions:
            raise ScenarioError(0, "Choose a saved result asset from this job")
        chosen = [positions[asset_id]]
        item = record.results[chosen[0] - 1]
        if item.receipt is None:
            raise ScenarioError(0, "Choose a downloaded saved result of this job")
        if envelope and preview_kind(item.asset.media_type) != "audio":
            raise ScenarioError(0, "Choose a downloaded saved audio result for its envelope")
    asset_ids = [record.results[number - 1].asset.asset_id for number in chosen]
    try:
        scheduler = session.result_previews
    except OriginUnavailable:
        raise ScenarioError(0, "Result previews are unavailable for this connection") from None
    canceled = None
    try:
        if action == "cancel":
            canceled = scheduler.cancel(request_id, asset_id)
        elif asset_ids:
            # Stills as the native tiles request them; the envelope as Waveform does.
            scheduler.request(
                request_id,
                asset_ids=asset_ids,
                renditions=(STILL, ENVELOPE) if envelope else (STILL,),
            )
            if action == "retry":
                scheduler.retry(request_id, asset_id)
                preview_tiles.controller.forget_failures(request_id, asset_id)
    except (PreviewError, ResultError) as error:
        raise ScenarioError(0, str(error)) from None
    if action == "cancel" and canceled is None:
        raise ScenarioError(0, "No preview of this saved result is being prepared")

    def respond():
        results = []
        for number in chosen:
            item = record.results[number - 1]
            kind = preview_kind(item.asset.media_type)
            status = scheduler.status(request_id, item.asset.asset_id)
            renditions = {}
            for rendition, current in () if status is None else status.renditions:
                renditions[rendition] = _preview_rendition(kind, rendition, current, bins)
            results.append(
                {
                    "asset_id": item.asset.asset_id,
                    "number": number,
                    "caption": preview_tiles.caption(item, number),
                    "media_type": item.asset.media_type,
                    "kind": kind,
                    "renditions": renditions,
                }
            )
        return {
            "context_id": context_id,
            "request_id": request_id,
            "revision": record.revision,
            "in_progress": not all(scheduler.quiet(request_id, value) for value in asset_ids),
            "results": results,
        }

    # Wait for what was asked: the envelope while the still may keep polling
    # Scenario for minutes, otherwise the stills.
    watched = (ENVELOPE,) if envelope else (STILL,)
    if not wait or all(scheduler.quiet(request_id, value, watched) for value in asset_ids):
        return respond()
    try:
        watch = scheduler.watch(request_id, asset_ids, watched)
    except PreviewError as error:
        raise ScenarioError(0, str(error)) from None
    server = runtime.state.mcp
    deadline = time.monotonic() + wait

    def run():
        # The HTTP thread only waits; the main thread pumps the scheduler.
        try:
            while not watch.wait(min(0.1, max(0.0, deadline - time.monotonic()))):
                if (
                    time.monotonic() >= deadline
                    or not session.active
                    or (server is not None and not server.running)
                ):
                    return
        finally:
            # finish may never run, for example once the server stopped; the
            # scheduler then forgets the watch on its next pump.
            watch.close()

    def finish(_):
        scheduler.unwatch(watch)
        if (
            runtime.state.job_session is not session
            or not session.active
            or runtime.state.job_context_id != context_id
        ):
            raise ScenarioError(0, "The saved-job context changed while waiting; inspect it again")
        store = runtime.state.job_store
        current = None if store is None else store.get(request_id)
        if current is None or current.revision != record.revision:
            # The response reports this revision's results; never a stale one.
            raise ScenarioError(0, "The saved job changed while waiting; list local jobs again")
        return respond()

    return DeferredTool(run, finish)


def list_local_jobs(args):
    context_id, items = runtime.local_job_recovery()
    return {
        "context_id": context_id,
        "jobs": [
            {
                "request_id": item.record.intent.request_id,
                "revision": item.record.revision,
                "state": item.record.state.value,
                "action": item.action.value,
                "operation": item.record.intent.operation,
                "target_id": item.record.intent.target_id,
                "cu_cost_exact": item.record.intent.quote_cost,
                "source": getattr(item.record.intent, "source", "generation"),
                "remote_job_id": item.record.remote_job_id,
            }
            for item in items
        ],
    }


def cancel_prepared_job(args):
    context_id, request_id = args.get("context_id"), args.get("request_id")
    revision = args.get("expected_revision")
    if not isinstance(context_id, str) or not context_id or not isinstance(request_id, str):
        raise ValueError("Use the context_id and request_id returned by list_local_jobs")
    if type(revision) is not int or revision < 0:
        raise ValueError("expected_revision must be a nonnegative integer")
    record = runtime.cancel_prepared_job(context_id, request_id, revision)
    return {"request_id": request_id, "revision": record.revision, "state": record.state.value}


def generate(args):
    import os

    if os.environ.get("SCENARIO_GUI_PROBE") == "1":
        raise PermissionError("Generation is disabled while an automated GUI probe runs")
    lane = args.get("lane") or "image"
    if lane not in LANES:
        raise ValueError(f"lane must be one of {LANES}")
    ticket = runtime.ensure_model_jobs().require_quote(args.get("quote_id"))
    if ticket.lane != lane:
        raise ScenarioError(0, "The generation lane changed; estimate again")
    record, body, render_request = _generation_input(args, lane)
    meta = {
        "prompt": str(body.get("prompt") or ""),
        "model_name": record.name,
        "source": "mcp",
        "target_objects": [o.name for o in bpy.context.selected_objects if o.type == "MESH"],
    }
    if render_request is not None:
        meta.update(generation.request_meta(bpy.context, lane, render_request))
        meta["source"] = "mcp"
    elif trained := trained_models.payload_meta(record, body):
        # Job views name the LoRAs or composition with the base, as for a form's choice.
        meta["model_name"] = trained_offers.selection_label(
            record.name, [item["name"] for item in trained]
        )
        meta["trained_models"] = trained
    jobs = runtime.ensure_model_jobs()
    rec = jobs.submit(
        args.get("quote_id"),
        bpy.context.scene,
        record.id,
        body,
        lane=lane,
        approved_cost=args.get("approved_cost"),
        meta=meta,
    )
    runtime.state.jobs_view.insert(0, rec)
    return {
        "local_id": rec.local_id,
        "status": rec.status,
        "lane": lane,
        "model_id": record.id,
        "note": "Submission is saved. Poll job_status for remote progress and verified downloads. "
        + (
            "Image jobs import supported images only into their unchanged original scene. "
            if lane == "image"
            else "Panorama results stay saved; change a scene World only through "
            "prepare_result_application(purpose=world) and explicit approval. "
            if lane == "panorama"
            else "Results remain saved for explicit application; this lane does not import automatically. "
        )
        + "Paused or restarted jobs require explicit recovery; never repeat an uncertain request.",
    }


def _job_ref(args):
    """Prefer the platform spelling while retaining the original local alias."""
    ref = args.get("job_id") or args.get("id")
    if not isinstance(ref, str) or not ref.strip():
        raise ValueError(
            "Provide job_id (or id): the local_id returned by generate or a Scenario job id"
        )
    return ref


def _local_registry():
    manager = runtime.state.manager
    return (
        manager.registry
        if manager is not None
        else runtime.JobRegistry(runtime.paths().registry_file).load()
    )


def _find(local_or_job_id):
    for rec in _local_registry().all():
        if rec.local_id == local_or_job_id or rec.job_id == local_or_job_id:
            return rec
    raise ValueError(f"Unknown job {local_or_job_id}")


def _status(rec):
    return {
        "local_id": rec.local_id,
        "job_id": rec.job_id,
        "status": rec.status,
        "progress": rec.progress,
        "cu_cost": rec.cu_cost,
        "files": list(rec.files),
        "error": rec.error,
        "kind": rec.kind,
    }


def _saved_status(reference):
    from ..blender import history

    if runtime.credentials().valid:
        matches = history.saved_matches(reference)
        if len(matches) > 1:
            raise ValueError("Several saved jobs match; use a request_id from list_local_jobs")
        if matches:
            status = runtime.ensure_model_jobs().status(matches[0].intent.request_id)
            if status is None:
                raise ValueError("The saved job changed; inspect list_local_jobs again")
            return status
    # Cold local imports must not require credentials or resume a job engine.
    if any(reference in (record.local_id, record.job_id) for record in _local_registry().all()):
        return None
    return runtime.ensure_model_jobs().status(reference)


def job_status(args):
    reference = _job_ref(args)
    saved = _saved_status(reference)
    return saved if saved is not None else _status(_find(reference))


def recover_local_job(args):
    from ..blender.model_jobs import WorkflowCancelUnavailable
    from ..core.ui.saved_job_actions import NATIVE_ONLY

    if args.get("action") in NATIVE_ONLY:
        raise ScenarioError(
            0,
            "Only the user can mark an unconfirmed request reviewed, in Blender's Jobs list. "
            "Nothing was changed",
        )
    try:
        jobs, task = runtime.control_model_job(
            args["context_id"], args["request_id"], args["expected_revision"], args["action"]
        )
    except WorkflowCancelUnavailable as error:
        raise ScenarioError(
            0,
            f"{error.reason}. Nothing was sent. When job_status reports awaiting_decision, show "
            "workflow_job_steps to the user and, only with their explicit approval to stop the "
            "workflow, send decide_workflow_step with action reject",
        ) from None
    if task is None:
        return jobs.status(args["request_id"])
    return _finish_recovery(jobs, task, args["request_id"])


# Format limits named in a model import approval.
_MODEL_NOTES = {
    "glb": "Rigs and node animation clips are retained using scene FPS; external-file GLBs are unsupported.",
    "gltf": "External buffers and images bind only to this job's saved files listed in resources; "
    "any other reference stops the import. Rigs and node animation clips use scene FPS.",
    "fbx": "Embedded textures are packed; an FBX that references external texture files is rejected. "
    "Animation clips keep the file's frame rate; model_import.frame_rate_mismatch reports a scene FPS difference. "
    "Requires Blender's bundled FBX add-on and a window context.",
    "obj": "MTL maps bind only to this job's saved maps; other map and library references are dropped "
    "and counted in model_import.dropped_references.",
    "ply": "A worker reads the PLY header: without splat data it imports as a mesh, otherwise "
    "as a point cloud under splat_options.",
    "spz": "SPZ versions 2 and 3 decode on a worker; versions 1 and 4 stop with the saved file kept.",
    "splat": "Whole 32-byte .splat records decode on a worker.",
}
# Shown as a point cloud, never rendered as Gaussian splats.
_SPLAT_NOTE = (
    " Splats become a new coloured point cloud (mesh plus Geometry Nodes, new material): "
    "no rotations, anisotropic scales, view-dependent colour or true alpha blending. "
    "Files denser than splat_options.max_points are thinned evenly. splat_options.axes "
    "defaults to the file format's convention (OPENGL for SPZ, OPENCV for PLY and .splat); "
    "provider conventions may differ, so check the result is upright and import again "
    "with the other axes if needed."
)


def prepare_result_application(args):
    purpose = args.get("purpose", "import")
    splat = {
        name: args[key]
        for key, name in (("splat_max_points", "max_points"), ("splat_axes", "axes"))
        if args.get(key) is not None
    }
    if splat and (purpose != "import" or not args.get("asset_id")):
        raise ValueError("splat_max_points and splat_axes need an SPZ, PLY or .splat asset_id")
    if purpose in {"mesh_edit", "mesh_source"}:
        _, approval = runtime.prepare_mesh_application(
            args["context_id"],
            args["request_id"],
            args["expected_revision"],
            bpy.context.scene,
            bpy.context.view_layer.objects.active,
            args.get("asset_id"),
            policy=args.get("mesh_policy", "REMESH"),
            placement=args.get("mesh_placement", "WORLD"),
            keep_original=args.get("keep_original", True),
            original_source=purpose == "mesh_source",
        )
        return {
            "context_id": args["context_id"],
            "application_id": approval.identifier,
            "request_id": approval.record.intent.request_id,
            "revision": approval.record.revision,
            "reuse": approval.record.state.value == "applied",
            "kind": "mesh_edit",
            "purpose": purpose,
            "scene": approval.scene_name,
            "target": approval.target_name,
            "asset_id": approval.asset_id,
            "mesh_policy": approval.policy,
            "mesh_placement": approval.placement,
            "keep_original": approval.keep_original,
            "result_to_source": [list(row) for row in approval.mapping],
            "note": "Approve applying one saved GLB to this captured mesh. "
            "REMESH replaces geometry, UVs and mesh materials; UV replaces only active UVs "
            "and requires exact indexed topology and positions. RETEXTURE preserves geometry and "
            "non-UV attributes, replacing all UV layers and mesh materials with exact topology/position matching. "
            "PARTS replaces source geometry with an empty mesh parent and 2 to 128 named parts; "
            "every mesh in the selected GLB is treated as a part, not an alternate variant. "
            "RIG preserves source geometry, UVs and materials, attaching compatible bone weights "
            "and one returned rig with its clips. Indexed geometry must match; morphs and mesh "
            "animation are unsupported. Move the source and its new rig group together afterward. "
            "WORLD preserves imported scene "
            "positions; LOCAL treats imported positions as object-local. Neither fits or rescales "
            "the result automatically. Keep original preserves an unselected copy. "
            "No new generation or blend save. Desktop undo follows Blender settings; "
            "undo/redo changes the scene only and never replays spending. Check mesh_edit.undo_available "
            "after application. Provider alignment is not guaranteed.",
        }
    if purpose == "material":
        _, approval = runtime.prepare_material_application(
            args["context_id"],
            args["request_id"],
            args["expected_revision"],
            bpy.context.scene,
            bpy.context.view_layer.objects.active,
        )
        return {
            "context_id": args["context_id"],
            "application_id": approval.identifier,
            "reuse": approval.record.state.value == "applied"
            and not getattr(approval, "restore", False),
            "request_id": approval.record.intent.request_id,
            "revision": approval.record.revision,
            "scene": approval.scene_name,
            "target": approval.target_name,
            "slot": approval.target.active + 1,
            "roles": list(approval.roles),
            "kind": "material",
            "purpose": purpose,
            "note": "Approve one saved texture set for this mesh's active material slot. "
            "Images are packed; old materials and other slots are untouched. "
            "Only a local single-user mesh with UVs in Object Mode is supported. "
            "Normals use Blender tangent space; height uses bump only. AO/edge nodes stay unconnected. "
            "This does not regenerate or save the blend file, and has no global undo transaction.",
        }
    if purpose in {"world", "restore_world"}:
        _, approval = runtime.prepare_world_application(
            args["context_id"],
            args["request_id"],
            args["expected_revision"],
            bpy.context.scene,
            args.get("asset_id"),
            restore=purpose == "restore_world",
        )
        return {
            "context_id": args["context_id"],
            "application_id": approval.identifier,
            "reuse": approval.record.state.value == "applied"
            and not getattr(approval, "restore", False),
            "request_id": approval.record.intent.request_id,
            "revision": approval.record.revision,
            "scene": approval.scene_name,
            "asset_id": approval.asset_id,
            "kind": "world",
            "purpose": purpose,
            "previous_world": approval.previous.name if approval.previous else None,
            "format": None if approval.restore else describe_world_media(approval.media_type),
            "note": "Approve restoring this session's original World; changed owned World/image data prevents restoration."
            if approval.restore
            else "Approve replacing this scene's World with one packed equirectangular panorama. "
            "Only supported 2:1 PNG, JPEG or scanline OpenEXR bytes matching the saved media type qualify; "
            "PNG/JPEG are LDR and EXR is not proof of actual HDR range. "
            "The original World stays untouched and this session can restore it while unchanged. Nothing has been applied.",
        }
    if purpose == "video_first_frame":
        if not isinstance(args.get("asset_id"), str) or not args["asset_id"]:
            raise ValueError("video_first_frame requires the saved image asset_id")
        _, approval = runtime.prepare_first_frame_application(
            args["context_id"],
            args["request_id"],
            args["expected_revision"],
            bpy.context.scene,
            args["asset_id"],
        )
        target = approval.target
        return {
            "context_id": args["context_id"],
            "application_id": approval.identifier,
            "request_id": approval.record.intent.request_id,
            "revision": approval.record.revision,
            "kind": approval.kind,
            "purpose": purpose,
            "scene": approval.scene_name,
            "lane": "render_video",
            "model_id": target.model_id,
            "input": target.param_name,
            "input_label": target.input_label,
            "sent_as": target.sent_as,
            "reason": target.reason,
            "asset_id": approval.asset.asset_id,
            "file": approval.asset.name,
            "replaces_first_frame_path": bool(target.previous_path),
            "enables_first_frame": not target.previous_enabled,
            "mode": "reuse_asset",
            "note": (target.note + " " if target.note else "")
            + "Approve using this saved image as the Render Video first frame. "
            "It reuses the saved Scenario asset; nothing is uploaded, generated or spent, and no "
            "local file path is stored in the form or blend file. It turns the first frame on, "
            "replaces any chosen first-frame file and invalidates Render Video and Prompt Spark "
            "prices; call estimate_cost again before generate. Remove an existing first-frame "
            "slot with render_form first. Nothing has been changed.",
        }
    if purpose != "import":
        raise ValueError("Choose import, material, world, restore_world or video_first_frame")
    if args.get("asset_id"):
        _, approval = runtime.prepare_asset_application(
            args["context_id"],
            args["request_id"],
            args["expected_revision"],
            bpy.context.scene,
            args["asset_id"],
            **splat,
        )
        if approval.kind == "model":
            unit = approval.unit
            media = {item.asset.asset_id: item.asset.media_type for item in approval.record.results}
            roles = {unit.key: "model", unit.material: "material"}
            roles.update({asset_id: slot for slot, asset_id in unit.textures})
            options = (
                None
                if approval.max_points is None
                else {"max_points": approval.max_points, "axes": approval.axes}
            )
            return {
                "context_id": args["context_id"],
                "application_id": approval.identifier,
                "reuse": approval.record.state.value == "applied"
                and not getattr(approval, "restore", False),
                "request_id": approval.record.intent.request_id,
                "revision": approval.record.revision,
                "scene": approval.scene_name,
                "asset_id": approval.asset_id,
                "kind": "model",
                "format": unit.format.value,
                "primary": unit.primary,
                "members": [
                    {"asset_id": asset_id, "role": roles[asset_id], "media_type": media[asset_id]}
                    for asset_id in unit.members
                ],
                "resources": list(unit.resources),
                "cursor": list(approval.cursor),
                "splat_options": options,
                "note": "Approve importing this saved model into a new group with its bottom at the cursor. "
                "Existing objects, selection and timeline stay unchanged. "
                + (
                    "A worker verifies every saved receipt and reads the listed file before the import is claimed; "
                    "a mesh PLY is then rehashed into a private copy. "
                    if options is not None
                    else "Every listed file is rehashed into a private copy before the import is claimed; "
                )
                + "a changed or unsupported file stops it with the saved job unchanged. "
                + _MODEL_NOTES[unit.format.value]
                + (_SPLAT_NOTE if options is not None else "")
                + " Nothing has been imported; changed destinations require review again.",
            }
        return {
            "context_id": args["context_id"],
            "application_id": approval.identifier,
            "reuse": approval.record.state.value == "applied"
            and not getattr(approval, "restore", False),
            "request_id": approval.record.intent.request_id,
            "revision": approval.record.revision,
            "scene": approval.scene_name,
            "asset_id": approval.asset_id,
            "kind": approval.kind,
            "frame": approval.frame,
            "note": "Approve inserting this one media asset at this scene/frame on an unused channel. "
            "Scene timing stays unchanged; video insertion omits embedded audio. "
            "Media stays in a local file that must remain available. Nothing has been inserted.",
        }
    _, approval = runtime.prepare_image_application(
        args["context_id"], args["request_id"], args["expected_revision"], bpy.context.scene
    )
    return {
        "context_id": args["context_id"],
        "application_id": approval.identifier,
        "reuse": approval.record.state.value == "applied"
        and not getattr(approval, "restore", False),
        "request_id": approval.record.intent.request_id,
        "revision": approval.record.revision,
        "scene": approval.scene_name,
        "images": [item.asset.name for item in approval.record.results],
        "note": "Ask the user to approve importing and packing these images into this file. "
        "The approval becomes invalid if the destination changes. No images have been imported.",
    }


def apply_result_application(args):
    jobs, request_id, task = runtime.apply_saved_result(args["context_id"], args["application_id"])
    return jobs.status(request_id) if task is None else _finish_recovery(jobs, task, request_id)


def _finish_recovery(jobs, task, request_id):

    def run():
        try:
            task.result()
        except Exception:
            pass  # The owned completion supplies sanitized state/error on the main thread.

    def finish(_):
        runtime.sync_catalog_context()
        if runtime.state.model_jobs is not jobs or not jobs.session.active:
            raise ScenarioError(0, "The job context changed during recovery; inspect it again")
        return jobs.status(request_id)

    return DeferredTool(run, finish)


def wait_for_job(args):
    ref = _job_ref(args)
    timeout = args.get("timeout", 170)
    if (
        isinstance(timeout, bool)
        or not isinstance(timeout, (int, float))
        or not 0 <= timeout <= 170
    ):
        raise ValueError("timeout must be a finite number from 0 to 170 seconds")
    saved = _saved_status(ref)
    if saved is not None:
        jobs = runtime.state.model_jobs
        server = runtime.state.mcp
        if timeout == 0 or saved["local_id"] not in jobs.views:
            return saved

        def finish_shared(_):
            runtime.sync_catalog_context()
            if (
                runtime.state.model_jobs is not jobs
                or not jobs.session.active
                or (server is not None and (runtime.state.mcp is not server or not server.running))
            ):
                raise ScenarioError(0, "The job context changed while waiting; inspect it again")
            return jobs.status(saved["local_id"])

        return DeferredTool(
            lambda: jobs.wait(
                saved["local_id"],
                timeout,
                stopped=lambda: server is not None and not server.running,
            ),
            finish_shared,
        )
    rec = _find(ref)
    result = _status(rec)
    if not rec.is_terminal:
        result["note"] = (
            "Prototype status is a local snapshot; automatic polling is retired. "
            "Use recover_cloud_job in the selected account, then approve its destination."
        )
    return result


def import_result(args):
    reference = _job_ref(args)
    if not runtime.credentials().valid:
        raise ScenarioError(
            0, "Select complete credentials before importing; inspect saved jobs first"
        )
    if _saved_status(reference) is not None:
        raise ValueError(
            "Use prepare_result_application and explicit destination approval for saved results; "
            "use prepare_blockout_plan for saved Blockout text"
        )
    raise ValueError(
        "Use recover_cloud_job with the remote job and model IDs, then explicit destination "
        "approval; cached prototype files cannot authorize application"
    )


def recover_cloud_job(args):
    jobs = runtime.ensure_model_jobs()
    item = jobs.recover_cloud(args["job_id"], args["model_id"], bpy.context.scene)

    def finish(_):
        if runtime.ensure_model_jobs() is not jobs:
            raise ScenarioError(0, "The cloud recovery context changed; inspect saved jobs")
        record = jobs.finish_cloud(item)
        generation.process_model_jobs()
        return {
            "context_id": runtime.state.job_context_id,
            "request_id": record.intent.request_id,
            "job_id": record.remote_job_id,
            "revision": record.revision,
            "state": record.state.value,
            "source": getattr(record.intent, "source", "generation"),
            "note": "Saved for explicit recovery; this read did not download or apply anything",
        }

    def run():
        try:
            item.task.result()
        except Exception:
            pass  # The main-thread finisher reports the owned, sanitized failure.

    return DeferredTool(run, finish)


def capture_reference(args):
    from ..blender.reference_uploads import capture_upload

    ticket = capture_upload(bpy.context, source=args.get("source") or "VIEWPORT")
    return _reference_response(ticket.identifier)


def upload_reference(args):
    from ..blender.reference_uploads import agent_upload_path

    path = args.get("path")
    if not isinstance(path, str) or not path:
        raise ValueError("Choose a local reference path to upload")
    # The allowlist refuses before anything is read or sent; upload the resolved file.
    allowed = agent_upload_path(path)
    owner = runtime.ensure_reference_uploads()
    ticket = owner.start(
        bpy.context.scene,
        allowed,
        kind=args.get("kind", "image"),
        external=True,
    )
    return _reference_response(ticket.identifier)


def _reference_response(identifier):
    return {
        "context_id": runtime.state.job_context_id,
        **runtime.ensure_reference_uploads().status(identifier),
        "note": "Poll reference_upload_status; use its imported asset_id in estimate_cost "
        "or estimate_workflow parameters. "
        "Do not upload the same file again for an unfinished upload; use list_reference_uploads "
        "and its suggested recovery. Uploading does not generate or approve spending.",
    }


def _upload_context(args):
    owner = runtime.ensure_reference_uploads()
    if args.get("context_id") != runtime.state.job_context_id:
        raise ScenarioError(0, "The upload context changed; list saved uploads again")
    return owner


def reference_upload_status(args):
    owner = _upload_context(args)
    return {"context_id": runtime.state.job_context_id, **owner.status(args["reference_id"])}


def list_reference_uploads(args):
    owner = runtime.ensure_reference_uploads()
    return {
        "context_id": runtime.state.job_context_id,
        "uploads": [
            {
                "request_id": item.record.intent.request_id,
                "revision": item.record.revision,
                "state": item.record.state.value,
                "action": item.action.value,
                "asset_id": item.record.asset_id,
                "upload_id": item.record.upload_id,
                "kind": item.record.intent.kind,
                "content_type": item.record.intent.content_type,
            }
            for item in owner.session.upload_recovery_plan()
        ],
    }


def recover_reference_upload(args):
    owner = _upload_context(args)
    command = owner.recover(args["request_id"], args["expected_revision"], args.get("action"))

    def finish(_):
        runtime.sync_catalog_context()
        if runtime.state.reference_uploads is not owner or not owner.session.active:
            raise ScenarioError(0, "The upload context changed during recovery")
        record = owner.recovery_result(command)
        result = {
            "request_id": record.intent.request_id,
            "state": record.state.value,
            "revision": record.revision,
            "asset_id": record.asset_id,
            "kind": record.intent.kind,
            "content_type": record.intent.content_type,
        }
        if command.reference_id is not None:
            result["reference_id"] = command.reference_id
            result["replacement_request_id"] = command.replacement.intent.request_id
        return result

    if command.task is None:
        return finish(None)

    def run():
        try:
            command.task.result()
        except Exception:
            # finish() checks the owned completion and reports recovery failure
            # on the main thread; this worker only waits for the task to finish.
            pass

    return DeferredTool(run, finish)


def list_generations(args):
    from ..blender import history

    refresh = args.get("refresh", False)
    if not isinstance(refresh, bool):
        raise ValueError("refresh must be a boolean")
    older = args.get("older", False)
    if not isinstance(older, bool):
        raise ValueError("older must be a boolean")
    if refresh and older:
        raise ValueError("Use refresh or older, not both")
    generation.process_catalog_events()
    if refresh:
        history.refresh()
        return {"generations": [], "note": "history requested, call again without refresh"}
    if older:
        # The same cursor as native Load older; a failed older read can be retried.
        if history.older():
            return {"generations": [], "note": "older history requested, call again without older"}
        if runtime.state.history_loading:
            return {"generations": [], "note": "history read pending, call again without older"}
        raise ValueError("No older cloud history page is available; call with refresh=true")
    if runtime.state.history_error:
        raise RuntimeError(f"{runtime.state.history_error}; retry with refresh=true")
    if not runtime.state.history_loaded:
        if not runtime.state.history_loading:
            history.refresh()
        return {"generations": [], "note": "history requested, call again in a few seconds"}
    limit = int(args.get("limit", 20))
    saved_ids = {}
    for record in history.saved_records():
        if record.remote_job_id:
            saved_ids.setdefault(record.remote_job_id, []).append(record.intent.request_id)
    result = {
        "generations": [
            {
                "job_id": e.job_id,
                "kind": history.entry_kind(e),
                "model_id": e.model_id,
                "prompt": e.prompt,
                "status": e.status,
                "cu_cost": e.cu_cost,
                "cost_unavailable": e.cost_unavailable,
                "workflow_id": e.workflow_id or None,
                "workflow_job_id": e.workflow_job_id or None,
                "local_files": [],
                "local_request_ids": saved_ids.get(e.job_id, list(e.local_request_ids)),
            }
            for e in runtime.state.history[:limit]
        ]
    }
    # Never truncate silently: say what limit hid and whether an older page exists.
    hidden = len(runtime.state.history) - len(result["generations"])
    if hidden > 0:
        result["more_loaded"] = hidden
    if runtime.state.history_token:
        result["older_page"] = True
    if runtime.state.history_older_error:
        # Like native Load older: keep loaded rows and retry the same cursor, unless it cycled.
        result["older_error"] = runtime.state.history_older_error
        result["note"] = (
            "older pages repeated a cursor; loaded rows kept; call with refresh=true to restart"
            if runtime.state.history_older_error == history.CURSOR_CYCLE
            else "older page read failed; loaded rows kept; retry with older=true"
        )
    if runtime.state.history_loading:
        result["note"] = "showing loaded history while refresh is pending; call again"
    return result


def _schema(props, required=()):
    return {"type": "object", "properties": props, "required": list(required)}


_JOB_REF = {
    "job_id": {
        "type": "string",
        "description": "Scenario job id (job_...) or the local_id returned by generate",
    },
    "id": {"type": "string", "description": "Same as job_id, kept for compatibility"},
}


SPECS = (
    ToolSpec(
        "list_assets",
        (
            "Read one asset-library page using the selected credentials and optional project scope.\n"
            "Args: public defaults to false (owned assets); page_size is 1 to 100, default 40; pagination_token and collection_id are optional.\n"
            "Returns: asset IDs, names, descriptions, MIME types, generation types, tags, collection IDs and next_pagination_token.\n"
            'Example: {"page_size": 20}.\n'
            "Reuse the same filters with the returned cursor. Ordering can change between reads. No download URLs, file bytes, complete text previews, upload or generation are returned or started.\n"
            "Platform equivalent: SDK assets.list through the shared adapter."
        ),
        _schema(
            {
                "public": {"type": "boolean"},
                "page_size": {"type": "integer", "minimum": 1, "maximum": 100},
                "pagination_token": {"type": "string"},
                "collection_id": {"type": "string"},
            }
        ),
        list_assets,
        {"readOnlyHint": True},
    ),
    ToolSpec(
        "search_assets",
        (
            "Search asset-library metadata through the shared SDK session.\n"
            "Args: query is required nonempty text, at most 4096 characters; public defaults to false; limit is 1 to 100 (default 40), offset is nonnegative (default 0).\n"
            "Returns: reusable asset metadata, estimated_total and next_offset; a total is an estimate, not a stable snapshot.\n"
            'Example: {"query": "ceramic cup", "limit": 20}.\n'
            "Continue with the same query/public selection and returned offset. Signed URLs, indexed text bodies and account identifiers are omitted. No upload, generation, file download or organization write.\n"
            "Platform equivalent: SDK search.asset_search through the shared adapter."
        ),
        _schema(
            {
                "query": {"type": "string", "minLength": 1, "maxLength": 4096},
                "public": {"type": "boolean"},
                "limit": {"type": "integer", "minimum": 1, "maximum": 100},
                "offset": {"type": "integer", "minimum": 0},
            },
            ["query"],
        ),
        search_assets,
        {"readOnlyHint": True},
    ),
    ToolSpec(
        "list_collections",
        (
            "Read one page of Scenario collections in Blender's selected credential and optional project scope.\n"
            "Args: page_size is 1 to 100, default 50; pagination_token is optional, from the previous page.\n"
            "Returns: collections[] with collection_id, name, asset_count, model_count and updated_at, plus next_pagination_token.\n"
            'Example: {"page_size": 20}.\n'
            "Use a returned collection_id with prepare_asset_organization or as the list_assets collection_id filter. No cursor is followed automatically. Thumbnail URLs and owner IDs are omitted. No write, upload or generation.\n"
            "Platform equivalent: collections_list."
        ),
        _schema(
            {
                "page_size": {"type": "integer", "minimum": 1, "maximum": 100},
                "pagination_token": {"type": "string"},
            }
        ),
        list_collections,
        {"readOnlyHint": True},
    ),
    ToolSpec(
        "prepare_asset_organization",
        (
            "Read the current tags and collection memberships of library assets and prepare one reviewed collection or tag change, without sending it.\n"
            "Args:\n"
            "  - operation: required, add_to_collection, remove_from_collection, update_tags or create_collection.\n"
            "  - asset_ids: 1 to 49 unique asset IDs from list_assets or search_assets; optional only for create_collection, whose assets are added after the create.\n"
            "  - collection_id: required for add_to_collection and remove_from_collection, from list_collections.\n"
            "  - collection_name: required for create_collection; an existing exact name is refused and its IDs returned.\n"
            "  - add_tags, remove_tags: for update_tags, at most 30 unique tags each, without commas; a tag cannot be in both.\n"
            "Returns: context_id, review_id, phase (READY, UNCHANGED or REJECTED; PREPARING while the read is pending), project_id (null for the key's own scope), operation, collection_id/collection_name, assets[] with asset_id, name, tags, in_collection and change, missing_asset_ids, existing_collection_ids, request_count, expires_in, message, notice and note.\n"
            'Example: {"operation": "add_to_collection", "asset_ids": ["asset-id"], "collection_id": "collection-id"}.\n'
            "Show the per-asset change and notice to the user and obtain explicit approval before apply_asset_organization. The change will be immediate Scenario account metadata, uses no credits and is not reversed by Blender Undo. A READY review expires after 10 minutes. This organizes assets in Blender's selected connection; collection rename/delete and model collections are hosted-server operations.\n"
            "Platform equivalent: collection_add_assets, collection_remove_assets, collection_create, asset_add_tags and asset_remove_tags, reviewed locally before any write."
        ),
        _schema(
            {
                "operation": {
                    "type": "string",
                    "enum": [
                        "add_to_collection",
                        "remove_from_collection",
                        "update_tags",
                        "create_collection",
                    ],
                },
                "asset_ids": {
                    "type": "array",
                    "items": {"type": "string"},
                    "maxItems": 49,
                    "uniqueItems": True,
                },
                "collection_id": {"type": "string"},
                "collection_name": {"type": "string"},
                "add_tags": {"type": "array", "items": {"type": "string"}, "maxItems": 30},
                "remove_tags": {"type": "array", "items": {"type": "string"}, "maxItems": 30},
            },
            ["operation"],
        ),
        prepare_asset_organization,
        {"readOnlyHint": True},
    ),
    ToolSpec(
        "apply_asset_organization",
        (
            "Apply one READY organization review once, after the user explicitly approved its shown change.\n"
            "Args: context_id and review_id are required, from prepare_asset_organization.\n"
            "Returns: the review with message and note, and either phase FINISHED and result {state VERIFIED, PARTIAL, UNCONFIRMED or REJECTED, requests_sent, collection_id, created_collection_id, create_outcome, outcomes[] with asset_id, state, status, tags and in_collection}, or result null when the outcome is unknown; or phase NOT_SENT with result null when nothing was sent, with any collection that took the name in existing_collection_ids.\n"
            'Example: {"context_id": "from-prepare", "review_id": "from-prepare"}.\n'
            "Assets already in the collection are skipped; if another client added one after the review, the rest are sent again within a fixed bound. Nothing else is retried, then the assets are read back. Never repeat UNCONFIRMED work: inspect with list_assets, then prepare a new review, which reads the current state first. After a timeout or error from this call, even one saying the tool was not executed, call asset_organization_status first: it shows whether the review is still READY, applying or finished, and a review never applies twice. A connection change during apply reports an unknown outcome in this context. Spends no credits; Blender Undo does not reverse it.\n"
            "Platform equivalent: collection_add_assets, collection_remove_assets, collection_create, asset_add_tags and asset_remove_tags, through the shared SDK."
        ),
        _schema(
            {"context_id": {"type": "string"}, "review_id": {"type": "string"}},
            ["context_id", "review_id"],
        ),
        apply_asset_organization,
        {"destructiveHint": True},
    ),
    ToolSpec(
        "asset_organization_status",
        (
            "Inspect an organization review, including an apply whose call timed out, or discard a review that is not applying.\n"
            "Args: context_id and review_id are required, from prepare_asset_organization; action is status (default) or discard.\n"
            "Returns: the same review payload as prepare_asset_organization or apply_asset_organization, with the current phase, result when finished, message and note.\n"
            'Example: {"context_id": "from-prepare", "review_id": "from-prepare", "action": "status"}.\n'
            "Never sends an organization write. Reviews live in this Blender session only: a file load, credential or project change discards them, and a write already sent keeps its effect.\n"
            "Platform equivalent: none; this reads the local review, while collection_get and asset_get read service state."
        ),
        _schema(
            {
                "context_id": {"type": "string"},
                "review_id": {"type": "string"},
                "action": {"type": "string", "enum": ["status", "discard"]},
            },
            ["context_id", "review_id"],
        ),
        asset_organization_status,
        {"readOnlyHint": True},
    ),
    ToolSpec(
        "list_workflows",
        (
            "List workflows in the selected credential/project scope without spending.\n"
            "Args: privacy is private (default) or public; query filters id/name/description; offset defaults to 0 and limit is 1 to 40 (default 40).\n"
            "Returns: workflows, total matching rows and next_offset, or an explicit catalog error.\n"
            'Example: {"privacy": "public", "query": "image", "limit": 20}.\n'
            "Each call reads the bounded complete catalog before local filtering/paging; ordering can change between calls. Context changes reject delivery. No upload or generation.\n"
            "Platform equivalent: workflows_list."
        ),
        _schema(
            {
                "privacy": {"type": "string", "enum": ["private", "public"]},
                "query": {"type": "string"},
                "offset": {"type": "integer", "minimum": 0},
                "limit": {"type": "integer", "minimum": 1, "maximum": 40},
            }
        ),
        list_workflows,
        {"readOnlyHint": True},
    ),
    ToolSpec(
        "workflow_schema",
        (
            "Read a workflow's declared input definitions without spending.\n"
            "Args: workflow_id is required.\n"
            "Returns: workflow_id, name, description and original inputs with conditional/default/file definitions.\n"
            'Example: {"workflow_id": "workflow-example"}.\n'
            "This does not prove every workflow feature is supported; estimate_workflow validates supported input forms with fresh metadata before pricing. For file inputs, use Library asset IDs or imported asset IDs from upload_reference or capture_reference.\n"
            "Platform equivalent: workflow_get."
        ),
        _schema({"workflow_id": {"type": "string"}}, ["workflow_id"]),
        workflow_schema,
        {"readOnlyHint": True},
    ),
    ToolSpec(
        "estimate_workflow",
        (
            "Request a free exact workflow price bound to the selected scene and connection.\n"
            "Args: workflow_id is required; parameters is an input object (default empty).\n"
            "Returns: quote_id, workflow_id, original parameters, normalized payload, cu_cost_exact, mesh_sources, loop_steps, quote_may_understate and cost_warning. loop_steps counts loop nodes (for-each, or any node carrying a ForEach field) in the workflow definition, or is null when coverage is unknown, including an empty flow not marked hasFlow false; when quote_may_understate is true the price covers one loop pass and the final charge can be higher. Show cost_warning with the price; it does not block approval.\n"
            'Example: {"workflow_id": "workflow-example", "parameters": {"prompt": "a cup"}}.\n'
            "No paid submission or upload. Review the normalized payload and exact price; run_workflow requires the same original parameters and explicit approved_cost. Scene, file, credential or project changes require a fresh estimate.\n"
            "Platform equivalent: dry_run on workflow_run."
        ),
        _schema(
            {"workflow_id": {"type": "string"}, "parameters": {"type": "object"}}, ["workflow_id"]
        ),
        estimate_workflow,
    ),
    ToolSpec(
        "run_workflow",
        (
            "Approve one unchanged workflow estimate and persist its identity before paid dispatch.\n"
            "Args: workflow_id, quote_id and approved_cost are required; parameters must match estimate_workflow's original parameters. approved_cost must be its exact cu_cost_exact string.\n"
            "Returns: local_id, saved state, loop_steps, quote_may_understate, cost_warning and note; use job_status, wait_for_job and explicit result application. job_status cu_cost_exact remains the approved quote, not the final charge.\n"
            'Example: {"workflow_id": "workflow-example", "quote_id": "approved-quote", "parameters": {"prompt": "a cup"}, "approved_cost": "1.25"}.\n'
            "Consumes the quote before persistence. Never repeat an uncertain submission; inspect saved jobs. Closing views does not stop it. No automatic scene import. A running workflow cannot be canceled. Waiting approval and selection steps are read with workflow_job_steps and decided only with the user's explicit choice through decide_workflow_step; rejecting a step stops the workflow.\n"
            "Platform equivalent: workflow_run."
        ),
        _schema(
            {
                "workflow_id": {"type": "string"},
                "parameters": {"type": "object"},
                "quote_id": {"type": "string"},
                "approved_cost": {"type": "string"},
            },
            ["workflow_id", "quote_id", "approved_cost"],
        ),
        run_workflow,
    ),
    ToolSpec(
        "discard_workflow_estimate",
        (
            "Discard one ready unsubmitted workflow approval without changing saved jobs.\n"
            "Args: quote_id is required.\n"
            "Returns: discarded=true.\n"
            'Example: {"quote_id": "unused-workflow-quote"}.\n'
            "This only releases local approval authority; it does not cancel remote work, upload inputs or request another price. Used quotes remain unusable.\n"
            "Platform equivalent: none; local estimate lifecycle."
        ),
        _schema({"quote_id": {"type": "string"}}, ["quote_id"]),
        discard_workflow_estimate,
    ),
    ToolSpec(
        "workflow_job_steps",
        (
            "Read a saved workflow job's waiting approval and selection steps again so the user can review them; decides nothing.\n"
            "Args:\n"
            "  - context_id: required string, current context from list_local_jobs.\n"
            "  - request_id: required string, the saved workflow job's request_id.\n"
            "Returns: context_id, request_id, workflow_id, revision, state, remote_status, cu_cost_exact (the job's approved price), awaiting_decision, steps, decision_error, delivery_paused and error. Each step has node_id, type (user-approval or user-selection), iteration_index, loop_node_id, depends_on, review_asset_ids (at most 50 of review_asset_total), items (a selection's frozen candidates: asset IDs or texts per item_kind), min, max, actions (the decisions that may be sent), blocked, blocked_reason, links_complete and review_id; never signed URLs.\n"
            'Example: {"context_id": "from-list", "request_id": "from-list"}.\n'
            "A remote job is retrieved once more, or the read already in flight is awaited; a job no longer remote returns without a request and with no steps. An automatically polled job stays polled and a paused or restarted job stays paused. Requires Online Access for a remote job; unknown and non-workflow jobs are rejected. Each call is one more status read, plus one read of the workflow's definition for its step links the first time a step is seen waiting, so follow a job with job_status or wait_for_job and call this only to review steps before a decision. Show each step, its review assets or items and the approved price to the user before decide_workflow_step. A selection blocked as loop (it feeds a ForEach loop, a nested workflow or an unlisted node type, which the approved price may not cover), unverified (the links between the workflow's steps could not be read and the workflow contains such a node, so it may feed one) or unsupported offers only reject; unidentified offers nothing. links_complete false means some step links are unknown, so depends_on and the review assets may be incomplete; tell the user before an approval. Review assets and candidates are asset IDs only; no preview is downloaded.\n"
            "Platform equivalent: job_get, reading the workflow flow through the shared SDK jobs.retrieve and its step links through workflows.retrieve."
        ),
        _schema(
            {"context_id": {"type": "string"}, "request_id": {"type": "string"}},
            ["context_id", "request_id"],
        ),
        workflow_job_steps,
        {"readOnlyHint": True},
    ),
    ToolSpec(
        "decide_workflow_step",
        (
            "Send one decision the user explicitly chose for a waiting workflow step: approve, select items or reject.\n"
            "Args:\n"
            "  - context_id: required string, current context from list_local_jobs or workflow_job_steps.\n"
            "  - request_id: required string, the saved workflow job's request_id.\n"
            "  - expected_revision: required nonnegative integer, the revision returned by workflow_job_steps.\n"
            "  - node_id: required string, the step's node_id.\n"
            "  - review_id: required string, the step's review_id; a changed step needs review again.\n"
            "  - action: required string listed in the step's actions: approve or reject for user-approval, select or reject for user-selection.\n"
            "  - selected_indices: integer array, select only: unique zero-based indices into the step's items, from min to max of them, sent in the given order.\n"
            "Returns: outcome (applied, refused, uncertain or not_sent) and note, plus the workflow_job_steps fields read after the decision.\n"
            'Example: {"context_id": "from-list", "request_id": "from-list", "expected_revision": 3, "node_id": "from-steps", "review_id": "from-steps", "action": "select", "selected_indices": [2, 0]}.\n'
            "Call only after showing the step, its review assets or items and the job's approved price to the user and receiving their explicit choice; never decide automatically, by default or on the user's behalf. Approving or selecting lets the job run its remaining steps, which spend credits; the charge after a decision is not live-verified against the approved price. Rejecting stops the workflow and is the only way to stop one; completed steps stay charged, and a rejected ForEach iteration cascades to its surrounding loop instead, so trust the refreshed state. The job is retrieved first and the step must be unchanged; one request follows and is never retried. Never repeat an uncertain decision until the step is shown waiting again; refused and not_sent decisions need a fresh review. Invalid picks, a blocked action, a changed step or revision, a stale context, a command in flight and disabled Online Access are refused before any request.\n"
            "Platform equivalent: workflow_approve or workflow_reject for approval steps; SDK workflows.user_approval and the documented user-selection endpoint through the shared adapter."
        ),
        _schema(
            {
                "context_id": {"type": "string"},
                "request_id": {"type": "string"},
                "expected_revision": {"type": "integer", "minimum": 0},
                "node_id": {"type": "string"},
                "review_id": {"type": "string"},
                "action": {"type": "string", "enum": ["approve", "select", "reject"]},
                "selected_indices": {
                    "type": "array",
                    "items": {"type": "integer", "minimum": 0},
                    "uniqueItems": True,
                    "maxItems": 50,
                },
            },
            ["context_id", "request_id", "expected_revision", "node_id", "review_id", "action"],
        ),
        decide_workflow_step,
        {"destructiveHint": True},
    ),
    ToolSpec(
        "prepare_film_composition",
        (
            "Inspect saved Film media and prepare a final or previs composition without spending.\n"
            "Args: context_id and production_id are required from film_recipe inspection; mode is final (default) or previs, score_task_id defaults to score.\n"
            "Returns: review_id and PREPARING phase; poll film_composition_review until READY.\n"
            'Example: {"context_id": "current-context", "production_id": "saved-production", "mode": "final"}.\n'
            "Film is experimental. Requires retained upload files or downloaded results and installed ffprobe. Uses the existing selected connection and recipe; no upload, download, scene mutation or generation. Reviews are session-local. A final recipe that omits audio_tracks plays score_task_id as its looping score, so that task must be declared; audio_tracks: [] means no score. An error names a missing or mismatched source task.\n"
            "Platform equivalent: none; local composition source verification."
        ),
        _schema(
            {
                "context_id": {"type": "string"},
                "production_id": {"type": "string"},
                "mode": {"type": "string", "enum": ["final", "previs"]},
                "score_task_id": {"type": "string"},
            },
            ["context_id", "production_id"],
        ),
        prepare_film_composition,
    ),
    ToolSpec(
        "film_composition_review",
        (
            "Inspect, cancel or discard a session-local Film composition review.\n"
            "Args: review_id is required; action is status (default), cancel or discard.\n"
            "Returns: phase, original scene/production/master, frames/fps, sources/layers, parameters, exact price, saved request_id and sanitized error when available.\n"
            'Example: {"review_id": "current-composition", "action": "status"}.\n'
            "Film is experimental. Cancel stops local inspection or discards a pending price after it finishes. Discard releases only the review after active work ends; media and saved jobs remain. Neither action cancels or repeats a generation. After restart inspect film_recipe and saved jobs.\n"
            "Platform equivalent: none; local composition review lifecycle."
        ),
        _schema(
            {
                "review_id": {"type": "string"},
                "action": {"type": "string", "enum": ["status", "cancel", "discard"]},
            },
            ["review_id"],
        ),
        film_composition_review,
    ),
    ToolSpec(
        "estimate_film_composition",
        (
            "Request the exact server price for one READY verified Film composition.\n"
            "Args: review_id is required from prepare_film_composition.\n"
            "Returns: QUOTED review with exact cu_cost_exact, model parameters and original master identity.\n"
            'Example: {"review_id": "ready-composition"}.\n'
            "Film is experimental. Rechecks saved sources, original recipe, scene and connection. No generation. Review the returned payload and obtain explicit spending approval before generate_film_composition.\n"
            "Platform equivalent: estimate_cost for model_scenario-compose-video."
        ),
        _schema({"review_id": {"type": "string"}}, ["review_id"]),
        estimate_film_composition,
        {"readOnlyHint": True},
    ),
    ToolSpec(
        "generate_film_composition",
        (
            "Approve one exact Film composition price and save its master identity before a single submission.\n"
            "Args: review_id and approved_cost are required strings; approve cu_cost_exact verbatim.\n"
            "Returns: SUBMITTED review with request_id for job_status and saved-job recovery.\n"
            'Example: {"review_id": "quoted-composition", "approved_cost": "0.10000000000000001"}.\n'
            "Film is experimental. Requires explicit approval of the reviewed payload and exact cost. Consumes approval before preparation. On an error or lost response inspect film_recipe and saved jobs; never repeat uncertain submission. Downloads remain saved for separate application; no recipe or scene edit.\n"
            "Platform equivalent: generate with model_scenario-compose-video through the shared model runtime."
        ),
        _schema(
            {"review_id": {"type": "string"}, "approved_cost": {"type": "string"}},
            ["review_id", "approved_cost"],
        ),
        generate_film_composition,
    ),
    ToolSpec(
        "prepare_film_review",
        (
            "Copy and measure saved Film media for a final or previs review scene without building or spending.\n"
            "Args: context_id and production_id are required from film_recipe inspection; mode is final (default) or previs, score_task_id defaults to score, include_master defaults to false.\n"
            "Returns: review_id and PREPARING phase with the recipe scene, mode and frame timing; poll film_review_status until READY.\n"
            'Example: {"context_id": "current-context", "production_id": "saved-production", "mode": "final"}.\n'
            "Film is experimental. Copies up to 2 GiB of downloaded results and retained upload files into private extension storage and measures them with installed ffprobe; video and Film frame rates must match. include_master adds the recipe's saved master as a muted alternate. Shares the single local media slot with capture and composition inspection. No download, upload, generation, claim or scene change; build_film_review requires separate approval. Keep the recipe scene selected and unchanged until building: object selection or edits, Undo or Redo, building another review for the scene, or leaving it and selecting it again invalidate the review. Reviews are session-local. A final recipe that omits audio_tracks plays score_task_id as its looping score, so that task must be declared; audio_tracks: [] means no score. An error names a missing or mismatched source task.\n"
            "Platform equivalent: none; local saved-media review preparation."
        ),
        _schema(
            {
                "context_id": {"type": "string"},
                "production_id": {"type": "string"},
                "mode": {"type": "string", "enum": ["final", "previs"]},
                "score_task_id": {"type": "string"},
                "include_master": {"type": "boolean"},
            },
            ["context_id", "production_id"],
        ),
        prepare_film_review,
    ),
    ToolSpec(
        "film_review_status",
        (
            "Inspect, cancel or discard a session-local Film review, or retry only its known saved receipt.\n"
            "Args: review_id is required; action is status (default), cancel, discard, retry_receipt or dismiss_uncertain. Dismissal requires inspected=true.\n"
            "Returns: review_id, production_id, mode, phase, recipe scene, review_scene, frames/fps, shots, sources, bytes, audio_segments, master, error, receipt_retry_available and inspection_required.\n"
            'Example: {"review_id": "current-review", "action": "status"}.\n'
            "Film is experimental. Status advances local preparation but never builds. Cancel stops preparation; discard deletes only an unbuilt review's private copies. Recipe, frame or scene changes, including object selection or edits, Undo or Redo, building another review for the scene, and leaving the recipe scene and selecting it again, invalidate an unbuilt review and delete its copies. Receipt retry never copies media or rebuilds. Dismissal retires an inspected uncertain review only when no known receipt remains; it never clears a saved claim. Built scenes, saved jobs and source media are never changed. Handles expire with the session.\n"
            "Platform equivalent: none; local review lifecycle and receipt recovery."
        ),
        _schema(
            {
                "review_id": {"type": "string"},
                "action": {
                    "type": "string",
                    "enum": ["status", "cancel", "discard", "retry_receipt", "dismiss_uncertain"],
                },
                "inspected": {"type": "boolean"},
            },
            ["review_id"],
        ),
        film_review_status,
    ),
    ToolSpec(
        "build_film_review",
        (
            "Approve one READY Film review and build a new review scene from its private copies.\n"
            "Args: review_id is the required string from prepare_film_review.\n"
            "Returns: the review status with BUILT, ERROR or UNCERTAIN phase and the new review_scene.\n"
            'Example: {"review_id": "ready-review"}.\n'
            "Film is experimental. Requires explicit build approval of the returned timing, sources and size. Needs a Blender window in Object Mode. Rechecks the recipe, scene and sources, consumes the review and saves application claims for generated sources before native decoding. Keeps the working scene selected. No generation, download, upload or native operator Undo entry. Never repeat an uncertain build; use retry_receipt when offered.\n"
            "Platform equivalent: none; local Blender review assembly."
        ),
        _schema({"review_id": {"type": "string"}}, ["review_id"]),
        build_film_review,
    ),
    ToolSpec(
        "film_export_sources",
        (
            "Inspect the built Film review scenes of the current recipe that can be exported as video.\n"
            "Args: production_id is the required string from film_recipe.\n"
            "Returns: context_id, production_id, recipe scene, default_directory (the Output Folder's Film subfolder), encoder_available, verification (ffprobe or blender) and sources with source_id, scene, kind (final/previs), frames/fps, width/height, strips, media, audible, blocked_reason and default_name.\n"
            'Example: {"production_id": "saved-production"}.\n'
            "Film is experimental. Lists only local review scenes built for this production and unchanged recipe; timeline, foreign and library scenes are excluded. Fresh inspection replaces unprepared source handles. No rendering, file write, upload or generation.\n"
            "Platform equivalent: none; local Film export inspection."
        ),
        _schema({"production_id": {"type": "string"}}, ["production_id"]),
        film_export_sources,
        {"readOnlyHint": True},
    ),
    ToolSpec(
        "prepare_film_export",
        (
            "Bind one built Film review scene and a new MP4 destination for a separate render approval.\n"
            "Args: context_id, production_id, source_id and destination_path are required; destination_path is an absolute new .mp4 path inside the Output Folder; width/height default to the scene's even output size, and both are required when that size is outside 64 to 4096; include_audio defaults to whether a sound strip is audible.\n"
            "Returns: review_id, READY phase, the exact resolved destination, source scene, frames/fps, duration, width/height, audio and media count.\n"
            'Example: {"context_id": "current-context", "production_id": "saved-production", "source_id": "current-source", "destination_path": "<default_directory>/Cut - Final Review.mp4"}.\n'
            "Film is experimental. Agents may write only inside the configured Output Folder, after resolving links; only its default Film subfolder is created when missing. The file must not exist and is never overwritten. Nothing renders yet: show the returned destination and settings and obtain explicit approval before render_film_export. Keep the recipe scene selected and both scenes unchanged until rendering: frame changes, object selection or edits, Undo or Redo, or leaving and selecting a scene again invalidate it. Exports are session-local.\n"
            "Platform equivalent: none; local video export settings review."
        ),
        _schema(
            {
                "context_id": {"type": "string"},
                "production_id": {"type": "string"},
                "source_id": {"type": "string"},
                "destination_path": {"type": "string"},
                "width": {"type": "integer", "minimum": 64, "maximum": 4096},
                "height": {"type": "integer", "minimum": 64, "maximum": 4096},
                "include_audio": {"type": "boolean"},
            },
            ["context_id", "production_id", "source_id", "destination_path"],
        ),
        prepare_film_export,
    ),
    ToolSpec(
        "render_film_export",
        (
            "Approve one READY Film export and render it to its destination in an offline Blender process.\n"
            "Args: review_id and approved_destination are required strings; approve the destination returned by prepare_film_export verbatim.\n"
            "Returns: export status with EXPORTING phase and progress; poll film_export_review until EXPORTED, PUBLISH_FAILED, ERROR or CANCELLED.\n"
            'Example: {"review_id": "ready-export", "approved_destination": "<default_directory>/Cut - Final Review.mp4"}.\n'
            "Film is experimental. Requires explicit approval of the exact destination and settings, a destination still inside the Output Folder, Object Mode and the unchanged recipe scene selected. Consumes the approval even when it cannot start. Snapshots the review scene privately, encodes H.264/AAC MP4 with Blender's FFmpeg, verifies it with installed ffprobe or Blender's decoder, then saves it without overwriting; the working file and scenes stay unchanged. One export runs at a time; later scene edits do not stop it. No upload, generation or spending.\n"
            "Platform equivalent: none; local offline video export."
        ),
        _schema(
            {"review_id": {"type": "string"}, "approved_destination": {"type": "string"}},
            ["review_id", "approved_destination"],
        ),
        render_film_export,
    ),
    ToolSpec(
        "film_export_review",
        (
            "Inspect, cancel or discard a session-local Film export, or save its verified video to another new file after saving failed.\n"
            "Args: review_id is required; action is status (default), cancel, discard, prepare_republish (requires destination_path inside the Output Folder) or republish (requires approved_destination equal to the returned pending_destination, inside the Output Folder).\n"
            "Returns: review_id, phase, scenes, frames/fps, duration, width/height, audio, destination, pending_destination, progress stage/done/total and percent, published, sha256, size, verification, republish_available, private directory and error.\n"
            'Example: {"review_id": "current-export", "action": "status"}.\n'
            "Film is experimental. Status advances local work but never starts a render. Cancel stops the export and leaves the destination absent. Discard deletes only the private snapshot, staged video and logs; an exported file stays. Republish copies the verified video only after a failed save and never renders again; it needs its own destination approval. Handles and private files end with the session.\n"
            "Platform equivalent: none; local video export lifecycle."
        ),
        _schema(
            {
                "review_id": {"type": "string"},
                "action": {
                    "type": "string",
                    "enum": ["status", "cancel", "discard", "prepare_republish", "republish"],
                },
                "destination_path": {"type": "string"},
                "approved_destination": {"type": "string"},
            },
            ["review_id"],
        ),
        film_export_review,
    ),
    ToolSpec(
        "film_case_study_sources",
        (
            "Inspect the built Film review scenes and director timelines of the current recipe that can form a portable case-study bundle.\n"
            "Args: production_id is the required string from film_recipe.\n"
            "Returns: context_id, production_id, recipe scene, default_directory (the Output Folder's Film subfolder), default_name, sources with source_id, scene, kind (final/previs), frames/fps, width/height, strips, media and blocked_reason, and timelines with timeline_id, scene, shots and blocked_reason.\n"
            'Example: {"production_id": "saved-production"}.\n'
            "Film is experimental. Lists only local review scenes and editable timelines built for this production and unchanged recipe; a timeline must still hold the current shot scenes in recipe order. Fresh inspection replaces unprepared source handles. No file write, upload or generation.\n"
            "Platform equivalent: none; local Film case-study inspection."
        ),
        _schema({"production_id": {"type": "string"}}, ["production_id"]),
        film_case_study_sources,
        {"readOnlyHint": True},
    ),
    ToolSpec(
        "prepare_film_case_study",
        (
            "Bind one built Film review scene, an optional director timeline and a new ZIP destination for a separate build approval.\n"
            "Args: context_id, production_id, source_id and destination_path are required; destination_path is an absolute new .zip path inside the Output Folder; timeline_id adds that timeline with its shot scenes.\n"
            "Returns: review_id, READY phase, the exact resolved destination, review and timeline scenes, shot, scene and media counts, media_bytes, approved_tasks, priced_tasks and quoted_cost_total for the ledger.\n"
            'Example: {"context_id": "current-context", "production_id": "saved-production", "source_id": "current-source", "timeline_id": "current-timeline", "destination_path": "<default_directory>/Cut - Case Study.zip"}.\n'
            "Film is experimental. Agents may write only inside the configured Output Folder, after resolving links; only its default Film subfolder is created when missing. The file must not exist and is never overwritten. Nothing is written yet: show the returned destination and contents and obtain explicit approval before export_film_case_study. Keep the recipe scene selected and the chosen scenes unchanged until building: frame changes, object selection or edits, Undo or Redo, or leaving and selecting a scene again invalidate it. Case studies are session-local.\n"
            "Platform equivalent: none; local case-study settings review."
        ),
        _schema(
            {
                "context_id": {"type": "string"},
                "production_id": {"type": "string"},
                "source_id": {"type": "string"},
                "timeline_id": {"type": "string"},
                "destination_path": {"type": "string"},
            },
            ["context_id", "production_id", "source_id", "destination_path"],
        ),
        prepare_film_case_study,
    ),
    ToolSpec(
        "export_film_case_study",
        (
            "Approve one READY Film case study and build its portable ZIP bundle in offline Blender processes.\n"
            "Args: review_id and approved_destination are required strings; approve the destination returned by prepare_film_case_study verbatim.\n"
            "Returns: case-study status with EXPORTING phase and progress; poll film_case_study_review until EXPORTED, PUBLISH_FAILED, ERROR or CANCELLED.\n"
            'Example: {"review_id": "ready-case-study", "approved_destination": "<default_directory>/Cut - Case Study.zip"}.\n'
            "Film is experimental. Requires explicit approval of the exact destination and contents, a destination still inside the Output Folder, Object Mode and the unchanged recipe scene selected. Consumes the approval even when it cannot start, including when the scene, media or ledger counts, media bytes or quoted total shown by prepare_film_case_study changed. Snapshots the chosen scenes privately; an offline child points media at the bundle's Media folder and removes Scenario identifiers and the extension's saved settings, a second child verifies the library, then the archive with the sanitized recipe, production ledger, manifest and opening guide is saved without overwriting. The working file and scenes stay unchanged. The ledger keeps quoted costs but no credentials, URLs, service identifiers or local paths. One export runs at a time; later scene edits do not stop it. No upload, generation or spending.\n"
            "Platform equivalent: none; local portable case-study export."
        ),
        _schema(
            {"review_id": {"type": "string"}, "approved_destination": {"type": "string"}},
            ["review_id", "approved_destination"],
        ),
        export_film_case_study,
    ),
    ToolSpec(
        "film_case_study_review",
        (
            "Inspect, cancel or discard a session-local Film case study, or save its verified archive to another new file after saving failed.\n"
            "Args: review_id is required; action is status (default), cancel, discard, prepare_republish (requires destination_path inside the Output Folder) or republish (requires approved_destination equal to the returned pending_destination, inside the Output Folder).\n"
            "Returns: review_id, phase, review and timeline scenes, scene, shot and media counts, ledger counts and quoted_cost_total, destination, pending_destination, progress stage/done/total and percent, published, sha256, size, republish_available, private directory and error.\n"
            'Example: {"review_id": "current-case-study", "action": "status"}.\n'
            "Film is experimental. Status advances local work but never starts a build. Cancel stops the build and leaves the destination absent. Discard deletes only the private snapshot, staged archive and logs; a saved archive stays. Republish copies the verified archive only after a failed save and never builds again; it needs its own destination approval. Handles and private files end with the session.\n"
            "Platform equivalent: none; local case-study export lifecycle."
        ),
        _schema(
            {
                "review_id": {"type": "string"},
                "action": {
                    "type": "string",
                    "enum": ["status", "cancel", "discard", "prepare_republish", "republish"],
                },
                "destination_path": {"type": "string"},
                "approved_destination": {"type": "string"},
            },
            ["review_id"],
        ),
        film_case_study_review,
    ),
    ToolSpec(
        "film_capture_sources",
        (
            "Inspect matching local scenes for one Film shot before capture.\n"
            "Args: production_id and shot_id are required strings from film_recipe.\n"
            "Returns: context_id, source_id/scene choices, editorial frames/fps and generated source timing.\n"
            'Example: {"production_id": "saved-production", "shot_id": "shot"}.\n'
            "Film is experimental. Fresh inspection replaces unprepared source handles; prepared reviews keep their identities. No rendering or upload.\n"
            "Platform equivalent: none; local Film capture inspection."
        ),
        _schema(
            {"production_id": {"type": "string"}, "shot_id": {"type": "string"}},
            ["production_id", "shot_id"],
        ),
        film_capture_sources,
        {"readOnlyHint": True},
    ),
    ToolSpec(
        "prepare_film_capture",
        (
            "Prepare a local shot capture for separate render approval.\n"
            "Args: context_id, production_id, shot_id and source_id are required; kind is VIDEO (default) or STILL, width/height default 1280/720, color_type is MATERIAL (default), TEXTURE or OBJECT.\n"
            "Returns: review_id, READY phase, exact source, dimensions, frames and fps.\n"
            'Example: {"context_id": "current-context", "production_id": "saved-production", "shot_id": "shot", "source_id": "current-source", "kind": "STILL"}.\n'
            "Film is experimental. No scene snapshot, render, upload or generation yet. VIDEO requires installed ffmpeg/ffprobe and uses the editorial range without padding; STILL uses its first frame. Ask for approval of the displayed settings before render_film_capture.\n"
            "Platform equivalent: none; local capture settings review."
        ),
        _schema(
            {
                "context_id": {"type": "string"},
                "production_id": {"type": "string"},
                "shot_id": {"type": "string"},
                "source_id": {"type": "string"},
                "kind": {"type": "string", "enum": ["STILL", "VIDEO"]},
                "width": {"type": "integer", "minimum": 64, "maximum": 4096},
                "height": {"type": "integer", "minimum": 64, "maximum": 4096},
                "color_type": {"type": "string", "enum": ["MATERIAL", "TEXTURE", "OBJECT"]},
            },
            ["context_id", "production_id", "shot_id", "source_id"],
        ),
        prepare_film_capture,
    ),
    ToolSpec(
        "render_film_capture",
        (
            "Approve a READY Film capture and start one local render on the shared workers.\n"
            "Args: review_id is required from prepare_film_capture.\n"
            "Returns: capture phase; poll film_capture_review without starting another render.\n"
            'Example: {"review_id": "approved-capture"}.\n'
            "Film is experimental. Requires explicit approval of the source and settings. The snapshot/render is local, preserves the working file and sends no bytes to Scenario. Changed scenes or credentials invalidate approval.\n"
            "Platform equivalent: none; local offline capture."
        ),
        _schema({"review_id": {"type": "string"}}, ["review_id"]),
        render_film_capture,
    ),
    ToolSpec(
        "film_capture_review",
        (
            "Inspect, cancel or discard an owner-local Film capture.\n"
            "Args: review_id is required; action is status (default), cancel or discard.\n"
            "Returns: phase, dimensions/timing, private output path/hash, error and any upload reference_id/request_id.\n"
            'Example: {"review_id": "current-capture", "action": "status"}.\n'
            "Film is experimental. Cancel stops only the local render. Explicit discard deletes its private snapshot/media/logs after active work ends, preserving saved uploads. Captures are session-local and cleaned on session shutdown; no render replays after restart. No upload or generation.\n"
            "Platform equivalent: none; local capture lifecycle."
        ),
        _schema(
            {
                "review_id": {"type": "string"},
                "action": {"type": "string", "enum": ["status", "cancel", "discard"]},
            },
            ["review_id"],
        ),
        film_capture_review,
    ),
    ToolSpec(
        "upload_film_capture",
        (
            "Upload the reviewed bytes of one completed Film capture through the shared upload runtime.\n"
            "Args: review_id is required for a CAPTURED result.\n"
            "Returns: reference_id for reference_upload_status and the original capture metadata.\n"
            'Example: {"review_id": "approved-capture"}.\n'
            "Film is experimental. Requires separate user approval of the captured output and selected connection. Staging verifies its exact content hash before any service request. Never restart an uncertain upload; inspect saved progress. Associate the imported upload with a recipe task separately using bind_film_upload. Does not generate or approve spending.\n"
            "Platform equivalent: shared uploads create/complete, followed by explicit local Film association."
        ),
        _schema({"review_id": {"type": "string"}}, ["review_id"]),
        upload_film_capture,
    ),
    ToolSpec(
        "film_timeline_sources",
        (
            "Inspect matching local scenes for every shot in the current Film recipe.\n"
            "Args: production_id is required from film_recipe.\n"
            "Returns: context_id, production_id, fps, total_frames and shots with source_id/scene choices.\n"
            'Example: {"production_id": "saved-production"}.\n'
            "Film is experimental. Choices are owner-issued live references, not scene names. Fresh inspection replaces previous choices but keeps prepared reviews. Existing scene markers identify recipe compatibility, not generation provenance; the user explicitly selects local scenes. No service call or scene mutation.\n"
            "Platform equivalent: none; local editable timeline planning."
        ),
        _schema({"production_id": {"type": "string"}}, ["production_id"]),
        film_timeline_sources,
        {"readOnlyHint": True},
    ),
    ToolSpec(
        "prepare_film_timeline",
        (
            "Prepare explicit completed shot choices for separate timeline build approval.\n"
            "Args: context_id and production_id from film_timeline_sources; selections maps every shot ID to one returned source_id.\n"
            "Returns: review_id, phase, scene, shot_count, fps, total_frames and error.\n"
            'Example: {"context_id": "current", "production_id": "saved-production", "selections": {"shot-one": "returned-source"}}.\n'
            "Film is experimental. Captures current recipe/destination and unchanged local scenes. No generation, download, render or scene build; build_film_timeline requires separate approval.\n"
            "Platform equivalent: none; local timeline review."
        ),
        _schema(
            {
                "context_id": {"type": "string"},
                "production_id": {"type": "string"},
                "selections": {"type": "object", "additionalProperties": {"type": "string"}},
            },
            ["context_id", "production_id", "selections"],
        ),
        prepare_film_timeline,
    ),
    ToolSpec(
        "film_timeline_review",
        (
            "Inspect or discard an owner-local Film timeline review.\n"
            "Args: review_id is required; action is status (default) or discard; inspected=true is required to dismiss uncertain partial cleanup.\n"
            "Returns: review_id, phase, scene, shot_count, fps, total_frames and error.\n"
            'Example: {"review_id": "returned-review", "action": "status"}.\n'
            "Film is experimental. Never builds, cleans Blender data or changes saved jobs. Handles expire when the session closes. Inspect uncertain local data before starting another review.\n"
            "Platform equivalent: none; local timeline review."
        ),
        _schema(
            {
                "review_id": {"type": "string"},
                "action": {"type": "string", "enum": ["status", "discard"]},
                "inspected": {"type": "boolean"},
            },
            ["review_id"],
        ),
        film_timeline_review,
    ),
    ToolSpec(
        "build_film_timeline",
        (
            "Approve one READY Film timeline review and create a new editable scene-strip sequence.\n"
            "Args: review_id is the required string from prepare_film_timeline.\n"
            "Returns: review_id, phase, scene, shot_count, fps, total_frames and error.\n"
            'Example: {"review_id": "returned-review"}.\n'
            "Film is experimental. Requires explicit build approval. Rechecks chosen scenes and consumes the review before mutation. Preserves the working scene and existing timelines. Strips reference live shot scenes; later edits affect the sequence. No generation, download, render, export, saved-job mutation or native operator Undo entry. Never repeat an uncertain build.\n"
            "Platform equivalent: none; local Blender timeline assembly."
        ),
        _schema({"review_id": {"type": "string"}}, ["review_id"]),
        build_film_timeline,
    ),
    ToolSpec(
        "film_shot_sources",
        (
            "Inspect eligible downloaded hero models for one shot in the current Film recipe.\n"
            "Args: production_id and shot_id are required strings from film_recipe.\n"
            "Returns: context_id, production_id, shot_id and heroes with request_id, revision and GLB asset choices.\n"
            'Example: {"production_id": "saved-production", "shot_id": "shot-one"}.\n'
            "Film is experimental. Reads the selected credential scope only. No verification, download, generation or scene build occurs.\n"
            "Platform equivalent: none; local saved Film model inspection."
        ),
        _schema(
            {"production_id": {"type": "string"}, "shot_id": {"type": "string"}},
            ["production_id", "shot_id"],
        ),
        film_shot_sources,
        {"readOnlyHint": True},
    ),
    ToolSpec(
        "prepare_film_shot",
        (
            "Verify explicit saved hero selections and prepare a shot for separate build approval.\n"
            "Args: context_id, production_id and shot_id are required strings from film_shot_sources; selections is a required object mapping every hero_id to request_id, integer revision and asset_id. Use {} for a no-hero shot.\n"
            "Returns: review_id, shot_id, phase, hero_count, scene, error and recovery flags.\n"
            'Example: {"context_id": "current", "production_id": "saved-production", "shot_id": "shot-one", "selections": {"hero": {"request_id": "saved-job", "revision": 8, "asset_id": "saved-glb"}}}.\n'
            "Film is experimental. Captures the current recipe and scene; verifies local receipts on the shared workers without building, downloading or spending. Poll film_shot_review; a READY review still needs explicit build_film_shot approval.\n"
            "Platform equivalent: none; local Film application preparation."
        ),
        _schema(
            {
                "context_id": {"type": "string"},
                "production_id": {"type": "string"},
                "shot_id": {"type": "string"},
                "selections": {
                    "type": "object",
                    "additionalProperties": {
                        "type": "object",
                        "properties": {
                            "request_id": {"type": "string"},
                            "revision": {"type": "integer", "minimum": 0},
                            "asset_id": {"type": "string"},
                        },
                        "required": ["request_id", "revision", "asset_id"],
                        "additionalProperties": False,
                    },
                },
            },
            ["context_id", "production_id", "shot_id", "selections"],
        ),
        prepare_film_shot,
    ),
    ToolSpec(
        "film_shot_review",
        (
            "Inspect or discard a Film shot review, or retry only its known persistence receipts.\n"
            "Args: review_id is required; action is status (default), discard, retry_receipts or dismiss_uncertain. Dismissal requires inspected=true.\n"
            "Returns: review_id, shot_id, phase, hero_count, scene, error, receipt_retry_available and inspection_required.\n"
            'Example: {"review_id": "returned-review", "action": "status"}.\n'
            "Film is experimental. Status advances pending local verification but never builds. Discard retires unapproved work. Receipt retry never calls the builder. After inspecting the scene and saved jobs, dismissal retires an uncertain review only if no known receipts remain; it never clears durable claims or repeats a build. Review handles do not survive a changed connection or restart.\n"
            "Platform equivalent: none; local review and receipt recovery."
        ),
        _schema(
            {
                "review_id": {"type": "string"},
                "action": {
                    "type": "string",
                    "enum": ["status", "discard", "retry_receipts", "dismiss_uncertain"],
                },
                "inspected": {"type": "boolean"},
            },
            ["review_id"],
        ),
        film_shot_review,
    ),
    ToolSpec(
        "build_film_shot",
        (
            "Approve one READY Film review and build a new shot scene from its verified saved models.\n"
            "Args: review_id is the required string from prepare_film_shot.\n"
            "Returns: review_id, shot_id, phase, hero_count, scene, error and recovery flags.\n"
            'Example: {"review_id": "returned-review"}.\n'
            "Film is experimental. Requires explicit scene-build approval. Rechecks the original recipe/destination, consumes the review and claims every source before mutation. Keeps the working scene selected. No generation or download occurs. Never repeat an uncertain build; use receipt-only recovery when offered.\n"
            "Platform equivalent: none; local Blender shot construction."
        ),
        _schema({"review_id": {"type": "string"}}, ["review_id"]),
        build_film_shot,
    ),
    ToolSpec(
        "film_recipe",
        (
            "Inspect or load the current scene's Film recipe, or explicitly start a new production.\n"
            "Args: action is inspect (default), load or new_production; recipe is a raw Film JSON object required for load.\n"
            "Returns: stable production_id, title, tasks, shots and warnings; inspection adds context_id and saved job/upload identities and states, including declared master jobs. A quoted task includes quote_id, model_id, parameters and cu_cost_exact for its existing approval. warnings is empty unless audio_tracks is omitted and no task is named score; it then says final review and composition with the default score_task_id will refuse the recipe. Preparation checks the score_task_id it receives.\n"
            'Example: {"action": "inspect"}.\n'
            "Film is experimental. Load validates before mutation and preserves identity. Save the blend file to retain it. New production deliberately gives the same task names a fresh identity; it does not submit or recover work.\n"
            "After a scene-switch error, return to the original scene and inspect to recover an unchanged quote or saved upload association. Inspection only completes already-admitted preparation; it never reprices, resumes saved jobs or submits. Stale quotes are omitted.\n"
            "Platform equivalent: none; local Film recipe and saved-task inspection."
        ),
        _schema(
            {
                "action": {"type": "string", "enum": ["inspect", "load", "new_production"]},
                "recipe": {"type": "object"},
            }
        ),
        film_recipe,
    ),
    ToolSpec(
        "estimate_film_task",
        (
            "Request the exact server price for one model task in the loaded Film recipe.\n"
            "Args: production_id and task_id are required strings from film_recipe.\n"
            "Returns: quote_id, production_id, task_id, model_id, resolved parameters and cu_cost_exact.\n"
            'Example: {"production_id": "saved-production", "task_id": "shot-one"}.\n'
            "Film is experimental. Uses saved scoped dependencies and the shared SDK model quote; never submits. Existing task identities cannot be spent again. Discard an unused quote before repricing.\n"
            "Platform equivalent: estimate_cost for the recipe model's resolved inputs."
        ),
        _schema(
            {"production_id": {"type": "string"}, "task_id": {"type": "string"}},
            ["production_id", "task_id"],
        ),
        estimate_film_task,
        {"readOnlyHint": True},
    ),
    ToolSpec(
        "approve_film_task",
        (
            "Approve one unchanged Film estimate and save its identity before one paid submission.\n"
            "Args: quote_id and approved_cost are required strings; approve the returned cu_cost_exact verbatim.\n"
            "Returns: request_id/local_id, state and recovery note.\n"
            'Example: {"quote_id": "returned-quote", "approved_cost": "0.10000000000000001"}.\n'
            "Film is experimental. Requires explicit spending approval. Results download through the shared model lifecycle and remain saved for explicit application. Never repeat an uncertain submission.\n"
            "Platform equivalent: generate for the approved Film model task."
        ),
        _schema(
            {"quote_id": {"type": "string"}, "approved_cost": {"type": "string"}},
            ["quote_id", "approved_cost"],
        ),
        approve_film_task,
    ),
    ToolSpec(
        "discard_film_estimate",
        (
            "Release one unsubmitted Film estimate so its task can be repriced.\n"
            "Args: quote_id is the required string from estimate_film_task.\n"
            "Returns: discarded.\n"
            'Example: {"quote_id": "returned-quote"}.\n'
            "Film is experimental. Does not cancel or change saved jobs.\n"
            "Platform equivalent: none; local approval handle."
        ),
        _schema({"quote_id": {"type": "string"}}, ["quote_id"]),
        discard_film_estimate,
    ),
    ToolSpec(
        "bind_film_upload",
        (
            "Associate one imported saved upload with a Film upload task without sending bytes.\n"
            "Args: production_id/task_id identify the loaded Film task; context_id, request_id and integer expected_revision come from list_reference_uploads. All required.\n"
            "Returns: production_id, task_id, upload_request_id and bound state.\n"
            'Example: {"production_id": "saved-production", "task_id": "reference", "context_id": "current", "request_id": "upload", "expected_revision": 4}.\n'
            "Film is experimental. Requires an unchanged imported upload in the selected scope. The association is durable and immutable; choose a new task name for a different source.\n"
            "Platform equivalent: none; local reference to an already imported asset."
        ),
        _schema(
            {
                "production_id": {"type": "string"},
                "task_id": {"type": "string"},
                "context_id": {"type": "string"},
                "request_id": {"type": "string"},
                "expected_revision": {"type": "integer", "minimum": 0},
            },
            ["production_id", "task_id", "context_id", "request_id", "expected_revision"],
        ),
        bind_film_upload,
    ),
    ToolSpec(
        "upload_reference",
        (
            "Upload an explicitly chosen image, audio, video or 3D file through the shared durable upload session.\n"
            "Args:\n  - path: required string, local file path selected by the user.\n"
            "  - kind: optional string, image (default), audio, video or 3d; must match the file extension.\n"
            "Returns: context_id, reference_id, staging/upload state, request_id when persisted, kind and content_type after staging, and note.\n"
            'Example: {"path": "/chosen/reference.png"}.\n'
            "Only two kinds of file are accepted: one the extension saved in its own folders (such as a downloaded result), or one the user chose in Blender during this session (a form's Upload button, the Render Video first-frame field or a workflow File field). Any other path, including the Output Folder, is refused with UploadRefused before anything is read or sent; ask the user to choose the file in Blender, or use capture_reference for the scene. Links are followed, so a link to an outside file is refused too.\n"
            "This sends the selected file to Scenario without conversion or sidecar discovery. Call only for an authorized upload; it does not generate or approve spending. Poll reference_upload_status until imported, then pass asset_id in estimate_cost or estimate_workflow parameters. Do not repeat an uncertain upload.\n"
            "Platform equivalent: upload_asset then upload_asset_complete."
        ),
        _schema(
            {
                "path": {"type": "string"},
                "kind": {
                    "type": "string",
                    "enum": ["image", "audio", "video", "3d"],
                    "default": "image",
                },
            },
            ["path"],
        ),
        upload_reference,
    ),
    ToolSpec(
        "reference_upload_status",
        (
            "Read a reference upload's progress while the shared session advances its already authorized work.\n"
            "Args:\n  - context_id: required string, context from upload_reference or capture_reference.\n"
            "  - reference_id: required string, the returned session-owned upload handle.\n"
            "Returns: reference_id, request_id, revision, state, asset_id, kind, content_type, error and pending. Kind and content_type are null until staging completes.\n"
            'Example: {"context_id": "from-upload", "reference_id": "from-upload"}.\n'
            "Use asset_id only after state imported. A changed context rejects old handles; use list_reference_uploads after restart. This does not create or replay uploads.\n"
            "Platform equivalent: upload status retrieval."
        ),
        _schema(
            {"context_id": {"type": "string"}, "reference_id": {"type": "string"}},
            ["context_id", "reference_id"],
        ),
        reference_upload_status,
        {"readOnlyHint": True},
    ),
    ToolSpec(
        "list_reference_uploads",
        (
            "Inspect saved uploads under the selected credential scope, including after restart.\n"
            "Args: none.\n"
            "Returns: context_id and uploads with request_id, revision, state, suggested action, upload_id, asset_id, kind and content_type.\n"
            "Example: {}.\n"
            "Inspection makes no network request, sends no bytes and never resumes uncertain initialization or parts. Action restart_upload marks an unfinished upload this session cannot continue. Use recover_reference_upload for explicit known-upload reads, restarts or local cleanup.\n"
            "Platform equivalent: none; this inspects local durable upload history."
        ),
        _schema({}),
        list_reference_uploads,
        {"readOnlyHint": True},
    ),
    ToolSpec(
        "recover_reference_upload",
        (
            "Explicitly inspect a known remote upload, cancel unclaimed preparation, restart an unfinished upload, or clean its finished private source copy.\n"
            "Args:\n  - context_id: required string, context from list_reference_uploads.\n"
            "  - request_id: required string, saved local upload identity.\n"
            "  - expected_revision: required nonnegative integer, observed saved revision.\n"
            "  - action: required string, refresh, cancel_prepared, restart or cleanup.\n"
            "Returns: request_id, state, revision, asset_id, kind and content_type; restart also returns reference_id and replacement_request_id.\n"
            'Example: {"context_id": "from-list", "request_id": "from-list", "expected_revision": 2, "action": "refresh"}.\n'
            "Refresh sends only a status read. Restart is for an uploading or part-uncertain upload whose suggested action is restart_upload: Scenario issues part destinations only once, so it marks that upload abandoned (never completed) and uploads the verified saved copy as a new request; poll reference_upload_status with the returned reference_id. Cleanup accepts only finished records and never deletes the original user file. No action resends a part to the same upload or repeats initialization or finalization.\n"
            "Platform equivalent: upload retrieval, a new upload of the same bytes, or local source cleanup."
        ),
        _schema(
            {
                "context_id": {"type": "string"},
                "request_id": {"type": "string"},
                "expected_revision": {"type": "integer", "minimum": 0},
                "action": {
                    "type": "string",
                    "enum": ["refresh", "cancel_prepared", "restart", "cleanup"],
                },
            },
            ["context_id", "request_id", "expected_revision", "action"],
        ),
        recover_reference_upload,
        {"destructiveHint": True},
    ),
    ToolSpec(
        "prepare_result_application",
        (
            "Prepare explicit saved image/media/model import, material assignment, panorama World replacement, session-local World restoration, or a Render Video first frame from a saved image.\n"
            "Args:\n"
            "  - context_id: required string, current context from list_local_jobs.\n"
            "  - request_id: required string, saved local job identity.\n"
            "  - expected_revision: required nonnegative integer, observed saved revision.\n"
            "  - purpose: import (default), material, world, restore_world, mesh_edit, mesh_source or video_first_frame; material uses the active mesh and saved unambiguous texture roles; World replacement requires asset_id; a material map, or an image whose downloaded file header declares a size other than 2:1, is refused, and job_status omits apply_world when no other image remains. video_first_frame binds one downloaded PNG, JPEG or WebP colour result (job_status action use_first_frame) to the current scene's Render Video first frame by reusing its saved asset ID; it requires asset_id, a loaded Render Video model with an image input and no existing first-frame slot. The frame goes where render_form first_frame_route says.\n"
            "  - asset_id: optional saved asset ID; required for one MP4/WebM video, MP3/WAV/OGG audio strip or video_first_frame image, or the key of one importable unit from job_status import_units for a GLB, glTF, FBX, OBJ or PLY model or SPZ or .splat Gaussian splats. Omit for PNG/EXR image import.\n"
            "  - splat_max_points: optional integer 250000, 500000, 1000000 (default) or 2000000 for an SPZ, PLY or .splat unit; denser files are thinned evenly.\n"
            "  - splat_axes: optional OPENGL (saved right/up/back) or OPENCV (saved right/down/forward) for an SPZ, PLY or .splat unit; the default is the file format's convention (OPENGL for SPZ, OPENCV for PLY and .splat), which providers may not follow.\n"
            "  - mesh_policy: REMESH (default) replaces geometry/UV/materials; UV replaces only active UVs; RETEXTURE preserves geometry/non-UV attributes and replaces all UV layers/materials. UV and RETEXTURE require exact topology/position matching. PARTS replaces source geometry with an empty mesh parent and 2 to 128 named parts from the selected static GLB; every mesh is a part, not an alternate variant. RIG preserves source geometry, UVs and materials and attaches matching weights and one rig with clips; it requires exact indexed geometry and rejects morphs/mesh animation.\n"
            "  - mesh_placement: WORLD (default) preserves imported scene positions; LOCAL uses imported positions in the object's local coordinates. No fitting is inferred.\n"
            "  - keep_original: boolean, default true; preserve an unselected original mesh copy. These mesh options apply to mesh_edit and mesh_source, which require asset_id. mesh_source requires exactly one captured input and its unchanged live export source; it ignores current selection and cannot restore authority after undo/load/restart.\n"
            "Returns: context_id, application_id, request_id, revision, reuse, scene, images or asset_id/kind/frame, model format/primary/members/resources, cursor and splat_options (max_points and axes, null for mesh formats), or World format, and note. video_first_frame returns kind, lane, model_id, input, input_label, sent_as, reason, asset_id, file, replaces_first_frame_path, enables_first_frame and mode=reuse_asset instead of reuse; sent_as=reference_image means the image becomes image 1 of input, and note says why.\n"
            'Example: {"context_id": "from-list", "request_id": "from-list", "expected_revision": 8}.\n'
            "Show the destination, selected assets and media frame, model format, files, cursor and splat options, material target/slot/roles, World operation/format, mesh target/policy/placement/Keep original, or first-frame model/input, sent_as with its note and replaced file before apply_result_application. A model import reads only the listed members and, for glTF, the listed resources its references name; nothing is imported automatically and a model that is not primary is an alternate result. Splats are shown as a coloured point cloud, not rendered as Gaussian splats; to change splat options, prepare again and show the new approval. This makes no network request, spends no credits and imports nothing. Ready or confirmed rolled-back results use their original application claim. Completed results require a new local reuse approval; show reuse=true as another application, never another generation. Unfinished reuse blocks another attempt. Restoration applies to this session's most recent World assignment for this job.\n"
            "Platform equivalent: none; this captures a local Blender destination."
        ),
        _schema(
            {
                "context_id": {"type": "string"},
                "request_id": {"type": "string"},
                "expected_revision": {"type": "integer", "minimum": 0},
                "asset_id": {"type": "string"},
                "purpose": {
                    "type": "string",
                    "enum": [
                        "import",
                        "material",
                        "world",
                        "restore_world",
                        "mesh_edit",
                        "mesh_source",
                        "video_first_frame",
                    ],
                },
                "mesh_policy": {
                    "type": "string",
                    "enum": ["REMESH", "UV", "RETEXTURE", "PARTS", "RIG"],
                },
                "mesh_placement": {"type": "string", "enum": ["WORLD", "LOCAL"]},
                "keep_original": {"type": "boolean"},
                "splat_max_points": {
                    "type": "integer",
                    "enum": [250000, 500000, 1000000, 2000000],
                },
                "splat_axes": {"type": "string", "enum": ["OPENGL", "OPENCV"]},
            },
            ["context_id", "request_id", "expected_revision"],
        ),
        prepare_result_application,
        {"readOnlyHint": True},
    ),
    ToolSpec(
        "apply_result_application",
        (
            "Apply or restore saved results after the user approves the prepared destination and operation.\n"
            "Args:\n"
            "  - context_id: required string, context from prepare_result_application.\n"
            "  - application_id: required string, single-use approval handle from prepare_result_application.\n"
            "Returns: saved job status, revision, images, imported object names, materials, model_import and any delivery error.\n"
            'Example: {"context_id": "from-prepare", "application_id": "from-prepare"}.\n'
            "Call only after explicit destination approval. Verification runs off the main thread; application rechecks the captured scene/file revision and media frame or model cursor. Media uses a persistent private file; video omits embedded audio and scene timing is unchanged. Changed contexts or records require fresh review. World replacement or restoration changes only the approved scene World with guarded owned-data cleanup. Material assignment changes only the approved mesh slot to a new packed material; it preserves old materials and other slots. Mesh edit replaces only the captured target under its prepared policy, placement and Keep original choice. A video first frame rehashes the saved image against its receipt, then binds its saved asset ID to the unchanged Render Video form without an upload, a local path or a saved-job change; status first_frame reports bound or failed, and the next quote needs a fresh estimate_cost. A model import rehashes every member into a private copy before its claim: a changed or unsupported package leaves the saved job unchanged (ready, or applied for a reuse) with an error starting with Nothing was imported; a reuse claim of a completed job names every member read. SPZ, PLY and .splat units are first read on a worker in the single local-operation slot: splats are decoded there with the approved splat_options and become a new point cloud, a PLY without splat data continues as a mesh, and a changed or undecodable file stops the same way. While a local render or Film media inspection holds that slot, SPZ and .splat imports are refused before anything is queued, and a PLY is only verified: a mesh imports and splat data stops the same way. This performs no generation, downloads or file save. Never repeat an uncertain import; inspect the saved job.\n"
            "Platform equivalent: none; this applies saved results locally in Blender."
        ),
        _schema(
            {"context_id": {"type": "string"}, "application_id": {"type": "string"}},
            ["context_id", "application_id"],
        ),
        apply_result_application,
        {"destructiveHint": True},
    ),
    ToolSpec(
        "recover_local_job",
        (
            "Explicitly recover a saved job without repeating generation or importing into another scene.\n"
            "Args:\n"
            "  - context_id: required string from list_local_jobs.\n"
            "  - request_id: required local job identity.\n"
            "  - expected_revision: required observed integer revision.\n"
            "  - action: refresh, resume, cancel, recover_download or retry_receipt.\n"
            "Returns: saved status, revision, available actions and delivery error if any.\n"
            'Example: {"context_id":"from-list","request_id":"from-list","expected_revision":2,"action":"resume"}.\n'
            "Refresh reads once; resume polls and downloads without automatic import. Cancel is listed only after a refresh confirms a running inference job, the only job type Scenario documents as cancellable; it requests cancellation once and observes the actual outcome. Other jobs are refused without a request and keep running. A running workflow job cannot be canceled: cancel returns an explanation and sends nothing; with the user's approval, reject a waiting step through decide_workflow_step instead. recover_download verifies interrupted local receipts without network calls. retry_receipt saves an already completed import without repeating it and requires the same live owner. Stale contexts/revisions and uncertain submissions are rejected.\n"
            "Platform equivalent: jobs.retrieve, jobs.trigger_action and assets.retrieve through the shared SDK, plus local receipt recovery."
        ),
        _schema(
            {
                "context_id": {"type": "string"},
                "request_id": {"type": "string"},
                "expected_revision": {"type": "integer", "minimum": 0},
                "action": {
                    "type": "string",
                    "enum": ["refresh", "resume", "cancel", "recover_download", "retry_receipt"],
                },
            },
            ["context_id", "request_id", "expected_revision", "action"],
        ),
        recover_local_job,
        {"destructiveHint": True},
    ),
    ToolSpec(
        "list_local_jobs",
        (
            "Inspect durable local jobs for the selected API-key pair without network requests.\n"
            "Args: none.\n"
            "Returns: context_id and jobs[] with request_id, revision, state, action, operation, target_id, cu_cost_exact and remote_job_id.\n"
            "Example: {}.\n"
            "Saved costs are informational, not spending approval. Unknown submissions require reconciliation, never automatic retry.\n"
            "Prototype generate/job_status records are separate and are not imported here. No remote job polling or result application occurs.\n"
            "Platform equivalent: none; this is local recovery inspection."
        ),
        _schema({}),
        list_local_jobs,
        {"readOnlyHint": True},
    ),
    ToolSpec(
        "cancel_prepared_job",
        (
            "Cancel an unsubmitted durable local intent without contacting Scenario.\n"
            "Args:\n"
            "  - context_id: required string, current context returned by list_local_jobs.\n"
            "  - request_id: required string, local durable request identity.\n"
            "  - expected_revision: required nonnegative integer, observed record revision.\n"
            "Returns: request_id, revision and state (canceled).\n"
            'Example: {"context_id": "from-list", "request_id": "from-list", "expected_revision": 0}.\n'
            "Only prepared jobs can be canceled here. Claimed, uncertain, remote and changed-context jobs are rejected; this does not cancel a remote generation.\n"
            "Platform equivalent: none; this changes only local durable state."
        ),
        _schema(
            {
                "context_id": {"type": "string"},
                "request_id": {"type": "string"},
                "expected_revision": {"type": "integer", "minimum": 0},
            },
            ["context_id", "request_id", "expected_revision"],
        ),
        cancel_prepared_job,
        {"destructiveHint": True},
    ),
    ToolSpec(
        "estimate_blockout",
        'Get the exact free estimate for the current scene\'s Blockout description or refinement. This reads the native Blockout fields and current plan; it does not generate or build geometry. Approve the exact returned cu_cost_exact separately. Args: action DESIGN or REFINE. Returns: quote_id, action, cu_cost_exact.\nExample: {"action": "DESIGN"}.\nPlatform equivalent: model_estimate for the Scenario LLM.',
        _schema({"action": {"type": "string", "enum": ["DESIGN", "REFINE"]}}, []),
        estimate_blockout,
        {"readOnlyHint": True},
    ),
    ToolSpec(
        "approve_blockout",
        'Spend the explicitly approved exact cost once for estimate_blockout. Args: quote_id and approved_cost. Inputs, current plan and origin must still match. Returns: request_id and state. The unchanged source scene receives a complete plan; no geometry is built automatically. Inspect list_local_jobs after failure or restart; never repeat an uncertain submission.\nExample: {"quote_id": "saved-quote", "approved_cost": "1.25"}.\nPlatform equivalent: model_generate after exact local approval.',
        _schema(
            {"quote_id": {"type": "string"}, "approved_cost": {"type": "string"}},
            ["quote_id", "approved_cost"],
        ),
        approve_blockout,
    ),
    ToolSpec(
        "prepare_blockout_plan",
        'Read and validate one saved Scenario LLM plan for the current scene without generating or building geometry. Args: context_id, request_id, expected_revision from list_local_jobs. Returns: review_id, state, scene, elements, groups, replaces_plan, error. Poll blockout_plan_status until ready, then review the destination and replacement before apply_blockout_plan.\nExample: {"context_id": "current-context", "request_id": "saved-request", "expected_revision": 3}.\nPlatform equivalent: job_get and asset_get, followed by local destination review.',
        _schema(
            {
                "context_id": {"type": "string"},
                "request_id": {"type": "string"},
                "expected_revision": {"type": "integer", "minimum": 0},
            },
            ["context_id", "request_id", "expected_revision"],
        ),
        prepare_blockout_plan,
        {"readOnlyHint": True},
    ),
    ToolSpec(
        "blockout_plan_status",
        'Inspect a prepared saved-plan review or discard its finished approval handle. Args: context_id, review_id, optional discard. Returns: state, scene, elements, groups, replaces_plan, error; discarded state when requested. Never generates or changes a scene.\nExample: {"context_id": "current-context", "review_id": "saved-review"}.\nPlatform equivalent: local saved-result review.',
        _schema(
            {
                "context_id": {"type": "string"},
                "review_id": {"type": "string"},
                "discard": {"type": "boolean"},
            },
            ["context_id", "review_id"],
        ),
        blockout_plan_status,
        {"readOnlyHint": True},
    ),
    ToolSpec(
        "apply_blockout_plan",
        'Use a ready saved-plan review once, after explicit destination and replacement approval. Args: context_id, review_id from prepare_blockout_plan. Returns: scene, elements, geometry_changed=false. Replaces only the unchanged destination scene stored Blockout plan; use native Build plan separately. Never spends or builds geometry.\nExample: {"context_id": "current-context", "review_id": "saved-review"}.\nPlatform equivalent: local plan application.',
        _schema(
            {"context_id": {"type": "string"}, "review_id": {"type": "string"}},
            ["context_id", "review_id"],
        ),
        apply_blockout_plan,
        {"destructiveHint": True},
    ),
    ToolSpec(
        "read_model_text",
        'Read one explicitly selected complete text asset from a successful saved model job, including after restart. Obtain context_id and revision from list_local_jobs and asset_id from job_status results. Returns: request_id, asset_id and bounded full text. Never spends, parses a plan, applies to the scene or substitutes a truncated preview. Args: context_id, request_id, expected_revision, asset_id.\nExample: {"context_id": "current-context", "request_id": "saved-request", "expected_revision": 3, "asset_id": "asset_text"}.\nPlatform equivalent: job_get and asset_get without generation.',
        _schema(
            {
                "context_id": {"type": "string"},
                "request_id": {"type": "string"},
                "expected_revision": {"type": "integer", "minimum": 0},
                "asset_id": {"type": "string"},
            },
            ["context_id", "request_id", "expected_revision", "asset_id"],
        ),
        read_model_text,
        {"readOnlyHint": True},
    ),
    ToolSpec(
        "inspect_result_previews",
        (
            "Inspect the previews of a downloaded saved job's results, as the native preview tiles and Waveform control show them, without generating, spending, applying or changing the scene or saved job.\n"
            "Args:\n"
            "  - context_id, request_id, expected_revision: required, from list_local_jobs; the job must be ready, applying, apply_failed or applied.\n"
            "  - asset_id: optional, one result from job_status results. Without it, every downloaded result with a preview kind, in the order the native tiles page through four at a time: video, audio, embedded models and base color or untagged images first.\n"
            "  - envelope: optional boolean, default false. Also decode this audio result's waveform envelope from its verified saved file in an offline Blender process, like Waveform; requires asset_id.\n"
            "  - action: inspect (default), retry (prepare asset_id's previews again and clear a missing marker, like Retry preview) or cancel (stop asset_id's local decoding, like the waveform's Cancel).\n"
            "  - bins: optional integer from 8 to 256, default 64, the envelope levels returned.\n"
            "  - wait: optional seconds from 0 to 60, default 0. Waits off Blender's main thread until the requested previews, the envelope when envelope is true and otherwise the stills, are no longer queued, pending or decoding; in_progress still reports any returned rendition in progress.\n"
            "Returns: context_id, request_id, revision, in_progress and results[] with asset_id, number, caption, media_type, kind and renditions. Each rendition has state (queued, pending, offline, decode, ready, missing, failed or unsupported) and reason when set. A still of a result with a preview kind adds source (local or scenario); a ready one adds media_type, width, height and size. A ready envelope adds seconds, sample_rate, channels, silent, overall_rms, overall_peak, bins and rms and peaks levels from 0 to 1. Never returns image bytes, file paths or URLs.\n"
            "Video, 3D and audio stills are Scenario's preview images, read while Online Access allows (offline otherwise; nothing is sent) and polled for about five minutes when missing. Image stills and envelopes use only verified saved files; only an interactive Blender decodes image stills. No tool launches a player.\n"
            'Example: {"context_id": "current-context", "request_id": "saved-request", "expected_revision": 4, "asset_id": "asset_audio", "envelope": true, "wait": 10}.\n'
            "Platform equivalent: asset_get and asset_display for Scenario's preview images; envelopes are local."
        ),
        _schema(
            {
                "context_id": {"type": "string"},
                "request_id": {"type": "string"},
                "expected_revision": {"type": "integer", "minimum": 0},
                "asset_id": {"type": "string"},
                "envelope": {"type": "boolean"},
                "action": {"type": "string", "enum": ["inspect", "retry", "cancel"]},
                "bins": {"type": "integer", "minimum": 8, "maximum": 256},
                "wait": {"type": "number", "minimum": 0, "maximum": 60},
            },
            ["context_id", "request_id", "expected_revision"],
        ),
        inspect_result_previews,
        {"readOnlyHint": True},
    ),
    ToolSpec(
        "estimate_prompt",
        'Get the exact server price for New, Rewrite or Translate on the current scene\'s native prompt field. This does not generate or change text. Args: lane and action (GENERATE, REWRITE, TRANSLATE). Returns: quote_id and cu_cost_exact. Obtain explicit approval of that exact cost before approve_prompt. The current field text/model must remain unchanged.\nExample: {"lane": "image", "action": "REWRITE"}.\nPlatform equivalent: prompt_spark for prompt generation; translation uses SDK generate.translate.',
        _schema(
            {
                # Only native lanes have a prompt field; MCP-only lanes have none.
                "lane": {"type": "string", "enum": list(NATIVE_LANES)},
                "action": {"type": "string", "enum": ["GENERATE", "REWRITE", "TRANSLATE"]},
            },
            ["action"],
        ),
        _after_description(estimate_prompt),
        {"readOnlyHint": True},
    ),
    ToolSpec(
        "approve_prompt",
        'Spend the explicitly approved exact price once for a quote from estimate_prompt. Args: quote_id and approved_cost (the unchanged decimal string). Returns: request_id and queued state. Queues one durable submission; never retry an uncertain outcome. The shared runtime updates only the unchanged original prompt field. Inspect list_local_jobs for recovery.\nExample: {"quote_id": "saved-quote", "approved_cost": "1.25"}.\nPlatform equivalent: prompt_spark after separate local approval.',
        _schema(
            {"quote_id": {"type": "string"}, "approved_cost": {"type": "string"}},
            ["quote_id", "approved_cost"],
        ),
        approve_prompt,
        {"destructiveHint": True},
    ),
    ToolSpec(
        "read_prompt_result",
        'Read full text from a saved successful prompt or translation job without generating, spending or applying it. Args: context_id, request_id and expected_revision from list_local_jobs; refresh a known remote job\'s status first if necessary. Returns: prompts; old scene origins are readable but are never silently applied to the current scene.\nExample: {"context_id": "current-context", "request_id": "saved-request", "expected_revision": 3}.\nPlatform equivalent: job_get and asset_get without generation.',
        _schema(
            {
                "context_id": {"type": "string"},
                "request_id": {"type": "string"},
                "expected_revision": {"type": "integer", "minimum": 0},
            },
            ["context_id", "request_id", "expected_revision"],
        ),
        read_prompt_result,
        {"readOnlyHint": True},
    ),
    ToolSpec(
        "list_models",
        (
            "List the loaded lane catalog, with curated models first and at most 40 matches, or the trained LoRAs and compositions the native picker offers.\n"
            "Args:\n"
            "  - lane: optional string, default image; image, video, 3d, material, audio, render_image, render_video, edit3d or panorama.\n"
            "  - query: optional string, substring of the model name, description or id.\n"
            "  - source: optional string, catalog (default), trained or all. trained and all need lane image or render_image.\n"
            "Returns: lane, models[] with id, name, description, capabilities and capability_status (empty, or the experimental note the picker shows). Retry after catalog loading completes. Lane panorama (Environment) lists only models with a verified 360 panorama contract; each entry adds contract with mode (GENERATE, FROM_IMAGE, HDRI or UPSCALE), output (ldr or hdr), description, references (parameter, kind still or panorama, required, max), sizes, locked, defaults and factor_parameter. trained and all also return trained[] (at most 40, available first, always including the rows the lane's saved default uses) with id, name, kind (lora or composition, or unsupported), type, private, available, reason when unavailable, routes[] (route stack or composition, model_id and name of the base model to run it with, model_input, and for stack scale_input, scale_range, scale_step, start_scale (where the native picker starts this LoRA's strength: its own declared strength, otherwise 0.75, within scale_range), default_scale (the base's declared default for an omitted strength), min_items and max_items), concepts for a composition and default when the lane's saved default uses it; bases reports checked/total/pending/error of base description reads and note asks to call again while they run.\n"
            'Example: {"lane": "image", "source": "trained"}.\n'
            "Prefer model_schema before choosing generation parameters; this is not the full platform catalog. A trained model runs only through a listed base: pass that base as model_id with the LoRA IDs and one strength each in its model_input/scale_input (start_scale sends what a picker choice sends; or the composition in its model_input) to estimate_cost. Trained listing reads the selected credentials and project's trained list and base model descriptions, like the picker's Trained chip; it never trains or spends.\n"
            "Platform equivalent: models_list, recommend."
        ),
        _schema(
            {
                "lane": {"type": "string", "enum": list(LANES)},
                "query": {"type": "string"},
                "source": {"type": "string", "enum": ["catalog", "trained", "all"]},
            }
        ),
        list_models,
        {"readOnlyHint": True},
    ),
    ToolSpec(
        "model_schema",
        (
            "Read the model's current form parameters for this Blender extension.\n"
            "Args:\n"
            "  - model_id: required string, the exact model identifier from list_models. A LoRA or composition has no inputs of its own: it is refused with how to run it through a base from list_models(source=trained) routes.\n"
            "Returns: model_id, name, prompt_parameter, parameters[] with name, type, label, required, default, allowed_values, range (per item for number arrays), cost_impact, file_kind, model_types, min_items/max_items for arrays and role (lora_models, lora_strengths or composition) when present, and lora_component: the base's LoRA inputs (model_input, scale_input, model_id_input, model_types, model_id_types, min_items, max_items, scale_range, scale_step, scale_default) or null; lora_component_error explains a malformed one. File parameters take Scenario asset ids.\n"
            'Example: {"model_id": "model_example"}.\n'
            "Prefer this before estimate_cost; do not guess parameter names or supported inputs. No LoRA strength is invented: pass one per LoRA within scale_range, at most 4 decimals, as the native strength fields send. "
            "A first call for a model whose description is not loaded yet waits up to 30 seconds for it; if it is still loading, call again.\n"
            "Platform equivalent: model_schema_get."
        ),
        _schema({"model_id": {"type": "string"}}, ["model_id"]),
        _after_description(model_schema),
        {"readOnlyHint": True},
    ),
    ToolSpec(
        "model_defaults",
        (
            "Read, save or clear the selected credentials and project's trained-model default of the Image or Render Image form, without spending.\n"
            "Args:\n"
            "  - action: optional string, get (default), set or clear.\n"
            "  - lane: image or render_image; required for set and clear, get without it returns both.\n"
            "  - context_id and expected_revision: required for set and clear, from the latest get of that lane (revision 0 when none was ever saved).\n"
            "  - base_model_id: required for set, a base from list_models(source=trained) routes.\n"
            "  - picks: required for set, [{model_id, scale}] with one strength per LoRA (at most 4 decimals; omitted, it starts where the native picker starts it) or one composition without scale.\n"
            "Returns: context_id and lanes[] with lane, revision, default (route, base_model_id, picks) or null, label, available, then reason or waiting, or parameters: the base's LoRA inputs to add to estimate_cost parameters when it runs now; error reports an unreadable stored default, which is kept.\n"
            'Example: {"action": "set", "lane": "image", "context_id": "from-get", "expected_revision": 0, "base_model_id": "model_example", "picks": [{"model_id": "model_lora", "scale": 0.8}]}.\n'
            "set refuses a choice that cannot run in that form now, as the native Save does; a stale revision or a context_id from other credentials or another project is refused. A default is not spending authority: estimate_cost and render quotes price the exact payload again. A form with no explicit choice takes it, as in the native UI; the image lane's estimate_cost uses only the parameters given.\n"
            "Platform equivalent: none; local per-project defaults."
        ),
        _schema(
            {
                "action": {"type": "string", "enum": ["get", "set", "clear"]},
                "lane": {"type": "string", "enum": ["image", "render_image"]},
                "context_id": {"type": "string"},
                "expected_revision": {"type": "integer", "minimum": 0},
                "base_model_id": {"type": "string"},
                "picks": {
                    "type": "array",
                    "maxItems": 16,
                    "items": {
                        "type": "object",
                        "properties": {
                            "model_id": {"type": "string"},
                            "scale": {"type": "number"},
                        },
                        "required": ["model_id"],
                        "additionalProperties": False,
                    },
                },
            }
        ),
        model_defaults,
    ),
    ToolSpec(
        "render_form",
        (
            "Inspect or prepare the native Render Image/Video form without submitting generation.\n"
            "Args:\n"
            "  - lane: required render_image or render_video.\n"
            "  - action: inspect (default), configure, prepare or remove.\n"
            "  - settings: configure edits model_id, look, capture_source (CAMERA/VIEWPORT), force_solid, spark_enabled, first_frame_path, use_first_frame, match_timeline, scalar parameters, style_assets (replaces unmarked styles) and, for render_image, trained: [{model_id, scale}] replacing the form's LoRAs (one strength each, at most 4 decimals; an omitted one starts where the native picker starts it) or composition, checked like a picker choice ([] removes them). Optional parameters accept null to disable them. Choose a model from list_models. Remove references before changing models.\n"
            "  - role: prepare explicitly captures/uploads scene or uploads the selected first_frame file using the shared reference lifecycle. Repeated preparation refuses an occupied slot. The first_frame file follows upload_reference's rule: one the extension saved or one the user chose in Blender this session; a first_frame_path set through configure is otherwise refused before anything is read or sent.\n"
            "  - reference_key: remove requires the exact key from a fresh inspection; detaches only that reference and does not cancel its saved upload. Inspect uncertain uploads before preparing another.\n"
            "Returns: current settings, trained_models (LoRAs or a composition chosen in the native picker or with trained, with kind and strength; quotes and generation include them), references with reference_key/upload_id/source_result, first_frame_route, preparation errors, ready_to_estimate and spark_required. Uploaded snapshots remain fixed when capture settings or the scene change. For render_video, first_frame_route gives the first frame's input, label and sent_as: first_frame for the model's own first-frame input, or reference_image with a reason and note when the image becomes image 1 of that input (exclusive: the model says its first frame cannot be combined with reference videos such as the scene clip; no_first_frame_input). It is null when no image input can go with the clip, and for render_image. Requests sending two file inputs whose descriptions say they can't be combined are refused before any quote; settings described that way are not checked. A first frame handed from a saved image (prepare_result_application purpose video_first_frame) keeps an empty first_frame_path and reports source_result: its saved request_id and asset_id, and saved=true while this credential scope still holds that verified download; source_result is null for other references.\n"
            'Example: {"lane":"render_image","action":"configure","settings":{"look":"copper sculpture","capture_source":"CAMERA"}}.\n'
            "Configure, prepare the scene and optional first frame, then inspect until ready. An empty automatic look needs estimate_prompt(action=GENERATE), separate approve_prompt spending approval and result delivery first. Finally use estimate_cost/generate with this lane/model and no parameters. File uploads and captures are explicit; no Python execution is required.\n"
            "Platform equivalent: upload_create and upload_complete for prepared snapshots; native form editing is local."
        ),
        _schema(
            {
                "lane": {"type": "string", "enum": ["render_image", "render_video"]},
                "action": {"type": "string", "enum": ["inspect", "configure", "prepare", "remove"]},
                "settings": {
                    "type": "object",
                    "properties": {
                        "model_id": {"type": "string"},
                        "look": {"type": "string"},
                        "capture_source": {"type": "string", "enum": ["CAMERA", "VIEWPORT"]},
                        "force_solid": {"type": "boolean"},
                        "spark_enabled": {"type": "boolean"},
                        "first_frame_path": {"type": "string"},
                        "use_first_frame": {"type": "boolean"},
                        "match_timeline": {"type": "boolean"},
                        "parameters": {"type": "object"},
                        "style_assets": {
                            "type": "array",
                            "items": {"type": "string"},
                            "maxItems": 15,
                        },
                        "trained": {
                            "type": "array",
                            "maxItems": 16,
                            "items": {
                                "type": "object",
                                "properties": {
                                    "model_id": {"type": "string"},
                                    "scale": {"type": "number"},
                                },
                                "required": ["model_id"],
                                "additionalProperties": False,
                            },
                        },
                    },
                    "additionalProperties": False,
                },
                "role": {"type": "string", "enum": ["scene", "first_frame"]},
                "reference_key": {"type": "string"},
            },
            ["lane"],
        ),
        _after_description(render_form),
    ),
    ToolSpec(
        "estimate_cost",
        (
            "Get the exact CU cost with a dry run that spends no credits.\n"
            "Args:\n"
            "  - model_id: required string, the model identifier.\n"
            "  - parameters: optional object, model parameters including Scenario asset ids for file inputs. LoRAs or a composition go in the base model's lora_component inputs from model_schema, one strength per LoRA; they are checked against the listed trained models and rebuilt as the native form builds them, refusing before any request what the form would refuse. Omit for render lanes: prepare render_form first; pricing uses its native scene prompt, uploaded snapshots and trained choice.\n"
            "  - lane: optional generation lane, default image. Every lane issues a single-use quote_id. Lane panorama accepts only list_models(lane=panorama) models and applies their contract: locked values (a different value is refused), lane defaults when unset, 2:1 sizes and reference rules. A panorama-kind reference must be the asset_id of a saved, downloaded 360 result of this account and project (job_status projection equirectangular, source asset); other asset IDs are refused. Upscale factors are capped for the 32-megapixel World limit: at most 2 for a reference of unknown size, which fits a 2:1 panorama up to 4096 pixels wide, and an upscaled result is not upscaled again. A changed model schema fails closed.\n"
            "Returns: model_id, lane, cu_cost, cu_cost_exact (decimal string), details, mesh_sources (local captured 3D input provenance) and quote_id bound to the lane, model, inputs, scene and credential context.\n"
            'Example: {"model_id": "model_example", "parameters": {"prompt": "a wooden crate"}}.\n'
            "Call before generate and show the cost to the user; an estimate does not authorize spending. Like model_schema, a first call for a model whose description is not loaded yet waits up to 30 seconds for it. Speech-to-text (audio2txt) and video-to-motion (video23d) models are experimental: they can be estimated and generated and their results stay in saved jobs, but motion and transcription handling is not accepted. prepare_result_application imports a returned model or media file by file type only.\n"
            "Platform equivalent: model_run with dry_run."
        ),
        _schema(
            {
                "model_id": {"type": "string"},
                "parameters": {"type": "object"},
                "lane": {"type": "string", "enum": list(LANES)},
            },
            ["model_id"],
        ),
        _after_description(estimate_cost),
        {"readOnlyHint": True},
    ),  # touches bpy (prefs, catalog): must run on the main thread
    ToolSpec(
        "generate",
        (
            "Submit a generation that spends the user's credits. Every model lane uses durable shared jobs.\n"
            "Args:\n"
            "  - lane: required string; image, video, 3d, material, audio, render_image, render_video, edit3d or panorama.\n"
            "  - model_id: required string, the exact model to run.\n"
            "  - parameters: optional object, model parameters; file inputs take Scenario asset ids and LoRA inputs follow estimate_cost. Omit for render lanes, which rebuild the current render_form before checking the approved quote.\n"
            "  - quote_id: required, from estimate_cost with the same lane, model, inputs and scene.\n"
            "  - approved_cost: required, the exact cu_cost_exact string explicitly approved by the user.\n"
            "Returns: local_id, status, lane, model_id and note. All model jobs poll and download through the shared session. Only the Image lane imports verified PNG/EXR images automatically into the unchanged origin. Other lanes stop at saved ready results; their scene application remains separate. Panorama results never change a World automatically; use prepare_result_application(purpose=world). Render lanes require explicit render_form uploads and separate Prompt Spark approval when enabled with an empty look. No capture, upload or Spark submission occurs during generate.\n"
            'Example: {"lane": "image", "model_id": "model_example", "parameters": {"prompt": "a wooden crate"}, "quote_id": "quote_from_estimate", "approved_cost": "1.25"}.\n'
            "Do not call before estimate_cost and explicit spending approval. Do not repeat a timed-out submission. Use prepare_result_application for saved-result imports. Speech-to-text (audio2txt) and video-to-motion (video23d) models are experimental: their results stay in saved jobs, but motion and transcription handling is not accepted. prepare_result_application imports a returned model or media file by file type only.\n"
            "Platform equivalent: model_run."
        ),
        _schema(
            {
                "lane": {"type": "string", "enum": list(LANES)},
                "quote_id": {"type": "string"},
                "approved_cost": {"type": "string"},
                "model_id": {"type": "string"},
                "parameters": {
                    "type": "object",
                    "description": "Model parameters; file parameters take Scenario asset ids",
                },
            },
            ["lane", "model_id", "quote_id", "approved_cost"],
        ),
        _after_description(generate),
    ),
    ToolSpec(
        "job_status",
        (
            "Read one local generation's status and cost without spending credits. Active model jobs advance through shared remote polling and verified downloads; restarted jobs remain inspection-only.\n"
            "Args:\n"
            "  - job_id: optional string, a Scenario job id or the local_id returned by generate.\n"
            "  - id: optional string, compatibility alias; provide job_id or id. job_id takes precedence if both are supplied.\n"
            "Returns: local_id, job_id, status, cu_cost, files, error and kind. Shared jobs also return revision, cu_cost_exact, results (asset_id, name, media_type, size, source, projection, downloaded), actions, images and mesh_sources. A result's source is asset, or original when a declared OpenEXR original was saved instead of its preview or a WAV original instead of Scenario's MP3 copy (size unknown); projection is equirectangular only when Scenario declares a 360 image, never inferred. actions name explicit follow-ups that never run automatically: cancel_prepared maps to the cancel_prepared_job tool; refresh, resume, cancel, recover_download and retry_receipt map to recover_local_job. use_first_frame offers a downloaded PNG, JPEG or WebP result to prepare_result_application with purpose video_first_frame, and first_frame reports this session's latest handoff for the job (state bound or failed, scene, input, asset_id, undo_recorded and error), or null. review_step marks a workflow job waiting for a decision: read it with workflow_job_steps and send only the user's explicit choice with decide_workflow_step; it is not a recover_local_job action. Mesh source records describe uploaded snapshots; they do not authorize finding or replacing an object after restart. Recovered jobs report kind=model; result media types remain available. Saved workflow jobs also return loop_steps, quote_may_understate and cost_warning: cu_cost_exact stays the approved quote, which may cover only one loop pass when quote_may_understate is true; loop_steps is null when this session did not record the definition's loop count. import_units classifies saved model results without reading files: key (the asset_id for prepare_result_application), format (glb, gltf, fbx, obj, ply, spz or splat), kind (mesh, splat, or ply until its bytes are read), primary (first in display order; the others are alternates), importable (true for every listed format in this release), members (asset_id, role and media_type) and glTF resources; import_findings lists saved files left out with a reason code. model_import summarizes this session's last import of the job (unit, format, members, dropped_references, frame_rate_mismatch and splat: points_total, points_kept, step, axes and spz_extensions, or null for a mesh) and is null after undo history changes; spz_extensions true means the SPZ may declare other storage axes. Unknown jobs raise ValueError.\n"
            "Shared jobs also return the same advisory in-memory projection as the native Jobs views, never persisted: lane is the model lane that submitted the job in this Blender session (null for workflow, Film, restarted and recovered jobs; it grants no destination). While a known remote job is active, remote_status is its latest validated Scenario status (pending, queued, warming-up, in-progress or finalizing), progress is the reported fraction from 0 to 1 or null when missing or invalid, remote_observed_at is that reading's UTC time and remote_stale is true when automatic polling is not keeping it current (delivery paused, Online Access off or a refresh overdue). All four are null otherwise. Only in-progress and finalizing fractions measure generation; the native Jobs views show a percentage only for those statuses above 0. Providers may keep progress at 0 until completion, so it is never a time estimate. delivery_paused is true while delivery waits for explicit review or recovery. delivery_active is true while this Blender session still advances the job by itself (submission, polling, download or an automatic image import); prepare no result application while it is true. delivery_offline is true while Blender's Online Access is disabled and holds a step this session would take by itself: status polling, or the download of a finished model or workflow job's results; delivery_active is then false, nothing is sent and the job resumes by itself once the user allows Online Access. It is false for paused, restarted and settled jobs and for finished Prompt Spark, Translate and Blockout jobs, whose text is read without a download. For a failed job, error is the reason Scenario gave (metadata.error, then metadata.hint) as one line of at most 400 characters, with its support error id (error_...) kept for the user to quote: each part is shown only when it is plain text and otherwise reads 'Scenario reported more details that are not shown here.', and when neither is plain text error is 'Scenario reported a failure without a readable reason.' It comes from the retrieval in this session that observed the failure, so a job that failed before a restart reports null.\n"
            "Workflow jobs also return awaiting_decision, true while the last retrieval in this session showed a waiting user-approval or user-selection step and no decision is being sent (delivery_active is then false), and pending_steps: node_id, type, iteration_index, loop_node_id, depends_on, review_asset_ids (at most 50 of review_asset_total), items (a selection's frozen candidates: asset IDs or texts per item_kind), min, max, actions, blocked, links_complete and review_id, never signed URLs. actions lists the decisions that may be sent. blocked is null, loop (the selection feeds a ForEach loop, a nested workflow or a node type the API reference does not list, which the approved price may not cover), unverified (the links between the workflow's steps could not be read and the workflow contains such a node, so the selection may feed it), unsupported (unreadable candidates or limits) or unidentified (no sendable node ID); loop, unverified and unsupported leave only reject, unidentified leaves none. Step links come from the job and, while a step waits, from the workflow's definition, read once per waiting period; links_complete is false when some links are still unknown, so depends_on and review_asset_ids may be incomplete. reject stops the workflow and is the only workflow cancellation. decision_error reports why the last decision was refused, not sent or is uncertain; never repeat an uncertain decision until the step is shown again. While a step waits, status refreshes every 15 seconds.\n"
            'Example: {"job_id": "job_example"}.\n'
            "Prefer this for one status check; it only knows jobs tracked by this Blender runtime.\n"
            "Platform equivalent: job_get."
        ),
        _schema({**_JOB_REF}),
        job_status,
        {"readOnlyHint": True},
    ),
    ToolSpec(
        "wait_for_job",
        (
            "Wait for a generation while Blender remains responsive. Shared jobs return when delivery finishes, pauses for review, a workflow waits for a decision, is held by disabled Online Access, or the wait expires. Restarted shared jobs remain inspection-only until explicitly resumed. Prototype records return a local snapshot immediately with scoped recovery guidance.\n"
            "Args:\n"
            "  - job_id: optional string, a Scenario job id or local_id returned by generate.\n"
            "  - id: optional string, compatibility alias; provide job_id or id. job_id takes precedence.\n"
            "  - timeout: optional finite number of seconds from 0 to 170, default 170.\n"
            "Returns: shared jobs return the job_status fields, including lane, remote_status, progress, remote_observed_at, remote_stale, delivery_active and delivery_offline, when delivery finishes, pauses for review (delivery_paused true), a workflow step waits for a decision (awaiting_decision true, returned at once), is held by disabled Online Access (delivery_offline true, returned at once) or the wait expires. delivery_active true means the wait expired, including while an automatic image import is pending in ready or applying; call again. With delivery_offline true, do not call again until the user allows Online Access. Prototype records return local_id, job_id, status, progress, cu_cost, files, error and kind, with a recovery note while unfinished.\n"
            'Example: {"job_id": "job_example", "timeout": 30}.\n'
            "Prefer job_status for a quick check. Waiting runs off the main thread; it does not cancel, retry or submit generation. Context changes require a fresh status query.\n"
            "Platform equivalent: jobs_wait."
        ),
        _schema({**_JOB_REF, "timeout": {"type": "number", "minimum": 0, "maximum": 170}}),
        wait_for_job,
        {"readOnlyHint": True},
    ),  # touches bpy (paths, manager): main thread
    ToolSpec(
        "recover_cloud_job",
        (
            "Read one completed cloud model job into the selected credential-scoped saved jobs.\n"
            "Args:\n"
            "  - job_id: required string, the exact remote job ID from list_generations.\n"
            "  - model_id: required string, that row's model ID, verified against the fresh SDK response.\n"
            "Returns: context_id, request_id, job_id, revision, state, source and note. Existing saved records are preserved; repeated reads do not duplicate them. No local quote is invented and this never submits generation, downloads media or changes the scene.\n"
            'Example: {"job_id": "job_example", "model_id": "model_example"}.\n'
            "Inspect list_local_jobs next. Use recover_local_job to resume downloads, then prepare_result_application and explicit destination approval. Failed reads may be retried; uncertain generation submissions must not be repeated.\n"
            "Platform equivalent: job_get followed by local recovery storage."
        ),
        _schema(
            {"job_id": {"type": "string"}, "model_id": {"type": "string"}}, ["job_id", "model_id"]
        ),
        recover_cloud_job,
    ),
    ToolSpec(
        "import_result",
        (
            "Reject direct cached-file import and explain the required saved-result approval flow.\n"
            "Args:\n"
            "  - job_id: optional string, a Scenario job id or local_id returned by generate.\n"
            "  - id: optional string, compatibility alias; provide job_id or id. job_id takes precedence.\n"
            "Returns: an error directing the caller to explicit destination approval; no scene mutation.\n"
            'Example: {"job_id": "job_example"}.\n'
            "Use recover_cloud_job for a completed cloud row that is not yet saved. For saved jobs, use prepare_result_application and approve its exact destination. Cached prototype files cannot authorize application.\n"
            "No platform equivalent."
        ),
        _schema({**_JOB_REF}),
        import_result,
    ),
    ToolSpec(
        "capture_reference",
        (
            "Capture a viewport/camera still or clip, or export selected meshes, and upload the snapshot as a Scenario reference asset.\n"
            "Args:\n"
            "  - source: optional string, VIEWPORT (default), CAMERA, VIEWPORT_CLIP, CAMERA_CLIP or MESH.\n"
            "Returns: context_id and reference_id; poll reference_upload_status until imported to obtain asset_id for a model or workflow file parameter. Captures use private temporary storage cleaned after staging.\n"
            'Example: {"source": "CAMERA"}.\n'
            "Stills/clips use 1280x720 and require an interactive Blender window with a 3D viewport. Clips use the preview range when enabled, otherwise the scene frame range, without audio or implicit duration padding. MESH exports the selected meshes as one GLB and also works in background mode. This sends scene content to Scenario; use it only for an authorized reference upload.\n"
            "Platform equivalent: upload_asset then upload_asset_complete."
        ),
        _schema(
            {
                "source": {
                    "type": "string",
                    "enum": ["VIEWPORT", "CAMERA", "VIEWPORT_CLIP", "CAMERA_CLIP", "MESH"],
                }
            }
        ),
        capture_reference,
    ),
    ToolSpec(
        "list_generations",
        (
            "List recent cloud generations using this Blender runtime's loaded history.\n"
            "Args:\n"
            "  - limit: optional integer, default 20, maximum number of rows to return.\n"
            "  - older: optional boolean, load the next older cloud page when older_page is true, or retry a failed older read; then poll without older. more_loaded counts loaded rows beyond limit. A failed older read keeps the loaded rows and returns older_error; retry it with older, or use refresh when the note reports a repeated cursor.\n"
            "  - refresh: optional boolean, request a new cloud page or retry a failed read; then poll without refresh.\n"
            "Returns: generations[] with job_id, kind (image, video, 3d, material, audio, workflow or unknown), model_id, prompt, status, cu_cost, cost_unavailable, workflow_id, workflow_job_id, empty local_files and local_request_ids. kind is workflow for a workflow run; otherwise it comes from local job records and their saved result media types, else the loaded model catalog; unknown means neither describes the row yet, never an image. cu_cost is billing.cuCost plus cuCostDetails add-ons; a workflow run (kind=workflow) reports its own charge plus its steps' charges, or cu_cost null with cost_unavailable true when a step could not be read. Any row with malformed billing also reports cu_cost null and cost_unavailable true. Rows with workflow_job_id are steps of that run; its cu_cost, once not null, already includes them. Matching scoped saved jobs expose request IDs; inspect list_local_jobs and use explicit result approval. Use recover_cloud_job for unsaved completed model jobs. The first call may return an empty list and a note while history loads; call again after loading.\n"
            'Example: {"limit": 10}.\n'
            "Prefer job_status for a tracked active generation; this is not a fresh platform-wide history query on every call.\n"
            "Platform equivalent: jobs_list."
        ),
        _schema(
            {
                "limit": {"type": "integer"},
                "older": {"type": "boolean"},
                "refresh": {"type": "boolean"},
            }
        ),
        list_generations,
        {"readOnlyHint": True},
    ),
)
