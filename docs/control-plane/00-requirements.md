# Control plane: requirements

**Status:** design proposal. **Date:** 2026-10-09. **Branch:** `harness/cpd-control-plane-design` (base `bf7485e35`).
**Scope:** one control plane for skills, MCP servers, toolsets, plugins (future) and the agent harness.
**Read next:** [01-hld.md](01-hld.md), [02-lld.md](02-lld.md), [03-interfaces.md](03-interfaces.md),
[04-bottom-up-verification.md](04-bottom-up-verification.md), [05-migration-plan.md](05-migration-plan.md).

This file is written top down: who uses the control plane, what they need from it, and the
non-functional bounds every lower layer must satisfy. Every requirement has an id (`FR-…`, `NFR-…`).
The other documents cite these ids, and [04-bottom-up-verification.md](04-bottom-up-verification.md)
checks each one against the layer that serves it.

Path prefixes used throughout this folder:

| Prefix | Path |
|---|---|
| `H/` | `backend/nodejs/apps/src/modules/agent-harness/` |
| `N/` | `backend/nodejs/apps/src/` |
| `PY/` | `backend/python/` |
| `FE/` | `frontend/app/(main)/` |
| `C/` | `contracts/harness/` |
| `CP/` | `contracts/control-plane/` (new, this design) |

Inputs: the R2 control-plane review (five-domain inventory, F1–F21, CP0–CP11), the R3 code and test
review (duplication map D1–D21, WP-1..15), the R1 UX and latency review, `docs/agent-harness-v2/`
(ARCHITECTURE, ENGINES-PLAN, FLAGS, SLO and the impl docs), and the repository `CLAUDE.md`.

---

## 1. Problem statement

Today each of the five domains has its own registry, installation record, approval flow, credential
layout, admin check, visibility rule and digest (R2 §1.2–1.3). A run's configuration is assembled by
about eight resolvers, several authority-bearing policies are read live instead of pinned, and no
configuration change reaches the audit chain (R2 F1, F2, F9). The result is that:

- an admin cannot answer "what can this agent do for this user, and why" in one place;
- a builder sees catalog items that will fail at run time (missing credential, not approved, not
  visible to the user) only after a user hits them;
- a security reviewer cannot reconstruct who widened what, when;
- every new domain (plugins next) would add a sixth copy.

The control plane replaces the copies with one core (catalog, installation, grant, policy, binding,
approval, resolution, pinning, audit, change notification) and makes each domain a module that plugs
into it.

---

## 2. Actors

| Id | Actor | Who | Touches the control plane through |
|---|---|---|---|
| A1 | **End user** | Any member of an org who chats, runs agents, schedules runs | Chat UI, agent picker, "My connections", approval cards, Slack, MCP clients |
| A2 | **Agent builder** | A user with `can_edit` on an agent | Agent builder (capabilities and runtime tabs), preview/explain |
| A3 | **Org admin** | Holder of the org admin role (`userAdminCheck`) | Workspace admin area: catalog, installations, approvals, policy, audit |
| A4 | **Team admin** (new role, reserved) | Admin of a user group, delegated by A3 | Same screens, restricted to the team scope |
| A5 | **Operator** | Whoever deploys PipesHub (Compose, Helm, managed) | Env, Helm values, release data; read-only deployment layer view |
| A6 | **Security reviewer** | Auditor, CISO staff; may be an org admin with read-only audit scope | Audit log, effective-config explain, run evidence |
| A7 | **Runtime** | Node admission, the durable worker (loop kernel), engine kernel and adapters, the legacy query path, the tool gateway, the model gateway, the egress proxy | Internal ports and routes only: resolve, pin, read pinned config, check the live layer, lease credentials |
| A8 | **Plugin author** (future) | First-party or third-party developer packaging skills, MCP servers and declarative toolsets | A signed plugin manifest and the catalog import path |

---

## 3. Jobs to be done and functional requirements

Jobs are grouped by actor; each job lists the functional requirements that serve it. Requirements
are domain-neutral unless a domain is named: "item" means a catalog item of any kind (skill, MCP
server, toolset, engine, harness option set, plugin).

### 3.1 End user (A1)

| Job | Requirement |
|---|---|
| "When I chat, the agent should just work with the tools it was built with." | **FR-1** A run resolves exactly the items the agent grants, intersected with what the user is allowed to see and use, without the user configuring anything that the builder or admin already configured. |
| "When a tool needs my account, ask me once, in context." | **FR-2** If a granted item needs a per-user credential binding that is missing or expired, the run is not refused silently: the UI shows a typed "connect your account" action for that item before or during the run, and the rest of the agent still works (the item is marked unavailable, not the run). |
| "I want to know why something is not available." | **FR-3** Every refusal or unavailable item carries a typed reason (`not_installed`, `not_approved`, `not_visible`, `not_granted`, `credential_missing`, `credential_revoked`, `policy_ceiling`, `kill_switch`, `control_plane_unavailable`) and the next action and actor (for example "ask an org admin to approve Linear MCP"). |
| "I can add my own skills and personal MCP servers and keep them private." | **FR-4** A user may install items at the user scope (where policy allows the kind), visible only to that user unless shared; shared use requires the same approval policy as an org install. |
| "Within what the agent allows, I choose model, effort, and tools for this turn." | **FR-5** Per-run choices (model, reasoning effort, tool subset, deadline) are accepted only inside the resolved ceilings; a narrowing is always accepted, a widening is refused with the dimension named. |
| "My preferences carry across chats." | **FR-6** User-scope choices (default model, effort, personal memory mode) are stored once and applied as defaults inside the agent's ceilings. |
| "Approve or deny a risky action quickly." | **FR-7** Per-call approvals keep today's interaction flow (P2.6c) and are driven by the pinned approval policy, not a live read. |

### 3.2 Agent builder (A2)

| Job | Requirement |
|---|---|
| "Pick capabilities for my agent from what I am allowed to grant." | **FR-8** The builder lists items *grantable by this builder* (installed, approved, visible to the builder, within org ceilings), each with its state: ready, needs credential, pending approval, deprecated, upgrade available. |
| "Narrow, not widen, what the org allows." | **FR-9** Agent-scope policy (engines, effects, approvals, budgets, models, egress, memory) is validated on save as a narrowing of the org layer, and re-validated at admission. |
| "See what a run of my agent will actually get." | **FR-10** A preview/explain call returns the effective set and effective run config for (agent, user?, request?) with provenance per dimension and per item, without starting a run. |
| "Share my agent safely." | **FR-11** Sharing an agent never shares the builder's personal bindings; agent-owned (service-account) bindings are explicit and visible as such. |
| "Version my agent's capabilities." | **FR-12** Grant changes bump the agent's grant revision; runs pin the revision they used; the builder can see which revision a run used. |

### 3.3 Org admin (A3) and team admin (A4)

| Job | Requirement |
|---|---|
| "See everything that can be installed and everything that is installed." | **FR-13** One catalog view across kinds (skills, MCP servers, toolsets, engines, plugins) with provenance (built-in, release, imported, org, user, plugin), version, risk class and descriptor digest. |
| "Install and configure an item once for the org or a team." | **FR-14** Installations are per org with an owner scope (org, team, user); configuration is validated against the item's configuration schema; each installation has a revision and a status lifecycle. |
| "Approve before anything new can run." | **FR-15** A configurable approval policy decides which changes need approval (new install, new or changed tool descriptors, version upgrade, scope widening, plugin install), by whom (org admin, team admin, owner), with history; until approved, the change has no effect on runs. |
| "Decide who may use it." | **FR-16** Grants bind subjects (org, team, agent, user) to installations, optionally to a subset of the installation's tools; visibility (who can see and pick an item) is distinct from grant (what a run can use). |
| "Set guard rails once." | **FR-17** Org-scope policy covers every harness dimension in R2 §2.3 plus catalog enablement per kind; each dimension is a ceiling (narrow-only below) or a choice (inside its ceiling). |
| "Change config without breaking running work, and roll back." | **FR-18** Every config write is optimistic-concurrency controlled (revision + `If-Match`), keeps history, and supports rollback to a prior revision, which itself is a new audited revision. |
| "Turn a capability off now." | **FR-19** Disable, revoke and kill-switch take effect on new admissions immediately (read-your-writes) and on running work within a bounded time (NFR-23), as a narrowing only. |
| "Bring organisation credentials." | **FR-20** Org- and agent-owned credential bindings are created, rotated and revoked through one binding API regardless of domain; secret material never leaves the credential store except as a short-lived, run-bound lease. |
| "Delegate to a team." | **FR-21** An org admin can delegate a subset of admin actions to team admins for installations and grants owned by that team; delegation is itself a policy ceiling. |

### 3.4 Operator (A5)

| Job | Requirement |
|---|---|
| "Set deployment limits that orgs cannot exceed." | **FR-22** A typed deployment layer, built from env and Helm values at startup, validated at boot, sets ceilings and operator-only settings (R2 §2.3 operator-only list); an invalid value fails startup. |
| "Roll features out gradually." | **FR-23** Feature stages `off | canary | on` per feature with deployment maximum, org opt-in/out and a per-run stable-hash canary, pinned on the run. |
| "Run on whichever stores the install chose." | **FR-24** The control plane uses the store abstractions (NFR-30) and needs no store the deployment does not already run. |
| "Know when the control plane is unhealthy." | **FR-25** Health, cache hit ratio, resolve latency, notifier lag and fail-closed refusals are exported as metrics. |

### 3.5 Security reviewer (A6)

| Job | Requirement |
|---|---|
| "Who changed what, when, and was it approved?" | **FR-26** Every control-plane write (catalog import, install, config, grant, policy, binding create/rotate/revoke, approval decision, Labs save) appends `config.changed` to the per-org hash-chained audit stream, in the same transaction as the write. |
| "What did this run actually have?" | **FR-27** Each run pins one `ResolvedRunConfig` (effective set + effective run config) with a digest and provenance; the per-run explain route returns it plus every live narrowing applied. |
| "Prove a run could not exceed the agent's grant." | **FR-28** The pin carries descriptor digests of the approved items; the runtime refuses a tool whose live descriptor digest differs from the pinned one (drift = `capability_lost`, never a silent upgrade). |

### 3.6 Runtime (A7)

| Job | Requirement |
|---|---|
| "Admit a run with one call." | **FR-29** One `RunConfigPinner.pin(subject)` is used by every admission path: chat submit, child spawn, next-run drain, schedule firing, Slack and MCP surfaces, and the legacy query path. |
| "Execute from the pin, not from live config." | **FR-30** The worker, engine kernel and gateways read authority only from the pin. The only live input is the `live` layer, which can narrow and never widen. |
| "Get credentials just in time." | **FR-31** The runtime receives binding references in the pin and leases secrets by reference, bound to (run, attempt, installation), with a TTL. |
| "Materialise items of any kind without knowing the kind." | **FR-32** Python resolves pinned items into tool sources, mounts and prompts through a registry of per-kind runtime adapters; adding a kind adds an adapter, not a branch in the assembler. |

### 3.7 Plugin author (A8, future)

| Job | Requirement |
|---|---|
| "Ship skills, MCP servers and declarative toolsets as one package." | **FR-33** A plugin is a catalog item whose manifest *contributes* items of other kinds; installing it creates child installations linked to it, and uninstalling cascades. |
| "Be governed like everything else." | **FR-34** Plugin installs go through the same approval policy, grants, bindings, audit and pinning; a plugin cannot contribute executable code into Node or the Python worker process (only skills, remote MCP servers, sandboxed MCP runners and declarative toolsets). |
| "Publish updates." | **FR-35** Plugin versions are immutable and digest-addressed; an upgrade is an approval-gated installation change, and running work keeps the version it pinned. |

### 3.8 Cross-cutting functional requirements

- **FR-36** Each domain has exactly one catalog, one installation model, one grant model, one binding
  model and one digest scheme: the core ones.
- **FR-37** The existing admin, agent and domain routes keep working (facades) until the frontend moves;
  their response shapes do not change except for additive fields.
- **FR-38** Every refusal from the control plane uses one error model (code, dimension or item, layer,
  remediation, actor) shared by Node, Python and the frontend through generated types.
- **FR-39** Configuration is explainable at three granularities: per org (what is installed and why),
  per agent and user (preview), per run (pinned).
- **FR-40** Every chat mode is served through the **existing chat routes** (`/api/v1/conversations/…/stream`,
  `/api/v1/agents/:agentKey/conversations/…/stream`, regenerate, and the `/internal/*` Slack twins) in **one
  request** that admits, resolves and streams. The server, not the client, decides durable versus legacy,
  project scope and agent scope from the conversation record and the resolved configuration (R1 §0, F6, F8).
  API and PAT clients get the same routing as the web UI. `POST …/runs` + `GET …/runs/:id/stream` remain for
  reconnect and existing clients.
- **FR-41** Regenerate is admitted like a send and governed by the same pin (R1 F2).

---

## 4. Non-functional requirements

### 4.1 Latency on the chat hot path

The chat hot path is everything between the browser's POST and the first model call. The SLOs that
already bind it (docs/agent-harness-v2/SLO.md):

- **#2** durable non-model overhead p50 ≤ legacy + 75 ms (TTFT), ≤ legacy + 100 ms (turn done);
- **#3** `conversation_created` first frame p95 < 300 ms;
- **#4** admission p95 < 150 ms;
- **#12** first token with 5 MCP servers better than the P0 baseline.

The control plane must fit inside those, not on top of them. Measured context (real Node journal and Mongo replica set, instant model, assembly on fakes): durable adds ≈ +80–90 ms to first token for plain chat and ≈ +175–215 ms for a read turn; admission is 27 ms p50 with agent access stubbed and 40–55 ms p50 / 92–117 ms p95 over HTTP, with host tails past 1 s; claim 14–20 ms; each journal commit 18–23 ms. None of that counts control-plane reads, so the control plane must reduce, not add, work on this path. The waits users feel are polling (outbox 2 s, claimer backoff to 5 s, `T_CLAIM_MS` 5 s, approval resume 1–5 s), addressed by NFR-48. Today an agent run pays its control-plane reads twice: Node admission makes 1–2 uncached KV reads plus a blocking Node → Python `GET /api/v1/agent/{key}/run-definition` (about six graph and KV reads, `H/application/run-admission-service.ts:1033-1056`, `PY/app/api/routes/agent.py:2512-2572`), and worker assembly re-reads the agent, the permission check, each toolset credential, MCP instances, approvals and the skill catalog (`PY/app/harness/assembly/tool_environment.py:182-371`, `tool_sources.py:250-255`).

The control plane's budget is the time spent in
`RunConfigPinner.pin` plus any control-plane read the runtime does before the first model call.

| Id | Mode | Path | Control-plane budget (warm cache) | Cold / cache-miss | Notes |
|---|---|---|---|---|---|
| **NFR-1** | `quick` (agents), `internal_search` / `web_search` (assistant) | durable inline | p50 ≤ 3 ms, p95 ≤ 8 ms | p95 ≤ 40 ms | The most latency-sensitive modes; resolve must not add a network hop to Python |
| **NFR-2** | `auto`, `react`, `deep` (agents) | durable | p50 ≤ 4 ms, p95 ≤ 10 ms | p95 ≤ 40 ms | Same pin as NFR-1; larger effective sets. `auto` adds its own tier-classifier LLM call, which is not control-plane time |
| **NFR-3** | `planExecute` / `verification`, Universal Agent Mode (`agent`), `image` (until each moves to the durable path) | legacy query path (`H/domain/rollout.ts:116-123`) | p50 ≤ 5 ms, p95 ≤ 12 ms added in Node; Python must **remove** its own per-request agent, toolset, MCP, skill and feature-flag reads | p95 ≤ 50 ms | Net latency must go down, not up. The rollout decision must stop costing the client a second request (today Node answers `{admission: null}` and the client re-submits on the legacy stream, `H/transport/session-controller.ts:352-357`) |
| **NFR-4** | Engine runs (`executor.kind = engine`) | durable worker | p95 ≤ 15 ms | p95 ≤ 60 ms | Dominated by sandbox start (SLO #17); the budget is for parity, not because it is user-visible |
| **NFR-5** | Child spawn, next-run drain, schedule firing | internal | p95 ≤ 15 ms | p95 ≤ 60 ms | Not user-facing at TTFT, but must reuse NFR-1's resolver |
| **NFR-6** | All modes | worker claim → first model call | **zero** synchronous control-plane reads for authority (the pin arrives with the claim); live-layer check is a local cache read, ≤ 1 ms p95 | n/a | Replaces today's live reads of approvals, LLM config, skills content and chat-mode policy (R2 F2) |
| **NFR-7** | All modes | tool-call time | live-layer and descriptor-drift check ≤ 1 ms p95 (in-process) | n/a | Must not add per-call network I/O |
| **NFR-8** | Admin writes | admin UI | p95 ≤ 300 ms for a policy or grant write including the audit append; preview/explain p95 ≤ 250 ms | — | UX: a save feels immediate |
| **NFR-9** | Builder catalog list | agent builder | p95 ≤ 300 ms for ≤ 500 items with states | — | One call, not one per kind |

**NFR-10** The resolve path performs at most **one** store round trip on a warm cache (the per-org head
revision check, §4.6 of the LLD) and at most **one batched read per store** on a miss. No N+1 over
items, agents or teams.

**NFR-11** Resolution is CPU-bounded: O(dimensions + items) with a hard cap (default 2,000 effective
items per run; a run that would exceed it is refused with `effective_set_too_large`).

### 4.2 Multi-tenancy

- **NFR-12** Every stored object carries `orgId`; every store method takes `orgId` from the
  authenticated principal, never from the request body. Contract suites include a cross-org isolation
  case for every port.
- **NFR-13** One org's corrupt or oversized document cannot degrade another org (no deployment-wide
  `{orgs:{…}}` documents; R2 F8).
- **NFR-14** Cache keys include `orgId` and the revision they were computed from; a multi-instance
  deployment never serves one org's entry to another.
- **NFR-15** Catalog items with provenance `builtin` or `release` are shared read-only across orgs; any
  org-specific state lives in installations.

### 4.3 Least privilege

- **NFR-16** Default deny: an item is usable by a run only if installed, approved, visible to the acting
  user, granted to the agent (or user), within every ceiling, and bound to a valid credential when it
  needs one.
- **NFR-17** Ceilings only narrow below the deployment layer; a widening at any layer is refused at write
  time *and* at admission (defence in depth).
- **NFR-18** A run acts as its sender ∩ the agent's grant (ARCHITECTURE §4.9); shared conversations,
  Slack and MCP surfaces never borrow another user's personal bindings.
- **NFR-19** Secrets never appear in the pin, in logs, in events or in the explain output; only binding
  references and secret digests do.
- **NFR-20** Control-plane writes require the authorizer's decision for (actor, action, resource);
  Python never authorises a control-plane write on its own.

### 4.4 Audit

- **NFR-21** `config.changed {scope, kind, objectRef, revisionFrom, revisionTo, changeDigest, actorRef,
  approvalRef?}` is appended to the per-org audit chain in the same transaction as the write (R2 CP1);
  the chain verifier covers the new type.
- **NFR-22** Run-side evidence (`run.spec_pinned` with the resolved-config digest, `interaction.granted`,
  live narrowings applied) is chained.

### 4.5 Propagation and consistency

- **NFR-23** Bounded staleness: a narrowing (disable, revoke, kill switch, approval tightening) reaches
  every new admission immediately and every running attempt's live layer within **5 s p99** with the
  notifier up, and within the live-layer TTL (**30 s**) with the notifier down.
- **NFR-24** A widening never reaches a running attempt (it applies from the next admission).
- **NFR-25** Admin read-your-writes: after a successful write, the same admin's next preview reflects
  it.

- **NFR-48** Notify, never poll, on the hot path: configuration invalidation and run hand-off are both
  driven by change notifications published after commit. Polling exists only as a bounded backstop
  (live layer 30 s, claimer safety poll) and never sits between a user's action and its first visible
  effect (R1 §2.3: outbox 2 s, claimer backoff 5 s, `T_CLAIM_MS` 5 s, approval resume 1–5 s).

### 4.6 Versioning and safe rollout

- **NFR-26** Every schema in `CP/` carries `schemaVersion` with upcasters; workers that do not understand
  a pinned schema version do not claim the run (the RunSpec v3 rule, F2).
- **NFR-27** Every mutable object has an integer `revision`; writes are compare-and-set; history is
  append-only and supports rollback.
- **NFR-28** Catalog item versions are immutable and digest-addressed; upgrades are installation changes
  subject to the approval policy.
- **NFR-29** New behaviour ships behind a rollout stage (FR-23) and with dual-read/dual-write migration
  steps that can be reverted without data loss.

### 4.7 Pluggable stores (CLAUDE.md)

- **NFR-30** Control-plane state uses ports with in-memory adapters and contract suites; production
  adapters use Mongo (document store, already required by Node) and the configured KV store
  (`KV_STORE_TYPE`: Redis or etcd) only through `KeyValueStore`/`ConfigurationService`; the notifier uses
  the configured broker or Redis through existing abstractions (`MESSAGE_BROKER`, LiveBus); graph data
  is reached only through Python's `IGraphDBProvider`. No new datastore.

### 4.8 Availability when the control plane is degraded

Decision rule: **anything that grants authority fails closed; anything that only narrows or only
presents fails open to the last known good value, with a bounded age.**

| Id | Decision | Mode when its store or cache is unavailable |
|---|---|---|
| **NFR-31** | Admission resolve with a warm cache entry whose head revision cannot be checked | Use the cached entry if younger than 30 s (bounded-stale), else **fail closed** (`control_plane_unavailable`, retryable) |
| **NFR-32** | Admission resolve with no cache entry | **Fail closed** |
| **NFR-33** | Live-layer read in the worker or gateway | Last known good live layer if younger than 30 s; else **fail closed for writes** (tools with external effects ask or deny) and **open for reads** inside the pin |
| **NFR-34** | Kill switch read | Last known value; if unknown, **closed** (no new admissions) |
| **NFR-35** | Credential lease | **Fail closed** for that item (item unavailable, run continues) |
| **NFR-36** | Audit append | Write and audit share a transaction; if the audit append fails the write fails (**closed**) |
| **NFR-37** | Notifier (change events) down | Admission unaffected (head-revision check); running work falls back to TTL polling (NFR-23) |
| **NFR-38** | Catalog import from a remote source (npm, GitHub, MCP discovery) | Install fails; existing installations unaffected |
| **NFR-39** | Explain / preview / admin list | Fail with a typed error; never fabricate a view |

### 4.9 Backward compatibility

- **NFR-40** The existing user-facing contract holds, as the SSE goldens and the surface-parity tests pin it:
  paths, bodies, SSE framing, persisted shapes, and the `AgentFormPayload` round trip.
- **NFR-41** Existing admin routes (`/admin/approvals`,
  `/admin/memory-policy`, `/admin/session-retention`, `/api/v1/toolsets/*`, `/api/v1/mcp-servers/*`,
  `/api/v1/skills/*`) stay as facades over the core for at least one release after their UI moves.
- **NFR-42** Runs pinned before the migration keep resolving: old pins (RunSpec v3 without
  `x.pipeshub.resolved`) replay with today's semantics; replay-compat corpora stay green.
- **NFR-43** Behaviour changes are listed per work package in [05-migration-plan.md](05-migration-plan.md);
  anything not listed must not change.

### 4.10 Engineering quality

- **NFR-44** Contracts are defined once as JSON Schema in `CP/` and generated into TS (zod + types) and
  Python (pydantic); no hand copies (R3 M15, D17–D19). The codegen drift test covers them.
- **NFR-45** Every port has an in-memory adapter (under test support, not production packages, R3 M14)
  and a contract suite that every adapter passes.
- **NFR-46** Layering is enforced: dependency-cruiser on Node, import-linter on Python (R2 §3.5, R3 M12).
- **NFR-47** The precedence engine is property-tested: meet is idempotent, commutative and associative;
  the result is ⊑ every ceiling; a choice lies within its ceiling; a refusal names the first widening.

---

## 5. Out of scope

- Connector (data source) configuration and sync; connectors are a separate plane. Toolsets that wrap a
  connector's API are in scope as toolsets.
- Knowledge grants (record permissions are graph-native). The agent's knowledge selection stays in the
  graph; the control plane only references it in the pin.
- A third-party plugin marketplace and code signing infrastructure beyond verifying a digest and
  a publisher signature from a configured trust root.

## 6. Open questions (with recommended answers)

| # | Question | Recommendation |
|---|---|---|
| Q1 | Which "team" is the team scope: the graph `teams` entity (membership by `permission` edges, `PY/app/api/routes/entity.py`; today used for KB permissions only) or Node user groups (Mongo `userGroup.schema.ts`)? | The graph Team for **scoping** (it is the product's "Teams" page and already carries KB permissions); user groups stay **rule principals**. Build the scope kind and precedence slot now; the `team` layer source and team grants ship in CPD-9 with a cached `MembershipPort`. |
| Q2 | Should a user be allowed to install personal MCP servers that run `stdio`? | No. Personal installs are remote-only (`streamable_http`, `sse`); `stdio` stays operator-gated (`MCP_ALLOW_CUSTOM_STDIO`) and org-scope only, run in MCP runners (P5.10). |
| Q3 | Is a per-user credential missing at admission a refusal? | No: the item is marked `unavailable{credential_missing}` in the effective set; the run proceeds without it and the UI offers "connect". Refusal only if the agent marks the item `required`. |
| Q4 | Should a widening by an admin apply to a run that is suspended waiting for approval? | No (NFR-24). It applies from the next admission. |
| Q5 | Are approval-policy changes themselves approval-gated? | Only by org admin. |
