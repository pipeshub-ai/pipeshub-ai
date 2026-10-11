# MCP hardening: working notes

Running log of the MCP hardening work that follows the readiness review of 2026-10-04
(private page: https://claude.ai/artifact/8q8MsFGEAJ8vCv3iPx55ec). Each entry says what changed,
what was decided and why, and how it is tested. Finding IDs (SEC-1, RUN-1, …) are the review's.

Branch: `mcp-module-rework`. Every change is one local commit, made through the loop:
plan → review → replan → implement → review → fix → commit.

**History:** on 2026-10-09 the branch's 90 commits were squashed into one, then rebased onto
`main` (a883af6d0), which by then had #3887 (custom STDIO opt-in) and #3949 (toolset save). The
commit hashes in this log refer to the un-squashed history, kept on
`mcp-module-rework-backup-2026-10-09`.

## PR description

Ready to paste into the pull request.

---

### MCP servers: hardening, the `mcp` 2.x client, a tool cache, per-tool approvals and better sign-in

**What and why.** This reworks how PipesHub connects to MCP servers. Customers reported three
problems:
- unreadable tool arguments and results;
- all-or-nothing tool attachment;
- slow chats, because every turn rediscovered every server.

A readiness review (security, reliability, OAuth, UX, scale) then found 67 issues, 13 of them
high. This PR fixes them and adds four features: users' own servers, per-agent tool selection, a
tool cache and per-tool approvals. It moves the client to the official `mcp` 2.3.0 SDK.

`docs/mcp-hardening-notes.md` records every change with its reasoning and tests. Finding IDs
below (SEC-, RUN-, AUTH-, DATA-, UX-, SCALE-) are the review's.

**How it fits with #3887.** This is rebased on `main` after #3887 (the custom STDIO opt-in).
Where both changed the same thing, #3887's behaviour and names are kept:
- `MCP_ALLOW_CUSTOM_STDIO`, `customStdioAllowed` in `GET /catalog` and `disabledReason` on
  instances, with its Disabled badge and callouts;
- its env-name rules, its 403 for a custom STDIO server while the opt-in is off, and its refusal
  to override a catalog server's command.

This PR adds the launcher allow-list and the npm/uvx parsing on top, and keeps STDIO off personal
servers. Also from `main`:
- the access-log redaction (`redact_sensitive_query_params`) now also covers OAuth `state`;
- a failed **Discover tools** answers everyone with #3819's fixed text (the gateway shows its own
  sentence for every 5xx anyway). Administrators read each server's error, a STDIO server's
  stderr included, in the Workspace listing.

#### What changed

**Security**
- **Org scoping:** every MCP instance lookup is scoped to the caller's org.
- **Visibility:**
  - members never see how org servers are reached (URLs, headers);
  - admins see only a summary of users' personal servers, never their credentials.
- **SSRF guard on every server-side MCP request:** connect, OAuth discovery and registration,
  token calls, redirects.
  - Private networks are allowed for admins' servers only (`MCP_ALLOW_PRIVATE_NETWORK_URLS`).
  - URLs a server names in its OAuth metadata are public-only.
  - IPv6-mapped IPv4 is judged as IPv4.
  - An egress proxy may resolve names the pod can't.
- **Local (STDIO) servers**, on top of `main`'s opt-in (`MCP_ALLOW_CUSTOM_STDIO`, #3887):
  - personal servers can't be STDIO;
  - launchers are allow-listed (`MCP_STDIO_ALLOWED_COMMANDS`);
  - the guard follows npm's and uvx's own option parsing;
  - catalog packages run an exact version.
- **Secrets kept out of:** errors shown to the model and UI, logs (OAuth `code`/`state` are
  redacted in the Node, proxy and uvicorn access logs), and store keys (sign-in state is stored
  by hash).
- **Sign-in:**
  - only http(s) sign-in addresses are opened;
  - a sign-in state can be used once, and only by the person who started it.
- **The shared admin credential** has its own slot. It never becomes someone's personal token,
  and is accepted only for API-token and header auth.
- **Inputs:** tool schema `$ref` inlining is bounded. Agent attachments and credentials take
  nothing on trust: limits, length caps, reserved header names refused.

**Sign-in (OAuth 2.1, MCP authorization spec 2025-11-25)**
- **Discovery:** RFC 9728 protected-resource metadata (including the one a 401's
  `WWW-Authenticate` names, checked against the server per §3.3), RFC 8414 and OIDC, in the
  spec's probe order. Entra ID, Okta and Keycloak issuers, whose metadata sits under the issuer
  path, are found; their layouts are covered by unit tests.
- **What is requested:**
  - the scopes the server asks for (challenge scope, then its own metadata), never the
    authorization server's whole list;
  - RFC 8707 `resource` when the server publishes its metadata.
- **One redirect URI:** `<public frontend address>/mcp-servers/oauth/callback/`. Clients:
  - a static app an admin sets up wins for new sign-ins;
  - a dynamically registered client is shared per instance and replaced when it no longer fits;
  - refresh always uses the client that issued the tokens.
- **Token handling:**
  - refresh runs once across processes;
  - errors are read from the response body (some providers answer 200);
  - no-expiry tokens are left alone;
  - a rejected refresh isn't treated as a 401;
  - a refresh never restores credentials the user removed.
- **Client authentication:** `client_secret_basic` when that is all a provider takes.
- **PKCE:** a provider that lists PKCE methods without S256 is refused.
- **More permission (step-up):** when a server refuses with 403 `insufficient_scope`, the scopes
  are remembered. The next sign-in asks for them on top of the current grant. In the web chat,
  a **Sign in again** card does it in place, then sends a follow-up so the model retries what
  failed.
- **Client ID Metadata Documents:** where the authorization server supports them, PipesHub's
  client id is a document the Node app serves (`/mcp-servers/oauth/client-metadata.json`).
  - Used only after a self-check, and never in place of a client that works.
  - A refusal falls back to dynamic registration for a week.

**Tool calls and sessions**
- **One connection per server per turn.** A dead session is replaced mid-turn, and one failed
  request no longer ends it.
- **A call is never sent twice** unless its own request was refused (never sent, an expired
  session, or a 401 before a token refresh). A call that may have run is reported as
  interrupted.
- **Stop and timeouts** interrupt a running call and reach the server: a 2026-07-28 session
  closes the call's request, and an older one sends `notifications/cancelled`.
- **Progress** restarts the call timeout, up to `MCP_TOOL_CALL_MAX_SECONDS` (30 min). It shows on
  the chat's activity row and in the Slack bot.
- **Results:**
  - returned verbatim and capped in size;
  - a result that misses its declared schema is kept;
  - tool schemas are reshaped into what every LLM provider accepts;
  - tool names are valid for providers and unique per request.
- **Timeouts and networking:** per-server timeouts; the HTTP read timeout outlasts the call
  timeout; local servers inherit the host's proxy and CA settings.
- **Server instructions** reach the model.
- **Failures:** each one has one reason code (`auth_expired`, `unauthorized`,
  `needs_permission`, `blocked`, `timeout`, `unreachable`), so the model and the UI say what is
  wrong and what fixes it.

**The `mcp` 2.3.0 client**
- fastmcp is removed; the client uses the official SDK and its `httpx2`.
- **Protocols:** 2026-07-28 servers are spoken to in their own version. Handshake-era servers
  work as before, and an older HTTP server is remembered for an hour so it skips the version
  probe.
- **Wire record:** a per-operation record of HTTP statuses and failures restores what the SDK
  hides: 401/403/404, whether a request reached the server, and each response's
  `WWW-Authenticate` challenge.
- **Identity:** the client introduces itself as `PipesHub <version>`.

**Tool cache**
- Chats take an attached server's tools from a cache kept per sign-in for 24 h
  (`MCP_TOOL_CACHE_TTL_SECONDS`; 0 turns it off), stored once per identical list.
- A server is contacted only when a tool is called. It lists once before that first call, and a
  tool whose schema or hints changed since is refused.
- **2026-07-28 servers:** a list they mark immediately stale (`ttlMs: 0`) is kept 15 min
  (`MCP_TOOL_CACHE_STALE_TTL_SECONDS`). `cacheScope: public` is honoured for admins' servers.
- The Workspace and builder listings read the same cache. A live listing discovers at most 8
  servers at once, within 15 s.

**Per-tool approvals (MCP tools)**
- **Rules:** Pre-approved, Allow on approval or Deny per tool. Where nobody set a rule, a tool
  starts from what it does to data:
  - read-only tools are Pre-approved;
  - tools that change data are Allow on approval;
  - tools that delete data are Denied.
- **How a tool's kind is decided:** a deleting word in the tool's name (delete, remove, drop,
  purge, revoke, …) counts as deleting, whatever the server says. Otherwise the server's
  `readOnlyHint` / `destructiveHint` decide, and a tool without them changes data. A name never
  makes a tool read-only. Every tool listing sends `kind` and `kindSource`.
- **In the web chat** the turn ends with a card: Allow once, Allow for this chat, Deny, and
  Always allow for whoever may set the rule. The approved call runs exactly as asked, once,
  within 15 minutes.
- **Who sets them:**
  - agent editors, on the agent's MCP node;
  - each person, for their own assistant chats, on the Workspace server card;
  - admins, as company rules: Allow on approval or Deny, a floor nobody goes below.

  The rules list groups tools by kind and says where each kind came from.
- **Where nobody can answer** (Slack, API, MCP gateway, non-streaming), Allow on approval counts
  as Deny unless an admin turns on "Allow when no one can approve".
- `MCP_TOOL_APPROVALS=false` turns approvals off.
- A reply that ran an approved action can't be regenerated, since that could run it again.

**Users' own servers and agent tool selection**
- Users can add personal MCP servers (`MCP_MAX_PERSONAL_INSTANCES`, 25). They are visible and
  usable by their owner only, and can't be put on shared or service-account agents.
- Agents choose which tools of a server they get ("All tools" or a list).
- **Saving attachments:** never relies on a graph rollback (Neo4j runs without explicit
  transactions by default). A failed MCP read never detaches servers.
- **Cleanup:** servers and credentials are removed with their user, org or agent.

**Interface**
- **Chat:**
  - tool cards show real arguments;
  - results get a one-line summary ("Found 23 issues") and a small table of what was found,
    with links, or a record's fields. The raw output is behind "View raw". The view is built on
    the backend from the full result, is size-limited, and is the same after a reload;
  - an agent chat stopped on an unconnected server offers Connect;
  - the approval and sign-in cards.
- **Workspace server pages:**
  - the personal page works like the toolsets page: tabs, one grid, and "Setup" cards for
    catalog servers not set up yet;
  - the Edit panel has a status, a More actions menu, and a Tools & approvals tab. That tab loads
    the tool list (cached first), edits the rules inline, and asks before dropping unsaved
    changes;
  - Disconnect asks first;
  - status says what is wrong and what fixes it;
  - the form asks before an edit signs people out;
  - sign-in popups say what happened, and Reconnect keeps the old token until the new one
    works;
  - when the tools were listed.
- **Agent builder:** the MCP node shows the live server (gone, new and dropped tools) and the
  approval rules.
- Strings are in all 9 locales.

**Other**
- **Slack catalog server:** Slack's own hosted MCP server replaces the archived community
  package.
- **Configuration store:**
  - directory listings use the backend's prefix scan;
  - etcd's directory listing returned values instead of keys, now fixed;
  - a key overwritten with an expiry keeps it on etcd.
- **Node API:** saving an agent keeps "All tools"; the MCP proxy is bounded.
- **OpenAPI:** documents every `/api/v1/mcp-servers` route.
- **Tests:** the test suite never sends traces to a real Opik account.
- **Admin-route inventory** (`integration-tests/helper/admin_route_*`): members now have MCP
  servers of their own, so the MCP routes that look a server up first, or answer members
  differently, moved to the conditional table with live cases. Its test world makes a personal
  server, an agent and servers to delete, at a URL that never resolves.

#### Upgrade notes (behaviour operators will notice)
- **Dependencies:** `fastmcp` is removed; `mcp==2.3.0` and `httpx2==2.12.0` are added. Reinstall
  the Python package (`pip install -e .`).
- **Custom STDIO servers** stay off unless `MCP_ALLOW_CUSTOM_STDIO=true`, as on `main` since
  #3887. When on, they may only use the launchers in `MCP_STDIO_ALLOWED_COMMANDS` (`npx,uvx`).
- **OAuth redirect URI** is always `<endpoints.frontend.publicEndpoint>/mcp-servers/oauth/callback/`.
  Static OAuth apps registered with another address must be updated at the provider; the
  server form shows the exact value.
- **Approvals:** MCP tools not marked read-only now ask first, and tools that delete data are
  denied until someone allows them. In Slack-facing, API and scheduled agents, tools that ask
  are blocked until an admin allows them unattended. Review those agents' MCP tools before
  upgrading, or set `MCP_TOOL_APPROVALS=false`.
- **Sign-in and server creation answer 503** when the configuration store or the role service
  can't be read, instead of guessing.
- **No data migration.** Older records (per-owner DCR clients, unhashed sign-in states, tokens
  without a recorded client) are still read.

| New setting | Default | Meaning |
|---|---|---|
| `MCP_STDIO_ALLOWED_COMMANDS` | `npx,uvx` | Launchers a custom STDIO server may use |
| `MCP_ALLOW_PRIVATE_NETWORK_URLS` | `true` | Admins' servers may be on the deployment's network |
| `MCP_MAX_PERSONAL_INSTANCES` | `25` | Personal servers per user |
| `MCP_TOOL_CACHE_TTL_SECONDS` | `86400` | How long a server's tool list is kept; `0` turns the cache off |
| `MCP_TOOL_CACHE_STALE_TTL_SECONDS` | `900` | How long a list marked immediately stale is kept |
| `MCP_TOOL_CALL_MAX_SECONDS` | `1800` | Longest one tool call may run while it reports progress |
| `MCP_TOOL_APPROVALS` | on | `false` turns per-tool approvals off |
| `MCP_OAUTH_CLIENT_METADATA_DOCUMENT` | on | `false` always registers a client instead |

#### How it was tested
- **Unit tests** cover every change in Python, Node and the frontend. The last full regressions
  (2026-10-08, before the rebase) showed only failures that also occur on `main`: 37
  `test_teams_toolset`, 1 SharePoint behaviour test, `test_artifact_pipeline_e2e[local]` on
  Windows, and the toast-container / upload-progress-tracker `tsc` errors. After the rebase
  (2026-10-09, Windows):
  - **Python**, the suites the rebase touched (MCP, agent loop and adapter, API routes, utils):
    11,039 passed. `test_artifact_pipeline_e2e[local]` fails as on `main`. 47 `test_model_egress`
    tests can't set up on Windows: their fixture blocks the socket asyncio's event loop needs
    there.
  - **Frontend** chat, MCP servers and agents suites: 987/987. `tsc` shows only `main`'s two
    errors.
  - **Integration helpers:** the admin-route inventory tests pass, and the live permission
    suites collect their 33 new MCP cases. The live suites need a running stack and were not run
    here.
  - **Node**, the suites the rebase touched (`mcp_servers`, `enterprise_search`, `api-docs`,
    libs, Slack bot, `tokens_manager`): 3,961/3,966. The 5 are log-capture tests: 3 fail on
    `main` too, and 2 pass when run alone. `tsc` shows only `main`'s Mongoose `_id` errors.
- **End-to-end test server:** `pipeshub-mcp-testbed`
  (https://github.com/tushar1245/pipeshub-mcp-testbed, private).
  - **What it is:** a custom MCP server on the `mcp` 2.3.0 SDK, with fault injection, an OAuth
    2.1 authorization server and a TCP proxy that really resets connections.
  - **What it drives:** this branch's own client code, routes and chat stack, over HTTP, SSE and
    stdio.
  - **Coverage:** 169 scenarios in `TEST_PLAN.md`, with results in `RESULTS.md`. The last full
    run (2026-10-09, after the rebase) passed 169/169.
  - **Fixed from its findings:** a tool cache that never cached 2026-07-28 servers, an
    unannounced 100-page tool list cut, the client identifying itself as the SDK, ignored 401
    challenge hints, and a legacy SSE session left in use after a refused POST.
- **Independent reviews:** each round, and each feature's plan, was reviewed independently, and
  the findings were fixed or recorded.
- **Smoke checks with real providers** are still to do (unit tests and the testbed can't cover
  them); the checklists are in the notes:
  - a DCR server;
  - a static OAuth app;
  - a sub-path deployment;
  - a `client_secret_basic`-only provider;
  - a CIMD-capable authorization server on a public https address;
  - a server that sends `insufficient_scope`;
  - a rolling upgrade;
  - etcd and Redis.

#### Known limitations and follow-ups
- **Sign-in:**
  - an authorization server that can't fetch the client metadata document shows its own error
    page, so nothing falls back automatically (`MCP_OAUTH_CLIENT_METADATA_DOCUMENT=false`
    turns the document off);
  - the in-chat sign-in covers missing permission, not expired sign-ins yet.
- **Progress** sent only on a server's GET stream is still cut at the HTTP read timeout.
- **Not wired yet:** elicitation and `subscriptions/listen`. Sampling and roots are refused by
  design.
- **Approvals** cover MCP tools only. The check sits at the tool executor, so toolsets and web
  tools can join later.
- **Data:**
  - org instances still share one cross-org `/instances/` directory (as on `main`; needs a
    migration);
  - the Python KV store has no compare-and-set, so a shared DCR record's read-modify-write has a
    small, self-healing lost-update window.

#### Reviewing this PR
It is large (about 290 files, +36k/−4k lines). The notes follow the work in order:
1. phase 0 to 6 of the review, then round 2: security, reliability, sign-in, UI;
2. round 3: the `mcp` 2.x move, the tool cache, approvals, Slack;
3. round 4: the testbed decisions and the in-chat sign-in;
4. round 5: approval defaults by tool kind, readable tool results, the personal page and the Edit
   panel redesign.

Each entry names the files and the tests. The un-squashed history (90 commits, one per change)
is kept on a local branch and can be pushed if reviewing commit by commit helps.

🤖 Generated with [Claude Code](https://claude.com/claude-code)

---

## Decisions from Tushar (2026-10-05)

| # | Question | Decision | What it means for the work |
|---|---|---|---|
| 1 | When to move to the new protocol | Right after phase 0 | Phase 1 is an SDK spike (`mcp` 2.x / fastmcp 4) before the session-level reliability fixes. |
| 2 | Approval for risky tool calls | Add approvals | Per-tool permissions (Always allow / Needs approval / Blocked); interactive chat asks before tools not marked read-only; unattended agents use only admin-approved tools. |
| 3 | Who may add servers | Keep letting users add their own | Personal servers stay. |
| 4 | Tool cache keying | Apply caching | Cache per sign-in context (each user's or agent's own account), as in the cache plan. |
| 5 | Protocol features this round | Go with the recommendation | Progress, server instructions and elicitation now; resources and prompts later. |

## Start here: what this session did

26 local commits on `mcp-module-rework` (25 changes plus this overview), none pushed. Each has its own entry in the Log below.

| Area | Findings fixed | Commits |
|---|---|---|
| Security | SEC-1, 2, 3, 4, 5, 6, 10, 12, 13 | eecba5db4, 59fdfeacb, cbe40725a, 6ac47935a, bd0d70bfd, 41b7d050d, 281ea25c5 |
| Data and lifecycle | DATA-1, DATA-2 (DATA-4 is covered by DATA-1) | 9fee1c1fa, 109a3320f |
| Tool calls | RUN-1, 2, 3, 7, 8, 9, 10, 11, 12 | e6c4c1fec, ccca4fcda, 13fbc2de5, 7ff54867b, 32c9a7388, 297327233 |
| Sign-in | AUTH-1, 2, 3, 4, 5, 6, 8, 9, 10 | 7c63728c3, 2eef12300, 90e7b0385, 5f3e26292 |
| Interface | UX-1, UX-2, UX-3, UX-4, UX-6, UX-7 (part), UX-10, UX-16 (part) | 2eef12300, 9610dc84f, 092f2dfe9, a00fd8d4d, 13fbc2de5 |
| MCP features | Server instructions | 10eb68991 |
| SDK | Spike: go for `mcp` 2.x (plan only) | a84df0bcd |

**Needs a decision from you:**
1. **The mcp 2.x migration environment.** The shared venv also serves the main checkout,
   which is on another branch. Choose an overlay (`pip --target` plus `PYTHONPATH`) or a
   separate venv for this worktree. The plan is in "Phase 1".
2. **Per-tool approvals design.** It changes the agent loop's control flow; outline under
   "Not done this session". Review it before it's built.
3. **The Slack catalog server.** Its npm package is archived and marked unsupported (SEC-12).
   Choose a replacement, such as Slack's hosted MCP server.

**Still open from the review:**
- Phase 5: the tool cache, and SCALE-1/2.
- Phase 6: approvals; progress and elicitation, after the migration.
- AUTH-7 and AUTH-11.
- RUN-4/5/6 (session recovery and cancellation), which belong to the migration.
- RUN-13 to 15 and SEC-7/8/9/11.
- UX-5, UX-8, UX-9, UX-11 to 17, and the second half of UX-7.
- DATA-3, 5 and 6.

**Test failures that predate this work** (all seen on the base commit too, as of 2026-09-30):
- `test_artifact_pipeline_e2e[local]`;
- `test_teams_toolset.py` (37);
- `test_sharepoint_tools_behaviour.py::TestFailures::test_a_refusal_without_a_reason_says_to_check_the_arguments`
  (the MS Graph SDK's error text in this venv);
- frontend `tsc` errors in toast-container and upload-progress-tracker.

## Environment

- Tests run with the shared venv in the main checkout
  (`C:\Users\Tushar\workspace\pipeshub-ai\backend\python\venv`), as agreed. Package versions
  before any change are saved in the session scratchpad (`hardening/venv_freeze_before.txt`).

## Log

(Entries are added as each task lands, newest last.)

### Phase 0 · SEC-4 — SDK pinned to `mcp` 1.30.0 (commit eecba5db4)
- **What:** `pyproject.toml` pins `mcp==1.30.0`; `tests/unit/agents/mcp/test_sdk_versions.py`
  fails if an install resolves an older SDK.
- **Why:** 1.29.1 fetched output-schema `$ref`s from server-chosen URLs while validating tool
  results (GHSA-rwrf-2pqf-9j8j), outside our URL guard. 1.30.0 resolves `$ref`s only inside the
  schema. It also fixes GHSA-5h93 and GHSA-qx49, which our own transport and OAuth client
  probably already blocked.
- **Decision: fastmcp stays at 3.2.0.** fastmcp 3.4.x requires starlette ≥ 1.0.1, and our
  FastAPI 0.115.6 requires starlette < 0.42. Upgrading FastAPI platform-wide is out of scope for a
  security patch. The same constraint shapes the phase 1 spike (see there).
- **Tests:** the full MCP / adapter / route suites pass on 1.30.0.

### Phase 0 · SEC-1 — a sign-in URL must be a web address (+ R28)
- **What:**
  - `models.is_web_url()`: absolute http(s) URL with a host and no user-info.
  - OAuth discovery ignores an authorization-server document whose authorization, token or
    registration endpoint isn't one (logged), so the next candidate can win.
  - `MCPServerInstanceConfig` rejects such `authorization_url` / `token_url` at save (422).
  - The authorize route refuses a non-web address with 502 *before* client registration, and
    stores the OAuth state only after the URL is built.
  - `build_authorization_url` raises `InvalidOAuthEndpointError` as a backstop. It now merges
    with a query already on the endpoint (one `?`; RFC 6749 §3.1), drops protocol parameters
    found in that query so none appears twice, and drops a fragment.
  - The frontend popup hook refuses anything but `http:`/`https:` before `window.open`.
- **Why:** a malicious or compromised server could return `javascript:` as its authorization
  endpoint, which then ran with the PipesHub session of whoever clicked Connect.
- **Decision:** `http` stays allowed. Admin servers may use an internal IdP over plain HTTP; the
  attack is the scheme, not TLS. Validation is layered (discovery, save, route, builder,
  browser), so one missed path can't reopen it.
- **Tests:** `test_models.py` (helper and model), `test_dcr.py` (discovery filter, builder query
  merge, fragment, refusal), `test_mcp_servers.py` (502 before registration, no state stored,
  legacy stored value), `frontend/.../oauth-popup-safety.test.ts` (helper and hook).

### Phase 0 · SEC-2 — the shared admin credential gets its own slot
- **What:**
  - `credential_owner_id` returns `_shared` for a shared-credential instance (`useAdminAuth` with
    api_token/headers) instead of the creator's id. All reads, writes and deletes go through it.
  - New and edited records carry `sharedCredentialSlot: true`.
  - `adopt_legacy_shared_credential` moves (copy + delete) a pre-change shared credential from
    the creator's path into `_shared`. It runs on first read, on an edit that keeps sharing on,
    and before Disconnect/Reconnect, and never for marked records.
  - Toggling `useAdminAuth` either way empties `_shared`. Turning it off reports
    `credentialsReset: true`.
- **Why:** the shared credential lived at the creator's personal path, so turning sharing on
  after the creator connected personally made every user and agent act as the creator.
- **Decisions:**
  - Move rather than copy legacy credentials, so an admin's Disconnect can't be undone by the
    fallback finding the old copy.
  - Unmarked (pre-change) shared records keep today's behaviour until first touched. That avoids
    forcing every admin to re-enter shared credentials after the upgrade. The vulnerable path
    (turning sharing on) always starts empty, because the edit sets the marker.
  - Users' personal credentials are never deleted by the toggle; they're just unused while
    sharing is on.
- **Tests:** `test_mcp_servers_correctness.py::TestSharedCredentialSlot` (toggle on with a personal
  token present, legacy toggle on, everyone reads the shared one, toggle off resets and reports,
  back on starts empty, legacy move on read, legacy rename, marked records never fall back,
  auto-authenticate). Older tests now assert the `_shared` slot.

### Phase 0 · SEC-3 — bounded `$ref` inlining
- **What:**
  - `tool_adapter._resolve_json_refs` carries a node budget (`MAX_INLINED_SCHEMA_NODES = 5_000`).
    Every dict and list costs one node.
  - Once the budget is spent, a `$ref` is left as a placeholder ("not expanded: the schema is
    too large"), like the existing recursion placeholder. Ordinary nodes are still walked, so the
    work stays linear in the input. A warning is logged when a schema is cut.
  - `MCPToolAdapter` builds `parameters` and `raw_input_schema` once, and returns copies.
- **Why:** definitions that share sub-definitions expand exponentially; the probe turned 1.9 KB
  into 17 MB in 4.5 s on the event loop, recomputed on every schema read. Any user's personal
  server could stall the query service.
- **Decisions:**
  - 5,000 nodes (~100 KB of JSON) is far above real tools and still sane for a model's context.
  - No thread offload: with the budget the walk takes milliseconds.
  - The budget lives in the shared `tool_adapter.py`, so every tool type benefits.
- **Tests:** `test_tool_adapter_nested_schemas.py`: a 30-level doubling schema finishes in < 1 s
  with a placeholder; a 6-level one is inlined in full; recursion keeps its own placeholder; the
  adapter resolves once across many reads and callers can't mutate the cache. The agent and
  adapter suites pass, apart from the known Windows sandbox failures.

### Phase 0 · SEC-5 + SEC-6 — members don't see how org servers are reached; agent tools need edit access
- **What:**
  - `/my-mcp-servers` and `/agents/{key}` drop `url`, `command`, `args`, `authorizationUrl`,
    `tokenUrl` and `scopes` from org servers unless the caller is an administrator. The owner of
    a personal server still sees all of it.
  - `/agents/{key}` lists tools only for callers who can edit the agent; viewers get the
    connection status alone.
  - The admin team page now edits the full records from the admin-only `/instances` listing
    and takes connection status from `/my-mcp-servers` (`orgInstancesWithStatus`).
  - Frontend types mark the admin-only fields optional.
- **Why:** a key embedded in an org server's URL or arguments leaked to every member. Any
  viewer of a service-account agent could make it connect with its own credentials (refreshing
  tokens, starting local processes).
- **Decisions:**
  - Members keep `authMode`, `requiredEnv`/`optionalEnv` names and `headerName`, which they need
    to connect.
  - The admin page no longer depends on `/my-mcp-servers` for records. If the admin check failed
    there, an admin would get stripped records, and saving the form could blank a custom
    server's OAuth URLs.
  - A viewer gets status, not a 403, so the builder still opens read-only for them.
- **Tests:** `test_mcp_servers_personal.py::TestWhatMembersSeeOfOrgServers` and
  `::TestAgentToolsNeedEditAccess`; `frontend/.../team-instances.test.ts`.

### Phase 0 · AUTH-1 + AUTH-9 — a refresh never restores removed credentials or loses a rotated token
- **What:**
  - After the provider call, `refresh_credential_record` re-reads the credential.
    - If it's gone (Disconnect, Reconnect or a credentials reset ran meanwhile), nothing is
      written and `MCPTokenRefreshError` is raised; callers already turn that into "reconnect".
    - If its refresh token changed (a new sign-in landed meanwhile), the newer grant is kept
      and returned.
    - Otherwise the new tokens are written to the current record.
  - The write is retried once. If it still fails, an error is logged and the new tokens are
    still returned for this request.
- **Why:** those deletions don't take the refresh lock, so a refresh in flight wrote the
  deleted credentials back. And with rotating refresh tokens, a silently lost write signed the
  user out at the next refresh.
- **Decision:** re-read-before-write rather than having every delete take the cross-process
  refresh lock. Deletes stay instant, and the remaining race window shrinks from the provider
  round trip (seconds) to the gap between the re-read and the write (milliseconds). Closing it
  fully needs a compare-and-set in the KV store, which `ConfigurationService` doesn't offer.
- **Tests:** `test_token_refresh.py::TestTheRecordChangedDuringTheRefresh` (removed meanwhile,
  newer sign-in meanwhile, unchanged, one retry, persistent failure).

### Phase 0 · DATA-1 (+ part of DATA-4) — MCP data goes away with its owner
- **What:** new `app/agents/mcp/lifecycle.py`, one place that removes MCP data:
  - `purge_instance_credentials` (moved from the routes), `delete_instance_data`,
    `remove_owner_credentials` (a user's or agent's credentials on one org's servers, nested
    keys included, with their refresh tasks cancelled), `remove_user_mcp_data` and
    `remove_org_mcp_data`.
  - Hooks: `userDeleted` and `orgDeleted` entity events, and service-account agent deletion,
    call them.
  - The background refresh deletes, instead of renewing, a credential whose server no longer
    exists.
  - `DELETE /instances/{id}` now fails with 500 if the record itself can't be deleted, and
    removes nothing else in that case.
- **Why:** deleting a user, org or service-account agent left their servers and credentials
  behind, and the refresh service renewed those tokens forever. A failed record delete was
  reported as success.
- **Decisions:**
  - Cleanup is best-effort and never fails the event or the agent delete, because the deletion
    itself is already committed and replaying it wouldn't help. Each function returns the keys
    it removed.
  - The user/agent sweep lists the credentials directory once and filters to the org's
    instances, so another org's data is never touched even if an id repeated.
  - Agent credentials are deleted, not just un-refreshed as toolsets do: agents have no
    restore path.
  - Orphan detection reads with `raise_on_error=True` and acts only on a definite "gone". A
    store outage or a record too old to name its org deletes nothing.
- **Not covered:** credentials of users deleted *before* this change, on servers that still
  exist. Python can't tell cheaply that a user is gone; they stop being a problem once the user
  reconnects or the server is deleted.
- **Tests:** `tests/unit/agents/mcp/test_lifecycle.py` (user, agent and org removal, org
  isolation, best-effort, orphan detection incl. store errors, entity events, refresh pass),
  `test_agent.py::TestDeleteAgent` (service account vs not, failing cleanup) and
  `test_mcp_servers_correctness.py` (record delete failure → 500).

### Phase 0 · RUN-1 (+ RUN-3 attribution, RUN-12) — a tool never runs twice
- **What:**
  - Every `tools/call` carries `_meta["com.pipeshub/call-id"]`, a random id.
  - `errors.http_status_error` finds the HTTP error in an exception chain, and
    `errors.http_error_call_id` reads which call's request it answered.
  - `MCPSessionManager.call` handles each failure case:
    - **This call's own 401 (OAuth):** refresh the token and retry once, as before.
    - **This call's own other error:** raised as is.
    - **An HTTP error that answered another request** (another call's 401, the SDK's own
      listing): `MCPCallInterruptedError` — "…may or may not have completed; check its effect
      before running it again". Never retried.
    - **A 401 while opening the connection:** the call was never sent, so refresh, reopen and
      run it once.
  - The adapter reports an interrupted call as such, not as rejected credentials.
- **Why:** when one call gets a 401, the SDK raises it in every call waiting on the session.
  Each was refreshed and retried, including calls the server had already started, so a tool
  that changes data ran twice.
- **Decision:** a random id in `_meta` rather than matching on tool name and arguments, which
  can't tell two identical parallel calls apart. The spec reserves `_meta` for protocol metadata,
  and servers ignore unknown keys. The id reveals nothing about the user.
- **Verified live:** the audit's probe (slow tool plus a parallel 401) now records one
  server-side execution instead of two.
- **Tests:** `test_mcp_session.py::TestOnlyACallsOwn401IsRetried` (another call's 401, two calls
  at once with only the refused one retried, another request's 503, own 503, fresh ids) and
  `::TestARefusedConnectionIsRefreshed`; `test_client.py` (id in `meta`). Existing refresh tests
  now use a 401 that answers the call's own request.

### Phase 0 · RUN-2 — a result that misses its declared schema is kept
- **What:** after `open()`, the session's `_validate_tool_result` is wrapped. A mismatch, or a
  failure of the listing the SDK makes to validate, is logged as a warning and the result kept.
- **Why:** the SDK raised `RuntimeError` after the tool had run and threw the result away, so the
  model usually ran the tool again.
- **Decision:** this patches a private SDK hook, because fastmcp exposes no switch. A test runs
  the wrapper against the real `ClientSession` of the installed SDK and checks the SDK still
  rejects a mismatch by default, so an upgrade that changes the hook fails loudly. If the hook
  is missing at runtime, validation just stays strict. Revisit in the phase 1 SDK migration.
- **Tests:** `test_client.py::TestResultsThatMissTheirSchemaAreKept`.

### Phase 0 · DATA-2 — a failed agent save keeps the agent's MCP servers
- **What:**
  - Updating an agent's MCP servers removes the old attachments and creates the new ones in
    one transaction. The removal moved into `_delete_agent_mcp_attachments`.
  - A server that fails to attach now raises inside that transaction, so it rolls back and the
    save returns 500, instead of committing with the server silently missing.
- **Why:** the old removal committed first and the creation ran in a second transaction, so
  any failure left the agent with no MCP servers at all. The builder sends `mcpServers` on
  every save, so every save took that risk.
- **Decision:** agent *create* keeps its "partial_success with warnings" response for a server
  that fails to attach. That response already tells the user the truth, and a new agent loses
  nothing it had.
- **Follow-up (not MCP):** the toolset update in the same route has the same two-step pattern.
- **Tests:** `test_agent_mcp_tool_selection.py::TestSavingIsAllOrNothing` (edge or node failure
  keeps the old server; one transaction per swap). `test_agent_route_access.py` now expects one
  commit.

## Phase 0 result
All eleven items landed (SEC-1–SEC-6, DATA-1, DATA-2, AUTH-1 with AUTH-9, RUN-1 with RUN-2;
RUN-3 attribution and RUN-12 came along with RUN-1). Regression after the phase:
- **Backend:** the MCP, routes, adapter, agent-loop and token-refresh suites pass (exit 0).
- **Frontend:** the MCP, agent-builder, chat and hooks suites pass (52 files, 737 tests).

## Phase 2 (SDK-independent items, done before the migration)

### RUN-7 — tool results have a size cap
- **What:** `mcp_result.MAX_RESULT_CHARS = 200_000`. Text, error and oversized structured
  results are cut to that, keeping the head (80%) and the tail, with a note saying how many
  characters weren't shown. Small structured results stay objects.
- **Why:** a 20 MB result was held, logged and stored in full, peaking at 115 MB.
- **Decision:** the existing `shape_budget_reduction` middleware already caps what the *model*
  sees at 64k characters per tool message, so this cap is for memory and storage, set well above
  it. The transport still reads the full response; bounding that needs the SDK's
  `max_sse_event_size` (mcp 2.3) and belongs to the migration.
- **Tests:** `test_mcp_result.py::TestOversizedResults`.

### RUN-11 — the HTTP read timeout no longer cuts a long call short
- **What:** `client.http_read_timeout(config)` = max(300 s SDK default, call timeout + 30 s).
  `build_transport` passes it to `guarded_http_client(read_timeout=…)`, which uses it when the
  transport passes no timeout of its own.
- **Why:** the call timeout can be 600 s, but the 300 s read timeout ended the call first and
  took the session with it.
- **Decision:** a timeout the transport passes in explicitly still wins. The 30 s margin lets
  our own call timeout fire first, with its clear message, rather than an httpx read error.
- **Tests:** `test_client.py::TestHttpReadTimeout` (default, 280 s, 600 s) and the build test;
  `test_url_guard.py` (the read timeout replaces the default; an explicit timeout is kept).

### RUN-9 — local servers work behind a proxy, a private CA or a package mirror
- **What:** `client.inherited_network_env()` copies a fixed list of host variables into every
  local server's environment, under the server's own values:
  - `HTTP(S)_PROXY`, `NO_PROXY`, `ALL_PROXY` (both cases on POSIX);
  - `SSL_CERT_FILE`/`DIR`, `REQUESTS_CA_BUNDLE`, `CURL_CA_BUNDLE`, `NODE_EXTRA_CA_CERTS`;
  - the npm registry and CA file, uv and pip index URLs, `UV_NATIVE_TLS` and `PIP_CERT`.
- **Why:** the SDK starts a local server with only PATH, HOME and a few like them, so npx and
  uvx couldn't download or connect in deployments that need a proxy or private CA.
- **Decisions:**
  - A fixed list, not the whole environment: the service's environment holds database
    passwords and API keys that a server has no business seeing.
  - The server's own values win. Credential names can't use the `NPM_CONFIG_`/`UV_`/`PIP_`
    prefixes (the STDIO policy blocks them), so only a variable an admin named on purpose, such
    as `HTTPS_PROXY`, can override the host.
  - On Windows the lowercase variants are skipped, because names there are case-insensitive.
  - Proxy URLs can contain credentials. The server runs on the same host the operator
    configured them for, as with any other locally run tool.
- **Tests:** `test_client.py` (proxy, CA and registry passed; an unrelated secret isn't; the
  server's value wins). The build tests clear these variables, so a developer's own proxy
  settings can't change their result.

### RUN-8 — an MCP tool schema always reaches the provider in a shape it accepts
- **What** (`tool_adapter.py`, applied in `resolve_json_schema_refs`, the MCP entry point):
  - **Root is an object.** A schema with properties but no `type` gets `type: object`.
  - **Root `anyOf`/`oneOf`/`allOf` of objects become one object.** It has every arm's
    properties. Required fields are what every alternative requires, or for `allOf` what any
    part requires. `null` arms are ignored (a nullable root). Combinators that only carry
    constraints are dropped.
  - **A root that isn't an object at all** is offered with no parameters, and a warning is
    logged.
  - **`type: array` without `items`,** at any depth, gets `items: {"type": "string"}`.
    `default`/`enum`/`const`/`examples` values are treated as data and left alone.
  - **`$ref` is a JSON pointer** into the document (`#/properties/x`, `#/definitions/a~1b`,
    `#`). It falls back to the old last-segment lookup in `$defs`. A remote or broken reference
    becomes a labelled object placeholder instead of `{}`. The recursion guard is now keyed by
    the full reference.
- **Why:** every tool goes up in one request, so a schema one provider rejects fails every tool
  in the turn. Anthropic rejects combinators at the root, OpenAI wants an object root and array
  `items`, and Gemini fails on a typeless `{}`.
- **Decisions:**
  - The reshaping applies only to MCP schemas. Native tools come from Pydantic models, which
    already produce these shapes, so they are unchanged. The pointer fix applies to both.
  - A string for missing `items`, because every provider accepts it, while `{}` is fatal on
    Gemini. A server that left `items` out said nothing about them, and lists of IDs and tags
    are the common case.
  - Merging a root union loses its "one of these" constraint. The server still validates its
    own input, and the alternative is the tool failing to load at all.
- **Tests:** `test_tool_adapter_nested_schemas.py`:
  - `TestProviderSafeShape` (9): untyped object, empty, root anyOf merge with the
    intersection of required, allOf union, nullable root, constraint-only arms, non-object
    root, array items at depth, data left alone;
  - `TestReferencesArePointers` (4): document pointer, escaped `definitions`, unresolvable
    placeholder, whole-schema recursion;
  - one existing expectation updated: an array arm now carries `items`.

### SEC-10 — the local-command guard covers npm's option parsing, uvx's interpreter and cmd.exe
- **What** (`stdio_policy.py`):
  - **npx:** options before the package are now allow-listed: `-y -q -s -p`, `--yes`, `--no`,
    quiet/verbose, offline/prefer flags, `--package`, `--loglevel`, `--registry`, `--cache`, a
    few install flags. Combined short flags are expanded:
    - `-yc` is refused as an inline command;
    - `-yp node` is refused as an interpreter;
    - any other letter, or a value attached to a boolean, is refused.
  - **uvx:** `-p`/`--python` must be a version (3.12, cpython@3.11), not a path.
  - **Windows:** when the launcher resolves to a `.cmd`/`.bat` (npx is `npx.cmd`), arguments
    may not contain `" % ^ & | < > !` or a line break.
  - The checks run at save time and again at launch for every non-catalog record, so servers
    saved before this change are covered.
- **Why:**
  - `npx -yc "<shell>"` passed the inline-command check, and npm ran it;
  - `--node-options`, `--script-shell`, `--userconfig` and `--git` let a server load code;
  - npm expands abbreviations (`--node-opt`), which no deny-list keeps up with;
  - `uvx --python /tmp/x` runs any program;
  - cmd.exe expands `%VAR%` and treats `&` and `|` as commands even inside quoted arguments
    (the "BatBadBut" class).
- **Decisions:**
  - An allow-list, because npm's parser accepts abbreviations, clusters and aliases. Arguments
    after the package aren't checked: npm passes them to the server (npm 7+ requires its own
    options before the first positional).
  - `--registry` stays allowed. Running a package from a private registry is no more power than
    running one from the public registry, which the guard already allows, and private registries
    are common in enterprises.
  - The Windows rule checks what the command resolves to (`shutil.which`), so an `.exe` launcher
    such as uvx isn't limited.
  - The `_on_windows` helper makes both platforms testable.
- **Tests:** `test_stdio_policy.py` (88 in all):
  - clusters (`-yc`, `-cy`, `-qyc=id`);
  - refused options (`--node-options`, `--node-opt`, `--script-shell`, `--userconfig`, `--git`,
    `--cal`, `-x`, `-py`);
  - interpreters in clusters;
  - ordinary launches, including server flags after the package;
  - uvx paths versus versions;
  - Windows `.cmd` metacharacters, a real `.exe`, and other hosts unaffected.

### SEC-12 — catalog packages run an exact version
- **What:** Exa runs `exa-mcp-server@3.4.1` and Slack `@modelcontextprotocol/server-slack@2025.4.25`.
  A registry test fails if any catalog STDIO template names a package without an exact version.
- **Why:** `npx -y pkg` runs whatever was published last, so a compromised release would reach
  every deployment the next time a session starts.
- **Decision:** existing records need no migration. `instance_config_from_dict` always launches a
  catalog server from its template, and a test checks that a record saved with the old args
  runs the pinned ones.
- **Follow-up (product decision):** the Slack package is archived upstream and npm marks it "no
  longer supported". The catalog entry should move to a maintained server, such as Slack's own
  hosted MCP server, rather than stay pinned to the last release.
- **Tests:** `test_registry.py::TestCatalogPackagesArePinned` and `test_mcp_servers_stdio_policy.py`
  (pinned args; an old record runs the pinned args).

## Phase 1 — SDK spike: result and migration plan

### What was tried
`mcp` 2.3.0 (client only, in a throwaway venv) against two live servers:
- a 2026-07-28 server built on `mcp` 2.3.0;
- the audit's legacy probe server on `mcp` 1.30.0, which speaks 2025-11-25.

Scripts: session scratchpad `hardening/spike/` (`spike_client.py`, `modern_server.py`).

### Findings
| Check | Modern server (2026-07-28) | Legacy server (2025-11-25) |
|---|---|---|
| Era negotiation (`Client(mode="auto")`) | 2026-07-28 | Falls back to 2025-11-25 |
| Custom `httpx2` transport sees every request (where the URL guard plugs in) | Yes | Yes |
| Timeout → server told and cancelled | Yes (`CancelledError` server-side) | Yes |
| A 401 on one call while another runs | Only that call fails; the other completes | Same |
| Calls after the 401 on the same client | Work | Work |
| Result that misses its `outputSchema` | n/a | **Still raises `RuntimeError`** (port the lenient check) |
| How a 401 surfaces | `MCPError -32603 "Server returned an error response"`, no HTTP status in the chain | Same |
| Works with our starlette 0.41.3 (FastAPI 0.115) | Yes | Yes |

So the SDK fixes these by design:
- RUN-1 / RUN-3: one request's error no longer fans out to others.
- RUN-4: no sessions to expire on modern servers, and re-initialization on legacy ones.
- RUN-6: cancellation is real.

### Decision: go, using `mcp` 2.x directly, without fastmcp
- fastmcp 4 needs starlette ≥ 1.0.1, which means a platform-wide FastAPI upgrade.
- `mcp` 2.x needs only starlette ≥ 0.27. Its own `Client` covers what we use from fastmcp, plus
  dual-era negotiation, `ttlMs`/`cacheScope` caching, elicitation, progress and
  `max_sse_event_size`.
- fastmcp is imported only in `app/agents/mcp/client.py` and `url_guard.py`.

### Why it isn't done in this session
The migration is several hours of careful work:
- rewrite `client.py`;
- port the URL guard's transports to `httpx2`;
- move 401 refresh into an `httpx2.Auth`;
- simplify `MCPSessionManager`;
- rewrite `test_client.py`.

Stopping halfway would leave the branch broken while unattended. It also needs a dependency
decision (below). I used the remaining time on SDK-independent fixes instead. The migration is
the next task.

### Migration plan (next session)
1. **Environment, decision needed.** The shared venv also serves the main checkout
   (`postgres-sql-kg`), which still uses fastmcp 3.2 with `mcp` 1.x, and installing `mcp` 2.x
   there would break it. Recommended: install `mcp==2.3.0`, `mcp-types` and `httpx2` into an
   overlay directory (`pip install --target`) and put it first on `PYTHONPATH` for this
   worktree's test runs, or give the worktree its own venv.
2. **Dependencies.** `pyproject.toml`: drop `fastmcp`, pin `mcp==2.3.0` and `httpx2`.
   `starlette` stays pinned for FastAPI.
3. **URL guard.** Split the pinning and origin logic from the HTTP library. Keep the `httpx`
   transport for our own OAuth and discovery code, and add an `httpx2.AsyncBaseTransport` for
   MCP traffic. Same tests, both stacks.
4. **Auth.** An `httpx2.Auth` that sends the bearer token and, on a 401, refreshes once
   (`refresh_credential_record`) and resends that same request. That's safe, because the server
   refused it. Once this is in, the call-id attribution in `MCPSessionManager` and the
   session-level refresh can go. A 403 `insufficient_scope` step-up hooks in here later.
5. **Client.** `MCPClientManager` on `mcp.client.Client` with:
   - `streamable_http_client(url, http_client=…, max_sse_event_size=…)`, `sse_client`, and
     `stdio_client` (env, stderr capture);
   - per-call timeouts;
   - the lenient output validation ported to 2.x's hook;
   - `client_info` set to PipesHub (RUN-15);
   - result handling: snake_case fields, which `mcp_result` already reads.
6. **Era cache.** Store the negotiated era per origin, and pin `mode` on later connects to skip
   the probe.
7. **Tests.** Rewrite `test_client.py` against the 2.x `Client`. Keep the live probes as
   opt-in integration tests.

## Phase 4 (interface) — quick wins

### UX-1 + AUTH-2 — sign-in says what happened, and Reconnect never disconnects you first
- **What:** `useMcpOAuthPopup` rewritten.
  - The popup opens as `about:blank` inside the click, before anything is awaited, and is sent
    to the sign-in address once the server returns it.
  - `onFailed` receives a reason: `blocked`, `cancelled`, `provider_error` (with the callback's
    message), `timeout` or `unavailable`.
  - `mcpOAuthFailureMessage(t, failure)` turns the reason into copy. It reuses the existing
    `agentBuilder.oauthPopupBlocked` / `oauthSignInCancelled` strings, plus two new ones
    (`workspace.mcpServers.toasts.oauthTimedOut`, `oauthProviderError`) in all 9 locales.
  - A popup closed without a message gets one immediate check and reports `cancelled`, instead
    of after an ~8.5 s retry loop.
  - Messages from other windows are ignored, and the blank popup is closed on any failure.
  - The team and personal pages toast the reason and reload the list. The builder's credentials
    dialog shows it inline.
  - Reconnect (OAuth) no longer calls `/reauthenticate` first: the callback replaces the tokens
    only when the new sign-in succeeds.
- **Why:**
  - a blocked or closed popup did nothing visible;
  - the provider's error was thrown away;
  - strict popup blockers stopped a popup opened after an await;
  - Reconnect deleted the working token before the popup, so a failed sign-in left the user
    disconnected while the UI said Connected.
- **Decision:** keep the `/reauthenticate` route for API clients. The UI no longer needs it for
  OAuth, and Disconnect remains the way to clear a broken registration.
- **Tests:** `oauth-popup-safety.test.ts` (opening order, unsafe address, blocked, cancelled after
  one check, closed-but-connected, provider message, foreign window ignored, fetch failure,
  messages) and `agent-mcp-credentials-dialog.test.tsx` (Reconnect keeps the token; blocked
  popup explained).

### UX-2 — a server's status says what's wrong and what fixes it
- **What:**
  - Backend: each listing entry now carries `toolsErrorCode`, one of `timeout`, `unauthorized`
    (the server answered 401), `auth_expired` (the token couldn't be refreshed), `unreachable`
    or `error`, next to the existing `toolsError` text.
  - Frontend: `mcp-servers/connection-state.tsx` holds one function, `mcpConnectionState(entry,
    { isAdmin })`, and one `McpStatusBadge`. The states are `ready`, `needs_connect`,
    `needs_reconnect`, `waiting_for_admin`, `shared_credential_missing`, `unreachable` and `slow`.
  - The personal card, the team card and the team row all use it. The card's button follows the
    state: Connect, Reconnect (which runs the existing reauthenticate flow), or none when only an
    administrator can fix it. The team row's banner says "Reconnect needed" for an expired
    sign-in.
  - The error text under a card is clamped to two lines, with the full text on hover. A slow
    server's note is grey instead of red.
  - New strings: `status.reconnectNeeded`, `waitingForAdmin`, `sharedCredentialMissing`,
    `unreachable`, `slow`, `cta.reconnect` and `details.reconnectBannerDescription`, in all 9
    locales.
- **Why:**
  - the badge said Ready whenever the server was marked shared or a token existed, even when the
    shared credential was missing, the token had expired, or the server was down;
  - a member was shown nothing they could act on;
  - the three components each had their own, slightly different rule.
- **Decisions:**
  - The state is derived from a code, not by matching error text, which changes with wording
    and translation.
  - An OAuth server is never treated as shared: each person signs in for themselves even when
    the server is marked shared, so they get Connect, not "Waiting for admin".
    `usesSharedCredential` mirrors the backend's `uses_shared_credential`. The card and row now
    use it for their Reconnect/Disconnect menu too; before, they read the raw `useAdminAuth`
    flag and hid both for a shared-marked OAuth server.
  - A rejected shared credential goes back to the administrator ("Shared credential missing" on
    the team page, "Waiting for admin" for members), since a member can't replace it.
  - A timeout is `slow`, not broken: the listing has a time budget, and chat waits longer.
  - Entries from an older backend, which send no code, still work: `toolsTimedOut` means slow,
    and any other error means unreachable.
- **Tests:**
  - backend: `test_mcp_servers_error_detail.py` (one code per failure) and
    `test_timeouts_and_reuse.py` (timeout → `timeout`);
  - frontend: `connection-state.test.tsx`, covering every state, the shared/OAuth rules,
    entries without a code, and the personal card, team card and team row (expired → Reconnect
    calls reauthenticate; waiting for admin shows no button; unreachable is never Ready).

### UX-3 + UX-4 + UX-10 (+ a UX-16 fix) — the server form asks before it signs people out
- **What:**
  - **UX-3:** `mcp-servers/edit-impact.ts` adds `mcpEditCredentialImpact(existing, payload)`,
    which returns `all_credentials`, `shared_credential` or `none`.
    - Saving an edit that would remove credentials first opens a confirmation: "Remove saved
      sign-ins?". It's a `ConfirmationDialog` in the drawer's nested-modal host.
    - Copy differs for an org server (everyone's credentials), a personal server (yours and
      your agents') and for turning off the shared credential.
    - Nothing is sent until the user confirms. The post-save `credentialsReset` toast stays as
      the backstop.
  - **UX-4:** a local-command server needs only a command; Arguments and Required env are
    optional. With API-token sign-in and no variables listed, the form says the token is passed
    as `API_TOKEN` (the backend's `DEFAULT_ENV_VAR_NAME`).
  - **UX-10:**
    - `isSecretFieldName` (in `stdio-env-auth.ts`) masks any field whose name contains TOKEN,
      SECRET, PASSWORD/PASSWD/PASSPHRASE/PWD, KEY, CREDENTIAL, COOKIE, SESSION, PRIVATE,
      SIGNATURE or AUTH, or the whole word PAT. Before, only "token" and "key" were masked.
    - Used by the config panel, the token dialog and the builder's credentials dialog.
    - The token dialog takes the instance's catalog `template`: its label, placeholder and help
      text, and a "Where to find this" link to the docs.
    - It now says "Credentials saved" instead of "Authenticated successfully".
  - **UX-16 (one item):** the token dialog's Cancel is a real button instead of a span keyboard
    users couldn't reach.
- **Why:**
  - the reset warning came only after the credentials were already gone;
  - the form demanded an argument and a variable the backend never needed;
  - `CLIENT_SECRET` and `DB_PASSWORD` were typed in clear text;
  - the token dialog ignored the catalog's hint;
  - a rejected token was reported as authenticated.
- **Decisions:**
  - `mcpEditCredentialImpact` mirrors the backend's `_connection_target_changed` and
    `_settle_shared_credential`:
    - same fields (`typeId, transport, url, command, args, authMode, tokenUrl`);
    - missing, empty and null compare equal;
    - a catalog server of the same type compares only `authMode`.
  - If the two ever drift, the backend still decides and the toast still reports it, so a miss
    costs only the advance warning.
  - The rule is a pure function so it can be tested without rendering the form.
  - Masking leans towards hiding: masking an identifier only costs a typo check, while showing
    a secret leaks it on screen.
  - Saving doesn't test the token. The listing that reloads after saving checks it, and UX-2's
    status then shows "Reconnect needed" if the server refuses it. A save-time check would add
    up to the connect timeout to every save.
- **Tests:** `edit-safety.test.tsx` (27):
  - the impact rule: address, program, sign-in method, token URL, rename, missing vs empty,
    catalog servers, shared on/off;
  - secret names;
  - the panel: confirm then save, cancel keeps everything, rename saves directly, shared-off
    copy, personal copy, command-only create, the `API_TOKEN` hint, CLIENT_SECRET masked but
    CLIENT_ID not;
  - the token dialog: catalog hint, link and "Credentials saved"; Cancel is a button.

### UX-6 — an agent chat stopped on an unconnected server offers Connect
- **What:**
  - **Backend** (`agent.py`): the `mcp_server_config_missing` RUN_ERROR now carries
    `details: {agentId, serviceAccount, servers: [{instanceId, name, problem, authMode,
    sharedCredential}]}`. `problem` is `not_found` or `not_connected`. `_stream_error_frame` takes
    the optional `details`.
  - **Frontend:**
    - `chat/stream-error.ts` adds `ChatStreamError` (keeps `code` and `details`) and
      `mcpConfigMissingDetails`, which checks the shape rather than trusting it.
    - The AG-UI handler raises a `ChatStreamError`. The slot stores `{code, details}` on the
      failed reply's `metadata.custom.streamError`, the message pair carries it, and
      `ChatResponse` renders `McpConnectRequiredCard` instead of the error text.
  - **The card:**
    - **Connect**, for a server the person can fix themselves. OAuth opens the popup from the
      click itself (the existing hook); a token or headers server opens the existing token
      dialog.
    - "Removed. Ask the agent's owner…" for a deleted server.
    - "Waiting for an admin…" for a missing shared credential.
    - A note and an "Open Agent Builder" link for a service-account agent.
    - Once every server is connected, **Try again** sends the question again, once, using the
      same path as the ask-user card.
  - New strings `chat.mcpConnect.*` in all 9 locales.
- **Why:** the whole turn failed as plain text with no way forward. The error code was dropped
  between the stream and the UI.
- **Decisions:**
  - The details travel on the existing RUN_ERROR rather than a new event: Node already passes
    it through unchanged, and an older frontend just ignores the extra field.
  - The card replaces the text only for this code; every other error renders as before.
  - "Try again" sends a new message instead of regenerating, because a failed run has no
    stored reply id to regenerate from.
  - **Known limit:** after a reload, the reply shows the stored text, since Node keeps the
    message but not `details`. Persisting it would mean a Node message-schema change, left as
    a follow-up.
- **Tests:**
  - backend `test_agent_chat_route.py::TestChatStreamMcpServers`: not connected with its sign-in,
    removed, shared credential flagged, service account;
  - `stream-error.test.ts`: parsing and distrust of the shape;
  - `agui-event-handler.test.ts`: the code and details are passed on;
  - `streaming-slot.test.ts`: kept on the reply;
  - `message-pairs.test.ts`;
  - `mcp-connect-required-card.test.tsx`: OAuth popup then Try again once, cancelled, token
    dialog, server missing from my list, nothing offered that a member can't do, service
    account link;
  - `chat-response-stream-error.test.tsx`: card instead of text; other errors unchanged.

### RUN-10 + UX-7 (part) — one reason code for every MCP failure; failed calls look failed
- **What:**
  - `app/agents/mcp/failure.py` adds `classify_mcp_failure(exc)`, returning `auth_expired`,
    `unauthorized`, `blocked`, `timeout`, `unreachable` or `error`.
    - It looks through the SDK's ExceptionGroup.
    - It checks timeouts before network errors, because `TimeoutError` is an `OSError` and
      httpx's timeouts are `HTTPError`s.
  - `MCPLaunchRefusedError` (an `MCPConnectionError`) is now raised when the local-command
    policy refuses a launch, so a refusal reads as `blocked`, not `unreachable`.
  - **Agent loop:** `mcp_tool_load_failures[].reason` is the classified code instead of a
    blanket `discovery_failed`. The model's "Unavailable MCP Servers" section gives one line per
    server saying what fixes it: reconnect, update credentials, an administrator's policy, or
    try again later. It also says never to claim the capability doesn't exist.
  - **Listing:** `toolsErrorCode` comes from the same classifier, so a blocked URL is reported
    as `blocked`. The UI shows it as a red "Blocked by policy" badge (new `blocked` state).
  - **Chat:** failed tool calls are drawn in red (icon and the "Error" heading) instead of grey.
    The tool card's Arguments/Result/Error/Blocked headings are translated (`chat.toolCard.*`).
- **Why:**
  - the model got "temporarily unavailable" for an expired sign-in, a policy block and a server
    that was down alike, so it couldn't tell the user what to do;
  - in grey, a failed call looked like a completed one.
- **Decision:** one classifier shared by the listing and the loader, so the same failure gets
  the same name on the workspace page, in the model's prompt and on the chat card.
- **Left for later (UX-7, second half):** telling the *user* directly that a server failed to
  load at the start of a turn. That needs an event from the loader to the stream, plus a
  matching part in the transcript collector, so a reload shows the same notice. For now the
  model is told precisely, and its answer says what's wrong.
- **Tests:**
  - `test_failure.py`: every reason, the ExceptionGroup, subclassing;
  - `test_mcp_tool_loader.py`: error, unauthorized and auth_expired recorded;
  - `test_capability_summary.py::TestUnavailableMcpServers`;
  - `test_mcp_servers_error_detail.py`: the `blocked` code;
  - `connection-state.test.tsx`: the `blocked` state;
  - `agent-activity.test.tsx`: the failed card in red, labels translated. That file now loads
    the test i18n setup, so its label assertions check real text rather than raw keys.

## Phase 3 (sign-in)

### AUTH-3 + AUTH-4 + AUTH-10 — token errors are read from the body, no-expiry tokens are left alone, and a rejected refresh isn't a 401
- **What:**
  - **`oauth_client._post_token_request`** treats a response carrying an RFC 6749 `error` as a
    failure whatever its HTTP status.
    - Permanent (`MCPRefreshTokenInvalidError`, meaning the user must reconnect):
      `invalid_grant`, `invalid_client`, `unauthorized_client`, `unsupported_grant_type`,
      `invalid_scope`, `invalid_request`, GitHub's `bad_refresh_token` and
      `incorrect_client_credentials`, plus the old text markers.
    - Anything else, such as `server_error` or `temporarily_unavailable`, can be retried.
    - The message is now `code: description`, or the first 500 characters, instead of the
      whole response body.
  - **The background refresh** skips a token that has no `expires_in`.
  - **`POST /oauth/refresh`** answers 409 instead of 401 when the provider rejects the refresh
    token.
- **Why:**
  - GitHub's `bad_refresh_token` arrives with HTTP 200, so it was parsed as a successful
    response and then failed as "missing access_token", which counts as temporary;
  - `invalid_client` was temporary too;
  - in both cases the credential was never marked disconnected and the refresh was retried
    forever;
  - a token with no expiry was refreshed on every five-minute pass, on every replica;
  - a 401 from our API means the caller's own session ended, and the app signs them out.
- **Decisions:**
  - A token with no expiry is refreshed on demand: the session manager already refreshes
    and retries once on a 401.
  - 409 Conflict means the stored credential no longer matches the provider. It's a 4xx, so
    Node passes it through with its message.
- **Tests:**
  - `test_oauth_client.py::TestErrorsInTheResponseBody`: five permanent shapes, including
    JSON and form bodies with HTTP 200; two retryable; the message names the error; a code
    exchange error with 200;
  - `test_mcp_token_refresh_service.py`: a no-expiry token is left alone;
  - `test_mcp_servers_extended.py`: 409.

### AUTH-6 — sign-in is discovered for Entra ID, Okta and Keycloak
- **What:** `dcr._authorization_server_metadata_urls(issuer)` probes in the MCP authorization
  spec's order for an issuer with a path:
  1. RFC 8414 with the path inserted;
  2. OpenID with the path inserted;
  3. OpenID appended after the path;
  4. RFC 8414 appended after the path;
  5. then the bare host (RFC 8414, then OpenID).
- **Why:** these providers serve metadata at `{issuer}/.well-known/openid-configuration`, which
  was never tried, so their sign-in couldn't be discovered.
- **Decision:** the bare host moved to last. On Okta it returns the *org* authorization
  server's metadata, not the custom one the MCP server trusts, so trying it first would pick
  the wrong issuer. Servers that only serve metadata at the root (Atlassian, GitHub) still
  get there, after a few extra 404s.
- **Tests:** `test_dcr.py::TestIdentityProviderIssuers` (spec order; host only; Entra, Okta and
  Keycloak issuers found through appended metadata). Two existing order tests updated.

### AUTH-5 + AUTH-8 — sign-in names the server (RFC 8707) and asks only for its scopes
- **What:**
  - **Discovery** returns `resource` and `resource_scopes` from the MCP server's own RFC 9728
    metadata, only when it publishes one. `canonical_resource_uri` lowercases the scheme and
    host and drops userinfo, query, fragment and any trailing slash. A declared `resource`
    that isn't a web address falls back to the server URL.
  - **Sign-in** requests the admin's scopes, else the server's own `scopes_supported`, else
    none. It sends `resource` on the authorize request (a reserved parameter, so a catalog
    template can't override it) and stores it in the state record.
  - **The callback** sends `resource` on the code exchange. The tokens keep it
    (`OAuthTokens.resource`), and every refresh sends it again.
- **Why:**
  - the protocol version we advertise requires `resource`, and servers that check a token's
    audience kept answering 401;
  - with no scopes configured, sign-in asked for every scope the authorization server lists,
    which on a shared identity provider means every application's.
- **Decisions:**
  - `resource` is sent only to a server that publishes RFC 9728 metadata, which means it
    follows the current MCP authorization spec. Some identity providers reject an unknown
    `resource` (Entra ID v2: AADSTS901002), and older servers fronted by them would break.
  - The authorization server's `scopes_supported` is still returned for display, never
    requested. This follows the spec's scope order: the server's metadata, else omit.
  - Tokens issued before this change have no `resource`, so they refresh without one, as
    before.
- **Tests:**
  - `test_dcr.py::TestTheServersOwnMetadata`: canonical URI cases, resource and scopes from
    the server's metadata, none without it, the authorize URL carries it, and a template
    can't change it;
  - `test_oauth_client.py::TestResourceIndicator`: exchange, refresh, none added when absent;
  - `test_token_refresh.py::TestRefreshKeepsTheResource`;
  - `test_mcp_servers.py::TestSignInAsksForTheServersScopesAndNamesIt`: the server's scopes
    and resource, neither without its metadata, an admin's scopes still win.

### SEC-13 — a sign-in state in the wrong hands can't cancel the sign-in; agent sign-ins are re-checked
- **What** (`/oauth/callback`):
  - The initiator and org checks now run *before* the state is claimed and deleted, so a
    callback from anyone else is refused without consuming the state.
  - For an agent's sign-in (`ownerType: agent`), the callback re-checks that the caller can
    still edit the agent (`_require_mcp_agent_edit_access`) before fetching or saving any
    token. If they can't, it returns `agent_access_revoked`.
- **Why:**
  - anyone holding a state value could burn another user's sign-in, because the claim and
    delete came first;
  - edit access to an agent could be revoked during the ten-minute sign-in window, and the
    agent's tokens were saved anyway.
- **Decision:** an expired state still returns `expired_state` without being consumed. The
  store's TTL removes it.
- **Tests:** `test_mcp_servers.py`:
  - the inverted test: a wrong caller doesn't consume the state;
  - `TestTheSignInStateBelongsToItsInitiator`: the initiator still completes afterwards; an
    agent sign-in without edit access isn't saved; with it, it's saved under the agent.

### DATA-4 — no code change; covered by DATA-1
- A failed credential delete, or a listing error swallowed during a server delete, still
  leaves credentials behind. But DELETE now removes the server record first and fails if it
  can't (DATA-1). Once the record is gone, the background refresh deletes any credential whose
  server is gone (`credential_has_instance(...) is False`) instead of renewing it. So the
  leftovers are removed on the next pass, and "keeps renewing them" no longer happens.

## Phase 6 (MCP features) — started

### Server instructions reach the model
- **What:**
  - `MCPClientManager.server_instructions` reads the open session's `initialize`
    instructions (trimmed; None when absent).
  - `MCPSessionManager.instructions(server)` passes them on. The loader records
    `{instanceId, name, instructions}` on `context.mcp_server_instructions` for each server
    that loaded.
  - The capability summary adds "Notes From MCP Servers", one bullet per server, each capped
    at 2,000 characters.
- **Why:** servers use `instructions` to say how their tools should be used (call search
  first, which IDs mean what), and we dropped it.
- **Decisions:**
  - The section says the notes come from the server, not from the user or PipesHub, and tells
    the model to ignore anything in them asking for something else. It is third-party text in
    the prompt.
  - Only servers that loaded contribute. A failed server's tools aren't available anyway.
  - Read from the session the turn already opened, so no extra request.
- **Tests:** `test_client.py::TestServerInstructions`; `test_mcp_tool_loader.py::TestServerInstructions`
  (recorded, none without, none when loading failed); `test_capability_summary.py::TestMcpServerNotes`
  (labelled, multi-line kept, capped).

### Not done this session (planned)
- **Per-tool approvals** (decision 2). Needs:
  - a policy per agent attachment: always allow / needs approval / blocked, defaulting to
    *needs approval* for tools the server marks `destructiveHint` and *always allow* for
    `readOnlyHint`;
  - the agent loop to pause a call and resume it after the user answers, building on the
    existing `ask_user_question` card and `CUSTOM` event;
  - builder UI to set the policy per tool.
  It changes the loop's control flow, so it should be designed and reviewed as its own piece,
  not rushed into the end of this session.
- **Tool cache per sign-in context** (decision 4): see `tool_cache_plan.md` from the earlier
  session. Key on `(instanceId, credential owner, credential version)`; invalidate on
  reconnect, credential change, server edit, and `notifications/tools/list_changed`.
- **Progress and elicitation** (decision 5): planned for after the mcp 2.x migration. The
  2.x client exposes both handlers cleanly, and progress needs a new AG-UI event the
  frontend can render.
- **UX-7, second half:** a user-facing notice when a server fails to load at the start of a
  turn (see the RUN-10 entry).
- **The mcp 2.x migration:** the plan is in the Phase 1 section. It needs an environment
  decision, because the shared venv also serves the main checkout.

## Final regression (end of session)
- **Backend:** `tests/unit/agents`, `tests/unit/modules/agents`, `tests/unit/api/routes` and the
  MCP token refresh service: 10,025 passed. The 38 failures are the pre-existing ones listed
  under "Start here": 37 in `test_teams_toolset.py` and 1 SharePoint behaviour test.
  `test_artifact_pipeline_e2e[local]` was deselected.
- **Frontend:** the full vitest suite (126 files, 1,560 tests) passes. `tsc` shows only the
  pre-existing errors. i18n parity holds for all 9 locales.

## Round 2 (2026-10-06): the remaining issues

Scope: what mattered beyond the open decisions. That is DATA-2 (reworked: the first fix relied
on a rollback that default Neo4j doesn't do), RUN-4/5/6, SEC-7/8/9/11, DATA-3, AUTH-7 (with
AUTH-11's issuing client), SCALE-1/2, UX-5 and test tracing. Not touched: the toolset save
(PR #3949 on another branch), the migration, approvals, the tool cache, the Slack replacement,
progress/elicitation, UX-7's second half, the Connect card after reload, and users deleted
before the cleanup change.

**Plan, reviewed and revised before any code.** v1 had ten problems the review fixed:
- tracing moved first;
- the save fix also covers agent create;
- sharing is blocked only by personal servers, not deleted ones;
- only the Neo4j provider swallows the MCP read error;
- Stop stays MCP-only (the adapter can reach the cancel token);
- the role is looked up only for org requests, and "couldn't confirm" never logs anyone out;
- old DCR clients without a recorded redirect URI are kept;
- sign-in order becomes static app → legacy per-owner DCR → shared DCR → register;
- live builder data mustn't mark the agent edited;
- the store change is tested on both backends.

Order: 0 tracing · 1 DATA-2 · 2 RUN-4/5/6 · 3 SEC-8/9/7 · 4 DATA-3 · 5 AUTH-7 · 6 SEC-11 ·
7 SCALE-2 · 8 SCALE-1 · 9 UX-5 · 10 wrap-up.

**Environment:** the worktree now has its own venv (`backend/python/venv`, gitignored). It
holds the shared venv's exact 444 package versions, so results stay comparable, and the
project is installed editable from this worktree. All round-2 tests run with it.

### 0 — tests never send traces to a real Opik account
- **What:** `tests/conftest.py` sets `OPIK_API_KEY`, `OPIK_WORKSPACE`, `OPIK_URL_OVERRIDE`
  and `OPIK_PROJECT_NAME` to empty strings at import time, sets `OPIK_TRACK_DISABLE=true`, and
  points `OPIK_CONFIG_PATH` at an empty config file. `tests/unit/test_no_external_tracing.py`
  guards it.
- **Why:**
  - backend test runs were sending spans to a hosted Opik account and hit its free-tier limit;
  - the SDK falls back to `~/.opik.config`, and outside tests it finds a real key there
    (checked);
  - a checkout's `.env` is loaded by `load_dotenv()` at import time.
- **Decision:** the variables are blanked rather than removed, because `load_dotenv()` never
  overwrites a variable that is already set, even to an empty value. Tests that exercise
  tracing patch `opik` themselves and are unaffected.
- **Tests:** the guard (gate closed, the SDK has no key, a `.env` can't switch it back on),
  plus every test file that mentions `opik` and `tests/unit/utils`: 4,064 passed.

### 1 — DATA-2 reworked: an MCP attachment save never relies on a rollback
- **What** (`api/routes/agent.py`):
  - **`_create_mcp_server_edges`** now writes in this order: server nodes, tool nodes,
    server→tool links, and the agent→server link **last**. It records every node key before
    writing (`written_server_keys` / `written_tool_keys`) and fails on any write that returns
    false.
  - **The update** reads the old set (`_read_agent_mcp_attachments`), writes the new set,
    unlinks the old servers one by one (recording each), then removes the old tools and
    servers, and commits. If it fails:
    - before any old server was unlinked, the agent is still on its old set, so the attempt's
      own writes are removed (`_remove_mcp_servers`);
    - after that, the new set is the intended state. `_finish_unlinking_old` reads the links
      back and, if the new servers are linked, finishes unlinking the old ones and removes
      their nodes.
  - **Agent create:** if the MCP part fails, it removes what it wrote.
  - `_finish_unlinking_old` is copied unchanged from PR #3949, in the same position. Drop this
    copy when rebasing onto #3949. `_delete_agent_mcp_attachments` is gone (no other callers).
- **Why:** the first DATA-2 fix (109a3320f) deleted the old set and then created the new one
  in one transaction, trusting the rollback. The toolset-save work found that Neo4j runs
  without explicit transactions by default (`NEO4J_EXPLICIT_TRANSACTIONS` is off), so the
  rollback undoes nothing. A failure could leave the agent with no MCP servers, or with a
  server linked before its tools existed.
- **Decisions:**
  - The same algorithm as #3949, so the two saves behave alike and share the helper after the
    rebase. Nothing in the toolset code was touched.
  - **Not fixed: the agent-wide create rollback.** On default Neo4j a failed *create* still
    leaves the agent node and whatever else was written (toolsets, knowledge); only its MCP
    part cleans up after itself. That's a separate, agent-wide fix (follow-up).
- **Tests:** `test_agent_mcp_tool_selection.py::TestASaveNeverLeavesTheAgentWithoutItsServers`
  (25):
  - each of the four writes failing, by raising or by returning false, in both transactional
    and auto-commit mode: the old servers are intact;
  - write order, with every write in the committed transaction;
  - a failure between old unlinks (rolled back, or finished onto the new set);
  - a failure removing old nodes;
  - detaching everything;
  - a failed create leaves no MCP nodes.
  14 of these fail against the previous code. All 890 agent route tests pass.

### 2 — RUN-4/5/6: a dead session is replaced, and Stop interrupts a call
- **What:**
  - **`errors.py`** adds `is_session_expired`, `is_connection_lost` and
    `request_never_reached_server`. They recognise mcp 1.x's shapes:
    - an expired session is `McpError(32600, "Session terminated")`;
    - a transport that closed under a pending request is
      `McpError(CONNECTION_CLOSED, "Connection closed")`;
    - a request that couldn't be written is `anyio.ClosedResourceError` /
      `BrokenResourceError`.
    They look through the SDK's ExceptionGroup.
  - **`MCPSessionManager.call`:**
    - on a lost connection the session is dropped (`_evict`), since the SDK still reports it
      open;
    - if the request never reached the server (expired session, or unwritten), it is sent
      once more on a new session with a new call id;
    - if it was in flight when the connection closed (a local server that exited), it's
      reported as `MCPCallInterruptedError` ("may or may not have completed") and never
      repeated;
    - a second refusal isn't retried again;
    - the 401 refresh path is unchanged (now `_refresh_and_call`).
  - **`discover`:** a lost connection is always reconnected and listed again, since listing
    changes nothing on the server.
  - **`MCPToolAdapter.execute`** races the call against the run's `cancellation_token`. On
    Stop, it cancels the wait and answers "Stopped before the … MCP server answered. The call
    may still finish on the server." Cancelling the turn cancels the call too.
- **Why:**
  - **RUN-4:** after a 404 for an expired session id, every later call in the turn failed with
    "Session terminated".
  - **RUN-5:** after a local server exited, every later call failed.
  - **RUN-6:** Stop only took effect between tool calls, so a stopped turn waited up to the
    call timeout (60 s by default, up to 600 s).
- **Decisions:**
  - Retry only when the call provably never ran, the same rule as RUN-1. A pending call on a
    closed connection may have run.
  - The Stop race lives in the MCP adapter, not the shared executor, which also runs
    non-MCP tools.
  - The server is **not** yet told about the cancellation. `notifications/cancelled` needs the
    JSON-RPC request id, which mcp 1.x/fastmcp don't expose. The 2.x client sends it on
    cancel, so this is completed by the migration.
  - These error shapes change with mcp 2.x, so the helpers sit in `errors.py` next to the 401
    helpers, which the migration rewrites anyway.
- **Tests:**
  - `test_mcp_session.py::TestADeadSessionIsReplaced` (6): expired session resent on a new
    one; unwritten request resent; in-flight call on a closed connection reported, not
    repeated, and the next call reconnects; a second refusal not retried; a server's own
    -32000 error keeps the session; discovery reconnects;
  - `TestDeadSessionErrors` (9 cases);
  - `test_mcp_tool_adapter.py::TestStopInterruptsACall` (4): Stop ends a waiting call and
    cancels it; a normal result; a failure; cancelling the turn cancels the call.
  The six behaviour tests fail against the old code. 2,395 agent, adapter, MCP and loop tests
  pass.

### 3a — SEC-8 / SEC-9 / SEC-7: attachments and credentials trust nothing from the request
- **What:**
  - **SEC-8** (`agent.py`):
    - `_parse_mcp_servers` caps an agent at 50 MCP servers and a server at 500 listed tools;
      past that, "attach all of its tools".
    - A tool name over 200 characters (full name over 300), or with control characters, is
      refused. Descriptions are cut to 2,000 characters and display names to 200.
    - `_bind_mcp_attachments_to_instances` then replaces each attachment's `name` and `typeId`
      with the stored server's, and re-applies one-server-per-type on the stored types. This
      runs on both create and update.
  - **SEC-7:** turning on sharing (org or service account) checks the attached servers with
    `_attached_personal_instances`, which uses `find_personal_instance_for_admin`. That is the
    org's view, so any personal server blocks it, whoever owns it. A server that no longer
    exists doesn't block anything.
  - **SEC-9:**
    - `MCPServerInstanceConfig` bounds every text field: name 200, description 2,000, URLs
      2,048, command 512, args 64×2,048, env names 64×128, scopes 64×256.
    - `headerName` must be an HTTP token and not one HTTP or MCP uses itself
      (`RESERVED_HEADER_NAMES`: Host, Content-Length, Mcp-Session-Id and others).
    - `useAdminAuth` is refused unless sign-in is by API token or headers.
    - `AuthenticateRequest` bounds secrets at 16,384 characters and env at 64 entries. A
      header value must be one line. The `headerName` a user sends is ignored: the instance
      decides which header carries the credential.
- **Why:**
  - the request's name and type were stored verbatim, so leaving out `typeId` skipped
    one-per-type, and 5,000 tools were accepted;
  - the sharing check used the editor's view, so a co-editor (ORGANIZER) could share an agent
    holding the owner's personal server, because it resolved to nothing and was dropped;
  - nothing was bounded, and a user could put their credential in any header, such as `Host`.
- **Decisions:**
  - The limits are generous and well above real use.
  - Display names and descriptions are cut, not refused, because they come from servers.
  - Every agent and personal-server test now uses the stored name and type. The route test
    fixtures gained a realistic `typeId`.
  - There's no endpoint that shares an agent with individual users in this codebase (only
    org sharing and service accounts). The review's "sharing with individual users isn't
    covered" has nothing to attach to here.
- **Tests:**
  - `test_agent_mcp_attachment_trust.py` (11): the limits and the boundary, invalid names,
    cutting, identity from the stored server, an omitted type still caught, custom servers
    never conflict, the saved node carries the stored name and type;
  - `test_agent_personal_mcp_servers.py::TestSharingLooksAtEveryPersonalServer`: the
    co-editor case fails against the old code; a deleted server doesn't block;
  - `test_models.py::TestInstanceConfigIsBounded`;
  - `test_mcp_servers.py::TestCredentialInputIsBounded` and the reworded header-name test.
  All 1,690 MCP and agent route tests pass.

### 3b — SEC-7: a failed MCP read can't make the builder detach every server
- **What:**
  - **`neo4j_provider`:** on both the single-agent and list paths, an exception while reading an
    agent's MCP servers still returns `mcpServers: []`, but now also sets
    `mcpServersUnavailable: true`.
  - **Frontend:** `AgentDetail.mcpServersUnavailable`. `extractAgentConfigFromFlow` leaves
    `mcpServers` out of the save payload when it's set; the update route treats a missing
    field as "keep". The builder shows a warning: "This agent's MCP servers couldn't be loaded.
    They aren't shown here, and saving won't change them." The text is translated into all
    9 locales.
- **Why:** Neo4j swallowed the read error and returned an empty list. The builder sends
  `mcpServers` on every save, so the next save detached every server.
- **Decision:** a flag, not an error, so the agent page still opens and everything else stays
  editable. Arango reads MCP servers inside the agent query itself, so a failure there fails
  the whole read and needs no flag.
- **Tests:**
  - `test_neo4j_provider.py`: flagged on a failed list read, not flagged on a good one,
    flagged on a failed single-agent read;
  - `mcp-tool-selection.test.tsx`: the payload has no `mcpServers` when flagged, and keeps an
    empty list when it was really read.

### 4 — DATA-3: an unconfirmed role creates nothing
- **What:**
  - `_check_user_is_admin(..., require_known=True)` raises 503 ("We couldn't confirm your role
    right now, so nothing was created.") when Node can't confirm the caller's role (unknown or
    rejected).
  - Create uses it whenever the result could be an org server. A request for a personal server
    skips the role lookup entirely.
  - Every other admin gate keeps "unconfirmed means not an administrator".
- **Why:** if Node was unreachable, an administrator's new server was quietly created as
  personal, and a server's scope can't change afterwards.
- **Decisions:**
  - The agreed design stays as it was: a confirmed member who asks for an org server still
    gets a personal one. I briefly made that a 403, then reverted it, because it contradicted
    the documented rule ("non-admins always get personal") and its existing test.
  - REJECTED is treated like UNKNOWN (503), never as a sign-out. Python has already accepted
    the request's own authentication.
- **Tests:** `test_mcp_servers_personal.py::TestAnUnconfirmedRoleCreatesNothing`:
  - unknown and rejected roles give 503 and store nothing;
  - a personal request needs no lookup;
  - a confirmed admin gets an org server and a confirmed member a personal one.
  The class restores the real admin check that the module's fixture otherwise fakes.

### 5 — AUTH-7 (+ AUTH-11): one redirect URI, a client that fits it, and refresh with the client that issued the tokens
- **What:**
  - **Redirect URI from server config only.** `_mcp_oauth_redirect_uri` is
    `endpoints.frontend.publicEndpoint`, sub-path included, plus `/mcp-servers/oauth/callback/`.
    The browser's `baseUrl` is still accepted but ignored. `_resolve_validated_base_url` is gone.
  - **Which client a sign-in uses** (`_resolve_oauth_client`):
    1. the admin's static app, its own org's or the one inherited from the parent org;
    2. a legacy per-owner DCR client;
    3. the shared DCR client, while it still fits (`_dcr_client_fits`);
    4. a new registration.
  - **When a shared client no longer fits.** It doesn't fit if any of these holds:
    - it was registered for another redirect URI;
    - its secret expires within 5 minutes;
    - the provider rejected it.

    Such a client is replaced. Unless its secret has expired, it is kept inside the new record as
    `previousClient`, one generation only, so the tokens it issued keep refreshing.
  - **The first shared registration** uses `create_config_if_absent`. If two sign-ins register at
    the same moment, the loser uses the winner's client.
  - **What is recorded.**
    - `register_dynamic_client` sends `application_type: "web"`. It records `redirect_uri` and
      `client_secret_expires_at` on `DCRClient`.
    - A response that isn't a JSON object is a `DCRError`, no longer an `AttributeError`.
    - `OAuthTokens.client_id` records the client that issued the tokens. The callback sets it, and
      every refresh carries it forward.
  - **Refresh uses the issuing client.** `resolve_client_credentials(..., issued_by=)` finds that
    client wherever it is stored: legacy, shared, retired or static (parent org included). If
    the client is gone, refresh gets nothing rather than a different client.
    - Tokens from before this change have no `clientId`. They use the order sign-in used then,
      with a retired shared client ahead of the current one, and learn their client at the
      first refresh.
  - **`invalid_client`.** `MCPOAuthError.error_code` and `rejected_client` expose the provider's
    error. On `invalid_client`, at refresh or at the code exchange, `retire_rejected_dcr_client`
    acts on the rejected client:
    - an owner's legacy client is deleted;
    - the shared client is marked `rejectedAt`;
    - a retired client is dropped;
    - an admin's static app is left alone.
  - **The admin's form** gets `redirectUri` from both `GET /instances/{id}/oauth-config` and
    `POST /oauth/discover`. The "Redirect URI" card shows the server's value. The page origin is
    only a stand-in until that arrives.
- **Why:**
  - The shared DCR client was registered with whichever redirect URI the first sign-in sent,
    sometimes a browser-chosen path. Changing the public address then broke every later sign-in
    with a provider-side "redirect_uri mismatch". That lasted until someone deleted the record
    by hand.
  - An expired or rejected client had the same effect.
  - Refresh guessed the client from a fixed order, so it could pick a client other than the one
    that issued the tokens. That fails at the provider.
- **Decisions:**
  - **Static app first.** An admin who adds an OAuth app expects new sign-ins to use it. Recording
    the issuing client makes that safe for existing tokens; without it, the old order had to
    match refresh exactly.
  - **Records without the new fields are trusted.** Before this change the browser sent
    `window.location.origin`, and a different host fell back to config. So a stored client
    already matches the configured URI unless the public address has a sub-path, and in that
    case the callback never worked anyway.
  - **A rejected shared client is marked, not deleted.** One spurious `invalid_client` would
    otherwise sign out everyone who signed in through it. Marked, it stops new sign-ins, and each
    user's own failing refresh still signs that user out.
  - **A replaced client is kept for its tokens.** Changing the public address would otherwise
    sign everyone out. The previous client is dropped only when its secret has expired or the
    provider rejects it.
  - **Static app lookup lives in `token_refresh.static_oauth_client`.** Refresh now needs the
    parent org's app too: before this change, tokens issued by an inherited static app couldn't
    refresh at all. The edition hook is imported lazily, like `agent.py` does.
  - **Not done.** Two sign-ins that replace the same unfit client at the same moment both
    write. The loser's sign-in fails at the callback with "configuration changed, try again".
    That's rare and recoverable, so I didn't add a compare-and-set.
- **Release notes:**
  - The OAuth redirect URI is now always the configured public frontend address. Admins whose
    static OAuth app was registered with a different address must update it at the provider;
    the form shows the exact value.
  - Changing the public address re-registers dynamically registered clients on the next
    sign-in. Existing connections keep working.
- **Tests:**
  - `test_mcp_servers.py`:
    - `TestTheRedirectUriComesFromServerConfig`: keeps the sub-path, uses the default when
      unconfigured, never shaped by the browser.
    - `TestWhichClientASignInUses`: static wins (own and parent org), a fitting client is
      reused; mismatched, rejected, expired and about-to-expire clients are replaced, kept or
      not as appropriate; a lost registration race uses the winner.
    - Callback: tokens record their client; `invalid_client` retires a DCR client, but not a
      static app or on other errors; a sign-in started before a replacement still completes.
    - `test_registers_shared_dcr_client_when_none_exists` now expects `create_config_if_absent`.
  - `test_token_refresh.py`:
    - `TestRefreshUsesTheClientThatIssuedTheTokens`;
    - `TestARefreshKeepsTheIssuingClientAndDropsARejectedOne`, which also checks that other
      users of a rejected shared client still refresh;
    - `TestRetireRejectedDcrClient`.
  - `test_dcr.py::TestTheRegistrationIsRecorded`; `test_oauth_client.py::TestTheProvidersErrorCodeIsKept`.
  - `oauth-dcr-requirement.test.ts::resolveMcpOAuthCallbackUrl`.
  - 38 of the new tests fail against the old code. The rest pin behaviour that's meant to stay
    the same.

### 6 — SEC-11: sign-in state and codes stay out of keys and logs; expected tool failures log one line
- **What:**
  - **State records are keyed by hash.** OAuth state records live at
    `/services/mcp/oauth-states/{sha256(state)}`, and the single-use claim at
    `/oauth-state-claims/{sha256(state)}`. The sweepers list by prefix, so they're unchanged.
    The callback also reads the old unhashed key (`get_mcp_unhashed_oauth_state_path`), so a
    sign-in started on an older server during a rolling deploy still completes.
  - **Node.** The MCP proxy's debug log passes the path through `redactSensitiveQueryParams`,
    and `state` joins that helper's sensitive set. The access log stops recording it too,
    matching what `logSafeUrl` already did for outbound commands.
  - **Tool failures** (`MCPToolAdapter.execute`) log one line: `failed (<reason>): <Type>: <text>`,
    with every URL in the text reduced to scheme, host and path. There's a stack trace only when
    `classify_mcp_failure` says `error`, which means an unexpected exception. Timeouts,
    unreachable servers, HTTP errors and sign-in failures get one line each.
- **Why:**
  - `ConfigurationService` logs every key it writes, and keys are visible to anyone who can list
    the store, so the state value was written to logs in plain text.
  - The proxy logged the callback path with its code and state.
  - The tool log used `%r` of the exception, plus a traceback for everything except
    `MCPConnectionError`. An `httpx` error's text names the request URL, query and all, and
    some servers take their API key there.
- **Decisions:**
  - Kept the existing `redactSensitiveQueryParams` rather than `logSafeUrl`. It keeps a relative
    path's harmless parameters (`includeTools`); `logSafeUrl` reduces a relative URL to its path.
  - The unhashed read is the only compatibility path. States expire in 10 minutes, so it can go
    in the release after this one.
- **Tests:**
  - `test_constants.py`: keys are the hash and never contain the state; the claim uses the same
    hash.
  - `test_mcp_servers_org_scoping.py`: the callback tests use the hashed key, plus a sign-in
    stored under the unhashed key that still completes and is claimed by hash.
  - `test_mcp_servers.py`: the authorize route never writes the state into a key.
  - `test_mcp_tool_adapter.py::TestAFailedCallIsLoggedSafely`: HTTP errors, timeouts and expired
    sign-ins log without a stack and without the API key; an unexpected error keeps its stack.
  - Node: `log-redaction.utils.test.ts` redacts `state`; `mcp_servers.controller.test.ts` logs
    the callback without its code or state while still forwarding both.

### 7 — SCALE-2: a listing discovers at most 8 servers at once, all within one deadline
- **What:**
  - `/my-mcp-servers` and `/agents/{key}` create one `_ListingDiscovery` per request:
    - a semaphore of `LISTING_DISCOVERY_CONCURRENCY` (8) slots;
    - a deadline of `LISTING_DISCOVERY_DEADLINE_SECONDS` (15 s) across the whole listing.
  - Each server gets `min(10 s budget, time left)`. If less than `LISTING_DISCOVERY_MIN_SECONDS`
    (1 s) is left when its turn comes, it isn't contacted and is reported as `timeout`, with text
    saying the listing's limit was reached.
  - A server that runs out its own 10 s keeps the old "took longer than 10 seconds" text.
  - Tool discovery moved into `_discover_listing_tools`, which also flattens the old nested try.
- **Why:** a listing gathered every server at once. With many servers, each page load opened that
  many connections, refreshed that many tokens, and could start that many local processes in
  parallel. Several users loading pages multiplied it.
- **Decisions:**
  - **One deadline for the whole listing.** A semaphore alone would stretch the listing to
    ceil(N/8) × 10 s.
  - **Store reads stay ungated.** The per-server reads (auth, OAuth app flag) still run in
    parallel; they're cheap, and only discovery is gated.
  - **The 1-second minimum** also came out of the tests: Windows timers fire up to ~15 ms early,
    so a server whose turn came at the deadline got a few milliseconds and was still contacted.
- **Not changed:**
  - The plan also listed "one instance load per request". I looked: no request path loads the
    instance list twice.
  - `get_instance_tools` makes one single-key read plus one full load, which namespacing needs.
  - The user-deletion event handler reads the user's own prefix twice, but the second read
    finds nothing; it isn't on a request path, so it's left as is.
- **Tests:** `test_timeouts_and_reuse.py::TestAListingDiscoversABoundedNumberOfServers`:
  - at most the limit at once, on both routes;
  - servers whose turn comes too late are reported as timed out and never contacted, and the
    listing returns within the deadline;
  - a server that's too slow by itself still gets the old text;
  - a deadline shorter than the minimum contacts nobody;
  - no discovery when tools aren't asked for.

  Three of these fail against the old code. The suites ran green three times in a row.

### 8 — SCALE-1: a directory is listed by the backend, not by reading every key
- **What:**
  - **`EncryptedKeyValueStore.list_keys_in_directory`.**
    - A directory ending in `/` goes to the backend's own prefix listing: Redis `SCAN MATCH`, or
      etcd `get_prefix(keys_only=True)`.
    - The root (`""` or `/`) still lists every key.
    - A prefix without the trailing slash is still matched as given against every key, so
      `/a/b` keeps finding `/a/b-2/...`; a behaviour test pins this.
    - The key-name "decryption" is gone.
  - **The etcd store's `list_keys_in_directory` had a bug.** etcd3's `get_prefix` yields
    `(value, metadata)`, and the code decoded the value as the key name. It now reads
    `metadata.key` and asks for keys only. Nothing used it until now: the encrypted store
    always went through `get_all_keys`, and the unit test mocked the wrong shape.
  - **The Redis store** escapes `* ? [ ] \` in the path before building the `SCAN` pattern, so a
    literal one in a key path can't act as a wildcard.
- **Why:**
  - Every directory listing read every key in the store and filtered it in Python, then tried
    to decrypt each name.
  - Listings are frequent: credential sweeps, OAuth state sweeps, instance loads, toolset
    counts. So their cost grew with the whole store, not with the directory.
- **Decisions:**
  - **Key names are never encrypted.** I checked before dropping the decryption:
    - Python's `create_key` stores the key as given in both encrypted-store classes;
    - Node lists by prefix on plain names;
    - git history shows the decryption branch arrived speculatively with toolsets.

    A name that only looks like ciphertext is now returned as stored. Even before, a decrypted
    name pointed at no readable key.
  - **Behaviour kept:**
    - The root and slash-less prefixes keep the full scan, so no caller's results change.
    - Every current caller passes a slash-terminated directory.
- **Tests:**
  - `test_encrypted_store.py::TestListKeysInDirectory`, replacing the decryption-pinned tests
    in it and in `_extended` / `_coverage`: a directory goes to the backend's prefix scan; the
    root lists everything; slash-less prefixes are matched as given; names are never
    decrypted; failures are raised.
  - `test_encrypted_store_behaviour.py`, against fake Redis and etcd:
    - listing a directory doesn't read every key (no etcd `get_all`, and Redis scans only the
      directory pattern);
    - etcd lists names, not values.

    The fake etcd client gained a `get_prefix` with the real `(value, metadata)` shape.
  - `test_etcd3_store.py` uses the real shape and expects `keys_only=True`.
  - `test_redis_store.py`: glob characters in a path are matched literally.
  - 7 of these fail against the old code; 4,093 config, MCP, toolset and connector-core tests
    pass.

### 9 — UX-5: the builder's MCP node shows the live server
- **What:**
  - **`mcp-live-servers.ts`.**
    - `McpLiveServersContext` holds the MCP server list the builder loaded, or `null` while
      there's no list to trust (loading, or the load failed).
    - `mcpLiveView` turns one node's instance into one of:
      - `unknown`;
      - `unavailable`, meaning not in the list;
      - `live`, with its `mcpConnectionState`, plus its tools when it's ready.
    - `toolsMissingFromServer` names the saved tools the server no longer offers.
  - **`McpFlowNode` reads the context.**
    - The header shows a red "Not available" badge, with a hint, for a server missing from the
      list. A server that isn't ready shows its state (`McpStatusBadge`): not connected,
      reconnect needed, slow, unreachable, and so on.
    - With "All tools" off, a saved tool the server no longer offers gets a "Not on the server"
      badge. Removing it uses the existing remove button.
    - **+** offers the server's current tools, so tools added since the agent was saved show up.
    - With "All tools" on, the node lists what the server offers now. Turning "All tools" on
      selects the live tools, and removing one keeps the live rest.
    - When the server can't be listed, the node falls back to the saved `availableTools`, as
      before.
  - **`useAgentBuilderData`** returns `mcpServersLoaded`. A failed load no longer looks like
    "no servers": that would have marked every attached server unavailable.
  - **i18n:** `agentBuilder.mcpServerUnavailable`, `mcpServerUnavailableHint`,
    `mcpToolNotOnServer` and `mcpToolNotOnServerHint`, in all 9 locales.
- **Why:**
  - Reconstruction set `availableTools` to the saved tools and `isAuthenticated: true`. So the
    node never showed tools the server added, never flagged tools it dropped, and looked fine
    for a server that was gone or signed out.
- **Decisions:**
  - **A context, not node data.** Dirty tracking compares serialized node data with the loaded
    snapshot. Merging live data into nodes would have made every freshly opened agent look
    edited, or needed a second snapshot. A test pins that reading the live server calls no
    `setNodes`, and another that save stays disabled after the live list arrives.
  - **The hint for a missing server** says "deleted, or it isn't shared with you". A co-editor
    of a shared agent doesn't see its owner's personal server in their own list, so "deleted"
    alone would be wrong.
  - **`isAdmin` isn't passed to the badge.** A shared credential that's missing shows "waiting
    for an administrator" even to an administrator. The Workspace page shows the admin
    wording.
- **Tests:**
  - `mcp-live-servers.test.ts`: every view, and the missing-tools set.
  - `mcp-tool-selection.test.tsx` ("McpFlowNode with the live server"):
    - flags a dropped tool;
    - offers an added tool;
    - "All tools" shows the live tools, and turning it on takes them;
    - marks an unavailable server, and shows a not-connected state while keeping the saved
      tools;
    - with no list, shows nothing extra;
    - reading the live server changes nothing on the canvas.
  - `agent-builder.test.tsx` ("an agent's MCP servers"): live tools don't make the agent dirty;
    a server missing from the list is marked; a failed load marks nothing.
  - 127 agent tests pass; tsc shows only the known two errors; ESLint and i18n parity are clean.

### 10 — Wrap-up: release notes, smoke checklist, review

**Release notes (round 2): behaviour people will notice.**
- **OAuth sign-in for MCP servers:**
  - **Redirect URI.** It is always the configured public frontend address
    (`endpoints.frontend.publicEndpoint`, sub-path included) plus `/mcp-servers/oauth/callback/`,
    never the browser's address. An administrator whose static OAuth app was registered with
    another address must update it at the provider. The server form shows the exact value.
  - **A static OAuth app** set up by an administrator is now used for new sign-ins even when a
    dynamically registered client exists. Existing connections keep refreshing with the client
    that issued them.
  - **Changing the public address** registers a new dynamic client at the next sign-in.
    Existing connections keep working.
  - **When a provider rejects a dynamically registered client,** the next sign-in registers a
    new one. Other people's connections aren't signed out by that.
  - **Sign-in state** is stored under its hash. A sign-in started before an upgrade still
    completes. During a rolling upgrade, one started on an upgraded server and finished on an
    old one fails, and the user signs in again.
- **Sign-in to an OAuth MCP server** fails with 503 ("The server's public address couldn't be
  read") when the configuration store doesn't answer, instead of using a default address.
- **Creating an MCP server** answers 503 ("We couldn't confirm your role right now, so nothing
  was created.") when the role service can't confirm the caller's role. It used to quietly
  create a personal server.
- **Agents:**
  - **Attachment limits.** An agent can have up to 50 MCP servers and up to 500 tools per
    server. Names, descriptions and header values are length-capped, and control characters
    are refused.
  - **Shared admin credential.** "Use a shared admin credential" is accepted only for API-token
    and header auth. Header names that HTTP or MCP use themselves (`Host`, `Content-Length`, `Mcp-Session-Id`, …)
    are refused as credential headers.
  - **A failed MCP read.** If the agent's MCP servers can't be read, the builder says so,
    saving leaves them as they are, MCP servers can't be added until the page is reloaded,
    and sharing the agent is refused with 503 until they can be checked.
  - **"All tools" on a large server.** A server with more than 500 tools can be attached with
    "All tools" on; the saved list shows the first 500.
  - **The builder's MCP node** shows the server's live state: a status badge, "Not available"
    for a server that's gone, "Not on the server" for tools it dropped, and tools it added in
    the **+** menu.
- **Chats:**
  - **Stop** interrupts a running MCP tool call. The server may still finish it, because mcp
    1.x doesn't send cancellation.
  - **A dead MCP session** is replaced mid-turn. A call that may already have reached the server
    is reported as interrupted, not sent again.
- **Server listings** (Workspace and builder) discover at most 8 servers at once and stop
  waiting after 15 seconds. Servers not reached in time show as slow; chats still wait for them.
- **Logs:**
  - The OAuth `code` and `state` are redacted from the Node access log, the MCP proxy's debug
    log and the Python services' uvicorn access log.
  - An expected MCP tool failure (timeout, unreachable, expired sign-in) is logged on one line,
    without a stack trace or the request URL's query.
- **Configuration store:**
  - Directory listings use the backend's prefix scan instead of reading every key.
  - The etcd store's own directory listing returned values instead of key names; that's fixed.

**Smoke checklist with real providers** (unit tests can't cover these):
1. **A DCR server** (e.g. Notion or Linear remote MCP):
   - connect, chat with a tool;
   - force a token refresh (`POST /instances/{id}/oauth/refresh`);
   - check the credential record's `oauthTokens.clientId` matches `/services/mcp/dcr-clients/{id}`.
2. **Change `publicEndpoint`,** then sign in as a second user. Check that:
   - a new client is registered;
   - the record has `previousClient`;
   - the first user's connection still refreshes.
3. **A static OAuth app** (e.g. GitHub OAuth app):
   - the admin form's redirect URI matches what the provider sends back;
   - sign-in works;
   - adding the app while a DCR client exists moves new sign-ins to it.
4. **A sub-path deployment** (`publicEndpoint` like `https://host/pipeshub`): the callback lands
   and completes.
5. **Rolling upgrade:** start a sign-in on the old build and finish it on the new one.
6. **Both stores:**
   - Workspace → MCP servers and the builder list correctly on etcd and on Redis;
   - the MCP refresh service's credential sweep and the toolset user counts still work.
7. **More than 8 servers,** including a slow STDIO one: the page loads within ~15 s and the slow
   ones are flagged.
8. **The builder:**
   - an agent whose server was deleted shows "Not available";
   - a server that gained a tool offers it under **+**;
   - opening the agent doesn't enable Save.
9. **Stop** during a long MCP tool call ends the turn promptly with the "Stopped before …
   answered" message.
10. **Restart a remote MCP server** mid-chat: the next call reconnects. A call in flight when it
    died reports "interrupted".
11. **On default Neo4j** (no explicit transactions) and on Arango: replace an agent's MCP
    servers and tools, and confirm the saved agent shows exactly the new set.

**Review pass.** An independent review of all 11 round-2 commits found ten issues. Nine are
fixed in the review-fix commit; one (#7) is recorded as a known limitation.
1. **AUTH-7: a store hiccup could sign out every user of a shared client.** `get_config`
   answers a failed read with the default, so the public address read as
   `http://localhost:3001`. Every shared client then looked registered for the wrong address
   and was replaced, and a second sign-in dropped the client the users' tokens came from.
   - **Fix:** the address is read with `raise_on_error=True`, and sign-in fails with 503
     instead.
   - The admin form's discovery probe and OAuth-config view just leave `redirectUri` out
     (`null`) when the address can't be read; the frontend type allows that.
2. **SEC-7: sharing could skip the personal-server check.** It could when the agent's MCP list
   was flagged unreadable, or when the store failed during the lookup.
   - **Fix:** sharing is refused with 503 in both cases.
   - `find_personal_instance_for_admin(..., raise_on_error=True)` raises instead of
     answering "none".
3. **SEC-8: the 500-tool cap applied with "All tools" on.** A bigger server couldn't be
   attached at all. Now the saved list (only a snapshot) is capped at 500 instead.
4. **RUN-6: Stop could cancel a token refresh or a connect halfway.**
   - `refresh_credential_record` now runs the refresh under `asyncio.shield`. A rotated refresh
     token is saved even when the caller gives up, whether through Stop or a listing deadline.
   - `MCPClientManager.open()` closes a half-started client on cancellation, so a local server
     isn't orphaned.
5. **SEC-11: uvicorn's access log still recorded the callback's code and state.**
   `AccessLogQueryRedactionFilter` now runs `redact_query_values` (new in `url_redaction.py`,
   the same parameter set as Node's) over every access-log path.
6. **RUN-4: any HTTP 404 counted as a forgotten session.** mcp 1.30 reports any 404 as
   "Session terminated", so a stateless server's 404 after a run would have been resent.
   - **Fix:** `request_never_reached_server(..., carried_session_id=)` counts the 404 only
     when the request carried a session id.
   - `MCPClientManager.session_id` reads the transport's session id.
7. **Known limitation: a rejection can overwrite a newly registered client.**
   `retire_rejected_dcr_client` reads the shared record and writes it back. The Python store
   has no compare-and-set, so a replacement landing in that one-round-trip window is lost.
   - It heals itself: the users signed in through the lost client reconnect once, and the next
     sign-in sees the rejection and registers again.
   - The replacement path has the same window.
8. **Rolling deploy: one state could be claimed twice.** A state found under the unhashed key
   is now claimed under the unhashed claim key, where an older server claims it, so the two
   can't both use it. The other direction, a sign-in started on a new server and called back on
   an old one, fails with `invalid_state`; it's rare during a rollout, and the user signs in
   again.
9. **The builder silently dropped MCP servers added while the list was unreadable.** Such a
   save leaves `mcpServers` out, so a server dropped then was lost without a word. MCP drops
   are now refused with a message while the agent's list couldn't be read
   (`agentBuilder.mcpServersUnavailableNoDrop`, in 9 locales).
10. **DATA-2: detach-all could leave orphans.** On auto-commit Neo4j, if every old server had
    been unlinked and removing the old nodes then failed, the old server and tool nodes were
    left behind. That happened because `_finish_unlinking_old` required an old link to remain.
    It now counts a removed link that stays gone as the save persisting.
    - The same edge case is in `_finish_removing_old_knowledge`, which came from main
      (b1916976d, another author), and in #3949's copy of the helper. Both are left as they are
      here.

**Review-fix tests:**
- unreadable public address (503, nothing registered or replaced);
- unreadable MCP list and store failure (sharing refused), plus a non-sharing edit that still
  saves;
- "All tools" above the cap;
- refresh outlives a cancelled caller, and a caller that stays still sees the failure;
- cancelled connect closes the client; `session_id`;
- stateless 404 not resent, and the error helper with and without a session id;
- `redact_query_values`; the access-log filter, and that it's installed;
- detach-all then failure;
- claims for unhashed and hashed states;
- MCP drop refused while flagged.

Eight of the behavioural tests fail against the pre-fix code. The final regression ran
13,696 Python tests with only the known artifact-pipeline failure, plus 228 frontend tests.

**Also noted:** on Redis, `SCAN MATCH` still walks the whole keyspace server-side. SCALE-1's
gain there is in what crosses the wire and gets decoded; etcd's `get_prefix` is a real range
read.

## Round 3 (2026-10-07): the protocol move, the tool cache, approvals and Slack

Tushar's go-ahead on all four, with these decisions:
- **Slack.** Use Slack's own hosted server. Existing community Slack servers keep running, with a
  notice, and no new ones can be made.
- **Protocol.** Move to `mcp` 2.x directly, without fastmcp (the Phase 1 plan).
- **Tool cache.**
  - Lists are kept per sign-in context, and identical lists are stored once.
  - Lists expire after 24 hours, and `MCP_TOOL_CACHE_TTL_SECONDS=0` turns the cache off.
  - A server that's down still offers its saved tools, and calls to it fail with a readable
    message.
  - A server's `cacheScope: public` is trusted only for org servers an admin set up.
  - It's built after the protocol move. Saving web search results isn't included.
- **Approvals:**
  - **Rules:** Allow / Ask / Block per tool. Starting rules: read-only tools are Allow, everything
    else is Ask.
  - **Asking:** the turn ends and the approval arrives as the next message. The exact call is
    saved server-side for 15 minutes and can be used once.
  - **The card:** Allow once, Allow for this chat, Deny, and Always allow for whoever can edit the
    agent.
  - **Scope:** MCP tools only for now, enforced at the tool executor's one checkpoint so toolsets
    and web tools can join later.
  - **Company rules** (admin): none / Always ask / Block, plus "Allowed when nobody is watching".
    They are a floor nobody goes below, and the strictest rule wins.
  - **Personal rules** on the Workspace server card apply to that person's assistant chats only.
  - **Unattended runs:** Ask counts as Block unless the admin allowed the tool unattended.

Order: Slack → protocol move → tool cache → approvals.

### R3-1 — Slack's own hosted server replaces the archived community package
- **What:**
  - **The new entry, `slack_official`:**
    - it connects to `https://mcp.slack.com/mcp` over Streamable HTTP, and sign-in is OAuth only;
    - authorization goes to `https://slack.com/oauth/v2_user/authorize`, and tokens come from
      `https://slack.com/api/oauth.v2.user.access`;
    - there are no default scopes, so sign-in asks for the ones the server publishes;
    - `supports_dcr=False`, and the documentation link is Slack's own setup guide.
  - **The old entry, `slack`:**
    - it's now named "Slack (community)";
    - it carries a new template field, `replaced_by="slack_official"`.
  - **Creating a server** from a replaced entry is refused with 400 ("… is no longer offered for
    new servers. Add Slack instead."). That covers both admin and personal servers, and switching
    an existing server to such a type.
  - **Existing servers** of the old type keep running and can still be edited.
  - **The catalog** still returns the replaced entry, with `replacedBy`, because existing servers'
    pages look their entry up in it. The OpenAPI schema gets the field.
  - **Frontend:**
    - the admin catalog shows a replaced entry only where servers made from it exist;
    - there, it carries "No longer maintained. Add Slack instead." and no "+";
    - the type's detail page shows the notice and has no "Add" button;
    - the personal "Add server" list leaves replaced entries out.

    The new text is in all 9 locales, and `catalog-replacement.ts` holds the rule.
- **Why:** `@modelcontextprotocol/server-slack` is archived upstream and npm marks it unsupported.
  Pinning it (SEC-12) stopped a bad release from sneaking in, but it gets no fixes.
- **Checked live, not just from docs:**
  - Slack's protected-resource and authorization-server metadata (scopes, endpoints,
    `client_secret_post`, S256 PKCE, `refresh_token` grant, no registration endpoint);
  - the 401 `WWW-Authenticate` pointing at that metadata;
  - the token response, where `access_token` is top-level and `token_type` is "user". We always
    send `Bearer`, so the type doesn't matter.

  Tokens without expiry are left alone (AUTH-4), and rotated ones refresh normally.
- **What an admin must do** (Slack's rule, not ours):
  1. Create an **internal** Slack app.
  2. Turn on its "Model Context Protocol" setting and PKCE.
  3. Add a bot user with one bot scope.
  4. Add the user scopes and our redirect URI, which the form shows.
  5. Enter the app's client ID and secret on the server.

  Slack allows MCP only for internal or Marketplace apps.
- **Decisions:**
  - **A new type id instead of changing `slack` in place.** Catalog servers launch from their
    template, so changing it would have broken every existing Slack server at upgrade, and their
    bot-token credentials can't become OAuth sign-ins. With a new id, nothing breaks, and admins
    move when ready.
  - **Setup steps come from the documentation link.** The OAuth form shows no template help text;
    `authHint` is used only for API-token labels.
- **Tests:** `test_mcp_servers_replaced_catalog.py`:
  - every replacement exists and is itself offered;
  - the new entry's sign-in setup;
  - the catalog still lists the replaced entry;
  - creating from it is refused, while an existing one can be edited and another server can't be
    switched to it;
  - the official entry can be added.

  `catalog-replacement.test.tsx`: the helpers; hidden when unused; shown with the notice and no
  "+" where used; "+" kept for an entry still offered; left out of the personal list. The handler
  tests' fake templates now set `replaced_by=None`.

### R3-2 — groundwork for the tool cache: a key can be overwritten with an expiry
- **What:** `ConfigurationService.set_config(..., ttl_seconds=None)` now passes an expiry through
  to the store, and only when one is asked for, so every existing call reaches the store exactly
  as before. The etcd store's overwrite path attaches a lease when a TTL is given: a plain `put`
  on an existing key used to leave it with no expiry at all.
- **Why:** the tool cache rewrites its entries with an expiry; on etcd those rewrites would never
  have expired. Redis (`SET … EX`) and the in-memory store already behaved.
- **Tests:** `test_etcd3_store.py` (overwrite with a TTL attaches a lease; without one it's a plain
  put); `test_configuration_service.py` (the TTL reaches the store).

### R3-3 — found on the way: "All tools" was lost on every agent save through Node
- **What:** the Node API's `agentMcpServerSchema` gains `allTools: z.boolean().optional()`, and
  its per-server tool bound goes from 200 to 5000. The bound now only limits payload size; Python
  allows 500 chosen tools and, with `allTools`, keeps the first 500 of however many there are.
- **Why:**
  - The validation middleware keeps unknown keys only on the outer request object. The nested
    agent body is parsed with Zod's default "strip", and the parsed body replaces the request
    before it is forwarded to Python. So `allTools` never reached Python:
    - "All tools" quietly became the fixed list the builder sent;
    - a server with "All tools" on and no tools listed was refused by Python's "choose at least
      one tool" check.
  - The 200-tool bound also refused large servers before Python's own, clearer limit applied.
  - Found while mapping the code for approvals; the round-1 frontend and Python tests
    couldn't see it, because they never go through Node.
- **Tests:** `es_validators.test.ts`:
  - `allTools` kept on create and on update;
  - an all-tools server with 600 tools accepted.

  All three fail against the old schema.

### R3-4 — protocol move, step A: everything around the client reads both SDK shapes
The move to `mcp` 2.x is two commits. This first one is green on the installed fastmcp/mcp 1.x;
the second swaps the package and rewrites `client.py` on `mcp.Client`.
- **What:**
  - New `app/agents/mcp/wire.py`: a per-operation record (every response status, whether a
    session id was sent, and the first cause of a request that never left), kept in a ContextVar.
  - `url_guard.py`: the guard is one implementation built for httpx and for httpx2 (the SDK's
    HTTP library). The httpx2 build answers a request that never reached the server (blocked
    origin or address, connect failure on every address, proxy error) with a stand-in 502 and
    records the cause. New `guarded_mcp_http_client` and `guarded_mcp_http_client_factory`
    (for SSE) build httpx2 clients with the guard, the SDK's default timeouts, `trust_env=False`
    and the recording hook.
  - Both builds verify certificates with `httpx.create_ssl_context()`.
  - `errors.py`: new `MCPHttpStatusError(status_code)`; a 401 is recognised from httpx2 and from
    the restored status.
  - `failure.py`, `mcp_tool_adapter.py`, `client.py`: httpx2 errors classify and read like httpx
    ones.
  - `mcp_result.py` and `discovery.py` read the 2.x snake_case names (`mime_type`,
    `input_schema`) as well as the 1.x ones.
- **Why:**
  - The 2.x SDK reports HTTP errors without their status, and its JSON-RPC codes don't say what
    happened (servers send their own; -32001 is both the SDK's timeout and many servers'
    "session not found"). The status is recorded where the request is made, and failures are
    read from that record.
  - The SDK runs each request in a copy of the caller's context, so a ContextVar set around an
    operation is seen by the guard and the hook.
  - A raw exception from the transport ends the SDK's whole session. A stand-in response doesn't.
    Only failures before anything was sent are turned into one: a timeout after the request went
    out may have been acted on, so it stays an error.
  - httpx2's `verify=True` means the OS trust store. Using httpx's context keeps certifi and
    `SSL_CERT_FILE` / `SSL_CERT_DIR` working as they do for every other outbound call here.
- **Tests:**
  - `test_url_guard_httpx2.py`:
    - pinning, the stand-in 502 for another origin and for loopback, failover across addresses
      before recording a connect failure, and a read timeout left as an error;
    - the client records statuses and a sent session id, and its timeouts, headers, auth and SSE
      factory;
    - certificate settings for both libraries, with `SSL_CERT_FILE` honoured;
    - the record itself.
  - `test_sdk2_shapes.py`: the restored status, 401 detection, failure reasons, what the model is
    told, and snake_case results and tools.

### R3-5 — protocol move, step B: the client runs on mcp 2.3.0, without fastmcp
- **What:**
  - `pyproject.toml`: `mcp==2.3.0`; fastmcp is gone.
  - `client.py` is rewritten on `mcp.Client`:
    - Every session runs in a task of its own (`_SessionRunner`). The connect is bounded by
      that task's cancel scope, and closing goes through the scope too.
    - Every listing and call is recorded on the wire, and its failure is read from its own
      requests (`_wire_failure`, `_operation_error`):
      - never sent → `MCPRequestNotSentError`;
      - lost after it went out → `MCPRequestLostError` (or a timeout);
      - a 404 on a session id → `MCPSessionExpiredError`;
      - another error status → `MCPHttpStatusError`;
      - the SDK's own timeout → `TimeoutError`;
      - the SDK's own "Connection closed" → `MCPConnectionLostError`.

      A server's own JSON-RPC error is raised as it is.
    - A call gets the SDK's read timeout (the call timeout) and our own clock 15 s later.
    - `update_headers()` puts a refreshed token into the open session.
    - The client the SDK is given is closed by us; the SDK only closes clients it made.
    - The SDK's response cache is off.
    - Lenient output-schema checks are kept: the hook is now the public `validate_tool_result`.
  - `errors.py`:
    - New `MCPRequestNotSentError`, `MCPRequestLostError`, `MCPConnectionLostError` and
      `MCPSessionExpiredError`.
    - `request_never_reached_server()` and `request_may_have_run()` replace the 1.x code and
      message matching.
    - `CALL_ID_META_KEY`, `http_status_error()` and `http_error_call_id()` are gone.
  - `mcp_session.py`:
    - What can't have run is sent again: never sent, or the session was refused.
    - What may have run is reported as interrupted. The session is replaced only when it is
      gone.
    - A 401 refreshes the token. An open session takes the fresh token in place; one the 401
      ended is replaced. The token the failed request carried goes to
      `refresh_credential_record`, so concurrent 401s share one refresh.
  - `url_guard.py`:
    - The httpx2 guard's stand-in 502 now also covers a request whose connection failed after
      it went out. It is recorded as lost, not as never sent.
    - The SSL context is built once per certificate setting.
    - fastmcp's `guarded_http_client` factory is gone.
  - `wire.py`: `WireRecord.lost`; the error status is the operation's last status.
  - `url_redaction.redact_urls_in_text()` is shared by the client and the tool adapter.
- **Decisions that differ from plan v2, and why:**
  - **Only "speaks only initialize" is remembered** (per instance and URL, one hour), not a
    modern server's version.
    - A pinned modern version adopts the remembered discover result without contacting the
      server. That would move a 401 or a dead server from the connect to the first listing,
      and a stale pin wouldn't show at connect.
    - A modern server's probe is its handshake anyway, so remembering it saves nothing.
    - For an initialize-only server, the probe is a wasted round trip, and a server that ignores
      it costs up to 10 s of the connect budget. If a remembered handshake fails for any reason
      other than the server being down, blocked or refusing the credentials, it is probed again
      once.
  - **The token goes into the client's headers** instead of an `httpx2.Auth` object. It has
    the same effect, with nothing extra on the request path.
  - **A request lost after it went out gets a stand-in too.** The plan left those ending the
    session. But mcp 2.x cancels the request in flight only on modern connections. On a legacy
    HTTP server, a call that timed out keeps its POST open until the read timeout (at least
    300 s). If that timeout were raised, it would end the session, and every call on it, minutes
    later.
  - **The SSL context is shared.** Loading the certificate bundle took about 0.2 s, and it ran
    on the event loop for every new session.
  - **No call id in `_meta`.** It existed to tell whose 401 a failure was. Now each call's
    record holds only its own requests, because the SDK sends each request in its caller's
    context. `session_id` is gone for the same reason.
  - **The error status is the last one.** A legacy server's refusal of the version probe (400)
    followed by a good handshake isn't the failure. A connect timeout is reported as a timeout,
    even when the probe left a status behind.
- **Known limits:**
  - **SSE (legacy):** a failed POST ends the transport's writer without a word, so the call
    only fails when its timeout runs out. The session is then marked gone, and the next call
    reconnects.
  - **A failure while a JSON response body is being read** (after the transport returned)
    still ends the session. It is reported as a lost connection, and the turn's next call
    reconnects.
- **Security:** on the 2.x line, `mcp` 2.2.0 fixes the three client advisories the 1.30.0
  floor was for (GHSA-rwrf-2pqf-9j8j, GHSA-5h93-6whr-6q8j, GHSA-qx49-fqc8-xw99). The SDK
  test pins `>=2.2.0,<3`.
- **Tests:**
  - `test_client.py` (rewritten, against a stand-in for `Client` that writes the wire record
    as the guard and the hook do):
    - transports, timeouts, the session task, instructions and stderr handling;
    - every way a connect and a call can fail;
    - the remembered handshake;
    - each call reading only its own requests, closing, and lenient schema checks.
  - `test_client_e2e.py` (new): the SDK's own server over streamable HTTP and SSE under
    uvicorn, and as a local process. An ASGI gate plays a refused token, an initialize-only
    server, a forgotten session and a redirect elsewhere. Covered:
    - listing and calls, and concurrent calls;
    - a 401 at connect and mid-turn, then the fresh token on the same session;
    - an expired session, and a server that isn't there;
    - a blocked redirect;
    - a timed-out call that tells the server (`notifications/cancelled`) and keeps the session;
    - closing during a call;
    - a local server that crashes mid-call, or fails to start (its stderr kept apart).
  - `test_mcp_session.py` (rewritten):
    - what's sent again and what's reported;
    - a 401 refreshing into the open session or a new one, with concurrent 401s sharing one
      refresh;
    - discovery retries.
  - `test_url_guard_httpx2.py`: lost versus never sent, the shared SSL context, and the error
    status. `test_timeouts_and_reuse.py`, `test_mcp_tool_loader.py`, `test_url_guard.py` and
    the STDIO-policy test follow the new client.
- **Independent review, then fixes** (each checked against the installed SDK source first):
  - **A call could be sent twice.**
    - A resumable server drops a long call's stream on purpose (SEP-1699), and the SDK
      resumes it with a GET inside the call's own request task. So that GET lands in the call's
      record.
    - If the GET couldn't connect, got a 401, or got a 404 on the session, the call was read
      as never sent, as its own 401, or as an expired session, and sent again. But the call
      itself had gone through.
    - Fix: `WireRecord.accepted`. Once the server took any request of a call, a later failure
      means the call may have run (`MCPRequestLostError`). Listings are exempt: they change
      nothing, so they are safe to repeat.
    - The SDK's "SSE stream ended and reconnection attempts were exhausted" now counts as a
      lost request, not a server error.
  - **A connection that ended between calls** (a local server that exited, an SSE stream that
    ended) left the session looking usable.
    - The `Client` stays entered; only the SDK's dispatcher knows the connection is gone. So
      the next call failed before it was written, and was reported as possibly run.
    - Now the session counts as unusable (`_connection_closed`, which reads the dispatcher's
      private `_closed`; a test pins it to the installed SDK), and the turn reconnects.
  - **A server's own error at a 4xx lost its message.**
    - 2.x servers send invalid params as HTTP 400 and an unknown method as 404, with the
      JSON-RPC error in the body. The client had replaced the error with "answered HTTP 400",
      so the model lost "'q' is required".
    - Now, except for 401/403, an error whose body the server sent is raised with its own
      message. A 404 counts as an expired session only when it isn't METHOD_NOT_FOUND.
  - **The handshake memory slid.** Every session that skipped the probe re-stamped it, so it
    never ran out while the server was in use. Only a probe's answer is remembered now.
  - **`aclose_all` closes every session at once.** Closing one at a time, each hung server
    added up to its close timeout.
  - Not changed: if a cancelled connect's runner outlives its close timeout, Windows can't
    delete the stderr temp file, and a stray temp file is left (harmless).
  - Tests:
    - a resumption failure (unsent, 401, 404) after the call was taken means it may have run;
    - reconnection attempts running out;
    - a server's own 400 and METHOD_NOT_FOUND keep their message;
    - a 401 with a body;
    - a listing refused on its second page is still a 401;
    - a dispatcher that saw its connection end, plus the SDK pin;
    - the memory not stretching;
    - concurrent closes, and a failing one;
    - E2E: a local server that exits between calls leaves the next call unsent.
- **Release notes (round 3): the protocol move.**
  - MCP servers are reached with the official `mcp` 2.x SDK; fastmcp is no longer installed.
    A development venv picks this up with `pip install -e .` (a fastmcp left behind is unused).
  - **Stop and timeouts reach the server.** A stopped or timed-out call now sends the server
    `notifications/cancelled`, so a server that honours it stops the work.
  - **Newer MCP servers** (protocol 2026-07-28) are spoken to in their own version; older ones
    exactly as before. An older HTTP server is remembered for an hour, so its sessions skip the
    version probe.
  - **Certificates** for MCP traffic are checked against certifi, or `SSL_CERT_FILE` /
    `SSL_CERT_DIR`, as for every other outbound request.
  - **A token refreshed mid-chat** goes into the open session; no reconnect.
  - **One failed request no longer ends the session** for the other calls on it.
  - **Errors:** a server's HTTP error at connect shows its status and the server's own message
    (the 404 hint is kept). A redirect to another address says to use that address as the
    server URL if it is the intended server.
  - **A server's own error at an HTTP error status** (2.x servers send invalid arguments as
    400) reaches the model with its own message, so it can correct the call.

### R3-6 — tool cache, step 1: a tool keeps the server's hints about it
- **What:** `MCPToolInfo.annotations` holds a tool's annotations with protocol names
  (`readOnlyHint`, `destructiveHint`, `idempotentHint`, `openWorldHint`, `title`), read by
  `tool_infos_from_listing` from 2.x objects and from dicts. It is None when the server sent
  none. Documented in the OpenAPI `McpToolInfo`.
- **Why:**
  - The cache (next steps) stores what a listing gave, so a cached turn has to carry them.
  - Approvals start read-only tools at Allow.
  - They are hints; a server can claim anything. So they only ever set a starting rule; an
    admin or the person decides.
- **Tests:** `test_sdk2_shapes.py`: a 2.x tool's hints keep protocol names; a dict tool's
  hints; none, empty or malformed hints are None.

### R3-7 — tool cache, step 2: the store and the cache module
- **Plan and review:** plan v2 (amending v1 for mcp 2.x) was reviewed independently. Plan v3
  takes the review's changes, below.
- **What:**
  - `ConfigurationService.get_config` / `set_config` get `keep_in_cache` (default True, so
    today's behaviour). The cache passes False.
  - New `app/agents/mcp/tool_cache.py`:
    - `/services/mcp/tool-catalogs/pointers/{instanceId}/{ownerKey}`: which catalog that
      sign-in's discovery produced, with the server target's fingerprint, the credential
      record's stamp and an expiry.
    - `/services/mcp/tool-catalogs/blobs/{sha256}`: the catalog (tools with their
      annotations, plus the server's instructions), stored once.
    - `read`, `write`, `forget`, `needs_refresh`, `digest`, `entry_ttl`.
- **Decisions (from the review), and why:**
  - **The owner key is `credential_owner_id`,** so a shared admin credential is one entry
    (`_shared`) for everyone, in chats and on the pages alike. Chat had keyed by the user.
  - **No org segment in the pointer path.** Instance ids are unique, and inherited instances
    cross orgs. Blobs are global: a blob is reached only through a pointer whose own discovery
    produced exactly those bytes.
  - **A credential stamp instead of forget hooks.** A pointer stores the `connectedAt` of the
    credential record it was discovered with (step 4 sets it where records are created). A
    reader compares it with the record it already holds.
    - So a reconnect, a new API token or new headers read as a miss.
    - A token refresh keeps the record, so it doesn't.
    - This replaces seven delete hooks, closes a race where a turn still on the old credential
      rewrote the pointer after a reconnect's delete, and covers paths the hooks would have
      missed (the lifecycle purges, the shared-credential move).
    - Records saved before this have no stamp; they match until their next save.
  - **Cache keys stay out of the process LRU.** That LRU keeps 1,000 entries whatever their
    size, and blobs go up to 512 KB.
  - **A blob is written only when it is missing, or older than a pointer could live.** It
    lives twice the TTL. So a fresh pointer never outlives its blob, and a turn doesn't pay an
    encrypted write plus a read-back each time.
  - **TTL:** ours (24 h, `MCP_TOOL_CACHE_TTL_SECONDS`, 0 = off), or the server's own `ttlMs`
    when it is shorter.
    - It is rounded up to whole seconds.
    - Under a second, nothing is cached, and what an earlier discovery left is dropped: a
      store TTL of 0 would mean no expiry.
  - **`_public`:** written instead of the owner's pointer when a modern server says
    `cacheScope: public` and the instance is an org instance. A personal instance's claim is
    ignored. It is read before the owner's own. A private result deletes it, so a server that
    turns private stops serving the public list.
  - **What's never cached:** an empty listing (a server still starting up), or one over
    512 KB.
  - **A blob that doesn't hash to its key** (corrupt) is a miss.
- **Tests:**
  - `test_tool_cache.py`:
    - what is kept: tools, hints and instructions, an order-free digest, empty and oversized
      lists;
    - who sees what: stored once, never another sign-in's, the shared credential;
    - misses: target, stamp, legacy stamp, expiry, corrupt blob, malformed pointer, missing
      blob;
    - TTLs: the default, a server's shorter hint, a server saying not to cache, the kill
      switch, pointer-only rewrites, a stale blob rewritten, refresh at half-life;
    - the public list: served to everyone, ignored for a personal server, dropped when the
      server turns private;
    - a store that fails;
    - forget.
  - `test_configuration_service.py`: a read or a write with `keep_in_cache=False` leaves the
    LRU alone and drops a stale copy.

### R3-8 — tool cache, step 3: chats take their tools from the cache
- **What:**
  - **Listings carry what the cache keeps.** `client.ToolListing` (tools, instructions, the
    server's `ttlMs` / `cacheScope`) comes from `MCPClientManager.fetch_tool_listing()` and
    `fetch_tool_listing_in_session()`. `discovery.discover_tool_listing()` returns it too.
    The hints count only on a 2026-07-28 session, and only when the server sent them; the
    protocol types default them to 0 and private.
  - **`MCPSessionManager.tools(server, namespace)`** returns the turn's tools and the server's
    instructions.
    - On a hit, from the cache, opening no session.
    - On a miss, listed on the session the calls will use, then remembered.
    - `MCPToolProvider` uses it, and instructions now come with the tools.
  - **On a hit, the server lists once before its first call.**
    - Parallel first calls share that listing (shielded, so Stop cancels only its caller).
      It runs outside the instance lock, because the token refresh it may need takes that
      lock too.
    - If the tools changed, the cache entry is rewritten; if it's past half its life, it is
      refreshed.
    - A tool the server no longer offers fails with `MCPToolNotOfferedError` and isn't sent.
    - If the listing fails (after the same one retry and one refresh discovery gets), the call
      fails with the listing's error when no session is left, and is sent anyway otherwise.
      Either way it is never reported as "may have run", because the listing wasn't the call.
    - A failed listing is tried again by the next call.
  - **`tools/list_changed`:** the client's message handler only notes it (it runs in the SDK's
    read loop, and also receives transport errors). `aclose_all` forgets the entry the turn
    used, so the next turn lists again. Only the notification handshake-era servers send on
    their own is seen; a modern server needs `subscriptions/listen`, and the first-call
    listing catches its changes anyway.
  - **A refreshed token keeps the rest of `server.auth`,** so the credential stamp survives.
  - **A listing timeout reads as one:** "didn't list its tools within 15 seconds"
    (`MCPListingTimeoutError`), not the call timeout.
  - `MCPSessionManager.instructions()` is gone; the loader gets instructions from `tools()`.
- **Tests:**
  - `test_mcp_session.py`:
    - a miss lists and the next turn opens no session;
    - a shared credential kept once;
    - the first call lists once for three parallel calls;
    - a dropped tool refused, then the cache updated;
    - an unchanged fresh entry not rewritten;
    - three parallel first calls whose listing gets a 401: one refresh, no hang;
    - an unreachable server fails the call as never sent and leaves the cache alone;
    - a failed listing on a live session still sends the call, and is tried again next call;
    - `list_changed` forgets the entry;
    - a refresh keeps `connectedAt`.
  - `test_client.py`:
    - a listing's instructions and hints, modern versus older sessions, and hints that
      weren't sent;
    - the message handler.
  - `test_sdk2_shapes.py`: what the model is told for a dropped tool and for a listing timeout.
  - The loader, timeout and reuse fakes follow `fetch_tool_listing_in_session`.

### R3-9 — tool cache, step 4: the server pages read it too
- **What:**
  - **Listings answer from the cache first.** `/my-mcp-servers` and `/agents/{key}` (through
    `_build_mcp_instance_entry`) use `discovery.cached_tools_for_owner`. A hit fills `tools`
    and `toolsCachedAt` (epoch ms) without connecting, and without taking one of the listing's
    eight discovery slots. A miss discovers live, as before.
  - **`discover_tools_for_owner` remembers what it finds.** It now goes through
    `discover_listing_for_owner`, so the listings and the Discover button
    (`/instances/{id}/tools`, always live) both write the same catalog a chat writes, with
    tools and instructions, so the digest matches.
  - **Credential records carry `connectedAt`,** set where they are created:
    `_build_credential_record` (user, shared and agent saves) and the OAuth callback. That's
    the stamp the cache checks.
  - **Frontend:** `McpMyServerEntry.toolsCachedAt` and `McpToolInfo.annotations` in the types.
    The personal server card's tool count shows "Tools as of …" on hover for cached tools
    (9 locales).
  - **Docs:** OpenAPI `toolsCachedAt`; `env.template` gets `MCP_TOOL_CACHE_TTL_SECONDS`.
- **Tests:**
  - New `test_mcp_servers_tool_cache.py`:
    - a live listing is remembered and the next one connects to nothing;
    - a reconnected sign-in lists live again;
    - the Discover button is always live and replaces the cached tools;
    - a failed discovery leaves the cached tools;
    - the kill switch lists live every time.
  - `test_mcp_servers.py`: both record builders set `connectedAt`.
  - `connection-state.test.tsx`: the card's "Tools as of …" appears for cached tools only.
  - The route test fake takes `ttl_seconds` / `keep_in_cache`. Tests that patched
    `discovery.discover_tools`, the client's `list_tools` or the session's `discover` now patch
    what the code calls (`discover_tool_listing`, `fetch_tool_listing`, `tools`).
- **Release notes (round 3): the tool cache.**
  - A chat no longer connects to every attached MCP server to learn its tools. They come from
    a cache kept per sign-in, for 24 hours by default (`MCP_TOOL_CACHE_TTL_SECONDS`; 0 turns
    it off). A server is contacted only when the model calls one of its tools, and it lists
    its tools once before that first call, which also updates the cache.
  - The Workspace and builder listings answer from the same cache. A server's card says when
    its tools were listed, and the Discover button always asks the server.
  - A server that is down still has its tools offered for the turn; calling one fails with the
    usual readable error. Before, the server dropped out of the turn.
  - A tool the server no longer offers fails with "The X MCP server no longer offers the tool
    Y", and isn't sent.
  - Reconnecting a server, or saving a new token or header, lists it again on the next use.
    A server that says its tools changed during a chat is listed again next time.
  - A server that declares its list the same for everyone (`cacheScope: public`, 2026-07-28
    servers) is listed once for all users, when an admin set it up.

### R3-10 — approvals, step 1: a call can wait for a person's approval on a later turn
- **Plan and review:** plan v1 was reviewed independently; plan v2 takes the review's changes.
  The main one: the approved call is **run on the server** at the start of the next turn,
  with the saved arguments, instead of asking the model to repeat it. Why:
  - deep-mode children regenerate arguments;
  - history cuts long argument values;
  - argument validation coerces values after the gate.

  So a repeated call wouldn't match the one approved.
- **What (agent_loop_lib, generic):**
  - `PendingApproval(message, end_turn, details)`, and `ToolCallContext.ask_later(pending)`
    (an ASK that is answered later; it never overrides a DENY).
  - `ToolExecutor.call_tool(on_pending=...)`: the call isn't run, `on_pending` gets it, and
    the result carries the message. Without `on_pending` it's an ordinary ASK.
  - The tool loop emits TOOL_BLOCKED with the new `ToolCallStatus.AWAITING_APPROVAL` and the
    details as `approval`. With `end_turn`, the turn ends like a terminal tool, with the
    message as the output.
  - The AG-UI emitter sends status `awaiting_approval` with `approval` on TOOL_CALL_RESULT. The
    transcript collector keeps both on the tool part, so the card can be drawn from the saved
    conversation after a reload. The legacy SSE emitter reports it as an unrun call.
- **Why here:** the executor's PreToolUse checkpoint is the one place every tool passes, so
  toolsets and web tools can use the same mechanism later. HIL, which blocks and waits, needs
  a long-lived process; PipesHub's web workers are request-per-turn (see `clarification.py`).
- **Tests:**
  - `test_pending_approval.py`:
    - the executor doesn't run the call and hands it to `on_pending`;
    - without `on_pending` it is an ordinary ask;
    - a denied call doesn't become pending;
    - an agent run ends after the call, with the message and an `awaiting_approval` event;
    - a pending call that doesn't end the turn lets the model go on.
  - `test_agui_emitter.py`: the status and `approval`, and a blocked call carries none.
  - `test_transcript_collector.py`: the part keeps both.
  - `test_sse_bridge.py`: the legacy status.

### R3-11 — approvals, step 2: the rules, the gate, and answering a card
- **What:** new `app/agents/agent_loop/tool_approvals.py`.
  - **The decision (`decide`):** effective = max(own rule or the starting rule, company rule),
    ordered allow < ask < block.
    - The starting rule is Allow when the server marks the tool read-only, otherwise Ask.
    - Ask becomes Allow for the approved call, or for a chat grant when the company rule isn't
      "Always ask".
    - With nobody watching, Ask is Allow if the company allowed the tool unattended, else
      Block.
  - **Whose rules:**
    - an agent chat uses the agent's (`agent-tool-rules/{agentKey}/{instanceId}`);
    - an assistant chat (the placeholder agent) uses the person's
      (`user-tool-rules/{userId}/{instanceId}`);
    - company rules sit at `tool-policies/{instanceId}`;
    - chat grants at `tool-approvals/chat-grants/{conversationId}/{instanceId}` (30 days).
  - **Watched** = `client-name: pipeshub-ai`, a streaming request, a conversation id, and not a
    service account. Slack sets `client-name: slack`; the API and the MCP gateway can't show a
    card.
  - **The gate** (PreToolUse, registered last):
    - an MCP tool is found by its path (`/mcp/{instanceId}/{tool}`), always by instance id
      and the server's own tool name, never by the per-request namespaced name;
    - Block denies with a reason the model can relay;
    - Ask saves the exact call (`tool-approvals/pending/{id}`, 15 min, bound to org, user and
      conversation) and leaves it pending (`ask_later`) with the card's details;
    - one pending approval per request; later asks in the run are denied with "another action
      is already waiting".
  - **Answering** (PRE_TURN, first turn of the root run): the next message's `toolApproval`
    claims the saved call (single-use), records the choice, and, unless the choice was deny,
    runs the saved call through the executor.
    - Every PreToolUse check runs again, so a Block set since the card was shown still stops
      it.
    - The call is shown on the activity row like any other.
    - What happened is added to the goal, so the model carries on from the result.
    - "Always allow" from someone who can't edit the agent covers this chat only.
  - **The asking turn's answer:** the approval message is added after what the model wrote,
    with `answerMatchType: "Approval Needed"`, and it doesn't count as an answer in the
    metrics. An approval turn never stops for clarifying questions.
  - **Plumbing:**
    - `ChatQuery.toolApproval` → `query_info` (with `agentKey`, `isAssistantChat`,
      `canEditAgent`, `chatStreaming`) → chat state → `AgentContext`;
    - Node: `contextFieldsSchema.toolApproval`, and `buildAiChatRequest` forwards it on both
      agent paths;
    - history replay counts `blocked` / `awaiting_approval` calls as not run;
    - `IMessagePart.status` and `approval` types.
  - `MCPToolAdapter` gets `instance_id`, `raw_name`, `read_only`, `server` and `tool_info`.
    The loader records adapters by path for the gate.
  - `MCP_TOOL_APPROVALS=false` turns it all off.
- **Why the call runs on the server:** see R3-10. The model can't be trusted to repeat it
  exactly.
- **Tests:**
  - `test_tool_approvals.py`:
    - every `decide` case;
    - who is watching, and whose rules apply;
    - the gate's outcomes: allow, ask with the saved call and details, one per request,
      unattended blocked or allowed, company and agent blocks, personal rules in assistant
      chats only, a call that can't be saved, the kill switch;
    - claiming: once only, only by that person in that conversation, not after expiry;
    - end to end over two agent runs:
      - allow once runs the saved arguments exactly once;
      - a second answer runs nothing, nor does another user's;
      - deny runs nothing;
      - "for this chat" stops later asks;
      - Always allow by an editor sets the agent's rule, and by a non-editor covers the chat;
      - a Block set since the card was shown still stops it.
  - `test_tool_approvals_wiring.py`: the context's answer and fields, the adapter's identity
    and hint, an approval turn without clarifying questions.
  - `test_answer_finalizer_transcript.py`: the asking turn's answer.
  - Node: the validator keeps `toolApproval` (and refuses an unknown decision); the payload
    forwards it on both agent paths and not on search; replay statuses.

### R3-12 — approvals, step 3: the rule APIs
- **What:**
  - Python routes:
    - `GET|PUT /instances/{id}/tool-policy`: company rules. Administrators, org servers only;
      inherited servers can't be changed.
    - `GET|PUT /instances/{id}/my-tool-rules`: the caller's own rules, on any server they can
      see.
    - `GET|PUT /agents/{agentKey}/instances/{id}/tool-rules`: anyone with access to the agent
      reads; only its editors change them. `_resolve_agent_with_permission`, since
      `_require_mcp_agent_edit_access` takes only service accounts.
  - Bodies:
    - rules are from the enums;
    - names are 1–200 characters, at most 500 entries;
    - names aren't checked against a listing, because a server may add tools and an admin
      may not be signed in to a per-user server;
    - a company entry with no rule and nothing allowed unattended is dropped.
  - Deleting an instance drops its company rules.
  - Node: six explicit proxy routes (MCP_READ/WRITE, AGENT_READ/WRITE).
  - OpenAPI: the routes, `McpToolRules`, `McpToolPolicy`, and `ToolApprovalAnswer` on the chat
    request bodies.
- **Tests:** `test_mcp_servers_tool_rules.py`:
  - company rules set and read by an admin, refused to others, none on a personal server,
    not found, dropped with the server;
  - validation;
  - personal rules: on any visible server, nobody else's, not found;
  - agent rules: editors set them, viewers read them, a viewer can't change them, no access
    is not found.

  The Node spec-match test covers the routes.

### R3-13 — approvals, step 4: the card in the chat
- **What:**
  - `ToolApprovalCard` (`chat/components/message-area/tool-approval-card.tsx`) sits under the
    answer of a turn that ended waiting for approval. It shows:
    - the tool (its title, plus the raw name) and the server;
    - the arguments, one line each, or the start of them when they were too large to send.
  - The buttons:
    - Allow once / Allow for this chat / Always allow (only when `canAlwaysAllow`) / Deny;
    - each sends the next message with `toolApproval {approvalId, decision}` and a short text
      ("Allow once: Create an issue on Jira");
    - they hide once answered, on an older reply, in a conversation shared with the viewer
      (who can't send there), and when the request expires (a timer flips it at
      `expiresAt`).
  - **Company "Always ask":** the gate now adds `companyAlwaysAsk` to the card details (and
    `canAlwaysAllow` is false then). The card offers only Allow once / Deny and says why.
    "For this chat" and "Always" wouldn't stop the next ask.
  - The card comes from the reply's parts (`findToolApproval`, which also looks inside
    sub-agents), so it is the same live and after a reload. It shows once the reply is
    settled, never while streaming.
  - The AG-UI handler copies `approval` onto the part, only for `awaiting_approval` and only
    when it has the required fields.
  - The activity row shows "Waiting for approval" with its own icon.
  - **Request:** `buildStreamChatRequestForSlot(..., toolApproval)` carries the answer, and
    `ChatApi.streamMessage` sends it on the agent path (the assistant path already spreads
    the request). Only an agent run can ask, so an assistant chat sends the answer in Agent
    mode even if the mode was switched since.
  - i18n in all 9 locales.
- **Tests:**
  - `tool-approval.test.ts`: the details parser, finding the waiting call (also in a
    sub-agent), the argument lines;
  - `tool-approval-card.test.tsx`:
    - what it shows;
    - each button sends its decision and text once;
    - Always allow only when allowed;
    - company always-ask;
    - no buttons on older replies or shared conversations;
    - expiry;
    - the preview;
    - `ChatResponse` shows the card when settled and not while streaming;
  - `tool-approval-request.test.ts`: the builder carries the answer and forces Agent mode for
    an assistant chat;
  - `api.test.ts`: the body carries the answer on both paths, and nothing when there is none;
  - `agui-event-handler.test.ts`, `agent-activity.test.tsx`;
  - Python: `companyAlwaysAsk` on the details.

### R3-14 — approvals, step 5: the rules dialog in three places
- **What:** one `McpToolRulesDialog` (`workspace/mcp-servers/components/mcp-tool-rules-dialog.tsx`)
  edits whose rules its `target` names. It is opened from:
  - **the Workspace server card** (menu → "Tool approvals"): the person's own rules, for their
    assistant chats;
  - **the server settings drawer** (Connection & Tools → "Tool approvals"): the company's rules.
    Shown for organization servers only, never personal or inherited ones;
  - **the agent builder's MCP node** (the rule icon): the agent's rules for the tools it has,
    saved straight away through the API, not with the agent. It shows only once the agent has
    been saved (`McpAgentRulesContext`). Viewers see the rules as labels, with no controls.
- **Rows:**
  - the server's tools in its order, then any other tool that has a rule ("Not offered now");
  - a tool can be added by name, because an administrator may not be signed in to a per-user
    server, and a server may add tools later;
  - a search box once there are more than 8.
- **Choices:**
  - own rules are Allow / Ask / Block, starting where the server does (Allow for tools marked
    read-only, otherwise Ask). Only the choices that differ from that are saved, plus every
    rule for a tool the server doesn't list;
  - company rules are No rule / Always ask / Block, plus "Allowed when nobody is watching"
    (off and disabled for Block). Entries that say nothing are dropped.
- **API client:** `getToolPolicy` / `updateToolPolicy`, `getMyToolRules` / `updateMyToolRules`,
  `getAgentToolRules` / `updateAgentToolRules`, and the `McpToolRule*` types.
- i18n in all 9 locales.
- **Tests:**
  - `tool-rules.test.ts`: hints, starting rules, rows, what is saved;
  - `mcp-tool-rules-dialog.test.tsx`:
    - own rules shown and saved as differences;
    - add by name;
    - a failed save keeps the dialog open;
    - a failed load offers no save;
    - company rules with unattended, and Block disabling it;
    - agent rules saved, and read-only for viewers;
  - `tool-rules-api.test.ts`: paths and encoding;
  - the card's menu entry, the drawer's button (org server yes, personal no), and the builder
    node: the hints, shown only for a saved agent, read-only for viewers.

- **Release notes (round 3): tool approvals.** This changes behaviour people will notice.
  - **MCP tools that change things now ask first.** A tool the server doesn't mark read-only
    starts at Ask. In the web chat the turn ends with a card: Allow once / Allow for this chat
    / Deny, plus Always allow for whoever can set the rule. The approved call runs exactly as
    asked, once, within 15 minutes. Tools marked read-only (`readOnlyHint`) run as before.
  - **Where nobody can answer, Ask counts as Block.** That covers Slack, the API, the MCP
    gateway and non-streaming chats. The model is told why, and an admin can mark a tool
    "Allowed when nobody is watching". **Operators:** before upgrading, review the MCP tools
    used by Slack-facing or scheduled agents.
  - **Who sets what:**
    - agent editors set an agent's rules on its MCP node;
    - each person sets their own for their assistant chats, on the Workspace server card;
    - admins set company rules in the server settings: Always ask or Block, a floor nobody
      goes below.
  - Changing a rule takes effect from the next message. A Block set while a card is open still
    stops the approved call.
  - `MCP_TOOL_APPROVALS=false` turns approvals off: every MCP tool runs as before.
  - Toolsets and web tools aren't covered yet; the check sits at the executor, so they can
    join later.
  - Known limitation: regenerating the reply to an approval re-sends the answer's text without
    the approval, so the model may call the tool again. After "for this chat" or "always", that
    call runs without asking. Regenerate re-runs side-effecting tools in any turn.

### R3-15 — approvals: fixes from the independent review
An independent review of R3-10..R3-14 found no blocking bugs and nine issues; eight are fixed.
- **Always allow can't undo a Block.**
  - The saved call now runs first (all checks again, with `on_denied` recorded); the choice is
    remembered only if it ran.
  - Always allow never replaces a Block on the agent or person.
  - A refused approved call shows as blocked on its row, and the model is told it wasn't run.
- **Rules that can't be read close the gate.**
  - `_read` passes `raise_on_error=True`: `get_config` used to answer a store failure as "not
    set", which dropped every Block.
  - Now every tool of that server is refused ("couldn't check the approval rules … try
    again"), read-only ones too, and nothing is saved from the empty fallback.
- **One pending approval per request, really.** The slot is taken before the save is awaited
  (calls in a wave run concurrently) and freed if the save fails.
- **A sub-agent's ask stops the parent too** (agent-loop library).
  - `tool_loop.PENDING_APPROVAL` is an inherited state slot, so a spawn tree shares one
    holder. `_on_pending` sets it, and `Agent.step` ends the turn with the pending message
    when it's set.
  - Before, the parent carried on, spent more model turns, and its answer wasn't marked
    "Approval Needed".
  - The finalizer now keys off the pending message being set. If a run answered anyway (a
    static-composition child, which doesn't share the parent's scope), the waiting message
    follows the answer.
- **The web app's `client-name` is the web app's.**
  - Node's auth middleware turns `client-name: pipeshub-ai` into `api` for OAuth and personal
    access tokens. Before, a token caller could claim it, get Ask instead of Block, and answer
    its own card.
  - Python answers a card only from a run someone is watching; an unwatched answer runs
    nothing and leaves the card answerable in the app.
- **The builder's rules dialog keeps rules whose hint isn't known.**
  - A tool missing from the live list gets no read-only hint, and its rule is saved whatever
    it is. `ownRulesToSave` drops a rule only when the starting rule is known.
  - The button shows once the live list has loaded.
- **The card shows everything being approved.** "Show all" expands values cut at 300
  characters, and arguments too large to send say "Only the start of the details is shown".
- **A failure after the call ran** says it ran ("don't run it again"), instead of "nothing ran".
- **Not changed:** regenerating the answer turn re-sends its text without the approval. The
  model may call the tool again, and with "for this chat" or "always" that runs without asking.
  Regenerate re-runs side-effecting tools for any turn today; this is noted as a known
  limitation.
- **Tests:**
  - approvals:
    - an unreadable store refuses a read-only tool;
    - two asks in a wave leave one pending;
    - a failed save frees the slot;
    - Always allow after a Block runs nothing and keeps the Block;
    - an answer while the store fails saves nothing;
    - an unwatched answer runs nothing and stays answerable;
    - a failure after the run says it ran;
  - library: a sub-agent asking ends the parent's turn (checked to fail without the slot);
  - finalizer: a waiting call follows a parent's answer;
  - Node: token callers lose the web name, sessions keep it;
  - frontend: unknown hints keep their rule, no button before the live list, Show all, the
    partial note.

### R3-16 — fixes from the testbed run (ISSUE-1..4)
The testbed repo (`C:\Users\Tushar\workspace\pipeshub-mcp-testbed`) runs PipesHub's real client
stack against a custom MCP server: transports, auth/OAuth, listing, calls, cache, approvals,
guards and routes. It found four issues; an independent review of the fix plan tightened two.
- **ISSUE-1 — modern servers were never cached.**
  - **Cause:** `ttlMs` and `cacheScope` are required on the 2026-07-28 wire. The SDK sends
    `ttlMs: 0` ("immediately stale") when a server sets nothing, and `tool_cache` read a 0 as
    "don't cache, forget". So every modern server was listed live on every turn.
  - **Fix:** `entry_ttl` keeps such a list briefly (`MCP_TOOL_CACHE_STALE_TTL_SECONDS`, 15 min;
    0 doesn't keep it). A positive hint under 1 s still means "don't cache".
  - **Made safe by the review:** the model plans, and the approval gate decides, on the cached
    schema and hints before the pre-call listing runs. So the pre-call listing now compares the
    called tool's live `inputSchema` and annotations with the cached ones. A changed tool is
    refused (`MCPToolChangedError`: "changed the tool X since this chat loaded it, so it
    wasn't called"), and the rewritten cache gives the next message the new version.
  - A tool that stopped being read-only can't run on the old hint. This also covers handshake-era
    lists cached for the full 24 h.
- **ISSUE-2 — a listing cut at 100 pages said nothing.** It now logs a warning, once per server
  per process: every turn lists again.
- **ISSUE-3 — PipesHub introduced itself as the SDK ("mcp 0.1.0").** The SDK `Client` now gets
  `client_info=PipesHub <package version>` (was RUN-15).
- **ISSUE-4 — the 401 challenge was ignored.** The MCP authorization spec says clients MUST use
  `resource_metadata` from `WWW-Authenticate` when present, and SHOULD request its `scope` first.
  - **The probe:**
    - discovery first sends one unauthenticated request to the MCP URL: a POST of
      `initialize`, or a GET with `text/event-stream` for a legacy SSE server;
    - no userinfo, no credentials;
    - only the status and headers are read (`stream=True`), so an open stream can't stall
      discovery.
  - **The header:**
    - parsed by an RFC 9110 challenge tokenizer: several header fields, case-insensitive
      names, quoted strings with escapes and commas, token68, the first of a repeated
      parameter, an 8 KiB cap, and bare values (some servers don't quote URLs);
    - scopes are split, checked against RFC 6750 characters, and deduped.
  - **Using it:**
    - a named metadata URL (a web URL under 2 KiB) is tried before the well-known ones, under
      the same SSRF policy;
    - it is used only if its `resource` covers the server's URL (RFC 9728 §3.3, the SDK's
      `check_resource_allowed`). Otherwise a server could name a genuine third party's
      metadata and receive a token minted for it. A mismatch falls back to the well-known
      URLs;
    - a well-known document that doesn't match is still used, as before, with a log line.
  - **Scope order on sign-in:** admin-configured, then the challenge's, then the metadata's
    `scopes_supported`, then none.
- **Tests:**
  - unit:
    - the cut-listing warning (once);
    - `CLIENT_INFO`;
    - `ttlMs: 0` kept for 15 min, or not at all with the env 0;
    - a changed tool (hints, arguments) is refused while a changed description isn't;
    - the challenge parser (10 cases and the cap);
    - discovery:
      - the hinted URL comes first and its scopes are kept;
      - no userinfo or credentials on the probe;
      - metadata for another resource, or with no resource, is ignored;
      - unusable named URLs aren't fetched;
      - a 200 or 403 gives no hints;
      - a failed probe changes nothing;
      - SSE servers are probed with GET;
    - route scope order;
  - testbed: L13, T14, A27 and L03 now pass; new L16, A31 and P15.
- **Not fixed, needs a decision** (testbed `results/findings.md`):
  - PKCE-support verification;
  - always sending `resource` (AUTH-5 chose not to);
  - Client ID Metadata Documents;
  - step-up on `insufficient_scope`;
  - `client_secret_basic`;
  - progress, elicitation, sampling and roots;
  - `subscriptions/listen` for list changes on 2026-07-28 sessions;
  - legacy-SSE refused-POST latency (SDK).

## Round 4 (2026-10-08): the decisions after the testbed run
Tushar approved the recommendations:
- **To do:**
  - R4-1 Regenerate guard;
  - R4-2 `client_secret_basic`;
  - R4-3 PKCE check (middle ground);
  - R4-5 progress;
  - R4-6 step-up via reconnect;
  - R4-7 Client ID Metadata Documents.
- **Left as they are:**
  - R4-4: `resource` stays sent only with protected-resource metadata (AUTH-5);
  - sampling and roots stay refused;
  - elicitation and `subscriptions/listen` are deferred (see the testbed RESULTS.md).

An independent review of the plan changed several details; each item says which.

### R4-1 — a reply that ran an approved action isn't regenerated
- **Why:** regenerating it re-sends the answer's text ("Allow …") without the approval. With
  "for this chat" or "always" the tool then runs again without asking: a duplicate Jira
  issue, a second email.
- **What:**
  - **Python:** the approved call's TOOL_CALL_START carries `approved: true`. The transcript
    keeps it on the part, and the AG-UI TOOL_CALL_START forwards it.
  - **Node:**
    - `regenerateAnswersInternal` refuses a reply whose parts, at any depth, hold an approved
      call (`ranApprovedCall`). It's sent as the stream's error frame;
    - a stopped reply keeps the mark too (from the review). The stream accumulator keeps
      approved TOOL_CALL_START frames, and `savePartialConversation` saves them as parts, on
      all five stream paths. `AGUIEventType.TOOL_CALL_START` added.
  - **Frontend:**
    - the live handler copies `approved`;
    - `ChatResponse` hides Regenerate on such a reply (`hasApprovedCall`, `canRegenerate`);
    - the Regenerate button got an accessible name.
- **Tests:**
  - Python: the transcript part and the AG-UI frame; the approved call's start;
  - Node:
    - both regenerate routes refused;
    - the helper at any depth;
    - the accumulator;
    - a stopped reply's parts;
  - frontend: the helper, the live mark, no Regenerate on that reply and one on others.

### R4-2 — `client_secret_basic` where that is all a provider takes
- **Why:** PipesHub always sent the client secret in the request body
  (`client_secret_post`). A provider that accepts it only in the `Authorization: Basic`
  header (RFC 6749 §2.3.1) rejected every sign-in.
- **What:**
  - `TokenEndpointAuthMethod` (post, basic, none).
  - `dcr.preferred_token_auth_method(supported)`:
    - post, as before, when the authorization server lists nothing or lists post;
    - otherwise basic, or `none` for a server that only has public clients.
    - RFC 8414's "unlisted means basic" default isn't followed: servers that list nothing
      take a posted secret in practice, and changing that would break working sign-ins.
  - Discovery keeps `token_endpoint_auth_methods_supported`, or None when the server
    doesn't say (`_string_list_or_none`).
  - Registration:
    - asks for the preferred method;
    - keeps the one the provider actually registered (RFC 7591 lets it choose);
    - fails on one PipesHub can't use (`private_key_jwt`, or a non-string).
  - The method is chosen per client:
    - an admin's static app, from the server's list;
    - a registered client, its recorded method;
    - one registered before the field existed, post.
  - The sign-in state carries the method, and the code exchange uses it.
  - The tokens record the method actually used (`OAuthTokens.token_endpoint_auth_method`), and
    every refresh uses it. A refresh never discovers metadata again, so this is the only way
    it knows.
  - `oauth_client._authenticate_client`:
    - basic is `base64(quote_plus(id) + ":" + quote_plus(secret))`, with no `client_id` in the
      body;
    - no secret, or `none`, sends only `client_id`.
  - From the review: no fallback to the other method on a failure, since a rejected secret
    looks the same either way. A shared client registered for post against a basic-only
    server is rejected with `invalid_client`, so it is retired as before and the next sign-in
    registers one for basic.
- **Tests:**
  - the header encoding (a `:` in the id, `&`, `/`, `+`, `=` and a space in the secret), with
    nothing in the body;
  - post by default;
  - no secret gives none;
  - a public client never sends a secret;
  - a refresh sends basic and records it;
  - the method table;
  - the registration asks for basic, keeps the provider's method, and fails on
    `private_key_jwt` or a list;
  - discovery keeps a clean list, or None;
  - sign-in: a static app follows the server's list, a new registration uses its method, an
    old record uses post;
  - the callback passes the method and the saved tokens keep it;
  - two refreshes in a row both send basic.

### R4-3 — a provider without S256 PKCE is refused
- **Why:** the MCP authorization spec has a client verify PKCE support before signing in.
  PipesHub always sent an S256 challenge but never checked that the provider could check it.
  A provider that can't would accept the code without the proof, so the code alone would be
  enough to get a token.
- **The middle ground:**
  - the spec also refuses a provider that lists no `code_challenge_methods_supported`;
  - Entra ID lists none yet does PKCE, so refusing that would break Microsoft sign-ins;
  - so only a provider that lists methods without S256 is refused, an empty list included.
- **What:**
  - discovery keeps `code_challenge_methods_supported`, or None when the provider doesn't say;
  - `DiscoveredOAuthMetadata.refuses_s256_pkce`;
  - `_build_oauth_authorization_url` answers 502 with a plain message.
  - From the review: the check runs before `_resolve_oauth_client`, so no client is
    registered for a sign-in that is then refused.
  - It applies only when the discovered provider is the one signing in. An administrator's
    own endpoints on a custom server may belong to another provider.
- **Tests:**
  - unit:
    - the rule table (S256, plain and S256, plain only, empty, absent);
    - discovery keeps a clean list, or None;
    - refused with 502 before any registration or state is written;
    - an unlisted provider still signs in with S256;
    - an administrator's own endpoints aren't judged by the discovered list;
  - testbed: A33 (plain only: refused, nothing registered, no browser redirect) and A34 (field
    absent: signs in). The testbed metadata override now removes a field given None.

### R4-5 — progress keeps a long call alive and is shown
- **Why:**
  - PipesHub never asked for progress, so a server doing real work (an export, a long search)
    was cut off at the call timeout, 60 s by default, however clearly it was still working;
  - the user saw only "Running …" with no sign of movement.
- **Calls:**
  - every call now sends a progress token (the SDK's `progress_callback`);
  - the call timeout bounds each wait for the answer or for progress: each report restarts it,
    as the MCP spec allows;
  - `MCP_TOOL_CALL_MAX_SECONDS` (default 1800, never below the call timeout, `max_call_seconds`)
    bounds the whole call however much progress arrives, as the spec asks. Hitting it raises
    `MCPCallTooLongError`, a `TimeoutError` with its own message.
  - **From the review:**
    - the clock is ours (`asyncio.timeout`, `reschedule` on each report). The SDK's own timeout
      is set past the limit, so it is only a backstop: it doesn't restart on progress, and it
      would report "60 seconds" after 30 minutes;
    - the SDK runs each progress callback in a task of its own, so one can arrive after the call
      ended or timed out. A `finished` flag and `clock.expired()` make those do nothing,
      instead of a `RuntimeError` the SDK would log;
    - the listener's own failure is logged at debug and never reaches the SDK;
    - cancelling (timeout or Stop) still sends the SDK's courtesy cancel (testbed C14, C15,
      C34 see the server's handler cancelled);
    - `http_read_timeout` is unchanged. It is an inactivity timer, and progress on the call's
      own stream keeps it alive. Known limit: a server that sends progress only on its GET
      stream is still cut at max(300 s, call timeout + 30 s).
  - **Found by the testbed (C32):** the SDK used to notice a legacy SSE POST refused with 500
    at its own timeout, which also ended the dead session (`_operation_error`). Ours now fires
    first, so the timeout path checks the wire too (`_failed_on_the_wire`, shared with
    `_operation_error`), or the next call went into the dead writer.
- **The chat:**
  - `MCPSessionManager.call(..., on_progress=)` passes it to every attempt, retries included;
  - `MCPToolAdapter` gives a reporter whenever the chat streams (`context.event_sink`). It
    writes at most two reports a second, with the server's message (whitespace collapsed, cut
    at 200 characters);
  - `formatter.tool_progress`: AG-UI sends a STATE_SNAPSHOT `{status: "running_tool",
    current_tool, progress, total?, progress_message?}` like `write_state`'s; legacy sends a
    `status` event;
  - the frontend's activity row shows "<tool>... <message>", else "3/10", else a percentage;
  - the Slack bot already shows a running_tool snapshot's tool label (throttled by its
    activity updates), without the progress.
- **Testbed note:** the formatter's package is loaded at startup by the query service's
  routes. A test process that never loaded it imported it during the first report, which
  blocked the loop for seconds and ran the call past its timeout. The testbed's chat helper
  now loads it first, as the service does.
- **Tests:**
  - client:
    - progress carries a call past its timeout;
    - without progress it ends at the timeout (not the limit error);
    - endless progress stops at the limit;
    - the listener hears each report;
    - a failing listener doesn't fail the call;
    - reports after the answer or after the timeout are ignored;
    - the SDK's timeout is the backstop;
    - the limit's env parsing;
    - an SSE call timed out after a refused POST ends the session;
  - session manager: a call sent again reports to the same listener;
  - adapter:
    - at most twice a second, with the message cleaned;
    - a long message cut;
    - no listener without a chat;
    - the limit's message;
  - formatter: both shapes;
  - frontend: "3/10", a percentage, the message, nothing without a total;
  - testbed: C33 (6 × 0.4 s with a 1 s timeout finishes; snapshots throttled), C34 (the limit
    at 2 s, the handler cancelled, the session still usable), C23 updated (a token is sent now).

### R4-6 — more scopes after a 403 (light step-up)
- **Why:** a server that refuses a request with HTTP 403 `error="insufficient_scope"` names the
  scopes it needs (RFC 6750 §3.1, the MCP spec's step-up). PipesHub showed "returned HTTP 403",
  and reconnecting asked for the same scopes again, so nothing could fix it.
- **The light version (no in-chat sign-in):**
  - the scopes are remembered for that sign-in;
  - the model is told to have the user reconnect, which asks for them.
- **What:**
  - **Parsing:** the challenge parser moves from `dcr` to `app/agents/mcp/www_authenticate.py`:
    `bearer_challenge`, and `challenge_scopes`, which keeps valid scope tokens, each once, at
    most 50. `dcr` uses both.
  - **The wire:**
    - `wire.record_response` keeps the Bearer challenge of the latest 401 and of the latest
      403, by status, so an error is never told another response's challenge (review);
    - `MCPHttpStatusError.challenge` carries it, through `_wire_failure` and the connect path
      that rebuilds the error (review);
    - `errors.insufficient_scope(exc)` finds such a 403 in the cause chain.
  - **Remembering (`app/agents/mcp/step_up.py`):**
    - `remember_needed_scopes`, for OAuth servers only, adds them (merged, capped) under
      `get_mcp_step_up_scopes_path` = `credentials/{instance}/{owner}/step-up-scopes`. That
      is its own key, not the credential record: writing into the record raced the token
      refresh, which rewrites it under its lock and could have lost a rotated refresh token
      (review). Being under the credentials prefix, it is purged with them, and the refresh
      service's scan already skips nested keys;
    - a failed save only logs.
  - **Where it's caught:**
    - `MCPSessionManager.call`, `discover_listing` and `discover` are wrapped whole
      (`_asks_for_more_scopes`). A 403 on connect, after a 401 refresh or in a retry all count
      (review). They raise `MCPInsufficientScopeError`, an `MCPHttpStatusError` 403 whose
      message the adapter passes to the model as is. "Reconnect it in Workspace → MCP
      Servers", or in the agent builder for a service-account agent's sign-in (review);
    - the workspace's listing (`discovery.discover_listing_for_owner`) does the same, with a
      message for the user.
  - **The failure code:**
    - a new `MCPFailureReason.NEEDS_PERMISSION` ("needs_permission");
    - the workspace shows it as "Reconnect needed";
    - the model's capability summary says reconnecting asks for it;
    - a 403 that signing in again can't fix stays "unreachable".
  - **The next sign-in:**
    - `_build_oauth_authorization_url` requests the usual scopes, then the ones the current
      tokens were granted (`oauthTokens.scope`), then the saved ones, deduplicated in order;
    - with no usual scopes the grant is still kept, so the request never narrows what the user
      has (review);
    - the callback clears the key after saving the new tokens.
  - A 403 that isn't `insufficient_scope`, has no scopes, or comes from a non-OAuth server
    changes nothing.
- **Tests:**
  - the parser's tests import from the new module, plus scope validation and the cap;
  - the wire keeps each status's challenge;
  - the challenge reaches the call's error and the handshake's error;
  - `insufficient_scope` through the cause chain, any case, and nothing else;
  - remembering:
    - saved and returned;
    - merged and capped;
    - nothing new writes nothing;
    - only what reconnecting fixes;
    - a failed save logged;
    - junk ignored on read;
    - cleared;
  - the session manager:
    - a call (Workspace wording) and an agent's (agent builder);
    - kept with earlier scopes;
    - a 403 on connect;
    - after a 401 refresh;
    - a listing;
    - three kinds of 403 that change nothing;
  - the workspace listing: refused, another failure, success;
  - the failure table (needs_permission, and a raw 403 stays unreachable);
  - the authorize union, with and without usual scopes;
  - the callback clears the key;
  - frontend: needs_permission shows "Reconnect needed";
  - testbed:
    - A24 updated (the 403 now names its scopes);
    - A35: refused, remembered, the next sign-in asks for `read write admin`, cleared, and
      the chat works.

### R4-7 — Client ID Metadata Documents, with DCR as the fallback
- **Why:** the MCP authorization spec (2025-11-25) prefers a Client ID Metadata Document (CIMD)
  to dynamic registration:
  - the client id is an https URL;
  - the authorization server reads the client's details from it;
  - nothing is registered, and there is no secret.
  Servers are starting to support it, and some will stop offering DCR.
- **What PipesHub does:**
  - **The document:**
    - the Node app serves `GET /mcp-servers/oauth/client-metadata.json`, public
      (`mcp_client_metadata.ts`). It is mounted in `configureRoutes`, before the static files
      and the SPA fallback, which answers `.json` with 404;
    - it contains `client_id` (its own URL), `client_name` "PipesHub", `client_uri`,
      `redirect_uris` [the callback], the authorization_code and refresh_token grants, and
      `token_endpoint_auth_method` none;
    - `Cache-Control: public, max-age=300`; 404 without a configured address.
  - **One URL in both languages (review):**
    - Node read the frontend address once at startup and stripped one trailing slash; Python
      reads it live and strips them all. The route now reads `/services/endpoints` on each
      request and strips every trailing slash, as Python does;
    - both tests share one table of addresses (trailing slashes, a sub-path).
  - **When it's used (`cimd.py`, `_client_metadata_document`). All of these must hold:**
    - the authorization server advertises `client_id_metadata_document_supported: true`;
    - `MCP_OAUTH_CLIENT_METADATA_DOCUMENT` isn't "false";
    - the public address is https;
    - the server hasn't refused it;
    - a self-check finds the document.
  - **The self-check (review):**
    - a public address only;
    - no redirects;
    - JSON content type, at most 64 KiB;
    - `client_id` equal to its URL, and the exact redirect URI among `redirect_uris`;
    - the result is kept 10 minutes per process, a failure too.
    A deployment whose public address doesn't serve the document (dev servers, another
    origin, a private address) quietly keeps using DCR.
  - **Order:**
    - static app → legacy DCR client → shared DCR client that still fits → the document → a
      new registration;
    - a client that works is never replaced. The document only saves registering one, so a
      deployment that works today keeps working however the server changes.
  - **The admin form:** `/oauth/discover` reports `supportsDcr` when the document would be
    used, so a server that supports documents but not DCR isn't made to need an OAuth app. Its
    hint, "no client credentials are required", still holds.
  - **Sign-in:**
    - client id = the document URL, no secret, `none`, PKCE as always;
    - the state record says `clientKind: "cimd"` and the server (`authorizationServer`, its
      issuer);
    - the callback looks up no secret for it.
  - **A refusal is remembered for 7 days, per server (review):** an `invalid_client` at the
    code exchange, or `invalid_client`/`unauthorized_client`/`invalid_request` on the way
    back. The latter only for a sign-in the caller started. The next sign-in there registers a
    client instead.
  - **Refresh (review):** the URL isn't rebuilt. That would need the routes module and break
    after an address change. `resolve_client_credentials` returns the tokens' own client id
    with no secret when it is a document URL and nothing stored matches, and the tokens'
    recorded method `none` sends only `client_id`.
- **Known gap:** a server that can't fetch the document must not redirect back (OAuth), so it
  shows its own error page and nothing is learned. The self-check makes this rare;
  `MCP_OAUTH_CLIENT_METADATA_DOCUMENT=false` turns the document off.
- **Tests:**
  - Python:
    - the client id built like Node's (the shared table);
    - what counts as a document URL;
    - the switch;
    - support;
    - refusals: the key, the 7 days, an empty server, a failed write;
    - the self-check: the good document, nine bad ones, a private address with no request;
    - the cache, failures included, and its expiry;
    - sign-in:
      - a server that takes documents gets one, with S256 and the state recorded;
      - a static app or a fitting shared client is kept;
      - switched off, not served, refused before or http: registers;
    - callback:
      - no secret, method none;
      - a rejection at the exchange remembered;
      - refusals on the way back: remembered only for the caller's own sign-in and only for
        a client error;
    - refresh: the tokens' own client id, no secret, only `client_id` sent; other unknown
      clients are still unknown;
    - the admin form needs no app where the document would be used;
  - Node:
    - the shared table;
    - the full document;
    - the handler: served and cacheable, read on each request, 404 without an address, a
      store failure passed on, and answered ahead of an SPA fallback through express;
  - testbed:
    - A36: the document as client, no registration, exchange and refresh with `none`;
    - A36b: refused at the exchange, then DCR works;
    - A36c: a real self-check against an unresolvable address falls back to DCR.

### Round 4 — wrap-up
- **Commits (local, not pushed):**

  | Item | Commit |
  |---|---|
  | R4-1 regenerate guard | e26be77d7 |
  | R4-2 `client_secret_basic` | 70af8e51a |
  | R4-3 PKCE | d76dd568b |
  | R4-5 progress | 12d3bbedf |
  | R4-6 step-up | 4bfeef69b |
  | R4-7 Client ID Metadata Documents | 78dd91cad |

- **Validation:**
  - testbed: 168/168. Scenarios A32–A36c and C33–C34 are new; A24 and C23 are updated;
    RESULTS.md has the decisions;
  - Python `tests/unit/agents`, `tests/unit/api`, `tests/unit/modules/agents`: only the 39 known
    failures (37 Teams, 1 SharePoint, the artifact e2e on Windows);
  - Node `tests/modules/mcp_servers`: 125 pass;
  - frontend chat and mcp-servers suites: 776/777, and the one (`edit-safety`) passes alone, a
    timing flake under load.
- **New settings (`backend/env.template`):**
  - `MCP_TOOL_CALL_MAX_SECONDS`, default 1800;
  - `MCP_OAUTH_CLIENT_METADATA_DOCUMENT`, default on.
- **Release notes:**
  - providers that accept the client secret only in the Authorization header now sign in;
  - a provider that lists PKCE methods without S256 is refused with a clear message;
  - long MCP tool calls that report progress keep going past the call timeout (up to 30
    minutes), and the chat shows their progress;
  - when a server says the sign-in lacks permission for a request, PipesHub says to reconnect,
    and reconnecting asks for the missing scopes;
  - servers that support Client ID Metadata Documents are signed in to without registering a
    client, where the deployment's https address serves the document;
  - a reply that ran an action the user approved can't be regenerated (that could run it again).
- **Real-provider smoke checks to add:**
  - a provider that takes only `client_secret_basic`;
  - a CIMD-capable authorization server against a deployment with a public https address,
    which needs the Node route reachable at the frontend's origin;
  - a server that sends `insufficient_scope` (GitHub's fine-grained scopes, for one).
- **Still open:**
  - an in-chat step-up sign-in;
  - elicitation;
  - `subscriptions/listen`;
  - an authorization server that can't fetch the client document shows its own error page,
    with no automatic fallback;
  - progress sent only on a server's GET stream is still cut at the HTTP read timeout;
  - the Slack bot shows a running tool's label but not its progress.

### R4-8 — the Slack bot shows a running tool's progress
- **Why:** R4-5 showed an MCP server's progress on the web chat's activity row. The Slack bot
  reads the same STATE_SNAPSHOT frames but showed only the tool's label.
- **What:** `agui-stream.ts` appends `toolProgressText(snapshot)` to the running tool's label, the
  same rule as the web:
  - the server's message;
  - else "3/10";
  - else a percentage;
  - nothing without a positive total.
  It's a copy of the frontend's few lines, since the Slack bot already mirrors `tool-display.ts`
  the same way. Slack updates stay batched (`scheduleActivityUpdate`), and Python sends at most two
  reports a second.
- **Tests:** a new `tests/integrations/slack-bot/agui-stream.test.ts`:
  - 3/10, a percentage, the message trimmed, no total;
  - values it can't read;
  - the settled answer is still no status.

- **R4-8 follow-up (3ebab4b04, from the review):** the server's message goes into Slack mrkdwn,
  where a third party's `<url|text>` would render as a link and `<!channel>` as a mention. `&`,
  `<` and `>` are escaped. `escapeForSlack` keeps that syntax on purpose, so it isn't used.

### R4-9 — sign in again from the chat
- **Why:** after R4-6, a server that refused for missing permission told the user to leave the
  chat and reconnect in Workspace → MCP Servers (or the agent builder), then come back and ask
  again.
- **What the user sees:**
  - a card under the latest reply: "Sign in again to give these MCP servers the permission they
    asked for";
  - one row per server, with the scopes it needs and "Sign in again" (the provider's popup; the
    authorize route already asks for the saved scopes on top of the usual and granted ones);
  - once every server they can sign in to is done, "Try again" sends a follow-up ("I signed in to
    Drive again. Please try that again.") rather than the original question, so what already ran
    isn't run twice.
- **Backend:**
  - `AgentContext.mcp_sign_in_needed`, filled by `mcp_sign_in.note_sign_in_needed`:
    - one entry per server: `{instanceId, serverName, scopes, agentKey?}`, scopes merged;
    - noted from a tool call refused for scope (`MCPToolAdapter.execute`);
    - noted from a listing refused for scope (`MCPToolProvider._load_one`), in agent chats only
      (review): the assistant attaches every server a person has, and a refused listing isn't
      cached, so the card would follow every unrelated reply.
  - The card only exists in the web chat on AG-UI (`shows_sign_in_card`: client `pipeshub-ai`,
    streaming, a conversation, `protocol == "agui"`). The legacy protocol keeps no transcript, so
    the model would mention a button that never appears (review).
  - **What the model hears there:**
    - a call adds `BUTTON_HINT`: "They can also sign in again with the button shown under this
      answer.";
    - a listing marks its load failure `signInHere`, and the capability summary says the same.
    Elsewhere (Slack, API) nothing changes.
  - `AnswerFinalizer._attach_parts` appends one part `{type: "mcp_sign_in", servers: [...]}` to a
    new list (the transcript itself is untouched). Every finalizer path goes through it. Node
    saves parts as `Mixed`, so it persists as is. `IMessagePart` and the Python `MessagePart` know
    the type.
  - `_build_mcp_instance_entry` returns `connectedAt`, when the current sign-in was made (not
    secret).
  - Each scope is capped at 200 characters (`challenge_scopes`, review): scopes are stored and
    shown.
- **An agent's own sign-in (service-account agents, review):**
  - the backend's `can_edit_agent` is always false in those chats, so the part carries only the
    `agentKey`;
  - the card offers the popup through the agent's authorize route when the chat's
    `agentContextAccess` says the viewer can edit that agent;
  - otherwise it says "Ask someone who can edit this agent to sign in again", with the Agent
    Builder link;
  - the route checks edit access either way.
- **Telling a finished sign-in from a cancelled one (review):** the person was already signed
  in, so `isAuthenticated` alone would read a closed popup as success. The card reads the
  server's `connectedAt` inside `getAuthorizationUrl`, after the blank popup opened and before the
  provider's page loads, and success means a later one.
  - a failed read aborts the sign-in;
  - a missing value counts as 0;
  - reads use `includeTools=false`.
- **Frontend:**
  - `mcp-sign-in.ts` parses the part, dropping what isn't a server;
  - `McpSignInCard`;
  - `ChatResponse` renders the card on the latest, settled reply;
  - the activity timeline ignores the part (`filterRootParts`);
  - a stopped reply whose only part is the sign-in part is still dropped on reload;
  - "Try again" goes out in agent mode (MCP only loads there): `buildStreamChatRequestForSlot`
    takes `{ agentMode }`, as an approval answer already does (review);
  - no buttons in a conversation shared with the viewer;
  - strings in all 9 locales.
- **Not handled:**
  - a reply that ends as an error row drops its parts (as before). A step-up rarely leads there,
    because the tool failure goes back to the model;
  - a sub-agent still running when the parent finalizes notes too late.
- **Tests:**
  - Python:
    - where the card shows (web on AG-UI) and nowhere else: legacy, Slack, API, not streaming,
      no conversation, no context;
    - noted once per server with scopes merged;
    - an agent's sign-in carries its key;
    - only a refusal signing in again fixes;
    - the part is a copy;
    - the adapter adds the hint and notes the server, and changes nothing elsewhere or without a
      context;
    - the loader notes a refused listing in an agent chat, not in the assistant;
    - the finalizer appends the part and leaves the transcript alone, and adds nothing without
      one;
    - the capability summary, with and without `signInHere`;
    - the entry's `connectedAt` (set, absent, junk);
    - the scope length cap;
  - frontend:
    - the parser;
    - the timeline ignores the part;
    - the card:
      - the scopes;
      - popup, then "Try again" in agent mode with the follow-up;
      - a closed popup without a new sign-in isn't one;
      - a failed baseline read doesn't open the provider;
      - an agent's sign-in through the agent for an editor;
      - an editor pointer and builder link for anyone else;
      - nothing in someone else's conversation;
    - `ChatResponse`: on the latest settled reply only;
    - an empty stopped reply with only the part is dropped;
    - agent mode without an approval answer;
  - testbed A35b, through the chat stack:
    - the listing works and one tool needs `notes.write`;
    - the call fails and doesn't run;
    - the model hears of the button;
    - the part lists the server and scope;
    - signing in again asks for it;
    - the call then works.

## Round 5 (2026-10-09/10): approval defaults by tool type, readable results, the personal page, the admin panel

Tushar's four requests (2026-10-09), in the order they were built:
1. **Item 3**: approval defaults by tool type.
2. **Item 2**: readable tool results.
3. **Item 4**: the personal page looks like the toolsets page.
4. **Item 1**: the admin panel follows the design canvas "MCP Server Panel Redesign".

The plan was reviewed independently before any code; the review's 17 points were all taken.
Each item is one local commit (not pushed).

### R5-1 — a tool starts from what it does to data: read, write or delete

- **Asked:**
  - Read-only tools are **Pre-approved**.
  - Tools that change data are **Allow on approval**.
  - Tools that delete data are **Deny**.
  - Use what the server says if it says anything, else the tool's name.
- **What MCP offers:**
  - Tool annotations `readOnlyHint` (default false) and `destructiveHint` (default true, meaningful only when not read-only).
  - The spec says a client shouldn't trust them from an untrusted server.
- **Decided with Tushar** (the "recommended" option A):
  - The order of checks:
    1. A deleting word in the name (delete, remove, destroy, drop, purge, erase, wipe, truncate, revoke, uninstall, reset, with their -s/-ed/-ing forms and deletion/removal) means **deletes**, whatever the server says.
    2. Else `readOnlyHint: true` means **read**.
    3. Else `destructiveHint: true` means **deletes**.
    4. Else `destructiveHint: false`, or a bare `readOnlyHint: false`, means **write**.
    5. Else (no hints) **write**: not denied, it asks.
  - A name never makes a tool read-only (`get_`/`list_` don't count). Names only make it stricter.
  - Only the starting rule changes, at the person and agent level. Company rules stay an optional floor; Option B (pre-filled company rules) was not done.
  - Saved rules don't change.
  - Tushar's "for option a do as i asked, for b avoid this" was read as the two smaller questions: a deleting tool defaults to **Deny**, and names give no read-only.
- **Backend:**
  - `app/agents/mcp/tool_kind.py`: `tool_kind(name, annotations) -> (kind, source)`. Words are split on snake/kebab/dots/camelCase, so `dropdown`, `preset` and `address` don't match.
  - `MCPToolInfo.kind` / `kindSource` are pydantic computed fields. Every route that sends tools sends them, so the builder, the personal page and the admin panel all get them. They are worked out again from name and hints on every load and never read back from storage. The tool cache hashes its own `CachedTool`, so digests don't change.
  - The kind is computed from the server's own tool name, not the namespaced one: a server called "reset-tools" doesn't make every tool deleting.
  - `tool_approvals.starting_rule(kind)`: read → allow, write → ask, destructive → block.
  - `decide(kind=…)`. A block that comes from the starting rule has reason `default`, and the model is told: "X can delete data, so it's denied unless your / this agent's tool approval rules allow it… Tell the user they can change its rule".
  - The approval card's details gain `kind`.
  - OpenAPI `McpToolInfo` documents both fields.
- **Effect on users:** tools named for deleting, or marked `destructiveHint: true`, used to ask and now are denied until someone sets Allow on approval or Pre-approved. Rules equal to the old starting rule were never saved, so nothing saved hides this.
- **Frontend:**
  - Labels:
    - Own rules: Pre-approved / Allow on approval / Deny.
    - Company rules: No limit / Allow on approval / Deny.
    - "Allowed when nobody is watching" becomes "Allow when no one can approve" ("Slack, the API and scheduled runs").
  - `tool-rules.ts`:
    - `startingRule(kind)`.
    - `deletesByName`: same words, same test names as the Python test.
    - `ruleToolFromName` gives a tool the server doesn't list a kind when its name deletes, otherwise its rule is kept as it is.
  - New `components/mcp-tool-rules-editor.tsx`:
    - `useToolRulesEditor` loads, edits, counts changes, discards and saves one target's rules.
    - `McpToolRulesList`:
      - groups: Deletes data ("Review these first") → Changes data → Read-only ("Only looks things up") → Not offered now;
      - a kind badge and where the kind came from ("From its name", "As the server says", "No labels from the server");
      - search by name or id, and filter chips (All / Deletes data / Changes data / Read-only / Has a rule);
      - company quick setup: "require approval for every tool that changes data" sets Allow on approval on write and deleting tools that have no company rule;
      - a legend and add-by-name.
    - `McpToolRulesDialog` is now a thin wrapper around it, used by the personal page, the admin panel and the agent builder.
  - The company "Allow when no one can approve" switch shows for No limit and Allow on approval. With No limit it still matters: members' Allow-on-approval tools are then allowed unattended. On Deletes-data rows it says "Only where someone has allowed this tool", because the default Deny wins otherwise.
  - The chat approval card shows a red "Deletes data" badge for a deleting tool someone set to ask.
- **Tests:**
  - Python:
    - the classifier table: names in every casing, negatives, a name beating `readOnlyHint`, the hint matrix, non-boolean hints ignored;
    - the computed fields are sent and recomputed on load, and a namespace never sets the kind;
    - `decide` per kind:
      - default Deny with reason `default`;
      - an own Allow on approval or Pre-approved overriding it;
      - a company Allow-on-approval floor not loosening the default Deny;
      - a company block with no own rule still reported as `company`;
    - the gate: a deleting tool is denied with the right message in assistant and agent chats, nothing is saved, and a deleting tool set to ask carries `kind` on the card;
    - the existing tests that used `delete_issue` as a write tool now use `update_issue`.
  - Frontend:
    - the kind mapping, the name table and the starting rules;
    - `ownRulesToSave` per kind, including not-offered rows;
    - the dialog: the new labels, grouping and source notes, the filters and clear, saving a deleting tool moved to Allow on approval, the company switch, quick setup;
    - the builder's rules start from the kind;
    - the card badge;
    - the approval parser keeps only a known kind.
  - Testbed:
    - P13: a tool without hints asks.
    - P14 (`delete_note` and `remove_tool`, whose server only says it writes): denied, then asks once someone sets Allow on approval.
    - L05 checks the kind reaching the gate.

### R5-2 — tool results the person can read: a better summary and a table

- **Problem:** the tool card showed raw JSON. Examples:
  - a Jira search's summary was `{`, the first line of its JSON;
  - the result was a 38 KB blob cut at 2,000 characters.
- **Decided with Tushar:**
  - Options A and B.
    - A: a one-line summary such as "Found 23 issues".
    - B: a view built on the backend from the full result. It is small, the same live and after reload, and Slack could use it later.
  - Not chosen:
    - C: AI summaries.
    - D: per-server layouts.
    - E: MCP Apps.
    - F: stripping noise for the model.
- **Backend:**
  - `app/agents/actions/util/result_view.py` `describe(content) -> (summary, view)`. It never raises and trusts no shape.
    - **What it reads:** text that is JSON, or that ends with JSON after a note (Atlassian sends a notice block first). Anything over 250,000 characters isn't parsed.
    - **A list of records:** the longest list of objects within three levels, e.g. `{"data": {"issues": {"nodes": [...]}}}`, when the top object has no identity of its own (id, key, title, name, …).
      - Summary: "Found 23 issues" or "Found 1 issue". Generic keys like results/items/data become "results".
      - A declared total larger than the page reads "Found 120 issues, 50 returned".
      - An empty list reads "No issues found".
      - View: a table of at most 20 rows × 5 columns. Columns are chosen by role, in this order: id (key/number), title (title/summary/name), status, person (assignee/owner/author…), time (updated/lastModified/created…).
      - Values come from the record and from one level into `fields`/`content`/`properties`/`attributes`/`data`. An object value is shown by its displayName/name/title/value, and ISO dates are cut to the day.
      - With fewer than two known roles, it falls back to the first short fields.
      - Each row gets a link (url/htmlUrl/webUrl/…, or Confluence's relative `url` joined to `_links.base`): http(s) only, no user-info, never the API's `self`.
    - **One record:** up to 12 label/value fields, plus a link. Summary "PA-7 · Login fails".
    - **Plain text:** no view. The card shows the existing preview as text instead of code.
    - **Size:**
      - a view is at most 5,000 UTF-8 bytes, dropping rows from the end;
      - a reply keeps at most 40 KB of views (`protocol/result_views.py`), because parts are stored inside the conversation document (16 MB limit). The AG-UI emitter and the transcript apply the same budget to the same events, so live and reload agree.
  - New hook `Tool.result_view(args, result)`, default None. `tool_loop` adds `result_view` to the normal TOOL_RESULT event only, never to errors, duplicates or malformed calls. A failing view is dropped.
  - The MCP adapter parses each result once for both the summary and the view. It caches the last content by identity and holds a reference to it, so an id can't be reused.
  - AG-UI `TOOL_CALL_RESULT.resultView` and the part's `resultView`. Node's `IMessagePart.resultView?: unknown`; parts are already `Mixed`.
- **Frontend:**
  - `chat/tool-result-view.ts` `toolResultView()` rebuilds the shape: strings only, capped, http(s) links only via `URL`. It runs where the card is drawn, so live frames and saved parts are checked the same way.
  - The card shows:
    - the summary;
    - a Radix table, with the first cell linked (new tab, `noopener noreferrer`) and "Showing n of N", or the field list with "Open";
    - "View raw" / "Hide raw" for the JSON.
  - A prose result is shown as text, and its first line isn't repeated when it is the summary. JSON with no view is shown as before.
  - `ToolSummaryText` loads no images, skips HTML and links only to http(s) in a new tab, because summaries now quote tool data (titles).
- **Not done:**
  - The Slack bot doesn't use the view yet; it can, since the view is plain JSON.
  - A live sample of Rovo's `searchJiraIssuesUsingJql` couldn't be fetched this session (the Atlassian sign-in of this tool session had expired), so the tests use the documented Jira and Confluence shapes. The saved Confluence search sample from earlier in the session was used for the Confluence case.
- **Tests:**
  - Python:
    - `result_view`: Jira search, Confluence after a notice with `_links.base`, a declared total, singular/plural nouns, an empty list, GraphQL nesting, a top-level list, an unknown shape, plain values, one record, an acknowledgement, text, broken and fenced JSON, unsafe links (javascript, data, protocol-relative, user-info, broken IPv6), the byte budget with CJK text, odd content, huge text, JSON after a note;
    - the adapter's summaries, its one parse, and no view for text, errors or images;
    - the tool loop's event carries the view, and nothing changes without one, with a non-dict one or with a failing one;
    - the emitter forwards it within the budget;
    - the transcript keeps exactly what the emitter sent.
  - Frontend:
    - the validator, `safeLink` and `looksLikeJson`;
    - the handler keeps the view;
    - the card: table, links, the unsafe link as text, "Showing 2 of 23", raw behind the toggle, fields with Open, bad views ignored, prose as text, and a summary that can't load an image or a `javascript:` link.

### R5-3 — the personal MCP page looks like the toolsets page

- **Asked:** catalog servers nobody has set up got no card on the personal page; they could only be found inside the Add dialog. The page should work like the toolsets page.
- **Now:**
  - The title and subtitle sit on the left, with the search (224 px) on the right.
  - Below that: All / Connected / Not connected tabs with counts, and Add server + refresh on the right.
  - Everything is in one grid of 2/3/3 columns, like `ActionsCatalogLayout`. The two section headings ("Your MCP servers", "Organization MCP servers") are gone.
  - Your own servers carry a "Yours" badge. Its tooltip is the old section text: only you can use them, and they can't go on shared agents.
  - Each catalog entry a person could set up gets a "+ Setup" card (the team page's `McpServerCard` registry variant). That means remote entries that still make new servers (the new `offeredForPersonalServers`, also used by the Add dialog) with no visible server of that type yet. The card opens the create panel filled from the entry, as picking it in the Add dialog does.
  - **Order:**
    1. Connected servers, your own before the organization's.
    2. Servers that aren't connected.
    3. Entries to set up.

    Each group is sorted by name.
  - **Connected** means ready or slow and not disabled. **Not connected** is everything else, including the entries to set up.
  - Empty states:
    - nothing at all: "No MCP servers available" plus what to do;
    - a search with no results: "No results";
    - an empty tab: "No MCP servers here."
- **Disconnect asks first,** on the personal page now and in the admin panel with R5-4. `McpDisconnectDialog`:
  - Title: "Disconnect {name}?"
  - For a sign-in of your own: "You'll stop using its N tools until you sign in again."
  - For a shared credential: "Agents in your organization will stop using its N tools right away, including in Slack and scheduled runs." This is only true for the shared credential, because Disconnect removes just the caller's own sign-in otherwise.
  - ✓ settings and approval rules are kept;
  - ✓ reconnect any time;
  - ✗ your (or the shared) saved sign-in is deleted.
- **Tests:**
  - the grid and its order;
  - Yours;
  - set-up cards only for entries without a server, not for STDIO or replaced ones, and Setup opens the entry;
  - tabs with counts;
  - search across both;
  - the empty state;
  - the disconnect dialog for a sign-in of your own and for a shared credential.

### R5-4 — the Edit MCP server panel, after the design canvas

The design canvas "MCP Server Panel Redesign" (claude.ai artifact V1fzWrYXpGZS9caPXwfH8D) was built in Radix Themes with our tokens, not its hard-coded dark colours, and with the R5-1 labels.

- **Header:**
  - The server's icon and name, and a status dot with its label (Ready, Reconnect needed, Can't reach server, …).
  - A **More actions** menu with:
    - Reauthenticate;
    - Copy server URL;
    - "Added by {name}" (the creator lookup moved up from the old tab);
    - Disconnect ("Signs you out. Settings and rules stay.");
    - Remove server ("Deletes it for everyone.", or "Deletes it and your sign-in." for a personal server).
  - The red trash icon is gone; removing is in the menu.
  - The menu is a Radix `DropdownMenu` drawn into the drawer's nested modal host, so it opens above the drawer (`EntityRowActionMenu` would open under it, the review found).
- **Tabs:** "Configuration" | "Tools & approvals".
- **Tools & approvals:**
  - **Sign-in:**
    - "Signed in" with "{transport} · signed in {time}" and Reauthenticate;
    - "Shared sign-in";
    - "Not signed in" with Connect;
    - "No sign-in needed";
    - an amber "Sign-in expired" (or "Shared sign-in missing") alert with Reauthenticate.
  - **The list loads when the tab opens.** It reads the tool cache through the new `GET /instances/{id}/tools?cached=true`, which falls back to a live listing when nothing is cached. The refresh icon is live. Both answers carry `syncedAt`, shown as "Synced 2 minutes ago". Before this, opening the tab listed nothing until someone pressed Discover, and every Discover connected live. Node forwards `cached` only.
  - **While it loads:** "Finding tools on this server", a spinner, skeleton rows and "Approval rules unlock once the list is ready."
  - **When it fails:** "Couldn't load the tool list", a fixed message (Node turns every 5xx into its own 500, R4 lessons), "Your saved approval rules are kept." and Try again.
    - A 409 marks the sign-in expired (or the shared credential missing) in the header and the sign-in card.
    - A 502 marks the server "Can't reach server".
    - A new sign-in (`connectedAt` changes) lists again.
  - **The rules are inline**, using the R5-1 editor:
    - the company floor for an organization server, or the owner's own rules for a personal server;
    - read-only for an inherited server;
    - the caption "Rules here apply to everyone in your organization. Members can add stricter rules, never looser ones.", plus the search, filters, quick setup, legend and groups.
  - **A sticky footer:** "N unsaved changes" with Discard and Save rules, or "All rules saved".
  - **Unsaved changes survive switching tabs** (the editor's state lives in the panel). Closing the panel with unsaved changes asks "Discard unsaved rule changes?".
- **Disconnect asks first,** with `McpDisconnectDialog` from R5-3:
  - inside the panel;
  - from the team details page's rows (new);
  - from the personal cards (R5-3).

  The pages pass the plain handler to the panel, so nobody is asked twice.
- **Not done** (from the canvas, on purpose):
  - The canvas showed the unattended switch only under "Ask". It is also shown under "No limit", because the flag matters there: members' Allow-on-approval tools run unattended.
  - "Signed in as X": PipesHub doesn't know the provider account's name, so it says "Signed in" with the time.
- **Not checked in a browser this session.** The checks were unit tests, the type check and lint; worth a look before merging.
- **Tests:**
  - Python: the cached read (no connection, `syncedAt`, kinds included), and with nothing cached the panel lists live.
  - Node: the proxy forwards `cached` and nothing else.
  - Frontend:
    - reads the cache when the tab opens and shows grouped company rules;
    - unsaved count, discard, quick setup and save;
    - the close guard and changes kept across tabs;
    - refresh is live;
    - a 502 gives the error box and status, and Try again;
    - a 409 gives the expired sign-in with Reauthenticate;
    - a new sign-in lists again;
    - a personal server edits its own rules;
    - no listing before sign-in;
    - the menu: Disconnect only after confirming, Remove through the page, Copy server URL.

### R5-5 — fixes from the independent review of round 5

An independent review of the four commits found seven problems. All are fixed, and the tests for the first and fifth were checked to fail without their fix.
1. **One server's tool list could land in another server's panel.**
   - Problem: the panel stays mounted after it closes. A slow live listing for server A that finished after the admin had opened server B showed A's tools in B. Quick setup would then have written A's tool names into B's policy.
   - Fix: every request takes a number from `toolsRequest` (bumped on reopen too), and an answer whose number isn't the latest is dropped.
2. **Saving the configuration dropped unsaved rule changes** without asking. It now closes through the same check, so the "Discard unsaved rule changes?" prompt comes up.
3. **Company rules could only be set after the tool list loaded.**
   - Problem: the old panel let an admin set them for a server they hadn't signed in to (the rules don't need the list).
   - Fix: when the list can't be had (no sign-in, or it failed), the rules are still shown and editable, grouped as "Rules set by name", with add-by-name and the footer. While the list is loading, the panel waits for it.
4. **The personal page's Disconnect prompt couldn't be cancelled** while another server's sign-in was in progress. Only the server being disconnected now holds it open.
5. **Escape in a menu or dialog also acted on the drawer behind it.** The drawer's window-level Escape handler now ignores an Escape Radix has already handled. Before, Escape on the discard prompt re-opened it, and Escape in the More actions menu closed the panel. This is a one-line change in the shared `WorkspaceRightPanel`, so it helps every drawer.
6. **A list's summary wasn't capped.** A server-chosen key over 40 characters now reads "results", and the list summary is clipped to 200 characters like the others.
7. **The per-reply budget for views went from 40 KB to 16 KB.** Messages live inside one conversation document, capped at 16 MB. Moving views (and previews) out to blob storage is the real fix for very long conversations; it is the same open question as "view full output".

Tests:
- the panel ignores a late answer for another server;
- saving the configuration with unsaved rules asks;
- Escape closes only the prompt;
- rules by name before a sign-in, saved to the policy without any listing;
- a long key gives a short summary;
- the emitter's budget test was updated.

The whole frontend suite passes (1906).
