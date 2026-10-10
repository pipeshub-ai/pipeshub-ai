# Agent Harness Runtime — Research Synthesis

This document collects the research behind [README.md](./README.md), [one-way-doors.md](./one-way-doors.md) and [test-scenarios.md](./test-scenarios.md). It was compiled on 2026-10-10.

**How reliable this is.** Agent CLIs change every week. Re-check flags and method names against the installed version before relying on them.
- Some primary sources could not be fetched from the research environment, including openai.com, opencode.ai, modelcontextprotocol.io, manus.im and cognition.ai. Claims taken from search summaries or secondary write-ups are marked **[secondary]**.
- Claims where sources disagree are marked **[verify]**.

---

## 0. What PipesHub already has

These are the starting points in the code at commit `18d929a`. Python paths are under `backend/python/app/`.

### Agent runtime: `agent_loop_lib/`

- **Agent definition and runner**
  - `agent/spec.py`: `AgentSpec` and `ModelSpec`.
  - `core/interfaces.py::AgentRunner`: `run`, `stream` (emits AG-UI events) and `resume`.
- **Loop styles** (`agent/loops.py`): ReAct, PlanExecute, PlanCritiqueExecute, Reflexion and Incremental.
- **Composition**
  - `AgentTool`, with `MAX_AGENT_TOOL_DEPTH = 6`.
  - `SpawnAgentTool`, which runs sub-agents as a DAG.
- **Hooks and middleware**
  - Hook events: `PRE`/`POST_TOOL_USE`, `PRE_MODEL_CALL` and others.
  - Middleware covers budget, compaction (L1–L8), permissions, sandbox guards, stall detection, skill preloading and skill learning.
- **Name collision:** `control_plane/control_plane.py::ControlPlane` already exists as the library's composition root.

### PipesHub adapter: `agents/agent_loop/`

- `factory.py::PipesHubAgentFactory` builds agents. Its budget, checkpoint, memory and workspace stores are deliberately left as `None`.
- `modes.py` defines the chat modes: quick, react, planExecute and deep.
- `stream_bridge.py` streams AG-UI events over SSE.
- `cancellation/kv_backed.py` stores cancel flags in the KV store under `chat:run:{id}:cancel`.
- `artifact_store.py` sits on top of `services/artifact_registry/registry.py::ArtifactRegistryService`.

### Agents in the graph

- Each agent is an `agentInstances` node.
- `AGENT_HAS_*` edges link it to toolsets, MCP servers, knowledge and skills.
- Routes live in `api/routes/agent.py`.

### Sandboxes

- **`agent_loop_lib/sandbox/`**
  - `coding/base.py::CodingSandboxBackend`, with local, Docker and E2B implementations.
  - `manager.py::SandboxManager`: per request, with a maximum life of 1,800 s and network off by default.
  - `governor.py`: in-process caps of 50 sandboxes total and 10 per org.
  - `egress_firewall.py`.
  - `os_sandbox.py`, which uses bubblewrap or Seatbelt.
  - `rpc.py`: JSON-lines RPC that implements programmatic tool calling. It is dev-only.
- **`app/sandbox/`**
  - `docker_proxy.py`: a Docker API proxy that enforces policy.
  - `base_executor.py::SANDBOX_ENV_ALLOWLIST`.
  - `package_policy.py`.
- **Deployment**
  - `deployment/sandbox/Dockerfile`.
  - `docker-socket-proxy` in the Compose build files.

### Skills

- Skills use the agentskills.io `SKILL.md` format.
- `modules/providers/skills/bundle.py` already handles mounting into a sandbox: `SKILLS_MOUNT_ROOT = "skills"`, `SkillBundle.as_sandbox_files()` and `SkillMaterializer`.
- Skills are stored by `GraphSkillStore` as `agentSkills` and `agentSkillVersions`.
- Built-in packs: pdf, docx, xlsx, pptx and data-analysis.
- REST routes are in `api/routes/skills.py`. The UI is under `workspace/skills`.

### Toolsets

- `agents/registry/toolset_registry.py` provides the `@Toolset` decorator. About 40 apps live under `agents/actions/`.
- `core/tool_schema.py::ToolSchema{name, description, input_schema}`.
- Lazy meta-tools: `list_toolsets`, `search_tools` and `fetch_tools`.
- Per-user credentials are kept in etcd and read through `ConfigurationService`.

### MCP

- **Node server at `/mcp`**
  - Built on the external package `@pipeshub-ai/mcp@2.4.2`.
  - Exposes 6 tools: `pipeshub_chat`, `pipeshub_search`, `pipeshub_sources`, `pipeshub_directory`, `pipeshub_download_record` and `pipeshub_agents`.
  - Accepts PAT, OAuth or JWT, and supports the OAuth protected-resource metadata endpoint and the device flow.
- **Python client:** `agents/mcp/` (fastmcp, stdio policy, OAuth and dynamic client registration).

### Third-party harness integration

- `integrations/omnigent/` is the only one. The user runs Omnigent themselves (`harness: claude-sdk`), pointed at `/mcp`.
- PipesHub does not orchestrate it.

### Gaps (none of these exist yet)

- Orchestrating third-party CLIs.
- Long-lived sessions or a persistent workspace.
- A scoped, short-lived token for sandboxes.
- A filesystem projection of tool schemas.
- An outbound model gateway. LiteLLM appears only as a provider type, not as a dependency.
- Agent schedules or triggers.
- Durable checkpoints in production.
- A cost ledger.
- An audit trail of tool calls.

---

## 1. Harnesses: how a control plane drives them

| Harness | Headless / programmatic | Stream format | Resume | MCP config | Instructions / skills | Permissions | Built-in sandbox | Custom model endpoint |
|---|---|---|---|---|---|---|---|---|
| **Claude Code / Agent SDK** | `claude -p`, `--input-format stream-json`, Python and TypeScript SDK (`query`, `canUseTool`, hooks); ACP via `@zed-industries/claude-agent-acp` | NDJSON: `system/init` (lists tools, MCP status and `capabilities[]`), `stream_event`, `assistant`, `result` | `--resume <id\|path>`, `--continue`, `--fork-session` | `--mcp-config`, `.mcp.json`, in-process SDK servers | `CLAUDE.md` (with `@AGENTS.md` import); `.claude/skills/` | Modes (`default`, `acceptEdits`, `plan`, `dontAsk`, `bypassPermissions`); allow and deny rules; `--permission-prompt-tool`; hooks | Seatbelt or bubblewrap, plus a proxy (`sandbox-runtime`) | `ANTHROPIC_BASE_URL` (Messages API) |
| **Codex CLI** | `codex exec --json`; **`codex app-server`** (JSON-RPC: `thread/*`, `turn/start\|steer\|interrupt`, server-to-client approval requests); TypeScript SDK. `codex mcp-server` is deprecated **[secondary]** | JSONL thread, turn and item events; JSON-RPC notifications | `exec resume`, `thread/resume`, `thread/fork` | `config.toml [mcp_servers.*]` | `AGENTS.md` (walked up the tree); skills in `.agents/skills` **[verify]** | `approval_policy` + `sandbox_mode`; `requirements.toml` | Seatbelt, or Landlock + seccomp (bwrap) | `model_providers`, **Responses API only** (chat wire removed in 2026) **[secondary]** |
| **OpenCode** | `opencode serve` (HTTP + SSE, OpenAPI at `/doc`); `opencode acp`; `run --format json` | SSE (`/event`; v2 `/api/event` still changing) | Session API | `opencode.json` → `mcp` (local and remote) | `AGENTS.md`; skills in `.opencode`, `.claude` and `.agents` | `permission` allow / ask / deny per tool and glob | None | Any (AI SDK `baseURL`) |
| **Gemini CLI** | `-p --output-format stream-json`; native ACP via `--acp` | NDJSON | `--resume` | `settings.json` → `mcpServers` | `GEMINI.md` (`contextFileName` is configurable); `.gemini/skills` | `--approval-mode` | Docker, Podman or Seatbelt | Gemini API; a gateway URL needs translation |
| **Qwen Code** | Fork of Gemini CLI; stream-json in and out (input side still beta) | NDJSON | **[verify]** | `~/.qwen/settings.json` | `QWEN.md` **[verify]** | Like Gemini | Like Gemini | OpenAI-compatible |
| **Goose** | `goose run`; native `goose acp` | json / stream-json **[verify]** | Sessions | `config.yaml` extensions | Recipes, `AGENTS.md` | Modes | None | Many |
| **Cursor CLI** | `cursor-agent -p --output-format stream-json`; cloud REST | NDJSON | `--resume` | `.cursor/mcp.json` | `AGENTS.md` / rules | Approvals | Seatbelt or Landlock | No (hosted models only) |
| **Amp** | `amp -x --stream-json` | NDJSON | `threads continue` | `amp.mcpServers` | `AGENTS.md` | Allowlist | None | No (models chosen server-side) |
| **DeepSeek Harness (`dsh`)** | Headless profile with JSON events (developer preview) **[secondary]** | JSON | Continuation | MCP client plugin | `AGENTS.md` and `CLAUDE.md`; bridges Claude and Codex hooks.json **[secondary]** | Hooks | **[verify]** | DeepSeek |
| **Aider** | `--message`, `--yes-always` | Plain text | History files | None | Conventions files | `--yes-always` | None | LiteLLM |

### Lessons for the adapter design

- **Inject all configuration explicitly** and ignore whatever the repo ships.
  - Claude Code `-p` without `--bare` runs project hooks and connects to `.mcp.json` servers, even in a folder it has never seen, with no trust prompt.
  - Codex and OpenCode read project config the same way.
- **Detect capabilities from the harness's own init event** (Claude's `capabilities[]`), not from version strings.
- **Store the harness's native transcripts verbatim**, so they can be re-parsed later.
- **Write a single `AGENTS.md`**, a `CLAUDE.md` that imports `@AGENTS.md`, and set Gemini's `contextFileName`.
- **Keep skills in one place.** Put them under `.agents/skills/` (or `/pipeshub/skills`) and symlink each harness's skills directory to it.

## 2. Protocols: which one to normalize on

- **ACP (Agent Client Protocol, Zed; Apache-2.0)**
  - Wire format: JSON-RPC over stdio.
  - Methods: `initialize`, `session/new` (which carries `mcpServers`), `session/load`, `session/prompt`, `session/update`, `session/request_permission` and `session/cancel`.
  - Native in Gemini CLI, OpenCode, Goose, Cursor and Copilot CLI. Claude Code and Codex reach it through adapters.
  - There is a JetBrains registry listing 30+ agents.
  - **Drafts in progress:**
    - v2 makes `session/list` and `session/resume` required, and turns the `prompt` response into an "accepted" acknowledgement.
    - A Streamable HTTP / WebSocket remote transport.
    - **MCP-over-ACP**, which would tunnel MCP servers we provide through the ACP channel, with no network path out of the sandbox. Watch this one closely.
  - OpenHands' `ACPAgent` already uses ACP for exactly this job.
- **AG-UI** carries events from the agent to the browser. PipesHub already uses it, so it stays as the browser projection.
- **A2A** (Linux Foundation, v1.0) is for agent-to-agent delegation across platforms. Use it to *expose* PipesHub agents. It is the wrong layer for driving CLIs: it has no notion of diffs, terminals or fine-grained approvals.
- **MCP** is how tools are injected. **2026-07-28 spec** changes, as reported **[secondary]**:
  - The protocol is stateless: no `initialize`, no session id, and server-minted handles carry state.
  - `server/discover`.
  - Tasks becomes an extension.
  - Multi-round-trip requests (`input_required`) replace server-initiated sampling and elicitation.
  - `ttlMs` / `cacheScope`.
  - JSON Schema 2020-12.
  - Dynamic Client Registration is replaced by Client ID Metadata Documents (CIMD).
  - SSE resumability is removed.

  Many harnesses still implement 2025-06-18 or 2025-11-25, so the Tool Gateway must serve those versions while being designed stateless.
- **Recommendation:** two layers.
  1. **Inside the sandbox:** `sandboxd` speaks ACP to harnesses that support it, and each harness's native protocol where ACP loses detail (the Claude SDK; the Codex app-server).
  2. **Between `sandboxd` and the orchestrator:** our own versioned event envelope, modelled on ACP `session/update`.

  Browser traffic uses AG-UI, outward-facing agents use A2A, and tools use MCP.

## 3. Prior art: running any coding agent in a sandbox from a control plane

| System | Pattern | Lesson for us |
|---|---|---|
| **Rivet sandbox-agent** | A Rust daemon inside the sandbox; adapters for Claude, Codex, OpenCode and Amp; one HTTP/SSE API with a universal event schema; transcripts in external SQLite or Postgres; approve or deny remotely | The closest blueprint. Keep the transcript outside the sandbox. |
| **OpenHands Agent SDK** (MLSys 2026) | sdk / tools / workspace / agent-server packages; an event-sourced, immutable log; `ACPAgent` wraps third-party harnesses; model traffic goes through a LiteLLM proxy for metering | Event sourcing plus a forced model gateway. |
| **Claude Code on the web** | An isolated VM per session; a git proxy swaps a scoped credential for the real one and allows pushes only to the session branch; an egress allowlist proxy; SessionStart hooks act as setup | Secrets never enter the sandbox; git operations are checked at the proxy. |
| **Codex cloud** | The setup phase has internet and secrets; **secrets are removed before the agent phase**, which has no internet by default (it can use a domain and method allowlist); containers are cached for about 12 h | Two phases: setup, then agent. |
| **GitHub Copilot coding agent** | Runs inside an Actions job; `copilot-setup-steps.yml` runs before the firewall goes up; output is a draft PR | Firewall after setup; PR as the output. |
| **Harbor** (Terminal-Bench 2.0) | An "installed agent" abstraction (install script, headless command, trajectory parser) over Docker, Daytona, Modal, E2B and Runloop | Reuse it for eval CI. |
| **Coder AgentAPI / Tasks** | Wraps any TUI agent with a terminal emulator behind an HTTP+SSE API; one workspace per task | Use screen-scraping only as a last-resort adapter. |
| **Sculptor (Imbue)** | One container per agent; deliberately ignores Claude's permission settings because the container is the boundary | The outer sandbox is the boundary. |
| **vibe-kanban, Conductor** | One git worktree per attempt; executor modules per agent | Isolate parallel attempts with worktrees or branches. |

## 4. Sandboxes and isolation

| Option | Isolation | Start | Snapshot / pause | Self-host fit | Notes |
|---|---|---|---|---|---|
| **kubernetes-sigs/agent-sandbox** | `RuntimeClass` (gVisor or Kata) | Warm pools; GKE claims p90 of 200 ms **[secondary]** | Pause and resume; pod snapshots are GKE- and gVisor-only | **Best fit for Helm** | CRDs: Sandbox, Template, Claim, WarmPool. API version **[verify]** |
| **Docker + gVisor (runsc)** | User-space kernel | Container start | Filesystem diff only | **Baseline for Compose** | No KVM needed. **File I/O 10–200× slower on open/close-heavy workloads**, so benchmark `git` and `npm` |
| **Kata (Cloud Hypervisor / Firecracker)** | MicroVM | ~150–300 ms **[secondary]** | Supported by the VMM | Needs KVM | GPU passthrough via Cloud Hypervisor |
| **E2B** | Firecracker | Sub-second | Pause/resume with memory, fork | OSS infra; BYOC; "Embed" single-host option | Needs KVM |
| **Daytona** | **[verify]** | ~90 ms (vendor figure) | Snapshots, fork | OSS repo status and AGPL licence **[verify]** | — |
| **Modal** | gVisor (VM option) | Sub-second | Filesystem snapshots; memory snapshots (experimental) | SaaS only | GPUs |
| **Vercel Sandbox** | Firecracker | Fast | Filesystem persistence GA | SaaS only | **Best-in-class egress:** a per-sandbox CA, header injection, DNS filtering |
| **Cloudflare Sandbox** | VM per instance | Seconds to resume | Backup and restore to R2 | SaaS only | Outbound Workers inject credentials per host |
| **Anthropic sandbox-runtime (srt)** | bubblewrap / Seatbelt + proxy | Instant | — | A library | No TLS inspection; shares the host kernel; has had CVEs (empty allowlist did not isolate) |

**Nested sandboxes.** A harness's own sandbox may not work inside gVisor: bubblewrap needs user namespaces, and Landlock support under gVisor is **[verify]**. Configure each harness to rely on the outer boundary, and test this per harness (conformance case CF-18).

## 5. Security: threats and patterns

### Threat models and attack research

- **Lethal trifecta** (Willison). Private data, plus untrusted content, plus a way to communicate externally, adds up to exfiltration. An enterprise search agent has all three by default.
- **Agents Rule of Two** (Meta, Oct 2025). In a session, allow at most two of: untrusted input, sensitive data or systems, and changing state or communicating externally. A task that needs all three requires human approval or a fresh session.
- **Design patterns against prompt injection** (Beurer-Kellner et al., 2025):
  - action-selector
  - plan-then-execute
  - LLM map-reduce
  - dual LLM
  - code-then-execute (CaMeL)
  - context-minimization

  We cannot restructure a third-party harness. We *can* apply these patterns at our tool and egress layer: taint labels, label-gated actions, and quarantined map-reduce tools for bulk untrusted data.
- **CaMeL** (DeepMind) uses capability and provenance tracking. It solved 77% of AgentDojo tasks with provable security, against 84% undefended.
- **EchoLeak** (CVE-2025-32711, M365 Copilot) was a zero-click attack. It used reference-style markdown, an image fetch and a proxy domain allowed by the CSP. Never auto-render remote images or links in agent output.
- **MCP tool poisoning, rug pulls and shadowing** (Invariant Labs). Defences:
  - an allowlist
  - pinned manifest hashes, with drift rejected
  - namespacing
  - scanning tool descriptions
  - re-approving when a manifest changes
- **MCP authorization**. Servers are OAuth 2.1 resource servers:
  - RFC 9728 protected-resource metadata
  - RFC 8707 resource indicators
  - audience validation
  - **no token passthrough**

  Enterprise-Managed Authorization (ID-JAG) suits SSO tenants **[verify revision status]**. The confused-deputy rule: the server derives identity from the validated token, never from arguments.
- **Skills supply chain.** The ClawHavoc campaign planted 341 malicious skills on ClawHub using ClickFix-style "prerequisites" **[secondary]**. Allow only a signed, admin-approved internal registry, and review skills as code.
- **Harness CVEs:**
  - Claude Code SOCKS null-byte hostname bypass
  - srt CVE-2025-66479
  - DNS exfiltration through auto-approved `ping`/`dig` (CVE-2025-55284)
  - WebFetch domain bypass (CVE-2026-24052)

  These argue for pinned versions, an outer boundary that does not depend on the harness, and parser-trick regression tests (SEC-EG-06).
- **OWASP references.** LLM Top 10 (2025): LLM01 injection, LLM06 excessive agency, LLM08 vector and embedding weaknesses (permission-aware RAG), LLM10 unbounded consumption. Agentic Top 10 (2026): ASI01 goal hijack through ASI10 rogue agents **[verify the list on genai.owasp.org]**.

### Credential handling: secrets never in the sandbox

- **Claude Code on the web:** git proxy.
- **Vercel:** a host-side firewall with a per-sandbox CA and header injection.
- **Cloudflare:** Outbound Workers with `outboundByHost`.
- **Codex cloud:** secrets stripped before the agent phase.
- **Anthropic secure-deployment guide:** `--network none` plus a Unix socket to a proxy; Envoy `credential_injector`; `ANTHROPIC_BASE_URL` pointed at a gateway; network-level enforcement for tools that ignore `HTTP_PROXY`.

### Memory

- Attacks: MINJA, MemoryGraft, and an ICML 2026 study showing injection defences miss memory poisoning.
- Requirements: record provenance per entry; treat retrieved memory as untrusted; review any write to org scope.

## 6. Context engineering and tool exposure (quantitative)

| Technique | Result | Source |
|---|---|---|
| MCP servers presented as files or code (progressive disclosure) | ~150k → ~2k tokens (−98.7%) | Anthropic, "Code execution with MCP" |
| Tool Search Tool (`defer_loading`) | ~77k → ~8.7k tokens (−85%); Opus 4.5 MCP eval 79.5% → 88.1% | Anthropic, "Advanced tool use" |
| Programmatic tool calling | −37% tokens; ~200 KB of raw data reduced to ~1 KB entering context | Anthropic |
| Tool use examples | 72% → 90% on complex parameters | Anthropic |
| Cloudflare Code Mode (two tools: `search`, `execute`) | Whole Cloudflare API in ~1k tokens | Cloudflare **[secondary]** |
| Tool-selection accuracy | Degrades past ~30–50 tools | Anthropic docs |
| Concise `response_format` | ~⅓ of the tokens of detailed output | Anthropic, "Writing tools for agents" |
| Tool response cap | Claude Code caps at 25k tokens | Anthropic |
| Subagent return size | ~1–2k-token summaries | Anthropic, context engineering |
| Agents vs chat; multi-agent | ~4× tokens; ~15× tokens | Anthropic, multi-agent research |
| Context editing + memory | +39% on internal eval; −84% tokens over 100 turns | Anthropic, context management |
| KV-cache economics | ~100:1 input:output tokens; cached input ~10× cheaper | Manus **[secondary]** |

### Design rules we adopt

- **Prefixes must be byte-stable** (Manus, Codex, DeepSeek disk cache):
  - sort tools deterministically
  - keep timestamps out of the prefix
  - append only
  - **mask tools rather than remove them**
  - never change the tool list in the middle of a session (Codex had a cache bug from inconsistent MCP tool order)
- **Use the filesystem as context:**
  - offload large results to files and return a path plus a summary
  - recite a todo list at the tail of context
  - keep errors in context
- **Keep `AGENTS.md` a ~100-line map** that points into a structured docs directory (OpenAI "Harness engineering"). LLM-written context files hurt results; short human-written ones help **[secondary]**.
- **Long-running work:** an initializer agent writes a feature list, `init.sh`, a progress file and a first commit. Each later session takes one feature, verifies it end to end, and commits (Anthropic, "Effective harnesses for long-running agents").
- **Parallelize read-only exploration; keep writes single-threaded.** This reconciles Anthropic's multi-agent research with Cognition's "Don't build multi-agents".

## 7. Long-running and durable execution

### Durable execution engines

| Engine | Model | Notes |
|---|---|---|
| Temporal | History replay | Operationally heavy for Compose installs |
| DBOS | Postgres step checkpoints | — |
| Restate | Push-based | Used by Replit **[secondary]** |
| Inngest | Per-step billing | Billing inflates in agent loops |

LangGraph interrupt semantics re-execute the node on resume, so **side effects must be idempotent**.

### Session and resume behaviour

- **Claude Agent SDK** sessions cover the conversation only, not files. File checkpointing is a separate feature.
- **Prompt cache TTLs:** 5 min (write 1.25×) and 1 h (write 2×), with reads at 0.1×. Use the 1 h TTL across human-approval waits.

### Recommendation for PipesHub

Build a home-grown durable state machine first: Mongo, KV leases and MessagingFactory, the same pattern the indexing service uses. Hide it behind the orchestrator interface so it can be swapped later.

## 8. Evaluation

- **Anthropic, "Demystifying evals for AI agents"**
  - Grade the final environment state, not what the agent claims.
  - You are evaluating harness and model together.
  - Use code graders, model graders and human graders. Give model graders an "Unknown" option.
  - Measure **pass^k**: 75% per trial gives about 42% at pass^3.
  - Start with 20–50 tasks drawn from real failures.
  - Grader bugs dominate. Example: CORE-Bench went from 42% to 95% after the graders were fixed.
- **Benchmarks**

  | Benchmark | What it offers | Reported result |
  |---|---|---|
  | **Terminal-Bench 2.0 + Harbor** | 89 tasks; any installed agent; runs on Docker, Daytona, Modal or E2B | — |
  | **SWE-bench Pro** | 1,865 tasks; resistant to contamination | — |
  | **τ²-bench** | Dual-control tasks | pass^k drops steeply |
  | **MCP-Universe** | Real MCP servers | Best model 43.7% |
  | **MCPMark** | — | — |
  | **AgentDojo** | Indirect-injection benchmark | More capable models are often easier to attack |
  | **ReliabilityBench** | Fault injection | pass@1 overstates reliability by 20–40% |

- **Inspect AI** (UK AISI): task = dataset + solver + scorer, with Docker, K8s and Modal sandboxes.

## 9. Observability

- **OpenTelemetry GenAI semantic conventions** (Development status):
  - span types: `invoke_agent`, `execute_tool`, `plan`, `invoke_workflow`
  - skill-load and command-execution spans
  - token counts: `gen_ai.usage.cache_read.input_tokens` and `cache_write`
  - tool call arguments and results are opt-in, because they are sensitive
- The 2026-07-28 MCP spec deprecates protocol-level logging in favour of OTel or stderr.
- **Harness OTel export:**
  - Claude Code: `CLAUDE_CODE_ENABLE_TELEMETRY` + `OTEL_*`
  - Codex: `[otel]`, which needs the full `/v1/logs` path
  - Gemini: `otlpEndpoint`, gRPC by default. It reportedly sends nothing, with no error, to collectors that accept HTTP only.
- **Existing PipesHub hooks:** Opik tracing (`agent_loop_lib/transport/opik_tracing.py`) and `app/telemetry/`.

## 10. Open-weight models and DeepSeek-specific notes

- **DeepSeek has an Anthropic-compatible endpoint**, `api.deepseek.com/anthropic`, so Claude Code can run on DeepSeek V4.
  - It maps `claude-opus*` to v4-pro and `claude-sonnet*`/`claude-haiku*` to v4-flash.
  - It **ignores `cache_control`** and has no image or document input.
  - Use `--bare` so local OAuth settings don't override `ANTHROPIC_BASE_URL`.
- **DeepSeek thinking mode requires earlier reasoning to be sent back** on tool turns, or the next request fails with HTTP 400. Kimi K2 (interleaved thinking, 200–300 sequential tool calls) and GLM 4.7+ ("preserved thinking") need the same. **The gateway and transcript store must never strip reasoning.**
- **DeepSeek V3.2:**
  - DeepSeek Sparse Attention (a lightning indexer selecting the top ~2,048 key-value tokens)
  - "thinking with tools"
  - large-scale synthesis of agentic tasks (1,800+ environments) **[secondary]**
  - the Speciale variant has no tool calling
- **DeepSeek V4** (Apr 2026): 1M context; DeepSeek reports 80.6% on SWE-bench Verified. It documents Claude Code and OpenCode as supported harnesses.
- **DeepSeek's disk context cache** matches exact prefixes only, and hits cost about 2% of a miss. That is another reason to keep prefixes byte-stable and avoid rewriting history.
- **DeepSeek Harness (`dsh`)** is plugin-based, reads `AGENTS.md`/`CLAUDE.md`, and **bridges Claude Code and Codex hooks.json**. That is evidence the industry is converging on shared conventions. Dates and features are **[verify]**.
- **Codex** removed the chat wire format, so DeepSeek, Kimi and others need a translation layer from Chat Completions to the Responses API in the gateway.
- **Qwen3-Coder** uses an XML tool-call format, and vLLM's parsers have known failure modes. Pin the vLLM version, chat template and parser per model, and run the conformance suite against each.

## 11. Recommended additions beyond the brief

1. **Harness-neutral `ph` CLI plus generated SDK stubs.** Any harness can run shell commands, so this works even without MCP, and it enables programmatic and code-mode tool calling. In published results this is the largest single token saving.
2. **Two-phase sessions (setup, then agent)**, with secrets and network available only during setup.
3. **Taint-label policy** (Rule of Two) enforced at the gateways. This is the realistic defence against prompt injection when the harness is a third-party black box.
4. **`HarnessAgentRunner`.** Third-party harnesses become workers inside PipesHub deep mode, so PipesHub orchestration composes with the best coding agents instead of competing with them.
5. **Golden-trace regression loop:** every production failure becomes an eval case before its fix ships.
6. **Cache-ratio SLO.** It catches nondeterministic tool ordering, which otherwise silently multiplies cost.
7. **Canary secrets and canary facts** in CI: a fake token planted where secrets must never appear, and a restricted-document fact that must never appear in an answer.
8. **Support for MCP Apps and MCP Tasks** (2025-11-25 and later) in the Tool Gateway for rich UI and long-running connector jobs. Use MRTR (`input_required`) for approvals where clients support it.
9. **Private MCP registry** that pulls selectively from the official registry, combined with manifest pinning.
10. **Harbor "installed agent" for PipesHub**, so harness × model × exposure-mode evals run the same way in CI and at scale.

---

## Sources

### Harnesses and protocols

- Claude Code headless and Agent SDK: https://code.claude.com/docs/en/headless · https://docs.claude.com/en/api/agent-sdk/typescript · https://code.claude.com/docs/en/agent-sdk/sessions · https://code.claude.com/docs/en/monitoring-usage
- Codex: https://github.com/openai/codex/blob/main/codex-rs/app-server/README.md · https://developers.openai.com/codex/config-reference · https://developers.openai.com/codex/security · https://developers.openai.com/codex/cloud/environments · https://developers.openai.com/codex/skills
- OpenCode: https://opencode.ai/docs/server/ · https://opencode.ai/docs/mcp-servers · https://opencode.ai/docs/skills
- Gemini CLI: https://www.geminicli.com/docs/cli/headless · Qwen Code: https://qwenlm.github.io/qwen-code-docs/
- Goose ACP: https://goose-docs.ai/docs/guides/acp-clients · Amp: https://ampcode.com/docs/cli/execute-mode · Cursor: https://cursor.com/docs/agent/tools/terminal.md · Factory: https://docs.factory.ai/cli/droid-exec/overview.md
- DeepSeek Harness: https://github.com/deepseek-ai/deepseek-harness
- ACP: https://agentclientprotocol.com (v2 migration, transport RFD, MCP-over-ACP RFD) · A2A: https://linuxfoundation.org/press/a2a-protocol-surpasses-150-organizations-lands-in-major-cloud-platforms-and-sees-enterprise-production-use-in-first-year
- MCP spec changelogs: https://github.com/modelcontextprotocol/modelcontextprotocol (2025-11-25, 2026-07-28) · MCP Apps SEP-1865 · Registry: https://blog.modelcontextprotocol.io/posts/2025-09-08-mcp-registry-preview/

### Prior art

- Rivet sandbox-agent: https://github.com/rivet-dev/sandbox-agent · OpenHands SDK: https://arxiv.org/abs/2511.03690 · https://docs.openhands.dev/sdk/guides/agent-acp
- Coder AgentAPI: https://github.com/coder/agentapi · Harbor: https://github.com/harbor-framework/harbor · https://www.tbench.ai/news/announcement-2-0
- Claude Code on the web: https://anthropic.com/news/claude-code-on-the-web · https://anthropic.com/engineering/claude-code-sandboxing
- Copilot coding agent: https://docs.github.com/en/copilot/how-tos/use-copilot-agents/coding-agent/customize-the-agent-environment · Sculptor: https://imbue.com/sculptor

### Sandboxes and egress

- https://code.claude.com/docs/en/agent-sdk/secure-deployment · https://github.com/anthropic-experimental/sandbox-runtime
- https://github.com/kubernetes-sigs/agent-sandbox · https://kubernetes.io/blog/2026/03/20/running-agents-on-kubernetes-with-agent-sandbox
- https://github.com/e2b-dev/infra · https://modal.com/docs/guide/sandbox-networking · https://vercel.com/blog/a-sandbox-without-a-network-boundary-is-only-half-a-sandbox · https://blog.cloudflare.com/sandbox-auth/
- https://northflank.com/blog/kata-containers-vs-firecracker-vs-gvisor

### Security

- https://simonwillison.net/2025/Jun/16/the-lethal-trifecta/ · https://ai.meta.com/blog/practical-ai-agent-security/ · https://arxiv.org/abs/2506.08837 · https://github.com/google-research/camel-prompt-injection
- EchoLeak: https://arxiv.org/html/2509.10540v1 · MCP tool poisoning: https://simonwillison.net/2025/Apr/9/mcp-prompt-injection/
- MCP authorization: https://modelcontextprotocol.io/specification/2025-11-25/basic/authorization · security best practices: https://modelcontextprotocol.io/specification/latest/basic/security_best_practices
- OWASP GenAI: https://genai.owasp.org · Memory poisoning: https://arxiv.org/html/2512.16962

### Context engineering, long-running agents and evals

- Anthropic engineering: code-execution-with-mcp · advanced-tool-use · writing-tools-for-agents · effective-context-engineering-for-ai-agents · building-effective-agents · multi-agent-research-system · equipping-agents-for-the-real-world-with-agent-skills · effective-harnesses-for-long-running-agents · demystifying-evals-for-ai-agents (all under https://www.anthropic.com/engineering/)
- Tool search: https://platform.claude.com/docs/en/agents-and-tools/tool-use/tool-search-tool · Memory tool: https://platform.claude.com/docs/en/agents-and-tools/tool-use/memory-tool · Prompt caching: https://platform.claude.com/docs/en/build-with-claude/prompt-caching
- Manus context engineering **[secondary]** · OpenAI "Unrolling the Codex agent loop" and "Harness engineering" **[secondary]** · Cognition "Don't build multi-agents" **[secondary]** · LangChain deep agents: https://docs.langchain.com/oss/python/deepagents/harness
- OTel GenAI semconv: https://github.com/open-telemetry/semantic-conventions-genai · Inspect AI: https://inspect.aisi.org.uk · AgentDojo: arXiv 2406.13352 · τ²-bench: arXiv 2506.07982 · MCP-Universe: arXiv 2508.14704 · ReliabilityBench: arXiv 2601.06112

### Open models

- DeepSeek Anthropic API: https://api-docs.deepseek.com/guides/anthropic_api · V3.2: https://arxiv.org/abs/2512.02556 · Kimi thinking: https://platform.kimi.ai/docs/guide/use-thinking-models · GLM thinking: https://docs.z.ai/guides/capabilities/thinking-mode.md
