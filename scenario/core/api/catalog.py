# SPDX-FileCopyrightText: 2026 Scenario Inc.
# SPDX-License-Identifier: GPL-3.0-or-later
"""Model records, schema hints, lane filtering and curated ordering.

Service reads and connection-local caches belong to SDKCatalog.
"""

from dataclasses import dataclass, field

from ..scene.panorama_contract import PANORAMA_MODELS

GENERATION_LANES = (
    "image",
    "video",
    "3d",
    "material",
    "audio",
    "render_image",
    "render_video",
    "edit3d",
    "panorama",
)
LANE_CAPS = {
    "image": {"txt2img", "img2img", "video2img"},
    "video": {"txt2video", "img2video", "video2video", "audio2video"},
    "3d": {"txt23d", "img23d"},
    "audio": {"txt2audio", "audio2audio", "video2audio"},
    "render_image": {"img2img"},  # a viewport capture plus style images to a finished still
    "render_video": {"video2video"},  # a playblast plus images to a finished clip
    "edit3d": {
        "3d23d"
    },  # a mesh from the scene to a retextured / remeshed / rigged / animated mesh
}
PATINA_MODELS = ("model_patina-material", "model_patina", "model_patina-material-extract")
DEFAULT_MODELS = {
    "image": [
        "model_openai-gpt-image-2",
        "model_google-gemini-3-1-flash",
        "model_bytedance-seedream-5-0-pro",
        "model_bytedance-seedream-5-0-lite",
        "model_z-image",
    ],
    "video": [
        "model_bytedance-seedance-2-0",
        "model_bytedance-seedance-2-5",
        "model_veo3-1",
        "model_kling-v3-omni-video",
    ],
    "3d": [
        "model_meshy-7-txt23d",
        "model_rodin-hyper3d-v2-5-text-to-3d",
        "model_tripo-v3-1-image-to-3d",
        "model_meshy-7-img23d",
        "model_hunyuan-3d-pro-3-1-i23d",
        "model_rodin-hyper3d-v2-5",
        "model_tripo-p1-image-to-3d",
        "model_meshy-7-multi-image-to-3d",
        "model_tripo-v3-1-multiview-to-3d",
        "model_hunyuan-3d-pro-3-1-multiview",
    ],
    "material": list(PATINA_MODELS),
    "audio": [
        "model_elevenlabs-music-v2",
        "model_lyria-3-pro",
        "model_ace-step-1-5-quality-text-to-music",
        "model_minimax-music-3-0",
        "model_elevenlabs-tts-v3",
        "model_google-gemini-3-1-flash-tts",
        "model_elevenlabs-sound-effects-v2",
        "model_sonilo-v1-1-text-to-sound-effects",
        "model_sonilo-v1-1-video-to-sound-effects",
    ],
    # The image edit models take a reference image and a look prompt; GPT Image 2 comes first (product decision).
    "render_image": [
        "model_openai-gpt-image-2",
        "model_google-gemini-3-1-flash",
        "model_bytedance-seedream-5-0-pro",
        "model_bfl-flux-2-max-editing",
        "model_bfl-flux-2-pro-editing",
        "model_qwen-image-edit-2511",
        "model_microsoft-mai-image-2-5-pro-edit",
        "model_xai-grok-imagine-image-2-0",
        "model_z-image",
    ],
    # the ten video models that accept a reference video (and images) to re-render
    "render_video": [
        "model_bytedance-seedance-2-0",
        "model_minimax-h3",
        "model_bytedance-seedance-2-5",
        "model_bytedance-seedance-2-0-mini",
        "model_runway-aleph-2",
        "model_alibaba-happy-horse-video-editing",
        "model_google-omni-1-1-flash-edit",
        "model_google-omni-flash-edit",
        "model_xai-grok-edit-video",
        "model_bfl-flux-3-extend",
    ],
    "edit3d": [
        "model_meshy-7-retexture",
        "model_tripo-retopology",
        "model_meshy-rigging",
        "model_meshy-animation",
        "model_meshy-uv-unwrap",
        "model_tripo-segmentation-v2",
    ],
    # The Environment lane lists only models with a verified panorama contract.
    # They also keep their capability lanes (Image, Render Image) unchanged.
    "panorama": list(PANORAMA_MODELS),
}
RENDER_LANES = ("render_image", "render_video")
# The kind of asset each lane produces (image / video / 3d / material / audio). One source of truth.
LANE_KIND = {
    "image": "image",
    "video": "video",
    "3d": "3d",
    "material": "material",
    "audio": "audio",
    "render_image": "image",
    "render_video": "video",
    "edit3d": "3d",
    "panorama": "image",
}


def lane_kind(lane):
    return LANE_KIND.get(lane, "image")


# Most specific lane first, so Patina reads as material and a mesh edit as 3d.
_KIND_LANES = (
    "material",
    "3d",
    "edit3d",
    "audio",
    "video",
    "render_video",
    "image",
    "render_image",
)


def model_kind(record):
    """The asset kind a catalog model produces, or None when no lane claims it.

    Unlike the displayed lane lists, this ignores deprecation, training and mode
    filters: they hide a model from a picker, not the kind of its results.
    """
    lanes = record.lanes
    return next((LANE_KIND[lane] for lane in _KIND_LANES if lane in lanes), None)


# Video models that address their inputs as @video1 / @image1 in the prompt (Seedance family). The others take plain words.
TAGGED_VIDEO_MODELS = ("seedance",)

# What a mesh from the scene can become, named like Scenario's 3D categories. Each task lists the models that do it,
# best first; a model can serve two tasks (Rodin Bang splits into parts and retextures); "ALL" shows every 3d23d model.
EDIT3D_TASKS = (
    (
        "REMESH",
        "Remesh",
        "Clean quad or triangle topology at a target polycount",
        ("model_tripo-retopology", "model_meshy-remesh", "model_tencent-smarttopology"),
    ),
    (
        "RETEXTURE",
        "Retexture",
        "New textures and materials for the mesh, from a prompt or a style image",
        (
            "model_meshy-7-retexture",
            "model_tripo-v3-0-texturing",
            "model_trellis-2-retexture",
            "model_tencent-texture-edit",
            "model_rodin-hyper3d-bang",
            "model_tripo-stylization",
            "model_hitem-3d-multicolor",
            "model_meshy-retexture",
        ),
    ),
    ("UV", "UV Unwrap", "New UV layout", ("model_meshy-uv-unwrap", "model_tencent-uv-unwrapping")),
    (
        "RIG",
        "Rigging",
        "A skeleton and skin weights for a character",
        (
            "model_meshy-rigging",
            "model_tripo-rigging-v2-5",
            "model_tripo-rigging-v1",
            "model_cartwheel-character-rigging",
        ),
    ),
    (
        "ANIMATE",
        "Animate",
        "An animated version of a rigged character",
        ("model_meshy-animation", "model_cartwheel-text-to-motion"),
    ),
    (
        "PARTS",
        "Parts",
        "Split the mesh into semantic parts",
        (
            "model_tripo-segmentation-v2",
            "model_tripo-segmentation-v1",
            "model_hunyuan-3d-part",
            "model_hitem-3d-split",
            "model_rodin-hyper3d-bang",
        ),
    ),
    ("ALL", "All", "Every model that takes a mesh as input", ()),
)
# Names that mark utilities rather than renderers; kept out of the Render Image list (they still show in Image).
UTILITY_HINTS = (
    "remove background",
    "background remov",
    "upscal",
    "vectoriz",
    "reframe",
    "pixelate",
    "blur",
    "color lut",
    "layerize",
    "image sequence",
)


# Schema defaults that produce surprising results in Blender. Applied when the value is among the allowed ones.
# Rodin's "All" material returns two meshes (a baked Shaded GLB and a PBR GLB); one PBR mesh is what a Blender user expects.
PARAM_OVERRIDES = {
    "model_rodin-hyper3d-v2-5-text-to-3d": {"material": "PBR"},
    "model_rodin-hyper3d-v2-5-text-to-3d-fast": {"material": "PBR"},
    "model_rodin-hyper3d-v2-5": {"material": "PBR"},
    "model_rodin-hyper3d-v2-5-fast": {"material": "PBR"},
}
MULTIVIEW_HINTS = ("multi", "multiview")


def param_override(model_id, name):
    return (PARAM_OVERRIDES.get(model_id) or {}).get(name)


@dataclass
class ModelRecord:
    id: str
    name: str
    short_description: str = ""
    capabilities: tuple = ()
    tags: tuple = ()
    type: str = ""
    status: str = ""
    privacy: str = ""
    raw: dict = field(default_factory=dict)

    @classmethod
    def from_api(cls, data):
        return cls(
            id=data.get("id", ""),
            name=data.get("name") or data.get("id", ""),
            short_description=data.get("shortDescription") or "",
            capabilities=tuple(
                c if isinstance(c, str) else c.get("type", "")
                for c in data.get("capabilities") or ()
            ),
            tags=tuple(data.get("tags") or ()),
            type=data.get("type") or "",
            status=data.get("status") or "",
            privacy=data.get("privacy") or "",
            raw=dict(data),
        )

    @property
    def parameters(self):
        """The parameter schema. REST records carry it under `inputs` (`parameters` is often an empty dict)."""
        params = self.raw.get("parameters")
        if isinstance(params, list) and params:
            return list(params)
        return list(self.raw.get("inputs") or [])

    @property
    def ui_config(self):
        return dict(self.raw.get("uiConfig") or {})

    @property
    def deprecated_successor(self):
        for tag in self.tags:
            if tag.startswith("deprecated:"):
                return tag.split(":", 1)[1] or None
        return None

    @property
    def lanes(self):
        caps = set(self.capabilities)
        lanes = {lane for lane, wanted in LANE_CAPS.items() if caps & wanted}
        if self.id in PATINA_MODELS:
            lanes.add("material")
        if self.id in PANORAMA_MODELS:
            lanes.add("panorama")
        return lanes


def is_utility(record):
    text = (record.name + " " + record.short_description[:60]).lower()
    return "tool" in record.tags or any(hint in text for hint in UTILITY_HINTS)


# Kinds of model records outside the shared base catalog, classified from REST fields only.
TRAINED_LORA = "lora"
TRAINED_COMPOSITION = "composition"
PRIVATE_CUSTOM = "custom_private"
TRAINED_UNSUPPORTED = "unsupported"
TRAINED_KINDS = (TRAINED_LORA, TRAINED_COMPOSITION, PRIVATE_CUSTOM, TRAINED_UNSUPPORTED)
# Kinds a later route may run through a compatible base model's REST schema (#97).
USABLE_TRAINED_KINDS = (TRAINED_LORA, TRAINED_COMPOSITION)
# Kinds the base lanes and picker leave out, as the former blanket LoRA exclusion did. A private
# custom model is an ordinary runnable model and keeps its former lane and picker behavior.
EXCLUDED_KINDS = (TRAINED_LORA, TRAINED_COMPOSITION, TRAINED_UNSUPPORTED)


def trained_kind(record, *, private_list=False):
    """Classify a model record by its REST `type`, training lineage and privacy.

    A plain record is a `custom` type (or one without a type, in older
    synthetic records) with no parent, training images or concepts. Returns
    None for a plain record that is not private. Otherwise returns one of
    TRAINED_KINDS:

    - lora: a `*-lora` type (Flux, Flux 2, Kontext, Krea, Qwen and Z-Image LoRAs);
    - composition: a `*-composition` type, which combines LoRA concepts;
    - custom_private: a plain record that is private. Either its `privacy`
      field says so or, with `private_list`, the selected scope's private model
      list returned it, whatever its privacy field says;
    - unsupported: any other type (for example a voice clone or a hosted base
      type), a type that is not a string, or a `custom` record trained on a
      parent model.

    A kind is not a route. Running a LoRA or composition needs a base model
    whose own REST schema declares a compatible model input; no route is
    inferred from names or remote-MCP metadata.
    """
    raw = record.raw
    kind = record.type or "custom"
    if not isinstance(kind, str):
        return TRAINED_UNSUPPORTED
    if kind.endswith("-composition"):
        return TRAINED_COMPOSITION
    if kind.endswith("-lora"):
        return TRAINED_LORA
    if kind != "custom" or any(
        raw.get(name) for name in ("parentModelId", "trainingImagesNumber", "concepts")
    ):
        return TRAINED_UNSUPPORTED
    if private_list or record.privacy == "private":
        return PRIVATE_CUSTOM
    return None


def is_trained(record):
    """True for records the base lanes and picker do not list.

    This covers LoRAs, compositions and other trained or unsupported types,
    which need a verified route before they can run. A private custom model
    is an ordinary runnable model and stays listed, as before.

    This is a lane filter, not a trained-model test: it is also true for
    hosted base types such as `flux.1-pro` and false for custom_private.
    Routing selects trained models with trained_kind and USABLE_TRAINED_KINDS.
    """
    return trained_kind(record) in EXCLUDED_KINDS


def trained_models(private_records, public_records):
    """(kind, record) pairs to offer later, in listing order and unique by ID.

    Every record of the selected scope's private trained list comes first, so
    its kind can explain an unavailable model; a plain record from that list
    is custom_private (trained_kind with `private_list`). Public records add
    only LoRAs and compositions; public base models stay in the lane lists.
    """
    result, seen = [], set()
    for record in private_records:
        if record.id not in seen:
            seen.add(record.id)
            result.append((trained_kind(record, private_list=True), record))
    for record in public_records:
        kind = trained_kind(record)
        if record.id not in seen and kind in USABLE_TRAINED_KINDS:
            seen.add(record.id)
            result.append((kind, record))
    return result


def models_for_lane(lane, records):
    """Filter records usable in `lane`, trained and deprecated models removed, curated first."""
    curated = DEFAULT_MODELS.get(lane, [])
    rank = {model_id: index for index, model_id in enumerate(curated)}
    usable = [
        r
        for r in records
        if lane in r.lanes
        and r.deprecated_successor is None
        and r.status in ("", "trained")
        and not is_trained(r)
    ]
    if lane == "material":
        usable = [r for r in usable if r.id in PATINA_MODELS]
    elif lane == "panorama":
        usable = [r for r in usable if r.id in PANORAMA_MODELS]
    elif lane == "render_image":
        usable = [r for r in usable if r.id not in PATINA_MODELS and not is_utility(r)]
    return sorted(usable, key=lambda r: (rank.get(r.id, len(rank)), r.name.lower()))


def edit3d_task(task_id):
    return next((t for t in EDIT3D_TASKS if t[0] == task_id), EDIT3D_TASKS[-1])


def edit3d_models(task_id, records):
    """Mesh-to-mesh models for one task, best first; ALL lists every 3d23d model with the curated ones first."""
    usable = models_for_lane("edit3d", records)
    if task_id == "ALL":
        return usable
    wanted = list(edit3d_task(task_id)[3])
    by_id = {r.id: r for r in usable}
    return [by_id[m] for m in wanted if m in by_id]


def tagged_video_model(model_id):
    return any(hint in (model_id or "").lower() for hint in TAGGED_VIDEO_MODELS)


def mesh_param(record):
    """Name of the parameter that takes the input mesh (kind 3d), or None."""
    for raw in record.parameters:
        if raw.get("type") in ("file", "file_array") and (raw.get("kind") or "").lower() == "3d":
            return raw.get("name")
    return None
