# Agent Harness Runtime — Design Plan

Status: **proposal / pre-ADR**. Date: 2026-10-10.

PipesHub becomes the **control plane** for third-party agent harnesses — Claude Code / Claude Agent SDK, OpenAI Codex, OpenCode, Gemini CLI, Goose, Qwen Code, DeepSeek Harness, and PipesHub's own `agent_loop_lib` — running inside **sandboxes**. PipesHub supplies identity, knowledge, tools, skills, models, policy, approvals, persistence and observability. The harness supplies the agent loop.

Companion documents:

| Doc | What it holds |
| --- | --- |
| [one-way-doors.md](./one-way-doors.md) | Decisions that are expensive or impossible to reverse, with a recommendation for each. Read this first. |
| [test-scenarios.md](./test-scenarios.md) | The QA and user-perspective catalogue: user journeys, functional, security, performance, chaos, compatibility, evals. About 300 cases with IDs. |
| [research.md](./research.md) | Research synthesis with sources: harness protocols, sandboxes, security, context engineering, evals, open-model quirks. |

---

## 1. Goals, non-goals, personas

### Goals

1. **Any harness, one contract.** Run any supported harness in a sandbox behind one session API, one event stream and one UI. Adding a new harness means writing one adapter, with no changes to the core.
2. **PipesHub is the source of truth for context.** Each harness gets these on day one:
   - permission-aware enterprise knowledge
   - the agent's toolsets, MCP servers and skills
   - toolset schemas on the filesystem
   - an `AGENTS.md` map

   All of this is scoped to the user it acts for.
3. **Secrets never enter the sandbox.** This covers model keys, connector OAuth tokens, git credentials and PipesHub tokens. Gateways outside the sandbox inject credentials.
4. **Safe by construction.** Isolation, default-deny egress, taint-aware policy, human approvals for consequential actions, and a complete audit trail.
5. **Production-grade lifecycle.** Long-running and background sessions, cancel, resume, fork, schedules and triggers, budgets, and recovery from any single component dying.
6. **Deployable where PipesHub is deployed.** This includes Docker Compose on one VM, Helm on Kubernetes, and air-gapped installs. SaaS sandbox providers are optional adapters, not requirements.

### Non-goals (for v1)

- Building a new agent loop. `agent_loop_lib` already exists; the harnesses bring their own.
- A general-purpose cloud IDE. We expose a file browser, diffs, a terminal view and a preview port, not a full editor.
- Training or fine-tuning.
- Desktop-local execution. `desktop_proxy` is a separate track.

### Personas (every test scenario maps to one)

| Persona | Wants |
| --- | --- |
| **Knowledge worker** | "Research X across Drive, Slack and Jira and write me a brief or deck." Never sees a terminal, but expects citations and correct permissions. |
| **Developer** | "Fix this bug / write this feature with Claude Code or Codex, using our internal docs." Expects git, tests, a PR, and a model of their choice. |
| **Agent builder** | Composes an agent from a harness, a model, tools, knowledge, skills and a schedule. Wants evals before publishing. |
| **Org admin** | Chooses which harnesses, models, MCP servers and egress domains are allowed; sets budgets; sees usage. |
| **Security / compliance officer** | Needs isolation guarantees, an audit trail, DLP, data residency, a kill switch and incident forensics. |
| **Platform operator (self-host)** | Installs on Compose or Helm, sizes capacity, upgrades harness versions, debugs stuck sessions. |
| **Integrator / API user** | Drives sessions over REST, SSE, MCP or A2A from their own systems, CI or Slack. |

---

## 2. Build on what exists — don't duplicate

A full map of the current code is in [research.md §0](./research.md#0-what-pipeshub-already-has). The parts that matter:

| Need | Already in repo | Plan |
| --- | --- | --- |
| Sandboxes | `agent_loop_lib/sandbox/` (`CodingSandboxBackend`: local, Docker, E2B; `SandboxManager`; `SandboxResourceGovernor`; `egress_firewall.py`) and `app/sandbox/docker_proxy.py` (policy-enforcing Docker API proxy) | Extend into a long-lived **`ISandboxProvider`** with attach, snapshot, suspend and egress policy. Reuse the docker-proxy policy and egress firewall. Move the governor from in-process to cluster-wide state (KV). |
| Skills | `modules/providers/skills/bundle.py` (`SKILLS_MOUNT_ROOT`, `SkillBundle.as_sandbox_files()`, `SkillMaterializer`), `GraphSkillStore`, skill REST + UI | Reuse as is for the skills part of workspace materialization. |
| Tool schemas | `core/tool_schema.py::ToolSchema`, `ToolsetRegistry`, `GET /api/v1/toolsets/registry/*`, lazy-tool meta-tools (`list_toolsets`, `search_tools`, `fetch_tools`) | Export to the filesystem and serve through the Tool Gateway. Keep the existing `tool_disclosure` eager/lazy setting as the per-agent switch. |
| Toolset auth | per-user creds in etcd via `ConfigurationService`, OAuth flows, service-account agents | Tool calls execute **outside** the sandbox with these credentials, unchanged. |
| External MCP | `agents/mcp/` (`MCPClientManager`, `MCPSessionManager`, catalog, OAuth, stdio policy) | Proxy external MCP servers through the Tool Gateway. Never hand server credentials to the sandbox. |
| PipesHub MCP server | Node `/mcp` (`@pipeshub-ai/mcp`, 6 fixed tools, PAT/OAuth) | Leave in place for user-run clients. Add a per-session, internal-only Tool Gateway endpoint for sandboxed harnesses. |
| Knowledge search | `retrieval_service.search_with_filters` (ACL-filtered); `actions/retrieval` (coupled to `ChatState`) | Separate a stateless `knowledge.search` / `knowledge.fetch` service API from `ChatState`. Citations go through `utils/citations.py` and `utils/text_fragments/`. |
| Agent definition | `agentInstances` in the graph with `AGENT_HAS_*` edges for toolsets, MCP, knowledge and skills | Add `runtime: {kind: native\|harness, harnessProfileId}`. Everything else about the agent stays. |
| Streaming to UI | AG-UI (`protocol/agui.py`, `frontend/app/(main)/chat/agui-event-handler.ts`) | Map canonical harness events to AG-UI, with new CUSTOM events for terminal, diff, approval and todo. |
| Cancel | `KVBackedRunCancellationRegistry` (`chat:run:{id}:cancel`) | Reuse the pattern for session interrupt and kill. |
| Artifacts | `ArtifactRegistryService` (blob + graph + permissions + lineage) | `/pipeshub/outputs/**` is collected into artifacts with lineage to the session. |
| Composition | `AgentRunner` protocol, `AgentTool`, `spawn_agent`, deep-mode orchestrator | Add a **`HarnessAgentRunner`** implementing `AgentRunner`, so a Claude Code or Codex session can be a worker inside PipesHub deep mode. |
| Auth | PAT `phpat_`, service tokens `phsvc_`, `generateScopedToken`, `mint_service_token`, `require_scopes` | Add a **session credential**: opaque, sandbox-bound, revocable, and injected by the egress proxy. |

Naming collision: `agent_loop_lib/control_plane/control_plane.py::ControlPlane` already exists as the library's composition root. The new subsystem must not reuse that class name. Use **Harness Runtime** (service `app.harness_main`) and `HarnessSessionService`.

---

## 3. Architecture

```text
                 ┌──────────────────────────── PipesHub (control plane) ─────────────────────────────┐
 Browser / API   │                                                                                    │
 Slack / A2A ───►│  Node gateway (auth, PAT/OAuth, rate limit, REST/SSE proxy, OpenAPI)                │
 Schedules  ────►│        │                                                                           │
 Triggers   ────►│        ▼                                                                           │
                 │  ┌──────────────── Harness Runtime service (Python, app.harness_main) ───────────┐ │
                 │  │ Session Orchestrator ── state machine, leases, budgets, retries, schedules     │ │
                 │  │ Workspace Materializer ── AGENTS.md, skills, tool schemas, harness configs     │ │
                 │  │ Event Store ── append-only canonical events (Mongo) + blobs (transcripts)     │ │
                 │  │ Policy Engine ── RBAC, taint labels, Rule-of-Two, egress & tool policy        │ │
                 │  │ Approval Service ── durable HITL requests, notifications, expiry              │ │
                 │  │ Tool Gateway (MCP + REST) ── knowledge, toolsets, external MCP, skills, ask    │ │
                 │  │ Model Gateway ── Anthropic/OpenAI-Responses/Chat/Gemini wire, metering, keys  │ │
                 │  └──────────────────────────────────────────────────────────────────────────────┘ │
                 │        │ provider API (create/attach/snapshot/destroy)        ▲ only path in/out   │
                 └────────┼──────────────────────────────────────────────────────┼───────────────────┘
                          ▼                                                      │
        ┌──────────────── Sandbox (one per session, per user; never reused) ─────┼──────────────────┐
        │  sandboxd (supervisor + adapter host)  ◄── control channel (attach/exec or WS via proxy)   │
        │    ├─ Harness adapter: claude | codex | opencode | gemini | goose | acp | terminal       │
        │    └─ Harness process (Claude Code, codex app-server, opencode serve, …)                 │
        │  /pipeshub  (materialized, read-only parts + writable workspace/outputs)                 │
        │  `ph` CLI   (talks to Tool Gateway via egress proxy; no token in sandbox)                │
        │  no network interface except → Egress Proxy                                             │
        └──────────────────────────────────────────┬──────────────────────────────────────────────┘
                                                   ▼
                     Egress Proxy (outside sandbox): per-session allowlist, DNS at proxy,
                     credential injection (session cred → Tool/Model Gateway; git token → git host,
                     branch-scoped), package mirrors, redacting logs, kill switch
```

**Principles**

- **The outer sandbox is the security boundary.** A harness's built-in sandbox (Seatbelt, Landlock, bubblewrap) is defence in depth where the kernel allows it. Each harness is configured explicitly to rely on the outer boundary, and this is tested per harness.
- **The transcript lives outside the sandbox.** If a sandbox dies, the session is not lost (Rivet sandbox-agent and OpenHands both converged on this).
- **Setup phase vs agent phase.** The setup phase may have network access and secrets (for example to clone a repo or install dependencies). The agent phase has restricted egress and no secrets, following the Codex cloud and Copilot coding agent pattern.
- **Inject config explicitly. Never trust config inside the workspace.** A cloned repo's `.mcp.json`, hooks, `.codex/config.toml` or `opencode.json` must not silently load. For example, Claude Code `-p` without `--bare` runs project hooks and connects `.mcp.json` servers with no trust prompt.
- **Deterministic invariants in code, judgment in the model.** This matches `docs/multi-agent-best-practices.md` §3. Budgets, approvals, egress and ACLs are enforced by gateways and the policy engine, never by prompt text.

---

## 4. Components

### 4.1 Session Orchestrator

- **Owns** the session state machine:

  ```text
  CREATED → PROVISIONING → SETUP → READY ⇄ RUNNING ⇄ AWAITING_INPUT | AWAITING_APPROVAL
          → IDLE → SUSPENDED → (RESUMING → READY) | TERMINATED | FAILED
  ```

- **One owner per session.** Ownership uses a lease (KV, the same pattern as the indexing service leases), so any replica can take over.
- **Budgets per session:** turns, wall-clock, tokens, cost, sandbox-seconds, and tool-call counts. Hard caps are enforced in the gateways. The orchestrator only aggregates.
- **Idempotent commands:** `create`, `send_message` (client message id), `interrupt`, `steer`, `approve`, `fork`, `suspend`, `resume`, `terminate`.
- **Schedules and triggers:** cron, MessagingFactory events (for example a record-event or a new Jira issue), webhooks, and Slack mentions. Each firing creates a session or resumes a pinned one.
- **Implements `HarnessAgentRunner(AgentRunner)`,** so `spawn_agent` / `AgentTool` can delegate to a harness session.

### 4.2 Sandbox Provider (`ISandboxProvider` + `SandboxProviderFactory`)

- **Interface:** `create(template, limits, egress_policy, volumes) → SandboxRef`, `attach(ref) → bidirectional stream`, `exec`, `put_files` / `get_files`, `snapshot`, `restore`, `suspend` / `resume`, `destroy`, `health`, `capabilities()`.
- **Backends, in priority order:**
  1. Docker + gVisor (`runsc`) for Compose installs, reusing `docker_proxy.py` policy.
  2. Kubernetes `kubernetes-sigs/agent-sandbox` (Sandbox / SandboxTemplate / SandboxClaim / SandboxWarmPool) with a gVisor or Kata `RuntimeClass` for Helm installs.
  3. E2B (also self-hostable), Daytona, Modal and Vercel as SaaS adapters.
- **Warm pools** keyed by (template, harness version). A pooled sandbox is claimed once and destroyed after use; it is **never** returned to the pool.
- **Images:** a base image plus a pinned harness layer per harness version, referenced by digest, never by tag. Harness binaries are pre-installed, never fetched at run time.
- **Resource limits:** CPU, memory, pids, ephemeral storage, disk quota, plus a cluster-wide governor (per org, per user, global) that replaces the per-process `SandboxResourceGovernor` caps.

### 4.3 `sandboxd` (in-sandbox supervisor)

- A small static binary (Go or Rust), or a Python process if we accept the image weight. Unprivileged.
- **Responsibilities:**
  - launch and supervise the harness process
  - host the **harness adapter**
  - normalize events to the canonical schema
  - keep a local ring buffer with sequence numbers, so the orchestrator can re-attach and replay from `ack_seq`
  - run a PTY for a live terminal view
  - watch files for diffs
  - collect outputs
  - send health heartbeats
- **Control channel:**
  - Preferred: the provider's attach/exec stdio (no network credential needed in the sandbox).
  - Fallback: a daemon-initiated WebSocket through the egress proxy, which injects the session credential.
  - The protocol is the same on both. It is modelled on ACP `session/update` so we can adopt ACP's remote transport later.
- **Holds no secrets.** Writes nothing outside `/pipeshub` and the workspace.

### 4.4 Harness Adapters

Every adapter implements one contract: `capabilities()`, `materialize_config(profile, catalog)`, `start()`, `send(message)`, `steer()`, `interrupt()`, `resume(native_session_ref)`, `fork()`, `on_permission_request → policy`, `parse(native_event) → canonical events`, `usage()`.

| Harness | Drive via | Approvals bridged through | Resume |
| --- | --- | --- | --- |
| Claude Code / Agent SDK | Agent SDK (`canUseTool`, hooks) or `claude -p --bare --input-format stream-json --output-format stream-json --verbose` | `canUseTool` / `--permission-prompt-tool` → Approval Service | `--resume <id>`; transcript `.jsonl` is persisted outside |
| Codex | `codex app-server` (JSON-RPC: `thread/*`, `turn/start\|steer\|interrupt`, approval requests). `codex exec --json` for batch | `item/*/requestApproval` server requests | `thread/resume`, `thread/fork`; `~/.codex/sessions` persisted |
| OpenCode | `opencode serve` (HTTP + SSE, OpenAPI) or `opencode acp` | permission events | session API |
| Gemini CLI, Goose, Qwen Code, Cursor CLI, Kilo | ACP (`--acp`, `goose acp`, …) via a **generic ACP adapter**; stream-json where ACP lacks fidelity | `session/request_permission` | `session/load` (ACP v1) / `session/resume` (v2 draft) |
| DeepSeek Harness (`dsh`) | headless profile JSON events (developer preview; verify) | hook bridge | session continuation |
| Aider and other TUI-only tools | terminal adapter (AgentAPI-style screen scraping). Last resort, marked "limited" | none (auto-mode only) | none |
| PipesHub native (`agent_loop_lib`) | in-process `AgentRunner`; sandbox only for code tools | existing middleware | existing |

- **Capability negotiation drives the UI and policy.** Each adapter declares capabilities such as `supports_steer`, `supports_fork`, `supports_resume`, `supports_permission_bridge`, `supports_mcp_http`, `supports_skills`, `reasoning_visible`, `subagents` and `structured_output`.
- **Conformance.** Every adapter must pass the shared conformance suite in [test-scenarios.md §5](./test-scenarios.md#5-harness-adapter-conformance-suite-run-per-adapter-per-version) for each pinned harness version before that version can be enabled.

### 4.5 Workspace Materializer

Produces the `/pipeshub` filesystem contract (§6) and the native config for each harness.

- **MCP config, per harness:**
  - Claude Code: `--mcp-config`
  - Codex: `[mcp_servers]` in `config.toml`
  - OpenCode: `mcp` in `opencode.json`
  - Gemini CLI: `mcpServers` in `settings.json`
  - ACP harnesses: `mcpServers` in `session/new`
- **Instructions:** one `AGENTS.md`. Add `CLAUDE.md` containing `@AGENTS.md`, and set Gemini's `contextFileName` to `AGENTS.md`.
- **Skills:** written to `/pipeshub/skills/<name>/`, and symlinked into `.claude/skills`, `.agents/skills`, `.opencode/skills` and `.gemini/skills`.
- **Permission policy, translated into each harness's native rules as defence in depth:**
  - Claude: `--settings` with allow/deny rules
  - Codex: `approval_policy`, `sandbox_mode`, `requirements.toml`
  - OpenCode: `permission`
- **Determinism (cache-critical):** sorted tool lists, stable JSON serialization, no timestamps or random IDs in prefixes. Output is byte-identical for the same `(agent version, catalog version, principal scope)`.
- **Tool catalog:** pinned per session (`catalog_hash`). Changes take effect at the next session or after an explicit refresh, never silently mid-turn. A mid-turn change breaks prompt caches in Claude, Codex and DeepSeek, and opens a rug-pull window.

### 4.6 Tool Gateway (MCP + REST, per session, internal-only)

- **Endpoint:** `…/harness/v1/sessions/{sid}/mcp` (streamable HTTP; design it stateless, 2026-07-28 spec style) and `…/tools/*` REST for `ph`. Reachable **only** via the egress proxy, which injects the session credential.
- **Default surface.** Kept small and stable, and namespaced `pipeshub_*`:
  - `knowledge_search(query, filters, top_k, response_format)`: ACL-filtered under the session principal. Returns citations plus untrusted-content provenance, and offloads large results to `/pipeshub/knowledge/<id>.md`.
  - `knowledge_fetch(record_id | citation_id, range)`
  - `tools_search(query, detail=name|summary|full)`, `tools_describe(name)`, `tools_call(name, args)`: lazy access to every toolset the agent has.
  - Optional direct tools for the agent's pinned toolsets. Keep these to 20 or fewer. Tool-selection accuracy drops beyond 30–50 tools.
  - `ask_user(question, options)`: durable wait, rendered as the existing ask-user-question card.
  - `artifact_publish(path, title)`, `skills_search` / `skill_load` for harnesses with no native skills support.
- **Every call:**
  - authenticate the session credential, then resolve the principal (user or service account) from server state. **Never trust IDs in tool arguments.**
  - policy check (RBAC, taint, approvals)
  - execute with the user's own connector credentials from etcd
  - output DLP / redaction
  - truncate to about 25k tokens or offload to a file
  - audit record
  - OTel `execute_tool` span
- **External MCP servers are proxied.** The manifest is pinned and hashed at session start. A changed manifest is rejected or needs re-approval (rug-pull defence). Tool names are namespaced per server to prevent shadowing.
- **Idempotency key per tool call** (`session, turn, call_id`), so resumes and retries don't send two emails.

### 4.7 Model Gateway

- **Speaks every harness's native wire protocol:**
  - Anthropic Messages (Claude Code, Kimi and GLM-style clients)
  - OpenAI Responses (Codex, which supports only Responses)
  - Chat Completions (OpenCode, Qwen, Goose)
  - Gemini (Gemini CLI)
- **Routes** to the org's configured models through `ConfigurationService` and the provider registry in `config/ai_models/`.
- **Must:**
  - preserve reasoning and thinking blocks round-trip. DeepSeek and Kimi return HTTP 400 if they are stripped on tool turns.
  - pass through `cache_control` and report cache read and write tokens
  - stream
  - map model aliases (`claude-sonnet-*` → org-chosen model)
  - meter per session, user, org and agent
  - enforce budgets and rate limits
  - translate errors (429, 529, context overflow) into each harness's expected shape
- **Implementation choice** (two-way door if wrapped): embed or deploy a LiteLLM proxy behind a thin PipesHub auth and metering layer, or build translators on `agent_loop_lib/transport/*`. Note that LiteLLM is not currently a Python dependency; it appears only as a provider type.
- **The harness sees** `ANTHROPIC_BASE_URL` / `OPENAI_BASE_URL` pointing at the egress proxy, with a placeholder key. The proxy swaps in the session credential.

### 4.8 Egress Proxy

- **Topology:** the sandbox has no route except to the proxy (Unix socket or vsock, or a NetworkPolicy that allows only the proxy). DNS is resolved at the proxy. Raw UDP/53, DoH and direct IP are blocked.
- **Policy per session:**
  - default deny
  - allowlist host + method + path
  - separate setup-phase and agent-phase policies
  - updatable live (for example, after an approval grants a new domain)
- **Credential injection:**
  - the session credential, sent to the Tool and Model Gateways
  - a git host token, with operation checks (push only to the session's branch, as in Claude Code on the web)
  - package mirror credentials
- **TLS interception** uses an ephemeral CA per sandbox, only for domains that need injection or inspection.
- **Logs** destination, bytes and decision, with redaction. **Kill switch** per session, org or global.
- **Candidates:** Envoy (`credential_injector`), mitmproxy, or a small Go proxy. The existing `egress_firewall.py` covers part of this.

### 4.9 Policy Engine

- **Inputs:** org policy, agent policy, harness profile, session taint labels, tool risk class (read, write-internal, write-external, destructive, financial), principal role, and destination.
- **Taint labels.** These are set by the gateways, not by the model:
  - `private_data_read`: any knowledge or toolset read
  - `untrusted_content_read`: Slack, email, web, Confluence, third-party MCP, a cloned repo
  - `external_comm_capable`
- **Rule of Two** (Meta) and the lethal trifecta (Willison). When a session has both private and untrusted labels, external-effect actions need human approval that shows the concrete effect, or are restricted (for example, internal recipients only, or no new egress domains).
- **Decisions:** `allow | deny | ask(approver_set, ttl) | allow_with_redaction`. Each decision carries a `reason`, which the harness sees as an actionable error and the audit log records.
- **Evaluation points:**
  - Tool Gateway: every call
  - Egress Proxy: every new destination
  - Adapter: the harness's own permission requests (Bash commands, file writes outside the workspace)

### 4.10 Approval Service

- **Durable approval requests** carrying: action, effect preview (recipients, diff, domain, cost), policy reason, requester (the session and agent), approver set, and expiry.
- **Channels:** in-app card, Slack, email, and mobile push where available.
- **Decisions:** approve once, approve for this session (same tool and argument pattern), deny with a reason (returned to the harness), edit-then-approve (for example, edit the email body), or escalate.
- **Waiting is a state, not a blocked thread.** The session moves to `AWAITING_APPROVAL`. The sandbox can suspend if the wait is long. The 1-hour prompt-cache TTL is worth it for long waits.

### 4.11 Event Store, Transcript and Checkpoints

- **Canonical event envelope**, versioned:

  ```json
  {"v":1,"session_id":"…","seq":1234,"turn_id":"…","ts":"…","source":"harness|sandboxd|gateway|policy|orchestrator",
   "type":"message.delta|reasoning.delta|tool.call|tool.result|command.exec|file.diff|todo.update|approval.request|
          approval.decision|subagent.start|usage|error|state.change|artifact|terminal.output",
   "parent":{"tool_call_id":"…","subagent_id":"…"},"payload":{…}}
  ```

- **Storage:** append-only, ordered by `seq`. The session index and recent events go in Mongo (the document store). Native harness transcripts (`.jsonl`, `~/.codex/sessions`) and large payloads go to blob via `StorageServiceInterface`. **Not the graph DB**, which is the wrong shape for high-write logs.
- **Projections:**
  - AG-UI for the browser
  - the existing conversation `parts` for chat history
  - OTel spans
  - the cost ledger
  - the audit log
- **Checkpoint** = {native session ref + transcript blob, workspace snapshot or volume, git commit, progress file, last acked `seq`}. Resume rebuilds from the checkpoint. **Tool side effects must be idempotent**, because a resumed step may run again.

### 4.12 Observability, Cost and Audit

- **OTel GenAI semconv:**
  - `invoke_agent` span per session turn
  - `execute_tool` per call
  - skill-load and command-exec spans
  - `gen_ai.usage.cache_read.input_tokens`
  - tags: tenant, user, agent, harness and version
- **Also ingest the harness's own OTel.** Claude Code (`CLAUDE_CODE_ENABLE_TELEMETRY`), Codex (`[otel]`) and Gemini (`otlpEndpoint`) can all export OTel; point them at a collector reached through the egress proxy.
- **Cost ledger** (persisted): model tokens by cache class, sandbox-seconds, storage, and tool calls; rolled up per session, user, agent and org.
- **Append-only audit log:** who, on whose behalf, which session, tool, args hash, decision, approver, egress destination, and artifacts produced. Retention follows tenant policy, and the log is encrypted per tenant.
- **Operator views:** live sessions, stuck sessions (lease expired, no heartbeat), pool health, gateway error rates, and cache hit ratio.

### 4.13 UI

- **Session view:**
  - streaming message
  - reasoning (when the harness exposes it)
  - todo/plan panel
  - tool-call timeline with a subagent tree
  - terminal output (read-only by default)
  - file tree, diffs and preview
  - artifacts panel (existing)
  - approval cards
  - cost meter
  - stop, steer and fork controls
- **Agent builder:** a "Runtime" node offering native or a harness profile, plus model route, egress policy, budgets, schedule/trigger, and "run evals before publish".
- **Admin:**
  - harness catalog and versions
  - allowed models per harness
  - egress allowlists
  - MCP server allowlist with manifest pinning
  - skills registry with signing
  - budgets
  - kill switches
  - audit search
- **UI conventions:** follow `frontend/CLAUDE.md` (no Tailwind; Collections vs Knowledge Base naming).

---

## 5. Key interactions

### 5.1 Start a session and run the first turn

1. User → Node `POST /api/v1/harness/sessions {agentId, message, clientMsgId}`. Auth, scope `agent:execute`, rate limit.
2. Orchestrator:
   - resolves agent + harness profile, then principal (user, or service account if the agent is configured that way)
   - checks quotas
   - writes the session (`CREATED`)
   - takes the lease
3. Policy builds the session policy: egress allowlist, tool catalog, approval rules, budgets. Catalog pinned and `catalog_hash` recorded.
4. Provider claims a warm sandbox, or cold-creates one (`PROVISIONING`). Egress Proxy registers the session policy and credential mapping.
5. Materializer writes `/pipeshub`, the harness configs, skills, and inputs/attachments. Optional setup phase (git clone, dependency install) under the setup egress policy → `READY`.
6. `sandboxd` starts the harness through the adapter and sends the user message → `RUNNING`.
7. Harness calls the model via the Egress Proxy → Model Gateway (key injected, metered), and calls `pipeshub_knowledge_search` via the Egress Proxy → Tool Gateway (ACL-filtered under the principal; taint `private_data_read`).
8. Events stream: harness → adapter → `sandboxd` → orchestrator (persist, `seq`) → projections → Node SSE → AG-UI in the browser.
9. Turn completes → outputs collected into artifacts → `IDLE`. Idle timer → snapshot → `SUSPENDED`.

### 5.2 Consequential action with approval

The harness calls `tools_call("gmail.send", {...})`.

1. The Tool Gateway sees taint `{private, untrusted}` and an external recipient, so Policy returns `ask`.
2. The Approval request is persisted and the session goes to `AWAITING_APPROVAL`. Card plus Slack notification.
3. The tool call is held open while the user is likely present. After a timeout it returns `pending_approval(handle)`, using the MCP Tasks or input-required pattern where the client supports it, or a polling handle via `ph`.
4. User approves (optionally after editing the body). The call executes with an idempotency key, the result is returned, and the audit is written.

On deny, the harness gets an actionable error: "User denied: reason …".

### 5.3 Sandbox dies mid-turn

1. Heartbeat is lost, or the provider reports an exit.
2. Orchestrator marks the turn `interrupted` and restores from the last checkpoint (snapshot, or a fresh sandbox with volume and git state). The native session is resumed from the transcript blob.
3. Orchestrator injects a resume note ("previous turn was interrupted at step X; tool calls already completed: …") and continues if policy allows auto-resume; otherwise it asks the user.
4. Completed tool calls are not re-executed (idempotency keys).

### 5.4 Orchestrator replica dies

1. Another replica's lease acquisition succeeds.
2. It re-attaches to `sandboxd` and requests replay from `ack_seq`. The harness kept running the whole time.
3. Clients reconnect to SSE with `Last-Event-ID` (`seq`) and get no gaps and no duplicates.

### 5.5 Scheduled / triggered background run

1. A cron or MessagingFactory event (for example, a new Jira issue labelled `triage`) fires a trigger.
2. The session is created as the agent's service account or the trigger owner, as configured.
3. It runs headless with `ask_user` disabled or routed to the owner.
4. On completion: artifacts, a summary notification, and optionally a posted comment. This is subject to policy; background runs default to stricter approval rules.

### 5.6 Harness as a worker inside PipesHub deep mode

1. The native orchestrator calls `spawn_agent(role="coder")`, which maps to a harness-backed agent.
2. `HarnessAgentRunner` creates a child session (inheriting principal, budget slice and taint) and returns a condensed result (1–2k tokens) plus artifact references. The full transcript stays linked.

### 5.7 Cancel / steer / fork

- **Interrupt** maps to the adapter's interrupt (Agent SDK `interrupt()`, Codex `turn/interrupt`, ACP `session/cancel`). Escalation: SIGINT → SIGTERM → destroy.
- **Steer** sends a message mid-turn where supported (Codex `turn/steer`, Claude stream-json input). Otherwise it is queued for the next turn.
- **Fork** copies the snapshot and transcript into a new session id (Claude `--fork-session`, Codex `thread/fork`). Budgets and approvals are not inherited unless policy says so.

---

## 6. The `/pipeshub` filesystem contract (versioned: `/pipeshub/VERSION`)

```text
/pipeshub/
  VERSION                     # contract version, e.g. "1"
  AGENTS.md                   # ≤ ~100 lines: what PipesHub provides, how to search, where things are, rules
  session.json                # non-secret: session id, agent, principal display name, capabilities, limits
  tools/
    INDEX.md                  # categories → toolsets, one line each; how to use `ph tools`
    <toolset>/README.md       # purpose, auth status (connected / needs auth), risk class per tool
    <toolset>/<tool>.schema.json   # JSON Schema 2020-12 input/output + 1–3 examples
  sdk/python/pipeshub_tools/  # generated typed stubs → call Tool Gateway (code-mode / programmatic calling)
  sdk/ts/                     # same for TS
  skills/<name>/SKILL.md      # agentskills.io format + resources (symlinked into harness skill dirs)
  knowledge/                  # offloaded search results / fetched records (read-only to agent)
  inputs/                     # user attachments staged in
  outputs/                    # anything written here becomes an artifact
  memory/                     # optional, scoped memory (provenance-tracked)
  workspace/                  # the agent's working dir (git repo for coding tasks)
  bin/ph                      # CLI: search, fetch, tools {list,search,describe,call}, ask, publish, skill
```

Why both a CLI and MCP:

- **Every harness can run a shell command.** `ph` works with harnesses that lack MCP, or support it poorly (Aider, terminal adapters).
- **The filesystem gives progressive disclosure for free.** Anthropic measured about 150k → 2k tokens with filesystem/code discovery.
- **The generated SDK enables programmatic tool calling.** Loops and filtering happen in code, so large intermediate data never enters the context.
- **MCP gives structured calls, approvals and native UI rendering** in harnesses that support it.

Paths in this contract become de facto API: skills, prompts and user scripts will hard-code them. Treat the layout as a versioned public contract (see one-way doors).

---

## 7. Feature breakdown and phasing

| # | Feature | MVP | v1 | v2 |
| --- | --- | :-: | :-: | :-: |
| F1 | Harness profiles (admin catalog, pinned versions, defaults) | ● | | |
| F2 | Session lifecycle (create, message, interrupt, terminate, list, history) | ● | | |
| F3 | Claude Code adapter | ● | | |
| F4 | Second adapter (Codex app-server **or** OpenCode) to prove the abstraction | ● | | |
| F5 | Generic ACP adapter (Gemini, Goose, Qwen, Cursor, Kilo) | | ● | |
| F6 | Terminal fallback adapter (Aider etc.) | | | ● |
| F7 | Docker + gVisor provider (Compose) | ● | | |
| F8 | K8s agent-sandbox provider (Helm); warm pools | | ● | |
| F9 | SaaS providers (E2B, Daytona, Modal, …) | | | ● |
| F10 | Workspace materializer: AGENTS.md, tool schemas, skills, MCP configs | ● | | |
| F11 | Tool Gateway: knowledge search/fetch, tools search/describe/call, ask_user | ● | | |
| F12 | External MCP proxying with manifest pinning | | ● | |
| F13 | Model Gateway: Anthropic + Responses + Chat wire, metering, reasoning passthrough | ● | | |
| F14 | Egress proxy: default deny, allowlist, credential injection | ● | | |
| F15 | Git workflows: clone in setup, branch-scoped push, PR creation | | ● | |
| F16 | Policy engine: RBAC + risk classes | ● | | |
| F17 | Taint tracking + Rule-of-Two approvals | | ● | |
| F18 | Approval service: in-app | ● | | |
| F19 | Approval service: Slack / email / edit-then-approve | | ● | |
| F20 | Canonical event store + AG-UI projection + SSE resume | ● | | |
| F21 | UI: session view (messages, tools, todo, terminal, diffs, artifacts) | ● | | |
| F22 | Suspend / resume / snapshot; crash recovery | | ● | |
| F23 | Fork session | | | ● |
| F24 | Schedules and event triggers; background sessions; notifications | | ● | |
| F25 | `HarnessAgentRunner` (harness as worker in deep mode) | | ● | |
| F26 | Cost ledger, budgets, quotas | ● (caps) | ● (ledger) | |
| F27 | Audit log + admin search; kill switches | ● (log) | ● (search) | |
| F28 | OTel tracing incl. harness telemetry ingestion | | ● | |
| F29 | Memory (scoped, provenance) | | | ● |
| F30 | Generated SDK stubs for programmatic tool calling | | ● | |
| F31 | Eval pipeline (Harbor/Inspect; harness × model × exposure matrix) in CI | ● (smoke) | ● | |
| F32 | Public API + OpenAPI + MCP/A2A exposure of harness agents | | ● | ● (A2A) |
| F33 | MCP Apps / rich tool UI; preview ports | | | ● |
| F34 | Data residency placement; per-tenant keys | | ● | |

### Phase exit criteria

- **Phase 0 (spikes, about 2–3 weeks):**
  - Run Claude Code, Codex and OpenCode headless in Docker+gVisor against a stub Tool Gateway and Model Gateway.
  - Measure gVisor I/O cost (`git clone`, `npm ci`, `pytest`) against runc and Kata.
  - Pick the control channel.
  - Write ADRs for every one-way door.
  - **Exit:** conformance-suite skeleton green on 2 harnesses.
- **MVP:**
  - P0 rows of test-scenarios.md pass on Claude Code + one other harness, Compose install, Neo4j + Qdrant.
  - Zero secrets found in sandbox by the secret-scan test.
  - Injection suite baseline recorded.
- **v1:**
  - P0 + P1 pass across Compose and Helm, on 4+ harnesses.
  - Chaos suite passes.
  - pass^4 ≥ target on the internal eval set.
  - SLOs in §8 met at target concurrency.
- **v2:** remaining features, SaaS providers, A2A.

---

## 8. Performance and latency budget (proposed targets — validate in Phase 0)

| Metric | Target |
| --- | --- |
| Session ready, warm pool (claim + materialize + harness start) | p50 ≤ 2 s, p95 ≤ 5 s |
| Session ready, cold (image pre-pulled) | p95 ≤ 30 s |
| Resume from suspended snapshot | p95 ≤ 10 s |
| First event after user message (excl. model TTFT) | p95 ≤ 1.5 s |
| Tool Gateway overhead (auth + policy + audit, excl. tool) | p50 ≤ 30 ms, p95 ≤ 100 ms |
| `knowledge_search` end-to-end | p95 ≤ 2.5 s |
| Model Gateway added latency to TTFT | p95 ≤ 50 ms |
| Event lag sandbox → browser | p95 ≤ 300 ms |
| Interrupt → harness stopped | ≤ 2 s; hard kill ≤ 10 s |
| Prompt-cache read ratio on multi-turn sessions | ≥ 70% of input tokens |
| Lost / duplicated events across orchestrator failover | 0 |
| Concurrent active sessions per Compose host (8 vCPU / 32 GB) | publish measured number; no OOM of control plane |

**Levers:**
- warm pools
- pre-pulled pinned images
- snapshot restore
- deterministic tool and prompt prefixes (cache)
- result offload to files
- concise `response_format` by default
- parallel read-only tool calls
- per-tenant, ACL-scope-keyed caching of idempotent reads
- streaming everywhere
- keep the Tool Gateway on the same network segment as the proxy

---

## 9. Security architecture summary

Threat model and control mapping. The full test list is in [test-scenarios.md §3](./test-scenarios.md#3-security).

| Threat | Primary controls |
| --- | --- |
| Sandbox escape / host compromise | microVM or gVisor boundary, non-root, cap-drop ALL, read-only rootfs, seccomp, no Docker socket (docker-proxy only on control side), pids/mem/disk limits, pinned images |
| Credential theft | no secrets in sandbox; proxy-injected session credential bound to sandbox identity; short TTL; revocation on terminate; secret scanning of sandbox FS and env in tests |
| Data exfiltration (lethal trifecta) | default-deny egress, DNS at proxy, taint-gated external actions, URL and markdown-image sanitization in UI output, DLP on outbound action payloads |
| Indirect prompt injection (Slack/Confluence/web/repo content) | untrusted-content labelling and provenance on tool results, Rule-of-Two approvals, no auto-render of remote images, quarantined summarization tools for bulk untrusted data |
| Confused deputy / ACL bypass | principal derived from session credential server-side; retrieval runs as principal; service-account agents explicit and visible; no ID args trusted |
| Malicious MCP server / tool poisoning / rug pull / shadowing | admin allowlist, manifest pin + hash + drift rejection, namespacing, description scanning, no token passthrough |
| Malicious skill / plugin / repo config | signed internal skills registry, review as code, `--bare`-style explicit config, repo `.mcp.json`/hooks ignored unless allowlisted |
| Cross-tenant leakage | one sandbox per session per user, never reused; tenant-scoped storage keys; cache keys include org + ACL scope; per-tenant encryption |
| Cost / DoS abuse | budgets in gateways, per-user/org concurrency caps, rate limits, wall-clock caps, fork-bomb limits |
| Log / transcript leakage | redaction at proxy and sinks, transcripts inherit source ACL sensitivity, retention policies, opt-in content capture in traces |
| Harness CVEs (e.g. sandbox-bypass bugs in harnesses) | pinned versions, upgrade pipeline gated on conformance + security suites, outer boundary independent of harness |

---

## 10. Open questions (resolve during Phase 0)

1. Is gVisor's file-I/O overhead acceptable for coding workloads on customer hardware, or do we default to Kata where KVM exists?
2. **Model Gateway build vs adopt.** LiteLLM proxy (operational weight, licence of enterprise features), or extend `agent_loop_lib/transport/*` into a server?
3. **ACP v2 timing.** Build the generic adapter on v1 with a v2 shim, or wait?
4. **Session principal for triggers.** Trigger owner, agent service account, or the user who owns the triggering record?
5. **Licensing and ToS per harness** for running inside customer infra with org keys: Claude Code and Codex terms for automated use, Cursor and Amp hosted-model-only constraints.
6. **Windows/macOS harness-native sandboxes** are irrelevant inside Linux sandboxes. Confirm each harness runs cleanly with its inner sandbox disabled, or with Landlock/bwrap unavailable under gVisor.
