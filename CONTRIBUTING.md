# Contributing to Open Studio for Blender

This guide covers the mechanics of a good contribution.
[AGENTS.md](AGENTS.md) is the conventions contract for humans and agents and
takes precedence where the guides overlap. Read the
[Python tooling guide](docs/PYTHON_STYLE.md) before making changes.

## Before you start

Use this fork's [issues](https://github.com/midnitefox/blender-plugin/issues)
for contribution scope and follow the [Code of Conduct](CODE_OF_CONDUCT.md).
Report vulnerabilities through [Security](SECURITY.md), never in a public issue.
Open Studio connects to an assistant and does not require a Scenario account,
generation credentials or credits. See the current [runtime map](docs/architecture/runtime.md)
and [user guide](docs/USER_GUIDE.md) before implementing a workflow.

This guide also retains upstream development utilities and service-contract
examples. Those explicitly selected maintenance tools do not belong to the
active assistant setup. Upstream account, package identity, publishing and
organization conventions apply only to retained Scenario source and releases;
the current installed extension id is `open_studio`.

This repository is public and permanent. Keep issue text, PR text, commits and
artifacts publicly shareable: no credentials or MCP bearer tokens, signed asset
URLs, private account/project/workspace identifiers, internal repositories or
hostnames, personal paths, agent session IDs or spend anecdotes. Review generated
fixtures, screenshots and logs before sharing them.

## Set up

Fork the repository if you are not a member, then clone your fork. Install the
pinned uv version from the [Python tooling guide](docs/PYTHON_STYLE.md) and run
`uv sync --locked` from the repository root. uv selects the locked development
interpreter; avoid a separate pip-managed environment.

Blender 5.0 is the extension minimum. Native compatibility is checked separately
on the supported 5.0, 5.1 and 5.2 builds; a system Python test run does not prove
Blender compatibility. On Windows, call the Python tools directly or use Make
from a compatible shell. The [portable tools](#portable-build-install-and-download-tools)
describe binary discovery and explicit `--blender` overrides for every platform.
No Scenario account or credentials are needed for offline unit tests, building
the extension or native tests. Build preparation can download pinned SDK wheels;
the native test processes themselves forbid external network access.

## Run the checks

| Command | Purpose |
| --- | --- |
| `make test` | Locked offline unit tests; Blender is not needed. |
| `make lint` | Check every Python file with pinned Ruff, as CI does. |
| `make knowledge` | Validate documentation navigation, local links and evidence metadata. |
| `make images-check` | Check documentation image references, alternatives and PNG policy. |
| `make images` | Optimize new documentation screenshots with the shared asset command. |
| `make build` | Build and validate the exact extension ZIP. |
| `make test-blender` | Build, install and test in a disposable Blender profile. |
| `make install` | Install into a new disposable profile for manual checks. |

The [validation guide](docs/development/validation.md) defines additional checks
for each kind of change and the implemented CI matrices. Test the exact candidate
ZIP, not a previously installed copy. Never use a normal Blender profile for
development builds. UI changes need native input, focus and viewport checks as
well as before/after images; follow [UI style](docs/UI_STYLE.md) and the
[capture procedure](#repeatable-gui-screenshots). A screenshot alone is not
interaction proof.

Pull requests run offline checks. A fork workflow can wait for a maintainer's
approval to run. Paid/live tools are opt-in and never required PR checks; the
[live-command section](#live-commands) states their authorization boundary.

## Commits, branches and pull requests

Work on a short-lived `type/issue-description` branch and target `main`.
Keep one concern per pull request. PRs are squash-merged: the title becomes the
commit header, so use `type(scope): summary`, an imperative summary without a
trailing period, and at most 120 characters. Both PR titles and branch commits
are checked with [commitlint.config.ts](commitlint.config.ts).

Use the [canonical contribution guide](docs/development/contributions.md) for
the type definitions, [scope-to-path map](docs/development/contributions.md#scopes),
breaking changes, attribution and review follow-up. Complete the
[PR template](.github/pull_request_template.md) with actual validation evidence
and limitations; human review can remain pending when an agent opens the PR.
Release automation owns version fields and changelog updates. See the
[release procedure](docs/RELEASING.md) for current publication behavior.

## Naming and trademarks

The manifest `name` stays `Scenario` and the `id` stays `scenario`; do not add the
Blender logo as extension branding in icons, docs or listings (see
[TRADEMARKS.md](TRADEMARKS.md)). Forks that ship a modified build must pick their
own extension id and name, unless they have written permission from Scenario Inc.

## Coding and documentation rules

Follow the canonical [repository rules](AGENTS.md), [Python style](docs/PYTHON_STYLE.md)
and [Blender boundaries](docs/architecture/blender.md). Scenario service operations
use the shared official SDK adapter; an exception needs a reproduced SDK gap,
tracking issue and narrow adapter fallback. Keep `bpy` out of `scenario/core`,
Blender operations on the main thread and property writes out of `draw()`.
Use the exact server estimate before spending; REST `dryRun` is a query parameter.
Do not introduce em dashes into maintained text or code.

History lives in Git. Recover older states with `git show <tag>:<path>` or the
release ZIP. Do not preserve retired code, prototype plans or copies of earlier
deliverables in the working tree. Keep useful architectural rationale in the
maintained guides linked from the [documentation index](docs/index.md).

Review the [knowledge-maintenance guide](docs/maintenance/knowledge.md) for every
documentation change. Keep current user-facing limits in
[Known limitations](docs/KNOWN_LIMITATIONS.md). Existing canonical guides retain
their names; new engineering and maintenance guides use lowercase filenames.
Follow the documentation index rather than adding a second copy of a maintained
policy.

## Licensing of contributions

By submitting an original contribution you agree that it is licensed under
GPL-3.0-or-later, the licence of this repository (inbound = outbound).
You keep the copyright in your work: there is no copyright assignment, no CLA and no DCO sign-off.
Existing history is not re-signed.

First-party source files carry the SPDX copyright and GPL-3.0-or-later lines
from [scenario/__init__.py](scenario/__init__.py), after a shebang when present.
In a file you materially change or create, you may add your own
`SPDX-FileCopyrightText: <year> <name>` line alongside the existing holder lines;
never remove an existing notice. The [GPL text](LICENSE) remains unchanged.

The copyright holder in every Scenario notice is `Scenario Inc.`, the legal name
in [Scenario's Terms and Conditions](https://www.scenario.com/terms-and-conditions):
`SPDX-FileCopyrightText: <year> Scenario Inc.` in source headers and
`copyright = ["<year> Scenario Inc."]` in `scenario/blender_manifest.toml`.
Do not shorten it to `Scenario`.

Adopted compatible source, fonts, icons, assets and SDK dependencies retain their
original licences and attribution. Identify the source, licence and modifications
in the PR, including when an agent assisted. The first-party licence policy does
not replace third-party notices. Blender's
[licensing guidance](https://www.blender.org/about/license/) requires published
scripts using its Python API to be GPL-compatible; this extension chooses
GPL-3.0-or-later.

## AI-agent contributors

Agents follow the same contribution and licensing rules. Name the contributing
harness in the PR, preserve existing authors and retain accurate co-author
trailers using [the attribution rule](AGENTS.md#codex-commit-attribution), including
in the squash message. A human reviews the complete diff before merge; an agent
may open a reviewable PR with that review explicitly pending. Automated bot
reviews do not constitute human approval.

## How issues are triaged

Maintainers choose the issue type and one primary area, using labels such as
`bug`, `enhancement`, `documentation`, `question` and `area:*`. For defects, include
the observed Blender and OS versions; `needs-repro` or `needs-info` requests
missing evidence. `model-behaviour` distinguishes provider behavior from an
extension defect, while `known-limit` points to the limitations guide.
`good first issue` and `help wanted` identify contribution opportunities.
Maintainers track priority and effort in the Blender Plugin project. Repository
administration and release authorization remain maintainer responsibilities.

### Sibling integration repositories

New Scenario integration repositories are named lowercase `<host>-plugin`
(this repository is `blender-plugin`). The extension or package id is `scenario`
inside the host's namespace: Blender extension id `scenario`, Unity package
`com.scenarioinc.scenario`. Existing repositories keep their names:
`Scenario-Unity` is not renamed because its Git URL is embedded in users' Unity
package manifests. Every integration repository carries a root `LICENSE`,
`CONTRIBUTING.md`, `CODE_OF_CONDUCT.md` and `SECURITY.md`, and the topic `scenario`.

## Optional local hooks

After `uv sync --locked`, run `make hooks` once in each checkout to install the
pre-commit and commit-msg hooks. The equivalent command works without Make:

```sh
uv run --locked --no-env-file pre-commit install --hook-type pre-commit --hook-type commit-msg
```

The hooks run Ruff on staged Python files, check file hygiene and house rules,
guard added staged lines against common secrets, and check the Conventional Commit
type. All tools use the pinned uv development group; no separate hook environment
or Node installation is required. Review and re-stage any formatting changes.
The full commitlint scope/header policy remains in CI.

Run `uv run --locked --no-env-file pre-commit run` to check staged files manually,
or add `--all-files` to visit the whole tree. Recorded fixtures are excluded
from newline/whitespace rewriting. Hooks are optional and do not replace CI or
review. See [hook maintenance and secret-check limits](docs/development/contributions.md#local-hook-maintenance).


## Environment variables

The extension uses saved Blender credentials by default and reads the runtime
API key and secret from the process environment only when that source is
explicitly selected. Development tools use a separate test credential pair. Copy
[`.env.example`](.env.example) to `.env.local` (git-ignored) for live tools.
Load it explicitly with `uv run --locked --env-file .env.local`; exported process
variables take precedence, including empty values. uv is the only dotenv loader;
Python scripts do not read credential files. CI can supply test variables directly
without a file. See [uv's dotenv options](https://docs.astral.sh/uv/configuration/files/#environment-variables).

`make test` and `make test-blender` need no credentials. `make test` disables
uv dotenv loading even if `UV_ENV_FILE` is set. Live and paid scripts are outside
the default unit-test collection and are not added to PR workflows.

| Variable | Read in | Effect | Set by |
| --- | --- | --- | --- |
| `SCENARIO_TEST_API_KEY`, `SCENARIO_TEST_API_SECRET` | `tools/dev_config.py`: `live_settings` | Required pair for smoke scripts, fixture recording and live payload audit; passed explicitly to the client, with no fallback to runtime credentials | Developer or CI secrets |
| `SCENARIO_TEST_PROJECT_ID` | `tools/dev_config.py`: `live_settings` | Optional; unset/blank omits project selection. When supplied, sent as `projectId` in live-tool request queries | Developer or project-specific fixture |
| `SCENARIO_API_KEY`, `SCENARIO_API_SECRET` | Retained `scenario/core/config.py`: `resolve_credentials` | Historical service helper only. The active Open Studio preferences and runtime do not read these variables | Explicit upstream maintenance only |
| `SCENARIO_API_BASE` | `tools/audit_payloads.py`: `run` | Audit-only REST base URL; default `https://api.cloud.scenario.com/v1` | Developer |
| `SCENARIO_SMOKE` | `tools/smoke_image.py`, `tools/smoke_suite.py`, `tools/smoke_inputs.py` | `=1` opts into explicitly authorized smoke spending or reference uploads; keep this out of dotenv files | Developer, on the command line after authorization; the protected smoke workflow |
| `SCENARIO_GUI_PROBE` | `scenario/blender/operators.py`: `probe_mode`; Film and model-job approvals in `scenario/blender/`; `scenario/mcp/tools_scenario.py`: `generate`; `tools/capture_gui_scene.py` | `=1` blocks panel, Film, workflow and MCP generation during screenshots and desktop review; it is not a general network or spending sandbox | `tools/capture_gui.py`, `tools/gui_screenshot.py`, `tools/desktop_review.py` |
| `SCENARIO_PROBE_MODEL` | `tools/gui_screenshot.py` | Selects a model for 3D-tab screenshots | Test tools |
| `SCENARIO_SHOT_SOURCE` | `tests/blender/test_shot_planner.py` | `=1` loads the shot planner from source rather than the installed extension; unsuitable as evidence of ZIP acceptance | Test tools |
| `SCHEMA_CACHE` | `tools/audit_payloads.py` | Optional private persistent schema-cache root; otherwise each run uses a new temporary cache. Live subdirectories are partitioned by credentials, project and API base URL | Developer |

`OPEN_STUDIO_TOKEN` can supply the local MCP bearer token to the headless
`open_studio` command. An explicit `--token` takes precedence. With neither
configured, the command generates a session token. Every token stays out of
startup output and copied client setup; the bundled bridge reads the private
connection file. This authenticates the local assistant connection. Empty or
malformed explicit tokens are rejected rather than silently replaced.

For retained upstream live tools, a project ID is **not required for API-key authentication**. Leave the optional
project variable blank to use the API key’s default scope, or supply a project ID
to select the intended project explicitly. Project-selection tests may require
their own fixture and skip clearly when it is absent; that is not a global credential requirement.
Never put development credentials in the extension ZIP or a normal Blender profile.

## Live commands

From the repository root, after `uv sync --locked`:

```sh
# Exercise the shared SDK adapter's catalog read (no record export).
uv run --locked --env-file .env.local python -m tools.check_sdk
# Explicit SDK estimate for a custom model; parameters.json is a local input object.
uv run --locked --env-file .env.local python -m tools.check_sdk --model MODEL_ID --parameters parameters.json
# Fetch schemas without submitting generations (or reuse the local schema cache).
uv run --locked --env-file .env.local python tools/audit_payloads.py
# Fetch records into tests/fixtures; review and sanitize before committing them.
uv run --locked --env-file .env.local python tools/record_fixtures.py
```

If CI or your shell already supplies the test pair, omit `--env-file .env.local`;
use `--no-env-file` to explicitly disable file loading. Missing/blank credentials
produce one actionable error before a service call. Neither script submits a
generation. The payload audit uses `SDKAdapter.model` and the pinned SDK's public
model-retrieve method. Its [offline mode and threshold/report options](docs/MODEL_PAYLOAD_AUDIT.md)
need no credentials; `--offline --cache tests/fixtures/models --models MODEL_ID`
reads only that explicit fixture directory. Live caches remain scoped to the
selected credentials, project and API base. The separate
[weekly API contract workflow](.github/workflows/api-contract.yml) uses dedicated
repository test secrets only on canonical `main`, with issue-writing permission
confined to its downstream reporter. It cannot run on PRs or forks. Maintainers
can dispatch it from Actions after configuring the intended test scope; actual
live and issue-deduplication acceptance remain #41. See
[reports and rerun boundaries](docs/MODEL_PAYLOAD_AUDIT.md#weekly-workflow-and-retained-results).
The [fixture inventory](tests/fixtures/README.md) documents fixture recording,
provenance, rights and licence.

`tools.check_sdk` uses the shared SDK read/estimate adapter and always sends
estimates with `dryRun=true` in the query. It prints counts and the exact cost,
not account records, request inputs or signed URLs. It has no paid submission
command and is never invoked by offline tests or PR CI. Live endpoint acceptance
must be recorded separately from synthetic SDK contract results.

The model acceptance command uses the shared SDK adapter and durable job
coordinator. Start with its non-submitting quote command, then review the exact
cost, request and selected account/project before authorizing any paid action:

```sh
uv run --locked --env-file .env.local python -m tools.smoke_image quote \
  --run-dir workdir/image-check --model MODEL_ID --parameters parameters.json
```

The parent directory must already exist; the run directory must be new. Follow
[the model check instructions](tests/smoke/README.md) for explicit quote-digest,
exact-cost and budget approval, single submission and restart recovery. API keys
can use their default scope without a project ID; confirm the intended test scope
before spending. Preserve the private run directory after uncertainty and use
`resume`, never another submission. This tool verifies downloaded receipts, not
Blender UI interaction, media decoding or scene application.

Use `tools.smoke_model quote --result-kind image|material|video|model|audio|panorama|hdri`
with the same arguments for other result kinds. The expected kind is part of the
saved approval. The Material, Video and image-to-3D scripts accept the
same quote/submit/resume commands through this shared engine; local references
must first be uploaded explicitly through the native/MCP shared upload path or
the [reviewed reference-plan command](tests/smoke/README.md#prepare-reference-inputs).
The [aggregate suite and protected workflow](tests/smoke/README.md#one-aggregate-budget-for-a-suite)
quote all cases before checking one total cap. Hosted environment setup and live
acceptance remain #40; none of these scripts runs live in offline verification. uv loads every variable
in a dotenv file, so keep `SCENARIO_SMOKE=1` on the command line only. Credentials,
an opt-in flag or a prepared quote alone do not authorize spending.

## Native Blender test loop

Run from the repository root after `uv sync --locked`:

```sh
make test-blender
# Equivalent portable command (also works without make):
uv run --locked --no-env-file python tools/test_blender.py
# Select a particular Blender executable, including paths containing spaces:
uv run --locked --no-env-file python tools/test_blender.py --blender /path/to/blender
# Also keep a combined diagnostic log (the destination must not exist):
uv run --locked --no-env-file python tools/test_blender.py --log workdir/native-check.log
```

The runner checks `--blender`, then `BLENDER`, then PATH, platform locations and
local `.blender/` builds. Platform locations include the standard or versioned
Blender applications under `/Applications` on macOS; `/snap/bin/blender`,
`/usr/bin/blender` and `/opt/blender*/blender` on Linux; and versioned Blender
Foundation installations under `C:/Program Files` on Windows. Versioned
installations and cached builds use numeric version ordering. Cache discovery
selects only the current platform's executable layout, including the nested
Linux/Windows archives produced by the fetcher. An explicit selection takes
precedence and a missing selection is an error.

`uv run --locked --no-env-file python tools/blender_env.py` prints the selected
executable without launching it. `--manifest-version` (also `--version`) prints
the extension manifest version and needs no Blender installation; it does not
report the selected Blender's version. The test runner reports actual
Blender/Python/OS versions and rejects binaries below the manifest minimum.
An installed Blender 4.x cannot provide acceptance evidence for this extension's
5.0+ target. Discovery alone does not prove runtime compatibility.

Build preparation stages the pinned SDK wheels and their original notices in a
temporary source tree, downloading missing artifacts from PyPI into the ignored
`.blender/wheels/` cache and checking their hashes. Native test processes still
forbid external network access. See [SDK bundle details](docs/SDK_BUNDLE.md).

Every run creates its own directory under `.blender-profile/`, builds to an exact
ZIP filename, validates that file, installs it in a fresh profile and checks all
installed files against the ZIP. Missing, changed and extra files fail the run;
source-checkout imports are forbidden. Supplying `--zip /path/to/candidate.zip`
tests a copied snapshot of an existing artifact instead of rebuilding.
`--no-build` selects only `dist/<manifest-id>-<manifest-version>.zip` and checks
the copied archive's id/version against the checkout manifest. It fails when that
exact file is absent; it never picks a newer-looking ZIP. Build it first with
`make build`. Both reuse modes retain ZIP, licence, SDK bundle and installed-byte
validation and report the tested SHA256; neither rebuilds the source checkout.
`--zip` and `--no-build` are mutually exclusive. `--fresh` is accepted for command
compatibility: every invocation already owns a fresh profile, so it never deletes
or reuses a shell-selected profile.

The default **baseline** covers registration defaults and paths,
fixture-driven generation events, image/material/GLB import, installed core/MCP
dependency imports, offline generation gating and authenticated MCP. A scene-tool
request also checks that Blender work runs on the main thread. The runner then
checks disable/re-enable. External socket connections are forbidden and recorded
as failures even if application code catches the exception; loopback is allowed
for local MCP. No Scenario credentials, dotenv files or paid calls are needed.

The baseline also covers history, render-lane request preparation and prompt
operators with synthetic clients. Test context managers restore credentials and
Blender's online-access preference, isolate job/cache/output paths, stop workers
and clean temporary storage. Prompt tests import only the verified installed ZIP.

Online/offline contracts toggle Blender's actual preference with fixture credentials.
They cover catalog worker success/failure and loading state, refused offline MCP
startup, network-operator polls, persisted jobs and pump resume gating. Synthetic
clients exercise online branches; service transport tripwires and the runner's
external socket guard remain active.

The baseline also imports the actual bundled SDK and all runtime dependencies,
checks loaded source/binary identities, and exercises catalog/estimate/auth
contracts through a synthetic HTTP transport with Blender's real online setting.
The JSON test report includes dependency versions and the native wheel identity.

This baseline is smaller than the full existing suite.
Use `--suite all` (or `make test-blender BLENDER_TEST_ARGS="--suite all"`) to run
all integration tests through the same guards. Full adoption coverage, broader
offline behavior, SDK bundle compatibility, GUI/input/rendering and OS acceptance
remain separate work; the baseline does not certify those paths.

The runner removes inherited Scenario credentials and Blender/Python path
overrides from its child environment. It uses fresh user resources and temporary
files, redirects test output into that profile, and compares file metadata in the
normal Blender profile before/after successful runs. It never reuses or deletes
a profile supplied through the shell. Avoid changing your normal preferences
while this check runs, since that would correctly report a profile change.

Native phase output streams to the terminal while each process runs. Optional
`--log PATH` also writes phase-labelled UTF-8 output to a new file outside the
normal Blender profile; an existing destination is never overwritten. The file
closes on success, failure or interruption. Per-phase logs remain available even
if forwarding output fails; a failing Blender process retains its exit code.

Artifacts in `.blender-profile/run-*` include per-phase logs, the candidate ZIP,
its SHA-256 and JSON runtime/test reports. Blender failures retain their exit code;
a per-process timeout prevents indefinite hangs. Successful profiles and temporary
files are removed unless `--keep-profile` is given. Failed profiles remain for
investigation. `--artifacts DIR` changes the parent directory for these unique runs.
Do not invoke `tests/blender/run_all.py` directly; missing, malformed or unrelated
runner ownership records are refused with exit status 2 before importing `bpy`.
Use `make test-blender` to create the managed profile and exact candidate ZIP.

[Blender baseline CI](.github/workflows/blender-baseline.yml) runs the full offline
unit suite on Linux and the same native baseline on Linux x64 and Windows x64
with Blender **5.0.1, 5.1.2 and 5.2.1**. It reuses the release pipeline's checksum-verifying setup action and
publishes logs/reports/ZIPs, including on failure. The `blender-baseline-ok` check
requires every leg to pass. To change a pinned version, verify each platform
archive checksum in the official [Blender download directory](https://download.blender.org/release/), update the
matrix version and hash together, and inspect the actual runtime report from CI.

### Bumping the Blender matrix

The separate [weekly OS workflow](.github/workflows/blender-os.yml) builds and runs
the full native suite on Blender **5.1.2**, macOS Apple silicon and Windows x64.
It is scheduled for Monday 04:17 UTC and supports manual dispatch. It has no
pull-request trigger, is outside `ci-ok`, and must not become a required check.
The workflow uses the same checksum-verifying fetcher and disposable-profile
runner as local development, with no Actions download cache.

Update each `version` and `sha256` together after checking that the official
`Blender<series>/blender-<version>.sha256` lists both archives:
`blender-5.1.2-macos-arm64.dmg` and `blender-5.1.2-windows-x64.zip` for the initial
matrix. Update the reporting job's `--version` to match the matrix. Confirm that
`macos-latest` still supplies Apple silicon and
`windows-latest` supplies x64 in the [runner image inventory](https://github.com/actions/runner-images#available-images).
Then dispatch `gh workflow run blender-os.yml`, watch the run and inspect both
runtime versions, native results and retained artifacts before claiming support.

Each leg retains download/test logs, the candidate ZIP and JSON reports for
14 days. A separate Ubuntu job reads this run's latest per-job results after both
OS jobs finish, including failures during setup, native tests or artifact upload
and job timeouts. It validates both expected matrix identities before creating
or commenting on an exact-title open issue labelled `area:ci` and the OS/Blender
series. Failed-jobs-only reruns may retain an earlier successful leg; future
attempts, missing results and ambiguous results are rejected before writing.
Deliberate whole-workflow cancellation does not create issues.

Workflow concurrency serializes reporting; the reporter reads every issue page
and refuses ambiguous duplicates. A failed issue report does not suppress the
other OS report; the reporting job still fails if either report could not be sent.
Only the reporting job receives `actions: read`
and `issues: write`, using its GitHub token rather than Scenario credentials.
The stdlib reporter uses `uv run --no-project --no-env-file` so a failed native
job's dependency sync does not prevent reporting. An unavailable reporting runner,
checkout, tool or GitHub API can still prevent the report; inspect Actions directly
then. The issue records the workflow failure and job conclusion, without claiming
that setup or artifact failures are native test failures.
Do not loosen tests to make a platform pass. Triage real incompatibilities into
focused bugs, and close a workflow-error tracking issue after its fix is verified.
GitHub may disable scheduled workflows after 60 days without repository activity;
re-enable from the Actions tab when needed. Recheck both jobs and issue delivery
when changing the workflow; offline reporter tests alone cannot prove hosted
delivery, and the two OS legs are separate builds, not a combined release
candidate or desktop, GPU/audio or live-service acceptance.


### Portable build, install and download tools

The build/install/download tools use Python 3.11.13+ and the standard library. Select Blender with `BLENDER`
or `--blender`; an explicitly selected missing binary is an error. Build/install
commands create fresh profiles under `.blender-profile/tools-*`, strip inherited
Scenario credentials and Blender/Python path overrides, and retain per-phase logs.

```sh
uv run --locked --no-env-file python tools/build.py --output dist
uv run --locked --no-env-file python tools/build.py --repo
uv run --locked --no-env-file python tools/build.py --repo /path/to/repository
uv run --locked --no-env-file python tools/install.py --zip dist/open_studio-<version>.zip --launch
```

`build.py` builds the manifest's exact ZIP, checks its GPL text and validates it
before copying it to the output directory. Bare `--repo` generates `OUTPUT/repo/`;
`--repo DIR` selects an explicit repository directory. Validation is always required.
Successful build profiles are removed; failures retain their profiles and logs.
`install.py` builds when `--zip` is absent, verifies installed files against that
ZIP and retains its new profile. `--launch` opens that profile for manual testing.
An existing shell profile is never reused. Remove the printed `tools-*` directory
when finished with it and after closing Blender. `make build` and `make install`
accept `BLENDER_BUILD_ARGS` and `BLENDER_INSTALL_ARGS`, respectively.
`make repo` forwards the build arguments and adds `--repo`; `make install-isolated`
is an alias for the already isolated `make install` command.

On Linux x64, Windows x64 or macOS Apple silicon, fetch an official Blender release with:

```sh
uv run --locked --no-env-file python tools/fetch_blender.py --version 5.0.1
```

The equivalent positional form is `tools/fetch_blender.py 5.0.1`. Use either
version form, not both. `--dest DIR` aliases `--cache DIR`; both choose the parent
for the same verified, managed cache slots. The Python `series()` and `url_for()`
helpers only construct official release paths; they perform no download or
runtime compatibility check.

The fetcher reads the official checksum file, verifies the archive before
extraction and prints the executable path to use with `BLENDER`. CI supplies
`--sha256` to pin the expected digest. Archives are cached under `.blender/`;
every invocation re-extracts the verified archive into the same managed slot for
that platform, version and checksum. Successful replacement removes the previous extraction;
a failed extraction leaves it intact. On macOS, the verified DMG is mounted read-only
at a private temporary path; only `Blender.app` is copied. The tool detaches that
mount before publishing the installation, retrying a busy mount once with forced
detach. If macOS refuses both attempts, the command fails and leaves the private
mount named in the error for manual cleanup, without traversing its contents.
Do not fetch a build while using that cached
Blender executable. Older builds from the previous tool may leave randomly named
version directories; remove those manually when no longer in use.
Other platforms require a separately installed Blender selected with `BLENDER`.
No download happens implicitly during build, install or tests.
The reusable setup action and pull-request native matrix cover Linux and Windows;
the separate weekly workflow adds macOS.

Screenshot probes prepare the blockout form without submitting a design.
Design/refine operators also respect offline access, missing credentials and
`SCENARIO_GUI_PROBE=1`. This flag is a development guard, not a paid-test mode.

The optional Pillow-based `tools/make_icons.py` renderer can use Windows Arial
or DejaVu, Linux system/Blender fonts, or fonts from flat and fetched Linux/Windows
cache layouts. This only broadens font discovery; it does not regenerate tracked
icons or add Pillow to the extension. If no usable font is found, the renderer
keeps its existing geometric fallback.

### Isolated Blender commands

Run a trusted local Blender command with its own temporary profile:

```sh
uv run --locked --no-env-file python tools/blender_env.py run -- \
  --background --python-exit-code 1 --python /path/to/local-probe.py
```

The wrapper uses the same discovery and credential/path scrubbing as the other
tools. `run --blender PATH` selects a binary. Each invocation creates a unique
`command-*` directory under `.blender-profile/`; `run --artifacts DIR` selects a
different parent outside the normal Blender profile. It never reuses a profile
from the shell. Blender starts in offline mode, and an explicit `--online-mode`
override is rejected. Relative input paths use the caller's working directory;
child input/output streams remain attached to the terminal.

The profile and temporary files remain until the child stops, then are removed
on success, failure, timeout or interruption. `run --timeout SECONDS` changes the
300-second limit, including for GUI commands. The Python `run(args, check=True)`
helper raises `subprocess.CalledProcessError` for a failed child; `check=False`
returns its completed-process result. Normal-profile execution is not supported.
The CLI preserves a child's exit status, reports timeout as 124 and interruption
as 130; invalid usage is 2 and configuration/launch errors are 1.

This command installs no extension and does not grant native ZIP acceptance.
Use the dedicated build/install/test/capture tools for those checks. Run only
trusted scripts: a private profile and Blender's offline flag are not a sandbox
for arbitrary Python file or socket access.

### Interactive desktop review on macOS

Use [desktop_review.py](tools/desktop_review.py) when a human or desktop-control
agent needs to interact with an exact installed ZIP. This command currently
supports macOS only; Linux/Windows window attachment needs separate validation.
It requires a graphical login, a trusted Blender `.app`, the locked uv environment,
and Apple's `/usr/bin/codesign`. Allow disk space for a full copy of Blender.

```sh
uv run --locked --no-env-file python tools/desktop_review.py \
  --blender /Applications/Blender.app/Contents/MacOS/Blender \
  --zip /path/to/scenario-candidate.zip
```

Each invocation creates a unique directory under `workdir/desktop-review`
(`--artifacts PATH` overrides the parent), copies the application, gives it a
stable bundle identifier, and applies an ad-hoc development signature to that
copy only. The `ScenarioReview-…` suffix is a hash of the resolved source Blender
executable path and artifact parent directory. Repeated runs with those same paths
keep the app name, executable name and bundle identifier, so desktop-control
approval can recognize the app across runs. Changing the ZIP, Blender bytes or
disposable profile does not change that identity. Permission persistence still
depends on the desktop-control service; a stable identity does not guarantee it.
Moving either path creates a new identity. Concurrent reviews of the same source
must use different `--artifacts` parents; an advisory lock rejects overlapping
runs with the same identity and is released when the runner exits.
Its native executable is renamed to the stable app name and remains the bundle
executable; a wrapper executable can prevent desktop-control tools from attaching
correctly.
The copy does not claim `.blend` file associations. The source application and
candidate ZIP are not modified. Do not move the generated app or profile.

The runner uses a minimal child environment and disposable home, configuration,
scripts, extensions, data and temporary directories. The copied app also embeds
these profile paths for Launch Services launches. It validates the exact ZIP,
installs and verifies its bytes, saves offline preferences with the first-run
splash disabled, and opens a uniquely named `Scenario-Review-…blend` fixture.
There are no credentials, simulated input events or automatic screenshots.
Offline mode, the generation probe flag and a Python socket guard support this
trusted local test; they are not an OS sandbox for arbitrary extensions.

Before sending input:

1. Read the printed app identifier, scene filename and PID, plus `ready.json` in
   the printed artifact directory. Its PID must match the runner, its paths must
   be inside that directory, and `online_access` must be false.
2. Attach using the **printed bundle identifier**, then inspect the window title
   and screenshot. The title must contain the printed fixture filename and the
   Scenario UI must be present. The bundle identifier is reused across runs, so
   verify the current PID and unique fixture even after a remembered approval.
   Do not select another Blender window by name alone.
3. Refresh the desktop tool's window state before keyboard actions. Stop if it
   reports a different/untitled window or the supervised process exits. A tool
   can relaunch an app; a replacement window is not evidence for the original run.
4. Attachment/focus failures can still occur in the desktop-control service.
   If attachment stalls or input has no visible effect, use a human reviewer in
   this same isolated window; do not record attempted input as passing.
5. Exercise the intended UI flow and record visible results. For a basic input
   check, edit the timeline's current-frame field and compare the visible value
   with `observed.json`. This smoke check does not establish full UI acceptance.

Quit the test Blender when finished. The default maximum GUI lifetime is
900 seconds (`--duration SECONDS`); creating an empty `stop` file in the artifact
directory also requests a clean exit. Setup steps use `--timeout SECONDS`
(default 300). The host attempts to terminate and reap its owned child on timeout
or interruption. If cleanup fails, the command exits unsuccessfully and records
`cleanup_error` in the final report; check the recorded PID before removing the
run's artifacts. It never kills other Blender processes.

`report.json` records ZIP SHA-256, app identity, PID, runtime version, process
exit and the normal profile's before/after metadata comparison. `ready.json`
records startup identity; `observed.json` records the latest frame, offline state
and rejected Python network attempts. `status: finished` means the supervised
process and isolation checks passed; `interaction_acceptance: not_assessed`
always requires separate human/agent evidence. A forced host timeout, missing
identity record, changed package or profile, or observed network activity fails
the command. A normal duration/stop-file exit alone does not prove interaction.

Artifacts, the copied application and disposable profile are retained for
inspection on success and failure. They can contain local paths and scene data;
keep them private and untracked. After all test windows/processes are closed,
remove only that run's directory. Rerun the repository command for a fresh review
rather than launching a retained app independently. This procedure establishes
neither release approval nor supported-platform acceptance for the extension.

### Repeatable GUI screenshots

Use an interactive desktop session (the command opens and closes its own Blender
window). The capture runner builds or installs an exact ZIP in a fresh offline
profile, prepares a version-matched default scene, and captures the Scenario UI:

```sh
uv run --locked --no-env-file python tools/capture_gui.py --blender /path/to/blender \
  --zip dist/scenario-<version>.zip --output workdir/screenshots \
  --view sidebar --fixture form --label "milestone / candidate commit"
uv run --locked --no-env-file python tools/capture_gui.py --blender /path/to/blender \
  --zip dist/scenario-<version>.zip --output workdir/screenshots \
  --view composer --fixture form --label "milestone / candidate commit"
```

Omit `--zip` to build the current source. Successful cleanup keeps one candidate
ZIP, screenshots, reports and
logs; staged source/wheels and the temporary ZIP copy are removed. Setup errors
produce a failed report when the output directory is writable. A cleanup error
retains capture evidence with `status: cleanup_failed` and a nonzero exit.
The Blender-side `gui.json` reports `captured` only after the sidebar/composer
and both credential-preference screenshots have been written; a timeout between
timer callbacks cannot leave a successful capture report.
The composer has no audio lane; use `--view sidebar --lane audio` for that lane.

`--fixture empty` (default) captures the
signed-out UI; `form` supplies a clearly named synthetic model and fake credentials
in memory, without service requests or an actual quote. The current sidebar hides
its form while offline; the composer can display the synthetic model and prompt.
`--lane` selects image,
video, audio or 3d. `make gui-check LANE=video` forwards this choice (`image` by
default); an explicit `--lane` in `BLENDER_GUI_ARGS` takes precedence. Capture
runs always use offline mode and the GUI probe guard.
Inherited Scenario credentials and Blender/Python overrides are removed.
`--gpu-backend` can select a Blender graphics backend explicitly; the capture
report records the actual backend and renderer. Blank captures are rejected.
On Linux, `--capture-backend x11` uses xdotool and ImageMagick to read only the
visible window belonging to the disposable Blender process. This is useful when
software OpenGL produces a blank GPU screenshot; the default remains Blender’s
screenshot operator. The report identifies the capture mechanism.

Each invocation keeps a unique timestamped directory containing `plugin.png`,
`report.json`, the candidate ZIP, the fixture blend and phase logs. The report
records the ZIP/PNG hashes, actual Blender/Python/OS, view, lane, fixture and an
optional user-supplied label. A label identifies the intended milestone; the ZIP
hash identifies the actual artifact. Successful profiles are removed. Failure
profiles remain for diagnosis, and the command exits unsuccessfully on missing
GUI evidence, a missing PNG, changed installed files, timeout or runtime mismatch.

**Inspect each saved image:** the API's active-tab value alone does not establish
what Blender rendered. A captured image also does not prove input/focus behavior,
live API access or paid generation. Keep local images and an evidence index in
ignored `workdir/screenshots/`; when using worktrees, pass the root checkout's
absolute screenshots directory to collect milestones together.

The Blender baseline CI matrix also captures sidebar and composer on 5.0.1,
5.1.2 and 5.2.1 using a virtual X display, Openbox and software OpenGL. The X11
capture backend reads the Blender window directly because llvmpipe can return
black frames through Blender’s GPU screenshot operator. CI passes the
same ZIP that completed the native tests to the capture runner. Each matrix
leg publishes `blender-screenshots-<version>` with PNGs, JSON evidence and logs;
the baseline artifact retains the corresponding ZIP. Profiles and local fixture
blend files are excluded from uploads. A capture failure fails that matrix leg.
Download and inspect these artifacts during review: software-rendered Linux
images complement local native GUI checks and do not replace input testing or
other OS/GPU acceptance.


## Documentation builds

Edit [the Markdown guide](docs/USER_GUIDE.md) for all guide prose. The
[handbook template](docs/handbook-template.html) contains presentation, navigation
and version slots only. `make docs` renders the guide to ignored `site/index.html`
and copies just its referenced images beside it. A neighboring `.assets.json`
record lets repeat builds remove unchanged image copies that the guide no longer
uses, including when switching the same output to inline mode. Unrecorded files
are retained; modified old copies and symlinked destinations stop the build for
inspection. Keep that record with a website output directory between builds.
The version comes from the
extension manifest. Python-Markdown is pinned in the development environment;
it is not included in the extension ZIP or imported by shared build/install tools.

For a single HTML file containing the content and images:

```sh
uv run --locked --no-env-file python tools/build_docs_html.py \
  --inline-images --output dist/scenario-handbook.html
```

Both modes retain the template's `fonts.googleapis.com` stylesheet, which loads
fonts from Google when viewed online. Single-file output does not mean fully
offline typography; system fonts remain available without that connection.
Relative documentation links point to their repository files or directories on GitHub.
The renderer rejects missing/empty images, empty alt text, image paths outside
the guide directory, incomplete document metadata and unknown template slots.
Authored `{{...}}` examples in Markdown remain literal guide content.
It accepts `--source`, `--template`, `--manifest` and `--output` paths; images are
relative to the Markdown source, and relative links must resolve in this repository.
Review the rendered page at wide and narrow widths after presentation changes.
Generated HTML and `site/` are ignored; do not commit them or copy old guides into
snapshot directories. The [publication workflow](docs/RELEASING.md#publish-the-guide-and-repository)
composes hosting and release attachment; native update controls live in Scenario
preferences. Production acceptance remains under #37.

### Complete site snapshots

`tools/build_site.py --pending-repository` builds only the handbook and a
repository-pending page, with no installable index or ZIPs. The publication
workflow allows it only for the pre-adoption `0.9.9` checkout, so released
versions always publish the repository.

`make site` combines the same website renderer with the validated native repository
generator. First [select the retained release inventory](docs/RELEASING.md#select-retained-releases-offline)
from verified downloaded assets for the supported Blender/platform matrix, then run:

```sh
make site SITE_ARGS="--inventory selected-assets/inventory.json"
```

The default output is `site/`, containing the guide at `index.html`, referenced
images as files, and `repo/` with the native `index.json`, HTML listing, normalized
inventory and every selected ZIP. The handbook version comes from the checkout's
manifest; offered versions and compatibility come from the retained archives.
The inventory's extension identity must match that manifest. No archive is rebuilt
or rewritten, and no release is dropped by this command. Reuse the complete
selected inventory for a docs-only rebuild, including older releases needed by
the supported matrix.

The output directory must be new. If `make docs` or an earlier site build already
created `site/`, choose a fresh ignored output such as
`SITE_ARGS="--inventory selected-assets/inventory.json --output dist/site-preview"`.
Existing output is preserved. Both builders run in a temporary sibling directory;
a failed renderer, archive validation or repository generation leaves no partial
site. Guide image paths cannot occupy the reserved `repo/` directory. Replaying
the same inputs with the same Blender executable produces identical site files.

`tools/build_site.py` accepts `--source`, `--template` and `--manifest`, plus the
shared `--blender`, `--artifacts` and `--timeout` options. `BLENDER` discovery works
as for other native tools. Blender validates the exact archives and generates the
repository in a fresh isolated profile with online access disabled; successful
profiles are cleaned, and logs remain outside the site under `.blender-profile`
(or `--artifacts`). The command makes no downloads and needs no publishing
credentials. Offline tests use synthetic archives and a fake native command
boundary; they do not establish native runtime or update acceptance.

This builds a local snapshot only. The separate publication workflow handles
online provenance verification, fresh inventory discovery and Pages deployment.
Release handbook attachment belongs to the release workflow; production acceptance
remains under #37. See the
[release procedure](docs/RELEASING.md#offline-extension-repository-snapshots)
for retention and publication requirements.

The read-only [Site preview workflow](.github/workflows/site-preview.yml) runs on
PRs changing the builder or handbook. Its `site-demo` artifact retains PDF previews
and the complete local site for 14 days. It uses synthetic versions covering two
Blender compatibility ranges, validated and indexed with Blender 5.0.1. These are
review fixtures, not releases: do not install or publish them. The PDFs use system
fonts without external font requests. This job needs no service credentials or
deployment permissions and does not change the publishing gate.



### Screenshots

Keep screenshots in `docs/images` as PNG files and inspect them at their intended
reading size. Reference each one from the guide or another document with alt text
of at least eight words describing the visible controls or result, not a lane
label or filename. Do not show credentials, account identifiers or private data.
Clearly label offline examples and distinguish them from live-provider evidence.

The repository CLI owns image selection, optimization and checks. From the
repository root, discover commands and check the current assets with:

```sh
uv run --locked --no-env-file python -m tools --help
uv run --locked --no-env-file python -m tools assets check
```

The default selection is every PNG directly inside this checkout's `docs/images`.
There is no output path to choose. Preview eligible files, optimize them, or name
one screenshot using its basename:

```sh
uv run --locked --no-env-file python -m tools assets optimize --dry-run
uv run --locked --no-env-file python -m tools assets optimize
uv run --locked --no-env-file python -m tools assets optimize panel-image.png
```

`make images` and `make images-check` call these same optimize/check commands.
The module entry point is repository tooling, not an installed global command.
Asset discovery uses its checkout location rather than the caller's working
directory. Explicit selections accept PNG basenames in their exact stored spelling;
missing files, directories, symlinks, animated PNGs and paths outside the image
folder fail before optimization.

Optimization requires [pngquant](https://pngquant.org) (`brew install pngquant` or
`apt-get install pngquant`) only when a non-palette screenshot needs work. It uses
the shared 70-90 quality target without dithering and leaves existing palette
PNGs alone, so repeat runs do not accumulate lossy changes. Dry runs validate and
report eligible inputs without requiring or invoking the optimizer or changing
files; they do not predict the resulting quality or savings.

Each optimizer process has a 30-second deadline. A temporary candidate must have
valid PNG chunk structure/checksums, the original dimensions, palette colour and
fewer bytes before it atomically replaces that one file. Source changes detected
during optimization reject the replacement. Failures clean up temporary outputs
and preserve the affected original; files completed earlier in a batch remain
optimized. Structural checks are not a complete PNG pixel decoder. Compare the
result with the original before committing; small text must stay legible.

After optimization, the command checks the whole documentation image inventory.
Exit 0 means no policy problems remain. Exit 1 reports image-policy problems or
pending dry-run work; exit 2 reports an invalid input, missing tool or optimizer
failure. pngquant statuses 98 (would not shrink) and 99 (minimum quality unmet)
retain the original and print the reason. An unchanged truecolour screenshot
still fails readiness; reshoot it or review a justified exception in
[the shared image policy](tools/docs_images.py), rather than changing a test alone.

The original `scenario-logo.png` is deliberately excluded: its unchanged upstream
bytes and separate licence record are preserved. The image checks permit that
one provenance exception; the CLI and unit suite share checks for missing images,
unused screenshots, truecolour screenshots and short Markdown alt text. Evidence
frontmatter cannot keep an unused image alive. The social card, when present,
has an explicit unused-image exception because GitHub repository settings host it.
Optimization also preserves this generated card; use its dedicated generator
below to rebuild it. An invalid non-palette card still fails the shared checks.
Generated website and handbook output remain untracked. Optimization does not
generate alt text, remove unused files or establish native UI acceptance.

### Repository sharing card

Regenerate `docs/images/social-preview.png` after changing its source artwork,
the logo or the manifest tagline:

```sh
uv run --locked --no-env-file python tools/make_social_preview.py
```

The generator uses pinned development-only Pillow and its bundled default font;
Pillow is not in the extension's SDK wheel bundle. The generated card is a
1280 by 640 palette PNG below one megabyte. `make images` leaves this generated
file alone. Regeneration is byte-identical in the verified environment; inspect
rendering differences when switching platforms.
The tagline comes from the extension manifest. The original logo and its separate
licence remain unchanged; the black background matches that upstream asset.

The checked-in [source artwork](tools/assets/social-preview-artwork.png) shows
an ivory stone and oxidized copper sculpture in a dark architectural environment.
It is AI-generated promotional illustration, not a screenshot or proof of a
Scenario generation. Its [source note and prompt](tools/assets/social-preview-artwork.LICENSE)
record that distinction. It is independent of the guide's screenshots.

For a replacement, pass `--artwork PATH`: use a 2:1 image of at least 1280 by 640
pixels, with the subject on the right and a dark left area for the title.
The generator adds a black fade behind the typography and keeps the original
logo's opaque background seamless. `--font PATH` accepts an optional font and
`--output PATH` writes a review copy. Inspect the whole card at thumbnail size
for legibility, trademarks and private account details before uploading.

After the image PR merges, an authorized repository admin uploads the merged
`docs/images/social-preview.png` under Settings > General > Social preview.
A committed PNG does not configure GitHub's sharing image. Verify the repository's
`usesCustomOpenGraphImage` and `openGraphImageUrl` after upload, then check a share
preview in the intended client. Actual upload and client acceptance remain #19;
this generator neither changes repository settings nor sends a share message.
