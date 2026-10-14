#!/usr/bin/env python3
# SPDX-FileCopyrightText: 2026 Scenario Inc.
# SPDX-License-Identifier: GPL-3.0-or-later
"""stdio <-> local HTTP bridge for command-based MCP clients.

Reads newline-delimited JSON-RPC on stdin, forwards each message to the local Blender server,
writes the JSON response on stdout. Standard library only; no bpy.

Usage: python3 stdio_shim.py --url http://127.0.0.1:9876/mcp --token TOKEN
"""

import argparse
import ipaddress
import json
import pathlib
import re
import sys
import urllib.error
import urllib.parse
import urllib.request


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, response, code, message, headers, new_url):
        # The bearer token belongs only to the configured local server.
        return None


def read_connection_file(path):
    """Read the extension-owned loopback connection afresh for every request.

    Token rotation after a Blender restart requires no new agent configuration.
    Invalid or stopped connections never expose the path or file contents.
    """
    try:
        path = pathlib.Path(path)
        if not path.is_absolute() or path.is_symlink() or not path.is_file():
            raise ValueError
        with path.open("rb") as stream:
            raw = stream.read(4097)
        if len(raw) > 4096:
            raise ValueError
        config = json.loads(raw)
        if not isinstance(config, dict) or set(config) != {"url", "token"}:
            raise ValueError
        url, token = config["url"], config["token"]
        if not isinstance(url, str) or not isinstance(token, str):
            raise ValueError
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,256}", token):
            raise ValueError
        parsed = urllib.parse.urlsplit(url)
        host = parsed.hostname
        if (
            parsed.scheme != "http"
            or not host
            or parsed.port is None
            or parsed.username is not None
            or parsed.password is not None
            or parsed.path != "/mcp"
            or parsed.query
            or parsed.fragment
            or "?" in url
            or "#" in url
        ):
            raise ValueError
        if host != "localhost" and not ipaddress.ip_address(host).is_loopback:
            raise ValueError
        return url, token
    except (OSError, ValueError, TypeError, UnicodeError):
        raise ValueError(
            "Blender connection unavailable. Start Blender and its Agents server, then retry."
        ) from None


def forward(url, token, message, timeout=300):
    data = json.dumps(message).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
            "Authorization": f"Bearer {token}",
        },
    )
    try:
        opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())
        with opener.open(req, timeout=timeout) as resp:
            body = resp.read()
            return json.loads(body) if body else None
    except urllib.error.HTTPError as err:
        body = err.read()
        try:
            return json.loads(body)
        except ValueError:
            return {
                "jsonrpc": "2.0",
                "id": message.get("id"),
                "error": {"code": -32000, "message": f"HTTP {err.code}: {body[:200]!r}"},
            }
    except (urllib.error.URLError, OSError) as err:
        return {
            "jsonrpc": "2.0",
            "id": message.get("id"),
            "error": {"code": -32001, "message": f"Blender is not reachable at {url}: {err}"},
        }


def main(argv=None):
    parser = argparse.ArgumentParser(prog="scenario-blender-stdio")
    parser.add_argument("--url", default="http://127.0.0.1:9876/mcp")
    auth = parser.add_mutually_exclusive_group(required=True)
    auth.add_argument("--token")
    auth.add_argument(
        "--connection-file", help="Private Blender connection file; reloaded per request"
    )
    args = parser.parse_args(argv)
    out = sys.stdout
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            message = json.loads(line)
        except ValueError as err:
            out.write(
                json.dumps(
                    {
                        "jsonrpc": "2.0",
                        "id": None,
                        "error": {"code": -32700, "message": f"Parse error: {err}"},
                    }
                )
                + "\n"
            )
            out.flush()
            continue
        try:
            url, token = (
                read_connection_file(args.connection_file)
                if args.connection_file
                else (args.url, args.token)
            )
            response = forward(url, token, message)
        except ValueError as err:
            response = {
                "jsonrpc": "2.0",
                "id": message.get("id"),
                "error": {"code": -32001, "message": str(err)},
            }
        if response is not None and message.get("id") is not None:
            out.write(json.dumps(response) + "\n")
            out.flush()
    return 0


if __name__ == "__main__":
    sys.exit(main())
