# SPDX-FileCopyrightText: 2026 Open Studio contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Local multi-file models and Gaussian point-cloud handoffs."""

import hashlib
import io
import json
import struct
import tempfile
import zlib
from pathlib import Path, PurePosixPath
from urllib.parse import unquote

import bpy

from ..blender import file_state, model_application, splat_application
from ..core.agent_artifacts import read_inbox_file
from ..core.jobs.store import ResultAsset, StoredResult
from ..core.jobs.transfers import DownloadedResult, TransferError, validate_result_name
from ..core.scene import model_formats, splats
from ..core.scene.model_references import _relative_stem
from .protocol import DeferredTool, ToolSpec
from .tools_agent import _inbox, _schema
from .tools_agent_models import _destination

_MODELS = {
    ".gltf": "model/gltf+json",
    ".obj": "model/obj",
    ".fbx": "model/x-fbx",
    ".ply": "model/ply",
}
_RESOURCES = {
    ".bin": "application/octet-stream",
    ".mtl": "model/mtl",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
}
_ROLES = {
    "base_color": "albedo",
    "normal": "normal",
    "roughness": "roughness",
    "metallic": "metallic",
}
_SPLATS = {".spz": "model/spz", ".splat": "model/splat", ".ply": "model/ply"}


def _fbx_array(data, expected):
    """Check compressed array lengths without allocating the expanded array."""
    decoder, produced = zlib.decompressobj(), 0
    try:
        for offset in range(0, len(data), 1 << 20):
            pending = data[offset : offset + (1 << 20)]
            while pending:
                block = decoder.decompress(pending, min(1 << 20, expected - produced + 1))
                produced += len(block)
                if produced > expected or decoder.unused_data:
                    raise ValueError("FBX array exceeds its declared size")
                pending = decoder.unconsumed_tail
        if not decoder.eof or produced != expected:
            raise ValueError("FBX array does not match its declared size")
    except zlib.error:
        raise ValueError("FBX contains a damaged compressed array") from None


def _embedded_fbx(data):
    """Refuse external FBX image access before Blender's importer can read it.

    Blender tries a texture's path before packing its embedded Content, even
    with use_image_search=False. Require a portable RelativeFilename on every
    Video/Texture and embedded Content for every matching image. The native
    importer then only probes the new private snapshot, where no image files
    exist, before packing the embedded bytes. Absolute fallback names are never
    consulted. Parse record/property bounds first, without decoding geometry.
    """
    model_formats.check_signature(model_formats.ModelFormat.FBX, data, len(data))
    if len(data) < 27:
        raise ValueError("Use a complete binary FBX")
    version = struct.unpack_from("<I", data, 23)[0]
    header = struct.Struct("<IIIB" if version < 7500 else "<QQQB")
    sentinel = bytes(header.size)
    view = memoryview(data)
    scalar = {
        ord(key): size
        for key, size in {"Z": 1, "Y": 2, "B": 1, "C": 1, "I": 4, "F": 4, "D": 8, "L": 8}.items()
    }
    arrays = {
        ord(key): size for key, size in {"b": 1, "c": 1, "i": 4, "l": 8, "f": 4, "d": 8}.items()
    }
    images, textures = set(), set()
    nodes = properties = expanded = 0

    def node(offset, limit, depth):
        nonlocal nodes, properties, expanded
        if depth > 64 or offset + header.size > limit:
            raise ValueError("FBX node nesting or bounds exceed the import limit")
        end, count, length, name_length = header.unpack_from(data, offset)
        if not offset + header.size + name_length <= end <= limit or not name_length:
            raise ValueError("Malformed FBX node bounds")
        nodes, properties = nodes + 1, properties + count
        if nodes > 100_000 or properties > 1_000_000:
            raise ValueError("FBX node or property count exceeds the import limit")
        start = offset + header.size
        name = data[start : start + name_length]
        cursor = start + name_length
        property_end = cursor + length
        if property_end > end:
            raise ValueError("Malformed FBX property bounds")
        value = None
        for _ in range(count):
            if cursor >= property_end:
                raise ValueError("Truncated FBX property")
            kind, cursor = data[cursor], cursor + 1
            if kind in scalar:
                cursor += scalar[kind]
            elif kind in (ord("S"), ord("R")):
                if cursor + 4 > property_end:
                    raise ValueError("Truncated FBX string")
                size = struct.unpack_from("<I", data, cursor)[0]
                cursor += 4
                if count == 1 and name == b"RelativeFilename" and kind == ord("S"):
                    if not 0 < size <= 4096:
                        raise ValueError("FBX textures need a portable relative filename")
                    value = bytes(view[cursor : cursor + size])
                elif count == 1 and name == b"Content" and kind == ord("R"):
                    value = size > 0
                cursor += size
            elif kind in arrays:
                if cursor + 12 > property_end:
                    raise ValueError("Truncated FBX array")
                size, encoding, compressed = struct.unpack_from("<III", data, cursor)
                cursor += 12
                expected = size * arrays[kind]
                expanded += expected
                if expanded > model_formats.MAX_PACKAGE_BYTES or cursor + compressed > property_end:
                    raise ValueError("FBX array storage exceeds the import limit")
                if encoding == 1:
                    _fbx_array(view[cursor : cursor + compressed], expected)
                elif encoding != 0 or compressed != expected:
                    raise ValueError("Unsupported FBX array encoding")
                cursor += compressed
            else:
                raise ValueError("Unsupported FBX property type")
            if cursor > property_end:
                raise ValueError("FBX property exceeds its node")
        if cursor != property_end:
            raise ValueError("FBX property size does not match its node")
        fields = {}
        if cursor < end:
            child_end = end - header.size
            if cursor > child_end or data[child_end:end] != sentinel:
                raise ValueError("Malformed FBX child sentinel")
            while cursor < child_end:
                cursor, child_name, child_value = node(cursor, child_end, depth + 1)
                if name in {b"Video", b"Texture"} and child_name in {
                    b"RelativeFilename",
                    b"Content",
                }:
                    if child_name in fields:
                        raise ValueError("Ambiguous FBX texture properties")
                    fields[child_name] = child_value
        if name in {b"Video", b"Texture"}:
            try:
                filename = fields[b"RelativeFilename"].decode("ascii").replace("\\", "/")
                parts = filename.split("/")
                if len(parts) > 32:
                    raise ValueError("FBX texture paths exceed the import limit")
                for part in parts:
                    validate_result_name(part)
            except (KeyError, AttributeError, UnicodeError, TransferError):
                raise ValueError("FBX textures need a portable relative filename") from None
            if name == b"Video":
                if fields.get(b"Content") is not True:
                    raise ValueError("FBX textures must contain embedded image data")
                images.add(filename)
            else:
                textures.add(filename)
        return end, name, value

    offset = 27
    while offset + header.size <= len(data) and data[offset : offset + header.size] != sentinel:
        offset, _, _ = node(offset, len(data), 0)
    if data[offset : offset + header.size] != sentinel or textures - images:
        raise ValueError("Use complete FBX records with matching embedded texture images")


def _gltf_document(data, resources):
    """Bind original URIs to exact explicitly supplied basenames before rewriting.

    Local filenames are not provider asset IDs. Preserve same-stem buffer/image
    pairs by giving each supplied file a unique internal resource identity.
    The existing strict glTF preflight still validates the entire document.
    """
    try:
        document = json.loads(data)
        for group in ("buffers", "images"):
            for entry in document.get(group, []):
                uri = entry.get("uri")
                if uri is None or (isinstance(uri, str) and uri.startswith("data:")):
                    continue
                if not isinstance(uri, str) or _relative_stem(uri) is None:
                    raise ValueError("glTF resources must be explicit relative package files")
                name = PurePosixPath(unquote(uri)).name
                if name not in resources:
                    raise ValueError("Supply every referenced glTF buffer and image in resources")
                identity, suffix = resources[name]
                entry["uri"] = identity + suffix
        return json.dumps(document, allow_nan=False).encode()
    except (KeyError, TypeError, AttributeError, UnicodeError, json.JSONDecodeError):
        raise ValueError("Use a valid glTF 2.0 document with explicit package resources") from None


def _stored(identity, path, data, media_type, role=None):
    path.write_bytes(data)
    digest = hashlib.sha256(data).hexdigest()
    return StoredResult(
        ResultAsset(identity, path.name, media_type, len(data), digest, texture_role=role),
        DownloadedResult(path.name, len(data), digest),
    ), path


def _package(args, inbox, directory):
    main = read_inbox_file(
        inbox,
        args.get("filename", ""),
        args.get("sha256"),
        suffixes=_MODELS,
        max_bytes=model_formats.MAX_MEMBER_BYTES,
    )
    supplied = args.get("resources", [])
    if main.suffix == ".gltf" and len(main.data) > model_formats.MAX_GLTF_JSON_BYTES:
        raise ValueError("glTF JSON exceeds 8 MiB")
    if main.suffix == ".fbx":
        _embedded_fbx(main.data)
    if not isinstance(supplied, list) or len(supplied) > 127:
        raise ValueError("Supply at most 127 explicit package resources")
    if main.suffix not in {".gltf", ".obj"} and supplied:
        raise ValueError("FBX must embed textures; mesh PLY has no companion resources")
    files, aliases, names, total = {}, {}, {main.name}, len(main.data)
    for index, item in enumerate(supplied):
        if not isinstance(item, dict) or set(item) - {"filename", "sha256", "role"}:
            raise ValueError(
                "Each resource needs filename, optional sha256 and optional texture role"
            )
        role = item.get("role")
        if role is not None and role not in _ROLES:
            raise ValueError("Use an explicit supported OBJ texture role")
        remaining = model_formats.MAX_PACKAGE_BYTES - total
        if remaining <= 0:
            raise ValueError("The package exceeds 512 MiB")
        snapshot = read_inbox_file(
            inbox,
            item.get("filename", ""),
            item.get("sha256"),
            suffixes=_RESOURCES,
            max_bytes=min(remaining, model_formats.MAX_MEMBER_BYTES),
        )
        if snapshot.name in names:
            raise ValueError("Supply each package file exactly once")
        names.add(snapshot.name)
        if main.suffix == ".gltf" and (snapshot.suffix == ".mtl" or role is not None):
            raise ValueError("glTF defines its own materials; supply only buffers and images")
        if main.suffix == ".obj" and (
            snapshot.suffix == ".bin" or (snapshot.suffix != ".mtl" and role is None)
        ):
            raise ValueError("OBJ resources are one MTL and explicitly role-labeled texture images")
        identity = f"resource-{index}"
        aliases[snapshot.name] = identity, snapshot.suffix
        files[identity] = _stored(
            identity,
            directory / (identity + snapshot.suffix),
            snapshot.data,
            _RESOURCES[snapshot.suffix],
            _ROLES.get(role),
        )
        total += len(snapshot.data)
    data = _gltf_document(main.data, aliases) if main.suffix == ".gltf" else main.data
    files["assistant-model"] = _stored(
        "assistant-model", directory / ("model" + main.suffix), data, _MODELS[main.suffix]
    )
    plan = model_formats.classify_packages(
        model_formats.members_from_results([item for item, _ in files.values()])
    )
    unit = plan.unit("assistant-model")
    if unit is None or plan.findings:
        raise ValueError(
            "Package resources are ambiguous, unsupported or not assigned to this model"
        )
    return main, unit, files


def import_agent_model_package(args):
    scene, location, name = _destination(args)
    inbox = _inbox()
    with tempfile.TemporaryDirectory(prefix="asset-package-", dir=inbox) as directory:
        main, unit, files = _package(args, inbox, Path(directory))
        snapshot = model_application.prepare_snapshot(unit, files)
        try:
            application = model_application.import_snapshot(scene, snapshot, cursor=location)
        except model_application.ModelApplicationError:
            raise
        except Exception:
            file_state.mark_modified("Open Studio package import needs inspection")
            raise
        else:
            try:
                application.collection.name = "Open Studio " + name
                application.root.name = "Open Studio " + name
                application.root["open_studio_model_sha256"] = main.sha256
                result = {
                    "collection": application.collection.name,
                    "root": application.root.name,
                    "objects": [obj.name for obj in application.objects],
                    "sha256": main.sha256,
                    "format": application.format.upper(),
                    "packed_textures": True,
                    "created_new_group": True,
                    "dropped_references": application.dropped_references,
                    "frame_rate_mismatch": application.frame_rate_mismatch,
                    "notes": "Each call creates a new group. Review dropped OBJ/MTL references. An FBX frame-rate mismatch means its clips need retiming; scene FPS is preserved.",
                }
            finally:
                # Record the completed names/metadata, even if response creation
                # or the subsequent temporary cleanup fails after publication.
                file_state.mark_modified("Open Studio assistant model package")
        finally:
            snapshot.cleanup()
    return result


def import_agent_splat(args):
    scene, location, name = _destination(args)
    snapshot = read_inbox_file(
        _inbox(),
        args.get("filename", ""),
        args.get("sha256"),
        suffixes=_SPLATS,
        max_bytes=model_formats.MAX_MEMBER_BYTES,
    )
    options = splats.SplatOptions(
        args.get("max_points", 1_000_000), args.get("axes", splats.FORMAT_AXES[snapshot.suffix[1:]])
    )

    def run():
        return splats.decode(
            io.BytesIO(snapshot.data),
            _SPLATS[snapshot.suffix],
            options=options,
            size=len(snapshot.data),
        )

    def finish(decoded):
        if bpy.context.scene != scene:
            raise ValueError(
                "The active scene changed during point-cloud preparation; inspect it again"
            )
        if decoded is None:
            raise ValueError("This PLY contains a mesh; use import_agent_model_package")
        try:
            application = splat_application.import_splat(
                scene,
                decoded,
                cursor=location,
                unit_key="assistant-" + snapshot.sha256,
                members=(),
                fmt=snapshot.suffix[1:],
            )
        except model_application.ModelApplicationError:
            raise
        except Exception:
            file_state.mark_modified("Open Studio point-cloud import needs inspection")
            raise
        try:
            application.collection.name = "Open Studio " + name
            application.root.name = "Open Studio " + name
            for obj in application.objects:
                obj.name = "Open Studio " + name + " points"
            return {
                "collection": application.collection.name,
                "root": application.root.name,
                "objects": [obj.name for obj in application.objects],
                "sha256": snapshot.sha256,
                **application.splat,
                "representation": "colored point cloud",
                "created_new_group": True,
                "notes": "Point-cloud approximation: Gaussian rotations, anisotropic shape and view-dependent color are not reproduced. SPZ coordinate extensions require checking the selected axes.",
            }
        finally:
            file_state.mark_modified("Open Studio assistant point cloud")

    return DeferredTool(run, finish)


SPECS = [
    ToolSpec(
        "import_agent_model_package",
        "Import an assistant-created glTF, OBJ, embedded-texture FBX or mesh PLY from the local inbox. "
        "List glTF buffers/images and OBJ MTL/texture maps explicitly in resources; copy all files into the flat inbox. "
        "OBJ texture images require base_color, normal, roughness or metallic role. Max 128 files, 256 MiB per file, "
        "512 MiB total; glTF JSON max 8 MiB and MTL max 4 MiB. Local references are rewritten into a private snapshot. "
        "Textures are packed. FBX textures must be embedded with portable relative paths without traversal; expanded FBX arrays max 512 MiB. "
        "FBX/OBJ/PLY and rigged glTF require a Blender window. Existing selection/timing is preserved. "
        "Inspect returned dropped_references and frame_rate_mismatch. Each call creates a new group; inspect before retrying.",
        _schema(
            {
                "filename": {"type": "string"},
                "sha256": {"type": "string"},
                "resources": {
                    "type": "array",
                    "maxItems": 127,
                    "items": {
                        "type": "object",
                        "properties": {
                            "filename": {"type": "string"},
                            "sha256": {"type": "string"},
                            "role": {
                                "type": "string",
                                "enum": ["base_color", "normal", "roughness", "metallic"],
                            },
                        },
                        "required": ["filename"],
                        "additionalProperties": False,
                    },
                },
                "location": {
                    "type": "array",
                    "items": {"type": "number"},
                    "minItems": 3,
                    "maxItems": 3,
                },
                "name": {"type": "string", "maxLength": 128, "default": "Model"},
            },
            ["filename"],
        ),
        import_agent_model_package,
        {"destructiveHint": False, "idempotentHint": False, "openWorldHint": False},
    ),
    ToolSpec(
        "import_agent_splat",
        "Import assistant-created SPZ v2-v3, Gaussian PLY or .splat as a colored point cloud. "
        "Copy the file into the local inbox. Max 256 MiB, 20 million source points, 2 million retained points. "
        "Choose OPENGL or OPENCV source axes; defaults are OPENGL for SPZ and OPENCV for PLY/.splat. "
        "This is a point-cloud approximation, without Gaussian anisotropy or view-dependent color. "
        "SPZ v1 and v4 are unsupported. Each call creates a new group at location or cursor; inspect before retrying.",
        _schema(
            {
                "filename": {"type": "string"},
                "sha256": {"type": "string"},
                "max_points": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 2000000,
                    "default": 1000000,
                },
                "axes": {"type": "string", "enum": ["OPENGL", "OPENCV"]},
                "location": {
                    "type": "array",
                    "items": {"type": "number"},
                    "minItems": 3,
                    "maxItems": 3,
                },
                "name": {"type": "string", "maxLength": 128, "default": "Point cloud"},
            },
            ["filename"],
        ),
        import_agent_splat,
        {"destructiveHint": False, "idempotentHint": False, "openWorldHint": False},
    ),
]
