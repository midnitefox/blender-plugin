# SPDX-FileCopyrightText: 2026 Scenario Inc.
# SPDX-License-Identifier: GPL-3.0-or-later
import os
import pathlib
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from helpers import submodule


class McpPanelTests(unittest.TestCase):
    def test_assistant_configs_use_private_connection_file(self):
        runtime = submodule("blender.runtime")
        service = submodule("blender.mcp_service")
        runtime.state.mcp_token = "tok123"
        configs = service.client_configs()
        for key in ("curl",):
            self.assertIn("tok123", configs[key], key)
            self.assertIn("/mcp", configs[key], key)
        for key in ("claude_desktop", "codex", "claude_code", "cursor"):
            self.assertIn("--connection-file", configs[key])
            self.assertNotIn("tok123", configs[key])
        self.assertIn("stdio_shim.py", configs["claude_desktop"])
        self.assertTrue(
            service.masked_token().endswith("...")
            or len(service.masked_token()) < len("tok123") + 4
        )

    def test_bundled_windows_python_and_powershell_quoting(self):
        service = submodule("blender.mcp_service")
        expected = os.path.join(service.sys.prefix, "bin", "python.exe")
        with (
            patch.object(service.os, "name", "nt"),
            patch.object(service.os.path, "isfile", side_effect=lambda path: path == expected),
        ):
            self.assertEqual(service._blender_python(), expected)
            self.assertEqual(service._shell_argument("C:/a b/it's.py"), "'C:/a b/it''s.py'")

    def test_connection_file_rotates_on_restart_and_is_removed_on_stop(self):
        runtime = submodule("blender.runtime")
        service = submodule("blender.mcp_service")
        with tempfile.TemporaryDirectory() as directory:
            state_dir = pathlib.Path(directory)
            server = Mock(running=True, url="http://127.0.0.1:9876/mcp")
            with (
                patch.object(runtime, "state", runtime.RuntimeState()),
                patch.object(runtime, "paths", return_value=SimpleNamespace(state_dir=state_dir)),
                patch.object(runtime, "online", return_value=True),
                patch.object(service, "McpServer", return_value=server),
            ):
                service.start()
                path = service.connection_file_path()
                first = service.read_connection_file(path)
                self.assertEqual(first[0], server.url)
                service.stop()
                self.assertFalse(path.exists())
                runtime.state.mcp_token = ""
                service.start()
                second = service.read_connection_file(path)
                self.assertNotEqual(first[1], second[1])
                service.stop()
                self.assertFalse(path.exists())

    def test_stopping_old_server_preserves_new_instances_connection(self):
        service = submodule("blender.mcp_service")
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / "connection.json"
            with patch.object(service, "connection_file_path", return_value=path):
                service._write_connection_file("http://127.0.0.1:9877/mcp", "new-token")
                service._remove_connection_file("http://127.0.0.1:9876/mcp", "old-token")
                self.assertTrue(path.is_file())
