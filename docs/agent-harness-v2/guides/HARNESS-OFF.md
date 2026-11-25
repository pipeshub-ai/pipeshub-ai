# The agent harness switches: what "off" means

The agent harness is in **beta**, and the app marks its pages and switches **Beta**. Two switches decide it:

1. **The server setting**, `AGENT_HARNESS_WORKER_ENABLED`, is **on by default** (ADR-OPS-06, amended 2026-11-20). The
   worker and its gateways start and stay idle: they claim no run, start no sandbox and make no model call until an
   organisation turns the harness on.
2. **Labs → Agent harness (Beta)**, `ENABLE_AGENT_HARNESS`, is **off by default**. An org admin turns it on for the
   organisation. Until then nobody in it uses the harness: chats run on the classic path.

To use the harness: keep the server setting on, then turn on **Agent harness (Beta)** in Labs.

**Coding engines on an upgraded install.** Engines run only behind the sandbox broker. An install upgraded under the
compatibility profile stays on `SANDBOX_MODE=docker` until the operator asks for the move, with either

```bash
PIPESHUB_AGENT_HARNESS=on ./install.sh --upgrade   # the harness, on the broker; the security profile is unchanged
PIPESHUB_HARDENED=true ./install.sh --upgrade      # the hardened profile, which includes the broker
```

The installer starts the broker and the egress proxy, and switches code execution to it once the broker is healthy.
The setting it writes into `.env` on a plain upgrade (`AGENT_HARNESS_WORKER_ENABLED=true`, the default) is not such a
request, so later plain upgrades keep docker. Then set `HARNESS_ENGINE_RUNS=true` (FLAGS.md) for the worker to run
engines.

**Resources and security.** With the server setting on, every install runs the harness worker, the Model Gateway and
Tool Gateway, even while no organisation uses the harness. A fresh install and a hardened one also run the sandbox
broker and the egress proxy; the sandbox broker holds the Docker socket. An upgrade under the compatibility profile
keeps running code as the release it came from did (`SANDBOX_MODE=docker`) and starts no broker, even though the
server setting is on by default: its harness runs loop runs, and coding engines are unavailable there until the
operator moves it to the broker (below). Idle, the harness adds about 0.6 GB to the app container and the broker about 0.1–0.2 GB
([footprint-baseline.md](../footprint-baseline.md)). Operators who do not want these processes set `AGENT_HARNESS_WORKER_ENABLED=false`; that is
the opt-out, and with it PipesHub runs as a release without the harness: RAG chat and search, connectors and indexing,
agents and workflow automation, the MCP gateway at `/mcp`, the context graph, the Slack bot, the desktop app and API
clients (PATs, OAuth apps, the SDK) work on the classic code path.

**With the server setting on and Labs off.** The organisation gets exactly what an install with the server setting
off gives it: there are no exceptions. One predicate decides it, "the harness is on for this organisation" (the
server setting and the org's Labs switch, [ADR-OPS-08](../adr/ADR-OPS-08-one-org-harness-predicate.md)), and every
behaviour a person or an organisation sees follows it; only process start-up follows the server setting alone. No
turn is admitted (`org_disabled`), so no run is claimed, no sandbox is started and the harness makes no model call.
The kill switch and infrastructure readiness only stop new runs: an org that turned the harness on keeps its
surfaces while an operator works on them. Its pages and settings stay (among them AI settings, where an admin lifts a
stop), the composer's **New agent harness** switch stays, off and disabled, saying new runs are paused, and Schedules
says its schedules won't run until the harness is back.

| With Labs off, as with the server setting off | Node / Python / browser |
|---|---|
| `/` commands are sent as typed; typing `/` opens no menu and reads no catalog; no pasted-token check in the composer | `chat-command.ts` / — / `chat-input.tsx` |
| Classic turns carry no control-plane pin (`X-PipesHub-Resolved`) and are routed by today's decider, so no `503 RUN_CONFIG_UNAVAILABLE` | `LegacyTurnHeader`, `OrgScopedRoutingDecider` |
| No gated-question check on a classic turn | `chat-turn-route.ts` |
| Deleting a chat, a person, a service account or the organisation is the soft delete (the hardened profile still erases, ADR-OPS-07) | `erasureOnDelete(orgId)`, `byOrgErasure` |
| The internal upload route keeps the platform's cap (30 MiB by default), not `HARNESS_INPUT_MAX_MB` | `storage.routes.ts` |
| An upload's record has no uploader or awaiting-send fields | — / `uploads_tracked` |
| The classic agent loop has no flow guard; an agent chat runs the saved agent, not its latest published version | — / `factory.py`, `agent_for_chat` |
| Sharing records no participants; Continue privately is not offered (503 if called) | `sessionParticipants`, `sessionForks` / — / `continuePrivately` |
| The harness's route families fall through to the classic routes; `/api/v1/agent-harness/*` answers a JSON 404 | `whenHarnessSwitchedOn`, `harnessNamespaceWhileOff` |
| No agent versions (one "Save changes"), export, import or coding-engine section; no organisation deletion section, profile extras, Jobs, Needs you or harness settings pages | — / — / `selectAgentHarnessOn`, `HarnessOnly` |
| `harnessReconcile`, `engineRuns` and `continuePrivately` read `false` | `cm_controller.ts` |
| Writing an agent, a toolset instance or an MCP instance asks Node for no control-plane capability sync (CPD-7's dual write), so nothing is sent or logged. When the harness turns on again (a Labs save from off to on, or a start with the server setting on), the capability migration's repair brings an org recorded as migrated up to date in the background; a failed repair is logged and retried on the org's next harness turn or the next start | `OrgCapabilityRepair` / `capability_sync.py` / — |
| The classic chat streams are AG-UI, as upstream's are: `RUN_STARTED` and `RUN_FINISHED` with their `runId`, text, tool and `STATE_SNAPSHOT` frames. A retried provider call streams upstream's `status` frame (`retrying`), not the `waiting_provider` snapshot; a reduced history streams no `context_compacted` frame and saves no such part | — / `factory.py` (`harness_on`) / — |
| The MCP servers page shows no hub and no tool-approval state, even where an admin turned approval on while the harness was on (`mcpHub` and `mcpToolApproval` read `false` unless `MCP_HUB` is set) | — / `mcp_servers.py` / — |

The decision and the reasons are in [ADR-OPS-06](../adr/ADR-OPS-06-one-harness-switch-off-by-default.md). This
page lists, area by area, what the server setting covers (**gated**). The security programme's platform defaults follow a
second switch, the security profile: an upgraded install keeps the previous release's behaviour until it opts in, a
fresh install starts hardened ([ADR-OPS-07](../adr/ADR-OPS-07-compatibility-profile-for-upgrades.md), "Security
profile" below).

## Turning it on and off

| Deployment | On (default) | Off (the opt-out) |
|---|---|---|
| `install.sh` | A fresh install writes `AGENT_HARNESS_WORKER_ENABLED=true` to `.env`. | `PIPESHUB_AGENT_HARNESS=off ./install.sh`, or set `AGENT_HARNESS_WORKER_ENABLED=false` in `.env` and run `./install.sh --upgrade`. |
| `install.sh --upgrade` | An `.env` without the variable gets `true`. | An `.env` that names it keeps it, so an upgrade never turns a disabled harness on. |
| Plain `docker compose` | Unset reads `true` (`${AGENT_HARNESS_WORKER_ENABLED:-true}`). | `AGENT_HARNESS_WORKER_ENABLED=false` in `.env`. |
| Helm | `agentHarness.enabled: true` (`values.yaml`). It sets `AGENT_HARNESS_WORKER_ENABLED` and `AGENT_HARNESS_RELAY_ENABLED`. | `--set agentHarness.enabled=false`. |
| From source | `backend/env.template` ships `true`; unset reads `true` in Node, Python and the image entrypoint. | `AGENT_HARNESS_WORKER_ENABLED=false` in the environment of Node and the Python services. |

In every case an organisation then turns on **Agent harness (Beta)** in Labs; it is off by default. An org that used
the harness before this default changed and never saved the Labs switch follows the new default and is off after the
upgrade until an admin turns it on (an explicitly saved value is kept).

Turning it off on an install that ran it is safe. Runs in flight stop being claimed, durable conversations stay
readable as messages, and every new turn takes the classic path. The harness's documents stay in the database until
the switch is turned on again.

## What the switch covers (gated)

Each row is what `AGENT_HARNESS_WORKER_ENABLED=false` changes. With the server setting on, an organisation with Labs
off gets the same rows (ADR-OPS-08), except the processes, which run idle, and Labs, which lists the harness's
switches so that an admin can turn it on.

| Area | With the switch off |
|---|---|
| Processes (image entrypoint `deployment/process_monitor.sh`) | No durable worker (`app.harness_main`), so no Tool Gateway `:8098`. No Model Gateway `:8096` unless `MODEL_GATEWAY_ENABLED=true` says otherwise. Test: `backend/python/tests/unit/deployment/test_process_monitor_harness_switch.py`. |
| Durable admission (Node) | `isAgentHarnessInfrastructureReady` is false, so every chat, agent, Slack and assistant turn takes the classic handler (`infrastructure_unavailable`). No harness background service starts: run sweeper, schedule firer, audit anchoring, capability refresh, relay wakeups. |
| Labs | `ENABLE_AGENT_HARNESS`, `ENABLE_SCHEDULED_RUNS` and `ENABLE_KNOWLEDGE_PUBLICATIONS` are not listed (`/platform/feature-flags/available`), not answered (`/effective`, `/platform/settings`), and a save that names one, or `agentHarness`, is refused: `400` with "The agent harness is not enabled on this install…" (`reasonCode: agent_harness_off`). Other Labs flags save as before. |
| Effective flags | `agentHarness: null`, `continuePrivately`, `engineRuns` and `harnessReconcile` are `false`; `featureFlags` has none of the three harness flags. |
| Public harness routes | Not served. Every path in the harness's own namespace, `/api/v1/agent-harness/*`, answers a JSON `404` "The agent harness is not enabled on this install." for every method. This covers admin, runs, model and engine accounts, schedules, triggers, publications, memories, inbox and data exports. Upstream answered an unknown GET with the web app's HTML page; an API client gets one plain answer instead. Families nested in classic prefixes are not mounted, so the request reaches the classic router and gets the answer a release without the harness gives. These are `/api/v1/conversations/…/runs…`, `…/workspace`, `…/sandbox`, `…/grants` and `…/compact`, and `/api/v1/agents/{key}/runs`, `…/conversations/…` and `…/export\|import`. Wiring: `app.ts` (`harnessNamespaceWhileOff`, `whenHarnessSwitchedOn`). |
| Chat commands | Node sends `/name …` to the model as typed; the server-side expansion (SLASH1/2) is off. In the browser, typing `/` opens no menu and no catalog is read. |
| Control-plane pin | `CONTROL_PLANE_PIN` unset reads `off`, so no `X-PipesHub-Resolved` pin is computed for a classic turn and no `503 RUN_CONFIG_UNAVAILABLE` path exists. An explicit value still decides. |
| Gated questions | A classic turn reads no "answers a gated question" check, so no 503 when it can't be read. |
| Classic agent loop (Python) | No flow guard on external writes (`HARNESS_FLOW_GUARD`), as before the harness. An agent chat runs the saved agent, not its latest published version (OWD-14 versions are a harness feature). |
| Query service | `/api/v1/harness/*` (the in-process run claimer) is not mounted. |
| MCP hub | `MCP_HUB` unset follows the switch, so it stays off: per-request MCP sessions. The MCP list's `mcpHub` and `mcpToolApproval` read `false`. |
| Browser | No harness entry or page. Hidden: Jobs and Needs you, the inbox poll, Security, AI access, Run settings, Memory policy, File downloads, Schedules, Memories and AI accounts (each page guarded by address too). Also hidden: the plus-menu harness switch and run gates, Continue privately, the agent builder's engine, gates and environment, agent versions (one "Save changes" as before), agent export and import, organisation deletion, "Your data" exports and the composer's pasted-token check. |

## Security profile: what an upgrade keeps (`PIPESHUB_HARDENED`)

The security programme changed platform defaults that apply whatever the harness switch says. An install upgraded
from a release without them keeps that release's behaviour (the **compatibility** profile) until the operator opts in;
a fresh install starts **hardened**. One switch decides it, `PIPESHUB_HARDENED` ([ADR-OPS-07](../adr/ADR-OPS-07-compatibility-profile-for-upgrades.md)).
Where compatibility differs from the hardened default, the service logs a warning that names the setting.

| Deployment | Compatibility (`false`, or unset) | Hardened (`true`) |
|---|---|---|
| `install.sh` (fresh) | — | Written `PIPESHUB_HARDENED=true`. |
| `install.sh --upgrade` | An `.env` from a release without the switch is written `PIPESHUB_HARDENED=false`, explicitly, and the installer says how to opt in. | An `.env` that names `true` keeps it; an `.env` this branch's earlier installer wrote ran hardened and gets `true`; `PIPESHUB_HARDENED=true ./install.sh --upgrade` opts in. |
| Plain `docker compose` | Unset reads `false`. An older `.env` renders and starts. | `PIPESHUB_HARDENED=true` in `.env`. The copied `env.template` says `true`. |
| Helm | `hardened: false` (the chart cannot tell an install from an upgrade under `helm template`). | `--set hardened=true`; NOTES says so on every compatibility release. |
| From source | `backend/env.template` leaves it unset. | `PIPESHUB_HARDENED=true` in the environment of Node and the Python services. |

| Behaviour | Compatibility (upgrade default) | Hardened (fresh default) | Per-item override |
|---|---|---|---|
| Store credentials (SEC-D-06) | A missing `REDIS_PASSWORD`, `QDRANT_API_KEY`, `KAFKA_PASSWORD` or `MONGO_PASSWORD` starts that store as before (no `requirepass`, no API key, Kafka on PLAINTEXT, MongoDB without authentication), with a warning in its log; the app warns about an anonymous store. `install.sh --upgrade` still generates the missing ones (invisible: each store reads it at start). | The app refuses a store that answers anonymously. | `STORE_AUTH_CHECK=enforce\|warn` |
| `SECRET_KEY` strength (SEC-D-05) | A weak key is accepted, with a warning. The installer does not rotate it. | A weak key stops the service; the installer moves it to `SECRET_KEY_PREVIOUS` and generates one. | — |
| Configuration encryption key (ADR-KEY-02) | New values are written under the SHA-256 key the previous release reads, so a rollback reads everything. HKDF values are still read. | New values are written under the HKDF key; SHA-256 values are still read. | `SECRET_KEY_KDF=sha256\|hkdf` |
| App port (SEC-D-08) | Compose publishes on every interface, as before. | The installer writes `APP_BIND_ADDRESS` (127.0.0.1 unless a public URL is set). | `APP_BIND_ADDRESS` |
| MongoDB | A replica set (`rs0`) either way. Authentication (the keyfile) only with a password: an install whose `MONGO_PASSWORD` was empty runs without it, as before. `install.sh --upgrade` creates the root user and writes the password first (R50OFF-FU-2), so nobody is locked out. | The same; the installer always writes a password. | `REPLICA_SET_AVAILABLE=false` for an external standalone |
| Code execution | `SANDBOX_MODE` unset reads `docker`: the app creates sandboxes through the Docker API proxy, as before (SEC-A-04's limits apply to each). The sandbox broker and egress proxy are not started (their own Compose profile, `sandbox-broker`). With no proxy token set, the proxy admits the app without one and warns. | The installer moves to the broker (`SANDBOX_MODE=broker`, profile `sandbox-broker`); the proxy refuses a caller without its token, and the app holds none in broker mode. | `SANDBOX_MODE`; `PIPESHUB_AGENT_HARNESS=on ./install.sh --upgrade` moves a compatibility install to the broker |
| Node's internal listener `:3002` | Not started unless the harness is on or `SANDBOX_MODE=broker`. | The same. | — |
| Refresh tokens (ADR-WEB-02 D9) | A refresh with a bearer token answers as before and does not rotate. The cookie session, and a client that sends `X-PipesHub-Refresh-Rotation: 1`, rotate; a token of a family that rotated is still checked for reuse. | Every refresh rotates. | `X-PipesHub-Refresh-Rotation` (per request) |
| Framing and headers (ADR-WEB-01) | No `frame-ancestors` and no `X-Frame-Options`: the app may be framed as before. The CSP is report-only (violations reach `/api/v1/csp-report`); no HSTS. `STRICT_MODE=true` (the previous release's opt-in) sends the hardened set. | `frame-ancestors 'none'`, `X-Frame-Options: DENY`, enforced CSP, HSTS on https. | `CSP_FRAME_ANCESTORS`, `CSP_REPORT_ONLY`, `CSP_*_SRCS` |
| `GET /api/v1/health`, `/services` | The full report, as before. | `status` (and the harness's) without an org admin's token. | — |
| Org deletion (ORGDEL) | `confirmName` optional (checked when sent); with the harness off for the org, a soft delete as before, no erasure. | `confirmName` required; the org's data is erased. | — |
| Conversation deletion (E7) | With the harness off for the org, a soft delete as before; no erasure job. | Erasure runs. | The org's harness on (ADR-OPS-08): erasure runs |
| Boolean env flags (F3) | A value outside `1/true/yes/on/0/false/no/off` is read as the previous release read it (or the default) with a warning naming the flag. | The service stops and names the flag. | — |
| Helm (ADR-OPS-04) | No NetworkPolicy, stores as before (their user and ownership unchanged), the app's root filesystem writable. | Namespace default-deny with one allow per component pair, non-root stores (`fsGroupChangePolicy: OnRootMismatch`), read-only root filesystem. | `networkPolicy.*`, the store and app `securityContext` values |

Opting in is one step and changes exactly the rows above. Before it, back up `.env` and the KV store: once new values
are written under the HKDF key, a rollback to the previous release cannot read them
([CHANGELOG upgrade notes](../../../CHANGELOG.md)).

## What else changed for every install

| Change | Why it stays | Upgrade |
|---|---|---|
| **Additive schema**: Mongo control-plane collections and indexes, Arango/Neo4j `agentVersions` and `knowledgePublications`, PostgreSQL `harness` migrations, nullable chat columns. | Created at startup; unused while off. | None. A rollback ignores them. |
| **Classic chat internals**: the classic loop runs on the shared kernel seams (`agent_loop_lib`); citations and streaming refactored; an MCP tool whose server is gone is refused ("connect again") instead of called. A failed tool call's `TOOL_CALL_RESULT` carries `errorCode` (and `nothingSent` for a write) with empty `content`, not the error's text (ERR-UX: the model's tool message keeps the detail). | Shared code with the harness. | Covered by the classic e2e specs (R50-OFF lane). |
| **Recents toggle and activity wording** in the chat sidebar and timeline. | Classic UI improvements merged with the programme. | None. |
| **The control-plane capability-sync route and admin API** stay mounted. | The query service posts to it after agent, toolset and MCP writes. | None. |

## Proving it

- Switch on + Labs off ≡ switch off, one row per item (ADR-OPS-08): Node
  `tests/modules/agent-harness/org-harness-matrix.test.ts`, Python `tests/unit/harness/test_org_harness_matrix.py`,
  browser `frontend/lib/store/__tests__/harness-org-matrix.test.ts` and the composer, General page, agent builder and
  agents sidebar component tests.
- Unit and cohesion: Node `tests/modules/configuration_manager/controller/cm_controller.test.ts` (Labs off),
  `tests/modules/agent-harness/compose-defaults.test.ts`, `flag-matrix.test.ts`, `pin-switch.test.ts`. Python
  `tests/unit/deployment/test_process_monitor_harness_switch.py`, `tests/unit/harness/test_flag_matrix.py` FB-3, and
  C-16 (`flags.json` defaults everywhere). Frontend `app/components/harness-gate/__tests__/harness-off.test.tsx` and
  the chat-input "switched off" block. Installer `installer_test.sh` "Agent harness switch".
- Lanes: `deployment/e2e/run.sh` with `E2E_HARNESS=off` runs a stack with the switch off. the project tracker R50OFF rows hold
  the counts: the classic specs, no harness container, upstream main's installer upgraded in place, and the
  harness-on smoke and journey gate.
