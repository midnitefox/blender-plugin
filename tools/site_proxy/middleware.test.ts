// SPDX-FileCopyrightText: 2026 Scenario Inc.
// SPDX-License-Identifier: GPL-3.0-or-later
// Run with: node --test tools/site_proxy/
import assert from "node:assert/strict";
import { createServer, type IncomingMessage, type Server } from "node:http";
import type { AddressInfo } from "node:net";
import { after, before, beforeEach, test } from "node:test";

import middleware, { config } from "./middleware.ts";

const BLENDER = "Blender/5.0.1 (Darwin arm64; cycle=release)";
const BROWSER = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) Firefox/140.0";

type Captured = { path: string; body: Record<string, unknown> };

let server: Server;
let received: Captured[] = [];
let respond: (request: IncomingMessage, reply: (status: number) => void) => void;

function read(request: IncomingMessage): Promise<string> {
  return new Promise((resolve) => {
    let text = "";
    request.on("data", (chunk) => (text += chunk));
    request.on("end", () => resolve(text));
  });
}

before(async () => {
  server = createServer(async (request, response) => {
    received.push({ path: request.url ?? "", body: JSON.parse(await read(request)) });
    respond(request, (status) => response.writeHead(status).end());
  });
  await new Promise<void>((resolve) => server.listen(0, "127.0.0.1", resolve));
  process.env.POSTHOG_HOST = `http://127.0.0.1:${(server.address() as AddressInfo).port}`;
});

after(() => {
  server.closeAllConnections();
  server.close();
});

beforeEach(() => {
  received = [];
  respond = (_request, reply) => reply(200);
  process.env.POSTHOG_PROJECT_TOKEN = "phc_test";
});

async function visit(path: string, headers: Record<string, string> = {}, method = "GET") {
  const pending: Promise<unknown>[] = [];
  const result = middleware(new Request(`https://blender.scenario.com${path}`, { method, headers }), {
    waitUntil: (promise) => pending.push(promise),
  });
  await Promise.all(pending);
  return result;
}

test("runs only for the repository directory", () => {
  assert.equal(config.matcher, "/repo/:path*");
  assert.equal(config.runtime, "nodejs");
});

test("records a Blender download with its versions, platform and country only", async () => {
  const result = await visit("/repo/scenario-0.10.0.zip", { "user-agent": BLENDER, "x-vercel-ip-country": "FR" });

  assert.equal(result, undefined);
  assert.equal(received.length, 1);
  const [{ path, body }] = received;
  assert.equal(path, "/i/v0/e/");
  assert.equal(body.api_key, "phc_test");
  assert.equal(body.event, "extension_downloaded");
  assert.match(String(body.distinct_id), /^[0-9a-f-]{36}$/);
  assert.deepEqual(body.properties, {
    extension_version: "0.10.0",
    client: "blender",
    blender_version: "5.0.1",
    os: "Darwin",
    arch: "arm64",
    blender_cycle: "release",
    country: "FR",
    $process_person_profile: false,
    $geoip_disable: true,
  });
});

test("uses a new anonymous identifier for every request", async () => {
  await visit("/repo/scenario-0.10.0.zip", { "user-agent": BLENDER });
  await visit("/repo/scenario-0.10.0.zip", { "user-agent": BLENDER });

  assert.equal(received.length, 2);
  assert.notEqual(received[0].body.distinct_id, received[1].body.distinct_id);
});

test("records a repository check from Blender", async () => {
  await visit("/repo/index.json?platform=macos-arm64&blender_version=5.0.1", { "user-agent": BLENDER });

  assert.equal(received.length, 1);
  assert.equal(received[0].body.event, "repository_checked");
  assert.deepEqual(received[0].body.properties, {
    client: "blender",
    blender_version: "5.0.1",
    os: "Darwin",
    arch: "arm64",
    blender_cycle: "release",
    $process_person_profile: false,
    $geoip_disable: true,
  });
});

test("records other clients without Blender details", async () => {
  await visit("/repo/scenario-0.10.0.zip", { "user-agent": BROWSER, "x-vercel-ip-country": "not a code" });

  assert.equal(received.length, 1);
  assert.deepEqual(received[0].body.properties, {
    extension_version: "0.10.0",
    client: "other",
    $process_person_profile: false,
    $geoip_disable: true,
  });
});

test("ignores other repository files, HEAD requests and resumed downloads", async () => {
  await visit("/repo/");
  await visit("/repo/inventory.json");
  await visit("/repo/scenario-0.10.0.zip.sha256");
  await visit("/repo/scenario-0.10.0.zip", { "user-agent": BLENDER }, "HEAD");
  await visit("/repo/scenario-0.10.0.zip", { "user-agent": BROWSER, range: "bytes=1048576-" });

  assert.deepEqual(received, []);
});

test("counts a download that requests its range from the first byte", async () => {
  await visit("/repo/scenario-0.10.0.zip", { "user-agent": BROWSER, range: "bytes=0-" });

  assert.equal(received.length, 1);
});

test("records nothing without a project token", async () => {
  delete process.env.POSTHOG_PROJECT_TOKEN;

  assert.equal(await visit("/repo/scenario-0.10.0.zip", { "user-agent": BLENDER }), undefined);
  assert.deepEqual(received, []);
});

test("never fails the download when the analytics service rejects the event", async () => {
  respond = (_request, reply) => reply(500);

  assert.equal(await visit("/repo/scenario-0.10.0.zip", { "user-agent": BLENDER }), undefined);
  assert.equal(received.length, 1);
});

test("gives up on an analytics service that does not answer", async () => {
  respond = () => {};
  const started = Date.now();

  assert.equal(await visit("/repo/scenario-0.10.0.zip", { "user-agent": BLENDER }), undefined);
  assert.equal(received.length, 1);
  assert.ok(Date.now() - started < 5000);
});
