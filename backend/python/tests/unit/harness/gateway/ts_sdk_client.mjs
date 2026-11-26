// TG-18: the official TypeScript MCP SDK's client (the Node app's locked @modelcontextprotocol/sdk) against the tool
// gateway. Its client validates every answer against the SDK's schemas, so a reply off the spec fails here.
// Usage: node ts_sdk_client.mjs <sdk dir> <gateway url> <bearer token>; prints one JSON object on stdout.
import { pathToFileURL } from "node:url";
import { join } from "node:path";

const [sdkDir, url, token] = process.argv.slice(2);
const load = (path) => import(pathToFileURL(join(sdkDir, "dist", "esm", path)).href);
const { Client } = await load("client/index.js");
const { StreamableHTTPClientTransport } = await load("client/streamableHttp.js");

const transport = new StreamableHTTPClientTransport(new URL(url), {
  requestInit: { headers: { Authorization: `Bearer ${token}` } },
});
const client = new Client({ name: "tg18-ts-sdk", version: "1.0.0" });
await client.connect(transport);
const listed = await client.listTools();
const ok = await client.callTool({ name: "search", arguments: { q: "q3" } });
const failed = await client.callTool({ name: "reject", arguments: { q: "1" } });
await client.ping();
const out = {
  protocolVersion: transport.protocolVersion ?? null,
  server: client.getServerVersion(),
  capabilities: client.getServerCapabilities(),
  tools: listed.tools.map((t) => t.name),
  ok: { isError: ok.isError ?? false, text: ok.content.map((c) => c.text).join(""), structured: ok.structuredContent },
  failed: { isError: failed.isError ?? false, structured: failed.structuredContent },
};
await client.close();
process.stdout.write(JSON.stringify(out));
