// SPDX-FileCopyrightText: 2026 Scenario Inc.
// SPDX-License-Identifier: GPL-3.0-or-later
// Counts extension downloads and repository checks on blender.scenario.com.
// It runs before vercel.json forwards the request to GitHub Pages and never
// changes the response: the event is sent after the request continues, and
// any failure only loses that count. Nothing identifies the visitor: no IP
// address, no stable identifier, only the fields built below.

export const config = { matcher: "/repo/:path*", runtime: "nodejs" };

interface RequestContext {
  waitUntil(promise: Promise<unknown>): void;
}

// Blender's extension manager sends "Blender/5.0.1 (Darwin arm64; cycle=release)".
const BLENDER = /^Blender\/(\d+\.\d+\.\d+)(?: \(([^ ;)]+) ([^ ;)]+); cycle=([a-z]+)\))?/;
const ARCHIVE = /^\/repo\/scenario-(\d+\.\d+\.\d+)\.zip$/;
const TIMEOUT_MS = 2000;

function describe(request: Request): { event: string; properties: Record<string, unknown> } | undefined {
  if (request.method !== "GET") return undefined;
  // A resumed download already counted when it started.
  const range = request.headers.get("range");
  if (range && !range.startsWith("bytes=0-")) return undefined;

  const path = new URL(request.url).pathname;
  const properties: Record<string, unknown> = {};
  let event: string;
  const archive = ARCHIVE.exec(path);
  if (archive) {
    event = "extension_downloaded";
    properties.extension_version = archive[1];
  } else if (path === "/repo/index.json") {
    event = "repository_checked";
  } else {
    return undefined;
  }

  const blender = BLENDER.exec(request.headers.get("user-agent") ?? "");
  properties.client = blender ? "blender" : "other";
  if (blender) {
    properties.blender_version = blender[1];
    if (blender[2]) {
      properties.os = blender[2];
      properties.arch = blender[3];
      properties.blender_cycle = blender[4];
    }
  }
  const country = request.headers.get("x-vercel-ip-country") ?? "";
  if (/^[A-Z]{2}$/.test(country)) properties.country = country;
  // Anonymous events: no person profile, and no location from the proxy's own address.
  properties.$process_person_profile = false;
  properties.$geoip_disable = true;
  return { event, properties };
}

async function send(token: string, event: string, properties: Record<string, unknown>): Promise<void> {
  const host = process.env.POSTHOG_HOST || "https://us.i.posthog.com";
  try {
    const response = await fetch(`${host}/i/v0/e/`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify({ api_key: token, event, distinct_id: crypto.randomUUID(), properties }),
      signal: AbortSignal.timeout(TIMEOUT_MS),
    });
    if (!response.ok) console.warn(`Download count not recorded: HTTP ${response.status}`);
  } catch (error) {
    console.warn(`Download count not recorded: ${(error as Error).name}`);
  }
}

export default function middleware(request: Request, context: RequestContext): undefined {
  const token = process.env.POSTHOG_PROJECT_TOKEN;
  const described = token ? describe(request) : undefined;
  if (token && described) context.waitUntil(send(token, described.event, described.properties));
  return undefined;
}
