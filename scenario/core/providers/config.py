# SPDX-FileCopyrightText: 2026 Open Studio contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Immutable connection identity and conservative endpoint validation."""

import hashlib
import ipaddress
import json
import re
from dataclasses import dataclass, field
from urllib.parse import unquote, urlsplit, urlunsplit


def canonical_endpoint(value):
    """External services require HTTPS; HTTP is restricted to explicit loopback."""
    if (
        not isinstance(value, str)
        or not value
        or not value.isascii()
        or any(ord(c) <= 32 or ord(c) == 127 for c in value)
        or "\\" in value
    ):
        raise ValueError("Use an HTTPS endpoint or an HTTP loopback address")
    try:
        parsed = urlsplit(value)
        host, port = parsed.hostname, parsed.port
        if (
            parsed.scheme not in {"https", "http"}
            or not host
            or parsed.username is not None
            or parsed.password is not None
            or parsed.query
            or parsed.fragment
            or "?" in value
            or "#" in value
        ):
            raise ValueError
        try:
            loopback = ipaddress.ip_address(host).is_loopback
        except ValueError:
            loopback = host == "localhost"
            if not re.fullmatch(r"[a-z0-9](?:[a-z0-9.-]*[a-z0-9])?", host):
                raise ValueError from None
        if parsed.scheme == "http" and not loopback:
            raise ValueError
        decoded = unquote(parsed.path)
        if (
            "\\" in decoded
            or any(segment in {".", ".."} for segment in decoded.split("/"))
            or any(ord(c) < 32 for c in decoded)
        ):
            raise ValueError
        authority = f"[{host}]" if ":" in host else host
        if port is not None and port != (443 if parsed.scheme == "https" else 80):
            authority += f":{port}"
        return urlunsplit((parsed.scheme, authority, parsed.path.rstrip("/"), "", ""))
    except (ValueError, UnicodeError):
        raise ValueError(
            "Use an HTTPS endpoint or an HTTP loopback address without credentials"
        ) from None


@dataclass(frozen=True)
class ProviderProfile:
    profile_id: str
    name: str
    provider: str
    base_url: str
    api_key: str = field(default="", repr=False)
    text_model: str = ""
    image_model: str = ""
    speech_model: str = ""
    workflows_path: str = ""
    options_json: str = field(default="{}", repr=False)

    def __post_init__(self):
        if not isinstance(self.profile_id, str) or not re.fullmatch(
            r"[A-Za-z0-9_-]{1,128}", self.profile_id
        ):
            raise ValueError("Use a stable profile identifier")
        if not isinstance(self.name, str) or not self.name.strip() or len(self.name) > 200:
            raise ValueError("Name this AI connection")
        if self.provider not in {"comfyui", "openai_compatible", "scenario", "fal"}:
            raise ValueError("Choose a supported provider")
        object.__setattr__(self, "base_url", canonical_endpoint(self.base_url))
        if (
            not isinstance(self.api_key, str)
            or not self.api_key.isascii()
            or any(ord(c) < 33 or ord(c) == 127 for c in self.api_key)
        ):
            raise ValueError("API credentials cannot contain whitespace or control characters")
        for value in (self.text_model, self.image_model, self.speech_model, self.workflows_path):
            if not isinstance(value, str) or len(value) > 4096 or any(ord(c) < 32 for c in value):
                raise ValueError("Use bounded model names and a workflow path")
        if not isinstance(self.options_json, str) or len(self.options_json.encode()) > 65536:
            raise ValueError("Provider options must be a small JSON object")
        try:
            options = json.loads(
                self.options_json, parse_constant=lambda _: (_ for _ in ()).throw(ValueError())
            )
            if not isinstance(options, dict):
                raise ValueError
            object.__setattr__(
                self, "options_json", json.dumps(options, sort_keys=True, allow_nan=False)
            )
        except (ValueError, TypeError, RecursionError):
            raise ValueError("Provider options must be a finite JSON object") from None

    @property
    def valid(self):
        return True

    @property
    def options(self):
        return json.loads(self.options_json)

    @property
    def identity_hash(self):
        """Profile changes isolate caches and receipts; display renaming does not."""
        values = (
            self.profile_id,
            self.provider,
            self.base_url,
            self.api_key,
            self.text_model,
            self.image_model,
            self.speech_model,
            self.workflows_path,
            self.options_json,
        )
        return hashlib.sha256(json.dumps(values, ensure_ascii=True).encode()).hexdigest()

    @property
    def storage_identity_hash(self):
        """Keep history across model/recipe edits within the same connection."""
        values = (self.profile_id, self.provider, self.base_url, self.api_key)
        return hashlib.sha256(json.dumps(values, ensure_ascii=True).encode()).hexdigest()
