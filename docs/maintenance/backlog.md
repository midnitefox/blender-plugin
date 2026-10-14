# Documentation and issue disposition

This is an inspection record, not a release acceptance report. Source locations
are linked in the [runtime map](../architecture/runtime.md) and in the evidence
registry. Follow the [knowledge review procedure](knowledge.md) when updating it.

## Existing documentation issues

| Issue | Disposition in this foundation |
| --- | --- |
| [#4](https://github.com/scenario-labs/blender-plugin/issues/4) | Closed after canonical root instructions, required guides, shared skills/path adapters and fresh CLI instruction-context verification. Historical Blender 4.2, root CLAUDE import and unconditional test requirements are superseded by the current contract. |
| [#6](https://github.com/scenario-labs/blender-plugin/issues/6) | The sanitized prototype design and plans are retired from the working tree; Git retains their history. Current instructions and useful architecture rationale live in the maintained guides. Personal product-decision comments and report-generator punctuation were cleaned up. Required Studio author credits remain under the current attribution policy; a historical blanket first-name exclusion does not override that policy. |
| [#25](https://github.com/scenario-labs/blender-plugin/issues/25) | Every legacy report has a current disposition below. Resolved reports stay resolved; existing integration issues retain their scope. The distinct remaining feature requests are filed. The project's Priority and Effort fields have no options yet, so follow-ups carry no priority or effort triage. |
| [#2](https://github.com/scenario-labs/blender-plugin/issues/2), [#13](https://github.com/scenario-labs/blender-plugin/issues/13) | The public README and current guide refresh are merged; both issues are closed. Screenshot optimization closed under #14; the open supported-platform and runtime checks are in #440. |
| [#48](https://github.com/scenario-labs/blender-plugin/issues/48) | Contributor setup, public-content rules, inbound licensing, attribution, issue triage and release version/override controls are documented against current tooling. Historical direct-profile installs, blanket dependency/originality bans and assumed hosted publication are superseded by the canonical guides. Optional local hooks are implemented; hosted handbook delivery retains its separate gate. |
| [#47](https://github.com/scenario-labs/blender-plugin/issues/47), [#52](https://github.com/scenario-labs/blender-plugin/issues/52) | Both are closed: the generated MCP reference/native tool contract and hosted external-link monitoring have their own verified checks. [The contribution guide](../development/contributions.md#link-check) describes the link scanner and scheduled reporter; local knowledge checks remain separate. |
| [#54](https://github.com/scenario-labs/blender-plugin/issues/54) | Shared skills and adapters exist; hooks, settings and client templates retain their own scope. |

## Legacy limitations audit

The retired prototype report mixed bugs, design boundaries and superseded
observations. This table preserves a disposition for each bullet without copying
private anecdotes or treating old live observations as current service contracts.

| Original subject | Current evidence and disposition |
| --- | --- |
| Native tests use real extension state | The current [native runner](../../tools/test_blender.py) and [profile helpers](../../tools/blender_env.py) own disposable profiles and verify installed contents. The original runner claim is superseded; direct ad hoc invocations must still isolate profiles. |
| Environment overrides preferences | Resolved by explicit source selection in [resolve_credentials](../../scenario/core/config.py): preferences are the default, environment requires opt-in, and incomplete pairs cannot mix. Shared account and project job scope shipped in 0.10.0; OAuth is deferred under #67. |
| Slow non-curated schema loading | [Generation](../../scenario/blender/generation.py) and [parameter UI](../../scenario/blender/params_ui.py) retain async loading. Panel/render-lane controls display loading or schema errors; a failed description read shows its reason with **Retry loading model** (see [the model chooser](../UI_STYLE.md#the-model-chooser)). |
| Single-line prompt editing | The current [composer](../../scenario/blender/composer/modal.py) submits on Enter, including modified Enter. Multiline editing and the associated focus/keyboard contract are [#444](https://github.com/scenario-labs/blender-plugin/issues/444); the removed editor-button report is not a current defect. |
| GUI-only capture | [Capture](../../scenario/blender/capture.py) and [MCP scene tools](../../scenario/mcp/tools_blender.py) use viewport/OpenGL behavior; this limitation remains. |
| No project switcher | [Preferences](../../scenario/prefs.py) now expose an optional Project ID for the explicitly selected API-key source. Blank uses credential-bound server scope; no discovery selection is required. Live permissions and native setup/switching acceptance continue in #440. Browser OAuth remains deferred under #67. |
| GUI probes take focus | [Probe script](../../tools/gui_screenshot.py) starts native interaction and sets a generation guard. Focus interference remains a reason to use controlled GUI validation; historical spend anecdotes are removed. |
| MCP bypasses render prompt decoration | Resolved at the command boundary: [MCP generation](../../scenario/mcp/tools_scenario.py) uses the native [render request builder](../../scenario/blender/render_commands.py), including decoration and prepared references. Spark preparation retains its separate quote/approval before final render pricing. The open render checks are in #440. |
| Spark generic mode only | Superseded by the model ID that [prompt jobs](../../scenario/blender/prompt_jobs.py) pass. An explicit generic/model-contextual preference is a separate request in [#188](https://github.com/scenario-labs/blender-plugin/issues/188). Fixed historical prices are not service contracts. |
| Model picker fallback | [Model picker](../../scenario/blender/model_picker.py) and [property enum handling](../../scenario/blender/props.py) retain fallback behavior; treat as an implementation detail, not a defect without a reproduction. |
| Shot markers and aim behavior | [Shot planner](../../scenario/blender/shot_planner.py) implements camera markers and aiming. This is a design behavior, not evidence of a broken feature. |
| Selection exported as one mesh | [Mesh export](../../scenario/blender/mesh_export.py) exports the selection; #99 must establish safe target/result identity for edits. |
| Composer lacks sidebar lanes | [Composer drawing](../../scenario/blender/composer/draw.py) and [properties](../../scenario/blender/props.py) define different interfaces by design: the composer has the seven lane tabs, and its model chip shows the sidebar, which holds the rest of the lane's form (see [composer placement](../UI_STYLE.md#composer-placement)). |
| Audio waveform absent | The [saved media controls](../../scenario/blender/result_media.py) draw a saved shared audio result's waveform without playback or service calls, from a receipt-keyed envelope that an [offline Blender process](../RESULT_PREVIEWS.md#offline-audio-envelopes) decodes from WAV, MP3, Ogg, FLAC, M4A or AAC, and play saved video and audio from private verified copies, saved audio inside Blender through an [owned offline process](../RESULT_PREVIEWS.md#in-extension-audio-playback) with Stop. The unreleased prototype waveform is retired. Local MCP [`inspect_result_previews`](../MCP.md#historical-upstream-reference) returns the same envelope as bounded levels and launches no player or audio. Desktop listening and the open waveform checks from [#189](https://github.com/scenario-labs/blender-plugin/issues/189) continue in #440. |
| Prompt helper pricing | [PromptJobs](../../scenario/blender/prompt_jobs.py) uses shared SDK quotes, exact approval and durable submission for New, Rewrite and Translate. Failure stops with saved-job guidance; no automatic paid LLM fallback remains. Historical numerical prices are retired; live acceptance continues in #440. |
| Motion, speech-to-text and standalone text lanes | Existing lane choices in props do not establish these product workflows. Video-to-motion and speech-to-text input/result paths are tracked in [#190](https://github.com/scenario-labs/blender-plugin/issues/190), subject to verified SDK/provider support. Capability adoption and the views shipped in 0.10.0; no standalone text workflow is implied. |
| Categories from names/tags | [model_filter](../../scenario/core/api/model_filter.py) contains heuristics; [catalog](../../scenario/core/api/catalog.py) classifies trained models from their REST fields. Provider taxonomy needs current evidence before the heuristics change. |
| Native versus custom theme | [UI style](../UI_STYLE.md) explains Blender widgets and custom compositor presentation. This is a design boundary, not a runtime bug. |
| Spark returns unusable asset references | The [shared text-result reader](../../scenario/core/jobs/results.py) rejects unusable asset references and retrieves complete bounded text through the SDK. The old Spark client and partial-preview fallback are removed. The provider-specific live failure is not freshly reproduced; live acceptance continues in #440. |
| Capture FPS follows scene | [capture.py](../../scenario/blender/capture.py) derives capture timing from scene settings. Whether capture timing should be settable independently is a product decision tracked in #440. |

## Review limits and next work

The separate Windows/Linux GUI verification request is carried in
[#440](https://github.com/scenario-labs/blender-plugin/issues/440) as a
non-gating platforms check: actual desktop focus/input/DPI/audio acceptance on
the supported Blender matrix. Headless tests
do not satisfy that requirement; the old Blender 4.x labels are superseded by
the approved Blender 5.0/5.1/5.2 matrix.

The current follow-up set is #99, #188, #190, #435, #440, #444, #445 and #446,
with browser sign-in deferred under #67.
On the 2026-10-14 read-back, #99, #188 and #190 were Feature issues on the
project's Backlog, #440 and #444 to #446 were on the project without an issue
type, and #435 was not on it.
#31 is closed for the implemented portable-tooling foundation; it does not establish every desktop/media acceptance gate. The historical request
for a fixed number of newly filed issues is reconciled with resolved credential
and isolation defects and existing integration owners. Do not reopen fixed bugs
or duplicate an existing issue to meet that historical count. Beginner and
help-wanted labels reflect the actual task's suitability; the old seeding quota
does not justify presenting broad runtime or spending-boundary work as beginner
tasks.

Filing and triage do not implement these features or establish new runtime, live
provider or human acceptance. The linked feature and integration issues remain
open for their own complete acceptance.

External link monitoring is implemented and accepted under closed #47, as a separate documentation check.
A scheduled model-generated updater is
deferred until maintainers choose its review behavior and spending limits.
