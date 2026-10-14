# SDK contracts for Studio adoption

The shared adapter for compact creation, expanded Studio and local MCP must use
`scenario-sdk`. The adoption baseline is **2.2.0**, inspected from the published
[PyPI release](https://pypi.org/project/scenario-sdk/2.2.0/). Its wheel is
`scenario_sdk-2.2.0-py3-none-any.whl`, with SHA256
`a1058ea5e41b6fadcdc399760ab22590409835ab7e66957fe719215c178779b6`.
The development dependency group pins this version and the MockTransport test
client, `httpx==0.28.1`; [uv.lock](../uv.lock) pins their transitive dependencies.

The SDK is pinned for development and packaged as a runtime dependency. The
[shared adapter](../scenario/core/api/sdk_adapter.py) uses it; the UI and local
MCP share SDK model listing/detail reads and model cost previews through
[SDKCatalog](../scenario/core/api/sdk_catalog.py), and cloud history uses the
same connection. UI and MCP model quote and submission use the selected shared
JobSession and the adapter's `models.retrieve` /
`generate.with_raw_response.run_model` contracts.
[The service operation inventory](#service-operation-inventory) maps each
operation to its SDK method. See [SDK_BUNDLE.md](SDK_BUNDLE.md) for exact
artifact/notice pinning, supported wheel targets, staging and installed-runtime
verification.

The development fixture recorder also uses this adapter for the twenty-four selected
model detail reads and one bounded public model-list page. `SDKAdapter.model_page`
returns a single validated response wrapper without exhausting its cursor; the
recorder retains that wrapper and reconstructs the detail `model` wrapper from
the adapter's record. Offline transport tests cover exact credentials/project,
page parameters, sanitization and failed-refresh behavior. This does not establish
live recording acceptance or ownership of existing fixture media; see the
[fixture inventory](../tests/fixtures/README.md).

## Service operation inventory

The source audit covers native UI, local MCP and the maintained development/smoke
entry points. The retired `ScenarioClient`, urllib service transport, raw
catalog cache, generation/job/asset/upload endpoints, Spark and LLM clients are
removed. No compatibility client or second job engine remains. Useful pure model
records, lane/schema helpers and local display status classification remain.

| Service operation | Shared implementation and selected SDK 2.2.0 method |
| --- | --- |
| Model catalog, schemas, connection check | `SDKCatalog` / `SDKAdapter`: `models.with_raw_response.list/retrieve`; [trained-model discovery](#trained-model-catalog-reads) adds `models.with_raw_response.get_bulk` |
| Exact model quote and submission, including render, Blockout and Film models | `JobCoordinator` / `SDKAdapter`: `generate.with_raw_response.run_model`, with `dry_run="true"` only for quotes |
| New/Rewrite and Translate | Shared prompt commands: `generate.with_raw_response.prompt/translate`, including separate exact estimates and submission claims |
| Cloud history, known-job recovery, polling, remote progress and inference cancellation | Shared catalog/coordinator: `jobs.with_raw_response.list/retrieve/trigger_action`; progress reads `Job.status` and `Job.progress` from the same polling retrieve, with no extra request; a [failure's reason](BLENDER_JOB_CONTEXT.md#failure-reasons) reads `Job.metadata.error` and `Job.metadata.hint` from it too |
| Waiting workflow step decisions (shared job command for the native review dialog and local MCP) | `JobCoordinator` / `SDKAdapter.workflow_decision`: `workflows.with_raw_response.user_approval`; selection uses the named [SDK issue #33 exception](#workflow-step-decisions); steps are read from the polling `jobs.retrieve`; while one waits, `SDKAdapter.workflow` (`workflows.retrieve`) reads the definition's step links once |
| Result and complete prompt/model-text metadata | Shared coordinator: `assets.with_raw_response.retrieve`; bounded complete text uses the signed result transport |
| Saved-result preview metadata | Shared coordinator on the preview lane: `assets.with_raw_response.get_bulk` for every batch, one asset included, so an omitted asset means the same at any size; stills and clips use the signed result transport ([result previews](RESULT_PREVIEWS.md)). Stills of a waiting workflow step's [review assets](RESULT_PREVIEWS.md#workflow-review-assets) use the same read and transport |
| Asset library browsing and text search | Shared coordinator: `assets.with_raw_response.list` and `search.with_raw_response.asset_search`; explicit single-page reads with selected project scope |
| Collection and tag organization (shared review commands, native Library and local MCP tools) | Shared organization commands / `SDKAdapter`: `collections.with_raw_response.list/retrieve/create`, `collections.with_raw_response.assets.add/remove`, `assets.with_raw_response.update_tags/get_bulk`; see [asset organization writes](#asset-organization-writes) |
| Reference upload metadata, progress and completion | Shared upload coordinator: `uploads.with_raw_response.create/retrieve/trigger_action` |
| Optional team/project discovery | Adapter-owned `SDKResourceExtensions`, the named [SDK issue #29 exception](https://github.com/scenario-labs/scenario-sdk-python/issues/29) below |
| Developer model audit, fixture recorder and smoke tools | The same `SDKAdapter`; smoke generation and explicit reference-plan uploads use shared coordinator commands. [Reference automation](../tests/smoke/README.md#prepare-reference-inputs) reuses the upload SDK methods above and signed-part transport. |

Workflow adapter primitives also use the pinned public SDK methods documented
below; their presence is not acceptance of a user-facing Film workflow service.
All paid SDK submissions retain `max_retries=0`, exact approval and durable claim
requirements.

Network protocols outside the Scenario service API remain distinct: guarded
result downloads and signed upload parts use `jobs/transfers.py` and
`jobs/upload_transfers.py`; model thumbnails use the credential-free downloader
in `api/assets.py`. Blender owns public update downloads. Local MCP's stdio bridge
uses authenticated loopback HTTP. None is an alternative Scenario service client.
The extension User-Agent lives in `api/user_agent.py`, so SDK and thumbnail
requests retain the same package version without importing a retired client.

Regression coverage follows the retained SDK catalog, adapter, coordinator,
upload, prompt-result and native entry points. Tests of the deleted client and
its retry/upload/partial-preview behavior are retired with it; complete-text
recovery continues to reject incomplete or unusable answers rather than falling
back to a truncated preview. Signed thumbnail byte, URL and retry tests remain.
This is a source and offline/native wiring inventory, not live provider, OAuth,
thumbnail transfer hardening or desktop acceptance. Desktop and live evidence is
in the [release acceptance record](maintenance/release-acceptance.md), and the
open checks are in #440.

## Executable contracts

Run the tests with:

```sh
uv sync --locked
uv run --locked python -m pytest tests/unit/test_scenario_sdk_contract.py -rx
```

[The tests](../tests/unit/test_scenario_sdk_contract.py) call public SDK methods
through `httpx.MockTransport`, with socket connections forbidden. All IDs,
credentials, payloads and responses are synthetic. `dry_run="true"`, `dry_run="api"` and omission for actual submission are tested without contacting Scenario or spending credits.
[SDK contracts CI](../.github/workflows/sdk-contracts.yml) repeats these checks
using the locked environment.

| Adapter requirement | SDK 2.2.0 contract exercised |
| --- | --- |
| Generic model estimate and submission | `generate.run_model`: POST, unchanged model-specific body, `dryRun` and `projectId` in the query |
| Workflow estimate and submission | `workflows.run`: PUT, unchanged workflow-specific body, `dryRun` and `projectId` in the query |
| Exact quote preservation | `generate.with_raw_response.run_model` retains JSON bytes for decimal parsing; this is a public SDK wrapper, not a custom endpoint call |
| Model, asset and job retrieval | `models.retrieve`, `assets.retrieve`, `jobs.retrieve`: project query and response wrappers, including unrecognized fields |
| Bulk asset previews | `assets.get_bulk`: POST with `assetIds` in the JSON body and `projectId` in the query; `thumbnail` and `preview` objects survive the raw wrapper and the parsed model, as they do for `assets.retrieve` |
| Remote job progress | `jobs.with_raw_response.retrieve`: raw JSON keeps a fractional `progress`, active `status` and `statusHistory`; the parsed `Job` exposes `progress` and `status` |
| Active UI/MCP model catalog | `models.with_raw_response.list/retrieve`: public privacy, opaque pagination cursor and original response fields; `status=trained` is used only for private model lists, as documented in the published wheel's `resources/models/models.py` |
| Trained-model discovery | `models.with_raw_response.list(privacy="private", status="trained")` keeps the filter, page size, cursor and project in the query; `models.with_raw_response.get_bulk`: POST `/models/get-bulk` with only `modelIds` in JSON, project in the query, original `models` bytes and parsed `uiConfig.lorasComponent`; a `models.retrieve` 403 or 404 is one status error |
| Multipart upload lifecycle | `uploads.create/retrieve/trigger_action`: project query, asset-option aliases, part URLs and processing/result fields; creation does not transfer bytes |
| Job discovery | `jobs.list`: `jobs` page wrapper, filters, comma-separated `types`, opaque cursor and project/filter preservation on the next page |
| Workflow approval decisions | `workflows.with_raw_response.user_approval`: one PUT with workflow, job and node identity and an explicit `approve` or `reject` action; `projectId` only when supplied, because `omit` drops it. Rejection stops a waiting step; it is not general workflow cancellation |
| Workflow selection gap | 2.2.0 has no `workflows.user_selection` ([#33](https://github.com/scenario-labs/scenario-sdk-python/issues/33)); the low-level `put` sends one PUT with the documented body and optional `projectId`, and a per-request `max_retries=0` holds even on a client configured to retry |
| Workflow step links | `workflows.with_raw_response.retrieve`: GET with `projectId` only when supplied; the raw JSON keeps each flow node's `dependsOn`, `inputs` references, `loopBodyNodeIds` and the workflow's `updatedAt`, including `user-selection` nodes that 2.2.0's typed flow model does not list |
| Remote cancellation | `jobs.trigger_action(action="cancel")`: POST action and project query; acknowledgements can remain in progress or report a completion race; upload/cancel failures make one attempt |
| Collection and tag writes | `collections.assets.add/remove`: PUT and DELETE with a JSON `assetIds` body; `collections.create`: POST `name`; `assets.update_tags`: PUT `add`/`delete` with explicit `strict=false`; `projectId` stays in the query. Timeouts, lost connections and 408/409/429/5xx make one attempt even with `x-should-retry: true`, and no idempotency header is sent. A refused add exposes its decoded JSON body, or the raw text, as `APIStatusError.body` |
| Collection pages and organization read-back | `collections.list/retrieve`: `collections` page wrapper, opaque cursor with unchanged scope and `collection` wrapper; `assets.get_bulk`: POST `assetIds` body, `projectId` query, `tags` and `collectionIds` retained, and a synthetic partial response passes through unchanged |
| Uncertain submissions | `max_retries=0` makes one attempt for model/workflow transport errors and retryable HTTP statuses, even with `Retry-After` |
| Redirect handling | An explicit HTTP client with `follow_redirects=False` prevents a second request; also use `trust_env=False` to avoid ambient proxy configuration |
| Authentication | Explicit Basic credentials take precedence over ambient Basic credentials; explicit Bearer precedence has the known failure below |

Synthetic responses intentionally cover partial and extended records. Passing
these tests proves serialization and parsing of those fixtures, not live endpoint
acceptance, complete schemas, remote cancellation or successful generation.

The active catalog captures the selected credentials on Blender's main thread,
uses the adapter's environment-isolated configuration and mirrors online
permission for workers. Reads reuse one HTTP pool per catalog connection;
retirement returns without waiting for network I/O, and the final reader closes
the pool. Late results cannot populate the replacement connection. Listing and
successful schema details are delivered progressively, so startup does not wait
for all curated schemas before exposing the catalog. Concurrent reads of the
same model share one request; a different selected model can finish independently.
Background delivery preserves unchanged quotes, while explicit model/mode/task
changes invalidate and re-arm pricing even if their schema is still loading or
requires a retry. Retiring credentials or resetting the active runtime clears
that pending intent along with the form caches.
Overlapping list refreshes also share one paginated read per connection and
privacy scope, with defensive copies for each caller. Failures release all
waiters without replacing the last complete cache; later explicit refreshes still
read the service. Every row must convert successfully before a list is cached;
malformed-record conversion failures become a sanitized `ScenarioError` shared
by the owner and waiters, so UI/MCP delivery reports `catalog_failed`.
Public/private reads remain separate, and retirement rejects
old results. Cache entries are in memory per connection; none persists or
crosses credentials. The active context also opens
[credential-bound local job storage](JOB_STORAGE.md#identity-and-ownership), and
its adapter carries the same scope. The local account pseudonym isolates records
without claiming a server identity or adding it to API requests. API-key requests
do not require explicit tenant selection or a generated SDK discovery method.
Opening the store does not activate shared job workers or paid submission.
[Runtime integration status](architecture/sdk-runtime.md#active-sdk-catalog) records
the remaining shared-job and paid-flow boundaries.
Pure model records, lane filters and schema hints remain in `api/catalog.py`; all
service model reads use the scoped SDK catalog.

The active **Test connection** operator also uses `SDKCatalog` and the adapter's
`models.with_raw_response.list` wrapper. It requests one fresh page with
`page_size=1`, without following a cursor or replacing the model cache. A worker
performs the read; the main-thread event handler accepts only the current
credential context and pending request. Concurrent clicks share that pending
check, and failures use sanitized SDK errors with no automatic retry. Success
proves only model access, including a valid empty list. It does not establish
account/project identity or resolve SDK issue #29.

The catalog preview worker remains a non-spending SDK helper using
`generate.with_raw_response.run_model(dry_run="true")`, captured nested inputs,
selected credentials and the connection's online-access gate. Its exact
`Estimate` object is an in-memory preview, not a durable approval. Missing or
invalid cost data is an error; explicit zero remains valid. A dry-run response
that carries a job raises `EstimateStartedJob` and issues no `Estimate`; see
[identical unconfirmed requests](JOB_COORDINATOR.md#identical-unconfirmed-requests). Native UI and MCP
model quotes use fresh metadata through the selected JobSession instead;
submission consumes that quote through the coordinator with the same raw SDK
estimate and no automatic retry.

## Trained-model catalog reads

The extension uses existing trained models: private LoRAs and compositions of
the selected credentials and project, plus public Scenario LoRAs. It does not train.
Lane lists are unchanged; the picker lists these records only under its Trained
chip (see [trained models in the native picker](#trained-models-in-the-native-picker)).

`SDKAdapter.models(privacy="private")` uses `models.with_raw_response.list` with
`privacy=private`, `status=trained`, page size 100 and the selected project on
every page. It follows cursors, keeps the first record of a repeated ID, fails
on a repeated cursor or the page limit, and rechecks online permission before
each page. The SDK documents `type` filters only for public lists, so
[catalog.trained_kind](../scenario/core/api/catalog.py) classifies records from
their REST fields:

| Kind | REST record |
| --- | --- |
| `lora` | A `*-lora` type, such as `flux.1-lora`, `flux.2-klein-4b-edit-lora`, `qwen-image-lora` or `zimage-lora` |
| `composition` | A `*-composition` type; SDK 2.2.0 declares `flux.1-composition` |
| `custom_private` | A `custom` record with no parent, training images or concepts that is private: its `privacy` field is `private`, or the selected scope's private model list returned it |
| `unsupported` | Any other type, such as `elevenlabs-voice`, `gpt-image-1` or `flux.1-pro`, a `type` that is not a string, or a `custom` record with a parent, training images or concepts |

A `custom` record without lineage that is neither marked private nor returned
by the private list has no kind. Offline tests cover every SDK 2.2.0 type
literal and fail when an SDK upgrade changes that set. `catalog.is_trained`
keeps `lora`, `composition` and `unsupported` records out of the base lanes and
the picker's base-model chips. For every type literal, privacy and lineage it
gives the same result as the former blanket exclusion, so lane lists and those
chips are unchanged. It is
a lane filter, not a trained-model test: it is also true for hosted base types
such as `flux.1-pro` and false for `custom_private`. Routing selects trained
models with `trained_kind` and `USABLE_TRAINED_KINDS` instead. A
private custom model stays an ordinary runnable model wherever records already
reach the lanes or picker, such as a saved selection or an MCP schema read. The
`custom_private` kind only describes it. `SDKCatalog.trained_models`
returns `(kind, record)` pairs: the scope's private trained list first, then
public LoRAs and compositions. It reuses each privacy list already cached on
the connection and reads only a missing list, or both on explicit refresh.

`SDKAdapter.models_bulk` uses the generated
[get-bulk method](https://docs.scenario.com/api/resources/models/methods/get_bulk):
POST `/models/get-bulk` with `modelIds` in JSON and the selected project in the
query. The reference states no batch limit and does not describe unknown or
inaccessible IDs, so the adapter sends at most 50 IDs per request and 200 per
call, rechecks online permission before each request, returns requested
identities in request order and omits absent IDs. Unrequested, malformed or
conflicting records fail the whole call without a partial result. The reference
directs readers of `inputs` to GET `/models/{modelId}`, so bulk records are
discovery summaries, not form schemas.

`SDKCatalog.get_many` reads each requested ID at most once per connection,
remembers IDs the service omitted, and shares pending reads between overlapping
callers. `refresh=True` skips cached summaries but joins a read of the same ID
already in flight. A request is checked with the adapter's identifier rules
before it owns any shared read, so an invalid ID fails only that request.
Summaries are cached apart from the model details used for forms and quotes,
never replace them, and are discarded on retirement. Catalog loading does not
start bulk reads; callers request them explicitly, which bounds read
amplification. `SDKAdapter.model` raises `AdapterUnavailable` for HTTP 403 or
404 with fixed text that names the status, and `SDKCatalog` keeps that status
on its `ScenarioError`. Form and MCP description reads show that text. The job
coordinator's metadata and quote reads call `SDKAdapter.model` directly and
receive the same text as an `AdapterError`, except a quote's reads of the models
a request names, which report a `RouteQuoteError` (see
[trained-model routes](#trained-model-routes)).

Every operation here is a public SDK 2.2.0 method, so there is no raw fallback,
SDK issue or dependency change. Offline transport tests establish serialization,
scope, caching and failure handling only. A zero-spend capture, described in
[trained-model REST contracts](#trained-model-rest-contracts), records which
base models declare `uiConfig.lorasComponent`, what list and bulk records carry
and whether a trained ID runs directly. The bulk batch limit above 50 IDs and
absent-ID behavior remain unverified.

## Trained-model REST contracts

[tools/capture_trained_contracts.py](../tools/capture_trained_contracts.py)
records how the service treats trained models, with reads and `dryRun=true`
quotes only, through the shared adapter: `models.with_raw_response.list`
(private trained and public lists), `models.with_raw_response.retrieve`,
`models.with_raw_response.get_bulk`, `assets.with_raw_response.list` (one image
of the scope for an image-to-image quote) and `generate.with_raw_response.run_model`
with `dry_run="true"`. Its probes use the adapter's request guard and configured
zero-retry client but issue no `Estimate`, so nothing they quote can be
submitted, and it never sends `ipDetection`, which is charged even in a dry run.
It quotes each accepted base-model route a second time through
`SDKAdapter.estimate_model`, the production quote path, and never submits that
estimate either. The sanitized results are committed under
`tests/fixtures/models/trained/` and pinned by
[offline contract tests](../tests/unit/test_trained_contracts.py); the
[fixture inventory](../tests/fixtures/README.md#trained-model-contracts)
describes recording and scrubbing. The recorded capture used SDK 2.2.0 and an
explicit API-key pair in its credential-bound scope, without a project override.

| Route kind | Request | Dry-run result |
| --- | --- | --- |
| LoRA stack | Base model with `loras` (`lorasComponent.modelInput`) and `lorasScale` (`scaleInput`) | Accepted: one and two `flux.1-lora` on FLUX.1 Dev, `flux.2-dev-lora` on FLUX.2 Dev, `flux.1-kontext-lora` on Flux Kontext with a reference image |
| Single LoRA by ID | Base model with `modelId` (`modelIdInput`) set to a LoRA | Accepted on FLUX.1 Dev |
| Composition | Base model with `modelId` set to a `flux.1-composition` | Accepted on FLUX.1 Dev, also for a composition with an unlisted concept LoRA and with extra `loras` |
| Direct trained ID | `POST /generate/custom/{trainedId}` | Rejected with HTTP 400 "Custom models only are supported for this endpoint" for `flux.1-lora`, `flux.1-composition` and `flux.2-dev-lora` |

What the reads show:

- A GET of a public LoRA or composition returns no `inputs` or `uiConfig` and
  reports `custom: false`. A trained record declares no runnable schema of its
  own; the base model's schema is the only REST contract for using it.
- Public list rows and get-bulk summaries carry neither `inputs` nor `uiConfig`,
  even for LoRA-capable bases, so finding `lorasComponent` needs a detail read
  of each candidate base.
- FLUX.1 Dev, FLUX.2 Dev, Flux Kontext, Qwen Image Edit 2511 and Z-Image declare
  `modelInput: loras`, a `model_array` with `modelTypes` and at most 6 items (1
  for Kontext), and `scaleInput: lorasScale`, a `number_array` with min 0, max 2,
  step 0.05 and no default. All but Kontext also declare `modelIdInput: modelId`,
  a single `model` input; only FLUX.1 Dev lists `flux.1-composition` there, and
  no captured base accepts a composition in `loras`. An uncommitted exploratory read
  found the same component on other FLUX.1, FLUX.2 Klein, Qwen Image and
  Z-Image Turbo bases.
- Public trained records are `flux.1-lora`, `flux.1-kontext-lora`,
  `flux.2-dev-lora` and `flux.1-composition` only, so the Z-Image and Qwen slots
  have no public LoRA to quote.
- The captured scope's private trained list was empty. Private LoRA and
  composition records, and their routes, are not captured; when present, the
  tool records only their REST types, schema presence and route statuses.
- Composition concepts are LoRA IDs with scales between 0 and 1. A concept LoRA
  that is not in the public list was still readable by ID.

The service's answers to validation probes on FLUX.1 Dev, compared with what
the client does before quoting (see [trained-model routes](#trained-model-routes)):

| Probe | Service dry run | Client before quoting |
| --- | --- | --- |
| LoRA type outside `modelTypes`, or a composition in `loras` | HTTP 400 naming the model and the allowed types | Refuses after a fresh read |
| Unknown model ID | HTTP 404 | Refuses after a fresh read |
| 7 LoRAs where 6 are allowed | HTTP 400 | Refuses |
| Strength 3 where the maximum is 2 | HTTP 400 | Refuses |
| Strength -0.5 where the minimum is 0 | Accepted | Refuses |
| Two LoRAs with one strength | Accepted | Refuses |
| LoRAs without `lorasScale` | Accepted; the strength the service then applies is unknown | Refuses: no base declares a default strength |

LoRA and composition selections did not change the FLUX.1 Dev quote, whose
`loras` input declares `costImpact: false`; the Kontext LoRA raised the Kontext
quote, as its `costImpact: true` declares. FLUX.2 Dev also declares
`costImpact: false`, but the capture has no FLUX.2 Dev quote without a LoRA to
compare. `estimate_model` quoted every accepted base-model selection at the
same price as the minimal request, after adding the schema defaults.

Routing may therefore offer LoRA stacks and single LoRAs through a base model's
`lorasComponent`, and compositions through its `modelIdInput`. Direct trained
IDs stay disabled and the `estimate_model` gate is unchanged. The service does
not enforce model types, existence, the strength minimum or strength alignment
in every case, and no base declares a default strength;
[trained-model routes](#trained-model-routes) records the client policy. A dry
run is not a paid run: result quality, how the service combines `modelId` with
`loras` (the schema text says `loras` overrides a composition's LoRAs) and
private-model behavior remain unverified.

## Trained-model routes

[trained_routes](../scenario/core/api/trained_routes.py) derives every route
from the base model's own REST record: `uiConfig.lorasComponent` names the
inputs, and their `modelTypes`, `maxLength`/`minLength` and scale `min`, `max`
and `default` bound the selection. Nothing comes from model names, remote-MCP
`run_with` metadata or a type table. The route kinds are the ones the
[captured dry runs](#trained-model-rest-contracts) accepted:

| Route | Payload | Client policy |
| --- | --- | --- |
| Stack | LoRA IDs in `modelInput`, one strength each in `scaleInput` | Every type in `modelInput.modelTypes`, within the item limits, each LoRA once, one strength per LoRA within the declared bounds |
| Composition | One composition ID in `modelIdInput` | Alone, without `modelInput`; its type in `modelIdInput.modelTypes`; each concept LoRA readable, trained and in `modelInput.modelTypes` |

Policies where the client is stricter than the captured service answers:

- **No invented strength.** A LoRA without a strength takes the scale input's
  declared default, or the request is refused with "provide a strength for each
  LoRA". No captured base declares a default.
- **A LoRA in `modelIdInput` is refused.** The service accepts it, but then
  applies a strength nobody chose; the message asks for the LoRA in the stack
  with a strength.
- **No composition with a stack.** The service accepts both, but its schema
  says `loras` overrides the composition's own LoRAs.
- **Direct trained IDs stay refused.** `SDKAdapter.estimate_model` still
  refuses a trained target, as the captured HTTP 400 answers require.

`forms.prepare_run` reads the LoRA inputs only from `uiConfig.lorasComponent`,
which `SDKAdapter` passes with each model or workflow schema
(`forms.record_schema`). A malformed component refuses only a request that sets
a model or number-array input, so a changed LoRA slot cannot block ordinary
generations. A selection made only by schema defaults, including a default a
form echoes back, is the service's own and is left unchanged. `SDKAdapter.prepare_model` and `prepare_workflow` return the
exact target and payload that `estimate_model` and `estimate_workflow` would
quote, without a request.

Before any dry run, `JobCoordinator._quote` prepares the payload, then reads
every model the caller chose in a `model` or `model_array` input (a value other
than empty or the schema default) again with `SDKAdapter.model` (`models.with_raw_response.retrieve`, selected
project in the query), composition concepts included. Reads are sequential,
deduplicated and bounded at 16 per quote, and the scene origin is rechecked
before each one. A deleted or inaccessible model (`AdapterUnavailable`, HTTP 403
or 404), an untrained one, a type the input does not accept, a LoRA in
`modelIdInput`, a composition whose concept LoRAs the stack would not accept,
or a strength policy failure raises `RouteQuoteError`, a `QuoteError`. So does
a chosen model in an input whose declared `modelTypes` is malformed, before any
reference read, rather than accepting every type. Item limits and strength
bounds stay form validation errors, as before. Nothing is
quoted, dispatched or persisted. Model and workflow quotes get the same
check; workflow `lorasComponent` inputs have no captured contract, so the check
only restricts them. The coordinator then quotes as before and refuses the
quote if its target or payload differs from the checked one. The durable intent
keeps the base model as target, and its payload hash covers the LoRA IDs,
strengths and composition ID.

`RouteQuoteError` text names input labels and positions, such as "Flux LoRA [2]
is not available to the selected credentials or project (HTTP 404)", never a
model ID, name or service reply. The sidebar estimate status shows it instead of
the generic price error, and local MCP `estimate_cost` returns it as the tool
error. The native picker and every form request build selections with `apply`
(see [trained models in the native picker](#trained-models-in-the-native-picker)),
and so do local MCP requests ([trained models through local MCP](#trained-models-through-local-mcp)).
A problem the listed records already show, such as a LoRA in `modelIdInput`, is
then refused before any request with the same text as a `RouteError`; local MCP
also refuses a listed composition named in the stack input instead of moving it
to `modelIdInput`. The fresh reads still catch what changed since the listing.

Every call here is an existing public SDK 2.2.0 method; there is no raw
fallback, SDK extension, SDK issue or dependency change. Offline tests compare
the policy with every captured dry run and exercise the coordinator through the
real SDK and a socket-free `MockTransport`. Private LoRAs and compositions, paid
results, the Z-Image and Qwen slots and workflow LoRA inputs remain unverified
against the service.

## Trained models in the native picker

The native catalog load also reads the selected connection's private trained list
with the existing `SDKAdapter.models(privacy="private")`
(`models.with_raw_response.list` with `privacy=private`, `status=trained` and the
selected project on every page), then pairs it with public LoRAs and compositions
through `SDKCatalog.trained_models`, reusing the public list the same load reads.
A failed private read is reported apart and never blocks the public lanes; the
picker's Trained chip or an explicit refresh reads it again.

List rows carry no `inputs` or `uiConfig`, and neither do the captured bulk rows,
so base-model slots come from description reads through the existing
`SDKCatalog.get` (`SDKAdapter.model`, `models.with_raw_response.retrieve` with the
selected project in the query), cached per connection. These reads start only when
the picker shows its Trained chip, never on a catalog refresh. One worker reads the
lane's base models whose descriptions are still unknown, in lane order, at most 400
per lane and connection, and reports progress every 10. One unreadable model is
skipped; three failures in a row stop the scan with the last sanitized reason. The
next Trained chip reads the models the scan did not reach, and an explicit refresh
lets it retry the failed ones. The pure
[trained_offers](../scenario/core/api/trained_offers.py) module pairs each trained
record with the detailed bases whose slot runs it; a LoRA also needs declared
strength bounds or a declared default. A newly chosen LoRA starts at the scale of
the first entry of its listed record's `concepts`, otherwise at 0.75, as the
Scenario web app does, clamped to the base's range; this reads no other record.
The captured public LoRA details carry `concepts` (scale 0.8), but the captures
do not record whether list rows carry it; a listed LoRA without it starts at 0.75.

A form keeps only the chosen IDs, kinds, strengths and a scope tag. Each request
rebuilds the payload with `trained_routes.apply`, and the unchanged quote check
reads every chosen model again before any dry run. Strengths are rounded to four
decimals, so Blender's 32-bit property storage does not change the payload. Local
MCP still takes the same inputs as base-model parameters; an installed test shows
that the form's payload and an MCP `estimate_cost` given those parameters dry-run
identical bytes.

Strength fields, project defaults and the composer label add no new Scenario
operation. Strengths stay within the range read from the base's description, and
a project default is checked with the same `apply` against the selected
connection's descriptions and trained list before it changes a form. When a
form's default names a base whose description is not loaded yet, the existing
model-description read loads it (`SDKAdapter.model`,
`models.with_raw_response.retrieve` with the selected project), at most once per
base and selection, never while one is pending and not again after a recorded
failure; the quote then reads every chosen model again as above. Defaults live in the local job store, not in the service.

Every call here is an existing public SDK 2.2.0 method; there is no new SDK method,
raw fallback, SDK extension, SDK issue or dependency change. Offline and installed
tests use the captured public fixtures and synthetic private records behind a
socket-free transport; private LoRAs and compositions remain unverified against
the service.

## Trained models through local MCP

Local MCP adds no Scenario operation for trained models. `list_models` with the
trained source starts the same reads as the picker's Trained chip: the private
trained list through `SDKAdapter.models(privacy="private")`
(`models.with_raw_response.list` with `privacy=private`, `status=trained` and the
selected project), read again only after a failed read, and the lane's unknown
base-model descriptions through `SDKCatalog.get` (`SDKAdapter.model`,
`models.with_raw_response.retrieve` with the selected project), once per
connection. `model_schema` reads a description the same way. A LoRA or composition
ID given as `model_id` makes no read when it is listed and one at most otherwise:
`generation.ensure_record` treats a listed trained model, or a description this
connection already read without inputs, as final instead of reading it again on
every call. `estimate_cost` rebuilds a base's LoRA inputs with
`trained_routes.apply` from the listed records before the unchanged quote: the
fresh reads above, then `generate.with_raw_response.run_model` with
`dry_run="true"`; `generate` submits through the same coordinator with
`max_retries=0`. A refused choice makes no request, including a composition named
in the LoRA input, which is refused rather than sent in the composition input.
`model_defaults` reads and writes only the local job store, and checks a new
default against the descriptions and trained list already read, starting one
missing base description read like a form's default does.

The same choice through the form and through MCP builds byte-identical payloads,
so the saved intent's `payload_sha256` and target match. Two client-side
representations changed for this: form decimal fields send the shortest decimal
their 32-bit storage holds, and MCP model requests start number parameters that
are not given at their declared default in the field's type, as a form does. The
service receives the same values as before in a different JSON spelling (`0.8`
rather than `0.800000011920929`, `0.0` rather than `0`). Environment lane
requests, which have no form, keep their panorama contract's lane defaults
instead.

Every call is an existing public SDK 2.2.0 method; there is no new SDK method, raw
fallback, SDK extension, SDK issue or dependency change. Installed tests use the
captured public fixtures and synthetic private records behind a socket-free
transport.

## Model acceptance commands

All model smoke entry points use `tools.smoke_image` as their shared engine;
`tools.smoke_model` selects Image, Material, Video, GLB, audio, panorama or HDRI
result checks.
Fresh metadata uses SDK 2.2.0 `models.with_raw_response.retrieve`, exact estimates
and one paid submission use `generate.with_raw_response.run_model`, and saved-job
polling/download metadata use `jobs.with_raw_response.retrieve` and
`assets.with_raw_response.retrieve`. The shared coordinator retains the existing
query-only dry run, exact Decimal cost, selected credentials/project and disabled
submission retries. No raw fallback or SDK dependency change is added.

The quote digest includes the expected result kind. Material quotes additionally
retain the exact normalized SDK payload, binding selectable map roles, schema
defaults and output count to the approval and saved job digest. Missing requested
maps fail submission-result and recovery checks without regenerating. Old material
quotes can recover results for inspection but cannot establish map completeness
or authorize new submission; unsubmitted runs need a fresh quote.
Offline SDK-transport tests
exercise every kind, uncertainty, cost/schema drift, scope changes and receipt
recovery. Result checks use verified nonempty bytes, MIME metadata and known
texture roles; they do not establish decoding, local reference upload, Film,
native interaction, live provider acceptance or a protected aggregate CI budget.
See [the command reference](../tests/smoke/README.md).

The `panorama` and `hdri` kinds check the saved `projection` and `source` fields
from the same `assets.with_raw_response.retrieve` metadata, then the bounded
2:1 container preflight on the verified files; `hdri` requires a saved OpenEXR
original. They add no SDK call or dependency. Offline tests use synthetic PNG,
JPEG and OpenEXR headers, not live skybox output.

[Panorama contract cases](../tests/smoke/README.md#panorama-contract-cases) for
the five Environment lane models read the model's current record with the same
`models.with_raw_response.retrieve`, through `SDKAdapter.model`, before the dry
run and again after claiming the submission attempt, and apply the shared
`panorama_contract.prepare` to it. Quotes and the single submission still use
`generate.with_raw_response.run_model` through the shared coordinator.
Saved panorama inputs and the
[advisory report](../tests/smoke/README.md#advisory-panorama-report) verify local
saved receipts with an adapter that refuses online access; they make no SDK call.

## Shared model text reads

The shared coordinator's [model text reader](JOB_COORDINATOR.md#model-text-recovery)
uses the same pinned SDK `jobs.with_raw_response.retrieve` and
`assets.with_raw_response.retrieve` methods. The selected model output must occur
in `metadata.assetIds`; its asset must report top-level `kind="text"` and
`mimeType="text/plain"`. `properties.hasFullPreview` controls whether the preview
is complete; otherwise the existing credential-free bounded storage downloader
reads the full body. These fields are established by the published 2.2.0 types
and the [asset API contract](https://docs.scenario.com/api/python/resources/assets/methods/retrieve).
No SDK extension, new raw API exception, dependency or retry change is needed.
This read-only command does not establish live provider acceptance.

### Active Blockout plans

Blockout Design and Refine use the selected JobSession through
[BlockoutJobs](../scenario/blender/blockout_jobs.py). Fresh model metadata uses
`models.with_raw_response.retrieve`; the exact quote and single paid submission
use `generate.with_raw_response.run_model` for `model_scenario-llm`, with the
existing query-only dry-run and no-retry policy. Job and complete text reads use
the methods above. UI and MCP share this route; there is no endpoint exception,
SDK pin change or parallel paid worker. API keys retain credential-bound server
scope without requiring discovery or a project override.

Offline SDK transport fixtures and installed native tests establish command
wiring and scope/origin guards. Saved-plan recovery reuses these same metadata
and complete-text reads through the selected JobSession, followed by explicit
local destination approval. No additional SDK method or exception is used.
Live model compatibility and release acceptance remain separate.

## Active history reads

UI refresh/load-older and MCP `list_generations` use the same connection and
worker queue. `SDKAdapter.job_page` calls the pinned SDK's
`jobs.with_raw_response.list` once with `hide_results=False`, a bounded page size
and the opaque cursor; its response preserves unknown fields while validating
job IDs and the next cursor. The existing adapter supplies selected credentials,
project context when configured and online permission. There is no raw fallback.

Prompt previews use `assets.with_raw_response.retrieve` on the captured connection,
bounded to 30 per page; step jobs that price a workflow run but are missing from
the page use `jobs.with_raw_response.retrieve`, bounded to 24 per page. Failed
lookups and explicitly incomplete previews stay
unresolved, and no prompt cache crosses requests or connections. Main-thread
delivery rejects stale credentials, superseded requests and cursor cycles before
changing the visible history. MCP can explicitly retry with `refresh=true` and
read the next page with `older=true`, which uses the same cursor as **Load older**.
Job types are filtered locally after each page: the SDK's `type` filter returned
no rows when combined with `hide_results=False`.
These reads do not activate durable recovery, project-selection UI, downloads,
submission or result application. See the
[runtime history boundary](architecture/sdk-runtime.md#active-sdk-history).

## Completed cloud job adoption

The [shared adoption command](JOB_COORDINATOR.md#adopting-a-completed-cloud-job)
uses SDK 2.2.0 `jobs.with_raw_response.retrieve` through `SDKAdapter.job`.
The published wheel and [job retrieval reference](https://docs.scenario.com/api/python/resources/jobs/methods/retrieve)
establish GET `/jobs/{jobId}`, optional `projectId` and the `job` response wrapper.
The adapter preserves the original metadata used to verify the selected model
and output identities. Existing selected credentials, permission checks and
sanitized errors apply; no new raw fallback or SDK extension is needed.
Synthetic transport/native checks do not prove live provider acceptance.

## Upload and job operation boundaries

The operation audit uses the published wheel's `resources/uploads.py`,
`resources/jobs.py`, `resources/workflows.py`, their generated parameter/response
models and `pagination.py`. The adapter exposes bounded job discovery as
described below; inference cancellation is exposed through the coordinator,
and multipart upload metadata commands are mapped in
[SDK_UPLOADS.md](SDK_UPLOADS.md).

| Operation | Request and response shape |
| --- | --- |
| Begin multipart upload | `uploads.create`: POST `/uploads`; `projectId` query; `kind`, `fileName`, `contentType`, `fileSize`, `parts` and optional `assetOptions` body; response wrapped in `upload` |
| Poll upload | `uploads.retrieve`: GET `/uploads/{id}` with `projectId`; `upload.status`, `jobId`, `entityId`, `errorMessage` and extension fields remain accessible |
| Finalize upload | `uploads.trigger_action(action="complete")`: POST `/uploads/{id}/action`, action body and `projectId` query; response remains an `upload`, potentially still `validating` |
| Discover jobs | `jobs.list`: GET `/jobs`; supports `authorId`, `workflowId`, `status`, `type` or comma-separated `types`, `hideResults`, `pageSize`, `paginationToken` and `projectId`; `jobs` array plus `nextPaginationToken` |
| Retrieve known job | `jobs.retrieve`: GET `/jobs/{id}`, `projectId` query, `job` wrapper |
| Request inference cancellation | `jobs.trigger_action(action="cancel")`: POST `/jobs/{id}/action`, `projectId` query, `job` wrapper |
| Decide workflow approval | `workflows.user_approval(action="approve" or "reject")`: PUT `/workflows/{id}/user-approval`, `projectId` query (typed required), `nodeId`/`workflowJobId`/`action` body, `job` wrapper; an omitted action approves |
| Decide workflow selection | Absent from 2.2.0. The [API reference](https://docs.scenario.com/api/resources/workflows/methods/user_selection) documents PUT `/workflows/{workflowId}/user-selection`, a `projectId` query marked required, `action` (`select` or `reject`), `nodeId`, `workflowJobId` and, for `select`, ordered zero-based `selectedIndices`; `job` wrapper |

### Uploads and signed storage

The SDK returns numbered upload parts with URLs and expiry values; creating an
upload makes only the Scenario API request. The inspected resource has no byte
transfer or upload-abort method. The generated completion parameter is
`Literal["complete"]`, while its docstring says `"upload-complete"`. Tests record
the literal's serialization, and the live service accepted it with a `validating`
acknowledgement. Only `create` returns the signed part plan; retrieval and the
action omit it, as described in the [upload contract](SDK_UPLOADS.md#inputs-and-responses).
Do not silently substitute the docstring value or invent an abort endpoint.

The [upload components](SDK_UPLOADS.md#shared-worker-commands) stage private
source snapshots, check signed destinations and expiry, persist mutation claims
and send bounded parts through a separate storage transport that checks online
permission, sends no Scenario Authorization header and never follows redirects
or retries implicitly. Saved records contain no
signed transfer URLs. Explicit retrieval of a known upload observes its status
without replaying a mutation; a completion acknowledgement alone does not
establish that an asset is imported. The
[JobSession facade](BLENDER_JOB_CONTEXT.md#upload-references) and
[active reference uploads](SDK_UPLOADS.md#active-reference-uploads) own the UI
and MCP controls and their recovery. Server-side cleanup and live service
acceptance remain unverified.

The [signed result transport](RESULT_TRANSFERS.md) downloads results with an
exact trusted-host policy, bounded reads and atomic non-overwriting output; the
coordinator binds it to scoped SDK job/asset metadata, persisted download state
and explicit retries with fresh URLs.

### Reconciliation and cancellation

Job records retain `jobId`, `jobType`, status and metadata such as inputs,
produced asset IDs, non-asset output and workflow/job relationships. The tests
exercise explicit next-page requests with unchanged project and filters. The
adapter implements bounded discovery through `SDKAdapter.jobs`: explicit page
requests, project and filters on every page, online permission before every request,
loop detection and a hard page limit. Exact duplicate IDs are deduplicated;
conflicting records with the same ID fail the listing so callers can refresh.
Malformed pages and later-page errors never return a silent partial result.
Discovery omits embedded results by default (`hide_results=True`); callers can
explicitly request them with `False`. This option stays unchanged on every page.
Job IDs use the same validation as retrieval, so malformed or padded IDs fail
the listing. Unknown response fields/statuses are preserved. Listings are not server snapshots;
retrieve a known remote ID again before acting on its state.

The inspected SDK has no dedicated lookup by client request identity or explicit
idempotency parameter for model/workflow submission. Generic extension parameters
do not establish server support for either. A listing of similar inputs is only
a set of candidates, not proof of which job belongs to an uncertain submission.
Persist scope and request identity before dispatch; poll a known remote ID, but
keep a lost-ID submission uncertain unless authoritative correlation is available.
Never resolve an empty or ambiguous listing by automatically submitting again.

The job action documentation limits cancellation to inference jobs ("Today only
cancel on inference jobs is supported", in both the public API reference and the
pinned `jobs.trigger_action` docstring). The captured model-generation record in
`tests/fixtures/patina-copper-512/job.json` uses `jobType=custom`; the pinned
retrieve-response enum lists `custom` and `inference` as distinct types. The
coordinator therefore accepts only a fresh `inference` observation for persisted
model operations, with a durable `cancel_requested` claim before its single
action, and refuses `custom` and every other type before any claim or request.
Native and MCP controls offer cancellation only after such an observation. A
contract test checks the pinned docstring and enum so an SDK upgrade that widens
the limit is reviewed. No general workflow-cancel method appears in the
inspected workflow resource. Rejecting a waiting user-approval or user-selection
step is the only documented way for a client to stop a workflow. The SDK
documents a loop-scoped effect for a rejected approval inside a ForEach
iteration, so callers must trust the refreshed job status. Rejection cannot stop
an executing step and must not be presented as general cancellation. A response
may still be `in-progress` or
already `success`; the SDK preserves it without forcing `canceled`. Live
cancellation of an `inference` job and rejection inside a loop continue in #440;
the coordinator tests completion races and known-ID restart polling offline.
Record a sanitized upstream SDK issue before any fallback for these boundaries;
these upload/job operations introduce no raw calls or fallback and claim no live
service failure. Discovery uses the separate exception below.

## SDK resource extensions

Start with the [Python SDK documentation](https://docs.scenario.com/api/python)
and the exact published wheel. When the SDK docs or generated methods do not
cover an operation, consult the [API reference](https://docs.scenario.com/api)
and its endpoint details. Confirm the request/response contract before extending
the adapter; missing SDK coverage does not mean missing API capability.

API-key requests use the server's credential-bound scope. Callers can omit
`teamId` and `projectId`; discovery and project selection are not prerequisites
for estimates or submission. OAuth's explicit tenant-selection requirements are
a separate concern, deferred for this release. Local durable jobs must still be
isolated when credentials or an optional project override change. A local
credential identity must not be presented as a server-reported account identity.

The selected SDK 2.2.0 has no generated `teams` or `projects` resource, tracked in
[SDK issue #29](https://github.com/scenario-labs/scenario-sdk-python/issues/29).
The API operations already used by Scenario MCP can be exposed without waiting
for SDK regeneration. [SDKResourceExtensions](../scenario/core/api/sdk_extensions.py)
holds named methods inside the shared adapter boundary, using the adapter's
existing SDK client and HTTP pool:

| Adapter method | Narrow SDK fallback | Query contract |
| --- | --- | --- |
| `teams()` | `Scenario.get("/teams", cast_to=httpx.Response)` | No team/project query, including when the adapter has a selected project. |
| `projects(team_id)` | `Scenario.get("/projects", cast_to=httpx.Response)` | Only the explicitly requested `teamId`; never inherit `projectId`. |
| `workflow_user_selection(workflow_id, body=...)` | `Scenario.put("/workflows/{workflowId}/user-selection", cast_to=httpx.Response)` with per-request `max_retries=0`; [SDK issue #33](https://github.com/scenario-labs/scenario-sdk-python/issues/33) | The adapter's project override as `projectId` when configured; otherwise no query, as for workflow runs. See [workflow step decisions](#workflow-step-decisions). |

These are explicit raw endpoint exceptions, not generated resource methods.
They retain the SDK's configured credentials, base URL, timeout, zero retries,
redirect policy and adapter online/lifetime checks and sanitized errors. For
discovery, the adapter validates the named list and each record's ID while
preserving unknown fields and the full response wrapper. Each discovery method
returns one response; no exhaustive pagination contract is inferred. Neither
selects the first project, changes adapter scope, nor claims that listed
projects identify the key's default project. Nested metadata remains unvalidated
service data.

Team discovery is optional. In the OAuth flow the backend may provision a
personal team/default project for a user without teams during `GET /teams`, so
do not describe that route as universally side-effect-free or add an automatic
onboarding probe. These methods are not yet called by the active Blender UI.

To add another missing method, inspect the pinned SDK, reproduce the gap, link
an upstream issue, then add a named extension with a verified endpoint/body/query
contract and offline transport tests. Keep generated SDK methods for operations
already covered. Do not expose an arbitrary-URL bypass to UI, jobs or local MCP.
Replace each fallback when the selected SDK provides an equivalent method and
its contract tests pass; the dependency tests flag newly available discovery
resources and `workflows.user_selection` for that review. No dependency upgrade
is required for this layer.

[Extension tests](../tests/unit/test_sdk_extensions.py) cover selected Basic and
Bearer credentials despite conflicting environment values, stale-project
discovery, permission/lifetime checks, malformed data and single-attempt errors,
including the selection fallback on an SDK client configured to retry.
They also exercise API-key estimate/submission without discovery or tenant IDs,
and the workflow decisions below. These synthetic checks and the installed-bundle
test do not claim live service acceptance.

### Workflow step decisions

`SDKAdapter.workflow_decision` answers one waiting workflow step. A
`user-approval` step accepts `approve` or `reject` through the generated
`workflows.with_raw_response.user_approval`. A `user-selection` step accepts
`select` or `reject` through `workflow_user_selection`, because SDK 2.2.0 lacks
the generated method. The [API reference](https://docs.scenario.com/api/resources/workflows/methods/user_selection)
documents that route and SDK `main` already has it, but no published release
does ([SDK issue #33](https://github.com/scenario-labs/scenario-sdk-python/issues/33)).
Remove the extension when the pinned SDK provides `workflows.user_selection` and
these contracts pass with its raw-response wrapper.

- **Request shape.** Workflow and job IDs use the adapter's path-safe identifier
  rules; the selection path uses the same segment encoding as the generated
  approval route. Node IDs travel only in the JSON body, so they need only be
  printable text of at most 1,024 characters without surrounding whitespace;
  the format of loop-iteration node IDs is not documented. All three IDs must
  pass Python's `str.isprintable`, which rejects control and format characters,
  such as bidi overrides, and lone surrogates before any request is built. The
  action is always explicit, because an omitted approval action approves.
- **Selection.** A selection sends 1 to 100 unique nonnegative integer indices,
  in the caller's order, which the node output preserves. The cap is the API
  reference's default maximum, and indices stay within the exact JSON integer
  range. The step's own min/max bounds and candidate count remain the caller's
  check.
- **Scope.** Both endpoints document `projectId` as required, and SDK 2.2.0
  types it as required for approvals. A decision uses the scope of the run it
  answers: the adapter's explicit project override when configured, otherwise
  API-key credential-bound scope with `projectId` omitted, as for `workflows.run`.
  Omission for these two endpoints is not yet live-verified. If a key needs the
  explicit project, decisions must require a project override instead.
- **One attempt.** A decision can resume paid steps or stop the workflow, so it
  is never retried, including after `Retry-After`. `AdapterStatusError` carries
  the HTTP status of a service reply with the adapter's sanitized status text,
  separating it from a lost response. A 403 names the Project ID only when the
  decision sent the project override. Callers decide which statuses are
  definitive refusals. A `ValueError` means nothing was sent. Treat every other
  `AdapterError` as an unknown outcome until the job is retrieved again: offline
  and closed-client refusals happen before sending, but share that type with a
  lost response and a malformed or mismatched acknowledgement. The status error
  keeps its status and text through `copy` and `pickle`.
- **Acknowledgement.** The returned `job` must name the same workflow job. It is
  not terminal-state evidence and may carry signed asset URLs, which callers must
  not persist or log.

This adapter primitive grants no spending or decision authority. The
[workflow job command](JOB_COORDINATOR.md#waiting-workflow-step-decisions) calls
it once per reviewed step, after retrieving the job and checking the step again.
That command treats an HTTP 4xx reply as a refusal and every other failure as an
uncertain outcome, stores no decision record and blocks choosing items for a
selection that feeds a ForEach loop or nested workflow, or whose links cannot be
read from the job or its definition in a flow that has one. The native review
dialog and local MCP `decide_workflow_step` call it through `ModelJobs.decide`.
Offline contracts do not establish live endpoint acceptance.

## Known authentication failure

[SDK issue #26](https://github.com/scenario-labs/scenario-sdk-python/issues/26)
tracks ambient Basic credentials overriding explicitly selected Bearer auth.
The expected behavior is an executable **strict expected failure**: the suite
reports it separately, and an unexpected pass fails CI so an SDK update requires
reviewing and removing the marker. This is an unresolved adoption blocker, not
accepted account-switching behavior in the unconfigured SDK.

The dependency tests clear SDK environment variables only to isolate fixtures.
The adapter does not edit process-wide environment variables. It configures all
credential arguments and the API URL explicitly, owns an HTTP client with
`trust_env=False` and `follow_redirects=False`, and overrides the SDK's public
`default_headers` property with adapter-owned headers including the selected
Authorization value. This configuration avoids ambient Basic/header overrides;
it is not a raw endpoint fallback. Adapter contracts leave conflicting ambient
values in place and verify both Basic and Bearer requests. Keep the upstream
expected failure until the SDK itself fixes #26, and remove the configuration
workaround only after a verified environment-isolation interface replaces it.
Token serialization does not establish browser OAuth acceptance by REST.

## Adapter coverage

The adapter provides reads/estimates, a coordinator-only submission hook and
unpaid [asset organization writes](#asset-organization-writes) used only by the
shared organization review commands.
The [job coordinator](JOB_COORDINATOR.md) commits a scoped intent before dispatch,
consumes each issued quote once and preserves uncertain outcomes. UI and MCP model
submission consumes session-owned quotes and persists intent before the adapter
hook; its result path uses `jobs.retrieve` and `assets.retrieve` from the same
adapter, followed by credential-free CDN transfer. Saved-job UI/MCP controls
expose known model-job cancellation and download recovery through the same
coordinator. Applying, importing or reusing a saved result in the scene uses
verified local receipts and a fresh local approval, with no Scenario call; see
the [Blender job context](BLENDER_JOB_CONTEXT.md#active-saved-job-controls).
Generated operations use public SDK methods
with `max_retries=0`; their `with_raw_response` wrappers preserve wire JSON.
The named discovery and selection exceptions also use the same zero-retry SDK
client; the selection request also disables retries on its own options.

| Adapter operation | SDK 2.2.0 method and contract |
| --- | --- |
| Public/private model catalog | `models.list`: explicit page size/status/privacy, `paginationToken`, scope on every page, deduplication and cursor-loop/page-limit failures |
| Bulk model summaries | `models.get_bulk` through its public raw-response wrapper: at most 50 IDs per request and 200 per call, requested identities only, identical duplicates merged, conflicts rejected, scope and online permission on every request, no partial result; see [trained-model catalog reads](#trained-model-catalog-reads) |
| Inaccessible model detail | `models.retrieve`: HTTP 403 or 404 raises `AdapterUnavailable`, an `AdapterError` carrying the status and fixed text; other reads and statuses keep the generic error |
| Public/private workflow catalog | `workflows.list`: SDK REST catalog replaces the need for Studio's public-workflow HTTP bypass; pagination and scope are tested synthetically |
| Known model-job cancellation | `jobs.trigger_action(action="cancel")` through its public raw-response wrapper: one attempt, selected project, no terminal-state assumption from acknowledgement; coordinator retrieves before and after the action |
| Waiting workflow step decision | `workflows.user_approval` through its public raw-response wrapper, or the named [selection extension](#workflow-step-decisions): explicit action, one attempt, project override only, status-carrying errors and a matching `job` acknowledgement; called only by the workflow job decision command |
| Scoped job discovery | `jobs.list` through the public raw-response wrapper: optional author/workflow/type/status filters, 1–200 items per page, bounded pagination and explicit errors instead of partial or conflicting history |
| Multipart upload metadata | `uploads.create/retrieve/trigger_action(action="complete")`: immutable project scope, strict input/receipt identity, retained processing/future fields; no byte transfer, retry or automatic completion |
| Collection and tag writes | `collections.create`, `collections.assets.add/remove` and `assets.update_tags(strict=false)` through public raw-response wrappers: one attempt, selected project, acknowledged identity checks, and `WriteRejected` (`AlreadyMembers` for the exact already-member add refusal) or `WriteUncertain` after dispatch |
| Collection pages and organization read-back | `collections.list/retrieve` and `assets.get_bulk`: one bounded collection page with cursor-loop failure, matching collection identity, and requested-only asset records whose `tags` and `collectionIds` must be lists of strings |
| Model/workflow/asset/job records | `models.retrieve`, `workflows.retrieve`, `assets.retrieve`, `jobs.retrieve`: unwrap the named record and retain unknown fields |
| Known asset records in bulk | `assets.get_bulk` through its public raw-response wrapper: 1 to 100 distinct validated identities, selected project, one attempt; omitted assets stay absent, while unrequested or conflicting rows fail. `network_allowed()` reads the same online predicate without a request |
| Custom-model estimate | `generate.run_model(dry_run="true")`: adopted form value validation plus retained conditional/one-of rules; inputs in JSON and dry-run/project in query |
| Prompt translation quote/submission | `generate.with_raw_response.translate`: POST `/generate/translate`, exact `dry_run="true"` response; optional selected `project_id` in the query; prompt in JSON; no raw API fallback |
| Prompt Spark quote/submission commands | `generate.with_raw_response.prompt`: POST `/generate/prompt`, `dry_run="true"` and optional selected `project_id` in the query; explicit mode, prompt, modelId, images and numResults in JSON; no raw API fallback |
| Workflow estimate | `workflows.run(dry_run="true")`: normalize workflow fields/defaults and preserve the same query/body boundary |
| Exact estimate record | Keep immutable request/response bytes and a `Decimal` cost, including zero; reject absent, negative, nonnumeric or non-finite costs rather than inventing a free estimate |

Each client owns an immutable selected project and connection scope. Estimates
from a different client fail `owns_estimate`, including a recreated client for
the same account. Only the original issued object is accepted; copies and consumed quotes fail
ownership checks. The coordinator binds it to persisted request/origin identity;
neither mechanism grants spending authorization. Online permission is
checked before every request, including every catalog page. Blender callers must
supply a predicate reflecting their actual online-access permission.

Custom-model records must explicitly declare `type=custom`; a trained model is
never the quoted target and runs only through a base model's
[trained-model routes](#trained-model-routes).
The adapter uses the same pure payload preparation as form callers: validate
explicit input types first, merge actual defaults and mandatory routing, then
validate the final values and each conditional/either-or clause. Overlapping
clauses remain separate requirements. Malformed schema names and route IDs fail
before SDK dispatch, without coercion. Captured custom-model fixtures exercise
this shared path, including Minimax frame dependencies and Rodin prompt/image
alternatives.

A conditional rule can name a sibling input that the schema does not declare.
The service validates only the inputs it declares, and a public schema can omit
a hidden input that a rule still names. The plugin never sends an input the
schema does not declare (it is refused as an unknown parameter), so such a
sibling is never defined in its requests. `parse_schema` therefore treats it as
never defined: `ifDefined` never applies, and `ifNotDefined` makes the input
always required. Native panels, local MCP `model_schema` and validation, strict
form preparation and SDK estimation all use that result, so the schema is priced
instead of refused. The captured Seedance 2.0 Mini record is the regression:
its first-frame rule names an undeclared last-frame input, so the first frame is
optional. Known sibling relationships remain enforced in every path. A rule that
is not an object, or whose names are not nonblank strings, still fails strict
preparation before dispatch; the native panel drops blank names so the
description can still render. This assumes the hidden input has no default; if
one did, the dry run would report the service's error and nothing would be
spent.

The pure LoRA/composition routing helper retains required base-model wiring for
its synthetic remote-MCP tests; strengths follow the policy in
[trained-model routes](#trained-model-routes), so a wired LoRA without one is
refused rather than given 1.0. That projection and its composition tests do not
establish REST routing, catalog discovery, model selection UI or paid
execution. Remote-MCP `run_with` metadata is not silently assumed to exist in
REST.

Reference uploads from forms, captures, workflow inputs and local MCP use
`uploads.with_raw_response` `create`, `retrieve` and
`trigger_action(action="complete")` through the same adapter, followed by
credential-free signed S3 PUTs. They support the published SDK kinds `image`,
`audio`, `video` and `3d`, with extension-specific MIME metadata; `3d` is asset
upload, not the SDK `model` kind used for model import. The
[active upload contract](SDK_UPLOADS.md#active-reference-uploads) owns
destination trust, source limits, attachment and recovery. Render generation
uses the shared model quote and submission path; Render Spark preparation has a
separate exact-price approval. The
[Render Video first-frame handoff](BLENDER_JOB_CONTEXT.md#saved-image-to-render-video-first-frame)
adds no Scenario operation: it places a saved result's existing asset ID in the
Render Video first-frame target, and the existing
`generate.with_raw_response.run_model` dry run and submission send it like any
other asset ID. That dry run resolves asset IDs but does not check which inputs
may be combined, so the shared form preparation refuses two file inputs whose
descriptions say they can't be combined, before any quote. Settings described that
way are not checked. This is local validation, not an SDK gap. Authoritative
account/project discovery remains integration work; live acceptance remains separate.

Run the adapter and command contracts offline with:

```sh
uv run --locked --no-env-file python -m pytest tests/unit/test_sdk_adapter.py tests/unit/test_check_sdk.py
```

The explicit live command is documented in
[CONTRIBUTING.md](../CONTRIBUTING.md#live-commands). Its existence and synthetic
tests do not claim live service acceptance or authorize a paid operation.

## Source baseline and remaining work

### Selected SDK 2.2.0 upgrade

The inspected 2.2.0 wheel retains the same required dependency closure and
byte-identical MIT notice as 2.1.0. Client/authentication and transport sources
are unchanged, so both the #26 header workaround and #29 discovery extensions
remain necessary. 2.2.0 also lacks `workflows.user_selection`, so the
[#33 selection extension](#workflow-step-decisions) is required too; the
dependency tests flag each missing method for review on upgrade. Resource/type
updates include stricter query annotations and additional model/job metadata;
raw-response parsing preserves those fields.

Model and workflow `dry_run` now declare `"true"` or `"api"`. The adapter uses
`"true"` for existing estimates and omits the parameter for actual submissions,
rather than passing booleans outside the declared type. Dependency contracts
exercise both estimate values and omission, and verify unchanged payload,
project-query, zero-retry, pagination, upload and cancellation behavior. The
optional `ip_detection` preflight is not enabled; its documented dry-run fee
must not silently become part of a cost preview. Other new SDK operations are
not automatically adopted by upgrading the bundle.

### Studio source

The selected Studio candidate is
[`204c847ad6f999e1050070a35e58af666b28ff9c`](https://github.com/edemaistre/scenario-blender-studio/tree/204c847ad6f999e1050070a35e58af666b28ff9c),
version 0.2.0, one squashed commit after the 0.1.5 revision the capability
decisions were first made against. See [the source and capability inventory](STUDIO_ADOPTION.md)
for tree identities, retained/replaced/deferred capabilities, ownership and intake
boundaries. Its `src/scenario_studio/client.py` still uses a custom remote-MCP
client, now with usage, recommendation and bulk asset reads, and keeps a raw
public-workflow HTTP path and a direct thumbnail download. Its read retries and
download resume map to SDK per-request retries and the shared transfer policy,
not to that client. Importing it unchanged would not satisfy the SDK-first contract.

When adopting further Studio code:

- Map the client operations to the exact SDK's methods and response wrappers.
  Verify catalog pagination/schema normalization, team/project discovery,
  multipart upload/finalization, signed downloads, asset search and collection
  operations. Do not infer coverage from a similar method name.
- Reproduce each uncovered operation and link an upstream SDK issue before a
  narrow raw API fallback in the shared adapter. The named discovery (#29) and
  workflow selection (#33) extensions above are the current exceptions, each
  with its issue and removal condition recorded.
- Bind exact quotes to payload/account/project, persist request identity before
  paid dispatch, and preserve an uncertain state after a lost response. SDK
  retry settings alone do not provide application persistence or prevent a
  second caller from submitting again.
- Verify workflow cancellation separately: the SDK's `jobs.trigger_action`
  documentation currently describes cancellation of inference jobs only.
- Maintain the pinned runtime bundle and its licenses, including the SDK's MIT
  notice and `pydantic-core` binary wheels. Re-run actual installed-bundle checks
  on dependency upgrades; a development lockfile alone is not bundle evidence.
- Preserve Studio source provenance/authorship, GPL text, Poppins OFL and Tabler
  MIT notices when importing useful source, tests and resources. Keep demo
  media, historical ZIPs and account-specific validation exports out of the
  canonical runtime tree.

Browser sign-in remains deferred under
[#67](https://github.com/scenario-labs/blender-plugin/issues/67). The open
acceptance checks on Blender 5.0, 5.1 and 5.2, including Linux and Windows
desktop interaction and coexistence with other extensions, are in
[#440](https://github.com/scenario-labs/blender-plugin/issues/440).

### Prompt Spark command boundary

`SDKAdapter.estimate_prompt` and `JobCoordinator.quote_prompt` use the public
[SDK prompt method](https://docs.scenario.com/api/python/resources/generate/methods/prompt),
verified against the pinned 2.2.0 artifact. This initial parameter subset accepts
an explicit SDK mode, optional text `prompt`, `modelId`, `images`, and an integer
`numResults` from 1 to 5 (default 1). It rejects unknown fields and scope/dry-run
injection. `contextual-v2` allows up to 15 references; other supported modes are
limited conservatively to 5. API-key requests omit project selection unless the
connection has an explicit override. The server dry run remains authoritative for
mode-specific requirements and pricing.

Both dry run and dispatch use the same normalized payload, exact response bytes,
credential scope and zero-retry client. Submission requires the issued quote and
a committed local intent before one service call. The command returns a durable
job receipt; it does not deliver inline prompt text or resolve prompt assets.
Full prompt result retrieval uses public `jobs.retrieve` and `assets.retrieve`
through the shared coordinator. Native New/Rewrite controls and MCP require
a separate exact-price approval, then apply only to the unchanged original field.
Render Spark preparation uses the same public method and explicit approval; live
service acceptance remains. No automatic LLM fallback or remote prompt
cancellation is enabled.

Prompt result recovery reads the successful job's `metadata.output.prompts` and
resolves any text asset references. The raw-response wrapper preserves these
fields even when the generated response model does not enumerate their contents.
No custom endpoint or SDK extension is required. Complete preview metadata is
accepted only with `properties.hasFullPreview == true`; other text uses the
bounded signed-storage protocol described in [result transfers](RESULT_TRANSFERS.md).

Translation uses the pinned public [SDK translate method](https://docs.scenario.com/api/python/resources/generate/methods/translate)
through `estimate_translate` and `quote_translate`. It accepts only nonempty
`prompt` text and follows the same dry-run, durable claim and zero-retry policy.
A successful `translate` job returns `metadata.output.translation`, read through
the same bounded text-result command. No LLM substitution or raw endpoint is used.

## Original result assets

`SDKAdapter.asset(identifier, original_assets=True)` passes the SDK 2.2.0
`assets.with_raw_response.retrieve` keyword of the same name, serialized as the
`originalAssets=true` query beside any `projectId`. The
[asset API contract](https://docs.scenario.com/api/python/resources/assets/methods/retrieve)
describes it as returning the original asset without transformation. Result
manifests, fresh download URLs and complete text reads request it, because a
large image's default `url` can serve converted bytes. Metadata-only reads, such
as history prompt previews, omit it. Offline contracts check the exact query; no
raw fallback, retry, authentication or dependency change is involved. See
[untransformed result assets](RESULT_TRANSFERS.md#untransformed-result-assets).

## Saved texture-map roles

The existing `SDKAdapter.asset` path uses SDK 2.2.0
`assets.with_raw_response.retrieve`, retaining the documented `mimeType` and
`metadata.type` fields without another API call or fallback. Offline contracts
exercise those fields through the public wrapper. The result command stores
an allowlisted image texture role independently from MIME, for later Materials
application; unknown semantics remain unknown. See
[texture result semantics](RESULT_TRANSFERS.md#texture-result-semantics) and the
[versioned store](JOB_STORAGE.md#atomicity-and-failures) for download guards and
atomic schema 2/3 upgrades. This does not change authentication, scope, retry
policy, provider acceptance or the dependency pin.

## Declared HDR originals and projections

The same `assets.with_raw_response.retrieve` response also keeps the SDK 2.2.0
`originalFileUrl` and `originalMimeType` fields. The typed
`AssetRetrieveResponse.original_mime_type` documents that an HDRi skybox exposes a
JPEG preview as `url` and its `.exr` or `.hdr` file as the original; for images
the extension selects only the OpenEXR labels. Its `original_file_url`
docstring still describes only video, audio and 3D originals, which is a
documentation gap rather than a missing operation. For audio, the extension also
selects a WAV original behind the MP3 that Scenario re-encodes
([schema 11](JOB_STORAGE.md#schema-11-wav-originals)). `metadata.type` includes the
skybox labels used for the projection. Offline contracts check the raw and typed
fields through the public wrapper. The result command downloads EXR-family and
WAV originals through the existing credential-free storage policy; no new endpoint,
SDK method, extension or raw fallback is added. `assets.download.request` was not
used: it requests a converted download with a status poll, and none of its
`target_format` values is EXR. See
[declared HDR originals](RESULT_TRANSFERS.md#declared-hdr-originals-and-360-projection)
and [schema 10](JOB_STORAGE.md#schema-10-declared-originals-and-lane-defaults).

## Panorama model contracts

The [panorama contracts](../scenario/core/scene/panorama_contract.py) behind the
native and MCP [Environment lane](MCP.md#historical-upstream-reference) use no
new SDK method, extension or raw fallback. The native form quotes and submits
through the same `ModelJobs` session commands as MCP. Model records come from
`models.with_raw_response.retrieve`; quotes and the single submission use
`generate.with_raw_response.run_model` through `SDKAdapter.estimate_model` and the
coordinator, with `dry_run="true"` only for quotes and `max_retries=0` unchanged.
The API reference has no skybox-specific pages, so each contract was checked
against the model's own `GET /models/{id}` inputs, as `run_model` documents.

The read-only [fixture recorder](../tests/fixtures/README.md) records the five
contract models' records with an API-key pair in its credential-bound scope. All five are public `custom` models with status
`trained` and no training lineage, so the adapter's estimate gate accepts them
and the catalog classifies none as trained. The contracts rely only on inputs
present in these records. The GPT skybox model's free `width` and `height`
accept any ratio and its resolution presets include cubemap layouts, so the
contract offers only its 2:1 sizes.

In the same scope, `dryRun=true` quotes through `SDKAdapter.estimate_model`
covered every contract preset, and the service accepted each one. The Flux quote scales with `numOutputs`, so the lane default of one output
costs about half the schema default of two. GPT quality, size and reference
count, the HY World backend and the upscale factor each change the quote; the
HDRI output toggles do not. The dry runs also accepted an ordinary generated
still as the `image` of the HDRI, upscale and Flux models, so an accepted quote
does not show that a reference is a 360 panorama; the contract checks that
locally. Offline [contract tests](../tests/unit/test_panorama_contract.py)
prepare each preset against the recorded schemas and the shared form
preparation. Paid-run evidence is in the
[release record](maintenance/release-acceptance.md#panorama-and-world); HY World
output, and whether returned assets carry `metadata.type` and the HDR original's
host, are open in [#440](https://github.com/scenario-labs/blender-plugin/issues/440).
No SDK gap was found, so no scenario-sdk-python issue is needed.

## Verified Film composition quotes

`JobSession.quote_film_composition` uses the existing shared adapter
`models.with_raw_response.retrieve` and `generate.with_raw_response.run_model`
methods with SDK 2.2.0. The former supplies current model inputs; the latter uses `dry_run="true"`
for estimation, query-only optional project scope and unchanged model parameters.
Approval submits the same exact estimate once through the existing durable
coordinator and adapter. Paid retries remain disabled. Composition uses no raw
endpoint or SDK extension.

Local receipt inspection and current-source checks precede those calls; guards
are repeated around price preparation and dispatch. The current composition
payload is a template subject to fresh schema validation, not a verified live
provider contract. Offline tests use synthetic metadata and transport responses;
no service or paid check is implied. See [the composition contract](FILM_PLAN.md#quoted-composition-generation).


### Local workflow command integration

Native workflow forms and local MCP list public/private workflows and read their
inputs through the adapter's `workflows.list` and `workflows.retrieve` wrappers. Estimates
and single approved submissions call `workflows.with_raw_response.run`; the
selected SDK 2.2.0 resource was inspected for its workflow-specific body,
`dry_run` and `project_id` query mapping. No adapter, dependency or raw-API
exception is added. The same JobSession and ModelJobs own scoped metadata,
approvals, persisted identity, uncertainty, polling and explicit saved results.

Installed synthetic tests exercise pagination, schema retention, exact prices,
model/workflow approval isolation, changed scenes/projects, failed persistence
and uncertain dispatch. They do not establish live workflow output acceptance,
interactive node handling, general workflow cancellation or expanded Studio.


## Asset library reads

The published SDK 2.2.0 provides [asset listing](https://docs.scenario.com/api/python/resources/assets/methods/list)
and [asset search](https://docs.scenario.com/api/python/resources/search/methods/asset_search).
`SDKAdapter.asset_page` uses the raw public list wrapper for one bounded page,
optional collection and opaque cursor. Owned-scope listing omits privacy; explicit
public listing requests public assets across organizations. Neither path infers
the key's default project. The adapter's configured project remains query-only.

`search_assets` uses the SDK's public search wrapper with query, public selection,
limit and offset in its POST body. It requests one page explicitly rather than
using automatic pagination. Search totals are estimates; missing totals permit
one explicit continuation after a full page, and empty pages stop. Continuation
counts raw hits before identical-record deduplication. Conflicting duplicate IDs,
malformed or oversized pages, repeated list cursors and invalid search pagination
fail with sanitized errors. Normalized pages retain internal asset metadata; MCP
projects reference fields and omits download URLs, previews and account records.

The existing coordinator and worker pool check active scope and online permission
around reads; no job, upload, write or paid submission is created. Offline
SDK contracts inspect actual method, query/body serialization and raw wrappers.
There is no raw API exception or dependency change. Synthetic tests do not
establish live search quality or provider acceptance.

## Asset organization writes

The published SDK 2.2.0 covers collection listing, retrieval and creation,
[collection membership](https://docs.scenario.com/api/python/resources/collections),
[asset tag changes](https://docs.scenario.com/api/python/resources/assets/methods/update_tags)
and the [bulk asset read](https://docs.scenario.com/api/python/resources/assets/methods/get_bulk)
used to verify them. `SDKAdapter` therefore uses only their public
`with_raw_response` wrappers: no SDK extension, raw API exception, SDK issue or
dependency change is needed. These are free account metadata changes. They
spend no credits, do not change the Blender scene and cannot be undone by
Blender Undo.

| Adapter method | SDK 2.2.0 method | Request and response contract |
| --- | --- | --- |
| `collection_page` | `collections.with_raw_response.list` | One page of 1 to 100 records and an opaque cursor. IDs are validated, identical duplicates are dropped, and conflicting duplicates, oversized pages or a repeated cursor fail. An empty page is valid. |
| `collection` | `collections.with_raw_response.retrieve` | The returned `collection.id` must equal the requested ID. |
| `asset_records` | `assets.with_raw_response.get_bulk` | POST with 1 to 200 IDs in the body; repeated IDs are read once. Found records are returned by ID in request order, with unknown fields kept. When a response omits a requested asset, the result leaves it out and the caller treats it as unverified; this is adapter handling, not a documented service contract. The identifier and record checks are the ones `models_bulk` uses: unrequested or conflicting records fail. Records whose `tags` or `collectionIds` are absent, null or not lists of strings also fail: SDK 2.2.0 declares both required, so missing metadata never reads as empty. |
| `create_collection` | `collections.with_raw_response.create` | POST `{"name": ...}`. The acknowledged `collection.name` must match exactly and its ID must be valid. A valid ID with another name raises `WriteUncertain` with that `collection_id`. |
| `add_collection_assets`, `remove_collection_assets` | `collections.with_raw_response.assets.add/remove` | PUT or DELETE with a JSON `assetIds` body of 1 to 49 unique IDs, the per-request maximum the SDK docstring states ("Max 49 at once"). The acknowledged `collection.id` must match. An add refused because an asset is already a member raises `AlreadyMembers`. |
| `update_asset_tags` | `assets.with_raw_response.update_tags` | PUT with explicit `strict=false` and a nonempty `add` and/or `delete` list. The reported `added` and `deleted` lists must be subsets of the request and may be empty for documented non-strict no-ops. |

Every request uses the adapter's selected credentials, its optional project
override in the query and its online-access predicate. Reads keep the existing
sanitized `AdapterError` behavior. Write outcomes are classified as follows:

- **Not sent.** Invalid input raises `ValueError`, including an identifier or
  label that cannot be encoded as UTF-8, such as a lone surrogate from a JSON
  client. A closed client or disabled online access raises a plain
  `AdapterError`. No request is made.
- **One attempt.** The client keeps `max_retries=0`. The SDK would otherwise
  retry 408, 409, 429 and 5xx responses, and the Scenario client sends no
  idempotency key. The API reference documents no idempotency or transaction
  contract for collection creation or membership changes. For
  `update_tags`, `strict=false` is documented to make the endpoint "behave
  as if it was idempotent": repeated additions and absent removals do not
  fail. That describes tag semantics, not safe replay after an unknown
  outcome, because a resend could reapply a change that another client has
  since reverted. Tag changes are therefore also sent once and read back.
- **`WriteRejected(status)`.** Any 4xx response except 408, 409, 425 and 429.
  The fixed text names the status class: rejected credentials (401), no
  permission for this asset or collection (403), not found in the selected
  connection (404), or a rejected change.
- **`AlreadyMembers`**, a `WriteRejected` subclass with status 400, only for an
  add refused with HTTP 400 whose JSON body has a `reason` exactly equal to
  "One or more assets are already part of the collection". It reads the
  decoded body the SDK exposes as `APIStatusError.body` and never echoes it.
  Another reason, a non-JSON body, a non-string `reason`, another status or
  another endpoint stays a plain `WriteRejected` (or `WriteUncertain` for 409).
- **`WriteUncertain(status or None)`.** Redirects (never followed), 408, 409,
  425, 429 and 5xx responses, timeouts, lost connections, other transport
  failures, and a 2xx response with invalid JSON (including nesting deeper
  than the decoder's recursion limit), a missing wrapper, a different
  collection ID or name, or tag lists that were not requested. Reads report
  the same malformed bodies as a sanitized `AdapterError`.
- **`WriteUncertain.collection_id`** is set only when a create acknowledgement
  has a valid collection ID but another name, for example after service
  normalization. That collection exists, so the caller reconciles by this ID,
  because an exact-name lookup would miss it. It is otherwise `None`.
  `WriteRejected` and `WriteUncertain` keep their attributes through copy and
  pickle.
- Messages contain at most the HTTP status code. They never include response
  bodies, URLs, credentials, project, asset or collection IDs, collection
  names or tags, and the original SDK exception is suppressed.

The adapter never resends a write. A caller reconciles an uncertain outcome by
reading the assets back with `asset_records`, not with search, whose index may
lag. Apart from `AlreadyMembers`, a refused multi-asset request is not proof
that no member changed, because no transaction contract is documented; read
those assets back too.

Behavior that the documentation does not establish is handled conservatively
and still needs authorized live evidence:

- **Re-adding an existing member.** The API reference does not document it,
  but the service was observed to treat an add as one transaction: if any
  requested asset is already a member, it refuses the whole request with HTTP
  400 and the `reason` above, and writes nothing, so repeating the same request
  can never succeed. More than 49 IDs were observed to be refused with a 400
  too. The hosted Scenario MCP's `collection_add_assets` relies on this: it
  skips assets already in the collection and sends the rest, in batches of 49.
  This repository has not yet checked the behavior live. The adapter raises
  `AlreadyMembers` and sends nothing more; a caller reads the assets back and
  may send only those that are not members yet. A 409 stays uncertain, and the
  read-back shows the actual membership. Offline tests cover both with exactly
  one write request each.
- **The DELETE JSON body** is serialized by the SDK, as the offline contracts
  check. A 2xx removal acknowledgement is not proof of removal and must be
  read back.
- **Collection names.** No uniqueness or idempotent create is documented.
  Callers must look up the exact name before creating and must not create
  again after an uncertain outcome.
- **Bulk read coverage.** The
  [get_bulk reference](https://docs.scenario.com/api/python/resources/assets/methods/get_bulk)
  describes IDs the team has read access to, with a limit of 200. It does not
  say whether a deleted or inaccessible ID is omitted or fails the whole batch
  with a 4xx. The adapter treats an omitted asset as unverified and a failed
  read as a sanitized `AdapterError`; neither is evidence of the asset's
  state. The 200-ID cap is enforced locally and is unverified live.
- **Local label limits.** Tag and name limits are undocumented. The adapter
  accepts exact single-line labels of at most 200 characters and at most 30
  tags per list, matching the Film plan's existing task tags. Labels cannot
  have surrounding whitespace, control characters (Cc), lone surrogates, line
  or paragraph separators, or format characters (Cf). The format category
  covers bidirectional controls and marks (U+200E, U+200F, U+061C, U+202A to
  U+202E, U+2066 to U+2069), invisible separators and operators (U+200B,
  U+2060 to U+2064, U+FEFF, U+180E), the soft hyphen and tag characters
  (U+E0001, U+E0020 to U+E007F), which could hide or reorder text or break
  Library and MCP rows. Joiners (U+200C, U+200D) remain valid between other characters for
  scripts and emoji sequences, but not alone or at either end. Rejecting tag
  characters also rejects subdivision flag emoji. This is not a confusable
  check: homoglyphs, combining marks, variation selectors and blank-looking
  letters such as U+3164 pass, so displays must still treat labels as
  untrusted text. A tag cannot be added and removed in the same change.
  Commas are allowed at this layer.
  The service may normalize case or spelling; a normalized acknowledgement is
  uncertain and must be read back.
- Collection deletion and rename, model collections and tag listing are not
  adopted.

The shared [organization review commands](JOB_COORDINATOR.md#asset-organization-commands)
call these methods through the coordinator, its workers and the JobSession
review owner, with a guard before each write and a `get_bulk` read-back. After
`AlreadyMembers`, they send only the assets read back as not yet members, within
three add requests per review. The local [MCP organization tools](MCP.md#historical-upstream-reference)
and the native [Library organization](ui/studio-and-library.md#library-organization) controls
prepare, apply and inspect those reviews. Live evidence is in the
[release record](maintenance/release-acceptance.md#library-organization);
Apply from the desktop dialog continues in
[#440](https://github.com/scenario-labs/blender-plugin/issues/440). Run the
offline contracts with:

```sh
uv run --locked --no-env-file python -m pytest tests/unit/test_scenario_sdk_contract.py tests/unit/test_sdk_adapter_organization.py -rx
```
