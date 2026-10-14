# SPDX-FileCopyrightText: 2026 Scenario Inc.
# SPDX-License-Identifier: GPL-3.0-or-later
"""Quote-bound Blockout plans on the application-owned SDK job session."""

import json
import time
import uuid
from dataclasses import dataclass, field

import bpy

from ..core.api.errors import ScenarioError
from ..core.jobs.coordinator import DuplicateRequestError
from ..core.jobs.store import JobState
from ..core.scene import blockout
from . import file_state

MODEL = "model_scenario-llm"
CANCELED = "Blockout request canceled; nothing was sent to Scenario"
BUSY = {"QUOTING", "SUBMITTING", "POLLING", "LISTING", "READING"}


def binding(scene):
    props = scene.scenario_blockout
    return props.prompt, props.refine, props.scene_type, props.scale, props.plan_json


def parse_complete_plan(text):
    return blockout.parse_complete_plan(text)


@dataclass
class BlockoutAction:
    identifier: str
    scene: object = field(repr=False)
    action: str
    binding: tuple = field(repr=False)
    task: object = field(repr=False)
    phase: str = "QUOTING"
    quote: object = field(default=None, repr=False)
    request_id: str = ""
    cost: str = ""
    error: str = ""
    next_poll: float = 0.0


class BlockoutJobs:
    """Own only presentation handles; the session owns workers and durable jobs."""

    def __init__(self, session, store, *, online=lambda: True):
        from .blockout_recovery import BlockoutRecovery

        self.session, self.store, self._online = session, store, online
        self.actions = {}
        self.recovery = BlockoutRecovery(session, store)

    def current(self, scene):
        return next(
            (item for item in reversed(tuple(self.actions.values())) if item.scene == scene), None
        )

    def _binding(self, item, scene):
        if (
            not self.session.active
            or item.scene != scene
            or scene not in tuple(bpy.data.scenes)
            or binding(scene) != item.binding
        ):
            raise ScenarioError(0, "The Blockout inputs or scene changed; request a new price")
        return scene.scenario_blockout

    def _reclaim_finished(self):
        for item in tuple(self.actions.values()):
            if item.task is not None or item.phase not in {"DONE", "ERROR"}:
                continue
            if item.request_id:
                record = self.store.get(item.request_id)
                # An ERROR presentation can still guard uncertain spending.
                # Retire only confirmed finished jobs, retaining durable history.
                if record is None or record.state not in {
                    JobState.SUCCEEDED,
                    JobState.FAILED,
                    JobState.CANCELED,
                    JobState.DOWNLOAD_FAILED,
                    JobState.READY,
                    JobState.APPLY_FAILED,
                    JobState.APPLIED,
                }:
                    continue
            self.actions.pop(item.identifier)
            return

    def quote(self, scene, action):
        if action not in {"DESIGN", "REFINE"}:
            raise ScenarioError(0, "Choose Design or Refine")
        current = self.current(scene)
        if current is not None and current.phase in BUSY:
            raise ScenarioError(0, "A Blockout plan is already running")
        if current is not None and current.request_id:
            record = self.store.get(current.request_id)
            if record is not None and record.state in {JobState.SUBMITTING, JobState.UNCERTAIN}:
                raise ScenarioError(0, "Inspect the uncertain saved Blockout job before continuing")
        snapshot = binding(scene)
        prompt, refine, scene_type, scale, previous = snapshot
        text = prompt if action == "DESIGN" else refine
        if not text.strip() or not self._online():
            raise ScenarioError(
                0, "Write the requested plan or refinement and enable online access"
            )
        original = None
        if action == "REFINE":
            try:
                original = parse_complete_plan(previous)
            except (ValueError, RecursionError):
                raise ScenarioError(
                    0, "Design or recover a complete Blockout plan before refining it"
                ) from None
        instruction = blockout.instruction(text, scene_type, scale, previous=original)
        if current is not None:
            self.actions.pop(current.identifier)
        if len(self.actions) >= 32:
            self._reclaim_finished()
        if len(self.actions) >= 32:
            raise ScenarioError(0, "Finish an existing Blockout action first")
        if scene == bpy.context.scene:
            bpy.context.view_layer.update()
        origin = self.session.capture(scene)
        task = self.session.quote_model(
            MODEL, {"instruction": instruction, "numOutputs": 1}, origin=origin
        )
        item = BlockoutAction(uuid.uuid4().hex, scene, action, snapshot, task)
        self.actions[item.identifier] = item
        return item

    def approve(self, identifier, scene, *, approved_cost):
        item = self.actions.get(identifier)
        if item is None or item.phase != "READY" or item.quote is None:
            raise ScenarioError(
                0, "Use a fresh Blockout quote; inspect existing jobs before continuing"
            )
        self._binding(item, scene)
        if approved_cost != item.cost or not self._online():
            raise ScenarioError(0, "Approve the unchanged exact price while online")
        item.phase, item.error = "ERROR", "Submission needs review; inspect saved jobs"
        prepared = self.session.prepare_quote(item.quote)
        item.request_id = prepared.intent.request_id
        estimate = item.quote.estimate
        item.task = self.session.submit(
            prepared,
            operation=estimate.operation,
            target_id=estimate.target_id,
            payload=estimate.payload,
        )
        item.phase, item.error = "SUBMITTING", ""
        return item

    def _completion(self, item):
        completions = self.session.drain(task=item.task)
        if not completions:
            raise ScenarioError(0, "Blockout command completion is unavailable")
        if completions[0].error is not None:
            raise completions[0].error
        return completions[0]

    def _apply_plan(self, item, result, scene, _target):
        if result.record.intent.request_id != item.request_id:
            raise ScenarioError(0, "The returned plan does not match this action")
        props = self._binding(item, scene)
        elements = parse_complete_plan(result.text)
        props.plan_json = json.dumps(elements)
        # Delivered by maintenance, after the approval operator has returned.
        file_state.mark_modified("Scenario Blockout plan")
        # Geometry is a separate explicit local action, never a late worker callback.

    def poll(self):
        if not self.session.active:
            return
        self.recovery.poll()
        for item in tuple(self.actions.values()):
            if item.phase not in BUSY:
                continue
            try:
                if item.task is not None:
                    if not item.task.done():
                        continue
                    completion = self._completion(item)
                    item.task = None
                    if item.phase == "QUOTING":
                        self._binding(item, item.scene)
                        item.quote = self.session.deliver(completion, lambda value, *_: value)
                        item.cost, item.phase = str(item.quote.estimate.cost), "READY"
                        continue
                    if item.phase == "READING":
                        self.session.deliver(
                            completion,
                            lambda result, scene, target, item=item: self._apply_plan(
                                item, result, scene, target
                            ),
                        )
                        item.phase = "DONE"
                        continue
                    if item.phase == "LISTING":
                        record = completion.result
                        if (
                            len(record.results) != 1
                            or record.results[0].asset.media_type != "text/plain"
                        ):
                            raise ScenarioError(0, "Choose a single plain-text Blockout result")
                        item.task = self.session.read_model_text(
                            item.request_id,
                            expected_revision=record.revision,
                            asset_id=record.results[0].asset.asset_id,
                        )
                        item.phase = "READING"
                        continue
                    if item.phase == "POLLING":
                        item.next_poll = time.monotonic() + 2
                record = self.store.get(item.request_id)
                if record is None:
                    raise ScenarioError(0, "The saved Blockout job is unavailable")
                if record.state == JobState.REMOTE:
                    item.phase = "POLLING"
                    if time.monotonic() >= item.next_poll and self._online():
                        item.task = self.session.refresh_remote(
                            item.request_id, expected_revision=record.revision
                        )
                elif record.state == JobState.SUCCEEDED:
                    if self._online():
                        item.phase = "LISTING"
                        item.task = self.session.load_results(
                            item.request_id, expected_revision=record.revision
                        )
                else:
                    raise ScenarioError(
                        0, "Inspect the saved Blockout job; do not repeat an uncertain submission"
                    )
            except Exception as error:
                item.task, item.phase = None, "ERROR"
                item.error = (
                    self._stopped(item.request_id)
                    if item.request_id
                    else str(error)
                    if isinstance(error, DuplicateRequestError)
                    else "Blockout price unavailable or inputs changed; request a new price"
                )

    def _stopped(self, request_id):
        # Local cancellation makes a queued submission fail its stored-state
        # check before dispatch; a remote job ID means Scenario saw the request.
        try:
            record = self.store.get(request_id)
        except Exception:
            record = None
        if record is not None and record.state == JobState.CANCELED and not record.remote_job_id:
            return CANCELED
        return "Blockout stopped; inspect the saved job"
