# Maintainers

This document is for repository admins and for agents that change repository
settings. Contributors read [CONTRIBUTING.md](../CONTRIBUTING.md); the release
procedure is [RELEASING.md](RELEASING.md). Every setting below is read back with
the command next to it; when a setting changes, the same pull request updates
this document.

The tables distinguish the inspected state from pending decisions. They do not
authorize settings changes. Re-read GitHub before acting: source checks cannot
prove that remote configuration still matches this record. Keep credentials,
raw private project exports and collaborator details out of public evidence.

## Branch model

`main` is the only long-lived development branch. Changes arrive through pull
requests from forks or short-lived `type/issue-description` branches and are
squash-merged. The Conventional Commit PR title becomes the commit header on
`main`; preserve contributor trailers in the squash body. See the
[commit guide](development/contributions.md).

Do not push directly to `main` or create release tags by hand. Release-please
owns the release PR, version changes and publication flow. New tags use
`blender-plugin-vX.Y.Z`; historical `v*` tags remain protected. Merging a release
PR authorizes publication, not just packaging validation. An older-release fix,
if needed, uses an explicitly reviewed, on-demand `release/X.Y` branch and
release-please target-branch configuration; the current workflow targets `main`.
There is no standing `develop` branch. Releases are ZIPs attached to tags, and
merging ordinary product work is not a deployment of the extension.

```sh
gh api repos/scenario-labs/blender-plugin/branches --paginate --jq '.[].name'
gh api repos/scenario-labs/blender-plugin --jq .default_branch
```

## Rulesets

All four listed rulesets are active. Empty exclusions apply to the listed
repository ref patterns.

| Ruleset | ID / target | Inspected rules | Bypass |
| --- | --- | --- | --- |
| main - integrity | `22257774`; branch `refs/heads/main` | No deletion or non-fast-forward updates; linear history; pull request with squash only and zero required approvals; extra approval for unattributed changes. Code quality severity `errors`. CodeQL security threshold `high_or_higher`, alert threshold `errors`. Required status checks `ci-ok`, `pr-title` and `commits`, all integration `15368`; strict up-to-date policy off, enforcement on creation on. | None |
| main - review | `22257776`; branch `refs/heads/main` | Pull request with squash only; one approval, dismiss stale reviews, resolve review threads, extra approval for unattributed changes. | `RepositoryRole` `5` (repository admin), `always` |
| versioning | `22257778`; tags `refs/tags/v*` and `refs/tags/blender-plugin-v*` | Restrict creation, updates, deletion and non-fast-forward updates. | Release App integration `4751046`, `always`. The repository-admin bypass was removed on 2026-10-13 after 0.10.0 was published (#36). |
| Default security | `3247630`; inherited organization repository policy | Restrict repository creation, deletion and transfer. | `OrganizationAdmin`, `always` |

Both branch pull-request rules have code-owner approval and last-push approval
off, no specific required reviewers, and dismissal restrictions disabled. The
integrity rule also has stale-review dismissal and thread-resolution requirements
off; the review rule supplies those requirements. Admin review bypass does not
bypass integrity: direct pushes, force pushes and deletion of `main` remain
prohibited. Other contributors need the configured review approval; unattributed
changes require an additional approval. Tag restrictions have their own bypasses.

On 2026-10-09, `ci-ok` from [CI](../.github/workflows/ci.yml) was added to
required status checks after its successful GitHub Actions check identity
(integration `15368`) was verified. API readback confirms that every other
integrity rule, condition and bypass actor is unchanged; the review ruleset is
unchanged too. CodeQL and code-quality enforcement remain in the integrity
ruleset. Preserve the existing `commits` and `pr-title` checks.

The [negative title check](https://github.com/scenario-labs/blender-plugin/actions/runs/37947533724)
on [the administration PR](https://github.com/scenario-labs/blender-plugin/pull/343)
failed for a deliberately nonconventional title while GitHub reported merging
blocked. After the conventional title was restored,
[the title check passed](https://github.com/scenario-labs/blender-plugin/actions/runs/37947630915),
alongside `commits` and [required `ci-ok`](https://github.com/scenario-labs/blender-plugin/actions/runs/37947534205).
This verifies the required
failure path without attempting a merge or changing bypass rules; human review
and the other required checks remain independent gates.

Renaming a required check can block every merge until an administrator updates
its ruleset context. Coordinate the workflow and settings changes, then update
this record. Do not replace a ruleset from a historical example; read its complete
conditions, rules and bypass actors before an approved update.

```sh
gh api repos/scenario-labs/blender-plugin/rulesets
gh api repos/scenario-labs/blender-plugin/rules/branches/main
gh api repos/scenario-labs/blender-plugin/rulesets/22257774
gh api repos/scenario-labs/blender-plugin/rulesets/22257776
gh api repos/scenario-labs/blender-plugin/rulesets/22257778
gh api repos/scenario-labs/blender-plugin/rulesets/3247630
```

## Merge settings

| Setting | Inspected value |
| --- | --- |
| `allow_squash_merge` | `true` |
| `allow_merge_commit` | `false` |
| `allow_rebase_merge` | `false` |
| `allow_update_branch` | `true` |
| `delete_branch_on_merge` | `true` |
| `allow_auto_merge` | `true` |
| `squash_merge_commit_title` | `PR_TITLE` |
| `squash_merge_commit_message` | `COMMIT_MESSAGES` |
| `web_commit_signoff_required` | `false` |

`COMMIT_MESSAGES` retains branch commit messages for the proposed squash body.
Review the final body and co-author trailers before merging; the setting alone
does not verify attribution or release notes. There is no DCO sign-off or
copyright assignment; the [contribution licensing policy](../CONTRIBUTING.md#licensing-of-contributions)
applies. Release-please's configuration and proposed release PR remain the
source of truth for resulting notes and versions.

```sh
gh api repos/scenario-labs/blender-plugin --jq '{allow_squash_merge,allow_merge_commit,allow_rebase_merge,allow_update_branch,delete_branch_on_merge,allow_auto_merge,squash_merge_commit_title,squash_merge_commit_message,web_commit_signoff_required}'
```

## Security baseline

| Capability | Inspected state / remaining scope |
| --- | --- |
| Secret scanning, push protection, non-provider patterns, validity checks | Enabled |
| AI secret detection, delegated alert dismissal, delegated bypass | Disabled |
| Dependabot alerts and security updates | Enabled; automated fixes are not paused |
| Private vulnerability reporting | Enabled; use [SECURITY.md](../SECURITY.md) |
| CodeQL default setup | Configured; default suite, remote threat model, weekly, standard runner. API language values: `actions`, `javascript`, `javascript-typescript`, `python`, `typescript`. |
| Actions | Enabled; allowed actions `all`; full-SHA pinning required since 2026-10-13 (`sha_pinning_required: true`, read back) |
| Default workflow token | `read`; cannot approve pull requests |
| Fork PR workflow approval | `all_external_contributors` |
| Immutable releases | Enabled on 2026-10-13 after 0.10.0 was published and verified (#36); not enforced by the owner. 0.10.0 predates the setting and is not itself immutable. |

[Dependabot](../.github/dependabot.yml) updates GitHub Actions weekly, seven days
after each release at the earliest.
The extension also has pinned Python/SDK dependencies and bundled wheels; the
old stdlib-only assumption is obsolete. Their coordinated update procedure is
[SDK_BUNDLE.md](SDK_BUNDLE.md), not automatic version updates from the Actions
configuration. Source checks enforce full action SHA pins, and since 2026-10-13
the repository's Actions settings also refuse an unpinned action. Allowed actions
stay `all` by the maintainer's decision (#39).

```sh
gh api repos/scenario-labs/blender-plugin --jq .security_and_analysis
gh api repos/scenario-labs/blender-plugin/private-vulnerability-reporting
gh api repos/scenario-labs/blender-plugin/code-scanning/default-setup
gh api --include repos/scenario-labs/blender-plugin/vulnerability-alerts # 204 means enabled
gh api repos/scenario-labs/blender-plugin/automated-security-fixes
gh api repos/scenario-labs/blender-plugin/actions/permissions
gh api repos/scenario-labs/blender-plugin/actions/permissions/workflow
gh api repos/scenario-labs/blender-plugin/actions/permissions/fork-pr-contributor-approval
gh api repos/scenario-labs/blender-plugin/immutable-releases
```

## Features and metadata

### Supply-chain report

[Scorecard CI](../.github/workflows/scorecard.yml) publishes after pushes to
`main` and each Monday. The [public report](https://scorecard.dev/viewer/?uri=github.com/scenario-labs/blender-plugin)
and README badge reported **6.4** for `5d1f5a2` on 2026-09-25. The
[default-branch run](https://github.com/scenario-labs/blender-plugin/actions/runs/36132873905)
succeeded, and its Scorecard SARIF analysis appears alongside CodeQL without an
upload error or a finding against the Scorecard workflow itself. Scorecard is
informational and is not a required check. This is a dated publication check,
not a claim that its findings are resolved or that the extension is accepted.

Use the [triage procedure](development/contributions.md#scorecard-triage) for
current results. Paginate code-scanning analyses: frequent CodeQL uploads can
push the latest Scorecard result beyond the first API page.

### Repository presentation

Issues and projects are on; wiki and Discussions are off. Keep documentation in
this repository, and route questions through [SUPPORT.md](../SUPPORT.md), the
issue forms or `support@scenario.com`. The approved direction keeps Discussions
off; revisit after external participation justifies a monitored Q&A channel.
The wiki is already off and needs no further change under #20.

Pages is configured with build type `workflow`, no custom domain and enforced
HTTPS, so it serves the site at `https://scenario-labs.github.io/blender-plugin/`.
A Vercel project serves `blender.scenario.com` in front of it, as described in
[site hosting and download counts](#site-hosting-and-download-counts).
The handbook at `https://blender.scenario.com/` serves the
installation, update and support guidance; the repository homepage and
[extension manifest](../scenario/blender_manifest.toml) Website link point there.
The native repository at `/repo/index.json` has listed the published releases
since 0.10.0 on 2026-10-13; check the live index after each release. The Pages
API's `status: null` is not a content check; verify the live response and page
before changing these links again.

The description is:

> Scenario for Blender: AI images, video, 3D and PBR materials in the viewport, plus a local MCP server for agents. Blender 5.0+ extension, GPL-3.0-or-later.

The repository has a custom social preview since 2026-10-13: `usesCustomOpenGraphImage`
is `true`, its image URL uses `repository-images.githubusercontent.com`, the
repository page's `og:image` points there, and the served image is byte-identical
to the committed `docs/images/social-preview.png`. To change the card, regenerate
it with `uv run --locked --no-env-file python tools/make_social_preview.py` and upload
`docs/images/social-preview.png` by hand through Settings > General > Social
preview > Edit > Upload an image. Use the merged artifact, and re-upload after
any approved card change. Confirm `usesCustomOpenGraphImage` is `true`, the image
URL uses `repository-images.githubusercontent.com`, and the rendered shared-link
preview matches the intended card. A committed PNG does not prove the admin
upload or downstream preview acceptance.

Topics are `3d`, `agentic-ai`, `ai`, `blender`, `blender-addon`,
`blender-extension`, `generative-ai`, `mcp`, `mcp-server`, `python`, `scenario`,
`text-to-3d`, `text-to-image` and `text-to-video`.

```sh
gh api repos/scenario-labs/blender-plugin --jq '{has_issues,has_projects,has_wiki,has_discussions,has_pages,homepage,topics,description}'
gh api repos/scenario-labs/blender-plugin/pages
gh api graphql -f query='{ repository(owner:"scenario-labs", name:"blender-plugin") { usesCustomOpenGraphImage openGraphImageUrl } }'
```

### Site hosting and download counts

`blender.scenario.com` is a Vercel project built from this repository with Root
Directory `tools/site_proxy`, framework preset Other, no build or install
command, production branch `main`, and deployments skipped when that directory
is unchanged. [vercel.json](../tools/site_proxy/vercel.json) forwards every path
to the Pages address above, and redirects `/repo` to `/repo/`, which Pages would
otherwise redirect to its own host. [.vercelignore](../tools/site_proxy/.vercelignore)
uploads only that file and the middleware, so no other file in the directory
shadows a forwarded path. Production deployments stay public because Blender
cannot authenticate. The DNS CNAME `blender` points to the target Vercel shows
for the domain. Cutover and acceptance are tracked in #492.

[The middleware](../tools/site_proxy/middleware.ts) runs on `/repo/*` and sends
one anonymous PostHog event per counted request after the request continues:
`extension_downloaded` for `GET /repo/scenario-X.Y.Z.zip` and
`repository_checked` for `GET /repo/index.json`, with the properties listed in
the [privacy page](PRIVACY.md#where-it-goes). HEAD requests, resumed downloads
and other files are not counted. It reads the project token from
`POSTHOG_PROJECT_TOKEN` and the optional ingestion host from `POSTHOG_HOST`
(US cloud by default); without a token it counts nothing. A send that fails or
takes over two seconds is logged and dropped without changing the response.

A Blender download is an install or an update; the request does not say which.
Index requests are repository additions and manual update checks, not active
installations, because the extension sets up the repository without startup
checks. Manual installs from release assets are not counted, and the release
download counters include CI downloads. [CI](../.github/workflows/site-proxy.yml)
type-checks the middleware and runs [its tests](../tools/site_proxy/middleware.test.ts);
locally, run `node --test tools/site_proxy/middleware.test.ts` with Node 24.

## Access

The inspected repository team grants are `@scenario-labs/platform` with
**maintain** and `@scenario-labs/scenario` with **write** (`push` in the API).
On 2026-10-13 the maintainer lowered `platform` from admin to maintain, and both
grants were read back (#20, #21). By the same decision no extra direct write
grants are added; do not publish personal access records.
[CODEOWNERS](../.github/CODEOWNERS) requests reviews for specific release,
automation, licence and security paths; it grants no access. Required
code-owner approval stays off by the maintainer's decision while there is one
active maintainer (#21).

Repository admins can bypass the review ruleset, and only the release App can
bypass versioning since 2026-10-13. Nothing bypasses main integrity. A collaborator's
write permission does not permit a direct update to protected `main`.
Administrative API access depends on the caller's repository/organization role
and token permissions. A permission error is not evidence that a feature is
disabled; do not automatically expand token scopes or change grants to inspect it.

```sh
gh api repos/scenario-labs/blender-plugin/teams --jq '.[] | {slug, permission}'
gh api orgs/scenario-labs/teams/platform/repos/scenario-labs/blender-plugin \
  -H 'Accept: application/vnd.github.v3.repository+json' --jq .role_name
```

### Smoke lane

The [paid smoke workflow](../.github/workflows/smoke.yml) requires the existing
`smoke` environment, at least one required reviewer and a custom deployment
policy allowing exactly the `main` branch. Its read-only admission checks use
GitHub's [environment API](https://docs.github.com/en/rest/deployments/environments#get-an-environment)
and [branch-policy API](https://docs.github.com/en/rest/deployments/branch-policies#list-deployment-branch-policies).
These checks run before the protected job and again after approval. A missing or
unreadable gate fails closed. API readback on
2026-10-09 confirms the environment exists, requires the maintainer reviewer,
allows self-review for an explicitly approved dispatch, and accepts exactly the
`main` branch. The workflow itself still never creates or relaxes these rules.

The authorized test key/default scope, private plan and recovery secret were
installed as environment secrets on 2026-10-09. Secret values were passed through
private standard input and never logged. The recovery passphrase is retained
privately outside Git. Environment secrets are:

- `SCENARIO_TEST_API_KEY` and `SCENARIO_TEST_API_SECRET`.
- Optional `SCENARIO_TEST_PROJECT_ID`; blank is the key's default scope.
- `SMOKE_PLAN_JSON`, following the [private suite format](../tests/smoke/README.md#one-aggregate-budget-for-a-suite).
  Only a manual dispatch that selects the `private` plan receives it.
- `SMOKE_RECOVERY_PASSPHRASE`, a randomly generated secret of at least 32 characters,
  retained privately for decrypting artifacts after rotation.

On 2026-10-10 the maintainer set the monthly allowance at 40 CU per scheduled
run. The schedule runs the committed
[monthly plan](../tests/smoke/README.md#monthly-plan) with that fixed cap,
`MONTHLY_MAX_CU` in `tools/smoke_ci.py`; both change only through a reviewed
commit. The workflow no longer reads the repository variable `SMOKE_MAX_TOTAL_CU`,
which stays unset. A manual dispatch defaults to the monthly plan and a 40 CU cap;
selecting the private plan, or a higher cap, is its own budget decision. API
readback on 2026-10-10 again shows the required reviewer and exactly the `main`
policy, so the monthly plan needs no further repository setting.
Configure any provider-side project/monthly budget separately; the workflow's cap
is per run, and manual runs do not share a monthly ledger. The approval job shows
the plan and cap frozen by admission. Review them and the configured scope before
approving. Version-2 plans also authorize exact input files and
hashes from the checked-out repository; the protected job stages them privately
and uploads through shared durable commands before quoting. Approval covers
those uploads even if model validation or the cap later prevents generation.
Do not put paid secrets at repository scope merely
to bypass this environment; the free API audit remains separate.

Actions reruns are refused, even after an apparently early failure. Recover from
the encrypted archive with the original scope using the non-submitting resume
command. A new dispatch requires a new spending decision, never an assumed retry.
See [hosted recovery and limits](../tests/smoke/README.md#protected-hosted-execution-and-recovery).
The [zero-cap hosted check](https://github.com/scenario-labs/blender-plugin/actions/runs/37946825690)
failed admission and skipped the protected job, with no Scenario request.
The [non-main zero-cap dispatch](https://github.com/scenario-labs/blender-plugin/actions/runs/37948136030)
skipped both jobs before any step ran. This verifies the workflow's branch guard;
API readback separately verifies the environment's exact `main` policy.
The [authorized hosted check](https://github.com/scenario-labs/blender-plugin/actions/runs/37946893572)
passed admission, visibly paused at the required-reviewer gate, then succeeded
after approval on source `88b6b3ec8ebd5707581f6d39f0fd158c784873a2`.
Image, material, video, 3D and audio each produced one saved `READY` job.
All 16 downloaded result files matched their saved byte counts and SHA-256
receipts. Only the encrypted recovery archive was published as an artifact;
private download/decryption and quote-binding verification passed. Running
`tools.smoke_suite resume` on the decrypted suite passed with sockets blocked,
zero network calls and the same five job records unchanged. Hosted logs contain
none of the configured secret values, saved job/asset identities or signed queries.

This version-1 plan exercises generation, polling, download and completed-result
recovery. It does not establish version-2 reference uploads, uncertain remote-job
recovery, Blender application, physical interaction or media-quality acceptance.
No provider-side monthly budget was configured. The 40 CU monthly allowance
above came later and has no scheduled run yet; the first approved monthly run is
separate acceptance. Keep those remaining #40 and #440 scopes separate from the
completed hosted check.

## Project and labels

[Project 31](https://github.com/orgs/scenario-labs/projects/31), titled
*Plugins*, is a **private** board shared by Scenario's editor plugins (Blender,
Godot and Photoshop), with one view per plugin. Public issues remain visible and
are triaged on that board. On 2026-10-13 the maintainer decided that, as a
shared board, it needs no views of its own for this repository; the views,
Auto-archive and workflow-target review once planned under #20 are dropped.
This document does not make the project public or disclose its item inventory.

| Field | Options, including the actual emoji spelling |
| --- | --- |
| Status | `📋 Backlog`, `🔖 Next`, `🏗 In progress`, `👀 In review`, `✅ Released`, `🗑️ Dropped` |
| Priority, Effort | Single-select fields; the 2026-10-13 read-back listed no options for them |

Read back on 2026-10-13, the views are `All (table)` (table) and `All`,
`Blender`, `Godot` and `Photoshop` (boards). Enabled workflows are
`Auto-add sub-issues to project`, `Auto-add to project`,
`Auto-add to project (blender)`, `Auto-add to project (godot)`,
`Auto-close issue`, `Code review approved`, `Item added to project (PR)`,
`Item closed`, `Pull request linked to issue` and `Pull request merged`.
An enabled flag alone does not prove its trigger/filter/target is correct.

Epics live in [scenario-labs/roadmap](https://github.com/scenario-labs/roadmap);
the project's enabled sub-issue workflow is intended to add their sub-issues.
Labels are curated: shared roadmap labels plus `area:*`, `blender:*`, `os:*`,
`needs-repro`, `needs-info`, `model-behaviour` and `known-limit`. Do not blindly
clone another repository's labels. Older Blender labels remain useful for
historical reports and do not establish current runtime support.

```sh
gh project view 31 --owner scenario-labs --format json
gh project field-list 31 --owner scenario-labs --format json
gh label list --repo scenario-labs/blender-plugin --limit 100
gh api graphql -f query='{ organization(login:"scenario-labs") { projectV2(number:31) { public views(first:20){nodes{name layout}} workflows(first:30){nodes{name enabled}} } } }'
```

## Open admin steps

These tasks retain their existing owner issues. Read back the result and update
this guide after an authorized change; do not treat the checklist as permission
to perform it.

- [x] Configure the protected smoke environment and private secrets, then verify the authorized five-lane hosted run and decrypted completed-result recovery: #40. Scheduled runs use the committed monthly plan with a fixed 40 CU cap. Reference-upload acceptance, the first approved monthly run and any provider-side budget remain separate.
- [x] Add and read back the `ci-ok` required check with its verified GitHub Actions identity; preserve existing `pr-title`, `commits`, CodeQL, code-quality and review rules: #45. The hosted negative title check is verified; documentation review and merge remain.
- [x] Verify the first automated release, then remove the repository-admin tag bypass while retaining release App integration `4751046`, both tag patterns and all protection rules; enable immutable releases only after publication and download verification: #36. Done on 2026-10-13 after 0.10.0 was published and its downloads verified, and read back: the tag ruleset keeps both patterns and its four rules with the release App as its only bypass actor, and immutable releases are enabled.
- [x] Decide restricted allowed actions and require SHA pinning after workflow pins and update behavior are verified: #39. Decided and applied on 2026-10-13: full-SHA pinning is required and allowed actions stay `all`, read back through the API; a Pages run on `main` passed with the setting on.
- [ ] Move `blender.scenario.com` behind the Vercel project and accept its download counts: #492.
- [ ] Complete #56: the published handbook, homepage and manifest Website link are aligned; desktop Website-action acceptance remains. The native repository has served releases since 0.10.0.
- [x] Upload the reviewed, merged social-preview card and verify its custom-image flag and URL: #19. Uploaded on 2026-10-13 and read back: the flag is `true`, the URL is on `repository-images.githubusercontent.com` and the image matches the committed card byte for byte.
- [ ] Recheck the rendered shared-link preview of the repository in an application that unfurls links once its link cache refreshes. A Slack unfurl right after the 2026-10-13 upload still showed the previous card. #19 is closed with the GitHub-side evidence above.
- [x] Decide the platform team's maintain/admin grant and confirm required code-owner review and direct-write policy: #20 / #21. Decided on 2026-10-13: `platform` holds maintain and `scenario` write, both read back; required code-owner review stays off; no extra direct write grants.
- [x] Decide project 31's views, Auto-archive and built-in workflow targets for this repository: #20. Decided on 2026-10-13: project 31 is a board shared by several plugins, so this repository needs no views of its own and that review is dropped. The project stays private (read back).

Release-App setup belongs to completed #35: the current workflow references its
organization-provided client ID/private-key secrets, and the tag ruleset includes
the App bypass. Do not re-install or replace credentials as a routine release
step. The 0.10.0 release on 2026-10-13 exercised these secrets and the publication
flow; see its [publication record](maintenance/release-acceptance.md#publication).
This guide does not reveal secret values. Wiki-off is verified. #20 and #21
closed on 2026-10-13 after the access and project decisions above.
