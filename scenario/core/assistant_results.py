# SPDX-FileCopyrightText: 2026 Open Studio contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Durable, explicit local asset bundles, independent of a scene or chat session."""

import hashlib
import json
import os
import re
import stat
import tempfile
from datetime import UTC, datetime
from pathlib import Path

from .agent_artifacts import MAX_ASSET_BYTES, read_inbox_file
from .jobs.transfers import TransferError, _root, validate_result_name

KINDS = {
    "IMAGE": ({".png", ".jpg", ".jpeg", ".webp"}, "import_agent_image"),
    "MATERIAL": ({".png", ".jpg", ".jpeg", ".webp", ".exr"}, "import_agent_material"),
    "ENVIRONMENT": ({".png", ".jpg", ".jpeg", ".exr", ".hdr"}, "import_agent_environment"),
    "MODEL": ({".glb"}, "import_agent_model"),
    "MODEL_PACKAGE": ({".gltf", ".obj", ".fbx", ".ply"}, "import_agent_model_package"),
    "SPLAT": ({".spz", ".splat", ".ply"}, "import_agent_splat"),
    "AUDIO": ({".wav", ".mp3", ".ogg"}, "import_agent_media"),
    "VIDEO": ({".mp4", ".webm"}, "import_agent_media"),
}
MAP_ROLES = {
    "base_color",
    "roughness",
    "smoothness",
    "metallic",
    "normal",
    "height",
    "ambient_occlusion",
    "edge",
}
RESOURCES = {".bin", ".mtl", ".png", ".jpg", ".jpeg", ".webp"}
MAX_RECORDS = 4096
MAX_MANIFEST = 128 * 1024


def private_root(path):
    path = Path(path)
    if not path.is_absolute():
        raise ValueError("Use an absolute private result directory")
    ancestor = path
    while True:
        try:
            ancestor.lstat()
            break
        except FileNotFoundError:
            if ancestor.parent == ancestor:
                raise ValueError(
                    "The private result directory needs an existing filesystem root"
                ) from None
            ancestor = ancestor.parent
    _directory(ancestor)
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    return _directory(path)


def _directory(path):
    path = _root(path)
    if getattr(path.lstat(), "st_file_attributes", 0) & getattr(
        stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400
    ):
        raise ValueError("Use a private directory without reparse points")
    return path


def _text(value, maximum, *, empty=False):
    if (
        not isinstance(value, str)
        or len(value) > maximum
        or (not empty and not value.strip())
        or any(ord(char) < 32 and char not in "\n\t" for char in value)
    ):
        raise ValueError("Use a bounded title or description without control characters")
    return value.strip()


def _canonical(value):
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode()


def _identity(payload):
    return hashlib.sha256(_canonical(payload)).hexdigest()


def _id(value):
    if not isinstance(value, str) or not re.fullmatch("[a-f0-9]{64}", value):
        raise ValueError("Choose a saved result_id from list_agent_results")
    return value


def _files(kind, files, *, stored=False):
    if not isinstance(kind, str) or kind not in KINDS:
        raise ValueError("Choose a supported saved asset kind")
    if not isinstance(files, list) or not 1 <= len(files) <= 128:
        raise ValueError("Supply 1 to 128 explicit asset files")
    names, roles, cleaned, total = set(), [], [], 0
    for item in files:
        allowed = {"filename", "sha256", "role"} | ({"size"} if stored else set())
        if not isinstance(item, dict) or set(item) - allowed:
            raise ValueError("Each saved file needs filename, sha256 and an optional role")
        name, digest, role = item.get("filename"), item.get("sha256"), item.get("role", "asset")
        if not isinstance(role, str):
            raise ValueError("Use an explicit supported file role")
        try:
            validate_result_name(name)
        except TransferError:
            raise ValueError("Saved files must use portable inbox basenames") from None
        if name.casefold() in names:
            raise ValueError("Saved file names must be unique, including letter case")
        _id(digest)
        suffix = Path(name).suffix.lower()
        if kind == "MATERIAL":
            valid = role in MAP_ROLES and suffix in KINDS[kind][0] and role not in roles
        elif kind == "MODEL_PACKAGE":
            valid = (role == "asset" and suffix in KINDS[kind][0]) or (
                role in {"resource", "base_color", "normal", "roughness", "metallic"}
                and suffix in RESOURCES
            )
        else:
            valid = role == "asset" and suffix in KINDS[kind][0]
        if not valid:
            raise ValueError("File role or extension does not match the saved asset kind")
        entry = {"filename": name, "sha256": digest, "role": role}
        if stored:
            size = item.get("size")
            if type(size) is not int or not 0 < size <= MAX_ASSET_BYTES:
                raise ValueError("Saved result has invalid file sizes")
            total += size
            if total > MAX_ASSET_BYTES:
                raise ValueError("Saved result exceeds 512 MiB")
            entry["size"] = size
        cleaned.append(entry)
        names.add(name.casefold())
        roles.append(role)
    if kind == "MATERIAL":
        if "base_color" not in roles or {"roughness", "smoothness"} <= set(roles):
            raise ValueError("Material needs base_color and at most one of roughness or smoothness")
    elif roles.count("asset") != 1 or (kind != "MODEL_PACKAGE" and len(files) != 1):
        raise ValueError("Save one primary asset, with explicit package resources when needed")
    if kind == "MODEL_PACKAGE":
        primary = next(item for item in cleaned if item["role"] == "asset")
        suffix = Path(primary["filename"]).suffix.lower()
        companions = [item for item in cleaned if item["role"] != "asset"]
        if suffix in {".fbx", ".ply"} and companions:
            raise ValueError("FBX and mesh PLY must be saved without companion files")
        for item in companions:
            extension, role = Path(item["filename"]).suffix.lower(), item["role"]
            if suffix == ".gltf" and (extension == ".mtl" or role != "resource"):
                raise ValueError("glTF companions must be buffers or images without map roles")
            if suffix == ".obj" and (
                extension == ".bin"
                or (extension == ".mtl" and role != "resource")
                or (extension != ".mtl" and role == "resource")
            ):
                raise ValueError("OBJ companions need MTL resources and explicit image map roles")
    return sorted(cleaned, key=lambda entry: entry["filename"])


def _read_record(root, result_id):
    directory = _directory(root / _id(result_id))
    raw = read_inbox_file(directory, "result.json", suffixes={".json"}, max_bytes=MAX_MANIFEST)
    try:
        record = json.loads(raw.data)
        if not isinstance(record, dict) or set(record) != {"result_id", "created_at", "asset"}:
            raise ValueError
        asset = record["asset"]
        if not isinstance(asset, dict) or set(asset) != {"title", "description", "kind", "files"}:
            raise ValueError
        normalized = {
            "title": _text(asset["title"], 160),
            "description": _text(asset["description"], 4000, empty=True),
            "kind": asset["kind"],
            "files": _files(asset["kind"], asset["files"], stored=True),
        }
        if normalized != asset or record["result_id"] != result_id or _identity(asset) != result_id:
            raise ValueError
        _text(record["created_at"], 40)
    except (ValueError, KeyError, TypeError, UnicodeError, RecursionError):
        raise ValueError("Saved result metadata is damaged or was changed") from None
    return record


def _snapshot(directory, item, maximum=MAX_ASSET_BYTES):
    return read_inbox_file(
        directory,
        item["filename"],
        item["sha256"],
        suffixes={Path(item["filename"]).suffix.lower()},
        max_bytes=maximum,
    )


def save(root, inbox, args):
    """Copy verified bytes before publishing a complete immutable record."""
    if not isinstance(args, dict) or set(args) - {"kind", "files", "title", "description"}:
        raise ValueError("Supply a title, kind, files and optional description")
    root, inbox = private_root(root), _directory(inbox)
    kind = args.get("kind")
    files = _files(kind, args.get("files"))
    title = _text(args.get("title"), 160)
    description = _text(args.get("description", ""), 4000, empty=True)
    # Bounded directory scan also avoids silently losing results from later listings.
    _record_ids(root)
    with tempfile.TemporaryDirectory(prefix=".saving-", dir=root) as temporary:
        stage = Path(temporary) / "result"
        stage.mkdir(mode=0o700)
        content = stage / "files"
        content.mkdir(mode=0o700)
        total, stored = 0, []
        for item in files:
            remaining = MAX_ASSET_BYTES - total
            if remaining <= 0:
                raise ValueError("A saved result can contain at most 512 MiB")
            snapshot = _snapshot(inbox, item, remaining)
            (content / snapshot.name).write_bytes(snapshot.data)
            total += len(snapshot.data)
            stored.append({**item, "size": len(snapshot.data)})
        asset = {"title": title, "description": description, "kind": kind, "files": stored}
        result_id = _identity(asset)
        record = {
            "result_id": result_id,
            "created_at": datetime.now(UTC).isoformat(),
            "asset": asset,
        }
        (stage / "result.json").write_bytes(_canonical(record))
        destination = root / result_id
        if os.path.lexists(destination):
            # Repetition is safe only if the existing record AND its contents still match.
            verified = inspect(root, result_id, verify=True)
            return {**verified, "reused": True}
        if len(_record_ids(root)) >= MAX_RECORDS:
            raise ValueError("Local result library is full; archive unneeded result folders first")
        try:
            os.rename(stage, destination)
        except OSError:
            if os.path.lexists(destination):
                return {**inspect(root, result_id, verify=True), "reused": True}
            raise
    return {**record, "verified": True, "reused": False}


def _record_ids(root):
    ids = []
    with os.scandir(root) as entries:
        for index, entry in enumerate(entries):
            if index >= MAX_RECORDS * 2:
                raise ValueError("Local result directory has too many entries to inspect safely")
            if re.fullmatch("[a-f0-9]{64}", entry.name):
                ids.append(entry.name)
                if len(ids) > MAX_RECORDS:
                    raise ValueError("Local result library exceeds its listing limit")
    return ids


def listing(root, *, offset=0, limit=25, kind=None, search=""):
    root = private_root(root)
    if type(offset) is not int or not 0 <= offset <= MAX_RECORDS:
        raise ValueError("Use a result offset from 0 to 4096")
    if type(limit) is not int or not 1 <= limit <= 100:
        raise ValueError("Request 1 to 100 results at a time")
    if kind is not None and (not isinstance(kind, str) or kind not in KINDS):
        raise ValueError("Choose a supported saved asset kind")
    search = _text(search, 160, empty=True).casefold()
    rows, invalid = [], 0
    for result_id in _record_ids(root):
        try:
            record = _read_record(root, result_id)
        except (OSError, ValueError, TransferError):
            invalid += 1
            continue
        asset = record["asset"]
        if kind is not None and asset["kind"] != kind:
            continue
        if search not in (asset["title"] + " " + asset["description"]).casefold():
            continue
        rows.append(
            {
                "result_id": result_id,
                "title": asset["title"],
                "kind": asset["kind"],
                "created_at": record["created_at"],
                "file_count": len(asset["files"]),
                "bytes": sum(item["size"] for item in asset["files"]),
            }
        )
    rows.sort(key=lambda item: (item["created_at"], item["result_id"]), reverse=True)
    return {
        "results": rows[offset : offset + limit],
        "total": len(rows),
        "next_offset": offset + limit if offset + limit < len(rows) else None,
        "damaged_records": invalid,
        "content_verified": False,
    }


def inspect(root, result_id, *, verify=False):
    root = _directory(root)
    record = _read_record(root, result_id)
    if verify:
        directory = _directory(root / result_id / "files")
        for item in record["asset"]["files"]:
            snapshot = _snapshot(directory, item)
            if len(snapshot.data) != item["size"]:
                raise ValueError("Saved result file size changed")
    return {**record, "verified": verify}


def read_preview(root, result_id, filename):
    record = inspect(root, result_id)
    item = next((f for f in record["asset"]["files"] if f["filename"] == filename), None)
    if item is None or Path(filename).suffix.lower() not in {
        ".png",
        ".jpg",
        ".jpeg",
        ".webp",
        ".exr",
    }:
        raise ValueError("Choose a saved PNG, JPEG, WebP or EXR image for preview")
    snapshot = _snapshot(_directory(Path(root) / result_id / "files"), item, 128 * 1024 * 1024)
    return record, snapshot


def import_recipe(record):
    asset = record["asset"]
    kind, files = asset["kind"], asset["files"]

    def brief(item):
        return {key: item[key] for key in ("filename", "sha256")}

    required = []
    if kind == "MATERIAL":
        args = {"maps": {item["role"]: brief(item) for item in files}}
        required = ["object_name"]
    else:
        args = brief(next(item for item in files if item["role"] == "asset"))
        if kind == "MODEL_PACKAGE":
            args["resources"] = [
                {**brief(item), **({"role": item["role"]} if item["role"] != "resource" else {})}
                for item in files
                if item["role"] != "asset"
            ]
        elif kind == "IMAGE":
            required = ["mode (IMAGE, REFERENCE or TEXTURE)", "object_name when using TEXTURE"]
        elif kind in {"AUDIO", "VIDEO"}:
            args["kind"] = kind
            required = ["start_frame", "channel", "audio_channel if including video audio"]
        elif kind == "SPLAT":
            required = ["axes"]
    return {
        "tool": KINDS[kind][1],
        "arguments": args,
        "choose_before_import": required,
        "note": "Inspect the current scene and tool schema before importing. Saved files are not a mesh-edit handle or permission to replace an existing target. Import validates format and decode separately.",
    }


def prepare(root, inbox, result_id):
    """Stage the entire bundle, then publish without overwriting inbox files."""
    record = inspect(root, result_id)
    directory = _directory(Path(root) / result_id / "files")
    inbox = _directory(inbox)
    published = []
    with tempfile.TemporaryDirectory(prefix=".reuse-", dir=inbox) as temporary:
        stage = Path(temporary)
        for item in record["asset"]["files"]:
            snapshot = _snapshot(directory, item)
            if len(snapshot.data) != item["size"]:
                raise ValueError("Saved result file size changed")
            destination = inbox / snapshot.name
            if destination.exists() or destination.is_symlink():
                try:
                    _snapshot(inbox, item)
                except ValueError:
                    raise ValueError(
                        "An inbox filename is occupied by different content; preserve or move that file before reusing this result"
                    ) from None
            (stage / snapshot.name).write_bytes(snapshot.data)
        try:
            for item in record["asset"]["files"]:
                source, destination = stage / item["filename"], inbox / item["filename"]
                identity = source.stat()
                try:
                    os.link(source, destination)
                except FileExistsError:
                    _snapshot(inbox, item)
                    continue
                published.append((destination, identity))
                source.unlink()  # Restore the inbox's single-link file contract immediately.
        except Exception as error:
            cleanup_failed = False
            for destination, identity in reversed(published):
                try:
                    current = destination.lstat()
                    if stat.S_ISREG(current.st_mode) and _file_stamp(current) == _file_stamp(
                        identity
                    ):
                        destination.unlink()
                except FileNotFoundError:
                    continue
                except OSError:
                    cleanup_failed = True
            if cleanup_failed:
                error.add_note(
                    "Some newly prepared inbox files could not be cleaned up; inspect them before retrying"
                )
            raise
    return {
        "result_id": result_id,
        "inbox": str(inbox),
        "files": record["asset"]["files"],
        "verified": True,
        "scene_changed": False,
        "import_recipe": import_recipe(record),
    }


def _file_stamp(info):
    return info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns
