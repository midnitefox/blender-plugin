# SPDX-FileCopyrightText: 2026 Scenario Inc.
# SPDX-License-Identifier: GPL-3.0-or-later
"""Scenario changes outside an Undo operator mark the open file modified.

Blender asks to save on quit only when ``bpy.data.is_dirty`` is true, and only
an undo push sets it. Local MCP tools and the results, Film builds, plans and
uploads that maintenance delivers change the file outside any operator that
records Undo. Each test starts from a freshly read, unmarked file where it can
and checks that the change marks it through the shared ``file_state`` helper,
with the expected Undo History message.

Catalog seeding, project defaults, quotes and estimate state deliberately leave
the file unmarked: they would otherwise flag every opened file as modified. A
call refused before any change leaves it unmarked too, while a failure after a
partial change still marks it.

Blender 5.0 reports every background file as modified, even right after reading
it. There the tests check the recorded marks and pushes; from 5.1 they also see
the flag change from false to true.
"""

import threading
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import bpy
import test_blockout_jobs as blockout_tests
import test_film_review_controls as review_tests
import test_film_shot_controls as shot_tests
import test_film_timeline_controls as timeline_tests
import test_first_frame_handoff as frame_tests
import test_job_session as session_tests
import test_model_generation as model_tests
import test_prompt_tools as prompt_tests
import test_reference_form as reference_tests
import test_render_lanes as render_tests
import test_trained_section as section_tests
import test_workflow_controls as workflow_tests
import test_workflow_uploads as upload_tests
from helpers import assert_unmarked, file_marks, reset_scene, submodule


def fixture(case):
    """A test case with another case's setUp, tearDown and helpers, but none of its tests."""
    helpers = {
        name: value for name, value in vars(case).items() if not name.startswith(("test", "__"))
    }
    return type(f"{case.__name__}Fixture", (unittest.TestCase,), helpers)


class FileStateTests(unittest.TestCase):
    def setUp(self):
        reset_scene()
        self.module = submodule("blender.file_state")
        self.pushes = []
        push = self.module._push

        def record(message):
            self.pushes.append(message)
            return push(message)

        self.enterContext(patch.object(self.module, "_push", side_effect=record))

    def test_a_mark_records_one_undo_step_that_marks_the_file(self):
        assert_unmarked(self)
        self.assertTrue(self.module.mark_modified("Scenario fixture"))
        self.assertEqual(self.pushes, ["Scenario fixture"])
        self.assertTrue(bpy.data.is_dirty)

    def test_a_timer_context_without_a_window_marks_through_the_first_window(self):
        # Blender runs app timers with its first window; a mark must not depend on it.
        with bpy.context.temp_override(window=None):
            self.assertIsNone(bpy.context.window)
            self.assertTrue(self.module.mark_modified("Scenario fixture"))
        self.assertTrue(bpy.data.is_dirty)

    def test_a_mark_tags_no_data_and_keeps_captured_origins(self):
        session = session_tests.JobSessionTests()
        session.setUp()
        self.addCleanup(session.doCleanups)
        self.addCleanup(session.tearDown)
        origin = session.session.capture(session.scene, session.target)
        updates, history = [], []

        def changed(scene, depsgraph):
            updates.extend(update.id.name for update in depsgraph.updates)

        def undo(*_):
            history.append("undo")

        hooks = (
            (bpy.app.handlers.depsgraph_update_post, changed),
            (bpy.app.handlers.undo_pre, undo),
            (bpy.app.handlers.redo_pre, undo),
        )
        for handlers, callback in hooks:
            handlers.append(callback)
            self.addCleanup(handlers.remove, callback)
        self.assertTrue(self.module.mark_modified("Scenario fixture"))
        bpy.context.view_layer.update()
        self.assertEqual((updates, history), ([], []))
        self.assertTrue(session.session._origins.current(origin))
        self.assertTrue(bpy.data.is_dirty)

    def test_a_mark_during_a_stroke_waits_for_the_stroke_to_end(self):
        with patch.object(self.module, "_stroke_running", return_value=True):
            self.assertFalse(self.module.mark_modified("Scenario fixture"))
            self.assertFalse(self.module.flush())
        self.assertEqual(self.pushes, [])
        assert_unmarked(self)
        self.assertTrue(self.module.flush())
        self.assertTrue(bpy.data.is_dirty)
        self.assertFalse(self.module.flush())
        self.assertEqual(self.pushes, ["Scenario fixture"])  # recorded once

    def test_strokes_are_recognized_by_their_operator_family(self):
        def windows(*names):
            return [SimpleNamespace(modal_operators=[SimpleNamespace(bl_idname=n) for n in names])]

        running = self.module._stroke_running
        for name in (
            "SCULPT_OT_brush_stroke",
            "PAINT_OT_image_paint",
            "PAINTCURVE_OT_draw",
            "SCULPT_CURVES_OT_brush_stroke",
            "GREASE_PENCIL_OT_brush_stroke",
            "sculpt.mesh_filter",
        ):
            with self.subTest(name=name):
                self.assertTrue(running(windows("VIEW3D_OT_rotate", name)))
        for name in ("TRANSFORM_OT_translate", "SCENARIO_OT_composer_modal", "WM_OT_open_mainfile"):
            with self.subTest(name=name):
                self.assertFalse(running(windows(name)))
        self.assertFalse(running([SimpleNamespace()]))
        self.assertFalse(running(bpy.context.window_manager.windows))

    def test_reading_a_file_drops_a_waiting_mark(self):
        self.assertIn(self.module._load_pre, bpy.app.handlers.load_pre)
        with patch.object(self.module, "_stroke_running", return_value=True):
            self.module.mark_modified("Scenario fixture")
        reset_scene()
        self.assertFalse(self.module.flush())
        self.assertEqual(self.pushes, [])
        assert_unmarked(self)

    def test_marks_are_refused_off_the_main_thread(self):
        errors = []

        def mark():
            try:
                self.module.mark_modified("Scenario fixture")
            except RuntimeError as error:
                errors.append(str(error))

        thread = threading.Thread(target=mark)
        thread.start()
        thread.join(5)
        self.assertEqual(errors, ["Mark the file modified on Blender's main thread"])
        self.assertEqual(self.pushes, [])


class SceneToolTests(unittest.TestCase):
    def setUp(self):
        reset_scene()
        self.tools = submodule("mcp.tools_blender")
        mesh = bpy.data.meshes.new("Crate")
        self.crate = bpy.data.objects.new("Crate", mesh)
        bpy.context.scene.collection.objects.link(self.crate)
        bpy.context.view_layer.update()
        prefs = submodule("prefs").get_prefs()
        allowed = prefs.mcp_allow_python
        prefs.mcp_allow_python = True
        self.addCleanup(setattr, prefs, "mcp_allow_python", allowed)

    def test_reading_tools_leave_the_file_unmarked(self):
        with file_marks(self) as marks:
            self.tools.scene_summary({})
            self.tools.object_detail({"name": "Crate"})
            self.tools.datablocks_summary({})
            self.tools.blender_api_help({"path": "bpy.types.Object"})
            blocked = self.tools.execute_python({"code": "bpy.ops.wm.quit_blender()"})
        self.assertIn("blocked", blocked["error"])
        self.assertEqual(marks, [])
        assert_unmarked(self)

    def test_selection_marks_the_file(self):
        with file_marks(self) as marks:
            self.tools.select_objects({"names": ["Crate"]})
        self.assertTrue(self.crate.select_get())
        self.assertEqual(marks, ["Open Studio MCP selection"])
        self.assertTrue(bpy.data.is_dirty)

    def test_frame_marks_the_file(self):
        with file_marks(self) as marks:
            self.tools.set_frame({"frame": 42})
        self.assertEqual(bpy.context.scene.frame_current, 42)
        self.assertEqual(marks, ["Open Studio MCP frame"])
        self.assertTrue(bpy.data.is_dirty)

    def test_python_marks_the_file(self):
        with file_marks(self) as marks:
            result = self.tools.execute_python({"code": "bpy.data.objects['Crate'].name = 'Box'"})
        self.assertNotIn("error", result)
        self.assertIn("Box", bpy.data.objects)
        self.assertEqual(marks, ["Open Studio MCP Python"])
        self.assertTrue(bpy.data.is_dirty)

    def test_camera_path_marks_the_file(self):
        waypoints = [{"position": [6, 0, 2]}, {"position": [0, 6, 2]}]
        with file_marks(self) as marks:
            result = self.tools.camera_path({"waypoints": waypoints, "duration": 2})
        self.assertEqual(bpy.context.scene.camera.name, result["camera"])
        self.assertEqual(marks, ["Open Studio camera path"])
        self.assertTrue(bpy.data.is_dirty)

    def test_refused_arguments_leave_the_file_clean(self):
        planner = submodule("blender.shot_planner")
        for args in (
            {"duration": "bad"},
            {"focal": "wide"},
            {"preset": "nowhere"},
            {"waypoints": [{"position": [1, 2]}]},
            {"waypoints": [{"position": [1, 2, 3], "rotation_euler": ["up", 0, 0]}]},
            {"waypoints": [{"position": [1, 2, 3], "hold": "long"}]},
        ):
            with self.subTest(args=args), file_marks(self) as marks:
                with self.assertRaises(ValueError):
                    self.tools.camera_path(args)
                self.assertEqual(marks, [])
        with file_marks(self) as marks, self.assertRaises(ValueError):
            self.tools.set_frame({"frame": "later"})
        self.assertEqual(marks, [])
        assert_unmarked(self)
        self.assertIsNone(bpy.context.scene.camera)
        self.assertEqual(planner.marker_objects(bpy.context.scene), [])

    def test_an_unchanged_selection_or_frame_leaves_the_file_clean(self):
        with file_marks(self) as marks:
            self.tools.select_objects({"names": []})  # Nothing was selected.
            self.tools.set_frame({"frame": bpy.context.scene.frame_current})
        self.assertEqual(marks, [])
        assert_unmarked(self)

    def test_a_frame_that_only_clears_the_subframe_marks_the_file(self):
        scene = bpy.context.scene
        scene.frame_subframe = 0.5
        with file_marks(self) as marks:
            self.tools.set_frame({"frame": scene.frame_current})
        self.assertEqual(scene.frame_subframe, 0.0)
        self.assertEqual(marks, ["Open Studio MCP frame"])
        self.assertTrue(bpy.data.is_dirty)

    def test_a_failure_after_the_path_changed_still_marks_it(self):
        planner = submodule("blender.shot_planner")
        waypoints = [{"position": [6, 0, 2]}, {"position": [0, 6, 2]}]
        with (
            patch.object(planner, "build_path", side_effect=RuntimeError("fixture failure")),
            file_marks(self) as marks,
            self.assertRaises(RuntimeError),
        ):
            self.tools.camera_path({"waypoints": waypoints})
        self.assertEqual(len(planner.marker_objects(bpy.context.scene)), 2)
        self.assertEqual(marks, ["Open Studio camera path"])
        self.assertTrue(bpy.data.is_dirty)


class ResultTests(fixture(model_tests.ModelGenerationTests)):
    """Saved results that maintenance applies, from the native UI or local MCP."""

    def test_catalog_seeding_quotes_and_submission_leave_the_file_unmarked(self):
        model = {
            **self.model,
            "inputs": [
                *self.model["inputs"],
                {"name": "numOutputs", "type": "number", "min": 1, "max": 4, "default": 2},
            ],
        }
        self.model = model  # Later model reads return the same description.
        catalog = submodule("core.api.catalog")
        with file_marks(self) as marks:
            # Loading the catalog seeds the new parameter's default into the form.
            record = catalog.ModelRecord.from_api(model)
            self.generation.set_catalog([record], [record])
            self.generation.set_models([record], {})
            self.assertTrue(self.lane.params["numOutputs"].enabled)
            self.ui_quote()
            self.mcp_submit(self.mcp_quote())
            self.settle()
        self.assertEqual(marks, [])
        assert_unmarked(self)

    def test_mcp_image_generation_marks_the_file_when_maintenance_imports_it(self):
        self.result_fixture()
        result = self.mcp_submit(self.mcp_quote())
        with file_marks(self) as marks:
            self.deliver_results()
        self.assertEqual(self.store.get(result["local_id"]).state, self.storemod.JobState.APPLIED)
        self.assertTrue(set(bpy.data.images) - self.before_images)
        self.assertEqual(marks, ["Scenario image import"])
        self.assertTrue(bpy.data.is_dirty)

    def test_native_image_generation_marks_the_file_when_maintenance_imports_it(self):
        self.result_fixture()
        self.ui_quote()
        view = self.generation.submit_generation(bpy.context, "image")
        with file_marks(self) as marks:
            self.deliver_results()
        self.assertEqual(self.store.get(view.local_id).state, self.storemod.JobState.APPLIED)
        self.assertEqual(marks, ["Scenario image import"])
        self.assertTrue(bpy.data.is_dirty)

    def test_recovered_images_mark_the_file(self):
        request_id = self.recovered_images()
        with file_marks(self) as marks:
            status = self.finish_application(self.prepare_import(request_id))
        self.assertEqual(status["status"], "applied", status)
        self.assertEqual(marks, ["Scenario image import"])
        self.assertTrue(bpy.data.is_dirty)

    def test_saved_media_marks_the_file(self):
        request_id = self.recovered_media()
        with file_marks(self) as marks:
            status = self.finish_application(self.prepare_media(request_id))
        self.assertEqual(status["status"], "applied", status)
        self.assertEqual(marks, ["Scenario media import"])
        self.assertTrue(bpy.data.is_dirty)

    def test_saved_model_marks_the_file_without_desktop_history(self):
        request_id = self.recovered_model()
        with file_marks(self) as marks:
            status = self.finish_application(self.prepare_model(request_id))
        self.assertEqual(status["status"], "applied", status)
        self.assertEqual(marks, ["Scenario model import"])
        self.assertTrue(bpy.data.is_dirty)

    def test_saved_model_history_checkpoints_mark_the_file_without_another_step(self):
        request_id = self.recovered_model()
        module = submodule("blender.model_application")
        pushes = []
        push = module._undo_push

        def record(message):
            pushes.append(message)
            return push(message)

        with (
            patch.object(module, "_undo_enabled", return_value=True),
            patch.object(module, "_undo_push", side_effect=record),
            file_marks(self) as marks,
        ):
            status = self.finish_application(self.prepare_model(request_id))
        self.assertEqual(status["status"], "applied", status)
        self.assertEqual(pushes, ["Before Scenario model import", "Scenario model import"])
        self.assertEqual(marks, [])
        self.assertTrue(bpy.data.is_dirty)

    def test_saved_world_and_its_restoration_mark_the_file(self):
        request_id = self.recovered_panorama()
        previous = bpy.context.scene.world
        with file_marks(self) as marks:
            status = self.finish_application(self.prepare_world(request_id))
        self.assertEqual(status["status"], "applied", status)
        self.assertEqual(marks, ["Scenario World"])
        self.assertTrue(bpy.data.is_dirty)
        restore = self.prepare_world(request_id, restore=True)
        with file_marks(self, clean=False) as marks:
            self.tools.apply_result_application(self.import_args(restore))
        self.assertEqual(bpy.context.scene.world, previous)
        self.assertEqual(marks, ["Scenario World restore"])

    def test_a_refused_world_restoration_leaves_the_file_clean(self):
        request_id = self.recovered_panorama()
        self.finish_application(self.prepare_world(request_id))
        restore = self.prepare_world(request_id, restore=True)
        changed = bpy.data.worlds.new("Changed after application")
        bpy.context.scene.world = changed  # Restoration refuses a World changed since.
        refused = (submodule("blender.world_application").WorldApplicationError, self.request_error)
        with file_marks(self, clean=False) as marks, self.assertRaises(refused):
            self.tools.apply_result_application(self.import_args(restore))
        self.assertEqual(bpy.context.scene.world, changed)
        self.assertEqual(marks, [])

    def test_saved_material_marks_the_file(self):
        request_id = self.recovered_material()
        with file_marks(self) as marks:
            status = self.finish_application(self.prepare_material(request_id))
        self.assertEqual(status["status"], "applied", status)
        self.assertTrue(status["materials"], status)
        self.assertEqual(marks, ["Scenario material"])
        self.assertTrue(bpy.data.is_dirty)

    def test_saved_mesh_edit_marks_the_file_without_desktop_history(self):
        request_id = self.recovered_mesh_edit()
        with file_marks(self) as marks:
            status = self.finish_application(self.prepare_mesh_edit(request_id))
        self.assertEqual(status["status"], "applied", status)
        self.assertEqual(marks, ["Scenario mesh edit"])
        self.assertTrue(bpy.data.is_dirty)

    def test_film_recipe_load_and_new_production_mark_the_file(self):
        recipe = {
            "title": "Marked recipe",
            "shots": [
                {
                    "id": "shot",
                    "title": "Shot",
                    "duration": 4,
                    "scene": submodule("core.scene.film_scene_plan").local_plan("studio", "", 4),
                }
            ],
            "tasks": [{"id": "source", "title": "Reference", "kind": "upload"}],
        }
        with file_marks(self) as marks:
            self.tools.film_recipe({"action": "load", "recipe": recipe})
            self.tools.film_recipe({})  # Inspection changes nothing.
        self.assertEqual(bpy.context.scene.scenario_film.title, "Marked recipe")
        self.assertEqual(marks, ["Scenario Film recipe"])
        self.assertTrue(bpy.data.is_dirty)
        with file_marks(self, clean=False) as marks:
            self.tools.film_recipe({"action": "load", "recipe": recipe})  # The same recipe.
            self.assertEqual(marks, [])
            self.tools.film_recipe({"action": "new_production"})
        self.assertEqual(marks, ["Scenario Film recipe"])

    def test_refused_film_recipes_leave_the_file_clean(self):
        with file_marks(self) as marks:
            with self.assertRaises(ValueError):
                self.tools.film_recipe({"action": "load", "recipe": {"title": "No shots"}})
            with self.assertRaises(ValueError):
                self.tools.film_recipe({"action": "new_production"})  # No recipe yet.
            with self.assertRaises(ValueError):
                self.tools.film_recipe({"action": "rename"})
        self.assertEqual(bpy.context.scene.scenario_film.recipe_json, "")
        self.assertEqual(marks, [])
        assert_unmarked(self)

    def test_a_recipe_load_that_fails_part_way_marks_the_file(self):
        film = submodule("blender.film_jobs")
        recipe = {
            "title": "Interrupted recipe",
            "shots": [
                {
                    "id": "shot",
                    "title": "Shot",
                    "duration": 4,
                    "scene": submodule("core.scene.film_scene_plan").local_plan("studio", "", 4),
                }
            ],
            "tasks": [{"id": "source", "title": "Reference", "kind": "upload"}],
        }
        # The recipe text is stored before a new production gets its identity.
        with (
            patch.object(film.uuid, "uuid4", side_effect=RuntimeError("fixture failure")),
            file_marks(self) as marks,
            self.assertRaises(RuntimeError),
        ):
            self.tools.film_recipe({"action": "load", "recipe": recipe})
        self.assertTrue(bpy.context.scene.scenario_film.recipe_json)
        self.assertEqual(marks, ["Scenario Film recipe"])
        self.assertTrue(bpy.data.is_dirty)

    def test_native_film_recipe_operators_record_undo(self):
        film = submodule("blender.film")
        for operator in (film.SCENARIO_OT_load_film, film.SCENARIO_OT_new_film_production):
            with self.subTest(operator=operator.bl_idname):
                self.assertIn("UNDO", operator.bl_options)
        with file_marks(self) as marks:
            # Python calls record an operator's Undo only when asked, as a click does.
            self.assertEqual(
                bpy.ops.scenario.new_film_production("EXEC_DEFAULT", True), {"FINISHED"}
            )
        self.assertEqual(marks, [])
        self.assertTrue(bpy.data.is_dirty)


class ProjectDefaultTests(fixture(section_tests.TrainedSectionTests)):
    def test_a_form_taking_its_project_default_leaves_the_file_unmarked(self):
        self.choose_stack()
        self.defaults.load()
        self.save_default()
        self.release(self.lane)
        with file_marks(self) as marks:
            self.assertTrue(self.defaults.apply_pending())
        trained = [slot.model_id for slot in self.lane.trained]
        self.assertEqual(trained, [section_tests.STUDIO_LORA, section_tests.LORA_A])
        self.assertEqual(marks, [])
        assert_unmarked(self)


class FirstFrameTests(fixture(frame_tests.FirstFrameHandoffTests)):
    def test_saved_first_frame_marks_the_file_without_desktop_history(self):
        request_id = self.saved_image()
        self.render_video(frame_tests.FRAME_AND_CLIP)
        approval = self.prepare(request_id)
        with file_marks(self) as marks:
            status = self.apply(approval)
        self.assertEqual(status["first_frame"]["state"], "bound", status)
        self.assertFalse(status["first_frame"]["undo_recorded"])
        self.assertEqual(marks, ["Scenario video first frame"])
        self.assertTrue(bpy.data.is_dirty)


class FilmReviewTests(fixture(review_tests.FilmReviewControlTests)):
    def setUp(self):
        reset_scene()
        super().setUp()

    def test_agent_build_marks_the_file(self):
        identifier = self.ready()
        with file_marks(self) as marks:
            status = self.tools.build_film_review({"review_id": identifier})
        self.assertEqual(status["phase"], "BUILT", status)
        self.assertEqual(marks, ["Scenario Film review"])
        self.assertTrue(bpy.data.is_dirty)

    def test_native_build_records_its_own_undo_step(self):
        identifier = self.ready()
        with file_marks(self) as marks:
            self.assertEqual(
                bpy.ops.scenario.build_film_review("EXEC_DEFAULT", True, review_id=identifier),
                {"FINISHED"},
            )
        self.assertEqual(self.commands.status(identifier)["phase"], "BUILT")
        self.assertEqual(marks, [])
        self.assertTrue(bpy.data.is_dirty)

    def test_a_refused_build_leaves_the_file_unmarked(self):
        identifier = self.ready()
        self.tools.film_review_status({"review_id": identifier, "action": "discard"})
        with file_marks(self) as marks, self.assertRaises(ValueError):
            self.tools.build_film_review({"review_id": identifier})
        self.assertEqual(marks, [])
        assert_unmarked(self)

    def test_a_rolled_back_build_leaves_the_file_unmarked(self):
        identifier = self.ready()
        with (
            patch.object(self.builder, "_sound", side_effect=ValueError("decode fixture failure")),
            file_marks(self) as marks,
        ):
            status = self.tools.build_film_review({"review_id": identifier})
        self.assertEqual(status["phase"], "ERROR", status)
        self.assertEqual(marks, [])
        assert_unmarked(self)

    def test_an_uncertain_build_that_kept_its_scene_marks_the_file(self):
        identifier = self.ready()
        original = self.store.transition

        def lose(*args, **kwargs):
            result = original(*args, **kwargs)
            if kwargs.get("state") == self.storage.JobState.APPLIED:
                raise OSError("lost receipt acknowledgement")
            return result

        with patch.object(self.store, "transition", side_effect=lose), file_marks(self) as marks:
            status = self.tools.build_film_review({"review_id": identifier})
        self.assertEqual(status["phase"], "UNCERTAIN", status)
        self.assertTrue(status["review_scene"])
        self.assertEqual(marks, ["Scenario Film review"])
        self.assertTrue(bpy.data.is_dirty)


class FilmShotTests(fixture(shot_tests.FilmShotControlTests)):
    def setUp(self):
        reset_scene()
        super().setUp()

    def test_agent_build_marks_the_file(self):
        identifier = self.mcp_prepare()
        self.assertEqual(self.settle(identifier)["phase"], "READY")
        with file_marks(self) as marks:
            status = self.tools.build_film_shot({"review_id": identifier})
        self.assertEqual(status["phase"], "BUILT", status)
        self.assertEqual(marks, ["Scenario Film shot"])
        self.assertTrue(bpy.data.is_dirty)

    def test_an_uncertain_build_that_built_nothing_leaves_the_file_unmarked(self):
        identifier = self.mcp_prepare()
        self.assertEqual(self.settle(identifier)["phase"], "READY")
        with (
            patch.object(
                self.session, "_claim_saved_application", side_effect=OSError("Before write")
            ),
            file_marks(self) as marks,
        ):
            status = self.tools.build_film_shot({"review_id": identifier})
        self.assertEqual(status["phase"], "UNCERTAIN", status)
        self.assertEqual(marks, [])
        assert_unmarked(self)


class FilmTimelineTests(fixture(timeline_tests.FilmTimelineControlTests)):
    def setUp(self):
        reset_scene()
        super().setUp()

    def test_agent_build_marks_the_file(self):
        identifier = self.prepare()
        with file_marks(self) as marks:
            status = self.tools.build_film_timeline({"review_id": identifier})
        self.assertEqual(status["phase"], "BUILT", status)
        self.assertEqual(marks, ["Scenario Film timeline"])
        self.assertTrue(bpy.data.is_dirty)


class BlockoutTests(fixture(blockout_tests.BlockoutJobsTests)):
    def test_delivered_plan_marks_the_file(self):
        item = self.quote()
        self.assertEqual(self.approve(item), {"FINISHED"})
        with file_marks(self) as marks:
            self.advance(item)
        self.assertEqual(item.phase, "DONE", item.error)
        self.assertTrue(self.scene.scenario_blockout.plan_json)
        self.assertEqual(marks, ["Scenario Blockout plan"])
        self.assertTrue(bpy.data.is_dirty)

    def test_agent_recovered_plan_marks_the_file(self):
        record = self.saved_plan()
        recovery, review = self.ready_review(record)
        keys = {"context_id": self.runtime.state.job_context_id, "review_id": review.identifier}
        with file_marks(self, clean=False) as marks:
            self.mcp.apply_blockout_plan(keys)
        self.assertTrue(self.scene.scenario_blockout.plan_json)
        self.assertEqual(marks, ["Scenario Blockout plan"])
        self.assertTrue(bpy.data.is_dirty)


class PromptTests(fixture(prompt_tests.PromptToolsTests)):
    def test_delivered_prompt_marks_the_file(self):
        item = self.quote()
        with file_marks(self) as marks:
            self.assertEqual(self.approve(item), {"FINISHED"})
            self.assertEqual(marks, [])  # Approval resets only the transient price.
            self.advance(item)
        self.assertEqual(item.phase, "DONE")
        self.assertEqual(self.lane.prompt, self.result_text)
        self.assertEqual(marks, ["Scenario prompt"])
        self.assertTrue(bpy.data.is_dirty)


class RenderFormTests(fixture(render_tests.RenderLanesTests)):
    def test_agent_form_edits_mark_the_file_and_inspection_does_not(self):
        self.image_lane.model_id = "model_google-gemini-3-1-flash"
        with file_marks(self) as marks:
            self.form()
            self.assertEqual(marks, [])
            with self.assertRaises(ValueError):
                self.form("configure", settings={"capture_source": "wrong"})
            self.assertEqual(marks, [])
            result = self.form("configure", settings={"look": "copper sculpture"})
        self.assertEqual(result["look"], "copper sculpture")
        self.assertEqual(marks, ["Scenario render form"])
        self.assertTrue(bpy.data.is_dirty)

    def test_refused_or_empty_form_edits_leave_the_file_clean(self):
        self.image_lane.model_id = "model_google-gemini-3-1-flash"
        form = submodule("blender.reference_form")
        with file_marks(self) as marks:
            self.form("configure", settings={})
            with self.assertRaises(ValueError):
                self.form("remove", reference_key="missing")
            with self.assertRaises(ValueError):
                self.form("prepare", role="sky")
            # A failed upload start that marked nothing removes its new slot again.
            with (
                patch.object(form, "start", side_effect=RuntimeError("fixture refusal")),
                self.assertRaises(RuntimeError),
            ):
                self.form("prepare", role="scene")
        self.assertEqual(len(self.image_lane.references), 0)
        self.assertEqual(marks, [])
        assert_unmarked(self)

    def test_an_upload_start_that_kept_its_marked_slot_marks_the_file(self):
        self.image_lane.model_id = "model_google-gemini-3-1-flash"
        form = submodule("blender.reference_form")

        def admitted_then_lost(context, index, lane_name):
            lane = context.scene.scenario.lane_state(lane_name)
            lane.references[index][form._MARKER] = "fixture-upload"
            raise RuntimeError("fixture uncertain admission")

        with (
            patch.object(form, "start", side_effect=admitted_then_lost),
            file_marks(self) as marks,
            self.assertRaises(RuntimeError),
        ):
            self.form("prepare", role="scene")
        self.assertEqual(len(self.image_lane.references), 1)
        self.assertEqual(marks, ["Scenario render form"])
        self.assertTrue(bpy.data.is_dirty)

    def test_a_configure_that_fails_part_way_marks_the_file(self):
        self.image_lane.model_id = "model_google-gemini-3-1-flash"
        params = submodule("blender.params_ui")
        with (
            patch.object(params, "sync_params", side_effect=RuntimeError("fixture failure")),
            file_marks(self) as marks,
            self.assertRaises(RuntimeError),
        ):
            self.form("configure", settings={"model_id": "model_openai-gpt-image-2"})
        self.assertEqual(self.image_lane.model_key, "model_openai-gpt-image-2")
        self.assertEqual(marks, ["Scenario render form"])
        self.assertTrue(bpy.data.is_dirty)


class ReferenceUploadTests(fixture(reference_tests.ReferenceFormTests)):
    def setUp(self):
        reset_scene()
        super().setUp()

    def test_delivered_reference_marks_the_file(self):
        binding = self.start()
        with file_marks(self) as marks:
            self.fixture.settle()
        self.assertTrue(binding.attached, binding.error)
        self.assertEqual(marks, ["Scenario reference upload"])
        self.assertTrue(bpy.data.is_dirty)


class WorkflowTests(fixture(workflow_tests.WorkflowControlTests)):
    def test_delivered_workflow_form_marks_the_file(self):
        with file_marks(self) as marks:
            self.load()
        self.assertEqual(self.form.title, "Fixture workflow")
        self.assertEqual(marks, ["Scenario workflow form"])
        self.assertTrue(bpy.data.is_dirty)

    def test_a_reload_that_changes_only_the_title_marks_the_file(self):
        self.form.workflow_id = "fixture-workflow"
        bpy.context.view_layer.update()
        self.assertFalse(self.wait(self.owner.start(self.scene, "load")).error)
        signature = self.ui.signature(self.form)
        self.fixture.workflow["name"] = "Renamed workflow"  # Renamed in Scenario since.
        with file_marks(self, clean=False) as marks:
            view = self.wait(self.owner.start(self.scene, "load"))
        self.assertFalse(view.error)
        self.assertEqual(self.form.title, "Renamed workflow")
        # The title is not part of the approval identity.
        self.assertEqual(self.ui.signature(self.form), signature)
        self.assertEqual(marks, ["Scenario workflow form"])

    def test_a_refused_workflow_schema_leaves_the_file_clean(self):
        self.form.workflow_id = "fixture-workflow"
        refusal = ValueError("This workflow has too many inputs for the native form")
        with patch.object(self.ui, "fields_for", side_effect=refusal), file_marks(self) as marks:
            view = self.wait(self.owner.start(self.scene, "load"))
        self.assertTrue(view.error)
        self.assertEqual(len(self.form.inputs), 0)
        self.assertEqual(marks, [])
        assert_unmarked(self)

    def test_a_workflow_form_load_that_fails_part_way_marks_the_file(self):
        self.load()
        self.assertTrue(self.form.inputs)

        def interrupted(form, record):
            form.inputs.clear()
            raise RuntimeError("fixture failure after clearing the inputs")

        self.form.workflow_id = "fixture-workflow"
        with (
            patch.object(self.ui, "load_form", side_effect=interrupted),
            file_marks(self, clean=False) as marks,
        ):
            view = self.wait(self.owner.start(self.scene, "load"))
        self.assertTrue(view.error)
        self.assertEqual(len(self.form.inputs), 0)
        self.assertEqual(marks, ["Scenario workflow form"])


class WorkflowUploadTests(fixture(upload_tests.WorkflowUploadTests)):
    def setUp(self):
        reset_scene()
        super().setUp()

    def test_delivered_workflow_upload_marks_the_file(self):
        binding = self.start()
        with file_marks(self) as marks:
            self.fixture.settle()
        self.assertTrue(binding.attached, binding.error)
        self.assertEqual(marks, ["Scenario workflow upload"])
        self.assertTrue(bpy.data.is_dirty)


if __name__ == "__main__":
    unittest.main()
