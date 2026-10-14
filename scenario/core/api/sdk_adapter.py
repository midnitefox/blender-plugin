# SPDX-FileCopyrightText: 2026 Scenario Inc.
# SPDX-License-Identifier: GPL-3.0-or-later
"""Scoped SDK reads, exact estimates and unpaid organization writes.

No bpy imports, ambient credentials, redirects or automatic retries. Paid
dispatch requires an issued estimate and a durable claim callback. Organization
writes are sent once and classified as rejected or uncertain, never replayed.
SDK imports are lazy so package registration does not start client work.
"""

import copy
import json
import math
import re
import threading
import time
import unicodedata
from collections.abc import Callable
from dataclasses import dataclass, field
from decimal import Decimal
from urllib.parse import urlsplit
from weakref import WeakValueDictionary

from ..schema.forms import prepare_run, record_schema
from .sdk_extensions import SDKResourceExtensions
from .user_agent import user_agent_string

API_URL = "https://api.cloud.scenario.com/v1"
# The API reference states no get-bulk batch limit; stay small until a capture
# establishes one, and bound each call so a refresh cannot fan out reads.
MODEL_BULK_CHUNK = 50
MODEL_BULK_LIMIT = 200


class AdapterError(RuntimeError):
    """Safe text for UI/worker boundaries; never include response bodies or URLs."""


class AdapterStatusError(AdapterError):
    """Scenario replied with a non-success HTTP status; the text stays sanitized.

    The status separates a service reply from a lost response. Callers decide
    which statuses are definitive refusals; a 5xx can follow an applied change.
    A model description read's HTTP 403 or 404 raises AdapterUnavailable instead.
    """

    def __init__(self, status_code, message):
        super().__init__(message)
        self.status_code = status_code

    def __reduce__(self):
        # args holds only the message, so copy and pickle must pass the status too.
        # The text can depend on the request's project, so keep it rather than rebuild it.
        return type(self), (self.status_code, str(self))


class EstimateStartedJob(AdapterError):
    """A dry-run response carried a job, so the service may have started real work.

    No Estimate is issued and nothing is sent again. `job_id` is the job's
    identifier when it is readable, otherwise None.
    """

    def __init__(self, job_id=None):
        named = f"job {job_id}" if job_id else "an unreadable job"
        super().__init__(f"Scenario returned {named} for a price check; nothing was sent again")
        self.job_id = job_id

    def __reduce__(self):
        return type(self), (self.job_id,)


# Set once any dry run in this process returns a job. It is recorded where the response is
# read, so a superseded, drained or orphaned quote still stops automatic pricing.
ESTIMATE_STARTED_JOB = threading.Event()


def _started_job(result):
    """Return (True, job ID or None) when an estimate response names a job."""
    if "job" not in result and "jobId" not in result:
        return False, None
    job = result.get("job")
    candidate = job.get("jobId") if isinstance(job, dict) else result.get("jobId")
    if isinstance(candidate, str) and re.fullmatch(r"[A-Za-z0-9_-]{1,128}", candidate):
        return True, candidate
    return True, None


class AdapterUnavailable(AdapterError):
    """The selected credentials or project cannot read a known model (HTTP 403 or 404)."""

    def __init__(self, status):
        self.status = status
        super().__init__(
            f"This model is not available to the selected credentials or project (HTTP {status})"
        )


def _unavailable_on_denial(method):
    """Raise AdapterUnavailable for HTTP 403/404 from one known-record read.

    The SDK raises inside the call, before the adapter's generic status mapping;
    other failures keep that mapping.
    """
    from scenario_sdk import APIStatusError

    def call(*args, **kwargs):
        try:
            return method(*args, **kwargs)
        except APIStatusError as error:
            if error.status_code in (403, 404):
                raise AdapterUnavailable(error.status_code) from None
            raise

    return call


class WriteRejected(AdapterError):
    """Scenario refused a sent organization write with a definite client error.

    The adapter never resends it. Apart from the add refusal typed as
    AlreadyMembers, the API documents no transaction contract, so a refused
    multi-asset request is not proof that no member changed; read the assets
    back with ``asset_records`` before reporting that nothing changed.
    """

    # Keyword defaults keep copy and pickle working: BaseException rebuilds the
    # instance from the message alone, then restores these attributes.
    def __init__(self, message, status=None):
        super().__init__(message)
        self.status = status


class AlreadyMembers(WriteRejected):
    """Scenario refused a whole add because some assets were already members.

    Raised only for a 400 from the add endpoint whose JSON ``reason`` is the
    service's exact already-member text. The add was observed to be one
    transaction: this refusal writes nothing, so repeating the same request can
    never succeed. Read the assets back with ``asset_records``; the caller may
    send only those that are not members yet. The adapter never resends.
    """

    def __init__(self, message=None, status=400):
        super().__init__(message or _ALREADY_MEMBERS_TEXT, status)


class WriteUncertain(AdapterError):
    """An organization write may have been applied; read it back, never resend it.

    ``collection_id`` is set only when a create was acknowledged with a valid
    collection ID but another name, so the caller can reconcile by that ID.
    """

    def __init__(self, message, status=None, *, collection_id=None):
        super().__init__(message)
        self.status = status
        self.collection_id = collection_id


# Local bounds. The SDK documents 49 assets per membership change and 200 per
# bulk read; tag and name limits are undocumented, so labels follow the Film
# plan's 200-character rule and tag changes allow its 30-tag task list.
MAX_COLLECTION_ASSETS = 49
MAX_ASSET_RECORDS = 200
MAX_ORGANIZATION_LABEL = 200
MAX_TAG_CHANGES = 30
# Labels are single-line display text. Reject control and format characters
# (bidirectional controls and marks, invisible separators and operators, soft
# hyphens, tag characters), unencodable surrogates and line or paragraph
# separators by category. Only the two joiners stay, between other characters.
_LABEL_CATEGORIES = frozenset({"Cc", "Cf", "Cs", "Zl", "Zp"})
_LABEL_JOINERS = frozenset("\u200c\u200d")
# 408, 425 and 429 can arrive after the service accepted the work, and 409 has
# no documented meaning for these endpoints (duplicate member, name or lock).
_UNCERTAIN_CLIENT_STATUSES = frozenset({408, 409, 425, 429})
_UNCONFIRMED = "Scenario did not confirm this change; read it back before changing it again"
# The add endpoint's reason for refusing a request that names an existing
# member. Matched exactly; the body itself is never echoed.
_ALREADY_MEMBERS_REASON = "One or more assets are already part of the collection"
_ALREADY_MEMBERS_TEXT = (
    "Scenario added none of these assets because some are already in the collection (HTTP 400)"
)

# Waiting workflow step types and the actions their documented endpoints accept.
_STEP_ACTIONS = {
    "user-approval": frozenset({"approve", "reject"}),
    "user-selection": frozenset({"select", "reject"}),
}
# The API reference's default maximum pick count for a user-selection step.
_MAX_SELECTED_ITEMS = 100
# Larger JSON integers lose precision in parsers that read numbers as doubles.
_MAX_JSON_INTEGER = 2**53 - 1


@dataclass(frozen=True)
class Credentials:
    api_key: str = field(default="", repr=False)
    api_secret: str = field(default="", repr=False)
    bearer_token: str = field(default="", repr=False)

    def authorization(self):
        import base64

        basic = bool(self.api_key and self.api_secret)
        partial = bool(self.api_key) != bool(self.api_secret)
        if partial or basic == bool(self.bearer_token):
            raise ValueError("Select one complete API-key pair or one bearer token")
        values = (self.api_key, self.api_secret, self.bearer_token)
        if any(not isinstance(value, str) or not value.isascii() for value in values):
            raise ValueError("Credentials must be ASCII strings")
        if any(any(ord(char) < 33 or ord(char) == 127 for char in value) for value in values):
            raise ValueError("Credentials cannot contain whitespace or control characters")
        if basic:
            if ":" in self.api_key:
                raise ValueError("API keys cannot contain a colon")
            encoded = base64.b64encode(f"{self.api_key}:{self.api_secret}".encode()).decode()
            return f"Basic {encoded}"
        return f"Bearer {self.bearer_token}"


@dataclass(frozen=True)
class Estimate:
    """Immutable payload/response bytes; dict access returns a fresh copy."""

    operation: str
    target_id: str
    project_id: str | None
    cost: Decimal
    payload_json: bytes = field(repr=False)
    response_json: bytes = field(repr=False)
    scope: object = field(repr=False)
    issued_at: float = field(repr=False)
    # Loop nodes in a quoted workflow definition; None when coverage is unknown.
    loop_steps: int | None = 0

    @property
    def payload(self):
        return json.loads(self.payload_json)

    @property
    def details(self):
        return _json(self.response_json, exact=True)


# SDK 2.2.0 WorkflowFlow.type values that neither loop nor nest a workflow, plus
# user-selection, observed in live workflow job flows but not in that type.
NON_LOOP_NODE_TYPES = frozenset(
    {
        "custom-model",
        "generate-prompt",
        "list",
        "logic",
        "model",
        "remove-background",
        "transform",
        "user-approval",
        "user-selection",
    }
)
# WorkflowFlow fields that only ForEach nodes and their iteration copies carry.
LOOP_NODE_FIELDS = ("loopBodyNodeIds", "count", "loopNodeId", "iterationIndex")


def workflow_loop_steps(workflow):
    """Count loop nodes in a retrieved workflow definition, or None if unknown.

    The server's workflow dry run prices one pass through a loop, so a run that
    iterates more often can charge more than its quote. A node is a loop when
    its type is `for-each` or it carries any ForEach field. The result is 0 only
    when every node is proven not to loop. Coverage is unknown for a missing or
    malformed flow; an empty flow, unless the record says `hasFlow: false` (the
    SDK documents `hasFlow` as present even when `flow` is not); a nested
    workflow step, or any node carrying a `workflowId`, whose definition is not
    read; or a node whose type is missing, malformed or not a known non-loop type.
    """
    flow = workflow.get("flow") if isinstance(workflow, dict) else None
    if not isinstance(flow, list) or (not flow and workflow.get("hasFlow") is not False):
        return None
    loops = 0
    for node in flow:
        if not isinstance(node, dict):
            return None
        kind = node.get("type")
        if not isinstance(kind, str) or (kind != "for-each" and kind not in NON_LOOP_NODE_TYPES):
            return None  # Includes workflow and any renamed or new node type.
        if "workflowId" in node:
            return None  # The SDK names it for workflow tasks; its flow is not read.
        if kind == "for-each" or any(name in node for name in LOOP_NODE_FIELDS):
            loops += 1
    return loops


def _reject_constant(value):
    raise ValueError("Non-finite JSON number")


def _json(raw, *, exact=False):
    try:
        result = json.loads(
            raw, parse_float=Decimal if exact else float, parse_constant=_reject_constant
        )
    except (ValueError, UnicodeError, RecursionError):
        # Deeply nested arrays or objects exceed the decoder's recursion limit.
        raise AdapterError("Scenario returned invalid JSON") from None
    if not isinstance(result, dict):
        raise AdapterError("Scenario returned an unexpected response")
    return result


def _status_text(status, *, project):
    """Short first sentence that survives clipped status lines, then fixed guidance.

    The guidance is credential-source neutral (saved or environment keys). Never
    include response bodies, URLs, identifiers or credentials.
    """
    if status == 401:
        return "Key or secret rejected (HTTP 401). Check the selected API key and secret."
    if status == 403 and project:
        return (
            "Access denied (HTTP 403). Check the selected API key and secret, and that the "
            "Project ID belongs to this key, or clear it to use the key's default scope."
        )
    if status == 403:
        return "Access denied (HTTP 403). Check the selected API key and secret."
    if status == 429:
        return "Too many requests (HTTP 429). Try again shortly."
    return f"Scenario request failed (HTTP {status})"


def _identifier(value):
    if not isinstance(value, str) or not value or value in {".", ".."} or value.strip() != value:
        raise ValueError("A nonempty identifier is required")
    # Lone surrogates cannot be encoded as UTF-8; the SDK would fail while
    # building the request, so reject them here as invalid input.
    if any(char in value for char in "/\\?#%") or any(
        ord(char) < 33 or 0xD800 <= ord(char) <= 0xDFFF for char in value
    ):
        raise ValueError("Identifier contains unsupported characters")
    return value


def _printable(value):
    """Reject controls, format characters such as bidi overrides and lone surrogates.

    A lone surrogate cannot be encoded as UTF-8, so it would otherwise fail
    inside SDK serialization instead of in this validation.
    """
    if not value.isprintable():
        raise ValueError("Identifier contains unsupported characters")
    return value


def _node_identifier(value):
    """Flow node IDs travel only in a JSON body, so path-unsafe characters are acceptable."""
    if not isinstance(value, str) or not value or value.strip() != value or len(value) > 1024:
        raise ValueError("A nonempty workflow step identifier is required")
    return _printable(value)


def _selected_indices(values):
    """Keep 1-100 zero-based picks in order; the step's own min/max is the caller's check."""
    if not isinstance(values, (list, tuple)):
        raise ValueError("Selected items must be a list of indices")
    picks = list(values)
    if not picks:
        raise ValueError("Select at least one item")
    if len(picks) > _MAX_SELECTED_ITEMS:
        raise ValueError(f"Select at most {_MAX_SELECTED_ITEMS} items")
    if any(type(value) is not int or not 0 <= value <= _MAX_JSON_INTEGER for value in picks):
        raise ValueError("Selected items must be nonnegative integer indices")
    if len(set(picks)) != len(picks):
        raise ValueError("Select each item at most once")
    return picks


def _upload_identifier(value):
    """Keep upload and asset-option identities safe for paths and future storage."""
    value = _identifier(value)
    if len(value) > 256 or any(char.isspace() or ord(char) == 127 for char in value):
        raise ValueError("Upload identifiers must be at most 256 characters without whitespace")
    return value


def _upload_record(raw, identifier=None):
    record = _json(raw).get("upload")
    if not isinstance(record, dict):
        raise AdapterError("Scenario returned no upload record")
    try:
        actual = _upload_identifier(record.get("id"))
    except ValueError:
        raise AdapterError("Scenario returned an invalid upload identity") from None
    if identifier is not None and actual != identifier:
        raise AdapterError("Scenario returned a different upload identity")
    status = record.get("status")
    if not isinstance(status, str) or not status.strip():
        raise AdapterError("Scenario returned no upload status")
    # Preserve future statuses, transfer instructions and fields verbatim. This
    # is metadata, not a validated transfer plan or proof of completed import.
    return record


def _organization_label(value, field):
    """Accept an exact bounded single-line label; never strip or rewrite what is sent.

    Lone surrogates would fail inside the SDK. Control and format characters
    (bidirectional controls, invisible separators and operators, tag characters)
    and line breaks could hide or reorder text or break Library and MCP rows.
    Joiners (U+200C, U+200D) stay valid between other characters for scripts and
    emoji sequences. This is not a confusable check: homoglyphs, combining marks
    and blank-looking letters such as U+3164 pass, so displays must still treat
    labels as untrusted text.
    """
    if (
        not isinstance(value, str)
        or not value.strip()
        or value != value.strip()
        or len(value) > MAX_ORGANIZATION_LABEL
    ):
        raise ValueError(
            f"{field} must be nonempty, at most {MAX_ORGANIZATION_LABEL} characters "
            "and without surrounding spaces"
        )
    if value[0] in _LABEL_JOINERS or value[-1] in _LABEL_JOINERS:
        raise ValueError(f"{field} cannot start or end with a joiner")
    if any(
        char not in _LABEL_JOINERS and unicodedata.category(char) in _LABEL_CATEGORIES
        for char in value
    ):
        raise ValueError(f"{field} cannot contain control, format or unencodable characters")
    return value


def _tag_changes(values, field):
    if values is None:
        return []
    if not isinstance(values, (list, tuple)) or len(values) > MAX_TAG_CHANGES:
        raise ValueError(f"Use a list of at most {MAX_TAG_CHANGES} tags to {field}")
    tags = [_organization_label(value, "Tag") for value in values]
    if len(set(tags)) != len(tags):
        raise ValueError(f"Tags to {field} must be unique")
    return tags


def _identifier_list(values, noun):
    """Valid identifiers in request order with repeats removed; ValueError otherwise."""
    if isinstance(values, (str, bytes)) or not isinstance(values, (list, tuple)):
        raise ValueError(f"Use a list of {noun} identifiers")
    return list(dict.fromkeys(_identifier(value) for value in values))


def _asset_ids(values, limit, *, unique):
    if not isinstance(values, (list, tuple)) or not 1 <= len(values) <= limit:
        raise ValueError(f"Use a list of 1 to {limit} asset identifiers")
    identifiers = _identifier_list(values, "asset")
    if unique and len(identifiers) != len(values):
        raise ValueError("Asset identifiers must be unique")
    return identifiers


def _records_by_id(rows, noun, hint, requested=None):
    """Map rows by valid ID; drop identical duplicates and fail on conflicting ones.

    With ``requested``, every row must carry one of those IDs.
    """
    records = {}
    for row in rows:
        try:
            identifier = _identifier(row.get("id") if isinstance(row, dict) else None)
        except ValueError:
            raise AdapterError(f"Scenario returned an invalid {noun} record") from None
        if requested is not None and identifier not in requested:
            article = "an" if noun[0] in "aeiou" else "a"
            raise AdapterError(f"Scenario returned {article} {noun} that was not requested")
        if identifier in records and records[identifier] != row:
            raise AdapterError(f"Scenario returned conflicting {noun} records; {hint}")
        records[identifier] = row
    return records


def _unique_rows(rows, limit, noun):
    """Validate one page of at most ``limit`` records by ID."""
    if not isinstance(rows, list) or len(rows) > limit:
        raise AdapterError(f"Scenario returned an invalid {noun} page")
    return _records_by_id(rows, noun, "refresh the library")


def _bulk_records(rows, requested, noun):
    """Records of one bulk read by requested ID, in request order.

    Shared by ``models_bulk`` and ``asset_records``. Every record must carry a
    requested ID, so identical duplicates collapse and the request bounds the
    result. IDs the response omits stay absent: the API reference does not say
    whether a missing or inaccessible ID is omitted or fails the request.
    """
    if not isinstance(rows, list):
        raise AdapterError(f"Scenario returned an invalid bulk {noun} list")
    records = _records_by_id(rows, noun, "refresh", frozenset(requested))
    return {identifier: records[identifier] for identifier in requested if identifier in records}


def _already_members(status, body):
    """Whether an add refusal is the service's exact already-member refusal."""
    return (
        status == 400 and isinstance(body, dict) and body.get("reason") == _ALREADY_MEMBERS_REASON
    )


def _write_rejected(status):
    if status == 401:
        text = "Scenario rejected the selected credentials"
    elif status == 403:
        text = "The selected credentials cannot change this asset or collection"
    elif status == 404:
        text = "Asset or collection not found in the selected connection"
    else:
        text = "Scenario rejected this organization change"
    return WriteRejected(f"{text} (HTTP {status})", status)


def _confirmed(raw):
    """Parse a 2xx write acknowledgement; malformed bodies leave the outcome unknown."""
    try:
        return _json(raw)
    except AdapterError:
        raise WriteUncertain(_UNCONFIRMED) from None


def _prepare(operation, record, parameters):
    """Use the shared pure form preparation, conditional requirements and LoRA inputs.

    The schema carries the record's `uiConfig`, so `lorasComponent` names the
    LoRA inputs and their strength policy applies.
    """
    identifier = _identifier(record.get("id"))
    return prepare_run(identifier, record_schema(operation, record), parameters)


def _client(credentials, base_url, timeout, transport):
    import httpx
    from scenario_sdk import Scenario

    authorization = credentials.authorization()

    class SelectedAccount(Scenario):
        @property
        def default_headers(self):
            # Public SDK hook: use only adapter-owned headers. SDK 2.2.0 otherwise
            # merges SCENARIO_CUSTOM_HEADERS and prefers Basic over Bearer.
            # https://github.com/scenario-labs/scenario-sdk-python/issues/26
            # Remove after upstream offers verified environment-isolated config.
            # This changes configuration, not endpoint routing; no raw API fallback.
            return {
                "Authorization": authorization,
                "Accept": "application/json",
                "Content-Type": "application/json",
                "User-Agent": user_agent_string(),
            }

    http = httpx.Client(
        transport=transport, follow_redirects=False, trust_env=False, timeout=timeout
    )
    try:
        return SelectedAccount(
            api_key=credentials.api_key,
            api_secret=credentials.api_secret,
            bearer_auth=credentials.bearer_token,
            base_url=base_url,
            max_retries=0,
            timeout=timeout,
            http_client=http,
        )
    except Exception:
        http.close()
        raise


def model_identifiers(values):
    """Unique model identifiers in request order; ValueError for any the adapter rejects.

    Callers that share pending reads by identifier check a request here first,
    so an invalid identifier fails only the request that contains it.
    """
    return _identifier_list(values, "model")


class SDKAdapter:
    """One selected account/project and owned HTTP pool; close after worker use.

    The caller supplies its online-access predicate. Blender callers must bind
    this to their actual online permission, not a saved startup preference.
    """

    def __init__(
        self,
        credentials: Credentials,
        *,
        online: Callable[[], bool],
        project_id=None,
        account_id=None,
        team_id=None,
        base_url=API_URL,
        timeout=45.0,
        transport=None,
    ):
        url = urlsplit(base_url)
        if (
            url.scheme != "https"
            or not url.hostname
            or url.username
            or url.password
            or url.query
            or url.fragment
        ):
            raise ValueError("Use an HTTPS API base URL without credentials, query or fragment")
        if not callable(online):
            raise TypeError("An online-access predicate is required")
        if isinstance(timeout, bool) or not math.isfinite(timeout) or timeout <= 0:
            raise ValueError("Timeout must be finite and positive")
        self._base_url = base_url.rstrip("/")
        self._account_id = _identifier(account_id) if account_id is not None else None
        self._team_id = _identifier(team_id) if team_id is not None else None
        self._estimates = WeakValueDictionary()
        self._estimate_lock = threading.RLock()
        self._project_id = _identifier(project_id) if project_id is not None else None
        self._online = online
        self._scope = object()
        self._closed = False
        self._sdk = _client(credentials, base_url, timeout, transport)
        self._extensions = SDKResourceExtensions(self._sdk)

    @property
    def project_id(self):
        return self._project_id

    @property
    def base_url(self):
        return self._base_url

    @property
    def account_id(self):
        return self._account_id

    @property
    def team_id(self):
        return self._team_id

    def close(self):
        with self._estimate_lock:
            self._estimates.clear()
            self._closed = True
        self._sdk.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    def _request(self, method, *args, **kwargs):
        if self.project_id is not None:
            kwargs["project_id"] = self.project_id
        return self._unscoped_request(method, *args, **kwargs)

    def _unscoped_request(self, method, *args, **kwargs):
        from scenario_sdk import APIConnectionError, APIStatusError, omit

        if self._closed:
            raise AdapterError("Scenario client is closed")
        if not self._online():
            raise AdapterError("Online access is disabled")
        try:
            response = method(*args, **kwargs)
            return response.read()
        except APIStatusError as error:
            # Only a request that carried the override can blame the Project ID;
            # the SDK's omit sentinel means the request sent no projectId.
            project_id = kwargs.get("project_id")
            project = project_id is not None and project_id is not omit
            status = error.status_code
            raise AdapterStatusError(status, _status_text(status, project=project)) from None
        except APIConnectionError:
            raise AdapterError("Could not reach Scenario") from None

    def _write(self, method, *args, adding=False, **kwargs):
        """Send one unpaid account mutation through a public SDK raw wrapper.

        A plain AdapterError (closed client, online access disabled) means
        nothing was sent. After dispatch, failures raise only WriteRejected or
        WriteUncertain; with ``adding``, the add endpoint's exact already-member
        refusal raises the AlreadyMembers subclass. The client keeps
        max_retries=0 because the SDK would otherwise retry 408/409/429/5xx.
        Collection creation and membership changes document no idempotency
        contract. Non-strict tag changes are documented to behave as if
        idempotent, but a resend after an unknown outcome could reapply a change
        another client has since reverted, so callers read back instead.
        """
        from scenario_sdk import APIStatusError

        if self.project_id is not None:
            kwargs["project_id"] = self.project_id
        if self._closed:
            raise AdapterError("Scenario client is closed")
        if not self._online():
            raise AdapterError("Online access is disabled")
        try:
            return method(*args, **kwargs).read()
        except APIStatusError as error:
            status = error.status_code
            if adding and _already_members(status, error.body):
                raise AlreadyMembers() from None
            if 400 <= status < 500 and status not in _UNCERTAIN_CLIENT_STATUSES:
                raise _write_rejected(status) from None
            raise WriteUncertain(
                f"Scenario did not confirm this change (HTTP {status}); "
                "read it back before changing it again",
                status,
            ) from None
        except Exception:
            # The SDK wraps every send failure, including timeouts and lost
            # connections, in APIConnectionError; those can follow an applied
            # change. It also processes the response outside that wrapper, so
            # an unexpected failure cannot be proven unsent. Every write method
            # validates its input, including UTF-8 encodability, before calling
            # this, so a local request-building failure is not expected here.
            raise WriteUncertain(_UNCONFIRMED) from None

    def _discovery(self, method, wrapper, *args):
        value = _json(self._unscoped_request(method, *args))
        records = value.get(wrapper)
        if not isinstance(records, list):
            raise AdapterError(f"Scenario returned no {wrapper} list")
        for record in records:
            try:
                _identifier(record.get("id") if isinstance(record, dict) else None)
            except ValueError:
                raise AdapterError(f"Scenario returned an invalid {wrapper} identity") from None
        return value

    def teams(self):
        """Return one unscoped discovery response, without selecting a team.

        Optional metadata discovery; API-key operations need no team/project
        selection. This list does not establish a key's default project.
        """
        return self._discovery(self._extensions.teams, "teams")

    def projects(self, team_id):
        """Return one team's discovery response, ignoring the selected project."""
        return self._discovery(self._extensions.projects, "projects", _identifier(team_id))

    def _retrieve(self, resource, identifier, wrapper, *, unavailable=False, **options):
        method = getattr(self._sdk, resource).with_raw_response.retrieve
        if unavailable:
            method = _unavailable_on_denial(method)
        value = _json(self._request(method, _identifier(identifier), **options))
        record = value.get(wrapper)
        if not isinstance(record, dict):
            raise AdapterError(f"Scenario returned no {wrapper} record")
        return record

    def model(self, identifier):
        """Read one model record; HTTP 403/404 raise AdapterUnavailable."""
        return self._retrieve("models", identifier, "model", unavailable=True)

    def models_bulk(self, identifiers, *, chunk=MODEL_BULK_CHUNK):
        """Read known models with the SDK's `models.get_bulk`, in bounded requests.

        Returns {id: record} in request order for the records Scenario returned.
        Omitted IDs stay absent: the API reference does not say whether a
        missing or inaccessible ID is omitted or fails the request. Bulk records
        are discovery data: the reference directs `inputs` readers to GET
        /models/{modelId}, so read the model before building a form or quote.
        Every chunk carries the selected project and rechecks online permission;
        any failure returns nothing rather than a partial result.
        """
        if type(chunk) is not int or not 1 <= chunk <= MODEL_BULK_CHUNK:
            raise ValueError(f"Bulk model chunks hold 1 to {MODEL_BULK_CHUNK} identifiers")
        requested = model_identifiers(identifiers)
        if len(requested) > MODEL_BULK_LIMIT:
            raise ValueError(f"Read at most {MODEL_BULK_LIMIT} models at once")
        records = {}
        for start in range(0, len(requested), chunk):
            batch = requested[start : start + chunk]
            page = _json(
                self._request(self._sdk.models.with_raw_response.get_bulk, model_ids=batch)
            )
            # Batches are disjoint and each record must belong to its own batch,
            # so merging them keeps request order and cannot hide a conflict.
            records.update(_bulk_records(page.get("models"), batch, "model"))
        return records

    def workflow(self, identifier):
        return self._retrieve("workflows", identifier, "workflow")

    def asset(self, identifier, *, original_assets=False):
        """Read one asset; pass original_assets=True before downloading its bytes.

        SDK 2.2.0 `original_assets` "returns the original asset without
        transformation". The default `url` of a large image can serve a converted
        file whose bytes differ from `mimeType` and `properties.size`.
        """
        if type(original_assets) is not bool:
            raise ValueError("Choose whether to request the original asset")
        options = {"original_assets": True} if original_assets else {}
        return self._retrieve("assets", identifier, "asset", **options)

    def network_allowed(self):
        """Read the caller's online permission without contacting Scenario."""
        return not self._closed and bool(self._online())

    def bulk_assets(self, identifiers):
        """Read up to 100 known asset records in the selected scope with one SDK request.

        SDK 2.2.0 ``assets.with_raw_response.get_bulk`` documents a 200-ID limit;
        callers chunk by 100. Assets the server omits are absent from the
        result, never inferred. This is a read without retries or side effects.
        """
        identifiers = tuple(identifiers)
        if not 1 <= len(identifiers) <= 100 or len(set(identifiers)) != len(identifiers):
            raise ValueError("Choose from 1 to 100 distinct asset identities")
        for identifier in identifiers:
            _identifier(identifier)
        page = _json(
            self._request(self._sdk.assets.with_raw_response.get_bulk, asset_ids=list(identifiers))
        )
        rows = page.get("assets")
        if not isinstance(rows, list) or len(rows) > len(identifiers):
            raise AdapterError("Scenario returned an invalid asset list")
        records = {}
        for row in rows:
            identifier = row.get("id") if isinstance(row, dict) else None
            if identifier not in identifiers:
                raise AdapterError("Scenario returned an unrequested asset record")
            if identifier in records and records[identifier] != row:
                raise AdapterError("Scenario returned conflicting asset records")
            records[identifier] = row
        return records

    def asset_page(self, *, public=False, page_size=40, pagination_token=None, collection_id=None):
        """Read one SDK asset page in the selected scope, without automatic traversal."""
        if type(public) is not bool or type(page_size) is not int or not 1 <= page_size <= 100:
            raise ValueError("Choose public/owned assets and a page size from 1 to 100")
        options = {"page_size": page_size}
        if public:
            options["privacy"] = "public"
        if pagination_token is not None:
            if not isinstance(pagination_token, str) or not pagination_token:
                raise ValueError("Use a nonempty asset cursor")
            options["pagination_token"] = pagination_token
        if collection_id is not None:
            options["collection_id"] = _identifier(collection_id)
        page = _json(self._request(self._sdk.assets.with_raw_response.list, **options))
        rows = list(_unique_rows(page.get("assets"), page_size, "asset").values())
        token = page.get("nextPaginationToken")
        if token not in (None, "") and (not isinstance(token, str) or token == pagination_token):
            raise AdapterError("Scenario repeated or returned an invalid asset cursor")
        return {"assets": rows, "next_pagination_token": token or None}

    def search_assets(self, query, *, public=False, limit=40, offset=0):
        """Use the public SDK search method with explicit body-based pagination."""
        if not isinstance(query, str) or not query.strip() or len(query) > 4096:
            raise ValueError("Use a nonempty asset search of at most 4096 characters")
        if (
            type(public) is not bool
            or type(limit) is not int
            or not 1 <= limit <= 100
            or type(offset) is not int
            or offset < 0
        ):
            raise ValueError(
                "Choose public/owned assets, a limit from 1 to 100 and nonnegative offset"
            )
        page = _json(
            self._request(
                self._sdk.search.with_raw_response.asset_search,
                query=query,
                public=public,
                limit=limit,
                offset=offset,
            )
        )
        hits = page.get("hits")
        rows = list(_unique_rows(hits, limit, "asset").values())
        total = page.get("estimatedTotalHits")
        returned_offset = page.get("offset")
        if (total is not None and (type(total) is not int or total < 0)) or (
            returned_offset is not None
            and (type(returned_offset) is not int or returned_offset != offset)
        ):
            raise AdapterError("Scenario returned invalid asset search pagination")
        end = offset + len(hits)
        more = bool(hits) and (end < total if total is not None else len(hits) == limit)
        return {"assets": rows, "estimated_total": total, "next_offset": end if more else None}

    def collection_page(self, *, page_size=50, pagination_token=None):
        """Read one collection page in the selected scope, without automatic traversal."""
        if type(page_size) is not int or not 1 <= page_size <= 100:
            raise ValueError("Collection page size must be an integer from 1 to 100")
        options = {"page_size": page_size}
        if pagination_token is not None:
            if not isinstance(pagination_token, str) or not pagination_token:
                raise ValueError("Use a nonempty collection cursor")
            options["pagination_token"] = pagination_token
        page = _json(self._request(self._sdk.collections.with_raw_response.list, **options))
        rows = _unique_rows(page.get("collections"), page_size, "collection")
        token = page.get("nextPaginationToken")
        if token not in (None, "") and (not isinstance(token, str) or token == pagination_token):
            raise AdapterError("Scenario repeated or returned an invalid collection cursor")
        return {"collections": list(rows.values()), "next_pagination_token": token or None}

    def collection(self, identifier):
        """Retrieve one known collection; another returned identity is an error."""
        identifier = _identifier(identifier)
        record = self._retrieve("collections", identifier, "collection")
        if record.get("id") != identifier:
            raise AdapterError("Scenario returned a different collection")
        return record

    def asset_records(self, asset_ids):
        """Read current tags and collection memberships of known assets.

        This is the verification read after organization writes; search indexes
        may lag. Duplicate requested IDs are read once. Assets the service does
        not return are absent from the result, which the caller must treat as
        unverified rather than unchanged. Unrequested records are an error, and
        so is a record whose ``tags`` or ``collectionIds`` is not a list of
        strings: SDK 2.2.0 declares both required, and absent metadata must
        never read as an empty, verified state.
        """
        identifiers = _asset_ids(asset_ids, MAX_ASSET_RECORDS, unique=False)
        page = _json(
            self._request(self._sdk.assets.with_raw_response.get_bulk, asset_ids=identifiers)
        )
        records = _bulk_records(page.get("assets"), identifiers, "asset")
        for record in records.values():
            for key in ("tags", "collectionIds"):
                values = record.get(key)
                if not isinstance(values, list) or any(not isinstance(v, str) for v in values):
                    raise AdapterError("Scenario returned invalid asset organization metadata")
        return records

    def create_collection(self, name):
        """Create one collection; this POST is not idempotent.

        The API documents no name uniqueness. Callers must look the exact name up
        first and, after WriteUncertain, reconcile by reading instead of
        creating again. Returns the acknowledged record, whose name must match.
        A valid acknowledged ID with another name, for example after service
        normalization, raises WriteUncertain carrying ``collection_id`` so the
        caller reconciles by ID; an exact-name lookup would miss it.
        """
        name = _organization_label(name, "Collection name")
        raw = self._write(self._sdk.collections.with_raw_response.create, name=name)
        record = _confirmed(raw).get("collection")
        if not isinstance(record, dict):
            raise WriteUncertain(_UNCONFIRMED)
        try:
            identifier = _identifier(record.get("id"))
        except ValueError:
            raise WriteUncertain(_UNCONFIRMED) from None
        if record.get("name") != name:
            raise WriteUncertain(_UNCONFIRMED, collection_id=identifier)
        return record

    def _membership(self, method, collection_id, asset_ids, *, adding=False):
        collection_id = _identifier(collection_id)
        identifiers = _asset_ids(asset_ids, MAX_COLLECTION_ASSETS, unique=True)
        raw = self._write(method, collection_id, asset_ids=identifiers, adding=adding)
        record = _confirmed(raw).get("collection")
        if not isinstance(record, dict) or record.get("id") != collection_id:
            raise WriteUncertain(_UNCONFIRMED)
        return record

    def add_collection_assets(self, collection_id, asset_ids):
        """Add 1 to 49 unique assets to one collection with a single request.

        49 is the API's per-request cap, as the SDK docstring states. The
        acknowledgement names the collection, not each asset. The add was
        observed to be one transaction: when any asset is already a member, the
        service refuses the whole request with a 400 and writes nothing, raised
        here as AlreadyMembers. Any other 400 stays a plain WriteRejected and a
        409 is uncertain. Verify the membership with ``asset_records``.
        """
        return self._membership(
            self._sdk.collections.with_raw_response.assets.add,
            collection_id,
            asset_ids,
            adding=True,
        )

    def remove_collection_assets(self, collection_id, asset_ids):
        """Remove 1 to 49 unique assets from one collection with a single request.

        The SDK sends the IDs as a DELETE JSON body. A 2xx acknowledgement is not
        proof that an intermediary preserved that body; verify with
        ``asset_records``.
        """
        return self._membership(
            self._sdk.collections.with_raw_response.assets.remove, collection_id, asset_ids
        )

    def update_asset_tags(self, asset_id, *, add=(), remove=()):
        """Add and remove exact tags on one asset with a single non-strict request.

        ``strict=False`` makes repeated additions and absent removals behave as
        documented idempotent no-ops, so ``added``/``deleted`` may be empty.
        Reported tags must be a subset of those requested. Verify with
        ``asset_records``; the service may normalize tags in undocumented ways.
        """
        identifier = _identifier(asset_id)
        added = _tag_changes(add, "add")
        removed = _tag_changes(remove, "remove")
        if not added and not removed:
            raise ValueError("Add or remove at least one tag")
        if set(added) & set(removed):
            raise ValueError("A tag cannot be added and removed in the same change")
        options = {"strict": False}
        if added:
            options["add"] = added
        if removed:
            options["delete"] = removed
        result = _confirmed(
            self._write(self._sdk.assets.with_raw_response.update_tags, identifier, **options)
        )
        for key, sent in (("added", added), ("deleted", removed)):
            values = result.get(key)
            if (
                not isinstance(values, list)
                or any(not isinstance(value, str) for value in values)
                or not set(values) <= set(sent)
            ):
                raise WriteUncertain(_UNCONFIRMED)
        return result

    def job(self, identifier):
        return self._retrieve("jobs", identifier, "job")

    def cancel_inference(self, identifier):
        """Request cancellation once; the coordinator verifies inference eligibility.

        This acknowledgement is not authoritative terminal-state evidence. Poll
        the known job afterwards, including after an uncertain request outcome.
        """
        identifier = _identifier(identifier)
        method = self._sdk.jobs.with_raw_response.trigger_action
        response = _json(self._request(method, identifier, action="cancel"))
        job = response.get("job")
        if not isinstance(job, dict) or job.get("jobId") != identifier:
            raise AdapterError("Scenario returned no matching cancellation acknowledgement")
        return job

    def workflow_decision(
        self, workflow_id, workflow_job_id, node_id, node_type, action, selected_indices=None
    ):
        """Send one decision for a waiting workflow approval or selection step.

        A decision can resume paid steps or stop the workflow, so it is never
        retried. ValueError means nothing was sent. AdapterStatusError is a
        service reply. Treat every other AdapterError as an unknown outcome
        until the job is read again: offline and closed-client refusals happen
        before sending but share that type with a lost response or a malformed
        acknowledgement. The acknowledgement is not terminal-state evidence and
        may contain signed asset URLs, which callers must not persist or log.
        """
        from scenario_sdk import omit

        workflow_id = _printable(_identifier(workflow_id))
        workflow_job_id = _printable(_identifier(workflow_job_id))
        node_id = _node_identifier(node_id)
        actions = _STEP_ACTIONS.get(node_type) if isinstance(node_type, str) else None
        if actions is None:
            raise ValueError("Choose a user-approval or user-selection workflow step")
        if not isinstance(action, str) or action not in actions:
            raise ValueError("Choose an action this workflow step accepts")
        if action != "select" and selected_indices is not None:
            raise ValueError("Only a selection sends selected items")
        if node_type == "user-approval":
            # An omitted action approves, so send it explicitly. projectId stays
            # omitted unless this connection has a project override.
            raw = self._request(
                self._sdk.workflows.with_raw_response.user_approval,
                workflow_id,
                project_id=omit,
                node_id=node_id,
                workflow_job_id=workflow_job_id,
                action=action,
            )
        else:
            body = {"action": action, "nodeId": node_id, "workflowJobId": workflow_job_id}
            if action == "select":
                body["selectedIndices"] = _selected_indices(selected_indices)
            raw = self._request(self._extensions.workflow_user_selection, workflow_id, body=body)
        job = _json(raw).get("job")
        if not isinstance(job, dict) or job.get("jobId") != workflow_job_id:
            raise AdapterError("Scenario returned no matching workflow decision acknowledgement")
        return job

    def create_upload(self, *, kind, file_name, content_type, file_size, parts, asset_options=None):
        """Initialize multipart metadata once; never read or transfer local bytes.

        Errors after dispatch may mean the server created an upload. Callers
        must preserve known identity and reconcile, not recreate automatically.
        """
        if not isinstance(kind, str) or kind not in {
            "3d",
            "asset",
            "audio",
            "avatar",
            "image",
            "model",
            "text",
            "video",
        }:
            raise ValueError("Use a supported upload kind")
        if (
            not isinstance(file_name, str)
            or not file_name.strip()
            or file_name in {".", ".."}
            or any(char in file_name for char in "/\\:")
            or any(ord(char) < 32 or ord(char) == 127 for char in file_name)
        ):
            raise ValueError("Use a file basename without paths or control characters")
        if not isinstance(content_type, str) or not re.fullmatch(
            r"[A-Za-z0-9!#$&^_.+-]+/[A-Za-z0-9!#$&^_.+-]+", content_type
        ):
            raise ValueError("Use a MIME type without parameters or control characters")
        if type(file_size) is not int or file_size < 0:
            raise ValueError("Upload file size must be a nonnegative integer byte count")
        if type(parts) is not int or parts < 1:
            raise ValueError("Upload part count must be a positive integer")
        options = {}
        if asset_options is not None:
            if kind == "model":
                raise ValueError("Model uploads do not support asset options")
            if not isinstance(asset_options, dict) or set(asset_options) - {
                "collection_ids",
                "parent_id",
                "hide",
            }:
                raise ValueError("Use supported SDK asset option names")
            copied = copy.deepcopy(asset_options)
            if "collection_ids" in copied:
                if not isinstance(copied["collection_ids"], list):
                    raise ValueError("Upload collections must be a list of identifiers")
                for value in copied["collection_ids"]:
                    _upload_identifier(value)
            if "parent_id" in copied:
                _upload_identifier(copied["parent_id"])
            if "hide" in copied and type(copied["hide"]) is not bool:
                raise ValueError("Upload visibility must be a boolean")
            options["asset_options"] = copied
        return _upload_record(
            self._request(
                self._sdk.uploads.with_raw_response.create,
                kind=kind,
                file_name=file_name,
                content_type=content_type,
                file_size=file_size,
                parts=parts,
                **options,
            )
        )

    def upload(self, identifier):
        """Retrieve one known upload in the selected project without side effects."""
        identifier = _upload_identifier(identifier)
        return _upload_record(
            self._request(self._sdk.uploads.with_raw_response.retrieve, identifier), identifier
        )

    def complete_upload(self, identifier):
        """Explicitly request completion once; an acknowledgement may be validating.

        The caller must establish that its parts were transferred. This adapter
        does not infer transfer success, poll, retry, or claim asset import.
        """
        identifier = _upload_identifier(identifier)
        return _upload_record(
            self._request(
                self._sdk.uploads.with_raw_response.trigger_action, identifier, action="complete"
            ),
            identifier,
        )

    def job_page(self, *, page_size=50, pagination_token=None):
        """One history page with inputs/results, using an opaque service cursor."""
        if type(page_size) is not int or not 1 <= page_size <= 200:
            raise ValueError("Job page size must be between 1 and 200")
        options = {"page_size": page_size, "hide_results": False}
        if pagination_token is not None:
            if not isinstance(pagination_token, str) or not pagination_token:
                raise ValueError("A nonempty job cursor is required")
            options["pagination_token"] = pagination_token
        page = _json(self._request(self._sdk.jobs.with_raw_response.list, **options))
        rows = page.get("jobs")
        if not isinstance(rows, list):
            raise AdapterError("Scenario returned an invalid job page")
        records = {}
        for row in rows:
            try:
                identifier = _identifier(row.get("jobId") if isinstance(row, dict) else None)
            except ValueError:
                raise AdapterError("Scenario returned an invalid job record") from None
            if identifier in records and records[identifier] != row:
                raise AdapterError("Scenario returned conflicting job records; refresh history")
            records[identifier] = row
        token = page.get("nextPaginationToken")
        if token not in (None, "") and (not isinstance(token, str) or token == pagination_token):
            raise AdapterError("Scenario repeated or returned an invalid job cursor")
        return {**page, "jobs": list(records.values())}

    def jobs(
        self,
        *,
        author_id=None,
        workflow_id=None,
        job_type=None,
        status=None,
        hide_results=True,
        page_size=100,
        max_pages=100,
    ):
        """Return scoped discovery candidates, never proof of submission identity.

        Fail instead of returning a partial history. Callers must retrieve a
        known job again before relying on its state; listing is not a snapshot.
        """
        for value in (page_size, max_pages):
            if not isinstance(value, int) or isinstance(value, bool) or value < 1:
                raise ValueError("Page size and limit must be positive integers")
        if page_size > 200:
            raise ValueError("Job page size cannot exceed 200")
        statuses = {
            "pending",
            "queued",
            "warming-up",
            "in-progress",
            "success",
            "failure",
            "canceled",
            "finalizing",
        }
        if status is not None and (not isinstance(status, str) or status not in statuses):
            raise ValueError("Choose a supported job status")
        if not isinstance(hide_results, bool):
            raise ValueError("Result visibility must be a boolean")
        options = {"page_size": page_size, "hide_results": hide_results}
        for key, value in (
            ("author_id", author_id),
            ("workflow_id", workflow_id),
            ("type", job_type),
        ):
            if value is not None:
                options[key] = _identifier(value)
        if status is not None:
            options["status"] = status
        records, tokens = {}, set()
        for _ in range(max_pages):
            page = _json(self._request(self._sdk.jobs.with_raw_response.list, **options))
            rows = page.get("jobs")
            if not isinstance(rows, list):
                raise AdapterError("Scenario returned an invalid job page")
            for row in rows:
                try:
                    identifier = _identifier(row.get("jobId") if isinstance(row, dict) else None)
                except ValueError:
                    raise AdapterError("Scenario returned an invalid job record") from None
                if identifier in records and records[identifier] != row:
                    raise AdapterError("Scenario returned conflicting job records; refresh history")
                records[identifier] = row
            token = page.get("nextPaginationToken")
            if token is None or token == "":
                return list(records.values())
            if not isinstance(token, str) or token in tokens:
                raise AdapterError("Scenario repeated or returned an invalid job cursor")
            tokens.add(token)
            options["pagination_token"] = token
        raise AdapterError("Scenario job history exceeded the page limit")

    def _catalog(self, resource, privacy, max_pages):
        if privacy not in {"public", "private"}:
            raise ValueError("Choose public or private catalog visibility")
        if not isinstance(max_pages, int) or isinstance(max_pages, bool) or max_pages < 1:
            raise ValueError("Page limit must be a positive integer")
        result, identifiers, tokens = [], set(), set()
        options = {"privacy": privacy, "page_size": 100}
        if resource == "models" and privacy == "private":
            options["status"] = "trained"
        method = getattr(self._sdk, resource).with_raw_response.list
        for _ in range(max_pages):
            page = _json(self._request(method, **options))
            rows = page.get(resource)
            if not isinstance(rows, list):
                raise AdapterError("Scenario returned an invalid catalog page")
            for row in rows:
                if not isinstance(row, dict) or not isinstance(row.get("id"), str) or not row["id"]:
                    raise AdapterError("Scenario returned an invalid catalog record")
                if row["id"] not in identifiers:
                    identifiers.add(row["id"])
                    result.append(row)
            token = page.get("nextPaginationToken")
            if token is None or token == "":
                return result
            if not isinstance(token, str) or token in tokens:
                raise AdapterError("Scenario repeated a catalog cursor")
            tokens.add(token)
            options["pagination_token"] = token
        raise AdapterError("Scenario catalog exceeded the page limit")

    def model_page(self, *, privacy="public", page_size=100, pagination_token=None):
        """Read one bounded model page, preserving the response wrapper and cursor."""
        if privacy not in {"public", "private"}:
            raise ValueError("Choose public or private catalog visibility")
        if type(page_size) is not int or not 1 <= page_size <= 500:
            raise ValueError("Model page size must be an integer from 1 to 500")
        options = {"privacy": privacy, "page_size": page_size}
        if pagination_token is not None:
            if not isinstance(pagination_token, str) or not pagination_token:
                raise ValueError("Model cursor must be a nonempty string")
            options["pagination_token"] = pagination_token
        if privacy == "private":
            options["status"] = "trained"
        page = _json(self._request(self._sdk.models.with_raw_response.list, **options))
        rows = page.get("models")
        if not isinstance(rows, list):
            raise AdapterError("Scenario returned an invalid catalog page")
        if any(
            not isinstance(row, dict) or not isinstance(row.get("id"), str) or not row["id"]
            for row in rows
        ):
            raise AdapterError("Scenario returned an invalid catalog record")
        token = page.get("nextPaginationToken")
        if token is not None and not isinstance(token, str):
            raise AdapterError("Scenario returned an invalid catalog cursor")
        return page

    def models(self, *, privacy="public", max_pages=100):
        return self._catalog("models", privacy, max_pages)

    def workflows(self, *, privacy="private", max_pages=100):
        return self._catalog("workflows", privacy, max_pages)

    def prepare_model(self, model, parameters):
        """Return the exact (target, payload) `estimate_model` would quote; no request.

        A trained record is never a target: captured dry runs reject
        /generate/custom/{trainedId}, so LoRAs and compositions run only
        through a base model's `lorasComponent` inputs.
        """
        if model.get("type") != "custom" or model.get("parentModelId") or model.get("runs_as"):
            raise ValueError("Trained-model routing requires a verified REST schema contract")
        return _prepare("model", model, parameters)

    def prepare_workflow(self, workflow, parameters):
        """Return the exact (target, payload) `estimate_workflow` would quote; no request."""
        return _prepare("workflow", workflow, parameters)

    def estimate_model(self, model, parameters):
        return self._estimate("model", *self.prepare_model(model, parameters))

    def estimate_workflow(self, workflow, parameters):
        return self._estimate(
            "workflow",
            *self.prepare_workflow(workflow, parameters),
            loop_steps=workflow_loop_steps(workflow),
        )

    def estimate_prompt(self, parameters):
        """Quote a bounded Prompt Spark request through the public SDK method.

        This initial contract accepts mode, prompt, modelId, images and numResults.
        Scope and dry-run flags are adapter-owned, never payload fields.
        """
        if not isinstance(parameters, dict) or parameters.keys() - {
            "mode",
            "prompt",
            "modelId",
            "images",
            "numResults",
        }:
            raise ValueError("Unsupported Prompt Spark parameters")
        payload = copy.deepcopy(parameters)
        mode = payload.get("mode")
        if not isinstance(mode, str) or mode not in {
            "completion",
            "contextual",
            "contextual-v2",
            "image-editing",
            "inventive",
            "structured",
        }:
            raise ValueError("Choose a supported Prompt Spark mode")
        count = payload.setdefault("numResults", 1)
        if type(count) is not int or not 1 <= count <= 5:
            raise ValueError("Prompt result count must be an integer from 1 to 5")
        if "prompt" in payload and not isinstance(payload["prompt"], str):
            raise ValueError("Prompt must be text")
        if "modelId" in payload:
            _identifier(payload["modelId"])
        if "images" in payload:
            images = payload["images"]
            limit = 15 if mode == "contextual-v2" else 5
            if (
                not isinstance(images, list)
                or len(images) > limit
                or any(not isinstance(item, str) or not item.strip() for item in images)
            ):
                raise ValueError("Choose valid Prompt Spark image references within the mode limit")
        return self._estimate("prompt", "prompt", payload)

    def estimate_translate(self, parameters):
        """Quote English translation through the SDK, without a model fallback."""
        if (
            not isinstance(parameters, dict)
            or set(parameters) != {"prompt"}
            or not isinstance(parameters["prompt"], str)
            or not parameters["prompt"].strip()
        ):
            raise ValueError("Provide only the nonempty prompt to translate")
        return self._estimate("translate", "translate", dict(parameters))

    def _dispatch_generation(self, operation, identifier, payload, *, dry_run=False):
        options = {"dry_run": "true"} if dry_run else {}
        if operation == "translate":
            return self._request(
                self._sdk.generate.with_raw_response.translate, **payload, **options
            )
        if operation == "prompt":
            names = {"modelId": "model_id", "numResults": "num_results"}
            options.update({names.get(key, key): value for key, value in payload.items()})
            return self._request(self._sdk.generate.with_raw_response.prompt, **options)
        if operation == "model":
            method = self._sdk.generate.with_raw_response.run_model
        elif operation == "workflow":
            method = self._sdk.workflows.with_raw_response.run
        else:
            raise ValueError("Unsupported generation operation")
        return self._request(method, identifier, body=payload, **options)

    def _estimate(self, operation, identifier, payload, *, loop_steps=0):
        try:
            payload_json = json.dumps(payload, ensure_ascii=False, allow_nan=False).encode()
        except (TypeError, ValueError):
            raise ValueError("Parameters must contain finite JSON values") from None
        raw = self._dispatch_generation(
            operation, identifier, json.loads(payload_json), dry_run=True
        )
        result = _json(raw, exact=True)
        started, job_id = _started_job(result)
        if started:
            ESTIMATE_STARTED_JOB.set()
            raise EstimateStartedJob(job_id)
        cost = result.get("creativeUnitsCost")
        if isinstance(cost, bool) or not isinstance(cost, (int, Decimal)) or cost < 0:
            raise AdapterError("Scenario returned no valid exact estimate")
        estimate = Estimate(
            operation,
            identifier,
            self.project_id,
            Decimal(cost),
            payload_json,
            raw,
            self._scope,
            time.monotonic(),
            loop_steps,
        )
        with self._estimate_lock:
            self._estimates[id(estimate)] = estimate
        return estimate

    def owns_estimate(self, estimate):
        with self._estimate_lock:
            return (
                not self._closed
                and isinstance(estimate, Estimate)
                and self._estimates.get(id(estimate)) is estimate
            )

    def submit_estimate(self, estimate, *, before_send):
        """Coordinator-only dispatch: commit intent in before_send or raise.

        Claim and consume an issued quote once, including across coordinators.
        The hook orders persistence; it does not grant spending authorization.
        """
        with self._estimate_lock:
            if not self.owns_estimate(estimate):
                raise ValueError("Use an unchanged, unused estimate issued by this active client")
            if not callable(before_send):
                raise TypeError("A durable submission claim callback is required")
            if not self._online():
                raise AdapterError("Online access is disabled")
            before_send()
            del self._estimates[id(estimate)]
        raw = self._dispatch_generation(estimate.operation, estimate.target_id, estimate.payload)
        job = _json(raw).get("job")
        if not isinstance(job, dict):
            raise AdapterError("Scenario returned no submission receipt")
        try:
            _identifier(job.get("jobId"))
        except ValueError:
            raise AdapterError("Scenario returned no valid remote job identity") from None
        return job
