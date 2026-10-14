# SPDX-FileCopyrightText: 2026 Scenario Inc.
# SPDX-License-Identifier: GPL-3.0-or-later
"""Installed MCP Python gate, private capture-file lifetime and the agent upload allowlist."""

import base64
import hashlib
import json
import os
import shutil
import tempfile
import time
import unittest
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

import bpy
from helpers import FIXTURES, submodule


class McpSecurityTests(unittest.TestCase):
    def setUp(self):
        self.tools = submodule("mcp.tools_blender")

    def test_execute_python_fails_closed_without_preferences(self):
        prefs = submodule("prefs")
        with patch.object(prefs, "get_prefs", return_value=None):
            with self.assertRaisesRegex(PermissionError, "not available"):
                self.tools.execute_python({"code": "raise AssertionError('must not execute')"})

    def test_execute_python_requires_enabled_preference(self):
        prefs = submodule("prefs").get_prefs()
        original = prefs.mcp_allow_python
        try:
            prefs.mcp_allow_python = False
            with self.assertRaises(PermissionError):
                self.tools.execute_python({"code": "result['value'] = 1"})
            prefs.mcp_allow_python = True
            result = self.tools.execute_python({"code": "result['value'] = 1"})
            self.assertEqual(result["result"]["value"], 1)
        finally:
            prefs.mcp_allow_python = original

    def test_private_png_lifetime_and_permissions(self):
        with self.tools._private_png("test.png") as path:
            directory = Path(path).parent
            if os.name == "posix":
                self.assertEqual(directory.stat().st_mode & 0o777, 0o700)
            Path(path).write_bytes(b"image")
        self.assertFalse(directory.exists())

    def test_still_output_retains_success_and_removes_partial_capture_read_and_decode_errors(self):
        root = Path(self.enterContext(tempfile.TemporaryDirectory(prefix="capture-contract-")))
        self.enterContext(patch.object(self.tools, "_still_outbox", return_value=root))
        fixture = FIXTURES / "synthetic" / "reference-toadstool-512.png"
        for failure in (None, "capture", "read", "decode"):
            with self.subTest(failure=failure):
                created, before = [], set(root.iterdir())

                def write_capture(path, *, created=created, failure=failure):
                    created.append(Path(path).parent)
                    shutil.copyfile(fixture, path)
                    if failure == "capture":
                        raise RuntimeError("capture failure")
                    if failure == "read":
                        Path(path).unlink()
                    if failure == "decode":
                        Path(path).write_bytes(fixture.read_bytes()[:33] + b"corrupt PNG body")

                def output():
                    return self.tools._still_payload(
                        write_capture, source="VIEWPORT", expected_size=(512, 512)
                    )

                if failure:
                    with self.assertRaises((RuntimeError, ValueError)):
                        output()
                    self.assertEqual(set(root.iterdir()), before)
                else:
                    result = output()
                    metadata = json.loads(result[0]["text"])
                    retained = Path(metadata["path"])
                    self.assertTrue(metadata["retained"])
                    self.assertEqual(retained.read_bytes(), fixture.read_bytes())
                    self.assertEqual(
                        metadata["sha256"], hashlib.sha256(retained.read_bytes()).hexdigest()
                    )
                    self.assertEqual(result[1]["mimeType"], "image/png")
                    self.assertTrue(
                        base64.b64decode(result[1]["data"]).startswith(b"\x89PNG\r\n\x1a\n")
                    )
                self.assertEqual(len(created), 1)
                self.assertEqual(created[0].exists(), failure is None)

    def test_render_still_and_screenshot_leave_no_temp_directory_in_background(self):
        self.assertTrue(bpy.app.background)
        before = set(Path(tempfile.gettempdir()).glob("scenario-mcp-*"))
        with self.assertRaises(RuntimeError):
            self.tools.render_still({"source": "VIEWPORT"})
        with self.assertRaises(RuntimeError):
            self.tools.screenshot_viewport({})
        self.assertEqual(set(Path(tempfile.gettempdir()).glob("scenario-mcp-*")), before)

    def test_installed_tool_failures_return_sanitized_text(self):
        protocol = submodule("mcp.protocol")
        registry = submodule("blender.mcp_service").build_registry()
        installed = str(Path(protocol.__file__).resolve().parents[1])
        private = os.path.join(tempfile.gettempdir(), "scenario-mcp-fixture", "private.png")

        def call(name, arguments):
            response = protocol.handle_message(
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "tools/call",
                    "params": {"name": name, "arguments": arguments},
                },
                registry,
                {"name": "fixture", "version": "0"},
            )
            self.assertTrue(response["result"]["isError"], response)
            text = response["result"]["content"][0]["text"]
            self.assertNotIn("Traceback", text)
            self.assertNotIn(installed, text)
            self.assertNotIn(tempfile.gettempdir(), text)
            return text

        self.assertTrue(bpy.app.background)
        self.assertEqual(
            call("object_detail", {"name": "Nope"}), "ValueError: No object named 'Nope'"
        )
        self.assertEqual(
            call("screenshot_viewport", {}), "RuntimeError: Screenshots need the Blender GUI"
        )
        prefs = submodule("prefs").get_prefs()
        original = prefs.mcp_allow_python
        try:
            prefs.mcp_allow_python = False
            self.assertEqual(
                call("execute_python", {"code": "result['value'] = 1"}),
                "PermissionError: Python execution is disabled in Open Studio preferences "
                "(Local assistant connection > Allow connected assistants to run Python)",
            )
        finally:
            prefs.mcp_allow_python = original
        # A conversion and a file read fail inside installed tool frames; their messages name
        # local paths, so the client receives fixed text and the console keeps the traceback.
        self.assertEqual(
            call("set_frame", {"frame": private}),
            "ValueError: set_frame failed unexpectedly; see the Blender console",
        )
        self.assertEqual(
            call("render_still", {"source": "VIEWPORT"}),
            "RuntimeError: Still capture needs the Blender GUI and an open 3D viewport",
        )
        # Exercise the registered capture tool's unexpected-error boundary with
        # an actual OS read failure containing a private filename. The existing
        # background guard intentionally prevents reaching a real GUI renderer.
        with (
            self.tools._private_png("missing.png") as missing,
            patch.object(
                self.tools, "_still_plan", side_effect=lambda _: Path(missing).read_bytes()
            ) as plan,
        ):
            self.assertEqual(
                call("render_still", {}),
                "FileNotFoundError: render_still failed unexpectedly; see the Blender console",
            )
        plan.assert_called_once()


class McpUploadPolicyTests(unittest.TestCase):
    """upload_reference and render_form first frames accept only allowlisted local files."""

    def setUp(self):
        self.runtime = submodule("blender.runtime")
        self.tools = submodule("mcp.tools_scenario")
        self.policy = submodule("mcp.upload_policy")
        self.server_module = submodule("mcp.server")
        self.enterContext(patch.object(self.runtime, "state", self.runtime.RuntimeState()))
        self.owner = Mock()
        self.owner.start.return_value = SimpleNamespace(identifier="fixture-reference")
        self.ensure = self.enterContext(
            patch.object(self.runtime, "ensure_reference_uploads", return_value=self.owner)
        )
        self.enterContext(
            patch.object(
                self.tools,
                "_reference_response",
                side_effect=lambda identifier: {"reference_id": identifier},
            )
        )
        self.outside = Path(self.enterContext(tempfile.TemporaryDirectory())).resolve()
        state = bpy.utils.extension_path_user(self.runtime.PACKAGE, path="state", create=True)
        self.extension = Path(state).resolve()
        self.scene = bpy.context.scene
        self.lane = self.scene.scenario.lane_state("render_video")
        self.addCleanup(setattr, self.lane, "first_frame_path", "")

    def file(self, folder, suffix=".png"):
        path = folder / f"policy-{uuid.uuid4().hex}{suffix}"
        path.write_bytes(b"synthetic reference")
        self.addCleanup(path.unlink, missing_ok=True)
        return path

    def link(self, link, target):
        try:
            os.symlink(target, link)
        except (OSError, NotImplementedError):
            self.skipTest("This platform or account cannot create symbolic links")
        self.addCleanup(link.unlink, missing_ok=True)
        return link

    def assert_uploaded(self, raw, resolved):
        self.owner.start.reset_mock()
        self.assertEqual(
            self.tools.upload_reference({"path": str(raw)}), {"reference_id": "fixture-reference"}
        )
        self.owner.start.assert_called_once_with(self.scene, resolved, kind="image", external=True)

    def assert_refused(self, raw):
        self.owner.start.reset_mock()
        self.ensure.reset_mock()
        with self.assertRaises(self.policy.UploadRefused) as refused:
            self.tools.upload_reference({"path": str(raw)})
        self.assertIn("Nothing was read or sent", str(refused.exception))
        self.ensure.assert_not_called()
        self.owner.start.assert_not_called()

    def test_file_the_extension_produced_is_uploaded_by_its_resolved_path(self):
        produced = self.file(self.extension)
        self.assert_uploaded(produced, produced)

    def test_files_chosen_in_the_extension_file_fields_are_allowed(self):
        first_frame, workflow, browsed = (self.file(self.outside) for _ in range(3))
        for path in (first_frame, workflow, browsed):
            self.assert_refused(path)
        self.lane.first_frame_path = str(first_frame)
        self.assert_uploaded(first_frame, first_frame)
        inputs = self.scene.scenario_workflow.inputs
        self.addCleanup(inputs.remove, len(inputs))
        item = inputs.add()
        item.upload_path = str(workflow)
        self.assert_uploaded(workflow, workflow)
        lane = self.scene.scenario.lane_state("image")
        count = len(lane.references)
        self.addCleanup(lambda: [lane.references.remove(count) for _ in lane.references[count:]])
        # Executing directly is what the file browser does once the user confirms a file.
        result = bpy.ops.scenario.add_reference(
            lane="image", param_name="image", source="FILE", filepath=str(browsed)
        )
        self.assertEqual(result, {"FINISHED"})
        self.assert_uploaded(browsed, browsed)

    def test_other_readable_missing_or_escaping_files_are_refused(self):
        other = self.file(self.outside)
        self.assertEqual(other.read_bytes(), b"synthetic reference")
        self.assert_refused(other)
        self.assert_refused(self.outside / "missing.png")
        self.assert_refused(self.outside)
        try:  # Spelled from inside the extension folder, resolved outside it.
            dotted = self.extension / os.path.relpath(other, self.extension)
        except ValueError:  # Windows: no relative path between two drives.
            dotted = None
        if dotted is not None:
            self.assert_refused(dotted)
        escape = self.link(self.extension / f"policy-{uuid.uuid4().hex}.png", other)
        self.assert_refused(escape)

    def test_refusal_reaches_the_client_as_written_text(self):
        protocol = submodule("mcp.protocol")
        registry = submodule("blender.mcp_service").build_registry()
        response = protocol.handle_message(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": "upload_reference", "arguments": {"path": str(self.outside)}},
            },
            registry,
            {"name": "fixture", "version": "0"},
        )
        self.assertTrue(response["result"]["isError"])
        self.assertEqual(
            response["result"]["content"][0]["text"], f"UploadRefused: {self.policy.REFUSED}"
        )
        self.owner.start.assert_not_called()

    def test_file_field_changed_by_an_agent_tool_is_not_a_choice(self):
        protocol = submodule("mcp.protocol")
        chosen = self.file(self.outside)
        registry = protocol.Registry()

        def set_first_frame(arguments):
            self.lane.first_frame_path = arguments["path"]
            return {"ok": True}

        registry.add(protocol.ToolSpec("set_first_frame", "Fixture", {}, set_first_frame))
        server = self.server_module.McpServer("127.0.0.1", 0, "fixture-token", registry, {})
        message = {
            "id": 1,
            "method": "tools/call",
            "params": {"name": "set_first_frame", "arguments": {"path": str(chosen)}},
        }
        with ThreadPoolExecutor(max_workers=1) as worker:
            result = worker.submit(server.handle, message)
            deadline = time.monotonic() + 5
            while not result.done() and time.monotonic() < deadline:
                server.process_pending()
                time.sleep(0.001)
            self.assertNotIn("isError", result.result(1)["result"])
        self.assertEqual(self.lane.first_frame_path, str(chosen))
        self.assertFalse(self.server_module.executing_tool())
        self.assertEqual(len(self.runtime.state.chosen_files), 0)
        self.assert_refused(chosen)

    def test_render_form_uploads_only_an_allowed_first_frame(self):
        render_commands = submodule("blender.render_commands")
        render_references = submodule("blender.render_references")
        generation = submodule("blender.generation")
        prepare = self.enterContext(patch.object(render_references, "prepare"))
        self.enterContext(patch.object(generation, "await_description"))
        self.enterContext(patch.object(render_commands, "inspect", return_value={}))
        self.enterContext(patch.object(self.runtime, "sync_catalog_context"))
        arguments = {"lane": "render_video", "action": "prepare", "role": "first_frame"}
        path = self.file(self.outside)
        with self.server_module._running_tool():  # As render_form configure sets it.
            self.lane.first_frame_path = str(path)
        with self.assertRaises(self.policy.UploadRefused):
            self.tools.render_form(arguments)
        prepare.assert_not_called()
        self.lane.first_frame_path = ""
        self.lane.first_frame_path = str(path)  # Now the user's choice.
        self.assertEqual(self.tools.render_form(arguments), {})
        prepare.assert_called_once()

    def test_render_form_prepares_the_checked_target_of_a_chosen_link(self):
        render_commands = submodule("blender.render_commands")
        render_references = submodule("blender.render_references")
        generation = submodule("blender.generation")
        prepare = self.enterContext(patch.object(render_references, "prepare"))
        self.enterContext(patch.object(generation, "await_description"))
        self.enterContext(patch.object(render_commands, "inspect", return_value={}))
        self.enterContext(patch.object(self.runtime, "sync_catalog_context"))
        arguments = {"lane": "render_video", "action": "prepare", "role": "first_frame"}
        path = self.file(self.outside)
        link = self.link(self.outside / f"policy-{uuid.uuid4().hex}.png", path)
        self.lane.first_frame_path = str(link)  # the user's choice, through a link
        self.assertEqual(self.tools.render_form(arguments), {})
        # Prepared from the checked target, never the link a later read would follow.
        self.assertEqual(self.lane.first_frame_path, str(path))
        prepare.assert_called_once()
