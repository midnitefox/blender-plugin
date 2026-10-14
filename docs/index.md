# Open Studio for Blender documentation

Use the current assistant-only guides for installation and everyday work:

- [Repository overview](../README.md)
- [User guide](USER_GUIDE.md)
- [Local MCP setup, tools and security](MCP.md)
- [Privacy and data handling](PRIVACY.md)
- [Assistant runtime and retained-source boundaries](architecture/runtime.md)
- [Support](../SUPPORT.md)

The active workflow is assistant-created asset handoff and local Blender
control. Generation takes place in the assistant's own tools. The
[changelog](../CHANGELOG.md) retains upstream release
history and does not establish this fork's current feature coverage.

## Historical upstream implementation reference

The guides below document inherited source and design decisions. They may name
retired panels, service operations, old screenshots or workflows that are not
registered in this build. Use them for source maintenance; use the current
guides above for setup and supported features.

- [Inherited known limitations](KNOWN_LIMITATIONS.md)
### Architecture and implementation

- [Blender boundaries and scene application](architecture/blender.md)
- [SDK adoption and operation contracts](SDK_ADOPTION.md)
- [Dependency bundle and supported artifacts](SDK_BUNDLE.md)
- [Durable job storage](JOB_STORAGE.md)
- [Job coordinator, workers and cancellation](JOB_COORDINATOR.md)
- [Blender job context and stale-result guards](BLENDER_JOB_CONTEXT.md)
- [Signed result transfers](RESULT_TRANSFERS.md)
- [Saved-result previews](RESULT_PREVIEWS.md)
- [Multipart upload lifecycle](SDK_UPLOADS.md)
- [Reversible mesh application](MESH_APPLICATION.md)
- [Panoramic World application](WORLD_APPLICATION.md)
- [Saved material application](MATERIAL_APPLICATION.md)
- [Film recipe, scope and editorial contracts](FILM_PLAN.md)
- [Source adoption decisions](STUDIO_ADOPTION.md)

## Development and contributions

Retained engineering guides include upstream integration and release assumptions.
Apply the assistant-only scope when those assumptions conflict with this build.

- [Private vulnerability reporting and security scope](../SECURITY.md)
- [Trademarks and official builds](../TRADEMARKS.md)
- [Contributor setup and environment](../CONTRIBUTING.md)
- [Canonical agent instructions](../AGENTS.md)
- [Python style and linting](PYTHON_STYLE.md)
- [Model schema audit and offline reports](MODEL_PAYLOAD_AUDIT.md)
- [UI style and native interaction](UI_STYLE.md)
- [Validation and local commands](development/validation.md)
- [Recorded fixture inventory and offline hygiene](../tests/fixtures/README.md)
- [Commits, PRs and review follow-up](development/contributions.md)
- [Agent skills and command adapters](development/agents.md)
- [Release procedure](RELEASING.md)

## Historical maintenance and evidence

- [Repository baseline and pending administration](MAINTAINERS.md)
- [Knowledge checks and review procedure](maintenance/knowledge.md)
- [Upstream release plan](maintenance/release-plan.md)
- [0.10.0 release acceptance record](maintenance/release-acceptance.md)
- [Licence decision and the alternatives compared](maintenance/licence-options.md)
- [Issue disposition and limitations audit](maintenance/backlog.md)
- [Evidence discovery configuration](knowledge.json)
- [Evidence records, one per document](knowledge/)

The evidence records name each document's sources and the main commit it was last
reviewed against, not human approval or live service acceptance. Inherited guides
keep explicit coverage limits; their presence here does not certify every
historical claim.
