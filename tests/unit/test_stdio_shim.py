# SPDX-FileCopyrightText: 2026 Scenario Inc.
# SPDX-License-Identifier: GPL-3.0-or-later
import http.server
import json
import pathlib
import subprocess
import sys
import threading

import pytest

from scenario.mcp import stdio_shim

SHIM = pathlib.Path(__file__).resolve().parents[2] / "scenario" / "mcp" / "stdio_shim.py"


class _Stub(http.server.BaseHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        if self.headers.get("Authorization") != f"Bearer {getattr(self.server, 'token', 'secret')}":
            self.send_response(401)
            self.end_headers()
            return
        payload = json.dumps(
            {"jsonrpc": "2.0", "id": body.get("id"), "result": {"echo": body.get("method")}}
        ).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


def test_shim_round_trips_requests_and_skips_notifications():
    server = http.server.HTTPServer(("127.0.0.1", 0), _Stub)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    url = f"http://127.0.0.1:{server.server_port}/mcp"
    try:
        proc = subprocess.run(
            [sys.executable, str(SHIM), "--url", url, "--token", "secret"],
            input='{"jsonrpc":"2.0","id":1,"method":"ping"}\n{"jsonrpc":"2.0","method":"notifications/initialized"}\n{"jsonrpc":"2.0","id":2,"method":"tools/list"}\n',
            capture_output=True,
            text=True,
            timeout=20,
        )
        lines = [json.loads(line) for line in proc.stdout.strip().splitlines()]
        assert [message["id"] for message in lines] == [1, 2]
        assert lines[1]["result"] == {"echo": "tools/list"}
        assert proc.returncode == 0
    finally:
        server.shutdown()


def test_connection_file_is_reloaded_after_blender_restart(tmp_path):
    path = tmp_path / "connection.json"
    path.write_text(json.dumps({"url": "http://127.0.0.1:9876/mcp", "token": "first-local-token"}))
    assert stdio_shim.read_connection_file(path) == (
        "http://127.0.0.1:9876/mcp",
        "first-local-token",
    )
    path.write_text(json.dumps({"url": "http://127.0.0.1:9877/mcp", "token": "second-local-token"}))
    assert stdio_shim.read_connection_file(path) == (
        "http://127.0.0.1:9877/mcp",
        "second-local-token",
    )


def test_local_bearer_token_is_not_forwarded_by_redirect():
    received = []

    class Redirect(http.server.BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            self.send_response(302)
            self.send_header("Location", "/redirect-target")
            self.end_headers()

        def do_GET(self):
            received.append(self.headers.get("Authorization"))
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"{}")

    server = http.server.HTTPServer(("127.0.0.1", 0), Redirect)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        result = stdio_shim.forward(
            f"http://127.0.0.1:{server.server_port}/mcp",
            "synthetic-local-token",
            {"jsonrpc": "2.0", "id": 1, "method": "ping"},
        )
        assert not received
        assert result["error"]["code"] == -32000
    finally:
        server.shutdown()
        server.server_close()


def test_local_bridge_does_not_use_environment_proxies(monkeypatch):
    def reject_proxy_discovery():
        raise AssertionError("The local authenticated connection must bypass proxy discovery")

    monkeypatch.setattr(stdio_shim.urllib.request, "_opener", None)
    monkeypatch.setattr(stdio_shim.urllib.request, "getproxies", reject_proxy_discovery)
    server = http.server.HTTPServer(("127.0.0.1", 0), _Stub)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        result = stdio_shim.forward(
            f"http://127.0.0.1:{server.server_port}/mcp",
            "secret",
            {"jsonrpc": "2.0", "id": 1, "method": "ping"},
        )
        assert result["result"]["echo"] == "ping"
    finally:
        server.shutdown()
        server.server_close()


def test_running_stdio_process_reconnects_without_reconfiguration(tmp_path):
    path = tmp_path / "connection.json"
    second = http.server.HTTPServer(("127.0.0.1", 0), _Stub)
    second.token = "second-token"

    class RestartAfterRequest(_Stub):
        def do_POST(self):
            path.write_text(
                json.dumps(
                    {
                        "url": f"http://127.0.0.1:{second.server_port}/mcp",
                        "token": second.token,
                    }
                )
            )
            super().do_POST()

    first = http.server.HTTPServer(("127.0.0.1", 0), RestartAfterRequest)
    first.token = "first-token"
    path.write_text(
        json.dumps({"url": f"http://127.0.0.1:{first.server_port}/mcp", "token": first.token})
    )
    for server in (first, second):
        threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        proc = subprocess.run(
            [sys.executable, str(SHIM), "--connection-file", str(path)],
            input='{"jsonrpc":"2.0","id":1,"method":"ping"}\n{"jsonrpc":"2.0","id":2,"method":"tools/list"}\n',
            capture_output=True,
            text=True,
            timeout=20,
        )
        replies = [json.loads(line) for line in proc.stdout.splitlines()]
        assert [reply["result"]["echo"] for reply in replies] == ["ping", "tools/list"]
        assert "first-token" not in proc.stdout + proc.stderr
        assert "second-token" not in proc.stdout + proc.stderr
    finally:
        for server in (first, second):
            server.shutdown()
            server.server_close()


@pytest.mark.parametrize(
    "contents",
    [
        "not-json",
        "[]",
        "{}",
        "x" * 4097,
        '{"url":"https://example.com/mcp","token":"private-value"}',
        '{"url":"http://127.0.0.1:9876/mcp","token":"bad token"}',
        '{"url":"http://name:password@127.0.0.1:9876/mcp","token":"private-value"}',
        '{"url":"http://127.0.0.1:9876/mcp?secret=yes","token":"private-value"}',
    ],
)
def test_invalid_connection_file_fails_without_echoing_contents(tmp_path, contents):
    path = tmp_path / "connection.json"
    path.write_text(contents)
    with pytest.raises(ValueError, match="Blender connection") as caught:
        stdio_shim.read_connection_file(path)
    assert "private-value" not in str(caught.value)
    assert str(path) not in str(caught.value)


def test_connection_file_must_be_absolute_and_exist(tmp_path):
    for path in (pathlib.Path("connection.json"), tmp_path / "absent.json"):
        with pytest.raises(ValueError, match="Blender connection"):
            stdio_shim.read_connection_file(path)


def test_stopped_blender_returns_actionable_json_rpc_without_private_path(tmp_path):
    path = tmp_path / "missing.json"
    proc = subprocess.run(
        [sys.executable, str(SHIM), "--connection-file", str(path)],
        input='{"jsonrpc":"2.0","id":1,"method":"ping"}\n',
        capture_output=True,
        text=True,
        timeout=10,
    )
    response = json.loads(proc.stdout)
    assert response["id"] == 1
    assert "Start Blender" in response["error"]["message"]
    assert str(path) not in proc.stdout + proc.stderr
