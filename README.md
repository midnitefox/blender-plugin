# Open Studio for Blender

Connect an assistant to Blender to inspect your scene, create camera moves, and
bring assistant-generated images into your project. Open Studio is an
assistant-only development fork of
[Scenario for Blender](https://github.com/scenario-labs/blender-plugin), maintained
in [midnitefox/blender-plugin](https://github.com/midnitefox/blender-plugin).
It retains the upstream GPL license and copyright notices.

**Development preview:** the current workflow supports local scene tools and
image handoff. Full creative feature parity with the upstream extension is
incomplete. Automated 3D generation, rigging, motion, complete PBR materials,
video, panorama and audio workflows are not exposed in this build.

## Install and connect

1. Use Blender 5.0 or later. Download the extension ZIP from this fork's
   [releases](https://github.com/midnitefox/blender-plugin/releases) when a build
   is available, or use a ZIP built from this checkout.
2. In Blender, open **Edit > Preferences > Get Extensions**, open the menu in
   the upper right and choose **Install from Disk**. Select the ZIP and enable
   **Open Studio**. Disable an older copy before installing this build.
3. Open the 3D viewport sidebar with **N**, then **Open Studio** in the viewport sidebar.
   Allow Blender's online access for the local MCP listener and start the server.
4. Copy the setup for your assistant from that panel. The Codex setup uses the
   included stdio bridge and a private connection file, which follows Blender's
   current local port and token. Keep Blender open, then reconnect MCP in the
   assistant.
5. Ask the assistant to inspect your Blender scene. For an image, ask it to use
   its own image-generation tool and import the result into Blender.

Open Studio requires no generation account, API key, model selection or credits
configuration. Image generation happens in your assistant and follows that
assistant's availability and account settings. The extension cannot invoke the
assistant's image-generation tool itself.

## Work with images

The assistant calls `agent_workspace`, saves or copies its generated image into
the returned inbox, and calls `import_agent_image` with the filename. Supported
files are PNG, JPEG and WebP, up to 64 MiB per image.

| Import mode | Result in Blender |
| --- | --- |
| `REFERENCE` | An image reference at the 3D cursor for concept art or modeling. |
| `TEXTURE` | A new base-color material in a named mesh's active slot. The mesh needs UV coordinates. |
| `IMAGE` | A packed image datablock for later work. |

Save the `.blend` file to retain the packed image. A base-color image supplies
one material input; full PBR maps and mesh generation need additional workflows
that are not implemented in this build.

## Local Blender control

Connected assistants can inspect objects and datablocks, change selection and
the timeline, build a camera path, and capture a viewport or quick camera still.
Captures require Blender's graphical interface. Arbitrary Python execution is
separately controlled by **Allow connected assistants to run Python** and is off by
default. The image handoff and dedicated scene tools work with that option off.

For an isolated headless session, the extension registers:

```sh
blender --background --online-mode --command open_studio --port 9876
```

The headless server uses local authentication and writes its connection details
to a private file. Viewport screenshots and quick OpenGL captures require the
GUI. See [MCP setup and security](docs/MCP.md) for connection details and limits.

## Privacy and help

Image import reads from the extension's local inbox and packs the pixels into
Blender. A connected assistant can receive the scene data and captures requested
through MCP. Image generation performed by the assistant follows its own
service policies. Keep the local token and connection file private.

- [User guide](docs/USER_GUIDE.md): installation, image handoff and troubleshooting.
- [MCP reference](docs/MCP.md): local tool contracts and client setup.
- [Privacy](docs/PRIVACY.md): data handling and Python permissions.
- [Support](SUPPORT.md): fork issues and useful diagnostic information.
- [Documentation index](docs/index.md): current guides and inherited source references.

Install updates from this fork's release ZIPs. Historical upstream guides and
screenshots describe a different generation workflow and do not establish what
this assistant-only build supports.

## Development

Read [AGENTS.md](AGENTS.md), [CONTRIBUTING.md](CONTRIBUTING.md), and the
[validation guide](docs/development/validation.md). Follow the current
assistant-only scope when retained upstream guidance describes service
integration. Use the pinned uv version and locked environment. Package through
the repository tools so required dependencies and notices are included. Native
tests install the actual ZIP into an isolated profile.

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
font option and the icons' first-party design origin.

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
