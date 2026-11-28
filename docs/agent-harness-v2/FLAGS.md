# Agent harness: feature flags

The one list of every switch that turns agent-harness behaviour on or off, who may change it, what it
needs, and what happens when something it needs is off. The data is
[`contracts/harness/flags.json`](../../contracts/harness/flags.json); this page renders it, and the cohesion
test C-16 (`backend/python/tests/cohesion/test_c16_flag_matrix.py`,
`backend/nodejs/apps/tests/cohesion/c16-flag-matrix.test.ts`) fails when the two differ, when code reads a
flag the matrix does not list, or when Compose, the env templates, Helm or `install.sh` ship a different
default. Edit `flags.json` first, then this page.

## What users see

**The harness switches (ADR-OPS-06, amended 2026-11-20).** `AGENT_HARNESS_WORKER_ENABLED` is on by default: the
worker and its gateways run idle until an org admin turns on Labs **Agent harness** (`ENABLE_AGENT_HARNESS`), which is
off by default. `AGENT_HARNESS_WORKER_ENABLED=false` runs a release without the agent harness (no worker, gateway,
harness route, Labs flag or UI; the classic chat path exactly; `CONTROL_PLANE_PIN` unset reads `off`).
[guides/HARNESS-OFF.md](guides/HARNESS-OFF.md) lists what it covers and what it does not. Everything below describes
an install with the switch on.

**The security profile (ADR-OPS-07).** `PIPESHUB_HARDENED` is the platform's other switch, independent of the harness:
unset or `false` keeps the previous release's behaviour (an upgraded install), `true` runs the security programme's
hardened defaults (a fresh install; `install.sh` writes it). It is not a harness flag and is not in the matrix below;
[guides/HARNESS-OFF.md](guides/HARNESS-OFF.md) "Security profile" lists what it changes.

With the switch on and the org's Labs **Agent harness** turned on (off by default), **agent chat runs on the durable harness** (DUR3): the relay and worker run, and an org's agent and
assistant chats are admitted durably whenever MongoDB is a replica set without an arbiter (see "Where the
journal is durable" below: not on Helm's own `values.yaml`). Conversation erasure always runs (E7),
harness on or off.

| Durable path for the org | Engine runs (`HARNESS_ENGINE_RUNS` on, an engine agent) | What users see |
|---|---|---|
| on (Labs **Agent harness**) | off (default) | Durable chat; code and documents through `run_code` |
| on | on | Durable chat; an engine agent's runs are coding-agent runs in a sandbox (E1), with approvals and crash recovery |
| off (Labs off, the default; the kill switch; or a standalone MongoDB or one with an arbiter) | — | Today's product; code and documents through `run_code` |

Stage-1 `code_task` (Labs "Enable Code Tasks", `PIPESHUB_CODE_TASK`) was retired by S1R: `run_code` covers in-loop data and office work in every chat mode it reached, and
durable engine runs cover coding-agent work.

"Durable path for the org" means: `REPLICA_SET_AVAILABLE`, `AGENT_HARNESS_RELAY_ENABLED` and
`AGENT_HARNESS_WORKER_ENABLED` all `true` and a durable journal (a replica set, not standalone or PSA), the
org's Labs flag `ENABLE_AGENT_HARNESS` ("Agent harness") on, the operator's env kill switch `AGENT_HARNESS_KILL_SWITCH` off, and the
chat's model family allowed. All of these hold at defaults on Compose, and on Helm with `values-cloud.yaml`,
`values-eks.yaml` or `values-local.yaml`. They do not hold on Helm's own `values.yaml`: its MongoDB is two data
members and an arbiter, the journal probe answers `arbiter`, and durable chat stays off there (chat runs on today's
loop). A request whose chat mode the durable loop does not run (`planExecute`, Universal Agent Mode, project chats)
stays on legacy.

**Where the journal is durable.** What Node's startup probe (`probeJournalDurability`) answers on the MongoDB each
shipped file deploys; C-16 FM-10 runs the probe on each and fails when this table, `flags.json` or the files differ.
Anything but `majority` keeps durable chat off, and `GET /api/v1/health` says why in `agentHarness.detail`.

| Deployment | Journal probe | Durable chat |
|---|---|---|
| Compose (`docker-compose.yml`) | `majority` | on |
| Helm, `values.yaml` | `arbiter` | off |
| Helm, `values-local.yaml` | `majority` | on |
| Helm, `values-cloud.yaml` | `majority` | on |
| Helm, `values-eks.yaml` | `majority` | on |
| Helm, `values-eks.yaml` + `values-eks-single-az.yaml` | `majority` | on |

Helm's own `values.yaml` keeps its arbiter for now (STATUS: deferred Helm items); install with one of the other
presets, or set `mongodb.replicaCount=3` and `mongodb.arbiter.enabled=false`, for durable chat.

**Labs and existing installs.** Labs records only the flags an admin changed (UX1); a flag with no saved value
follows its release default. `ENABLE_AGENT_HARNESS` was on by default from DUR3 until BETA (2026-11-20), when it
became off by default: an org that used the harness without saving the switch is off after the upgrade until an admin
turns it on, and an org whose admin saved it keeps that value.

### Labs: three agent-harness flags (CTL-5)

Labs offers **"Agent harness"** (`ENABLE_AGENT_HARNESS`), **"Scheduled and Triggered Agent Runs (Experimental)"** and
**"Knowledge Publications (Experimental)"**. Turning on "Agent harness" brings durability and, with it, the default
chat's Agent mode on the harness, collaborators' turns in conversations shared for editing, the activity drawer
("View activity") and suggested next questions; none of them has a switch of its own any more. The Labs flags they
had (`ENABLE_DURABLE_ASSISTANT_AGENT`, `ENABLE_COLLABORATOR_TURNS`, `ENABLE_AGENT_ACTIVITY_PANEL`,
`ENABLE_FOLLOWUP_SUGGESTIONS`) and the withdrawn `ENABLE_WORK_UI` are retired: a stored key of a flag Labs no longer
lists is ignored, never shown, and dropped by the next Labs save. Main's own product flags sit beside them, and main
(#4166) shows its internal ones in an "advanced" accordion, among them `ENABLE_NAMED_ENTITY_EXTRACTION` (off; #3975,
read by the shared knowledge tools on both loops, live per call like `ENABLE_CONTAINER_PERMISSION_FILTER`); the three
harness flags stay in the main list (SYNC3 §6.1). The kill switch left Labs for the operator's env
(below): a stored Labs kill switch that was on reads as "Agent harness" off for that org, so retiring it never turns
the harness back on unseen.

The default chat's Agent mode on the harness names the tools of a user's own MCP servers at admission (MCPLIVE).
Without the MCP hub (`MCP_HUB` off) it discovers them live, as today's loop does (the same `MCPToolProvider`, the
caller's own credentials, all servers at once, 5 s at most), and pins each tool's descriptor digest; the claim
discovers again and refuses a tool that changed in between (`CapabilityLost`), as it does for hub-approved tools.
Under the hub, a server's discovered tools are named at once (D-MCP-5: OSS requires no admin approval; an edition
may require it, and then only approved tools are named); the description scanner holds back the tools it flags. A server whose tools could not be named (discovery failed or timed out,
or nothing approved) is left out of that run, which proceeds with one `mcp_unavailable` notice per server (CTL-5b,
logged `run.assistant_mcp_unavailable`): the server's name, why (`sign_in`, `unreachable`, `not_approved`,
`no_tools`, `flagged`, from what its listing recorded at admission) and, for an OAuth server whose sign-in the person can redo,
the instance the chat's "Sign in to …" opens (MCPNOTE-1). Only the run's owner reads it. MCP never sends a turn to
today's loop.
Policy layers stored before CTL-5 may carry the retired rollout keys: every reader (Node's `readPolicyLayer`, the
worker's live-layer loader) strips exactly those and keeps the rest of the layer
(`contracts/harness/vectors/retired-layer-keys.json`).

### Rolling back (mid-flight)

Two levels, the same effect. An org admin turns Labs → **"Agent harness"** off for the org; each person stops their own
runs in flight from the chat. The operator sets the env setting
**`AGENT_HARNESS_KILL_SWITCH=true`** (Compose `.env`, Helm `agentHarness.killSwitch`) on Node and the egress proxy for
the whole instance, which also cancels the harness runs in flight within 60 s (restart to apply).
From the next admission:

- every new chat turn goes to today's loop (`kill_switch`), and the browser is told so by the effective-flags answer;
- a run already admitted on the harness (queued, running, waiting for an approval or an answer) finishes or is
  cancelled there, its stream and reattach keep working, and a later turn of the conversation runs on today's
  loop with the durable turns as its history;
- a message that was queued behind a durable run (`next_run`) is refused with `kill_switch`, never admitted as a new
  durable run;
- set on the egress proxy as well (and the proxy restarted), it ends the sandboxes' egress at once: the proxy binds
  no sandbox serving a harness run and drops the ones it restored, so an engine run in flight loses its model and
  tool calls (C6.14). One org or one conversation alone is stopped with the proxy's control call
  `POST /v1/revocations {"orgId", "sessionId"?}` (worker credential): those sandboxes lose egress now and are never
  bound again;
- what to watch: `pipeshub_agent_chat_turns_total{path}` moves to `legacy` and
  `pipeshub_agent_harness_run_claims_total` stops growing once the runs in flight end.

**Rolling back to a release before PERF-B** (history by reference). That release cannot read a run that names
its history (`input.historyRef`): its worker refuses the claim and the run waits. So, in this order:

1. set `HARNESS_RUN_HISTORY_BY_REFERENCE=false` on every Node and restart them: new follow-up turns are written
   with their history in the run document, as that release wrote them;
2. let the queued follow-up turns admitted before step 1 be claimed (seconds; none is left when
   `db.agentRuns.countDocuments({status: "queued", "input.historyRef": {$exists: true}})` is 0);
3. roll back. Runs claimed before the rollback keep running: only the claim reads the history.

Left on during a rolling upgrade: a turn the new release admitted waits for a new Node to claim it, and is never
run without its history.

**Rolling back to a release before CPD-6 while `CONTROL_PLANE_PIN=on`** (the pin). A run admitted under `on`
requires `resolved_config` of its worker, and a release before CPD-6 never claims such a run: its Node filters its
own worker's claim (`claim.filtered`). The run is not lost, failed or run without its policy: it stays `queued`
(or suspended) and runs when a worker that reads pins returns. So, in this order:

1. set `CONTROL_PLANE_PIN=shadow` on every Node and restart them: new admissions carry no pin and require nothing
   of the worker;
2. let the pinned runs finish; none is left when
   `db.agentRuns.countDocuments({status: {$nin: ["completed", "failed", "cancelled"]}, "requires.features": "resolved_config"})`
   is 0. A run suspended on an approval counts until it is decided or its deadline passes;
3. roll back.

Rolled back without steps 1 and 2: the pinned runs wait, each until its own deadline, and run if the newer release
returns before it (fault suite, CPD6-39).

Turning the relay and worker off (`AGENT_HARNESS_*_ENABLED=false`) is a second, coarser step for after the runs in
flight ended: with them off, a run still queued is never claimed and fails at its deadline.

## Turning things on (operators)

| To get | Set |
|---|---|
| Durable chat (Compose) | nothing: the base file runs the relay and worker (the model gateway follows the worker), and Labs → "Enable Durable Agent Harness" is on unless the org saved it off |
| Durable chat (Helm) | `-f values-cloud.yaml`, `values-eks.yaml` or `values-local.yaml` (MongoDB without an arbiter); `agentHarness.enabled: true` is the default (both relay and worker). The chart's own `values.yaml` runs an arbiter, so durable chat stays off there (reason `arbiter`) |
| Today's loop for every chat (rollback) | Labs → "Agent harness" off, or `AGENT_HARNESS_KILL_SWITCH=true` on Node for the whole instance (see "Rolling back") |
| Engine agents (a coding agent in a sandbox, durable) | durable chat on, `SANDBOX_MODE=broker` (the default), `HARNESS_ENGINE_RUNS=true` on the worker; an org admin makes an agent an engine agent (`engineProfile`) |
| Which engines an org's runs may use | nothing: every released engine, the ACP agents (opencode over ACP, Gemini CLI, Goose, Qwen Code, DeepSeek Harness) too, where its image is installed; an agent's `engineProfile.engines` narrows its own runs, and the first released engine it admits runs |
| Stop durable admissions now | `AGENT_HARNESS_KILL_SWITCH=true` on Node and the egress proxy, restarted (cancels runs in flight within 60 s) |
| Stop one run | its person stops it from the chat (`POST /api/v1/conversations/{sessionId}/runs/{runId}/cancel`) |
| Traces of durable runs (OpenTelemetry) | `HARNESS_OTEL=true` on Node, the worker and the model gateway, `OTEL_EXPORTER_OTLP_ENDPOINT` at your collector (OTLP/HTTP); prompts and tool arguments stay off the spans unless `OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT=true` (see guides/operator-guide.md, "Tracing") |
| Ask before writes in durable runs | `/services/agentHarness/approvals` = `{"default": {"writes": "ask"}}`, set by the operator in the configuration store |
| Scheduled and triggered agent runs | Durable chat on for the org, then Labs → "Scheduled and Triggered Agent Runs (Experimental)"; users create schedules with `POST /api/v1/agent-harness/schedules` |
| Knowledge publications (D21) | Labs → "Knowledge Publications (Experimental)"; an owner of a knowledge base or record group (or an org admin) publishes it with `POST /api/v1/agent-harness/publications` and revokes with `DELETE /api/v1/agent-harness/publications/{publicationId}` |
| Governed memory (P7.3) | `/services/agentHarness/memory` = `{"orgs": {"<orgId>": {"mode": "user", "review": "auto"}}}`, set by an org admin (`PUT /api/v1/agent-harness/admin/memory-policy`); `agent` also lets agents keep memories, which an admin reviews |
| STDIO MCP servers in sandboxes (D23) | `MCP_STDIO_RUNNERS=true` on the query service and the worker, with the sandbox broker running (`SANDBOX_MODE=broker`); the runner image (`MCP_RUNNER_IMAGE`, default the sandbox image) must hold each server's program; an instance reaches only the hosts it declares (a catalog entry's, or a custom server's "Hosts it reaches" at setup), through the egress proxy |
| The MCP hub (P2.8): cached tool lists, pooled sessions, the description scanner's hold on flagged tools, pinned URLs (approved tools only where an edition requires an admin's approval, D-MCP-5) | for harness runs, on wherever the harness is (`MCP_HUB` unset follows `AGENT_HARNESS_WORKER_ENABLED`; D-MCP-1; the classic agent chat, builder and agent save keep x4's behaviour), or for every surface `MCP_HUB=true` on the query service, the worker and the connectors service; instances that predate the default are baselined once (their current tools approved, except those the description scanner flags); where an edition requires it (D-MCP-5; never in OSS), an admin approves each MCP instance's tools (`GET`/`PUT /api/v1/mcp-servers/instances/{instanceId}/manifest`): until then its tools are not registered (`mcp_tool_unapproved`) |
| HTTPS from MCP runners to their listed hosts (P5.4b) | `docker compose … -f docker-compose.egress-tunnels.yml` (sets `EGRESS_CONNECT_TUNNELS=true` on the egress proxy and the PipesHub service, and gives the proxy a network off the host; it is also the sign-in add-on people's own Claude accounts need); list `host:443` among the hosts the instance declares. The proxy cannot see inside a tunnel: anything sent through a tunnel to an allowed host leaves unread; it checks only the `CONNECT` line, the allowlist and the resolved address |
| Two instances of one toolset or MCP type on one agent (P2.2) | `INSTANCE_TOOL_IDS=true` on the query service, the worker and the connectors service; agents saved before admin-created toolset instances are moved to their instance with `python -m app.harness.tool_identity.migrate` (dry-run; then `--apply`; `--revert` undoes it) |
| Reconcile unknown effects by a read (P2.9) | `HARNESS_RECONCILE=true` on the worker; whatever it says, the chat shows an unknown effect as a card and the person the run acts for says whether it happened (UX3) |
| Hand reasoning models their own thinking back on a resumed tool turn (P4.3) | `HARNESS_PROVIDER_STATE=true` on the worker, with `PIPESHUB_AGENT_TRANSPORT=direct` (the LangChain transport keeps none) |
| Move a run whose model provider is down to the agent's other models (P4.7, G-48) | On by default (`HARNESS_MODEL_FALLBACK`, WP-100); an agent that grants more than one model gets up to two alternates (never on a content filter), for loop runs in the worker's kernel and for engines in the Model Gateway. `HARNESS_MODEL_FALLBACK=false` on the `pipeshub-ai` container turns it off |

`install.sh` writes only `SANDBOX_MODE` (and `COMPOSE_PROFILES`, `ROTATE_SIGNING_SECRETS`) of these; the
harness is turned on with the overlay or `.env`.

Every boolean env flag reads the same way, in Python (`app/utils/env_flags.py`), Node
(`src/libs/utils/env-flags.ts`) and the image's process monitor (`env_flag`): `1/true/yes/on` and
`0/false/no/off` in any case; unset or blank is the flag's default; any other value stops the process at
startup with an error naming the flag (`InvalidEnvFlag`). C-16 fails on a read that bypasses the helper.

## Flags

| Flag | Kind | Scope | Default | Changed by | Gates | Shipped |
|---|---|---|---|---|---|---|
| `REPLICA_SET_AVAILABLE` | env | deployment | `false` | operator (follows the MongoDB topology: Compose base file, Helm mongodb.architecture) | MongoDB transactions; one of the durable harness's infrastructure conditions (isAgentHarnessInfrastructureReady) | Compose: `true`; Helm: `mongodb.architecture="replicaset"` |
| `AGENT_HARNESS_RELAY_ENABLED` | env | deployment | `true` | operator (Compose .env; Helm agentHarness.enabled, the harness switch, sets it with AGENT_HARNESS_WORKER_ENABLED). Alone it starts nothing: durable runs need AGENT_HARNESS_WORKER_ENABLED too (ADR-OPS-06) | durable runs: the outbox relay half of isAgentHarnessInfrastructureReady and the dispatch capability probe | Compose: `true`; harness overlay: `true`; backend/env.template: `true`; Helm: `agentHarness.enabled=true` |
| `AGENT_HARNESS_WORKER_ENABLED` | env | deployment | `true` | operator: the agent harness's switch (ADR-OPS-06, amended 2026-11-20), on by default and idle until an org admin turns on Labs ENABLE_AGENT_HARNESS (off by default); false is the opt-out; install.sh writes true on a fresh install or an upgrade whose .env does not name it and keeps a value an .env already names; Helm agentHarness.enabled | the whole agent harness (ADR-OPS-06): the worker half of isAgentHarnessInfrastructureReady; the image starts python -m app.harness_main; the model gateway follows it unless MODEL_GATEWAY_ENABLED says otherwise; the MCP hub follows it for harness runs unless MCP_HUB says otherwise; off, Node serves none of the harness's Labs flags, public routes or browser surfaces, `CONTROL_PLANE_PIN` unset is off, and the classic loop runs without the flow guard and on the saved agent | Compose: `true`; harness overlay: `true`; Compose env.template: `true`; backend/env.template: `true`; Helm: `agentHarness.enabled=true`; install.sh: see guides/HARNESS-OFF.md |
| `MODEL_GATEWAY_ENABLED` | env | deployment | `follows AGENT_HARNESS_WORKER_ENABLED` | operator (Compose .env) | the model gateway process (python -m app.model_gateway_main) in the app container | Compose: blank |
| `SANDBOX_MODE` | env | deployment | `unset` | operator (install.sh wizard, Compose .env, Helm config.sandboxMode) | run_code's backend; broker mode also provisions the worker credential and gateway keys with the harness off (prepareAgentHarnessAuthority) | Compose: `broker`; env.template: `broker`; Helm: `config.sandboxMode="broker"` (`values-local.yaml`: `local`, with `sandboxAllowLocal`, as on main); main's build-from-source, beta and prod-legacy Compose files: `docker` through main's Docker policy proxy, as on main; install.sh: `broker (slim and full; an existing broker|docker|e2b|local is kept); blank for eval` |
| `SANDBOX_ALLOW_NETWORK` | env | deployment | `false` | operator (Helm config.sandboxAllowNetwork; Compose .env) | network for run_code's sandbox (package installs); public addresses only: private, link-local/metadata and egress-bridge addresses are firewalled (main #3780; under broker mode the broker installs the rules, and refuses open egress it cannot filter: egress_firewall_unavailable) | Compose: `false`; Helm: `config.sandboxAllowNetwork=false` |
| `PIPESHUB_SANDBOX_ROOT` | env | deployment | `unset` | operator (the query service's and the worker's environment) | where run_code's local, Docker and broker executors keep each run's files, and the only folders an artifact is read back from: unset, `<tempdir>/<name>-<uid>`; set, `<value>/<name>`; each made 0700 and refused (`SandboxRootUnsafe`) unless it is a real directory the running user owns that no other user can write (R13-ROOT) | none ship it |
| `AGENT_HARNESS_BUDGET_LEDGER` | env | deployment | `true` | operator (escape hatch) | the durable worker charging every model call to the run's budget-ledger account (P6.5); off: model calls are not metered by the worker and deep runs size no child shares from the ledger | not set anywhere |
| `HARNESS_ENGINE_RUNS` | env | deployment | `false` | operator (the worker's environment; Compose .env) | whether a durable worker registers the engine kernel and serves its tool gateway (E1); off: it advertises no executor_engine, so Node never hands it an engine run | Compose: `false` |
| `HARNESS_ENGINE_SNAPSHOTS` | env | deployment | `false` | operator (the worker's environment) | encrypted sandbox snapshots of engine runs (P5.9b): the sandbox's HOME caches, sealed with the session key, taken at autosave points and at turn end and restored before the engine opens on resume and in the session's next run; off: none taken or restored | not set anywhere |
| `AGENT_HARNESS_OFFERING` | env | deployment | `managed` | operator (the install's offering; PipesHub has no licence key or edition marker to read it from) | which vendor-rule rows apply to model access (A1): a row limited to other offerings is refused offering_not_permitted at admission and at the model gateway; values `oss`, `managed` (an edition may add its own) | not set anywhere |
| `CONTROL_PLANE_PIN` | env | deployment | `on` | operator (rollout of the control plane, 05-migration-plan §1 rule 6: on by default since C4.04, after the shadow release recorded every gate; shadow, then off, to roll back) | shadow: the control plane pins each admission beside today's decision, resolves its effective set beside the agent's definition, and counts every difference in pipeshub_cp_shadow_mismatch_total{dimension}, acting on none, with a startup warning (cp.pin_shadow); the Labs switches are read once per org head; on: the pin decides (CPD-5), and items not visible to the acting user are dropped at admission (CPD-7); off: neither, and the admin API's explain and grantable routes (CPD-11 R21, R22) answer 503 `CP_UNAVAILABLE`; values `off`, `shadow`, `on` | not set anywhere |
| `CONTROL_PLANE_POLICY_STORE` | env | deployment | `dual` | operator (rollback: off returns to the release before CPD-4; on, the flip, only together with `CONTROL_PLANE_PIN=on`) | where the five admin policy documents live (CPD-4): dual, the control plane's policy store with every save also written to the KV documents, and ETag / If-Match on the admin policy routes; off, the KV documents only; in every mode except off, the admin API (CPD-11 R10–R13) reads sections, their history and rollback, and writes only the paths no KV document holds (budgets, models.routes/roleCostCeilings, rollout.durable.*, catalog.approval/delegation), which a pin in dual reads from the store (D5); off: those routes answer 503; on, the store only (CPD-10): allowed only with `CONTROL_PLANE_PIN=on` (any other pair refuses to start); every run then requires `resolved_config` whatever workers advertise, and nothing reads a KV policy document: a run without a pin, the model gateway and the agent-write check of an engine profile read the org's live layer, today's loop takes its flow rules from `X-PipesHub-Resolved` or refuses; values `off`, `dual`, `on` | not set anywhere |
| `SANDBOX_WARM_POOL_MAX` | env | deployment | `0` | operator (the sandbox broker's environment) | the sandbox broker's warm pool of engine sandboxes (P5.9a): the most it keeps ready, below what probed host memory allows (warm sandboxes hold at most min(memory x 0.25, memory - held memory), counted per sandbox by its own memory); 0 keeps none. On, the broker reports warmPool=true and engine runs use the tar workspace strategy (a warm sandbox holds scratch mounts only) | not set anywhere |
| `MCP_STDIO_RUNNERS` | env | deployment | `false` | operator (the query service's and the durable worker's environment) | where STDIO MCP servers run (P5.10, D23): on, every STDIO server for agents (discovery and sessions, legacy and durable) and the admin discover-tools routes runs in a per-owner MCP runner sandbox through the sandbox broker, and an instance no runner can serve is a load failure with the runner's reason code, never a host process; off, a child process of the query service or worker (today); SSE and streamable HTTP servers are unchanged either way | not set anywhere |
| `HARNESS_CONTEXT_COMPILER` | env | deployment | `false` | operator (the durable worker's environment) | the context compiler on durable loop runs (P4.1/P4.2): each request compiled from the journal in fixed cache bands (sorted, append-only tools; volatile content in the tail; other principals' and external content quoted), and the reduction ladder on the provider's usage (context.cleared above 50% of the window, context.compacted through a metered summarising call above 80%); off: requests are assembled by the turn shaper as before (byte-equal) | not set anywhere |
| `HARNESS_AUTO_COMPACT` | env | deployment | `true` | operator (the durable worker's environment; the off switch) | the reduction ladder's compact rung on durable loop runs whose requests are shaped (HARNESS_CONTEXT_COMPILER off, LC2FIX-2): when the provider counts a request above 80% of the model's window, everything before the last three replies is summarised through a metered call and journaled as context.compacted before the next request, which the shaped history then applies; no clearing rung, and below 80% requests are unchanged. With HARNESS_CONTEXT_COMPILER on, its own ladder applies; off: a shaped request is never reduced on its own (the person's /compact still is) | not set anywhere |
| `HARNESS_DEEP_ROUTING` | env | deployment | `true` | operator (the durable worker's environment; the off switch) | DEEPROUTE: a durable loop run that may hand off to a coding engine (x.pipeshub.codeTask pinned, the org's "Hand long work to the coding engine" on) hands work bigger than a chat reply to the engine on its own: up front when the goal step judges the request research, analysis across many items, planning or a long report, and mid-run when the run crosses HARNESS_DEEP_ROUTING_THRESHOLDS while still gathering. Off: code_task is still offered for the model to choose, nothing hands off on its own | not set anywhere |
| `HARNESS_OTEL` | env | deployment | `false` | operator (Node's, the durable worker's and the model gateway's environment) | OpenTelemetry traces of durable runs (P9): on, Node records each run's admission span and the worker and model gateway their invoke_agent, sandbox acquire, execute_tool and chat spans (GenAI semantic conventions), exported over OTLP/HTTP to OTEL_EXPORTER_OTLP_ENDPOINT; off, no-op tracers and no OpenTelemetry import. Spans carry ids, enums, token counts and timings, never content (see OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT) | not set anywhere |
| `OTEL_INSTRUMENTATION_GENAI_CAPTURE_MESSAGE_CONTENT` | env | deployment | `false` | operator (the durable worker's and the model gateway's environment) | content on harness spans (P9, the GenAI conventions' own opt-in): on, the chat span carries gen_ai.input.messages and gen_ai.output.messages and the execute_tool span gen_ai.tool.call.arguments and gen_ai.tool.call.result; off, no prompt, answer or tool argument is recorded. Read only when HARNESS_OTEL is on | not set anywhere |
| `MCP_HUB` | env | deployment | `follows AGENT_HARNESS_WORKER_ENABLED` | operator (the query service's, the durable worker's and the connectors service's environment) | the MCP hub (P2.8), for agents' MCP servers (legacy and durable) and the connectors service's discovery routes: on, SSE and streamable-HTTP URLs are resolved once, refused when they reach a loopback, link-local or metadata address, or a private one the deployment blocks (main's URL guard, reason blocked), and pinned; discovered tool lists are cached per (org, instance, credential owner, instance configuration) and dropped on tools/list_changed; MCP sessions (HTTP, and STDIO servers in P5.10 runners) are pooled per (org, instance, credential owner) across requests; tool names are checked against the MCP rule and exported as valid function names; only tools whose name, description and schema an admin approved (on by the default, an install's instances that predate it are baselined once: their current tools approved, except those the description scanner flags) (GET/PUT /api/v1/mcp-servers/instances/{instanceId}/manifest) are registered, a changed one is disabled and the drift recorded; off, today: per-request sessions, no cache, no approval gate, names as they are, URLs connected as configured, and the manifest routes answer 404 | not set anywhere |
| `CONTROL_PLANE_CREDENTIALS` | env | deployment | `false` | operator (the connectors service's, the query service's and the durable worker's environment), after the credential migration (`python -m app.harness.credentials.migrate --apply`) and again after the flip | where toolset and MCP credentials live (CPD-8): on, they are read from control-plane bindings first (a revoked binding is final, a copy older than its old record is not served), the legacy credential routes write through Node's audited binder (the old paths become read-only, narrowings excepted), and one TokenRefresher renews bindings in place of the toolset and MCP refresh services; off, today: the old paths and the two refresh services | not set anywhere |
| `EGRESS_CONNECT_TUNNELS` | env | deployment | `false` | operator (the egress proxy's and the harness worker's environment; deployment/docker-compose/docker-compose.egress-tunnels.yml sets it on both and gives the proxy, and only the proxy, a network off the host; the worker advertises `vendor_signin`, a person's own vendor sign-in, only with it on: SIGNGAP) | opaque CONNECT tunnels in the egress proxy (P5.4b): on, a sandbox may open a TLS tunnel to a host:port on its own egress allowlist that has no token route and is not a gateway; the name is resolved once through P2.8's SSRF policy, every address checked (private, loopback, link-local and metadata, CGNAT, blocked CIDRs refused: destination_private_address), the tunnel dials only the checked addresses, an IP literal is refused (tunnel_ip_literal), and each tunnel is audited (egress.tunnel_opened / egress.tunnel_closed); the proxy cannot see inside the tunnel, so the credential-shape scan no longer covers that traffic; off, as P5.4: CONNECT is refused tunnel_refused and HTTPS from a sandbox has no way out | not set anywhere (the egress-tunnels overlay: `true`) |
| `EGRESS_STATE_REQUIRED` | env | deployment | `follows AGENT_HARNESS_STATE_URL` | operator (the egress proxy's environment) | whether the egress proxy may start without its durable state (P5.4b, H1): on, a proxy that cannot reach Redis or has no SECRET_KEY at start refuses to start (egress_state_unavailable, logged once at error; Compose restarts it until Redis answers), so it never serves on in-memory bindings a restart would lose; off, as P5.4b: it starts with in-memory bindings and logs egress.state_unavailable. Unset: on wherever `AGENT_HARNESS_STATE_URL` is set (Compose's proxy sets it), off for a from-source run | not set anywhere |
| `INSTANCE_TOOL_IDS` | env | deployment | `false` | operator (the query service's, the durable worker's and the connectors service's environment) | instance-scoped tool identity (P2.2): on, an agent may bind two instances of one toolset type (and two MCP instances of one type), each instance loads with its own credentials, and when a type has several instances each instance's tools reach the model and the engines under names carrying the instance's tag (at most 51 characters); one instance per type keeps today's names; off, today: toolsets are keyed by type on save and load (a second instance merges into the first), two MCP instances of one type are refused, names as they are. Either way a run pinned to a type-keyed toolset resolves it against the agent's one binding of that type, and the migration (python -m app.harness.tool_identity.migrate) is available | not set anywhere |
| `HARNESS_RECONCILE` | env | deployment | `false` | operator (the durable worker's and the Node API's environment) | reconcile hooks for unknown effects (P2.9): on, a non-idempotent write whose outcome is unknown (a crash after its intent, a lost acknowledgement) is decided by a read of the provider for the tools with a hook (Jira create_issue by its effect label, Slack send_message by its message metadata), after the R-31 window (intent time + tool timeout + lease), never by a write: applied journals the outcome from the evidence, not applied re-calls once, still unknown parks it for a person; hooked writes carry the effect tag; off: every unknown effect is parked for a person and requests carry no tag. The chat's unknown-effect card, where the person the run acts for says whether it happened, is served whatever this flag says (UX3) | not set anywhere |
| `HARNESS_RUN_HISTORY_BY_REFERENCE` | env | deployment | `true` | operator (the Node API's environment; off only to roll back to a release before PERF-B) | writing a run's conversation history beside its document (PERF-B, QA22-F2): on, admission seals the history it read into the payload store and the stored run input names it (`input.historyRef`); off, admission writes the history into the run document as releases before PERF-B did, sealed with the org's data key since GAPS2 (`input.historySealed`). Reading is not gated: this release claims runs of either form. A release before PERF-B cannot read the reference and its worker refuses such a claim, so before rolling back turn this off, let the queued follow-up turns be claimed, then roll back | not set anywhere |
| `HARNESS_FLOW_GUARD` | env | deployment | `true` | operator (the durable worker's environment) | the flow rule (SEC1): on, once a run's context holds private data (a record any run of its session read, or content of unknown provenance such as an MCP or connector result), a call that sends data outside the organization (a write or execute tool with an external sink: every MCP tool, a Slack post, a Jira create; and, since FG2, a read whose request carries what the model chose to an outside host, such as an open-world MCP read; PipesHub's own web search is never held, `flow=platform_web`, nor its fetch_url, which once a send would be held opens only an address the run was given and refuses any other with tool.denied{rule: web_address}, `flow=opens_address`) is held for a person's approval (HARNESS_FLOW_GUARD_LEVEL=ask, the default) or refused with tool.denied{rule: flow_external_write} (deny); it narrows the run's approval policy and never widens it; off: approvals decide by effect class alone (the operator's off switch; an org's own flow rule still applies, SEC2 O-1). On by default since UX1. Today's loop (planExecute, Universal Agent Mode, project chats, regenerate through the API, the kill switch) applies the same rules since UX3, and an org's or agent's own rule with this switch off since FG2: it cannot ask, so such a call after the request saw any tool result that may carry private content (a send's included; web pages and the loop's own machinery do not), or in a conversation with history, is refused with approval_unavailable | Compose: `true`; backend/env.template: `true`; Helm: `agentHarness.flowGuard=true` |
| `HARNESS_PROVIDER_STATE` | env | deployment | `false` | operator (the durable worker's environment) | provider state on durable loop runs (P4.3): the direct transports capture what a provider needs back on the next request (Anthropic thinking blocks with their signatures, Responses reasoning items with encrypted content) on the assistant message, journaled with the response (rawRef), and send it back on their own wire only; off: nothing is captured and every request and journaled blob is byte-equal to before | not set anywhere |
| `HARNESS_MODEL_FALLBACK` | env | deployment | `true` | operator (the durable worker's and the Model Gateway's environment: both run in the `pipeshub-ai` container) | G-48 (WP-100): a run whose model provider is down (a 5xx, a timeout, no connection) moves to the next alternate model its pin names for that failure (`ModelRoute.on`; Node pins at most two of the agent's other models, never on a content filter), journaled as model.fallback before that model is called, and the chat says which model answered: a loop run in the durable kernel, an engine's calls in the Model Gateway (the same `ModelPolicy`); the worker advertises `model_fallback` only while it is on, so Node pins alternates only then; off: one route, a provider outage fails or yields the run as before. It replaced `HARNESS_MODEL_POLICY` (P4.7), which gated nothing else once same-route retries ran without it (P4.9 D-1) | not set anywhere (on by default) |
| `MCP_ALLOW_CUSTOM_STDIO` | env | deployment | `false` | operator (the query service's, the durable worker's and the connectors service's environment; Compose .env, Helm config.mcpAllowCustomStdio) | custom (non-catalog) STDIO MCP servers (#3887): on, an admin-entered command runs inside the app container with its privileges; off, creating, updating or launching one is refused custom_stdio_disabled; catalog and remote (HTTP) servers are unaffected | Compose: `false`; Helm `config.mcpAllowCustomStdio: false`; env templates: commented out |
| `PIPESHUB_ENABLE_SKILLS` | env | deployment | `true` | operator (the query service's and the durable worker's environment) | the skills subsystem on both loops (the skill tools, preloading, learning, builtin seeding), under the org's Labs ENABLE_SKILLS; off, no run gets skills whatever the org set | not set anywhere |
| `PIPESHUB_ENABLE_CODE_EXECUTION` | env | deployment | `true` | operator (the query service's and the durable worker's environment) | the code-execution tools (run_code and the legacy coding and database sandboxes) on both loops; a request's enable_code_execution overrides it; an override that cannot be read turns code execution off (fail closed) | not set anywhere |
| `PIPESHUB_SKILLS_AUTO_APPROVE` | env | deployment | `false` | operator (the query service's and the durable worker's environment) | learning-loop skill candidates approved without review when PIPESHUB_SKILLS_WRITE_APPROVAL is off; with write approval on (the default) every candidate waits for an org admin | not set anywhere |
| `PIPESHUB_SKILLS_WRITE_APPROVAL` | env | deployment | `true` | operator (the query service's and the durable worker's environment) | learning-loop skill candidates wait for review (approve or reject: org admins only since CPS); off, PIPESHUB_SKILLS_AUTO_APPROVE decides | not set anywhere |
| `PIPESHUB_SKILLS_LEARNING_ENABLED` | env | deployment | `false` | operator (the query service's and the durable worker's environment) | the skill-learning loop: after a run, a skill writer proposes candidates from it (both loops); off, no candidate is learned | not set anywhere |
| `PIPESHUB_AGENT_TRANSPORT` | env | deployment | `langchain` | operator (the query service's and the durable worker's environment; Compose .env, Helm config.agentTransport, install.sh) | the model transport of the agent loop: langchain, or direct (the provider SDK transports; azure_direct is a deprecated alias); store false on the shared Responses transport (A-28) applies under direct | Compose, deployment env template, install.sh: `langchain`; Helm `config.agentTransport: langchain` |
| `ENABLE_AGENT_HARNESS` | labs | org | `false` | org admin (Labs: POST /api/v1/configurationManager/platform/settings, config:write + admin) | durable runs for the org (HarnessRollout); off by default since BETA (2026-11-20; on by default from DUR3 until then); the effective-flags route reports the rollout decision under this key. The off switch is AGENT_HARNESS_KILL_SWITCH | — |
| `AGENT_HARNESS_KILL_SWITCH` | env | deployment | `false` | operator (the Node API's and the egress proxy's environment: Compose `AGENT_HARNESS_KILL_SWITCH`, Helm `agentHarness.killSwitch`); never a Labs setting (CTL-5) | the instance-wide incident stop over ENABLE_AGENT_HARNESS: on, every new admission of every org goes to legacy (`kill_switch`); Node holds it as the instance stop and its sweeper cancels harness runs in flight (ADR-OPS-01 D4); an egress proxy started with it on binds no sandbox serving a harness run (C6.14); queued next runs are refused | compose `false`; helm `agentHarness.killSwitch: false` |
| `ENABLE_SCHEDULED_RUNS` | labs | org | `false` | org admin (Labs) | scheduled and triggered agent runs (P8a), on every install whatever the org's harness (ADR-SCH-02 D1): off, every schedule route answers 404 scheduled_runs_disabled except delete, webhooks answer 404, and a due slot is refused scheduled_runs_disabled (recorded, the schedule stays active) | — |
| `ENABLE_KNOWLEDGE_PUBLICATIONS` | labs | org | `false` | org admin (Labs) | knowledge publications (P2.13, D21): on, container owners and org admins publish a knowledge base or record group to an audience (answer_only or full, optional expiry) and retrieval admits (actor ∩ grant) ∪ the publications for the audience on every knowledge surface; off, the publication routes answer 404 publications_disabled and retrieval, raw reads and record access checks are today's | — |
| `ENABLE_SKILLS` | labs | org | `true` | org admin (Labs) | skill tools; the durable run's skill tool source honours it exactly as the legacy loop does (with the legacy env switch PIPESHUB_ENABLE_SKILLS, read in shared legacy code) | — |
| `ENABLE_USER_CONTEXT` | labs | org | `true` | org admin (Labs) | the user's and org's names in the assistant's prompt; the durable chat assembly honours it as legacy does | — |
| `ENABLE_ACTIONS` | labs | org | `true` | org admin (Labs) | toolset actions (connector tools) on both loops and their management in the UI; a pin holds it as rollout.ENABLE_ACTIONS and a legacy turn's resolved header answers it (CPD-10) | — |
| `ENABLE_MCP` | labs | org | `true` | org admin (Labs) | MCP servers on both loops and their management in the UI; a pin holds it as rollout.ENABLE_MCP and a legacy turn's resolved header answers it (CPD-10) | — |
| `ENABLE_NAMED_ENTITY_EXTRACTION` | labs | org | `false` | org admin (Labs) | typed named-entity extraction while indexing, and the entity search tool agents are offered (search_by_entities) on both loops; a pin holds it as rollout.ENABLE_NAMED_ENTITY_EXTRACTION, so a durable run offers the tools its admission saw (WP-117) | — |
| `agentHarness.modelFamilies` | config | org | `unset` | org admin (POST /api/v1/configurationManager/platform/settings) | which models' runs the harness admits (HarnessRollout) | — |
| `/services/agentHarness/approvals` | config | deployment | `unset` | operator (the configuration store): the deployment-wide default and agents[agentKey] (one agent's rule in every org) | whether a durable run's write and execute tools wait for an approval | — |
| `/services/agentHarness/memory` | config | org | `unset` | org admin (PUT /api/v1/agent-harness/admin/memory-policy, config:write + admin; P7.3) | governed memory: whether durable runs are given the user's (and, under agent, the agent's) memories and may propose new ones, and whether kept memories wait for review | — |
| `/services/agentHarness/engineProfiles` | config | org | `unset` | nothing in this build writes it | an org's engine ceiling (allowed engines, limits) where one is set; unset: every released engine with the release's limits | — |
| `/services/agentHarness/modelAccess` | config | org | `unset` | nothing in this build writes it | an org's narrowing of the signed vendor rules for engine runs' model access (A1) where one is set; unset: every signed vendor row on, consumer plans off | — |
| `/services/agentHarness/sessionRetention` | config | org | `unset` | org admin (PUT /api/v1/agent-harness/admin/session-retention, config:write + admin) | automatic erasure of conversations idle longer than the period | — |
| `/services/agentHarness/repositoryCache` | config | deployment | `unset` | the harness worker (app.harness.repositories.CacheStatusPublisher, on change or at most once a minute) | WP-63: the git hosts screen's cache line and its low-disk warning (`context.deployment.cache`); a status, not a setting: the worker's own disk guard refuses new fetches below 10% free | — |
| `engineProfile` | config | agent | `unset` | agent author (POST /api/v1/agents/create, PUT /api/v1/agents/{agentKey}; parsed and checked under the org ceiling on write, a widening refused ENGINE_PROFILE_WIDENING) | whether an agent's durable runs use an engine | — |
| `chatMode` | request | request | `auto` | the user, per request | which requests of a durable org run durably; deep runs its sub-agents as durable child runs | — |
| `MCP_ALLOW_CUSTOM_STDIO` | env | deployment | `false` | operator (the query service's and the connectors service's environment); never a ConfigurationService key, which org admins can write | custom STDIO MCP servers (#3887): on, an org may add an STDIO server with its own command; off, only catalog servers with pinned commands run, and a custom one is refused (custom_stdio_disabled) | Compose: `false`; Helm: `config.mcpAllowCustomStdio=false` |
| `PIPESHUB_AGENT_TRANSPORT` | env | deployment | `langchain` | operator (the query service's environment; install.sh and the deployment env template write it) | how the legacy agent loop calls models: langchain (the LangChain chat models) or direct (the SDK transports, chosen per request) | Compose: `langchain`; deployment env.template: `langchain`; install.sh: `langchain`; Helm: `config.agentTransport="langchain"` |
| `PIPESHUB_ENABLE_SKILLS` | env | deployment | `true` | operator (the query service's environment) | skills on the legacy agent loop, the deployment layer under the Labs flag ENABLE_SKILLS: both must be on | not set anywhere |
| `PIPESHUB_ENABLE_CODE_EXECUTION` | env | deployment | `true` | operator (the query service's environment); a request may override it | the code execution tool on the legacy agent loop | not set anywhere |
| `PIPESHUB_SKILLS_AUTO_APPROVE` | env | deployment | `false` | operator (the query service's environment) | skill candidates the agent proposes are published without review (skills manager, legacy agent loop) | not set anywhere |
| `PIPESHUB_SKILLS_WRITE_APPROVAL` | env | deployment | `true` | operator (the query service's environment) | the agent's skill writes wait for a person's approval (skills manager, legacy agent loop) | not set anywhere |
| `PIPESHUB_SKILLS_LEARNING_ENABLED` | env | deployment | `false` | operator (the query service's environment) | the agent may propose new skills from what it learned in a conversation (skills manager, legacy agent loop) | not set anywhere |

## Features and what they need

Each row is checked in order; the first unmet need decides. "legacy" means the request runs on today's
path with the rollout reason in the admission answer and the logs; "hidden" means the tool or toolset is not
registered and nothing in the prompt changes; "refused" means the feature answers with the typed reason;
"absent" means the process or container is not started.

### `durable_chat`

Chat and agent runs on the durable harness (resumable, crash-safe). When off: legacy.

| Needs | On when | Otherwise | Reason |
|---|---|---|---|
| flag `REPLICA_SET_AVAILABLE` | true | legacy | infrastructure_unavailable |
| flag `AGENT_HARNESS_RELAY_ENABLED` | true | legacy | infrastructure_unavailable |
| flag `AGENT_HARNESS_WORKER_ENABLED` | true | legacy | infrastructure_unavailable |
| condition `journal_durable` | true | legacy | infrastructure_unavailable |
| flag `AGENT_HARNESS_KILL_SWITCH` | false | legacy | kill_switch |
| flag `ENABLE_AGENT_HARNESS` | true | legacy | org_disabled |
| flag `agentHarness.modelFamilies` | the model's family is allowed | legacy | model_family_not_allowed |
| flag `chatMode` | a durable chat mode | legacy | chat_mode_not_supported |

### `deep_durable`

Deep mode's sub-agents as durable child runs (K-L). When off: legacy.

| Needs | On when | Otherwise | Reason |
|---|---|---|---|
| feature `durable_chat` | true | legacy | the durable_chat reason |
| flag `chatMode` | deep | legacy | chat_mode_not_supported (planExecute) |
| condition `worker_feature_deep_mode` | true | waits | the run is routed only to workers advertising deep_mode |

### `approvals`

Write and execute tools of durable runs ask before running. When off: allow.

| Needs | On when | Otherwise | Reason |
|---|---|---|---|
| feature `durable_chat` | true | legacy | the durable_chat reason |
| flag `/services/agentHarness/approvals` | writes: ask in the deployment default, the org's rule or the rule of the run's agent | allow | absent: allow (today's behaviour); unreadable or malformed: ask (fail closed) |

### `governed_memory`

Durable runs are given the facts and preferences their actor (or agent) kept, labelled and checked against the actor's access, and may propose new ones for review (P7.3). When off: absent.

| Needs | On when | Otherwise | Reason |
|---|---|---|---|
| feature `durable_chat` | true | absent | the legacy route has no memory |
| flag `/services/agentHarness/memory` | mode user or agent for the org | absent | absent: off (today's behaviour); unreadable or malformed: off (fail closed) |
| condition `worker_feature_governed_memory` | true | waits | a run whose spec pins memory is routed only to workers advertising governed_memory |

### `collaborator_turns`

Collaborators send turns in a shared conversation, each run acting as its sender; a message to someone else's running task waits as the next run (SS2). When off: refused.

| Needs | On when | Otherwise | Reason |
|---|---|---|---|
| feature `durable_chat` | true | refused | collaborator_turns_disabled (the legacy route refuses collaborators: it cannot filter history per sender) |
| condition `worker_feature_session_roles` | true | waits | a run in a shared context is routed only to workers advertising session_roles |

### `scheduled_runs`

An agent runs on a cron schedule or on a webhook or connector event, as the user who created the schedule, re-authorized at every firing; each slot fires once (P8a). When off: refused.

| Needs | On when | Otherwise | Reason |
|---|---|---|---|
| feature `durable_chat` | true | legacy | the firing runs as the classic chat turn a person would start (ADR-SCH-02 D2, D3); a cost cap per run does not apply (D6) |
| flag `ENABLE_SCHEDULED_RUNS` | true | refused | scheduled_runs_disabled |
| condition `actor_active` | the schedule's user is not deleted or disabled, is a person, in an active org | refused | actor_deleted (schedule disabled), actor_disabled / actor_not_person / org_inactive (schedule paused) |
| condition `agent_access` | the user may still run the agent | refused | agent_forbidden (schedule paused) |

### `knowledge_publications`

Container owners publish a knowledge base or record group to an audience, answer_only or full; retrieval admits what is published to the reader, raw reads of answer_only content are refused, and labels name the publication so it is never re-shared outside the audience (P2.13, D21). When off: legacy.

| Needs | On when | Otherwise | Reason |
|---|---|---|---|
| flag `ENABLE_KNOWLEDGE_PUBLICATIONS` | true | legacy | publications_disabled (routes 404; retrieval as today) |

### `continue_privately`

A participant copies a shared conversation into a private one: their own turns, copies of the citations, artifacts and (for the owner) workspace files they may see, and a placeholder for everyone else's turns (SS3). When off: refused.

| Needs | On when | Otherwise | Reason |
|---|---|---|---|
| flag `REPLICA_SET_AVAILABLE` | true | refused | 503: the fork writes the journal and needs the session data keys |
| flag `AGENT_HARNESS_RELAY_ENABLED` | true | refused | 503: the fork writes the journal and needs the session data keys |
| flag `AGENT_HARNESS_WORKER_ENABLED` | true | refused | 503: the fork writes the journal and needs the session data keys |
| condition `journal_durable` | true | refused | 503: the fork writes the journal and needs the session data keys |
| condition `participant` | the caller takes part in the conversation | refused | 404, as for a conversation they cannot read |
| condition `no_active_run` | no task is running in the conversation | refused | run_active (409) |

### `engine_run`

An agent whose definition carries an engineProfile runs on an engine (E4a admission, E1 durable execution). When off: loop.

| Needs | On when | Otherwise | Reason |
|---|---|---|---|
| feature `durable_chat` | true | legacy | the durable_chat reason |
| flag `engineProfile` | present on the agent | loop | absent: the agent runs on the loop |
| flag `/services/agentHarness/engineProfiles` | the org ceiling's engines admit a released engine (release profiles in order: opencode, then Codex; the first admitted one runs) | refused | admission refused profile_widening |
| flag `HARNESS_ENGINE_RUNS` | true | queued | no worker advertises executor_engine: the run waits for one (admission requirement) |
| feature `sandbox_plane` | true | refused | no broker: the worker plans no engines (engine.worker.unavailable broker_unavailable) |
| condition `model_api` | the model gateway serves a wire the chosen engine speaks (chat completions for opencode, Responses for Codex; EngineControllerConfig.model_apis, both by default) | refused | model_api_unavailable |
| feature `model_gateway` | true | failed | the engine's model calls are refused; the run fails with the engine's error code |

### `model_access_routes`

An engine run reaches its model through a pinned access route: the org's key, the user's own key or an org cloud account, under the signed vendor rules (A1). When off: refused.

| Needs | On when | Otherwise | Reason |
|---|---|---|---|
| feature `engine_run` | true | refused | account_requires_engine (a loop run naming a model account; without one it runs on the configured models as before) |
| condition `vendor_rules` | the signed release rules verify against the release key in the code | failClosed | vendor_rules_invalid |
| flag `AGENT_HARNESS_OFFERING` | an offering the deciding vendor row lists | refused | offering_not_permitted |
| flag `/services/agentHarness/modelAccess` | the deciding row not switched off (consumer-plan rows: opted into) | refused | org_policy_refused (unreadable: access_policy_unavailable) |

### `sandbox_plane`

Sandbox broker and egress proxy containers (Compose profile sandbox, Helm sandboxBroker). When off: absent.

| Needs | On when | Otherwise | Reason |
|---|---|---|---|
| flag `SANDBOX_MODE` | broker | absent | the broker and proxy are not deployed (Compose profile sandbox follows SANDBOX_MODE broker|docker) |

### `run_code`

Today's run_code / coding sandbox tools. When off: hidden.

| Needs | On when | Otherwise | Reason |
|---|---|---|---|
| flag `SANDBOX_MODE` | broker|docker|e2b|local | hidden | SandboxUnavailableError: the sandbox tools are dropped, the chat still answers |

### `mcp_stdio_runners`

STDIO MCP servers run in MCP-runner sandboxes (P5.10, D23), not as child processes of PipesHub's services. When off: legacy.

| Needs | On when | Otherwise | Reason |
|---|---|---|---|
| flag `MCP_STDIO_RUNNERS` | true | legacy | off: STDIO servers start as child processes of the query service or worker, as today |
| feature `sandbox_plane` | true | refused | runner_unavailable (cause broker_unreachable): the instance is a load failure and never starts on the host |
| condition `runner_isolation` | the broker offers a mode at or above MCP_RUNNER_ISOLATION (default docker) | refused | runner_isolation_unavailable |
| condition `egress_proxy` | present, for an instance that declares hosts (none: the runner has no network) | refused | runner_unavailable (cause egress_proxy_missing) |

### `context_compiler`

Durable loop requests compiled in cache bands with a journaled reduction ladder (P4.1, P4.2). When off: legacy.

| Needs | On when | Otherwise | Reason |
|---|---|---|---|
| flag `HARNESS_CONTEXT_COMPILER` | true | legacy | off: the turn shaper assembles every request as before |
| feature `durable_chat` | true | legacy | the compiler runs only in the durable worker's kernel |
### `mcp_hub`

The MCP hub (P2.8): schema cache, pooled sessions, the description scanner's hold on flagged tools, tool-name rules and the SSRF guard on org-configured MCP URLs. When off: legacy.

| Needs | On when | Otherwise | Reason |
|---|---|---|---|
| flag `MCP_HUB` | true | legacy | off: each request discovers and connects on its own, every listed tool is registered, URLs are connected as configured, as today |
| condition `manifest_approved` | an admin approved the instance's tools (PUT /api/v1/mcp-servers/instances/{instanceId}/manifest) | refused | mcp_tool_unapproved (a tool changed since: mcp_tool_changed); the tool, or the instance when none is left, is not registered |
| condition `public_url` | the instance's URL resolves only to public (or CGNAT) addresses | refused | blocked |

### `egress_connect_tunnels`

HTTPS from a sandbox (in practice a P5.10 MCP runner) to a host on its own egress allowlist, through an opaque CONNECT tunnel in the egress proxy (P5.4b). When off: refused.

| Needs | On when | Otherwise | Reason |
|---|---|---|---|
| flag `EGRESS_CONNECT_TUNNELS` | true | refused | tunnel_refused (as P5.4: listed hosts are reached over plain HTTP only) |
| feature `sandbox_plane` | true | absent | no egress proxy is deployed |
| condition `allowlisted_host` | host:port is on the sandbox's egress allowlist (the hosts an MCP instance declares, host:443 for HTTPS), has no token route and is not a gateway | refused | destination_not_allowed; tunnel_refused for a credentialed or gateway destination; tunnel_ip_literal for an address |
| condition `public_address` | every address the name resolves to passes P2.8's SSRF policy and the proxy's blocked CIDRs | refused | destination_private_address (destination_unresolvable when there is none) |
| condition `proxy_route_off_host` | the proxy has a network with a route off the host (the egress-tunnels Compose overlay) | failed | upstream_unavailable (502): the proxy's own networks are internal |
### `instance_tool_ids`

Instance-scoped tool identity (P2.2): two instances of one toolset or MCP type on one agent, each with its own tools, credentials and names. When off: legacy.

| Needs | On when | Otherwise | Reason |
|---|---|---|---|
| flag `INSTANCE_TOOL_IDS` | true | legacy | off: toolsets are keyed by type; a second instance of a type merges into the first, as today |
### `reconcile_hooks`

Unknown effects decided by a read of the provider (R-31); those it cannot decide wait for the person the run acts for (P2.9). When off: legacy.

| Needs | On when | Otherwise | Reason |
|---|---|---|---|
| flag `HARNESS_RECONCILE` | true | legacy | off: every unknown effect is parked for a person, as before, and decided from the chat or the console |
| feature `durable_chat` | true | legacy | unknown effects are journaled only by the durable worker's kernel |

### `flow_guard`

An external write after a run has read private data waits for a person or is refused (SEC1). When off: legacy.

| Needs | On when | Otherwise | Reason |
|---|---|---|---|
| flag `HARNESS_FLOW_GUARD` | true | legacy | off: approvals decide by effect class alone (on by default since UX1) |
| feature `durable_chat` | true | legacy | the rule reads the session's journal, which only the durable worker keeps |

### `provider_state`

Durable loop transports keep and replay provider state (thinking signatures, reasoning items) (P4.3). When off: legacy.

| Needs | On when | Otherwise | Reason |
|---|---|---|---|
| flag `HARNESS_PROVIDER_STATE` | true | legacy | off: nothing captured; requests and journaled blobs as before |
| feature `durable_chat` | true | legacy | the durable worker's kernel and transports only |

### `model_policy`

Durable loop model calls retried and moved to a pinned fallback route, journaled (P4.7). When off: legacy.

| Needs | On when | Otherwise | Reason |
|---|---|---|---|
| flag `HARNESS_MODEL_FALLBACK` | true | legacy | off: a model failure fails or yields the run as before |
| feature `durable_chat` | true | legacy | the durable worker's kernel and transports only |

### `model_gateway`

The model gateway process engines reach through the egress proxy. When off: absent.

| Needs | On when | Otherwise | Reason |
|---|---|---|---|
| flag `MODEL_GATEWAY_ENABLED` | true | absent | the process is not started (unset follows AGENT_HARNESS_WORKER_ENABLED) |

### `budget_ledger`

Durable runs charge model calls to budget-ledger accounts (P6.5). When off: unmetered.

| Needs | On when | Otherwise | Reason |
|---|---|---|---|
| feature `durable_chat` | true | legacy | the durable_chat reason |
| flag `AGENT_HARNESS_BUDGET_LEDGER` | true | unmetered | the worker leaves model calls unmetered (operator escape hatch) |

### `session_retention`

Org retention: idle conversations are erased after the period. When off: no expiry.

| Needs | On when | Otherwise | Reason |
|---|---|---|---|
| flag `/services/agentHarness/sessionRetention` | days set for the org | no expiry | absent: never expires automatically |

### `session_erasure`

Deleting a conversation erases its messages, citations, artifacts, workspaces and journal (E7). Always on; no flag.
Conversations deleted **before** the upgrade are not touched until an operator runs the one-time backfill
(`enterprise_search/ops/erasure-backfill-cli.js`, E7B): a dry run, then `execute` with the dry run's digest.

## Release notes

- **Conversation erasure (E7, E7B).** From this release, deleting a conversation erases it for good: its messages,
  citations, artifacts, workspaces and journal. Conversations that were deleted **before** the upgrade are left
  exactly as they were: upgrading erases nothing of them. To erase them too (they were shown to their users as
  "permanently removed"), an operator runs the one-time backfill once, per deployment:
  1. `node dist/modules/enterprise_search/ops/erasure-backfill-cli.js plan --mongo-uri "$MONGO_URI" --db "$MONGO_DB_NAME" --out plan.json`
     (the dry run: per org, how many sessions, messages, citations, legacy copies and harness runs would be
     erased, and a digest; it writes nothing).
  2. Review `plan.json`, then `… execute --mongo-uri … --db … --digest <digest from plan.json> --operator <your name>`.
     It refuses a digest the data no longer matches, erases in batches of 100, re-checks every batch first,
     records every batch on the org's audit chain, and marks each org done. Exit 3 means the erasure worker is
     still busy: run the same command again. Running it after it completed changes nothing.
  It needs MongoDB with transactions (a replica set) and a Node process running (its erasure worker does the
  erasing).
- **Agent chat runs on the durable harness by default (DUR3).** The relay and the worker start with the app
  (Compose, Helm, `backend/env.template`), and Labs "Enable Durable Agent Harness" is on. Requests reach the model
  exactly as on today's loop (C-24, every case), and runs survive restarts. The app container runs the worker
  process (see footprint-baseline.md). Rollback: Labs "Agent harness" off, or the env kill switch ("Rolling back" above). Upgrade order:
  migrations, then workers, then Node.
- **The agent asks before sending private content outside (UX1, `HARNESS_FLOW_GUARD` on).** In an agent chat that
  read private content earlier (a record from a search or a fetch, or content from a connected app or an MCP tool
  whose origin cannot be checked), a tool call that would send something outside the organization (a Slack post,
  an email, a Jira issue, any MCP tool, and a web search or page fetch whose query or address could carry what it
  read) waits for the user. The result of PipesHub's own calculators and image generator is not "content whose
  origin cannot be checked": it is made from what the agent already had, so a web search after generating an image
  from the user's own words does not ask, and one after an image whose prompt used a private record still does
  (FLOWTRUST). The card in the chat says why it asks, what will be sent, where,
  and which sources the conversation read, with Deny, Allow once and, where a destination is named, Allow for this
  conversation. Nothing asks for reads, for the conversation's own artifacts or the org's skill library, for code
  run without network, or for a draft or new folder in the user's own mailbox or drive; a conversation that read
  nothing private never asks, and plain web browsing (search, then pages) never asks. Engine runs also ask before every write that leaves the user's own space, whatever
  they read (UX4). A denied call is refused to the agent, which tells the user it was not done; a run whose
  deadline passes while it waits says nothing was sent. Admins see held calls in the run's
  `effectivePolicy.heldRequests`. Turn it off for the deployment with `HARNESS_FLOW_GUARD=false`; an org can make it
  stricter (`deny`) in its approval policy, not weaker.
  Conversations imported from before the durable harness count as unverifiable, so their external writes ask.
- **Labs stores only the settings an admin changes (UX1).** A flag nobody changed follows the release default,
  including future changes to it. Settings saved before this release are read as they are: nothing an admin set
  changes. Where Labs was saved with "Enable Durable Agent Harness" off, it stays off and admins see a one-time
  notice in the workspace settings explaining the new default, with "Use the new default" and "Keep it off".
  Labs now marks a flag changed from its default and offers "Reset to default". The settings API accepts a save
  of just the changed flags (`fileUploadMaxSizeBytes` optional) and returns `featureFlagOverrides` and `notices`.
- **Stage-1 `code_task` is retired (S1R).** Labs "Enable Code Tasks (Experimental)" and `PIPESHUB_CODE_TASK`
  are gone. An org that had turned it on keeps every earlier answer, citation and file; new messages use
  `run_code` for data files and office documents, as the agent already chose in 171 of 180 measured trials when
  both were offered (G2N), at parity on those suites. A stored Labs value and a set `PIPESHUB_CODE_TASK` are
  ignored (neither stops a process). Coding-agent work runs as durable engine runs (engine agents). Other
  release-note items: the strict flag parsing (F3 OI-5).

## Kill switches

| Kill switch | Overrides | Effect |
|---|---|---|
| `AGENT_HARNESS_KILL_SWITCH` (env, instance) | `ENABLE_AGENT_HARNESS` | the operator's incident stop: every new admission → legacy (`kill_switch`); runs in flight finish; queued next runs refused (see "Rolling back"). An org turns the harness off with Labs "Agent harness" |
| `/services/agentHarness/engineProfiles` org ceiling (`engines`) | per engine, per org | an engine the ceiling does not allow is refused |
| `AGENT_HARNESS_BUDGET_LEDGER=off` (env) | P6.5 metering | escape hatch: the worker stops charging model calls to ledger accounts |

## Settings that are not flags

Names harness code reads that are addresses, ports, secrets, key material or tuning values. They are listed
so C-16 can tell "not a flag" from "forgotten".

| Setting | Kind | Why it is not a flag |
|---|---|---|
| `AGENT_HARNESS_STATE_URL` | env | address: Node's internal API for the worker, gateways and proxy (its own listener, AGENT_HARNESS_INTERNAL_PORT; default http://localhost:3002) |
| `AGENT_HARNESS_WORKER_ID` | env | identity: the worker's id (default host-pid) |
| `ROTATE_SIGNING_SECRETS` | env | one-shot operator action (install.sh --rotate-signing-secrets), not a switch |
| `MODEL_GATEWAY_PORT` | env | address: the model gateway's listen port |
| `MODEL_GATEWAY_OUTPUT_ALLOWANCE_TOKENS` | env | tuning: caps the output-token reservation |
| `MODEL_GATEWAY_DEFAULT_OUTPUT_TOKENS` | env | tuning: output allowance for unlisted models |
| `TIKTOKEN_CACHE_DIR` | env | address: tiktoken's own BPE cache folder; the budget's token counter (WP-66) reads a verified o200k or cl100k file from it (else the copy the pinned litellm ships, else its dense fallback) and never fetches one |
| `MODEL_GATEWAY_CUT_HOLD_TTL_S` | env | tuning: how long a model call the gateway cut short (Stop, the engine gone, a failed stream) keeps the rest of its upper bound held on the run's budget beside the estimate it is charged, when the provider cannot say what it billed (WP-66, default 600; whole seconds 1 to 3600, else the default) |
| `MODEL_GATEWAY_CUT_OUTPUT_TPS` | env | tuning: the output tokens a second a model is assumed to make after its last streamed token, until a call of it has finished in this process (WP-66, default 60; a positive number, else the default); the estimate never passes the call's bound |
| `MODEL_GATEWAY_PROVIDER_CONCURRENCY` | env | tuning: calls one upstream (a provider account or key at one endpoint or region; a configuration naming neither: the provider) takes at once from the whole deployment, engine calls at the model gateway and loop runs' own calls in the workers alike, shared over Redis (each process alone without it) (G-45, WP-61, default 64; 0 no limit), served highest priority class first with a floor share for lower classes and, within a class, the org holding fewest of its slots first (WP56-FAIR); set it alike on every harness process (unset or not a whole number: the default) |
| `AGENT_HARNESS_CLAIM_FLOOR_EVERY` | env | tuning: every n-th claim-next of a Node process takes the oldest due run of any priority class (G-45, default 5; 0 strict priority; not a whole number: the default), so background work is never starved |
| `AGENT_HARNESS_PREEMPT_OVERCOMMIT` | env | tuning: slots past a worker's own a higher priority class may take at once while the background run it preempts reaches its step boundary (G-45, default 2; 0 turns preemption off; not a whole number up to 64: the default) |
| `AGENT_HARNESS_PREEMPT_MIN_SLICE_S` | env | tuning: seconds a background or batch run holds its slot before it may be preempted (G-45, default 5; not a whole number from 0 to 3600: the default) |
| `PIPESHUB_PROMPT_CACHE` | env | tuning: main's provider prompt caching (#4038), on unless set to anything but true; changes cost and latency, never what a run may do |
| `PIPESHUB_LLM_STALL_TIMEOUT_SECONDS` | env | tuning: main's per-phase read, write and pool limit for the OpenAI and Azure transports (#4290), unset or 0 keeps the one request timeout; a stalled stream fails sooner and the SDK retries it |
| `PIPESHUB_PROMPT_CACHE_TTL` | env | tuning: the Anthropic cache breakpoint lifetime (5m or 1h; anything else is 5m) |
| `PIPESHUB_PROMPT_CACHE_RETENTION` | env | tuning: the OpenAI prompt-cache retention (default 24h; off or none sends none) |
| `PIPESHUB_PROMPT_CACHE_SHARDS` | env | tuning: how many prompt-cache routing keys a session spreads over (1-1024, default 8) |
| `MCP_STDIO_ALLOWED_COMMANDS` | env | policy list, not a switch: main's commands a catalog STDIO MCP server may launch (#4003), comma-separated; unset is npx and uvx, a shell is never allowed whatever it lists |
| `SANDBOX_EGRESS_PROXY_CONTAINER` | env | address: the proxy container the broker attaches sandboxes to |
| `SANDBOX_EGRESS_SUBNETS` | env | address: subnet pool for per-sandbox networks |
| `SANDBOX_OPEN_EGRESS_SUBNETS` | env | address: subnet pool the shared open-egress bridge takes a /22 from (default `10.212.0.0/16`), never Docker's default pools |
| `EGRESS_BLOCKED_CIDRS` | env | address: extra destination networks the proxy refuses, also over every EGRESS_PRIVATE_ALLOW entry (the host's LAN address, a cluster's pod and service ranges) |
| `EGRESS_UPSTREAM_PROXY` | env | address: the upstream forward proxy (http://host:port, no credentials) every public destination goes through (G-21); it is given the checked address, never the name; gateways and private entries go direct; unset, the proxy dials public addresses itself |
| `EGRESS_UPSTREAM_PROXY_AUTH` | env | secret: user:password the egress proxy sends the upstream forward proxy as Proxy-Authorization (G-21); never sent toward a sandbox or logged |
| `EGRESS_UPSTREAM_NO_PROXY` | env | address: public hosts (name, .domain, network) the egress proxy dials direct instead of through EGRESS_UPSTREAM_PROXY (G-21); * is refused |
| `REDIS_HOST` | env | address: main's Redis host; the egress proxy also refuses its addresses as a destination (G-21, G-38) |
| `ETCD_URL` | env | address: main's etcd endpoint; the egress proxy also refuses its addresses as a destination (G-21, G-38) |
| `DOC_STORE` | env | deployment setting from main, not a harness switch: where the documents live (mongo, postgres, shadow); postgres stands in for REPLICA_SET_AVAILABLE as the durable harness's transactions condition (WP-114), shadow reads as mongo |
| `EGRESS_PRIVATE_ALLOW` | env | policy list, not a switch: private host:port, address:port or network:port entries the egress proxy may dial for a sandbox whose own allowlist names the host (G-21: an internal Git server); loopback, link-local, metadata and PipesHub's own addresses stay refused whatever it lists |
| `EGRESS_VOLUME_ALERT_UP_MB` | env | tuning (C6.12): the egress proxy journals egress.volume_alert (run_threshold, up) when a run's sandboxes have sent more than this many MiB to outside destinations only (tunnels, intercepted TLS, databases, any other host); PipesHub's Model and Tool Gateways are counted for the metrics only (WP-61) (default 256; 0 off; malformed: the egress proxy does not start) |
| `EGRESS_VOLUME_ALERT_DOWN_MB` | env | tuning (C6.12): the same alert when a run's sandboxes have received more than this many MiB from outside destinations (the gateways not counted) (default 2048; 0 off; malformed: the egress proxy does not start) |
| `EGRESS_VOLUME_BASELINE_FACTOR` | env | tuning (C6.12): the egress proxy journals egress.volume_alert (org_baseline) when a run sends more than this many times its org's usual run to outside destinations (the gateways not counted, in the run or the baseline), the mean of the org's ended runs, then from its 50th a moving average that gives each new run a weight of 1/50 (default 10; 0 off; otherwise at least 1; malformed: the egress proxy does not start) |
| `EGRESS_VOLUME_BASELINE_MIN_RUNS` | env | tuning (C6.12): how many ended runs an org needs before its baseline alert is armed (default 20; 0 arms it from the first ended run, as 1 does; malformed: the egress proxy does not start) |
| `EGRESS_VOLUME_BASELINE_FLOOR_MB` | env | tuning (C6.12): the org-baseline alert never fires below this many MiB sent (default 32; malformed: the egress proxy does not start) |
| `EGRESS_RUN_MAX_MB` | env | limit (C6.12): the hard cap on what one run's sandboxes move (a sandbox kept for a conversation counts toward the run it serves now) through the egress proxy to outside destinations only (tunnels, intercepted TLS, databases, any other host); PipesHub's Model and Tool Gateways are counted for the metrics only (WP-61), up and down together, in MiB (default 0: no cap); past it the run's outside streams and tunnels are cut, every new outside request is refused volume_cap_exceeded and egress.volume_alert (run_cap) is journaled, a model stream in flight finishes, and the run's next Model Gateway call is refused egress_volume_exceeded so the engine ends the run with that code, which the chat words; a malformed value stops the egress proxy at start |
| `EGRESS_CONTROL_HOST` | env | address: where the proxy's control plane listens (required; never a wildcard or the sandbox pool) |
| `EGRESS_TOKEN_SECRET` | env | secret: the standalone proxy's token key |
| `EGRESS_SEALING_SECRET` | env | secret: the standalone proxy's key for sealed credentials (WP-06); set, it opens sealed routes, ends TLS to their hosts and serves database routes; PipesHub's role uses SECRET_KEY |
| `EGRESS_DATABASE_PORT` | env | address: the standalone proxy's PostgreSQL port for database routes (default 5432; only with EGRESS_SEALING_SECRET) |
| `EGRESS_CONTROL_TOKEN` | env | secret: the standalone proxy's control bearer |
| `EGRESS_TOKEN_TTL_S` | env | tuning: standalone proxy token TTL |
| `EGRESS_UPSTREAMS` | env | address: the standalone proxy's gateway upstreams |
| `EGRESS_BIND_HOST` | env | address |
| `EGRESS_DATA_PORT` | env | address |
| `EGRESS_CONTROL_PORT` | env | address |
| `DOCKER_HOST` | env | address: the broker's Docker daemon |
| `DOCKER_TLS_VERIFY` | env | Docker client convention: when set, a plain tcp daemon address is refused |
| `OPIK_API_KEY` | env | secret: tracing |
| `OPIK_URL_OVERRIDE` | env | address: tracing |
| `OPIK_PROJECT_NAME` | env | tuning: tracing project |
| `MCP_RUNNER_IMAGE` | env | image: the MCP runners' default image (blank reads as unset: the sandbox image); the broker allows it |
| `AGENT_HARNESS_AUDIT_CREDENTIAL` | env | secret: the worker credential the grant auditor presents to Node's audit route when run with --credential-from-env (P9); unset, it reads the deployment's configuration as the worker does |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | env | address: the OTLP/HTTP collector harness spans go to (standard OpenTelemetry name; Node's exporter reads it, Python's OTLP exporter reads it itself; default http://localhost:4318) |
| `OTEL_SERVICE_NAME` | env | address: the OpenTelemetry service name on harness spans (default pipeshub-<role>); the OTLP exporter reads OTEL_EXPORTER_OTLP_ENDPOINT and its other standard variables itself |
| `PIPESHUB_ENGINES_WITHOUT_GVISOR` | env | dev/test only, off by default (exactly `true` turns it on): coding engines run on hardened runc below the release's gVisor floor, and admins see "Coding engines run without gVisor — weaker isolation". Unset, an install without `runsc` refuses engine runs (`gvisor_unavailable`). Defined in `policy/release.json` `engineIsolation`; install guide `deployment/docker-compose/GVISOR.md` |
| `PIPESHUB_ACP_PROFILES_UNSIGNED` | env | dev only, off by default (exactly `true` turns it on): the packaged ACP agent profiles are read while they carry no signature. The tree never holds one; a release build signs them (RELEASE-CHECKLIST.md). Its own switch since R32-TRUST, independent of `PIPESHUB_ENGINES_WITHOUT_GVISOR` (ADR-SEC-01 D7). Unset, an unsigned profile installs no ACP engine, and admins read "This engine's profile isn't signed for this deployment" |
| `PIPESHUB_TEST_TRUST_ROOT` | env | operator, test stacks only, unset by default: a read-only folder holding a test stack's own public key (`trust-root.json`, key id `test-…`) and its signature of the packaged ACP profiles, written at stack build by `scripts/engines/sign_vendor_rules.py --test-root DIR` (the private half is never written). The key vouches for the ACP profiles only, never the vendor rules or images; a release build refuses to start with it (ADR-SEC-01 D7). The e2e lane sets it with `E2E_SANDBOX_RUNTIME=runsc` or `E2E_ACP_PROFILES=signed` |
| `PIPESHUB_ALLOW_TEST_TRUST_ROOT` | env | dev only, off by default (exactly `true`): lets a release build (its packaged ACP profiles carry the release signature) start with `PIPESHUB_TEST_TRUST_ROOT`; without it such a build refuses to start (ADR-SEC-01 D7) |
| `PH_TIMEOUT_S` | env | tuning, inside an engine sandbox: seconds the `ph` CLI waits for one tool gateway answer (default 900) |
| `PH_POLICY_TIMEOUT_S` | env | tuning, inside an engine sandbox: seconds `ph policy check` waits for the tool gateway's policy answer (default 10, inside the managed hook's 30 s timeout); no answer refuses the call |
| `PIPESHUB_FOLDER` | env | tuning, inside an engine sandbox: where the `ph` CLI finds its folder (default `/pipeshub`); tests point it elsewhere |
| `MCP_RUNNER_ISOLATION` | env | tuning: the runners' isolation floor (a runtime mode, default docker); never weakened: a floor the broker lacks is refused |
| `MCP_RUNNER_IDLE_TTL_S` | env | tuning: seconds an MCP runner with no session lives (default 900) |
| `MCP_RUNNER_MAX_AGE_S` | env | tuning: seconds after which a free MCP runner is recycled (default 21600); older runner sandboxes are reaped as orphans |
| `MCP_RUNNER_MAX_PER_ORG` | env | tuning: MCP runners one org keeps in a process's pool (default 10); past it the org's least recently used idle runner makes way (R36COVER-FU-4) |
| `MCP_HUB_SESSION_IDLE_TTL_S` | env | tuning: seconds a pooled MCP session with no lease stays open (default 300) |
| `MCP_HUB_SESSION_MAX_AGE_S` | env | tuning: seconds after which a pooled MCP session is replaced on its next lease (default 3600) |
| `MCP_HUB_SCHEMA_TTL_S` | env | tuning: seconds a discovered tool list is fresh (default 600; a 2026-07-28 server's ttlMs may shorten it) |
| `MCP_HUB_BREAKER_FAILURES` | env | tuning: timeouts or unreachable answers in a row that open an MCP server's circuit for one credential owner (default 5; 0: no breaker; MX-08) |
| `MCP_HUB_BREAKER_COOLDOWN_S` | env | tuning: seconds an open MCP server circuit fails calls at once before one probe goes through (default 30; MX-08) |
| `MCP_PRIVATE_NETWORKS` | env | address: the private networks (CIDRs, comma-separated) an MCP server may be on when PIPESHUB_BLOCK_PRIVATE_ADDRESSES is on; unset: none; a malformed value refuses (MCPFIX F2) |
| `PIPESHUB_BLOCK_PRIVATE_ADDRESSES` | env | switch, shared with the Python fetchers (`app.utils.url_fetcher.private_addresses_blocked`): when on, the harness's MCP hub (strict SSRF check) and every shared outbound fetch refuse private addresses too; loopback, link-local and metadata addresses are refused whatever it says |
| `PIPESHUB_ENGINE_LAZY_TOOLS_THRESHOLD` | env | tuning: above this many granted tools an engine's tool list shows the grant's connector and MCP tools only through search_tools and call_tool (default 50; LZ1); PIPESHUB_ENABLE_LAZY_TOOLS=false lists every tool |
| `SANDBOX_DOCKER_IMAGE` | env | image: run_code's sandbox image (blank reads as unset) |
| `SANDBOX_DATA_VOLUME` | env | address: the sandbox broker's data volume holding workspaces and other persistent sandbox folders (default pipeshub_sandbox_data; WP-100) |
| `SANDBOX_EGRESS_NETWORK` | env | address: run_code's egress network (blank reads as unset) |
| `SANDBOX_PIP_INDEX_URL` | env | address: package index; a password in it never enters a sandbox: on the broker the egress proxy adds it (WP-06, OWD-1), docker mode refuses it |
| `SANDBOX_NPM_REGISTRY` | env | address: package registry; a password in it is handled as SANDBOX_PIP_INDEX_URL's |
| `SANDBOX_MAX_CONCURRENT_PER_REQUEST` | env | tuning |
| `SANDBOX_MAX_LIFETIME_S` | env | tuning |
| `SANDBOX_WORKSPACE_QUOTA_MB` | env | tuning: how much a session's persistent workspace may hold on the Docker host's disk (default 4096); the sandbox broker measures it every 10 s and ends a sandbox over it (SEC-ISO-06); not a positive integer refuses to start |
| `SANDBOX_DATA_DEVICE` | env | deployment: the block device under the daemon's volumes (a whole disk or a device-mapper device; install.sh finds it); the sandbox broker caps each sandbox's writes on it (SEC-A-03, ADR-SEC-03 D4); unset: no cap, logged as writeMbps=0 |
| `SANDBOX_WRITE_MBPS` | env | tuning: each sandbox's write cap on SANDBOX_DATA_DEVICE, MiB/s (default 32): a workspace can run past its quota by at most this times the guard's 10 s; not a positive integer refuses to start |
| `HARNESS_WAIT_FOR_PERSON_DAYS` | env | tuning: days a chat run waiting for a person's decision stays answerable (default 7; fractions allowed; the wait does not count against the run's deadline, FIX-P1-F3) |
| `SANDBOX_PROVISION_TIMEOUT_S` | env | tuning |
| `SANDBOX_MAX_TOTAL` | env | tuning: the install's ceiling on live sandboxes (default 50; 0 or less is none). The sandbox broker counts every sandbox of its namespace, whichever broker made it, and a create at the ceiling waits as capacity (C2.19); the legacy per-process governor reads the same value |
| `SANDBOX_SLOT_WAIT_S` | env | tuning: how long a sandbox call waits for a free slot of the per-process governor (main #5245; default 120 s) |
| `SANDBOX_WITHHOLD_RUNTIMES` | env | operator: runtimes the Docker daemon has that the sandbox broker must not offer (only `runsc`; comma-separated, empty by default, any other name refuses to start). Withholding runsc leaves engines to the gVisor floor: refused on a production install, hardened runc under `PIPESHUB_ENGINES_WITHOUT_GVISOR`; the broker logs `withheld=runsc`. The e2e lane sets it with `E2E_SANDBOX_RUNTIME=runc` (R31-GVISO) |
| `SANDBOX_MAX_PER_ORG` | env | tuning: governor cap |
| `SANDBOX_ALLOW_LOCAL` | env | security opt-in from main (#3803), not a harness flag: `SANDBOX_MODE=local` is refused unless it is true; read by the sandbox resolver's own fail-closed parse (a typo stays refused) |
| `SANDBOX_EGRESS_ALLOW_CIDRS` | env | address: private ranges a sandbox with open egress may still reach (main #3780; docker mode and the sandbox broker) |
| `SANDBOX_DISK_QUOTA` | env | tuning from main (#3810): the per-run writable-layer cap for docker-mode sandboxes (default 10g; 0 disables; enforced only where the storage driver supports it) |
| `SANDBOX_STATE_DIR` | env | address from main (#5959): the absolute directory the code sandbox keeps its cross-worker slot and load state in, instead of the temp folder or the home fallback (resource_governor/shared_slots.py) |
| `SANDBOX_STATE_ID` | env | tuning from main (#5959): names this stack's code sandbox state and labels its sandbox containers, for stacks that share one host or daemon |
| `SANDBOX_MAX_PER_REQUEST` | env | tuning from main (#5959): the most code sandbox slots one chat request holds at once (default from the machine-sized ceiling) |
| `SANDBOX_MIN_TOTAL` | env | tuning from main (#5959): the code sandbox ceiling's floor while the machine is being sized |
| `SANDBOX_CAPACITY_DIVISOR` | env | tuning from main (#5959): how many workers share the machine-sized code sandbox ceiling |
| `SANDBOX_HOST_CPU_MILLIS` | env | override from main (#5959): the CPU the code sandbox ceiling is sized from, instead of the machine's |
| `SANDBOX_HOST_MEMORY_BYTES` | env | override from main (#5959): the memory the code sandbox ceiling is sized from, instead of the machine's |
| `SANDBOX_HOST_PID_LIMIT` | env | override from main (#5959): the process limit the code sandbox ceiling is sized from, instead of the machine's |
| `SANDBOX_HOST_MEM_FLOOR_MB` | env | tuning from main (#5959): free host memory below which the live limit admits no new code sandbox |
| `SANDBOX_HOST_MEM_SOFT_MB` | env | tuning from main (#5959): free host memory below which the live limit lowers the code sandbox limit |
| `SANDBOX_DISK_WATCHDOG` | env | tuning from main (#5959): auto, on or off: stop a docker-mode run whose writable layer passes its quota where the daemon does not enforce it |
| `SANDBOX_DISK_WATCH_CHECK_TIMEOUT_S` | env | tuning from main (#5959): how long one disk watchdog size check may take before it counts as failed |
| `SANDBOX_DISK_WATCH_MAX_FAILURES` | env | tuning from main (#5959): failed disk watchdog checks in a row after which the run is stopped |
| `SANDBOX_LOG_MAX_SIZE` | env | tuning from main (#5959): the size a sandbox container's log rotates at (default 10m) |
| `SANDBOX_LOG_MAX_FILES` | env | tuning from main (#5959): the rotated log files a sandbox container keeps (default 3) |
| `SANDBOX_EGRESS_DNS_SERVERS` | env | address from main (#5959): IPv4 resolvers a networked sandbox may reach on port 53, beside the cloud resolvers |
| `SANDBOX_OUTPUT_MAX_FILE_BYTES` | env | tuning from main (#5959): the largest file one run hands back |
| `SANDBOX_OUTPUT_MAX_TOTAL_BYTES` | env | tuning from main (#5959): the bytes one run hands back in all |
| `SANDBOX_OUTPUT_MAX_FILES` | env | tuning from main (#5959): the files one run hands back |
| `SANDBOX_OUTPUT_MAX_SANDBOX_BYTES` | env | tuning from main (#5959): the bytes a sandbox keeps across its runs |
| `SANDBOX_OUTPUT_MAX_SANDBOX_FILES` | env | tuning from main (#5959): the files a sandbox keeps across its runs |
| `SANDBOX_DEPS_MAX_TOTAL_BYTES` | env | tuning from main (#5959): the bytes one package install hands back |
| `SANDBOX_STDIO_HEAD_BYTES` | env | tuning from main (#5959): the head of a run's stdout and stderr that is kept |
| `SANDBOX_STDIO_TAIL_BYTES` | env | tuning from main (#5959): the tail of a run's stdout and stderr that is kept |
| `DOCKER_PROXY_SIDECAR_STATS_FILE` | env | address from main (#5959): the file the Docker-in-Docker sidecar's usage loop writes, read by the proxy's /_pipeshub/usage |
| `SANDBOX_EXTRA_ALLOWED_PY_PACKAGES` | env | tuning from main: Python packages a sandbox may install beyond the built-in allowlist |
| `SANDBOX_EXTRA_ALLOWED_NPM_PACKAGES` | env | tuning from main: npm packages a sandbox may install beyond the built-in allowlist |
| `SANDBOX_PG_URL` | env | address from main: the PostgreSQL server the legacy database_sandbox toolset uses (unset: the toolset says how to enable it); on the broker the run reaches it through the egress proxy, which signs in, so its password never enters the sandbox (WP-06); docker mode refuses one with a password |
| `E2B_API_KEY` | env | secret: the E2B account key for SANDBOX_MODE=e2b |
| `ARTIFACT_CLEANUP_INTERVAL_MINUTES` | env | tuning from main: how often temporary sandbox artifacts are swept |
| `ARTIFACT_TEMP_TTL_HOURS` | env | tuning from main: how long a temporary sandbox artifact is kept |
| `DOCKER_PROXY_HOST` | env | address from main (#3782): the Docker policy proxy's listen host (docker mode only) |
| `DOCKER_PROXY_PORT` | env | address from main (#3782): the Docker policy proxy's listen port (docker mode only) |
| `DOCKER_PROXY_UPSTREAM` | env | address from main (#3782): the Docker daemon the policy proxy forwards to (docker mode only) |
| `DOCKER_PROXY_LOG_LEVEL` | env | tuning from main (#3782): the Docker policy proxy's log level |
| `DOCKER_PROXY_TOKEN` | env | credential (SEC-A-04): the app's legacy executor's credential at the Docker policy proxy; unset on the compatibility profile (R51-COMPAT, ADR-OPS-07), the executor calls without one, as main did |
| `AGENT_HARNESS_INTERNAL_PORT` | env | address (H6): the port on which Node alone serves /api/v1/agent-harness/internal/* (default 3002); never published; AGENT_HARNESS_STATE_URL points at it |
| `AGENT_HARNESS_INTERNAL_HOST` | env | address (H6): the host Node's internal listener binds (default 0.0.0.0, for Compose's sandbox plane; Helm binds 127.0.0.1) |
| `PIPESHUB_BIND_HOST` | env | address (H6): the one address a process listens on (the native bundle's loopback); the internal listener binds it when `AGENT_HARNESS_INTERNAL_HOST` is unset |
| `HARNESS_SIGNIN_IDLE_MINUTES` | env | limit: how long a person's signed-in Claude Code sandbox is kept between a conversation's turns (5-120, default 30; SIGNIN-5) |
| `HARNESS_SIGNIN_MAX_HOURS` | env | limit: the longest a person's Claude sign-in is kept after it was made (1-24, default 12; SIGNIN-5) |
| `HARNESS_TOOL_GATEWAY_PORT` | env | address: the worker's tool gateway listen port (default 8098) |
| `HARNESS_TOOL_GATEWAY_URL` | env | address: where the egress proxy reaches the worker's tool gateway; its origin is where Node's GET /api/v1/agent-harness/internal/metrics scrapes the gateway's /metrics (C9.04) |
| `EGRESS_PROXY_CONTROL_BACKEND` | env | address: the egress proxy's control plane, which Node writes into /services/endpoints and whose /metrics its internal metrics route scrapes with the worker credential (C9.04) |
| `MODEL_GATEWAY_BACKEND` | env | address: the Model Gateway, which Node writes into /services/endpoints and whose /metrics its internal metrics route scrapes with the worker credential (C9.04) |
| `TOOL_GATEWAY_BACKEND` | env | address: the Tool Gateway entry Node writes into /services/endpoints; the metrics route falls back to its origin only when HARNESS_TOOL_GATEWAY_URL is unset (C9.04) |
| `SANDBOX_INSTALL_ID` | env | identity: the install's folder on the broker's shared data volume (E1) |
| `SANDBOX_PROVIDER` | env | deployment: which sandbox provider the install runs (C2.14): unset or `docker` is the sandbox broker's Docker provider; `k8s`, `managed` and `microvm` are refused by name at the worker and the broker's start, and any other value is refused, never read as Docker |
| `COMPOSE_PROJECT_NAME` | env | identity: the broker's install id when SANDBOX_INSTALL_ID is unset |
| `SANDBOX_BROKER_BACKEND` | env | address: the sandbox broker for Node's run sweeper when /services/endpoints names none; its origin is where Node's GET /api/v1/agent-harness/internal/metrics scrapes the broker's /metrics (C9.04) |
| `HARNESS_METRICS_CREDENTIAL_FILE` | env | address: where Node writes the worker credential (mode 0600) for the deployment's own Prometheus to read the internal metrics route; unset (the default) writes nothing; set by docker-compose.monitoring.yml (C9.04) |
| `HARNESS_METRICS_CREDENTIAL_OWNER` | env | identity: the uid that file is given when Node runs as root (default 65534, the Prometheus image's user) (C9.04) |
| `/services/agentHarness/workerCredential` | config | key material: the worker credential Node provisions |
| `/services/agentHarness/attemptSigningKeys` | config | key material |
| `/services/agentHarness/attemptPublicKeys` | config | key material |
| `/services/agentHarness/gatewaySigningKeys` | config | key material |
| `/services/agentHarness/gatewayPublicKeys` | config | key material |
| `/services/agentHarness/credentials` | config | key material: vault secrets under /services/agentHarness/credentials/{orgId}/{name} |
| `/services/agentHarness/credentialKeys` | config | key material: the vault keyring |
| `SECRET_KEY` | env | secret: the deployment key; the egress proxy derives the key that seals its durable state from it (P5.4b) |
| `HARNESS_FLOW_GUARD_LEVEL` | env | policy: what the flow rule does to a call that sends data out once the run read private data: ask (default) or deny; any other value asks, logged once |
| `HARNESS_FLOW_GUARD_ABOVE` | env | policy: the flow rule's threshold: public (default: any record read) or private (only content of unknown provenance); any other value is public, logged once |
| `HARNESS_ALLOWED_WEBSITES_CAP` | env | policy: the deployment's cap on allowed websites (WP-63): comma-separated host (read only), host=read-write or host=host-level entries, *.domain for subdomains, none for no site; unset: no cap; an entry Node cannot read stops it at start |
| `HARNESS_ALLOWED_REPOSITORIES_CAP` | env | policy: the deployment's cap on allowed git hosts for repository_fetch (WP-63 part A): comma-separated host (every public repository on it), host/owner/ or host/owner/repo entries, none for no host; unset: no cap; an entry Node cannot read stops it at start; an org's list outside it is CP_WIDENING |
| `HARNESS_REPO_CACHE_DIR` | env | storage: where the worker keeps fetched repositories for repository_fetch (WP-63 part A; default /data/pipeshub/harness/repository-cache, inside Compose's pipeshub_data volume; on Helm mount a volume there or point this at one), shared by every run of the worker |
| `HARNESS_REPO_CACHE_MAX_MB` | env | tuning: the most the repository cache holds, in MB (default 2048); past it the least recently used fetches are removed |
| `HARNESS_REPO_CACHE_MIN_FREE_MB` | env | tuning: the free disk, in MB, a repository_fetch must leave on the cache's volume; unset: 5% of the volume, at least 2048 and at most 20480; a clone that would leave less is refused, and the Git hosts screen warns below twice it |
| `HARNESS_REPO_MAX_MB` | env | tuning: the most one repository_fetch may bring, in MB (default 500); a clone past it is killed and nothing is kept |
| `HARNESS_INPUT_MAX_MB` | env | limit (J-26): the largest file a run is given as an input, in MiB (default 100), and so the largest artifact the registry keeps and the largest object Node's internal storage upload takes (with `AGENT_HARNESS_WORKER_ENABLED` on; off, that route keeps the platform's cap and never reads this), and the largest file an engine's workspace sync writes back changed (round 14: one number in and out); an org's sandbox.inputMaxMb lowers it, never raises it; inputs over 8 MiB reach the sandbox only on their first read; not a positive whole number refuses to start |
| `HARNESS_REPO_FETCH_CONCURRENCY` | env | tuning: how many repository fetches one worker runs at once (default 2); the rest wait |
| `HARNESS_REPO_FETCH_TIMEOUT_S` | env | tuning: how long one repository fetch may take, in seconds (default 300); past it the clone is killed |
| `HARNESS_REPO_CACHE_MAX_AGE_S` | env | tuning: how long a fetched repository is reused for the same org, URL, ref and depth before it is fetched again, in seconds (default 600; 0: always fetch) |
| `HARNESS_REPO_GIT_BIN` | env | security: the pinned git binary repository_fetch runs (default /usr/bin/git; git 2.37 or newer, for http.curloptResolve) |
| `HARNESS_REPO_CA_FILE` | env | security: an authority bundle git checks the git host's certificate with instead of the system's (an enterprise mirror with its own authority); unset: the system's |
| `PIPESHUB_SKILLS_PRELOAD_BODY_THRESHOLD` | env | tuning: the relevance above which a skill's body is preloaded into the prompt (float, default 0.75) |
| `PIPESHUB_SKILLS_PRELOAD_MENTION_THRESHOLD` | env | tuning: the relevance above which a skill is mentioned in the prompt (float, default 0.4) |
| `PIPESHUB_SKILLS_CATALOG_RENDER_LIMIT` | env | tuning: how many skills the catalog lists in a prompt (int, default 40) |
| `PIPESHUB_SKILLS_MAX_RESOURCE_FILE_BYTES` | env | limit: the largest bundled resource file a skill may hold (bytes, default 1 MiB) |
| `PIPESHUB_SKILLS_MAX_RESOURCE_BYTES` | env | limit: the most bundled resource bytes one skill may hold (bytes, default 5 MiB) |
| `HARNESS_MODEL_OUTPUT_LIMIT` | env | tuning: tokens one model call may produce (P4.17): agent turns and engine calls through the model gateway (default 32768; structured calls and summaries 8192 under it), also the switch for the repetition cut; 0: no limit sent and no cut (the provider's own maximum, as before); 1-4095 reads as 4096 |
| `HARNESS_DEEP_ROUTING_THRESHOLDS` | env | tuning: when a durable loop run that may hand off has outgrown a chat reply (DEEPROUTE), as toolCalls=12,turns=6,contextShare=0.5,spendShare=0.3 (the defaults; a key left out keeps its default, 0 turns that one off; one the worker cannot read is logged and the defaults apply) |
| `PIPESHUB_SKILLS_CATALOG_RENDER_LIMIT` | env | tuning: how many skills the prompt's skill catalog lists (default 40) |
| `PIPESHUB_SKILLS_PRELOAD_BODY_THRESHOLD` | env | tuning: the similarity at which a skill's body is preloaded into a run (default 0.75) |
| `PIPESHUB_SKILLS_PRELOAD_MENTION_THRESHOLD` | env | tuning: the similarity at which a skill is mentioned to a run (default 0.4) |
| `PIPESHUB_SKILLS_MAX_RESOURCE_FILE_BYTES` | env | limit: the largest resource file a skill may bundle (default 1 MiB) |
| `PIPESHUB_SKILLS_MAX_RESOURCE_BYTES` | env | limit: all of a skill's bundled resources together (default 5 MiB) |
| `PIPESHUB_SKILL_VECTOR_CACHE_SIZE` | env | tuning: how many skill embeddings the process caches (default 4096) |
| `HARNESS_HTTP_HEADER_TIMEOUT_S` | env | tuning (C6.40, SEC-DOS-04): seconds a client of a Python harness listener (tool gateway, model gateway, egress proxy, sandbox broker) has to send a request head, from its first byte or from connecting (default 15) |
| `HARNESS_HTTP_REQUEST_TIMEOUT_S` | env | tuning (C6.40, SEC-DOS-04): seconds a client of a Python harness listener has to send a whole request, plus the time its bytes take at HARNESS_HTTP_MIN_BODY_BYTES_PER_S; a response is never timed (default 60) |
| `HARNESS_HTTP_MIN_BODY_BYTES_PER_S` | env | tuning (C6.40, SEC-DOS-04): the rate at which a request body earns time past HARNESS_HTTP_REQUEST_TIMEOUT_S, so a large upload at speed is never cut and a drip is (default 65536) |
| `HARNESS_HTTP_MAX_CONNECTIONS` | env | tuning (C6.40, SEC-DOS-04): connections one Python harness listener holds at once; past it a new one is closed on arrival (default 4096) |
| `HARNESS_ARCHIVE_MAX_RATIO` | env | tuning (C6.40, SEC-FS-03): how many times its own size an archive the harness unpacks may expand to, past HARNESS_ARCHIVE_RATIO_FLOOR_BYTES (snapshots, sandbox folders, collected outputs, pushed packs; default 100) |
| `HARNESS_ARCHIVE_RATIO_FLOOR_BYTES` | env | tuning (C6.40, SEC-FS-03): the bytes an archive may expand to before HARNESS_ARCHIVE_MAX_RATIO applies (default 67108864, the snapshot capture cap) |
| `HARNESS_ARCHIVE_MAX_MEMBERS` | env | tuning (C6.40, SEC-FS-03): members an archive the harness reads whole may hold (snapshots, sandbox folders, a pushed pack's objects; default 50000; the workspace sync keeps its own 50000) |
| `AGENT_HARNESS_INTERNAL_HEADERS_TIMEOUT_MS` | env | tuning (C6.40, SEC-DOS-04): milliseconds a client of Node's internal listener has to send a request head (default 15000) |
| `AGENT_HARNESS_INTERNAL_REQUEST_TIMEOUT_MS` | env | tuning (C6.40, SEC-DOS-04): milliseconds a client of Node's internal listener has to send a whole request; a response is never timed (default 60000) |
| `AGENT_HARNESS_INTERNAL_MAX_CONNECTIONS` | env | tuning (C6.40, SEC-DOS-04): connections Node's internal listener holds at once (default 1024) |
| `/services/agentHarness/roleCredentials/scrape` | config | key material: the scraper's credential (SEC-C-10, ADR-CRED-01) |
| `/services/agentHarness/roleCredentials/egressProxy` | config | key material: the egress proxy's credential (SEC-C-10, ADR-CRED-01) |
| `/services/agentHarness/roleCredentials/sandboxBroker` | config | key material: the sandbox broker's credential (SEC-C-10, ADR-CRED-01) |
| `/services/agentHarness/components/egressProxy` | config | key material: what Node seals for the egress proxy under its own key (ADR-KEY-02) |
| `/services/agentHarness/components/sandboxBroker` | config | key material: what Node seals for the sandbox broker under its own key (ADR-KEY-02) |
| `EGRESS_PROXY_COMPONENT_KEY` | env | key material: the egress proxy's own key, held by Node and the proxy (ADR-KEY-02); install.sh generates it |
| `SANDBOX_BROKER_COMPONENT_KEY` | env | key material: the sandbox broker's own key, held by Node and the broker (ADR-KEY-02); install.sh generates it |

## Deep-run defaults (WP-62, not flags)

Fixed in code, documented here so an operator knows what limits a long run. None is a switch.

- **Web allowance** (`app/web/budget.py`). A chat answer: 10 searches and 20 page reads; with deep research on, 24 and 40. A coding engine run (`code_task`, a coder spawn), a deep loop run (its root and its researcher children, WP-56), and a scheduled or triggered loop run in any mode (unattended: nobody is there to say "continue", WP56-WEB-4): 150 searches and 400 page reads. A person's quick or plan-and-execute chat answer keeps an answer's count. For such a run this is a backstop against a runaway loop, not the limit: the run's USD budget (`budgets.maxCostUsd`, release default USD 5) is. Each of its web results ends with `[web allowance left for this task: N searches, M page reads]`; a spent allowance answers `budget_exhausted` with what is left and a hint to finish from what was gathered. A spent allowance is never a stall.
- **One page's text** (`app/web/tools/fetch_url.py`): 1,000,000 characters; a longer page is cut and the result says how to read the rest (a narrower address).
- **Journal writes carrying tool output** (`harness_core/journal/bounds.py`, `app/harness/citations.py`): a tool result as journaled 2 MiB, a citation delta 4 MiB, a child's result document 2 MiB (its answer kept first), a run's final answer 1 MiB, each well under Node's 8 MiB content limit (`CONTENT_MAX_BYTES`). Past a bound the copy is shortened with a note; the engine still reads the full result from the gateway's answer or its shelf file.
- **Engine stall rounds** (`harness_core/engines/controller.py`): a round is the gateway calls of one model turn of the engine, marked by the run's usage ledger (the engine's own usage events where no ledger is kept), and weighed before the next turn's first call. Thresholds are unchanged (6 stale, 4 repeated rounds).
- **Estimated charge** (`harness_core/gateway/model_gateway.py`). A model call the provider never reported usage for (the engine went away mid-stream, the run was stopped, or a transport reports none) is charged its input at the gateway's estimate. Of that, the run's last prompt on the same route is charged at the cache-read price when the provider served that prompt from its cache within the last 5 minutes. Output is charged only as far as it streamed. Reported usage always wins. A failure before any output is not charged. The log line keeps `estimated=True` and shows the estimated `inputTokens`, `cacheReadTokens` and `outputTokens`. The cache-credit record is per gateway process.
- **Engine step allowance** (`harness_core/engines/allowance.py`, WP-70). A metered engine run with a USD budget may take 100 model steps per USD of it (at least 50), never more than its profile's `limits.maxSteps` (release 1000; an admin's narrower ceiling binds). An unmetered run (a person's own plan) keeps its profile's steps. The stall rounds above still stop a loop that makes no progress.
- **Writing reserve** (`harness_core/engines/allowance.py`, WP-70). Within 15% of a turn's steps (at least 8), wall time (at least 2 minutes) or the run's money (read from its ledger every 30 seconds), the engine is told once to stop gathering and write its deliverable (steered where the engine supports it), and its later gateway calls are refused with the same words. A turn a limit then cuts is answered by the run's wrap-up from what it gathered; a requested file still owed (nothing under out/ over 1.5 KB) is that answer, or, with no wrap-up answer, stays a draft and the answer says it is not written yet.
- **Refusing hosts** (`app/web/tools/fetch_url.py`, WP-70). fetch_url counts each host's 401, 403 and 429 per run; from the second refusal by one host the result is not retryable and says to stop calling it; GitHub hosts get a hint to clone or read a raw file.
- **Web search on a keyless engine** (`app/web/chain.py`): DuckDuckGo takes one search at a time per process and ddgs two; a source that just blocked is skipped for its cooldown. An org that searches a lot should add a provider under Workspace > Web Search (a self-hosted SearXNG needs no key).

## Priority classes in flight (G-45, not flags)

Fixed in code (`harness_core/priority.py`, `harness_core/gateway/priority_gate.py`, `app/harness/provider_gate.py`), documented here so an operator knows when a class takes effect and how one upstream's limit is shared. None is a switch.

- **When an answered run's class rises.** A run a person answers (an approval, a question) rises to `approval_resume`. The attempt already running is not changed: a loop run's own model calls, its place in the provider limit and whether it may be preempted stay at the class its claim named, and the run's next claim is served at the raised class, ahead of older runs below it. A coding-engine attempt is the exception: its calls through the Model Gateway read the class from the run's model grant, which the gateway caches for up to 60 seconds per attempt (`CACHE_TTL_S`, `app/harness/access/grants.py`), so within that attempt they rise at the grant's next read, at most 60 seconds after the answer, never sooner and never through another attempt's cache. A preemptible attempt a person just answered can still be asked to yield before then; it comes back first at its next claim.
- **Orgs sharing one upstream.** The provider limit (`MODEL_GATEWAY_PROVIDER_CONCURRENCY`) is per upstream: the provider, the account and the endpoint or region. When several orgs are served on one account (one key configured for all of them), they share that one limit. The class order comes first; within a class a freed slot goes to the waiting call of the org holding fewest of that upstream's slots, oldest first among equals, so one org's burst never queues another org's call behind all of it. An org alone on an upstream still uses the whole limit. Calls already running are never cut to make room. The shared gate stores a digest of the org, never its id. Each ask weighs only the first 256 waiting calls of the queue (`FAIR_SCAN`), so one ask stays cheap whatever the backlog; calls queued beyond them keep queue order (FIFO within their class) until the queue ahead shortens.

## Open product questions (defaults not changed by F3)

- **PQ-1** Session retention defaults to no automatic expiry (E7; ENGINES-PLAN decision 8).
- **PQ-2** (closed by main #3780) `SANDBOX_ALLOW_NETWORK`: Helm, Compose and the code all default to `false`.
- **PQ-3** Approvals had no admin route (closed by P2.11e; that route has since moved out of this repository);
  P2.6c's product question on defaulting new orgs to "ask for writes" is still open.
- **PQ-4** Helm turns relay and worker on together (`agentHarness.enabled`) and renders no
  `MODEL_GATEWAY_ENABLED`; Compose has three switches. Intended (the worker and relay are only useful
  together), recorded so a split is a decision.
