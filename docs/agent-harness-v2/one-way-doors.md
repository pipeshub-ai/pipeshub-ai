# One-way doors — decide before writing production code

A **one-way door** is a decision that becomes expensive or impossible to reverse once data, customers, skills, prompts or integrations depend on it. Everything else is a two-way door: ship a default, measure, then change it.

Each entry has four parts:

- **Decision** — what has to be settled.
- **Why it is one-way** — what makes it hard to undo.
- **Recommendation** — the proposed choice.
- **Keep flexible** — the parts of the area that can stay changeable.

Each entry has an ADR under [adr/](./adr/), linked from its `**Status.**` line.

---

## OWD-1 — Secrets never enter the sandbox

**Status.** Accepted. Built for engine sandboxes with the SIGNIN exception; since WP-06 the legacy `run_code` registry and database credentials and MCP-runner secrets are injected by the egress proxy (`backend/python/harness_core/sandbox/egress/sealed.py`, `interception.py`, `postgres.py`; `backend/python/tests/unit/harness_core/sandbox/egress/test_wp06_injection.py`). ADR: [ADR-OWD-01](adr/ADR-OWD-01-secrets-outside-sandbox.md).

**Decision.** No secret is ever present in a sandbox's filesystem, environment or memory. That covers:

- model API keys
- connector OAuth tokens
- git credentials
- PipesHub PATs or session tokens

**Why it is one-way.**

- Once harness configs, skills and user scripts expect `OPENAI_API_KEY=sk-…` or a token in env, removing it breaks them.
- Every log, snapshot and transcript taken while secrets were inside is contaminated forever.
- Retrofitting gateways changes every network path.

**Recommendation.**

- Route all outbound traffic through an egress proxy that injects credentials.
- Use a Model Gateway for model calls and a Tool Gateway for PipesHub and connector actions.
- Inside the sandbox, the harness sees placeholder keys and base URLs that point at the proxy.
- The proxy binds the session credential to the sandbox identity (network source, or a vsock/Unix socket), so a copied placeholder is useless outside.
- The setup phase may receive scoped secrets (for example, a read-only clone token), but they are wiped before the agent phase. Snapshots are only taken after the wipe.

**Keep flexible.** The proxy implementation (Envoy, mitmproxy or custom), and which domains get TLS interception.

**Exception (2026-10-26, see OWD-17).** When an admin turns on Claude sign-in, Claude Code's own login token lives in that user's own sandbox for their own interactive turns. PipesHub never collects, stores or relays it. No other secret is excepted.

## OWD-2 — Identity and delegation model ("on whose behalf")

**Status.** Accepted and built. Principal, grant narrowing and argument distrust as before; WP-07 added `principalKind`, `initiatorRef` and `onBehalfOf` on the admitted run and its `run.admitted` audit record (from verified auth only, `domain/principal.ts`), a child keeping its parent's, and the service-agent label in the chat. Tests: `backend/nodejs/apps/tests/modules/agent-harness/owd2-run-attribution.test.ts`, `backend/nodejs/apps/tests/modules/agent-harness/child-runs.http.test.ts` "OWD-2 / SEC-AZ-04: a child cannot widen through admission". ADR: [ADR-OWD-02](adr/ADR-OWD-02-identity-and-delegation.md).

**Decision.** Settle three things:

- who a session acts as
- how that identity flows to every tool call
- how it appears in audit

**Why it is one-way.**

- Audit records, ACL semantics, approval routing and the permission edges on artifacts all encode this model.
- Changing it later re-interprets historical audit data and silently changes who can see what.

**Recommendation.**

- Every session has exactly one **principal**: a user, or an agent service account (the existing `isServiceAccount` agents with `phsvc_` tokens). It also records an **initiator** (the human or trigger that started it) and an **agent** identity.
- Every tool call is attributed as `(org, principal, initiator, agent, session, harness@version)`.
- Retrieval always runs as the principal. Service-account principals must be visibly labelled in the UI and audit.
- Never use OAuth `client_credentials` to act as a user (an existing rule in AGENTS.md).
- Never trust org or user IDs in tool arguments.
- Child sessions inherit the principal and can only narrow scope, never widen it.

**Keep flexible.** UI wording, and which roles may approve.

## OWD-3 — Isolation boundary and sandbox tenancy

**Status.** Accepted. gVisor floor and no-reuse built and tested on runc; ⏸ gVisor and Kata runs deferred to the end. ADR: [ADR-OWD-03](adr/ADR-OWD-03-isolation-and-tenancy.md).

**Decision.** Settle three things:

- what the security boundary is
- what may share a sandbox
- whether sandboxes are reused

**Why it is one-way.** Customers and auditors will be told "each session runs in its own isolated VM/sandbox". Weakening that later is a security regression, and strengthening it later forces changes to snapshots, volumes and performance assumptions.

**Recommendation.**

- **One sandbox per session per principal.** A pooled sandbox is claimed once and never reused.
- The **outer** sandbox is the boundary: gVisor or a microVM (Kata or Firecracker). Plain runc is allowed only behind an explicit "dev/insecure" flag.
- The harness's own sandbox is defence in depth, never the boundary.
- Persistent workspaces are volumes or snapshots owned by one principal and attached only to that principal's sessions.

**Keep flexible.**

- gVisor vs Kata as the default per deployment target. Decide this after the Phase 0 I/O benchmark.
- Which providers are supported.

## OWD-4 — Canonical session event schema and transcript storage

**Status.** Accepted, with PipesHub's typed taxonomy and `runId` as accepted divergences; the verbatim native stream lands in WP-03. ADR: [ADR-OWD-04](adr/ADR-OWD-04-event-envelope-and-storage.md).

**Decision.** Define the versioned event envelope and taxonomy (README §4.11), and decide where transcripts live.

**Why it is one-way.** The following all consume the schema:

- persisted transcripts
- the UI
- audit
- cost ledger
- the public API
- SSE resume (`Last-Event-ID`)

Historic sessions must stay replayable for years for compliance.

**Recommendation.**

- **Envelope fields:** `{v, session_id, seq, turn_id, ts, source, type, parent, payload}`.
- **Versioning:** additive-only evolution within a version. Breaking changes bump `v`, and readers must handle all historical versions.
- **Event types:** modelled on ACP `session/update`, the de facto harness-normalization standard. The browser keeps AG-UI as a projection.
- **Event store:** append-only. Session index and events in Mongo; native harness transcripts and large payloads in blob storage via `StorageServiceInterface`.
- **Never the graph DB** for event streams. The graph DB holds only the session ↔ agent/artifact/user relationships needed for permissions and lineage.

**Keep flexible.** UI rendering, projection code, retention durations.

## OWD-5 — Session as a durable, harness-neutral resource (and its state machine)

**Status.** Accepted. The durable unit is a run per turn and the conversation is the session (accepted divergence); the SL-05 table test lands in WP-04. ADR: [ADR-OWD-05](adr/ADR-OWD-05-durable-run-and-session.md).

**Decision.** Settle three things:

- the session resource model
- its lifecycle states
- resume semantics

**Why it is one-way.**

- Public API clients, schedules and triggers, `HarnessAgentRunner`, billing and UI all depend on these states and their transitions.
- Changing "a session is a request" into "a session is durable" later is a rewrite. Today every agent run is request-scoped SSE, and that is exactly the wall to avoid.

**Recommendation.**

- Sessions are durable and independent of any HTTP request. They follow the state machine in README §4.1.
- Turns are idempotent by client message id.
- A **checkpoint** is the tuple (native session ref, transcript blob, workspace snapshot or volume, git commit, last acked `seq`).
- Tool side effects use idempotency keys `(session, turn, call_id)`, so replay never repeats an action.
- The **PipesHub native agent runtime becomes one harness kind** behind the same session resource. Otherwise there will be two of everything: two UIs, two APIs and two audit trails.

**Keep flexible.** Whether durability is implemented with a home-grown state machine (Mongo + KV leases + MessagingFactory, like indexing) or with Temporal/DBOS. Hide it behind the orchestrator interface.

## OWD-6 — The `/pipeshub` filesystem contract and the `ph` CLI surface

**Decision.** Fix the mount point, directory layout, file formats and CLI verbs that are exposed inside sandboxes.

**Why it is one-way.**

- Skills, AGENTS.md, user prompts and agent-written scripts will hard-code these paths and commands.
- Agents also learn them in context.
- Renaming them breaks every saved skill and published agent.

**Recommendation.**

- **Mount point:** `/pipeshub` with a `VERSION` file.
- **Layout:** as in README §6.
- **Formats:**
  - tool schemas are JSON Schema 2020-12 files (`<tool>.schema.json`)
  - skills follow the agentskills.io `SKILL.md` standard (already used by `bundle.py`)
- **CLI:** `ph` with noun-verb commands and a `--json` output mode.
- **Change policy:** additive only within a contract version. Add aliases rather than rename.

**Keep flexible.** The content of `AGENTS.md` and `INDEX.md` (prompt tuning), and the generated SDK internals.

**Status.** Contract 1 is described in [guides/pipeshub-contract-reference.md](guides/pipeshub-contract-reference.md) (WP-02). It is enforced by `harness_engines/pipeshub` (`install.sh`, `pipeshub_tool_folder`, `ph.py` 2.1.0 since round 15) and by `ImageContract` in `harness_core/engines/tool_folder.py`, which refuses an image with an unknown `VERSION`. Tests: `tests/unit/harness_engines/pipeshub/test_contract_v1.py`, `test_ph_cli.py` (with the `--json` golden), and D `test_recs_audit_real_engine::test_r3_*`. `knowledge/` was added without a version change (WP-12: over-cap knowledge results, `harness_engines/pipeshub` `knowledge_shelf`, `tests/unit/harness/gateway/test_wp12_knowledge_shelf.py`). Deviations, each additive later: no `session.json`, `memory/` or per-toolset README; the generated SDK (WS-14, WP-57) is in the session folder at `/pipeshub/session/sdk/`, and the `/pipeshub/sdk` link waits for the next engine image; `inputs/`, `outputs/` and `workspace/` are links into `/workspace`; `ph skill` reads the run's skills folder (SK-05); `ph ask` and `ph publish` (WP-67, C3.12) are in the worker's `ph` (`session/ph.py`) and reach the image's `/usr/local/bin/ph` with the next engine image. Additive only is enforced by WS-20 (WP-92: `harness_engines/pipeshub/surface.py`, golden `contract_v1_surface.json`); the contract is the engine sandbox's (a loop run has none, WP-92 decision). ADR: [ADR-OWD-06](adr/ADR-OWD-06-pipeshub-contract-and-ph.md).

## OWD-7 — Tool identity, naming and catalog-pinning semantics

**Status.** Accepted. WP-22: the risk classes (`destructive`, `financial`) are in the tool metadata contract; an engine's gateway tool list is pinned per conversation, as the loop's; spec-form model-facing names (internal ids kept) are built at the engine gateway and off per engine until each engine is measured on them (`EngineCapabilities.spec_tool_names`). ADR: [ADR-OWD-07](adr/ADR-OWD-07-tool-identity-and-pinning.md). "Mask, don't remove" for missing credentials is enforced since WP-11: `UnconnectedMcpTool` (`app/harness/assembly/needs_authentication.py`) and the `pipeshub/auth` mark in `app/harness/gateway/mcp.py`; tests `test_qa23_credential_availability::test_ws09_*`, `harness/gateway/test_ws09_needs_authentication.py`.

**Decision.**

- How tools are named across PipesHub toolsets, external MCP servers and dynamic tools.
- How the catalog is versioned within a session.

**Why it is one-way.**

- Tool names appear in persisted transcripts, approvals ("approve `gmail.send` for this session"), policies, audit, skills and replay goldens.
- Prompt caches in Claude, Codex and DeepSeek require a byte-stable tool prefix.

**Recommendation.**

- **Stable IDs:** `pipeshub_<toolset>_<tool>` (MCP-safe characters), with `<toolset>.<tool>` as the canonical dotted ID in `ph` and policies.
- **External MCP tools** are prefixed with the server instance slug.
- **Catalog pinning:** the catalog is pinned per session (`catalog_hash`) and ordered deterministically. Changes apply only at the next session or on an explicit refresh event.
- **Mask, don't remove:** an unavailable tool stays listed and returns an actionable error (for example "needs authentication" or "denied by policy").
- **Risk class** (read, write_internal, write_external, destructive, financial) is part of the tool metadata contract.

**Keep flexible.** Which tools are direct and which are lazy (the existing `tool_disclosure` setting), descriptions, and examples.

## OWD-8 — Model traffic always goes through a PipesHub Model Gateway

**Status.** Accepted, with the SIGNIN exception. Since WP-06 every `vendor_injected` row decides `prohibited` by a code floor (`GATEWAY_BYPASS_ROUTES`, `backend/python/harness_core/access/vendor_rules.py`); the signed rows are re-signed after the credential rotation. Since WP-16 the gateway ledger is the authority on usage: a turn's usage is the run's ledger `used` over the turn, never the engine's own report (`harness_core/engines/usage.py`; `tests/unit/harness_core/engines/test_cf13_ledger_usage.py`, real opencode over ACP in `test_real_acp_controller.py::test_rxacpc3_*`). ADR: [ADR-OWD-08](adr/ADR-OWD-08-model-gateway.md).

**Decision.** Harnesses never call model providers directly, even when a user has their own key.

**Why it is one-way.** If some harnesses talk to providers directly, then:

- metering, budgets, placement, audit and key custody all fork
- users configure keys inside harnesses
- that can't be taken back without breaking their setups

**Recommendation.**

- **Mandatory gateway** supporting:
  - Anthropic Messages
  - OpenAI Responses
  - Chat Completions
  - Gemini (wire formats)
- **Faithful passthrough:**
  - pass reasoning and thinking blocks through faithfully
  - pass cache controls through and report cache usage
- **Model aliasing:** model aliases are mapped to the org's configured models.
- **BYO keys:** BYO keys are stored in PipesHub and used by the gateway.

**Keep flexible.**

- Build vs adopt (LiteLLM or extending `agent_loop_lib/transport/*`).
- Routing policies and fallbacks.

**Exception (2026-10-26, see OWD-17).** A run that uses an admin-enabled Claude sign-in sends its model traffic from the sandbox to Anthropic under the user's own plan, not through the Model Gateway. That usage is not metered by the gateway. Every other route keeps going through the gateway.

## OWD-9 — Policy model: risk classes, taint labels and approvals as protocol-level concepts

**Status.** Accepted, with labels (D15) for taint; `allow_with_redaction` built in WP-22 (the call runs unasked and its result is redacted before the model reads it; `harness_core/effects/redaction.py`, `test_wp22_allow_with_redaction.py`). A denial's reason reaches the harness and the engine's approval lines carry an effect preview (WP-10: `interaction.resolved.answerRef`, `harness_core/effects/preview.py`; `test_wp10_gateway_hitl.py`). ADR: [ADR-OWD-09](adr/ADR-OWD-09-policy-and-approvals.md).

**Decision.** Approvals and policy decisions are first-class events and states, not text conventions.

**Why it is one-way.**

- The approval event shape, approval scoping ("once" / "this session" / "pattern") and the taint semantics get encoded in the UI, the public API, Slack integrations, audit and compliance reports.
- Adding taint tracking after action tools ship means re-classifying everything.

**Recommendation.**

- Decisions are `allow | deny | ask | allow_with_redaction`, each with a `reason`.
- Session taint labels (`private_data_read`, `untrusted_content_read`, `external_comm_capable`) are set by the gateways.
- Default org policy follows the Rule of Two.
- Approval requests carry a concrete effect preview, and decisions are audited and immutable.

**Keep flexible.** Policy rules and defaults per org, and the policy engine implementation (in-house rules vs OPA/Cedar).

## OWD-10 — Where the runtime lives and which store owns what

**Status.** Accepted, with leases in Mongo (accepted divergence). Built and tested. ADR: [ADR-OWD-10](adr/ADR-OWD-10-runtime-placement-and-stores.md).

**Decision.** Settle which service hosts sessions, gateways and the event store.

**Why it is one-way.**

- Ports, Helm charts, Compose files, network policies, auth paths and OpenAPI paths all get published.
- Moving the Tool Gateway between Node and Python later changes its auth and network topology.

**Recommendation.**

- **A new Python service, `app.harness_main`,** hosts the orchestrator, Tool Gateway, policy and approvals. Toolsets, retrieval, the MCP client and the sandbox code already live in Python.
- **Node stays the public edge:**
  - auth
  - rate limits
  - public REST/SSE proxy under `/api/v1/harness/...`, which must be added to `pipeshub-openapi.yaml`
- **The Tool Gateway is internal-only.** It is reachable from sandboxes solely via the egress proxy.
- **The Model Gateway** is a separate deployable, so it can scale with token traffic.
- **Store ownership:**
  - Mongo for sessions and events
  - blob for transcripts and snapshots
  - graph for agent ↔ session ↔ artifact permission edges
  - KV for leases and cancel flags
  - MessagingFactory for triggers and cross-service events
- **Abstractions:** use the existing abstractions throughout, as AGENTS.md requires.

**Keep flexible.** Replica counts, and whether the Model Gateway is co-located.

## OWD-11 — Harness adapter contract and the normalization protocol

**Status.** Accepted, with no in-sandbox `sandboxd` and re-drive instead of re-attach (accepted divergences). Built and tested; the CH-12 partition row (FM-11, `backend/python/tests/unit/harness/engines/test_p9_fault_matrix.py::test_fm11_ch12_worker_sandbox_partition`) passes (WP-20). ADR: [ADR-OWD-11](adr/ADR-OWD-11-adapter-contract.md).

**Decision.**

- The adapter interface: capabilities, lifecycle verbs and event mapping.
- The protocol between `sandboxd` and the orchestrator.

**Why it is one-way.**

- Every adapter, the conformance suite and the in-sandbox daemon depend on it.
- Customers may write their own adapters if a plugin API is exposed.

**Recommendation.**

- **Normalize on ACP semantics.** ACP is the emerging standard (Zed, JetBrains registry, native in Gemini/OpenCode/Goose/Cursor, adapters for Claude/Codex).
- **Use native protocols where ACP loses fidelity:**
  - Claude Agent SDK / stream-json for hooks, `canUseTool`, the subagent tree and cost
  - Codex app-server for steer, fork and typed approvals
- **Capabilities** are declared explicitly, and the UI and policy degrade gracefully when one is missing.
- **Third-party adapter plugins** stay internal-only until the contract has survived at least 5 harnesses.

**Keep flexible.** The daemon's implementation language, and the transport (attach/exec stdio vs WebSocket).

## OWD-12 — Run placement, tenancy keys and retention

**Status.** Accepted. Keys, retention and erasure built; the region on every object and the 7-day snapshot TTL built (WP-23); region placement and model-call placement are extension points with no default in this build (`RunPlacement`, `ModelPlacementPort`); retention default no expiry. ADR: [ADR-OWD-12](adr/ADR-OWD-12-placement-keys-retention.md).

**Decision.** Settle where sandboxes, snapshots, transcripts and model calls physically run and are stored, and how they are encrypted and retained.

**Why it is one-way.**

- Snapshots and transcripts contain private enterprise data.
- Once they are written to the wrong region or under a shared key, remediation means deletion and incident handling.

**Recommendation.**

- Every stored object carries `org_id` and region.
- Use per-tenant encryption keys for transcripts and snapshots.
- Apply retention policy per tenant, and make deletion verifiable.
- Sandbox placement respects the tenant region.
- Snapshot TTLs default short (for example, 7 days) unless the org configures otherwise.

**Keep flexible.** Retention defaults, KMS provider.

## OWD-13 — Public API surface and naming

**Status.** Accepted as amended (2026-10-26) and built to the amended design: conversation = session, run = turn, interaction = approval or question, on the existing chat API under `/api/v1`. Enforced by the route-vs-OpenAPI drift test `backend/nodejs/apps/tests/cohesion/openapi-harness-routes.test.ts` (WP-19, API-01), which builds the routes from `app.ts` and fails on a route missing from `pipeshub-openapi.yaml`, a documented operation with no route, or wrong path parameters. ADR: [ADR-OWD-13](adr/ADR-OWD-13-public-api-naming.md).

**Decision.** Set the resource names and paths: `/api/v1/harness/sessions`, harness profiles, approvals, triggers.

**Why it is one-way.** Integrators, the Slack bot, CI users and MCP/A2A exposure will depend on them.

**Recommendation.**

- Use **session**, **turn**, **event**, **approval**, **harness profile** and **trigger**.
- Avoid "ControlPlane", which collides with `agent_loop_lib.control_plane.ControlPlane`.
- Version under `/api/v1`.
- Document everything in `pipeshub-openapi.yaml` from day one (a mismatch is a blocking review issue).

**Keep flexible.** UI labels.


**Amendment (2026-10-26).** The harness uses the existing chat API, per the quality bar, so the resource names are:
- **conversation** = session;
- **run** = turn;
- **interaction** = approval or question.

The routes:
- conversations at `/api/v1/conversations/{id}/runs…` and `/api/v1/agents/{key}/conversations/…`;
- admin at `/api/v1/agent-harness/*`.

What stays from the original decision:
- versioning under `/api/v1`;
- every route documented in `pipeshub-openapi.yaml`, where a mismatch is blocking. A test fails if the routes and the OpenAPI file drift apart.

A `/api/v1/harness/sessions` alias is added only if an integrator needs it; it would be additive and non-breaking.
---

## Additions from gap review

See [gaps-and-additions.md](./gaps-and-additions.md) for context.

## OWD-14 — Agent versioning and what sessions/triggers pin

**Status.** Accepted and built in WP-21: immutable published versions plus a draft, rollback by re-publishing, runs and schedules pin a version number, every run records its `agentVersion`, existing agents migrated to version 1 (GA-01). Tests: `backend/python/tests/unit/agents/test_owd14_agent_versions.py`, `backend/python/tests/unit/api/routes/test_ga01_agent_version_routes.py`, `backend/nodejs/apps/tests/modules/agent-harness/owd14-agent-versions.test.ts`. The builder UI is a mock, not built. ADR: [ADR-OWD-14](adr/ADR-OWD-14-agent-versioning.md).

- **Decision:** whether agent definitions are mutable in place (today) or immutable versions.
- **Why one-way:** schedules, audit ("which instructions acted?"), replay goldens, and staged rollout all reference a version; adding versions after triggers exist forces a migration of every trigger and loses historical attribution.
- **Recommendation:** immutable published versions + drafts; sessions record `agent_version`; triggers pin a version (explicit opt-in to "latest published"); rollback = re-publish an old version.
- **Keep flexible:** UI for diffing versions; canary percentages.

## OWD-15 — Data classification as a first-class policy input

**Status.** Accepted as amended (2026-10-29). This repository holds only the extension point, with no default: a run carries an opaque classification and asks only whether one is configured (`ClassificationPort`, `SourceMetadataReader`, `HarnessEdition.classification`); with none, a classification changes no decision. ADR: [ADR-OWD-15](adr/ADR-OWD-15-data-classification.md).

- **Decision:** whether the taint/policy model carries a record classification field alongside the boolean taint labels.
- **Why one-way:** model-allowlists, egress, snapshot and retention rules keyed on classification cannot be applied retroactively to transcripts and snapshots already written without it.
- **Recommendation:** the labels carry the field from the start, and an unlabelled record never counts as "public"; what reads it is an extension point.
- **Keep flexible:** what an implementation stores in the field.

## OWD-16 — Workspace ownership and sharing

**Status.** Accepted and built for v1. Each principal of a session has its own workspace (`ws:{sessionId}` the owner's, `ws:{sessionId}:p:{actorId}` a collaborator's, forks under it); Node's workspace routes refuse any other (`domain/workspace.ts` `workspaceOfPrincipal`), WP-07. Tests: `backend/python/tests/unit/harness/assembly/test_owd16_principal_workspace.py`, `backend/nodejs/apps/tests/modules/agent-harness/owd16-principal-workspaces.test.ts`. Project service-account workspaces wait for a project principal (C20.23, P2). ADR: [ADR-OWD-16](adr/ADR-OWD-16-workspace-ownership.md).

- **Decision:** whether persistent workspaces can be shared across principals (project/team workspaces) or are strictly per principal.
- **Why one-way:** OWD-3 promises per-principal isolation; a shared workspace mixes data fetched under different ACLs. Once teams depend on shared workspaces, reverting breaks them; allowing it carelessly is a data-leak class.
- **Recommendation:** v1: per-principal workspaces only. Shared project workspaces only under a project **service-account principal** whose ACL is the project's, never an individual's; personal data fetched by a user cannot be written into it without explicit export.
- **Keep flexible:** quotas, GC policy.

## OWD-17 — Vendor subscription logins inside harnesses

**Status.** Accepted as amended (2026-10-26). Built, off by default; GA-23 passes (`backend/python/tests/unit/harness/security/test_ga23_vendor_logins_refused.py`). ADR: [ADR-OWD-17](adr/ADR-OWD-17-vendor-subscription-logins.md).

- **Decision:** whether users may authenticate harnesses with personal vendor subscriptions (Claude Max/Pro, ChatGPT plans).
- **Why one-way:** supporting it puts long-lived vendor OAuth tokens in sandboxes (violates OWD-1), bypasses metering/placement (OWD-8), and raises ToS questions; once users rely on it, removal is a visible regression.
- **Recommendation:** not supported in v1; images disable harness login flows; all model access via the Model Gateway with org-managed keys. Revisit only for vendor-sanctioned delegated auth that the gateway can hold.
- **Keep flexible:** n/a until revisited.
- **Amendment (2026-10-26; SIGNIN of 2026-10-18):**
  - **Allowed:** one narrow exception, Claude Code's own, unmodified login, run inside the user's own sandbox. PipesHub never collects, stores or relays the token.
  - **Limits on the exception:** admin-gated and off by default; work plans only, with consumer Pro/Max a separate opt-in; only the signed-in user's own interactive turns.
  - **Off until Anthropic answers:** the admin default stays off until Anthropic answers the drafted terms questions.
  - **Still blocked:** every other vendor subscription login (ChatGPT/Codex, Gemini, others). Conformance (GA-23) checks that they are refused.
  - **Knock-on changes:** OWD-1 and OWD-8 record the matching exceptions.

## OWD-18 — Identity and AI disclosure on external writes

**Status.** Accepted. The AI disclosure built (WP-23) with its wording per action type (WP-90); the identity per action type is an extension point (`WriteIdentityPort`, WP-132), and this build writes as the user; commit signing open (its bot key waits on the credential rotation, OB-1). ADR: [ADR-OWD-18](adr/ADR-OWD-18-write-identity-disclosure.md).

- **Decision:** how commits, emails, messages, tickets created by agents are attributed in downstream systems.
- **Why one-way:** downstream systems (git history, mail archives, Jira audit) keep this forever; changing it later leaves inconsistent historic attribution.
- **Recommendation:** org-configurable per action type: act-as-user with disclosure footer/header ("via PipesHub agent X, session link"), or bot identity with `Co-authored-by: <user>`. Git commits signed with a bot key held outside the sandbox. Every external write carries `session_id` where the target supports metadata.
- **Keep flexible:** disclosure wording.

## OWD-19 — Multi-participant session semantics

**Status.** Accepted, with the principal fixed per run (stricter). Built and tested. ADR: [ADR-OWD-19](adr/ADR-OWD-19-multi-participant-sessions.md).

- **Decision:** whether non-principal users can steer/approve a session, and what data access they get.
- **Why one-way:** interacts with OWD-2; if a steerer could direct an agent running with someone else's access, that is privilege escalation baked into the API.
- **Recommendation:** principal is immutable per session; participants get explicit roles (viewer/commenter/steerer/approver); steering messages are attributed and labelled; policy may require steerers to have access ≥ the session's classification; "take over" = fork into a new session under the new principal.
- **Keep flexible:** role names, UI.

---

## Two-way doors (decide fast, revisit with data)

These can be decided quickly and changed later:

- Which harness ships second (Codex vs OpenCode).
- Docker+gVisor vs Kata default per environment, chosen with benchmark data.
- Model Gateway build vs adopt, as long as it sits behind the OWD-8 contract.
- Durable-execution engine, as long as it sits behind the orchestrator interface.
- Daemon language.
- Warm-pool sizes, TTLs and idle timeouts.
- Content of `AGENTS.md`, tool descriptions, `response_format` defaults and truncation limits.
- Observability backend: Opik is already wired, and OTel export keeps this open.
- UI layout.

## Cheap insurance to add now (costs little, saves rewrites)

- Put `seq` and `v` on every event from the first commit.
- Put idempotency keys on every tool call and every user message from the first commit.
- Record `harness@version`, `catalog_hash`, `policy_version` and `image_digest` on every session.
- Store native harness transcripts verbatim alongside the canonical events, so a re-parse is possible when adapters improve.
- Emit audit records from the gateways, not from the harness, because harness self-reporting can't be trusted.
- Reserve `/pipeshub/VERSION` and `ph --version`, and have the materializer refuse to mount an unknown contract version.

