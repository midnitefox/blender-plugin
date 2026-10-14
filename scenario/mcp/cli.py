# SPDX-FileCopyrightText: 2026 Scenario Inc.
# SPDX-License-Identifier: GPL-3.0-or-later
"""Pure helpers for local assistant authentication and safe startup reporting."""

import os
import secrets

CLI_COMMAND = "open_studio"


def resolve_cli_token(argument=None, environ=None):
    """Explicit flag wins over environment; only absent inputs generate a token."""
    environ = os.environ if environ is None else environ
    token = argument if argument is not None else environ.get("OPEN_STUDIO_TOKEN")
    generated = token is None
    if generated:
        token = secrets.token_urlsafe(24)
    if (
        not isinstance(token, str)
        or not 1 <= len(token) <= 256
        or any(not 33 <= ord(c) <= 126 for c in token)
    ):
        raise ValueError(
            "The local MCP token must be 1 to 256 printable ASCII characters without spaces"
        )
    return token, generated


def cli_banner(url, token, generated):
    """The private connection file supplies authentication without console exposure."""
    return f"Open Studio MCP server on {url} (local authentication enabled)"
