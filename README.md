# Open Studio for Blender

Use Blender with Codex ImageGen, another connected assistant, local ComfyUI workflows,
or a compatible AI API. Open Studio is a development fork of
[Scenario for Blender](https://github.com/scenario-labs/blender-plugin), maintained
in [midnitefox/blender-plugin](https://github.com/midnitefox/blender-plugin).
It retains the upstream GPL license and copyright notices.

**Development preview:** provider selection and assistant image handoff are under
active integration. Full end-to-end parity across 3D, rigging, motion, materials,
video, environments and sound has not yet been verified. The detailed upstream
guides describe the inherited Scenario workflow; availability with another
provider depends on its supported models and declared workflow recipes.

## Requirements

- Blender 5.0 or later. Development checks use an isolated Blender profile.
- For Codex image work: Codex with ImageGen and a connection to the local Blender
  MCP server. This route needs no Scenario account, separate image API key or
  ComfyUI installation.
- For direct generation: a configured provider that supports the requested media
  type. Chat API compatibility alone does not provide image, video, audio or 3D.
- Blender's Allow Online Access must be on for the local MCP server and provider
  connections. Local image import itself makes no network requests.

## Install

Use an **Open Studio** development ZIP from this fork, then open Blender's
Edit > Preferences > Get Extensions > Install from Disk. Keep the ZIP zipped.
The extension appears as Open Studio in Add-ons and the viewport sidebar.

Disable the original Scenario extension before enabling Open Studio. This preview
retains some inherited internal Blender names and refuses to enable alongside the
original. It has a distinct extension ID and never registers Scenario's update feed.

### Updates

Check [this fork's releases](https://github.com/midnitefox/blender-plugin/releases)
for explicitly published builds. Install updates from disk. A development branch
or passing unit test does not establish a released or fully verified build.

## Quick start

1. Open **Open Studio > Choose AI**. The default is **Codex / external assistant**.
2. Start the assistant connection and copy the Codex setup. Ask Codex to configure
   that local connection. The connection file follows Blender restarts, including
   a changed local port or token; saved setup does not contain a fixed bearer token.
3. Ask Codex, for example: **Create a painted stone concept image with ImageGen
   and add it as a reference in my Blender scene.**
4. Codex calls `agent_workspace`, saves or copies its generated image into the
   returned inbox, and calls `import_agent_image`. You can request `REFERENCE`,
   `IMAGE`, or a `TEXTURE` on a named mesh with UVs.
5. Save the blend file. Imported images are packed so temporary image cleanup
   does not remove them from the saved project.

Codex orchestrates its own ImageGen tool and the Blender handoff. Blender cannot
invoke Codex's built-in image tool directly. An image texture supplies base color;
a full PBR set, 3D mesh or animation needs the corresponding workflow.

## Features

The inherited scene tools include image/mesh/material/media application, camera
paths, validated greybox plans, timeline captures, environments and local MCP.
Provider support is explicit:

| Connection | Intended use | Setup |
| --- | --- | --- |
| Codex / external assistant | Images created by the assistant, imported locally | Local MCP connection; no generation API credentials |
| ComfyUI | Local or hosted graph workflows, including specialized installed models | Running server, checkpoint for basic images, explicit recipes for other workflows |
| OpenAI-compatible API | Configured text, image generation/editing and speech endpoints | Base URL, applicable key, model IDs and explicit capability selection |
| fal | Optional model-specific 3D, material, video and audio workflows | API key and a supported preset; integration in progress |
| Scenario | Existing Scenario generation and cloud features | Scenario API key/secret and applicable credits |

Unavailable capabilities are not interchangeable. Music and sound effects need
appropriate models; speech support does not imply either. Rigging and preset
animation do not establish text-to-motion support.

Exact Scenario prices retain their CU units. Other connections can return no
price quote; the UI states that cost is not quoted, and API tools require the
`provider-billed` approval token. Local compute and hosted billing are described
by the selected connection. Uncertain submissions are not automatically retried.

## Agents (MCP)

Start with `scene_summary`. For native assistant image generation, use
`agent_workspace` and `import_agent_image`; these tools do not require provider
credentials or the arbitrary Python execution permission. The inbox accepts
bounded PNG, JPEG and WebP files by basename, with an optional SHA-256 check.

The local MCP server uses authenticated loopback HTTP. The copied Codex setup
uses a local stdio bridge and private connection file, refreshed when Blender
starts the server. Treat the connection file as a credential. Keep Blender running
while using its tools. Arbitrary Python execution remains separately opt-in.

See the [MCP reference](docs/MCP.md) for inherited tool contracts and the
[privacy guide](docs/PRIVACY.md) for upstream Scenario behavior.

## What leaves your machine

The assistant image handoff reads a file from the extension's inbox and packs it
locally. Any image generation performed by Codex follows Codex's own service and
account settings. Direct provider generation sends its prompts and selected
references to the configured service. ComfyUI may run locally or remotely.
Scenario's optional path continues to use Scenario APIs and storage.

Profiles isolate job history, receipts and cached artifacts by connection identity.
API keys remain in local Blender preferences and are not encrypted by this fork.
Do not share preferences, connection files or provider keys.

## Documentation

The [documentation index](docs/index.md) links inherited architecture, application
and development references. Some guides and screenshots still describe upstream
Scenario. This README is the entry point for the fork's new setup and provider
choices. [Known upstream limitations](docs/KNOWN_LIMITATIONS.md) also apply where
those implementations are retained.

## Support

Report fork issues in [midnitefox/blender-plugin](https://github.com/midnitefox/blender-plugin/issues).
Include the Blender version, provider type, selected workflow and sanitized error.
Keep keys, tokens and private prompts out of public reports. Provider billing and
account questions belong with the provider.

## Development

Read [AGENTS.md](AGENTS.md), [CONTRIBUTING.md](CONTRIBUTING.md), and the
[validation guide](docs/development/validation.md). Use the pinned uv version and
locked environment. Package through the repository tools so the exact dependency
wheels and notices are included. Native tests install the actual ZIP into a
fresh profile and preserve the normal Blender profile.

Protocol fixtures and local loopback tests prove request serialization, storage,
recovery and error behavior. Live model generation, output quality, GUI interaction
and each advertised creative workflow require separate acceptance.

## Licence

Copyright 2026 Scenario Inc. First-party extension code is free software: you
can redistribute it and/or modify it under the terms of the GNU General Public
License as published by the Free Software Foundation, either version 3 of the License, or (at your option) any later version.
It is distributed WITHOUT ANY WARRANTY; see [LICENSE](LICENSE) for the full text.
First-party source headers and the extension manifest declare GPL-3.0-or-later,
and the extension ZIP includes the same GPL text. Bundled dependencies, adopted
sources and assets retain their original licences and notices; see the
[SDK bundle](docs/SDK_BUNDLE.md) and [source adoption](docs/STUDIO_ADOPTION.md).
The [bundled icon notice](scenario/icons/README.md) records the generator, the
font option and the icons' first-party design origin. Scenario Inc. made the
exploration gallery covers (`docs/images/exploration-*.png`) and also publishes
them at [www.scenario.com/explorations](https://www.scenario.com/explorations/).

[Blender's licence guidance](https://www.blender.org/about/license/) requires a
GPL-compatible licence for published add-ons using its Python API. The
[Blender Extensions Platform](https://docs.blender.org/manual/en/latest/advanced/extensions/licenses.html)
requires GPL-3.0-or-later for add-ons it hosts. This project distributes outside
that platform and uses GPL-3.0-or-later for its first-party extension code.

The GPL grants no trademark rights: "Scenario" and the Scenario logo belong to Scenario Inc., and Blender is a registered trademark of the Blender Foundation; this extension is not affiliated with or endorsed by the Blender Foundation. See [TRADEMARKS.md](TRADEMARKS.md).

### Acknowledgements

The local MCP design acknowledges
[Blender Lab's blender_mcp project](https://projects.blender.org/lab/blender_mcp)
(GPL-3.0-or-later, Blender Authors) as a design reference.
Its [project page](https://www.blender.org/lab/mcp-server/) describes
the upstream implementation, which uses a separate stdio MCP server and TCP
connection to its Blender add-on. This extension implements authenticated
loopback HTTP, main-thread tool dispatch and an opt-in Python gate.

The [SPZ reader](scenario/core/scene/spz.py) reads
[Niantic's SPZ Gaussian splat format](https://github.com/nianticlabs/spz).
The [MCP protocol module](scenario/mcp/protocol.py) supports protocol revisions
2025-06-18, 2025-03-26 and 2024-11-05.

The Scenario logo comes from the [official skills repository](https://github.com/scenario-labs/skills/blob/main/resources/scenario-logo.png);
its [original source licence](docs/images/scenario-logo.LICENSE) is retained.
