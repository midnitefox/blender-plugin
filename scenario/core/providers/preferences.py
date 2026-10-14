"""Read provider configuration without importing Blender or touching its state."""

from .config import ProviderProfile

LOCAL_URL = "http://127.0.0.1:8188"


def uses_external_assistant(prefs):
    return getattr(prefs, "provider_mode", "SCENARIO") == "AGENT"


def active_provider_preferences(prefs):
    """Select one saved profile, preserving legacy Scenario preference objects.

    A stale index fails closed: selecting another provider implicitly could send
    prompts to another service. Drawing and conversion never create profiles.
    """
    if uses_external_assistant(prefs):
        return prefs
    rows = getattr(prefs, "provider_profiles", ())
    if rows:
        index = getattr(prefs, "active_provider_index", -1)
        if not 0 <= index < len(rows):
            raise ValueError("Choose an AI profile in Choose AI")
        return rows[index]
    return prefs


def profile_from_preferences(prefs):
    """Return immutable external-provider settings, or None for Scenario."""
    selected = active_provider_preferences(prefs)
    if selected is prefs:
        if getattr(prefs, "provider_mode", "SCENARIO") in {"SCENARIO", "AGENT"}:
            return None
        return ProviderProfile("local-comfyui", "Local ComfyUI", "comfyui", LOCAL_URL)
    if selected.provider == "scenario":
        return None
    fields = (
        "profile_id",
        "name",
        "provider",
        "base_url",
        "api_key",
        "text_model",
        "image_model",
        "speech_model",
        "workflows_path",
        "options_json",
    )
    return ProviderProfile(**{field: getattr(selected, field) for field in fields})


def setup_hints(profile):
    """Actionable setup information without implying modality verification."""
    if profile is None:
        return (
            "Use your Scenario API key and secret. Generation uses Scenario credits.",
            "A connection check verifies access; generation depends on the chosen model.",
        )
    if profile.provider == "comfyui":
        return (
            "Start ComfyUI, then test this connection. Local ComfyUI needs no Scenario account.",
            "Install a checkpoint in ComfyUI for the basic image workflow.",
            "Other models and media types need a compatible workflow recipe JSON.",
        )
    if profile.provider == "fal":
        return (
            "Enter your fal key, then load the included model choices.",
            "Setup checks the configured models; it does not verify your fal key or generate media.",
            "fal bills approved generations. Advanced options can supply custom model recipes.",
        )
    return (
        "Enter the API base URL and token from your chosen provider.",
        "Model IDs must match that provider's text, image or speech API.",
        "A connection check does not verify every model or media type. Cloud usage may be billed.",
    )
