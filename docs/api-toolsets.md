# API actions

API actions (called API toolsets in the code) let an organization give its agents tools for its own REST APIs. An administrator
uploads the API's OpenAPI spec, picks the operations agents may call and says how PipesHub
authenticates. Each operation becomes one tool. No code runs inside PipesHub: calls go out over
plain HTTP to the API the admin configured.

API actions are separate from MCP servers. Use an MCP server when the tool already speaks the
Model Context Protocol; use an API action when it is an ordinary REST API.

## Turning it on

API actions sit behind the `ENABLE_API_TOOLSETS` Labs flag, off by default. An administrator
turns it on in **Workspace → Labs**. With the flag off, the routes answer 403, the API actions
section is hidden and agents load none of these tools.

The PipesHub backend must be able to reach the API. Base URLs on the deployment's own network
(RFC1918, ULA, CGNAT) are allowed unless the operator sets
`API_TOOLSETS_ALLOW_PRIVATE_NETWORK_URLS=false`.

## Adding an API

In **Workspace → Actions**, under **API actions**, an administrator chooses **Add API action** and:

1. Drops, picks or pastes the spec (OpenAPI 3.0 or 3.1, JSON or YAML, up to 5 MB). PipesHub shows the
   operations it can offer and warns about the ones it can't.
2. Selects the operations agents may call (up to 200). Reads are preselected. With **No
   authentication**, operations the spec marks as needing credentials are flagged, and the
   connection test only calls ones it doesn't.
3. Sets the name, the base URL (prefilled from the spec's `servers`; a `localhost` address is
   flagged, since it means the PipesHub server itself), the security scheme and,
   for OAuth, the scopes and the OAuth app.
4. Chooses whether every member connects their own credential or the admin stores one shared
   credential. OAuth client credentials are always shared: such a token is the client's, not a
   person's.

The spec is compiled again on the server and only the selected operations are stored. The spec
file itself is not kept; upload it again to add operations.

### Credentials

| Security scheme in the spec | What is stored | Who connects |
| --- | --- | --- |
| `apiKey` (header, query or cookie) | the key | each member, or the admin for everyone |
| `http` bearer | the token | each member, or the admin for everyone |
| `http` basic | username and password | each member, or the admin for everyone |
| `oauth2` client credentials | client ID and secret | the admin, shared |
| `oauth2` authorization code | tokens from signing in | each member signs in, or the admin once for everyone |

Credentials are encrypted at rest by the configuration store and never returned by the API.
**Test connection** calls a read operation with the caller's credential and shows the start of
the answer.

### Agents

In Agent Builder, API actions are listed under **Tools** next to the other actions. Drag one onto
the canvas like any action, then keep or drop its operations on the node. A service-account agent
acts as no particular person, so it can only use API actions with a shared credential (or no
auth).

In chat, the **Actions** filter lists the agent's API actions, so a turn can be limited to some
of them. The global agent offers every operation of every API action the member has connected.

When a member chats with the agent, each toolset loads with that member's credential (or the
shared one). A toolset the member hasn't connected is left out, and the agent tells them to
connect it under **Workspace → Your actions**.

Every call goes through the tool approval gate:

| Operation | Starting rule |
| --- | --- |
| `GET`, `HEAD`, `OPTIONS` | runs |
| `POST`, `PUT`, `PATCH` | asks the person first |
| `DELETE`, or an `operationId` with a deleting word (`delete`, `remove`, `purge`, ...) | refused |

## Writing a spec agents use well

The agent only knows what the spec tells it. The reference spec,
[`integration-tests/openapi_fixture/openapi.yaml`](../integration-tests/openapi_fixture/openapi.yaml),
shows each of these:

- **Give every operation an `operationId`.** It becomes the tool name (`list_tickets`). Without
  one, PipesHub derives a name from the method and path.
- **Write `summary` for the tool list and `description` for when to use it.** Say what the
  operation is for, not how it is implemented.
- **Describe parameters and properties, and use `enum`s and `examples`.** The model fills
  arguments from these.
- **Keep shared schemas in `components/schemas` and use `$ref`.** Only references inside the
  file are followed; PipesHub never fetches remote references. Bundle multi-file specs first.
- **Use JSON request bodies.** An operation that needs a form, multipart or file body is skipped.
- **List every way the API accepts credentials in `securitySchemes`.** The admin picks one,
  and every call carries it, also to an operation whose `security` allows anonymous calls (`[]`
  or `{}`), since many APIs answer an anonymous caller with less. Such operations are the ones an
  API action with no authentication can call.

How the model's arguments become the request: path, query, header and cookie parameters are
filled by name (OpenAPI `style`/`explode` rules apply), and the JSON body is the `body` argument.
A path value is percent-encoded as a whole, so it can't change the path or the host.

## Trying it with the reference API

The repository ships Acme Helpdesk, a small API with tickets, comments and announcements that
accepts every supported security scheme at once:

```bash
python integration-tests/openapi_fixture/server.py --port 8098 --public-url http://localhost:8098
curl -H 'X-API-Key: acme-demo-key' http://localhost:8098/tickets
curl http://localhost:8098/announcements          # no credentials needed
curl http://localhost:8098/openapi.yaml
```

| Scheme in its spec | Demo credentials |
| --- | --- |
| `apiKeyAuth` (header `X-API-Key`) | `acme-demo-key` |
| `bearerAuth` | `acme-demo-token` |
| `basicAuth` | `acme` / `acme-demo-password` |
| `oauth2ClientCredentials` | client `acme-client` / `acme-client-secret` |
| `oauth2SignIn` (authorization code, PKCE, refresh) | same client; sign in on Acme's page as `acme` / `acme-demo-password` |
| none (`GET /announcements` only) | nothing |

Override any of them with `--api-key`, `--bearer-token`, and so on. `--token-ttl 60` makes OAuth
access tokens expire quickly, to watch them refresh. `--public-url` is the address a browser uses
for the sign-in page; PipesHub itself calls the API by whatever base URL the admin set.

PipesHub refuses loopback addresses, so run the server where the backend reaches it by name or
private address (in the integration compose stack it is the `openapi-fixture` service at
`http://openapi-fixture:8080`).

## Security

- Only administrators add, change and delete toolsets, see their settings, and manage shared
  credentials. Routes that refuse members do so before looking anything up.
- The base URL is the admin's, never the model's. Requests can't leave its origin, redirects are
  not followed, and loopback, link-local and cloud metadata addresses are always refused (the
  same guard as MCP servers, `app/agents/mcp/url_guard.py`).
- YAML aliases are refused, so a small spec can't expand into a huge one.
- An answer larger than 256 KB is cut before the model sees it.
- An OAuth sign-in can be finished only by the person who started it, once, within 10 minutes.
- A toolset an agent uses can't be deleted; remove it from the agents first. If PipesHub can't
  tell which agents use it, nothing is deleted.

## Known limitations

- No admin screen yet to change the approval rule of one operation.
- An OAuth sign-in URL in the spec is used as written (a relative one resolves against the base
  URL), so it must be an address the member's browser can open.
- Swagger 2.0, form and multipart bodies, file uploads, OpenID Connect and mutual TLS are not
  supported.

## Design

```text
Admin UI ── Node gateway (/api/v1/api-toolsets, proxy) ── connectors service
                                                            api/routes/api_toolsets.py
                                                            agents/openapi/service.py ── spec_compiler.py
                                                            agents/openapi/store.py ── config store
Agent Builder ── agent create/update (agent.py) ── apiToolsetTools on the agent
Chat ── factory.py ── OpenAPIToolProvider ── OpenAPIToolAdapter ── executor.py ── internal API
                                                                   (request_builder, auth)
```

| Module | Job |
| --- | --- |
| `agents/openapi/spec_compiler.py` | Spec to one `CompiledOperation` per operation; pure |
| `agents/openapi/request_builder.py` | Arguments to an `httpx.Request` |
| `agents/openapi/auth.py` | One `AuthApplier` per scheme; OAuth tokens refreshed before expiry and after a 401 |
| `agents/openapi/executor.py` | Sends through the SSRF guard, caps the answer |
| `agents/openapi/store.py` | Config-store records under `/services/api-toolsets/` |
| `agents/openapi/service.py` | Instances, credentials, OAuth sign-in, building the executor |
| `agents/openapi/attachments.py` | Checks an agent's `apiToolsetTools` |
| `agents/agent_loop/openapi_tool_loader.py` | Loads attached operations for one chat |
| `agents/agent_loop/openapi_tool_adapter.py` | One operation as an agent `Tool` |

An agent stores its operations as `apiToolsetTools: ["<toolsetId>:<operation>", ...]`, a list of
strings both graph backends keep as a plain property.
