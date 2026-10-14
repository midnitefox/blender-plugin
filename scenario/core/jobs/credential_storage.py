# SPDX-FileCopyrightText: 2026 Scenario Inc.
# SPDX-License-Identifier: GPL-3.0-or-later
"""Local API-key storage isolation, without claiming a server account identity."""

import errno
import hashlib
import hmac
import json
import os
import stat
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path

from ..api.sdk_adapter import API_URL, Credentials
from .store import JobScope, JobStore, StoreError

_HEADER = b"SCENARIO-LOCAL-SCOPE-v1\n"


@contextmanager
def _initialization_lock(root):
    # Keep this file: unlinking a lock can give competing owners different inodes.
    path = root / ".scope.lock"
    if path.is_symlink():
        raise StoreError("Local scope lock must be a regular private file")
    flags = os.O_RDWR | os.O_CREAT | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    descriptor = os.open(path, flags, 0o600)
    try:
        info = os.fstat(descriptor)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > 1:
            raise StoreError("Local scope lock must be a regular private file")
        # Windows supports byte-range locks beyond EOF. Keep a new file empty:
        # writing a sentinel before locking races with another owner's mandatory lock.
        os.lseek(descriptor, 0, os.SEEK_SET)
        deadline = time.monotonic() + 2
        while True:
            try:
                if os.name == "nt":
                    import msvcrt

                    msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
                elif os.name == "posix":
                    import fcntl

                    fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                else:
                    raise StoreError("Local scope locking is unsupported on this platform")
                break
            except OSError as error:
                # POSIX flock and Windows byte-range locks use these for contention.
                if error.errno not in (errno.EAGAIN, errno.EACCES):
                    raise
                if time.monotonic() >= deadline:
                    raise StoreError("Local scope initialization is busy; retry later") from None
                time.sleep(0.01)
        yield
    finally:
        os.close(descriptor)


def _read_key(path):
    # The parent remains caller-owned. O_NONBLOCK also prevents a replaced FIFO
    # from hanging startup; fstat validates the actual opened object.
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    if path.is_symlink():
        raise StoreError("Local scope key must be a regular file; preserve it for recovery")
    with os.fdopen(os.open(path, flags), "rb") as source:
        if not stat.S_ISREG(os.fstat(source.fileno()).st_mode):
            raise StoreError("Local scope key must be a regular file; preserve it for recovery")
        data = source.read(len(_HEADER) + 33)
    if not data.startswith(_HEADER) or len(data) != len(_HEADER) + 32:
        raise StoreError("Local scope key is invalid; preserve it for recovery")
    return data[len(_HEADER) :]


def _scope_key(root, database):
    path = root / "scope.key"
    if os.path.lexists(path):
        return _read_key(path)
    with _initialization_lock(root):
        if os.path.lexists(path):
            return _read_key(path)
        if os.path.lexists(database):
            raise StoreError("Local scope key is missing; preserve existing jobs for recovery")
        # All creators hold the same lock through this recheck and publication.
        # Atomic replacement publishes a complete file without requiring hard links.
        # mkstemp requests 0600; its temporary name never becomes a job identity.
        descriptor, temporary = tempfile.mkstemp(prefix=".scope-", dir=root)
        try:
            with os.fdopen(descriptor, "wb") as output:
                output.write(_HEADER + os.urandom(32))
                output.flush()
                os.fsync(output.fileno())
            os.replace(temporary, path)
        finally:
            if os.path.lexists(temporary):
                os.unlink(temporary)
    return _read_key(path)


def open_credential_store(root, credentials, *, service=API_URL, project_id=None, team_id=None):
    """Reopen this key pair's local jobs; never discover or select a remote tenant.

    The private root belongs under extension user data. Keep scope.key with the
    database in backups. This is isolation, not encryption or credential storage.
    """
    if not isinstance(credentials, Credentials) or credentials.bearer_token:
        raise ValueError("Local credential storage requires an explicit API-key pair")
    credentials.authorization()
    # Validate non-secret context before creating any files.
    JobScope(service, "local", project_id, team_id)
    try:
        root = Path(root).absolute()
        if root.is_symlink():
            raise StoreError("Local job storage must use a private directory")
        root.mkdir(mode=0o700, parents=True, exist_ok=True)
        root = root.resolve(strict=True)
        database = root / "jobs.sqlite3"
        key = _scope_key(root, database)
        payload = json.dumps(
            ["api-key-v1", service, credentials.api_key, credentials.api_secret],
            separators=(",", ":"),
        ).encode()
        identity = "local-key-" + hmac.new(key, payload, hashlib.sha256).hexdigest()
        return JobStore(database, JobScope(service, identity, project_id, team_id))
    except OSError:
        raise StoreError(
            "Local job storage is unavailable; preserve existing data for recovery"
        ) from None
