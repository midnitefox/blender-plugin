# SPDX-FileCopyrightText: 2026 Open Studio contributors
# SPDX-License-Identifier: GPL-3.0-or-later
"""Lazy adapter selection; provider setup never changes installed dependencies."""


def make_adapter(
    profile,
    *,
    online,
    base_url=None,
    account_id=None,
    project_id=None,
    team_id=None,
    state_dir=None,
    transport=None,
):
    options = dict(
        online=online,
        base_url=base_url,
        account_id=account_id,
        project_id=project_id,
        team_id=team_id,
        state_dir=state_dir,
        transport=transport,
    )
    if profile.provider == "comfyui":
        from .comfy import ComfyUIAdapter

        return ComfyUIAdapter(profile, **options)
    if profile.provider == "openai_compatible":
        from .openai_compatible import OpenAICompatibleAdapter

        return OpenAICompatibleAdapter(profile, **options)
    if profile.provider == "fal":
        from .fal import FalAdapter

        return FalAdapter(profile, **options)
    raise ValueError("Scenario connections use the existing official SDK adapter")
