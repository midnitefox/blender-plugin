"""Native AI setup with reusable, explicitly selected provider profiles."""

import textwrap
import uuid
from types import SimpleNamespace

import bpy
from bpy.props import BoolProperty, EnumProperty, StringProperty

from ..core.api.errors import ScenarioError
from ..core.providers.preferences import (
    LOCAL_URL,
    active_provider_preferences,
    profile_from_preferences,
    setup_hints,
    uses_external_assistant,
)

PROVIDERS = (
    ("comfyui", "ComfyUI", "Run installed models locally or use your own ComfyUI server"),
    ("openai_compatible", "OpenAI-compatible API", "Use a compatible local or hosted AI API"),
    ("fal", "fal", "Use selected fal models for image, video, audio and 3D tasks"),
    ("scenario", "Scenario", "Use the optional Scenario service with your own credentials"),
)
PROFILE_FIELDS = (
    "profile_id",
    "name",
    "provider",
    "base_url",
    "api_key",
    "api_secret",
    "credential_source",
    "project_id",
    "text_model",
    "image_model",
    "speech_model",
    "workflows_path",
    "options_json",
    "show_advanced",
)


def provider_changed(self, context):
    from . import runtime

    runtime.state.account_label = ""
    runtime.state.connection_status = ""
    runtime.sync_catalog_context()


class SCENARIO_PG_ai_profile(bpy.types.PropertyGroup):
    profile_id: StringProperty(options={"HIDDEN"})
    name: StringProperty(name="Profile name", default="My AI", update=provider_changed)
    provider: EnumProperty(name="Provider", items=PROVIDERS, update=provider_changed)
    base_url: StringProperty(
        name="API base URL",
        description="HTTPS, or HTTP on this computer's loopback address",
        update=provider_changed,
    )
    api_key: StringProperty(name="API token", subtype="PASSWORD", update=provider_changed)
    api_secret: StringProperty(name="API secret", subtype="PASSWORD", update=provider_changed)
    credential_source: EnumProperty(
        name="Credentials",
        items=(
            ("PREFERENCES", "Saved in Blender", "Use this profile's saved credentials"),
            ("ENVIRONMENT", "Environment", "Use SCENARIO_API_KEY and SCENARIO_API_SECRET"),
        ),
        default="PREFERENCES",
        update=provider_changed,
    )
    project_id: StringProperty(name="Project ID (optional)", update=provider_changed)
    text_model: StringProperty(name="Text model ID", update=provider_changed)
    image_model: StringProperty(name="Image model ID", update=provider_changed)
    speech_model: StringProperty(name="Speech model ID", update=provider_changed)
    workflows_path: StringProperty(
        name="Workflow recipes (optional)",
        subtype="FILE_PATH",
        description="Provider recipe JSON with explicit workflow inputs and outputs",
        update=provider_changed,
    )
    options_json: StringProperty(
        name="Provider options (JSON)", default="{}", update=provider_changed
    )
    show_advanced: BoolProperty(name="Advanced options", default=False)


def add_profile(prefs, provider):
    """Create identity once; credential and model fields stay separate per row."""
    row = prefs.provider_profiles.add()
    row.profile_id = uuid.uuid4().hex
    row.provider = provider
    row.name = {
        "comfyui": "Local ComfyUI",
        "openai_compatible": "My API",
        "scenario": "My Scenario account",
        "fal": "My fal account",
    }[provider]
    row.base_url = {"comfyui": LOCAL_URL, "fal": "https://queue.fal.run"}.get(provider, "")
    prefs.active_provider_index = len(prefs.provider_profiles) - 1
    prefs.provider_mode = "PROFILES"
    return row


def _wrapped(layout, message, icon="INFO"):
    for index, line in enumerate(textwrap.wrap(message, 70)):
        layout.label(text=line, icon=icon if index == 0 else "NONE")


def _draw_scenario(layout, selected):
    layout.prop(selected, "credential_source")
    if selected.credential_source == "PREFERENCES":
        layout.prop(selected, "api_key", text="API key")
        layout.prop(selected, "api_secret")
        _wrapped(layout, "Saved as plain text in userpref.blend")
    else:
        _wrapped(layout, "SCENARIO_API_KEY + SCENARIO_API_SECRET from Blender's launch environment")
    layout.prop(selected, "project_id")
    _wrapped(layout, "Blank uses the API key's default scope")
    layout.operator(
        "scenario.open_url", text="Create a Scenario key", icon="URL"
    ).url = "https://app.scenario.com/team"


def draw_provider_setup(layout, context, prefs):
    """Read preferences only. Creating/editing profiles belongs to operators."""
    from . import runtime

    box = layout.box()
    box.label(text="Choose your AI", icon="PREFERENCES")
    if hasattr(prefs, "provider_mode"):
        mode = box.row(align=True)
        mode.prop_enum(prefs, "provider_mode", "AGENT", text="Codex / external assistant")
        mode.prop_enum(prefs, "provider_mode", "PROFILES", text="AI provider")
    if uses_external_assistant(prefs):
        from . import mcp_service

        _wrapped(
            box,
            "Use Codex ImageGen with your existing Codex access. No provider API key is needed here.",
        )
        _wrapped(
            box,
            "Copy the setup below into Codex and ask it to connect. Keep Blender open while you work.",
        )
        row = box.row(align=True)
        if mcp_service.status()["running"]:
            row.label(text="Assistant connection running", icon="CHECKMARK")
        else:
            row.operator("scenario.mcp_start", text="Start assistant connection", icon="PLAY")
        row.operator("scenario.mcp_copy", text="Copy Codex setup", icon="COPYDOWN").kind = "codex"
        if not runtime.online():
            _wrapped(
                box,
                "Enable Allow Online Access in Blender's System preferences for the local assistant connection.",
                "INFO",
            )
        _wrapped(
            box,
            "Your assistant creates the media using its available tools. Blender handles scene setup, cameras and importing the result.",
        )
        _wrapped(box, "Choose AI provider for optional ComfyUI workflows or a compatible API.")
        box.operator("wm.save_userpref", text="Save preferences", icon="FILE_TICK")
        return
    rows = getattr(prefs, "provider_profiles", ())
    if rows:
        box.template_list(
            "SCENARIO_UL_ai_profiles",
            "",
            prefs,
            "provider_profiles",
            prefs,
            "active_provider_index",
            rows=3,
        )
    if hasattr(prefs, "provider_profiles"):
        row = box.row(align=True)
        for provider, label, _description in PROVIDERS:
            row.operator("scenario.add_ai_profile", text=label, icon="ADD").provider = provider
        if rows:
            box.operator(
                "scenario.remove_ai_profile", text="Remove selected profile", icon="REMOVE"
            )
    try:
        selected = active_provider_preferences(prefs)
    except (ValueError, ScenarioError):
        _wrapped(box, "Choose an AI profile above, or add one to continue", "ERROR")
        return
    error = ""
    try:
        profile = profile_from_preferences(prefs)
    except (ValueError, ScenarioError) as err:
        profile = SimpleNamespace(provider=selected.provider)
        error = str(err)
    if selected is not prefs:
        box.prop(selected, "name")
    if profile is None:
        _draw_scenario(box, selected)
    elif selected is prefs:
        _wrapped(box, "Default: local ComfyUI at http://127.0.0.1:8188")
        _wrapped(box, "Add ComfyUI above to customize this connection or save a named profile")
    else:
        box.prop(selected, "base_url")
        token_label = "fal API key" if profile.provider == "fal" else "API token (optional)"
        box.prop(selected, "api_key", text=token_label)
        if profile.provider == "openai_compatible":
            box.prop(selected, "text_model")
            box.prop(selected, "image_model")
            box.prop(selected, "speech_model")
        elif profile.provider == "comfyui":
            box.prop(selected, "workflows_path")
        box.prop(selected, "show_advanced")
        if selected.show_advanced:
            box.prop(selected, "options_json")
        _wrapped(box, "Saved tokens remain plain text in Blender preferences")
    for hint in setup_hints(profile):
        _wrapped(box, hint)
    if error:
        _wrapped(box, error, "ERROR")
    row = box.row(align=True)
    check_label = "Check setup" if profile and profile.provider == "fal" else "Test connection"
    row.operator("scenario.test_ai_connection", text=check_label, icon="CHECKMARK")
    row.operator("scenario.refresh_catalog", text="Load models", icon="FILE_REFRESH")
    if runtime.state.account_label:
        icon = "ERROR" if runtime.state.connection_status == "error" else "INFO"
        _wrapped(box, runtime.state.account_label, icon)
    if not runtime.online():
        _wrapped(box, "Allow Online Access is off in Blender's System preferences", "ERROR")
    if hasattr(prefs, "provider_profiles"):
        box.operator("wm.save_userpref", text="Save preferences", icon="FILE_TICK")


class SCENARIO_UL_ai_profiles(bpy.types.UIList):
    def draw_item(self, context, layout, data, item, icon, active_data, active_propname):
        layout.label(text=item.name or "Unnamed AI", icon="NETWORK_DRIVE")
        label = next(
            label for provider, label, _description in PROVIDERS if provider == item.provider
        )
        layout.label(text=label)


class SCENARIO_OT_add_ai_profile(bpy.types.Operator):
    bl_idname = "scenario.add_ai_profile"
    bl_label = "Add AI profile"
    bl_description = "Save a separate provider connection and select it"
    provider: EnumProperty(items=PROVIDERS)

    def execute(self, context):
        from . import runtime

        prefs = runtime.prefs()
        if prefs is None:
            return {"CANCELLED"}
        add_profile(prefs, self.provider)
        return {"FINISHED"}


class SCENARIO_OT_remove_ai_profile(bpy.types.Operator):
    bl_idname = "scenario.remove_ai_profile"
    bl_label = "Remove AI profile"
    bl_description = "Remove the selected saved connection; generated files remain available"

    def invoke(self, context, event):
        return context.window_manager.invoke_confirm(self, event)

    def execute(self, context):
        from . import runtime

        prefs = runtime.prefs()
        index = prefs.active_provider_index
        if not 0 <= index < len(prefs.provider_profiles):
            return {"CANCELLED"}
        prefs.provider_profiles.remove(index)
        prefs.active_provider_index = max(0, min(index, len(prefs.provider_profiles) - 1))
        if not prefs.provider_profiles:
            prefs.provider_mode = "AGENT"
        provider_changed(self, context)
        return {"FINISHED"}


class SCENARIO_OT_choose_ai(bpy.types.Operator):
    bl_idname = "scenario.choose_ai"
    bl_label = "Choose AI"
    bl_description = "Configure local AI or your preferred API and test the selected connection"

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self, width=660)

    def draw(self, context):
        from . import runtime

        draw_provider_setup(self.layout, context, runtime.prefs())

    def execute(self, context):
        return {"FINISHED"}


class SCENARIO_OT_test_ai_connection(bpy.types.Operator):
    bl_idname = "scenario.test_ai_connection"
    bl_label = "Test AI connection"
    bl_description = "Check the selected provider without submitting generation or spending credits"

    def execute(self, context):
        from . import generation, runtime

        try:
            worker = runtime.request_connection_check()
            if bpy.app.background and worker is not None:
                worker.join(15)
                generation.process_catalog_events()
        except (ScenarioError, ValueError) as err:
            reason = err.reason if isinstance(err, ScenarioError) else "Choose a valid AI profile"
            runtime.state.account_label = reason
            runtime.state.connection_status = "error"
            self.report({"ERROR"}, reason)
            return {"CANCELLED"}
        self.report({"INFO"}, runtime.state.account_label or "Connection check requested")
        return {"FINISHED"}


CLASSES = (
    SCENARIO_UL_ai_profiles,
    SCENARIO_OT_add_ai_profile,
    SCENARIO_OT_remove_ai_profile,
    SCENARIO_OT_choose_ai,
    SCENARIO_OT_test_ai_connection,
)


def register():
    for cls in CLASSES:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)
