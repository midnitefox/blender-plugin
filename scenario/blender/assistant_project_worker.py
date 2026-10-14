# SPDX-FileCopyrightText: 2026 Scenario Inc.
# SPDX-FileCopyrightText: 2026 Open Studio contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Offline portable project rewrite, including manually edited sound source trims."""

import importlib.util
from pathlib import Path


def _rebuild_sound(worker, bpy, scene, strip, path, index):
    """Preserve source trims before restoring timeline placement and animation."""
    if len(strip.modifiers) or len(getattr(strip, "retiming_keys", ())):
        raise ValueError("Sound strip modifiers and retiming are unsupported in packages")
    parent = strip.parent_meta()
    container = parent.strips if parent is not None else scene.sequence_editor.strips
    name, channel = strip.name, strip.channel
    timing = (strip.frame_start, strip.frame_offset_start, strip.frame_offset_end)
    trim = (strip.animation_offset_start, strip.animation_offset_end)
    expected = (strip.frame_final_start, strip.frame_final_end, channel)
    settings = {key: getattr(strip, key) for key in worker.SOUND_SETTINGS if hasattr(strip, key)}
    private = worker._private(strip)
    custom = {key: worker._plain(strip[key]) for key in strip.keys() if key not in private}
    temporary = f"open-studio-project-{index}"
    strip.name = temporary
    mono = strip.sound.use_mono
    replacement = container.new_sound(name, path, channel, int(timing[0]))
    if replacement.name != name or replacement.sound is None:
        raise ValueError("Blender could not rebuild a project sound strip")
    replacement.sound.use_mono = mono
    replacement.animation_offset_start, replacement.animation_offset_end = trim
    replacement.frame_start = timing[0]
    replacement.frame_offset_start, replacement.frame_offset_end = timing[1:]
    for key, value in settings.items():
        setattr(replacement, key, value)
    for key, value in custom.items():
        replacement[key] = value
    old = f'strips_all["{bpy.utils.escape_identifier(temporary)}"]'
    new = f'strips_all["{bpy.utils.escape_identifier(name)}"]'
    for curve in worker._channels(bpy, scene.animation_data):
        if old in curve.data_path:
            curve.data_path = curve.data_path.replace(old, new)
    container.remove(strip)
    replacement.channel = channel
    actual = (replacement.frame_final_start, replacement.frame_final_end, replacement.channel)
    source = (replacement.animation_offset_start, replacement.animation_offset_end)
    if actual != expected or source != trim:
        raise ValueError("Blender did not preserve the project sound strip timing")


def main():
    module_spec = importlib.util.spec_from_file_location(
        "open_studio_project_worker", Path(__file__).with_name("film_case_study_worker.py")
    )
    worker = importlib.util.module_from_spec(module_spec)
    module_spec.loader.exec_module(worker)
    worker._rebuild_sound = lambda *args: _rebuild_sound(worker, *args)
    worker.main()


if __name__ == "__main__":
    main()
