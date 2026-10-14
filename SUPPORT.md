# Open Studio support

Start with the [user guide](docs/USER_GUIDE.md), especially
[troubleshooting](docs/USER_GUIDE.md#troubleshooting), and the
[MCP setup guide](docs/MCP.md).

## Report a fork issue

Use [midnitefox/blender-plugin issues](https://github.com/midnitefox/blender-plugin/issues)
for this Open Studio fork. Include:

- Blender and extension versions, operating system and assistant client;
- the action you attempted and what happened;
- a sanitized error and small reproducible example when possible;
- whether the failure concerns the local connection, image import or a scene tool.

Keep bearer tokens, connection-file contents, private project data and client
configurations containing secrets out of public reports. Review screenshots for
sensitive information before sharing them. For a suspected security issue,
request a private reporting channel from the fork maintainer without publishing
exploit details or credentials.

## Updates and scope

Use this fork's [releases](https://github.com/midnitefox/blender-plugin/releases)
when a new build is available. This is a development preview supporting local
Blender tools and assistant image handoff. The broader upstream generation
workflows are not exposed by the current build.

The assistant provides its own image-generation tools. Generation availability,
subscription and account questions belong with that assistant's service.
Extension support is provided on a best-effort basis without a response-time
commitment. See [Privacy](docs/PRIVACY.md) before sharing diagnostic information.
