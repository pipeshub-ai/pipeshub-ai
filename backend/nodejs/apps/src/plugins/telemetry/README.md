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

## How it is sent: the legacy endpoints by default, usage telemetry v3 by choice

`USAGE_TELEMETRY_CONTRACT` chooses how the data reaches the collector. It is
the only setting; the switch above and each organisation's Data Collection
setting decide whether anything is sent at all, exactly as before.

| Value | Behaviour |
| --- | --- |
| `legacy` (default; also unset or unrecognised) | The legacy endpoints only: `/collect-metrics` and `/collect-events`, as every earlier version did. No v3 request is made and the install is not registered |
| `auto` | Use contract v3. If the collector does not answer as a v3 collector, use the legacy endpoints and ask again a day later |
| `v3` | v3 only. Against a collector without v3 nothing is sent; what was counted is kept and sent when v3 answers |

v3 is opt-in because it sends less (the table under "What v3 does not carry"):
dashboards that group by organisation or domain, and those built on the HTTP
metrics, show nothing for an install that sends v3. Set `auto` or `v3` once
that is acceptable. The Node and Python services read the same variable; set
it the same for all of them.

**v3** (`v3/`): the install registers once with `POST /v3/installs` and is
given a secret, stored as `installSecret` in the same encrypted configuration
value as `installId` (`/services/metricsCollection`) and never logged. This
service registers and is the only writer of that value; the Python services
read the secret from it. Every push is authenticated with the secret. Metrics
go to `/v3/metrics` as named points, counters as the amount counted since the
previous push; events go to `/v3/events`. Each process has its own id and
numbers its pushes, so the collector adds up several replicas and applies a
push it receives twice once.

- A push is kept until the collector acknowledges it and is sent again
  unchanged: after 30 seconds, then twice as long each time up to 30 minutes,
  or as long as the collector asks. One batch of metrics and one of events is
  pending at most; what is counted meanwhile stays in the registry and in the
  event buffer (5000 events).
- A collector is a v3 collector when its answer carries `X-Usage-Contract: 3`.
  Any other answer to a v3 request (the `404` or `405` of an older collector,
  a proxy's login page) switches to the legacy endpoints for 24 hours.
- A restarted process gets a new id and numbers from 1 again; the one batch
  the old process had not got acknowledged is lost with it.
- If the collector refuses the secret, the configuration is read again (another
  replica may have rotated it), then the install registers again. If the id is
  held by someone else, the legacy endpoints are used for a day and a warning
  is logged.
- `USAGE_TELEMETRY_EDITION=ee` reports the edition as `ee`; anything else is `oss`.

**What v3 does not carry.** v3 sends nothing the legacy endpoints did not
send, and less: only what the usage contract names, and nothing that says who.

| Legacy | v3 |
| --- | --- |
| labels `org`, `domain` on every usage metric | dropped; values are added up over them, so a count is for the install |
| labels `service`, `kb`, `mimetype` on `pipeshub_activity_total`; `service` on `pipeshub_usage_sdk_requests_total` | dropped |
| event props `orgId`, `userId`, `email`, `domain`, `fullName` | dropped |
| event props `user_agent`, `auth_type`, `oauth_client_id`, `client_id`, `transport`, `referrer_domain`, and `channel` on `org_created` | dropped |
| `pipeshub_http_requests_total`, `pipeshub_http_request_duration_seconds` | not sent: the contract calls them operational |
| `pipeshub_metric_collection_enabled` | not sent: an install that has opted out sends nothing |
| `pipeshub_db_*`, `pipeshub_org_context_missing_total`, `docstore_*`, `pipeshub_consumer_inbox_*`, `notification_event_refused_total` | not sent |
| events `pat_created`, `pat_access_changed`, `oauth_token_issued`, `mcp_tool_called` | not sent: the contract has no such event |

What is sent, and under which name, is the two tables in `v3/translate.ts`.
One difference in meaning follows from dropping the organisation:
`sdk.client.seen` is still recorded once per organisation, client and day, so
over v3 the collector sees one event per organisation without knowing which.

## Building an image without it

Nothing outside this directory imports the plug-in or `prom-client`;
`tests/architecture/telemetry-is-a-plugin.test.ts` fails if that changes, and
it builds and type-checks the tree with this directory removed.

`prom-client` is in `package.json` only for this plug-in, and
`plugins.dependencies.json` (next to `src`) says so as data (`pluginDependencies`: plug-in
name to package names). An image that must not contain usage collection removes
both, in `backend/nodejs/apps`, before it installs and compiles:

    rm -rf src/plugins/telemetry
    node scripts/generate-plugins.mjs          # rewrites src/plugins.generated.ts without it
    node scripts/remove-plugin-deps.mjs        # drops the packages of plug-ins that are gone
    npm ci                                     # as the Dockerfile does; prom-client is no longer installed
    npm run build

`scripts/remove-plugin-deps.mjs` edits `package.json` and `package-lock.json`
without the network; from the lockfile it also drops the packages only
`prom-client` needed (`tdigest`, `bintrees`, `@opentelemetry/api`). With
`--remove telemetry` it does the same while the directory is still there, which
is what a Dockerfile needs: there the two `package*.json` files are copied
before `npm ci` and `src` after it. That stage does not copy `scripts/` or
`plugins.dependencies.json` today; a build that does this copies them as
well.

A build that selects plug-ins from a `plugins.profiles.json` (see the header of
`scripts/generate-plugins.mjs`) declares the same thing as data, under
`plugins."plugins/telemetry".dependencies`, and
`node scripts/generate-plugins.mjs --describe` then lists the directories and
dependencies to remove for the chosen selection.
