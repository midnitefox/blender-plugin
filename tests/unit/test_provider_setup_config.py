"""Provider selection must remain explicit, portable and safe to inspect."""

import json
from types import SimpleNamespace

import pytest

from scenario.core.providers.preferences import (
    active_provider_preferences,
    profile_from_preferences,
    setup_hints,
    uses_external_assistant,
)


def preferences(*profiles, selected=0, mode="COMFYUI"):
    return SimpleNamespace(
        provider_profiles=list(profiles), active_provider_index=selected, provider_mode=mode
    )


def profile(**values):
    return SimpleNamespace(
        **{
            "profile_id": "profile-1",
            "name": "My local AI",
            "provider": "comfyui",
            "base_url": "http://127.0.0.1:8188",
            "api_key": "",
            "text_model": "",
            "image_model": "",
            "speech_model": "",
            "workflows_path": "",
            "options_json": "{}",
            **values,
        }
    )


def test_first_run_selects_local_comfy_without_scenario_credentials():
    selected = profile_from_preferences(preferences())
    assert selected.provider == "comfyui"
    assert selected.base_url == "http://127.0.0.1:8188"
    assert selected.api_key == ""


def test_external_assistant_uses_no_provider_and_preserves_saved_profiles():
    prefs = preferences(profile(), mode="AGENT")
    assert uses_external_assistant(prefs)
    assert profile_from_preferences(prefs) is None
    assert active_provider_preferences(prefs) is prefs
    assert len(prefs.provider_profiles) == 1
    prefs.provider_mode = "PROFILES"
    assert profile_from_preferences(prefs).provider == "comfyui"


def test_legacy_preferences_and_explicit_scenario_keep_existing_authentication():
    assert profile_from_preferences(SimpleNamespace()) is None
    assert profile_from_preferences(preferences(mode="SCENARIO")) is None
    legacy = SimpleNamespace(api_key="saved", credential_source="PREFERENCES")
    assert active_provider_preferences(legacy) is legacy


def test_named_profiles_preserve_exact_provider_fields_and_independent_identity():
    first = profile()
    second = profile(
        profile_id="cloud-2",
        name="My API",
        provider="openai_compatible",
        base_url="https://example.com/v1",
        api_key="synthetic-token",
        text_model="my-text-model",
        image_model="my-image-model",
        speech_model="my-speech-model",
        workflows_path="/tmp/my-recipes.json",
        options_json='{"voice":"nova"}',
    )
    selected = profile_from_preferences(preferences(first, second, selected=1))
    for field in vars(second):
        if field == "options_json":
            assert json.loads(selected.options_json) == json.loads(second.options_json)
        else:
            assert getattr(selected, field) == getattr(second, field)
    assert "synthetic-token" not in repr(selected)


def test_scenario_profile_uses_its_own_saved_account_fields():
    account = profile(provider="scenario", api_key="account-key", api_secret="account-secret")
    prefs = preferences(profile(), account, selected=1)
    assert profile_from_preferences(prefs) is None
    assert active_provider_preferences(prefs) is account


@pytest.mark.parametrize("selected", [-1, 2, 900])
def test_stale_selection_never_silently_switches_to_a_different_saved_provider(selected):
    with pytest.raises(ValueError, match="Choose an AI profile"):
        profile_from_preferences(preferences(profile(), selected=selected))


def test_guidance_does_not_promise_generation_from_a_successful_connection():
    hints = " ".join(setup_hints(profile_from_preferences(preferences())))
    assert "workflow" in hints.lower()
    assert "models" in hints.lower()
    assert "connection" in hints.lower()
    assert "all features" not in hints.lower()


def test_missing_preferences_are_not_silently_a_working_local_account():
    assert profile_from_preferences(None) is None
