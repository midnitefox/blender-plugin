# SPDX-FileCopyrightText: 2026 Scenario Inc.
# SPDX-License-Identifier: GPL-3.0-or-later
"""Portable Blender discovery and exact installed-package verification (stdlib only)."""

import argparse
import codecs
import hashlib
import io
import math
import os
import platform
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import threading
import tomllib
import zipfile
from pathlib import Path, PurePosixPath

if __package__:
    from . import zip_limits
else:
    import zip_limits

ROOT = Path(__file__).resolve().parents[1]


def _version_key(path):
    """Sort versioned installation paths numerically, including multi-digit releases."""
    for part in reversed(path.parts):
        match = re.search(r"(?<!\d)(\d+)\.(\d+)(?:\.(\d+))?", part)
        if match:
            return tuple(int(value or 0) for value in match.groups()), str(path)
    return (0, 0, 0), str(path)


def manifest_version():
    """Read the extension version without discovering or launching Blender."""
    manifest = tomllib.loads((ROOT / "scenario/blender_manifest.toml").read_text())
    version = manifest.get("version")
    if not isinstance(version, str) or not version:
        raise ValueError("The extension manifest must contain a version string")
    return version


def find_blender(explicit=None):
    selected = explicit or os.environ.get("BLENDER")
    if selected:
        candidate = shutil.which(selected) or str(Path(selected).expanduser())
        if Path(candidate).is_file() and os.access(candidate, os.X_OK):
            return Path(candidate).resolve()
        raise ValueError("Blender not found: set BLENDER=/path/to/blender or use --blender PATH")
    found = shutil.which("blender")
    if found:
        return Path(found).resolve()
    candidates = []
    system = platform.system()
    if system == "Darwin":
        candidates = [
            Path("/Applications/Blender.app/Contents/MacOS/Blender"),
            *sorted(
                [
                    *Path("/Applications").glob("Blender*.app/Contents/MacOS/Blender"),
                    *Path("/Applications").glob("Blender*/Blender.app/Contents/MacOS/Blender"),
                ],
                key=_version_key,
                reverse=True,
            ),
        ]
    elif system == "Windows":
        candidates = sorted(
            Path("C:/Program Files/Blender Foundation").glob("Blender*/blender.exe"),
            key=_version_key,
            reverse=True,
        )
    else:
        candidates = [
            Path("/snap/bin/blender"),
            Path("/usr/bin/blender"),
            *sorted(Path("/opt").glob("blender*/blender"), key=_version_key, reverse=True),
        ]
    for folder in sorted((ROOT / ".blender").glob("*"), key=_version_key, reverse=True):
        if not folder.is_dir():
            continue
        if system == "Darwin":
            candidates.append(folder / "Blender.app/Contents/MacOS/Blender")
        elif system == "Windows":
            candidates.extend(
                [folder / "blender.exe", *folder.glob("blender-*-windows-x64/blender.exe")]
            )
        else:
            candidates.extend(
                [
                    folder / "blender",
                    *folder.glob("blender-*-linux-x64/blender"),
                ]
            )
    for candidate in candidates:
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return candidate.resolve()
    raise ValueError("Blender not found: set BLENDER=/path/to/blender or use --blender PATH")


def isolated_environment(profile, temporary, environ=None):
    """Do not inherit credentials, source paths, or any user's Blender directory overrides."""
    original = os.environ if environ is None else environ
    env = {
        key: value
        for key, value in original.items()
        if not key.startswith(("SCENARIO_", "BLENDER_", "PYTHON"))
    }
    env["BLENDER_USER_RESOURCES"] = str(Path(profile).resolve())
    env["PYTHONNOUSERSITE"] = "1"
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    for key in ("TMPDIR", "TEMP", "TMP"):
        env[key] = str(Path(temporary).resolve())
    return env


def sha256(path):
    with Path(path).open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def inspect_zip(path):
    """Reject unsafe or ambiguous layouts before Blender installs the archive."""
    files = {}
    with zipfile.ZipFile(path) as archive:
        zip_limits.check_archive(archive)
        manifest = tomllib.loads(
            zip_limits.read_member(
                archive, "blender_manifest.toml", limit=zip_limits.MAX_METADATA_BYTES
            ).decode()
        )
        for entry in archive.infolist():
            name = PurePosixPath(entry.filename)
            if (
                name.is_absolute()
                or ".." in name.parts
                or "\\" in entry.filename
                or "\\" in entry.orig_filename
                or ":" in entry.filename
                or entry.filename in files
                or stat.S_ISLNK(entry.external_attr >> 16)
            ):
                raise ValueError("Unsafe or duplicate extension ZIP member")
            if not entry.is_dir():
                with archive.open(entry) as member:
                    files[entry.filename] = hashlib.file_digest(member, "sha256").hexdigest()
        if not re.fullmatch(r"[a-z][a-z0-9_]*", manifest["id"]):
            raise ValueError("Invalid extension module ID")
        if "LICENSE" not in files or "__init__.py" not in files:
            raise ValueError("Extension ZIP must include LICENSE and __init__.py")
    return manifest, files


def verify_installed(archive_path, package_dir):
    manifest, expected = inspect_zip(archive_path)
    package_dir = Path(package_dir).resolve()
    actual = {}
    for path in package_dir.rglob("*"):
        relative = path.relative_to(package_dir)
        if "__pycache__" in relative.parts or path.suffix == ".pyc":
            continue
        if path.is_symlink():
            raise ValueError("Installed package contains a symlink")
        if path.is_file():
            actual[relative.as_posix()] = sha256(path)
    if actual != expected:
        missing = sorted(expected.keys() - actual.keys())
        extra = sorted(actual.keys() - expected.keys())
        changed = sorted(
            name for name in expected.keys() & actual.keys() if expected[name] != actual[name]
        )
        raise ValueError(
            f"Installed package differs from candidate ZIP: missing={missing}, extra={extra}, changed={changed}"
        )
    return manifest


def normal_profile_root():
    """Find the regular profile independently of Blender's isolated environment."""
    if platform.system() == "Darwin":
        return Path.home() / "Library/Application Support/Blender"
    if platform.system() == "Windows":
        return (
            Path(os.environ.get("APPDATA", Path.home() / "AppData/Roaming"))
            / "Blender Foundation/Blender"
        )
    return Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "blender"


def profile_snapshot(root):
    """Record file metadata without reading credentials or following directory symlinks."""
    result = {}
    if root.exists():
        for folder, directories, files in os.walk(root, followlinks=False):
            for name in directories + files:
                path = Path(folder) / name
                info = path.lstat()
                result[str(path.relative_to(root))] = (info.st_mode, info.st_size, info.st_mtime_ns)
    return result


def _forward_log(path, stopped, destinations, errors):
    """Tail the owned regular log, so child stdout never depends on a pipe reader."""
    destinations = list(destinations)
    decoder = io.IncrementalNewlineDecoder(
        codecs.getincrementaldecoder("utf-8")("replace"), translate=True
    )
    try:
        with path.open("rb") as source:
            while True:
                # Only an empty read started after child completion proves EOF.
                # The child may append between a temporary EOF and the event read.
                completed = stopped.is_set()
                chunk = source.read(65536)
                finished = not chunk and completed
                text = decoder.decode(chunk, final=finished)
                if text:
                    for destination in tuple(destinations):
                        try:
                            destination.write(text)
                            destination.flush()
                        except Exception as error:
                            errors.append(error)
                            destinations.remove(destination)
                if finished:
                    return
                if not chunk:
                    stopped.wait(0.025)
    except Exception as error:
        errors.append(error)


def run_step(binary, args, *, env, directory, name, timeout, stream=False, log_output=None):
    log = directory / f"{name}.log"
    print(f"{name}: {log}", flush=True)
    destinations = ([sys.stdout] if stream else []) + (
        [log_output] if log_output is not None else []
    )
    if log_output is not None:
        log_output.write(f"\n[{name}]\n")
        log_output.flush()
    stopped, errors = threading.Event(), []
    with log.open("w", encoding="utf-8") as output:
        reader = None
        if destinations:
            reader = threading.Thread(
                target=_forward_log,
                args=(log, stopped, destinations, errors),
                name=f"blender-output-{name}",
                daemon=True,
            )
            reader.start()
        try:
            result = subprocess.run(
                [str(binary), *args],
                cwd=directory,
                env=env,
                stdout=output,
                stderr=subprocess.STDOUT,
                timeout=timeout,
            )
        finally:
            # subprocess.run reaps a timed-out/interrupted child before returning.
            # Drain the final log bytes before any caller closes its combined log.
            stopped.set()
            if reader is not None:
                reader.join()
    if result.returncode:
        raise subprocess.CalledProcessError(result.returncode, name)
    if errors:
        raise OSError(f"Could not forward Blender output; phase log retained at {log}") from errors[
            0
        ]
    return log


def run(args, isolated=True, check=True, *, blender=None, artifacts=None, timeout=300):
    """Run trusted Blender arguments offline in a fresh profile, then remove it.

    This is process/profile isolation, not a security sandbox for Python scripts.
    Streams stay attached to the caller and relative input paths keep its cwd.
    """
    if isolated is not True:
        raise ValueError("Normal-profile execution is not supported")
    if isinstance(args, (str, bytes)):
        raise ValueError("Supply Blender arguments as a sequence of strings")
    arguments = list(args)
    if not arguments or not all(isinstance(value, str) for value in arguments):
        raise ValueError("Supply Blender arguments after run --")
    blender_arguments = arguments[: arguments.index("--")] if "--" in arguments else arguments
    if any(value.split("=", 1)[0] == "--online-mode" for value in blender_arguments):
        raise ValueError("Isolated commands require Blender offline mode")
    if not math.isfinite(timeout) or timeout <= 0:
        raise ValueError("Timeout must be finite and positive")
    binary = find_blender(blender)
    root = (ROOT / ".blender-profile" if artifacts is None else Path(artifacts)).resolve()
    if root.is_relative_to(normal_profile_root().resolve()):
        raise ValueError("Command artifacts must be outside the normal Blender profile")
    root.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="command-", dir=root) as directory:
        profile = Path(directory) / "profile"
        temporary = Path(directory) / "tmp"
        profile.mkdir()
        temporary.mkdir()
        # subprocess.run waits for child termination on timeout/control failures,
        # so storage remains alive for the entire child lifetime.
        return subprocess.run(
            [str(binary), "--offline-mode", *arguments],
            env=isolated_environment(profile, temporary),
            check=check,
            timeout=timeout,
        )


def main():
    parser = argparse.ArgumentParser(
        description="Locate Blender, print the extension version, or run an isolated command."
    )
    selection = parser.add_mutually_exclusive_group()
    selection.add_argument(
        "--blender", help="Executable path or command; defaults to BLENDER/discovery"
    )
    selection.add_argument(
        "--version",
        "--manifest-version",
        action="store_true",
        dest="manifest_version",
        help="Print the extension manifest version without launching Blender",
    )
    commands = parser.add_subparsers(dest="command")
    command = commands.add_parser("run", help="Run trusted Blender arguments in a fresh profile")
    command.add_argument("--blender", dest="run_blender", help="Override Blender discovery")
    command.add_argument("--artifacts", type=Path, help="Parent for temporary command profiles")
    command.add_argument("--timeout", type=float, default=300, help="Child timeout in seconds")
    command.add_argument("arguments", nargs=argparse.REMAINDER, metavar="-- BLENDER_ARGS")
    args = parser.parse_args()
    if args.command == "run" and args.manifest_version:
        parser.error("--version cannot be combined with run")
    try:
        if args.command == "run":
            arguments = args.arguments[1:] if args.arguments[:1] == ["--"] else args.arguments
            if not arguments:
                parser.error("Supply Blender arguments after run --")
            result = run(
                arguments,
                check=False,
                blender=args.run_blender or args.blender,
                artifacts=args.artifacts,
                timeout=args.timeout,
            )
            return result.returncode if result.returncode >= 0 else 128 - result.returncode
        value = manifest_version() if args.manifest_version else find_blender(args.blender)
    except subprocess.TimeoutExpired:
        print("Isolated Blender command timed out", file=sys.stderr)
        return 124
    except KeyboardInterrupt:
        return 130
    except (OSError, ValueError) as error:
        print(error, file=sys.stderr)
        return 1
    print(value)
    return 0


if __name__ == "__main__":
    sys.exit(main())
