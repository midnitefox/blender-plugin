# Privacy and data handling

Scenario for Blender is a client for your own Scenario account. This page describes
what the extension sends, where it sends it, what stays on your machine, and the
limits of its current controls. It does not replace Scenario's service policies.

## What leaves your machine, and when

- With valid credentials and online access enabled, cost previews send your prompt
  and available model parameters while you edit the visible form, before you press
  Generate. The API request uses `?dryRun=true` to request a price without starting
  a generation. Generate sends the completed request again and can spend credits.
- Viewport/camera stills, playblast clips, Render Result and selected-mesh GLB
  snapshots can be prepared locally. Upload reference is a separate action before
  the final generation quote. Exports can include evaluated geometry and embedded
  materials. Local file attachment alone does not start generation. Film capture
  likewise requires separate capture and upload approval.
- New, Rewrite and Translate send prompt text and model context for an exact
  estimate, then require separate approval to submit the prompt operation.
  Render Image/Video Spark estimates also send uploaded view references and need
  their own approval before the final render quote. Prompt helpers can spend
  credits independently of generation.
- The model catalog is requested automatically when credentials are available;
  opening the model picker can download thumbnails. The same load lists the
  trained models of the selected credentials and project. Opening the picker's
  Trained chip reads base-model descriptions to find which accept LoRAs. For
  downloaded saved jobs listed in Jobs or Generations, whether or not those
  panels are open, the extension reads the asset metadata of video, 3D and
  audio results from Scenario and downloads Scenario's preview images,
  automatically and repeatedly for up to about five minutes per result while a
  preview is not ready; image previews are decoded from the downloaded files
  without a request. Test connection requests one
  public-model list page with at most one model to verify model access; it does
  not discover account or project identity. Refresh cloud and Older in Generations retrieve job
  history and prompt text; connected agents can request history too. Local
  MCP `inspect_result_previews` requests the same previews for any downloaded
  saved job an agent names, with the same reads.
- Scenario API requests authenticate with your selected key/secret pair using an
  HTTP Basic `Authorization` header over TLS. They identify the extension with
  `ScenarioBlender/<version>`. Signed storage transfers use the URL's authorization;
  they do not forward the Scenario API key/secret header.
- Studio > Library reads one page of asset metadata, sending any search text,
  only when you choose **Refresh**, **Previous**, **Next** or a collection's
  **Browse**, and collection names and counts only when you choose
  **Load collections**. Confirming **Organize** reads the chosen asset's tags and
  memberships and the target collection or matching names; after you review the
  change and choose **Apply**, it sends that asset ID with the tags or collection
  name you entered. The change uses no
  credits and Blender Undo does not reverse it.
- Workflows send only on explicit actions: **Private** or **Public** lists
  workflows and **Load workflow inputs** reads one, **Request workflow price**
  sends the form's inputs for an exact estimate, and the workflow's **Generate**
  submits them and can spend credits. Reviewing a waiting step sends your decision.
- An agent connected to the local MCP server can request scene information,
  captures, uploads and paid generations on your behalf. It can also change
  the collection membership and tags of your Scenario assets or create a
  collection, sending the chosen asset IDs, tags and collection names. Its own
  service may receive what that agent reads from Blender. That includes your
  asset library's metadata, the names, IDs and types of the selected project's
  trained models, and its per-project trained-model defaults, which the agent
  can also save or clear.
- Set up official repository and Check for updates use Blender's native extension
  manager to contact the public repository. Blender sends its usual compatibility
  parameters and network metadata; these actions do not send Scenario credentials,
  prompts, scene contents or job history. Installing an update is a separate choice.

## Where it goes

The Scenario API is `https://api.cloud.scenario.com`. Upload parts, generated
files, full text results, model thumbnails, saved-result preview images or
clips and workflow review images use content URLs returned by the service, so
content transfers are not limited to the API hostname. The Create a key in the
portal button opens `https://app.scenario.com/team` in your browser. The local
agent connection uses loopback HTTP, described below.

The official update repository and handbook use `https://blender.scenario.com/`,
served by Vercel from the copy hosted by GitHub Pages. The site counts each
download of a release ZIP and each fetch of the repository index. A count records
the extension version for a download, whether the request came from Blender,
Blender's version, release cycle, operating system and processor from its
User-Agent, and the country Vercel derives from your address. The count holds
no IP address and no identifier that links one request to another. The Release
ZIPs button opens `https://github.com/scenario-labs/blender-plugin/releases` in
your browser.
Blender stores the repository configuration and cache through its native manager.

The **Create a key in the portal**, **Update guide** and **Release ZIPs** links
and the folder and file buttons (**Output folder**, the Film **Open capture**
and **Open capture files**, and the Film export and case-study **Open folder**)
start your web browser, file manager or default application: the extension
starts `open` on macOS, `xdg-open` on Linux, or on Windows Blender's isolated
Python interpreter calling `os.startfile`, and strips `SCENARIO_`, `PYTHON` and proxy variables
from the environment it passes. They never use Blender's `wm.path_open` or
`wm.url_open`, which pass Blender's whole environment, including
`SCENARIO_API_KEY` and `SCENARIO_API_SECRET` when Credentials > Environment
supplies them, to the application they start. Links open only as `https`
addresses. A browser or file manager that is already running receives only the
link or path; what it records, such as history, is outside the extension's
control, and macOS still adds proxy settings from your login session to every
application it launches.

The extension has no telemetry, analytics or automatic crash-reporting service,
and no independent update-check service. Blender manages configured extension
repositories and their update checks separately.

Blender's Allow Online Access setting gates catalog loading, MCP startup and
interactive network actions. It is not an instantaneous network cutoff: work
already started can finish. Shared SDK requests and result/upload transfers check
the selected permission snapshot. Model picker thumbnail downloads check it, or a
main-thread mirror of online access when no catalog is selected, only before
connecting; they do not recheck it during a transfer or retry. Close Blender or
disconnect the network when you need a complete cutoff. These gaps are tracked in
[#440](https://github.com/scenario-labs/blender-plugin/issues/440).

## What is stored on your machine

When Credentials is set to Saved in Blender, Blender saves the key and secret in
`userpref.blend` as plain text. Password fields hide their display; they do not
encrypt storage or use an OS keychain. Standard preference locations are:

- Linux: `$HOME/.config/blender/X.Y/config/userpref.blend`
- macOS: `/Users/$USER/Library/Application Support/Blender/X.Y/config/userpref.blend`
- Windows: `%USERPROFILE%\AppData\Roaming\Blender Foundation\Blender\X.Y\config\userpref.blend`

Portable installations and custom Blender resource paths can use another location.
To avoid saving the pair in Blender, select Credentials > Environment and launch
Blender with both `SCENARIO_API_KEY` and `SCENARIO_API_SECRET`. This source is
explicitly selected; it does not override or combine with saved credentials.
Switching sources does not erase values already saved in Blender. Clear both
saved fields and save preferences to remove them there; also check backups and
how your launcher stores environment variables. Rotate the key in Scenario if
it leaks or a machine holding it is lost.

The extension's own user directory under Blender's extensions folder stores
shared job requests, quotes, prompts and recovery state in
`state/shared-jobs/jobs.sqlite3`. Keep its `scope.key` alongside the database for
recovery. The key derives local credential-bound identities; it does not encrypt
the database or store your API key/secret. An explicit project override selects a
separate scope in that storage. The same database keeps any per-lane default you
save for trained or private models: their model IDs and optional strengths, per
credential and project scope, without prompts or model details.

Verified result files live under `state/shared-results`; upload intents and
private source copies live under `state/shared-uploads`, including
`uploads.sqlite3` and `sources`. Requested
[saved-result previews](RESULT_PREVIEWS.md) are cached under
`cache/result-previews`: server preview images and clips, decoded stills, audio
envelopes, temporary private copies of saved results awaiting decoding, and
digests and asset IDs, without signed URLs. **Load previews** on a waiting
workflow step adds stills of the assets that step lists for review: the images
themselves, up to 8 MiB each, or their server thumbnails. Unused entries beyond
512 MiB are evicted, and the directory is safe to delete, even while Blender
runs. Preview icons, of saved results and of review assets, are built in memory
from those files; the extension never asks Blender to load them through its
file-thumbnail service, which writes copies to the operating system's thumbnail
folder, and images decoded for previews never enter the open blend file. Audio
envelopes are computed by a separate offline Blender process that reads only
the private copy; its disposable profile, temporary files and log stay in that
copy's work directory and are removed with it.
**Play** and **Play in Blender** copy one verified saved video or audio result
into a private, read-only file under `cache/media-playback` and open that copy,
never the saved file, in the system player, your operating system's default
application started like the links above, or in a separate Blender animation
player. Neither player receives `SCENARIO_`, `PYTHON` or proxy variables, and
so no Scenario credentials; one already running receives only the copy's path,
and what it records, such as recent files, is outside the extension's control. Up to four copies or 1 GiB are kept for reuse; the rest,
and every copy when the connection changes, a file loads or the extension is
disabled, are removed unless a running animation player, system player launcher
or audio playback still uses one. A copy left behind, for example one a player
still read when the extension was disabled or a Windows player held open, is
removed once a day old by the next Play. The **Play** and **Play in Blender**
buttons of retained prototype results start their players the same way.
**Play audio** plays the same kind of private copy through Blender's audio
engine in a separate offline Blender process, not in your session. That process
starts from the same filtered environment, without `SCENARIO_`, `BLENDER_`,
`PYTHON` or proxy variables, does not load the extension, and makes no network
request. It reads only the copy and sends its sound to the output device chosen
in Blender's Preferences > System > Sound. Its request, disposable profile,
temporary files and two small status files live in a private `listen-*`
directory under `cache/audio-player`, without a URL, credential or prompt; its
output is discarded. The directory is removed when the process ends, and one
left by a crash is removed once a day old by the next Play audio. Stop, another
Play audio, a connection change, a file load, disabling the extension and
quitting Blender end the process; it also ends when Blender exits unexpectedly.
Capture/export staging and thumbnails also use the extension's user state/cache
directories. Explicit local output tools can write to the configured Output
Folder, normally `~/Downloads/Scenario`.
A Film video export writes only to the destination you approve; local agents
can choose destinations only inside the Output Folder. Its private
staging keeps a scene snapshot, the encoded video and child logs until the
owner discards it; the Blender console logs the destination file name and its
SHA-256. An interrupted export can leave an empty placeholder or a hidden
`.scenario-*.partial` file next to the destination for manual removal.
A Film case-study bundle follows the same destination, staging and leftover
rules; its staging also holds copies of the bundled media until the archive is
verified. The archive contains the chosen scenes, copies of the media they use,
the recipe without its project, credentials or URLs, and a production ledger with
titles, models, saved states and quoted costs but no credentials, URLs, request,
job, asset, upload, project or team identifiers or local paths. Scenario
properties naming jobs, assets or productions and the extension's saved settings
(composer lanes with their reference uploads and local reference paths, Film and
workflow forms) are removed from the bundled data, and the export refuses a
bundle that still holds a known private identifier, extension storage or a source
media folder. Model IDs, scene and object names, other custom properties,
output-only path settings the export does not rewrite (such as strip proxy
folders, which can name your user folder) and unused bytes Blender keeps from
earlier settings can remain; review a bundle before sharing it.
Scene properties, imported media and prompts can be saved in your `.blend` file.
A form's chosen LoRAs and compositions are saved there too: their model IDs,
names, strengths and a hashed connection scope that stops another credential pair
or project from using them. Model IDs are opaque references, not credentials.
Thumbnails of your private models are cached in a separate folder per credential
and project scope, apart from public model thumbnails.
Older `state/jobs.json` or model-cache files may remain from the prototype; local
record inspection does not resume their cloud jobs or migrate their ownership.

Uninstalling is not a promise to erase these files. Remove extension user data,
outputs and `.blend` copies separately when no longer needed. Local deletion does
not delete cloud jobs or assets.

Extension logs go to Blender's console; it does not configure its own log file.
A terminal or host application may retain that output. Diagnostics can include
file paths, job identifiers and service errors; review them before sharing.
Agent setup snippets contain the local session token. Do not assume logs,
clipboard history or screenshots are safe to publish without inspection.

The clipboard is written by Copy buttons (asset IDs, errors and MCP setup) and by
copy/cut in the composer; it is read when you paste into the composer. Clipboard
managers may retain copied tokens or text.

## The local MCP server

In a Blender window, the server starts by itself with the extension whenever
**Allow Online Access** is on, and listens for local agents until Blender quits or
you choose **Stop MCP server** in **Scenario > Agents (MCP)**; the extension's
network permission names this `127.0.0.1` listener. Background Blender starts it
only with the explicit `scenario_blender` command. The server binds to `127.0.0.1`
and refuses any other address, using the port selected in Preferences (default
`9876`), so only programs on this computer can connect. Every request must name
`127.0.0.1`, `localhost` or `[::1]` as its `Host`, which stops a web page that
rebinds its own domain to this computer from reading even the server's version
status, and the server closes a connection that sends nothing for 60 seconds while
it waits to read a request. Calls
require the local session token. Blender normally generates it for the session;
an explicitly supplied token must be changed by its owner. This is a local
connection, not Scenario's API credential, and programs holding it can read the
scene, take captures and request generation that spends your Scenario credits.
An agent can send a local file to your Scenario account only when the extension
saved it in its own folders or you chose it in Blender during this session, in a
reference **Upload** file browser, the Render Video first-frame field or a workflow
input's **File** field; any other path, including the Output Folder, is refused
before the file is read. See [agent file uploads](MCP.md#agent-file-uploads).
MCP `generate` requires the unchanged shared quote ID and exact approved cost.
Prompt, Blockout and Film commands likewise expose separate estimate/approval
steps. Collection and tag changes need a separate review and apply call; once
applied they change Scenario account metadata immediately, use no credits and
are not reversed by Blender Undo. Recovery cannot resubmit an uncertain
generation, and applying recovered results requires explicit destination
approval. These controls do not establish that an agent has obtained your
permission: only connect agents you authorize to perform those actions.

Arbitrary Python execution is disabled by default. Enabling it gives a connected
agent code execution in Blender with your user's permissions, including reading
and sending files the upload rule above would refuse. That switch is not
a sandbox for Python or for the other scene and generation tools. The
[MCP reference](MCP.md#security-model) describes access controls and connection setup.

## Terms

Scenario service use is covered by its [Terms and Conditions](https://www.scenario.com/terms-and-conditions)
and [Privacy Policy](https://www.scenario.com/privacy-policy), subject to any
applicable account agreement. Security and compliance information is available
in the [Scenario Trust Portal](https://trust.scenario.com). Consult
[Scenario documentation](https://docs.scenario.com) for account and API access
requirements. This extension note describes local behavior; service retention,
processing and contractual commitments are governed by those service policies.
