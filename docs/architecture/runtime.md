# Runtime map and integration status

Open Studio is an assistant-only extension. Generation takes place in the
connected assistant's tools; Blender accepts local assets and performs scene
operations. The source package remains [scenario/](../../scenario/), while the
installed extension id and headless command are `open_studio`.
The supported minimum in the [manifest](../../scenario/blender_manifest.toml)
is Blender 5.0; dependency and runtime acceptance have separate gates.

## Current assistant entry points

- [registry.py](../../scenario/blender/registry.py) registers the compact
  assistant UI, connection setup, camera tools and local asset lifecycle hooks.
  It does not register the upstream composer, model forms or cloud jobs.
- [runtime.py](../../scenario/blender/runtime.py) owns local connection and
  filesystem state. [prefs.py](../../scenario/prefs.py) stores the loopback port,
  optional Python permission and logging level. No generation credentials are
  part of this runtime.
- [assistant_loop.py](../../scenario/blender/assistant_loop.py) processes local
  MCP requests and pending file-modified marks on Blender's main thread.
- [mcp_service.py](../../scenario/blender/mcp_service.py) builds the active tool
  registry, manages authenticated loopback transport and writes a private
  connection file for the bundled stdio client. The `open_studio` command
  provides a blocking queue pump for background Blender.
- [tools_agent.py](../../scenario/mcp/tools_agent.py) exposes the bounded inbox
  and image handoff. Dedicated modules handle PBR maps, panoramic Worlds,
  models, point clouds, media, mesh edits, blockouts, animated captures and film
  scenes. Film builders use local recipes, persistent scene identities and
  explicit inbox assets; they do not use retained service job/session wrappers.
  [The current MCP reference](../MCP.md) is generated from that registry.
- [assistant_film_export.py](../../scenario/blender/assistant_film_export.py)
  owns local export/package operations. Main-thread preparation snapshots current
  scene contents; offline workers render or copy/rewrite/reopen portable projects.
  A registered timer or operation poll finalizes completed work. File load,
  Undo/Redo and disable invalidate unpublished operations. Background hashing
  binds the result digest to a prepared-file identity/size/time stamp; the main
  thread rechecks that stamp and publishes without overwriting. Private staging
  and these checks detect ordinary changes and replacements; they are not an
  isolation boundary against a hostile process running as the same OS user.
- [agent_artifacts.py](../../scenario/core/agent_artifacts.py) verifies bounded
  local file snapshots. Image and supported model resources are packed;
  audio/video remains in retained external files. Operations preserve unrelated
  scene data and reject stale targets or protected edits.

Retained SDK and service modules are not active registration dependencies.
Reusing a local helper must not import those modules transitively. Native
assistant tests check the registered tools and loaded modules in an isolated
installed package. Full film orchestration and animation retargeting remain
unfinished; asset import alone does not establish creative workflow parity.

## Historical upstream implementation

The remainder records the inherited service architecture for source maintenance.
Its credentials, SDK sessions, service operations and release assumptions do not
describe the active Open Studio build or authorize restoring service setup.

### Historical entry points

- **Registration** ([registry.py](../../scenario/blender/registry.py)): Registers
  properties, panels, operators, composer, pump and local server integration. The
  `scenario_blender` headless command serves local MCP on the main thread.
- **UI lifetime and state** ([runtime.py](../../scenario/blender/runtime.py)):
  Owns the credential-bound SDK catalog and process-wide UI/MCP state; native form,
  workflow, Library organization and Film controls use the selected `JobSession`.
- **UI generation** ([generation.py](../../scenario/blender/generation.py)): Every
  native model form consumes a lane-bound session quote before durable submission;
  unfinished file/capture/Spark inputs block final pricing and submission.
- **Main-thread application** ([pump.py](../../scenario/blender/pump.py)): Drains
  SDK catalog events, maintains shared job delivery and redraws when events arrive
  or drawn job rows change. GUI timer handling differs from headless execution.
- **Local MCP** ([server.py](../../scenario/mcp/server.py),
  [tools_scenario.py](../../scenario/mcp/tools_scenario.py),
  [mcp_service.py](../../scenario/blender/mcp_service.py)): Queues scene tools for
  main-thread execution; model listing/schema use the same SDK catalog as the UI,
  all model generation lanes use the shared session; prototype records remain
  local snapshots.
- **Credentials** ([config.py](../../scenario/core/config.py),
  [prefs.py](../../scenario/prefs.py)): Credentials default to the saved Blender
  pair; environment credentials require explicit selection and cannot mix with
  preferences. Optional project selection scopes both catalog and shared jobs;
  OAuth and live permission acceptance remain separate.

Do not infer UI/MCP parity from the shared adapter's test coverage, or promote
prototype transport usage into an approved exception to the mandatory SDK policy
in [AGENTS.md](../../AGENTS.md).

## Component maps

Each area keeps its integration status in its own map, so parallel changes edit
different files:

- [SDK catalog, pricing and history](sdk-runtime.md)
- [Scene inputs and application boundaries](scene-inputs.md)
- [Workflows, Studio and Library](workflows-and-library.md)

## Local prototype records

The catalog worker manager retains read-only local registry access for saved
media tools and status inspection. It has no client factory, paid submission,
raw estimate, polling, upload, download or automatic resume methods. Starting it
or changing online access, credentials or project does not modify old records or
send requests for them. New generation and remote recovery belong to the scoped
SDK JobSession. No second job engine or prototype migration is provided.

For a prototype record, local MCP `job_status` reads the saved snapshot and
`wait_for_job` returns it immediately, even when nonterminal, with guidance to use
`recover_cloud_job` under explicitly selected credentials. It does not imply the
saved remote status is current. Cold lookups need no manager or complete credentials.
Known cloud jobs can be recovered through the SDK; applying their results still
requires a separately approved destination. Local video insertion and audio
preview continue to use their existing explicit file controls.

An unbound `job_done` event may project a local status row but cannot invoke an
image, material, mesh, media or render-lane application callback. It displays
recovery guidance instead. Shared delivery retains its own scope, receipt and
scene guards. The
[service operation inventory](../SDK_ADOPTION.md#service-operation-inventory)
records removal of the unused prototype service helpers.

## Shared runtime components

The table describes implemented connections in this checkout. It does not certify
live provider behavior or physical interaction. See the
[candidate evidence](../maintenance/release-acceptance.md) for tested artifacts
and environments, and [#440](https://github.com/scenario-labs/blender-plugin/issues/440)
for the open checks.

| Component | Current connection | Remaining boundary |
| --- | --- | --- |
| Scoped SDK commands | [SDKAdapter](../../scenario/core/api/sdk_adapter.py) serves the selected catalog and job pools. Adopted service operations and tracked exceptions are listed in the [SDK guide](../SDK_ADOPTION.md#service-operation-inventory). | Live authentication/provider acceptance; unsupported operations are not implied by SDK availability. |
| Catalog and quotes | [SDKCatalog](../../scenario/core/api/sdk_catalog.py) owns model browsing; [ModelJobs](../../scenario/blender/model_jobs.py) routes UI/MCP exact quotes through the selected JobSession coordinator and workers. | Catalog browsing alone creates no quote or spending approval. Full retained-lane live acceptance remains. |
| Durable intent and coordination | [JobSession](../../scenario/blender/job_session.py) uses the scoped [store](../../scenario/core/jobs/store.py) and [coordinator](../../scenario/core/jobs/coordinator.py). Model forms, workflows, Film generation and recovery share this application owner. | Uncertain submissions require reconciliation; they cannot be retried as new paid requests. |
| Worker ownership | [RuntimeState](../../scenario/blender/runtime.py) selects one active session; its [workers](../../scenario/core/jobs/workers.py) outlive panel closure. Retirement stops admission while old owners retain in-flight persistence and cleanup. | Retired owners cannot deliver into a replacement connection or scene; cleanup failures may retain ownership for retry. |
| Origin and stale-result protection | [JobSession](../../scenario/blender/job_session.py) binds entry points to the selected credentials/project and captured scene/target. Restart recovery uses fresh context and destination approval. | Saved names or IDs cannot restore live scene authority; see the [context contract](../BLENDER_JOB_CONTEXT.md). |
| Result downloads | [ModelJobs](../../scenario/blender/model_jobs.py) drives saved manifests, bounded transfers and explicit interrupted-download recovery for model lanes. [Transfers](../RESULT_TRANSFERS.md) retain verified receipts and configured storage hosts. | Only admitted Image jobs automatically apply supported images to their original scene; other model results need explicit destination approval. Unsupported formats and uncertain states remain saved for inspection. |
| Result previews | [Result previews](../RESULT_PREVIEWS.md) give the selected [JobSession](../../scenario/blender/job_session.py) a main-thread scheduler and the [workers](../../scenario/core/jobs/workers.py)' dedicated preview lane. Native preview tiles, workflow review previews, waveforms, private player copies and local MCP [`inspect_result_previews`](../MCP.md#historical-upstream-reference) use that scheduler; the guide owns each source, cache and offline process. | Clip display is #445, and live thumbnail coverage is not established. Desktop checks of preview tiles, playback, waveforms and review previews continue in #440. |
| Application claims | Native [saved-job controls](../../scenario/blender/job_recovery.py) and [MCP result commands](../MCP.md) prepare and apply image, media, saved model package, material, World and supported mesh-edit destinations through ModelJobs/JobSession. Claims precede scene mutation; known receipt retries do not repeat it. The native and MCP Render Video first-frame handoff is a form binding: it verifies the saved image but takes no claim and leaves the job state unchanged. | Restarted uncertain claims require inspection. Format, target, rollback and undo limits differ by application; no atomic blend-file save is promised. Remaining first-frame handoff checks continue in #440. |
| Prompt Spark | [Prompt commands](../SDK_ADOPTION.md#prompt-spark-command-boundary) use exact quotes, durable submission and complete text recovery on the shared workers. Native/MCP New, Rewrite and Translate require unchanged-field approval; render preparation has a separate Spark approval. | Live provider acceptance remains; Spark approval does not authorize the subsequent render generation. |
| Uploads | [Reference uploads](../SDK_UPLOADS.md) use private staging, signed storage transfers and durable SDK commands on the shared workers. Generation and workflow forms attach through guarded bindings; native/MCP recovery and saved-reference attachment require the selected scope and fresh destination approval. | Live upload/result journeys remain open. |
| Model forms | [Schema forms](../../scenario/core/schema/forms.py) validate adopted model inputs before shared pricing and submission. | Trained LoRAs and compositions use routes checked before quoting; a private composition and the desktop recheck of project defaults continue in #440. |
| Mesh and World | [Mesh](../MESH_APPLICATION.md) and [World](../WORLD_APPLICATION.md) application have explicit UI/MCP destination flows for supported saved results. The native and MCP [Environment lane](../MCP.md#historical-upstream-reference) generates panoramas through verified [model contracts](../../scenario/core/scene/panorama_contract.py) and leaves results saved for World approval. | Provider-specific mesh contracts under #99 remain incomplete. HY World output, seam/pole review and the remaining desktop evidence for the Environment tab (the rebased composer tab row and a local panorama file upload) continue in #440; local application does not close them. |

The job lifecycle records intent before submission, binds an exact estimate to
scope and origin, and treats transport uncertainty as recoverable uncertainty.
A timeout does not authorize another paid request. Follow each component guide
for its actual state machine and tested boundaries.

## Local MCP queue lifetime

The main-thread executor in [server.py](../../scenario/mcp/server.py) admits each
queued request once before its monotonic deadline. Expiry and shutdown cancel
requests that have not started; a later GUI/headless pump or server restart cannot
execute them. Queue admission, start and shutdown are synchronized, while handlers
run without holding the admission lock. Shutdown therefore releases queued callers
without waiting for an unrelated tool to finish.

A timeout after the handler starts reports an unknown outcome. The executor neither
interrupts nor replays the handler, and a completed result wins a concurrent timeout
observation. This local boundary does not replace durable request identity or remote
job recovery. The [MCP security model](../MCP.md#security-model) states what
clients see.

## Where to make a change

- Pure request, schema, persistence and scene-planning logic belongs under
  `scenario/core/`, without importing `bpy`.
- Blender state, operators and main-thread application belong under
  `scenario/blender/`; see [Blender boundaries](blender.md).
- Protocol/transport code belongs under `scenario/mcp/`; scene tools still obey
  the Blender main-thread rule.
- [Unit tests](../../tests/unit/) exercise pure logic; [native tests](../../tests/blender/)
  exercise the installed package. [Smoke scripts](../../tests/smoke/) are opt-in
  paid work, never part of documentation maintenance.
- Build, installation and isolation are owned by [tools/](../../tools/) and
  [Makefile](../../Makefile), not a second packaging path.

## Historical upstream acceptance boundaries

0.10.0 delivered consolidated adoption, the shared runtime and the views. The
open checks, fixes and decisions are in
[#440](https://github.com/scenario-labs/blender-plugin/issues/440), and future
OAuth authentication remains deferred under
[#67](https://github.com/scenario-labs/blender-plugin/issues/67). Offline
serialization tests and source inspection do not satisfy live or native
acceptance criteria.

The current release follows the [API-key release plan](../maintenance/release-plan.md).
OAuth deferral does not relax credential precedence, project scope or paid-job safety.
