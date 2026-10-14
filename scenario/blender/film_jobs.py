# SPDX-FileCopyrightText: 2026 Scenario Inc.
# SPDX-License-Identifier: GPL-3.0-or-later
"""Film task controls over the selected session and ordinary model job lifecycle."""

import json
import os
import uuid
from dataclasses import dataclass, field, replace

import bpy

from ..core.api.errors import ScenarioError
from ..core.scene.film_plan import require_plan_scope, validate_film_plan


def recipe(scene):
    props = scene.scenario_film
    if not props.production_id or not props.recipe_json:
        raise ValueError("Load a Film recipe first")
    raw = json.loads(props.recipe_json)
    return raw, validate_film_plan(raw)


def load_recipe(scene, raw):
    """Validate before changing saved scene data; reload preserves production identity."""
    plan = validate_film_plan(raw)
    encoded = json.dumps(raw, allow_nan=False, ensure_ascii=False, sort_keys=True)
    props = scene.scenario_film
    selected = (
        props.tasks[props.task_index].name if 0 <= props.task_index < len(props.tasks) else ""
    )
    props.recipe_json = encoded
    if not props.production_id:
        props.production_id = uuid.uuid4().hex
    props.title = plan["title"]
    selected_shot = (
        props.shots[props.shot_index].name if 0 <= props.shot_index < len(props.shots) else ""
    )
    props.shots.clear()
    props.shot_index = 0
    for index, shot in enumerate(plan["shots"]):
        row = props.shots.add()
        row.name, row.title = shot["id"], shot["title"]
        if row.name == selected_shot:
            props.shot_index = index
    props.tasks.clear()
    props.task_index = 0
    for index, task in enumerate(plan["tasks"]):
        row = props.tasks.add()
        row.name, row.title, row.kind = task["id"], task["title"], task["kind"]
        row.model_id = task.get("model", "")
        if row.name == selected:
            props.task_index = index
    return plan


def snapshot(scene):
    props = scene.scenario_film
    return props.production_id, props.recipe_json


@dataclass
class FilmAction:
    identifier: str
    scene: object = field(repr=False)
    binding: tuple = field(repr=False)
    task_id: str
    task: object = field(repr=False)
    phase: str
    quote: object = field(default=None, repr=False)
    cost: str = ""
    request_id: str = ""
    error: str = ""
    completion: object = field(default=None, repr=False)


class FilmApprovalUnavailable(ScenarioError):
    """A preflight rejection before any submission persistence was attempted."""


class FilmJobs:
    """Retain bounded presentation handles only; jobs/uploads keep their existing owners."""

    def __init__(self, model_jobs, *, online=lambda: True):
        self.models = model_jobs
        self.session, self.store = model_jobs.session, model_jobs.store
        self._online = online
        self.actions = {}
        self._saved_actions = {}
        from .film_composition import FilmCompositionCommands

        self.compositions = FilmCompositionCommands(self)

    def current(self, scene, task_id):
        binding = snapshot(scene)
        for item in reversed((*self._saved_actions.values(), *self.actions.values())):
            try:
                if item.scene == scene and item.task_id == task_id and item.binding == binding:
                    return item
            except ReferenceError:
                # A retained RNA wrapper can outlive its scene. Drawing is read-only;
                # the maintenance pump still owns draining and retiring its action.
                continue
        return None

    def _retain_saved_status(self, item):
        if item.phase in {"SUBMITTED", "BOUND"}:
            try:
                key = (item.scene.session_uid, item.binding, item.task_id)
                self._saved_actions[key] = replace(item, task=None, quote=None)
            except ReferenceError:
                # Deleted scenes do not need presentation state.
                return
        # At most the current recipe's tasks for each live scene. This cache
        # carries no approval, worker or persistent reservation authority.
        for key, saved in tuple(self._saved_actions.items()):
            try:
                current = (
                    saved.scene in tuple(bpy.data.scenes) and snapshot(saved.scene) == saved.binding
                )
            except ReferenceError:
                current = False
            if not current:
                del self._saved_actions[key]

    def _check(self, item, scene):
        try:
            valid = (
                self.session.active
                and self.actions.get(item.identifier) is item
                and item.scene == scene
                and scene in tuple(bpy.data.scenes)
                and snapshot(scene) == item.binding
            )
            if valid and item.phase == "READY":
                valid = item.quote is not None and self.session.capture(scene) == item.quote.origin
        except ReferenceError:
            valid = False
        if not valid:
            raise ScenarioError(0, "The Film recipe, production or scene changed; inspect it again")

    def _start(self, scene, task_id, phase, **upload):
        if not self.session.active:
            raise ScenarioError(0, "The Film connection changed; inspect it again")
        self.poll()
        raw, plan = recipe(scene)
        require_plan_scope(plan, self.store.scope)
        current = self.current(scene, task_id)
        if current and current.phase in {"QUOTING", "BINDING"}:
            raise ScenarioError(0, "This Film task already has an action running")
        if current and current.phase == "READY":
            raise ScenarioError(
                0, "Approve or discard this Film estimate before requesting another"
            )
        if current and current.phase == "SUBMITTED":
            raise ScenarioError(
                0, "This Film task already has saved work; inspect it or name a new take"
            )
        if current and current.phase == "BOUND":
            saved = self.store.film_upload(current.binding[0], task_id)
            if (
                phase != "BINDING"
                or saved is None
                or upload.get("request_id") != saved.upload_request_id
                or upload.get("expected_revision") != saved.upload_revision
            ):
                raise ScenarioError(0, "This Film upload task is already bound; name a new task")
        if len(self.actions) >= 128:
            for key, old in tuple(self.actions.items()):
                if old.phase not in {"QUOTING", "BINDING", "READY"}:
                    self._retain_saved_status(old)
                    del self.actions[key]
                    break
            else:
                raise ScenarioError(0, "Finish or discard an existing Film estimate first")
        if scene == bpy.context.scene:
            bpy.context.view_layer.update()
        origin = self.session.capture(scene)
        binding = snapshot(scene)
        options = dict(production_id=binding[0], task_id=task_id, origin=origin)
        if phase == "QUOTING":
            if not self._online():
                raise ScenarioError(0, "Enable online access to request a Film price")
            task = self.session.quote_film_task(raw, **options)
        else:
            task = self.session.bind_film_upload(raw, **options, **upload)
        item = FilmAction(uuid.uuid4().hex, scene, binding, task_id, task, phase)
        self.actions[item.identifier] = item
        return item

    def quote(self, scene, task_id):
        return self._start(scene, task_id, "QUOTING")

    def bind_upload(self, scene, task_id, *, request_id, expected_revision):
        return self._start(
            scene, task_id, "BINDING", request_id=request_id, expected_revision=expected_revision
        )

    def discard(self, identifier, scene):
        item = self.actions.get(identifier)
        if item is None:
            raise ScenarioError(0, "The Film estimate is unavailable")
        self._check(item, scene)
        if item.phase != "READY":
            raise ScenarioError(0, "Only an unsubmitted ready Film estimate can be discarded")
        item.phase, item.quote = "DISCARDED", None

    def poll(self):
        if not self.session.active:
            return
        self.compositions.poll()
        for item in self.actions.values():
            if item.phase == "READY":
                try:
                    self._check(item, item.scene)
                except Exception:
                    item.phase, item.quote = "DISCARDED", None
                continue
            if item.phase not in {"QUOTING", "BINDING"} or not item.task.done():
                continue
            try:
                if item.completion is None:
                    completions = self.session.drain(task=item.task)
                    if len(completions) != 1:
                        raise ScenarioError(0, "The Film completion is unavailable")
                    item.completion = completions[0]
                self._check(item, item.scene)
                if item.scene != bpy.context.scene:
                    # Keep the bounded outcome here, freeing shared admission slots.
                    # Delivery still requires the unchanged source scene.
                    continue
                result = self.session.deliver(item.completion, lambda value, *_: value)
                item.completion = None
                if item.phase == "QUOTING":
                    item.quote = result
                    item.cost, item.phase = str(result.estimate.cost), "READY"
                else:
                    item.request_id = result.reference.upload_request_id
                    item.phase = "BOUND"
            except Exception:
                self.session.drain(task=item.task)
                item.completion = None
                item.phase, item.error = (
                    "ERROR",
                    (
                        "Film action could not complete in this context; inspect saved tasks before continuing"
                    ),
                )

    def finish(self, item, scene):
        self._check(item, scene)
        self.poll()
        if item.phase not in {"READY", "BOUND"}:
            raise ScenarioError(0, item.error or "The Film action is still running")
        return item

    def quote_details(self, item, scene):
        """Describe an existing approval without repricing or changing its origin."""
        self._check(item, scene)
        if item.phase != "READY" or item.quote is None:
            raise ScenarioError(0, "Use a ready Film estimate")
        return {
            "quote_id": item.identifier,
            "production_id": item.binding[0],
            "task_id": item.task_id,
            "model_id": item.quote.estimate.target_id,
            "parameters": item.quote.estimate.payload,
            "cu_cost_exact": item.cost,
        }

    def approve(self, identifier, scene, *, approved_cost):
        if os.environ.get("SCENARIO_GUI_PROBE") == "1":
            raise PermissionError("Generation is disabled while an automated GUI probe runs")
        try:
            self.poll()
            item = self.actions.get(identifier) if isinstance(identifier, str) else None
            if item is None or item.phase != "READY" or item.quote is None:
                raise ScenarioError(0, "Use a fresh Film estimate; inspect existing tasks first")
            self._check(item, scene)
            if approved_cost != item.cost or not self._online():
                raise ScenarioError(0, "Approve the unchanged exact Film price while online")
        except ScenarioError as error:
            raise FilmApprovalUnavailable(0, error.reason) from None
        # Consume before persistence, including a committed write with a lost acknowledgement.
        item.phase, item.error = "ERROR", "Submission needs review; inspect saved Film tasks"
        view = self.models.submit_film(item.quote, approved_cost=approved_cost)
        item.request_id, item.phase, item.error = view.local_id, "SUBMITTED", ""
        return view

    def inspect(self, scene):
        """Inspect saved work and existing quotes; never start network or scene work."""
        _, plan = recipe(scene)
        require_plan_scope(plan, self.store.scope)
        # Finish already-admitted preparation only in its unchanged source scene.
        # A deferred MCP response can fail while another scene is current.
        self.poll()
        production_id = scene.scenario_film.production_id
        rows = []
        tasks = list(plan["tasks"])
        names = {task["id"] for task in tasks}
        for mode in ("previs", "final"):
            master = plan[mode + "_master_task"]
            if master not in names and self.store.film_job(production_id, master) is not None:
                tasks.append({"id": master, "title": mode.title() + " master", "kind": "model"})
                names.add(master)
        for task in tasks:
            row = {"task_id": task["id"], "title": task["title"], "kind": task["kind"]}
            job = self.store.film_job(production_id, task["id"])
            upload = self.store.film_upload(production_id, task["id"])
            if job is not None:
                row.update(
                    request_id=job.intent.request_id,
                    state=job.state.value,
                    revision=job.revision,
                    cu_cost_exact=job.intent.quote_cost,
                )
            elif upload is not None:
                row.update(
                    upload_request_id=upload.upload_request_id,
                    upload_revision=upload.upload_revision,
                    asset_id=upload.asset_id,
                    state="bound",
                )
            else:
                row["state"] = "unstarted"
                item = self.current(scene, task["id"])
                if item is not None and item.phase == "READY":
                    row.update(self.quote_details(item, scene), state="quoted")
                elif item is not None and item.phase in {"QUOTING", "BINDING"}:
                    row["state"] = item.phase.lower()
            rows.append(row)
        return {
            "production_id": production_id,
            "title": plan["title"],
            "tasks": rows,
            "shots": [{"shot_id": shot["id"], "title": shot["title"]} for shot in plan["shots"]],
        }
