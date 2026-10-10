# Agent Harness Runtime — Readiness Checklist

This checklist is how we track whether the harness runtime is complete, robust, production-ready and highly capable. Each item can be verified. Where a test covers an item, its ID points into [test-scenarios.md](./test-scenarios.md). Design terms come from [README.md](./README.md), and the decisions referred to here are in [one-way-doors.md](./one-way-doors.md).

**Priority tags**

| Tag | Meaning |
| --- | --- |
| **P0** | Required for MVP |
| **P1** | Required for v1 / general availability |
| **P2** | Hardening, and v2 work |

**Scope tags** (used in §4)

| Tag | Scope |
| --- | --- |
| **G** | Global (operator / deployment) |
| **O** | Org (admin) |
| **A** | Agent (builder) |
| **H** | Harness profile |
| **S** | Session |
| **U** | User preference |

**How to use it.**
- Copy the checklist into the tracking epic.
- An item counts as done only when its linked tests pass in CI on the release matrix.
- Any item marked "decision" needs a merged ADR before work on it starts.

---

## 1. Foundational decisions (gate everything else)

- [ ] **P0** Merge an ADR for each one-way door: OWD-1 through OWD-13.
- [ ] **P0** Choose the name of the subsystem. Use "Harness Runtime" / `app.harness_main`; do not reuse `agent_loop_lib.control_plane.ControlPlane`.
- [ ] **P0** Run the Phase 0 spike: Claude Code, Codex and OpenCode each run headless in Docker + gVisor against stub gateways.
- [ ] **P0** Benchmark sandbox runtimes. Compare gVisor, runc and Kata on `git clone`, `npm ci` and `pytest`, and record the default runtime chosen for each deployment target (PF-09).
- [ ] **P0** Choose the control channel: provider attach/exec stdio, or a WebSocket initiated by the daemon through the proxy.
- [ ] **P0** Decide whether to build or adopt the Model Gateway (LiteLLM proxy vs `agent_loop_lib/transport/*`). Either way it must sit behind the OWD-8 contract.
- [ ] **P0** Decide the durable-execution approach: a home-grown state machine (Mongo + KV leases + MessagingFactory) or Temporal/DBOS, hidden behind the orchestrator interface.
- [ ] **P1** Complete a license and terms-of-service review for each harness: automated use, use inside customer infrastructure, and use with org-owned keys.

---

## 2. Control plane

### 2.1 Session orchestrator

- [ ] **P0** Sessions are durable resources, independent of any HTTP request (OWD-5).
- [ ] **P0** Implement the full state machine. Every illegal transition is rejected, checked with a table-driven test (SL-05).
- [ ] **P0** Each session has exactly one owner, enforced by a KV lease. Another replica takes over within the lease TTL, and there is no split-brain (SL-15, CH-02).
- [ ] **P0** Commands are idempotent: create, send_message (keyed by client message id), interrupt, approve and terminate (SL-04, CH-17).
- [ ] **P0** Interrupt escalates SIGINT → SIGTERM → destroy, with a 2 s stop and a 10 s kill (CF-09).
- [ ] **P0** An orphan sweeper destroys sandboxes that have no live session (SL-16).
- [ ] **P0** Every session pins `harness@version`, `image_digest`, `catalog_hash`, `policy_version` and the `/pipeshub` contract version (SL-14).
- [ ] **P1** Steer works mid-turn where the harness supports it, and the message is queued otherwise. The UI says which happened (J-09, CF-10).
- [ ] **P1** Idle → suspend → resume works transparently (SL-08, SL-09).
- [ ] **P1** A checkpoint consists of: native session ref, transcript blob, workspace snapshot or volume, git commit and last acked `seq`.
- [ ] **P1** Crash recovery (CH-01):
  - restore from the checkpoint
  - add a resume note to the context
  - never re-execute tool calls that already completed
- [ ] **P1** A maximum session lifetime is enforced with a clean stop and an option to resume (SL-10).
- [ ] **P2** Fork: snapshot + transcript → a new session. Budgets and approvals are not inherited by default (J-10).

### 2.2 Sandbox provider layer

- [ ] **P0** Define the `ISandboxProvider` interface with `SandboxProviderFactory`: create, attach, exec, put/get files, snapshot, restore, suspend/resume, destroy, health and capabilities.
- [ ] **P0** Build the Docker + gVisor provider for Compose, reusing `app/sandbox/docker_proxy.py` policy and `egress_firewall.py`.
- [ ] **P0** Plain runc is allowed only behind an explicit dev/insecure flag, and the UI shows a warning while it is on (OP-01).
- [ ] **P0** Each sandbox serves one session for one principal and is never reused (SEC-ISO-05).
- [ ] **P0** Resource limits cover CPU, memory, pids, ephemeral storage and disk quota (SEC-ISO-06).
- [ ] **P0** A cluster-wide governor (KV-backed) replaces the per-process `SandboxResourceGovernor` caps, with limits per org, per user and globally.
- [ ] **P0** Images are pinned by digest. Harness binaries are baked in at a pinned version and never fetched at run time.
- [ ] **P1** Build the Kubernetes provider on `kubernetes-sigs/agent-sandbox` with a configurable `RuntimeClass` (gVisor or Kata) (OP-03).
- [ ] **P1** Warm pools are keyed by (template, harness version) and claimed with per-tenant admission control (PF-01, SEC-DOS-05).
- [ ] **P1** Snapshot and restore are scoped to one principal, and restoring into a different principal is rejected (SEC-ISO-08).
- [ ] **P2** Build SaaS providers: E2B (also self-hostable), Daytona, Modal and Vercel, each with a capability matrix.
- [ ] **P2** Support GPU templates on providers that have them.

### 2.3 `sandboxd` (supervisor inside the sandbox)

- [ ] **P0** Runs unprivileged and holds no secrets.
- [ ] **P0** Launches and supervises the harness, and turns a crash into a typed error (CF-16).
- [ ] **P0** Converts native harness events into the canonical schema.
- [ ] **P0** Keeps a local ring buffer with `seq` and supports replay from `ack_seq` (CH-02, CH-12).
- [ ] **P0** Sends heartbeats and health reports.
- [ ] **P0** Rate-limits events so an event flood cannot hurt the control plane (SEC-DOS-02).
- [ ] **P1** Runs a PTY for a read-only terminal view, bounded and chunked (EV-09).
- [ ] **P1** Watches the file system and emits `file.diff` events (EV-08).
- [ ] **P1** Collects outputs from `/pipeshub/outputs` without following symlinks (SEC-FS-01).

### 2.4 Harness adapters

- [ ] **P0** Define the adapter contract: `capabilities`, `materialize_config`, `start`, `send`, `steer`, `interrupt`, `resume`, `fork`, the permission bridge, `parse` and `usage` (OWD-11).
- [ ] **P0** Claude Code adapter:
  - runs through the Agent SDK, or `-p --bare` with stream-json input and output
  - bridges permissions through `canUseTool` or the prompt tool
- [ ] **P0** Build a second adapter to prove the abstraction: Codex `app-server` or OpenCode `serve`.
- [ ] **P0** Every adapter passes the conformance suite CF-01 to CF-20, and must pass it before a harness version can be enabled.
- [ ] **P0** Native transcripts are stored verbatim so they can be re-parsed later (UP-07).
- [ ] **P0** Unknown native events are passed through as `native.unknown` rather than crashing (EV-13).
- [ ] **P1** A generic ACP adapter covers Gemini CLI, Goose, Qwen Code, Cursor CLI and Kilo, with ACP v1 support and a v2 shim.
- [ ] **P1** The PipesHub native `agent_loop_lib` is exposed as a harness kind behind the same session resource (OWD-5).
- [ ] **P2** DeepSeek Harness (`dsh`) adapter, once its developer preview stabilizes.
- [ ] **P2** Terminal fallback adapter for Aider and other TUI-only tools, marked "limited" in the UI.

### 2.5 Event store and projections

- [ ] **P0** Use a versioned envelope `{v, session_id, seq, turn_id, ts, source, type, parent, payload}`, with a strictly increasing `seq` (EV-01).
- [ ] **P0** Storage (OWD-4):
  - the session index and events live in Mongo
  - large payloads and native transcripts go to blob storage via `StorageServiceInterface`
  - the graph holds only permission and lineage edges
- [ ] **P0** SSE resume works with `Last-Event-ID` and produces no gaps or duplicates (EV-02).
- [ ] **P0** AG-UI projection:
  - the existing `agui-event-handler.ts` keeps working
  - new CUSTOM events cover approval, terminal, diff and todo
  - older clients ignore those events safely (EV-11)
- [ ] **P0** Every `tool.call` gets exactly one result, error or cancellation (EV-06).
- [ ] **P1** Project sessions into conversation `parts` so chat history renders harness sessions (EV-12).
- [ ] **P1** History is paginated, and the UI virtualizes lists of 10k+ events (EV-15).
- [ ] **P1** Readers handle every historical `v` (UP-03).

### 2.6 Scheduling, triggers and background runs

- [ ] **P1** Cron schedules fire exactly once per firing across replicas (TR-01).
- [ ] **P1** A missed-firing catch-up policy (skip, run once, or run all) is configurable (TR-02).
- [ ] **P1** MessagingFactory event triggers are idempotent on redelivery, and the event context is staged in `/pipeshub/inputs/trigger.json` (TR-03).
- [ ] **P1** Webhook triggers verify signatures, cap payload size and are rate-limited (TR-04).
- [ ] **P1** The trigger principal is explicit, and audit records the trigger as initiator (TR-05).
- [ ] **P1** An overlap policy (skip, queue or allow) applies when a trigger fires while a previous run is active (TR-06).
- [ ] **P1** Completion notifications go to in-app, email and Slack (TR-07).
- [ ] **P1** Background runs never auto-approve, and approvals route to the owner (AP-14).
- [ ] **P2** Slack-mention trigger with replies in the thread (J-23).

### 2.7 Composition

- [ ] **P1** `HarnessAgentRunner` implements `AgentRunner`, so a harness session can act as a `spawn_agent` / `AgentTool` worker (CM-02).
- [ ] **P1** A child session:
  - inherits the principal and can only narrow its scope
  - receives a slice of the parent's budget
  - passes its taint up to the parent (CM-03, CM-04)
- [ ] **P1** Cancelling the parent cascades to all children within 5 s (CM-05).
- [ ] **P1** Depth and fan-out limits are enforced (CM-06).
- [ ] **P1** Harness-native subagents render as a nested tree (CM-01, EV-07).
- [ ] **P2** Parallel workers on the same repository use separate worktrees or branches (CM-07).

---

## 3. Capabilities exposed to the harness

### 3.1 Workspace materialization and the `/pipeshub` contract

- [ ] **P0** The contract v1 layout is present, including a `VERSION` file (WS-01).
- [ ] **P0** `AGENTS.md`:
  - 100 lines or fewer
  - human-curated template
  - no secrets, timestamps or random IDs (WS-04)
- [ ] **P0** `CLAUDE.md` contains `@AGENTS.md`, and Gemini's `contextFileName` is set to `AGENTS.md`.
- [ ] **P0** Output is byte-deterministic for the same (agent version, catalog, principal scope) (WS-02, WS-03).
- [ ] **P0** Native configs are injected for each harness (WS-05):
  - Claude: `--mcp-config` and `--settings`
  - Codex: `config.toml`
  - OpenCode: `opencode.json`
  - Gemini: `settings.json`
  - ACP: `session/new`
- [ ] **P0** Configuration that a repository provides is ignored by default: `.mcp.json`, hooks, `.codex/config.toml`, `opencode.json` (WS-07).
- [ ] **P0** Read-only mounts cover `tools/`, `skills/` and `AGENTS.md`; `workspace/` and `outputs/` are writable (WS-13).
- [ ] **P0** Inputs are staged with sanitized names and size limits (WS-10, SEC-PI-07).
- [ ] **P0** Files in outputs become artifacts with lineage (WS-11, AR-01, AR-02).
- [ ] **P1** Repository `AGENTS.md` / `CLAUDE.md` are merged as content with a lower trust label, never treated as config (WS-08).
- [ ] **P1** A contract-version mismatch is refused with a clear error (WS-12).

### 3.2 `ph` CLI and generated SDK

- [ ] **P0** Commands: `ph search`, `ph fetch`, `ph tools {list,search,describe,call}`, `ph ask`, `ph publish`, `ph skill`, `ph --version` (WS-15).
- [ ] **P0** A `--json` mode with stable output, non-zero exit codes and actionable error messages.
- [ ] **P0** No token inside the sandbox: the egress proxy injects the credential.
- [ ] **P1** Typed Python and TypeScript stubs are generated under `/pipeshub/sdk/` for programmatic tool calling (WS-14).
- [ ] **P1** Stubs and `ph tools call` return identical results.

### 3.3 Knowledge tools

- [ ] **P0** `knowledge_search` and `knowledge_fetch` are separated from `ChatState` and run as a stateless service API.
- [ ] **P0** Searches are filtered by ACL under the principal, using `get_accessible_virtual_record_ids` (KN-01).
- [ ] **P0** A service-account principal searches with that account's access only (KN-03).
- [ ] **P0** Filters for connector, date, collection/KB and record type are applied server-side (KN-04).
- [ ] **P0** Results carry stable citation IDs and deep links built with `app/utils/text_fragments/`. Citations in final answers resolve (KN-05).
- [ ] **P0** Output is concise by default, with a `response_format=detailed` option. Results over the cap are offloaded to `/pipeshub/knowledge/` (KN-06).
- [ ] **P0** Content provenance and an `untrusted` label are attached, and the session taint is set (KN-08).
- [ ] **P1** No results, no access and index-not-ready are reported as distinct messages (KN-09).
- [ ] **P1** Results are the same across every graph and vector backend (KN-10).
- [ ] **P1** Knowledge-graph and entity tools respect ACLs (KN-11).
- [ ] **P1** Retrieval quality on the golden set is at least as good as native-agent retrieval (KN-12).
- [ ] **P2** Quarantined map-reduce summarization tool for bulk untrusted content.

### 3.4 Toolsets through the Tool Gateway

- [ ] **P0** The default surface is small and stable: `knowledge_*`, `tools_search`, `tools_describe`, `tools_call`, `ask_user`, `artifact_publish`.
- [ ] **P0** Arguments are validated against JSON Schema 2020-12, and validation errors name the field and the fix (TG-04, TG-05).
- [ ] **P0** Tools execute with the user's own connector credentials from etcd, which are never returned to the sandbox (TG-06).
- [ ] **P0** OAuth tokens refresh transparently. If refresh fails, the user gets a re-authenticate link (TG-07).
- [ ] **P0** Per-tool timeouts and cancellation propagation (TG-09).
- [ ] **P0** Connector 429s are retried with backoff within budget (TG-08).
- [ ] **P0** Each call is idempotent on `(session, turn, call_id)` (TG-10).
- [ ] **P0** Output is capped at about 25k tokens or offloaded (TG-11).
- [ ] **P0** Masked tools: a tool denied by policy stays listed and returns a reason (TG-13).
- [ ] **P0** Toolsets with missing credentials show `needs_authentication` instead of crashing (WS-09).
- [ ] **P0** MCP protocol compliance for the supported spec revisions (TG-18).
- [ ] **P1** Per-session concurrency cap, and parallel calls are isolated from each other (TG-15).
- [ ] **P1** Smoke test for every toolset category through the gateway (TG-16).
- [ ] **P1** DLP and redaction on tool output (TG-12).
- [ ] **P2** Behavior matches the native lazy meta-tools (TG-17).

### 3.5 External MCP servers

- [ ] **P1** Only admin-allowlisted servers are allowed, and all traffic is proxied through the Tool Gateway (MX-01, MX-02).
- [ ] **P1** Tool names are namespaced per server instance (MX-05).
- [ ] **P1** The manifest is pinned and hashed at session start. Drift is rejected or requires re-approval (MX-03).
- [ ] **P1** Tool descriptions are scanned for injection when a server is attached (MX-04).
- [ ] **P1** Per-user OAuth stays on the server side, with no token passthrough (MX-06).
- [ ] **P1** Custom stdio servers need `MCP_ALLOW_CUSTOM_STDIO`, run outside the harness sandbox, and receive only an allowlisted environment (MX-07).
- [ ] **P1** Timeouts and a circuit breaker for each server (MX-08).
- [ ] **P2** Private MCP registry that pulls selectively from the official registry.

### 3.6 Skills

- [ ] **P0** Agent-assigned skills are materialized from `GraphSkillStore` using `SkillBundle.as_sandbox_files()` (SK-01).
- [ ] **P0** Skills are symlinked into `.claude/skills`, `.agents/skills`, `.opencode/skills` and `.gemini/skills` (WS-06).
- [ ] **P0** Built-in packs (pdf, docx, xlsx, pptx, data-analysis) work, with their dependencies in the image (SK-03).
- [ ] **P1** The skill version is pinned for each session (SK-04).
- [ ] **P1** Harnesses without native skills get `skills_search` / `skill_load` or `ph skill` (SK-05).
- [ ] **P1** Only signed or approved skills can be mounted, and imports go to review (SK-06, SEC-SC-04).
- [ ] **P2** Skills learned from harness sessions go to candidates and are never auto-published (SK-07).

### 3.7 Model access

- [ ] **P0** Supported wire formats: Anthropic Messages and OpenAI Responses (MG-01, MG-02).
- [ ] **P0** Reasoning and thinking blocks are preserved round-trip, as DeepSeek, Kimi and GLM require (MG-05).
- [ ] **P0** Model aliases map to org-configured models. Disallowed models are rejected (MG-06, MG-07).
- [ ] **P0** Metering is exact, including cache read and write tokens (MG-11, EV-10).
- [ ] **P0** Bring-your-own (BYO) keys are stored encrypted and never visible in the sandbox (MG-12).
- [ ] **P0** Budget enforcement mid-stream returns a typed error (MG-08).
- [ ] **P0** Provider 429/529/5xx errors are translated into the error shape each harness expects (MG-09).
- [ ] **P1** Chat Completions wire format for OpenCode, Qwen and Goose (MG-03).
- [ ] **P1** A context-overflow error is translated so the harness triggers its own compaction (MG-10).
- [ ] **P1** Routing respects data residency (MG-15).
- [ ] **P2** Gemini wire format (MG-04). Image and document passthrough with a capability check (MG-14).

### 3.8 Human in the loop

- [ ] **P0** `ask_user` is a durable wait: the session moves to `AWAITING_INPUT` and survives a suspend (AP-13, J-25).
- [ ] **P0** Approval cards show the concrete effect (recipients, domains, diff, cost) rather than raw JSON alone (UX-03).
- [ ] **P0** Approve once, deny with a reason that the harness receives, and expiry defaults to deny (AP-03, AP-05, AP-07).
- [ ] **P0** Harness-native permission requests are bridged through `canUseTool`, `requestApproval` or ACP `request_permission` (AP-11).
- [ ] **P0** The sandbox can never forge an approval (AP-15).
- [ ] **P1** Approve for the session (pattern-scoped), and edit-then-approve with both versions audited (AP-04, AP-06).
- [ ] **P1** Approvers other than the principal are routed correctly, and multiple concurrent approvals are supported (AP-08, AP-09).
- [ ] **P1** Slack and email approval links are signed, single-use and expiring (AP-10).

### 3.9 Outputs and git

- [ ] **P0** Artifacts are versioned, carry lineage and are downloaded through signed URLs (AR-01..04).
- [ ] **P1** Clone happens in the setup phase with a scoped token that is removed before the agent phase (GT-01).
- [ ] **P1** A git proxy allows pushes only to the session branch. Force-push, tags and branch deletion are denied (GT-02, GT-03).
- [ ] **P1** PRs are created by the control plane after approval, with citations and a link to the session (GT-04).
- [ ] **P1** Hooks and submodules from a malicious repository are neutralized (GT-05).

### 3.10 Memory (v2)

- [ ] **P2** Scoped memory (user, agent, org) is mounted at `/pipeshub/memory` (ME-01).
- [ ] **P2** Each entry records its source session, author and connector (ME-02).
- [ ] **P2** Writes from untrusted sessions stay in user scope; org-scope writes require review (ME-04).
- [ ] **P2** Path-traversal protection, size caps, TTLs, and a user can view and delete their memory (ME-03, ME-05).

---

## 4. Configurability

### 4.1 Configuration model

- [ ] **P0** Precedence is **Global → Org → Agent → Harness profile → Session → User preference**. Lower scopes can only narrow security settings, never widen them.
- [ ] **P0** Any setting can be **locked** at a scope (an admin lock), and a lower scope cannot override a locked value.
- [ ] **P0** All reads go through `ConfigurationService`, never `KeyValueStore` directly (AGENTS.md rule).
- [ ] **P0** The **effective config** is resolved once at session start, hashed into `policy_version`, and shown in the session details.
- [ ] **P0** Config changes are validated against a schema. Invalid values are rejected with field-level errors.
- [ ] **P1** Every config change is audited with old value, new value, actor and time.
- [ ] **P1** A config change affects new sessions only. A running session must refresh explicitly or be killed.
- [ ] **P1** Export and import of agent + harness profile config, with secrets excluded, for promotion between environments.
- [ ] **P2** A dry-run "what would this session get" preview in the agent builder.

### 4.2 Configuration surface

Scopes where a setting can be set are marked. "Lock" means an org admin can lock it.

| Setting | G | O | A | H | S | U | Lock | Default |
|---|:-:|:-:|:-:|:-:|:-:|:-:|:-:|---|
| Feature enabled | ● | ● | | | | | ● | off |
| Allowed harnesses + versions | ● | ● | ● | | | | ● | none |
| Harness default version / pin | | ● | ● | ● | | | | latest approved |
| Sandbox provider + runtime class | ● | ● | | ● | | | ● | Docker+gVisor (Compose), agent-sandbox+gVisor (Helm) |
| Sandbox CPU / memory / disk / pids | ● | ● | ● | ● | | | ● | template |
| Warm-pool size per template | ● | | | ● | | | | 0 (Compose), 2 (Helm) |
| Idle timeout / max lifetime | ● | ● | ● | | ● | | ● | 15 min / 8 h |
| Snapshot TTL / retention | ● | ● | | | | | ● | 7 days |
| Allowed models per harness | | ● | ● | ● | | | ● | org default model |
| Model alias map | | ● | | ● | | | | provider defaults |
| Egress policy (setup phase) | ● | ● | ● | ● | | | ● | git host + package mirrors |
| Egress policy (agent phase) | ● | ● | ● | ● | ● (via approval) | | ● | deny all except gateways |
| Package mirrors | ● | ● | | | | | ● | none |
| Git hosts + push policy | | ● | ● | | | | ● | session branch only |
| Toolsets / MCP servers / skills / knowledge | | ● (allowlist) | ● | | | | ● | none |
| Tool disclosure (`eager` / `lazy` / `filesystem`) | | ● | ● | ● | | | | per-harness default (§5) |
| Direct-tool count cap | ● | ● | ● | | | | | 20 |
| Tool result cap / offload threshold | ● | ● | ● | | | | | ~25k tokens |
| `response_format` default | | | ● | | ● | | | concise |
| Policy: risk-class defaults (allow / ask / deny) | | ● | ● | | | | ● | read=allow, write_internal=allow, write_external=ask, destructive=ask, financial=deny |
| Rule-of-Two enforcement | | ● | ● | | | | ● | on |
| Approval TTL / approver set | | ● | ● | | | | ● | 1 h / principal |
| "Approve for session" allowed | | ● | ● | | | | ● | on |
| Budgets: tokens / cost / turns / wall-clock / sandbox-seconds | ● | ● | ● | | ● | | ● | org-defined |
| Concurrency caps (org / user) | ● | ● | | | | | ● | 10 / 2 |
| Reasoning visibility in UI | | ● | ● | | | ● | ● | on if harness exposes it |
| Terminal view (read-only / hidden) | | ● | ● | | | ● | | read-only |
| Transcript + audit retention | ● | ● | | | | | ● | 90 days / 1 year |
| Trace content capture | ● | ● | | | | | ● | off |
| Data residency region | ● | ● | | | | | ● | deployment region |
| Schedules / triggers | | ● (allow) | ● | | | | ● | off |
| Notifications channels | | ● | ● | | | ● | | in-app |
| Harness telemetry export (OTel) | ● | ● | | ● | | | ● | off |
| Auto-resume after crash | | ● | ● | | | | | on, if no pending side effects |
| Dev / insecure mode (runc, open egress) | ● | | | | | | ● | off (never in prod) |

All defaults above are proposals to validate in Phase 0.

- [ ] **P0** Every row above has a schema, a default, docs and a test proving precedence and lock behavior.
- [ ] **P1** Admin UI for org-scope rows; agent builder for agent-scope rows; a harness profile editor for harness-scope rows.

---

## 5. Lazy loading and progressive disclosure

Rule of thumb: load **lazily what the model or user pays for**, and load **eagerly what keeps prompt prefixes stable**. Never mutate the tool list during a session.

| What | Strategy | Why | Checks |
|---|---|---|---|
| Sandbox | **Lazy for knowledge-only agents**: no sandbox until a code, file or shell capability is first needed; native/in-process path otherwise. Eager (warm pool) for coding harnesses | Cost and latency for simple Q&A; harness CLIs need a sandbox from the start | Knowledge-only session never allocates a sandbox |
| Harness image | Pre-pulled on nodes; never pulled at session time | Cold start | PF-02 |
| Tool catalog (names) | **Eager, pinned** per session, deterministic order | Prompt cache and rug-pull defense | WS-02, WS-03, TG-14 |
| Tool schemas (full) | **Lazy**: `tools_search` / `tools_describe` / files under `/pipeshub/tools/` | ~85–98% token savings; accuracy drops past 30–50 tools | PF-11, EV-CAP-03 |
| Direct tools | Eager for ≤ ~20 pinned tools only (the 3–5 most-used plus those the agent builder pins) | Lowest latency for common calls | TG-03 |
| Tool schema files on disk | Materialized at session start (cheap; deterministic); generated SDK stubs built lazily on first import if large | Fast start, still discoverable | WS-01, WS-14 |
| Toolset credentials | **Lazy**: resolved at first call; listed with auth status up front | Don't fail a session over an unused toolset | WS-09, TG-07 |
| External MCP servers | Manifest fetched and pinned at start; **connection opened lazily** on first call; pooled per session | Startup latency, rug-pull defense | MX-03, MX-08 |
| Skills | Frontmatter eager (in skill index); body on use; resources on demand | ~100 tokens/skill at rest | SK-02 |
| Knowledge results | Concise inline; full content **offloaded** to `/pipeshub/knowledge/` and fetched by path or `knowledge_fetch` | Context budget | KN-06, KN-07 |
| Large tool results | Truncate or offload, return path + summary | Context budget | TG-11 |
| Inputs / attachments | Staged at start if small; large files streamed on first read | Start latency, disk | J-26 |
| Memory | Index eager, entries on demand | Context budget, poisoning surface | ME-01 |
| Event history (UI) | Paginated; virtualized list; terminal output chunked | Large sessions stay responsive | EV-15, PF-17 |
| Transcripts / snapshots | Loaded only on resume, fork or replay | Storage I/O | PF-03 |

- [ ] **P0** Every row above is implemented as stated, and each tool-disclosure mode is selectable per agent (it extends the existing `tool_disclosure` eager/lazy setting).
- [ ] **P0** Lazy loading never changes the model-visible tool list mid-session. Discovery happens through stable meta-tools or files.
- [ ] **P0** A knowledge-only agent on the native harness never allocates a sandbox.
- [ ] **P1** A/B eval of disclosure modes for each harness, with the chosen defaults recorded in the harness profile (EV-CAP-03).
- [ ] **P1** Cache-read ratio SLO (≥ 70%) is monitored, with an alarm on regression (MG-13, PF-10).
- [ ] **P1** On Claude-family models, use tool search or `defer_loading` where the harness supports it. Note that this depends on the harness, since PipesHub does not control the API call shape.

---

## 6. Security

### 6.1 Isolation

- [ ] **P0** The outer sandbox (gVisor or a microVM) is the security boundary. The harness's inner sandbox is defense in depth only (OWD-3).
- [ ] **P0** Containers run hardened:
  - non-root
  - `cap-drop ALL`
  - `no-new-privileges`
  - seccomp
  - read-only rootfs
  - (SEC-ISO-02)
- [ ] **P0** No Docker socket, no Kubernetes service account token, and cloud metadata is blocked (SEC-ISO-03).
- [ ] **P0** No east-west traffic between sandboxes (SEC-ISO-04).
- [ ] **P0** Escape and exhaustion tests pass, including with the inner sandbox disabled (SEC-ISO-01, -06, -07).

### 6.2 Egress

- [ ] **P0** Default deny. The only route out is the egress proxy, enforced at the network level rather than through environment variables (SEC-EG-12).
- [ ] **P0** DNS is resolved at the proxy. UDP/53, DoH/DoT, direct IPs, IPv6 and ICMP are blocked (SEC-EG-02..04).
- [ ] **P0** The parser-trick regression suite passes: null byte, userinfo, suffix tricks, homoglyphs, trailing dot (SEC-EG-06).
- [ ] **P0** An empty allowlist means no network (SEC-EG-10).
- [ ] **P1** Setup-phase and agent-phase policies are separate, with the switch made before the harness starts (SEC-EG-11).
- [ ] **P1** Allowed domains carry method and path restrictions. Echo domains (gist, pastebin) are kept out of the defaults (SEC-EG-07).
- [ ] **P1** Alert on high-entropy DNS labels and unusual egress volume.

### 6.3 Credentials

- [ ] **P0** No secrets in the sandbox, enforced by a canary scan across all phases (SEC-CR-01, OWD-1).
- [ ] **P0** The session credential is bound to the sandbox identity, short-lived, and revoked on terminate, suspend or kill switch (SEC-CR-02, -03).
- [ ] **P0** Snapshots are taken only after the setup-secrets wipe (SEC-CR-04).
- [ ] **P0** PATs and tokens are never logged (SEC-CR-06, SEC-LG-01).
- [ ] **P1** Rotating credentials (model keys, git, proxy CA) requires no sandbox restart (OP-08).

### 6.4 Authorization and tenancy

- [ ] **P0** The principal is derived on the server side. Org and user IDs in tool arguments are never trusted (TG-02, SEC-AZ-03).
- [ ] **P0** Users cannot reach another user's or another org's sessions, artifacts, approvals or triggers (SEC-AZ-01, -02).
- [ ] **P0** A shared agent runs with each user's own credentials and ACLs. Service-account agents are labeled as such (SEC-AZ-04).
- [ ] **P0** Cache keys include the org and the ACL scope (SEC-AZ-05).
- [ ] **P0** Every new route declares `AUTH_POLICY_ATTR` / `require_scopes`, and the route-inventory test passes (AD-07).
- [ ] **P0** IDs are unguessable, and authorization is checked on every fetch (SEC-AZ-07).
- [ ] **P1** Deactivating a user cleans up that user's sessions, triggers and tokens (SEC-AZ-08).

### 6.5 Prompt injection and the lethal trifecta

- [ ] **P0** Taint labels (`private_data_read`, `untrusted_content_read`, `external_comm_capable`) are set by the gateways, not by the model.
- [ ] **P0** The Rule of Two is enforced on external-effect actions (SEC-PI-01, -08).
- [ ] **P0** The UI never auto-fetches remote images, and URLs that carry query data are sanitized (SEC-EG-09, UX-04).
- [ ] **P0** Agent text is visually distinct from system and approval UI. Citations are resolved on the server side only (SEC-PI-04).
- [ ] **P1** The injection eval suite (AgentDojo-style, using enterprise fixtures) shows attack success = 0 on consequential actions (EV-SAF-01).
- [ ] **P1** Canary facts are planted in restricted docs and never leak (EV-SAF-03).

### 6.6 Supply chain

- [ ] **P1** Harness images ship with an SBOM and a signature that is verified at use (SEC-SC-01).
- [ ] **P0** Harness auto-update and phone-home are disabled and blocked (SEC-SC-02, CF-17).
- [ ] **P1** Packages install only from vetted mirrors, with the typosquat policy from `package_policy.py` (SEC-SC-03).
- [ ] **P1** Skills and MCP servers come from a signed or approved registry, and manifests are pinned (SEC-SC-04, -05).
- [ ] **P1** A response process for harness CVEs: a version kill switch plus a fast-track upgrade through conformance (J-19).

### 6.7 Filesystem, logs and abuse

- [ ] **P0** The outputs collector rejects symlinks, path traversal, hardlinks and device files (SEC-FS-01, -02, -05).
- [ ] **P0** Risky output types are served with a safe content-type and CSP (SEC-FS-04).
- [ ] **P0** Transcript access follows the session ACL. Admin access to transcripts is itself audited (SEC-LG-02).
- [ ] **P0** Per-user and per-org concurrency caps, size limits and event-flood throttles (SEC-DOS-01..03).
- [ ] **P1** Zip-bomb and huge-file limits (SEC-FS-03). Slowloris protection (SEC-DOS-04).

### 6.8 Security process

- [ ] **P1** Threat model is reviewed at every phase gate. A quarterly external red-team.
- [ ] **P1** The security suite plus canary scans gate every release.
- [ ] **P1** An incident runbook: kill switch, credential rotation, forensic export of session events and egress logs.

---

## 7. Performance and latency

- [ ] **P0** Session ready from a warm pool: p50 ≤ 2 s, p95 ≤ 5 s (PF-01).
- [ ] **P0** Cold session ready: p95 ≤ 30 s, with a breakdown by stage (PF-02).
- [ ] **P0** First event after a message: p95 ≤ 1.5 s, excluding model time to first token (PF-04).
- [ ] **P0** Tool Gateway overhead: p50 ≤ 30 ms and p95 ≤ 100 ms (PF-05).
- [ ] **P0** Model Gateway adds ≤ 50 ms at p95 to time to first token, with no stream buffering (PF-07).
- [ ] **P1** Resume from a snapshot: p95 ≤ 10 s (PF-03).
- [ ] **P1** Event lag from sandbox to browser: p95 ≤ 300 ms (PF-08).
- [ ] **P1** Parallel tool calls take about as long as the slowest one, not the sum (PF-18).
- [ ] **P1** Capacity for the reference Compose host is published (PF-12).
- [ ] **P1** Helm scale-out shows no hot partitions (PF-13).
- [ ] **P1** 24 h soak with no leaks (PF-14).
- [ ] **P1** Burst of 0 → 200 sessions handled (PF-15).
- [ ] **P1** UI stays responsive with 10k events or 10 MB of terminal output (PF-17).
- [ ] **P1** Idempotent connector reads are cached per tenant and ACL scope.

---

## 8. Reliability and resilience

- [ ] **P0** Tool Gateway, Model Gateway and egress proxy restarts are survivable (CH-03, -04, -05).
- [ ] **P0** Duplicate client messages result in exactly one turn (CH-17).
- [ ] **P1** Sandbox death, orchestrator death and network partition are recoverable with zero lost events (CH-01, -02, -12).
- [ ] **P1** Store outages are survivable: Mongo, KV, broker, blob (CH-06..09).
- [ ] **P1** Malformed connector or model output does not crash anything or cause loops (CH-10, -11).
- [ ] **P1** Rolling deploys with active sessions (CH-15, UP-06).
- [ ] **P1** Sandbox provider errors are retried, with fallback to another provider if configured (CH-16).
- [ ] **P1** Stall and runaway-loop detection pauses the session with an explanation (BU-06).

---

## 9. Observability, cost and audit

- [ ] **P0** Usage events per model call, reconciled with the gateway ledger (EV-10).
- [ ] **P0** Append-only audit records for every tool call, approval and egress decision (OB-03), emitted by the gateways rather than by the harness.
- [ ] **P1** OTel GenAI spans, with trace IDs propagated from Node → harness service → gateways → sandbox (OB-01):
  - `invoke_agent`
  - `execute_tool`
  - model calls, including cache-token counts
- [ ] **P1** Metrics and dashboards (OB-04):
  - active sessions
  - pool depth
  - claim latency
  - gateway errors
  - cache ratio
  - cost per minute
- [ ] **P1** Stuck-session detection with alerting (OB-05).
- [ ] **P1** A cost ledger per session, user, agent and org; budgets with 80% and 100% alerts (BU-03, BU-05).
- [ ] **P1** Sandbox-seconds metering accurate to within 1% (BU-04).
- [ ] **P1** Audit records are tamper-evident (hash chain or WORM storage), searchable and exportable (J-18).
- [ ] **P2** Ingest harness OTel (OB-02), trace replay (OB-06), and a redacted support bundle (OB-07).

---

## 10. User experience

- [ ] **P0** The session view renders every canonical event type (UX-01).
- [ ] **P0** Every typed error has a human-readable message and a next action (UX-06).
- [ ] **P0** Stop, approve and answer-question controls are always reachable while a session runs.
- [ ] **P1** Controls follow harness capabilities (steer, fork, reasoning), with a tooltip when one is unavailable (UX-02).
- [ ] **P1** A todo/plan panel, elapsed time, live cost meter and current step (UX-05).
- [ ] **P1** File tree, diffs and preview of outputs. Read-only terminal view.
- [ ] **P1** A "Runtime" node in the agent builder: harness profile, model, egress, budgets and schedule, validated before save (UX-07).
- [ ] **P1** The builder blocks invalid harness × model combinations, for example Codex with a model that only offers the Chat Completions API (§7 of test-scenarios).
- [ ] **P1** Strings go through i18n, and naming follows `frontend/CLAUDE.md` (UX-09, UX-10).
- [ ] **P2** Accessibility: keyboard approvals, screen reader support, contrast (UX-08). Mobile approvals (J-27).

---

## 11. Admin and governance

- [ ] **P0** A harness catalog: enable or disable each harness and version. Allowed models per harness are enforced by the gateway (AD-01, AD-02).
- [ ] **P0** Org feature flag. When it is off, routes return 403/404 and no background work runs (AD-08).
- [ ] **P0** RBAC for creating harness agents, approving actions and viewing other people's sessions (AD-07).
- [ ] **P1** Egress allowlist editor with validation and audit (AD-03).
- [ ] **P1** MCP allowlist with manifest diff review (AD-04). Skills signing and approval (AD-05).
- [ ] **P1** Kill switches at four levels: session, org, harness version and global egress. Each takes effect in 60 s or less (AD-06, J-19).
- [ ] **P1** Usage and cost reports per org, user and agent, exportable.
- [ ] **P1** Audit search UI (J-18).

---

## 12. API and integrations

- [ ] **P0** Routes under `/api/v1/harness/*` are proxied by Node and kept in sync with `pipeshub-openapi.yaml` (API-01, OWD-13).
- [ ] **P0** Least-privilege scopes for personal access tokens (PATs) and OAuth (API-02). Idempotency keys (API-03).
- [ ] **P1** A non-streaming variant with the same error contract (API-04). Rate limits that return `Retry-After` (API-05).
- [ ] **P1** CI integrators get parity with the UI (J-22).
- [ ] **P2** Harness agents exposed through `/mcp` `pipeshub_agents` (API-06). A2A Agent Cards (API-07).

---

## 13. Compatibility

- [ ] **P0** MVP matrix:
  - Claude Code plus one other harness
  - Anthropic and OpenAI model families
  - Compose with Docker + gVisor
  - Neo4j + Qdrant
- [ ] **P1** Run the pairwise matrix nightly and the full matrix before each release (test-scenarios §7).
- [ ] **P1** Store known incompatibilities as data in harness profiles. Examples:
  - Codex supports the Responses API only.
  - Cursor and Amp run on hosted models only.
  - DeepSeek's Anthropic-compatible endpoint has no `cache_control` and no image input.
- [ ] **P1** Open-model support:
  - Pin the vLLM version, chat template and tool-call parser for each model (Qwen3-Coder, Kimi K2, GLM).
  - Each model passes conformance.
- [ ] **P1** All graph backends (Neo4j, Arango), vector backends (Qdrant, OpenSearch, Redis), KV stores, brokers and blob stores pass the knowledge and session suites.

---

## 14. Upgrades and versioning

- [ ] **P0** Old and new harness versions run side by side. A session stays pinned to the version it started with (UP-01).
- [ ] **P1** Retiring a harness version is a defined flow (UP-02). Event schema, `/pipeshub` contract and tool schemas all evolve additively (UP-03..05).
- [ ] **P1** Each harness upgrade goes through a pipeline:
  1. Nightly conformance run against `latest`.
  2. Promotion to the catalog.
  3. Org opt-in.
  4. Default flip.
- [ ] **P2** Canonical events can be regenerated from stored native transcripts (UP-07).

---

## 15. Deployment and operations

- [ ] **P0** The `install.sh` preflight checks the sandbox runtime and gives clear remediation (OP-01, OP-02).
- [ ] **P0** The new service, its port, and the internal-only Tool Gateway are documented in `AGENTS.md` / `CLAUDE.md` (OP-09).
- [ ] **P1** Helm chart (OP-03):
  - CRDs
  - RuntimeClass
  - default-deny NetworkPolicies
  - dashboards and alerts
- [ ] **P1** Air-gapped installs (OP-04):
  - private registry
  - local models
  - mirrored skills and images
- [ ] **P1** Published sizing guide (OP-05). Backup and restore (OP-06).
- [ ] **P1** Runbooks for (OP-07):
  - a stuck session
  - an exhausted pool
  - a gateway 5xx spike
  - a harness CVE
  - credential rotation
  - a pending-approval backlog

---

## 16. Compliance and data governance

- [ ] **P1** Every stored object carries `org_id` and region. Per-tenant encryption keys for transcripts and snapshots (OWD-12).
- [ ] **P1** Retention and deletion apply across Mongo, blob, snapshots and caches, and can be verified (SEC-LG-04, SL-13).
- [ ] **P1** A GDPR/DSR path: export and delete one user's sessions, artifacts and memory.
- [ ] **P1** A vendor DPA checklist for any SaaS sandbox or model provider that is enabled.
- [ ] **P2** Map controls to SOC 2 / ISO 27001 evidence: access reviews for approvers, change management for manifests and harness versions.

---

## 17. Quality gates and evals

- [ ] **P0** CI runs: unit tests, integration with a stub model, recorded-model end-to-end journeys, the conformance suite, and the security suite with canary scans.
- [ ] **P0** Smoke test of the enterprise-task eval with recorded models (EV-CAP-01).
- [ ] **P1** A live-model nightly run of the harness × model matrix through Harbor or Inspect (EV-CAP-02).
- [ ] **P1** pass^4 targets defined and met on the internal enterprise eval set before GA.
- [ ] **P1** Chaos-in-the-loop (EV-REL-01) and long-horizon (EV-REL-02) evals.
- [ ] **P1** Injection, over-approval, ACL-fidelity and red-team exfiltration evals (EV-SAF-01..04).
- [ ] **P1** A golden-trace loop: every incident becomes a regression case before its fix ships (EV-OPS-01).
- [ ] **P1** Grader calibration against human labels, with transcripts reviewed every week.

---

## 18. Documentation

- [ ] **P0** ADRs for every one-way door.
- [ ] **P0** OpenAPI spec for all new routes.
- [ ] **P1** Admin guide: enabling harnesses, egress, budgets, kill switches, audit.
- [ ] **P1** Builder guide:
  - choosing a harness and model
  - choosing a tool-disclosure mode
  - writing skills
  - writing evals
- [ ] **P1** User guide: sessions, approvals, artifacts, git.
- [ ] **P1** `/pipeshub` contract reference and `ph` CLI reference, both versioned.
- [ ] **P1** "Adding a harness adapter" guide plus a conformance kit.
- [ ] **P1** Security whitepaper: isolation, egress, credentials, Rule of Two, audit.
- [ ] **P1** Operator guide: sizing, runbooks, upgrades.

---

## 19. Release readiness (per release)

- [ ] Every P0 item is checked (MVP), or every P0 and P1 item is checked (v1).
- [ ] Every enabled harness version passes conformance on the release matrix.
- [ ] Security suite passes. The red-team report has no open high-severity findings.
- [ ] Performance targets are met on reference hardware, and the numbers are in the release notes.
- [ ] Chaos suite is green in staging. Eval gates are met.
- [ ] Docs, OpenAPI and runbooks are published. The changelog entry includes the harness versions supported.

---

## 20. Additions from gap review

Details, rationale and acceptance criteria: [gaps-and-additions.md](./gaps-and-additions.md). Tests: GA-xx in [test-scenarios.md §13](./test-scenarios.md#13-additions-from-gap-review).

**Decisions**
- [ ] **P0** ADRs for OWD-14 (agent versioning), OWD-15 (classification), OWD-16 (workspace sharing), OWD-17 (vendor logins), OWD-18 (write identity/disclosure), OWD-19 (multi-participant sessions).

**Lifecycle**
- [ ] **P1** Immutable agent versions; sessions/triggers pin versions; rollback (G-01, GA-01).
- [ ] **P2** Staged rollout/canary of agent and harness versions (G-02).
- [ ] **P1** Shadow / dry-run mode with write tools stubbed in the gateway (G-03, GA-02).
- [ ] **P1** Plan-approval gate as a policy option (G-04, GA-03).
- [ ] **P1** Outcome verification gate (tests, citation text existence, file validity) (G-05, GA-04).
- [ ] **P2** Workspace rewind + compensating actions for external writes (G-06).
- [ ] **P1** End-of-session summary attached to notifications (G-07, GA-05).
- [ ] **P2** Session export and local hand-off without secrets (G-08).

**Interaction**
- [ ] **P1** Session participant roles; principal immutable (G-09, GA-06).
- [ ] **P2** Human takeover terminal with hand-back diff (G-10).
- [ ] **P2** Authenticated, expiring preview URLs (G-11).
- [ ] **P1** `auth_required` pause → connect → auto-retry (G-12, GA-07).
- [ ] **P1** Unified approvals/questions inbox with safe batch approve (G-13, GA-08).
- [ ] **P2** Teams / email-to-agent surfaces (G-14).
- [ ] **P1** User feedback capture feeding the eval backlog (G-15).
- [ ] **P2** Speculative pre-warm with per-user cost cap (G-16).

**Environment**
- [ ] **P1** Environment definitions (image, setup script, devcontainer subset, env vars) + post-setup snapshot (G-17, GA-09).
- [ ] **P2** Service sidecars with ephemeral credentials (G-18).
- [ ] **P1** Read-only org caches populated by trusted jobs (G-19, GA-10, SEC-SC-06).
- [ ] **P1** Signed custom images + corporate CA injection (G-20, GA-11).
- [ ] **P1** Upstream proxy chaining; private CIDRs default-deny; PipesHub internals always denied (G-21, G-38, SEC-EG-13, SEC-ISO-09).
- [ ] **P2** Persistent project workspaces per OWD-16 (G-22).
- [ ] **P1** Safe web-research tier via control-plane fetch/search tool (G-23, GA-12).
- [ ] **P0** Built-in harness tool policy (allow/deny/replace) per profile, verified in conformance (G-24, GA-13).
- [ ] **P1** Managed read-only policy hook in each harness that supports hooks (G-25, GA-14).
- [ ] **P2** Harness extension assets (commands, subagents, plugins) as signed, versioned agent assets (G-26).
- [ ] **P1** Role → model mapping (primary/fast/subagent/compaction) enforced by gateway (G-27, GA-15).
- [ ] **P2** Headless browser under egress policy (G-28).

**Data and knowledge**
- [ ] **P1** Classification in taint; classification → model/egress/retention/approver matrix (G-29, GA-16).
- [ ] **P1** Model registry data-handling metadata (ZDR, region, training opt-out) used by policy (G-30).
- [ ] **P1** Shared per-(org, connector) rate budget between Tool Gateway and connector sync (G-31, GA-17).
- [ ] **P2** Read-after-write overlay for agent-created records (G-32, GA-18).
- [ ] **P2** Index agent artifacts as records with most-restrictive-source ACL (G-33).
- [ ] **P2** Search past sessions (ACL'd) (G-34).
- [ ] **P1** Async bulk export with quotas (G-35, GA-19).

**Security**
- [ ] **P1** Volume quotas + anomaly alerts for authorized bulk access (G-36, SEC-AZ-09).
- [ ] **P1** Secret detection on user input with vault offer and redaction (G-37, SEC-CR-07).
- [ ] **P0** Sandbox cannot reach any PipesHub internal datastore/service (G-38, SEC-ISO-09).
- [ ] **P2** Content-safety guardrails at the Model Gateway (G-39).
- [ ] **P1** SIEM export + fail-closed external policy webhook (G-40, GA-20).
- [ ] **P1** IdP group-based policy subjects (G-41, GA-21).
- [ ] **P1** Configured write identity + AI disclosure; bot-key commit signing outside sandbox (G-42, GA-22).
- [ ] **P0** Vendor subscription login flows disabled and blocked (G-43, GA-23).
- [ ] **P2** Legal hold / eDiscovery overriding retention (G-44).

**Operations and quality**
- [ ] **P1** Priority classes and preemption across claims, gateways and provider limits (G-45, GA-24).
- [ ] **P2** Warm-pool autoscaling with idle-cost reporting (G-46).
- [ ] **P1** Availability SLO, error budgets, RPO/RTO, DR drill (G-47).
- [ ] **P1** Graceful degradation chain with visible notice (G-48, GA-25).
- [ ] **P0** Mock harness adapter, gateway record/replay, seeded fixture tenant, ephemeral PR envs (G-49, GA-26).
- [ ] **P1** Local dev profile documented in CONTRIBUTING (G-50).
- [ ] **P2** Product analytics dashboard (G-51).
- [ ] **P1** Timezone-aware schedules; date injected at context tail (G-52, GA-27).
- [ ] **P2** Entitlement gating (G-53); chargeback/showback export (G-54).

**Capability ideas**
- [ ] **P2** Cross-harness best-of-N / cross-review (G-55).
- [ ] **P2** Graduated autonomy within admin limits (G-56).
- [ ] **P2** Initializer → incremental long-horizon mode (G-57).
- [ ] **P1** Quarantined summarizer for bulk untrusted content (G-58, GA-28).
