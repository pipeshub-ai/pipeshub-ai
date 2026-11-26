# Control plane: bottom-up verification

**Status:** design proposal. **Date:** 2026-10-09. Prefixes as in [00-requirements.md](00-requirements.md).

[00-requirements.md](00-requirements.md) to [03-interfaces.md](03-interfaces.md) went top down. This file
goes the other way. It places every existing component from the R2 inventory in the layer it serves,
checks that each layer gives the layer above exactly what that layer needs, and then traces four (plus
two) end-to-end scenarios through every layer. Gaps, mismatches and deletions are marked:

- **G** gap: something the layer above needs that nothing below provides yet;
- **M** mismatch: an existing piece that serves the need with the wrong shape or semantics;
- **D** delete: made redundant by the design.

Layers, bottom up:

| Layer | Contents |
|---|---|
| L0 Data model | `contracts/harness/core/policy/*`, `contracts/control-plane/*`, generated TS, Python and frontend types, vectors |
| L1 Stores | `CatalogStore`, `InstallationStore`, `GrantStore`, `PolicyStore`, `BindingStore`, `ApprovalStore`, `ControlPlaneTransaction`, vault, audit chain |
| L2 Resolvers and services | `PolicyResolver`, `EffectiveSetResolver`, `RunConfigPinner`, snapshot cache, core services, `Authorizer`, `ChangeNotifier`, domain modules |
| L3 APIs | `/api/v1/control-plane/*`, facades, existing chat routes (`ChatTurnService`), internal routes |
| L4 Runtime consumers | Node admission (submit, child spawn, next-run drain, schedules, Slack, MCP), durable worker loop kernel, engine kernel, tool gateway, model gateway, egress proxy, legacy query path |
| L5 UI | Workspace capabilities area, agent builder, chat, My connections, approvals inbox, audit |

---

## 1. Every inventoried component mapped to its layer

### 1.1 Harness knobs (R2 §1.1)

| Component today | Evidence | Layer | Disposition |
|---|---|---|---|
| Env flags (`REPLICA_SET_AVAILABLE`, relay, worker, `SANDBOX_*`, `MCP_*`, `EGRESS_*`, `HARNESS_*`) | `H/config.ts:29-59`; FLAGS.md | L0 (deployment layer schema) + L2 (`DeploymentPolicyLoader`) | Adapt: typed deployment layer built at boot; env stays the operator interface |
| `AGENT_HARNESS_OFFERING` (late throw) | `H/application/model-access-service.ts:56-77`; `PY/app/harness/access/offering.py:17` | L0 + L2 | **M**: validated at boot (CP0); duplicate in Python **D** (generated constant) |
| Worker features `HARNESS_CONTEXT_COMPILER`, `_MODEL_POLICY`, `_PROVIDER_STATE`, `AGENT_HARNESS_BUDGET_LEDGER` (live, not in the spec) | `PY/app/harness/assembly/context_compiler.py:20`, `model_policy.py:87`, `model.py:189,193` | L0 `rollout` dimensions → pinned | **M** → staged rollout pinned per run (R2 CP8) |
| `HARNESS_FLOW_GUARD*` (live per attempt) | `PY/app/harness/approvals.py:132-164` | L0 `approvals.flow` (deployment floor) → pinned | **M** → pinned (R2 CP6) |
| `MCP_ALLOW_CUSTOM_STDIO`, `PIPESHUB_*` skills and transport env | `PY/app/agents/mcp/stdio_policy.py:21`; `skills_wiring.py:154,209` | L0 operator-only rows | **G** not in `flags.json` (CP0) |
| Compile-time constants: budgets, quotas, memory caps, child depth, deadlines, schedule limits | `H/domain/run-spec.ts:44-49`, `admission-quotas.ts:8`, `memory.ts:38-46`, `child-runs.ts:40-42`, `schedules.ts:90-113` | L0 `release.json` | Copies **D** (R2 F10) |
| Labs flags (no CAS, no audit) | `N/modules/configuration_manager/controller/cm_controller.ts:869-907` | L1 `PolicyStore` (org `rollout`) + facade L3 | **M** → CAS + audit (R2 CP1) |
| `agentHarness.modelFamilies` (malformed → allow all, FM-F7) | `H/domain/rollout.ts:98-137` | L0 `models.families` ceiling | **M**: schema-validated; invalid → refused write |
| Approvals document (live per call in Python) | `H/application/approval-settings.ts:30`; `PY/app/harness/approvals.py:111-129` | L1 `PolicyStore` + L2 pinned | Merge; class **D** after facade window |
| Engine profile ceiling | `H/application/engine-profile-settings.ts:20` (moved out of this repository; `H/ports/engine-profiles.ts` is the port) | L1 `PolicyStore` (`executor`) | Merge; class **D** |
| Model access (`disabledRules`, `consumerPlans`; live at gateway) | `H/application/model-access-policies.ts:16` (moved out of this repository; `H/ports/model-access.ts` is the port) | L1 + L2 pinned; live layer narrows | Merge; class **D** |
| Memory policy | `H/application/memory-settings.ts:17` | L1 | Merge; class **D** |
| Session retention (no FE page) | `H/application/session-retention-settings.ts:15` | L1 + L5 page | Merge; **G** FE page (CP9) |
| Model accounts vault | `H/adapters/configuration/model-account-vault.ts:60-97` | L1 vault (unchanged), bindings of mode `model_account` | Keep; referenced by bindings |
| AI models config (`modelKey` re-read every attempt) | `PY/app/harness/assembly/model.py:136-152` | L2 snapshot → pinned `llm` | **M** → pinned snapshot (R2 F2) |
| MCP manifest approval, runner settings | `PY/app/api/routes/mcp_hub.py:188-268` | L1 `InstallationStore.approved` + L2 `ApprovalWorkflow` | Merge; route → facade |
| Agent definition capability fields | `N/modules/enterprise_search/validators/es_validators.ts:651-654`; `PY/app/api/routes/agent.py:940,1168,1528` | L1 `GrantStore` + agent `PolicyStore` layer | Move; edges **D** after window; Node hard-coded `AGENT_CREATE_TOOLSET_NAMES` **D** (validated against the catalog instead) |
| Schedules (`maxCostUsd` ≤1000 vs ≤5) | `H/transport/schedule-routes.ts:46-106`; `H/domain/schedules.ts:293` | L0 `schedules` ceiling; trigger layer | **M** → one bound (R2 CP7) |
| Run request fields | `H/transport/schemas.ts:636-677` | L2 request layer | Adapt: request becomes a `PolicyLayer` |
| Child grant ⊆ registry | `H/domain/child-runs.ts:160-314` | L2 `parent` layer + `PolicyResolver` | Generalise; `childGrants` subset logic **D** (R2 §3.2) |
| `interaction.granted` (not chained) | `H/domain/audit.ts:93-134` | L1 audit | **G** chain it (CP1) |

### 1.2 The five domains (R2 §1.2)

| Component today | Layer | Disposition |
|---|---|---|
| `SkillMetadata`/`Skill` models; `GraphSkillStore`; `FilesystemSkillStore` (unused in prod) | L1 skill driver (content) + L1 `CatalogStore` (catalog rows) | Keep graph store for content; `FilesystemSkillStore` **D** |
| `builtin_seeder.py` per-org copies | L0 `CP/catalog/skills.json` | **D** (release catalog shared across orgs) |
| Skill importer (`package_importer.py`), `skill_manage` tool, learning loop | L2 skills `CatalogSource.importItem`; candidates → `ApprovalWorkflow` | Adapt |
| Skill candidate approval by any `SKILL_WRITE` user | L2 `ApprovalWorkflow` | **M** (CPS fixes in place first) |
| Runtime `SkillManager` with `visibility_scope=None` | L4 `SkillsItemRuntime` | **M** (CPS fixes in place first; target loads only pinned digests) |
| `SkillsSource` live content; empty `run.skills` = full catalog | L4 `SkillsItemRuntime` | **M** → pinned digests only (CPS + CPD-7) |
| Engine `skill_bundles_for` not wired | L4 engine kernel | **G** (CPS) |
| `@mcp_server` templates, `MCPRegistry` | L0 `CP/catalog/mcp.json` | Adapt (generated at build) |
| MCP instances `/services/mcp/instances/{id}` (path disagreement `models.py:123`) | L1 `InstallationStore` | Migrate; **M** path |
| MCP credentials, OAuth + DCR, `McpSecretSource`, refresh service | L1 vault + `BindingStore`; L2 `TokenRefresher` | Merge; refresh service **D** |
| `MCPToolProvider.load_into`, hub sessions, schema cache | L4 `McpItemRuntime` | Keep, wrapped |
| Node MCP pin `manifestDigest = digest(names)` | L0 digest scheme | **M** (CPS fixes; target `descriptorSetDigest`) |
| `@Toolset` + `ToolsetRegistry` | L0 `CP/catalog/toolsets.json` (descriptors with schema) + L4 execution | Adapt (CPD-2) |
| Toolset instances in one KV list (RMW race) | L1 `InstallationStore` | Migrate; list **D** |
| Toolset credentials, OAuth app per type, refresh service | L1 vault + `BindingStore`; L2 `TokenRefresher` | Merge; refresh service **D** |
| Node toolsets router: 0 `requireScopes`, probable dead routes `:311-415` | L3 facade + `Authorizer` | **M** (CPS adds scopes); dead routes **D** |
| `ToolsetSource.observe` returns `{}`; `descriptorDigest(toolId, fullName)` | L4 `ToolsetItemRuntime.verify`; L0 digest | **M** (CPS fixes) |
| `PluginRegistry` (unused) | — | **D** |
| Engine plugins switched off at launch | L4 engine adapters | Keep (FR-34) |
| Harness release data: `allowlist.json`, signed vendor rules, `flags.json` | L0 | Keep; `allowlist.json` also generates `CP/catalog/engines.json` |
| Four admin checks (`check_user_is_admin`, `_check_user_is_admin`, inline `fetch_caller_role`, `userAdminCheck`) | L2 `Authorizer` | Three Python copies **D** when routes become facades |
| Five skill visibility checks; none for MCP; org-wide toolsets | L2 visibility in `EffectiveSetResolver` | Merge; copies **D** |
| Three MCP digests, two skill digests, toolset fullName digest | L0 one digest scheme | **D** after the dual-write window (old pins keep resolving) |
| Tool naming three ways | L0 `ToolId` (`harness_core/tool_identity/names.py`) | Keep the one implementation |
| `build_tool_environment` + fixed `default_sources()` + `build_catalog` + `engine_grant` | L4 `ItemRuntimeRegistry` | **M** → registry (R2 §3.6) |

### 1.3 Resolution and ports (R2 §1.4–1.5)

| Component today | Layer | Disposition |
|---|---|---|
| `HarnessRollout.decide` (model family checked on the raw request model) | L2 `rollout` + `models` dimensions inside `PolicyResolver` | **M** → checks the resolved model |
| `resolveEngineProfile` (request layer never wired) | L2 `PolicyResolver` restricted to `executor.*` | Merge |
| `ModelAccessService.admit`, `MemoryService.admission`, `effectiveApprovals` (test-only caller) | L2 `PolicyResolver` | Merge; `effectiveApprovals` test-only path **D** |
| `authorizeAgent` (Node → Python run-definition, uncached) | L2 `AgentDirectory` + cache | **M** → slimmed route + cache |
| `assertClientNarrowing`, `resolveRunSpec` inline merges | L2 request layer; `resolveRunSpec` keeps assembly only | Adapt |
| Worker re-derivation (approvals, LLM config, chat-mode policy, skills, code-exec gates, feature flags) | L4 reads the pin | **D** the live reads |
| `childGrants`/`childExecutor`/`childRunSpec`; `engineAuthority` in composition | L2 `RunConfigPinner` with parent layer | `engineAuthority` **D** (R2 F5) |
| `NextRunService.drain` direct `store.admit` | L4 → `RunConfigPinner` | **M** (CPS fixes the re-admission; CPD-5 routes it through the pinner) |
| Executor kind derived three times | L2 `executorOf(config)` | Two copies **D** |
| `effectivePolicy` (half of what governs the run) | L3 view over `ResolvedRunConfig` | Adapt, additive |
| `ConfigDocumentStore` (no contract suite) | L1 kept for key material only | Add contract suite (CP4) |
| `Config*` adapters in `application/`; ports in `domain/` | layering | Move (R2 CP11, R3 WP-9) |

### 1.4 What R3 adds to the inventory

| R3 item | Layer | Disposition |
|---|---|---|
| D1 six Node HTTP clients in Python (`journal_node`, `access/budget.py`, `access/grants.py`, `refusals.py`, `audit/node_source.py`, `gateway/tokens.py`) | L4 | The control-plane live-layer client is the seventh; it is built on R3 WP-3's `NodeServiceClient`, not inline |
| D10 canonical JSON SHA-256 copies | L0 | One digest helper per language (R3 WP-13) used by every digest in §5.4 of the LLD |
| D11 per-org config doc read copies in Python | L1/L4 | **D**: Python no longer reads policy documents |
| D16 fail-closed settings read helper copies (Node) | L2 | **D**: one snapshot loader |
| D17–D19 hand-mirrored enums, error codes, defaults | L0 | Generated (R3 WP-11) |
| D21 frontend error mappers and pickers | L5 | One `Problem` renderer from generated types |
| M14 test fakes in production packages | L1 | New in-memory adapters live under test support from day one |
| M15 hand-mirrored contracts | L0 | Frontend codegen target |

---

## 2. Layer-by-layer verification

Each row is a need the layer above has, the component in this layer that serves it, and the verdict.

### 2.1 L0 Data model → serves L1 Stores

| Need of L1 | Served by | Verdict |
|---|---|---|
| A validated, versioned shape for every stored object | `CP/*.schema.json` with `schemaVersion`; generated zod (Node) and pydantic (Python) | OK |
| Per-object revision field | `revision` in every mutable schema | OK |
| A digest that is identical across languages | `canonical_json` (both languages) + `CP/vectors/digests.json` | OK; **G** the TS half of C-5 drift must run in CI (CP0) |
| Upcasters for old documents | `schemaVersion` + upcaster per schema (RunSpec pattern) | OK |
| Release items without a database migration | `CP/catalog/*.json` generated at build | OK; **G** build steps that dump `ToolsetRegistry` and `MCPRegistry` |
| Secrets never representable in a stored control-plane object | `binding.schema.json` has no secret field; token-pattern contract case | OK |

### 2.2 L1 Stores → serve L2 Resolvers and services

| Need of L2 | Served by | Verdict |
|---|---|---|
| Load everything a run needs for an org in ≤ 1 round trip per collection (NFR-10) | `listForOrg`, `getMany`, `readMany`, `listForOwner` | OK; contract suites count queries |
| Atomic write + history + head + audit + outbox | `ControlPlaneTransaction` (Mongo replica set) | OK on replica set; **M** standalone uses ordered steps + repair (LLD §6.4) |
| Head revision readable inside the admission transaction | `cpOrgHeads` in the same Mongo as `agentRuns` | OK |
| Immutable catalog versions | `CatalogStore` has no update | OK |
| Secrets readable by Python by reference | vault paths (P2.4b envelope) | OK; **G** toolset and MCP secrets need migration to the envelope (CPD-8) |
| Team membership | `MembershipPort` over the graph via connectors | **M** no change events today; **G** `teamMembershipChanged` on `entity-events` |
| Agent identity, permission, owner team | `AgentDirectory` over the slimmed run-definition route | **G** `agentChanged` event and owner-team field on the agent (agents have no team owner today; agent permission checks ignore TEAM edges, `arango_http_provider.py:25288-25321`) |

### 2.3 L2 Resolvers and services → serve L3 APIs

| Need of L3 | Served by | Verdict |
|---|---|---|
| Write with OCC, history, rollback, audit | core services over `ControlPlaneTransaction` | OK |
| Typed refusal naming dimension and layer | `PolicyResolver` refusal → `Problem` | OK |
| Preview/explain without a run | `ExplainService` = `RunConfigPinner` dry run | OK |
| The builder's "grantable" list with states in one call | `GrantService.grantable(agent)` over the org snapshot + bindings | OK |
| Authorization for every action, including team delegation | `Authorizer` | OK; team delegation depends on L1 **G** above |
| Server-side routing and scope for chat (FR-40) | `ResolvedRunConfig.routing`, `scope` | OK |
| Approval decisions that cannot apply something the approver did not see | CAS on `targetRevision` | OK |

### 2.4 L3 APIs → serve L4 Runtime consumers

| Need of L4 | Served by | Verdict |
|---|---|---|
| One admission entry point for every path (submit, child, next-run, schedule, Slack, MCP, legacy) | `RunConfigPinner` (in-process for Node consumers) | OK; **M** next-run drain and child spawn today bypass it (CPS, CPD-5) |
| The pin delivered with the claim, so assembly reads nothing live | `x.pipeshub.resolved` in the pinned spec | OK; **G** worker feature `resolved_config` and claim gating (F2 rule) |
| A narrow-only live layer with bounded staleness | `control-plane-events` + `GET …/internal/control-plane/orgs/{org}/live` | OK |
| Credentials by reference, leased per attempt | pinned `binding` + `CredentialBroker` (`BindingSecretSource`) | OK |
| Legacy path: an authoritative resolved config without re-reading | signed `X-PipesHub-Resolved` | OK; **G** Python verifier and the query path consuming it (CPD-10) |
| Drift detection per tool call | pinned `descriptorDigest` per tool; `ItemRuntime.verify` | OK; today only MCP hub checks it |
| Run hand-off without polling | LAT1 `run-ready` publish | Owned by LAT1; shares `AfterCommitPublisher` |

### 2.5 L4 Runtime consumers → serve L5 UI

| Need of L5 | Served by | Verdict |
|---|---|---|
| "Connect your account" in the chat when an item is unavailable | effective set `unavailable{credential_missing}` → a projected `CUSTOM capability_unavailable` frame with `Problem.remediation` | **G** new frame name under the existing `CUSTOM` vocabulary (additive; NFR-40 allows additive) |
| Approval cards for every approval, including deep-mode children | P2.6c interactions | **M** R1 F3 (child cards) is a harness fix outside this design, listed in the migration plan for ordering |
| Run explain ("what did this run have") | `GET …/runs/{id}/resolved` | OK |
| Typed, translated errors | `Problem` with generated codes and remediation | OK |

### 2.6 Requirement traceability (summary)

| Requirement | Served at | By |
|---|---|---|
| FR-1, NFR-16 | L2 | `EffectiveSetResolver` (grant ∩ visible ∩ enabled ∩ approved ∩ ceilings) |
| FR-2, FR-3, FR-38 | L2 → L5 | unavailable reasons → `Problem.remediation` → chat frame |
| FR-4, Q2 | L2 | `catalog.kinds.<kind>.userInstall` ceiling; owner approval |
| FR-5, FR-9, FR-17, NFR-17 | L2 | `PolicyResolver` ceilings and choices |
| FR-8, NFR-9 | L3 | `GET /agents/{key}/grantable` |
| FR-10, FR-39 | L3 | `POST /explain`, `GET …/runs/{id}/resolved` |
| FR-12, FR-18, NFR-27 | L1 | revisions, history, rollback |
| FR-13–FR-16 | L1/L2 | catalog, installations, approvals, grants |
| FR-19, NFR-23, NFR-24 | L2/L4 | live layer via `control-plane-events` |
| FR-20, FR-31, NFR-19 | L1/L4 | vault + `CredentialBinder` + `CredentialBroker` |
| FR-22, FR-23 | L0/L2 | deployment layer; `rollout` dimensions pinned |
| FR-26, NFR-21 | L1 | `ConfigAuditLog` in the transaction |
| FR-27, FR-28 | L0/L4 | `x.pipeshub.resolved`, descriptor digests, `verify` |
| FR-29, FR-30 | L2/L4 | `RunConfigPinner`; pin-only assembly |
| FR-32 | L4 | `ItemRuntimeRegistry` |
| FR-33–FR-35 | L2 | plugins module (`InstallExpander`) |
| FR-40, FR-41 | L3 | `ChatTurnService` on existing routes; routing in the pin |
| NFR-1–NFR-11 | L2/L3 | snapshot cache, head check in the admission transaction, prefix fold |
| NFR-12–NFR-15 | L1 | `orgId` keys, per-org rows, cache keys, shared release items |
| NFR-31–NFR-39 | L2/L4 | fail rules per decision (§4.8 of the requirements) |
| NFR-44–NFR-47 | L0/L1 | codegen, contract suites, layering checks, property tests |
| NFR-48 | L2/L4 | `ChangeNotifier`, LAT1 `run-ready` |

---

## 3. End-to-end scenarios

### 3.1 An admin approves an MCP server for one team

Setup: org `o1`; team `sales` (graph team `t9`); Priya is a delegated team admin for `t9`
(`catalog.delegation.teamAdmin ⊇ installation.create, grant.write`); org approval rule for
`{change: install, kind: mcp}` is `orgAdmin`. Ahmed is an org admin.

| Layer | What happens | Checked by |
|---|---|---|
| L5 | Priya opens Capabilities → Catalog → "Linear (MCP)", picks scope "Team: Sales", fills the URL, sees "needs org admin approval" before submitting | `GET /catalog/mcp:pipeshub/linear@3`; approval rule preview from the org layer |
| L3 | `POST /control-plane/installations {item, owner: team:t9, visibility: team, config}` with `Idempotency-Key` | route chain; zod from `CP/api/installations.schema.json` |
| L2 | `Authorizer.check(priya, installation.create, team:t9/mcp)` → allow via delegation + `MembershipPort` (Priya OWNER of `t9`) | `RoleAuthorizer` rule vectors |
| L2 | `ConfigValidator(mcp)` validates transport and URL; `DescriptorProvider(mcp)` calls the Python driver, which SSRF-pins and lists tools, returning 14 `ToolDescriptor`s and digests | module conformance suite; P2.8 SSRF rules |
| L2 | `ApprovalTriggers(mcp).changesOf(null, inst) = [install]`; rule → `orgAdmin` | `catalog.approval` vectors |
| L1 | One transaction: `cpInstallations` (status `pending_approval`, rev 1) + history + `cpApprovalRequests` + `cpOrgHeads` 1041 → 1042 + audit `config.changed` (stream `cp:o1`) + outbox | `ControlPlaneTransaction` contract |
| L2 | Outbox → `control-plane-events` `cp.changed{1041→1042, narrowing: false}`; Node caches advance untouched entries; workers ignore (not narrowing) | notifier contract |
| L5 | Ahmed's approvals inbox shows the request with the descriptor list, risk class per tool (`effect`, `sink`) | `GET /approvals?state=open` |
| L3/L2 | Ahmed approves 12 of 14 tools: `POST /approvals/apr…/decision {approve, subset, targetRevision: 1}` → CAS installation rev 1 → 2, `approved{descriptorSetDigest, tools}` = the 12 | approval CAS; `CP_APPROVAL_SUPERSEDED` if the installation changed |
| L1 | Transaction as above; head 1043 | |
| L5/L3 | Priya grants it to the "Sales assistant" agent (owned by `t9`): `PUT /grants/agent/a7` with `If-Match`; `Authorizer` checks `grant.write` + `can_edit` on `a7` | `GrantService` |
| L4 (next run) | A Sales member's run of `a7` resolves `mcp@inst…/list_issues` etc. (12 tools), pinned with descriptor digests; a non-member's run of `a7` gets the item `unavailable{not_visible}` (team visibility) | `EffectiveSetResolver` vectors |
| L4 | Worker `McpItemRuntime` loads exactly the 12 tools; hub schema cache keyed by installation revision 2 | `ItemRuntime` contract |

Verdict: served end to end. Gaps on this path: **G** team delegation requires the `MembershipPort`
change events (CPD-9); **G** the approvals inbox page (CPD-11).

### 3.2 A user chats in quick mode with a skill and a toolset

Setup: agent `a3` grants skill `skill:org/quarterly-report@1.4.2` (org-visible) and the Jira toolset
installation `inst_j` (credential mode `per_user`). Maya has an active Jira binding. Warm caches.

| Layer | What happens | Time budget |
|---|---|---|
| L5 | Maya sends a message in the existing chat UI; `chatMode: quick` | — |
| L3 | `POST /api/v1/agents/a3/conversations/:id/messages/stream` (existing route, existing body). `ChatTurnService` loads the conversation (server-side `projectId = null`) | already on the path |
| L2 | `RunConfigPinner.pin`: snapshot hit (org head 1043 = cached); `AgentDirectory.authorizeRun(a3, maya)` cache hit (definitionVersion 9); prefix fold for `(o1, a3, 1043)` cached; fold user + request + live; effective set: skill (bundle digest), `jira@inst_j/*` tools with descriptor digests and `binding: bnd_m (user:maya)`; `routing: durable` (quick ∈ `rollout.durable.modes`) | p95 ≤ 8 ms (NFR-1) |
| L1 | Admission transaction asserts `cpOrgHeads[o1] == 1043`, writes the run with `resolved` (digest), `run.admitted`, outbox | inside SLO #4 |
| L4 | After commit: `run-ready{runId}` (LAT1); inline claimer claims; Node copies `resolved` into the pinned spec (`x.pipeshub.resolved`, legacy fields derived) | no polling |
| L4 | Worker: `LiveLayerCache.current(o1)` (local); `SkillsItemRuntime` loads the pinned bundle digest only (no `list_skills`, no co-worker skills); `ToolsetItemRuntime` builds one Jira client; credentials leased lazily at first Jira call (`bnd_m`, purpose `(o1, inst_j, run, attempt)`) | 0 authority reads (NFR-6) |
| L4 | Each Jira call: live-layer check and descriptor digest check (local) | ≤ 1 ms (NFR-7) |
| L5 | Frames stream on the same response; "Explain" on the message calls `GET …/runs/{id}/resolved` | — |

Variant: Maya has **no** Jira binding. The item is `unavailable{credential_missing}`; the run proceeds
with the skill only; the chat shows a `capability_unavailable` card "Connect Jira" (remediation
`connect_account`, actor `self`). After connecting, the next turn includes Jira.

Verdict: served. **M** today's `SkillsSource` loads live content and the full org catalog when
`run.skills` is empty (fixed first by CPS, then replaced by `SkillsItemRuntime` in CPD-7).

### 3.3 A scheduled engine run with a narrowed budget

Setup: org ceiling `budgets.maxCostUsd = 5`, `executor.engines = [opencode, codex]`. Agent `a9` (engine
agent, `executor.engine = opencode`) narrows `budgets.maxCostUsd = 2`. A schedule by Lee fires nightly
with `maxCostUsd = 1` (trigger layer). Lee's run of `a9` normally pins $2.

| Layer | What happens | Checked by |
|---|---|---|
| L0 | `budgets.maxCostUsd` row: ceiling, order `min`, exposure deployment/org/agent/trigger/request | dimension table |
| L1 | Org and agent layers in `cpPolicyLayers`; the schedule document holds the trigger layer (schedule routes validate it with the same zod, one bound instead of 1000 vs 5) | R2 CP7 |
| L2 | `ScheduleFirer` builds `PinSubject{surface: schedule, trigger: {budgets.maxCostUsd: 1}}` → `RunConfigPinner.pin` → meet: min(5, 2, 1) = $1, `setBy: trigger`, ceilings listed with their layers; executor engine opencode admitted by `executor.engines` | `PolicyResolver` vectors; property: result ⊑ every layer |
| L2 | If the trigger said `maxCostUsd: 3`, resolution refuses `CP_WIDENING{dimension: budgets.maxCostUsd, layer: trigger}` and the schedule's firing records the refusal (schedule notifier) | refusal vector |
| L1 | Admission transaction (head check); the budget ledger opens the run account with cap $1 (P6.5) | ledger contract unchanged |
| L4 | Engine kernel reads `effectiveConfig` (engine profile limits derived from the pin, R2 F5 reconciliation: `limits.maxUsd = min(profile, budgets)`); the model gateway reserves against the ledger; the sandbox gets skills pinned for `a9` (CPS wires `skill_bundles_for`) | engine conformance |
| L4 | Mid-run, the org admin lowers the org ceiling to $0.50: `cp.changed{narrowing: true, live: {budgets.maxCostUsd: 0.5}}`; the live layer meets it; the gateway's next reservation is capped at the remaining amount under $0.50 | live-layer property: never widens |
| L5 | Lee's schedule page shows the run's resolved budget and where it came from | `GET …/runs/{id}/resolved` |

Verdict: served. **M** today's `RUN_SPEC_BUDGETS` and `EngineProfile.limits` are not reconciled (R2 F5,
F11; CPS fixes the narrow-only gaps, CPD-5 makes it one dimension). **G** live-layer budget narrowing
needs the gateway to consult the live layer at reservation time (CPD-5).

### 3.4 A revoked credential mid-run

Setup: Raj's deep-mode run of agent `a4` uses GitHub (installation `inst_g`, binding `bnd_r`, owner
`user:raj`). The run has made two GitHub calls. Raj revokes the GitHub connection from "My connections".

| Layer | What happens | Bound |
|---|---|---|
| L5 | My connections → GitHub → Revoke | — |
| L3 | `POST /control-plane/bindings/bnd_r/actions/revoke` (`If-Match`) | — |
| L2/L1 | `CredentialBinder.revoke`: vault tombstone (CAS on the versioned envelope) + `cpBindings` status `revoked` + head++ + audit + outbox, one transaction (the vault write is first and idempotent; the transaction is the commit point) | — |
| L2 | `cp.changed{narrowing: true, live: {revokedBindings: [bnd_r]}}` on `control-plane-events` | publish after commit |
| L4 | Every worker's `LiveLayerCache` adds `bnd_r` to revoked | ≤ 5 s p99 with the notifier (NFR-23); ≤ 30 s by the poll backstop |
| L4 | The deep-mode sub-agent's next GitHub call: `ItemRuntime` live check → `CP_CREDENTIAL_REVOKED` tool result; the model is told the tool is unavailable; the run continues | per call, local |
| L4 | A lease issued before the revoke: the next `resolve` reads the vault uncached (P2.4b rule) and hits the tombstone → refused; exposure is at most one in-flight HTTP call | P2.4b |
| L4 | Egress proxy (engine runs): injection for `bnd_r` refused at the next request for the same reason | SEC1 gateway path |
| L5 | The chat shows "GitHub was disconnected; reconnect to continue using it" (`Problem.remediation`) | — |
| Audit | `config.changed{binding bnd_r, revoked}` on `cp:o1`; the run's journal records `tool.denied{rule: credential_revoked}` | chain verify |

Verdict: served. **G** today's MCP and toolset secret sources read old KV paths without tombstones
(CPD-8); until then revocation deletes the key, which is also fail-closed but unaudited.

### 3.5 (extra) A queued next-run after the agent's grant narrows

Kim's collaborator turn is queued (`next_run`) while the builder removes Slack from agent `a4`. On drain,
`NextRunService.drain` calls `RunConfigPinner.pin` with the frozen request as the request layer; the
effective set no longer includes Slack, so the turn runs without it (or is refused if the request had
explicitly narrowed to Slack-only tools). Today the drain admits the stale grant (R2 F6); CPS adds
re-admission, CPD-5 routes it through the pinner.

### 3.6 (extra, future) A plugin installs two skills and a remote MCP server

The plugins module verifies the publisher signature against `catalog.trustRoots`, creates one parent
installation and, through `InstallExpander`, three children (`installedVia: parent`) in one transaction;
the approval rule for `{change: install, kind: plugin}` covers all four with one request listing every
contributed descriptor. Grants reference the children; uninstalling the parent disables the children
(live layer). No core file changes (03-interfaces §3.3).

---

## 4. Gaps, mismatches and deletions (consolidated)

| # | Item | Type | Fixed in |
|---|---|---|---|
| 1 | Skills runtime visibility and empty-means-all | M | CPS (in place), CPD-7 (pinned digests) |
| 2 | Engine runs get no skills | G | CPS |
| 3 | MCP and toolset pins do not bind approved descriptors | M | CPS (in place), CPD-2 (digest scheme) |
| 4 | Narrow-only gaps: reasoning effort, deadline vs wall time, budgets vs profile limits, loop-parent engine children | M | CPS |
| 5 | Next-run drain admits stale grants | M | CPS (re-admission), CPD-5 (pinner) |
| 6 | Node toolsets router has no scopes | M | CPS |
| 7 | No config audit; Labs without CAS | G | CP1 → CPD-1 |
| 8 | Five KV policy documents | D | CPD-4 |
| 9 | Live reads in the worker (approvals, flow, LLM config, chat-mode policy, skills, flags) | M | CPD-5/CPD-6 (R2 CP6) |
| 10 | No team membership or agent change events | G | CPD-9 |
| 11 | Agents have no owner team; agent permission ignores TEAM edges | G | CPD-9 (owner team field; team-scoped agent sharing stays a separate product decision) |
| 12 | Toolset and MCP secrets without versions or tombstones | G | CPD-8 |
| 13 | Three refresh services | D | CPD-8 |
| 14 | `builtin_seeder.py` per-org copies, `FilesystemSkillStore`, `PluginRegistry` | D | CPD-2 (`PluginRegistry`, `FilesystemSkillStore`), CPD-7 (`builtin_seeder.py`) |
| 15 | `ToolsetRegistry`/`MCPRegistry` not exported as data | G | CPD-2 |
| 16 | No frontend codegen target | G | CPD-0 (with R3 WP-11) |
| 17 | MCP instance path disagreement (`PY/app/agents/mcp/models.py:123` vs `mcp_server_constants.py`) | M | CPD-7 migration reads both |
| 18 | Client-decided routing, project scope; regenerate always legacy; two round trips | M | LAT1 + CPD-10 |
| 19 | Deep-mode child approval cards (R1 F3), unknown-effect console (R1 F1) | M | harness UX WPs (outside this design; ordering only) |
| 20 | `capability_unavailable` chat frame | G | CPD-11 |
