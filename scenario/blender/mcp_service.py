# SPDX-FileCopyrightText: 2026 Scenario Inc.
# SPDX-License-Identifier: GPL-3.0-or-later
"""Lifecycle of the local MCP server inside Blender, plus client setup snippets."""

import json
import logging
import os
import secrets
import shlex
import sys
import tempfile

import bpy

from .. import __version__
from ..mcp import protocol
from ..mcp.cli import CLI_COMMAND, cli_banner, resolve_cli_token
from ..mcp.server import McpServer
from ..mcp.stdio_shim import read_connection_file
from . import runtime

log = logging.getLogger("scenario.mcp")
SERVER_INFO = {"name": "open-studio", "version": __version__}


def build_registry():
    from ..mcp import tools_agent, tools_blender, tools_scenario

    registry = protocol.Registry()
    for spec in (*tools_agent.SPECS, *tools_blender.SPECS, *tools_scenario.SPECS):
        registry.add(spec)
    return registry


def status():
    server = runtime.state.mcp
    return {
        "running": bool(server and server.running),
        "url": server.url if server else "",
        "token": runtime.state.mcp_token,
        "error": runtime.state.mcp_error,
        "calls": server.calls_served if server else 0,
    }


def start():
    if runtime.state.mcp is not None and runtime.state.mcp.running:
        return runtime.state.mcp
    if not runtime.online():
        runtime.state.mcp_error = "Allow Online Access is off"
        return None
    prefs = runtime.prefs()
    port = prefs.mcp_port if prefs else 9876
    if not runtime.state.mcp_token:
        runtime.state.mcp_token = secrets.token_urlsafe(24)
    try:
        server = McpServer(
            "127.0.0.1",
            port,
            runtime.state.mcp_token,
            build_registry(),
            SERVER_INFO,
            blender_version=bpy.app.version_string,
        )
        server.start()
        _write_connection_file(server.url, runtime.state.mcp_token)
    except OSError as err:
        if "server" in locals():
            server.stop()
        runtime.state.mcp_error = str(err)
        runtime.state.mcp = None
        return None
    runtime.state.mcp = server
    runtime.state.mcp_error = ""
    return server


def stop():
    server = runtime.state.mcp
    if server is not None:
        server.stop()
        _remove_connection_file(server.url, runtime.state.mcp_token)
    runtime.state.mcp = None


def process_pending():
    runtime.sync_catalog_context()
    server = runtime.state.mcp
    if server is not None:
        server.process_pending()


def masked_token():
    token = runtime.state.mcp_token or ""
    return (token[:4] + "...") if len(token) > 8 else token


def _blender_python():
    windows = [
        os.path.join(sys.prefix, "bin", "python.exe"),
        os.path.join(sys.prefix, "python.exe"),
    ]
    unix = [
        os.path.join(sys.prefix, "bin", f"python{sys.version_info.major}.{sys.version_info.minor}"),
        os.path.join(sys.prefix, "bin", "python3"),
    ]
    candidates = windows + unix if os.name == "nt" else unix
    for path in candidates:
        if os.path.isfile(path):
            return path
    return "python" if os.name == "nt" else "python3"


def connection_file_path():
    return runtime.paths().state_dir / "agent-connection.json"


def _write_connection_file(url, token):
    """Publish only after the authenticated local server has started."""
    destination = connection_file_path()
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=destination.parent, prefix=".agent-", delete=False
        ) as stream:
            temporary = stream.name
            os.chmod(temporary, 0o600)
            json.dump({"url": url, "token": token}, stream)
        os.replace(temporary, destination)
    finally:
        if temporary and os.path.exists(temporary):
            os.unlink(temporary)


def _remove_connection_file(url, token):
    """Do not remove another running instance's replacement connection."""
    destination = connection_file_path()
    try:
        if read_connection_file(destination) == (url, token):
            destination.unlink(missing_ok=True)
    except (OSError, ValueError):
        pass


def _shell_argument(value):
    # PowerShell single quotes are literal; double a quote embedded in a path.
    return "'" + value.replace("'", "''") + "'" if os.name == "nt" else shlex.quote(value)


def shim_path():
    return os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "mcp", "stdio_shim.py"
    )


def client_configs():
    st = status()
    url, token = st["url"] or "http://127.0.0.1:9876/mcp", st["token"] or "<token>"
    stdio = {
        "mcpServers": {
            "open-studio": {
                "command": _blender_python(),
                "args": [shim_path(), "--connection-file", str(connection_file_path())],
            }
        }
    }
    return {
        "claude_code": f'claude mcp add --transport http open-studio {url} --header "Authorization: Bearer {token}"',
        "cursor": json.dumps(
            {
                "mcpServers": {
                    "open-studio": {
                        "url": url,
                        "headers": {"Authorization": f"Bearer {token}"},
                    }
                }
            },
            indent=2,
        ),
        "claude_desktop": json.dumps(stdio, indent=2),
        "codex": "codex mcp add open-studio -- "
        + " ".join(
            _shell_argument(arg)
            for arg in (
                _blender_python(),
                shim_path(),
                "--connection-file",
                str(connection_file_path()),
            )
        )
        + "\n# Keep Blender open with the Agents server running. Reconnect MCP in Codex after setup.",
        "curl": f'curl -s -X POST {url} -H "Authorization: Bearer {token}" -H "Content-Type: application/json" -d \'{{"jsonrpc":"2.0","id":1,"method":"tools/list"}}\'',
    }


def cli(argv):
    """blender --background file.blend --command scenario_blender [--port N] [--token T]"""
    import argparse
    import signal
    import threading

    parser = argparse.ArgumentParser(prog=CLI_COMMAND)
    parser.add_argument("--port", type=int, default=9876)
    parser.add_argument(
        "--token",
        default=None,
        help="Local MCP token; otherwise SCENARIO_BLENDER_TOKEN or a new token",
    )
    args = parser.parse_args(argv)
    if not 1 <= args.port <= 65535:
        parser.error("port must be between 1 and 65535")
    if not runtime.online():
        parser.error("Allow Online Access is off; enable it before starting local MCP")
    try:
        token, generated = resolve_cli_token(args.token)
    except ValueError as error:
        parser.error(str(error))
    runtime.state.mcp_token = token
    server = McpServer(
        "127.0.0.1",
        args.port,
        token,
        build_registry(),
        SERVER_INFO,
        blender_version=bpy.app.version_string,
    )
    stop_event = threading.Event()
    previous = {}
    try:
        for sig in (signal.SIGINT, signal.SIGTERM):
            previous[sig] = signal.signal(sig, lambda *_: stop_event.set())
        try:
            server.start()
            _write_connection_file(server.url, token)
        except OSError as error:
            parser.error(f"Unable to start local MCP: {error}")
        print(cli_banner(server.url, token, generated), flush=True)
        server.serve_blocking(stop_event, before_process=runtime.sync_catalog_context)
    except KeyboardInterrupt:
        stop_event.set()
    finally:
        server.stop()
        _remove_connection_file(server.url, token)
        for sig, handler in previous.items():
            # Blender can install a native handler that Python reports as None.
            # None is not accepted by signal.signal; the command is exiting.
            if handler is not None:
                signal.signal(sig, handler)
    return 0
