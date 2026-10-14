# SPDX-FileCopyrightText: 2026 Scenario Inc.
# SPDX-License-Identifier: GPL-3.0-or-later
"""Composition review on the existing Film/session owner and model job lifecycle."""

import copy
import os
import uuid
from dataclasses import dataclass, field

import bpy

from ..core.jobs.coordinator import OriginQuote
from ..core.jobs.film_media import VerifiedComposition
from ..core.jobs.media_probe import MediaProbeError
from ..core.scene.film_plan import FilmSourceError, require_plan_scope
from . import film_jobs
from .film_application import _main_thread


@dataclass(eq=False)
class _Review:
    identifier: str
    scene: object
    scene_name: str
    binding: tuple
    origin: object
    mode: str
    master: str
    frames: int
    fps: int
    phase: str = "PREPARING"
    task: object = field(default=None, repr=False)
    completion: object = field(default=None, repr=False)
    waiting_for: str = ""
    verified: object = field(default=None, repr=False)
    quote: object = field(default=None, repr=False)
    cost: str = ""
    request_id: str = ""
    error: str = ""
    sources: int = 0
    layers: int = 0
    parameters: object = field(default=None, repr=False)


class FilmCompositionCommands:
    """Bounded review handles only; no second store, executor or submission path."""

    def __init__(self, owner):
        self.owner, self.session = owner, owner.session
        self._reviews = {}

    def _get(self, identifier):
        try:
            return self._reviews[identifier]
        except (KeyError, TypeError):
            raise ValueError("The composition review is unavailable") from None

    def _check(self, review, *, selected=False):
        try:
            current = (
                self.session.active
                and review.scene in tuple(bpy.data.scenes)
                and not review.scene.library
                and not review.scene.override_library
                and film_jobs.snapshot(review.scene) == review.binding
                and self.session._origins.current(review.origin)
            )
        except ReferenceError:
            current = False
        if not current:
            raise ValueError("The Film recipe, scene or connection changed; prepare again")
        if selected:
            self.session.validate_destination(review.origin)

    def prepare(self, scene, *, mode="final", score_task_id="score"):
        _main_thread()
        if mode not in {"final", "previs"}:
            raise ValueError("Choose final or previs composition")
        if (
            not self.session.active
            or scene != bpy.context.scene
            or scene.library
            or scene.override_library
        ):
            raise ValueError("Select the current local Film recipe scene")
        self.poll()
        raw, plan = film_jobs.recipe(scene)
        require_plan_scope(plan, self.session.scope)
        existing = self.current(scene, mode)
        if existing and existing["phase"] not in {"ERROR", "CANCELLED", "DISCARDED"}:
            raise ValueError("Finish or discard this composition review first")
        if len(self._reviews) >= 16:
            retired = next(
                (
                    key
                    for key, review in self._reviews.items()
                    if review.task is None
                    and review.phase
                    in {"ERROR", "CANCELLED", "DISCARDED", "SUBMITTED", "SUBMISSION_REVIEW"}
                ),
                None,
            )
            if retired is None:
                raise ValueError("Finish or discard an existing composition review first")
            del self._reviews[retired]
        bpy.context.view_layer.update()
        review = _Review(
            uuid.uuid4().hex,
            scene,
            scene.name,
            film_jobs.snapshot(scene),
            self.session.capture(scene),
            mode,
            plan[mode + "_master_task"],
            sum(shot["frames"] for shot in plan["shots"]),
            plan["fps"],
        )
        self._check(review, selected=True)
        review.task = self.session.prepare_film_composition(
            raw,
            production_id=review.binding[0],
            origin=review.origin,
            mode=mode,
            score_task_id=score_task_id,
        )
        self._reviews[review.identifier] = review
        return self.status(review.identifier)

    def poll(self):
        _main_thread()
        for review in self._reviews.values():
            if review.phase in {"PREPARING", "READY", "QUOTING", "QUOTED", "WAITING"}:
                try:
                    self._check(review)
                except Exception:
                    if review.task is not None:
                        self.session.cancel_local_render(review.task)
                    review.phase, review.error = (
                        "ERROR",
                        "The Film recipe, scene or connection changed; prepare again",
                    )
                    review.completion = review.quote = review.verified = None
            if review.task is not None and review.task.done():
                completions = self.session.drain(task=review.task)
                review.task = None
                if review.phase == "CANCELLING":
                    review.phase = "CANCELLED"
                elif review.phase in {"PREPARING", "QUOTING"}:
                    if len(completions) != 1 or completions[0].error is not None:
                        error = completions[0].error if len(completions) == 1 else None
                        review.phase = "ERROR"
                        # Both first-party types name only local tools or recipe identifiers.
                        review.error = (
                            str(error)
                            if isinstance(error, (MediaProbeError, FilmSourceError))
                            else "Inspect saved media and model requirements before preparing again"
                        )
                    else:
                        review.waiting_for = review.phase
                        review.completion, review.phase = completions[0], "WAITING"
            if review.phase == "WAITING" and review.scene == bpy.context.scene:
                try:
                    self._check(review, selected=True)
                    result = self.session.deliver(review.completion, lambda value, *_: value)
                    if review.waiting_for == "PREPARING":
                        if not isinstance(result, VerifiedComposition):
                            raise ValueError("Unexpected composition result")
                        review.verified, review.phase = result, "READY"
                        review.sources = len(result.media)
                        review.parameters = result.draft.recipe["tasks"][-1]["parameters"]
                        review.layers = len(review.parameters["layers"])
                    else:
                        if (
                            not isinstance(result, OriginQuote)
                            or result.composition is not review.verified.draft
                        ):
                            raise ValueError("Unexpected composition quote")
                        review.quote, review.cost, review.phase = (
                            result,
                            str(result.estimate.cost),
                            "QUOTED",
                        )
                        review.parameters = result.estimate.payload
                except Exception:
                    review.phase, review.error = (
                        "ERROR",
                        "The composition needs a fresh review in its original scene",
                    )
                finally:
                    review.completion = None

    def estimate(self, identifier):
        _main_thread()
        self.poll()
        review = self._get(identifier)
        self._check(review, selected=True)
        if review.phase != "READY" or review.verified is None:
            raise ValueError("Prepare a ready composition before requesting its price")
        if not self.owner._online():
            raise PermissionError("Enable online access to request a composition price")
        review.task = self.session.quote_film_composition(review.verified, origin=review.origin)
        review.phase, review.error = "QUOTING", ""
        return self.status(identifier)

    def approve(self, identifier, *, approved_cost):
        _main_thread()
        if os.environ.get("SCENARIO_GUI_PROBE") == "1":
            raise PermissionError("Generation is disabled while an automated GUI probe runs")
        self.poll()
        review = self._get(identifier)
        self._check(review, selected=True)
        if review.phase != "QUOTED" or review.quote is None:
            raise ValueError("Use a fresh composition price and inspect existing jobs")
        if approved_cost != review.cost or not self.owner._online():
            raise ValueError("Approve the unchanged exact composition price while online")
        review.phase, review.error = (
            "SUBMISSION_REVIEW",
            "Inspect saved jobs before continuing; do not repeat an uncertain submission",
        )
        try:
            view = self.owner.models.submit_film(review.quote, approved_cost=approved_cost)
            review.request_id, review.phase, review.error = view.local_id, "SUBMITTED", ""
        except Exception:
            # Preparation can commit even if its acknowledgement or queue admission fails.
            saved = self.owner.store.film_job(review.binding[0], review.master)
            if saved is not None and saved.intent.film_task == review.quote.film_task:
                review.request_id = saved.intent.request_id
            raise
        finally:
            review.quote = None
        return self.status(identifier)

    def cancel(self, identifier):
        _main_thread()
        review = self._get(identifier)
        if review.phase not in {"PREPARING", "QUOTING", "WAITING"}:
            raise ValueError("Cancel only active composition preparation or pricing")
        review.completion = None
        if review.task is None:
            review.phase = "CANCELLED"
        else:
            self.session.cancel_local_render(review.task)
            review.phase = "CANCELLING"
        return self.status(identifier)

    def discard(self, identifier):
        _main_thread()
        review = self._get(identifier)
        if review.task is not None:
            raise ValueError("Wait for active work to stop before discarding its review")
        review.phase = "DISCARDED"
        review.quote = review.verified = review.completion = None
        review.parameters = None
        return self.status(identifier)

    def current(self, scene, mode):
        for review in reversed(tuple(self._reviews.values())):
            try:
                if (
                    review.scene == scene
                    and review.mode == mode
                    and review.phase != "DISCARDED"
                    and film_jobs.snapshot(scene) == review.binding
                ):
                    return self.status(review.identifier, include_parameters=False)
            except ReferenceError:
                continue
        return None

    def status(self, identifier, *, include_parameters=True):
        _main_thread()
        review = self._get(identifier)
        return {
            "review_id": identifier,
            "production_id": review.binding[0],
            "task_id": review.master,
            "mode": review.mode,
            "phase": review.phase,
            "scene": review.scene_name,
            "frames": review.frames,
            "fps": review.fps,
            "sources": review.sources,
            "layers": review.layers,
            "cu_cost_exact": review.cost,
            "request_id": review.request_id,
            "error": review.error,
            "parameters": copy.deepcopy(review.parameters) if include_parameters else None,
        }
