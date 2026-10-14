# SPDX-FileCopyrightText: 2026 Open Studio contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Prove real assistant MP4 capture in an exact-ZIP isolated offline Blender GUI."""

import argparse
import json
import shutil
import subprocess
from pathlib import Path

from blender_env import (
    ROOT,
    find_blender,
    normal_profile_root,
    profile_snapshot,
    sha256,
    verify_installed,
)
from build import Session, build, validate


def run(args):
    session = Session(find_blender(args.blender), args.artifacts, args.timeout, prefix="capture-")
    normal = normal_profile_root()
    before = profile_snapshot(normal)
    report = dict(status="failed")
    try:
        if args.zip:
            candidate = session.directory / "candidate.zip"
            shutil.copyfile(args.zip.resolve(), candidate)
            manifest = validate(session, candidate)
        else:
            candidate = build(session, session.temporary / "dist")
            manifest = validate(session, candidate)
        report["zip_sha256"] = sha256(candidate)
        session.step(
            "install",
            [
                "--offline-mode",
                "--command",
                "extension",
                "install-file",
                "--repo",
                "user_default",
                "--enable",
                str(candidate),
            ],
        )
        installed = session.profile / "extensions" / "user_default" / manifest["id"]
        verify_installed(candidate, installed)
        session.env["OPEN_STUDIO_CAPTURE_PROBE"] = "1"
        session.step(
            "gui",
            [
                "--offline-mode",
                "--disable-autoexec",
                "--python-exit-code",
                "1",
                "--python",
                str(ROOT / "tests/blender/agent_capture_gui.py"),
                "--",
                str(session.directory),
                str(installed),
            ],
        )
        result = json.loads((session.directory / "gui.json").read_text(encoding="utf-8"))
        report["gui"] = result
        if result.get("status") != "passed":
            raise ValueError("Real GUI capture did not pass")
        verify_installed(candidate, installed)
        if sha256(candidate) != report["zip_sha256"]:
            raise ValueError("Candidate changed during capture")
        ffprobe = shutil.which("ffprobe")
        if ffprobe is None:
            raise ValueError("Install ffprobe to independently verify capture timing and silence")
        probes = []
        for item in result["outputs"]:
            path = Path(item["evidence_path"])
            metadata = json.loads(
                subprocess.run(
                    [
                        ffprobe,
                        "-v",
                        "error",
                        "-show_streams",
                        "-show_format",
                        "-of",
                        "json",
                        str(path),
                    ],
                    check=True,
                    capture_output=True,
                    text=True,
                    timeout=15,
                ).stdout
            )
            streams = metadata["streams"]
            if len(streams) != 1 or streams[0]["codec_type"] != "video":
                raise ValueError("Capture must contain exactly one silent video stream")
            video = streams[0]
            if (
                int(video["nb_frames"]) != item["frame_count"]
                or video["width"] != item["width"]
                or video["height"] != item["height"]
                or abs(float(video["duration"]) - item["duration_seconds"]) > 0.002
                or sha256(path) != item["sha256"]
            ):
                raise ValueError("Independent media readback disagrees with the handoff")
            probes.append(metadata)
        report["ffprobe"] = probes
        ffmpeg = shutil.which("ffmpeg")
        if ffmpeg is None:
            raise ValueError("Install ffmpeg to verify the actual camera framing")
        pixels = subprocess.run(
            [
                ffmpeg,
                "-v",
                "error",
                "-i",
                str(session.directory / "camera-framing.mp4"),
                "-vf",
                "crop=2:2",
                "-frames:v",
                "1",
                "-pix_fmt",
                "rgb24",
                "-f",
                "rawvideo",
                "-",
            ],
            check=True,
            capture_output=True,
            timeout=15,
        ).stdout
        rgb = [sum(pixels[index::3]) / (len(pixels) / 3) / 255 for index in range(3)]
        framing = result["camera_choice"]
        framing["animation_center_rgb"] = rgb
        red, blue = framing["center_rgb"]["CAMERA"], framing["center_rgb"]["VIEWPORT"]
        if not all(color[0] > 0.5 and color[0] > 2 * max(color[1:]) for color in (red, rgb)):
            raise ValueError("Camera capture did not frame the requested red camera")
        if not (blue[2] > 0.5 and blue[2] > 2 * max(blue[:2])):
            raise ValueError("Viewport capture did not preserve the pinned blue camera")
        if profile_snapshot(normal) != before:
            raise ValueError("Normal Blender profile changed during capture")
        report.update(status="passed", normal_profile_unchanged=True)
        session.cleanup()
        return 0
    finally:
        (session.directory / "report.json").write_text(
            json.dumps(report, indent=2), encoding="utf-8"
        )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--blender")
    parser.add_argument("--zip", type=Path)
    parser.add_argument("--artifacts", type=Path, default=ROOT / ".blender-profile")
    parser.add_argument("--timeout", type=float, default=180)
    return run(parser.parse_args())


if __name__ == "__main__":
    raise SystemExit(main())
