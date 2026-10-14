# SPDX-FileCopyrightText: 2026 Scenario Inc.
# SPDX-License-Identifier: GPL-3.0-or-later
"""Turn a model record's parameter schema into UI specs and request bodies."""

import math
import re
import struct
from dataclasses import dataclass, field

FILE_TYPES = ("file", "file_array")


def _float32(value):
    return struct.unpack("<f", struct.pack("<f", value))[0]


def float32_decimal(value):
    """The shortest decimal that 32-bit float storage reads back as `value`.

    Blender's FloatProperty holds 0.8 as 0.800000011920929. A request carries the
    0.8 the form shows and the schema declares instead, so a form value and the
    same value passed through local MCP serialize alike. Whole numbers are exact
    already, and a value that is not a 32-bit reading, or not finite, is
    returned unchanged.
    """
    value = float(value)
    if not math.isfinite(value) or value.is_integer():
        return value
    try:
        stored = _float32(value)
    except OverflowError:
        return value
    if stored != value:
        return value
    for digits in range(1, 10):  # nine significant digits always read back exactly
        candidate = float(f"{value:.{digits}g}")
        if _float32(candidate) == stored:
            return candidate
    return value


@dataclass
class ParamSpec:
    name: str
    label: str
    ptype: str  # string | number | boolean | file | file_array | string_array
    default: object = None
    description: str = ""
    group: str = "Settings"
    required_always: bool = False
    required_if_defined: tuple = ()
    required_if_not_defined: tuple = ()  # required only when none of these siblings are provided (either/or inputs)
    allowed_values: tuple = ()
    allowed_labels: dict = field(default_factory=dict)
    min: float = None
    max: float = None
    step: float = None
    max_length: int = None
    cost_impact: bool = False
    kind: str = None  # image | video | audio | 3d for files
    is_prompt: bool = False
    is_array: bool = False

    @property
    def is_file(self):
        return self.ptype in FILE_TYPES

    @property
    def is_integer(self):
        if self.ptype != "number":
            return False
        candidates = [
            v for v in (self.step, self.default, self.min, self.max) if isinstance(v, (int, float))
        ]
        if self.allowed_values:
            candidates.extend(v for v in self.allowed_values if isinstance(v, (int, float)))
        return bool(candidates) and all(float(v).is_integer() for v in candidates)

    def label_for(self, value):
        return self.allowed_labels.get(value, str(value))


@dataclass
class Schema:
    specs: list
    resolution_presets: list = field(default_factory=list)
    prompt_name: str = None
    one_of: list = field(
        default_factory=list
    )  # groups where at least one member must be provided (either image or prompt)
    exclusive: list = field(
        default_factory=list
    )  # sorted name pairs that must not both be provided (first frame and reference video)

    def by_name(self, name):
        return next((s for s in self.specs if s.name == name), None)

    def excludes(self, first, second):
        """Whether the model says these two inputs cannot be sent together."""
        return tuple(sorted((first, second))) in self.exclusive


# Some models mark a file input `required: true` when it is really required only in the absence of an alternative
# (Rodin Bang: "Provide a reference image ... if no prompt is provided"). Faithfully requiring it blocks the valid
# prompt-only path. We relax such a file to optional and instead require at least one of the either/or group.
CONDITIONAL_MARKERS = ("if no ", "if not ", "when no ", "when not ", "unless ", "if none")


def _is_conditional(description):
    text = (description or "").lower()
    return any(marker in text for marker in CONDITIONAL_MARKERS)


def _names_input(name):
    return isinstance(name, str) and bool(name.strip())


# No schema field marks inputs that cannot be sent together, but some file inputs
# say so in their description: Seedance 2.x "Mutually exclusive with reference
# images/videos.", Minimax H3 and Wan 3.0 "Can't be combined with reference images,
# videos, or audio." The service quotes such a body and accepts the job, which then
# fails. This narrow rule pairs file inputs only and reads file-input descriptions
# only. After a marker, the clause names a sibling input when every word of its label
# (its server name when unlabelled) appears in it: "a first or last frame" names First
# Frame and Last Frame. A clause that names no file input but mentions references
# ("reference inputs") names the file inputs whose server names start with
# "reference". Either side is enough. A setting on either side is not guarded (Kling
# V3 Omni Generate Audio: "Mutually exclusive with reference video."), and wording
# that names no sibling maps to nothing; tools/audit_payloads.py reports both.
EXCLUSIVE_MARKERS = (
    "mutually exclusive with",
    "can't be combined with",
    "cannot be combined with",
    "can not be combined with",
)


def exclusive_clauses(description):
    """The text after each exclusivity marker, up to the end of its sentence."""
    text = (description or "").replace("\u2019", "'").lower()
    clauses = []
    for marker in EXCLUSIVE_MARKERS:
        for match in re.finditer(re.escape(marker), text):
            clauses.append(re.split(r"[.;]", text[match.end() :], maxsplit=1)[0])
    return clauses


def _words(text):
    """Lowercase words of a label or sentence; a plural s is dropped."""
    spaced = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", text or "").lower()
    return {
        word[:-1] if len(word) > 3 and word.endswith("s") else word
        for word in re.findall(r"[a-z0-9]+", spaced)
    }


def clause_inputs(spec, specs, clause):
    """The sibling inputs, files and settings alike, that one exclusivity clause names."""
    words = _words(clause)
    others = [other for other in specs if other is not spec]
    named = [other for other in others if set() < _words(other.label) <= words]
    if not any(other.is_file for other in named) and "reference" in words:
        named += [
            other
            for other in others
            if other.is_file and other.name.lower().startswith("reference")
        ]
    return named


def _exclusive_pairs(specs):
    pairs = set()
    for spec in specs:
        if not spec.is_file:
            continue
        for clause in exclusive_clauses(spec.description):
            pairs.update(
                tuple(sorted((spec.name, other.name)))
                for other in clause_inputs(spec, specs, clause)
                if other.is_file
            )
    return sorted(pairs)


def _parse_required(raw):
    if isinstance(raw, bool):
        return raw, (), ()
    if isinstance(raw, dict):
        always = bool(raw.get("always"))
        if_defined = tuple(sorted((raw.get("ifDefined") or {}).keys()))
        # ifNotDefined names siblings whose absence makes this input required: an either/or group (Cartwheel: a 3D
        # character mesh OR a reference image). The input is not required on its own; at least one of the group is.
        if_not_defined = tuple(sorted((raw.get("ifNotDefined") or {}).keys()))
        return always, if_defined, if_not_defined
    return False, (), ()


def parse_schema(record):
    ui = record.ui_config
    selects = ui.get("selects") or {}
    specs = []
    for raw in record.parameters:
        name = raw.get("name")
        if not name:
            continue
        ptype = raw.get("type") or "string"
        always, if_defined, if_not_defined = _parse_required(raw.get("required"))
        allowed = tuple(raw.get("allowedValues") or raw.get("allowed_values") or ())
        labels = {}
        for key, label in (selects.get(name) or {}).items():
            match = next((v for v in allowed if str(v) == str(key)), key)
            labels[match] = label
        specs.append(
            ParamSpec(
                name=name,
                label=raw.get("label") or name,
                ptype=ptype,
                default=raw.get("default"),
                description=raw.get("description") or "",
                group=raw.get("group") or ("Prompt" if raw.get("prompt") else "Settings"),
                required_always=always,
                required_if_defined=if_defined,
                required_if_not_defined=if_not_defined,
                allowed_values=allowed,
                allowed_labels=labels,
                min=raw.get("min"),
                max=raw.get("max"),
                step=raw.get("step"),
                max_length=raw.get("maxLength") or raw.get("max_length"),
                cost_impact=bool(raw.get("costImpact") or raw.get("cost_impact")),
                kind=raw.get("kind"),
                is_prompt=bool(raw.get("prompt")),
                is_array=ptype in ("file_array", "string_array") or bool(raw.get("array")),
            )
        )
    presets = []
    res = ui.get("resolutionComponent") or {}
    for preset in res.get("presets") or []:
        presets.append(
            {
                "label": preset.get("label"),
                "width": preset.get("width"),
                "height": preset.get("height"),
                "width_param": res.get("widthInput", "width"),
                "height_param": res.get("heightInput", "height"),
            }
        )
    prompt_name = next((s.name for s in specs if s.is_prompt), None)
    by_name = {s.name: s for s in specs}
    for spec in specs:
        # The service validates only the inputs it declares, and a public schema can omit a hidden input that a rule
        # still names. Nothing can set such a sibling: ifDefined never applies and ifNotDefined always does. Blank
        # names are dropped here for panel drawing; strict form preparation refuses them before any SDK dispatch.
        if any(_names_input(name) and name not in by_name for name in spec.required_if_not_defined):
            spec.required_always = True
        spec.required_if_defined = tuple(
            name for name in spec.required_if_defined if name in by_name
        )
        spec.required_if_not_defined = tuple(
            name for name in spec.required_if_not_defined if name in by_name
        )
    one_of = []
    # Explicit either/or from the schema: `required: {ifNotDefined: {sibling: ...}}` means "at least one of this
    # input and its named siblings" (Cartwheel: a 3D character mesh OR a reference image).
    # Each distinct group is required. Overlapping groups are not interchangeable:
    # (a OR b) AND (b OR c) cannot be weakened to (a OR b OR c).
    for spec in specs:
        siblings = set(spec.required_if_not_defined)
        if not siblings:
            continue
        members = {spec.name, *siblings}
        group = tuple(sorted(members))
        if group not in one_of:
            one_of.append(group)
    if prompt_name:
        # implicit either/or: a required file whose own description says it applies only "if no prompt" is an
        # alternative to the prompt, not a hard requirement. Preserve each file-or-prompt clause.
        conditional = [
            s for s in specs if s.is_file and s.required_always and _is_conditional(s.description)
        ]
        if conditional:
            for spec in conditional:
                spec.required_always = False
                one_of.append((spec.name, prompt_name))
    return Schema(
        specs=specs,
        resolution_presets=presets,
        prompt_name=prompt_name,
        one_of=one_of,
        exclusive=_exclusive_pairs(specs),
    )


def _coerce(spec, value):
    if spec.ptype == "number":
        if isinstance(value, str):
            value = float(value) if value.strip() else None
            if value is None:
                return None
        return int(round(value)) if spec.is_integer else float(value)
    if spec.ptype == "boolean":
        return bool(value)
    if spec.ptype == "string_array":
        return [str(v) for v in value]
    if spec.allowed_values and not isinstance(spec.allowed_values[0], str):
        for candidate in spec.allowed_values:
            if str(candidate) == str(value):
                return candidate
    return value


def build_body(specs, values, files, enabled=None):
    """Flat request body. Unset optionals are omitted; arrays stay arrays; files become asset ids."""
    body = {}
    for spec in specs:
        if enabled is not None and not spec.required_always and not enabled.get(spec.name, True):
            continue
        if spec.is_file:
            ids = list(files.get(spec.name) or [])
            if not ids:
                continue
            body[spec.name] = ids if spec.ptype == "file_array" else ids[0]
            continue
        value = values.get(spec.name)
        if value is None or value == "" or (isinstance(value, (list, tuple)) and len(value) == 0):
            continue
        coerced = _coerce(spec, value)
        if coerced is None:
            continue
        body[spec.name] = coerced
    return body


def _defined(value):
    """Treat blank strings as absent while preserving supplied false and zero."""
    return value is not None and value != [] and not (isinstance(value, str) and not value.strip())


def validate(specs, body, one_of=(), exclusive=()):
    errors = []
    for spec in specs:
        value = body.get(spec.name)
        present = _defined(value)
        if spec.required_always and not present:
            errors.append(f"{spec.label} is required")
            continue
        if not present:
            continue
        if spec.max_length and isinstance(value, str) and len(value) > spec.max_length:
            errors.append(f"{spec.label} is longer than {spec.max_length} characters")
        if (
            spec.allowed_values
            and spec.ptype != "string_array"
            and value not in spec.allowed_values
        ):
            options = ", ".join(str(v) for v in spec.allowed_values)
            errors.append(f"{spec.label} must be one of {options}")
        if spec.ptype == "number" and isinstance(value, (int, float)) and not spec.allowed_values:
            lo, hi = spec.min, spec.max
            if (lo is not None and value < lo) or (hi is not None and value > hi):
                errors.append(f"{spec.label} must be between {_fmt(lo)} and {_fmt(hi)}")
    errors.extend(validate_requirements(specs, body, one_of, exclusive))
    return errors


def validate_requirements(specs, body, one_of=(), exclusive=()):
    """Validate sibling relationships independently of field type/value checks."""
    errors = []
    by_name = {s.name: s for s in specs}
    for spec in specs:
        for dep_name in spec.required_if_defined:
            dep = by_name.get(dep_name)
            if (
                dep is not None
                and _defined(body.get(dep_name))
                and not _defined(body.get(spec.name))
            ):
                errors.append(f"{spec.label} is required when {dep.label} is set")
    for group in one_of:
        if not any(_defined(body.get(name)) for name in group):
            labels = [(by_name[name].label if name in by_name else name) for name in group]
            errors.append("Provide one of: " + " or ".join(labels))
    for first, second in exclusive:
        if _defined(body.get(first)) and _defined(body.get(second)):
            labels = [
                (by_name[name].label if name in by_name else name) for name in (first, second)
            ]
            errors.append(f"{labels[0]} can't be combined with {labels[1]}; remove one of them")
    return errors


def missing_required_files(specs, body):
    return [
        s.name
        for s in specs
        if s.is_file and s.required_always and body.get(s.name) in (None, "", [])
    ]


def _fmt(value):
    if value is None:
        return "?"
    return str(int(value)) if float(value).is_integer() else str(value)
