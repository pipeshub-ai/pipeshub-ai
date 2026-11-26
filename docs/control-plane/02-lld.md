# Control plane: low-level design

**Status:** design proposal. **Date:** 2026-10-09. HLD: [01-hld.md](01-hld.md). Interfaces:
[03-interfaces.md](03-interfaces.md). Prefixes as in [00-requirements.md](00-requirements.md).

Contents: §1 contracts and codegen, §2 data model, §3 scopes and precedence, §4 storage and indexes,
§5 resolution algorithms, §6 concurrency, §7 audit, §8 caching and invalidation, §9 APIs and DTOs,
§10 error model, §11 migration of today's stores into the core.

---

## 1. Contracts: one source, generated everywhere

### 1.1 Layout

Two roots, one generator:

```text
contracts/
  harness/                         existing; product-neutral core + x.pipeshub extension
    core/policy/                   NEW (R2 CP3): harness-generic policy model, embeddable in Loom
      policy-layer.schema.json       one partial layer, every dimension optional
      effective-run-config.schema.json
      policy-dimensions.schema.json  schema of the dimension table
      policy-dimensions.json         DATA: one row per harness dimension
      release.json                   DATA: release defaults (replaces hand copies, R2 F10)
    ext/pipeshub/run-spec.schema.json  MODIFY: x.pipeshub.resolved (§2.9)
  control-plane/                   NEW: PipesHub control-plane objects (not Loom-embeddable)
    common.schema.json             OrgId, ScopeRef, ItemRef, InstallationRef, BindingRef, Revision, ActorRef, Digest
    catalog-item.schema.json
    descriptors/                   one schema per descriptor kind
      tool.schema.json             $ref ../../harness/core/tool-descriptor.schema.json + display fields
      skill-bundle.schema.json     skill-bundle/1 file list (P7.1)
      engine.schema.json           engine kind, version, binaryDigest, options schema ref
      plugin.schema.json           contributes[]
    installation.schema.json
    grant.schema.json
    binding.schema.json
    approval-request.schema.json
    approval-policy.schema.json
    catalog-policy.schema.json     catalog dimensions section of a layer (allowed kinds, user installs, trust roots)
    effective-set.schema.json
    resolved-run-config.schema.json
    change-event.schema.json       cp.changed
    config-changed.schema.json     audit payload
    problem.schema.json            the error model (§10)
    api/                           request and response DTOs, one file per route group
    errors.json                    DATA: error codes, HTTP status, retryable, remediation actor
    dimensions.json                DATA: catalog dimension rows contributed by domain modules
    catalog/                       DATA (generated at build): toolsets.json, mcp.json, skills.json, engines.json
    vectors/                       resolver, precedence, digest and migration vectors
```

`core/policy` stays in `contracts/harness` because the dimension engine is generic and must run in Loom
(ARCHITECTURE §3.1 principle 9; C6 forbids `x.pipeshub` in core). Everything that names installations,
bindings, teams or approval workflow is PipesHub product data and goes in `contracts/control-plane`.

### 1.2 Codegen

Extend `contracts/harness/codegen/generate.py` rather than adding a second generator:

| Change | Detail |
|---|---|
| Roots | `schema_files()` (`generate.py:51-52`) globs a list of roots: `harness/core`, `harness/ext`, `control-plane` |
| Python target | `backend/python/app/control_plane/contracts/generated/` (pydantic v2, same `datamodel-code-generator==0.83.0` flags: `--extra-fields forbid`, `--snake-case-field`) |
| Node target | `N/modules/control-plane/contracts/generated/` (zod via `generate-zod.mjs`, `$ref` bundled) |
| Frontend target (new, R3 WP-11) | `frontend/lib/contracts/control-plane/generated/` and `frontend/lib/contracts/harness/generated/`: zod schemas and `z.infer` types. The frontend validates API responses in dev builds and uses the types everywhere; hand-written DTOs in `FE/workspace/*/types.ts` and `FE/chat/types.ts` are deleted as their routes move |
| Data files | `errors.json`, `dimensions.json`, `policy-dimensions.json`, `release.json` are emitted as typed constants in all three targets (`ERROR_CODES`, `DIMENSIONS`, `RELEASE_DEFAULTS`), so no language hand-copies a default or a code |
| Drift | `test_c5_codegen_drift.py` covers all roots and all three targets; the TS half must run in CI (R2 CP0, R3 CI-1) |
| C1 | Every new schema has `examples` and a sibling `*.invalid.json` (`support.py:43-48`) |

### 1.3 Identifiers

| Id | Format | Example | Notes |
|---|---|---|---|
| `ItemRef` | `{kind}:{publisher}/{name}@{version}` | `mcp:pipeshub/linear@3`, `skill:org/quarterly-report@1.4.2`, `toolset:pipeshub/jira@2026.10.1`, `engine:pipeshub/opencode@1.18.34` | `publisher` is `pipeshub` (release), `org` (org-authored or imported), `user`, or a plugin publisher id |
| `InstallationRef` | `inst_{ulid}` | `inst_01J9…` | Today's toolset and MCP `instanceId`s are kept verbatim as installation ids during migration (§11.2) |
| `ToolId` | unchanged from P2.2 | `jira@inst_01J9/create_issue`, `mcp@inst_01J9/list_issues` | `harness_core/tool_identity/names.py` stays the one implementation |
| `BindingRef` | `bnd_{ulid}` | | |
| `ScopeRef` | `{kind}:{id}` | `org:o1`, `team:t9`, `agent:a7`, `user:u3` | `deployment` and `release` have no id |
| `Digest` | `sha256:{hex}` | | RFC 8785 canonical JSON + SHA-256, `harness_core/contracts/canonical_json.py` and its TS twin (vector `canonical-json.json`) |

---

## 2. Data model

Shapes below are abbreviated JSON Schema (`jsonc`); the committed schemas are strict
(`additionalProperties: false`) and carry `schemaVersion`.

### 2.1 CatalogItem

```jsonc
{
  "schemaVersion": 1,
  "ref": "mcp:pipeshub/linear@3",
  "kind": "mcp",                       // open string validated against registered DomainModules
  "orgId": null,                       // null = shared (builtin/release); else org-scoped row
  "provenance": "release",             // builtin | release | imported | org | user | plugin
  "source": { "type": "release" },     // or {type: npm|github|url|upload|plugin, locator, fetchedAt, digest}
  "display": { "name": "Linear", "description": "…", "icon": "linear", "categories": ["project"] },
  "riskClass": "write-external",       // read | write-internal | write-external | execute (max over descriptors)
  "configSchema": { "$ref": "…" },     // domain-specific; validated by the module
  "credentialRequirements": [ { "mode": "oauth", "owners": ["user", "agent"], "provider": "linear" } ],
  "descriptors": { "kind": "tool", "items": [ /* ToolDescriptor + display */ ] },
  "descriptorSetDigest": "sha256:…",   // digest over canonical descriptors (§5.4)
  "deprecated": false,
  "createdAt": 0
}
```

Rules: immutable after insert (a new version is a new row); `descriptorSetDigest` must equal the
recomputed digest at insert; for `kind: mcp`, release templates carry no tool descriptors (they are
discovered per installation, §2.2), so the item's `descriptors` is empty and the installation's
approved descriptor set is the authority.

### 2.2 Installation

```jsonc
{
  "schemaVersion": 1,
  "id": "inst_01J9…",
  "orgId": "o1",
  "item": "mcp:pipeshub/linear@3",
  "kind": "mcp",
  "owner": "team:t9",                  // org | team:<id> | user:<id>
  "visibility": "team",                // owner | team | org  (never wider than owner scope's ceiling)
  "config": { "url": "https://mcp.linear.app/sse", "transport": "sse" },   // non-secret only
  "approved": {                        // what runs may use; null until first approval
    "descriptorSetDigest": "sha256:…",
    "tools": { "list_issues": "sha256:…", "create_issue": "sha256:…" },    // name → descriptor digest
    "approvalRef": "apr_01J9…",
    "at": 0, "by": "user:u1"
  },
  "pending": null,                     // {item?, config?, descriptors?, approvalRef} while an upgrade waits
  "status": "enabled",                 // draft | pending_approval | enabled | disabled | upgrade_pending | removed
  "installedVia": null,                // parent installation id when contributed by a plugin
  "credentialMode": "per_user",        // per_user | agent | org | none
  "revision": 4,
  "updatedAt": 0, "updatedBy": "user:u1"
}
```

### 2.3 Grant

One document per subject (CAS on the whole grant keeps "edit the agent's capabilities" atomic):

```jsonc
{
  "schemaVersion": 1,
  "orgId": "o1",
  "subject": "agent:a7",               // org | team:<id> | agent:<key> | user:<id>
  "entries": [
    { "installation": "inst_01J9…", "tools": ["list_issues"], "required": false },
    { "installation": "inst_skill_…", "tools": null, "required": false }     // null = all approved
  ],
  "revision": 12
}
```

`org` grants are the built-in assistant's grant (replacing `chat.mode` / `internalSearch` grant logic
for tools, not for knowledge). At most 500 entries per subject (NFR-11).

### 2.4 PolicyLayer

The R2 §2.4 `harness-policy-layer` plus a `catalog` section contributed by domain modules:

```jsonc
{
  "schemaVersion": 1,
  "scope": { "kind": "agent", "id": "a7" },
  "executor": { … }, "engineOptions": { … }, "tools": { "effects": ["read", "write"], "maxTools": 64 },
  "approvals": { … },                  // approval-rules.schema.json (P2.11e), incl. flow (SEC2 O-1)
  "models": { … }, "budgets": { … }, "egress": { … }, "context": { … },
  "memory": { … }, "schedules": { … }, "retention": { … }, "rollout": { … },
  "catalog": {                         // CP/catalog-policy.schema.json
    "kinds": { "mcp": { "allowed": true, "userInstall": "remote_only" }, "plugin": { "allowed": false } },
    "approval": {                      // configuration-time approval policy (§2.6)
      "rules": [ { "change": "install", "kind": "mcp", "approver": "orgAdmin" },
                 { "change": "descriptor_change", "kind": "*", "approver": "orgAdmin" } ]
    },
    "delegation": { "teamAdmin": ["installation.create", "grant.write"] }
  }
}
```

### 2.5 Binding

```jsonc
{
  "schemaVersion": 1,
  "id": "bnd_01J9…",
  "orgId": "o1",
  "installation": "inst_01J9…",
  "owner": "user:u3",                  // org | agent:<key> | user:<id>
  "mode": "oauth",                     // oauth | api_key | header | dcr | model_account
  "status": "active",                  // pending | active | expiring | expired | revoked | error
  "secret": { "path": "/services/controlPlane/credentials/o1/inst_01J9…/user/u3", "version": 7,
              "versionDigest": "sha256:…" },
  "scopes": ["read", "write"], "expiresAt": 0, "refreshedAt": 0, "lastError": null,
  "revision": 9
}
```

### 2.6 ApprovalRequest

```jsonc
{
  "schemaVersion": 1,
  "id": "apr_01J9…",
  "orgId": "o1",
  "target": { "type": "installation", "id": "inst_01J9…", "revision": 1 },
  "change": { "type": "install", "descriptors": { "list_issues": "sha256:…", … }, "config": { … } },
  "changeDigest": "sha256:…",
  "requestedBy": "user:u7",
  "approvers": [ { "principal": "role:orgAdmin" } ],   // resolved from the approval policy at request time
  "state": "open",                     // open | approved | rejected | superseded | expired | withdrawn
  "decisions": [ { "by": "user:u1", "decision": "approve", "subset": ["list_issues"], "at": 0, "note": "" } ],
  "expiresAt": 0
}
```

### 2.7 Deployment layer

Built once at boot from env and Helm values by `DeploymentPolicyLoader` (Node) and its Python twin,
validated against `policy-layer.schema.json` with `scope.kind = deployment`; invalid values fail startup
(FR-22, extends F3's `InvalidEnvFlag`). Not stored; its digest is logged and pinned.

### 2.8 EffectiveSet

```jsonc
{
  "items": [
    {
      "installation": "inst_01J9…", "item": "mcp:pipeshub/linear@3", "kind": "mcp",
      "state": "ready",                // ready | unavailable
      "reason": null,                  // credential_missing | credential_expired | not_visible | … (§10)
      "tools": [ { "toolId": "mcp@inst_01J9…/list_issues", "descriptorDigest": "sha256:…" } ],
      "binding": { "ref": "bnd_01J9…", "owner": "user:u3", "versionDigest": "sha256:…" },
      "provenance": { "grantedBy": "agent:a7", "visibleVia": "team:t9", "approvedBy": "apr_01J9…" }
    }
  ],
  "digest": "sha256:…"                 // over items sorted by installation id
}
```

### 2.9 ResolvedRunConfig (the pin)

```jsonc
// RunSpec.extensions['x.pipeshub'].resolved  (additive; ext/pipeshub/run-spec.schema.json)
{
  "schemaVersion": 1,
  "headRevision": 1042,                // org head revision the resolution saw
  "routing": { "path": "durable", "reason": null },   // durable | legacy{reason: chat_mode_not_supported | kill_switch | …}
  "scope": { "agentKey": "a7", "projectId": "p3" },   // decided by the server from the conversation record
  "effectiveSet": { … },               // §2.8
  "effectiveConfig": { … },            // C/core/policy/effective-run-config: {dimension: {value, setBy, ceilings[]}}
  "llm": { "modelKey": "…", "provider": "…", "model": "…", "endpointRef": "…", "configDigest": "sha256:…" },
  "deploymentDigest": "sha256:…",
  "digest": "sha256:…"                 // over all of the above; recorded in run.spec_pinned
}
```

The existing pinned fields (`grants.tools`, `x.pipeshub.mcp[]`, `x.pipeshub.skills[]`, budgets,
`executor.engine.profile`) are still written, **derived from** `resolved`, so workers without the
`resolved_config` feature keep working (F2 rule: a worker that does not advertise the feature does not
claim a run that requires it; runs admitted while any worker lacks it are pinned without the
requirement and read the legacy fields).

### 2.10 ChangeEvent and audit payload

```jsonc
// topic control-plane-events, key = orgId
{ "type": "cp.changed", "orgId": "o1", "revision": 1042, "previous": 1041,
  "touched": [ { "kind": "binding", "id": "bnd_01J9…" } ],
  "narrowing": true,                   // true when the change can only remove authority
  "live": { "revokedBindings": ["bnd_01J9…"] } }   // present when narrowing; the live-layer delta

// audit chain entry payload (streamId cp:{orgId})
{ "kind": "config.changed", "scope": "team:t9", "object": { "type": "binding", "id": "bnd_01J9…" },
  "revisionFrom": 8, "revisionTo": 9, "changeDigest": "sha256:…", "actor": "user:u3",
  "approvalRef": null, "headRevision": 1042 }
```

---

## 3. Scopes and precedence

### 3.1 The chain

```text
release            code data (signed where safety-relevant: vendor rules, engine allowlist)
 └ deployment      operator; env/Helm → typed layer at boot; ceilings + operator-only
    └ org          org admin
       └ team      team admin (delegated); applies to agents and installations OWNED by the team
          └ agent  agent builder; ceilings narrow org/team, choices inside
             └ user      the acting user; choices only (no ceilings)
                └ trigger | parent    schedule caps; child ⊆ parent
                   └ request          per-run choices and narrowings
                      └ live          narrow-only overlay while running
```

Which team: the **agent's owner team** (0 or 1), not the actor's teams. Folding every team the actor
belongs to would make joining a team reduce a person's authority, and a multi-team meet is hard to
explain. The actor's teams matter only for **visibility** (union: you can see what any of your teams can
see).

### 3.2 Dimension kinds

| Kind | Merge | Who may set it | Example |
|---|---|---|---|
| Ceiling | meet (⊓) in the dimension's order; a layer below may only narrow | per `exposure` | `budgets.maxCostUsd` (min), `tools.grant` (subset), `executor.isolationMin` (rank-raise) |
| Choice | nearest-wins inside the meet of ceilings at or above the setting layer | per `exposure` | `models.primary`, `models.reasoningEffort.default` |
| Operator-only | deployment layer only | operator | `SANDBOX_MODE`, `mcp.stdio`, vendor rules |
| Set-union (visibility only) | union over the actor's principals, then intersected with ceilings | n/a | visibility of installations |

### 3.3 What each layer may set

The harness rows are R2 §2.3 unchanged. Catalog rows added by domain modules:

| Dimension | Kind | Order | Deploy | Org | Team | Agent | User | Request |
|---|---|---|---|---|---|---|---|---|
| `catalog.kinds.<kind>.allowed` | ceiling | bool-and | ✓ | ✓ | ✓ | – | – | – |
| `catalog.kinds.<kind>.userInstall` | ceiling | rank (`none` < `remote_only` < `any`) | ✓ | ✓ | ✓ | – | – | – |
| `catalog.trustRoots` (plugin publishers) | ceiling | subset | ✓ | ✓ narrow | – | – | – | – |
| `tools.grant` | ceiling | subset (installation × tool) | – | ✓ (enabled installations) | ✓ (team-owned) | ✓ | – | ✓ narrows (`tools` body field) |
| `tools.effects` | ceiling | subset | ✓ | ✓ | ✓ | ✓ | – | – |
| `catalog.approval` | ceiling | rank per rule (`none` < `owner` < `teamAdmin` < `orgAdmin`) | ✓ floor | ✓ | ✓ raise only | – | – | – |
| `catalog.delegation` | ceiling | subset of actions | – | ✓ | – | – | – | – |
| `skills.allowed` | ceiling | subset | – | ✓ | ✓ | ✓ | – | ✓ narrows |

### 3.4 Invariants (property-tested, NFR-47)

1. For every ceiling dimension d and every layer L: `resolved(d) ⊑ value_L(d)` when L sets d.
2. For every choice c: `resolved(c) ∈ meet(ceilings governing c at layers ≤ setBy(c))`.
3. `meet` is idempotent, commutative, associative per order kind.
4. A refusal names the first layer (top-down) whose value is not ⊑ the meet of the layers above it.
5. Adding a layer never widens any dimension (monotonicity).
6. `effectiveSet.items ⊆ grant(agent) ∩ visible(actor) ∩ enabled(org) ∩ approved` (tool level).

---

## 4. Storage and indexes

### 4.1 Where each object lives

| Object | Store | Port | Why |
|---|---|---|---|
| Catalog items (org rows) | Mongo `cpCatalogItems` | `CatalogStore` | Queryable, per org |
| Catalog items (release, builtin) | Release data files `CP/catalog/*.json`, loaded at boot into the same port (read-only adapter) | `CatalogStore` (composite) | Shipped with the code that implements them; no migration on upgrade |
| Installations | Mongo `cpInstallations` + `cpInstallationHistory` | `InstallationStore` | Per-object revision and history; replaces the toolsets KV list race (R2 F21) |
| Grants | Mongo `cpGrants` | `GrantStore` | One doc per subject |
| Policy layers | Mongo `cpPolicyLayers` + `cpPolicyHistory` | `PolicyStore` | Per-org rows (R2 CP4) |
| Bindings (metadata) | Mongo `cpBindings` | `BindingStore` | List, join, audit, refresher scan |
| Secrets | KV vault through `EncryptedConfigDocumentStore` / `ConfigurationService` | `SecretVault` (inside `CredentialBinder`) | Already encrypted per version; Python reads it; KV is the configured secret store |
| Approval requests | Mongo `cpApprovalRequests` | `ApprovalStore` | Queue queries |
| Org head revision | Mongo `cpOrgHeads` | inside each store adapter's transaction | Serialises an org's writes; cache key |
| Change outbox | Mongo, existing `MongoDispatchOutbox` | `ChangeNotifier` (publisher side) | Same transaction as the write |
| Audit | Existing `AuditChainStore` | `ConfigAuditLog` | Hash chain, anchoring |
| Agent definition, knowledge, teams | Graph (Python, `IGraphDBProvider`) | `AgentDirectory`, `MembershipPort` (Node adapters over Python HTTP) | Unchanged ownership |
| Skill content | Graph + Blob (Python `GraphSkillStore`, `BinaryObjectStore`) | Skill driver | Unchanged; the catalog row holds the bundle digest |

Mongo is the document store and is not swapped (CLAUDE.md table); every collection is behind a port with
an in-memory adapter under test support (R3 M14) and a contract suite (NFR-45).

### 4.2 Collections and indexes

| Collection | `_id` | Indexes | Notes |
|---|---|---|---|
| `cpCatalogItems` | `ref` (org rows prefixed `{orgId}|`) | `{orgId:1, kind:1, 'display.name':1}`; unique `{orgId:1, kind:1, publisher:1, name:1, version:1}` | Large descriptor sets (> 256 KB) stored in Blob, referenced by digest |
| `cpInstallations` | `id` | `{orgId:1, status:1, kind:1}`; `{orgId:1, owner:1}`; `{orgId:1, installedVia:1}` sparse; `{orgId:1, item:1}` | |
| `cpInstallationHistory` | `{id}:{revision}` | `{orgId:1, id:1, revision:-1}` | Append-only; TTL none (audit evidence) |
| `cpGrants` | `{orgId}:{subject}` | multikey `{orgId:1, 'entries.installation':1}` | "Who uses this installation" for impact preview |
| `cpPolicyLayers` | `{orgId}:{scope}` | `{orgId:1}` | |
| `cpPolicyHistory` | `{orgId}:{scope}:{revision}` | `{orgId:1, scope:1, revision:-1}` | |
| `cpBindings` | `id` | unique `{orgId:1, installation:1, owner:1}`; `{status:1, expiresAt:1}` (refresher); `{orgId:1, owner:1}` (My connections) | |
| `cpApprovalRequests` | `id` | `{orgId:1, state:1, createdAt:-1}`; `{orgId:1, 'target.id':1, state:1}` | |
| `cpOrgHeads` | `orgId` | — | `{revision, updatedAt}` |

Index existence is part of each Mongo adapter's contract suite (`listIndexes` assertion), so an index is
never assumed (CLAUDE.md review rule).

### 4.3 Vault layout

One layout for every domain:

```text
/services/controlPlane/credentials/{orgId}/{installationId}/{ownerKind}/{ownerId}   versions[], lastVersion, meta (no secret in meta)
/services/controlPlane/oauthApps/{orgId}/{provider}                                  per-org OAuth app (client id/secret)
/services/agentHarness/credentials/{orgId}/…                                         unchanged (model accounts, P2.4b); bound as mode model_account
/services/agentHarness/credentialKeys                                                unchanged keyring
```

Each document is the P2.4b versioned envelope (AES-256-GCM per version, CAS writes from Node,
`raise_on_error` reads from Python), so the toolset and MCP credentials gain versions, rotation and
tombstones they lack today.

---

## 5. Resolution algorithms

### 5.1 `PolicyResolver.resolve(layers)`

```text
input: layers ordered release, deployment, org, team?, agent?, user?, trigger|parent?, request?, live?
ceil = {}; choice = {}; prov = {}
for L in layers:
  for (path, v) in L.set_dimensions():
    d = DIMENSIONS[path]
    if L.kind ∉ d.exposure: return refusal(path, L, 'not_exposed')
    if d.kind == ceiling:
      if path in ceil and not d.order.leq(v, ceil[path]): return refusal(path, L, 'widening')
      ceil[path] = path in ceil ? d.order.meet(v, ceil[path]) : v; prov[path].ceilings += (L, v)
    else:  // choice
      choice[path] = (v, L)
for (path, (v, L)) in choice:
  bound = ceil[DIMENSIONS[path].within]
  if not within(v, bound): return refusal(path, L, 'outside_ceiling')
return EffectiveRunConfig(ceil ∪ choice ∪ defaults(release), prov), digest
```

Cost: O(set dimensions); the dimension table is ~60 rows. The `org ⊓ team ⊓ agent` prefix is cached per
`(org, agent, headRevision)` (§8.2), so a run folds only user, trigger, request and live.

### 5.2 `EffectiveSetResolver.resolve(subject, effectiveConfig)`

```text
grant = subject.agent ? grants[agent:key] : grants[org] ∪ grants[user:actor] ∪ grants[team:t] for t in actorTeams
visible(inst) = inst.visibility == org
             or (inst.visibility == team and inst.owner.team ∈ actorTeams)
             or inst.owner == user:actor
for entry in grant.entries (sorted by installation id):
  inst = installations[entry.installation]
  if inst missing or status ∉ {enabled, upgrade_pending}: unavailable(entry, not_installed|disabled); continue
  if not effectiveConfig.catalog.kinds[inst.kind].allowed: unavailable(entry, policy_ceiling); continue
  if not visible(inst): unavailable(entry, not_visible); continue
  tools = inst.approved.tools
        ∩ entry.tools
        ∩ effectiveConfig.tools.grant          // ceilings from org/team/agent/request
        ∩ effectsAllowed(effectiveConfig.tools.effects, descriptors)
  binding = module(inst.kind).selectBinding(inst, subject, bindings)   // agent → actor → org, per credentialMode
  if inst needs credential and binding not active: unavailable(entry, credential_missing|expired|revoked)
  emit item(inst, tools, binding, provenance)
if count(tools) > cap: refusal(effective_set_too_large)
if any required entry unavailable: refusal(required_item_unavailable, item)
return EffectiveSet(items, digest)
```

No store access inside the loop: `installations`, `grants`, `bindings` and `actorTeams` come from the
snapshot (§8). Unavailable items are listed (with reasons) so the UI can offer the fix (FR-2, FR-3);
the runtime never loads them.

### 5.3 `RunConfigPinner.pin(subject)`

```text
snap = cache.snapshot(org, agent, actor)                 // §8; may batch-load on miss
layers = [release, deployment, snap.org, snap.team(agent), snap.agent, snap.user, trigger|parent, request, snap.live]
cfg = PolicyResolver.resolve(layers)                     // uses cached prefix
set = EffectiveSetResolver.resolve(subject, cfg)
llm = snap.models[cfg.models.primary]                    // snapshot of the configured model (fixes R2 F2 modelKey re-read)
return ResolvedRunConfig{headRevision: snap.head, effectiveSet: set, effectiveConfig: cfg, llm, digest}
```

Admission asserts `cpOrgHeads[org].revision == headRevision` inside its transaction (§6.3).

### 5.4 One digest scheme

| Digest | Over | Replaces |
|---|---|---|
| `descriptorDigest` (tool) | canonical `{name, description, inputSchema, effect, sink}` | MCP hub digest (P2.8 used name, description, inputSchema; `effect` and `sink` are added because they decide approvals); Node `digest(toolId, fullName)` (`H/domain/run-spec.ts:220-226`); Node MCP `digest(instanceId, typeId, names)` (`run-spec.ts:274-280`) |
| `descriptorSetDigest` | sorted `{name: descriptorDigest}` | MCP manifest digest; toolset "manifest" |
| `bundleDigest` (skill) | `skill-bundle/1` file map (P7.1) | `digest(name, version)` fallback (X-8) and `model_dump` digest |
| `engineDigest` | `{kind, version, binaryDigest, optionsDigest}` | `profileDigest` stays as a component |
| `effectiveSet.digest`, `ResolvedRunConfig.digest` | as defined above | `specDigest` remains the outer digest |

Vectors: `CP/vectors/digests.json` (both languages).

---

## 6. Concurrency

### 6.1 Revisions and compare-and-set

- Every mutable object has an integer `revision`. Writes are `updateOne({_id, revision: expected},
  {$set: …, $inc: {revision: 1}})`; zero matched → `409 CP_REVISION_CONFLICT {current}`.
- HTTP: `ETag: "<revision>"` on GET; `If-Match` required on PUT/PATCH/DELETE (`428` without it). This
  generalises P2.11e's `baseUpdatedAt`.
- Within one transaction: object CAS → history insert → `cpOrgHeads` `$inc` → audit append → outbox
  insert. Two concurrent writes to different objects in one org conflict on `cpOrgHeads` and are retried
  by `withTransaction` (Mongo write conflict is transient); writes are admin-rate, so per-org
  serialisation is acceptable and gives a total order for cache invalidation.
- Idempotency: create endpoints accept `Idempotency-Key`; the key and the response digest are stored on
  the created object for 24 h.

### 6.2 Approval application

`decide(request, decision)`: in one transaction, CAS the request `state: open → approved`, CAS the target
at `request.target.revision`, apply `request.change`, head++, audit, outbox. If the target moved, the
request becomes `superseded` and the approver sees the new pending change (no approval of something the
approver did not see).

### 6.3 Admission and the head revision

- Durable path: `RunAdmissionStore.admit` already runs a snapshot transaction (`H/adapters/mongo/durable-transaction.ts`).
  It adds one read of `cpOrgHeads[org]` **inside** that transaction. If it differs from
  `resolved.headRevision`, the transaction aborts with `CP_STALE` and admission re-pins once with a
  refreshed snapshot (the delta load is bounded by `touched`). Linearisation point: admissions that start
  after a control-plane write commits observe it.
- Legacy path: one `findOne({_id: org}, {revision: 1})` before proxying (≈1 ms on the same Mongo).
- Standalone Mongo (no replica set): see §6.4.

### 6.4 Standalone Mongo

Transactions need a replica set (`REPLICA_SET_AVAILABLE`). On standalone deployments the write path runs
the same steps in order with each step idempotent and keyed by `(object, revision)`: history insert
(the source of truth for the step list) → object CAS → head `$inc` → audit append → outbox. A reconciler
(`ControlPlaneRepair`, run on boot and every minute) completes any revision whose history row exists
without its later steps. Admission on standalone is legacy-only already (DUR3), and its head check is the
legacy one. Open question Q3 in §12.

---

## 7. Audit

- `ConfigAuditLog.record(change, tx)` appends to `AuditChainStore` with `streamId = cp:{orgId}`,
  `writer = control-plane`, `sessionId = null`, `runId = null`, `journalSeq = headRevision`. This needs
  `AuditEntry` (`H/domain/audit.ts:36-45`) to allow a config-stream variant (discriminated by
  `kind: config.changed`) and `AUDIT_EVENT_FIELDS` to list it; `verifyAuditChain` is unchanged because
  it chains records, not journal semantics.
- Covered writes: every route in §9 plus the facades, the Labs save (R2 CP1), schedules, model accounts,
  and `interaction.granted` (run stream, already chained events but not this one).
- What is recorded: ids, revisions, digests, actor, approval reference, and a field-path list of what
  changed. Values are recorded only for policy layers (non-secret) as the canonical JSON patch; never for
  bindings.
- Read: `GET /api/v1/control-plane/audit?object=&since=&limit=` for admins and the security-reviewer
  scope (`CP_AUDIT_READ`).

---

## 8. Caching and invalidation

### 8.1 Node: `ResolvedSnapshotCache`

| Tier | Key | Content | Bound | Loaded by |
|---|---|---|---|---|
| Org | `orgId` | head revision, enabled installations (approved sets, owner, visibility, credentialMode), org and team layers, org grant, live layer, model configs (`AI_MODELS` snapshot), Labs rollout values | 2,000 orgs LRU | one query per collection with `{orgId}` |
| Agent | `(orgId, agentKey)` | agent grant, agent layer, owner team, agent definition digest, cached `org ⊓ team ⊓ agent` prefix fold | 20,000 LRU | `cpGrants` + `cpPolicyLayers` by `_id`; agent directory |
| Principal | `(orgId, userId)` | actor teams, user layer, user grant, the actor's binding metadata | 50,000 LRU | `cpPolicyLayers`, `cpGrants`, `cpBindings` by owner; `MembershipPort` |
| Agent access | `(orgId, agentKey, userId)` | allow/deny + `definitionVersion` | 50,000 LRU, 30 s TTL backstop | `AgentDirectory.authorizeRun` (today's run-definition call) |

Every entry stores `asOf` (the head revision it reflects). On `cp.changed{previous, revision, touched}`:

- if the entry's `asOf == previous`, untouched entries advance to `revision`; touched entries are dropped;
- if `asOf < previous` (a gap: missed event), the entry is dropped;
- the org tier's live layer is patched from `event.live` without a reload.

Admission does not trust `asOf` alone: the head check (§6.3) catches a missed or delayed event. A miss
costs one batched read per collection for the tier (NFR-10).

### 8.2 Precomputed folds

`AgentSnapshot.prefix = PolicyResolver.foldPrefix([release, deployment, org, team, agent])`, stored with
the fold's provenance. Per run: fold `user, trigger|parent, request, live` onto it. Measured target: ≤ 1 ms
CPU for 60 dimensions.

### 8.3 Python

| Cache | Key | Source | Invalidation | Failure |
|---|---|---|---|---|
| `LiveLayerCache` | `orgId` | `control-plane-events` consumer (each worker process, its own consumer group) | event revision chain; on a gap or every 30 s, `GET /api/v1/agent-harness/internal/control-plane/orgs/{org}/live?since=` (worker credential) | NFR-33 |
| Resolved-config key cache | `kid` | Node public keys (existing gateway-key route, 30 s cache) | rotation by `kid` | unknown `kid` → refuse (legacy path) |
| Skill index | `(orgId, skillSetDigest)` | pinned bundle digests | content-addressed: never stale | rebuild |
| MCP schema cache (P2.8) | unchanged, but the key's config digest is the installation revision | — | `tools/list_changed`, revision change | fail closed (`mcp_manifest_unavailable`) |
| `ConfigurationService` LRU | unchanged | — | existing `pipeshub:cache:invalidate` / etcd watch | — |

### 8.4 Run hand-off (LAT1) uses the same pattern

Config invalidation and run hand-off are two streams of the same "notify after commit, poll only as a
backstop" rule (NFR-48):

| Stream | Publisher | Channel | Consumer | Backstop |
|---|---|---|---|---|
| Config change | outbox dispatcher after the control-plane transaction | broker topic `control-plane-events` (ordered per org) | every Node process (snapshot cache), every worker (live layer) | head check in admission; live-layer poll 30 s |
| Run ready | admission, resume, child spawn, next-run drain, after their transaction commits | Redis `harness:run-ready` (pub/sub, per namespace) with `{runId, executor, orgId}` | claimers (inline and worker); any hint resets the idle backoff | existing outbox (2 s) and sweeper |
| Interaction resolved | decision route after commit | existing `harness:interaction-notify:<run>` (P2.6c) | the holding attempt | journal poll |

The control plane owns only the first row; LAT1 owns the second and third. They share one
`AfterCommitPublisher` helper (Node `application/after-commit.ts`) so neither invents its own
outbox-or-publish logic.

### 8.5 Membership and agent access

- `MembershipPort.teamsOf(org, user)` is implemented over the connectors service
  (`/api/v1/entity/user/teams`); cached in the principal tier. Team membership changes must emit
  `entity-events` `teamMembershipChanged{orgId, teamId, userIds}` from `PY/app/api/routes/entity.py`
  (new event type), consumed by Node to drop principal entries. If membership cannot be read, the actor's
  team set is empty: this can only hide team-visible items (fail closed for visibility); team *ceilings*
  come from the agent's owner team, which the control plane stores itself.
- Agent sharing and deletion in `PY/app/api/routes/agent.py` emit `agentChanged{orgId, agentKey,
  definitionVersion}` on `entity-events`; Node drops agent and agent-access entries. The 30 s TTL is the
  backstop if an event is lost.

---

## 9. APIs and DTOs

### 9.0 The chat hot path stays on the existing routes

The control plane adds no chat route. The existing handlers in `N/modules/enterprise_search/controller/es_controller.ts`
(`POST /api/v1/conversations/stream`, `/:id/messages/stream`, `/:id/message/:mid/regenerate`,
`/api/v1/agents/:agentKey/conversations/stream`, their `:id/messages/stream` and regenerate twins, and the
`/internal/*` Slack variants) call one `ChatTurnService.start(req)`:

1. authenticate; load the conversation (server-side `projectId`, `agentKey`, participants);
2. `RunConfigPinner.pin(subject)` → `ResolvedRunConfig` (with `routing` and `scope`);
3. durable: `RunAdmissionStore.admit` (asserting the head revision) → after-commit `run-ready` →
   stream the projection on **this** response (the `RunStreamService` the `GET …/runs/:id/stream` route
   uses), first frame `conversation_created`;
4. legacy: proxy to Python with `X-PipesHub-Resolved: <compact JWS>` carrying `ResolvedRunConfig`.

Request bodies, SSE framing and persisted shapes are unchanged (NFR-40). `POST …/runs` and
`GET …/runs/:id/stream` call the same `ChatTurnService` core and remain for reconnect and existing
clients. LAT1 implements steps 3–4's transport; the control plane supplies step 2 and the `routing`
decision.

### 9.1 Control-plane routes

Base path `/api/v1/control-plane`. Every route: `auth.authenticate` → `requireScopes(...)` (OAuth tokens)
→ `Authorizer.check(actor, action, resource)` → `ValidationMiddleware.validate(generated zod)`. `orgId` and
actor always come from the token. One route factory (`cpRoute(spec)`) replaces the four copies of guard +
validate + log in `H/transport/admin-routes.ts` (R2 §3.3).

New OAuth scopes (`N/libs/enums/oauth-scopes.enum.ts`): `CP_CATALOG_READ`, `CP_INSTALL_WRITE`,
`CP_GRANT_WRITE`, `CP_POLICY_WRITE`, `CP_BINDING_WRITE`, `CP_APPROVAL_DECIDE`, `CP_AUDIT_READ`. Existing
`CONFIG_*`, `MCP_*`, `SKILL_*` and `CONNECTOR_*` keep working on the facades. Regular (non-OAuth) JWTs
pass `requireScopes` (`require-scopes.middleware.ts:37-39`), so the `Authorizer` is the actual gate.

| Method and path | Action (Authorizer) | Request | Response | Notes |
|---|---|---|---|---|
| `GET /catalog?kind=&q=&cursor=` | `catalog.read` | — | `CatalogPage{items[CatalogItemSummary], next}` | Shared + org rows; user sees what policy lets them install |
| `GET /catalog/{ref}` | `catalog.read` | — | `CatalogItem` | |
| `POST /catalog/imports` | `catalog.import` | `{source: {type, locator}}` | `202 {itemRef, approvalRef?}` | Skill and plugin import (npm, GitHub, URL, upload) through the domain driver |
| `GET /installations?kind=&owner=&status=` | `installation.read` | — | `InstallationPage` | Filtered by visibility for non-admins |
| `POST /installations` | `installation.create` | `{item, owner, visibility, config}` | `201 Installation` or `202 {installation, approvalRef}` | `Idempotency-Key` |
| `GET /installations/{id}` | `installation.read` | — | `Installation` + `ETag` | Includes `usage{grants, agents}` for impact |
| `PATCH /installations/{id}` | `installation.update` | `If-Match`, `{config?, visibility?}` | `Installation` or `202` | Config change may trigger re-discovery and approval |
| `POST /installations/{id}/actions/{enable|disable|rediscover|upgrade}` | `installation.update` | `If-Match`, `{version?}` | `Installation` or `202` | `disable` is narrowing: `live` delta |
| `DELETE /installations/{id}` | `installation.delete` | `If-Match` | `204` | Cascades plugin children; grants keep a dangling entry shown as `not_installed` until edited |
| `GET /grants/{subjectKind}/{subjectId}` | `grant.read` | — | `Grant` + `ETag` | |
| `PUT /grants/{subjectKind}/{subjectId}` | `grant.write` | `If-Match`, `{entries}` | `Grant` | Agent subject: requires `can_edit` on the agent (AgentDirectory) |
| `GET /policy/{scopeKind}/{scopeId}/{section}` | `policy.read` | — | `{layer, inherited{ceilings with setBy}, etag}` | `scopeId` = `current` for org |
| `PUT /policy/{scopeKind}/{scopeId}/{section}` | `policy.write:{section}` | `If-Match`, section document | `{revision}` or `422 CP_WIDENING{dimension, layer}` | |
| `GET …/{section}/history?limit=` | `policy.read` | — | `PolicyRevision[]` | |
| `POST …/{section}/rollback` | `policy.write:{section}` | `If-Match`, `{revision}` | `{revision}` | A rollback is a new revision |
| `GET /bindings?owner=me|agent:{key}|org` | `binding.read` | — | `BindingSummary[]` (status, expiry, never secrets) | Feeds "My connections" |
| `POST /bindings` | `binding.create` | `{installation, owner, mode, apiKey?}` | `201 Binding` or `{authorizeUrl}` (OAuth) | API keys go straight to the vault; never echoed |
| `POST /bindings/{id}/actions/{rotate|revoke|refresh}` | `binding.update` | `If-Match` | `Binding` | `revoke` is narrowing |
| `GET /approvals?state=open&mine=true` | `approval.read` | — | `ApprovalRequest[]` with diffs | |
| `POST /approvals/{id}/decision` | `approval.decide` | `{decision, subset?, note?, targetRevision}` | `ApprovalRequest` | `409 CP_APPROVAL_SUPERSEDED` |
| `POST /explain` | `explain` | `{agentKey?, userId?, request?}` | `ResolvedRunConfig` with provenance, or the refusal | `userId` other than self requires admin |
| `GET /agents/{agentKey}/grantable?kind=` | `grant.write` on agent | — | `GrantableItem[]{installation, item, state, reason?, action?}` | One call for the builder (NFR-9) |
| `GET /me/capabilities` | self | — | `{assistant: EffectiveSet, connections: BindingSummary[], routing: {chatMode: durable|legacy}}` | Chat bootstrap; cacheable by `ETag = headRevision` |
| `GET /audit?object=&since=&limit=` | `audit.read` | — | `ConfigAuditEntry[]` | |
| `GET /api/v1/agent-harness/runs/{runId}/resolved` | run owner or admin | — | `ResolvedRunConfig` + `liveApplied[]` + `heldRequests` + `blockedRequests` | Extends `effectivePolicy` (SEC1 H6) additively |

Internal (listener on `AGENT_HARNESS_INTERNAL_PORT`, worker credential or service token):

| Route | Caller | Purpose |
|---|---|---|
| `GET /api/v1/agent-harness/internal/control-plane/orgs/{orgId}/live?since=` | Python workers, gateways | Live-layer backstop |
| `POST /api/v1/control-plane/internal/oauth/callback` | Connectors service | Hand tokens to `BindingService` (service token, `scopedTokenValidator`) |
| Driver calls Node → Python | Node domain modules | `POST {connectorBackend}/api/v1/control-plane/drivers/{kind}/{discover|validate|describe}` and `{aiBackend}/api/v1/control-plane/drivers/skill/{build|describe}` |

Existing routes become facades (NFR-41), implemented in `N/modules/control-plane/facades/`:

| Legacy route | Facade maps to |
|---|---|
| `GET/PUT /api/v1/agent-harness/admin/{approvals,engine-profile,model-access,memory-policy,session-retention}` | `policy/org/current/{approvals,executor,models,memory,retention}` with the old DTO shape |
| `/api/v1/toolsets/instances*`, `/api/v1/mcp-servers/instances*`, `…/manifest`, `…/runner` | installations + approvals |
| `/api/v1/toolsets/…/credentials*`, `/api/v1/mcp-servers/…/auth*` | bindings |
| `/api/v1/skills*` CRUD, import, candidates | catalog (org/user rows), approvals (candidates) |
| `POST /api/v1/agents/create`, `PUT /api/v1/agents/:agentKey` (toolsets, mcpServers, skills fields of `AgentFormPayload`) | Node writes `cpGrants` for the capability fields, forwards the rest to Python unchanged |
| `POST /api/v1/configurationManager/platform/settings` (Labs) | harness flags → `policy/org/current/rollout`; other flags unchanged; CAS + audit |

---

## 10. Error model

One generated type, `Problem` (`CP/problem.schema.json`), used in HTTP bodies, refusals stored on runs,
tool results for unavailable items, and the frontend:

```jsonc
{
  "code": "CP_CREDENTIAL_MISSING",
  "status": 409,
  "message": "Connect your Linear account to use Linear tools in this agent.",
  "retryable": false,
  "subject": { "installation": "inst_01J9…", "item": "mcp:pipeshub/linear@3" },
  "dimension": null, "layer": null,
  "remediation": { "action": "connect_account", "actor": "self", "href": "/workspace/connections?installation=inst_01J9…" },
  "requestId": "…"
}
```

| Code | HTTP | Retryable | Remediation actor | Meaning |
|---|---|---|---|---|
| `CP_REVISION_CONFLICT` | 409 | yes (re-read) | self | `If-Match` mismatch |
| `CP_PRECONDITION_REQUIRED` | 428 | no | self | missing `If-Match` |
| `CP_WIDENING` | 422 | no | admin of the layer above | a layer widens a ceiling (`dimension`, `layer`) |
| `CP_NOT_EXPOSED` | 422 | no | — | dimension not settable at this layer |
| `CP_OUTSIDE_CEILING` | 422 | no | self | a choice outside its ceiling |
| `CP_LOCKED` | 422 | no | admin of the locking layer | a layer changes a setting a layer above locked (`dimension`, `layer`; C4.02) |
| `CP_UNKNOWN_REFERENCE` | 422 | no | self | a layer refers to something the org does not have (`ReferenceChecker`, 03-interfaces §5.6; added by CP-SKEL) |
| `CP_INVALID_CONFIG` | 422 | no | self | a configuration does not validate: an installation's (`ConfigValidator`, 03-interfaces §3.1) or a layer value (the resolver's `invalid` refusal); added by CP-SKEL |
| `CP_FORBIDDEN` | 403 | no | admin | Authorizer denied |
| `CP_NOT_FOUND` | 404 | no | — | no such object in the caller's org; another org's object answers the same, so ids do not leak (added by CPD-11) |
| `CP_NOT_INSTALLED`, `CP_DISABLED`, `CP_NOT_APPROVED`, `CP_NOT_VISIBLE`, `CP_NOT_GRANTED` | 409 at admission for `required` items; item state otherwise | no | admin / team admin / builder | effective-set reasons |
| `CP_CREDENTIAL_MISSING`, `CP_CREDENTIAL_EXPIRED`, `CP_CREDENTIAL_REVOKED` | 409 / item state | no | binding owner | |
| `CP_POLICY_CEILING` | 409 / item state | no | admin | kind not allowed |
| `CP_APPROVAL_SUPERSEDED` | 409 | no | approver | target changed |
| `CP_DESCRIPTOR_CHANGED` | tool result | no | admin | live descriptor ≠ pinned (`mcp_tool_changed` generalised) |
| `CP_EFFECTIVE_SET_TOO_LARGE` | 422 | no | builder | > cap |
| `CP_STALE` | internal | yes | — | admission head moved; re-pin |
| `CP_UNAVAILABLE` | 503 | yes | operator | store or cache unavailable under a fail-closed rule |
| `CP_KILL_SWITCH` | 503 | no | admin | kill switch on |

Python maps the same codes (generated `ERROR_CODES`); the frontend renders `remediation` as the action
button (UX). Existing harness codes (`AGENT_HARNESS_ENGINE_PROFILE_WIDENING`, `APPROVALS_CHANGED`,
`mcp_tool_unapproved`, `credential_*`) keep their wire names on facades and are listed as aliases in
`errors.json`.

---

## 11. Migration of today's stores into the core

Each migration is a D2-style registered migration (`HARNESS_DATA_MIGRATIONS`): dry run, `--apply`,
`--revert`, a ledger key, idempotent, with reads served through a read-through adapter during the
dual-write window.

### 11.1 The five per-org KV settings classes

| Today (`H/application/`) | KV path (`paths.ts:95-111`) | Target |
|---|---|---|
| `ConfigApprovalSettings` (`approval-settings.ts:30`) | `/services/agentHarness/approvals` `{default, orgs, agents, orgAgents, updated}` (plus any section an extension maps to its own layer section) | `orgs[o]` → `cpPolicyLayers[o:org].approvals`; `orgAgents[o][a]` → `cpPolicyLayers[o:agent:a].approvals` **with `lockedBy: org`** (keeps P2.11e's rule that editors cannot remove admin asks: the agent layer gets an admin-owned sub-section the builder cannot write); `default`, `agents[*]` → deployment layer (operator data file) |
| `ConfigEngineProfileSettings` (moved out of this repository) | `/services/agentHarness/engineProfiles` `{orgs:{o:{ceiling}}}` | org layer `executor` + `engineOptions` |
| `ConfigModelAccessPolicies` (moved out of this repository) | `/services/agentHarness/modelAccess` | org layer `models.vendorRules` (narrow-only), `models.consumerPlans` |
| `ConfigMemorySettings` | `/services/agentHarness/memory` | org layer `memory` |
| `ConfigSessionRetentionSettings` | `/services/agentHarness/sessionRetention` | org layer `retention` |
| `HarnessRollout` / Labs harness flags | `/services/platform/settings` | org layer `rollout` (`ENABLE_AGENT_HARNESS`, `AGENT_HARNESS_KILL_SWITCH`, `ENABLE_SCHEDULED_RUNS`, `ENABLE_COLLABORATOR_TURNS`, `ENABLE_SKILLS`, `ENABLE_MCP`, `ENABLE_ACTIONS`, `ENABLE_USER_CONTEXT`, `ENABLE_KNOWLEDGE_PUBLICATIONS`); `modelFamilies` → `models.families` |

Steps: (1) `PolicyStore` + read-through adapter that serves `cpPolicyLayers` if present else the KV
document; (2) migration copies each org section; (3) the old `Config*` classes become thin adapters over
`PolicyStore` (dual-write KV for one release so a rollback to the previous Node build still reads current
values); (4) Python readers (`PY/app/harness/approvals.py`, `access/policies.py`, `engine_profiles.py`)
stop reading KV because the worker reads the pin; (5) delete the classes, `updateConfigDocument`, the KV
documents and their `flags.json` rows.

### 11.2 Registries and installations

| Today | Target |
|---|---|
| `ToolsetRegistry` (`PY/app/agents/registry/toolset_registry.py:113`) | Build step `python -m app.control_plane.catalog.dump toolsets` → `CP/catalog/toolsets.json` (release items with full `ToolDescriptor`s including schema). The runtime registry stays as the code that executes tools |
| Toolset instances, one global KV list `/services/toolset-instances` (`PY/app/api/routes/toolsets.py:96`, no CAS, R2 F21) | `cpInstallations` (kind `toolset`), `id` = existing `instanceId` |
| `MCPRegistry` templates (`PY/app/agents/mcp/registry.py:18`) | `CP/catalog/mcp.json` |
| MCP instances `/services/mcp/instances/{instanceId}` (note the `{orgId}` path disagreement in `PY/app/agents/mcp/models.py:123`; the migration reads both) | `cpInstallations` (kind `mcp`), `id` = `instanceId`; `runner` settings (P5.10) → `config.runner` |
| MCP manifests `/services/mcp/manifests/{id}` + drift docs | `installation.approved` (descriptor digests, approvedBy/At) + `cpInstallationHistory` (the bounded history moves to unbounded history); drift doc → `installation.pending` |
| Skills `GraphSkillStore` (`agentSkills`, versions, candidates) | Catalog rows (org or user publisher) with `bundleDigest`; content stays in the graph/blob; built-in packs → release catalog, **no more per-org copies** (`builtin_seeder.py` retired) |
| Engines `harness_engines/allowlist.json` | `CP/catalog/engines.json` (generated from the allowlist); org enablement → `executor.engines` ceiling (already) |
| Agent edges `agentHasToolset/McpServer/Skill` (`PY/app/api/routes/agent.py:940,1168,1528`) | `cpGrants[o:agent:{key}]`; edges dual-written for one release, then no longer read at run time; deleted after the facade window |

### 11.3 Approval flows

| Today | Target |
|---|---|
| MCP manifest approval (`PY/app/api/routes/mcp_hub.py:188-268`, digest OCC, history 50) | `ApprovalRequest{change: descriptor_change}`; existing approved manifests migrate as already-approved installations (no re-approval) |
| Skill candidate approve/reject by any `SKILL_WRITE` user (`skills.py:765-786`) | `ApprovalRequest{change: publish}`; approver from `catalog.approval` (default: owner for user scope, org admin for org scope). Fixes R2 F3's approval half; the CPS branch fixes it first in place |
| Engine-agent admin gate (P2.11e: only org admin may make an agent an engine agent) | `catalog.approval` rule `{change: enable_engine, approver: orgAdmin}` evaluated on agent-layer writes |
| Per-call approvals (P2.6c) | Unchanged mechanism; rules now pinned (R2 CP6) and approvers read from the org layer |

### 11.4 Credential layouts

| Today | Target |
|---|---|
| `/services/toolsets/{instanceId}/{userId|agentKey}` (unversioned) | `/services/controlPlane/credentials/{org}/{inst}/{user|agent}/{id}` (P2.4b envelope) + `cpBindings` row |
| `/services/mcp/credentials/{instanceId}/{owner}[/oauth-tokens|/dcr-client]` | same layout; DCR client → `/services/controlPlane/oauthApps/{org}/dcr/{inst}` |
| `/services/oauths/toolsets/{type}` | `/services/controlPlane/oauthApps/{org}/{provider}` |
| `/services/agentHarness/credentials/{org}/…` (model accounts) | unchanged path; bindings of mode `model_account` reference it |
| Refresh: `ToolsetTokenRefreshService`, `mcp_token_refresh_service.py`, `app/agents/mcp/token_refresh.py` | one `TokenRefresher` driven by `cpBindings{status, expiresAt}` with per-provider strategies |

Secrets are copied, verified by digest and the old path is kept read-only for one release; the
`ToolsetSecretSource` and `McpSecretSource` (`PY/app/harness/credentials/sources.py`) gain the new path
first and fall back to the old.

### 11.5 Digests

Old pins keep resolving (NFR-42): `x.pipeshub.mcp[].manifestDigest` (name digest), toolset
`descriptorDigest` (`toolId, fullName`), skill `digest(name, version)`. New pins write the §5.4 digests
inside `resolved` and in the legacy fields; the worker verifies the new ones when present.

---

## 12. Open questions (with recommended answers)

| # | Question | Recommendation |
|---|---|---|
| Q1 | Store agent grants in Mongo (`cpGrants`) or keep graph edges as the store behind `GrantStore`? | Mongo. One transaction with installations, policy and audit; Node can read it without a Python hop. Graph edges are dual-written for one release for any graph query that uses them. |
| Q2 | Ship release catalog items as data files or seed rows? | Data files loaded into a read-only `CatalogStore` adapter. No per-org copies; upgrades need no migration. |
| Q3 | Support control-plane writes on standalone Mongo? | Yes, with the ordered-steps + `ControlPlaneRepair` path (§6.4), marked degraded in health; no new feature depends on transactions beyond what the harness already requires. |
| Q4 | Should `effect` and `sink` be in the tool descriptor digest? | Yes: they decide approvals and flow guard, so a server that relabels a write as a read must be re-approved. |
| Q5 | Where does a per-user MCP/toolset install's approval go when the org allows user installs? | `owner` approval (self) for `remote_only` personal use; sharing beyond the user requires the org rule. |
| Q6 | One `control-plane-events` topic or reuse `entity-events`? | A new topic: different consumers (every Node and worker process), ordering by org, and payloads that carry live-layer deltas. |
