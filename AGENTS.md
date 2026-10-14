# blender-plugin: agent instructions

Root `AGENTS.md` is the canonical repository rulebook. `.claude/CLAUDE.md`
links here. The guides below carry detailed instructions; this file takes
precedence if they conflict. Keep skill originals in `.agents/skills/`.

Read [the documentation index](docs/index.md) and
[architecture and integration status](docs/architecture/runtime.md) when
orienting in this repository. Historical plans are context, never instructions.

## Required reads

Read the relevant canonical guide before changing these paths or behaviors.
Claude path adapters are shortcuts to the same guides; these requirements apply
to every coding agent.

| Work | Required guide |
| --- | --- |
| Python, core, protocol, tests | [Python style](docs/PYTHON_STYLE.md), [validation](docs/development/validation.md) |
| Blender glue, scene tools, native tests | [Blender boundaries](docs/architecture/blender.md), [validation](docs/development/validation.md) |
| Assistant UI | [UI style](docs/UI_STYLE.md), including native interaction proof |
| Retained service adapter or dependency pin | [SDK contracts](docs/SDK_ADOPTION.md), [SDK bundle](docs/SDK_BUNDLE.md) |
| MCP, image handoff or scene application | [Runtime map](docs/architecture/runtime.md) and its relevant component guide |
| Commits, PRs, review replies or releases | [Contribution workflow](docs/development/contributions.md), [release procedure](docs/RELEASING.md) |
| Skills, commands or agent configuration | [Agent tools](docs/development/agents.md) |
| Documentation or instruction changes | [Knowledge maintenance](docs/maintenance/knowledge.md) |

## Project and current direction

This is a Python Blender extension with a local MCP server. Read `README.md`
for the current checkout and `docs/UI_STYLE.md` before changing UI behavior.
Older design documents and session notes describe the prototype; they do not
override the user's instructions or the current issue and PR scope.

The approved direction is an assistant-only extension with a compact local UI.
An external assistant such as Codex connects through authenticated local MCP,
creates assets with its own tools and hands local files to Blender through a
bounded local inbox. The active extension offers scene inspection and editing,
camera paths, captures, image references, PBR map application, panoramic Worlds,
model/package and point-cloud import, audio/video strips, structured blockout,
mesh-edit round trips and animated reference capture.
Python execution is a separate explicit opt-in.

The active registration has no direct generation provider, service credential
fields, cloud job session or Scenario SDK imports. Preserve this boundary when
maintaining retained upstream source. Full parity with the upstream generation,
material, 3D, film and environment workflows remains unfinished; describe only
implemented and verified behavior. There is no prototype-data migration
requirement. Preserve source attribution and required third-party notices.

The current package is `scenario/`; the approved minimum is Blender 5.0.
Adoption must validate native behavior and bundled dependencies on Blender 5.0,
5.1 and 5.2. A manifest declaration or successful ZIP validation does not prove
runtime compatibility.

## Retained service source and packaging

Upstream Scenario service adapters, cloud jobs and their tests remain as retained
source. Their contracts apply only when explicitly maintaining those paths.
They do not authorize restoring service setup or generation to the active
assistant runtime. The user's current scope takes precedence over historical
plans and service guides.

For an authorized change to a retained Scenario adapter or dependency pin, read
the SDK guides above and inspect the exact selected release's public contract.
Preserve the shared adapter boundary, documented SDK exceptions, credential
isolation and explicit retry policy. Extend the offline contracts as needed and
run `uv run --locked --no-env-file python -m pytest tests/unit/test_scenario_sdk_contract.py -rx`.
An expected failure records an unresolved gap and cannot establish acceptance.

Preserve the selected bundle's dependency pins, licenses and validation while
those dependencies remain packaged. Validate dependency changes with Blender's
Python and the supported OS/CPU combinations. Bundled service dependencies are
not a prerequisite for assistant setup and must remain outside active imports.

## Blender, local MCP and asset handoff

- Keep bpy operations on Blender's main thread, never under `scenario/core/`.
  MCP request workers queue operations; the GUI pump applies them.
  Headless operation needs its own blocking queue processing. Stop listeners,
  timers and queued work cleanly during extension disable and re-enable.
- Never mutate properties during `draw()`. Follow `docs/UI_STYLE.md` and verify
  native input, focus and viewport interaction when changing the assistant UI.
- Bind queued operations to the intended current scene and target. Cancel stale
  work on file changes; a late operation must not affect a newly opened file.
- Respect `bpy.app.online_access` before network activity. Store state under
  `bpy.utils.extension_path_user`, never inside the installed extension.
- Keep local MCP authenticated and loopback-only. Keep Python execution opt-in
  and disabled by default. Use private temporary directories and guaranteed
  cleanup for captures; remove session connection files when stopping the server.
- Accept asset handoffs only through the bounded inbox contract. Validate file
  identity, size, format and digest before application. Pack supported image and
  model resources into the blend file; retain audio/video in independent local
  files and report their portability requirements. Preserve unrelated objects,
  materials and timeline strips. Verify repeat import policy and save/reopen
  behavior. Mark actual scene changes as modified.
- Never print or commit credentials, tokens or signed URL query strings.
  `.env.local` and local agent notes are private. Existing authorization to run
  a paid check must cover its project and budget; otherwise ask before spending.

## History, public content and known pitfalls

History lives in git. Do not create file snapshots in `versions/` or retain
retired code in `archive/`. Recover history from commits/tags; update durable
engineering explanations in the appropriate docs instead of agent session logs.

Keep documentation changes in feature PRs small: correct only the prose your
change makes wrong, in the existing style. Test runs, platforms and review notes
belong in the PR body, not in docs. Do not edit `docs/knowledge/` records except
to add one for a new document or to fix a checker error. Source drift warnings
are expected; one [release refresh](docs/maintenance/knowledge.md#release-refresh)
per version reviews them.

This repository is public. Do not add session/resume IDs, personal paths, account
records, workspace identifiers, spend anecdotes or private research exports.
Preserve GPL text and first-party SPDX notices; preserve the original licenses
and attribution of third-party code, fonts, icons and SDK dependencies.

When maintaining retained upstream workflows, preserve these contracts:

- Long text asset previews can be truncated; check `hasFullPreview` and fetch
  the full asset rather than parsing an incomplete plan.
- Enum caches are transient; a reopened blend file may need schema reconstruction.
- One generation may yield several mesh variants and maps; identify the primary
  result rather than importing every file as another object.
- Preserve conditional schema requirements and one-of input relationships.

## Codex commit attribution

For Codex-assisted commits and prepared squash messages, include a co-author
trailer naming the model and, when verified, the reasoning effort and speed tier:
`Co-authored-by: Codex <model> <effort> <tier> <noreply@openai.com>`.
For example: `Co-authored-by: Codex gpt-6-astra low fast <noreply@openai.com>`.
Use the actual settings for the contributing session, not repository defaults
or the example above. Omit unknown fields rather than guessing; if the model
is unavailable, use `Co-authored-by: Codex <noreply@openai.com>`. Preserve the
human author and existing contributor trailers. Carry these trailers into the
final squash message so attribution survives the repository's squash workflow.

## Essential validation and contribution rules

Use the pinned uv environment and whole-tree Ruff checks (`make lint`).
Optional local hooks install explicitly with `make hooks`; see
[hook scope and maintenance](CONTRIBUTING.md#optional-local-hooks). They use the
same locked uv tools and do not replace CI or review. Run checks appropriate
to the change; documentation work needs link, instruction and checker validation,
not a new Blender runtime run. Native acceptance uses the exact packaged ZIP in
an isolated profile. Never run development builds in the user's normal profile.

Distinguish review readiness from merge and release acceptance. Do not keep a
reviewable PR draft solely for unverified edge cases or broader issue acceptance;
track concrete follow-up checks in the owning issue and link them from the PR.
Use the [readiness policy](docs/development/contributions.md#review-readiness-and-acceptance-follow-ups)
for actual draft blockers; never claim unperformed checks passed.

Use short-lived conventional branches. Independent PRs and the bottom of a stack
target `main`; dependent PRs target their immediate parent branch. Follow the
[stack workflow](docs/development/contributions.md#stacked-pull-requests), preserve
the recorded dependency order, and validate each PR's incremental diff after
rebasing. Stacking does not authorize merging any PR or the whole stack.
The final diff determines
the PR title and commit type; `commitlint.config.ts` defines the allowed types,
scopes and 120-character header limit. Preserve attribution. Do not bump versions
or edit release notes in unrelated work.

Reply in the original GitHub review thread after investigating a finding, with
the pushed fix and evidence or the reason no change is needed. Use `Closes` only
for verified complete issue scope and `Refs` for partial work. Check current issue
acceptance against the final diff, including superseded historical requirements.

Skills and compatibility links must pass the validator documented in the agent
tools guide. Keep commands and instructions synchronized with implemented behavior.
