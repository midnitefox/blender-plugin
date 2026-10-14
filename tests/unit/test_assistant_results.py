# SPDX-FileCopyrightText: 2026 Open Studio contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Durable local bytes and explicit reuse recipes, independent of image decoding."""

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from scenario.core import assistant_results as results
from scenario.core.jobs.transfers import TransferError


@pytest.fixture
def storage(tmp_path):
    inbox, library, reuse = (tmp_path / name for name in ("inbox", "library", "reuse"))
    inbox.mkdir()
    reuse.mkdir()
    return library, inbox, reuse


def file(inbox, name="concept.png", data=b"unvalidated image bytes", role="asset"):
    (inbox / name).write_bytes(data)
    return {"filename": name, "sha256": hashlib.sha256(data).hexdigest(), "role": role}


def image(inbox, title="Concept", name="concept.png", data=b"unvalidated image bytes"):
    return {"kind": "IMAGE", "title": title, "files": [file(inbox, name, data)]}


def material(inbox):
    return {
        "kind": "MATERIAL",
        "title": "Copper maps",
        "description": "An explicit saved set",
        "files": [
            file(inbox, "a-base.exr", b"base map bytes", "base_color"),
            file(inbox, "b-smooth.exr", b"smoothness bytes", "smoothness"),
            file(inbox, "c-edge.png", b"edge map bytes", "edge"),
        ],
    }


def bundle_files(library, record):
    return library / record["result_id"] / "files"


def test_survives_inbox_deletion_and_new_process_with_multifile_recipe(storage):
    library, inbox, reuse = storage
    args = material(inbox)
    record = results.save(library, inbox, args)
    expected = {item["filename"]: (inbox / item["filename"]).read_bytes() for item in args["files"]}
    for path in inbox.iterdir():
        path.unlink()
    program = """
import json, sys
from pathlib import Path
from scenario.core import assistant_results as r
root, inbox = map(Path, sys.argv[1:3])
result_id = sys.argv[3]
listed = r.listing(root)
record = r.inspect(root, result_id, verify=True)
prepared = r.prepare(root, inbox, result_id)
print(json.dumps({'listing': listed, 'record': record, 'prepared': prepared,
                 'sdk_loaded': any(n.endswith('.sdk_adapter') or n.startswith('scenario_sdk') for n in sys.modules)}))
"""
    completed = subprocess.run(
        [sys.executable, "-c", program, str(library), str(reuse), record["result_id"]],
        cwd=Path(__file__).resolve().parents[2],
        check=True,
        capture_output=True,
        text=True,
        timeout=30,
    )
    fresh = json.loads(completed.stdout)
    assert fresh["record"]["verified"] is True
    assert fresh["listing"]["total"] == 1
    assert fresh["listing"]["content_verified"] is False
    assert fresh["sdk_loaded"] is False
    prepared = fresh["prepared"]
    assert prepared["scene_changed"] is False
    assert prepared["import_recipe"]["tool"] == "import_agent_material"
    assert prepared["import_recipe"]["choose_before_import"] == ["object_name"]
    assert prepared["import_recipe"]["arguments"]["maps"] == {
        item["role"]: {key: item[key] for key in ("filename", "sha256")} for item in args["files"]
    }
    assert {path.name: path.read_bytes() for path in reuse.iterdir()} == expected
    assert all(path.stat().st_nlink == 1 for path in reuse.iterdir())


@pytest.mark.parametrize("main", ["scene.gltf", "scene.obj"])
def test_model_package_recipe_preserves_companion_names_and_roles(storage, main):
    library, inbox, reuse = storage
    args = {"kind": "MODEL_PACKAGE", "title": "Model", "files": [file(inbox, main)]}
    if main.endswith(".gltf"):
        args["files"] += [
            file(inbox, "mesh.bin", role="resource"),
            file(inbox, "map.png", role="resource"),
        ]
    else:
        args["files"] += [
            file(inbox, "mesh.mtl", role="resource"),
            file(inbox, "map.png", role="base_color"),
        ]
    record = results.save(library, inbox, args)
    recipe = results.prepare(library, reuse, record["result_id"])["import_recipe"]
    assert recipe["tool"] == "import_agent_model_package"
    assert recipe["arguments"]["filename"] == main
    companions = {entry["filename"]: entry for entry in recipe["arguments"]["resources"]}
    assert "role" not in companions["mesh.bin" if main.endswith(".gltf") else "mesh.mtl"]
    if main.endswith(".obj"):
        assert companions["map.png"]["role"] == "base_color"
    else:
        assert "role" not in companions["map.png"]


@pytest.mark.parametrize(
    "kind,name,tool,choice",
    [
        ("IMAGE", "image.png", "import_agent_image", "mode (IMAGE, REFERENCE or TEXTURE)"),
        ("ENVIRONMENT", "world.hdr", "import_agent_environment", None),
        ("MODEL", "character.glb", "import_agent_model", None),
        ("SPLAT", "world.spz", "import_agent_splat", "axes"),
        ("AUDIO", "music.wav", "import_agent_media", "channel"),
        ("VIDEO", "clip.mp4", "import_agent_media", "start_frame"),
    ],
)
def test_single_asset_recipes_leave_destination_choices_explicit(storage, kind, name, tool, choice):
    library, inbox, reuse = storage
    record = results.save(
        library, inbox, {"kind": kind, "title": kind, "files": [file(inbox, name)]}
    )
    recipe = results.prepare(library, reuse, record["result_id"])["import_recipe"]
    assert recipe["tool"] == tool
    assert recipe["arguments"]["filename"] == name
    assert "handle" not in recipe["arguments"]
    if choice:
        assert choice in recipe["choose_before_import"]
    if kind in {"AUDIO", "VIDEO"}:
        assert recipe["arguments"]["kind"] == kind


def test_repeat_save_and_prepare_are_verified_without_overwrite_at_capacity(storage, monkeypatch):
    library, inbox, reuse = storage
    monkeypatch.setattr(results, "MAX_RECORDS", 1)
    args = image(inbox)
    saved = results.save(library, inbox, args)
    manifest = library / saved["result_id"] / "result.json"
    original = manifest.read_bytes(), manifest.stat().st_mtime_ns
    repeated = results.save(library, inbox, args)
    assert repeated["reused"] is True
    assert repeated["created_at"] == saved["created_at"]
    assert (manifest.read_bytes(), manifest.stat().st_mtime_ns) == original
    results.prepare(library, reuse, saved["result_id"])
    retained = reuse / "concept.png"
    before = retained.stat().st_ino, retained.stat().st_mtime_ns
    results.prepare(library, reuse, saved["result_id"])
    assert (retained.stat().st_ino, retained.stat().st_mtime_ns) == before
    with pytest.raises(ValueError, match="full"):
        results.save(library, inbox, {**args, "title": "Different metadata"})
    assert {path.name for path in library.iterdir()} == {saved["result_id"]}


@pytest.mark.parametrize("mutation", ["tamper", "missing"])
def test_file_integrity_failure_refuses_verify_preview_reuse_and_resave(storage, mutation):
    library, inbox, reuse = storage
    args = material(inbox)
    saved = results.save(library, inbox, args)
    damaged = bundle_files(library, saved) / "c-edge.png"
    if mutation == "tamper":
        damaged.write_bytes(b"X" * damaged.stat().st_size)
    else:
        damaged.unlink()
    assert results.listing(library)["content_verified"] is False
    assert results.inspect(library, saved["result_id"])["verified"] is False
    for action in (
        lambda: results.inspect(library, saved["result_id"], verify=True),
        lambda: results.prepare(library, reuse, saved["result_id"]),
        lambda: results.read_preview(library, saved["result_id"], "c-edge.png"),
        lambda: results.save(library, inbox, args),
    ):
        with pytest.raises(ValueError):
            action()
    assert not list(reuse.iterdir())
    assert {path.name for path in library.iterdir()} == {saved["result_id"]}


@pytest.mark.parametrize("mutation", ["title", "broken_json", "deep_json"])
def test_damaged_metadata_is_counted_and_cannot_be_prepared(storage, mutation):
    library, inbox, reuse = storage
    saved = results.save(library, inbox, image(inbox))
    manifest = library / saved["result_id"] / "result.json"
    if mutation == "title":
        raw = json.loads(manifest.read_bytes())
        raw["asset"]["title"] = "Changed after publication"
        manifest.write_text(json.dumps(raw))
    else:
        manifest.write_bytes(b"{" if mutation == "broken_json" else b"[" * 1200 + b"]" * 1200)
    listed = results.listing(library)
    assert listed["damaged_records"] == 1
    assert listed["total"] == 0
    with pytest.raises(ValueError, match="metadata"):
        results.prepare(library, reuse, saved["result_id"])
    assert not list(reuse.iterdir())


def test_preview_reads_only_an_explicit_saved_image(storage):
    library, inbox, _ = storage
    saved = results.save(library, inbox, material(inbox))
    record, snapshot = results.read_preview(library, saved["result_id"], "a-base.exr")
    assert record["result_id"] == saved["result_id"]
    assert snapshot.data == b"base map bytes"
    with pytest.raises(ValueError):
        results.read_preview(library, saved["result_id"], "../a-base.exr")


def test_prepare_collision_preserves_existing_file_and_publishes_nothing(storage):
    library, inbox, reuse = storage
    saved = results.save(library, inbox, material(inbox))
    (reuse / "b-smooth.exr").write_bytes(b"keep this unrelated file")
    with pytest.raises(ValueError, match="occupied"):
        results.prepare(library, reuse, saved["result_id"])
    assert {path.name: path.read_bytes() for path in reuse.iterdir()} == {
        "b-smooth.exr": b"keep this unrelated file"
    }


@pytest.mark.parametrize("failure", ["read", "write", "publish"])
def test_failed_save_preserves_old_records_and_removes_staging(storage, monkeypatch, failure):
    library, inbox, _ = storage
    existing = results.save(library, inbox, image(inbox))
    args = material(inbox)
    if failure == "read":
        (inbox / "c-edge.png").unlink()
    elif failure == "write":
        original = Path.write_bytes

        def write(path, data):
            if path.name == "b-smooth.exr" and path.parent.name == "files":
                raise OSError("write failed")
            return original(path, data)

        monkeypatch.setattr(Path, "write_bytes", write)
    else:

        def rename(*args):
            raise OSError("publish failed")

        monkeypatch.setattr(results.os, "rename", rename)
    with pytest.raises((OSError, ValueError)):
        results.save(library, inbox, args)
    assert {path.name for path in library.iterdir()} == {existing["result_id"]}
    assert results.inspect(library, existing["result_id"], verify=True)["verified"] is True


@pytest.mark.parametrize("external_change", [None, "deleted", "replaced", "edited"])
def test_failed_prepare_rolls_back_own_files_and_preserves_external_changes(
    storage, monkeypatch, external_change
):
    library, inbox, reuse = storage
    saved = results.save(library, inbox, material(inbox))
    original = os.link

    def link(source, destination, *args, **kwargs):
        if Path(destination).name == "c-edge.png":
            published = reuse / "b-smooth.exr"
            if external_change in {"deleted", "replaced"}:
                published.unlink()
            if external_change in {"replaced", "edited"}:
                published.write_bytes(b"preserve another writer's bytes")
            raise OSError("publication failed")
        return original(source, destination, *args, **kwargs)

    monkeypatch.setattr(results.os, "link", link)
    with pytest.raises(OSError, match="publication failed"):
        results.prepare(library, reuse, saved["result_id"])
    assert not (reuse / "a-base.exr").exists()
    assert not (reuse / "c-edge.png").exists()
    expected = {"b-smooth.exr"} if external_change in {"replaced", "edited"} else set()
    assert {path.name for path in reuse.iterdir()} == expected
    if expected:
        assert (reuse / "b-smooth.exr").read_bytes() == b"preserve another writer's bytes"


def test_prepare_collision_created_after_preflight_is_never_overwritten(storage, monkeypatch):
    library, inbox, reuse = storage
    saved = results.save(library, inbox, material(inbox))
    original = os.link

    def link(source, destination, *args, **kwargs):
        if Path(destination).name == "b-smooth.exr":
            Path(destination).write_bytes(b"concurrent file")
        return original(source, destination, *args, **kwargs)

    monkeypatch.setattr(results.os, "link", link)
    with pytest.raises(ValueError, match="changed"):
        results.prepare(library, reuse, saved["result_id"])
    assert {path.name: path.read_bytes() for path in reuse.iterdir()} == {
        "b-smooth.exr": b"concurrent file"
    }


@pytest.mark.parametrize(
    "name",
    ["../outside.png", "a/b.png", "C:\\outside.png", "CON.png", "map.png.", "a" * 121 + ".png"],
)
def test_invalid_paths_fail_without_publishing(storage, name):
    library, inbox, _ = storage
    with pytest.raises(ValueError):
        results.save(
            library,
            inbox,
            {
                "kind": "IMAGE",
                "title": "Invalid",
                "files": [{"filename": name, "sha256": "0" * 64}],
            },
        )
    assert not list(library.iterdir())


@pytest.mark.parametrize(
    "change",
    [
        "role_type",
        "kind_type",
        "duplicate_name",
        "duplicate_role",
        "conflicting_maps",
        "missing_base",
        "missing_hash",
        "too_many",
        "extra_key",
    ],
)
def test_invalid_roles_and_file_lists_are_bounded(storage, change):
    library, inbox, _ = storage
    args = material(inbox)
    if change == "role_type":
        args["files"][0]["role"] = []
    elif change == "kind_type":
        args["kind"] = {}
    elif change == "duplicate_name":
        args["files"][1]["filename"] = args["files"][0]["filename"].upper()
    elif change == "duplicate_role":
        args["files"][1]["role"] = "base_color"
    elif change == "conflicting_maps":
        args["files"][2]["role"] = "roughness"
    elif change == "missing_base":
        args["files"][0]["role"] = "normal"
    elif change == "missing_hash":
        del args["files"][0]["sha256"]
    elif change == "too_many":
        args["files"] *= 44
    else:
        args["files"][0]["unexpected"] = True
    with pytest.raises(ValueError):
        results.save(library, inbox, args)
    assert not list(library.iterdir())


@pytest.mark.parametrize(
    "main,companion,role",
    [
        ("model.gltf", "map.mtl", "resource"),
        ("model.gltf", "map.png", "normal"),
        ("model.obj", "map.png", "resource"),
        ("model.obj", "map.bin", "resource"),
        ("model.fbx", "map.png", "resource"),
        ("model.ply", "map.png", "resource"),
    ],
)
def test_package_recipes_reject_structurally_unusable_companions(storage, main, companion, role):
    library, inbox, _ = storage
    args = {
        "kind": "MODEL_PACKAGE",
        "title": "Invalid package",
        "files": [file(inbox, main), file(inbox, companion, role=role)],
    }
    with pytest.raises(ValueError):
        results.save(library, inbox, args)
    assert not list(library.iterdir())


def test_combined_byte_limit_and_input_digest_refuse_without_publication(storage, monkeypatch):
    library, inbox, _ = storage
    args = material(inbox)
    monkeypatch.setattr(results, "MAX_ASSET_BYTES", 20)
    with pytest.raises(ValueError, match="size|512"):
        results.save(library, inbox, args)
    assert not list(library.iterdir())
    args = image(inbox, data=b"short")
    args["files"][0]["sha256"] = "0" * 64
    with pytest.raises(ValueError, match="changed"):
        results.save(library, inbox, args)
    assert not list(library.iterdir())


def test_listing_filter_pagination_and_invalid_types(storage):
    library, inbox, _ = storage
    first = results.save(library, inbox, image(inbox, title="Copper courtyard"))
    results.save(
        library, inbox, {"kind": "AUDIO", "title": "Music", "files": [file(inbox, "music.wav")]}
    )
    page = results.listing(library, limit=1)
    next_page = results.listing(library, offset=page["next_offset"], limit=1)
    assert page["total"] == 2 and next_page["next_offset"] is None
    assert page["results"][0]["result_id"] != next_page["results"][0]["result_id"]
    matches = results.listing(library, search="COPPER", kind="IMAGE")
    assert [row["result_id"] for row in matches["results"]] == [first["result_id"]]
    for args in ({"kind": []}, {"limit": True}, {"offset": -1}, {"search": "x" * 161}):
        with pytest.raises(ValueError):
            results.listing(library, **args)


def test_relative_and_symlinked_root_refused_before_creating_nested_paths(
    storage, tmp_path, monkeypatch
):
    _, inbox, _ = storage
    monkeypatch.chdir(tmp_path)
    with pytest.raises(ValueError):
        results.private_root(Path("relative"))
    assert not (tmp_path / "relative").exists()
    outside = tmp_path / "outside"
    outside.mkdir()
    link = tmp_path / "linked"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("Directory symlinks unavailable")
    with pytest.raises((ValueError, TransferError)):
        results.save(link / "nested", inbox, image(inbox))
    assert not (outside / "nested").exists()


def test_missing_filesystem_root_refused_without_unbounded_parent_walk(tmp_path, monkeypatch):
    def missing(path):
        raise FileNotFoundError("filesystem unavailable")

    monkeypatch.setattr(Path, "lstat", missing)
    with pytest.raises(ValueError, match="filesystem root"):
        results.private_root(tmp_path / "missing" / "library")


def test_hardlinked_asset_cannot_be_saved(storage, tmp_path):
    library, inbox, _ = storage
    outside = tmp_path / "outside.png"
    outside.write_bytes(b"external bytes")
    entry = {"filename": "linked.png", "sha256": hashlib.sha256(outside.read_bytes()).hexdigest()}
    os.link(outside, inbox / "linked.png")
    with pytest.raises(ValueError, match="regular"):
        results.save(library, inbox, {"kind": "IMAGE", "title": "Linked", "files": [entry]})
    assert outside.read_bytes() == b"external bytes"
    assert not list(library.iterdir())


def test_manifest_limit_prevents_unbounded_metadata_parse(storage):
    library, inbox, _ = storage
    saved = results.save(library, inbox, image(inbox))
    manifest = library / saved["result_id"] / "result.json"
    manifest.write_bytes(b" " * (results.MAX_MANIFEST + 1))
    assert results.listing(library)["damaged_records"] == 1
