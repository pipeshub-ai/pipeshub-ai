# Usage telemetry plug-in

Everything this build knows about collecting and sending usage data is in this
directory. The application never imports it: application code announces
platform events (`src/libs/platform-events`) and this plug-in listens. It is
listed in `src/plugins.generated.ts`, which `scripts/generate-plugins.mjs`
writes.

At run time it is switched by `TELEMETRY_COLLECTION_FLAG` and by each
organisation's Data Collection setting. It sends no document or query content.

## What it records

| Source | Becomes |
| --- | --- |
| HTTP middleware | `pipeshub_http_requests_total`, `pipeshub_http_request_duration_seconds` |
| `http.request.completed` | `pipeshub_usage_sdk_requests_total`, event `sdk.client.seen` |
| sign-in, organisation, token and MCP events | events `login`, `signup`, `org_created`, `pat_created`, `mcp_connected`, `mcp_tool_called`, ... and `pipeshub_activity_total` |
| database, document store and messaging events | the `db_*`, `docstore_*` and consumer metrics |

### SDK and MCP server usage

The application classifies every request (`classifyCaller` in
`src/libs/utils/request-client.utils.ts`) from `X-PipesHub-Client`, the
generated clients' User-Agent and the request-id prefix, and puts the result in
the `http.request.completed` event. This plug-in counts the requests whose
channel is `sdk` or `mcp`:

    pipeshub_usage_sdk_requests_total{service, channel, sdk_language, sdk_version, outcome, org, domain}

- `channel`: `sdk` or `mcp` (the PipesHub MCP server calling the REST API).
- `sdk_language`: `typescript`, `python`, `go` or `unknown`; `other` for the MCP server.
- `sdk_version`: the SDK release, or for `mcp` the MCP server's release;
  `unknown` when none was sent. At most 20 distinct versions are kept per
  language in a process; later ones are counted as `other`.
- `outcome`: `success` or `failure`.

The event `sdk.client.seen` (`orgId`, `domain`, `channel`, and `sdk_language` +
`sdk_version` or `mcp_server_version`) is sent the first time an organisation
uses a given client in a UTC day. It is de-duplicated in the process, with at
most 5000 entries a day, so several replicas may each send it once.

MCP clients of the hosted `/mcp` endpoint are reported separately, by
`mcp_connected` (client name and version) and `mcp_tool_called`.

None of this carries a user id, an address or a name: only the organisation id
and the address domain that the other metrics already carry.

## Building an image without it

Nothing outside this directory imports the plug-in or `prom-client`;
`tests/architecture/telemetry-is-a-plugin.test.ts` fails if that changes, and
it builds and type-checks the tree with this directory removed.

`prom-client` is in `package.json` only for this plug-in. An image that must
not contain usage collection removes both, in `backend/nodejs/apps`, before it
installs and compiles:

    rm -rf src/plugins/telemetry
    node scripts/generate-plugins.mjs          # rewrites src/plugins.generated.ts without it
    npm uninstall --package-lock-only --ignore-scripts prom-client
    npm ci                                     # as the Dockerfile does; prom-client is no longer installed
    npm run build

`npm uninstall --package-lock-only` edits `package.json` and
`package-lock.json` without touching the network; it also drops the packages
only `prom-client` needed (`tdigest`, `bintrees`, `@opentelemetry/api`). In the
repository's `Dockerfile` the two `package*.json` files are copied before
`npm ci` and `src` after it, so the `npm uninstall` line goes before `npm ci`
and the first two lines after `COPY backend/nodejs/apps/src`. That stage does not copy
`scripts/` today; a build that does this copies `scripts/generate-plugins.mjs`
as well.

A build that selects plug-ins from a `plugins.profiles.json` (see the header of
`scripts/generate-plugins.mjs`) declares the same thing as data, under
`plugins."plugins/telemetry".dependencies`, and
`node scripts/generate-plugins.mjs --describe` then lists the directories and
dependencies to remove for the chosen selection.
