# Validation and local commands

The active assistant runtime needs no generation credentials. Use isolated
native assistant tests for its current behavior. The
[environment reference](../../CONTRIBUTING.md#environment-variables) also retains
explicit upstream service-maintenance commands; those are outside normal setup.

Use the uv version required by `pyproject.toml`. Run `uv sync --locked` to create
the worktree's `.venv` from `uv.lock` and the interpreter in `.python-version`.
`make test` runs `uv run --locked --no-env-file python -m pytest`; use the same prefix for
focused tests. Dependency and interpreter changes follow
[Python style](../PYTHON_STYLE.md#dependency-changes). Blender native tests still
use Blender's bundled Python and an isolated profile. Preserve exit codes when
capturing logs, and distinguish passed checks from checks that were not run.

[CI](../../.github/workflows/ci.yml) runs Python lint, unit tests, SDK contracts,
the Linux/Windows Blender baseline, knowledge checks and agent-skill validation
as reusable workflows on pull requests and pushes to `main`. The `ci-ok` job
waits for all six workloads in that run and passes only when each succeeds;
failed, cancelled, skipped or missing results fail the gate. PR updates cancel
older CI runs for that PR. Concurrency belongs to the caller so reusable
workflows cannot cancel their parent run.

Individual job names can include their caller prefix; artifact names and native
matrix coverage are unchanged. The separate `pr-title` and `commits` checks
remain independent. The [current repository rules](../MAINTAINERS.md#rulesets)
require `ci-ok`, `pr-title` and `commits`; the workflow itself does not change
those settings. Keep their reported names synchronized with the ruleset.

[Workflow contract tests](../../tests/unit/test_workflows.py) follow PR callers
into their local reusable workflows and composite actions to check the explicit
read-only/no-secrets convention. They also check distinct declared job contexts
and run the actual `ci-ok` shell with every workload successful, failed,
cancelled, skipped, empty or absent. Every workload must appear in the gate's
dependencies and result inputs. These stdlib checks cover the repository's
block-style workflow subset and reject unsupported trigger/permission forms;
they do not replace general YAML/action validation or GitHub's actual scheduling,
permissions and check-result evidence. Python lint runs `make lint` over the
whole tree.

[Weekly OS checks](../../.github/workflows/blender-os.yml) are separate,
informational schedule/manual jobs for Blender 5.1.2 on macOS Apple silicon and
Windows x64. They fetch pinned official archives and run the full installed native
suite in new profiles, as one Blender process with a 900-second `--timeout`
because hosted macOS can exceed the 300-second default. A separate reporter opens
or updates one issue per failed OS; reporting does not turn a failed job green.
See [matrix maintenance and failure reporting](../../CONTRIBUTING.md#bumping-the-blender-matrix).

[Unit-test CI](../../.github/workflows/unit-tests.yml) runs the complete unit suite
on pinned Python 3.11 and 3.13 interpreters with the same locked dependencies and
dotenv loading disabled. It needs no credentials.

Each unit matrix leg measures statement coverage for `scenario/core` and
`scenario/mcp` using the pinned pytest-cov development dependency and locked
coverage.py version. The log and Actions summary contain per-file totals;
`unit-coverage-3.11` and `unit-coverage-3.13` retain XML and browsable HTML reports
for 14 days, including when tests fail. Coverage is measured without a minimum
threshold. The reports include unexecuted files in those directories; Blender-only
MCP modules can therefore show zero coverage. These reports do not measure native
Blender behavior or subprocess execution and do not prove live service acceptance.

To generate the same XML and HTML reports locally from the repository root:

```sh
uv run --locked --no-env-file python -m pytest tests/unit -rx \
  --cov --cov-report=term --cov-report=xml --cov-report=html
```

Open `htmlcov/index.html` to inspect missing lines. Report paths and `.coverage`
data are ignored by Git. The coverage source root and report scope live in
`pyproject.toml`, so `--cov` needs no value; reports store relative source paths
for portability and preserve distinct `core/` and `mcp/` filenames.

An autouse fixture rejects non-loopback `socket.connect` and `connect_ex` calls
in each unit test. Loopback TCP and Unix sockets remain available for local MCP
tests. `@pytest.mark.allow_network` explicitly opts a test out; unknown markers
fail collection. This catches accidental service connections in the pytest
process, not DNS lookups, subprocesses or arbitrary network code. Paid/live
checks belong in their separate opt-in tools, never the default unit suite.

`make format` applies safe Ruff fixes and formatting; `make lint`, which CI runs
over the whole tree, checks both. Follow [Python style](../PYTHON_STYLE.md#whole-tree-lint)
for path scoping, mechanical reformatting and Blender registration/annotation
cautions.

`make check-rules` runs the currently implemented offline house rules. Its
`actions-pinned` rule checks remote workflow and composite-action references;
the unit suite exercises failures and checks the repository, so no extra CI
job is needed. See [the contribution guide](contributions.md#github-actions-updates)
for supported declaration syntax, explicit file selection and remaining scope.

`make test-blender` owns fresh disposable profiles for its entire build/install/test
sequence, including probes. It strips inherited credentials and Blender/Python path
overrides, verifies installed ZIP contents and reports actual runtime versions.
See [the native test loop](../../CONTRIBUTING.md#native-blender-test-loop) for
binary discovery, `tools/blender_env.py` and version ordering. Use `BLENDER` or
`--blender` to select a supported binary; an invalid explicit selection exits with
status 1 rather than falling back to another installation, and discovery does not
prove compatibility.

| Environment | Available checks | Limits |
| --- | --- | --- |
| Desktop with supported Blender on macOS, Linux or Windows | Locked unit checks, exact-ZIP build/validation, isolated headless tests, GUI capture | A working desktop session is required for capture; inspect its PNG and record actual OS/Blender versions. Discovery is not compatibility proof. |
| Linux x64 without a display | Locked unit checks, build and isolated headless tests using an installed or explicitly fetched Blender | The official fetcher verifies archives. No GUI capture or input/rendering acceptance is established. |
| Sandbox without Blender | Locked unit checks, `make lint`, knowledge and agent-skill checks; manifest-version output | Build, native and GUI checks cannot run. State those omissions in the PR so a reviewer or CI can supply the missing evidence. |

Build/install/test/capture commands always own fresh disposable profiles. The
[isolated command wrapper](../../CONTRIBUTING.md#isolated-blender-commands) also
owns a fresh profile for trusted local probes, without installing an extension.
None of these tools has a normal-profile mode. The unmanaged native-suite guard
exits with status 2 before importing Blender; use `make test-blender` for acceptance.
Capture artifacts use unique directories rather than overwriting a fixed PNG.
GUI probes never spend credits; capture requires a desktop session with the
necessary display access, which may be unavailable to a cloud agent.

The portable build/install tools also create fresh isolated profiles and scrub
inherited credentials and path overrides. For every direct Blender invocation outside these tools,
including probes, export an absolute disposable profile:
`export BLENDER_USER_RESOURCES="$PWD/.blender-profile"`.
Never install development builds into the user's normal profile.

Source builds run Blender's `extension validate` against `scenario/` before
staging dependencies or building an archive. The isolated runner records this
as `validate-source.log`, so malformed manifest metadata fails at a distinct
step on every Linux/Windows Blender CI version. A validation failure stops
packaging and installation and preserves its exit status and diagnostic logs.
The finished ZIP is still validated separately. `--zip` and `--no-build` skip
this source preflight, not the archive identity, ZIP, licence, SDK bundle or
installed-byte checks; see [the native test loop](../../CONTRIBUTING.md#native-blender-test-loop).

Output streaming and `--log` follow the same native test loop. A forwarding error
fails an otherwise successful command without discarding the phase log; healthy
destinations continue after another destination fails. If Blender itself fails,
its exit status takes precedence. Failure to open or write the requested log
before a phase prevents that phase from starting; a log-close failure also fails
the command because the requested evidence is incomplete.
`--timeout` bounds each Blender child process, not the runner's total wall-clock
time: terminal and filesystem I/O can block. The runner waits for its forwarding
thread before closing the log, so it does not leave a writer using closed storage.
Inside Blender, the installed suite prints each failure's traceback as soon as
its test ends, not only in unittest's closing summary, so a run that `--timeout`
stops still records why earlier tests failed. A test running longer than 60
seconds also dumps every thread's stack, and the run continues. Keep each wait in
a native test bounded well below that timeout, so a condition that never arrives
fails its own test instead of using up the whole run's time.

The separate [native update fixture](../../tools/test_repository_update.py) tests
Blender's repository install and update mechanism with two synthetic extension
ZIPs. Run it with the selected binary:

```sh
uv run --locked --no-env-file python tools/test_repository_update.py \
  --blender /path/to/blender --expected-version 5.0.1
```

It validates and generates both repositories through the existing exact-inventory
helper, installs the first ZIP, and uses native refresh/update operators to load
the second version in the same Blender process while preserving enabled state.
Installed bytes must match each exact artifact. This tests the native mechanism;
it does not certify a published Scenario release, hosted repository, UI controls,
or production add-on upgrade behavior.

The fixture serves only its generated index and ZIP filenames on `127.0.0.1`.
Its disposable profile contains only that repository; Scenario credential variables,
Blender/Python path overrides and proxies are removed. Native network operations
use `--online-mode` for this loopback repository, without changing the normal
profile's online-access setting. This is configured loopback isolation, not a
sandbox policing arbitrary subprocess sockets. Do not add external destinations
to the fixture. The runner checks that the normal profile's file metadata is
unchanged and refuses artifact directories inside that profile.

The loopback server stops on success and failure. Successful profiles and
temporary files are removed; failed profiles remain with logs for diagnosis.
Reports, synthetic ZIPs and native logs stay under `.blender-profile/update-*`
(or `--artifacts`). The Linux and Windows baseline matrix runs this separate
fixture on Blender 5.0, 5.1 and 5.2 and preserves its evidence even on failure.

### Scenario package state across updates

The same runner accepts two exact adopted Scenario ZIPs to exercise the real
extension, including its dependencies, through native installation and upgrade:

```sh
uv run --locked --no-env-file python tools/test_repository_update.py \
  --blender /path/to/blender --expected-version 5.0.1 \
  --previous-zip artifacts/scenario-PREVIOUS.zip \
  --candidate-zip artifacts/scenario-CANDIDATE.zip
```

For a selected release pair, both ZIP options are required together. The candidate must have a newer stable
version and the same `scenario` identity. The runner copies the selected archives
without rebuilding or rewriting their contents, generates each repository through
the existing validator, and verifies installed bytes before and after upgrade.
Only the index and the two explicit archive filenames are served on loopback.
The default invocation still runs the small synthetic fixture above.

The package probe seeds synthetic saved preferences, credentials and a nonempty
Project ID, six durable job states (prepared, uncertain, remote, download failed, ready and applied), a
second credential scope with the same project, result manifests/receipts and
local result bytes,
three image upload states plus an imported captured-mesh upload with verified
staged bytes, and a saved blend with a reference.
It compares these after native upgrade in the same process and after reopening
the blend in a second, offline Blender process. Exact quote strings, nonempty texture roles, application
origins, upload markers and the installation's scope key must survive. Original
reference bytes are checked too. The saved project preference and runtime job scope
must agree; the same key with default scope or another project must see no jobs,
uploads or Film upload association. Both native reports require
`project_scope_preserved: true`. Packages without the optional project preference
need their matching historical probe and cannot establish this coverage.
Ready/applied jobs retain the exact mesh input
binding, export hash, source transform and upload revision. The completed job
retains successful, confirmed-failed and unfinished local application claims;
the probe attempts another claim and requires rejection without changing the
record before update, after update and after restart. These fixtures require
packages with the captured-source and local-reuse storage APIs. They do not
restore live object authority or replay scene application. The actual package's storage APIs perform reads
and verification; no mocked add-on replaces the installed extension.

When the installed predecessor exposes Film task storage, all six selected jobs
also retain their production/task and recipe hashes. When Film upload storage is
available, the captured mesh has a separate saved Film upload association; the
snapshot compares its complete identity and checks that the other credential
scope cannot read it. This association lives outside serialized job/upload
records, so checking those records alone is insufficient. Older packages record
empty Film fields; that is not Film acceptance. Check `expected-state.json` for
nonempty task bindings and `film_upload` before claiming this coverage. Adding
Film storage to a package must not remove the captured-mesh, texture-role or local
reuse checks above.

Both native reports also record the job database schema read before and after
the update (`store_schema`), and the runner requires the versions declared by the
two archives' `core/jobs/store.py`; a candidate with an older schema fails before
installation. When the predecessor already has schema 10 or later, the probe
seeds a ready job whose saved result is a declared EXR original with an equirectangular
projection, a saved `image` lane default and a cleared `render_image` default
(`schema_10_state: before-update`). When only the candidate has it, the
first post-update store access upgrades the predecessor's database; after the
unchanged-state comparison the probe writes the same schema 10 state into the
upgraded store, and the offline restart must preserve it
(`schema_10_state: after-upgrade`). Both forms require the original's receipt,
the exact default, the cleared lane's revision and that the other credential scope
sees no default. The project check also requires the same credentials with the
default scope or another project to see no saved or cleared default.

When the package seeding the defaults ships the runtime
[lane-defaults owner](../JOB_STORAGE.md#runtime-owner), the probe saves and clears
them through `runtime.ensure_model_defaults` instead of the store. After the update
and after the offline restart it reads every seeded lane through the candidate's
owner, whose scope must equal the selected store's and whose states must equal the
stored rows. Both reports record `model_defaults_preserved`; the runner requires it
to be true exactly when the candidate archive contains `core/jobs/model_defaults.py`.

When the candidate declares a newer schema than the predecessor, the runner then
starts one more offline Blender process with factory settings, so neither package
loads. It imports only the predecessor ZIP's own `core` storage code under a
private package name and opens the upgraded `shared-jobs/jobs.sqlite3` with it.
That older code must raise its unsupported-format `StoreError`, and every file in
the store directory must keep its exact bytes. `result.json` records this as
`predecessor_reopen` with the predecessor's schema. Packages without a shared job
store, including every published release through 0.9.9, cannot run this probe.

When the predecessor exposes saved workflow Library bindings, the probe also
seeds a workflow schema, a Unicode prompt and single/array reference inputs. It
compares the complete form signature, canonical parameter values and persisted
scope/value bindings after update and offline reopen. The parameter builder must
reject the marked inputs under a different credential and default/alternate
project stores. Both reports include `workflow_references_preserved`, which must
be true when `expected-state.json` contains a workflow form. Predecessors without
these fields report false and cannot establish workflow preservation. They still
run all applicable job/upload/scene checks; the probe does not invent prior state
for newly introduced properties.

When the predecessor's workflow inputs also expose `upload_path`, the probe seeds
a completed upload's blend-relative path and request ID beside the image binding.
A second scene holds a pending upload marker, request ID and path on an array
input; its parameter builder must refuse pricing for that marker. Snapshots
compare these fields and that form's signature after update and offline reopen.
Both reports then include `workflow_uploads_preserved: true`; it is false when the
expected workflow state has no pending upload form. Upload fields are recorded
only when present, so an unmarked form keeps the same signature and snapshot as
a predecessor without them.

A saved blend stores each scene's lane tab as its item index. The probe sets the
seeded scene's tab to Blockout, the last tab before Environment was appended, and
the scene snapshot requires that tab after update and offline reopen. When the
predecessor has the Environment lane, a second saved scene also seeds that tab,
its HDRI mode, a 3072 x 1536 size, its prompt and model choice, a saved
panorama reference with scope, asset, kind and projection markers, and a checked
upload's reference with its measured 2:1 size marker. Both reports
then include `environment_lane_preserved: true`, which the runner requires
exactly when `expected-state.json` contains that Environment state. Older
predecessors report false: they show only that an older saved tab survives the
appended item.

When the predecessor saves trained-model choices on generation forms, the probe
seeds two LoRAs with strengths and ranges on the Image form, including a Unicode name,
each tagged with the selected connection's scope. After the update and the offline
reopen it compares their IDs, names, kinds, strengths, ranges and scope tags. The
candidate's trained-model scope check must accept them for the selected store and
refuse them under the other credential scope and under the same credentials with
the default scope or another project. Both reports include
`trained_selection_preserved`, which must be true exactly when
`expected-state.json` contains a trained choice; predecessors without these
properties report false.

The probe rejects Python socket connections while seeding/checking/upgrading.
Blender's native updater uses its configured loopback repository; this is not an
OS sandbox for arbitrary package code or subprocesses. Use only trusted archives.
The run strips inherited credentials and proxies and never uses a normal profile.
This is local artifact/state acceptance, not hosted download, provenance,
physical GUI interaction, a paid journey or production-release authorization.
The fixture requires the adopted shared store/reference APIs; it performs no
prototype migration. A test-only predecessor with altered version metadata can
exercise lifecycle behavior before two adopted releases exist, but must be named
as synthetic evidence and cannot prove compatibility between published releases.
Repeat with verified release artifacts for production update acceptance.

For explicit CI lifecycle testing before a published predecessor exists:

```sh
uv run --locked --no-env-file python tools/test_repository_update.py \
  --candidate-zip artifacts/scenario-CANDIDATE.zip --test-predecessor
```

This mode derives a synthetic `0.0.0` predecessor from the candidate, replacing
only its two matching version declarations in the manifest and package initializer.
All other entry contents remain unchanged; the supplied candidate is never
rewritten. Missing/mismatched declarations fail. The result records
`test_predecessor: true`, `predecessor_code` and both artifact hashes. These test
archives must never become release or public repository inputs.

To upgrade real predecessor code that still carries the candidate's version, such
as a ZIP built from the previous main commit before a storage migration, add
`--previous-zip`. The `0.0.0` predecessor is then derived from that exact ZIP's
code (`predecessor_code: previous-zip`, with its original `previous_sha256`):

```sh
uv run --locked --no-env-file python tools/test_repository_update.py \
  --previous-zip artifacts/scenario-PREVIOUS-COMMIT.zip \
  --candidate-zip artifacts/scenario-CANDIDATE.zip --test-predecessor
```

Build the previous ZIP with that commit's own `tools/build.py`. This exercises the
actual schema upgrade through Blender's updater; like the candidate-derived mode,
it is synthetic version evidence, not published release-pair acceptance.

Each Linux and Windows baseline job runs both the small fixture and this
actual-package lifecycle check for Blender 5.0, 5.1 and 5.2. It requires exactly one
ZIP from that job's successful native run and preserves update/restart reports,
logs, indices and both test archives on failure as well as success. A failed
package check fails its matrix job and the existing aggregate gate. Hosted results
remain evidence for their actual run, platform and archives; this CI mode does
not establish published release-pair compatibility or public update availability.

- `make build`: build and validate the extension ZIP; use
  `BLENDER_BUILD_ARGS="--repo"` to also generate a local extension repository.
- `make install`: build and install into a new isolated profile; use
  `BLENDER_INSTALL_ARGS="--launch"` to open it.
- `make test-blender`: build/validate/install an exact ZIP, then run the offline
  baseline in a new profile. No prior installation is needed. Logs and ZIP remain
  under `.blender-profile/run-*`; successful profiles are removed. Use
  `BLENDER_TEST_ARGS="--suite all"` for the full existing integration suite.
  For a targeted fix, use repeated `--test` arguments, for example
  `uv run --locked --no-env-file python tools/test_blender.py --test test_provider_setup --test test_agent_artifacts`.
  Each name can select a module, class or method. The runner still verifies the
  exact installed ZIP, isolated profile, network guard and extension lifecycle;
  its report identifies this as a focused run and records the selected tests.
  A focused pass does not establish baseline or full-suite acceptance.
- `make gui-check`: build and capture an exact ZIP in a fresh offline GUI profile.
  `LANE=video` selects a lane; the default is `image`. An explicit `--lane` in
  `BLENDER_GUI_ARGS` takes precedence over `LANE`.
  Use `BLENDER_GUI_ARGS="--view composer --lane image --fixture form"` for a
  synthetic form, or `BLENDER_GUI_ARGS="--zip /path/to/scenario.zip"` to capture
  an existing artifact. A desktop display is required. Each capture keeps its
  PNG, ZIP, report and logs in a unique directory under `workdir/screenshots/`;
  successful disposable profiles are removed. Inspect the PNG before claiming
  visual acceptance. GUI probes never spend credits.
- For package changes, inspect the resulting ZIP, validate it and check its
  license content. Keep root `LICENSE` and `scenario/LICENSE` identical.
- For UI changes, exercise native behavior and inspect captured screenshots.
  Report actual Blender/OS versions and limitations.
- For instruction or documentation changes, check links, symlinks, commands and
  the diff. Do not run the full Blender suite just to populate a test count.
- Smoke tests, generation, prompt helpers and scene design can spend credits.
  Do not run them as part of a documentation command.

Use `blender --background scene.blend --command open_studio` for local MCP.
Set `SCENARIO_BLENDER_TOKEN` for an explicit token, or use the generated session
token shown at startup. Online access must be enabled; SIGINT/SIGTERM stop the
server cleanly. Run it only in the isolated development profile described above.
