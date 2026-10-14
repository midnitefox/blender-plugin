# Open Studio for Blender: user guide

Open Studio connects an assistant to your local Blender project. The current
build supports assistant-generated image import and local scene and camera
tools. It is a development preview; the broader creative workflows from the
upstream extension are not yet available here.

## Install

Use Blender 5.0 or later and this fork's extension ZIP. Builds, when published,
are available in [Open Studio releases](https://github.com/midnitefox/blender-plugin/releases).
A ZIP built from this checkout can also be installed.

1. Disable an older copy of the extension if one is installed.
2. Open **Edit > Preferences > Get Extensions** and choose **Install from Disk**
   from the menu in the upper right.
3. Select the ZIP and enable **Open Studio**.
4. Open the 3D viewport sidebar with **N** and select **Open Studio**.

No generation account or credentials are configured in Blender. Your assistant
handles its own generation tools. Update by installing a newer ZIP from this
fork, with Blender closed while replacing files if an installation tool requires
it. Keep a backup of important `.blend` files before testing development builds.

## Assistant connection (MCP)

MCP lets your assistant call tools in the open Blender project. Open
**Open Studio** in the viewport sidebar, allow Blender's online access and
start the local server. Copy the setup shown for your assistant. Keep Blender open while using it.

All copied client setups launch the included stdio bridge with `--connection-file`.
The bridge reads Blender's current local address and token from that private
file, so it can follow a server restart. Paste the copied setup into the
assistant's MCP configuration workflow, then reconnect the assistant's MCP client.
Ask the assistant to run `scene_summary` to check the connection.

Copy a fresh configuration after moving or reinstalling the extension if its
installation path changes. Keep the connection file and token private. The
[MCP reference](MCP.md) explains the other clients and local permissions.

## Bring an assistant-generated image into Blender

For example, ask: "Generate a weathered wood base-color image and apply it to
my UV-mapped crate in Blender." The assistant needs its own working image tool
and the ability to save or copy the resulting file on this computer.

The handoff is:

1. Inspect the scene and identify the destination.
2. Generate or edit the image using the assistant's image tool.
3. Call `agent_workspace` and copy the file into its returned inbox.
4. Call `import_agent_image` with the image's basename and the desired mode.
5. Check the result in Blender and save the `.blend` file.

Blender receives an existing image through this handoff. The extension does not
invoke the assistant's image tool or manage its account. A local image you
already have can follow the same import process.

| Mode | Use | Requirements |
| --- | --- | --- |
| `REFERENCE` | Concept art or a modeling reference at the 3D cursor. | An image file; this is the default mode. |
| `TEXTURE` | Apply a base-color texture to a mesh's active material slot. | Exact editable mesh name and existing UV coordinates. |
| `IMAGE` | Load packed pixels for later use. | An image file. |

The inbox accepts PNG, JPEG and WebP files up to 64 MiB. Import validates the
file and decodes it in Blender before applying it. It packs the image into the
project, so the saved `.blend` file can retain it after inbox cleanup. Repeating
the same image and destination reuses the imported content.

Texture mode creates a new base-color material for the chosen object. Materials
used by other objects are preserved. It supplies one texture input; roughness,
normal, displacement and other PBR maps are not generated or assembled by this
handoff.

## Local scene and camera tools

Ask the assistant to inspect your scene, describe a named object, select objects,
move to a frame, or plan a camera move. `camera_path` can create an animated
camera from a preset, a description or explicit waypoints. Review the resulting
camera and animation in Blender before saving.

`screenshot_viewport` captures the visible 3D viewport with overlays.
`render_still` returns a quick OpenGL camera or viewport still at a chosen size.
Both require the graphical interface and return image content to the assistant.
They do not export a finished video.

## Python permission

The dedicated tools above work with arbitrary Python execution disabled. For
more general Blender scripting, **Allow connected assistants to run Python** is a
separate preference, off by default. Enable it only for an assistant you trust
with your local files and Blender project. Python runs with Blender's user
permissions; the limited blocked operations do not make it a sandbox.

The assistant can call `blender_api_help` to inspect the running Blender Python
API before writing a script. This read-only helper does not need Python
execution permission.

## Headless use

For a separately configured isolated background session:

```sh
blender --background --online-mode --command open_studio --port 9876
```

The extension must be enabled in that Blender profile. Authentication details
are written to the private connection file. Use the stdio bridge with that
file; screenshots and OpenGL captures need the GUI. See the [MCP reference](MCP.md)
for the optional explicit token configuration.

## Troubleshooting

- **Assistant cannot connect:** keep Blender open, allow its online access,
  start the local server, copy the current setup and reconnect the MCP client.
  The stdio bridge needs the current extension and connection-file paths.
- **Assistant cannot generate an image:** check the assistant's own image tool.
  Blender's local import works with a generated or existing supported file.
- **Image import fails:** put the actual image file in the returned inbox and
  pass only its basename. Use PNG, JPEG or WebP within the size limit.
- **Texture destination fails:** use an editable mesh in the current scene with
  UV coordinates. Inspect the exact object name with `scene_summary`.
- **Screenshot fails:** open a 3D viewport in Blender's graphical interface.
- **Python is disabled:** use the dedicated local tools, or explicitly enable
  the separate Python preference when the task requires trusted scripting.

For fork bugs, follow [Support](../SUPPORT.md). Include Blender and extension
versions, the action and a sanitized error. Keep the token and connection file
out of reports.

## Privacy

A connected assistant can receive requested scene data and viewport captures.
It may process those results using its own service. Local image import makes no
external request; the assistant's generation step follows its own account and
service settings. Keep the local token and private connection file out of shared
reports. Imported pixels remain in a saved `.blend` file after inbox cleanup.
See [Privacy](PRIVACY.md) for permissions and local storage details.

## Historical upstream reference

Retained architecture documents and old screenshots describe the upstream
service-backed extension. Their references to model catalogs, saved cloud
results, materials, panoramas or generation panels are historical source context.
Those workflows are not current setup or usage instructions for this build.
This guide and the [current MCP reference](MCP.md) define the exposed workflow.
