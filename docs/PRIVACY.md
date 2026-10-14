# Open Studio privacy and data handling

Open Studio connects your assistant to Blender on this computer. The extension
exposes local scene tools and an inbox for images produced by the assistant.
This page covers the extension; image generation and assistant processing follow
the policies and account settings of the assistant you choose.

## What a connected assistant can receive

A client with the local MCP token can request scene summaries, object properties,
datablock counts and Blender API descriptions. Scene details can include object
names, custom properties, material names and the current file path. Viewport and
camera captures can reveal anything visible in the project. The assistant may
send those tool results to its own service as part of your conversation.

Connect only clients you authorize to work on that project. Dedicated tools can
also change selection, the frame, the scene camera and imported images. Stopping
the local server prevents further MCP requests.

## Image handoff and local storage

The assistant creates or edits an image using its own tool, then saves or copies
it into the extension's local inbox. `import_agent_image` reads that bounded
local file and packs the decoded image into Blender. It makes no external
request. The assistant's generation step is governed by that assistant's service.

The inbox persists in the extension's user state directory until its files are
removed. Imported packed pixels persist in the saved `.blend` file; deleting the
inbox copy does not remove pixels already packed into a saved project. The tool
can return image names, object names and a content hash to the assistant.
Temporary capture and image-decoding files are cleaned up after use.

## The local MCP server

The server listens on `127.0.0.1` and requires a bearer token. Blender's online
access must be enabled to start the listener. The stdio bridge uses a private
`agent-connection.json` to find the current address and token. Treat that file,
the token and any copied HTTP client configuration as access credentials.

Do not publish them in screenshots, logs, issue reports or shared repositories.
The extension's user data lives outside the installed package. A copied
configuration may include local installation paths even when it contains no token.

## Python permission

**Allow connected assistants to run Python** is off by default. Enabling it gives
the connected assistant arbitrary Python execution with Blender's local user
permissions. The limited blocked call spellings are not a security sandbox.
Code can access files or the network according to those permissions.

The dedicated image and scene tools work without enabling arbitrary Python.
`blender_api_help` only inspects the running Blender Python API.

## Release and help links

The release and setup-guide buttons open this fork's pages on
[GitHub](https://github.com/midnitefox/blender-plugin) in your browser when you
click them. The browser handles those website requests and its own stored sign-in.

## Reports and other references

For help with the extension, follow [Support](../SUPPORT.md). Remove tokens,
connection-file contents and private scene details from public reports. For
image-generation account or data-handling questions, consult your assistant's
own service documentation.

Historical upstream documentation describes other data flows. This page and the
[current MCP reference](MCP.md) describe the exposed assistant-only workflow.
