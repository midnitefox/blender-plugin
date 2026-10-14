# SPDX-FileCopyrightText: 2026 Scenario Inc.
# SPDX-License-Identifier: GPL-3.0-or-later
"""Glue between scene state, schemas, estimates and the job manager (main thread)."""

import logging
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field

import bpy

from ..core.api import trained_offers
from ..core.api.catalog import (
    DEFAULT_MODELS,
    LANE_KIND,
    MULTIVIEW_HINTS,
    RENDER_LANES,
    USABLE_TRAINED_KINDS,
    edit3d_models,
    lane_kind,
    mesh_param,
    models_for_lane,
    trained_kind,
)
from ..core.api.errors import ScenarioError
from ..core.jobs.coordinator import DuplicateRequestError, RouteQuoteError
from ..core.scene import capture_plan, panorama_contract
from ..core.schema.params import build_body, missing_required_files, parse_schema, validate
from . import model_jobs, params_ui, props, runtime, trained_models
from .job_session import OriginUnavailable

log = logging.getLogger("scenario.generation")

_schemas = {}
MODEL_OFFLINE = "Online access is disabled; the model description cannot load"
_MODEL_FAILED = "Could not load this model: "


class ModelLoading(ScenarioError):
    """A read of this model's description is in flight; retry after its delivery.

    `required` is False when the caller can still answer without the description,
    such as an inspection that reports the form's state.
    """

    def __init__(self, model_id, reason="Loading the model description", *, required=True):
        super().__init__(0, reason)
        self.model_id = model_id
        self.required = required


def reload_lane(record):
    """Return the recorded generation form, without guessing for Film or generic jobs.

    A catalog lane without a native form never falls back to its asset kind's
    form, which would drop that lane's own rules; only generic jobs use the kind.
    """
    if record.lane in props.GENERATION_LANES:
        return record.lane
    if record.lane in LANE_KIND:
        return None
    return record.kind if record.kind in props.GENERATION_LANES else None


def schema_for(model_id):
    if not model_id or model_id == "NONE":
        return None
    schema = _schemas.get(model_id)
    if schema is None:
        record = runtime.state.records.get(model_id)
        if record is None or not record.parameters:
            return None
        schema = parse_schema(record)
        _schemas[model_id] = schema
    return schema


def request_catalog():
    runtime.sync_catalog_context()
    if runtime.state.catalog_loading or not runtime.online():
        return False
    try:
        manager = runtime.ensure_manager()
        catalog = runtime.ensure_catalog()
    except ScenarioError as err:
        runtime.state.catalog_error = err.reason
        runtime.state.catalog_error_selection = (runtime.credentials(), runtime.project_id())
        runtime.set_message(err.reason)
        return False
    runtime.state.catalog_error = ""
    runtime.state.catalog_error_selection = None
    wanted = [m for lane in DEFAULT_MODELS for m in DEFAULT_MODELS[lane]]
    cached = catalog.load_list_cached("public")
    if cached:
        detailed = []
        for model_id in wanted:
            record = catalog.load_cached(model_id)
            if record is not None:
                detailed.append(record)
        set_catalog(cached, detailed)
    runtime.state.catalog_loading = True
    manager.fetch_catalog(catalog, "public", wanted)
    trained_models.request(manager, catalog)
    return True


def clear_catalog():
    """Drop every form/cache tied to retired credentials on the main thread."""
    _clear_model_failures(tuple(runtime.state.model_errors))
    _schemas.clear()
    _pending_models.clear()
    _model_readers.clear()
    _pending_dirty_models.clear()
    runtime.state.estimates.clear()
    runtime.state.estimate_origins.clear()
    runtime.state.repeats.clear()  # a repeat belongs to the retired scope's price
    runtime.state.records.clear()
    runtime.state.lane_models.clear()
    runtime.state.enum_cache.clear()
    runtime.state.catalog_loaded = False
    runtime.state.catalog_loading = False
    runtime.state.catalog_error = ""
    runtime.state.catalog_error_selection = None
    trained_models.clear()
    for scene in bpy.data.scenes:
        if not hasattr(scene, "scenario"):
            continue
        for lane in props.GENERATION_LANES:
            lane_state = scene.scenario.lane_state(lane)
            lane_state.estimate_key = ""
            lane_state.estimate_state = "IDLE"
            lane_state.estimate_dirty_at = 0.0


def process_catalog_events():
    """Deliver the same queued reads in GUI ticks and headless MCP calls."""
    runtime.sync_catalog_context()
    manager = runtime.state.manager
    if manager is not None:
        from . import handlers

        events = manager.drain_catalog()
        for event in events:
            try:
                handlers.dispatch(event)
            except Exception:
                # One malformed schema or stale scene must not drop later
                # completions already removed from the queue. Keep transport
                # payloads and exception details out of logs and UI messages.
                log.warning("Could not apply catalog event %s", event[0])
                if event[0] == "catalog":
                    runtime.state.catalog_loading = False
                    runtime.state.catalog_loaded = False
                    runtime.state.catalog_error = "Could not read the model catalog"
                elif event[0] in ("trained_catalog", "trained_catalog_failed"):
                    # A later Trained chip or refresh may read the private list again.
                    runtime.state.trained_loading = False
                    runtime.state.trained_error = "Could not read trained models"
                elif event[0] == "trained_bases":
                    # End the scan so the picker stops showing progress; a refresh retries it.
                    trained_models.stop_scan(event[1], "Could not read base model descriptions")
                runtime.set_message("Could not read a model description; refresh the catalog")
        return bool(events)
    return False


def set_catalog(records, detailed, *, warmup=False):
    changed = _store_details(detailed)
    for rec in records:
        runtime.state.records.setdefault(rec.id, rec)
    for lane in props.GENERATION_LANES:
        lane_records = models_for_lane(lane, records)
        runtime.state.lane_models[lane] = lane_records
        runtime.set_enum_items(
            ("models", lane), [(r.id, r.name, r.short_description) for r in lane_records]
        )
    runtime.state.catalog_loaded = True
    runtime.state.catalog_loading = False
    runtime.state.catalog_error = ""
    for scene in bpy.data.scenes:
        for lane in props.GENERATION_LANES:
            lane_state = scene.scenario.lane_state(lane)
            restore_model_key(lane_state)
            # A catalog refresh must not re-price forms that are already quoted, unless
            # it changed the LoRA inputs their trained choice runs through.
            on_model_changed(
                bpy.context, lane_state, mark_dirty=_trained_inputs_changed(lane_state, changed)
            )
    if bpy.context.scene is not None:
        refresh_3d_models(bpy.context, mark_dirty=False, prefetch=not warmup)
        refresh_edit3d_models(bpy.context, mark_dirty=False, prefetch=not warmup)
        refresh_panorama_models(bpy.context, mark_dirty=False, prefetch=not warmup)


def restore_model_key(lane_state):
    """Point the enum back at the model the user chose (stored by id), if the rebuilt list still has it."""
    key = lane_state.model_key
    if not key:
        return
    valid = [item[0] for item in runtime.enum_items(("models", props.lane_of(lane_state)))]
    if key in valid and lane_state.model_id != key:
        # Rebuilding a dynamic enum can change its stored index while the
        # stable chosen id is unchanged. Restoration is not a new selection:
        # store the index without the RNA update, which would tag the scene and
        # make its captured origins stale. The caller applies its own dirty
        # intent afterwards. Each enum value is its 3-tuple item's index.
        lane_state["model_id"] = valid.index(key)


def _store_details(detailed):
    """Keep fresh model descriptions; returns the IDs whose declared LoRA inputs changed.

    A form whose trained choice runs through such a base is priced again even
    when none of its slots changes: the declared default a LoRA without its own
    strength sends, or whether the choice runs at all, may differ. Slots that do
    change are repriced by trained_models.sync_bounds.

    Only a base this session already described can change: its first description
    (a reopened file, the catalog warm-up) has nothing to compare, so a price
    saved with the form stays, as the warm-up's mark_dirty=False intends.
    """
    changed = set()
    for rec in detailed:
        previous = runtime.state.records.get(rec.id)
        described = previous is not None and bool(previous.parameters)
        before = trained_models.base_slot(rec.id) if described else None
        runtime.state.records[rec.id] = rec
        runtime.state.model_errors.pop(rec.id, None)
        _schemas.pop(rec.id, None)
        if described and trained_models.base_slot(rec.id) != before:
            changed.add(rec.id)
    return changed


def _trained_inputs_changed(lane_state, changed):
    return lane_state.model_id in changed and len(lane_state.trained) > 0


def set_models(detailed, failed, *, mark_dirty=True):
    changed = _store_details(detailed)
    for model_id, reason in failed.items():
        runtime.set_message(f"{model_id}: {reason}")
    succeeded = {r.id for r in detailed}
    # A form without its description keeps the sanitized failure until a new
    # read, a success, another model or retired credentials replaces it.
    unavailable = {
        model_id: f"{_MODEL_FAILED}{reason}"
        for model_id, reason in failed.items()
        if model_id not in succeeded and schema_for(model_id) is None
    }
    runtime.state.model_errors.update(unavailable)
    dirty_models = _pending_dirty_models & succeeded
    # A transient failure ends this request, not the user's pending selection.
    # Its eventual background retry still needs to re-arm the estimate timer.
    _pending_dirty_models.difference_update(succeeded)
    _pending_models.difference_update(succeeded | set(failed))
    for model_id in succeeded | set(failed):
        _model_readers.pop(model_id, None)
    for scene in bpy.data.scenes:
        for lane in props.GENERATION_LANES:
            lane_state = scene.scenario.lane_state(lane)
            if lane_state.model_id in succeeded:
                on_model_changed(
                    bpy.context,
                    lane_state,
                    mark_dirty=mark_dirty
                    or lane_state.model_id in dirty_models
                    or _trained_inputs_changed(lane_state, changed),
                )
            elif lane_state.model_id in unavailable:
                lane_state.last_error = unavailable[lane_state.model_id]


def _clear_model_failures(model_ids):
    """A new read or retired credentials supersede these failures (main thread)."""
    failures = {
        model_id: runtime.state.model_errors.pop(model_id)
        for model_id in model_ids
        if model_id in runtime.state.model_errors
    }
    if not failures:
        return
    for scene in bpy.data.scenes:
        if not hasattr(scene, "scenario"):
            continue
        for lane in props.GENERATION_LANES:
            lane_state = scene.scenario.lane_state(lane)
            if failures.get(lane_state.model_id) == lane_state.last_error:
                lane_state.last_error = ""


def lane_error(lane_state):
    """The lane error to draw; read-only, so drawing never rewrites the saved value.

    A model-load failure is current only while this session records it for the
    lane's model. One saved in a reopened file is stale and is not drawn.
    """
    error = lane_state.last_error
    if error.startswith(_MODEL_FAILED):
        if runtime.state.model_errors.get(lane_state.model_id) != error:
            return ""
    return error


_pending_models = set()
_model_readers = {}  # model_id -> the worker reading its pending description
_pending_dirty_models = set()


def is_loading(model_id):
    """Whether this model's description read is in flight; read-only for drawing."""
    return model_id in _pending_models


def model_reader(model_id):
    """The worker of this model's in-flight description read, or None (main thread).

    Another thread may wait for it to end; the read is delivered on the main thread.
    """
    return _model_readers.get(model_id) if model_id in _pending_models else None


def request_model(model_id, *, mark_dirty=True):
    """Fetch a model record in the background; the 'models' event finishes the job.

    Returns whether a read for this model is in flight.
    """
    return not _start_model_read(model_id, mark_dirty)


def _start_model_read(model_id, mark_dirty):
    """Queue one description read; return why none can start, or "" while one is in flight."""
    runtime.sync_catalog_context()
    if model_id in _pending_models:
        return ""
    if not runtime.online():
        return MODEL_OFFLINE
    try:
        manager = runtime.ensure_manager()
        catalog = runtime.ensure_catalog()
    except ScenarioError as err:
        runtime.set_message(err.reason)
        return err.reason or "Could not read the model description"
    _pending_models.add(model_id)
    _clear_model_failures((model_id,))
    _model_readers[model_id] = manager.fetch_models(catalog, [model_id], mark_dirty=mark_dirty)
    return ""


def _image_inputs(record):
    return [
        p
        for p in record.parameters
        if p.get("type") in ("file", "file_array") and (p.get("kind") or "image") == "image"
    ]


def _looks_multiview(record):
    text = (record.id + " " + record.name).lower()
    return any(hint in text for hint in MULTIVIEW_HINTS)


def three_d_models(mode, records):
    """Bucket 3D models by how they are driven: TEXT (txt23d), IMAGE (one picture), MULTI (several views).

    With a schema, a file_array image input that accepts more than one file means multi-view. Without a schema
    (list entries), the model id and name decide ("multi", "multiview"). Models taking 1 to N images appear in both."""
    out = []
    for record in records:
        caps = set(record.capabilities)
        if record.deprecated_successor:
            continue
        if mode == "TEXT":
            if "txt23d" in caps:
                out.append(record)
            continue
        if "img23d" not in caps:
            continue
        inputs = _image_inputs(record)
        if not inputs:
            multi_named = _looks_multiview(record)
            if (mode == "MULTI" and multi_named) or (mode == "IMAGE" and not multi_named):
                out.append(record)
            continue
        multi = any(p.get("type") == "file_array" and (p.get("maxLength") or 2) > 1 for p in inputs)
        single = any(p.get("type") == "file" for p in inputs) or any(
            p.get("type") == "file_array" and (p.get("minLength") or 1) <= 1 for p in inputs
        )
        if mode == "MULTI" and (multi or _looks_multiview(record)):
            out.append(record)
        elif (
            mode == "IMAGE"
            and (single or not multi)
            and not (_looks_multiview(record) and not single)
        ):
            out.append(record)
    return models_for_lane("3d", out)


def refresh_3d_models(context=None, *, mark_dirty=True, prefetch=True):
    scene = getattr(context, "scene", None) or bpy.context.scene
    if scene is None:
        return
    records = three_d_models(scene.scenario.three_d_mode, list(runtime.state.records.values()))
    runtime.state.lane_models["3d"] = records
    runtime.set_enum_items(("models", "3d"), [(r.id, r.name, r.short_description) for r in records])
    lane_state = scene.scenario.lane_state("3d")
    restore_model_key(lane_state)
    on_model_changed(context or bpy.context, lane_state, mark_dirty=mark_dirty)
    # models known only from the list get their schema in the background so the form and the quote work
    missing = [r.id for r in records if not r.parameters and r.id not in _pending_models][:12]
    if prefetch and missing:
        request_models(missing, mark_dirty=mark_dirty)


def refresh_edit3d_models(context=None, *, mark_dirty=True, prefetch=True):
    """The Edit 3D model list follows the task tabs (Retexture, Retopology, Rigging...)."""
    scene = getattr(context, "scene", None) or bpy.context.scene
    if scene is None:
        return
    records = edit3d_models(scene.scenario.edit3d_task, list(runtime.state.records.values()))
    runtime.state.lane_models["edit3d"] = records
    runtime.set_enum_items(
        ("models", "edit3d"), [(r.id, r.name, r.short_description) for r in records]
    )
    lane_state = scene.scenario.lane_state("edit3d")
    restore_model_key(lane_state)
    if lane_state.model_id == "NONE" or lane_state.model_id not in [r.id for r in records]:
        if records:
            lane_state.model_id = records[0].id
    on_model_changed(context or bpy.context, lane_state, mark_dirty=mark_dirty)
    missing = [r.id for r in records if not r.parameters and r.id not in _pending_models][:12]
    if prefetch and missing:
        request_models(missing, mark_dirty=mark_dirty)


def panorama_models(mode, records):
    """The Environment lane's verified contract models of one mode, curated order."""
    return panorama_contract.models_for_mode(mode, models_for_lane("panorama", records))


def refresh_panorama_models(context=None, *, mark_dirty=True, prefetch=True):
    """The Environment model list follows the mode tabs (Generate, From image, HDRI, Upscale)."""
    scene = getattr(context, "scene", None) or bpy.context.scene
    if scene is None:
        return
    records = panorama_models(scene.scenario.panorama_mode, list(runtime.state.records.values()))
    runtime.state.lane_models["panorama"] = records
    runtime.set_enum_items(
        ("models", "panorama"), [(r.id, r.name, r.short_description) for r in records]
    )
    lane_state = scene.scenario.lane_state("panorama")
    restore_model_key(lane_state)
    if records and lane_state.model_id not in [r.id for r in records]:
        lane_state.model_id = records[0].id  # another mode's model cannot stay selected
    if records and lane_state.model_key != lane_state.model_id:
        # A rebuilt list can show its first model without an assignment; record
        # it as the choice, so a later list change cannot silently swap it. The
        # key is stored without an RNA update, which would make origins captured
        # in the scene stale during a catalog load.
        lane_state["model_key"] = lane_state.model_id
    on_model_changed(context or bpy.context, lane_state, mark_dirty=mark_dirty)
    missing = [r.id for r in records if not r.parameters and r.id not in _pending_models][:12]
    if prefetch and missing:
        request_models(missing, mark_dirty=mark_dirty)


def select_panorama_mode(context, mode):
    """Show one Environment mode with its own model list on the context scene.

    The list is shared by scenes, so an unchanged mode is refreshed as well:
    another scene may have listed a different mode since. Operators only.
    """
    scenario = context.scene.scenario
    if scenario.panorama_mode != mode:
        scenario.panorama_mode = mode  # its update refreshes the list
    else:
        refresh_panorama_models(context)


def request_models(model_ids, *, mark_dirty=True):
    runtime.sync_catalog_context()
    if not runtime.online():
        return
    try:
        manager = runtime.ensure_manager()
        catalog = runtime.ensure_catalog()
    except ScenarioError as err:
        runtime.set_message(err.reason)
        return
    _pending_models.update(model_ids)
    _clear_model_failures(model_ids)
    reader = manager.fetch_models(catalog, list(model_ids), mark_dirty=mark_dirty)
    _model_readers.update(dict.fromkeys(model_ids, reader))


class WithoutInputs(ScenarioError):
    """A model whose record declares no inputs: a final answer that no new read changes."""

    def __init__(self, record):
        self.kind = trained_kind(record)
        super().__init__(0, trained_offers.no_inputs_reason(self.kind))


def ensure_record(model_id, *, mark_dirty=True):
    """Read the selected connection's cache or queue a description for UI/MCP.

    Both entry points run on the main thread and retry after asynchronous delivery;
    ModelLoading tells them a read is in flight, which local MCP waits for.
    A listed LoRA or composition, or a completed description without inputs,
    raises WithoutInputs instead: reading it again would return the same record,
    so callers waiting for a read would never finish.
    """
    process_catalog_events()
    record = runtime.state.records.get(model_id)
    if record is not None and record.parameters:
        return record
    listed = runtime.state.trained_records.get(model_id) or record
    if listed is not None and trained_kind(listed) in USABLE_TRAINED_KINDS:
        raise WithoutInputs(listed)
    catalog = runtime.ensure_catalog()
    cached = catalog.load_cached(model_id)
    if cached is not None and cached.parameters:
        runtime.state.records[model_id] = cached
        _schemas.pop(model_id, None)
        _clear_model_failures((model_id,))
        return cached
    if cached is not None:
        raise WithoutInputs(cached)  # this connection already read its description
    failure = runtime.state.model_errors.get(model_id)
    refusal = _start_model_read(model_id, mark_dirty)
    if refusal:
        raise ScenarioError(0, refusal)  # nothing is loading, so callers must not wait for it
    if failure is not None:
        # The new read replaces the failure the form showed. Name it once for
        # direct callers; local MCP waits for the new read instead.
        reason = failure.removeprefix(_MODEL_FAILED)
        raise ModelLoading(
            model_id, f"Loading the model description again; the last read failed: {reason}"
        )
    raise ModelLoading(model_id)


_waited = set()  # descriptions the current local MCP call has already waited for


@contextmanager
def waited_for(model_id):
    """Let an optional description wait end once (main thread, around one tool call)."""
    _waited.add(model_id)
    try:
        yield
    finally:
        _waited.discard(model_id)


def await_description(model_id, *, required=True):
    """Make a form model's description available before a command uses it (main thread).

    Returns at once without a chosen model or once its schema is loaded. Otherwise it
    reads like ensure_record and raises ModelLoading while the read is in flight, which
    local MCP waits for. A required description also raises a failed or refused read.
    An optional one, for inspection, leaves a recorded failure, disabled online access
    or an expired wait to the caller's own errors and never replaces a failure.
    """
    if not model_id or model_id == "NONE" or schema_for(model_id) is not None:
        return
    if not required and (
        model_id in _waited or model_id in runtime.state.model_errors or not runtime.online()
    ):
        return
    try:
        ensure_record(model_id)
    except ModelLoading as loading:
        if required:
            raise
        raise ModelLoading(model_id, loading.reason, required=False) from None
    except ScenarioError:
        if required:
            raise


def on_model_changed(context, lane_state, mark_dirty=True):
    model_id = lane_state.model_id
    if not model_id or model_id == "NONE":
        return
    # User intent invalidates the old quote even while a background schema read
    # is pending. Its later background completion must not resurrect that quote.
    if mark_dirty:
        props.mark_estimate_dirty(lane_state)
    try:
        ensure_record(model_id, mark_dirty=mark_dirty)
    except ScenarioError as err:
        # A pending read and disabled online access are drawn from live state;
        # neither persists as the lane's error once access or the read changes.
        deferred = isinstance(err, ModelLoading) or err.reason == MODEL_OFFLINE
        if deferred and mark_dirty:
            # The estimate debounce may observe the missing schema before its
            # background completion. Re-arm pricing when that detail arrives.
            _pending_dirty_models.add(model_id)
        lane_state.last_error = "" if deferred else err.reason
        return
    lane_state.last_error = ""
    schema = schema_for(model_id)
    if schema is None:
        return
    previous = {item.name for item in lane_state.params if item.model_id == model_id}
    seeded = params_ui.sync_params(lane_state, schema, model_id)
    if props.lane_of(lane_state) == "panorama":
        seeded |= _apply_contract_settings(lane_state, schema, model_id, previous)
    trained_models.sync_bounds(lane_state)  # strength fields follow the base's declared range
    # Seeded values skip their RNA callbacks: re-arm this form's price and let a
    # seeded model duration drive the camera path, as an edit would.
    if seeded or lane_state.estimate_state == "IDLE":
        props.mark_estimate_dirty(lane_state)
    if "duration" in seeded:
        sync_shot_duration(lane_state.id_data)


def _apply_contract_settings(lane_state, schema, model_id, previous):
    """Show the Environment contract's forced values, and its defaults on new settings.

    Runs from property callbacks and catalog delivery, never while drawing.
    Unchanged values are not written, so a catalog refresh keeps a ready price.
    Like schema defaults, the values are seeded without their RNA updates, which
    would make origins captured in the scene stale; the caller re-arms the price
    for the returned names.
    """
    contract = panorama_contract.contract_for(model_id)
    if contract is None:
        return set()
    values = list(contract.locked)
    values += [(name, value) for name, value in contract.defaults if name not in previous]
    seeded = set()
    for name, value in values:
        spec = schema.by_name(name)
        index = lane_state.params.find(name)
        if spec is None or index < 0:
            continue
        item = lane_state.params[index]
        attr = None
        if spec.ptype == "boolean":
            attr = "bool_value"
        elif spec.ptype == "number" and not spec.allowed_values:
            attr = "int_value" if spec.is_integer else "float_value"
        if attr is not None and getattr(item, attr) != value:
            item[attr] = value  # each a plain ID property value, as params_ui seeds them
            seeded.add(name)
        if not item.enabled:
            item["enabled"] = True
            seeded.add(name)
    return seeded


@dataclass
class Request:
    lane: str
    kind: str
    model_id: str
    body: dict
    files: dict = field(default_factory=dict)
    array_params: set = field(default_factory=set)
    errors: list = field(default_factory=list)
    partial: bool = False
    captures: list = field(default_factory=list)
    spark: dict = None  # set by the render lanes when Prompt Spark must write the look first
    meta: dict = field(default_factory=dict)


def build_request(scene, lane, for_estimate=False):
    from .reference_form import scope_error

    lane_state = scene.scenario.lane_state(lane)
    model_id = lane_state.model_id
    from . import trained_defaults

    # A form with no explicit choice takes the project default; it never prices the base
    # alone. Checked first, so a blocked form always shows the reason the pump compares.
    if error := trained_defaults.blocking_reason(lane_state):
        return Request(lane, lane_kind(lane), model_id, {}, errors=[error])
    if runtime.state.prompt_jobs is not None:
        from .prompt_jobs import BUSY

        action = runtime.state.prompt_jobs.current(scene, lane)
        if action is not None and action.phase in BUSY:
            return Request(
                lane,
                lane_kind(lane),
                model_id,
                {},
                errors=["Wait for prompt preparation to finish"],
            )
    references = lane_state.references
    if lane in RENDER_LANES:
        from .render_references import active_references

        references = active_references(lane_state)
    if error := scope_error(lane_state, references=references):
        return Request(lane, lane_kind(lane), model_id, {}, errors=[error])
    schema = schema_for(model_id)
    if schema is None:
        return Request(lane, lane_kind(lane), model_id, {}, errors=["Model not loaded yet"])
    if lane == "video" and schema.by_name("duration") is not None and lane_state.match_timeline:
        apply_match_timeline(
            scene, lane_state, schema
        )  # base Video: the model duration follows the timeline
    values, enabled = params_ui.collect_values(lane_state, schema)
    record = runtime.state.records.get(model_id)
    refs = {
        spec.name: [ref for ref in references if ref.param_name == spec.name]
        for spec in schema.specs
        if spec.is_file
    }
    files, array_params, asset_ids, captures = {}, set(), {}, []
    # A model's 3D mesh input is fed by the scene selection automatically, in every lane: the Edit lane requires it,
    # elsewhere (a text-to-motion model's character mesh) it is optional but still used when something is selected.
    # Skip it only when the user attached an explicit file/asset for that input.
    mesh_name = mesh_param(record) if record is not None else None
    if lane == "edit3d" and mesh_name is None:
        return Request(
            lane, lane_kind(lane), model_id, {}, errors=["This model takes no mesh input"]
        )
    if mesh_name is not None and not any(r.param_name == mesh_name for r in lane_state.references):
        from . import mesh_export

        if lane == "edit3d" or mesh_export.source_objects(bpy.context):
            captures.append({"param": mesh_name, "source": "MESH", "camera": None, "first": True})
    for spec in schema.specs:
        if not spec.is_file:
            continue
        if spec.ptype == "file_array":
            array_params.add(spec.name)
        for ref in refs.get(spec.name, []):
            if ref.source == "ASSET" and ref.asset_id:
                asset_ids.setdefault(spec.name, []).append(ref.asset_id)
            elif ref.source == "FILE" and ref.filepath:
                files.setdefault(spec.name, []).append(bpy.path.abspath(ref.filepath))
            elif ref.source == "RENDER":
                if for_estimate or lane == "image":
                    captures.append(
                        {"param": spec.name, "source": "RENDER", "camera": None}
                    )  # quoted like a pending file, no disk write
                else:
                    path = _save_render_result(scene)
                    if path:
                        files.setdefault(spec.name, []).append(path)
            elif ref.source in props.CAPTURE_SOURCES:
                captures.append(
                    {"param": spec.name, "source": ref.source, "camera": ref.asset_id or None}
                )
            elif ref.source == "MESH":
                # the user attached the selected scene mesh to a 3D input outside the edit3d lane (e.g. a text-to-motion
                # model's optional character mesh); export it at generate time just like edit3d does
                captures.append({"param": spec.name, "source": "MESH", "camera": None})
    body = build_body(schema.specs, values, asset_ids, enabled=enabled)
    if record is not None:
        # The lane's LoRAs or composition go into the base's own lorasComponent inputs,
        # built by the same trained_routes.apply that local MCP and the quote check use.
        body, error = trained_models.apply_to(lane_state, record, body)
        if error:
            return Request(lane, lane_kind(lane), model_id, {}, errors=[error])
    request = Request(
        lane, lane_kind(lane), model_id, body, files, array_params, [], False, captures
    )
    if lane in RENDER_LANES:
        from . import render_lanes

        render_lanes.decorate(scene, lane, lane_state, schema, request, for_estimate)
        if request.errors:
            return request
    contract_errors = []
    if lane == "panorama":
        contract_errors = _prepare_panorama(scene, lane_state, schema, request, references)
    pending = {name: list(paths) for name, paths in request.files.items()}
    for cap in request.captures:
        if cap.get("param"):
            pending.setdefault(cap["param"], []).append("<capture>")
    check = dict(body)
    for name, paths in pending.items():
        check.setdefault(name, paths if name in request.array_params else paths[0])
    if "seedance" in model_id and schema.prompt_name and (pending or asset_ids):
        has_video = any(s.kind == "video" and s.name in check for s in schema.specs if s.is_file)
        has_image = any(
            (s.kind or "image") == "image" and s.name in check for s in schema.specs if s.is_file
        )
        body[schema.prompt_name] = capture_plan.tag_prompt(
            body.get(schema.prompt_name, ""), has_video, has_image
        )
        check[schema.prompt_name] = body[schema.prompt_name]
    errors = validate(schema.specs, check, schema.one_of, schema.exclusive)
    if lane == "edit3d":
        from . import mesh_export

        if not mesh_export.source_objects(bpy.context):
            errors.append("Select the mesh to edit")
    if for_estimate:
        # The dry run rejects unknown asset ids (404), so files still to be uploaded are left out of the quote.
        if missing_required_files(schema.specs, check):
            errors.append("Add a reference to see the cost")
        elif missing_required_files(schema.specs, body):
            # the required file only exists as a pending capture or export: the server prices it after the upload
            errors.append(
                "Price shown after the upload (the model needs the "
                + ("mesh" if lane == "edit3d" else "capture")
                + " first)"
            )
        request.partial = bool(request.captures) or any(
            spec.cost_impact and spec.name in request.files for spec in schema.specs if spec.is_file
        )
    request.errors = contract_errors + [error for error in errors if error not in contract_errors]
    return request


def _prepare_panorama(scene, lane_state, schema, request, references):
    """Apply the shared Environment contract to the native body; return its errors.

    The same panorama_contract.prepare runs for local MCP. Locked, default,
    size and factor values replace the form's values in the body. Local files
    and captures still waiting for upload stand in as placeholders, so the
    contract judges the rest of the form: a waiting panorama file is checked by
    its upload preflight, and a capture is never a panorama.
    """
    from . import panorama_lane

    contract = panorama_contract.contract_for(request.model_id)
    if contract is None:
        return ["Choose a verified panorama model for the Environment lane"]
    if contract.mode != scene.scenario.panorama_mode or lane_state.model_key not in (
        "",
        request.model_id,
    ):
        return ["Pick the model again for this Environment mode"]
    body = request.body
    if contract.sizes:
        width, height = contract.size_params
        body[width], body[height] = props.panorama_size(scene)
    try:
        refs = panorama_lane.native_refs(lane_state, references)
    except ScenarioError as error:
        return [error.reason]
    check = dict(body)
    waiting = [(name, len(paths), True) for name, paths in request.files.items()]
    waiting += [(cap["param"], 1, False) for cap in request.captures if cap.get("param")]
    for name, count, is_file in waiting:
        spec = schema.by_name(name)
        if spec is None:
            continue
        placeholders = [f"waiting-upload:{name}:{index}" for index in range(count)]
        if is_file and panorama_lane.is_panorama_input(lane_state, name):
            refs.update({item: panorama_contract.PanoramaRef(item) for item in placeholders})
        if spec.ptype == "file_array":
            current = check.get(name) or []
            check[name] = [*current, *placeholders]
        elif check.get(name) is None:
            check[name] = placeholders[0]
    prepared, errors = panorama_contract.prepare(contract, schema.specs, check, panorama_refs=refs)
    for name, value in prepared.items():
        spec = schema.by_name(name)
        if spec is not None and not spec.is_file:
            body[name] = value
    return errors


def model_duration_value(lane_state):
    """The Render Video model chosen output duration in seconds, or None."""
    schema = schema_for(lane_state.model_id)
    spec = schema.by_name("duration") if schema is not None else None
    if spec is None:
        return None
    index = lane_state.params.find("duration")
    if index < 0:
        return None
    item = lane_state.params[index]
    try:
        if spec.allowed_values:
            return float(item.enum_value)
        return float(item.int_value if spec.is_integer else item.float_value)
    except (ValueError, TypeError):
        return None


def sync_shot_duration(scene):
    """With Match timeline on, the camera path (and the captured clip) lasts exactly the model video duration, so the
    motion maps one to one. Called from property callbacks, never during draw, so it may write scene data."""
    if scene is None or not hasattr(scene, "scenario_shot"):
        return
    lane_state = scene.scenario.lane_state("render_video")
    if lane_state is None or not lane_state.match_timeline:
        return
    value = model_duration_value(lane_state)
    if value is None or value <= 0:
        return
    shot = scene.scenario_shot
    prop = shot.bl_rna.properties["duration"]
    value = max(prop.hard_min, min(prop.hard_max, value))
    if abs(shot.duration - value) > 1e-6:
        shot.duration = value


def apply_match_timeline(scene, lane_state, schema):
    """Drive the model's duration from the timeline: a choice list (Seedance: Auto, 4..15) picks the first value that
    fits the clip; a numeric range (Minimax H3: 5 to 15 s) takes the clip length rounded up and clamped. The capture is
    then trimmed or padded to that duration in perform_captures, so the clip and the video always have the same length."""
    spec = schema.by_name("duration")
    if spec is None or not lane_state.match_timeline:
        return None
    fps = scene.render.fps / (scene.render.fps_base or 1.0)
    _, _, seconds = capture_plan.frame_span(
        scene.frame_start,
        scene.frame_end,
        fps,
        use_preview=scene.use_preview_range,
        preview_start=scene.frame_preview_start,
        preview_end=scene.frame_preview_end,
    )
    index = lane_state.params.find("duration")
    if index < 0:
        return None
    item = lane_state.params[index]
    if spec.allowed_values:
        value, note = capture_plan.choose_duration(seconds, spec.allowed_values)
        if value is not None and item.enum_value != str(value):
            item.enum_value = str(value)
    elif spec.ptype == "number":
        value, note = capture_plan.clamp_duration(
            seconds, spec.min, spec.max, integer=spec.is_integer
        )
        if spec.is_integer:
            if item.int_value != int(value):
                item.int_value = int(value)
        elif abs(item.float_value - value) > 1e-6:
            item.float_value = float(value)
    else:
        return None
    if not item.enabled:
        item.enabled = True
    return value, note, seconds


def timeline_sync_info(scene, lane_state, schema):
    """(clip seconds, model duration, note) for the UI, without touching the form."""
    spec = schema.by_name("duration") if schema else None
    fps = scene.render.fps / (scene.render.fps_base or 1.0)
    _, _, seconds = capture_plan.frame_span(
        scene.frame_start,
        scene.frame_end,
        fps,
        use_preview=scene.use_preview_range,
        preview_start=scene.frame_preview_start,
        preview_end=scene.frame_preview_end,
    )
    if spec is None:
        return seconds, None, ""
    if spec.allowed_values:
        value, note = capture_plan.choose_duration(seconds, spec.allowed_values)
    elif spec.ptype == "number":
        value, note = capture_plan.clamp_duration(
            seconds, spec.min, spec.max, integer=spec.is_integer
        )
    else:
        return seconds, None, ""
    return seconds, value, note


def perform_captures(context, request, runner=None):
    """Run the pending viewport/camera captures on the main thread and turn them into files."""
    from . import capture

    scene = context.scene
    lane_state = scene.scenario.lane_state(request.lane)
    schema = schema_for(request.model_id)
    limit = None
    chosen = request.body.get("duration")
    duration_spec = schema.by_name("duration") if schema else None
    if isinstance(chosen, (int, float)) and chosen > 0:
        limit = float(chosen)
    elif duration_spec and duration_spec.allowed_values:
        numeric = [
            int(v)
            for v in duration_spec.allowed_values
            if isinstance(v, (int, float)) and int(v) > 0
        ]
        limit = max(numeric) if numeric else None
    elif duration_spec and duration_spec.ptype == "number" and duration_spec.max:
        limit = float(duration_spec.max)

    def _add(cap, path):
        if cap.get("param") is None:
            return
        target = request.files.setdefault(cap["param"], [])
        if cap.get("first"):
            target.insert(0, path)
        else:
            target.append(path)

    for cap in request.captures:
        source = cap["source"]
        if source == "RENDER":
            continue
        if source == "MESH":
            from . import mesh_export

            objects = mesh_export.source_objects(context)
            path = mesh_export.export_glb(context, objects)
            request.meta["source_object"] = objects[0].name if objects else ""
            request.meta["source_objects"] = [o.name for o in objects]
            _add(cap, path)
            continue
        camera = bpy.data.objects.get(cap["camera"]) if cap.get("camera") else None
        base = "CAMERA" if source.startswith("CAMERA") else "VIEWPORT"
        force_solid = bool(lane_state.force_solid) if lane_state is not None else False
        if cap.get("role") == "spark":
            # a still for Prompt Spark to look at, never sent to the generation model; taken at the first frame of the clip
            path = capture.new_capture_path("spark", "png")
            request.meta["spark_image"] = capture.first_frame_still(
                context, path, source=base, camera=camera, force_solid=False, runner=runner
            )
            continue
        if source in props.CLIP_SOURCES:
            fps = scene.render.fps / (scene.render.fps_base or 1.0)
            start, end, _ = capture_plan.frame_span(
                scene.frame_start,
                scene.frame_end,
                fps,
                use_preview=scene.use_preview_range,
                preview_start=scene.frame_preview_start,
                preview_end=scene.frame_preview_end,
            )
            if limit:
                start, end = capture_plan.clip_frames_for(limit, fps, start, end)
            # the clip lasts exactly the duration the model was asked for (and never less than Seedance's 4 s)
            target = (
                max(capture_plan.MIN_CLIP_SECONDS, float(chosen))
                if isinstance(chosen, (int, float)) and chosen > 0
                else capture_plan.MIN_CLIP_SECONDS
            )
            start, end, padded = capture_plan.ensure_min_frames(start, end, fps, min_seconds=target)
            if padded:
                runtime.set_message(
                    f"Clip padded to {target:g} s (frames {start} to {end}) to match the video duration"
                )
            path = capture.new_capture_path("playblast", "mp4")
            info = capture.capture_playblast(
                context,
                path,
                source=base,
                camera=camera,
                frame_start=start,
                frame_end=end,
                force_solid=force_solid,
                runner=runner,
            )
            _add(cap, info["path"])
            request.meta.setdefault("captures", []).append(info["path"])
        else:
            path = capture.new_capture_path("still", "png")
            still = capture.capture_still(
                context, path, source=base, camera=camera, force_solid=force_solid, runner=runner
            )
            _add(cap, still)
            request.meta.setdefault("captures", []).append(still)
            if (
                cap.get("first")
                and request.spark is not None
                and request.spark.get("kind") == "image"
            ):
                request.meta["spark_image"] = still
    request.captures = []
    return request


def request_meta(context, lane, request=None):
    lane_state = context.scene.scenario.lane_state(lane)
    record = runtime.state.records.get(lane_state.model_id)
    meta = {
        "prompt": lane_state.prompt,
        "model_name": record.name if record else lane_state.model_id,
    }
    if len(lane_state.trained):
        # Job views name the LoRAs or composition with the base ("FLUX.1 Dev · LoRA (+1)").
        meta["model_name"] = trained_models.selection_label(lane_state, meta["model_name"])
        meta["trained_models"] = trained_models.selection_meta(lane_state)
    if lane == "material":
        meta["target_objects"] = [o.name for o in context.selected_objects if o.type == "MESH"]
    if request is not None:
        # the local files that went into the job, so Generations can show what was used (like the web app)
        inputs = []
        for paths in request.files.values():
            inputs.extend(p for p in paths if p not in inputs)
        meta["inputs"] = inputs
        meta.update(request.meta)
    return meta


def _prune_ui_estimates():
    """Property callbacks clear keys before repricing; retire their old UI handles."""
    jobs = runtime.state.model_jobs
    for key, ticket in tuple(runtime.state.estimates.items()):
        try:
            current = ticket.scene.scenario.lane_state(ticket.lane).estimate_key == key
        except ReferenceError:
            current = False
        if not current:
            del runtime.state.estimates[key]
            if jobs is not None and jobs.quotes.get(ticket.identifier) is ticket:
                jobs.quotes.pop(ticket.identifier)


def request_estimate(scene, lane):
    runtime.sync_catalog_context()
    _prune_ui_estimates()
    lane_state = scene.scenario.lane_state(lane)
    previous = runtime.state.estimates.pop(lane_state.estimate_key, None)
    if previous is not None and runtime.state.model_jobs is not None:
        # Repricing this form explicitly supersedes its former UI approval.
        runtime.state.model_jobs.quotes.pop(previous.identifier, None)
    lane_state.estimate_key = ""
    request = build_request(scene, lane, for_estimate=True)
    if request.spark:
        from .render_prompt_jobs import request_price

        lane_state.estimate_state = "UNAVAILABLE"
        lane_state.estimate_error = "Approve the Prompt Spark price before pricing the render"
        try:
            request_price(scene, lane)
        except ScenarioError as error:
            lane_state.estimate_error = error.reason
        except Exception:
            lane_state.estimate_error = (
                "Could not prepare the look; check references and connection"
            )
        return
    if request.files or request.captures:
        request.errors.append("Use uploaded Scenario references before requesting a final price")
    if request.errors:
        lane_state.estimate_state = "UNAVAILABLE"
        lane_state.estimate_error = request.errors[0]
        return
    key = uuid.uuid4().hex
    log.info("estimate requested %s", key)
    lane_state.estimate_key = key
    lane_state.estimate_state = "PENDING"
    lane_state.estimate_partial = request.partial
    try:
        jobs = runtime.ensure_model_jobs()
        ticket = jobs.quote(scene, request.model_id, request.body, lane=lane)
        runtime.state.model_previews[key] = ticket
    except ScenarioError as err:
        runtime.state.estimate_origins.pop(key, None)
        lane_state.estimate_state = "ERROR"
        lane_state.estimate_error = err.reason
    except Exception:
        lane_state.estimate_state = "ERROR"
        lane_state.estimate_error = "Could not request a price for the selected scene"


def process_model_jobs():
    """Deliver owned quote tasks without requiring an open panel or GUI timer."""
    jobs = runtime.state.model_jobs
    if jobs is None:
        return
    _prune_ui_estimates()
    jobs.poll()
    runtime.show_job_views(jobs.views.values())
    for key, ticket in tuple(runtime.state.model_previews.items()):
        if not ticket.task.done():
            continue
        del runtime.state.model_previews[key]
        try:
            lane_state = ticket.scene.scenario.lane_state(ticket.lane)
            if lane_state.estimate_key != key:
                jobs.session.drain(task=ticket.task)
                jobs.quotes.pop(ticket.identifier, None)
                continue
            estimate = jobs.finish_quote(ticket)
            runtime.state.estimates[key] = ticket
            lane_state.estimate_cu = float(estimate.cost)
            lane_state.estimate_state = "READY"
            lane_state.estimate_error = ""
        except Exception as error:
            jobs.quotes.pop(ticket.identifier, None)
            # Source/transport details can contain private inputs. Keep failures
            # actionable without copying arbitrary exception text into the UI.
            # A refused LoRA or composition carries fixed text naming only
            # input labels and positions.
            if isinstance(error, (RouteQuoteError, DuplicateRequestError)):
                message = str(error)
            elif isinstance(error, model_jobs.AutomaticEstimatesStopped):
                message = error.reason
            else:
                message = "Could not confirm this price; estimate again"
            try:
                lane_state = ticket.scene.scenario.lane_state(ticket.lane)
                if lane_state.estimate_key != key:
                    continue
                if isinstance(error, OriginUnavailable) and ticket.scene != bpy.context.scene:
                    # Another scene was selected before this price arrived. The
                    # pump prices the form again once its own scene is selected.
                    props.mark_estimate_dirty(lane_state)
                else:
                    lane_state.estimate_state = "ERROR"
                    lane_state.estimate_error = message
            except ReferenceError:
                pass  # A removed scene cannot receive its old quote.


# After Generate, an unchanged form keeps its price and re-enables once Scenario has
# confirmed the job and at least this long has passed, so a double click cannot send
# twice. Each send still needs its own free, single-use server quote.
REPEAT_DELAY = 2.0


@dataclass
class Repeat:
    """One form waiting to offer Generate again; in memory on the main thread only."""

    request_id: str
    not_before: float
    key: str = ""  # the fresh quote's estimate_key once it is requested


def _repeat(lane_state):
    """The form's pending repeat, or None once an edit made it another request."""
    try:
        key = (lane_state.id_data.session_uid, props.lane_of(lane_state))
    except (AttributeError, ReferenceError):
        return None
    entry = runtime.state.repeats.get(key)
    if entry is None:
        return None
    if entry.key:
        return entry if lane_state.estimate_key == entry.key else None
    return entry if lane_state.estimate_state == "IDLE" else None


def repeat_label(lane_state):
    """Generate keeps the last price while the unchanged form waits; read-only for drawing."""
    entry = _repeat(lane_state)
    return entry is not None and lane_state.estimate_state in ("IDLE", "PENDING")


def repeat_holding(lane_state):
    """Generate stays disabled until the delay passed; read-only for drawing."""
    entry = _repeat(lane_state)
    return entry is not None and props.clock() < entry.not_before


def rearm_repeat(scene, lane, lane_state, now):
    """Price an unchanged form again once Scenario confirmed its last submission (pump only).

    Returns True when the form changed. An edit, a removed scene or a finished repeat
    drops the entry; the form's own state shows from then on.
    """
    key = (scene.session_uid, lane)
    entry = runtime.state.repeats.get(key)
    if entry is None:
        return False
    if _repeat(lane_state) is not entry:
        del runtime.state.repeats[key]
        return False
    if entry.key:
        if lane_state.estimate_state != "PENDING" and now >= entry.not_before:
            del runtime.state.repeats[key]
            return True
        return False
    if model_jobs.automatic_estimates_stopped():
        del runtime.state.repeats[key]  # no automatic prices this session; the form stays idle
        return False
    jobs = runtime.state.model_jobs
    if jobs is not None and entry.request_id in jobs.submissions:
        return False  # still being sent: an identical quote would be refused
    if not (runtime.credentials().valid and runtime.online()):
        return False  # keep holding until a fresh quote can actually be requested
    props.mark_estimate_dirty(lane_state)
    lane_state.estimate_dirty_at = now - 1000.0  # price now; the delay gates Generate
    return True


def prune_repeats():
    """Forget repeats of scenes that no longer exist (pump only)."""
    if runtime.state.repeats:
        live = {scene.session_uid for scene in bpy.data.scenes}
        for key in [key for key in runtime.state.repeats if key[0] not in live]:
            del runtime.state.repeats[key]


def note_repeat_quote(scene, lane, lane_state):
    """Remember which quote the pump requested for a repeating form."""
    entry = runtime.state.repeats.get((scene.session_uid, lane))
    if entry is not None and not entry.key and lane_state.estimate_key:
        entry.key = lane_state.estimate_key


def submit_generation(context, lane):
    runtime.sync_catalog_context()
    scene = context.scene
    lane_state = scene.scenario.lane_state(lane)
    request = build_request(scene, lane, for_estimate=True)
    if request.errors:
        raise ScenarioError(0, "; ".join(request.errors))
    if request.files or request.captures or request.spark:
        raise ScenarioError(0, "Use uploaded Scenario references before requesting a final price")
    ticket = runtime.state.estimates.get(lane_state.estimate_key)
    if lane_state.estimate_state != "READY" or ticket is None:
        raise ScenarioError(0, "Wait for a fresh price before generating")
    if repeat_holding(lane_state):
        raise ScenarioError(0, "Generate is available again in a moment")
    # A rejected attempt can consume the handle before storage reports an
    # error. Never keep that price actionable or automatically reprice it.
    lane_state.estimate_state = "IDLE"
    runtime.state.estimates.pop(lane_state.estimate_key, None)
    try:
        jobs = runtime.ensure_model_jobs()
        estimate = jobs.finish_quote(ticket)
        rec = jobs.submit(
            ticket.identifier,
            scene,
            request.model_id,
            request.body,
            approved_cost=str(estimate.cost),
            lane=lane,
            meta=request_meta(context, lane, request),
        )
    except ScenarioError:
        lane_state.estimate_state = "ERROR"
        lane_state.estimate_error = "Inspect saved jobs and request a fresh price"
        raise
    except Exception:
        lane_state.estimate_state = "ERROR"
        lane_state.estimate_error = "Inspect saved jobs and request a fresh price"
        raise ScenarioError(
            0, "Could not submit; inspect saved jobs and request a fresh price"
        ) from None
    runtime.show_job_views((rec,))
    lane_state.estimate_state = "IDLE"
    lane_state.last_error = ""
    runtime.state.repeats[(scene.session_uid, lane)] = Repeat(
        rec.local_id, props.clock() + REPEAT_DELAY
    )
    runtime.set_message("Generation queued; its submission is saved for recovery")
    return rec


def _save_render_result(scene):
    image = bpy.data.images.get("Render Result")
    if image is None:
        return None
    path = runtime.paths().cache_dir / "captures" / f"render_{int(time.time())}.png"
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        image.save_render(str(path), scene=scene)
    except RuntimeError as err:
        log.warning("no render result to save: %s", err)
        return None
    return str(path)
