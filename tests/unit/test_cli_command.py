# SPDX-FileCopyrightText: 2026 Scenario Inc.
# SPDX-License-Identifier: GPL-3.0-or-later
"""Explicit local MCP credentials and safe startup reporting."""

import tomllib
from pathlib import Path

import pytest

from scenario.mcp.cli import CLI_COMMAND, cli_banner, resolve_cli_token

ROOT = Path(__file__).resolve().parents[2]


def test_cli_registration_and_current_docs_agree():
    assert CLI_COMMAND == "open_studio"
    for path in ("README.md", "docs/USER_GUIDE.md"):
        text = (ROOT / path).read_text(encoding="utf-8")
        assert f"--command {CLI_COMMAND}" in text
        assert "--command scenario-mcp" not in text


def test_repository_codex_entry_is_fork_named_and_stays_disabled():
    config = tomllib.loads((ROOT / ".codex/config.toml").read_text(encoding="utf-8"))
    server = config["mcp_servers"]["open-studio"]
    variable = server["bearer_token_env_var"]
    assert resolve_cli_token(None, {variable: "shared-name"}) == ("shared-name", False)
    snippet = (ROOT / "scenario/blender/mcp_service.py").read_text(encoding="utf-8")
    assert "--connection-file" in snippet
    assert variable == "OPEN_STUDIO_TOKEN"
    assert server["enabled"] is False


def test_explicit_cli_token_wins_and_supplied_tokens_never_appear_in_banner():
    for argument, env, expected in [
        ("a", {"OPEN_STUDIO_TOKEN": "env"}, "a"),
        (None, {"OPEN_STUDIO_TOKEN": "environment-secret"}, "environment-secret"),
    ]:
        token, generated = resolve_cli_token(argument, env)
        assert token == expected
        assert not generated
        assert cli_banner("http://127.0.0.1:9876/mcp", token, generated).endswith(
            "(local authentication enabled)"
        )


def test_absent_credentials_generate_distinct_bootstrap_tokens():
    first, generated = resolve_cli_token(None, {})
    second, _ = resolve_cli_token(None, {})
    assert generated and first != second and len(first) >= 32
    assert first not in cli_banner("http://127.0.0.1:9876/mcp", first, generated)


@pytest.mark.parametrize("token", ["", " ", "with space", "line\nbreak", "tök", "\x7f"])
def test_invalid_explicit_credentials_are_rejected(token):
    with pytest.raises(ValueError):
        resolve_cli_token(token, {"OPEN_STUDIO_TOKEN": "valid"})
    with pytest.raises(ValueError):
        resolve_cli_token(None, {"OPEN_STUDIO_TOKEN": token})


@pytest.mark.parametrize("token", ["abc.def", "synthetic:token/+", "x" * 256])
def test_explicit_token_roundtrips_through_private_connection_file(tmp_path, token):
    import json

    from scenario.mcp.stdio_shim import read_connection_file

    accepted, generated = resolve_cli_token(token, {})
    path = tmp_path / "connection.json"
    path.write_text(json.dumps({"url": "http://127.0.0.1:9876/mcp", "token": accepted}))
    assert read_connection_file(path) == ("http://127.0.0.1:9876/mcp", token)
    assert generated is False


def test_oversized_token_is_refused_before_server_start():
    with pytest.raises(ValueError):
        resolve_cli_token("x" * 257, {})
