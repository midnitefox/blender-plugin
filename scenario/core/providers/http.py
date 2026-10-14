# SPDX-FileCopyrightText: 2026 Open Studio contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Bounded same-origin transport: one attempt, no redirects, no ambient auth."""

import json
import time
from urllib.parse import unquote, urlsplit

from ..api.sdk_adapter import AdapterError, AdapterStatusError


class ProviderHTTP:
    def __init__(self, profile, *, online, transport=None):
        import httpx

        self._profile = profile
        self._online = online if callable(online) else lambda: bool(online)
        self._closed = False
        self._client = httpx.Client(
            transport=transport,
            trust_env=False,
            follow_redirects=False,
            timeout=httpx.Timeout(120.0, connect=10.0),
        )

    def close(self):
        self._closed = True
        self._client.close()

    def request_bytes(
        self, method, path, body=None, *, content_type=None, max_bytes=134217728, cancel=None
    ):
        import httpx

        parsed = urlsplit(path)
        if (
            not isinstance(path, str)
            or not path.startswith("/")
            or path.startswith("//")
            or parsed.scheme
            or parsed.netloc
            or parsed.fragment
            or "\\" in path
            or any(ord(c) < 32 for c in path)
            or any(p in {".", ".."} for p in unquote(parsed.path).split("/"))
        ):
            raise ValueError("Use a provider-relative endpoint path")
        if type(max_bytes) is not int or not 1 <= max_bytes <= 536870912:
            raise ValueError("Use a bounded positive response size")
        if self._closed or not self._online():
            raise AdapterError("Online access is disabled or this connection was closed")
        if cancel is not None and cancel.is_set():
            raise AdapterError("Provider transfer canceled")
        headers = {"Accept": "application/json"}
        if self._profile.api_key:
            scheme = "Key" if self._profile.provider == "fal" else "Bearer"
            headers["Authorization"] = f"{scheme} {self._profile.api_key}"
        if self._profile.provider == "fal" and method.upper() == "POST":
            # Fal documents this queue header to disable worker retries.
            # The HTTP client itself already makes exactly one attempt.
            headers["X-Fal-No-Retry"] = "1"
        if body is not None and not isinstance(body, bytes):
            body = json.dumps(body, allow_nan=False).encode()
            content_type = "application/json"
        if content_type:
            headers["Content-Type"] = content_type
        started = time.monotonic()
        try:
            with self._client.stream(
                method, self._profile.base_url + path, headers=headers, content=body
            ) as response:
                if not 200 <= response.status_code < 300:
                    raise AdapterStatusError(
                        response.status_code,
                        f"The selected provider returned HTTP {response.status_code}; nothing was retried",
                    )
                size = response.headers.get("Content-Length")
                if size is not None and (not size.isdigit() or int(size) > max_bytes):
                    raise AdapterError("Provider response exceeds the configured limit")
                result = bytearray()
                for block in response.iter_bytes(65536):
                    if (cancel is not None and cancel.is_set()) or not self._online():
                        raise AdapterError("Provider transfer canceled or online access disabled")
                    if time.monotonic() - started > 180:
                        raise AdapterError(
                            "Provider response exceeded the time limit; nothing was retried"
                        )
                    result.extend(block)
                    if len(result) > max_bytes:
                        raise AdapterError("Provider response exceeds the configured limit")
                return bytes(result)
        except httpx.HTTPError:
            raise AdapterError(
                "The provider connection failed; the request was not retried"
            ) from None

    def request_json(self, method, path, body=None, *, max_bytes=33554432, cancel=None):
        data = self.request_bytes(method, path, body, max_bytes=max_bytes, cancel=cancel)
        try:
            value = json.loads(data, parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
            if not isinstance(value, (dict, list)):
                raise ValueError
            return value
        except (ValueError, UnicodeError, RecursionError):
            raise AdapterError("The provider returned malformed JSON") from None
